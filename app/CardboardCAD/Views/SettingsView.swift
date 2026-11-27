import AppKit
import CardboardCADKit
import SwiftUI

struct SettingsView: View {
    let engine: EngineManager
    @Bindable var assistant: DesignAssistant
    let claudeKey: ClaudeKey
    let workflows: WorkflowController
    let projects: ProjectController
    @State private var portText = ""
    @State private var copied: String?
    @State private var keyText = ""

    var body: some View {
        Form {
            Section {
                if let masked = claudeKey.masked {
                    LabeledContent("API key") {
                        HStack {
                            Text(masked).font(.system(.body, design: .monospaced))
                            Text("saved in Keychain").foregroundStyle(.secondary)
                            Button("Remove") { claudeKey.remove() }
                        }
                    }
                }
                HStack {
                    SecureField(claudeKey.isSet ? "Replace the key" : "Paste your Claude API key (sk-ant-…)", text: $keyText)
                        .onSubmit(saveKey)
                    Button("Save", action: saveKey)
                        .disabled(keyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                if !keyText.isEmpty && !APIKey.looksValid(keyText) {
                    Text("Claude API keys start with sk-ant-.").font(.caption).foregroundStyle(.orange)
                }
                if let error = claudeKey.error {
                    Text(error).font(.caption).foregroundStyle(.red)
                }
                Picker("Model", selection: $assistant.modelID) {
                    ForEach(AssistantModel.all) { m in
                        Text("\(m.name) · \(m.note)").tag(m.id)
                    }
                }
                Picker("Thinking effort", selection: $assistant.effort) {
                    ForEach(AssistantModel.efforts, id: \.self) { e in
                        Text(e.capitalized).tag(e)
                    }
                }
                Link("Get an API key at console.anthropic.com",
                     destination: URL(string: "https://console.anthropic.com/settings/keys")!)
                    .font(.caption)
            } header: {
                Text("Design Assistant (Claude)")
            } footer: {
                Text("Each message is billed to this key by Anthropic. The key never leaves this Mac except to api.anthropic.com.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            WorkflowSettingsSection(workflows: workflows)
            ProjectLocationsSection(projects: projects)
            Section("Engine") {
                HStack {
                    TextField("Port", text: $portText)
                        .frame(width: 90)
                        .onSubmit(applyPort)
                    Button("Apply", action: applyPort)
                        .disabled(parsedPort == nil)
                }
                if parsedPort == nil {
                    Text("Use a port between 1024 and 65535.").font(.caption).foregroundStyle(.red)
                }
                Text(engine.state.summary).font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Restart Engine") { Task { await engine.restart() } }
                    Button("Open Logs Folder") { LogsFolder.reveal(engine) }
                }
            }
            Section("Grasshopper Hops") {
                ForEach(EngineConfig.hopsEndpoints, id: \.self) { endpoint in
                    let url = engine.config.hopsURL(for: endpoint).absoluteString
                    HStack {
                        Text(url).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                        Spacer()
                        Button(copied == endpoint ? "Copied" : "Copy Hops URL") {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(url, forType: .string)
                            copied = endpoint
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 520)
        .onAppear {
            portText = String(engine.config.port)
            claudeKey.refresh()
        }
        .onChange(of: assistant.modelID) {
            UserDefaults.standard.set(assistant.modelID, forKey: AppInfo.assistantModelKey)
        }
        .onChange(of: assistant.effort) {
            UserDefaults.standard.set(assistant.effort, forKey: AppInfo.assistantEffortKey)
        }
    }

    private func saveKey() {
        claudeKey.save(keyText)
        keyText = ""
    }

    private var parsedPort: Int? {
        guard let p = Int(portText.trimmingCharacters(in: .whitespaces)), EngineConfig.isValidPort(p) else { return nil }
        return p
    }

    private func applyPort() {
        guard let p = parsedPort else { return }
        UserDefaults.standard.set(p, forKey: AppInfo.portDefaultsKey)
        Task { await engine.updatePort(p) }
    }
}

// MARK: - Projects location (#49)

/// Where projects and the library are saved. Point it at a folder Google Drive,
/// OneDrive or iCloud Drive syncs and every cut file, quote and PDF reaches the cloud.
struct ProjectLocationsSection: View {
    let projects: ProjectController

    var body: some View {
        Section {
            row("Projects", path: projects.locations?.projects ?? projects.projectsFolder.path,
                isDefault: projects.locations?.isDefault.projects ?? true,
                choose: { Task { await choose(projectsFolder: true) } },
                reset: { Task { await projects.setLocations(projects: "") } })
            row("Library", path: projects.locations?.library ?? "",
                isDefault: projects.locations?.isDefault.library ?? true,
                choose: { Task { await choose(projectsFolder: false) } },
                reset: { Task { await projects.setLocations(library: "") } })
            if let error = projects.locationError {
                Text(error).font(.caption).foregroundStyle(.red)
            }
        } header: {
            Text("Locations")
        } footer: {
            Text("To keep projects in Google Drive, OneDrive or iCloud Drive, choose a folder inside the one it syncs, "
                 + "for example ~/Library/CloudStorage/GoogleDrive-…/My Drive/Fold Theory/Projects. "
                 + "This setting is kept on this Mac only.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .task { await projects.refreshLocations() }
    }

    private func row(_ title: String, path: String, isDefault: Bool,
                     choose: @escaping () -> Void, reset: @escaping () -> Void) -> some View {
        LabeledContent(title) {
            VStack(alignment: .trailing, spacing: 4) {
                Text(path.isEmpty ? "—" : path)
                    .font(.system(.caption, design: .monospaced))
                    .lineLimit(2)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
                HStack {
                    Button("Choose…", action: choose)
                    Button("Use Default", action: reset).disabled(isDefault)
                    Button("Show in Finder") {
                        let url = URL(fileURLWithPath: path, isDirectory: true)
                        if FileManager.default.fileExists(atPath: path) { NSWorkspace.shared.open(url) }
                    }
                    .disabled(path.isEmpty)
                }
            }
        }
    }

    @MainActor
    private func choose(projectsFolder: Bool) async {
        let panel = NSOpenPanel()
        panel.title = projectsFolder ? "Projects Location" : "Library Location"
        panel.message = "Choose the folder to save into. A folder inside Google Drive, OneDrive or iCloud Drive is synced."
        panel.prompt = "Choose"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        let current = projectsFolder ? projects.locations?.projects : projects.locations?.library
        if let current, FileManager.default.fileExists(atPath: current) {
            panel.directoryURL = URL(fileURLWithPath: current, isDirectory: true)
        }
        guard await ModalUI.run(panel) == .OK, let url = panel.url else { return }
        if projectsFolder {
            await projects.setLocations(projects: url.path)
        } else {
            await projects.setLocations(library: url.path)
        }
    }
}

/// Warn when the open project's files changed on disk (another Mac, a sync): checked each
/// time the app comes to the front, and before Save writes over them.
@MainActor
struct ProjectChangeAlert: ViewModifier {
    let projects: ProjectController

    func body(content: Content) -> some View {
        content
            .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
                Task { await projects.checkForOutsideChanges() }
            }
            .alert("Project changed on disk", isPresented: shown) {
                Button("Open Again") { Task { await projects.reopen() } }
                Button("Save Anyway") { Task { await projects.saveAgain(overwrite: true) } }
                Button("Cancel", role: .cancel) { projects.dismissOutsideChanges() }
            } message: {
                Text((projects.outsideChanges?.summary ?? "")
                     + "\n\nOpen Again loads the files as they are now. Save Anyway replaces them with this design.")
            }
    }

    private var shown: Binding<Bool> {
        Binding(get: { projects.outsideChanges != nil }, set: { if !$0 { projects.dismissOutsideChanges() } })
    }
}

extension View {
    func projectChangeAlert(_ projects: ProjectController) -> some View {
        modifier(ProjectChangeAlert(projects: projects))
    }
}
