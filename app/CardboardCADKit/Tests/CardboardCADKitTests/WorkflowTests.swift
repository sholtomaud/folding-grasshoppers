import XCTest
@testable import CardboardCADKit

// MARK: - Fakes

final class FakeWorkflowClient: WorkflowSettingsClient {
    var list = WorkflowList(active: "Standard", applyDefaults: true, folder: "/Users/t/Documents/CardboardCAD/Workflows",
                            workflows: [WorkflowSummary(name: "Standard", description: "Design, nest, cost", builtin: true),
                                        WorkflowSummary(name: "Big board", description: "", builtin: false)])
    var failWith: Error?
    private(set) var updates: [(String?, Bool?)] = []

    func workflows() async throws -> WorkflowList {
        if let failWith { throw failWith }
        return list
    }

    func updateWorkflowSettings(active: String?, applyDefaults: Bool?) async throws -> WorkflowList {
        if let failWith { throw failWith }
        updates.append((active, applyDefaults))
        if let active { list.active = active }
        if let applyDefaults { list.applyDefaults = applyDefaults }
        return list
    }
}

private func toolUse(_ id: String, _ name: String) -> JSONValue {
    ["type": "tool_use", "id": .string(id), "name": .string(name), "input": [:]]
}

// MARK: - EngineClient routes

final class WorkflowClientTests: XCTestCase {
    var client: EngineClient!

    override func setUp() {
        StubURLProtocol.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        client = EngineClient(port: 5001, session: URLSession(configuration: config))
    }

    func testWorkflowsDecodesTheList() async throws {
        StubURLProtocol.stub("GET", "/api/workflows", json: """
        {"active":"Standard","apply_defaults":true,"folder":"/u/Documents/CardboardCAD/Workflows",
         "workflows":[{"name":"Standard","description":"Design, nest, cost","builtin":true,"steps":6}]}
        """)
        let list = try await client.workflows()
        XCTAssertEqual(list.active, "Standard")
        XCTAssertTrue(list.applyDefaults)
        XCTAssertEqual(list.workflows, [WorkflowSummary(name: "Standard", description: "Design, nest, cost", builtin: true)])
    }

    func testUpdateSendsOnlyWhatChanged() async throws {
        StubURLProtocol.stub("POST", "/api/workflows/settings", json: """
        {"active":"Standard","apply_defaults":false,"folder":"/w","workflows":[]}
        """)
        let list = try await client.updateWorkflowSettings(active: nil, applyDefaults: false)
        XCTAssertFalse(list.applyDefaults)
        let body = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(StubURLProtocol.requests.last?.httpBody))
        XCTAssertEqual(body, ["apply_defaults": false])
        _ = try await client.updateWorkflowSettings(active: "Big board", applyDefaults: nil)
        let body2 = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(StubURLProtocol.requests.last?.httpBody))
        XCTAssertEqual(body2, ["active": "Big board"])
    }

    func testUnknownWorkflowCarriesTheEnginesMessage() async {
        StubURLProtocol.stub("POST", "/api/workflows/settings", status: 422,
                             json: #"{"error":"invalid_input","message":"There is no workflow called Nope"}"#)
        do {
            _ = try await client.updateWorkflowSettings(active: "Nope", applyDefaults: nil)
            XCTFail("expected an error")
        } catch {
            XCTAssertEqual(error as? EngineClientError, .invalidInput("There is no workflow called Nope"))
        }
    }

    func testToolOutputSaysWhenTheSetupChanged() async throws {
        StubURLProtocol.stub("POST", "/api/design/tool",
                             json: #"{"content":"Saved","is_error":false,"version":3,"setup_changed":true}"#)
        let out = try await client.runDesignTool(name: "save_workflow", input: [:])
        XCTAssertTrue(out.setupChanged)
        StubURLProtocol.stub("POST", "/api/design/tool", json: #"{"content":"ok","is_error":false,"version":4}"#)
        let old = try await client.runDesignTool(name: "model_info", input: [:])
        XCTAssertFalse(old.setupChanged, "older engines do not send it")
    }
}

// MARK: - The assistant picks up a changed workflow

@MainActor
final class WorkflowAssistantTests: XCTestCase {
    func testSetupIsFetchedAgainAfterAWorkflowTool() async {
        let tools = FakeDesignTools()
        tools.results["use_workflow"] = DesignToolOutput(content: "Using Big board", isError: false, version: 1,
                                                         setupChanged: true)
        let model = ScriptedModel([
            .success(ModelResponse(content: [toolUse("t1", "use_workflow")], stopReason: "tool_use", model: nil)),
            .success(ModelResponse(content: [["type": "text", "text": "Done"]], stopReason: "end_turn", model: nil)),
        ])
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: tools)
        var changes = 0
        assistant.onSetupChanged = { changes += 1 }
        await assistant.send("use the big board workflow")
        XCTAssertEqual(tools.setupCalls, 2, "the next request in the same turn uses the new workflow")
        XCTAssertEqual(model.requests.count, 2)
        XCTAssertEqual(changes, 1)
    }

    func testReloadSetupMakesTheNextMessageFetchIt() async {
        let tools = FakeDesignTools()
        let assistant = DesignAssistant(model: ScriptedModel([]))
        assistant.attach(tools: tools)
        await assistant.send("a")
        assistant.reloadSetup()
        await assistant.send("b")
        XCTAssertEqual(tools.setupCalls, 2)
    }
}

// MARK: - The toggle and picker

@MainActor
final class WorkflowControllerTests: XCTestCase {
    func testRefreshLoadsTheList() async {
        let client = FakeWorkflowClient()
        let c = WorkflowController()
        c.attach(client: client)
        await c.refresh()
        XCTAssertEqual(c.active, "Standard")
        XCTAssertTrue(c.applyDefaults)
        XCTAssertEqual(c.names, ["Standard", "Big board"])
        XCTAssertNil(c.error)
    }

    func testToggleApplyDefaultsTellsTheEngineAndTheAssistant() async {
        let client = FakeWorkflowClient()
        let c = WorkflowController()
        var changed = 0
        c.onChange = { changed += 1 }
        c.attach(client: client)
        await c.refresh()
        await c.setApplyDefaults(false)
        XCTAssertFalse(c.applyDefaults)
        XCTAssertEqual(client.updates.count, 1)
        XCTAssertEqual(client.updates.first?.1, false)
        XCTAssertEqual(changed, 1)
        await c.setApplyDefaults(false)
        XCTAssertEqual(client.updates.count, 1, "no call when nothing changes")
    }

    func testChooseSwitchesTheActiveWorkflow() async {
        let client = FakeWorkflowClient()
        let c = WorkflowController()
        c.attach(client: client)
        await c.refresh()
        await c.choose("Big board")
        XCTAssertEqual(c.active, "Big board")
        XCTAssertEqual(client.updates.first?.0, "Big board")
    }

    func testFailuresKeepTheOldValueAndShowAnError() async {
        let client = FakeWorkflowClient()
        let c = WorkflowController()
        c.attach(client: client)
        await c.refresh()
        client.failWith = Boom()
        await c.setApplyDefaults(false)
        XCTAssertTrue(c.applyDefaults)
        XCTAssertEqual(c.error, "network down")
    }

    func testWithoutAnEngineNothingIsLoaded() async {
        let c = WorkflowController()
        await c.refresh()
        XCTAssertTrue(c.names.isEmpty)
        await c.setApplyDefaults(false)
        XCTAssertNotNil(c.error)
    }

    func testDefaultFolderIsBesideProjects() {
        let home = URL(fileURLWithPath: "/Users/t", isDirectory: true)
        XCTAssertEqual(WorkflowController.defaultWorkflowsFolder(home: home).path, "/Users/t/Documents/Fold Theory/Workflows")
    }
}
