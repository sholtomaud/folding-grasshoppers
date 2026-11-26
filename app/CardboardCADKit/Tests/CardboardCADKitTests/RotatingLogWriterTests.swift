import XCTest
@testable import CardboardCADKit

final class RotatingLogWriterTests: XCTestCase {
    var dir: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("logtest-\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: dir)
    }

    func read(_ name: String) -> String? {
        try? String(contentsOf: dir.appendingPathComponent(name), encoding: .utf8)
    }

    func testCreatesDirectoryAndAppendsLines() {
        let w = RotatingLogWriter(directory: dir, fileName: "engine.log", maxBytes: 1_000, keep: 3)
        w.append("hello")
        w.append("world")
        XCTAssertEqual(read("engine.log"), "hello\nworld\n")
    }

    func testRotatesAtMaxBytesKeepingThreeFiles() {
        let w = RotatingLogWriter(directory: dir, fileName: "engine.log", maxBytes: 10, keep: 3)
        for line in ["aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd", "eeeeeeee"] {
            w.append(line)
        }
        XCTAssertEqual(read("engine.log"), "eeeeeeee\n")
        XCTAssertEqual(read("engine.log.1"), "dddddddd\n")
        XCTAssertEqual(read("engine.log.2"), "cccccccc\n")
        XCTAssertNil(read("engine.log.3"), "only 3 files are kept")
    }

    func testDefaultsAreFiveMegabytesAndThreeFiles() {
        XCTAssertEqual(RotatingLogWriter.defaultMaxBytes, 5 * 1024 * 1024)
        XCTAssertEqual(RotatingLogWriter.defaultKeep, 3)
    }

    func testContinuesExistingFile() {
        RotatingLogWriter(directory: dir, fileName: "engine.log", maxBytes: 1_000, keep: 3).append("one")
        RotatingLogWriter(directory: dir, fileName: "engine.log", maxBytes: 1_000, keep: 3).append("two")
        XCTAssertEqual(read("engine.log"), "one\ntwo\n")
    }
}
