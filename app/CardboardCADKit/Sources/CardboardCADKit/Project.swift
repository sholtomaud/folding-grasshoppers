import Foundation
import Observation

// MARK: - Engine routes

/// Reply of POST /api/design/save.
public struct ProjectSaved: Codable, Equatable, Sendable {
    public var name: String
    public var folder: String
    public var files: [String]
    public var version: Int

    public init(name: String, folder: String, files: [String], version: Int) {
        self.name = name
        self.folder = folder
        self.files = files
        self.version = version
    }
}

/// Reply of POST /api/design/open. `chat` is what the app saved with the project, if anything.
public struct ProjectOpened: Codable, Equatable, Sendable {
    public var name: String
    public var folder: String
    public var chat: JSONValue?
    public var version: Int

    public init(name: String, folder: String, chat: JSONValue?, version: Int) {
        self.name = name
        self.folder = folder
        self.chat = chat
        self.version = version
    }
}

/// Saves and opens project folders. The engine writes the files; the app sends the chat along.
public protocol DesignProjectStore: AnyObject {
    /// An empty `folder` means ~/Documents/CardboardCAD/Projects/<name>.
    func saveProject(name: String, folder: String, chat: JSONValue?) async throws -> ProjectSaved
    func openProject(folder: String) async throws -> ProjectOpened
}

extension EngineClient: DesignProjectStore {
    /// The Grasshopper listener script for live Rhino sync.
    public var liveListenerURL: URL { baseURL.appendingPathComponent("rhino/live_listener.py") }

    /// The Fold Theory script for Rhino 8's Script Editor: two-way sync without Grasshopper.
    public var rhinoScriptURL: URL { baseURL.appendingPathComponent("rhino/fold_theory.py") }

    public func saveProject(name: String, folder: String, chat: JSONValue?) async throws -> ProjectSaved {
        var body: JSONValue = ["name": .string(name), "folder": .string(folder)]
        if let chat { body["chat"] = chat }
        return try await postJSON("/api/design/save", body: body)
    }

    public func openProject(folder: String) async throws -> ProjectOpened {
        try await postJSON("/api/design/open", body: ["folder": .string(folder)])
    }
}

// MARK: - Conversation save / restore

extension DesignAssistant {
    public static let conversationSchema = "cardboardcad.chat/1"

    /// The chat as JSON, to save with a project: what the designer sees and what Claude was sent.
    public var conversation: JSONValue {
        let shown: [JSONValue] = items.map { (item: ChatItem) -> JSONValue in
            switch item.kind {
            case .user: return ["kind": "user", "text": .string(item.text)]
            case .assistant: return ["kind": "assistant", "text": .string(item.text)]
            case .error: return ["kind": "error", "text": .string(item.text)]
            case .notice: return ["kind": "notice", "text": .string(item.text)]
            case .tool(let name, let isError):
                return ["kind": "tool", "tool": .string(name), "is_error": .bool(isError), "text": .string(item.text)]
            }
        }
        return ["schema": .string(Self.conversationSchema), "items": .array(shown), "history": .array(history)]
    }

    /// Replace the chat with a saved one. Returns false (and leaves an empty chat) when it is not one.
    @discardableResult
    public func restore(conversation: JSONValue) -> Bool {
        guard conversation["schema"]?.stringValue == Self.conversationSchema,
              let rawItems = conversation["items"]?.arrayValue,
              let rawHistory = conversation["history"]?.arrayValue
        else {
            replaceConversation(items: [], history: [])
            return false
        }
        let restored: [ChatItem] = rawItems.compactMap { (raw: JSONValue) -> ChatItem? in
            guard let text = raw["text"]?.stringValue else { return nil }
            switch raw["kind"]?.stringValue {
            case "user": return ChatItem(kind: .user, text: text)
            case "assistant": return ChatItem(kind: .assistant, text: text)
            case "error": return ChatItem(kind: .error, text: text)
            case "notice": return ChatItem(kind: .notice, text: text)
            case "tool":
                return ChatItem(kind: .tool(name: raw["tool"]?.stringValue ?? "tool",
                                            isError: raw["is_error"]?.boolValue ?? false), text: text)
            default: return nil
            }
        }
        replaceConversation(items: restored, history: Self.completeTurns(rawHistory))
        return true
    }

    /// History cut back to its last finished assistant turn (no tool call left waiting for a result).
    static func completeTurns(_ history: [JSONValue]) -> [JSONValue] {
        var h = history
        while let last = h.last {
            let isAssistant = last["role"]?.stringValue == "assistant"
            let waitsForTool = last["content"]?.arrayValue?.contains { $0["type"]?.stringValue == "tool_use" } ?? false
            if isAssistant && !waitsForTool { break }
            h.removeLast()
        }
        return h
    }
}

// MARK: - Current project

/// The project the design belongs to, if it was saved or opened.
public struct ProjectRef: Equatable, Sendable {
    public var name: String
    public var folder: String

    public init(name: String, folder: String) {
        self.name = name
        self.folder = folder
    }

    public var url: URL { URL(fileURLWithPath: folder, isDirectory: true) }
}

/// Save, Save As and Open for the File menu. The engine writes the project files;
/// this sends the chat along and restores it on open.
@MainActor
@Observable
public final class ProjectController {
    public private(set) var current: ProjectRef?
    public private(set) var isWorking = false
    /// The last failure, for an alert. Cleared by the next success.
    public var error: String?
    /// The last success, for the status line ("Saved Stool").
    public private(set) var lastMessage: String?

    /// Runs after a successful save with the project folder, e.g. to add a preview snapshot.
    @ObservationIgnored public var afterSave: ((URL) async -> Void)?

    @ObservationIgnored private let assistant: DesignAssistant
    @ObservationIgnored private var store: DesignProjectStore?

    public init(assistant: DesignAssistant) {
        self.assistant = assistant
    }

    /// Point at the running engine (nil while it is not running).
    public func attach(store: DesignProjectStore?) {
        self.store = store
    }

    public var canSaveAgain: Bool { current != nil && store != nil && !isWorking }

    public static func defaultProjectsFolder(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        home.appendingPathComponent("Documents/CardboardCAD/Projects", isDirectory: true)
    }

    /// Save As: `folder` empty means ~/Documents/CardboardCAD/Projects/<name>.
    @discardableResult
    public func save(name: String, folder: String = "") async -> Bool {
        guard let store else {
            error = "The engine is not running, so the project cannot be saved."
            return false
        }
        isWorking = true
        defer { isWorking = false }
        do {
            let saved = try await store.saveProject(name: name, folder: folder, chat: assistant.conversation)
            current = ProjectRef(name: saved.name, folder: saved.folder)
            error = nil
            lastMessage = "Saved \(saved.name)"
            if let afterSave { await afterSave(URL(fileURLWithPath: saved.folder, isDirectory: true)) }
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }

    /// Save into the current project's folder.
    @discardableResult
    public func saveAgain() async -> Bool {
        guard let current else {
            error = "This design has not been saved yet. Use Save As."
            return false
        }
        return await save(name: current.name, folder: current.folder)
    }

    @discardableResult
    public func open(folder: String) async -> Bool {
        guard let store else {
            error = "The engine is not running, so the project cannot be opened."
            return false
        }
        guard !assistant.isBusy else {
            error = "Wait for the assistant to finish before opening a project."
            return false
        }
        isWorking = true
        defer { isWorking = false }
        do {
            let opened = try await store.openProject(folder: folder)
            current = ProjectRef(name: opened.name, folder: opened.folder)
            if let chat = opened.chat, assistant.restore(conversation: chat) {
                // restored
            } else {
                assistant.restore(conversation: .null)
                assistant.addNotice("Opened \(opened.name). Its chat was not saved, so this is a new conversation.")
            }
            error = nil
            lastMessage = "Opened \(opened.name)"
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }

    /// The design was cleared (New Design): it no longer belongs to a project.
    public func forget() {
        current = nil
        lastMessage = nil
    }
}
