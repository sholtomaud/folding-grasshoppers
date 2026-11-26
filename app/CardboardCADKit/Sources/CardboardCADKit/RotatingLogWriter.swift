import Foundation

public protocol LogWriting: AnyObject {
    func append(_ line: String)
}

/// Appends lines to `<dir>/<file>`, rotating to `.1`, `.2`, ... at `maxBytes`.
/// `keep` counts the live file too (keep 3 = engine.log, .1, .2).
public final class RotatingLogWriter: LogWriting, @unchecked Sendable {
    public static let defaultMaxBytes = 5 * 1024 * 1024
    public static let defaultKeep = 3

    public let directory: URL
    public let fileURL: URL
    private let fileName: String
    private let maxBytes: Int
    private let keep: Int
    private let lock = NSLock()
    private let fm = FileManager.default

    public init(directory: URL, fileName: String = "engine.log",
                maxBytes: Int = RotatingLogWriter.defaultMaxBytes,
                keep: Int = RotatingLogWriter.defaultKeep) {
        self.directory = directory
        self.fileName = fileName
        self.fileURL = directory.appendingPathComponent(fileName)
        self.maxBytes = maxBytes
        self.keep = max(1, keep)
    }

    public func append(_ line: String) {
        let data = Data((line + "\n").utf8)
        lock.lock()
        defer { lock.unlock() }
        do {
            try fm.createDirectory(at: directory, withIntermediateDirectories: true)
            let size = (try? fm.attributesOfItem(atPath: fileURL.path)[.size] as? Int) ?? 0
            if size > 0, size + data.count > maxBytes {
                rotate()
            }
            if !fm.fileExists(atPath: fileURL.path) {
                fm.createFile(atPath: fileURL.path, contents: nil)
            }
            let handle = try FileHandle(forWritingTo: fileURL)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: data)
        } catch {
            // Logging must never take the app down.
        }
    }

    private func rotated(_ n: Int) -> URL {
        directory.appendingPathComponent("\(fileName).\(n)")
    }

    private func rotate() {
        // Drop the oldest, shift the rest up by one.
        try? fm.removeItem(at: rotated(keep - 1))
        if keep > 1 {
            for n in stride(from: keep - 2, through: 1, by: -1) where fm.fileExists(atPath: rotated(n).path) {
                try? fm.moveItem(at: rotated(n), to: rotated(n + 1))
            }
            try? fm.moveItem(at: fileURL, to: rotated(1))
        } else {
            try? fm.removeItem(at: fileURL)
        }
    }
}
