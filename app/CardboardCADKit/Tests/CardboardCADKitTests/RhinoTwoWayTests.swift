import XCTest
@testable import CardboardCADKit

/// Design tools that also have Rhino edits waiting for the assistant (issue #40).
final class NotesDesignTools: DesignToolRunner {
    var notes: [[String]] = []
    private(set) var noteCalls = 0

    func designSetup() async throws -> DesignSetup { DesignSetup(system: "SYSTEM", tools: []) }
    func runDesignTool(name: String, input: JSONValue) async throws -> DesignToolOutput {
        DesignToolOutput(content: "\(name) done", isError: false, version: 1)
    }
    func resetDesign() async throws {}
    func pendingDesignNotes() async throws -> [String] {
        noteCalls += 1
        return notes.isEmpty ? [] : notes.removeFirst()
    }
}

@MainActor
final class RhinoNotesAssistantTests: XCTestCase {
    func testRhinoEditsAreToldToClaudeBeforeTheDesignersMessage() async throws {
        let model = ScriptedModel([
            .success(ModelResponse(content: [["type": "text", "text": "OK"]], stopReason: "end_turn", model: nil)),
            .success(ModelResponse(content: [["type": "text", "text": "OK"]], stopReason: "end_turn", model: nil)),
        ])
        let tools = NotesDesignTools()
        tools.notes = [["In Rhino, the designer edited part X3 (1 more hole)."]]
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: tools)

        await assistant.send("make the ribs thicker")

        let first = try XCTUnwrap(model.requests.first?.messages.first?["content"]?.stringValue)
        XCTAssertTrue(first.contains("X3 (1 more hole)"), first)
        XCTAssertTrue(first.hasSuffix("make the ribs thicker"), first)
        // The designer sees what the assistant was told.
        XCTAssertTrue(assistant.items.contains { $0.kind == .notice && $0.text.contains("X3") })
        // The chat bubble shows only what the designer typed.
        XCTAssertEqual(assistant.items.first { $0.kind == .user }?.text, "make the ribs thicker")

        // No new notes: the next message goes as typed.
        await assistant.send("thanks")
        let second = model.requests.last?.messages.last { $0["role"]?.stringValue == "user" }
        XCTAssertEqual(second?["content"]?.stringValue, "thanks")
        XCTAssertEqual(tools.noteCalls, 2)
    }

    func testOtherDesignToolsHaveNoNotes() async throws {
        let model = ScriptedModel([
            .success(ModelResponse(content: [["type": "text", "text": "OK"]], stopReason: "end_turn", model: nil)),
        ])
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: FakeDesignTools())
        await assistant.send("hello")
        XCTAssertEqual(model.requests.first?.messages.first?["content"]?.stringValue, "hello")
    }
}

final class RhinoClientTests: XCTestCase {
    var client: EngineClient!

    override func setUp() {
        StubURLProtocol.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        client = EngineClient(port: 5001, session: URLSession(configuration: config))
    }

    func testPendingNotesDecode() async throws {
        StubURLProtocol.stub("GET", "/api/design/notes", json: #"{"notes":["In Rhino, the designer edited the model."]}"#)
        let notes = try await client.pendingDesignNotes()
        XCTAssertEqual(notes, ["In Rhino, the designer edited the model."])
    }

    func testFoldTheoryScriptURL() {
        XCTAssertEqual(client.rhinoScriptURL.absoluteString, "http://127.0.0.1:5001/rhino/fold_theory.py")
    }
}
