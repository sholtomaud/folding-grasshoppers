import XCTest
@testable import CardboardCADKit

final class JSONValueTests: XCTestCase {
    func testDecodesEveryKind() throws {
        let json = #"{"a":1.5,"b":"x","c":true,"d":null,"e":[1,"two",false],"f":{"g":2}}"#
        let v = try JSONDecoder().decode(JSONValue.self, from: Data(json.utf8))
        XCTAssertEqual(v["a"], .number(1.5))
        XCTAssertEqual(v["b"]?.stringValue, "x")
        XCTAssertEqual(v["c"], .bool(true))
        XCTAssertEqual(v["d"], .null)
        XCTAssertEqual(v["e"], .array([.number(1), .string("two"), .bool(false)]))
        XCTAssertEqual(v["f"]?["g"]?.numberValue, 2)
        XCTAssertNil(v["missing"])
    }

    func testRoundTrip() throws {
        let v: JSONValue = .object(["k": .array([.number(3), .null]), "s": .string("é \"q\"")])
        let data = try JSONValue.encoder.encode(v)
        XCTAssertEqual(try JSONDecoder().decode(JSONValue.self, from: data), v)
    }

    func testEncodingIsDeterministicSoPromptCachingWorks() throws {
        let v: JSONValue = .object(["zebra": .number(1), "apple": .number(2), "mango": .number(3)])
        let text = String(decoding: try JSONValue.encoder.encode(v), as: UTF8.self)
        XCTAssertEqual(text, #"{"apple":2,"mango":3,"zebra":1}"#)
    }

    func testLiteralsAndSetters() {
        var v: JSONValue = ["name": "box", "n": 3, "ok": true]
        XCTAssertEqual(v["n"], .number(3))
        v["extra"] = .string("y")
        XCTAssertEqual(v["extra"]?.stringValue, "y")
        XCTAssertEqual(v.objectValue?.count, 4)
    }
}
