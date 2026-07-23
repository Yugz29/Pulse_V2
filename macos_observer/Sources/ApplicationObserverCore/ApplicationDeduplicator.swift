import Foundation

public struct ApplicationDeduplicator: Sendable {
    private var lastContext: ApplicationContext?

    public init() {}

    public func isDuplicate(_ context: ApplicationContext) -> Bool {
        guard let previous = lastContext else { return false }
        if let currentBundleID = context.bundleID,
           let previousBundleID = previous.bundleID {
            return currentBundleID == previousBundleID
        }
        return context.app == previous.app
    }

    public mutating func record(_ context: ApplicationContext) {
        lastContext = context
    }
}
