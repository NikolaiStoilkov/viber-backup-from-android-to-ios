import AppKit
import SwiftUI

@main
struct ViberTransferApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = TransferModel()

    init() {
        // If the engine dies, writing to its stdin must fail with an error, not kill the app.
        signal(SIGPIPE, SIG_IGN)
    }

    var body: some Scene {
        Window("Viber Transfer", id: "main") {
            ContentView()
                .environmentObject(model)
                .onAppear {
                    delegate.model = model
                    Snapshot.startIfRequested(model: model)
                }
        }
        .commands {
            CommandGroup(replacing: .newItem) {}
        }

        Settings {
            SettingsView().environmentObject(model)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: TransferModel?

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    @MainActor
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model else { return .terminateNow }
        if model.running && !Snapshot.active {
            let alert = NSAlert()
            alert.messageText = "A transfer is still running"
            alert.informativeText = "Quitting cancels it. Temporary files on the phone are removed first; " +
                "Viber's own data is never changed."
            alert.addButton(withTitle: "Cancel Transfer and Quit")
            alert.addButton(withTitle: "Keep Running")
            if alert.runModal() != .alertFirstButtonReturn { return .terminateCancel }
        }
        // Let the engine finish its cleanup before the app goes away.
        model.shutdown { sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }
}

/// Development check used by build scripts: with VT_SNAPSHOT=<file.png> the app saves an image
/// of its window (after a finished transfer if VT_SNAPSHOT_AUTOSTART=1, otherwise after a few
/// seconds) and quits. It has no effect in normal use.
@MainActor
enum Snapshot {
    static var active: Bool { ProcessInfo.processInfo.environment["VT_SNAPSHOT"] != nil }

    static func startIfRequested(model: TransferModel) {
        let env = ProcessInfo.processInfo.environment
        guard let path = env["VT_SNAPSHOT"] else { return }
        let autostart = env["VT_SNAPSHOT_AUTOSTART"] == "1"
        Task {
            try? await Task.sleep(nanoseconds: 3_000_000_000)  // first status poll
            if autostart {
                model.start()
                for _ in 0..<120 where model.job?.result == nil {
                    try? await Task.sleep(nanoseconds: 500_000_000)
                }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
            save(to: URL(fileURLWithPath: path))
            NSApp.terminate(nil)
        }
    }

    private static func save(to url: URL) {
        // Render the window's layer tree (cacheDisplay misses SwiftUI's layer-backed text).
        guard let window = NSApp.windows.first(where: { $0.isVisible }),
              let layer = window.contentView?.superview?.layer ?? window.contentView?.layer else { return }
        let scale = window.backingScaleFactor
        let size = layer.bounds.size
        guard let context = CGContext(data: nil, width: Int(size.width * scale), height: Int(size.height * scale),
                                      bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return }
        context.scaleBy(x: scale, y: scale)
        layer.render(in: context)
        guard let image = context.makeImage() else { return }
        try? NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])?.write(to: url)
    }
}
