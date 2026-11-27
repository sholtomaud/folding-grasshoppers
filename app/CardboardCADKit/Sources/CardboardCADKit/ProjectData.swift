import Foundation
import Observation

// Settings, input forms, JSON views and diagnostics (roadmap section 3).
// The engine holds and checks all of it; the app draws it and sends edits back.

// MARK: - Input forms (request_inputs)

/// One typed field on a form Claude asked for, e.g. Sheet price in AUD/sheet.
public struct InputFormField: Codable, Equatable, Sendable, Identifiable {
    public var key: String
    public var label: String
    public var type: String
    public var unit: String
    public var minimum: Double?
    public var maximum: Double?
    public var help: String?
    /// The value it has now, if any, and the layer it comes from.
    public var current: JSONValue?
    public var currentLayer: String?

    public var id: String { key }

    public init(key: String, label: String, type: String = "number", unit: String = "", minimum: Double? = nil,
                maximum: Double? = nil, help: String? = nil, current: JSONValue? = nil, currentLayer: String? = nil) {
        self.key = key
        self.label = label
        self.type = type
        self.unit = unit
        self.minimum = minimum
        self.maximum = maximum
        self.help = help
        self.current = current
        self.currentLayer = currentLayer
    }

    enum CodingKeys: String, CodingKey {
        case key, label, type, unit, minimum, maximum, help, current
        case currentLayer = "current_layer"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? key
        type = try c.decodeIfPresent(String.self, forKey: .type) ?? "number"
        unit = try c.decodeIfPresent(String.self, forKey: .unit) ?? ""
        minimum = try c.decodeIfPresent(Double.self, forKey: .minimum)
        maximum = try c.decodeIfPresent(Double.self, forKey: .maximum)
        help = try c.decodeIfPresent(String.self, forKey: .help)
        current = try c.decodeIfPresent(JSONValue.self, forKey: .current)
        currentLayer = try c.decodeIfPresent(String.self, forKey: .currentLayer)
    }

    /// The current value as text for the field, or "".
    public var currentText: String {
        switch current {
        case .number(let n)?: return NumberGuard.format(n)
        case .string(let s)?: return s
        case .bool(let b)?: return b ? "yes" : "no"
        default: return ""
        }
    }
}

/// A form in the chat. The designer's answer goes to the engine, never to Claude.
public struct InputForm: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var question: String
    public var fields: [InputFormField]
    /// "project" and "all" (Fold Theory defaults for every project).
    public var scopes: [String]
    public var scope: String

    public init(id: String, question: String = "", fields: [InputFormField], scopes: [String] = ["project", "all"],
                scope: String = "project") {
        self.id = id
        self.question = question
        self.fields = fields
        self.scopes = scopes
        self.scope = scope
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        question = try c.decodeIfPresent(String.self, forKey: .question) ?? ""
        fields = try c.decode([InputFormField].self, forKey: .fields)
        scopes = try c.decodeIfPresent([String].self, forKey: .scopes) ?? ["project", "all"]
        scope = try c.decodeIfPresent(String.self, forKey: .scope) ?? "project"
    }

    public static func scopeTitle(_ scope: String) -> String {
        scope == "all" ? "All projects" : "This project"
    }
}

/// The engine's answer to a submitted form. `errors` maps a field key to what is wrong with it.
public struct FormSubmission: Codable, Equatable, Sendable {
    public var ok: Bool
    public var message: String
    public var errors: [String: String]
    public var keys: [String]

    public init(ok: Bool, message: String, errors: [String: String] = [:], keys: [String] = []) {
        self.ok = ok
        self.message = message
        self.errors = errors
        self.keys = keys
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decode(Bool.self, forKey: .ok)
        message = try c.decodeIfPresent(String.self, forKey: .message) ?? ""
        errors = try c.decodeIfPresent([String: String].self, forKey: .errors) ?? [:]
        keys = try c.decodeIfPresent([String].self, forKey: .keys) ?? []
    }
}

// MARK: - Numbers Claude did not get from the engine

/// Claude never calculates. Any amount or quantity in a reply that no tool
/// gave this turn (and the designer did not type) is marked unverified.
public enum NumberGuard {
    private static let unitWords: Set<String> = ["mm", "cm", "m", "min", "mins", "h", "hr", "hrs", "s", "sec",
                                                 "kg", "g", "x", "k", "pc", "pcs"]

    /// The numbers in `text`, as written ("1,234.50") and as values.
    static func numbers(in text: String) -> [(raw: String, value: Double, digits: Int, flagged: Bool)] {
        let chars = Array(text)
        var out: [(raw: String, value: Double, digits: Int, flagged: Bool)] = []
        var i = 0
        while i < chars.count {
            guard chars[i].isNumber, chars[i].isASCII else { i += 1; continue }
            // Part of a word (v2, X3, rib-4a) or of another number (1.5.2)?
            let before: Character? = i > 0 ? chars[i - 1] : nil
            var j = i
            while j < chars.count, chars[j].isASCII,
                  chars[j].isNumber || ((chars[j] == "," || chars[j] == ".") && j + 1 < chars.count
                                        && chars[j + 1].isNumber && chars[j + 1].isASCII) {
                j += 1
            }
            let raw = String(chars[i..<j])
            defer { i = j }
            if let b = before, b.isLetter || b == "_" || b == "-" && i > 1 && chars[i - 2].isLetter { continue }
            if let b = before, b == "." || b == "," { continue }
            // A word glued on after: 3D is a name, 450mm is a quantity.
            var k = j
            while k < chars.count, chars[k].isLetter { k += 1 }
            let suffix = String(chars[j..<k]).lowercased()
            if !suffix.isEmpty, !unitWords.contains(suffix) { continue }
            // "1. " or "2) " at the start of a line: a list marker.
            let lineStart = before == nil || before == "\n" || (before == " " && (i < 2 || chars[i - 2] == "\n"))
            if lineStart, j < chars.count, chars[j] == "." || chars[j] == ")", raw.allSatisfy(\.isNumber) { continue }
            let plain = raw.replacingOccurrences(of: ",", with: "")
            guard let value = Double(plain) else { continue }
            let digits = plain.split(separator: ".").dropFirst().first?.count ?? 0
            // Money or a quantity is worth checking; a bare small count ("2 options") is prose.
            let money = before == "$" || (i >= 4 && String(chars[(i - 4)..<i]).trimmingCharacters(in: .whitespaces)
                .range(of: "^[A-Z]{3}$", options: .regularExpression) != nil)
            let checked = money || !suffix.isEmpty || digits > 0 || value >= 10 || followedByUnit(chars, j)
            out.append((raw: raw, value: value, digits: digits, flagged: checked))
        }
        return out
    }

    private static func followedByUnit(_ chars: [Character], _ j: Int) -> Bool {
        var k = j
        while k < chars.count, chars[k] == " " { k += 1 }
        var e = k
        while e < chars.count, chars[e].isLetter { e += 1 }
        let word = String(chars[k..<e]).lowercased()
        return unitWords.contains(word) || ["sheet", "sheets", "minutes", "hours", "parts", "ribs", "aud", "dollars",
                                            "pierces", "layers"].contains(word)
    }

    /// The numbers in `reply` that match nothing in `known` (tool results and the designer's own words).
    public static func unverified(reply: String, known: [String]) -> [String] {
        let knownValues = known.flatMap { numbers(in: $0).map(\.value) }
        var out: [String] = []
        for n in numbers(in: reply) where n.flagged {
            let scale = pow(10.0, Double(n.digits))
            let matches = knownValues.contains { k in
                abs(k - n.value) < 1e-9 || abs((k * scale).rounded() / scale - n.value) < 1e-9
            }
            if !matches, !out.contains(n.raw) { out.append(n.raw) }
        }
        return out
    }

    /// 38.5 -> "38.5", 60 -> "60".
    public static func format(_ value: Double) -> String {
        if value.rounded() == value, abs(value) < 1e15 { return String(Int64(value)) }
        return String(value)
    }
}

// MARK: - Settings, views and diagnostics from the engine

/// One setting with its value and where the value comes from.
public struct SettingRow: Codable, Equatable, Sendable, Identifiable {
    public var key: String
    public var label: String
    public var type: String
    public var unit: String
    public var value: JSONValue?
    /// default, workflow, project or concept; nil when it is not set anywhere.
    public var layer: String?
    public var setBy: String
    public var setAt: String
    public var group: String

    public var id: String { key }

    public init(key: String, label: String, type: String = "number", unit: String = "", value: JSONValue? = nil,
                layer: String? = nil, setBy: String = "", setAt: String = "", group: String = "Costs") {
        self.key = key
        self.label = label
        self.type = type
        self.unit = unit
        self.value = value
        self.layer = layer
        self.setBy = setBy
        self.setAt = setAt
        self.group = group
    }

    enum CodingKeys: String, CodingKey {
        case key, label, type, unit, value, layer, group
        case setBy = "set_by"
        case setAt = "set_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? key
        type = try c.decodeIfPresent(String.self, forKey: .type) ?? "number"
        unit = try c.decodeIfPresent(String.self, forKey: .unit) ?? ""
        value = try c.decodeIfPresent(JSONValue.self, forKey: .value)
        layer = try c.decodeIfPresent(String.self, forKey: .layer)
        setBy = try c.decodeIfPresent(String.self, forKey: .setBy) ?? ""
        setAt = try c.decodeIfPresent(String.self, forKey: .setAt) ?? ""
        group = try c.decodeIfPresent(String.self, forKey: .group) ?? ""
    }

    /// "38.50 AUD/sheet · project · SM, 27 Nov"
    public var provenance: String {
        guard let layer else { return "not set" }
        var parts = [layer]
        let date = Self.shortDate(setAt)
        let who = [setBy, date].filter { !$0.isEmpty }.joined(separator: ", ")
        if !who.isEmpty { parts.append(who) }
        return parts.joined(separator: " · ")
    }

    public var valueText: String {
        switch value {
        case .number(let n)?: return type == "currency" ? String(format: "%.2f", n) : NumberGuard.format(n)
        case .string(let s)?: return s
        case .bool(let b)?: return b ? "yes" : "no"
        default: return ""
        }
    }

    static func shortDate(_ iso: String) -> String {
        let f = ISO8601DateFormatter()
        guard let date = f.date(from: iso) else { return "" }
        let out = DateFormatter()
        out.locale = Locale(identifier: "en_AU")
        out.dateFormat = "d MMM"
        return out.string(from: date)
    }
}

public struct SettingsList: Codable, Equatable, Sendable {
    public var layers: [String]
    public var settings: [SettingRow]

    public init(layers: [String] = ["default", "workflow", "project", "concept"], settings: [SettingRow]) {
        self.layers = layers
        self.settings = settings
    }
}

/// One block of a JSON view (schema cardboardcad.view/1). The engine has already checked it.
public struct ViewBlock: Codable, Equatable, Sendable {
    public var type: String
    public var blockID: String?
    public var title: String?
    public var label: String?
    public var text: String?
    public var style: String?
    public var bind: String?
    public var caption: String?
    public var group: String?
    public var action: String?
    public var columns: [String]?
    public var editable: Bool?
    public var collapsed: Bool?
    public var height: Double?
    public var blocks: [ViewBlock]?
    public var tabs: [ViewTab]?

    public init(type: String, title: String? = nil, label: String? = nil, text: String? = nil, bind: String? = nil,
                action: String? = nil, columns: [String]? = nil, editable: Bool? = nil, blocks: [ViewBlock]? = nil) {
        self.type = type
        self.title = title
        self.label = label
        self.text = text
        self.bind = bind
        self.action = action
        self.columns = columns
        self.editable = editable
        self.blocks = blocks
    }

    enum CodingKeys: String, CodingKey {
        case type, title, label, text, style, bind, caption, group, action, columns, editable, collapsed, height,
             blocks, tabs
        case blockID = "id"
    }
}

public struct ViewTab: Codable, Equatable, Sendable {
    public var title: String
    public var blocks: [ViewBlock]
}

public struct ViewDocument: Codable, Equatable, Sendable, Identifiable {
    public var schema: String?
    public var id: String
    public var title: String
    public var description: String?
    public var blocks: [ViewBlock]
}

/// A view and the current value of every binding it uses.
public struct RenderedView: Codable, Equatable, Sendable {
    public var view: ViewDocument
    public var data: [String: JSONValue]

    public init(view: ViewDocument, data: [String: JSONValue]) {
        self.view = view
        self.data = data
    }
}

public struct ViewSummary: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var title: String
    /// builtin, default (saved for all projects) or project.
    public var source: String

    public init(id: String, title: String, source: String = "builtin") {
        self.id = id
        self.title = title
        self.source = source
    }
}

/// One line of the diagnostics log (Grasshopper, Rhino, the engine, validation, the app).
public struct DiagnosticEntry: Codable, Equatable, Sendable, Identifiable {
    public var id: Int
    public var time: String
    public var source: String
    public var level: String
    public var message: String
    public var designVersion: Int?
    public var details: JSONValue?

    public init(id: Int, time: String, source: String, level: String, message: String, designVersion: Int? = nil,
                details: JSONValue? = nil) {
        self.id = id
        self.time = time
        self.source = source
        self.level = level
        self.message = message
        self.designVersion = designVersion
        self.details = details
    }

    enum CodingKeys: String, CodingKey {
        case id, time, source, level, message, details
        case designVersion = "design_version"
    }
}

/// What the Project tab, the JSON views and the Diagnostics panel need from the engine.
public protocol ProjectDataClient: AnyObject {
    func settingsList() async throws -> SettingsList
    func setSetting(key: String, value: JSONValue, scope: String, setBy: String) async throws -> SettingsList
    func viewList() async throws -> [ViewSummary]
    func renderView(id: String) async throws -> RenderedView
    func undoView(id: String, scope: String) async throws
    func diagnostics(source: String?, level: String?, limit: Int) async throws -> [DiagnosticEntry]
    /// Run a registered view action that is a design tool (estimate_cost, nest_sheets, export_cut_files).
    func runViewAction(_ action: String) async throws -> DesignToolOutput
}

extension EngineClient: ProjectDataClient {
    public func settingsList() async throws -> SettingsList {
        try await getJSON("/api/settings")
    }

    public func setSetting(key: String, value: JSONValue, scope: String, setBy: String) async throws -> SettingsList {
        try await postJSON("/api/settings", body: ["key": .string(key), "value": value, "scope": .string(scope),
                                                   "set_by": .string(setBy)], timeout: 30)
    }

    public func viewList() async throws -> [ViewSummary] {
        struct Reply: Decodable { let views: [ViewSummary] }
        let reply: Reply = try await getJSON("/api/views")
        return reply.views
    }

    public func renderView(id: String) async throws -> RenderedView {
        let safe = id.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? id
        return try await getJSON("/api/views/\(safe)")
    }

    public func undoView(id: String, scope: String) async throws {
        let safe = id.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? id
        let _: JSONValue = try await postJSON("/api/views/\(safe)/undo", body: ["scope": .string(scope)], timeout: 5)
    }

    public func diagnostics(source: String?, level: String?, limit: Int) async throws -> [DiagnosticEntry] {
        struct Reply: Decodable { let entries: [DiagnosticEntry] }
        var query = ["limit=\(limit)"]
        if let source { query.append("source=\(source)") }
        if let level { query.append("level=\(level)") }
        let reply: Reply = try await getJSON("/api/diagnostics?" + query.joined(separator: "&"))
        return reply.entries
    }

    public func runViewAction(_ action: String) async throws -> DesignToolOutput {
        try await runDesignTool(name: action, input: [:])
    }
}

// MARK: - The Project tab and other JSON views

/// State behind the tabs drawn from JSON views (Project first) and the Diagnostics panel.
@MainActor
@Observable
public final class ProjectViewsController {
    public private(set) var views: [ViewSummary] = []
    public var selected: String = "project"
    public private(set) var rendered: RenderedView?
    public private(set) var diagnostics: [DiagnosticEntry] = []
    /// The last failure (a refused setting, an engine error), shown above the view.
    public var error: String?
    /// Actions the app handles itself (save_project, open_diagnostics).
    @ObservationIgnored public var onAppAction: ((String) -> Void)?
    @ObservationIgnored private var client: ProjectDataClient?

    public static let designTools: Set<String> = ["estimate_cost", "nest_sheets", "export_cut_files", "check_brief"]

    public init() {}

    public var isConnected: Bool { client != nil }

    var attachedClient: ProjectDataClient? { client }

    public func attach(client: ProjectDataClient?) {
        self.client = client
        if client == nil {
            rendered = nil
            views = []
        }
    }

    /// Fetch the list of views and draw the selected one again.
    public func refresh() async {
        guard let client else { return }
        do {
            views = try await client.viewList()
            if !views.contains(where: { $0.id == selected }), let first = views.first { selected = first.id }
            if views.contains(where: { $0.id == selected }) {
                rendered = try await client.renderView(id: selected)
            } else {
                rendered = nil
            }
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    public func select(_ id: String) async {
        selected = id
        await refresh()
    }

    /// The designer typed a value in a setting field. The engine checks it and works the quote out again.
    @discardableResult
    public func setSetting(key: String, text: String, scope: String = "project", setBy: String) async -> Bool {
        guard let client else {
            error = "The engine is not running."
            return false
        }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let value: JSONValue = Double(trimmed.replacingOccurrences(of: ",", with: "")).map { .number($0) }
            ?? .string(trimmed)
        do {
            _ = try await client.setSetting(key: key, value: value, scope: scope, setBy: setBy)
            error = nil
            await refresh()
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }

    public func run(action: String) async {
        guard Self.designTools.contains(action) else {
            onAppAction?(action)
            return
        }
        guard let client else { return }
        do {
            let out = try await client.runViewAction(action)
            error = out.isError ? out.content : nil
            await refresh()
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Put back the view as it was before its last save.
    public func undo(scope: String) async {
        guard let client else { return }
        do {
            try await client.undoView(id: selected, scope: scope)
            await refresh()
        } catch {
            self.error = error.localizedDescription
        }
    }

    public func refreshDiagnostics(source: String? = nil, level: String? = nil) async {
        guard let client else { return }
        do {
            diagnostics = try await client.diagnostics(source: source, level: level, limit: 300)
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// The bound value of `binding` in the view on screen.
    public func value(_ binding: String?) -> JSONValue? {
        guard let binding else { return nil }
        return rendered?.data[binding]
    }
}
