import XCTest
@testable import CardboardCADKit

// Projects location (#49): a Settings choice of where projects (and the library)
// are saved, e.g. a Google Drive / OneDrive / iCloud folder, and a warning when
// the open project's files changed on disk (another Mac).

private let locationsJSON = """
{"projects":"/Users/t/Library/CloudStorage/GoogleDrive-t@example.com/My Drive/FT/Projects",
 "library":"/Users/t/Documents/Fold Theory/Library",
 "defaults":{"projects":"/Users/t/Documents/Fold Theory/Projects","library":"/Users/t/Documents/Fold Theory/Library"},
 "is_default":{"projects":false,"library":true}}
"""

private func status(changed: [String] = [], missing: [String] = [], syncing: [String] = [],
                    conflicts: [String] = []) -> ProjectStatus {
    ProjectStatus(project: "Stool", folder: "/p/Stool", changed: changed, missing: missing, syncing: syncing,
                  conflicts: conflicts,
                  outsideChanges: !(changed.isEmpty && missing.isEmpty && conflicts.isEmpty))
}

final class FakeLocationStore: DesignProjectStore, ProjectLocationStore {
    var locations = ProjectLocations(
        projects: "/Users/t/Documents/Fold Theory/Projects", library: "/Users/t/Documents/Fold Theory/Library",
        defaults: .init(projects: "/Users/t/Documents/Fold Theory/Projects",
                        library: "/Users/t/Documents/Fold Theory/Library"),
        isDefault: .init(projects: true, library: true))
    var setError: Error?
    var statusResult = status()
    private(set) var saves: [(name: String, folder: String, overwrite: Bool)] = []
    private(set) var opens: [String] = []
    private(set) var locationChanges: [(projects: String?, library: String?)] = []

    func saveProject(name: String, folder: String, chat: JSONValue?) async throws -> ProjectSaved {
        try await saveProject(name: name, folder: folder, chat: chat, overwrite: false)
    }

    func saveProject(name: String, folder: String, chat: JSONValue?, overwrite: Bool) async throws -> ProjectSaved {
        saves.append((name, folder, overwrite))
        statusResult = status()
        return ProjectSaved(name: name, folder: folder.isEmpty ? "/p/\(name)" : folder, files: [], version: 2)
    }

    func openProject(folder: String) async throws -> ProjectOpened {
        opens.append(folder)
        statusResult = status()
        return ProjectOpened(name: "Stool", folder: folder, chat: nil, version: 3)
    }

    func projectLocations() async throws -> ProjectLocations { locations }

    func setProjectLocations(projects: String?, library: String?) async throws -> ProjectLocations {
        locationChanges.append((projects, library))
        if let setError { throw setError }
        if let projects {
            locations.projects = projects.isEmpty ? locations.defaults.projects : projects
            locations.isDefault.projects = projects.isEmpty
        }
        return locations
    }

    func projectStatus() async throws -> ProjectStatus { statusResult }
}

// MARK: - EngineClient routes

final class ProjectLocationsClientTests: XCTestCase {
    var client: EngineClient!

    override func setUp() {
        StubURLProtocol.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        client = EngineClient(port: 5001, session: URLSession(configuration: config))
    }

    func testGetLocationsDecodes() async throws {
        StubURLProtocol.stub("GET", "/api/locations", json: locationsJSON)
        let loc = try await client.projectLocations()
        XCTAssertEqual(loc.projects, "/Users/t/Library/CloudStorage/GoogleDrive-t@example.com/My Drive/FT/Projects")
        XCTAssertFalse(loc.isDefault.projects)
        XCTAssertTrue(loc.isDefault.library)
        XCTAssertEqual(loc.defaults.projects, "/Users/t/Documents/Fold Theory/Projects")
    }

    func testSetLocationsSendsOnlyWhatChanged() async throws {
        StubURLProtocol.stub("POST", "/api/locations", json: locationsJSON)
        _ = try await client.setProjectLocations(projects: "/Users/t/Drive/P", library: nil)
        let body = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(StubURLProtocol.requests.last?.httpBody))
        XCTAssertEqual(body, ["projects": "/Users/t/Drive/P"])
    }

    func testSetLocationsErrorCarriesTheEnginesMessage() async {
        StubURLProtocol.stub("POST", "/api/locations", status: 422,
                             json: #"{"error":"invalid_input","message":"must be inside your home folder"}"#)
        do {
            _ = try await client.setProjectLocations(projects: "/Volumes/x", library: nil)
            XCTFail("expected an error")
        } catch {
            XCTAssertEqual(error as? EngineClientError, .invalidInput("must be inside your home folder"))
        }
    }

    func testProjectStatusDecodes() async throws {
        StubURLProtocol.stub("GET", "/api/design/project/status", json: """
        {"project":"Stool","folder":"/p/Stool","changed":["model/model.obj"],"missing":[],
         "syncing":["parts/parts.json"],"conflicts":["project (1).json"],"outside_changes":true}
        """)
        let st = try await client.projectStatus()
        XCTAssertEqual(st, ProjectStatus(project: "Stool", folder: "/p/Stool", changed: ["model/model.obj"],
                                         missing: [], syncing: ["parts/parts.json"],
                                         conflicts: ["project (1).json"], outsideChanges: true))
    }

    func testOverwriteIsSentOnlyWhenAsked() async throws {
        StubURLProtocol.stub("POST", "/api/design/save", json: #"{"name":"S","folder":"/p/S","files":[],"version":1}"#)
        _ = try await client.saveProject(name: "S", folder: "/p/S", chat: nil, overwrite: true)
        var body = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(StubURLProtocol.requests.last?.httpBody))
        XCTAssertEqual(body, ["name": "S", "folder": "/p/S", "overwrite": true])
        _ = try await client.saveProject(name: "S", folder: "/p/S", chat: nil, overwrite: false)
        body = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(StubURLProtocol.requests.last?.httpBody))
        XCTAssertEqual(body, ["name": "S", "folder": "/p/S"])
    }
}

// MARK: - Status text

final class ProjectStatusTextTests: XCTestCase {
    func testSummaryNamesWhatChanged() {
        let st = status(changed: ["model/model.obj"], missing: ["preview/model.svg"], conflicts: ["project (1).json"])
        XCTAssertTrue(st.summary.contains("model/model.obj"))
        XCTAssertTrue(st.summary.contains("preview/model.svg"))
        XCTAssertTrue(st.summary.contains("project (1).json"))
        XCTAssertTrue(st.summary.contains("Stool"))
    }

    func testSyncingAloneIsNotAnOutsideChange() {
        XCTAssertFalse(status(syncing: ["parts/parts.json"]).outsideChanges)
        XCTAssertTrue(status(syncing: ["parts/parts.json"]).summary.contains("still syncing"))
    }
}

// MARK: - ProjectController

@MainActor
final class ProjectLocationControllerTests: XCTestCase {
    func testProjectsFolderFollowsTheSetting() async {
        let store = FakeLocationStore()
        let projects = ProjectController(assistant: DesignAssistant(model: ScriptedModel([])))
        let home = FileManager.default.homeDirectoryForCurrentUser
        XCTAssertEqual(projects.projectsFolder, ProjectController.defaultProjectsFolder(home: home))
        projects.attach(store: store)
        await projects.refreshLocations()
        XCTAssertEqual(projects.projectsFolder.path, "/Users/t/Documents/Fold Theory/Projects")
        let ok = await projects.setLocations(projects: "/Users/t/Drive/P")
        XCTAssertTrue(ok)
        XCTAssertEqual(projects.projectsFolder.path, "/Users/t/Drive/P")
        XCTAssertEqual(projects.locations?.isDefault.projects, false)
    }

    func testABadLocationShowsTheEnginesMessage() async {
        let store = FakeLocationStore()
        store.setError = EngineClientError.invalidInput("must be inside your home folder")
        let projects = ProjectController(assistant: DesignAssistant(model: ScriptedModel([])))
        projects.attach(store: store)
        let ok = await projects.setLocations(projects: "/Volumes/x")
        XCTAssertFalse(ok)
        XCTAssertEqual(projects.locationError, "must be inside your home folder")
    }

    func testSaveAgainStopsWhenFilesChangedElsewhere() async {
        let store = FakeLocationStore()
        let projects = ProjectController(assistant: DesignAssistant(model: ScriptedModel([])))
        projects.attach(store: store)
        _ = await projects.save(name: "Stool")
        store.statusResult = status(changed: ["model/model.obj"])
        let ok = await projects.saveAgain()
        XCTAssertFalse(ok)
        XCTAssertEqual(store.saves.count, 1, "nothing was written")
        XCTAssertEqual(projects.outsideChanges?.changed, ["model/model.obj"])

        let forced = await projects.saveAgain(overwrite: true)
        XCTAssertTrue(forced)
        XCTAssertEqual(store.saves.last?.overwrite, true)
        XCTAssertNil(projects.outsideChanges)
    }

    func testCheckForOutsideChangesAndReopen() async {
        let store = FakeLocationStore()
        let projects = ProjectController(assistant: DesignAssistant(model: ScriptedModel([])))
        projects.attach(store: store)
        await projects.checkForOutsideChanges()
        XCTAssertNil(projects.outsideChanges, "no project open")
        _ = await projects.open(folder: "/p/Stool")
        store.statusResult = status(conflicts: ["project (1).json"])
        await projects.checkForOutsideChanges()
        XCTAssertEqual(projects.outsideChanges?.conflicts, ["project (1).json"])
        let ok = await projects.reopen()
        XCTAssertTrue(ok)
        XCTAssertEqual(store.opens, ["/p/Stool", "/p/Stool"])
        XCTAssertNil(projects.outsideChanges)
    }

    func testAStoreWithoutLocationsStillSaves() async {
        let store = FakeProjectStore()
        let projects = ProjectController(assistant: DesignAssistant(model: ScriptedModel([])))
        projects.attach(store: store)
        _ = await projects.save(name: "Stool")
        let ok = await projects.saveAgain()
        XCTAssertTrue(ok)
        await projects.checkForOutsideChanges()
        XCTAssertNil(projects.outsideChanges)
    }
}
