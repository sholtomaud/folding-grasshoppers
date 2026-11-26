import CardboardCADKit
import SwiftUI

struct ContentView: View {
    let engine: EngineManager
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                HelloCard(engine: engine)
                CutEstimateForm(engine: engine)
            }
            .frame(minWidth: 380)
            ChatPlaceholder()
                .frame(minWidth: 240)
        }
        .toolbar {
            ToolbarItem(placement: .status) {
                EngineStatusBadge(engine: engine, openSettings: { openSettings() })
            }
        }
    }
}

// MARK: - Status

struct EngineStatusBadge: View {
    let engine: EngineManager
    let openSettings: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(color)
                .frame(width: 10, height: 10)
                .accessibilityLabel(engine.state.summary)
            Text(label).font(.callout)
            if case .failed(let failure) = engine.state {
                switch failure.action {
                case .openSettings:
                    Button("Open Settings", action: openSettings)
                case .viewLogs:
                    Button("View Logs") { LogsFolder.reveal(engine) }
                }
            }
        }
        .help(tooltip)
    }

    private var color: Color {
        switch engine.state {
        case .stopped: return .gray
        case .starting, .restarting: return .orange
        case .running: return .green
        case .failed: return .red
        }
    }

    private var label: String {
        switch engine.state {
        case .stopped: return "Stopped"
        case .starting: return "Starting…"
        case .restarting(let n): return "Restarting (\(n))…"
        case .running(let port): return "Running on \(port)"
        case .failed(let f): return f.reason
        }
    }

    private var tooltip: String {
        var s = engine.state.summary
        if let v = engine.engineVersion { s += "\nEngine \(v)" }
        if engine.config.usesDevRuntime { s += "\nDev runtime" }
        return s
    }
}

// MARK: - Hello world demo

/// Smallest app -> engine round trip: POST /api/hello.
struct HelloCard: View {
    let engine: EngineManager
    @State private var name = "world"
    @State private var reply: String?

    var body: some View {
        Form {
            Section("Hello world") {
                HStack {
                    TextField("Name", text: $name)
                    Button("Say hello") { Task { await sayHello() } }
                        .disabled(!isRunning)
                }
                if let reply {
                    Text(reply).textSelection(.enabled)
                }
            }
        }
        .formStyle(.grouped)
        .frame(maxHeight: 150)
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

struct CutEstimateForm: View {
    let engine: EngineManager
    @State private var input = CutEstimateInput(cutLength: 2000)
    @State private var result: CutEstimateResult?
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        Form {
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
            Section {
                HStack {
                    Button("Estimate") { Task { await run() } }
                        .keyboardShortcut(.return, modifiers: [.command])
                        .disabled(!isRunning || busy)
                    if busy { ProgressView().controlSize(.small) }
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
        .formStyle(.grouped)
    }

    private var isRunning: Bool {
        if case .running = engine.state { return true }
        return false
    }

    private func field(_ name: String, _ nick: String, _ value: Binding<Double>) -> some View {
        LabeledContent {
            TextField(nick, value: value, format: .number)
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 140)
        } label: {
            Text(name) + Text("  \(nick)").foregroundColor(.secondary)
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

// MARK: - Chat placeholder

struct ChatPlaceholder: View {
    @State private var draft = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Chat (coming soon)").font(.headline)
            Spacer()
            TextField("Ask about your design…", text: $draft)
                .disabled(true)
        }
        .padding()
        .foregroundStyle(.secondary)
    }
}
