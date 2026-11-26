import AppKit
import CardboardCADKit
import SwiftUI

struct SettingsView: View {
    let engine: EngineManager
    @State private var portText = ""
    @State private var copied: String?

    var body: some View {
        Form {
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
        .onAppear { portText = String(engine.config.port) }
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
