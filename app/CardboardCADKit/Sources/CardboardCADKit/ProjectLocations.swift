import Foundation

// Projects location (#49). Projects can live in a folder that Google Drive for
// desktop, OneDrive or iCloud Drive already syncs. The engine keeps the setting
// per Mac and notices when the open project's files change on disk.

/// Reply of GET/POST /api/locations.
public struct ProjectLocations: Codable, Equatable, Sendable {
    public struct Paths: Codable, Equatable, Sendable {
        public var projects: String
        public var library: String

        public init(projects: String, library: String) {
            self.projects = projects
            self.library = library
        }
    }

    public struct Flags: Codable, Equatable, Sendable {
        public var projects: Bool
        public var library: Bool

        public init(projects: Bool, library: Bool) {
            self.projects = projects
            self.library = library
        }
    }

    public var projects: String
    public var library: String
    public var defaults: Paths
    public var isDefault: Flags

    public init(projects: String, library: String, defaults: Paths, isDefault: Flags) {
        self.projects = projects
        self.library = library
        self.defaults = defaults
        self.isDefault = isDefault
    }

    enum CodingKeys: String, CodingKey {
        case projects, library, defaults
        case isDefault = "is_default"
    }
}

/// Reply of GET /api/design/project/status: what changed on disk since this Mac saved or opened the project.
public struct ProjectStatus: Codable, Equatable, Sendable {
    public var project: String?
    public var folder: String?
    public var changed: [String]
    public var missing: [String]
    /// Files iCloud Drive has not downloaded yet. Not a change on its own.
    public var syncing: [String]
    /// Sync-conflict copies such as `project (1).json`.
    public var conflicts: [String]
    public var outsideChanges: Bool

    public init(project: String?, folder: String?, changed: [String], missing: [String], syncing: [String],
                conflicts: [String], outsideChanges: Bool) {
        self.project = project
        self.folder = folder
        self.changed = changed
        self.missing = missing
        self.syncing = syncing
        self.conflicts = conflicts
        self.outsideChanges = outsideChanges
    }

    enum CodingKeys: String, CodingKey {
        case project, folder, changed, missing, syncing, conflicts
        case outsideChanges = "outside_changes"
    }

    /// For the alert: what changed, in plain words.
    public var summary: String {
        var lines = ["The files of \(project ?? "this project") changed on disk, perhaps on another Mac."]
        func list(_ files: [String]) -> String {
            files.prefix(6).joined(separator: ", ") + (files.count > 6 ? " and \(files.count - 6) more" : "")
        }
        if !changed.isEmpty { lines.append("Changed: \(list(changed)).") }
        if !missing.isEmpty { lines.append("Removed: \(list(missing)).") }
        if !conflicts.isEmpty {
            lines.append("Sync conflict copies: \(list(conflicts)). Your sync app kept both versions.")
        }
        if !syncing.isEmpty { lines.append("Still syncing: \(list(syncing)).") }
        if !outsideChanges { lines[0] = "Some files of \(project ?? "this project") are still syncing." }
        return lines.joined(separator: "\n")
    }
}

/// The engine routes for locations and the open project's status.
public protocol ProjectLocationStore: AnyObject {
    func projectLocations() async throws -> ProjectLocations
    /// nil leaves a location as it is; an empty string goes back to the default.
    func setProjectLocations(projects: String?, library: String?) async throws -> ProjectLocations
    func projectStatus() async throws -> ProjectStatus
    /// `overwrite` saves over files that changed on disk (otherwise the engine answers 409).
    func saveProject(name: String, folder: String, chat: JSONValue?, overwrite: Bool) async throws -> ProjectSaved
}

extension EngineClient: ProjectLocationStore {
    public func projectLocations() async throws -> ProjectLocations {
        try await getJSON("/api/locations")
    }

    public func setProjectLocations(projects: String?, library: String?) async throws -> ProjectLocations {
        var body: JSONValue = [:]
        if let projects { body["projects"] = .string(projects) }
        if let library { body["library"] = .string(library) }
        return try await postJSON("/api/locations", body: body, timeout: 10)
    }

    public func projectStatus() async throws -> ProjectStatus {
        try await getJSON("/api/design/project/status", timeout: 30)
    }

    public func saveProject(name: String, folder: String, chat: JSONValue?, overwrite: Bool) async throws -> ProjectSaved {
        guard overwrite else { return try await saveProject(name: name, folder: folder, chat: chat) }
        var body: JSONValue = ["name": .string(name), "folder": .string(folder), "overwrite": true]
        if let chat { body["chat"] = chat }
        return try await postJSON("/api/design/save", body: body)
    }
}

// MARK: - ProjectController

extension ProjectController {
    /// Where Save As and Open start: the chosen projects location, else ~/Documents/Fold Theory/Projects.
    public var projectsFolder: URL {
        if let path = locations?.projects, !path.isEmpty { return URL(fileURLWithPath: path, isDirectory: true) }
        return Self.defaultProjectsFolder()
    }

    var locationStore: ProjectLocationStore? { attachedStore as? ProjectLocationStore }

    /// Read the locations from the engine (on launch and when Settings opens).
    public func refreshLocations() async {
        guard let store = locationStore else { return }
        if let loc = try? await store.projectLocations() { locations = loc }
    }

    /// Change the projects and/or library location. An empty string goes back to the default.
    @discardableResult
    public func setLocations(projects: String? = nil, library: String? = nil) async -> Bool {
        guard let store = locationStore else {
            locationError = "The engine is not running, so the location cannot be changed."
            return false
        }
        do {
            locations = try await store.setProjectLocations(projects: projects, library: library)
            locationError = nil
            return true
        } catch {
            locationError = error.localizedDescription
            return false
        }
    }

    /// Ask the engine whether the open project's files changed on disk; sets `outsideChanges` if they did.
    public func checkForOutsideChanges() async {
        guard current != nil, let store = locationStore, let st = try? await store.projectStatus() else {
            outsideChanges = nil
            return
        }
        outsideChanges = st.outsideChanges ? st : nil
    }

    /// Open the current project again from disk, taking the other Mac's changes.
    @discardableResult
    public func reopen() async -> Bool {
        guard let folder = current?.folder else { return false }
        return await open(folder: folder)
    }

    /// Before Save into the current folder: true if files changed elsewhere (and `outsideChanges` is set).
    func blockedByOutsideChanges() async -> Bool {
        guard let store = locationStore, let st = try? await store.projectStatus() else { return false }
        let blocked = !(st.changed.isEmpty && st.missing.isEmpty)
        outsideChanges = blocked ? st : nil
        return blocked
    }
}
