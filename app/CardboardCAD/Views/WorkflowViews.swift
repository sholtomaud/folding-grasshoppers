import AppKit
import CardboardCADKit
import SwiftUI

/// Bindings shared by the toolbar, the Workflow menu and Settings.
@MainActor
enum WorkflowBindings {
    static func applyDefaults(_ workflows: WorkflowController) -> Binding<Bool> {
        Binding(get: { workflows.applyDefaults },
                set: { on in Task { await workflows.setApplyDefaults(on) } })
    }

    static func active(_ workflows: WorkflowController) -> Binding<String> {
        Binding(get: { workflows.active },
                set: { name in Task { await workflows.choose(name) } })
    }

    static func revealFolder(_ workflows: WorkflowController) {
        let url = workflows.folder.map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? WorkflowController.defaultWorkflowsFolder()
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        NSWorkspace.shared.open(url)
    }

    static let applyHelp = "On: the assistant fills in the workflow's saved values (board size, gap, prices) and "
        + "carries on. Off: it asks you for them at each step."
}

/// Window toolbar: which workflow the assistant follows, and the Apply Defaults switch.
struct WorkflowToolbar: ToolbarContent {
    let workflows: WorkflowController

    var body: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            if workflows.isConnected && !workflows.names.isEmpty {
                Picker("Workflow", selection: WorkflowBindings.active(workflows)) {
                    ForEach(workflows.workflows) { w in
                        Text(w.name).tag(w.name)
                    }
                }
                .help("The steps the Design Assistant follows. Ask it to save a new one from the chat.")
                Toggle("Apply defaults", isOn: WorkflowBindings.applyDefaults(workflows))
                    .toggleStyle(.switch)
                    .help(WorkflowBindings.applyHelp)
            }
        }
    }
}

/// Design > Workflow submenu.
struct WorkflowMenu: View {
    let workflows: WorkflowController

    var body: some View {
        Menu("Workflow") {
            Toggle("Apply Workflow Defaults", isOn: WorkflowBindings.applyDefaults(workflows))
                .keyboardShortcut("d", modifiers: [.command, .shift])
                .disabled(!workflows.isConnected)
            Divider()
            Picker("Workflow", selection: WorkflowBindings.active(workflows)) {
                ForEach(workflows.workflows) { w in
                    Text(w.name).tag(w.name)
                }
            }
            .pickerStyle(.inline)
            .disabled(!workflows.isConnected)
            Divider()
            Button("Show Workflows Folder") { WorkflowBindings.revealFolder(workflows) }
            Button("Reload Workflows") { Task { await workflows.refresh() } }
                .disabled(!workflows.isConnected)
        }
    }
}

/// Settings > Workflow.
struct WorkflowSettingsSection: View {
    let workflows: WorkflowController

    var body: some View {
        Section {
            if workflows.isConnected {
                Picker("Active workflow", selection: WorkflowBindings.active(workflows)) {
                    ForEach(workflows.workflows) { w in
                        Text(w.builtin ? "\(w.name) (built in)" : w.name).tag(w.name)
                    }
                }
                if let current = workflows.workflows.first(where: { $0.name == workflows.active }),
                   !current.description.isEmpty {
                    Text(current.description).font(.caption).foregroundStyle(.secondary)
                }
                Toggle("Apply defaults", isOn: WorkflowBindings.applyDefaults(workflows))
            } else {
                Text("Start the engine to choose a workflow.").foregroundStyle(.secondary)
            }
            if let error = workflows.error {
                Text(error).font(.caption).foregroundStyle(.red)
            }
            HStack {
                Button("Show Workflows Folder") { WorkflowBindings.revealFolder(workflows) }
                Button("Reload") { Task { await workflows.refresh() } }
                    .disabled(!workflows.isConnected)
            }
        } header: {
            Text("Workflow")
        } footer: {
            Text(WorkflowBindings.applyHelp + " Ask the assistant to \"save this as a workflow\" to add your own; "
                 + "each is a JSON file in ~/Documents/CardboardCAD/Workflows.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .task { await workflows.refresh() }
    }
}
