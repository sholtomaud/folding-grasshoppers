import Foundation

/// A Claude model the Design Assistant can use (Settings > Design Assistant).
public struct AssistantModel: Hashable, Identifiable, Sendable {
    public let id: String
    public let name: String
    public let note: String

    public static let defaultID = "claude-opus-5-5"
    public static let all: [AssistantModel] = [
        AssistantModel(id: "claude-opus-5-5", name: "Claude Opus 5.5", note: "Best results (default)"),
        AssistantModel(id: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", note: "Faster, about half the price"),
        AssistantModel(id: "claude-haiku-5-5", name: "Claude Haiku 5.5", note: "Fastest and cheapest"),
    ]
    /// How hard Claude thinks before answering. Higher is slower and costs more.
    public static let efforts = ["low", "medium", "high"]
    public static let defaultEffort = "medium"
}

public struct ModelRequest: Equatable, Sendable {
    public var model: String
    public var system: String
    public var tools: [JSONValue]
    public var messages: [JSONValue]
    public var maxTokens: Int
    public var effort: String?

    public init(model: String, system: String, tools: [JSONValue], messages: [JSONValue],
                maxTokens: Int = 16000, effort: String? = nil) {
        self.model = model
        self.system = system
        self.tools = tools
        self.messages = messages
        self.maxTokens = maxTokens
        self.effort = effort
    }
}

public struct ModelResponse: Equatable, Sendable {
    /// Content blocks exactly as Claude sent them (text, tool_use, thinking...).
    public var content: [JSONValue]
    public var stopReason: String?
    public var model: String?

    public init(content: [JSONValue], stopReason: String?, model: String?) {
        self.content = content
        self.stopReason = stopReason
        self.model = model
    }
}

public protocol ChatModelClient: AnyObject, Sendable {
    func send(_ request: ModelRequest) async throws -> ModelResponse
}

public enum AnthropicError: Error, Equatable, LocalizedError {
    case missingAPIKey
    case http(Int, String)
    case invalidResponse

    public var errorDescription: String? {
        switch self {
        case .missingAPIKey:
            return "No Claude API key yet. Add one in Fold Theory > Settings > Design Assistant."
        case .http(401, let message):
            return "Claude rejected the API key (\(message)). Check it in Settings > Design Assistant."
        case .http(429, let message):
            return "Claude is rate limiting this key (\(message)). Wait a minute and try again."
        case .http(let status, let message) where status >= 500:
            return "Claude's servers had a problem (HTTP \(status): \(message)). Try again shortly."
        case .http(let status, let message):
            return "Claude returned HTTP \(status): \(message)"
        case .invalidResponse:
            return "Claude sent a response the app could not read."
        }
    }
}

/// Calls the Claude Messages API directly with URLSession (no SDK: the app
/// has no third-party dependencies). Only this class sees the API key.
public final class AnthropicClient: ChatModelClient, @unchecked Sendable {
    public static let endpoint = URL(string: "https://api.anthropic.com/v1/messages")!
    public static let apiVersion = "2026-10-21"
    /// Lets the API retry a declined request on another model inside the same call.
    public static let fallbackBeta = "server-side-fallback-2026-07-01"

    private let apiKey: @Sendable () -> String?
    private let session: URLSession
    private let timeout: TimeInterval

    public init(apiKey: @escaping @Sendable () -> String?, session: URLSession = .shared, timeout: TimeInterval = 600) {
        self.apiKey = apiKey
        self.session = session
        self.timeout = timeout
    }

    public static func body(for r: ModelRequest, fallbacks: Bool) -> JSONValue {
        var body: JSONValue = [
            "model": .string(r.model),
            "max_tokens": .number(Double(r.maxTokens)),
            "system": .string(r.system),
            "tools": .array(r.tools),
            "messages": .array(r.messages),
            "thinking": ["type": "adaptive"],
            // Caches the tools, system prompt and conversation so far between turns.
            "cache_control": ["type": "ephemeral"],
        ]
        if let effort = r.effort {
            body["output_config"] = ["effort": .string(effort)]
        }
        if fallbacks {
            body["fallbacks"] = "default"
        }
        return body
    }

    public func makeURLRequest(_ r: ModelRequest, apiKey: String, fallbacks: Bool) throws -> URLRequest {
        var request = URLRequest(url: Self.endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "content-type")
        request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        request.setValue(Self.apiVersion, forHTTPHeaderField: "anthropic-version")
        if fallbacks {
            request.setValue(Self.fallbackBeta, forHTTPHeaderField: "anthropic-beta")
        }
        request.httpBody = try JSONValue.encoder.encode(Self.body(for: r, fallbacks: fallbacks))
        return request
    }

    public func send(_ r: ModelRequest) async throws -> ModelResponse {
        guard let key = apiKey()?.trimmingCharacters(in: .whitespacesAndNewlines), !key.isEmpty else {
            throw AnthropicError.missingAPIKey
        }
        do {
            return try await post(r, key: key, fallbacks: true)
        } catch AnthropicError.http(400, let message) where message.lowercased().contains("fallback") {
            // Fallbacks are a beta; if this account cannot use them, ask without.
            return try await post(r, key: key, fallbacks: false)
        }
    }

    private func post(_ r: ModelRequest, key: String, fallbacks: Bool) async throws -> ModelResponse {
        let (data, response) = try await session.data(for: makeURLRequest(r, apiKey: key, fallbacks: fallbacks))
        guard let http = response as? HTTPURLResponse else { throw AnthropicError.invalidResponse }
        let json = try? JSONDecoder().decode(JSONValue.self, from: data)
        guard http.statusCode == 200 else {
            let message = json?["error"]?["message"]?.stringValue ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw AnthropicError.http(http.statusCode, message)
        }
        guard let json, let content = json["content"]?.arrayValue else { throw AnthropicError.invalidResponse }
        return ModelResponse(content: content, stopReason: json["stop_reason"]?.stringValue, model: json["model"]?.stringValue)
    }
}
