import AppKit
import CardboardCADKit
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
            ContentView(engine: delegate.engine)
                .frame(minWidth: 720, minHeight: 520)
        }
        .commands {
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

        Settings {
            SettingsView(engine: delegate.engine)
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
