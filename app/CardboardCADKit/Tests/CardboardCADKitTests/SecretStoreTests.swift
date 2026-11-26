import XCTest
@testable import CardboardCADKit

final class SecretStoreTests: XCTestCase {
    func testInMemoryStoreSavesReadsAndDeletes() throws {
        let store = InMemorySecretStore()
        XCTAssertNil(try store.read())
        try store.save("sk-ant-one")
        XCTAssertEqual(try store.read(), "sk-ant-one")
        try store.save("sk-ant-two")
        XCTAssertEqual(try store.read(), "sk-ant-two")
        try store.delete()
        XCTAssertNil(try store.read())
    }

    func testAPIKeyIsTrimmedAndBlankMeansRemove() throws {
        let store = InMemorySecretStore()
        try APIKey.save("  sk-ant-abc\n", to: store)
        XCTAssertEqual(try store.read(), "sk-ant-abc")
        try APIKey.save("   ", to: store)
        XCTAssertNil(try store.read())
    }

    func testAPIKeyShapeCheck() {
        XCTAssertTrue(APIKey.looksValid("sk-ant-api03-abcdefghijklmnopqrstuvwxyz"))
        XCTAssertFalse(APIKey.looksValid("hello"))
        XCTAssertFalse(APIKey.looksValid(""))
    }

    func testMaskedKeyNeverShowsTheSecret() {
        XCTAssertEqual(APIKey.masked("sk-ant-api03-abcdefghijklmnopqrstuvwxyz"), "sk-ant-…wxyz")
        XCTAssertEqual(APIKey.masked("short"), "•••••")
    }

    func testKeychainStoreUsesTheAppService() {
        let store = KeychainSecretStore()
        XCTAssertEqual(store.service, "com.example.cardboardcad.claude")
        XCTAssertEqual(store.account, "anthropic-api-key")
    }
}
