import Foundation
import Security

/// Where the Claude API key lives. The real one is the login Keychain; it is
/// never written to UserDefaults, logs, or the engine.
public protocol SecretStore: AnyObject {
    func read() throws -> String?
    func save(_ secret: String) throws
    func delete() throws
}

public enum SecretStoreError: Error, LocalizedError, Equatable {
    case keychain(OSStatus)

    public var errorDescription: String? {
        switch self {
        case .keychain(let status):
            let text = SecCopyErrorMessageString(status, nil) as String? ?? "error \(status)"
            return "Keychain: \(text)"
        }
    }
}

/// A generic password item in the login Keychain.
public final class KeychainSecretStore: SecretStore {
    public let service: String
    public let account: String

    public init(service: String = "com.example.cardboardcad.claude", account: String = "anthropic-api-key") {
        self.service = service
        self.account = account
    }

    private var query: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    public func read() throws -> String? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw SecretStoreError.keychain(status) }
        guard let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    public func save(_ secret: String) throws {
        let data = Data(secret.utf8)
        let update = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else { throw SecretStoreError.keychain(update) }
        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrLabel as String] = "CardboardCAD Claude API key"
        add[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlocked
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else { throw SecretStoreError.keychain(status) }
    }

    public func delete() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw SecretStoreError.keychain(status) }
    }
}

/// For tests and previews.
public final class InMemorySecretStore: SecretStore {
    private var value: String?
    public init(_ value: String? = nil) { self.value = value }
    public func read() throws -> String? { value }
    public func save(_ secret: String) throws { value = secret }
    public func delete() throws { value = nil }
}

/// Small helpers for the Claude API key field in Settings.
public enum APIKey {
    /// Saves the trimmed key; a blank key removes it.
    public static func save(_ raw: String, to store: SecretStore) throws {
        let key = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if key.isEmpty {
            try store.delete()
        } else {
            try store.save(key)
        }
    }

    /// Anthropic keys start with "sk-ant-". Only a hint for the Settings field.
    public static func looksValid(_ key: String) -> Bool {
        let k = key.trimmingCharacters(in: .whitespacesAndNewlines)
        return k.hasPrefix("sk-ant-") && k.count > 20
    }

    /// "sk-ant-…wxyz": enough to recognise a key without showing it.
    public static func masked(_ key: String) -> String {
        guard key.count > 12 else { return String(repeating: "•", count: key.count) }
        return "sk-ant-…" + key.suffix(4)
    }
}
