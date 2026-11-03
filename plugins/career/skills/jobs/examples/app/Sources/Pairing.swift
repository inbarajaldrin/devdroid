import Foundation
import Security

/// Host lives in UserDefaults, token in the Keychain after redeeming a single-use pairing code.
struct Pairing: Equatable {
    /// Server origin and optional API path prefix, entered or received in a one-time code link.
    var host: String
    var token: String

    var baseURL: URL? {
        var h = host.trimmingCharacters(in: .whitespacesAndNewlines)
        if h.isEmpty { return nil }
        if !h.contains("://") { h = "http://" + h }
        while h.hasSuffix("/") { h.removeLast() }
        return URL(string: h)
    }

    var isComplete: Bool { baseURL != nil && !token.isEmpty }

    init(host: String, token: String) {
        self.host = host
        self.token = token
    }
}

enum PairingStore {
    private static let hostKey = "api_host"
    private static let service = Bundle.main.bundleIdentifier ?? "careerapp.starterkit"
    private static let account = "api_token"

    static func load() -> Pairing? {
        guard let host = UserDefaults.standard.string(forKey: hostKey), !host.isEmpty,
              let token = Keychain.read(service: service, account: account), !token.isEmpty else { return nil }
        return Pairing(host: host, token: token)
    }

    /// Returns false when the Keychain refused the token (the host is still saved).
    @discardableResult
    static func save(_ p: Pairing) -> Bool {
        UserDefaults.standard.set(p.host, forKey: hostKey)
        return Keychain.write(service: service, account: account, value: p.token) == errSecSuccess
    }

    static func clear() {
        UserDefaults.standard.removeObject(forKey: hostKey)
        Keychain.delete(service: service, account: account)
    }
}

enum Keychain {
    private static func query(_ service: String, _ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    static func read(service: String, account: String) -> String? {
        var q = query(service, account)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func write(service: String, account: String, value: String) -> OSStatus {
        let data = Data(value.utf8)
        let q = query(service, account)
        let attrs: [String: Any] = [kSecValueData as String: data,
                                    kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        var status = SecItemUpdate(q as CFDictionary, attrs as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(q.merging(attrs) { $1 } as CFDictionary, nil)
        }
        return status
    }

    static func delete(service: String, account: String) {
        SecItemDelete(query(service, account) as CFDictionary)
    }
}
