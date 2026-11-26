// swift-tools-version:5.10
// Engine lifecycle, HTTP client and config for CardboardCAD.
// Kept as a local package (no third-party deps) so it can be unit tested
// with `swift test`, independently of the app target and its UI.
import PackageDescription

let package = Package(
    name: "CardboardCADKit",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "CardboardCADKit", targets: ["CardboardCADKit"]),
    ],
    targets: [
        .target(name: "CardboardCADKit"),
        .testTarget(name: "CardboardCADKitTests", dependencies: ["CardboardCADKit"]),
    ]
)
