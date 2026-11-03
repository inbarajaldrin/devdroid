// Native policy tests use generated images/metadata, never a real window.
import AppKit
import ScreenCaptureKit

@main struct PolicyTests {
    static func main() throws {
        precondition(thumbnailSignal(errorCode: -3811))
        precondition(thumbnailSignal(contentScale: 0.1378))
        precondition(thumbnailSignal(contentScale: 0.99))
        precondition(!thumbnailSignal(contentScale: 1))
        precondition(geometrySpoiled(CGSize(width: 400, height: 300), CGSize(width: 100, height: 75)))
        precondition(!geometrySpoiled(CGSize(width: 40, height: 30), CGSize(width: 40, height: 30)))
        precondition(!sustainedBlank([false, false, true, false, false], fps: 5))
        precondition(sustainedBlank([true, true, true], fps: 5))
        precondition(recordingLate(4, requested: 2, fps: 60))
        precondition(!meaningfulPixelMotion([0, 10, 200], [1, 9, 201]))
        precondition(meaningfulPixelMotion([0, 10, 200], [0, 14, 200]))
        let ctx = CGContext(data: nil, width: 1200, height: 800, bitsPerComponent: 8,
                            bytesPerRow: 1200, space: CGColorSpaceCreateDeviceGray(), bitmapInfo: 0)!
        ctx.setFillColor(CGColor(gray: 0, alpha: 1)); ctx.fill(CGRect(x: 0, y: 0, width: 1200, height: 800))
        precondition(uniformPixels(ctx.makeImage()!))
        ctx.setFillColor(CGColor(gray: 0.25, alpha: 1)); ctx.fill(CGRect(x: 10, y: 10, width: 2, height: 2))
        precondition(!uniformPixels(ctx.makeImage()!))
        print("Native policy tests passed")
    }
}
