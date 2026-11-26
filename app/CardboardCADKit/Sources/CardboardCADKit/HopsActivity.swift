import Foundation

/// GET /api/hops_activity: the Hops (Grasshopper) requests the engine has served.
///
/// Hops never keeps a connection open. It sends a request whenever a component
/// loads or solves, so "connected" really means "called recently".
public struct HopsActivity: Codable, Equatable, Sendable {
    public var requests: Int
    public var lastRequestAt: String?
    public var secondsSinceLast: Double?
    public var lastPath: String?
    public var lastStatus: Int?

    public init(requests: Int, lastRequestAt: String? = nil, secondsSinceLast: Double? = nil,
                lastPath: String? = nil, lastStatus: Int? = nil) {
        self.requests = requests
        self.lastRequestAt = lastRequestAt
        self.secondsSinceLast = secondsSinceLast
        self.lastPath = lastPath
        self.lastStatus = lastStatus
    }

    enum CodingKeys: String, CodingKey {
        case requests
        case lastRequestAt = "last_request_at"
        case secondsSinceLast = "seconds_since_last"
        case lastPath = "last_path"
        case lastStatus = "last_status"
    }

    /// Green while Grasshopper has called within this many seconds.
    public static let recentWindow: Double = 120

    public enum Level: Equatable, Sendable { case never, recent, idle, error }

    public var level: Level {
        guard requests > 0, let age = secondsSinceLast else { return .never }
        if let status = lastStatus, status >= 400 { return .error }
        return age <= Self.recentWindow ? .recent : .idle
    }

    /// One line for the status bar, e.g. "Grasshopper: last call 12 s ago".
    public var summary: String {
        guard requests > 0, let age = secondsSinceLast else {
            return "Grasshopper: no calls yet"
        }
        var s = "Grasshopper: last call \(Self.formatAge(age))"
        if let status = lastStatus, status >= 400 { s += " failed (HTTP \(status))" }
        return s
    }

    static func formatAge(_ seconds: Double) -> String {
        let s = Int(seconds.rounded(.down))
        if s < 5 { return "just now" }
        if s < 60 { return "\(s) s ago" }
        if s < 3600 { return "\(s / 60) min ago" }
        return "\(s / 3600) h ago"
    }
}
