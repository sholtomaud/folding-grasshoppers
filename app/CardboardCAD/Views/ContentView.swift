import CardboardCADKit
import SwiftUI

struct ContentView: View {
    let engine: EngineManager
    let assistant: DesignAssistant
    let claudeKey: ClaudeKey
    let projects: ProjectController
    let workflows: WorkflowController
    let projectViews: ProjectViewsController
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        // No HSplitView. It is an NSSplitView with each pane in its own hosting
        // view, and on a designer's Mac the chat pane came out taller than the
        // space above the status bar, hiding the message box. ChatSplit is plain
        // SwiftUI inside a GeometryReader: each pane gets exactly that height,
        // and DesignAssistantView lets only the transcript shrink.
        VStack(spacing: 0) {
            ChatSplit {
                DesignAssistantView(engine: engine, assistant: assistant, claudeKey: claudeKey, projects: projects)
            } right: {
                PreviewArea(engine: engine, assistant: assistant, views: projectViews)
            }
            .frame(maxHeight: .infinity)
            StatusBar(engine: engine, openSettings: { openSettings() })
        }
        .task(id: runningPort) {
            let client = runningPort.map { EngineClient(port: $0) }
            assistant.attach(tools: client)
            projects.attach(store: client)
            workflows.attach(client: client)
            projectViews.attach(client: client)
            await workflows.refresh()
        }
        .onReceive(NotificationCenter.default.publisher(for: .foldTheorySaveProject)) { _ in
            Task {
                if projects.current == nil {
                    await ProjectPanels.saveAs(projects)
                } else {
                    await projects.saveAgain()
                }
            }
        }
        .toolbar { WorkflowToolbar(workflows: workflows) }
        .navigationSubtitle(projects.current?.name ?? "")
        .alert("Project", isPresented: projectErrorShown) {
            Button("OK") { projects.error = nil }
        } message: {
            Text(projects.error ?? "")
        }
    }

    private var projectErrorShown: Binding<Bool> {
        Binding(get: { projects.error != nil }, set: { if !$0 { projects.error = nil } })
    }

    private var runningPort: Int? {
        if case .running(let port) = engine.state { return port }
        return nil
    }
}

// MARK: - Chat | preview split

/// Two panes side by side with a draggable divider. The chat's width is kept
/// between launches.
struct ChatSplit<Left: View, Right: View>: View {
    static var minLeft: CGFloat { 340 }
    static var maxLeft: CGFloat { 640 }
    static var minRight: CGFloat { 420 }

    @AppStorage("chatPaneWidth") private var storedWidth: Double = 420
    @State private var dragStartWidth: CGFloat?
    private let left: Left
    private let right: Right

    init(@ViewBuilder left: () -> Left, @ViewBuilder right: () -> Right) {
        self.left = left()
        self.right = right()
    }

    var body: some View {
        GeometryReader { geo in
            let width = Self.clamp(CGFloat(storedWidth), total: geo.size.width)
            HStack(spacing: 0) {
                left
                    .frame(width: width, height: geo.size.height)
                    .clipped()
                handle(width: width, total: geo.size.width)
                right
                    .frame(maxWidth: .infinity)
                    .frame(height: geo.size.height)
                    .clipped()
            }
            .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
        }
    }

    static func clamp(_ width: CGFloat, total: CGFloat) -> CGFloat {
        let upper = max(minLeft, min(maxLeft, total - minRight - 1))
        return min(max(width, minLeft), upper)
    }

    private func handle(width: CGFloat, total: CGFloat) -> some View {
        Rectangle()
            .fill(Color(nsColor: .separatorColor))
            .frame(width: 1)
            .frame(maxHeight: .infinity)
            .overlay {
                Color.clear
                    .frame(width: 9)
                    .contentShape(Rectangle())
                    .onHover { inside in
                        if inside { NSCursor.resizeLeftRight.push() } else { NSCursor.pop() }
                    }
                    .gesture(
                        DragGesture(minimumDistance: 1)
                            .onChanged { value in
                                let start = dragStartWidth ?? width
                                if dragStartWidth == nil { dragStartWidth = width }
                                storedWidth = Double(Self.clamp(start + value.translation.width, total: total))
                            }
                            .onEnded { _ in dragStartWidth = nil }
                    )
            }
            .accessibilityHidden(true)
    }
}

// MARK: - Manual tools window

/// The hand-entered forms from before the Design Assistant: still handy for
/// a quick cut-cost check. Window > Manual Tools.
struct ManualToolsView: View {
    static let windowID = "manual-tools"
    let engine: EngineManager

    var body: some View {
        Form {
            HelloSection(engine: engine)
            CutEstimateSections(engine: engine)
        }
        .formStyle(.grouped)
        .frame(minWidth: 420, idealWidth: 480, minHeight: 500)
    }
}

// MARK: - Status bar

/// Bottom bar, like Xcode's or Finder's: engine state on the left, when
/// Grasshopper last called on the right. Replaces the toolbar pill.
struct StatusBar: View {
    let engine: EngineManager
    let openSettings: () -> Void
    @State private var hops: HopsActivity?

    var body: some View {
        VStack(spacing: 0) {
            Divider()
            HStack(spacing: 12) {
                engineStatus
                Spacer(minLength: 12)
                if runningPort != nil { hopsStatus }
            }
            .font(.callout)
            .padding(.horizontal, 12)
            .frame(height: 28)
        }
        .background(.bar)
        .task(id: runningPort) { await pollHops() }
    }

    // MARK: engine

    private var engineStatus: some View {
        HStack(spacing: 6) {
            StatusDot(color: engineColor)
            Text(engineTitle)
            if let detail = engineDetail {
                Text(detail).foregroundStyle(.secondary)
            }
            if case .failed(let failure) = engine.state {
                switch failure.action {
                case .openSettings:
                    Button("Open Settings…", action: openSettings).buttonStyle(.link)
                case .viewLogs:
                    Button("Show Logs") { LogsFolder.reveal(engine) }.buttonStyle(.link)
                }
            }
        }
        .lineLimit(1)
        .help(engineTooltip)
        .accessibilityElement(children: .combine)
    }

    private var runningPort: Int? {
        if case .running(let port) = engine.state { return port }
        return nil
    }

    private var engineColor: Color {
        switch engine.state {
        case .stopped: return .secondary
        case .starting, .restarting: return .orange
        case .running: return .green
        case .failed: return .red
        }
    }

    private var engineTitle: String {
        switch engine.state {
        case .stopped: return "Engine stopped"
        case .starting: return "Engine starting…"
        case .restarting(let n): return "Engine restarting (attempt \(n))…"
        case .running: return "Engine running"
        case .failed(let f): return f.reason
        }
    }

    private var engineDetail: String? {
        guard let port = runningPort else { return nil }
        var s = "127.0.0.1:\(port)"
        if let v = engine.engineVersion { s += " · v\(v)" }
        return s
    }

    private var engineTooltip: String {
        var s = engine.state.summary
        if let v = engine.engineVersion { s += "\nEngine \(v)" }
        if engine.config.usesDevRuntime { s += "\nDev runtime" }
        return s
    }

    // MARK: Grasshopper

    private var hopsStatus: some View {
        HStack(spacing: 6) {
            StatusDot(color: hopsColor)
            Text(hops?.summary ?? "Grasshopper: no calls yet")
                .foregroundStyle(hops?.level == .recent ? .primary : .secondary)
        }
        .lineLimit(1)
        .help(hopsTooltip)
        .accessibilityElement(children: .combine)
    }

    private var hopsColor: Color {
        switch hops?.level ?? .never {
        case .never: return .secondary
        case .recent: return .green
        case .idle: return .secondary
        case .error: return .red
        }
    }

    private var hopsTooltip: String {
        guard let port = runningPort else { return "" }
        var s = "Hops calls the engine each time a component loads or solves.\n"
        s += "Paste http://127.0.0.1:\(port)/hello into a Hops component's Path."
        if let h = hops, h.requests > 0 {
            s += "\n\n\(h.requests) call(s) so far"
            if let path = h.lastPath { s += "\nLast: \(path)" }
            if let status = h.lastStatus { s += " → HTTP \(status)" }
        }
        return s
    }

    private func pollHops() async {
        hops = nil
        guard let port = runningPort else { return }
        let client = EngineClient(port: port)
        while !Task.isCancelled {
            if let a = try? await client.hopsActivity() { hops = a }
            try? await Task.sleep(for: .seconds(2))
        }
    }
}

struct StatusDot: View {
    let color: Color
    var body: some View {
        Circle().fill(color).frame(width: 8, height: 8)
    }
}

// MARK: - Hello world demo

/// Smallest app -> engine round trip: POST /api/hello.
struct HelloSection: View {
    let engine: EngineManager
    @State private var name = "world"
    @State private var reply: String?

    var body: some View {
        Section("Hello world") {
            LabeledContent("Name") {
                HStack {
                    TextField("Name", text: $name)
                        .labelsHidden()
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 180)
                        .onSubmit { Task { await sayHello() } }
                    Button("Say Hello") { Task { await sayHello() } }
                        .disabled(!isRunning)
                }
            }
            if let reply {
                LabeledContent("Reply") {
                    Text(reply).textSelection(.enabled).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var isRunning: Bool {
        if case .running = engine.state { return true }
        return false
    }

    private func sayHello() async {
        guard case .running(let port) = engine.state else { return }
        do {
            reply = try await EngineClient(port: port).hello(name: name)
        } catch {
            reply = error.localizedDescription
        }
    }
}

// MARK: - Cut estimate

struct CutEstimateSections: View {
    let engine: EngineManager
    @State private var input = CutEstimateInput(cutLength: 2000)
    @State private var result: CutEstimateResult?
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        Group {
            Section("Cutting") {
                field("Cut length (mm)", "Lc", $input.cutLength)
                field("Score length (mm)", "Ls", $input.scoreLength)
                field("Pierce count", "N", $input.pierceCount)
                field("Sheet count", "S", $input.sheetCount)
            }
            Section("Machine") {
                field("Cut speed (mm/s)", "Vc", $input.cutSpeed)
                field("Score speed (mm/s)", "Vs", $input.scoreSpeed)
                field("Pierce time (s)", "Tp", $input.pierceTime)
                field("Travel overhead (fraction)", "k", $input.travelOverhead)
            }
            Section("Cost") {
                field("Sheet price", "Ps", $input.sheetPrice)
                field("Machine rate (per hour)", "R", $input.machineRate)
            }
            Section("Estimate") {
                HStack {
                    Button("Estimate") { Task { await run() } }
                        .keyboardShortcut(.return, modifiers: [.command])
                        .buttonStyle(.borderedProminent)
                        .disabled(!isRunning || busy)
                    if busy { ProgressView().controlSize(.small) }
                    Spacer()
                    Text("⌘↩").foregroundStyle(.tertiary)
                }
                if let error {
                    Text(error).foregroundStyle(.red).textSelection(.enabled)
                }
                if let r = result {
                    LabeledContent("Time (min)", value: format(r.timeMin))
                    LabeledContent("Material cost", value: format(r.materialCost))
                    LabeledContent("Machine cost", value: format(r.machineCost))
                    LabeledContent("Total cost", value: format(r.totalCost)).bold()
                }
            }
        }
    }

    private var isRunning: Bool {
        if case .running = engine.state { return true }
        return false
    }

    private func field(_ name: String, _ nick: String, _ value: Binding<Double>) -> some View {
        LabeledContent {
            TextField(name, value: value, format: .number)
                .labelsHidden()
                .textFieldStyle(.roundedBorder)
                .multilineTextAlignment(.trailing)
                .monospacedDigit()
                .frame(width: 110)
        } label: {
            HStack(spacing: 6) {
                Text(name)
                Text(nick)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 4)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 3))
                    .help("Hops input nickname")
            }
        }
    }

    private func format(_ v: Double) -> String {
        v.formatted(.number.precision(.fractionLength(2...4)))
    }

    private func run() async {
        guard case .running(let port) = engine.state else { return }
        busy = true
        defer { busy = false }
        do {
            result = try await EngineClient(port: port).cutEstimate(input)
            error = nil
        } catch {
            result = nil
            self.error = error.localizedDescription
        }
    }
}
