import CoreGraphics
import Foundation
import ImageIO
@testable import CardboardCADKit
import XCTest

/// The app is called Fold Theory (it used to be CardboardCAD).
final class FoldTheoryNameTests: XCTestCase {
    let home = URL(fileURLWithPath: "/Users/tester", isDirectory: true)

    func testBrandNames() {
        XCTAssertEqual(AppBrand.name, "Fold Theory")
        XCTAssertEqual(AppBrand.legacyName, "CardboardCAD")
    }

    func testLibraryFoldersUseTheNewName() {
        let c = EngineConfig.resolve(
            bundleURL: URL(fileURLWithPath: "/Applications/Fold Theory.app", isDirectory: true),
            pythonHome: nil, port: 5001, environment: [:], allowDevRuntime: false, home: home
        )
        XCTAssertEqual(c.appName, "Fold Theory")
        XCTAssertEqual(c.logsDirectory.path, "/Users/tester/Library/Logs/Fold Theory")
        XCTAssertEqual(c.dataDirectory.path, "/Users/tester/Library/Application Support/Fold Theory")
        XCTAssertEqual(c.cacheDirectory.path, "/Users/tester/Library/Caches/Fold Theory")
        XCTAssertEqual(c.interpreterURL.path, "/Applications/Fold Theory.app/Contents/Frameworks/python/bin/python3.12")
    }

    func testDocumentsFoldersUseTheNewName() {
        XCTAssertEqual(AppBrand.documentsFolder(home: home).path, "/Users/tester/Documents/Fold Theory")
        XCTAssertEqual(ProjectController.defaultProjectsFolder(home: home).path,
                       "/Users/tester/Documents/Fold Theory/Projects")
        XCTAssertEqual(WorkflowController.defaultWorkflowsFolder(home: home).path,
                       "/Users/tester/Documents/Fold Theory/Workflows")
    }

    func testMissingKeyMessageNamesTheApp() {
        let message = AnthropicError.missingAPIKey.errorDescription ?? ""
        XCTAssertTrue(message.contains("Fold Theory"), message)
        XCTAssertFalse(message.contains("CardboardCAD"), message)
    }
}

/// Folders made by CardboardCAD move to their Fold Theory names on first launch.
final class LegacyDataMigrationTests: XCTestCase {
    var home: URL!
    let fm = FileManager.default

    override func setUpWithError() throws {
        home = fm.temporaryDirectory.appendingPathComponent("ft-migration-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: home, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? fm.removeItem(at: home)
    }

    private func write(_ rel: String, _ text: String = "x") throws {
        let url = home.appendingPathComponent(rel)
        try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(text.utf8).write(to: url)
    }

    private func read(_ rel: String) -> String? {
        (try? Data(contentsOf: home.appendingPathComponent(rel))).map { String(decoding: $0, as: UTF8.self) }
    }

    private func isSymlink(_ rel: String) -> Bool {
        let attrs = try? fm.attributesOfItem(atPath: home.appendingPathComponent(rel).path)
        return attrs?[.type] as? FileAttributeType == .typeSymbolicLink
    }

    func testPlanCoversLibraryAndDocuments() {
        let moves = LegacyDataMigration.moves(home: URL(fileURLWithPath: "/Users/t", isDirectory: true))
        XCTAssertEqual(moves.map { $0.from.path }, [
            "/Users/t/Library/Logs/CardboardCAD",
            "/Users/t/Library/Application Support/CardboardCAD",
            "/Users/t/Library/Caches/CardboardCAD",
            "/Users/t/Documents/CardboardCAD",
        ])
        XCTAssertEqual(moves.map { $0.to.path }, [
            "/Users/t/Library/Logs/Fold Theory",
            "/Users/t/Library/Application Support/Fold Theory",
            "/Users/t/Library/Caches/Fold Theory",
            "/Users/t/Documents/Fold Theory",
        ])
        // Only Documents keeps a link at the old path, for saved paths in Rhino files and Grasshopper inputs.
        XCTAssertEqual(moves.map(\.linkBack), [false, false, false, true])
    }

    func testMovesOldFoldersAndLinksDocumentsBack() throws {
        try write("Documents/CardboardCAD/Projects/Stool/project.json", "stool")
        try write("Documents/CardboardCAD/Workflows/Standard.json", "wf")
        try write("Library/Logs/CardboardCAD/engine.log", "log")
        try write("Library/Application Support/CardboardCAD/state.json", "state")

        let report = LegacyDataMigration.run(home: home)

        XCTAssertEqual(read("Documents/Fold Theory/Projects/Stool/project.json"), "stool")
        XCTAssertEqual(read("Documents/Fold Theory/Workflows/Standard.json"), "wf")
        XCTAssertEqual(read("Library/Logs/Fold Theory/engine.log"), "log")
        XCTAssertEqual(read("Library/Application Support/Fold Theory/state.json"), "state")
        XCTAssertFalse(fm.fileExists(atPath: home.appendingPathComponent("Library/Logs/CardboardCAD").path))
        // The old Documents path still opens the same files.
        XCTAssertTrue(isSymlink("Documents/CardboardCAD"))
        XCTAssertEqual(read("Documents/CardboardCAD/Projects/Stool/project.json"), "stool")
        XCTAssertEqual(try fm.destinationOfSymbolicLink(atPath: home.appendingPathComponent("Documents/CardboardCAD").path),
                       "Fold Theory")
        XCTAssertEqual(report.filter { $0.hasPrefix("Moved") }.count, 3)  // Logs, Application Support, Documents
    }

    func testRunningTwiceChangesNothing() throws {
        try write("Documents/CardboardCAD/Projects/Stool/project.json", "stool")
        _ = LegacyDataMigration.run(home: home)
        let second = LegacyDataMigration.run(home: home)
        XCTAssertTrue(second.isEmpty, "\(second)")
        XCTAssertEqual(read("Documents/Fold Theory/Projects/Stool/project.json"), "stool")
    }

    func testNothingToDoOnAFreshMac() {
        XCTAssertTrue(LegacyDataMigration.run(home: home).isEmpty)
        XCTAssertFalse(fm.fileExists(atPath: home.appendingPathComponent("Documents/Fold Theory").path))
    }

    func testKeepsBothWhenTheNewFolderAlreadyExists() throws {
        try write("Documents/CardboardCAD/old.txt", "old")
        try write("Documents/Fold Theory/new.txt", "new")
        let report = LegacyDataMigration.run(home: home)
        XCTAssertEqual(read("Documents/CardboardCAD/old.txt"), "old")
        XCTAssertEqual(read("Documents/Fold Theory/new.txt"), "new")
        XCTAssertFalse(isSymlink("Documents/CardboardCAD"))
        XCTAssertTrue(report.contains { $0.contains("both") }, "\(report)")
    }
}

/// The macOS app icon: transparent background, mark drawn to the edge.
final class AppIconTests: XCTestCase {
    static let iconSet = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()   // CardboardCADKitTests
        .deletingLastPathComponent()   // Tests
        .deletingLastPathComponent()   // CardboardCADKit
        .deletingLastPathComponent()   // app
        .appendingPathComponent("CardboardCAD/Assets.xcassets/AppIcon.appiconset", isDirectory: true)

    /// Alpha channel of a PNG, row-major from the top.
    private func alpha(_ name: String) throws -> (width: Int, height: Int, a: [UInt8]) {
        let url = Self.iconSet.appendingPathComponent(name)
        let src = try XCTUnwrap(CGImageSourceCreateWithURL(url as CFURL, nil), name)
        let image = try XCTUnwrap(CGImageSourceCreateImageAtIndex(src, 0, nil), name)
        let w = image.width, h = image.height
        var px = [UInt8](repeating: 0, count: w * h * 4)
        let ctx = try XCTUnwrap(CGContext(
            data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
        return (w, h, stride(from: 3, to: px.count, by: 4).map { px[$0] })
    }

    func testEveryIconSizeIsTransparentWithTheMarkFullSize() throws {
        let sizes: [(String, Int)] = [
            ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32), ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
            ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256), ("icon_256x256.png", 256),
            ("icon_256x256@2x.png", 512), ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
        ]
        for (name, size) in sizes {
            let (w, h, a) = try alpha(name)
            XCTAssertEqual(w, size, name)
            XCTAssertEqual(h, size, name)
            // Transparent background: the four corners and the centre of the triangle's hole.
            for (x, y) in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)] {
                XCTAssertEqual(a[y * w + x], 0, "\(name) corner \(x),\(y)")
            }
            // Full size: opaque mark pixels reach (within 2%) both side edges.
            let margin = max(1, size * 2 / 100)
            var minX = w, maxX = -1
            for y in 0..<h { for x in 0..<w where a[y * w + x] > 128 { minX = min(minX, x); maxX = max(maxX, x) } }
            XCTAssertLessThanOrEqual(minX, margin, "\(name) left edge")
            XCTAssertGreaterThanOrEqual(maxX, w - 1 - margin, "\(name) right edge")
        }
    }
}
