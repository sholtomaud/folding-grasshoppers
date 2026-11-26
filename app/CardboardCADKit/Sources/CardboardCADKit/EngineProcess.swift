import Foundation

public struct LaunchSpec: Equatable, Sendable {
    public var executableURL: URL
    public var arguments: [String]
    public var environment: [String: String]
    public var currentDirectoryURL: URL?

    public init(executableURL: URL, arguments: [String], environment: [String: String], currentDirectoryURL: URL? = nil) {
        self.executableURL = executableURL
        self.arguments = arguments
        self.environment = environment
        self.currentDirectoryURL = currentDirectoryURL
    }
}

/// The child process, behind a protocol so EngineManager can be tested with fakes.
/// Callbacks may arrive on any thread.
public protocol EngineProcess: AnyObject {
    var isRunning: Bool { get }
    var processIdentifier: Int32 { get }
    func launch(
        _ spec: LaunchSpec,
        onStdout: @escaping @Sendable (String) -> Void,
        onStderr: @escaping @Sendable (String) -> Void,
        onExit: @escaping @Sendable (Int32) -> Void
    ) throws
    /// SIGTERM
    func terminate()
    /// SIGKILL
    func kill()
}

/// Splits a byte stream into lines. Not thread-safe; one per pipe.
final class LineBuffer {
    private var pending = Data()

    func feed(_ data: Data) -> [String] {
        pending.append(data)
        var lines: [String] = []
        while let nl = pending.firstIndex(of: 0x0A) {
            let lineData = pending[pending.startIndex..<nl]
            pending.removeSubrange(pending.startIndex...nl)
            lines.append(String(decoding: lineData, as: UTF8.self))
        }
        return lines
    }

    func flush() -> String? {
        guard !pending.isEmpty else { return nil }
        defer { pending.removeAll() }
        return String(decoding: pending, as: UTF8.self)
    }
}

/// `Foundation.Process` with stdout/stderr pipes delivered line by line.
public final class FoundationEngineProcess: EngineProcess, @unchecked Sendable {
    private let process = Process()
    private let queue = DispatchQueue(label: "engine.process.io")

    public init() {}

    public var isRunning: Bool { process.isRunning }
    public var processIdentifier: Int32 { process.processIdentifier }

    public func launch(
        _ spec: LaunchSpec,
        onStdout: @escaping @Sendable (String) -> Void,
        onStderr: @escaping @Sendable (String) -> Void,
        onExit: @escaping @Sendable (Int32) -> Void
    ) throws {
        process.executableURL = spec.executableURL
        process.arguments = spec.arguments
        process.environment = spec.environment // explicit; nothing inherited
        if let cwd = spec.currentDirectoryURL { process.currentDirectoryURL = cwd }

        let out = Pipe(), err = Pipe()
        process.standardOutput = out
        process.standardError = err
        process.standardInput = FileHandle.nullDevice
        Self.attach(out.fileHandleForReading, queue: queue, handler: onStdout)
        Self.attach(err.fileHandleForReading, queue: queue, handler: onStderr)

        process.terminationHandler = { [queue] p in
            let code = p.terminationReason == .uncaughtSignal ? -p.terminationStatus : p.terminationStatus
            // Let pending output drain before reporting the exit.
            queue.asyncAfter(deadline: .now() + 0.05) {
                out.fileHandleForReading.readabilityHandler = nil
                err.fileHandleForReading.readabilityHandler = nil
                onExit(code)
            }
        }
        try process.run()
    }

    private static func attach(_ handle: FileHandle, queue: DispatchQueue, handler: @escaping @Sendable (String) -> Void) {
        let buffer = LineBuffer()
        handle.readabilityHandler = { h in
            let data = h.availableData
            queue.async {
                if data.isEmpty {
                    h.readabilityHandler = nil
                    if let rest = buffer.flush() { handler(rest) }
                    return
                }
                for line in buffer.feed(data) { handler(line) }
            }
        }
    }

    public func terminate() {
        if process.isRunning { process.terminate() }
    }

    public func kill() {
        if process.isRunning { Darwin.kill(process.processIdentifier, SIGKILL) }
    }
}
