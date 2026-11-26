import XCTest
@testable import CardboardCADKit

/// The status bar's "when did Grasshopper last call?" line.
final class HopsActivityTests: XCTestCase {
    var client: EngineClient!

    override func setUp() {
        StubURLProtocol.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        client = EngineClient(port: 5001, session: URLSession(configuration: config))
    }

    func testDecodesActivity() async throws {
        StubURLProtocol.stub("GET", "/api/hops_activity", json: """
            {"requests":3,"last_request_at":"2026-11-26T05:00:00Z","seconds_since_last":12.4,
             "last_path":"/solve /hello","last_status":200}
            """)
        let a = try await client.hopsActivity()
        XCTAssertEqual(a, HopsActivity(requests: 3, lastRequestAt: "2026-11-26T05:00:00Z",
                                       secondsSinceLast: 12.4, lastPath: "/solve /hello", lastStatus: 200))
        XCTAssertEqual(StubURLProtocol.requests.last?.httpMethod, "GET")
    }

    func testDecodesNoActivityYet() async throws {
        StubURLProtocol.stub("GET", "/api/hops_activity", json: """
            {"requests":0,"last_request_at":null,"seconds_since_last":null,"last_path":null,"last_status":null}
            """)
        let a = try await client.hopsActivity()
        XCTAssertEqual(a.requests, 0)
        XCTAssertEqual(a.level, .never)
        XCTAssertEqual(a.summary, "Grasshopper: no calls yet")
    }

    func testRecentCall() {
        let a = HopsActivity(requests: 1, secondsSinceLast: 12, lastPath: "/hello", lastStatus: 200)
        XCTAssertEqual(a.level, .recent)
        XCTAssertEqual(a.summary, "Grasshopper: last call 12 s ago")
    }

    func testIdleAfterWindow() {
        let a = HopsActivity(requests: 5, secondsSinceLast: 600, lastStatus: 200)
        XCTAssertEqual(a.level, .idle)
        XCTAssertEqual(a.summary, "Grasshopper: last call 10 min ago")
    }

    func testFailedCall() {
        let a = HopsActivity(requests: 2, secondsSinceLast: 1, lastPath: "/solve /cut_estimate", lastStatus: 500)
        XCTAssertEqual(a.level, .error)
        XCTAssertEqual(a.summary, "Grasshopper: last call just now failed (HTTP 500)")
    }

    func testAgeFormatting() {
        XCTAssertEqual(HopsActivity.formatAge(0), "just now")
        XCTAssertEqual(HopsActivity.formatAge(59.9), "59 s ago")
        XCTAssertEqual(HopsActivity.formatAge(3599), "59 min ago")
        XCTAssertEqual(HopsActivity.formatAge(7200), "2 h ago")
    }
}
