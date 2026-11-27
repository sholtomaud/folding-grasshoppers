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

    /// The window probe (CI only) uses a throwaway in-memory key, never the Keychain.
    let claudeKey = ClaudeKey(store: WindowProbe.isEnabled
        ? InMemorySecretStore(WindowProbe.placeholderKey) as SecretStore
        : KeychainSecretStore())

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
        if WindowProbe.isEnabled {
            let engine = engine, assistant = assistant
            Task { await WindowProbe.run(engine: engine, assistant: assistant) }
        }
    }

    /// Set once the engine is stopped (or the cap ran out): the next terminate goes through.
    private var readyToQuit = false
    private var quitInProgress = false

    /// Quitting (Cmd+Q, the menu, Dock, AppleScript).
    ///
    /// This used to return `.terminateLater` and stop the engine in a Task while
    /// AppKit waited in a nested run loop. When the quit came from code already
    /// running on the main queue (a menu action dispatched there), that Task
    /// could never run and the app hung on Cmd+Q. CI's window probe reproduced it
    /// on macOS 15 and 26. Now the first request is cancelled, the engine is
    /// stopped normally, and the app terminates again by itself. A run-loop
    /// timer caps the wait, and logout or shutdown stop the engine at once.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if readyToQuit || engine.state == .stopped { return .terminateNow }
        if Self.isSystemQuit() {
            engine.stopImmediately()
            return .terminateNow
        }
        guard !quitInProgress else { return .terminateCancel }
        quitInProgress = true

        let engine = engine
        Task { @MainActor [weak self] in
            await engine.stop()
            self?.finishQuit()
        }
        // Safety net in common run-loop modes, independent of Swift tasks.
        let cap = Timer(timeInterval: Self.terminationCap, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated { self?.finishQuit() }
        }
        RunLoop.main.add(cap, forMode: .common)
        return .terminateCancel
    }

    private func finishQuit() {
        guard !readyToQuit else { return }
        engine.stopImmediately()   // no-op when stop() already finished
        readyToQuit = true
        NSApp.terminate(nil)
    }

    /// Logout, restart or shutdown: the system sends a quit reason, and
    /// cancelling would cancel the logout, so those quit at once.
    private static func isSystemQuit() -> Bool {
        guard let event = NSAppleEventManager.shared().currentAppleEvent else { return false }
        return event.attributeDescriptor(forKeyword: AEKeyword(kAEQuitReason)) != nil
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
                .frame(minWidth: 900, minHeight: 480)
        }
        .defaultSize(width: 1280, height: 800)
        .commands {
            ProjectCommands(projects: delegate.projects, assistant: delegate.assistant)
            DesignCommands(assistant: delegate.assistant, projects: delegate.projects, engine: delegate.engine,
                           workflows: delegate.workflows)
            // Cmd+Q: quit and stop the engine (AppDelegate.applicationShouldTerminate).
            CommandGroup(replacing: .appTermination) {
                Button("Quit \(AppInfo.name)") { NSApp.terminate(nil) }
                    .keyboardShortcut("q", modifiers: .command)
            }
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
        guard await ModalUI.run(panel) == .OK, let url = panel.url else { return }
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
        guard await ModalUI.run(panel) == .OK, let url = panel.url else { return }
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
        ModalUI.alert(title, text)
    }
}

// MARK: - Alerts and panels

/// Alerts and open/save panels shown as sheets on the main window.
///
/// The listener alert used `NSAlert.runModal()` from inside a Swift `Task`
/// (after an `await`), which nests a modal run loop inside a suspended
/// main-actor job. On a real Mac it drew but would not dismiss, and while an
/// app-modal alert is up the app cannot quit. A sheet does not block anything:
/// it ends through its completion handler, and Cmd+Q keeps working.
@MainActor
enum ModalUI {
    static func alert(_ title: String, _ text: String) {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.addButton(withTitle: "OK")
        if let window = hostWindow() {
            a.beginSheetModal(for: window, completionHandler: nil)
        } else {
            // No window to attach to: run it on the next turn of the run loop,
            // outside any task.
            DispatchQueue.main.async { _ = a.runModal() }
        }
    }

    static func run(_ panel: NSSavePanel) async -> NSApplication.ModalResponse {
        guard let window = hostWindow() else {
            return await withCheckedContinuation { done in
                panel.begin { done.resume(returning: $0) }
            }
        }
        return await withCheckedContinuation { done in
            panel.beginSheetModal(for: window) { done.resume(returning: $0) }
        }
    }

    /// The main window, if it is up and has no sheet already.
    static func hostWindow() -> NSWindow? {
        let window = NSApp.windows.first { $0.isVisible && !($0 is NSPanel) && $0.title == AppInfo.name }
            ?? NSApp.mainWindow ?? NSApp.keyWindow
        guard let window, window.attachedSheet == nil else { return nil }
        return window
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
