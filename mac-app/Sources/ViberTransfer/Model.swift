import AppKit
import Foundation

// MARK: - What the engine sends (snake_case keys are converted by the decoder)

struct DeviceStatus: Decodable, Equatable {
    var state: String
    var label: String?
    var message: String?
    var model: String?
    var viber: String?
    var name: String?
    var iosVersion: String?
}

struct ToolInfo: Decodable, Identifiable {
    var name: String
    var path: String?
    var install: String?
    var id: String { name }
}

struct EngineSettings: Decodable, Equatable {
    var adbPath = ""
    var apkPath = ""
    var androidSerial = ""
    var iosTargetApp = ""
    var skipIos = false

    var asParams: [String: Any] {
        ["adb_path": adbPath, "apk_path": apkPath, "android_serial": androidSerial,
         "ios_target_app": iosTargetApp, "skip_ios": skipIos]
    }
}

struct StatusResponse: Decodable {
    struct Apk: Decodable { var path: String; var exists: Bool }
    var android: DeviceStatus
    var iphone: DeviceStatus
    var tools: [ToolInfo]
    var apk: Apk
    var settings: EngineSettings
}

struct Choice: Decodable, Equatable, Identifiable {
    struct Option: Decodable, Hashable { var value: String; var label: String }
    var question: String
    var options: [Option]
    var id: String { question }
}

struct Summary: Decodable {
    struct Folder: Decodable { var files: Int; var size: String }
    struct Verified: Decodable { var verifiedFiles: Int?; var abSha256: String? }
    struct IOSResult: Decodable { var status: String; var detail: String }
    var backupDir: String
    var method: String
    var device: String
    var viberVersion: String?
    var files: Int
    var size: String
    var folders: [String: Folder]
    var conversations: Int?
    var messages: Int?
    var conversionError: String?
    var verified: Verified
    var ios: IOSResult
}

struct JobState: Decodable {
    var running: Bool
    var step: String?
    var stepIndex: Int?
    var steps: [String]?
    var progress: Double?
    var prompt: String?
    var choice: Choice?
    var result: String?
    var error: String?
    var summary: Summary?
    var skipped: [String]?
    var log: [String]
    var logNext: Int
}

struct IOSApp: Decodable, Hashable, Identifiable {
    var bundleId: String
    var name: String
    var id: String { bundleId }
}

private struct IOSAppList: Decodable { var apps: [IOSApp]; var error: String? }
private struct Empty: Decodable {}

// MARK: - The app's state, polled from the engine

@MainActor
final class TransferModel: ObservableObject {
    static let defaultSteps = ["Check tools", "Connect Android phone", "Root access on phone",
                               "Copy Viber data on phone", "Pull backup to Mac", "Verify checksums",
                               "Convert chats", "Write manifest", "Copy to iPhone"]

    @Published var status: StatusResponse?
    @Published var job: JobState?
    @Published var log: [String] = []
    @Published var engineError: String?
    @Published var alertMessage: String?
    @Published var iosApps: [IOSApp] = []
    @Published var iosAppsMessage: String?
    @Published var loadingApps = false

    private let engine: Engine?
    private var logFrom = 0
    private var generation = 0  // bumps on Start so a late poll of the previous run is ignored
    private var statusInFlight = false
    private var loops: [Task<Void, Never>] = []

    var running: Bool { job?.running ?? false }
    var steps: [String] { job?.steps ?? Self.defaultSteps }

    init() {
        do {
            engine = try Engine()
        } catch {
            engine = nil
            engineError = error.localizedDescription
        }
        loops.append(Task { [weak self] in
            while !Task.isCancelled {
                await self?.refreshState()
                try? await Task.sleep(nanoseconds: 700_000_000)
            }
        })
        loops.append(Task { [weak self] in
            while !Task.isCancelled {
                if let self, !self.running { await self.refreshStatus() }
                try? await Task.sleep(nanoseconds: 4_000_000_000)
            }
        })
    }

    // MARK: polling

    func refreshStatus() async {
        guard let engine, !statusInFlight else { return }
        statusInFlight = true
        defer { statusInFlight = false }
        do {
            status = try await engine.call("status")
        } catch {
            engineFailed(error)
        }
    }

    private func refreshState() async {
        guard let engine else { return }
        let asked = generation
        do {
            let state: JobState = try await engine.call("state", ["from": logFrom])
            guard asked == generation else { return }
            log.append(contentsOf: state.log)
            logFrom = state.logNext
            job = state
        } catch {
            engineFailed(error)
        }
    }

    private func engineFailed(_ error: Error) {
        if case Engine.Failure.stopped = error {
            engineError = error.localizedDescription
            loops.forEach { $0.cancel() }
        }
    }

    // MARK: actions

    func start() {
        generation += 1
        log = []
        logFrom = 0
        job = nil
        perform("start")
    }

    func cancel() { perform("cancel") }

    func answer(_ value: String) { perform("answer", ["value": value]) }

    func save(_ settings: EngineSettings) {
        perform("settings", ["values": settings.asParams]) { [weak self] in
            Task { await self?.refreshStatus() }
        }
    }

    func uninstallHelper() {
        perform("uninstall_helper", ["confirmed": true]) { [weak self] in
            self?.alertMessage = "The helper app was removed from the Android phone."
        }
    }

    func loadIOSApps() async {
        guard let engine else { return }
        loadingApps = true
        defer { loadingApps = false }
        do {
            let list: IOSAppList = try await engine.call("ios_apps")
            iosApps = list.apps
            iosAppsMessage = list.error ?? (list.apps.isEmpty
                ? "No app on the iPhone accepts files over USB. Install a file manager app first." : nil)
        } catch {
            iosAppsMessage = error.localizedDescription
        }
    }

    func revealBackup() {
        guard let dir = job?.summary?.backupDir else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: dir)])
    }

    /// Stop polling, let the engine cancel/clean up, then call `done` on the main thread.
    func shutdown(_ done: @escaping () -> Void) {
        loops.forEach { $0.cancel() }
        guard let engine else { return done() }
        DispatchQueue.global().async {
            engine.shutdown()
            // While termination is pending AppKit runs the main run loop in modal-panel mode,
            // where DispatchQueue.main blocks don't run, so schedule on the run loop directly.
            RunLoop.main.perform(inModes: [.common, .modalPanel], block: done)
            CFRunLoopWakeUp(CFRunLoopGetMain())
        }
    }

    private func perform(_ cmd: String, _ params: [String: Any] = [:], then: (() -> Void)? = nil) {
        guard let engine else { return }
        Task {
            do {
                let _: Empty = try await engine.call(cmd, params)
                then?()
            } catch {
                alertMessage = error.localizedDescription
            }
        }
    }
}
