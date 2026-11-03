import AppKit
import ScreenCaptureKit
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
import AVFoundation
import CoreImage
import CryptoKit

struct CaptureFailure: Error {
    let code: Int
    let message: String
    var stageSpoiled: Bool = false
}

func requireGrant() throws {
    guard CGPreflightScreenCaptureAccess() else {
        throw CaptureFailure(code: 3, message: "This agent process may not hold the Mac screen-recording grant. No prompt was requested. Run from a terminal that holds the grant, or arrange permission before retrying.")
    }
}

func stageManager() -> Bool? {
    UserDefaults(suiteName: "com.apple.WindowManager")?.object(forKey: "GloballyEnabled") as? Bool
}

func front() -> [String: Any]? {
    guard let app = NSWorkspace.shared.frontmostApplication else { return nil }
    return ["app": app.localizedName ?? "", "id": String(app.processIdentifier)]
}

func fullGrayPixels(_ image: CGImage) -> [UInt8] {
    // Inspect original-resolution luma; sparse text must survive the quality test.
    var bytes = [UInt8](repeating: 0, count: image.width * image.height)
    bytes.withUnsafeMutableBytes { data in
        let ctx = CGContext(data: data.baseAddress, width: image.width, height: image.height,
                            bitsPerComponent: 8, bytesPerRow: image.width,
                            space: CGColorSpaceCreateDeviceGray(), bitmapInfo: 0)!
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
    }
    return bytes
}

func uniformPixels(_ image: CGImage) -> Bool {
    let bytes = fullGrayPixels(image)
    return Int(bytes.max() ?? 0) - Int(bytes.min() ?? 0) <= 2
}

func meaningfulPixelMotion(_ first: [UInt8], _ next: [UInt8]) -> Bool {
    first.count == next.count && zip(first, next).contains { abs(Int($0) - Int($1)) > 2 }
}

func geometrySpoiled(_ frame: CGSize, _ content: CGSize) -> Bool {
    min(content.width / frame.width, content.height / frame.height) < 0.85
}

func thumbnailSignal(errorCode: Int? = nil, contentScale: Double? = nil) -> Bool {
    errorCode == -3811 || (contentScale.map { $0 < 1 } ?? false)
}

func sustainedBlank(_ flags: [Bool], fps: Int) -> Bool {
    if !flags.isEmpty && flags.allSatisfy({ $0 }) { return true }
    var longest = 0, current = 0
    for blank in flags { current = blank ? current + 1 : 0; longest = max(longest, current) }
    return longest >= fps
}

func recordingLate(_ elapsed: Double, requested: Double, fps: Double) -> Bool {
    elapsed > requested + max(0.2, 2 / fps)
}

func grayPixels(_ image: CGImage) -> [UInt8] {
    var pixels = [UInt8](repeating: 0, count: 64 * 64)
    pixels.withUnsafeMutableBytes { bytes in
        let ctx = CGContext(data: bytes.baseAddress, width: 64, height: 64, bitsPerComponent: 8,
                            bytesPerRow: 64, space: CGColorSpaceCreateDeviceGray(), bitmapInfo: 0)!
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: 64, height: 64))
    }
    return pixels
}

func spread(_ image: CGImage) -> Double {
    let pixels = grayPixels(image)
    let mean = pixels.reduce(0.0) { $0 + Double($1) } / Double(pixels.count)
    return sqrt(pixels.reduce(0.0) { $0 + pow(Double($1) - mean, 2) } / Double(pixels.count))
}

@MainActor
func content() async throws -> SCShareableContent {
    try requireGrant()
    return try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
}

func intersectsActiveDisplay(_ rect: CGRect) -> Bool {
    var count: UInt32 = 0
    guard CGGetActiveDisplayList(0, nil, &count) == .success else { return false }
    var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
    guard CGGetActiveDisplayList(count, &ids, &count) == .success else { return false }
    return ids.prefix(Int(count)).contains { rect.intersects(CGDisplayBounds($0)) }
}

func metadata(_ w: SCWindow) -> [String: Any] {
    let rows = CGWindowListCopyWindowInfo(.optionIncludingWindow, w.windowID) as? [[String: Any]] ?? []
    let cgOnscreen = rows.first?[kCGWindowIsOnscreen as String] as? Bool == true
    let onDisplay = intersectsActiveDisplay(w.frame)
    let available = cgOnscreen && onDisplay
    let pid = w.owningApplication?.processID ?? 0
    let launched = NSRunningApplication(processIdentifier: pid)?.launchDate?.timeIntervalSince1970 ?? 0
    return ["id": String(w.windowID), "title": w.title ?? "", "app": w.owningApplication?.applicationName ?? "",
     "x": w.frame.minX, "y": w.frame.minY, "width": w.frame.width, "height": w.frame.height,
     "capturable": available, "cg_is_on_screen": cgOnscreen, "intersects_active_display": onDisplay,
     "capture_state": available ? "onscreen CG metadata; pixels checked at capture" : "not onscreen or outside active displays; possible Stage Manager strip",
     "process_identity": "\(pid):\(launched)",
     "sck_is_on_screen": w.isOnScreen] // Diagnostic only; never a filter.
}

func filterConfig(_ w: SCWindow) throws -> (SCContentFilter, SCStreamConfiguration) {
    try requireGrant()
    let f = SCContentFilter(desktopIndependentWindow: w)
    guard !geometrySpoiled(w.frame.size, f.contentRect.size) else {
        throw CaptureFailure(code: 6, message: "Capture content is smaller than the selected window frame; Stage Manager may have shrunk it.", stageSpoiled: stageManager() == true)
    }
    let c = SCStreamConfiguration()
    c.width = Int((f.contentRect.width * CGFloat(f.pointPixelScale)).rounded())
    c.height = Int((f.contentRect.height * CGFloat(f.pointPixelScale)).rounded())
    guard c.width > 1 && c.height > 1 else {
        throw CaptureFailure(code: 6, message: "Window has no full-sized content; wait for it to be visible before capture.", stageSpoiled: stageManager() == true)
    }
    c.showsCursor = false
    c.ignoreShadowsSingleWindow = true
    c.includeChildWindows = true
    c.colorSpaceName = CGColorSpace.sRGB
    return (f, c)
}

func assertAvailable(_ w: SCWindow) throws {
    // CG metadata is independent of SCK's unreliable isOnScreen diagnostic.
    let rows = CGWindowListCopyWindowInfo(.optionIncludingWindow, w.windowID) as? [[String: Any]] ?? []
    guard !rows.isEmpty else { throw CaptureFailure(code: 4, message: "The selected window closed during capture.") }
    guard let row = rows.first, row[kCGWindowIsOnscreen as String] as? Bool == true else {
        // With Stage Manager on, a window in another stage group is off screen too; bringing its app forward shows it.
        throw CaptureFailure(code: 6, message: "Window is off-Space, hidden, minimized or in another Stage Manager group. It must be visible in the current Space before capture can work.", stageSpoiled: stageManager() == true)
    }
    guard intersectsActiveDisplay(w.frame) else {
        throw CaptureFailure(code: 6, message: "The window is outside the active displays; it may be in the Stage Manager thumbnail strip.", stageSpoiled: stageManager() == true)
    }
    if let bounds = row[kCGWindowBounds as String] as? [String: Any],
       let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary),
       abs(rect.width - w.frame.width) > 1 || abs(rect.height - w.frame.height) > 1 {
        throw CaptureFailure(code: 6, message: "Window changed size after selection. Wait for its intended size and retry.", stageSpoiled: stageManager() == true && rect.width < w.frame.width && rect.height < w.frame.height)
    }
}

func loadImage(_ path: String) throws -> CGImage {
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw CaptureFailure(code: 7, message: "Cannot read sequence image")
    }
    return image
}

func verifyMovie(_ path: String, changing: Bool) async throws -> [String: Any] {
    let asset = AVURLAsset(url: URL(fileURLWithPath: path))
    guard let track = try await asset.loadTracks(withMediaType: .video).first else {
        throw CaptureFailure(code: 7, message: "Encoder produced no video track; retry at a supported size and rate.")
    }
    let reader = try AVAssetReader(asset: asset)
    let output = AVAssetReaderTrackOutput(track: track, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
    reader.add(output)
    guard reader.startReading() else { throw CaptureFailure(code: 7, message: "Cannot decode encoded movie") }
    let context = CIContext()
    var hashes = Set<Data>()
    var count = 0
    var minimum = Double.infinity
    var blanks: [Bool] = []
    while let sample = output.copyNextSampleBuffer() {
        guard let buffer = sample.imageBuffer else { throw CaptureFailure(code: 7, message: "Decoded movie has missing pixels") }
        let ci = CIImage(cvPixelBuffer: buffer)
        guard let image = context.createCGImage(ci, from: ci.extent) else { throw CaptureFailure(code: 7, message: "Cannot inspect decoded pixels") }
        minimum = min(minimum, spread(image))
        blanks.append(uniformPixels(image))
        hashes.insert(Data(SHA256.hash(data: Data(grayPixels(image)))))
        count += 1
    }
    guard reader.status == .completed, count > 0 else { throw CaptureFailure(code: 7, message: "Encoded movie is incomplete; retry encoding at a supported size and rate.") }
    let frameRate = try await track.load(.nominalFrameRate)
    guard !sustainedBlank(blanks, fps: max(1, Int(frameRate.rounded()))) else { throw CaptureFailure(code: 6, message: "Encoded movie is uniformly blank for a sustained interval. Wait for app content and retry.") }
    guard !changing || hashes.count >= 2 else { throw CaptureFailure(code: 7, message: "Encoded movie has no visible changes; use the app's own export or a larger UI change.") }
    return ["decoded_frames": count, "decoded_distinct_frames": hashes.count, "decoded_min_spread": minimum]
}

final class MovieWriter {
    let writer: AVAssetWriter
    let input: AVAssetWriterInput
    let adaptor: AVAssetWriterInputPixelBufferAdaptor
    let width: Int
    let height: Int
    let fps: Int32
    var count = 0

    deinit { if writer.status == .writing { writer.cancelWriting() } }

    init(path: String, width: Int, height: Int, fps: Int32) throws {
        self.width = width + width % 2; self.height = height + height % 2; self.fps = fps
        writer = try AVAssetWriter(outputURL: URL(fileURLWithPath: path), fileType: .mp4)
        input = AVAssetWriterInput(mediaType: .video, outputSettings: [AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: self.width, AVVideoHeightKey: self.height])
        input.expectsMediaDataInRealTime = false
        adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32ARGB,
            kCVPixelBufferWidthKey as String: self.width, kCVPixelBufferHeightKey as String: self.height,
            kCVPixelBufferCGImageCompatibilityKey as String: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey as String: true])
        guard writer.canAdd(input) else { throw CaptureFailure(code: 7, message: "H.264 writer unavailable") }
        writer.add(input)
        guard writer.startWriting() else { throw CaptureFailure(code: 7, message: writer.error?.localizedDescription ?? "Writer failed") }
        writer.startSession(atSourceTime: .zero)
    }

    func append(_ image: CGImage) async throws {
        let deadline = Date().addingTimeInterval(10)
        while !input.isReadyForMoreMediaData {
            guard writer.status == .writing, Date() < deadline else {
                throw CaptureFailure(code: 7, message: writer.error?.localizedDescription ?? "Video writer stalled")
            }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        var optional: CVPixelBuffer?
        guard let pool = adaptor.pixelBufferPool,
              CVPixelBufferPoolCreatePixelBuffer(nil, pool, &optional) == kCVReturnSuccess,
              let buffer = optional else { throw CaptureFailure(code: 7, message: "Cannot allocate video pixels") }
        CVPixelBufferLockBaseAddress(buffer, [])
        let ctx = CGContext(data: CVPixelBufferGetBaseAddress(buffer), width: width, height: height,
                            bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
                            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipFirst.rawValue)!
        ctx.setFillColor(CGColor(gray: 0, alpha: 1)); ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
        ctx.draw(image, in: CGRect(x: 0, y: height - image.height, width: image.width, height: image.height))
        CVPixelBufferUnlockBaseAddress(buffer, [])
        guard adaptor.append(buffer, withPresentationTime: CMTime(value: Int64(count), timescale: fps)) else {
            throw CaptureFailure(code: 7, message: writer.error?.localizedDescription ?? "Cannot append video frame")
        }
        count += 1
    }

    func finish() async throws {
        writer.endSession(atSourceTime: CMTime(value: Int64(count), timescale: fps))
        input.markAsFinished()
        await writer.finishWriting()
        guard writer.status == .completed else { throw CaptureFailure(code: 7, message: writer.error?.localizedDescription ?? "Cannot finalize movie") }
    }
}

// Only .complete supplies fresh pixels. Idle gaps may reuse the last valid frame;
// blank/suspended frames invalidate the run instead of extending stale video.
final class Frames: NSObject, SCStreamOutput, SCStreamDelegate {
    let lock = NSLock()
    let context = CIContext()
    var latest: CGImage?
    var serial = 0
    var statuses: [String: Int] = [:]
    var failure: CaptureFailure?
    var initialRect: CGRect?
    var initialSize: CGSize?
    var initialContentScale: Double?

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        lock.lock(); defer { lock.unlock() }
        failure = CaptureFailure(code: 7, message: "Stream stopped: \(error.localizedDescription). Use step.")
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen else { return }
        lock.lock(); defer { lock.unlock() }
        guard let rows = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let info = rows.first, let raw = info[.status] as? Int,
              let status = SCFrameStatus(rawValue: raw) else {
            failure = CaptureFailure(code: 7, message: "Stream frame has no status; use step."); return
        }
        let names: [SCFrameStatus: String] = [.complete: "complete", .idle: "idle", .started: "started", .blank: "blank", .suspended: "suspended", .stopped: "stopped"]
        statuses[names[status] ?? "unknown", default: 0] += 1
        switch status {
        case .complete:
            guard sample.isValid, let buffer = sample.imageBuffer else {
                failure = CaptureFailure(code: 7, message: "Complete frame has no valid pixels; use step."); return
            }
            let size = CGSize(width: CVPixelBufferGetWidth(buffer), height: CVPixelBufferGetHeight(buffer))
            if let old = initialSize, old != size {
                failure = CaptureFailure(code: 6, message: "Stream size changed. Wait for the window's intended size and retry.", stageSpoiled: stageManager() == true); return
            }
            initialSize = size
            if let rectInfo = info[.contentRect] as? [String: Any], let rect = CGRect(dictionaryRepresentation: rectInfo as CFDictionary) {
                if let old = initialRect, abs(old.width - rect.width) > 1 || abs(old.height - rect.height) > 1 {
                    failure = CaptureFailure(code: 6, message: "Stream content rectangle changed size; wait for the intended window size and retry.", stageSpoiled: stageManager() == true); return
                }
                initialRect = rect
            }
            if let scale = info[.contentScale] as? Double {
                if thumbnailSignal(contentScale: scale) {
                    failure = CaptureFailure(code: 6, message: "ScreenCaptureKit reports thumbnail contentScale below 1; these are Stage Manager thumbnail pixels.", stageSpoiled: true); return
                }
                if let old = initialContentScale, scale < old * 0.85 {
                    failure = CaptureFailure(code: 6, message: "Stream content scale shrank; wait for the intended window size and retry.", stageSpoiled: stageManager() == true); return
                }
                initialContentScale = initialContentScale ?? scale
            }
            let ci = CIImage(cvPixelBuffer: buffer)
            guard let image = context.createCGImage(ci, from: ci.extent) else {
                failure = CaptureFailure(code: 7, message: "Cannot read stream pixels; use step."); return
            }
            latest = image; serial += 1
        case .idle: break
        case .started: break
        case .blank, .suspended, .stopped:
            failure = CaptureFailure(code: 6, message: "Stream has no available backing frames. Wait for the window to be visible in the current Space.")
        @unknown default:
            failure = CaptureFailure(code: 7, message: "Unknown stream frame status; use step.")
        }
    }

    func snapshot() throws -> (CGImage?, Int, [String: Int]) {
        lock.lock(); defer { lock.unlock() }
        if let error = failure { throw error }
        return (latest, serial, statuses)
    }

    func diagnostics() -> (Int, [String: Int]) {
        lock.lock(); defer { lock.unlock() }
        return (serial, statuses)
    }
}

@MainActor
func record(_ window: SCWindow, path: String, seconds: Double, fps: Int32, result: inout [String: Any]) async throws {
    try assertAvailable(window)
    let (filter, config) = try filterConfig(window)
    // H.264 needs even dimensions. Expanding the writer canvas preserves all source pixels.
    config.minimumFrameInterval = CMTime(value: 1, timescale: fps)
    config.pixelFormat = kCVPixelFormatType_32BGRA
    let frames = Frames()
    try requireGrant()
    let stream = SCStream(filter: filter, configuration: config, delegate: frames)
    try requireGrant()
    try stream.addStreamOutput(frames, type: .screen, sampleHandlerQueue: DispatchQueue(label: "capture-window.frames"))
    try requireGrant()
    try await stream.startCapture()
    do {
        let deadline = Date().addingTimeInterval(3)
        var first: CGImage?
        while first == nil && Date() < deadline {
            first = try frames.snapshot().0
            if first == nil { try await Task.sleep(nanoseconds: 20_000_000) }
        }
        guard let initial = first else {
            try assertAvailable(window)
            throw CaptureFailure(code: 7, message: "Window metadata is available but the stream supplied no complete frames; use shot or step for static content.")
        }
        let movie = try MovieWriter(path: path, width: initial.width, height: initial.height, fps: fps)
        let clock = ContinuousClock()
        let start = clock.now
        let count = Int(ceil(seconds * Double(fps)))
        var hashes = Set<Data>()
        var lastSerial = -1
        var repeated = 0
        var minimum = Double.infinity
        var blankFrames: [Bool] = []
        var observed: [[String: Any]] = []
        var firstPixels: [UInt8]?
        var maximumDelta = 0
        var firstFullPixels: [UInt8]?
        var meaningfulMotion = false
        for i in 0..<count {
            try await clock.sleep(until: start.advanced(by: .seconds(Double(i) / Double(fps))))
            try assertAvailable(window)
            let (optional, serial, _) = try frames.snapshot()
            guard let image = optional else { throw CaptureFailure(code: 7, message: "Live frame disappeared; use step.") }
            let value = spread(image)
            let fullPixels = fullGrayPixels(image)
            blankFrames.append(Int(fullPixels.max() ?? 0) - Int(fullPixels.min() ?? 0) <= 2)
            if let first = firstFullPixels {
                if !meaningfulMotion { meaningfulMotion = meaningfulPixelMotion(first, fullPixels) }
            } else { firstFullPixels = fullPixels }
            if let app = front(), (observed.last?["id"] as? String) != (app["id"] as? String) {
                observed.append(app)
                result["foreground_observed"] = observed
            }
            minimum = min(minimum, value)
            let sampled = grayPixels(image)
            if let first = firstPixels { maximumDelta = max(maximumDelta, zip(first, sampled).map { abs(Int($0) - Int($1)) }.max() ?? 0) }
            else { firstPixels = sampled }
            hashes.insert(Data(SHA256.hash(data: Data(fullPixels))))
            if serial == lastSerial { repeated += 1 }
            lastSerial = serial
            try await movie.append(image)
            let elapsed = Double(start.duration(to: clock.now).components.seconds) + Double(start.duration(to: clock.now).components.attoseconds) / 1e18
            if recordingLate(elapsed, requested: seconds, fps: Double(fps)) {
                throw CaptureFailure(code: 7, message: "Recorder cannot keep real time at this size/rate. Reduce fps or use checkpoint step capture.")
            }
            if elapsed >= 3 && !meaningfulMotion {
                throw CaptureFailure(code: 7, message: "No changing source frames in the first three seconds; use step for static content.")
            }
        }
        let (_, complete, statuses) = try frames.snapshot()
        result["stream_status_counts"] = statuses
        result["complete_frames"] = complete
        result["distinct_frames"] = hashes.count
        result["repeated_frames"] = repeated
        result["max_source_sample_delta"] = maximumDelta
        result["meaningful_source_change"] = meaningfulMotion
        result["foreground_observed"] = observed
        guard !sustainedBlank(blankFrames, fps: Int(fps)) else {
            throw CaptureFailure(code: 6, message: "Source frames are uniformly blank for a sustained interval; wait for app content.", stageSpoiled: stageManager() == true)
        }
        guard hashes.count >= 2 && meaningfulMotion else {
            throw CaptureFailure(code: 7, message: "No changing complete frames: static or unavailable window. Idle gaps alone cannot establish a live movie. Use step.")
        }
        try await clock.sleep(until: start.advanced(by: .seconds(seconds)))
        let captured = start.duration(to: clock.now).components
        result["capture_elapsed_seconds"] = Double(captured.seconds) + Double(captured.attoseconds) / 1e18
        try requireGrant()
        try await stream.stopCapture()
        try await movie.finish()
        result.merge(try await verifyMovie(path, changing: true)) { _, new in new }
        result["frame_spread"] = minimum
        result["pixel_size"] = [movie.width, movie.height]
        result["frame_count"] = count
        result["fps"] = fps
        result["duration_seconds"] = Double(count) / Double(fps)
        result["backend"] = "ScreenCaptureKit complete frames + AVAssetWriter idle repeats"
    } catch {
        let diagnostic = frames.diagnostics()
        result["stream_status_counts"] = diagnostic.1; result["complete_frames"] = diagnostic.0
        if CGPreflightScreenCaptureAccess() { try? await stream.stopCapture() }
        try assertAvailable(window)
        throw error
    }
}

#if !CAPTURE_WINDOW_TESTING
@main struct Capture {
    @MainActor static func main() async {
        NSApplication.shared.setActivationPolicy(.prohibited)
        let args = Array(CommandLine.arguments.dropFirst())
        var result: [String: Any] = ["stage_manager_enabled": stageManager() as Any? ?? NSNull(), "helper_process_id": String(ProcessInfo.processInfo.processIdentifier)]
        do {
            switch args.first {
            case "front": result["front"] = front() as Any? ?? NSNull()
            case "bundle":
                guard args.count == 2, let pid = Int32(args[1]), let app = NSRunningApplication(processIdentifier: pid), let path = app.bundleURL?.path else { throw CaptureFailure(code: 4, message: "That app is not running") }
                result["bundle"] = path
                result["hidden"] = app.isHidden
            case "hide":
                guard args.count == 2, let pid = Int32(args[1]), let app = NSRunningApplication(processIdentifier: pid) else { throw CaptureFailure(code: 4, message: "That app is not running") }
                result["hidden"] = app.hide()
            case "check":
                result["screen_recording_grant"] = CGPreflightScreenCaptureAccess()
                result["shot_available"] = CGPreflightScreenCaptureAccess()
                result["record_available"] = CGPreflightScreenCaptureAccess()
                result["encode_available"] = true
                result["tools"] = ["swiftc": true]
                result["backend"] = "ScreenCaptureKit"
                result["record"] = "Complete moving frames required; static/idle-only streams fail with a step alternative."
                result["setup"] = "Xcode command line tools; arrange Screen Recording permission before capture. No permission prompt is requested."
            case "list":
                result["windows"] = try await content().windows.filter {
                    $0.windowLayer == 0 && $0.owningApplication != nil && $0.frame.width > 1 && $0.frame.height > 1
                }.map(metadata)
            case "record":
                guard args.count == 5, let id = UInt32(args[1]), let seconds = Double(args[3]), seconds > 0,
                      let fps = Int32(args[4]), fps > 0 else { throw CaptureFailure(code: 2, message: "Invalid record arguments") }
                guard let window = try await content().windows.first(where: { $0.windowID == id }) else {
                    throw CaptureFailure(code: 4, message: "Window closed before recording")
                }
                if stageManager() == true { result["warnings"] = ["Stage Manager is enabled; compare pixel_size with the intended window size. No setting was changed."] }
                try await record(window, path: args[2], seconds: seconds, fps: fps, result: &result)
            case "encode":
                guard args.count == 4, let fps = Int32(args[3]), fps > 0,
                      let paths = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[1]))) as? [String],
                      let first = paths.first else { throw CaptureFailure(code: 2, message: "Invalid sequence arguments") }
                let initial = try loadImage(first)
                let movie = try MovieWriter(path: args[2], width: initial.width, height: initial.height, fps: fps)
                var minimum = Double.infinity
                for path in paths {
                    let image = try loadImage(path)
                    guard image.width == initial.width && image.height == initial.height else {
                        throw CaptureFailure(code: 7, message: "Sequence dimensions changed; use separate sequences")
                    }
                    let value = spread(image)
                    guard !uniformPixels(image) else { throw CaptureFailure(code: 6, message: "Sequence contains a full-frame uniform image") }
                    minimum = min(minimum, value)
                    try await movie.append(image)
                }
                try await movie.finish()
                result.merge(try await verifyMovie(args[2], changing: false)) { _, new in new }
                result["pixel_size"] = [movie.width, movie.height]
                result["frame_spread"] = minimum
                result["frame_count"] = paths.count
                result["fps"] = fps
                result["duration_seconds"] = Double(paths.count) / Double(fps)
                result["backend"] = "AVAssetWriter checkpoint sequence"
            case "shot":
                guard args.count == 3, let id = UInt32(args[1]) else { throw CaptureFailure(code: 2, message: "Invalid shot arguments") }
                guard let w = try await content().windows.first(where: { $0.windowID == id }) else {
                    throw CaptureFailure(code: 4, message: "Window closed before capture")
                }
                try assertAvailable(w)
                let (f, c) = try filterConfig(w)
                try requireGrant()
                let image = try await SCScreenshotManager.captureImage(contentFilter: f, configuration: c)
                let value = spread(image)
                result["frame_spread"] = value
                result["pixel_size"] = [image.width, image.height]
                guard !uniformPixels(image) else { throw CaptureFailure(code: 6, message: "Uniform pixels across the full frame; wait for app content or use its own export.", stageSpoiled: stageManager() == true) }
                let url = URL(fileURLWithPath: args[2])
                guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
                    throw CaptureFailure(code: 7, message: "Cannot create PNG")
                }
                CGImageDestinationAddImage(dest, image, nil)
                guard CGImageDestinationFinalize(dest) else { throw CaptureFailure(code: 7, message: "Cannot finalize PNG") }
                result["backend"] = "ScreenCaptureKit desktop-independent window"
                if stageManager() == true { result["warnings"] = ["Stage Manager is enabled; inactive windows may be thumbnails. Inspect pixel_size and the image."] }
            default: throw CaptureFailure(code: 2, message: "Unknown backend verb")
            }
        } catch let error as CaptureFailure {
            result["error"] = error.message; result["exit_code"] = error.code
            result["stage_manager_spoiled"] = error.stageSpoiled
        } catch {
            if thumbnailSignal(errorCode: (error as NSError).code) {
                result["error"] = "ScreenCaptureKit returned -3811: the measured Stage Manager thumbnail signal."
                result["exit_code"] = 6
                result["stage_manager_spoiled"] = true
            } else {
                result["error"] = error.localizedDescription; result["exit_code"] = 7
            }
        }
        let data = try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        print(String(data: data, encoding: .utf8)!)
    }
}
#endif
