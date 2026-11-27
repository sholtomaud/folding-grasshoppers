import Foundation

// MARK: - Models

public struct HealthResponse: Codable, Equatable, Sendable {
    public var status: String
    public init(status: String) { self.status = status }
    public var isHealthy: Bool { status == "healthy" }
}

public struct VersionInfo: Codable, Equatable, Sendable {
    public var engine: String
    public var python: String
    public var packages: [String: String?]

    public init(engine: String, python: String, packages: [String: String?]) {
        self.engine = engine
        self.python = python
        self.packages = packages
    }
}

/// Plain-JSON twin of the Hops `/cut_estimate` inputs (mm, s). Defaults match the engine.
public struct CutEstimateInput: Codable, Equatable, Sendable {
    public var cutLength: Double
    public var scoreLength: Double = 0
    public var pierceCount: Double = 0
    public var sheetCount: Double = 1
    public var cutSpeed: Double = 20
    public var scoreSpeed: Double = 40
    public var pierceTime: Double = 0.5
    public var travelOverhead: Double = 0.15
    public var sheetPrice: Double = 0
    public var machineRate: Double = 0

    public init(cutLength: Double) { self.cutLength = cutLength }

    enum CodingKeys: String, CodingKey {
        case cutLength = "cut_length"
        case scoreLength = "score_length"
        case pierceCount = "pierce_count"
        case sheetCount = "sheet_count"
        case cutSpeed = "cut_speed"
        case scoreSpeed = "score_speed"
        case pierceTime = "pierce_time"
        case travelOverhead = "travel_overhead"
        case sheetPrice = "sheet_price"
        case machineRate = "machine_rate"
    }
}

public struct CutEstimateResult: Codable, Equatable, Sendable {
    public var timeMin: Double
    public var materialCost: Double
    public var machineCost: Double
    public var totalCost: Double

    enum CodingKeys: String, CodingKey {
        case timeMin = "time_min"
        case materialCost = "material_cost"
        case machineCost = "machine_cost"
        case totalCost = "total_cost"
    }
}

public enum EngineClientError: Error, Equatable, LocalizedError {
    case httpStatus(Int)
    case invalidInput(String)
    case invalidResponse

    public var errorDescription: String? {
        switch self {
        case .httpStatus(let code): return "Engine returned HTTP \(code)"
        case .invalidInput(let message): return message
        case .invalidResponse: return "Engine returned an unreadable response"
        }
    }
}

// MARK: - Client

public protocol EngineClientProtocol: AnyObject {
    func health() async throws -> HealthResponse
    func version() async throws -> VersionInfo
    func cutEstimate(_ input: CutEstimateInput) async throws -> CutEstimateResult
    /// Hello-world demo round trip (POST /api/hello).
    func hello(name: String) async throws -> String
    /// Returns the HTTP status (202 accepted, 403 bad token) instead of throwing on it.
    func shutdown(token: String) async throws -> Int
    /// Hops requests the engine has served (GET /api/hops_activity).
    func hopsActivity() async throws -> HopsActivity
}

public extension EngineClientProtocol {
    /// Default for clients that do not report Hops activity.
    func hopsActivity() async throws -> HopsActivity {
        throw EngineClientError.httpStatus(404)
    }
}

/// Typed client for the engine on `http://127.0.0.1:<port>`.
public final class EngineClient: EngineClientProtocol, @unchecked Sendable {
    public let baseURL: URL
    private let session: URLSession
    private let timeout: TimeInterval

    public init(port: Int, session: URLSession = EngineClient.makeSession(), timeout: TimeInterval = 2) {
        self.baseURL = URL(string: "http://127.0.0.1:\(port)")!
        self.session = session
        self.timeout = timeout
    }

    /// No caching, no cookies, no proxies: this only ever talks to loopback.
    public static func makeSession() -> URLSession {
        let c = URLSessionConfiguration.ephemeral
        c.connectionProxyDictionary = [:]
        c.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        c.waitsForConnectivity = false
        return URLSession(configuration: c)
    }

    public func health() async throws -> HealthResponse {
        try await get("/health", timeout: min(timeout, 1))
    }

    public func version() async throws -> VersionInfo {
        try await get("/version")
    }

    public func cutEstimate(_ input: CutEstimateInput) async throws -> CutEstimateResult {
        var request = makeRequest("/api/cut_estimate", method: "POST", timeout: timeout)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(input)
        let (data, status) = try await send(request)
        if status == 422 || status == 400 {
            let message = (try? JSONDecoder().decode(APIError.self, from: data))?.message
            throw EngineClientError.invalidInput(message ?? "Invalid input")
        }
        guard status == 200 else { throw EngineClientError.httpStatus(status) }
        return try decode(data)
    }

    public func hello(name: String) async throws -> String {
        struct Body: Encodable { let name: String }
        struct Reply: Decodable { let message: String }
        var request = makeRequest("/api/hello", method: "POST", timeout: timeout)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(Body(name: name))
        let (data, status) = try await send(request)
        guard status == 200 else { throw EngineClientError.httpStatus(status) }
        let reply: Reply = try decode(data)
        return reply.message
    }

    public func hopsActivity() async throws -> HopsActivity {
        try await get("/api/hops_activity", timeout: min(timeout, 1))
    }

    public func shutdown(token: String) async throws -> Int {
        var request = makeRequest("/shutdown", method: "POST", timeout: 1)
        request.setValue(token, forHTTPHeaderField: "X-Engine-Token")
        let (_, status) = try await send(request)
        return status
    }

    // MARK: plumbing

    private struct APIError: Decodable {
        var error: String?
        var message: String?
    }

    private func makeRequest(_ path: String, method: String, timeout: TimeInterval) -> URLRequest {
        var request = URLRequest(url: baseURL.appendingPathComponent(String(path.dropFirst())))
        request.httpMethod = method
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    private func get<T: Decodable>(_ path: String, timeout: TimeInterval? = nil) async throws -> T {
        let (data, status) = try await send(makeRequest(path, method: "GET", timeout: timeout ?? self.timeout))
        guard status == 200 else { throw EngineClientError.httpStatus(status) }
        return try decode(data)
    }

    private func send(_ request: URLRequest) async throws -> (Data, Int) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw EngineClientError.invalidResponse }
        return (data, http.statusCode)
    }

    private func decode<T: Decodable>(_ data: Data) throws -> T {
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw EngineClientError.invalidResponse
        }
    }
}

// MARK: - Design Assistant routes

/// The engine's system prompt and tool definitions for Claude (GET /api/design/tools).
public struct DesignSetup: Codable, Equatable, Sendable {
    public var system: String
    public var tools: [JSONValue]

    public init(system: String, tools: [JSONValue]) {
        self.system = system
        self.tools = tools
    }
}

/// One tool run on the engine's design (POST /api/design/tool).
public struct DesignToolOutput: Codable, Equatable, Sendable {
    public var content: String
    public var isError: Bool
    public var version: Int
    /// The engine's system prompt changed (a workflow tool ran), so fetch the setup again.
    public var setupChanged: Bool

    public init(content: String, isError: Bool, version: Int, setupChanged: Bool = false) {
        self.content = content
        self.isError = isError
        self.version = version
        self.setupChanged = setupChanged
    }

    enum CodingKeys: String, CodingKey {
        case content
        case isError = "is_error"
        case version
        case setupChanged = "setup_changed"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        content = try c.decode(String.self, forKey: .content)
        isError = try c.decode(Bool.self, forKey: .isError)
        version = try c.decode(Int.self, forKey: .version)
        setupChanged = try c.decodeIfPresent(Bool.self, forKey: .setupChanged) ?? false
    }
}

/// Runs Claude's tool calls. The engine holds the design; the app only relays.
public protocol DesignToolRunner: AnyObject {
    func designSetup() async throws -> DesignSetup
    func runDesignTool(name: String, input: JSONValue) async throws -> DesignToolOutput
    func resetDesign() async throws
}

extension EngineClient: DesignToolRunner {
    /// Slicing and nesting a big model can take a while.
    public static let designToolTimeout: TimeInterval = 180

    /// The live preview page the app shows beside the chat.
    public var previewURL: URL { baseURL.appendingPathComponent("preview") }

    public func designSetup() async throws -> DesignSetup {
        try await get("/api/design/tools", timeout: 5)
    }

    public func runDesignTool(name: String, input: JSONValue) async throws -> DesignToolOutput {
        var request = makeRequest("/api/design/tool", method: "POST", timeout: Self.designToolTimeout)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: JSONValue = ["name": .string(name), "input": input]
        request.httpBody = try JSONValue.encoder.encode(body)
        let (data, status) = try await send(request)
        guard status == 200 else { throw EngineClientError.httpStatus(status) }
        return try decode(data)
    }

    /// GET a JSON reply (for routes defined in other files).
    func getJSON<T: Decodable>(_ path: String, timeout: TimeInterval = 5) async throws -> T {
        try await get(path, timeout: timeout)
    }

    /// POST a JSON body. 400/422 become `.invalidInput` with the engine's message.
    func postJSON<T: Decodable>(_ path: String, body: JSONValue,
                                timeout: TimeInterval = EngineClient.designToolTimeout) async throws -> T {
        var request = makeRequest(path, method: "POST", timeout: timeout)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONValue.encoder.encode(body)
        let (data, status) = try await send(request)
        if status == 422 || status == 400 {
            let message = (try? JSONDecoder().decode(APIError.self, from: data))?.message
            throw EngineClientError.invalidInput(message ?? "The engine refused the request")
        }
        guard status == 200 else { throw EngineClientError.httpStatus(status) }
        return try decode(data)
    }

    public func resetDesign() async throws {
        let (_, status) = try await send(makeRequest("/api/design/reset", method: "POST", timeout: 5))
        guard status == 200 else { throw EngineClientError.httpStatus(status) }
    }
}
