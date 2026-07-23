import Foundation

private struct ProducerPayload: Codable {
    let name: String
    let version: String
    let instanceID: String

    enum CodingKeys: String, CodingKey {
        case name
        case version
        case instanceID = "instance_id"
    }
}

private struct ApplicationDetailsPayload: Codable {
    let app: String
    let bundleID: String?

    enum CodingKeys: String, CodingKey {
        case app
        case bundleID = "bundle_id"
    }
}

private struct CanonicalEventPayload<Details: Codable>: Codable {
    let eventID: String
    let schemaVersion: Int
    let type: String
    let producer: ProducerPayload
    let occurredAt: String
    let details: Details

    enum CodingKeys: String, CodingKey {
        case eventID = "event_id"
        case schemaVersion = "schema_version"
        case type
        case producer
        case occurredAt = "occurred_at"
        case details
    }
}

public struct CanonicalEventBuilder: Sendable {
    public static let producerName = "pulse-macos-application-observer"
    public static let producerVersion = "1"

    private let instanceID: String

    public init(instanceID: String) throws {
        let normalized = instanceID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else {
            throw BuilderError.emptyInstanceID
        }
        self.instanceID = normalized
    }

    public func build(
        context: ApplicationContext,
        occurredAt: Date = Date(),
        eventID: UUID = UUID()
    ) throws -> Data {
        let event = CanonicalEventPayload(
            eventID: eventID.uuidString.lowercased(),
            schemaVersion: 1,
            type: "app_activated",
            producer: ProducerPayload(
                name: Self.producerName,
                version: Self.producerVersion,
                instanceID: instanceID
            ),
            occurredAt: Self.timestamp(occurredAt),
            details: ApplicationDetailsPayload(
                app: context.app,
                bundleID: context.bundleID
            )
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(event)
    }

    public func build(
        systemEvent: SystemEvent,
        occurredAt: Date = Date(),
        eventID: UUID = UUID()
    ) throws -> Data {
        let event = CanonicalEventPayload(
            eventID: eventID.uuidString.lowercased(),
            schemaVersion: 1,
            type: systemEvent.rawValue,
            producer: ProducerPayload(
                name: Self.producerName,
                version: Self.producerVersion,
                instanceID: instanceID
            ),
            occurredAt: Self.timestamp(occurredAt),
            details: EmptyDetailsPayload()
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(event)
    }

    private static func timestamp(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        return formatter.string(from: date)
    }

    public enum BuilderError: Error {
        case emptyInstanceID
    }
}

private struct EmptyDetailsPayload: Codable {}
