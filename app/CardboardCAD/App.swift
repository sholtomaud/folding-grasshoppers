import AppKit
import CardboardCADKit
import Observation
import SwiftUI

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

    func applicationDidFinishLaunching(_ notification: Notification) {
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
            ContentView(engine: delegate.engine, assistant: delegate.assistant, claudeKey: delegate.claudeKey)
                .frame(minWidth: 900, minHeight: 600)
        }
        .defaultSize(width: 1280, height: 800)
        .commands {
            DesignCommands(assistant: delegate.assistant)
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
            SettingsView(engine: delegate.engine, assistant: delegate.assistant, claudeKey: delegate.claudeKey)
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
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        CommandMenu("Design") {
            Button("New Design") {
                Task { await assistant.newDesign() }
            }
            .keyboardShortcut("n", modifiers: [.command, .shift])
            Divider()
            Button("Manual Tools") {
                openWindow(id: ManualToolsView.windowID)
            }
            .keyboardShortcut("t", modifiers: [.command, .shift])
        }
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
