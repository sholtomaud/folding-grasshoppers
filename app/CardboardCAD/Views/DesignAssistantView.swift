import AppKit
import CardboardCADKit
import SwiftUI
import UniformTypeIdentifiers

/// The chat: describe what to make, and Claude runs the engine's tools.
struct DesignAssistantView: View {
    let engine: EngineManager
    let assistant: DesignAssistant
    let claudeKey: ClaudeKey
    let projects: ProjectController
    @Environment(\.openSettings) private var openSettings
    @State private var draft = ""
    @FocusState private var composerFocused: Bool

    static let examples = [
        "Make a cylinder stool 450 mm tall and 350 mm wide as a waffle from 6 mm board",
        "A sphere 400 mm across as a contour stack of 10 mm layers, nested on 1000 × 700 sheets",
        "Unfold an 8-sided cone 300 mm tall with glue tabs, then export SVG files",
    ]

    var body: some View {
        VStack(spacing: 0) {
            header
                .fixedSize(horizontal: false, vertical: true)
            Divider()
            if !claudeKey.isSet {
                missingKey
            } else {
                // The transcript takes whatever height is left and scrolls; the
                // message box keeps its full height and is laid out first.
                transcript
                    .frame(minHeight: 0, maxHeight: .infinity)
                Divider()
                composer
                    .fixedSize(horizontal: false, vertical: true)
                    .layoutPriority(1)
            }
        }
        .onAppear { claudeKey.refresh() }
    }

    // MARK: header

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "bubble.left.and.text.bubble.right")
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text("Design Assistant").font(.headline)
                Text(AssistantModel.all.first { $0.id == assistant.modelID }?.name ?? assistant.modelID)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                Task {
                    await assistant.newDesign()
                    projects.forget()
                }
            } label: {
                Label("New Design", systemImage: "square.and.pencil")
            }
            .labelStyle(.iconOnly)
            .help("Start over: clears this chat and the design")
            .disabled(assistant.isBusy || assistant.items.isEmpty)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    // MARK: no key

    private var missingKey: some View {
        ContentUnavailableView {
            Label("Add your Claude API key", systemImage: "key")
        } description: {
            Text("The Design Assistant uses Claude. Paste an API key from console.anthropic.com into Settings. It is stored in your Mac's Keychain.")
        } actions: {
            Button("Open Settings…") { openSettings() }
                .buttonStyle(.borderedProminent)
        }
        .frame(maxHeight: .infinity)
    }

    // MARK: transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if assistant.items.isEmpty { welcome }
                    ForEach(assistant.items) { item in
                        ChatRow(item: item, assistant: assistant).id(item.id)
                    }
                    if assistant.isBusy {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text(assistant.activity ?? "Working…").foregroundStyle(.secondary)
                        }
                        .id("busy")
                    }
                }
                .padding(12)
            }
            .onChange(of: assistant.items.count) {
                if let id = assistant.items.last?.id {
                    withAnimation { proxy.scrollTo(id, anchor: .bottom) }
                }
            }
            .onChange(of: assistant.isBusy) {
                if assistant.isBusy { withAnimation { proxy.scrollTo("busy", anchor: .bottom) } }
            }
        }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Describe what you want to make. The assistant builds it with the engine, and the preview on the right shows the 3D model and the flat cut sheets.")
                .foregroundStyle(.secondary)
            Text("Try:").font(.caption).foregroundStyle(.secondary)
            ForEach(Self.examples, id: \.self) { example in
                Button {
                    draft = example
                    composerFocused = true
                } label: {
                    Text(example)
                        .multilineTextAlignment(.leading)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
            }
            Text("You can also attach your own model from Rhino (.stl or .obj) with the paperclip.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: composer

    private var canSend: Bool {
        isRunning && !assistant.isBusy && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var isRunning: Bool {
        if case .running = engine.state { return true }
        return false
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .bottom, spacing: 8) {
                Button(action: openModel) {
                    Image(systemName: "paperclip").font(.title3)
                }
                .buttonStyle(.borderless)
                .help("Open a model from Rhino (.stl or .obj)")
                .disabled(!isRunning || assistant.isBusy)
                .padding(.bottom, 8)

                TextField(isRunning ? "Describe what to make…" : "Waiting for the engine…", text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.body)
                    .lineLimit(2...8)
                    .focused($composerFocused)
                    .onSubmit(send)
                    .padding(.vertical, 10)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 56, alignment: .topLeading)
                    .background(.background, in: RoundedRectangle(cornerRadius: 10))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .strokeBorder(composerFocused ? Color.accentColor : Color.secondary.opacity(0.35),
                                          lineWidth: composerFocused ? 2 : 1)
                    )

                Button(action: send) {
                    Image(systemName: "arrow.up.circle.fill").font(.system(size: 28))
                }
                .buttonStyle(.borderless)
                .keyboardShortcut(.return, modifiers: [.command])
                .help("Send (Return)")
                .disabled(!canSend)
                .padding(.bottom, 6)
            }
            Text("Return sends · Option-Return adds a new line")
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .padding(.leading, 34)
        }
        .padding(.horizontal, 12)
        .padding(.top, 10)
        .padding(.bottom, 12)
        .background(.bar)
    }

    private func send() {
        guard canSend else { return }
        let text = draft
        draft = ""
        Task { await assistant.send(text) }
    }

    private func openModel() {
        let panel = NSOpenPanel()
        panel.title = "Open a model"
        panel.message = "Choose an .stl or .obj file in your home folder (in Rhino: File > Export Selected)."
        panel.allowedContentTypes = ["stl", "obj"].compactMap { UTType(filenameExtension: $0) }
        panel.allowsMultipleSelection = false
        panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { await assistant.send("Load my model file: \(url.path)") }
    }
}

// MARK: - One message

struct ChatRow: View {
    let item: DesignAssistant.ChatItem
    let assistant: DesignAssistant

    var body: some View {
        switch item.kind {
        case .user:
            HStack {
                Spacer(minLength: 40)
                Text(item.text)
                    .textSelection(.enabled)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .foregroundStyle(.white)
                    .background(Color.accentColor, in: RoundedRectangle(cornerRadius: 12))
            }
        case .assistant:
            VStack(alignment: .leading, spacing: 4) {
                Text(markdown(item.text))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if !item.unverified.isEmpty {
                    // Claude never calculates: these numbers did not come from the engine this turn.
                    Label("Unverified: \(item.unverified.joined(separator: ", ")). The engine did not give these numbers; check the Project tab.",
                          systemImage: "exclamationmark.shield")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .help("Every number that matters is worked out by the engine. Numbers in a reply that no tool gave are marked.")
                }
            }
        case .tool(let name, let isError):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Image(systemName: isError ? "exclamationmark.triangle" : "hammer")
                    .foregroundStyle(isError ? .orange : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(DesignAssistant.title(forTool: name)).font(.caption.bold())
                    Text(item.text)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .lineLimit(6)
                }
            }
            .padding(.leading, 4)
        case .error:
            Label {
                Text(item.text).textSelection(.enabled)
            } icon: {
                Image(systemName: "xmark.octagon").foregroundStyle(.red)
            }
        case .notice:
            Label(item.text, systemImage: "info.circle")
                .foregroundStyle(.secondary)
        case .form(let form):
            InputFormCard(itemID: item.id, form: form, assistant: assistant)
        }
    }

    private func markdown(_ raw: String) -> AttributedString {
        // Inline-only parsing keeps line breaks but not list syntax: show "- x" as "• x".
        let text = raw.split(separator: "\n", omittingEmptySubsequences: false).map { line -> String in
            let t = line.drop { $0 == " " }
            let indent = String(repeating: " ", count: line.count - t.count)
            if t.hasPrefix("- ") || t.hasPrefix("* ") { return indent + "• " + t.dropFirst(2) }
            return String(line)
        }.joined(separator: "\n")
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return (try? AttributedString(markdown: text, options: options)) ?? AttributedString(text)
    }
}
