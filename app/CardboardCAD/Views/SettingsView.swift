import AppKit
import CardboardCADKit
import SwiftUI

struct SettingsView: View {
    let engine: EngineManager
    @Bindable var assistant: DesignAssistant
    let claudeKey: ClaudeKey
    let workflows: WorkflowController
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
