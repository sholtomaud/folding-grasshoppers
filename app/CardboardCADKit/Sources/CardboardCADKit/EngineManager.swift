import Foundation
import Observation
import Security

/// Owns the Python engine child process: start, readiness, crash restarts, stop.
///
/// Everything runs on the main actor. Process callbacks arrive on background
/// queues and hop here tagged with a launch `generation`, so events from an
/// old process can never affect a newer one.
@MainActor
@Observable
public final class EngineManager {
    // MARK: tunables (SPEC.md 8.2)
    public static let readinessTimeout: Double = 20
    public static let initialPollDelay: Double = 0.1
    public static let maxPollDelay: Double = 1
    public static let restartBackoff: [Double] = [1, 2, 4]
    public static let restartWindow: Double = 60
    public static let shutdownGrace: Double = 3
    public static let terminateGrace: Double = 2
    public static let killGrace: Double = 1
    /// stopImmediately(): how long to wait after SIGTERM before SIGKILL.
    public static let immediateGrace: Double = 0.5
    public static let exitCodePortInUse: Int32 = 98

    // MARK: observable state
    public private(set) var state: EngineState = .stopped
    public private(set) var config: EngineConfig
    public private(set) var engineVersion: String?

    /// Called on every state transition (handy for tests and logging).
    @ObservationIgnored public var onStateChange: ((EngineState) -> Void)?

    // MARK: dependencies
    @ObservationIgnored private let makeProcess: () -> EngineProcess
    @ObservationIgnored private let makeClient: (Int) -> EngineClientProtocol
    @ObservationIgnored private let sleeper: EngineSleeper
    @ObservationIgnored private let log: LogWriting
    @ObservationIgnored private let parentPID: Int32
    @ObservationIgnored private let tokenGenerator: () -> String

    // MARK: per-launch bookkeeping
    @ObservationIgnored private var process: EngineProcess?
    @ObservationIgnored private var client: EngineClientProtocol?
    @ObservationIgnored private var token = ""
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var readyLineSeen = false
    @ObservationIgnored private var startupExitCode: Int32?
    @ObservationIgnored private var stopping = false
    @ObservationIgnored private var restartTimes: [Double] = []
    @ObservationIgnored private var restartTask: Task<Void, Never>?

    public init(
        config: EngineConfig,
        makeProcess: @escaping () -> EngineProcess = { FoundationEngineProcess() },
        makeClient: @escaping (Int) -> EngineClientProtocol = { EngineClient(port: $0) },
        sleeper: EngineSleeper = LiveSleeper(),
        log: LogWriting? = nil,
        parentPID: Int32 = getpid(),
        tokenGenerator: @escaping () -> String = EngineManager.randomToken
    ) {
        self.config = config
        self.makeProcess = makeProcess
        self.makeClient = makeClient
        self.sleeper = sleeper
        self.log = log ?? RotatingLogWriter(directory: config.logsDirectory)
        self.parentPID = parentPID
        self.tokenGenerator = tokenGenerator
    }

    /// 32 random bytes as 64 lowercase hex characters.
    nonisolated public static func randomToken() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        if status != errSecSuccess {
            // Fall back to the system CSPRNG via SystemRandomNumberGenerator.
            var rng = SystemRandomNumberGenerator()
            bytes = (0..<32).map { _ in UInt8.random(in: 0...255, using: &rng) }
        }
        return bytes.map { String(format: "%02x", $0) }.joined()
    }

    // MARK: - public API

    /// Start the engine and wait until it is running or has failed.
    public func start() async {
        switch state {
        case .running, .starting, .restarting:
            return
        case .stopped, .failed:
            break
        }
        restartTimes.removeAll()
        setState(.starting)
        await launchAndAwaitReadiness()
    }

    /// Graceful stop: POST /shutdown, then SIGTERM after 3 s, then SIGKILL 2 s later.
    public func stop() async {
        restartTask?.cancel()
        restartTask = nil

        guard let p = process, p.isRunning else {
            invalidateCurrentLaunch()
            if state != .stopped { setState(.stopped) }
            return
        }

        stopping = true
        defer { stopping = false }
        note("stopping engine (pid \(p.processIdentifier))")

        if let client {
            let status = try? await client.shutdown(token: token)
            note("POST /shutdown -> \(status.map(String.init) ?? "no response")")
        }
        if await !waitForExit(p, seconds: Self.shutdownGrace) {
            note("engine still running after \(Self.shutdownGrace) s; sending SIGTERM")
            p.terminate()
            if await !waitForExit(p, seconds: Self.terminateGrace) {
                note("engine still running after SIGTERM; sending SIGKILL")
                p.kill()
                _ = await waitForExit(p, seconds: Self.killGrace)
            }
        }
        invalidateCurrentLaunch()
        setState(.stopped)
    }

    /// Stop now, without HTTP or async waits: SIGTERM, a short wait, then
    /// SIGKILL. For quitting when there is no time for `stop()` (logout,
    /// shutdown, or a graceful stop that ran past the quit cap). Never restarts.
    public func stopImmediately() {
        restartTask?.cancel()
        restartTask = nil
        guard let p = process, p.isRunning else {
            invalidateCurrentLaunch()
            if state != .stopped { setState(.stopped) }
            return
        }
        note("quitting: stopping engine (pid \(p.processIdentifier)) now")
        invalidateCurrentLaunch()
        p.terminate()
        let deadline = Date().addingTimeInterval(Self.immediateGrace)
        while p.isRunning && Date() < deadline { usleep(20_000) }
        if p.isRunning {
            note("engine still running after SIGTERM; sending SIGKILL")
            p.kill()
        }
        setState(.stopped)
    }

    /// Stop (if needed) and start again with a fresh crash budget.
    public func restart() async {
        await stop()
        await start()
    }

    /// Change the port. Restarts the engine unless it is stopped.
    /// Re-applying the same port retries a failed engine (e.g. after freeing the port).
    public func updatePort(_ port: Int) async {
        guard EngineConfig.isValidPort(port) else { return }
        let changed = port != config.port
        config.port = port
        if case .failed = state {
            await restart()
        } else if changed, state != .stopped {
            await restart()
        }
    }

    public var logFileURL: URL? {
        (log as? RotatingLogWriter)?.fileURL
    }

    // MARK: - launch + readiness

    private func launchAndAwaitReadiness() async {
        generation += 1
        let gen = generation
        readyLineSeen = false
        startupExitCode = nil
        engineVersion = nil
        token = tokenGenerator()

        for dir in [config.dataDirectory, config.cacheDirectory, config.logsDirectory] {
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        }

        let spec = LaunchSpec(
            executableURL: config.interpreterURL,
            arguments: config.arguments(parentPID: parentPID),
            environment: config.processEnvironment(token: token),
            currentDirectoryURL: config.temporaryDirectory
        )
        let p = makeProcess()
        process = p
        note("launching \(spec.executableURL.path) \(spec.arguments.joined(separator: " "))"
             + (config.usesDevRuntime ? " [dev runtime]" : ""))

        do {
            try p.launch(
                spec,
                onStdout: { [weak self] line in
                    Task { @MainActor in self?.handleStdout(line, generation: gen) }
                },
                onStderr: { [weak self] line in
                    Task { @MainActor in self?.handleStderr(line, generation: gen) }
                },
                onExit: { [weak self] code in
                    Task { @MainActor in self?.handleExit(code, generation: gen) }
                }
            )
        } catch {
            note("launch failed: \(error.localizedDescription)")
            process = nil
            setState(.failed(EngineFailure(
                reason: "Could not start the engine: \(error.localizedDescription)",
                action: .viewLogs
            )))
            return
        }

        let client = makeClient(config.port)
        self.client = client

        let deadline = sleeper.now + Self.readinessTimeout
        var delay = Self.initialPollDelay
        while true {
            guard gen == generation, !stopping else { return }
            if readyLineSeen { break }
            if let code = startupExitCode {
                failStartup(code)
                return
            }
            if let health = try? await client.health(), health.isHealthy { break }
            guard gen == generation, !stopping else { return }
            if readyLineSeen { break }
            if let code = startupExitCode {
                failStartup(code)
                return
            }
            let remaining = deadline - sleeper.now
            if remaining <= 1e-9 {
                note("engine not ready after \(Int(Self.readinessTimeout)) s; killing it")
                invalidateCurrentLaunch()
                p.kill()
                setState(.failed(EngineFailure(
                    reason: "Engine did not become ready within \(Int(Self.readinessTimeout)) s",
                    action: .viewLogs
                )))
                return
            }
            try? await sleeper.sleep(seconds: min(delay, remaining))
            delay = min(delay * 2, Self.maxPollDelay)
        }

        setState(.running(port: config.port))
        Task { [weak self] in
            guard let self else { return }
            let info = try? await client.version()
            if gen == self.generation { self.engineVersion = info?.engine }
        }
    }

    private func failStartup(_ code: Int32) {
        invalidateCurrentLaunch()
        if code == Self.exitCodePortInUse {
            setState(.failed(EngineFailure(reason: "Port \(config.port) is in use", action: .openSettings)))
        } else {
            setState(.failed(EngineFailure(
                reason: "Engine exited during startup (code \(code))",
                action: .viewLogs
            )))
        }
    }

    // MARK: - process events

    private func handleStdout(_ line: String, generation gen: Int) {
        log.append(line)
        if gen == generation, line.hasPrefix("ENGINE_READY") {
            readyLineSeen = true
        }
    }

    private func handleStderr(_ line: String, generation gen: Int) {
        log.append(line)
    }

    private func handleExit(_ code: Int32, generation gen: Int) {
        note("engine exited with code \(code)")
        guard gen == generation, !stopping else { return }
        process = nil
        switch state {
        case .starting, .restarting:
            startupExitCode = code
        case .running:
            handleCrash(code)
        case .stopped, .failed:
            break
        }
    }

    private func handleCrash(_ code: Int32) {
        let now = sleeper.now
        restartTimes = restartTimes.filter { now - $0 < Self.restartWindow }
        guard restartTimes.count < Self.restartBackoff.count else {
            setState(.failed(EngineFailure(
                reason: "Engine crashed \(restartTimes.count + 1) times within \(Int(Self.restartWindow)) s (last exit code \(code))",
                action: .viewLogs
            )))
            return
        }
        restartTimes.append(now)
        let attempt = restartTimes.count
        setState(.restarting(attempt: attempt))
        let backoff = Self.restartBackoff[attempt - 1]
        note("engine crashed (code \(code)); restart \(attempt) in \(backoff) s")
        restartTask = Task { [weak self] in
            guard let self else { return }
            try? await self.sleeper.sleep(seconds: backoff)
            guard !Task.isCancelled, self.state == .restarting(attempt: attempt), !self.stopping else { return }
            await self.launchAndAwaitReadiness()
        }
    }

    // MARK: - helpers

    private func waitForExit(_ p: EngineProcess, seconds: Double) async -> Bool {
        let end = sleeper.now + seconds
        while p.isRunning {
            if sleeper.now >= end - 1e-9 { return false }
            try? await sleeper.sleep(seconds: min(0.1, end - sleeper.now))
        }
        return true
    }

    /// Ignore any further events from the current process.
    private func invalidateCurrentLaunch() {
        generation += 1
        process = nil
        engineVersion = nil
    }

    private func setState(_ new: EngineState) {
        guard new != state else { return }
        state = new
        note("state -> \(new.summary)")
        onStateChange?(new)
    }

    private func note(_ message: String) {
        log.append("[app] \(message)")
    }
}
