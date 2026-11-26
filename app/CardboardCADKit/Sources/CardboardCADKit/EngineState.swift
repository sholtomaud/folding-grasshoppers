import Foundation

public struct EngineFailure: Equatable, Sendable {
    public enum Action: Equatable, Sendable {
        case openSettings
        case viewLogs
    }

    public var reason: String
    public var action: Action

    public init(reason: String, action: Action) {
        self.reason = reason
        self.action = action
    }
}

public enum EngineState: Equatable, Sendable {
    case stopped
    case starting
    case running(port: Int)
    case failed(EngineFailure)
    case restarting(attempt: Int)

    public var isBusy: Bool {
        switch self {
        case .starting, .restarting: return true
        default: return false
        }
    }

    public var summary: String {
        switch self {
        case .stopped: return "Engine stopped"
        case .starting: return "Engine starting"
        case .running(let port): return "Engine running on 127.0.0.1:\(port)"
        case .failed(let f): return "Engine failed: \(f.reason)"
        case .restarting(let n): return "Engine restarting (attempt \(n))"
        }
    }
}
