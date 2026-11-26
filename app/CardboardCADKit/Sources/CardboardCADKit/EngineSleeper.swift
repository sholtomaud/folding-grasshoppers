import Foundation

/// Clock + sleep, injectable so the state machine can run on virtual time in tests.
public protocol EngineSleeper: AnyObject {
    /// Monotonic seconds.
    var now: Double { get }
    func sleep(seconds: Double) async throws
}

public final class LiveSleeper: EngineSleeper {
    private let start = ContinuousClock.now

    public init() {}

    public var now: Double {
        let d = ContinuousClock.now - start
        return Double(d.components.seconds) + Double(d.components.attoseconds) / 1e18
    }

    public func sleep(seconds: Double) async throws {
        try await Task.sleep(nanoseconds: UInt64(max(0, seconds) * 1_000_000_000))
    }
}
