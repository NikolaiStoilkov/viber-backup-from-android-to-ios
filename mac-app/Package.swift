// swift-tools-version:5.9
// Native macOS front end for Viber Transfer. Build with ../run.sh (development) or ../build-dmg.sh.
import PackageDescription

let package = Package(
    name: "ViberTransfer",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "ViberTransfer", path: "Sources/ViberTransfer"),
    ]
)
