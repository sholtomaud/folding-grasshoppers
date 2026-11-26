import Foundation

/// Where the engine lives, which port it uses and where it may write.
public struct EngineConfig: Equatable, Sendable {
    public static let defaultPort = 5001
    /// Hops component routes, for the "Copy Hops URL" buttons.
    public static let hopsEndpoints = ["hello", "cut_estimate", "echo"]
    /// Env var that points a Debug build at `build/python-runtime`.
    public static let devRuntimeVariable = "CARDBOARD_DEV_RUNTIME"
    public static let pythonExecutableName = "python3.12"
    /// Info.plist key naming the runtime's location inside the bundle. Set by
    /// scripts/build_app.sh so the layout can change without code changes.
    public static let pythonHomeInfoKey = "CardboardPythonHome"
    public static let defaultPythonHome = "Contents/Frameworks/python"

    public var appName: String
    public var port: Int
    public var interpreterURL: URL
    public var logsDirectory: URL
    public var dataDirectory: URL
    public var cacheDirectory: URL
    public var homeDirectory: URL
    public var temporaryDirectory: URL
    public var usesDevRuntime: Bool

    public init(
        appName: String,
        port: Int,
        interpreterURL: URL,
        logsDirectory: URL,
        dataDirectory: URL,
        cacheDirectory: URL,
        homeDirectory: URL,
        temporaryDirectory: URL,
        usesDevRuntime: Bool = false
    ) {
        self.appName = appName
        self.port = port
        self.interpreterURL = interpreterURL
        self.logsDirectory = logsDirectory
        self.dataDirectory = dataDirectory
        self.cacheDirectory = cacheDirectory
        self.homeDirectory = homeDirectory
        self.temporaryDirectory = temporaryDirectory
        self.usesDevRuntime = usesDevRuntime
    }

    /// `<App>.app/<pythonHome>/bin/python3.12`, default `Contents/Frameworks/python`.
    public static func bundledInterpreterURL(bundleURL: URL, pythonHome: String? = nil) -> URL {
        var home = pythonHome ?? defaultPythonHome
        if home.isEmpty || home.hasPrefix("/") || home.contains("..") { home = defaultPythonHome }
        return bundleURL
            .appendingPathComponent(home, isDirectory: true)
            .appendingPathComponent("bin", isDirectory: true)
            .appendingPathComponent(pythonExecutableName)
    }

    /// Resolve paths from the running bundle (never a hard-coded /Applications path).
    public static func resolve(
        appName: String = "CardboardCAD",
        bundleURL: URL = Bundle.main.bundleURL,
        pythonHome: String? = Bundle.main.object(forInfoDictionaryKey: EngineConfig.pythonHomeInfoKey) as? String,
        port: Int,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        allowDevRuntime: Bool,
        home: URL = FileManager.default.homeDirectoryForCurrentUser,
        temporaryDirectory: URL = FileManager.default.temporaryDirectory
    ) -> EngineConfig {
        var interpreter = bundledInterpreterURL(bundleURL: bundleURL, pythonHome: pythonHome)
        var dev = false
        if allowDevRuntime, let devRoot = environment[devRuntimeVariable], !devRoot.isEmpty {
            interpreter = URL(fileURLWithPath: devRoot, isDirectory: true)
                .appendingPathComponent("bin", isDirectory: true)
                .appendingPathComponent(pythonExecutableName)
            dev = true
        }
        let library = home.appendingPathComponent("Library", isDirectory: true)
        return EngineConfig(
            appName: appName,
            port: port,
            interpreterURL: interpreter,
            logsDirectory: library.appendingPathComponent("Logs/\(appName)", isDirectory: true),
            dataDirectory: library.appendingPathComponent("Application Support/\(appName)", isDirectory: true),
            cacheDirectory: library.appendingPathComponent("Caches/\(appName)", isDirectory: true),
            homeDirectory: home,
            temporaryDirectory: temporaryDirectory,
            usesDevRuntime: dev
        )
    }

    public static func isValidPort(_ port: Int) -> Bool {
        (1024...65535).contains(port)
    }

    public var baseURL: URL {
        URL(string: "http://127.0.0.1:\(port)")!
    }

    public func hopsURL(for endpoint: String) -> URL {
        baseURL.appendingPathComponent(endpoint)
    }

    /// `-I` isolated mode ignores every PYTHON* variable, so `-B` (no bytecode
    /// writes) and `-u` (unbuffered) are passed as flags. The env vars are still
    /// set as documentation and for any non-isolated debugging run.
    public func arguments(parentPID: Int32) -> [String] {
        ["-I", "-B", "-u", "-m", "cardboard_engine.server",
         "--port", String(port), "--parent-pid", String(parentPID)]
    }

    /// The complete child environment. Nothing is inherited from the user's shell.
    public func processEnvironment(token: String) -> [String: String] {
        [
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "ENGINE_SHUTDOWN_TOKEN": token,
            "ENGINE_DATA_DIR": dataDirectory.path,
            "ENGINE_CACHE_DIR": cacheDirectory.path,
            "HOME": homeDirectory.path,
            "TMPDIR": temporaryDirectory.path,
            "PATH": "/usr/bin:/bin",
        ]
    }
}
