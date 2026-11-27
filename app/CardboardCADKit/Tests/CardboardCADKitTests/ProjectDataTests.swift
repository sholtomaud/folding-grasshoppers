import XCTest
@testable import CardboardCADKit

// Settings, input forms, the unverified-number guard, JSON views and diagnostics (#26-#30, #34, #61).

private func text(_ s: String) -> JSONValue { ["type": "text", "text": .string(s)] }
private func toolUse(_ id: String, _ name: String, _ input: JSONValue) -> JSONValue {
    ["type": "tool_use", "id": .string(id), "name": .string(name), "input": input]
}

private let sheetPriceForm = InputForm(
    id: "f1", question: "What does a sheet of 10 mm honeycomb cost?",
    fields: [InputFormField(key: "sheet_price", label: "Sheet price", type: "currency", unit: "AUD/sheet", minimum: 0)])

/// A tool runner that also answers forms, like the engine.
final class FormTools: DesignToolRunner {
    var results: [String: DesignToolOutput] = [:]
    var answer = FormSubmission(ok: true, message: "I filled in the form: sheet_price set for this project.",
                                keys: ["sheet_price"])
    private(set) var submitted: [(String, [String: String], String, String)] = []
    private(set) var calls: [String] = []

    func designSetup() async throws -> DesignSetup { DesignSetup(system: "SYSTEM", tools: []) }

    func runDesignTool(name: String, input: JSONValue) async throws -> DesignToolOutput {
        calls.append(name)
        return results[name] ?? DesignToolOutput(content: "\(name) done", isError: false, version: calls.count)
    }

    func resetDesign() async throws {}

    func submitInputForm(id: String, values: [String: String], scope: String, setBy: String) async throws
        -> FormSubmission {
        submitted.append((id, values, scope, setBy))
        return answer
    }
}

final class NumberGuardTests: XCTestCase {
    func testANumberTheEngineGaveIsNotFlagged() {
        XCTAssertEqual(NumberGuard.unverified(reply: "Total 512.40", known: ["Material 115.50, Total 512.40."]), [])
        XCTAssertEqual(NumberGuard.unverified(reply: "Total 512.4 AUD", known: ["Total 512.40"]), [])
    }

    func testANumberClaudeMadeUpIsFlagged() {
        XCTAssertEqual(NumberGuard.unverified(reply: "That comes to about 500.", known: ["Total 512.40"]), ["500"])
        XCTAssertEqual(NumberGuard.unverified(reply: "Roughly $48 a sheet", known: ["Total 512.40"]), ["48"])
    }

    func testThousandsSeparatorsAndRoundingToTheDigitsShown() {
        XCTAssertEqual(NumberGuard.unverified(reply: "Total 1,234.50", known: ["Total 1234.50"]), [])
        XCTAssertEqual(NumberGuard.unverified(reply: "Machine time 12.4 min", known: ["time_min 12.437"]), [])
    }

    func testWhatTheDesignerTypedCounts() {
        XCTAssertEqual(NumberGuard.unverified(reply: "A 450 mm stool", known: ["make a stool 450 mm tall"]), [])
    }

    func testNamesListMarkersAndSmallCountsAreNotQuantities() {
        XCTAssertEqual(NumberGuard.unverified(reply: "1. Make a 3D model, v2 of rib X3\n2. Pick 2 options",
                                              known: []), [])
    }

    func testSmallQuantitiesWithAUnitAreChecked() {
        XCTAssertEqual(NumberGuard.unverified(reply: "It needs 5 sheets", known: ["4 sheet(s)"]), ["5"])
    }
}

@MainActor
final class InputFormTests: XCTestCase {
    func testToolOutputCarriesTheForm() throws {
        let json = """
        {"content": "A form asking for Sheet price is shown", "is_error": false, "version": 4,
         "form": {"id": "abc", "question": "Sheet price?", "scopes": ["project", "all"], "scope": "project",
                  "fields": [{"key": "sheet_price", "label": "Sheet price", "type": "currency",
                              "unit": "AUD/sheet", "minimum": 0, "maximum": null, "help": "",
                              "current": null, "current_layer": null}]}}
        """
        let out = try JSONDecoder().decode(DesignToolOutput.self, from: Data(json.utf8))
        XCTAssertEqual(out.form?.id, "abc")
        XCTAssertEqual(out.form?.fields.first?.unit, "AUD/sheet")
        XCTAssertNil(out.form?.fields.first?.current)
        let plain = try JSONDecoder().decode(DesignToolOutput.self,
                                             from: Data(#"{"content": "x", "is_error": false, "version": 1}"#.utf8))
        XCTAssertNil(plain.form)
    }

    func testRequestInputsShowsAFormInTheChat() async {
        let model = ScriptedModel([
            .success(ModelResponse(content: [toolUse("t1", "request_inputs", ["fields": [["key": "sheet_price"]]])],
                                   stopReason: "tool_use", model: nil)),
            .success(ModelResponse(content: [text("Please fill in the sheet price.")], stopReason: "end_turn",
                                   model: nil)),
        ])
        let tools = FormTools()
        tools.results["request_inputs"] = DesignToolOutput(content: "A form is shown. Stop and wait.", isError: false,
                                                           version: 1, form: sheetPriceForm)
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: tools)
        await assistant.send("How much will it cost?")
        let forms = assistant.items.compactMap { item -> InputForm? in
            if case .form(let f) = item.kind { return f }
            return nil
        }
        XCTAssertEqual(forms, [sheetPriceForm])
    }

    func testSubmittingTheFormTellsClaudeWhichKeysWereSetNotTheNumbers() async throws {
        let model = ScriptedModel([
            .success(ModelResponse(content: [toolUse("t1", "request_inputs", ["fields": [["key": "sheet_price"]]])],
                                   stopReason: "tool_use", model: nil)),
            .success(ModelResponse(content: [text("Waiting for the form.")], stopReason: "end_turn", model: nil)),
            .success(ModelResponse(content: [text("Thanks.")], stopReason: "end_turn", model: nil)),
        ])
        let tools = FormTools()
        tools.results["request_inputs"] = DesignToolOutput(content: "Form shown", isError: false, version: 1,
                                                           form: sheetPriceForm)
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: tools)
        await assistant.send("Cost it")
        let item = try XCTUnwrap(assistant.items.first { if case .form = $0.kind { return true } else { return false } })

        let answer = await assistant.submitForm(itemID: item.id, form: sheetPriceForm, values: ["sheet_price": "38.50"],
                                                scope: "project", setBy: "SM")
        XCTAssertEqual(answer?.ok, true)
        XCTAssertEqual(tools.submitted.first?.1, ["sheet_price": "38.50"])
        XCTAssertEqual(tools.submitted.first?.2, "project")
        XCTAssertEqual(tools.submitted.first?.3, "SM")
        // The form became a note, and Claude's next message names the key but not the price.
        XCTAssertFalse(assistant.items.contains { if case .form = $0.kind { return true } else { return false } })
        let last = try XCTUnwrap(model.requests.last?.messages.last { $0["role"]?.stringValue == "user" })
        let sent = last["content"]?.stringValue ?? ""
        XCTAssertTrue(sent.contains("sheet_price"))
        XCTAssertFalse(sent.contains("38.5"))
    }

    func testARefusedAnswerKeepsTheFormAndSendsNothing() async throws {
        let tools = FormTools()
        tools.answer = FormSubmission(ok: false, message: "Sheet price must be a number",
                                      errors: ["sheet_price": "Sheet price must be a number (got 'abc')"])
        let model = ScriptedModel([])
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: tools)
        let answer = await assistant.submitForm(itemID: UUID(), form: sheetPriceForm, values: ["sheet_price": "abc"],
                                                scope: "project", setBy: "SM")
        XCTAssertEqual(answer?.ok, false)
        XCTAssertEqual(answer?.errors["sheet_price"], "Sheet price must be a number (got 'abc')")
        XCTAssertTrue(model.requests.isEmpty)
    }

    func testUnverifiedNumbersInARepliesAreMarked() async throws {
        let model = ScriptedModel([
            .success(ModelResponse(content: [toolUse("t1", "estimate_cost", [:])], stopReason: "tool_use", model: nil)),
            .success(ModelResponse(content: [text("Quote total 512.40, so about 500 each time.")],
                                   stopReason: "end_turn", model: nil)),
        ])
        let tools = FormTools()
        tools.results["estimate_cost"] = DesignToolOutput(content: "Quote total 512.40 AUD", isError: false, version: 2)
        let assistant = DesignAssistant(model: model)
        assistant.attach(tools: tools)
        await assistant.send("cost it")
        let reply = try XCTUnwrap(assistant.items.last { $0.kind == .assistant })
        XCTAssertEqual(reply.unverified, ["500"])
    }

    func testASavedChatKeepsAFormAsANote() async {
        let assistant = DesignAssistant(model: ScriptedModel([]))
        assistant.replaceConversation(items: [.init(kind: .form(sheetPriceForm), text: sheetPriceForm.question)],
                                      history: [])
        let shown = assistant.conversation["items"]?.arrayValue ?? []
        XCTAssertEqual(shown.first?["kind"], "notice")
        XCTAssertEqual(shown.first?["text"]?.stringValue?.contains("honeycomb"), true)
    }
}

// MARK: - JSON views and the Project tab

final class FakeProjectData: ProjectDataClient {
    var views = [ViewSummary(id: "costs", title: "Costs", source: "default"), ViewSummary(id: "project", title: "Project")]
    var settingError: Error?
    private(set) var setCalls: [(String, JSONValue, String, String)] = []
    private(set) var actions: [String] = []
    private(set) var undos: [String] = []

    func settingsList() async throws -> SettingsList { SettingsList(settings: []) }

    func setSetting(key: String, value: JSONValue, scope: String, setBy: String) async throws -> SettingsList {
        if let settingError { throw settingError }
        setCalls.append((key, value, scope, setBy))
        return SettingsList(settings: [])
    }

    func viewList() async throws -> [ViewSummary] { views }

    func renderView(id: String) async throws -> RenderedView {
        RenderedView(view: ViewDocument(schema: "cardboardcad.view/1", id: id, title: id.capitalized,
                                        description: nil, blocks: [ViewBlock(type: "total", bind: "quote.total")]),
                     data: ["quote.total": 512.4])
    }

    func undoView(id: String, scope: String) async throws { undos.append(id) }

    func diagnostics(source: String?, level: String?, limit: Int) async throws -> [DiagnosticEntry] {
        [DiagnosticEntry(id: 1, time: "2026-11-27T03:40:00Z", source: source ?? "grasshopper", level: "info",
                         message: "LiveDesign listener turned off", designVersion: 4)]
    }

    func runViewAction(_ action: String) async throws -> DesignToolOutput {
        actions.append(action)
        return DesignToolOutput(content: "done", isError: false, version: 5)
    }
}

@MainActor
final class ProjectViewsTests: XCTestCase {
    func testTheProjectViewDecodesFromTheEnginesJSON() throws {
        let json = """
        {"view": {"schema": "cardboardcad.view/1", "id": "project", "title": "Project", "blocks": [
            {"type": "section", "title": "Quote", "blocks": [
                {"type": "table", "bind": "quote.lines", "columns": ["label", "value", "unit"]},
                {"type": "total", "bind": "quote.total", "label": "Total incl. GST"},
                {"type": "button", "label": "Work out again", "action": "estimate_cost"}]},
            {"type": "tabs", "tabs": [{"title": "More", "blocks": [{"type": "text", "text": "hi", "id": "note"}]}]}]},
         "data": {"quote.total": 512.4, "quote.lines": [{"label": "Board", "value": 115.5, "unit": "AUD"}]}}
        """
        let r = try JSONDecoder().decode(RenderedView.self, from: Data(json.utf8))
        XCTAssertEqual(r.view.id, "project")
        XCTAssertEqual(r.view.blocks[0].blocks?.count, 3)
        XCTAssertEqual(r.view.blocks[0].blocks?[0].columns, ["label", "value", "unit"])
        XCTAssertEqual(r.view.blocks[0].blocks?[2].action, "estimate_cost")
        XCTAssertEqual(r.view.blocks[1].tabs?.first?.blocks.first?.blockID, "note")
        XCTAssertEqual(r.data["quote.total"], 512.4)
    }

    func testRefreshShowsTheProjectViewFirst() async {
        let c = ProjectViewsController()
        let fake = FakeProjectData()
        c.attach(client: fake)
        await c.refresh()
        XCTAssertEqual(c.selected, "project")
        XCTAssertEqual(c.rendered?.view.id, "project")
        XCTAssertEqual(c.value("quote.total"), 512.4)
        await c.select("costs")
        XCTAssertEqual(c.rendered?.view.title, "Costs")
    }

    func testEditingASettingSendsANumberAndARefusalIsShown() async {
        let c = ProjectViewsController()
        let fake = FakeProjectData()
        c.attach(client: fake)
        let ok = await c.setSetting(key: "sheet_price", text: " 38.50 ", setBy: "SM")
        XCTAssertTrue(ok)
        XCTAssertEqual(fake.setCalls.first?.0, "sheet_price")
        XCTAssertEqual(fake.setCalls.first?.1, .number(38.5))
        XCTAssertEqual(fake.setCalls.first?.2, "project")
        fake.settingError = EngineClientError.invalidInput("Sheet price must not be below 0 AUD/sheet (got -5)")
        let refused = await c.setSetting(key: "sheet_price", text: "-5", setBy: "SM")
        XCTAssertFalse(refused)
        XCTAssertEqual(c.error, "Sheet price must not be below 0 AUD/sheet (got -5)")
    }

    func testButtonsRunDesignToolsOrAppActions() async {
        let c = ProjectViewsController()
        let fake = FakeProjectData()
        c.attach(client: fake)
        var appActions: [String] = []
        c.onAppAction = { appActions.append($0) }
        await c.run(action: "estimate_cost")
        await c.run(action: "save_project")
        XCTAssertEqual(fake.actions, ["estimate_cost"])
        XCTAssertEqual(appActions, ["save_project"])
    }

    func testUndoAndDiagnostics() async {
        let c = ProjectViewsController()
        let fake = FakeProjectData()
        c.attach(client: fake)
        await c.select("costs")
        await c.undo(scope: "all")
        XCTAssertEqual(fake.undos, ["costs"])
        await c.refreshDiagnostics(source: "grasshopper")
        XCTAssertEqual(c.diagnostics.first?.designVersion, 4)
    }

    func testSettingProvenanceReadsLikeTheIssue() throws {
        let json = #"{"key": "sheet_price", "label": "Sheet price", "type": "currency", "unit": "AUD/sheet", "value": 38.5, "layer": "project", "set_by": "SM", "set_at": "2026-11-27T03:30:00Z", "group": "Costs"}"#
        let row = try JSONDecoder().decode(SettingRow.self, from: Data(json.utf8))
        XCTAssertEqual(row.valueText, "38.50")
        XCTAssertEqual(row.provenance, "project · SM, 27 Nov")
    }
}
