import Foundation

public struct ApplicationActivationFilter: Sendable {
    public init() {}

    public func isFrontmostActivation(
        notifiedProcessID: Int32,
        frontmostProcessID: Int32
    ) -> Bool {
        notifiedProcessID == frontmostProcessID
    }
}
