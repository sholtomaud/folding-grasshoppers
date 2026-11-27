import XCTest
@testable import CardboardCADKit

// The design brief (#44): the Brief view's fields edit the brief through the engine,
// and its Check again button runs check_brief.

final class FakeBriefData: ProjectDataClient, BriefClient {
    let base = FakeProjectData()
    var briefError: Error?
    private(set) var briefCalls: [JSONValue] = []

    func settingsList() async throws -> SettingsList { try await base.settingsList() }
    func setSetting(key: String, value: JSONValue, scope: String, setBy: String) async throws -> SettingsList {
        try await base.setSetting(key: key, value: value, scope: scope, setBy: setBy)
    }
    func viewList() async throws -> [ViewSummary] { try await base.viewList() }
    func renderView(id: String) async throws -> RenderedView { try await base.renderView(id: id) }
    func undoView(id: String, scope: String) async throws { try await base.undoView(id: id, scope: scope) }
    func diagnostics(source: String?, level: String?, limit: Int) async throws -> [DiagnosticEntry] {
        try await base.diagnostics(source: source, level: level, limit: limit)
    }
    func runViewAction(_ action: String) async throws -> DesignToolOutput { try await base.runViewAction(action) }

    func setBrief(values: JSONValue) async throws -> JSONValue {
        if let briefError { throw briefError }
        briefCalls.append(values)
        return ["values": values]
    }
}

final class BriefClientTests: XCTestCase {
    func testSetBriefPostsTheValues() async throws {
        StubURLProtocol.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        let client = EngineClient(port: 5001, session: URLSession(configuration: config))
        StubURLProtocol.stub("POST", "/api/brief", json: #"{"values":{"max_height_mm":2400},"checks":[],"fields":[]}"#)
        _ = try await client.setBrief(values: ["max_height_mm": 2400])
        let body = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(StubURLProtocol.requests.last?.httpBody))
        XCTAssertEqual(body, ["values": ["max_height_mm": 2400]])
    }
}

@MainActor
final class BriefViewTests: XCTestCase {
    func testNumbersTextAndClearing() async {
        let c = ProjectViewsController()
        let fake = FakeBriefData()
        c.attach(client: fake)
        let height = await c.setBrief(key: "max_height_mm", text: "2,400")
        let branding = await c.setBrief(key: "branding", text: " Acme blue ")
        let cleared = await c.setBrief(key: "budget", text: "")
        XCTAssertTrue(height)
        XCTAssertTrue(branding)
        XCTAssertTrue(cleared)
        XCTAssertEqual(fake.briefCalls, [["max_height_mm": 2400], ["branding": "Acme blue"], ["budget": nil]])
    }

    func testARefusedValueShowsTheEnginesMessage() async {
        let c = ProjectViewsController()
        let fake = FakeBriefData()
        fake.briefError = EngineClientError.invalidInput("seats must be a whole number")
        c.attach(client: fake)
        let ok = await c.setBrief(key: "seats", text: "2.5")
        XCTAssertFalse(ok)
        XCTAssertEqual(c.error, "seats must be a whole number")
    }

    func testWithoutABriefClientNothingIsSent() async {
        let c = ProjectViewsController()
        c.attach(client: FakeProjectData())
        let ok = await c.setBrief(key: "seats", text: "4")
        XCTAssertFalse(ok)
        XCTAssertNotNil(c.error)
    }

    func testCheckBriefIsADesignToolAction() async {
        let c = ProjectViewsController()
        let fake = FakeBriefData()
        c.attach(client: fake)
        await c.run(action: "check_brief")
        XCTAssertEqual(fake.base.actions, ["check_brief"])
    }

    func testBriefBindingsAreEditableFields() {
        XCTAssertTrue(ViewBinding.isEditable("brief.max_height_mm"))
        XCTAssertTrue(ViewBinding.isEditable("settings.sheet_price"))
        XCTAssertFalse(ViewBinding.isEditable("quote.total"))
    }
}
