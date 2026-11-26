import XCTest
@testable import CardboardCADKit

/// EngineClient's Design Assistant routes (/api/design/...).
final class DesignClientTests: XCTestCase {
    var client: EngineClient!

    override func setUp() {
        StubURLProtocol.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        client = EngineClient(port: 5001, session: URLSession(configuration: config))
    }

    func testSetupDecodesSystemAndTools() async throws {
        StubURLProtocol.stub("GET", "/api/design/tools", json: """
        {"system":"You are...","tools":[{"name":"create_shape","input_schema":{"type":"object"}}]}
        """)
        let setup = try await client.designSetup()
        XCTAssertEqual(setup.system, "You are...")
        XCTAssertEqual(setup.tools.first?["name"], .string("create_shape"))
    }

    func testRunToolPostsNameAndInput() async throws {
        StubURLProtocol.stub("POST", "/api/design/tool", json: #"{"content":"Created box","is_error":false,"version":4}"#)
        let out = try await client.runDesignTool(name: "create_shape", input: ["shape": "box"])
        XCTAssertEqual(out, DesignToolOutput(content: "Created box", isError: false, version: 4))
        let body = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(StubURLProtocol.requests.last?.httpBody))
        XCTAssertEqual(body, ["name": "create_shape", "input": ["shape": "box"]])
        XCTAssertGreaterThanOrEqual(StubURLProtocol.requests.last?.timeoutInterval ?? 0, 60, "nesting can take a while")
    }

    func testToolErrorsAreResultsNotThrows() async throws {
        StubURLProtocol.stub("POST", "/api/design/tool", json: #"{"content":"No model yet","is_error":true,"version":5}"#)
        let out = try await client.runDesignTool(name: "waffle_slice", input: [:])
        XCTAssertTrue(out.isError)
    }

    func testResetAndPreviewURL() async throws {
        StubURLProtocol.stub("POST", "/api/design/reset", json: #"{"version":9}"#)
        try await client.resetDesign()
        XCTAssertEqual(StubURLProtocol.requests.last?.httpMethod, "POST")
        XCTAssertEqual(client.previewURL.absoluteString, "http://127.0.0.1:5001/preview")
    }
}
