// Owned test fixture. It never becomes key and never activates the application.
import AppKit

final class Pattern: NSView {
    var tick = 0
    var ready = false
    override func draw(_ rect: NSRect) {
        defer { if !ready { ready = true; print("ready"); fflush(stdout) } }
        if CommandLine.arguments.contains("--dark") {
            NSColor.black.setFill(); bounds.fill()
            ("Sparse fixture" as NSString).draw(at: NSPoint(x: 10, y: 10), withAttributes: [.foregroundColor: NSColor.darkGray])
            return
        }
        if CommandLine.arguments.contains("--blink-dark") && (tick / 7) % 40 < 4 {
            NSColor.black.setFill(); bounds.fill(); return
        }
        NSColor.white.setFill(); bounds.fill()
        NSColor.black.setFill(); NSRect(x: 10, y: 10, width: 100, height: 180).fill()
        NSColor.systemBlue.setFill()
        NSRect(x: 120 + CGFloat(tick % 100), y: 30, width: 70, height: 100).fill()
        ("Capture fixture" as NSString).draw(at: NSPoint(x: 140, y: 180), withAttributes: [.foregroundColor: NSColor.black])
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let borderless = CommandLine.arguments.contains("--static") || CommandLine.arguments.contains("--dark") || CommandLine.arguments.contains("--blink-dark")
let size = CommandLine.arguments.contains("--dark") ? NSSize(width: 1200, height: 800) : NSSize(width: 423, height: 267)
let window = NSWindow(contentRect: NSRect(origin: NSPoint(x: 180, y: 180), size: size),
                      styleMask: borderless ? [.borderless] : [.titled, .closable], backing: .buffered, defer: false)
window.title = CommandLine.arguments.dropFirst().first ?? "capture-fixture"
let view = Pattern(frame: window.contentView!.bounds)
window.contentView = view
var hiddenWindow: NSWindow?
if CommandLine.arguments.contains("--hidden-large") {
    hiddenWindow = NSWindow(contentRect: NSRect(x: 20, y: 20, width: 900, height: 700), styleMask: [.titled, .miniaturizable], backing: .buffered, defer: false)
    hiddenWindow!.title = window.title + "-hidden"
    hiddenWindow!.orderBack(nil)
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { hiddenWindow!.miniaturize(nil) }
}
window.orderBack(nil)
let timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
    if !CommandLine.arguments.contains("--static") { view.tick += 7; view.needsDisplay = true }
}
if CommandLine.arguments.contains("--hide") {
    Timer.scheduledTimer(withTimeInterval: 1.2, repeats: false) { _ in window.orderOut(nil) }
}
if CommandLine.arguments.contains("--shrink") {
    Timer.scheduledTimer(withTimeInterval: 1.2, repeats: false) { _ in window.setContentSize(NSSize(width: 123, height: 97)) }
}
if CommandLine.arguments.contains("--close") {
    Timer.scheduledTimer(withTimeInterval: 1.2, repeats: false) { _ in app.terminate(nil) }
}
print(window.windowNumber)
fflush(stdout)
app.run()
