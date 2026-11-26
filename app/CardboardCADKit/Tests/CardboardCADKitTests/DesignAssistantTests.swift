import XCTest
@testable import CardboardCADKit

// MARK: - Fakes

final class ScriptedModel: ChatModelClient, @unchecked Sendable {
    var responses: [Result<ModelResponse, Error>]
    private(set) var requests: [ModelRequest] = []

    init(_ responses: [Result<ModelResponse, Error>]) { self.responses = responses }

    func send(_ request: ModelRequest) async throws -> ModelResponse {
        requests.append(request)
        guard !responses.isEmpty else { return ModelResponse(content: [], stopReason: "end_turn", model: nil) }
        return try responses.removeFirst().get()
    }
}

final class FakeDesignTools: DesignToolRunner {
    var setup = DesignSetup(system: "SYSTEM", tools: [["name": "waffle_slice"]])
    var results: [String: DesignToolOutput] = [:]
    var failWith: Error?
    private(set) var calls: [(String, JSONValue)] = []
    private(set) var resets = 0
    private(set) var setupCalls = 0

    func designSetup() async throws -> DesignSetup {
        setupCalls += 1
        return setup
    }

    func runDesignTool(name: String, input: JSONValue) async throws -> DesignToolOutput {
        calls.append((name, input))
        if let failWith { throw failWith }
        return results[name] ?? DesignToolOutput(content: "\(name) done", isError: false, version: calls.count)
    }

    func resetDesign() async throws { resets += 1 }
}

struct Boom: Error, LocalizedError {
    var errorDescription: String? { "network down" }
}

private func text(_ s: String) -> JSONValue { ["type": "text", "text": .string(s)] }
private func toolUse(_ id: String, _ name: String, _ input: JSONValue) -> JSONValue {
    ["type": "tool_use", "id": .string(id), "name": .string(name), "input": input]
}

// MARK: - Tests

@MainActor
final class DesignAssistantTests: XCTestCase {
    func testToolLoopRunsToolsAndReturnsResultsToClaude() async throws {
        let model = ScriptedModel([
            .success(ModelResponse(
                content: [["type": "thinking", "thinking": "", "signature": "sig"],
                          text("Making waffle ribs."),
                          toolUse("t1", "waffle_slice", ["x_ribs": 4])],
                stopReason: "tool_use", model: "claude-opus-5-5")),
            .success(ModelResponse(content: [text("Done: 8 ribs.")], stopReason: "end_turn", model: "claude-opus-5-5")),
        ])
        let tools = FakeDesignTools()
        tools.results["waffle_slice"] = DesignToolOutput(content: "Waffle: 8 parts", isError: false, version: 3)
        let assistant = DesignAssistant(model: model, modelID: "claude-sonnet-5-5", effort: "low")
        assistant.attach(tools: tools)

        await assistant.send("  waffle it at 4 ribs  ")

        // The tool ran with Claude's input.
        XCTAssertEqual(tools.calls.count, 1)
        XCTAssertEqual(tools.calls[0].0, "waffle_slice")
        XCTAssertEqual(tools.calls[0].1, ["x_ribs": 4])

        // Two model calls; both carry the engine's system prompt, tools and the chosen model.
        XCTAssertEqual(model.requests.count, 2)
        XCTAssertEqual(model.requests[0].system, "SYSTEM")
        XCTAssertEqual(model.requests[0].tools, [["name": "waffle_slice"]])
        XCTAssertEqual(model.requests[0].model, "claude-sonnet-5-5")
        XCTAssertEqual(model.requests[0].effort, "low")

        // History: user, assistant (unchanged, thinking kept), tool results, final assistant.
        let h = assistant.history
        XCTAssertEqual(h.count, 4)
        XCTAssertEqual(h[0], ["role": "user", "content": "waffle it at 4 ribs"])
        XCTAssertEqual(h[1]["content"]?.arrayValue?.first?["type"], .string("thinking"))
        let result = try XCTUnwrap(h[2]["content"]?.arrayValue?.first)
        XCTAssertEqual(h[2]["role"], .string("user"))
        XCTAssertEqual(result["type"], .string("tool_result"))
        XCTAssertEqual(result["tool_use_id"], .string("t1"))
        XCTAssertEqual(result["content"], .string("Waffle: 8 parts"))
        XCTAssertEqual(result["is_error"], .bool(false))
        XCTAssertEqual(model.requests[1].messages, Array(h.prefix(3)))

        // What the person sees.
        XCTAssertEqual(assistant.items.map(\.kind), [
            .user, .assistant, .tool(name: "waffle_slice", isError: false), .assistant,
        ])
        XCTAssertEqual(assistant.items[0].text, "waffle it at 4 ribs")
        XCTAssertEqual(assistant.items[2].text, "Waffle: 8 parts")
        XCTAssertEqual(assistant.items[3].text, "Done: 8 ribs.")
        XCTAssertFalse(assistant.isBusy)
    }

    func testParallelToolCallsGoBackInOneMessage() async {
        let model = ScriptedModel([
            .success(ModelResponse(content: [toolUse("a", "model_info", [:]), toolUse("b", "estimate_cost", [:])],
                                   stopReason: "tool_use", model: nil)),
            .success(ModelResponse(content: [text("ok")], stopReason: "end_turn", model: nil)),
        ])
        let tools = FakeDesignTools()
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: tools)
        await assistant.send("how much?")
        XCTAssertEqual(tools.calls.map(\.0), ["model_info", "estimate_cost"])
        let results = assistant.history[2]["content"]?.arrayValue ?? []
        XCTAssertEqual(results.map { $0["tool_use_id"] }, [.string("a"), .string("b")])
    }

    func testFailedToolIsReportedToClaudeAsAnError() async {
        let model = ScriptedModel([
            .success(ModelResponse(content: [toolUse("t", "nest_sheets", [:])], stopReason: "tool_use", model: nil)),
            .success(ModelResponse(content: [text("The engine is down.")], stopReason: "end_turn", model: nil)),
        ])
        let tools = FakeDesignTools()
        tools.failWith = Boom()
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: tools)
        await assistant.send("nest")
        let result = assistant.history[2]["content"]?.arrayValue?.first
        XCTAssertEqual(result?["is_error"], .bool(true))
        XCTAssertTrue(result?["content"]?.stringValue?.contains("network down") ?? false)
        XCTAssertEqual(assistant.items[1].kind, .tool(name: "nest_sheets", isError: true))
    }

    func testModelErrorRollsBackSoTheConversationStaysValid() async {
        let model = ScriptedModel([.failure(Boom())])
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: FakeDesignTools())
        await assistant.send("hello")
        XCTAssertTrue(assistant.history.isEmpty, "a half-finished turn is not kept")
        XCTAssertEqual(assistant.items.map(\.kind), [.user, .error])
        XCTAssertTrue(assistant.items[1].text.contains("network down"))
        XCTAssertFalse(assistant.isBusy)
    }

    func testWithoutTheEngineNothingIsSent() async {
        let model = ScriptedModel([])
        let assistant = DesignAssistant(model: model)
        await assistant.send("hello")
        XCTAssertTrue(model.requests.isEmpty)
        XCTAssertEqual(assistant.items.last?.kind, .error)
        XCTAssertTrue(assistant.items.last?.text.contains("engine") ?? false)
    }

    func testBlankMessagesAreIgnored() async {
        let model = ScriptedModel([])
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: FakeDesignTools())
        await assistant.send("   \n ")
        XCTAssertTrue(assistant.items.isEmpty)
        XCTAssertTrue(model.requests.isEmpty)
    }

    func testRefusalAndMaxTokensAreExplained() async {
        let model = ScriptedModel([
            .success(ModelResponse(content: [], stopReason: "refusal", model: nil)),
        ])
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: FakeDesignTools())
        await assistant.send("x")
        XCTAssertEqual(assistant.items.last?.kind, .notice)

        let model2 = ScriptedModel([
            .success(ModelResponse(content: [text("partial")], stopReason: "max_tokens", model: nil)),
        ])
        let a2 = DesignAssistant(model: model2)
        a2.attach(tools: FakeDesignTools())
        await a2.send("x")
        XCTAssertEqual(a2.items.last?.kind, .notice)
    }

    func testRunawayToolLoopsStop() async {
        let looping = (0..<50).map { i in
            Result<ModelResponse, Error>.success(ModelResponse(
                content: [toolUse("t\(i)", "model_info", [:])], stopReason: "tool_use", model: nil))
        }
        let model = ScriptedModel(looping)
        let tools = FakeDesignTools()
        let assistant = DesignAssistant(model: model)
        assistant.maxSteps = 5
        assistant.attach(tools: tools)
        await assistant.send("go")
        XCTAssertEqual(model.requests.count, 5)
        XCTAssertEqual(assistant.items.last?.kind, .notice)
        // The history still ends with tool results, so the next message is valid.
        XCTAssertEqual(assistant.history.last?["role"], .string("user"))
    }

    func testSetupIsFetchedOnceAndAgainAfterReattaching() async {
        let tools = FakeDesignTools()
        let assistant = DesignAssistant(model: ScriptedModel([]))
        assistant.attach(tools: tools)
        await assistant.send("a")
        await assistant.send("b")
        XCTAssertEqual(tools.setupCalls, 1)
        assistant.attach(tools: tools)
        await assistant.send("c")
        XCTAssertEqual(tools.setupCalls, 2)
    }

    func testNewDesignClearsTheChatAndTheEngine() async {
        let tools = FakeDesignTools()
        let assistant = DesignAssistant(model: ScriptedModel([
            .success(ModelResponse(content: [text("hi")], stopReason: "end_turn", model: nil)),
        ]))
        assistant.attach(tools: tools)
        await assistant.send("hello")
        await assistant.newDesign()
        XCTAssertTrue(assistant.items.isEmpty)
        XCTAssertTrue(assistant.history.isEmpty)
        XCTAssertEqual(tools.resets, 1)
    }

    func testToolTitlesReadNaturally() {
        XCTAssertEqual(DesignAssistant.title(forTool: "waffle_slice"), "Waffle slice")
        XCTAssertEqual(DesignAssistant.title(forTool: "export_cut_files"), "Export cut files")
    }
}
