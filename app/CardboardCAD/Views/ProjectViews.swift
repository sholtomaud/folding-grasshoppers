import AppKit
import CardboardCADKit
import SwiftUI

// The right-hand side of the window: the live 3D / sheets preview, the tabs
// drawn from JSON views (Project first), and the Diagnostics panel. The JSON
// views are checked by the engine before they get here; each block type below
// is drawn by native SwiftUI code, so a new tab is a file, not a new build.

struct PreviewArea: View {
    let engine: EngineManager
    let assistant: DesignAssistant
    let views: ProjectViewsController
    @State private var tab = PreviewArea.designTab

    static let designTab = "_design"
    static let diagnosticsTab = "_diagnostics"

    var body: some View {
        VStack(spacing: 0) {
            Picker("Show", selection: $tab) {
                Text("3D & Sheets").tag(Self.designTab)
                ForEach(views.views) { v in
                    Text(v.title).tag(v.id)
                }
                Text("Diagnostics").tag(Self.diagnosticsTab)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            Divider()
            switch tab {
            case Self.designTab:
                PreviewPane(engine: engine)
            case Self.diagnosticsTab:
                DiagnosticsPanel(views: views)
            default:
                JSONViewScreen(views: views)
                    .task(id: tab) { await views.select(tab) }
            }
        }
        // Work the shown view out again after each step of the assistant.
        .task(id: assistant.items.count) {
            if tab != Self.designTab { await views.refresh() }
        }
        .task(id: views.isConnected) { await views.refresh() }
        .onAppear {
            views.onAppAction = { action in
                if action == "open_diagnostics" { tab = Self.diagnosticsTab }
                if action == "save_project" { NotificationCenter.default.post(name: .foldTheorySaveProject, object: nil) }
            }
        }
    }
}

extension Notification.Name {
    /// A view's Save button: the File menu's Save Project handles it.
    static let foldTheorySaveProject = Notification.Name("FoldTheorySaveProject")
}

// MARK: - A JSON view

struct JSONViewScreen: View {
    let views: ProjectViewsController

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if let error = views.error {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                        .textSelection(.enabled)
                }
                if let rendered = views.rendered {
                    HStack {
                        Text(rendered.view.title).font(.title2.bold())
                        Spacer()
                        if views.views.first(where: { $0.id == rendered.view.id })?.source != "builtin" {
                            Button("Undo Last Change") {
                                let source = views.views.first { $0.id == rendered.view.id }?.source
                                Task { await views.undo(scope: source == "project" ? "project" : "all") }
                            }
                            .help("Put this view back as it was before its last change")
                        }
                    }
                    ForEach(Array(rendered.view.blocks.enumerated()), id: \.offset) { _, block in
                        BlockView(block: block, views: views)
                    }
                } else if !views.isConnected {
                    ContentUnavailableView("Project", systemImage: "list.bullet.rectangle",
                                           description: Text("This appears when the engine is running."))
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct BlockView: View {
    let block: ViewBlock
    let views: ProjectViewsController

    var body: some View {
        switch block.type {
        case "section":
            GroupBox {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(Array((block.blocks ?? []).enumerated()), id: \.offset) { _, b in
                        BlockView(block: b, views: views)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(4)
            } label: {
                if let title = block.title { Text(title).font(.headline) }
            }
        case "tabs":
            TabsBlock(tabs: block.tabs ?? [], views: views)
        case "text":
            Text(block.text ?? "")
                .font(block.style == "heading" ? .headline : block.style == "caption" ? .caption : .body)
                .foregroundStyle(block.style == "caption" ? .secondary : .primary)
                .fixedSize(horizontal: false, vertical: true)
        case "field":
            FieldBlock(block: block, views: views)
        case "total":
            HStack {
                Text(block.label ?? "Total").font(.headline)
                Spacer()
                Text(JSONText.money(views.value(block.bind), currency: views.value("quote.currency")))
                    .font(.title3.monospacedDigit().bold())
                    .textSelection(.enabled)
            }
        case "table":
            TableBlock(rows: views.value(block.bind)?.arrayValue ?? [], columns: block.columns ?? [], title: block.title)
        case "settings":
            SettingsBlock(rows: views.value(block.bind)?.arrayValue ?? [], group: block.group,
                          editable: block.editable ?? false, views: views)
        case "checklist":
            VStack(alignment: .leading, spacing: 4) {
                if let title = block.title { Text(title).font(.subheadline.bold()) }
                ForEach(Array((views.value(block.bind)?.arrayValue ?? []).enumerated()), id: \.offset) { _, row in
                    let done = row["done"]?.boolValue ?? false
                    Label(row["title"]?.stringValue ?? "", systemImage: done ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(done ? .green : (row["optional"]?.boolValue == true ? .secondary : .primary))
                }
            }
        case "image":
            if let svg = views.value(block.bind)?.stringValue, let image = NSImage(data: Data(svg.utf8)) {
                Image(nsImage: image).resizable().scaledToFit().frame(maxHeight: 240)
            }
        case "model3d", "cut_sheets":
            Text("The 3D model and cut sheets are on the 3D & Sheets tab.")
                .font(.caption).foregroundStyle(.secondary)
        case "button":
            Button(block.label ?? "Run") {
                if let action = block.action { Task { await views.run(action: action) } }
            }
        default:
            EmptyView()  // the engine rejects unknown blocks before they get here
        }
    }
}

struct TabsBlock: View {
    let tabs: [ViewTab]
    let views: ProjectViewsController
    @State private var index = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picker("", selection: $index) {
                ForEach(Array(tabs.enumerated()), id: \.offset) { i, t in Text(t.title).tag(i) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            if tabs.indices.contains(index) {
                ForEach(Array(tabs[index].blocks.enumerated()), id: \.offset) { _, b in
                    BlockView(block: b, views: views)
                }
            }
        }
    }
}

/// One value. A field bound to settings.<key> or brief.<key> with editable true can be changed in place.
struct FieldBlock: View {
    let block: ViewBlock
    let views: ProjectViewsController
    @State private var draft = ""
    @State private var editing = false

    var body: some View {
        let value = views.value(block.bind)
        let isSetting = ViewBinding.isEditable(block.bind)
        HStack(alignment: .firstTextBaseline) {
            Text(block.label ?? value?["label"]?.stringValue ?? block.bind ?? "")
                .foregroundStyle(.secondary)
                .frame(width: 130, alignment: .leading)
            if isSetting, block.editable == true {
                TextField("", text: $draft, onEditingChanged: { editing = $0 })
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { commit(value) }
                    .onAppear { draft = JSONText.plain(value?["value"]) }
                    .onChange(of: value) { if !editing { draft = JSONText.plain(value?["value"]) } }
            } else {
                Text(isSetting ? JSONText.plain(value?["value"]) : JSONText.plain(value))
                    .textSelection(.enabled)
            }
            Spacer()
        }
    }

    private func commit(_ value: JSONValue?) {
        guard let key = value?["key"]?.stringValue else { return }
        if ViewBinding.isBrief(block.bind) {
            Task { await views.setBrief(key: key, text: draft) }
        } else {
            Task { await views.setSetting(key: key, text: draft, setBy: NSFullUserName()) }
        }
    }
}

struct TableBlock: View {
    let rows: [JSONValue]
    let columns: [String]
    let title: String?

    var body: some View {
        let cols = columns.isEmpty ? (rows.first?.objectValue?.keys.sorted() ?? []) : columns
        VStack(alignment: .leading, spacing: 4) {
            if let title { Text(title).font(.subheadline.bold()) }
            if rows.isEmpty {
                Text("Nothing yet.").foregroundStyle(.secondary)
            }
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 4) {
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    GridRow {
                        ForEach(cols, id: \.self) { c in
                            let v = row[c]
                            Text(c == "value" ? JSONText.amount(v) : JSONText.plain(v))
                                .font(c == "value" ? .body.monospacedDigit() : .body)
                                .gridColumnAlignment(c == "value" ? .trailing : .leading)
                                .textSelection(.enabled)
                        }
                    }
                }
            }
        }
    }
}

/// Every setting with its value and where it came from: "38.50 AUD/sheet · project · SM, 27 Nov".
struct SettingsBlock: View {
    let rows: [JSONValue]
    let group: String?
    let editable: Bool
    let views: ProjectViewsController

    var body: some View {
        let settings = rows.compactMap { row -> SettingRow? in
            guard let data = try? JSONValue.encoder.encode(row) else { return nil }
            return try? JSONDecoder().decode(SettingRow.self, from: data)
        }.filter { group == nil || $0.group == group }
        let groups = Array(Set(settings.map(\.group))).sorted()
        VStack(alignment: .leading, spacing: 10) {
            ForEach(groups, id: \.self) { g in
                Text(g).font(.subheadline.bold())
                ForEach(settings.filter { $0.group == g }) { row in
                    SettingLine(row: row, editable: editable, views: views)
                }
            }
        }
    }
}

struct SettingLine: View {
    let row: SettingRow
    let editable: Bool
    let views: ProjectViewsController
    @State private var draft = ""
    @State private var scope = "project"

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(row.label).frame(width: 130, alignment: .leading)
            if editable {
                TextField(row.layer == nil ? "not set" : "", text: $draft)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 110)
                    .onSubmit {
                        Task { await views.setSetting(key: row.key, text: draft, scope: scope, setBy: NSFullUserName()) }
                    }
                    .onAppear { draft = row.valueText }
                    .onChange(of: row) { draft = row.valueText }
            } else {
                Text(row.valueText.isEmpty ? "not set" : row.valueText).monospacedDigit()
            }
            Text(row.unit).foregroundStyle(.secondary)
            Spacer()
            Text(row.provenance)
                .font(.caption)
                .foregroundStyle(row.layer == nil ? .orange : .secondary)
            if editable {
                Picker("", selection: $scope) {
                    Text("This project").tag("project")
                    Text("All projects").tag("all")
                }
                .labelsHidden()
                .frame(width: 120)
                .help("Where a new value is saved: this project, or Fold Theory's defaults for every project")
            }
        }
    }
}

enum JSONText {
    static func plain(_ v: JSONValue?) -> String {
        switch v {
        case .string(let s)?: return s
        case .number(let n)?: return NumberGuard.format(n)
        case .bool(let b)?: return b ? "yes" : "no"
        case .array(let a)?: return a.map { plain($0) }.joined(separator: ", ")
        case .null?, nil: return "—"
        case .object?: return "…"
        }
    }

    static func amount(_ v: JSONValue?) -> String {
        if case .number(let n)? = v { return String(format: "%.2f", n) }
        return plain(v)
    }

    static func money(_ v: JSONValue?, currency: JSONValue?) -> String {
        guard case .number = v else { return "—" }
        return amount(v) + " " + (currency?.stringValue ?? "")
    }
}

// MARK: - Diagnostics

struct DiagnosticsPanel: View {
    let views: ProjectViewsController
    @State private var source = "all"
    @State private var level = "info"

    static let sources = ["all", "grasshopper", "hops", "rhino", "engine", "validation", "app"]

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Picker("Source", selection: $source) {
                    ForEach(Self.sources, id: \.self) { Text($0.capitalized).tag($0) }
                }
                .frame(width: 200)
                Picker("Level", selection: $level) {
                    ForEach(["debug", "info", "warning", "error"], id: \.self) { Text($0.capitalized).tag($0) }
                }
                .frame(width: 160)
                Spacer()
                Button {
                    Task { await reload() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
            .padding(10)
            Divider()
            if views.diagnostics.isEmpty {
                ContentUnavailableView("No diagnostics", systemImage: "stethoscope",
                                       description: Text("Grasshopper, Rhino, the engine and the checks on forms and views report here. Ask the assistant \"why is Rhino not updating?\" and it reads this log."))
            } else {
                List(Array(views.diagnostics.reversed())) { e in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Image(systemName: icon(e.level)).foregroundStyle(color(e.level))
                            Text(e.source).font(.caption.bold())
                            if let v = e.designVersion { Text("v\(v)").font(.caption).foregroundStyle(.secondary) }
                            Spacer()
                            Text(e.time).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        }
                        Text(e.message).textSelection(.enabled)
                        if let details = e.details, details != [:] {
                            Text(String(decoding: (try? JSONValue.encoder.encode(details)) ?? Data(), as: UTF8.self))
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    }
                }
            }
        }
        .task(id: "\(source)|\(level)") { await reload() }
    }

    private func reload() async {
        await views.refreshDiagnostics(source: source == "all" ? nil : source, level: level)
    }

    private func icon(_ level: String) -> String {
        switch level {
        case "error": return "xmark.octagon"
        case "warning": return "exclamationmark.triangle"
        default: return "info.circle"
        }
    }

    private func color(_ level: String) -> Color {
        switch level {
        case "error": return .red
        case "warning": return .orange
        default: return .secondary
        }
    }
}

// MARK: - A form in the chat (request_inputs)

/// Typed fields with units and a choice of this project or all projects. The values go to
/// the engine's settings; Claude is only told which settings were set.
struct InputFormCard: View {
    let itemID: UUID
    let form: InputForm
    let assistant: DesignAssistant
    @State private var values: [String: String] = [:]
    @State private var scope = "project"
    @State private var errors: [String: String] = [:]
    @State private var sending = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(form.question.isEmpty ? "The assistant needs these values" : form.question,
                  systemImage: "list.bullet.clipboard")
                .font(.headline)
            ForEach(form.fields) { field in
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(field.label).frame(width: 120, alignment: .leading)
                        TextField(placeholder(field), text: binding(field.key))
                            .textFieldStyle(.roundedBorder)
                            .onSubmit(submit)
                        if !field.unit.isEmpty {
                            Text(field.unit).foregroundStyle(.secondary)
                        }
                    }
                    if let help = field.help, !help.isEmpty {
                        Text(help).font(.caption).foregroundStyle(.secondary).padding(.leading, 124)
                    }
                    if let error = errors[field.key] {
                        Text(error).font(.caption).foregroundStyle(.red).padding(.leading, 124)
                    }
                }
            }
            if let error = errors["_form"] {
                Text(error).font(.caption).foregroundStyle(.red)
            }
            HStack {
                Picker("Save for", selection: $scope) {
                    ForEach(form.scopes, id: \.self) { Text(InputForm.scopeTitle($0)).tag($0) }
                }
                .frame(maxWidth: 260)
                Spacer()
                Button("Save", action: submit)
                    .buttonStyle(.borderedProminent)
                    .disabled(sending || assistant.isBusy)
            }
        }
        .padding(12)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
        .onAppear {
            scope = form.scope
            for f in form.fields where values[f.key] == nil { values[f.key] = f.currentText }
        }
    }

    private func placeholder(_ field: InputFormField) -> String {
        switch field.type {
        case "currency": return "0.00"
        case "percent": return "%"
        case "text": return ""
        default: return "number"
        }
    }

    private func binding(_ key: String) -> Binding<String> {
        Binding(get: { values[key] ?? "" }, set: { values[key] = $0 })
    }

    private func submit() {
        guard !sending else { return }
        sending = true
        let values = values, scope = scope
        Task {
            let answer = await assistant.submitForm(itemID: itemID, form: form, values: values, scope: scope,
                                                    setBy: NSFullUserName())
            errors = answer?.errors ?? [:]
            sending = false
        }
    }
}
