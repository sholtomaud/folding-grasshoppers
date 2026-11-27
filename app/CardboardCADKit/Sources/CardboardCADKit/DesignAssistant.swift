import Foundation
import Observation

/// The chat side of the Design Assistant: sends the conversation to Claude,
/// runs the tools Claude asks for on the engine, and loops until Claude is done.
///
/// The engine supplies the system prompt and tool definitions and holds the
/// design itself, so this class only relays. The conversation sent to Claude
/// (`history`) keeps every content block exactly as Claude returned it.
@MainActor
@Observable
public final class DesignAssistant {
    public struct ChatItem: Identifiable, Equatable, Sendable {
        public enum Kind: Equatable, Sendable {
            case user
            case assistant
            case tool(name: String, isError: Bool)
            case error
            case notice
        }

        public let id: UUID
        public let kind: Kind
        public let text: String

        public init(kind: Kind, text: String, id: UUID = UUID()) {
            self.id = id
            self.kind = kind
            self.text = text
        }
    }

    // MARK: observable state
    public private(set) var items: [ChatItem] = []
    public private(set) var isBusy = false
    /// What the assistant is doing right now, e.g. "Running Waffle slice…".
    public private(set) var activity: String?

    // MARK: settings
    public var modelID: String
    public var effort: String
    @ObservationIgnored public var maxSteps = 25

    // MARK: conversation sent to Claude
    @ObservationIgnored public private(set) var history: [JSONValue] = []

    @ObservationIgnored private let model: ChatModelClient
    @ObservationIgnored private var tools: DesignToolRunner?
    @ObservationIgnored private var setup: DesignSetup?
    /// Called after a tool changed the engine's workflow (so a workflow picker can refresh).
    @ObservationIgnored public var onSetupChanged: (() -> Void)?

    public init(model: ChatModelClient, modelID: String = AssistantModel.defaultID,
                effort: String = AssistantModel.defaultEffort) {
        self.model = model
        self.modelID = modelID
        self.effort = effort
    }

    /// Point the assistant at the running engine (nil while it is not running).
    public func attach(tools: DesignToolRunner?) {
        self.tools = tools
        setup = nil
    }

    public var isConnected: Bool { tools != nil }

    /// Fetch the system prompt and tools again before the next request (the workflow changed).
    public func reloadSetup() {
        setup = nil
    }

    public static func title(forTool name: String) -> String {
        let words = name.split(separator: "_").map(String.init)
        guard let first = words.first else { return name }
        return ([first.prefix(1).uppercased() + first.dropFirst()] + words.dropFirst()).joined(separator: " ")
    }

    // MARK: actions

    public func send(_ raw: String) async {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isBusy else { return }
        items.append(ChatItem(kind: .user, text: text))
        guard let tools else {
            items.append(ChatItem(kind: .error, text: "The engine is not running, so the assistant cannot work on the design. Check the status bar below."))
            return
        }
        isBusy = true
        defer {
            isBusy = false
            activity = nil
        }

        // What the designer changed in Rhino since the last message goes to Claude first.
        let notes = (try? await tools.pendingDesignNotes()) ?? []
        var message = text
        if !notes.isEmpty {
            let list = notes.map { "- " + $0 }.joined(separator: "\n")
            items.append(ChatItem(kind: .notice, text: "Rhino edits told to the assistant:\n" + list))
            message = "[Rhino edits since my last message]\n" + list + "\n\n" + text
        }
        let checkpoint = history.count
        history.append(["role": "user", "content": .string(message)])
        do {
            activity = "Thinking…"
            var steps = 0
            while true {
                steps += 1
                if self.setup == nil { self.setup = try await tools.designSetup() }
                guard let setup = self.setup else { return }
                let response = try await model.send(ModelRequest(
                    model: modelID, system: setup.system, tools: setup.tools, messages: history, effort: effort
                ))
                history.append(["role": "assistant", "content": .array(response.content)])
                for block in response.content where block["type"]?.stringValue == "text" {
                    if let t = block["text"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty {
                        items.append(ChatItem(kind: .assistant, text: t))
                    }
                }
                switch response.stopReason {
                case "tool_use":
                    history.append(["role": "user", "content": .array(await runTools(in: response.content, with: tools))])
                    if steps >= maxSteps {
                        items.append(ChatItem(kind: .notice, text: "Stopped after \(maxSteps) steps. Say \"continue\" to let the assistant carry on."))
                        return
                    }
                    activity = "Thinking…"
                case "refusal":
                    items.append(ChatItem(kind: .notice, text: "Claude declined this request. Try rewording it."))
                    return
                case "max_tokens":
                    items.append(ChatItem(kind: .notice, text: "The reply was cut off because it got too long. Say \"continue\" for the rest."))
                    return
                default:
                    return
                }
            }
        } catch {
            history.removeSubrange(checkpoint...)
            items.append(ChatItem(kind: .error, text: error.localizedDescription + " Your message was not sent; try again."))
        }
    }

    /// Run every tool_use block and return the tool_result blocks, in order, for one user message.
    private func runTools(in content: [JSONValue], with tools: DesignToolRunner) async -> [JSONValue] {
        var results: [JSONValue] = []
        for block in content where block["type"]?.stringValue == "tool_use" {
            let id = block["id"]?.stringValue ?? ""
            let name = block["name"]?.stringValue ?? ""
            activity = "Running \(Self.title(forTool: name))…"
            let output: DesignToolOutput
            do {
                output = try await tools.runDesignTool(name: name, input: block["input"] ?? [:])
            } catch {
                output = DesignToolOutput(content: "The engine could not run \(name): \(error.localizedDescription)",
                                          isError: true, version: 0)
            }
            items.append(ChatItem(kind: .tool(name: name, isError: output.isError), text: output.content))
            if output.setupChanged {
                setup = nil
                onSetupChanged?()
            }
            results.append([
                "type": "tool_result",
                "tool_use_id": .string(id),
                "content": .string(output.content),
                "is_error": .bool(output.isError),
            ])
        }
        return results
    }

    /// Replace the whole chat (opening a project). Use `restore(conversation:)`.
    func replaceConversation(items: [ChatItem], history: [JSONValue]) {
        self.items = items
        self.history = history
    }

    /// A line in the chat from the app itself (not sent to Claude).
    public func addNotice(_ text: String) {
        items.append(ChatItem(kind: .notice, text: text))
    }

    /// Clear the chat and the engine's design.
    public func newDesign() async {
        guard !isBusy else { return }
        items = []
        history = []
        try? await tools?.resetDesign()
    }
}
