import Foundation
@testable import CardboardCADKit

// MARK: - Fake process

/// What a fake engine process does when launched.
enum FakeBehavior {
    /// Prints ENGINE_READY on stdout.
    case readyLine
    /// Prints nothing; readiness must come from /health.
    case silent
    /// Exits immediately with this code (e.g. 98 = port in use).
    case exitImmediately(Int32)
}

final class FakeEngineProcess: EngineProcess {
    let behavior: FakeBehavior
    var ignoresTerminate = false
    private(set) var spec: LaunchSpec?
    private(set) var terminateCalls = 0
    private(set) var killCalls = 0
    private(set) var isRunning = false
    let processIdentifier: Int32

    private var onStdout: (@Sendable (String) -> Void)?
    private var onStderr: (@Sendable (String) -> Void)?
    private var onExit: (@Sendable (Int32) -> Void)?

    init(behavior: FakeBehavior, pid: Int32) {
        self.behavior = behavior
        self.processIdentifier = pid
    }

    func launch(
        _ spec: LaunchSpec,
        onStdout: @escaping @Sendable (String) -> Void,
        onStderr: @escaping @Sendable (String) -> Void,
        onExit: @escaping @Sendable (Int32) -> Void
    ) throws {
        self.spec = spec
        self.onStdout = onStdout
        self.onStderr = onStderr
        self.onExit = onExit
        isRunning = true
        switch behavior {
        case .readyLine:
            onStdout("some startup noise")
            onStdout(#"ENGINE_READY {"port":5001,"version":"0.1.0"}"#)
        case .silent:
            break
        case .exitImmediately(let code):
            onStderr(#"{"error":"port_in_use","port":5001}"#)
            exit(code)
        }
    }

    func terminate() {
        terminateCalls += 1
        if !ignoresTerminate { exit(0) }
    }

    func kill() {
        killCalls += 1
        exit(-9)
    }

    /// Simulate the process dying (crash, `kill -9`, clean exit).
    func exit(_ code: Int32) {
        guard isRunning else { return }
        isRunning = false
        onExit?(code)
    }

    func emitStdout(_ line: String) { onStdout?(line) }
}

/// Hands out fake processes, one per launch, following a behaviour script.
@MainActor
final class FakeProcessFactory {
    var behaviors: [FakeBehavior]
    var defaultBehavior: FakeBehavior
    var ignoreTerminate = false
    private(set) var launched: [FakeEngineProcess] = []

    init(_ behaviors: [FakeBehavior] = [], default defaultBehavior: FakeBehavior = .readyLine) {
        self.behaviors = behaviors
        self.defaultBehavior = defaultBehavior
    }

    func make() -> EngineProcess {
        let behavior = behaviors.isEmpty ? defaultBehavior : behaviors.removeFirst()
        let p = FakeEngineProcess(behavior: behavior, pid: Int32(1000 + launched.count))
        p.ignoresTerminate = ignoreTerminate
        launched.append(p)
        return p
    }

    var current: FakeEngineProcess? { launched.last }
}

// MARK: - Fake client

struct FakeError: Error {}

final class FakeEngineClient: EngineClientProtocol {
    /// Results returned by successive health() calls; the last one repeats.
    var healthResults: [Bool] = [false]
    private(set) var healthCalls = 0
    private(set) var shutdownTokens: [String] = []
    /// Called when /shutdown is POSTed (e.g. to make the fake process exit).
    var onShutdown: (() -> Void)?
    var shutdownStatus = 202
    var versionInfo = VersionInfo(engine: "0.1.0", python: "3.12.13", packages: ["flask": "3.1.3"])

    func health() async throws -> HealthResponse {
        let index = min(healthCalls, healthResults.count - 1)
        healthCalls += 1
        if healthResults[index] { return HealthResponse(status: "healthy") }
        throw FakeError()
    }

    func version() async throws -> VersionInfo { versionInfo }

    func cutEstimate(_ input: CutEstimateInput) async throws -> CutEstimateResult {
        throw FakeError()
    }

    func hello(name: String) async throws -> String { "Hello, \(name)" }

    func shutdown(token: String) async throws -> Int {
        shutdownTokens.append(token)
        onShutdown?()
        return shutdownStatus
    }
}

// MARK: - Virtual clock

/// Sleeps instantly and advances a virtual clock, recording every sleep.
final class FakeSleeper: EngineSleeper {
    private(set) var now: Double = 0
    private(set) var sleeps: [Double] = []

    func sleep(seconds: Double) async throws {
        sleeps.append(seconds)
        now += seconds
        // Let callbacks hopping to the main actor run.
        for _ in 0..<5 { await Task.yield() }
    }

    func advance(_ seconds: Double) { now += seconds }
}

// MARK: - Log sink

final class MemoryLog: LogWriting {
    private(set) var lines: [String] = []
    func append(_ line: String) { lines.append(line) }
}

// MARK: - Helpers

@MainActor
func eventually(
    _ condition: @MainActor () -> Bool,
    timeout: TimeInterval = 2,
    file: StaticString = #filePath,
    line: UInt = #line
) async -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if condition() { return true }
        try? await Task.sleep(nanoseconds: 1_000_000)
    }
    return condition()
}

func testConfig(port: Int = 5001) -> EngineConfig {
    let base = URL(fileURLWithPath: "/tmp/cardboardcad-tests", isDirectory: true)
    return EngineConfig(
        appName: "CardboardCAD",
        port: port,
        interpreterURL: URL(fileURLWithPath: "/Bundle/CardboardCAD.app/Contents/Frameworks/python/bin/python3.12"),
        logsDirectory: base.appendingPathComponent("Logs"),
        dataDirectory: base.appendingPathComponent("Data"),
        cacheDirectory: base.appendingPathComponent("Caches"),
        homeDirectory: URL(fileURLWithPath: "/Users/tester", isDirectory: true),
        temporaryDirectory: URL(fileURLWithPath: "/private/tmp/t", isDirectory: true)
    )
}
