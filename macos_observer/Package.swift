// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "PulseApplicationObserver",
    platforms: [.macOS(.v13)],
    products: [
        .executable(
            name: "PulseApplicationObserver",
            targets: ["PulseApplicationObserver"]
        )
    ],
    targets: [
        .target(name: "ApplicationObserverCore"),
        .executableTarget(
            name: "PulseApplicationObserver",
            dependencies: ["ApplicationObserverCore"]
        ),
        .testTarget(
            name: "ApplicationObserverCoreTests",
            dependencies: ["ApplicationObserverCore"]
        ),
    ]
)
