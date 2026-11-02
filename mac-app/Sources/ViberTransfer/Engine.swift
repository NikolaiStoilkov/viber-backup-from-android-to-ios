import Foundation

/// Runs the Python backend (`vibertransfer engine`) as a child process and talks to it with
/// one JSON object per line over stdin/stdout. See vibertransfer/engine.py for the commands.
///
/// Where the backend comes from:
/// - Packaged app: Contents/Resources/engine/ViberTransferEngine (built by build-dmg.sh).
/// - Development: `VT_PROJECT_ROOT` (set by run.sh) → `<root>/.venv/bin/python -m vibertransfer engine`.
final class Engine: @unchecked Sendable {
    enum Failure: LocalizedError {
        case notFound
        case stopped
        case remote(String)

        var errorDescription: String? {
            switch self {
            case .notFound: return "The transfer engine is missing from the app. Reinstall Viber Transfer."
            case .stopped: return "The transfer engine stopped unexpectedly. Details are in ~/Library/Logs/ViberTransfer/engine.log."
            case .remote(let message): return message
            }
        }
    }

    private struct Header: Decodable { let id: Int? }
    private struct Envelope<T: Decodable>: Decodable {
        let ok: Bool
        let result: T?
        let error: String?
    }

    static let logURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/ViberTransfer/engine.log")

    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private let lock = NSLock()
    private let writeLock = NSLock()
    private var nextID = 1
    private var waiting: [Int: CheckedContinuation<Data, Error>] = [:]
    private var buffer = Data()
    private var stopped = false
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    init() throws {
        let env = ProcessInfo.processInfo.environment
        if let root = env["VT_PROJECT_ROOT"] {
            let venvPython = URL(fileURLWithPath: root).appendingPathComponent(".venv/bin/python")
            process.executableURL = FileManager.default.isExecutableFile(atPath: venvPython.path)
                ? venvPython : URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = ["-m", "vibertransfer", "engine"]
            process.currentDirectoryURL = URL(fileURLWithPath: root)
        } else if let bundled = Bundle.main.resourceURL?.appendingPathComponent("engine/ViberTransferEngine"),
                  FileManager.default.isExecutableFile(atPath: bundled.path) {
            process.executableURL = bundled
            process.arguments = ["engine"]
        } else {
            throw Failure.notFound
        }
        var childEnv = env
        childEnv["PYTHONIOENCODING"] = "utf-8"  // apps started from Finder get no locale
        childEnv["PYTHONUNBUFFERED"] = "1"
        process.environment = childEnv

        // The engine's own log (everything it prints) goes to a file.
        let fm = FileManager.default
        try? fm.createDirectory(at: Engine.logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !fm.fileExists(atPath: Engine.logURL.path) { fm.createFile(atPath: Engine.logURL.path, contents: nil) }
        let log = try FileHandle(forWritingTo: Engine.logURL)
        log.seekToEndOfFile()

        process.standardInput = input
        process.standardOutput = output
        process.standardError = log
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.received(handle.availableData)
        }
        process.terminationHandler = { [weak self] _ in self?.failAll() }
        try process.run()
    }

    /// Send a command and decode its result.
    func call<T: Decodable>(_ cmd: String, _ params: [String: Any] = [:], as type: T.Type = T.self) async throws -> T {
        let line: Data = try await withCheckedThrowingContinuation { continuation in
            lock.lock()
            if stopped {
                lock.unlock()
                continuation.resume(throwing: Failure.stopped)
                return
            }
            let id = nextID
            nextID += 1
            waiting[id] = continuation
            lock.unlock()

            var request = params
            request["id"] = id
            request["cmd"] = cmd
            do {
                var data = try JSONSerialization.data(withJSONObject: request)
                data.append(0x0A)
                writeLock.lock()
                defer { writeLock.unlock() }
                try input.fileHandleForWriting.write(contentsOf: data)
            } catch {
                lock.lock()
                let pending = waiting.removeValue(forKey: id)
                lock.unlock()
                pending?.resume(throwing: error)
            }
        }
        let envelope = try decoder.decode(Envelope<T>.self, from: line)
        guard envelope.ok, let result = envelope.result else {
            throw Failure.remote(envelope.error ?? "Unknown error")
        }
        return result
    }

    /// Close the engine's stdin. It cancels a running transfer, cleans up and exits.
    /// Blocks until it has exited (or `timeout` passes), so call it off the main thread.
    func shutdown(timeout: TimeInterval = 150) {
        try? input.fileHandleForWriting.close()
        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.2)
        }
        if process.isRunning { process.terminate() }
    }

    private func received(_ data: Data) {
        guard !data.isEmpty else { return }  // end of stream: terminationHandler takes care of it
        var lines: [Data] = []
        lock.lock()
        buffer.append(data)
        while let newline = buffer.firstIndex(of: 0x0A) {
            lines.append(Data(buffer[buffer.startIndex..<newline]))
            buffer.removeSubrange(buffer.startIndex...newline)
        }
        lock.unlock()
        for line in lines {
            // Lines without an id (the "ready" event) need no reply.
            guard let id = (try? decoder.decode(Header.self, from: line))?.id else { continue }
            lock.lock()
            let continuation = waiting.removeValue(forKey: id)
            lock.unlock()
            continuation?.resume(returning: line)
        }
    }

    private func failAll() {
        lock.lock()
        stopped = true
        let pending = waiting
        waiting.removeAll()
        lock.unlock()
        for continuation in pending.values { continuation.resume(throwing: Failure.stopped) }
    }
}
