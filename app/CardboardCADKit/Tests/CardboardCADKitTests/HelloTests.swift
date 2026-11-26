import XCTest
@testable import CardboardCADKit

/// Hello-world demo: the app sends a name to POST /api/hello.
final class HelloTests: XCTestCase {
    var client: EngineClient!

    override func setUp() {
        StubURLProtocol.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        client = EngineClient(port: 5001, session: URLSession(configuration: config))
    }

    func testHelloSendsNameAndDecodesMessage() async throws {
        StubURLProtocol.stub("POST", "/api/hello", json: #"{"message":"Hello, Mac, from CardboardCAD by Fold Theory"}"#)
        let message = try await client.hello(name: "Mac")
        XCTAssertEqual(message, "Hello, Mac, from CardboardCAD by Fold Theory")

        let request = try XCTUnwrap(StubURLProtocol.requests.last)
        XCTAssertEqual(request.httpMethod, "POST")
        let body = try XCTUnwrap(request.httpBody)
        XCTAssertEqual(try JSONSerialization.jsonObject(with: body) as? [String: String], ["name": "Mac"])
    }

    func testHelloServerErrorThrows() async {
        StubURLProtocol.stub("POST", "/api/hello", status: 500, json: "{}")
        do {
            _ = try await client.hello(name: "x")
            XCTFail("expected throw")
        } catch let error as EngineClientError {
            XCTAssertEqual(error, .httpStatus(500))
        } catch {
            XCTFail("unexpected \(error)")
        }
    }

    func testHopsEndpointsIncludeHello() {
        XCTAssertEqual(EngineConfig.hopsEndpoints, ["hello", "cut_estimate", "echo"])
    }
}
