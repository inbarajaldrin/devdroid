// Shows the text in a file as a QR code and follows the file as it changes.
// A line of "done" closes the window; "failed <reason>" shows the reason.
// A web address gets a Copy button and, unless --code-only, a one-line shortened form.
//
// Usage: qr-window <file> [--title TEXT] [--hint TEXT] [--code-only]
import AppKit
import CoreImage

final class Controller: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let file: String, titleText: String, hint: String, codeOnly: Bool
    let image = NSImageView(), status = NSTextField(wrappingLabelWithString: ""), copy = NSButton()
    var window: NSWindow!, shown: String?
    init(file: String, title: String, hint: String, codeOnly: Bool) { self.file = file; titleText = title; self.hint = hint; self.codeOnly = codeOnly }

    func applicationDidFinishLaunching(_ note: Notification) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 360, height: 450), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = titleText; window.delegate = self; window.level = .floating; window.isReleasedWhenClosed = false
        let stack = NSStackView(views: [image, status, copy])
        stack.orientation = .vertical; stack.spacing = 14; stack.edgeInsets = NSEdgeInsets(top: 20, left: 20, bottom: 20, right: 20)
        image.imageScaling = .scaleProportionallyUpOrDown
        image.widthAnchor.constraint(equalToConstant: 300).isActive = true
        image.heightAnchor.constraint(equalToConstant: 300).isActive = true
        status.alignment = .center; status.isSelectable = true; status.preferredMaxLayoutWidth = 320
        copy.title = "Copy link"; copy.bezelStyle = .rounded; copy.target = self; copy.action = #selector(copyLink); copy.isHidden = true
        window.contentView = stack
        window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in self?.refresh() }
        refresh()
    }

    func refresh() {
        guard let line = try? String(contentsOfFile: file, encoding: .utf8).split(whereSeparator: \.isNewline).first.map(String.init) else {
            if shown != nil { NSApp.terminate(nil) } else { status.stringValue = "Waiting…" }
            return
        }
        guard line != shown else { return }
        shown = line
        if line == "done" {
            image.image = NSImage(systemSymbolName: "checkmark.circle.fill", accessibilityDescription: "Done")
            image.contentTintColor = .systemGreen; status.stringValue = "Done"; copy.isHidden = true
            try? FileManager.default.removeItem(atPath: file)
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { NSApp.terminate(nil) }
        } else if line.hasPrefix("failed") {
            image.image = NSImage(systemSymbolName: "xmark.octagon.fill", accessibilityDescription: "Failed")
            image.contentTintColor = .systemRed; status.stringValue = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces); copy.isHidden = true
        } else {
            let web = !codeOnly && (line.hasPrefix("https://") || line.hasPrefix("http://"))
            image.image = qr(line); image.contentTintColor = nil
            status.stringValue = web ? (line.count > 60 ? String(line.prefix(40)) + "…" + String(line.suffix(12)) : line) : hint; copy.isHidden = !web
        }
    }

    func qr(_ text: String) -> NSImage? {
        guard let filter = CIFilter(name: "CIQRCodeGenerator") else { return nil }
        filter.setValue(Data(text.utf8), forKey: "inputMessage"); filter.setValue("M", forKey: "inputCorrectionLevel")
        guard let code = filter.outputImage else { return nil }
        let scaled = code.transformed(by: CGAffineTransform(scaleX: 10, y: 10))
        guard let cg = CIContext().createCGImage(scaled, from: scaled.extent) else { return nil }
        return NSImage(cgImage: cg, size: NSSize(width: 300, height: 300))
    }

    @objc func copyLink() {
        guard let shown else { return }
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(shown, forType: .string); copy.title = "Copied"
    }

    func windowWillClose(_ note: Notification) { NSApp.terminate(nil) }
}

@main enum Main {
    static func main() {
        var rest = Array(CommandLine.arguments.dropFirst()), title = "Scan this code", hint = ""
        let codeOnly = rest.contains("--code-only"); rest.removeAll { $0 == "--code-only" }
        func option(_ name: String) -> String? {
            guard let i = rest.firstIndex(of: name), i + 1 < rest.count else { return nil }
            defer { rest.removeSubrange(i...(i + 1)) }; return rest[i + 1]
        }
        title = option("--title") ?? title; hint = option("--hint") ?? hint
        guard rest.count == 1 else { FileHandle.standardError.write(Data("usage: qr-window <file> [--title TEXT] [--hint TEXT] [--code-only]\n".utf8)); exit(2) }
        let controller = Controller(file: rest[0], title: title, hint: hint, codeOnly: codeOnly)
        let app = NSApplication.shared; app.delegate = controller; app.setActivationPolicy(.accessory); app.run()
    }
}
