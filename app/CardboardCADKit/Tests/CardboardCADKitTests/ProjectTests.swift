import XCTest
@testable import CardboardCADKit

// MARK: - Fakes

final class FakeProjectStore: DesignProjectStore {
    var saveResult: Result<ProjectSaved, Error> = .success(
        ProjectSaved(name: "Stool", folder: "/Users/t/Documents/CardboardCAD/Projects/Stool",
                     files: ["project.json"], version: 3))
    var openResult: Result<ProjectOpened, Error> = .success(
        ProjectOpened(name: "Stool", folder: "/Users/t/Documents/CardboardCAD/Projects/Stool", chat: nil, version: 7))
    private(set) var saves: [(name: String, folder: String, chat: JSONValue?)] = []
    private(set) var opens: [String] = []

    func saveProject(name: String, folder: String, chat: JSONValue?) async throws -> ProjectSaved {
        saves.append((name, folder, chat))
        return try saveResult.get()
    }

    func openProject(folder: String) async throws -> ProjectOpened {
        opens.append(folder)
        return try openResult.get()
    }
}

private func textBlock(_ s: String) -> JSONValue { ["type": "text", "text": .string(s)] }

@MainActor
private func chattedAssistant() async -> DesignAssistant {
    let assistant = DesignAssistant(model: ScriptedModel([
        .success(ModelResponse(content: [
            textBlock("Making it."),
            ["type": "tool_use", "id": "t1", "name": "create_shape", "input": ["shape": "cylinder"]],
        ], stopReason: "tool_use", model: nil)),
        .success(ModelResponse(content: [textBlock("Done.")], stopReason: "end_turn", model: nil)),
    ]))
    assistant.attach(tools: FakeDesignTools())
    await assistant.send("a cylinder stool")
    return assistant
}

// MARK: - EngineClient routes

final class ProjectClientTests: XCTestCase {
    var client: EngineClient!

    override func setUp() {
        StubURLProtocol.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        client = EngineClient(port: 5001, session: URLSession(configuration: config))
    }

    func testSavePostsNameFolderAndChat() async throws {
        StubURLProtocol.stub("POST", "/api/design/save", json: """
        {"name":"Acme - A","folder":"/Users/t/Documents/CardboardCAD/Projects/Acme - A",
         "files":["model/model.obj","project.json"],"version":4}
        """)
        let saved = try await client.saveProject(name: "Acme - A", folder: "", chat: ["items": []])
        XCTAssertEqual(saved, ProjectSaved(name: "Acme - A", folder: "/Users/t/Documents/CardboardCAD/Projects/Acme - A",
                                           files: ["model/model.obj", "project.json"], version: 4))
        let body = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(StubURLProtocol.requests.last?.httpBody))
        XCTAssertEqual(body, ["name": "Acme - A", "folder": "", "chat": ["items": []]])
    }

    func testSaveErrorsCarryTheEnginesMessage() async {
        StubURLProtocol.stub("POST", "/api/design/save", status: 422,
                             json: #"{"error":"invalid_input","message":"There is nothing to save yet"}"#)
        do {
            _ = try await client.saveProject(name: "x", folder: "", chat: nil)
            XCTFail("expected an error")
        } catch {
            XCTAssertEqual(error as? EngineClientError, .invalidInput("There is nothing to save yet"))
        }
    }

    func testOpenPostsTheFolderAndDecodesTheChat() async throws {
        StubURLProtocol.stub("POST", "/api/design/open", json: """
        {"name":"Stool","folder":"/p/Stool","chat":{"items":[{"kind":"user","text":"hi"}]},"version":9,"summary":{}}
        """)
        let opened = try await client.openProject(folder: "/p/Stool")
        XCTAssertEqual(opened.name, "Stool")
        XCTAssertEqual(opened.version, 9)
        XCTAssertEqual(opened.chat?["items"]?.arrayValue?.count, 1)
        let body = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(StubURLProtocol.requests.last?.httpBody))
        XCTAssertEqual(body, ["folder": "/p/Stool"])
    }

    func testOpenWithoutChat() async throws {
        StubURLProtocol.stub("POST", "/api/design/open", json: #"{"name":"S","folder":"/p/S","chat":null,"version":2}"#)
        let opened = try await client.openProject(folder: "/p/S")
        XCTAssertNil(opened.chat)
    }

    func testLiveListenerURL() {
        XCTAssertEqual(client.liveListenerURL.absoluteString, "http://127.0.0.1:5001/rhino/live_listener.py")
    }
}

// MARK: - Conversation save / restore

@MainActor
final class ConversationTests: XCTestCase {
    func testConversationRoundTrips() async {
        let assistant = await chattedAssistant()
        let saved = assistant.conversation
        XCTAssertEqual(saved["schema"], "cardboardcad.chat/1")

        let other = DesignAssistant(model: ScriptedModel([]))
        XCTAssertTrue(other.restore(conversation: saved))
        XCTAssertEqual(other.items.map(\.kind), assistant.items.map(\.kind))
        XCTAssertEqual(other.items.map(\.text), assistant.items.map(\.text))
        XCTAssertEqual(other.history, assistant.history)
    }

    func testToolItemsKeepNameAndError() {
        let a = DesignAssistant(model: ScriptedModel([]))
        let ok = a.restore(conversation: [
            "schema": "cardboardcad.chat/1",
            "items": [["kind": "tool", "tool": "waffle_slice", "is_error": true, "text": "no model"]],
            "history": [],
        ])
        XCTAssertTrue(ok)
        XCTAssertEqual(a.items.first?.kind, .tool(name: "waffle_slice", isError: true))
    }

    func testRestoringSomethingElseClearsAndSaysSo() async {
        let a = await chattedAssistant()
        XCTAssertFalse(a.restore(conversation: "nope"))
        XCTAssertTrue(a.items.isEmpty)
        XCTAssertTrue(a.history.isEmpty)
    }

    func testRestoreDropsAHalfFinishedToolTurn() {
        // History must end on a complete turn, or Claude rejects the next message.
        let a = DesignAssistant(model: ScriptedModel([]))
        _ = a.restore(conversation: [
            "schema": "cardboardcad.chat/1",
            "items": [],
            "history": [
                ["role": "user", "content": "hi"],
                ["role": "assistant", "content": [["type": "tool_use", "id": "t", "name": "x", "input": [:]]]],
            ],
        ])
        XCTAssertEqual(a.history, [])
    }
}

// MARK: - ProjectController

@MainActor
final class ProjectControllerTests: XCTestCase {
    func testSaveAsSendsTheChatAndRemembersTheProject() async {
        let store = FakeProjectStore()
        let assistant = await chattedAssistant()
        let projects = ProjectController(assistant: assistant)
        projects.attach(store: store)
        var snapshotFolder: URL?
        projects.afterSave = { snapshotFolder = $0 }

        let ok = await projects.save(name: "Stool")
        XCTAssertTrue(ok)
        XCTAssertEqual(store.saves.first?.name, "Stool")
        XCTAssertEqual(store.saves.first?.folder, "")
        XCTAssertEqual(store.saves.first?.chat, assistant.conversation)
        XCTAssertEqual(projects.current?.name, "Stool")
        XCTAssertEqual(projects.current?.folder, "/Users/t/Documents/CardboardCAD/Projects/Stool")
        XCTAssertEqual(snapshotFolder?.path, "/Users/t/Documents/CardboardCAD/Projects/Stool")
        XCTAssertNil(projects.error)
        XCTAssertEqual(projects.lastMessage, "Saved Stool")
    }

    func testSaveAgainUsesTheSameFolder() async {
        let store = FakeProjectStore()
        let projects = ProjectController(assistant: DesignAssistant(model: ScriptedModel([])))
        projects.attach(store: store)
        XCTAssertFalse(projects.canSaveAgain)
        _ = await projects.save(name: "Stool")
        XCTAssertTrue(projects.canSaveAgain)
        _ = await projects.saveAgain()
        XCTAssertEqual(store.saves.last?.folder, "/Users/t/Documents/CardboardCAD/Projects/Stool")
        XCTAssertEqual(store.saves.last?.name, "Stool")
    }

    func testOpenRestoresTheChat() async {
        let store = FakeProjectStore()
        let source = await chattedAssistant()
        store.openResult = .success(ProjectOpened(name: "Stool", folder: "/p/Stool", chat: source.conversation, version: 5))
        let assistant = DesignAssistant(model: ScriptedModel([]))
        let projects = ProjectController(assistant: assistant)
        projects.attach(store: store)

        let ok = await projects.open(folder: "/p/Stool")
        XCTAssertTrue(ok)
        XCTAssertEqual(store.opens, ["/p/Stool"])
        XCTAssertEqual(projects.current, ProjectRef(name: "Stool", folder: "/p/Stool"))
        XCTAssertEqual(assistant.items.map(\.text), source.items.map(\.text))
    }

    func testOpenWithoutAChatStartsAFreshChatWithANote() async {
        let store = FakeProjectStore()
        let assistant = await chattedAssistant()
        let projects = ProjectController(assistant: assistant)
        projects.attach(store: store)
        _ = await projects.open(folder: "/p/Stool")
        XCTAssertTrue(assistant.history.isEmpty)
        XCTAssertEqual(assistant.items.count, 1)
        XCTAssertEqual(assistant.items.first?.kind, .notice)
    }

    func testErrorsAreShownAndNothingChanges() async {
        let store = FakeProjectStore()
        store.saveResult = .failure(EngineClientError.invalidInput("There is nothing to save yet"))
        store.openResult = .failure(EngineClientError.invalidInput("not a CardboardCAD project"))
        let projects = ProjectController(assistant: DesignAssistant(model: ScriptedModel([])))
        projects.attach(store: store)
        let saved = await projects.save(name: "X")
        XCTAssertFalse(saved)
        XCTAssertEqual(projects.error, "There is nothing to save yet")
        XCTAssertNil(projects.current)
        let opened = await projects.open(folder: "/nope")
        XCTAssertFalse(opened)
        XCTAssertEqual(projects.error, "not a CardboardCAD project")
    }

    func testWithoutTheEngineNothingHappens() async {
        let projects = ProjectController(assistant: DesignAssistant(model: ScriptedModel([])))
        let ok = await projects.save(name: "X")
        XCTAssertFalse(ok)
        XCTAssertNotNil(projects.error)
    }

    func testForgetClearsTheCurrentProject() async {
        let store = FakeProjectStore()
        let projects = ProjectController(assistant: DesignAssistant(model: ScriptedModel([])))
        projects.attach(store: store)
        _ = await projects.save(name: "Stool")
        projects.forget()
        XCTAssertNil(projects.current)
        XCTAssertFalse(projects.canSaveAgain)
    }

    func testDefaultFolderIsDocumentsProjects() {
        let home = URL(fileURLWithPath: "/Users/t")
        XCTAssertEqual(ProjectController.defaultProjectsFolder(home: home).path,
                       "/Users/t/Documents/Fold Theory/Projects")
    }
}
