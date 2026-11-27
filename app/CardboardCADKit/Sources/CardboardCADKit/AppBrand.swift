import Foundation

/// The product name. The app was called CardboardCAD; Swift module, target and
/// bundle identifier keep that name so settings and the Keychain item carry
/// over. Everything a designer sees says Fold Theory.
public enum AppBrand {
    public static let name = "Fold Theory"
    public static let legacyName = "CardboardCAD"

    /// ~/Documents/Fold Theory: exports, Projects/ and Workflows/.
    public static func documentsFolder(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        home.appendingPathComponent("Documents", isDirectory: true)
            .appendingPathComponent(name, isDirectory: true)
    }
}

/// Moves the folders CardboardCAD made to their Fold Theory names, once, before
/// the engine starts. ~/Documents/CardboardCAD is left as a link to
/// ~/Documents/Fold Theory so paths saved in Rhino files, Grasshopper inputs
/// and recent-project lists still open. If both folders exist, both are kept.
public enum LegacyDataMigration {
    public struct Move: Equatable, Sendable {
        public let from: URL
        public let to: URL
        /// Leave a symbolic link at `from` pointing to `to`.
        public let linkBack: Bool
    }

    public static func moves(
        home: URL, from legacy: String = AppBrand.legacyName, to name: String = AppBrand.name
    ) -> [Move] {
        let library = home.appendingPathComponent("Library", isDirectory: true)
        let documents = home.appendingPathComponent("Documents", isDirectory: true)
        let libraryMoves = ["Logs", "Application Support", "Caches"].map { sub -> Move in
            let parent = library.appendingPathComponent(sub, isDirectory: true)
            return Move(from: parent.appendingPathComponent(legacy, isDirectory: true),
                        to: parent.appendingPathComponent(name, isDirectory: true), linkBack: false)
        }
        return libraryMoves + [
            Move(from: documents.appendingPathComponent(legacy, isDirectory: true),
                 to: documents.appendingPathComponent(name, isDirectory: true), linkBack: true),
        ]
    }

    /// Returns one line per thing done (or kept); empty when there was nothing to do.
    @discardableResult
    public static func run(
        home: URL = FileManager.default.homeDirectoryForCurrentUser, fileManager fm: FileManager = .default
    ) -> [String] {
        var report: [String] = []
        for move in moves(home: home) {
            // attributesOfItem does not follow a final symlink: a link left by an earlier run is skipped.
            guard let attrs = try? fm.attributesOfItem(atPath: move.from.path),
                  attrs[.type] as? FileAttributeType == .typeDirectory else { continue }
            if (try? fm.attributesOfItem(atPath: move.to.path)) != nil {
                report.append("Kept both \(move.from.path) and \(move.to.path); move files across by hand if needed")
                continue
            }
            do {
                try fm.createDirectory(at: move.to.deletingLastPathComponent(), withIntermediateDirectories: true)
                try fm.moveItem(at: move.from, to: move.to)
                report.append("Moved \(move.from.path) to \(move.to.path)")
                if move.linkBack {
                    // Relative, so it survives the home folder being moved or restored.
                    try fm.createSymbolicLink(atPath: move.from.path, withDestinationPath: move.to.lastPathComponent)
                }
            } catch {
                report.append("Could not move \(move.from.path) to \(move.to.path): \(error.localizedDescription)")
            }
        }
        return report
    }
}
