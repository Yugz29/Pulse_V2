import Foundation

public struct ApplicationContext: Equatable, Sendable {
    public let app: String
    public let bundleID: String?

    public init?(name: String?, bundleID: String?) {
        guard let readableName = name?.trimmingCharacters(in: .whitespacesAndNewlines),
              !readableName.isEmpty else {
            return nil
        }
        self.app = readableName
        let normalizedBundleID = bundleID?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.bundleID = normalizedBundleID?.isEmpty == false ? normalizedBundleID : nil
    }
}
