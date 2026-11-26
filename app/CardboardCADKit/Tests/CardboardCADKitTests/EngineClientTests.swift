import XCTest
@testable import CardboardCADKit

/// Serves canned responses to an ephemeral URLSession.
final class StubURLProtocol: URLProtocol {
    struct Stub {
        var status: Int
        var body: Data
    }

    nonisolated(unsafe) static var stubs: [String: Stub] = [:]
    nonisolated(unsafe) static var requests: [URLRequest] = []

    static func reset() {
        stubs = [:]
        requests = []
    }

    static func stub(_ method: String, _ path: String, status: Int = 200, json: String) {
        stubs["\(method) \(path)"] = Stub(status: status, body: Data(json.utf8))
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        var recorded = request
        if recorded.httpBody == nil, let stream = request.httpBodyStream {
            recorded.httpBody = Self.readAll(stream)
        }
        Self.requests.append(recorded)
        let key = "\(request.httpMethod ?? "GET") \(request.url?.path ?? "")"
        let stub = Self.stubs[key] ?? Stub(status: 404, body: Data(#"{"error":"not stubbed"}"#.utf8))
        let response = HTTPURLResponse(
            url: request.url!, statusCode: stub.status, httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: stub.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

    private static func readAll(_ stream: InputStream) -> Data {
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let n = stream.read(&buffer, maxLength: buffer.count)
            if n <= 0 { break }
            data.append(buffer, count: n)
        }
        return data
    }
}

final class EngineClientTests: XCTestCase {
    var client: EngineClient!

    override func setUp() {
        StubURLProtocol.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        client = EngineClient(port: 5001, session: URLSession(configuration: config))
    }

    func testBaseURLIsLoopbackOnly() {
        XCTAssertEqual(client.baseURL.absoluteString, "http://127.0.0.1:5001")
    }

    func testHealthDecodes() async throws {
        StubURLProtocol.stub("GET", "/health", json: #"{"status":"healthy"}"#)
        let health = try await client.health()
        XCTAssertEqual(health, HealthResponse(status: "healthy"))
        XCTAssertTrue(health.isHealthy)
    }

    func testHealthNon200Throws() async {
        StubURLProtocol.stub("GET", "/health", status: 500, json: "{}")
        do {
            _ = try await client.health()
            XCTFail("expected throw")
        } catch let error as EngineClientError {
            XCTAssertEqual(error, .httpStatus(500))
        } catch {
            XCTFail("unexpected \(error)")
        }
    }

    func testVersionDecodesIncludingNullPackages() async throws {
        StubURLProtocol.stub("GET", "/version", json: """
        {"engine":"0.1.0","python":"3.12.13 (main) [Clang]",
         "packages":{"flask":"3.1.3","ghhops-server":"1.5.5","rhino3dm":null}}
        """)
        let v = try await client.version()
        XCTAssertEqual(v.engine, "0.1.0")
        XCTAssertEqual(v.python, "3.12.13 (main) [Clang]")
        XCTAssertEqual(v.packages["flask"], .some("3.1.3"))
        XCTAssertEqual(v.packages["rhino3dm"], .some(nil))
    }

    func testCutEstimateEncodesSnakeCaseAndDecodesResult() async throws {
        StubURLProtocol.stub("POST", "/api/cut_estimate", json: """
        {"time_min":2.2520833333,"material_cost":7.0,"machine_cost":2.2520833333,"total_cost":9.2520833333}
        """)
        var input = CutEstimateInput(cutLength: 2000)
        input.scoreLength = 500
        input.pierceCount = 10
        input.sheetCount = 2
        input.sheetPrice = 3.5
        input.machineRate = 60
        let result = try await client.cutEstimate(input)
        XCTAssertEqual(result.timeMin, 2.2520833333, accuracy: 1e-9)
        XCTAssertEqual(result.materialCost, 7.0)
        XCTAssertEqual(result.totalCost, 9.2520833333, accuracy: 1e-9)

        let request = try XCTUnwrap(StubURLProtocol.requests.last)
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        let body = try XCTUnwrap(request.httpBody)
        let sent = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Double])
        XCTAssertEqual(sent, [
            "cut_length": 2000, "score_length": 500, "pierce_count": 10, "sheet_count": 2,
            "cut_speed": 20, "score_speed": 40, "pierce_time": 0.5, "travel_overhead": 0.15,
            "sheet_price": 3.5, "machine_rate": 60,
        ])
    }

    func testCutEstimateDefaultsMatchEngine() {
        let input = CutEstimateInput(cutLength: 1)
        XCTAssertEqual(input.scoreLength, 0)
        XCTAssertEqual(input.pierceCount, 0)
        XCTAssertEqual(input.sheetCount, 1)
        XCTAssertEqual(input.cutSpeed, 20)
        XCTAssertEqual(input.scoreSpeed, 40)
        XCTAssertEqual(input.pierceTime, 0.5)
        XCTAssertEqual(input.travelOverhead, 0.15)
        XCTAssertEqual(input.sheetPrice, 0)
        XCTAssertEqual(input.machineRate, 0)
    }

    func testCutEstimateValidationErrorSurfacesMessage() async {
        StubURLProtocol.stub("POST", "/api/cut_estimate", status: 422, json: """
        {"error":"invalid_input","message":"Cut speed (Vc) must be greater than 0 (got 0)"}
        """)
        do {
            _ = try await client.cutEstimate(CutEstimateInput(cutLength: 1))
            XCTFail("expected throw")
        } catch let error as EngineClientError {
            XCTAssertEqual(error, .invalidInput("Cut speed (Vc) must be greater than 0 (got 0)"))
            XCTAssertEqual(error.errorDescription, "Cut speed (Vc) must be greater than 0 (got 0)")
        } catch {
            XCTFail("unexpected \(error)")
        }
    }

    func testMalformedJSONIsInvalidResponse() async {
        StubURLProtocol.stub("GET", "/health", json: "not json")
        do {
            _ = try await client.health()
            XCTFail("expected throw")
        } catch let error as EngineClientError {
            XCTAssertEqual(error, .invalidResponse)
        } catch {
            XCTFail("unexpected \(error)")
        }
    }

    func testShutdownSendsTokenHeaderAndReturnsStatus() async throws {
        StubURLProtocol.stub("POST", "/shutdown", status: 202, json: #"{"status":"shutting_down"}"#)
        let status = try await client.shutdown(token: "abc123")
        XCTAssertEqual(status, 202)
        let request = try XCTUnwrap(StubURLProtocol.requests.last)
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Engine-Token"), "abc123")
        XCTAssertEqual(request.httpMethod, "POST")
    }

    func testShutdownForbiddenReturnsStatusWithoutThrowing() async throws {
        StubURLProtocol.stub("POST", "/shutdown", status: 403, json: #"{"error":"forbidden"}"#)
        let status = try await client.shutdown(token: "wrong")
        XCTAssertEqual(status, 403)
    }

    func testDecodesRealEngineFixture() throws {
        // Captured from the Python engine (engine/tests) for the README vector.
        let json = #"{"machine_cost": 2.2520833333333334, "material_cost": 7.0, "time_min": 2.2520833333333334, "total_cost": 9.252083333333333}"#
        let r = try JSONDecoder().decode(CutEstimateResult.self, from: Data(json.utf8))
        XCTAssertEqual(r.machineCost, 135.125 / 60, accuracy: 1e-12)
    }
}
