import AppKit
import CardboardCADKit
import Observation
import SwiftUI
import UniformTypeIdentifiers

enum AppInfo {
    static let name = "CardboardCAD"
    static let portDefaultsKey = "enginePort"

    /// Debug builds may run `build/python-runtime` via CARDBOARD_DEV_RUNTIME.
    static var allowDevRuntime: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }

    static let assistantModelKey = "assistantModel"
    static let assistantEffortKey = "assistantEffort"

    static var storedAssistantModel: String {
        let id = UserDefaults.standard.string(forKey: assistantModelKey) ?? ""
        return AssistantModel.all.contains { $0.id == id } ? id : AssistantModel.defaultID
    }

    static var storedAssistantEffort: String {
        let e = UserDefaults.standard.string(forKey: assistantEffortKey) ?? ""
        return AssistantModel.efforts.contains(e) ? e : AssistantModel.defaultEffort
    }

    static var storedPort: Int {
        let p = UserDefaults.standard.integer(forKey: portDefaultsKey)
        return EngineConfig.isValidPort(p) ? p : EngineConfig.defaultPort
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    /// Hard cap on how long quitting may wait for the engine (SPEC 8.2).
    static let terminationCap: Double = 6

    let engine = EngineManager(
        config: EngineConfig.resolve(
            appName: AppInfo.name,
            port: AppInfo.storedPort,
            allowDevRuntime: AppInfo.allowDevRuntime
        )
    )

    let claudeKey = ClaudeKey(store: KeychainSecretStore())

    /// The chat. Claude is called from the app with the key from the Keychain;
    /// the key is read on each request and never passed to the engine.
    let assistant = DesignAssistant(
        model: AnthropicClient(apiKey: { try? KeychainSecretStore().read() }),
        modelID: AppInfo.storedAssistantModel,
        effort: AppInfo.storedAssistantEffort
    )

    /// Save / Open for the File menu. The chat is saved with the project.
    lazy var projects: ProjectController = {
        let p = ProjectController(assistant: self.assistant)
        p.afterSave = { folder in await PreviewSnapshot.shared.writePNG(into: folder) }
        return p
    }()

    /// The workflow the assistant follows and the Apply Defaults switch (kept by the engine).
    let workflows = WorkflowController()

    func applicationDidFinishLaunching(_ notification: Notification) {
        let assistant = assistant
        let workflows = workflows
        workflows.onChange = { assistant.reloadSetup() }
        assistant.onSetupChanged = { Task { await workflows.refresh() } }
        Task { await engine.start() }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if engine.state == .stopped { return .terminateNow }
        let engine = engine
        Task { @MainActor in
            let stop = Task { @MainActor in await engine.stop() }
            let cap = Task { @MainActor in
                try? await Task.sleep(nanoseconds: UInt64(Self.terminationCap * 1_000_000_000))
            }
            // Whichever finishes first.
            await withTaskGroup(of: Void.self) { group in
                group.addTask { await stop.value }
                group.addTask { await cap.value }
                await group.next()
                group.cancelAll()
            }
            cap.cancel()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct CardboardCADApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        WindowGroup(AppInfo.name) {
            ContentView(engine: delegate.engine, assistant: delegate.assistant, claudeKey: delegate.claudeKey,
                        projects: delegate.projects, workflows: delegate.workflows)
                .frame(minWidth: 900, minHeight: 600)
        }
        .defaultSize(width: 1280, height: 800)
        .commands {
            ProjectCommands(projects: delegate.projects, assistant: delegate.assistant)
            DesignCommands(assistant: delegate.assistant, projects: delegate.projects, engine: delegate.engine,
                           workflows: delegate.workflows)
            CommandMenu("Engine") {
                Button("Restart Engine") {
                    Task { await delegate.engine.restart() }
                }
                .keyboardShortcut("r", modifiers: [.command, .shift])
                Button("Show Logs") {
                    LogsFolder.reveal(delegate.engine)
                }
            }
        }

        Window("Manual Tools", id: ManualToolsView.windowID) {
            ManualToolsView(engine: delegate.engine)
        }
        .defaultSize(width: 520, height: 720)

        Settings {
            SettingsView(engine: delegate.engine, assistant: delegate.assistant, claudeKey: delegate.claudeKey,
                         workflows: delegate.workflows)
        }
    }
}

enum LogsFolder {
    @MainActor
    static func reveal(_ engine: EngineManager) {
        let dir = engine.config.logsDirectory
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        if let file = engine.logFileURL, FileManager.default.fileExists(atPath: file.path) {
            NSWorkspace.shared.activateFileViewerSelecting([file])
        } else {
            NSWorkspace.shared.open(dir)
        }
    }
}

// MARK: - Design menu

struct DesignCommands: Commands {
    let assistant: DesignAssistant
    let projects: ProjectController
    let engine: EngineManager
    let workflows: WorkflowController
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        CommandMenu("Design") {
            Button("New Design") {
                Task {
                    await assistant.newDesign()
                    projects.forget()
                }
            }
            .keyboardShortcut("n", modifiers: [.command, .shift])
            Divider()
            WorkflowMenu(workflows: workflows)
            Divider()
            Button("Manual Tools") {
                openWindow(id: ManualToolsView.windowID)
            }
            .keyboardShortcut("t", modifiers: [.command, .shift])
            Divider()
            Button("Copy Rhino Live Listener Script") {
                Task { await RhinoLiveSync.copyListenerScript(engine: engine) }
            }
            Button("Rhino Live Sync Help") {
                NSWorkspace.shared.open(RhinoLiveSync.helpURL)
            }
        }
    }
}

// MARK: - File menu: projects

struct ProjectCommands: Commands {
    let projects: ProjectController
    let assistant: DesignAssistant

    var body: some Commands {
        CommandGroup(after: .newItem) {
            Button("Open Project…") {
                Task { await ProjectPanels.open(projects) }
            }
            .keyboardShortcut("o", modifiers: .command)
            .disabled(projects.isWorking || assistant.isBusy)
            Divider()
            Button("Save Project") {
                Task {
                    if projects.current == nil {
                        await ProjectPanels.saveAs(projects)
                    } else {
                        await projects.saveAgain()
                    }
                }
            }
            .keyboardShortcut("s", modifiers: .command)
            .disabled(projects.isWorking)
            Button("Save Project As…") {
                Task { await ProjectPanels.saveAs(projects) }
            }
            .keyboardShortcut("s", modifiers: [.command, .shift])
            .disabled(projects.isWorking)
            Button("Show Project in Finder") {
                if let folder = projects.current?.folder {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: folder, isDirectory: true)])
                }
            }
            .disabled(projects.current == nil)
        }
    }
}

/// The open and save panels. A project is a folder; Save As names a new folder.
@MainActor
enum ProjectPanels {
    static func saveAs(_ projects: ProjectController) async {
        let panel = NSSavePanel()
        panel.title = "Save Project"
        panel.message = "A project is a folder with the model, parts, cut files, cost estimate and chat."
        panel.prompt = "Save"
        panel.nameFieldLabel = "Project name:"
        panel.nameFieldStringValue = projects.current?.name ?? "Untitled Project"
        panel.canCreateDirectories = true
        let base = ProjectController.defaultProjectsFolder()
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        panel.directoryURL = projects.current.map { $0.url.deletingLastPathComponent() } ?? base
        guard panel.runModal() == .OK, let url = panel.url else { return }
        await projects.save(name: url.lastPathComponent, folder: url.path)
    }

    static func open(_ projects: ProjectController) async {
        let panel = NSOpenPanel()
        panel.title = "Open Project"
        panel.message = "Choose a project folder (or its project.json)."
        panel.prompt = "Open"
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowedContentTypes = [.folder, .json]
        panel.allowsMultipleSelection = false
        let base = ProjectController.defaultProjectsFolder()
        if FileManager.default.fileExists(atPath: base.path) { panel.directoryURL = base }
        guard panel.runModal() == .OK, let url = panel.url else { return }
        await projects.open(folder: url.path)
    }
}

// MARK: - Rhino live sync

@MainActor
enum RhinoLiveSync {
    static let helpURL = URL(string: "https://github.com/sholtomaud/folding-grasshoppers/blob/main/docs/RHINO_LIVE_SYNC.md")!

    /// Put the Grasshopper listener script on the clipboard, to paste into a Python script component.
    static func copyListenerScript(engine: EngineManager) async {
        guard case .running(let port) = engine.state else {
            alert("The engine is not running", "Start CardboardCAD's engine first (Engine > Restart Engine).")
            return
        }
        do {
            let (data, _) = try await URLSession.shared.data(from: EngineClient(port: port).liveListenerURL)
            let script = String(decoding: data, as: UTF8.self)
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(script, forType: .string)
            alert("Listener script copied",
                  "In Grasshopper, add a Python script component, open it, select all, paste, and give it the inputs On, Interval, Port and the outputs Version, Status. Then connect Version to the LiveDesign Hops component. Design > Rhino Live Sync Help has the full steps.")
        } catch {
            alert("Could not get the listener script", error.localizedDescription)
        }
    }

    private static func alert(_ title: String, _ text: String) {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.runModal()
    }
}

// MARK: - Claude API key

/// Whether a Claude API key is saved, for the chat and Settings. The key itself
/// stays in the Keychain; only a masked form is kept here.
@MainActor
@Observable
final class ClaudeKey {
    private let store: SecretStore
    private(set) var masked: String?
    private(set) var error: String?

    init(store: SecretStore) {
        self.store = store
        refresh()
    }

    var isSet: Bool { masked != nil }

    func refresh() {
        do {
            masked = try store.read().map(APIKey.masked)
            error = nil
        } catch {
            masked = nil
            self.error = error.localizedDescription
        }
    }

    func save(_ raw: String) {
        do {
            try APIKey.save(raw, to: store)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        refreshKeepingError()
    }

    func remove() {
        do {
            try store.delete()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        refreshKeepingError()
    }

    private func refreshKeepingError() {
        masked = (try? store.read()).map(APIKey.masked)
    }
}
