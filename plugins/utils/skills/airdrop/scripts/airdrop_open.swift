// Reference: https://github.com/su-mt/tty-airdrop  https://github.com/vldmrkl/airdrop-cli
//
// airdrop_open: opens the macOS AirDrop picker pre-loaded with one or more files.
//
// NSSharingService(.sendViaAirDrop) presents the system AirDrop picker; Apple gives no
// API to choose a recipient, so the person clicks the device.
//
// The process stays alive on the AppKit run loop while the picker is open. When the
// transfer completes, `didShareItems` fires, we print "SHARED" and exit 0. On error
// we print "FAILED: ..." to stderr and exit 1. A caller-side timeout handles the
// "user never accepted / wrong recipient" case (no delegate callback fires for that).

import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSSharingServiceDelegate {
    let urls: [URL]
    init(urls: [URL]) { self.urls = urls }

    func applicationDidFinishLaunching(_ note: Notification) {
        guard let service = NSSharingService(named: .sendViaAirDrop) else {
            FileHandle.standardError.write("FAILED: AirDrop sharing service unavailable\n".data(using: .utf8)!)
            exit(1)
        }
        service.delegate = self
        // Bring this dockless process forward so the picker takes key focus.
        NSApp.activate(ignoringOtherApps: true)
        service.perform(withItems: urls)
    }

    func sharingService(_ service: NSSharingService, didShareItems items: [Any]) {
        print("SHARED")
        exit(0)
    }

    func sharingService(_ service: NSSharingService,
                        didFailToShareItems items: [Any],
                        error: Error) {
        FileHandle.standardError.write("FAILED: \(error.localizedDescription)\n".data(using: .utf8)!)
        exit(1)
    }
}

let rawArgs = Array(CommandLine.arguments.dropFirst())
guard !rawArgs.isEmpty else {
    FileHandle.standardError.write("usage: airdrop_open <file> [<file> ...]\n".data(using: .utf8)!)
    exit(2)
}

let urls: [URL] = rawArgs.map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) }
for u in urls where !FileManager.default.fileExists(atPath: u.path) {
    FileHandle.standardError.write("FAILED: file not found: \(u.path)\n".data(using: .utf8)!)
    exit(2)
}

let app = NSApplication.shared
let delegate = AppDelegate(urls: urls)
app.delegate = delegate
// .accessory = no Dock icon / menu bar, but windows still appear and take events.
app.setActivationPolicy(.accessory)
app.run()
