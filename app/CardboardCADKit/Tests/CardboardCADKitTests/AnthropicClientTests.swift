import XCTest
@testable import CardboardCADKit

final class AnthropicClientTests: XCTestCase {
    private func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        return URLSession(configuration: config)
    }

    override func setUp() {
        StubURLProtocol.reset()
    }

    private let request = ModelRequest(
        model: "claude-opus-5-5",
        system: "You design cardboard.",
        tools: [["name": "waffle_slice", "description": "Slice", "input_schema": ["type": "object"]]],
        messages: [["role": "user", "content": "a stool please"]],
        effort: "medium"
    )

    func testDefaultModelIsOpus55() {
        XCTAssertEqual(AssistantModel.defaultID, "claude-opus-5-5")
        XCTAssertTrue(AssistantModel.all.map(\.id).contains("claude-sonnet-5-5"))
    }

    func testRequestHeadersAndURL() throws {
        let client = AnthropicClient(apiKey: { "sk-ant-test" }, session: session())
        let r = try client.makeURLRequest(request, apiKey: "sk-ant-test", fallbacks: true)
        XCTAssertEqual(r.url?.absoluteString, "https://api.anthropic.com/v1/messages")
        XCTAssertEqual(r.httpMethod, "POST")
        XCTAssertEqual(r.value(forHTTPHeaderField: "x-api-key"), "sk-ant-test")
        XCTAssertEqual(r.value(forHTTPHeaderField: "anthropic-version"), "2026-10-21")
        XCTAssertEqual(r.value(forHTTPHeaderField: "anthropic-beta"), "server-side-fallback-2026-07-01")
        XCTAssertEqual(r.value(forHTTPHeaderField: "content-type"), "application/json")
    }

    func testRequestBody() throws {
        let body = AnthropicClient.body(for: request, fallbacks: true)
        XCTAssertEqual(body["model"], .string("claude-opus-5-5"))
        XCTAssertEqual(body["max_tokens"]?.numberValue, 16000)
        XCTAssertEqual(body["system"], .string("You design cardboard."))
        XCTAssertEqual(body["thinking"], ["type": "adaptive"])
        XCTAssertEqual(body["output_config"], ["effort": "medium"])
        XCTAssertEqual(body["fallbacks"], .string("default"))
        XCTAssertEqual(body["cache_control"], ["type": "ephemeral"])
        XCTAssertEqual(body["tools"]?.arrayValue?.count, 1)
        XCTAssertEqual(body["messages"]?.arrayValue?.count, 1)
        XCTAssertNil(body["tool_choice"], "forced tool choice is a 400 on current models")
        XCTAssertNil(body["temperature"])

        let plain = AnthropicClient.body(for: request, fallbacks: false)
        XCTAssertNil(plain["fallbacks"])
    }

    func testSendDecodesContentAndStopReason() async throws {
        StubURLProtocol.stub("POST", "/v1/messages", json: """
        {"id":"msg_1","type":"message","role":"assistant","model":"claude-opus-5-5",
         "content":[{"type":"text","text":"Slicing it."},
                    {"type":"tool_use","id":"toolu_1","name":"waffle_slice","input":{"x_ribs":4}}],
         "stop_reason":"tool_use","usage":{"input_tokens":10,"output_tokens":5}}
        """)
        let client = AnthropicClient(apiKey: { "sk-ant-test" }, session: session())
        let response = try await client.send(request)
        XCTAssertEqual(response.stopReason, "tool_use")
        XCTAssertEqual(response.content.count, 2)
        XCTAssertEqual(response.content[1]["input"]?["x_ribs"]?.numberValue, 4)
        XCTAssertEqual(response.model, "claude-opus-5-5")
        let sent = try XCTUnwrap(StubURLProtocol.requests.first?.httpBody)
        let sentBody = try JSONDecoder().decode(JSONValue.self, from: sent)
        XCTAssertEqual(sentBody["model"], .string("claude-opus-5-5"))
    }

    func testMissingKeyThrowsWithoutCallingTheAPI() async {
        let client = AnthropicClient(apiKey: { "  " }, session: session())
        do {
            _ = try await client.send(request)
            XCTFail("expected throw")
        } catch {
            XCTAssertEqual(error as? AnthropicError, .missingAPIKey)
        }
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testAPIErrorMessageIsShown() async {
        StubURLProtocol.stub("POST", "/v1/messages", status: 401, json: """
        {"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}
        """)
        let client = AnthropicClient(apiKey: { "sk-ant-bad" }, session: session())
        do {
            _ = try await client.send(request)
            XCTFail("expected throw")
        } catch let error as AnthropicError {
            XCTAssertEqual(error, .http(401, "invalid x-api-key"))
            XCTAssertTrue(error.localizedDescription.contains("API key"))
        } catch {
            XCTFail("unexpected \(error)")
        }
    }

    func testRetriesWithoutFallbacksWhenTheAccountCannotUseThem() async throws {
        // First call is rejected for the fallbacks field; the client retries once without it.
        StubURLProtocol.stub("POST", "/v1/messages", status: 400, json: """
        {"type":"error","error":{"type":"invalid_request_error","message":"fallbacks: not available"}}
        """)
        let client = AnthropicClient(apiKey: { "sk-ant-test" }, session: session())
        do {
            _ = try await client.send(request)
        } catch {
            // The stub answers 400 both times; what matters is the second request.
        }
        XCTAssertEqual(StubURLProtocol.requests.count, 2)
        let second = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(StubURLProtocol.requests[1].httpBody))
        XCTAssertNil(second["fallbacks"])
        XCTAssertNil(StubURLProtocol.requests[1].value(forHTTPHeaderField: "anthropic-beta"))
    }
}
