import Foundation

// The design brief (#44). The engine keeps the brief and checks the design against it;
// the Brief view (a JSON view) shows the checks and edits the fields here.

/// Writes brief fields. `values` is {field: value}; null clears a field.
public protocol BriefClient: AnyObject {
    func setBrief(values: JSONValue) async throws -> JSONValue
}

extension EngineClient: BriefClient {
    public func setBrief(values: JSONValue) async throws -> JSONValue {
        try await postJSON("/api/brief", body: ["values": values], timeout: 30)
    }
}

/// Which view bindings a field block can edit in place.
public enum ViewBinding {
    public static func isEditable(_ binding: String?) -> Bool {
        guard let binding else { return false }
        return binding.hasPrefix("settings.") || binding.hasPrefix("brief.")
    }

    public static func isBrief(_ binding: String?) -> Bool { binding?.hasPrefix("brief.") == true }
}

extension ProjectViewsController {
    /// The designer typed a brief value. Numbers become numbers; an empty field clears it.
    @discardableResult
    public func setBrief(key: String, text: String) async -> Bool {
        guard let client = attachedClient as? BriefClient else {
            error = "The engine is not running."
            return false
        }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let value: JSONValue
        if trimmed.isEmpty {
            value = .null
        } else if let n = Double(trimmed.replacingOccurrences(of: ",", with: "")) {
            value = .number(n)
        } else {
            value = .string(trimmed)
        }
        do {
            _ = try await client.setBrief(values: .object([key: value]))
            error = nil
            await refresh()
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }
}
