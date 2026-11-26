import XCTest
@testable import CardboardCADKit

@MainActor
final class EngineManagerTests: XCTestCase {
    var factory: FakeProcessFactory!
    var client: FakeEngineClient!
    var sleeper: FakeSleeper!
    var log: MemoryLog!
    var tokens: [String] = []

    override func setUp() async throws {
        factory = FakeProcessFactory()
        client = FakeEngineClient()
        sleeper = FakeSleeper()
        log = MemoryLog()
        tokens = []
    }

    func makeManager(port: Int = 5001) -> EngineManager {
        var n = 0
        return EngineManager(
            config: testConfig(port: port),
            makeProcess: { [unowned self] in self.factory.make() },
            makeClient: { [unowned self] _ in self.client },
            sleeper: sleeper,
            log: log,
            parentPID: 4242,
            tokenGenerator: { [unowned self] in
                n += 1
                let t = String(repeating: String(n % 10), count: 64)
                self.tokens.append(t)
                return t
            }
        )
    }

    // MARK: start / readiness

    func testInitialStateIsStopped() {
        XCTAssertEqual(makeManager().state, .stopped)
    }

    func testReadyLineMovesToRunning() async {
        let m = makeManager()
        await m.start()
        XCTAssertEqual(m.state, .running(port: 5001))
        XCTAssertEqual(factory.launched.count, 1)
    }

    func testRunningFetchesEngineVersionForTooltip() async {
        let m = makeManager()
        await m.start()
        let fetched = await eventually { m.engineVersion == "0.1.0" }
        XCTAssertTrue(fetched)
    }

    func testHealthyHealthWithoutReadyLineMovesToRunning() async {
        factory.defaultBehavior = .silent
        client.healthResults = [false, false, true]
        let m = makeManager()
        await m.start()
        XCTAssertEqual(m.state, .running(port: 5001))
        // Backoff starts at 100 ms and doubles.
        XCTAssertEqual(Array(sleeper.sleeps.prefix(2)), [0.1, 0.2])
    }

    func testHealthBackoffIsCappedAtOneSecond() async {
        factory.defaultBehavior = .silent
        client.healthResults = Array(repeating: false, count: 8) + [true]
        let m = makeManager()
        await m.start()
        XCTAssertEqual(m.state, .running(port: 5001))
        XCTAssertEqual(Array(sleeper.sleeps.prefix(8)), [0.1, 0.2, 0.4, 0.8, 1, 1, 1, 1])
    }

    func testReadinessTimesOutAfterTwentySecondsAndKillsProcess() async {
        factory.defaultBehavior = .silent
        client.healthResults = [false]
        let m = makeManager()
        await m.start()
        guard case .failed(let failure) = m.state else {
            return XCTFail("expected failed, got \(m.state)")
        }
        XCTAssertEqual(failure.action, .viewLogs)
        XCTAssertTrue(failure.reason.contains("20"), failure.reason)
        XCTAssertGreaterThanOrEqual(sleeper.now, 20)
        XCTAssertLessThan(sleeper.now, 22)
        XCTAssertFalse(factory.current!.isRunning)
    }

    func testExitCode98IsPortInUse() async {
        factory.defaultBehavior = .exitImmediately(98)
        let m = makeManager(port: 5050)
        await m.start()
        XCTAssertEqual(
            m.state,
            .failed(EngineFailure(reason: "Port 5050 is in use", action: .openSettings))
        )
        XCTAssertEqual(factory.launched.count, 1, "port-in-use must not auto-restart")
    }

    func testOtherExitDuringStartupFailsWithViewLogs() async {
        factory.defaultBehavior = .exitImmediately(1)
        let m = makeManager()
        await m.start()
        guard case .failed(let failure) = m.state else {
            return XCTFail("expected failed, got \(m.state)")
        }
        XCTAssertEqual(failure.action, .viewLogs)
    }

    func testStartWhileRunningDoesNothing() async {
        let m = makeManager()
        await m.start()
        await m.start()
        XCTAssertEqual(factory.launched.count, 1)
    }

    // MARK: launch spec

    func testLaunchSpecArgumentsAndExplicitEnvironment() async throws {
        let m = makeManager(port: 5123)
        await m.start()
        let spec = try XCTUnwrap(factory.current?.spec)
        XCTAssertEqual(spec.executableURL.path, "/Bundle/CardboardCAD.app/Contents/Frameworks/python/bin/python3.12")
        XCTAssertEqual(
            spec.arguments,
            ["-I", "-B", "-u", "-m", "cardboard_engine.server", "--port", "5123", "--parent-pid", "4242"]
        )
        XCTAssertEqual(
            Set(spec.environment.keys),
            [
                "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "ENGINE_SHUTDOWN_TOKEN",
                "ENGINE_DATA_DIR", "ENGINE_CACHE_DIR", "HOME", "TMPDIR", "PATH",
            ]
        )
        XCTAssertEqual(spec.environment["PATH"], "/usr/bin:/bin")
        XCTAssertEqual(spec.environment["PYTHONDONTWRITEBYTECODE"], "1")
        XCTAssertEqual(spec.environment["PYTHONUNBUFFERED"], "1")
        XCTAssertEqual(spec.environment["ENGINE_SHUTDOWN_TOKEN"], tokens.last)
        XCTAssertEqual(spec.environment["HOME"], "/Users/tester")
        XCTAssertEqual(spec.environment["ENGINE_DATA_DIR"], "/tmp/cardboardcad-tests/Data")
        XCTAssertEqual(spec.environment["ENGINE_CACHE_DIR"], "/tmp/cardboardcad-tests/Caches")
        XCTAssertFalse(spec.arguments.contains(tokens.last!), "token must not be in argv")
    }

    func testEachLaunchGetsAFreshToken() async throws {
        let m = makeManager()
        await m.start()
        await m.restart()
        XCTAssertEqual(factory.launched.count, 2)
        let t1 = try XCTUnwrap(factory.launched[0].spec?.environment["ENGINE_SHUTDOWN_TOKEN"])
        let t2 = try XCTUnwrap(factory.launched[1].spec?.environment["ENGINE_SHUTDOWN_TOKEN"])
        XCTAssertNotEqual(t1, t2)
    }

    func testRandomTokenIs32BytesHex() {
        let a = EngineManager.randomToken()
        let b = EngineManager.randomToken()
        XCTAssertEqual(a.count, 64)
        XCTAssertTrue(a.allSatisfy { $0.isHexDigit })
        XCTAssertNotEqual(a, b)
    }

    func testEngineOutputGoesToLog() async {
        let m = makeManager()
        await m.start()
        XCTAssertTrue(log.lines.contains { $0.contains("some startup noise") })
        XCTAssertTrue(log.lines.contains { $0.contains("ENGINE_READY") })
    }

    // MARK: crash handling

    func testCrashWhileRunningRestartsWithOneSecondBackoff() async {
        let m = makeManager()
        await m.start()
        factory.current!.exit(-9)
        let restarted = await eventually { self.factory.launched.count == 2 && m.state == .running(port: 5001) }
        XCTAssertTrue(restarted, "state: \(m.state)")
        XCTAssertTrue(sleeper.sleeps.contains(1.0))
    }

    func testRestartingStateReportsAttempt() async {
        let m = makeManager()
        await m.start()
        var seen: [EngineState] = []
        m.onStateChange = { seen.append($0) }
        factory.current!.exit(-9)
        _ = await eventually { m.state == .running(port: 5001) && self.factory.launched.count == 2 }
        XCTAssertTrue(seen.contains(.restarting(attempt: 1)), "\(seen)")
    }

    func testThreeRestartsThenFailedWithinSixtySeconds() async {
        let m = makeManager()
        await m.start()
        for expectedLaunches in 2...4 {
            factory.current!.exit(-9)
            let ok = await eventually { self.factory.launched.count == expectedLaunches && m.state == .running(port: 5001) }
            XCTAssertTrue(ok, "restart \(expectedLaunches - 1) failed; state \(m.state)")
        }
        // Backoff 1 s, 2 s, 4 s.
        let backoffs = sleeper.sleeps.filter { [1.0, 2.0, 4.0].contains($0) }
        XCTAssertEqual(Array(backoffs.prefix(3)), [1, 2, 4])

        factory.current!.exit(-9)
        let failed = await eventually {
            if case .failed(let f) = m.state { return f.action == .viewLogs }
            return false
        }
        XCTAssertTrue(failed, "state: \(m.state)")
        XCTAssertEqual(factory.launched.count, 4)
    }

    func testCrashesSpreadOverMoreThanSixtySecondsKeepRestarting() async {
        let m = makeManager()
        await m.start()
        for expectedLaunches in 2...6 {
            sleeper.advance(61)
            factory.current!.exit(-9)
            let ok = await eventually { self.factory.launched.count == expectedLaunches && m.state == .running(port: 5001) }
            XCTAssertTrue(ok, "state: \(m.state)")
        }
    }

    func testStaleExitFromPreviousProcessIsIgnored() async {
        let m = makeManager()
        await m.start()
        let first = factory.current!
        await m.restart()
        XCTAssertEqual(factory.launched.count, 2)
        first.exit(-9) // already exited; must not trigger anything
        try? await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(factory.launched.count, 2)
        XCTAssertEqual(m.state, .running(port: 5001))
    }

    // MARK: stop

    func testStopUsesShutdownEndpointFirst() async {
        let m = makeManager()
        await m.start()
        let p = factory.current!
        client.onShutdown = { p.exit(0) }
        await m.stop()
        XCTAssertEqual(m.state, .stopped)
        XCTAssertEqual(client.shutdownTokens, [tokens.last!])
        XCTAssertEqual(p.terminateCalls, 0)
        XCTAssertEqual(p.killCalls, 0)
        XCTAssertEqual(factory.launched.count, 1, "a requested stop must not auto-restart")
    }

    func testStopEscalatesToSigtermAfterThreeSeconds() async {
        let m = makeManager()
        await m.start()
        let p = factory.current!
        let before = sleeper.now
        await m.stop()
        XCTAssertEqual(m.state, .stopped)
        XCTAssertEqual(p.terminateCalls, 1)
        XCTAssertEqual(p.killCalls, 0)
        XCTAssertEqual(sleeper.now - before, 3, accuracy: 0.25)
    }

    func testStopEscalatesToSigkillTwoSecondsAfterSigterm() async {
        factory.ignoreTerminate = true
        let m = makeManager()
        await m.start()
        let p = factory.current!
        let before = sleeper.now
        await m.stop()
        XCTAssertEqual(m.state, .stopped)
        XCTAssertEqual(p.terminateCalls, 1)
        XCTAssertEqual(p.killCalls, 1)
        XCTAssertFalse(p.isRunning)
        XCTAssertEqual(sleeper.now - before, 5, accuracy: 0.25)
    }

    func testStopWhenStoppedIsNoop() async {
        let m = makeManager()
        await m.stop()
        XCTAssertEqual(m.state, .stopped)
        XCTAssertTrue(client.shutdownTokens.isEmpty)
    }

    func testRestartResetsCrashHistory() async {
        let m = makeManager()
        await m.start()
        for expected in 2...4 {
            factory.current!.exit(-9)
            _ = await eventually { self.factory.launched.count == expected && m.state == .running(port: 5001) }
        }
        await m.restart() // manual restart clears the budget
        factory.current!.exit(-9)
        let ok = await eventually { m.state == .running(port: 5001) && self.factory.launched.count == 6 }
        XCTAssertTrue(ok, "state: \(m.state)")
    }

    func testRestartFromFailedStartsAgain() async {
        factory.behaviors = [.exitImmediately(98)]
        let m = makeManager()
        await m.start()
        guard case .failed = m.state else { return XCTFail() }
        await m.restart()
        XCTAssertEqual(m.state, .running(port: 5001))
    }

    func testChangingPortRestartsOnNewPort() async throws {
        let m = makeManager()
        await m.start()
        await m.updatePort(6001)
        XCTAssertEqual(m.state, .running(port: 6001))
        XCTAssertEqual(m.config.port, 6001)
        let args = try XCTUnwrap(factory.current?.spec?.arguments)
        XCTAssertEqual(args[args.firstIndex(of: "--port")! + 1], "6001")
    }

    func testMissingInterpreterFailsWithoutLaunching() async {
        let m = EngineManager(
            config: testConfig(),
            makeProcess: { FailingProcess() },
            makeClient: { _ in FakeEngineClient() },
            sleeper: FakeSleeper(),
            log: MemoryLog(),
            parentPID: 1
        )
        await m.start()
        guard case .failed(let f) = m.state else { return XCTFail("\(m.state)") }
        XCTAssertEqual(f.action, .viewLogs)
    }
}

/// A process whose launch throws (e.g. interpreter missing from the bundle).
final class FailingProcess: EngineProcess {
    var isRunning: Bool { false }
    var processIdentifier: Int32 { 0 }
    func launch(
        _ spec: LaunchSpec,
        onStdout: @escaping @Sendable (String) -> Void,
        onStderr: @escaping @Sendable (String) -> Void,
        onExit: @escaping @Sendable (Int32) -> Void
    ) throws {
        throw CocoaError(.fileNoSuchFile)
    }
    func terminate() {}
    func kill() {}
}
