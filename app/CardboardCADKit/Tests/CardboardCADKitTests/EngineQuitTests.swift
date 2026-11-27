import XCTest
@testable import CardboardCADKit

/// stopImmediately(): the last-resort stop used when the app has to quit now
/// (logout, shutdown, or a graceful stop that ran past the quit time cap).
@MainActor
final class EngineQuitTests: XCTestCase {
    var factory: FakeProcessFactory!
    var client: FakeEngineClient!

    override func setUp() async throws {
        factory = FakeProcessFactory()
        client = FakeEngineClient()
    }

    func makeManager() -> EngineManager {
        EngineManager(
            config: testConfig(port: 5001),
            makeProcess: { [unowned self] in self.factory.make() },
            makeClient: { [unowned self] _ in self.client },
            sleeper: FakeSleeper(),
            log: MemoryLog(),
            parentPID: 4242,
            tokenGenerator: { String(repeating: "7", count: 64) }
        )
    }

    func testStopImmediatelyTerminatesWithoutWaitingAndDoesNotRestart() async {
        let m = makeManager()
        await m.start()
        let p = factory.current!
        m.stopImmediately()
        XCTAssertEqual(m.state, .stopped)
        XCTAssertEqual(p.terminateCalls, 1)
        XCTAssertTrue(client.shutdownTokens.isEmpty, "no HTTP round trip when quitting now")
        // Give a would-be auto-restart a chance to run.
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(factory.launched.count, 1, "an immediate stop must not auto-restart")
    }

    func testStopImmediatelyKillsAProcessThatIgnoresSigterm() async {
        factory.ignoreTerminate = true
        let m = makeManager()
        await m.start()
        let p = factory.current!
        m.stopImmediately()
        XCTAssertEqual(p.terminateCalls, 1)
        XCTAssertEqual(p.killCalls, 1)
        XCTAssertFalse(p.isRunning)
        XCTAssertEqual(m.state, .stopped)
    }

    func testStopImmediatelyWhenStoppedIsANoOp() {
        let m = makeManager()
        m.stopImmediately()
        XCTAssertEqual(m.state, .stopped)
        XCTAssertTrue(factory.launched.isEmpty)
    }
}
