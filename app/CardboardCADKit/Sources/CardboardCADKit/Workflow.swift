import Foundation
import Observation

// MARK: - Engine routes

/// One saved workflow, as listed by GET /api/workflows.
public struct WorkflowSummary: Codable, Equatable, Sendable, Identifiable {
    public var name: String
    public var description: String
    public var builtin: Bool

    public var id: String { name }

    public init(name: String, description: String, builtin: Bool) {
        self.name = name
        self.description = description
        self.builtin = builtin
    }
}

/// The saved workflows, which one the assistant follows, and whether it fills in saved values.
public struct WorkflowList: Codable, Equatable, Sendable {
    public var active: String
    public var applyDefaults: Bool
    public var folder: String
    public var workflows: [WorkflowSummary]

    public init(active: String, applyDefaults: Bool, folder: String, workflows: [WorkflowSummary]) {
        self.active = active
        self.applyDefaults = applyDefaults
        self.folder = folder
        self.workflows = workflows
    }

    enum CodingKeys: String, CodingKey {
        case active
        case applyDefaults = "apply_defaults"
        case folder
        case workflows
    }
}

/// Reads and changes the workflow settings. The engine keeps them in
/// ~/Documents/CardboardCAD/Workflows/, shared with Claude's workflow tools.
public protocol WorkflowSettingsClient: AnyObject {
    func workflows() async throws -> WorkflowList
    /// nil leaves a setting as it is.
    func updateWorkflowSettings(active: String?, applyDefaults: Bool?) async throws -> WorkflowList
}

extension EngineClient: WorkflowSettingsClient {
    public func workflows() async throws -> WorkflowList {
        try await getJSON("/api/workflows")
    }

    public func updateWorkflowSettings(active: String?, applyDefaults: Bool?) async throws -> WorkflowList {
        var body: JSONValue = [:]
        if let active { body["active"] = .string(active) }
        if let applyDefaults { body["apply_defaults"] = .bool(applyDefaults) }
        return try await postJSON("/api/workflows/settings", body: body, timeout: 5)
    }
}

// MARK: - The workflow picker and the Apply Defaults toggle

/// State behind the Workflow menu, the toolbar toggle and Settings.
/// Every change goes to the engine first; `onChange` then tells the
/// assistant to fetch its system prompt again.
@MainActor
@Observable
public final class WorkflowController {
    public private(set) var active: String = ""
    public private(set) var applyDefaults = true
    public private(set) var workflows: [WorkflowSummary] = []
    public private(set) var folder: String?
    /// The last failure, shown under the controls. Cleared by the next success.
    public private(set) var error: String?

    /// Runs after a successful change, e.g. `assistant.reloadSetup()`.
    @ObservationIgnored public var onChange: (() -> Void)?
    @ObservationIgnored private var client: WorkflowSettingsClient?

    public init() {}

    public var names: [String] { workflows.map(\.name) }
    public var isConnected: Bool { client != nil }

    public static func defaultWorkflowsFolder(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        home.appendingPathComponent("Documents/CardboardCAD/Workflows", isDirectory: true)
    }

    /// Point at the running engine (nil while it is not running).
    public func attach(client: WorkflowSettingsClient?) {
        self.client = client
    }

    public func refresh() async {
        guard let client else { return }
        do {
            apply(try await client.workflows())
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    public func setApplyDefaults(_ on: Bool) async {
        guard client == nil || on != applyDefaults else { return }
        await update(active: nil, applyDefaults: on)
    }

    public func choose(_ name: String) async {
        guard client == nil || name != active else { return }
        await update(active: name, applyDefaults: nil)
    }

    private func update(active: String?, applyDefaults: Bool?) async {
        guard let client else {
            error = "The engine is not running, so the workflow cannot be changed."
            return
        }
        do {
            apply(try await client.updateWorkflowSettings(active: active, applyDefaults: applyDefaults))
            error = nil
            onChange?()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func apply(_ list: WorkflowList) {
        active = list.active
        applyDefaults = list.applyDefaults
        workflows = list.workflows
        folder = list.folder
    }
}
