import XCTest
@testable import CardboardCADKit

final class EngineConfigTests: XCTestCase {
    let bundleURL = URL(fileURLWithPath: "/Applications/CardboardCAD.app", isDirectory: true)
    let home = URL(fileURLWithPath: "/Users/tester", isDirectory: true)

    func testBundledInterpreterLivesInFrameworks() {
        let url = EngineConfig.bundledInterpreterURL(bundleURL: bundleURL)
        XCTAssertEqual(url.path, "/Applications/CardboardCAD.app/Contents/Frameworks/python/bin/python3.12")
    }

    func testResolveUsesBundleAndStandardDirectories() {
        let c = EngineConfig.resolve(
            bundleURL: bundleURL, port: 5001, environment: [:], allowDevRuntime: true, home: home
        )
        XCTAssertEqual(c.interpreterURL.path, "/Applications/CardboardCAD.app/Contents/Frameworks/python/bin/python3.12")
        XCTAssertEqual(c.logsDirectory.path, "/Users/tester/Library/Logs/CardboardCAD")
        XCTAssertEqual(c.dataDirectory.path, "/Users/tester/Library/Application Support/CardboardCAD")
        XCTAssertEqual(c.cacheDirectory.path, "/Users/tester/Library/Caches/CardboardCAD")
        XCTAssertEqual(c.homeDirectory.path, "/Users/tester")
        XCTAssertFalse(c.usesDevRuntime)
    }

    func testDevRuntimeOverrideOnlyWhenAllowed() {
        let env = ["CARDBOARD_DEV_RUNTIME": "/src/folding-grasshoppers/build/python-runtime"]
        let dev = EngineConfig.resolve(bundleURL: bundleURL, port: 5001, environment: env, allowDevRuntime: true, home: home)
        XCTAssertEqual(dev.interpreterURL.path, "/src/folding-grasshoppers/build/python-runtime/bin/python3.12")
        XCTAssertTrue(dev.usesDevRuntime)

        let release = EngineConfig.resolve(bundleURL: bundleURL, port: 5001, environment: env, allowDevRuntime: false, home: home)
        XCTAssertEqual(release.interpreterURL.path, "/Applications/CardboardCAD.app/Contents/Frameworks/python/bin/python3.12")
        XCTAssertFalse(release.usesDevRuntime)
    }

    func testEnvironmentIsNotInheritedFromShell() {
        let c = testConfig()
        let env = c.processEnvironment(token: "t")
        XCTAssertNil(env["PYTHONPATH"])
        XCTAssertNil(env["VIRTUAL_ENV"])
        XCTAssertEqual(env.count, 8)
    }

    func testHopsURLs() {
        let c = testConfig(port: 5050)
        XCTAssertEqual(c.baseURL.absoluteString, "http://127.0.0.1:5050")
        XCTAssertEqual(c.hopsURL(for: "cut_estimate").absoluteString, "http://127.0.0.1:5050/cut_estimate")
        XCTAssertEqual(EngineConfig.hopsEndpoints, ["cut_estimate", "echo"])
    }

    func testPortValidation() {
        XCTAssertTrue(EngineConfig.isValidPort(5001))
        XCTAssertTrue(EngineConfig.isValidPort(1024))
        XCTAssertTrue(EngineConfig.isValidPort(65535))
        XCTAssertFalse(EngineConfig.isValidPort(0))
        XCTAssertFalse(EngineConfig.isValidPort(80), "privileged ports are rejected")
        XCTAssertFalse(EngineConfig.isValidPort(70000))
        XCTAssertEqual(EngineConfig.defaultPort, 5001)
    }
}
