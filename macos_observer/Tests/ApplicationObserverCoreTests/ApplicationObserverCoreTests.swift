import Foundation
import AppKit
import Testing
@testable import ApplicationObserverCore

@Test
func contextRequiresReadableApplicationName() {
    #expect(ApplicationContext(name: nil, bundleID: "com.example.App") == nil)
    #expect(ApplicationContext(name: "  ", bundleID: "com.example.App") == nil)
}

@Test
func contextNormalizesOptionalBundleIdentifier() {
    let withoutBundle = ApplicationContext(name: "Terminal", bundleID: " ")
    let withBundle = ApplicationContext(
        name: "Visual Studio Code",
        bundleID: "com.microsoft.VSCode"
    )

    #expect(withoutBundle?.app == "Terminal")
    #expect(withoutBundle?.bundleID == nil)
    #expect(withBundle?.bundleID == "com.microsoft.VSCode")
}

@Test
func deduplicationPrefersBundleIdentifier() {
    var deduplicator = ApplicationDeduplicator()
    let first = ApplicationContext(name: "Code", bundleID: "com.microsoft.VSCode")!
    let renamed = ApplicationContext(
        name: "Visual Studio Code",
        bundleID: "com.microsoft.VSCode"
    )!

    #expect(!deduplicator.isDuplicate(first))
    deduplicator.record(first)
    #expect(deduplicator.isDuplicate(renamed))
}

@Test
func deduplicationFallsBackToApplicationName() {
    var deduplicator = ApplicationDeduplicator()
    let first = ApplicationContext(name: "Terminal", bundleID: nil)!
    let repeated = ApplicationContext(name: "Terminal", bundleID: nil)!
    let changed = ApplicationContext(name: "Safari", bundleID: nil)!

    deduplicator.record(first)
    #expect(deduplicator.isDuplicate(repeated))
    #expect(!deduplicator.isDuplicate(changed))
}

@Test
func initialApplicationIsRecordedAndFirstNotificationIsDeduplicated() {
    var deduplicator = ApplicationDeduplicator()
    let initial = ApplicationContext(
        name: "Terminal",
        bundleID: "com.apple.Terminal"
    )!

    #expect(!deduplicator.isDuplicate(initial))
    deduplicator.record(initial)
    #expect(deduplicator.isDuplicate(initial))
}

@Test
func canonicalPayloadContainsOnlyAllowedApplicationDetails() throws {
    let context = ApplicationContext(
        name: "Visual Studio Code",
        bundleID: "com.microsoft.VSCode"
    )!
    let builder = try CanonicalEventBuilder(instanceID: "stable-instance")
    let date = Date(timeIntervalSince1970: 1_700_000_000)
    let eventID = UUID(uuidString: "019C0000-0000-7000-8000-000000000001")!

    let data = try builder.build(
        context: context,
        occurredAt: date,
        eventID: eventID
    )
    let payload = try #require(
        JSONSerialization.jsonObject(with: data) as? [String: Any]
    )
    let producer = try #require(payload["producer"] as? [String: Any])
    let details = try #require(payload["details"] as? [String: Any])

    #expect(payload["event_id"] as? String == eventID.uuidString.lowercased())
    #expect(payload["schema_version"] as? Int == 1)
    #expect(payload["type"] as? String == "app_activated")
    #expect((payload["occurred_at"] as? String)?.hasSuffix("Z") == true)
    #expect(producer["name"] as? String == "pulse-macos-application-observer")
    #expect(producer["version"] as? String == "1")
    #expect(producer["instance_id"] as? String == "stable-instance")
    #expect(details["app"] as? String == "Visual Studio Code")
    #expect(details["bundle_id"] as? String == "com.microsoft.VSCode")
    #expect(Set(details.keys) == ["app", "bundle_id"])
}

@Test
func payloadOmitsUnavailableBundleIdentifier() throws {
    let context = ApplicationContext(name: "Terminal", bundleID: nil)!
    let builder = try CanonicalEventBuilder(instanceID: "stable-instance")
    let data = try builder.build(context: context)
    let payload = try #require(
        JSONSerialization.jsonObject(with: data) as? [String: Any]
    )
    let details = try #require(payload["details"] as? [String: Any])

    #expect(details["app"] as? String == "Terminal")
    #expect(details["bundle_id"] == nil)
}

@Test(
    arguments: [
        (NSWorkspace.willSleepNotification, SystemEvent.systemSleep),
        (NSWorkspace.didWakeNotification, SystemEvent.systemWake),
        (
            SystemNotificationProjector.screenLockedNotification,
            SystemEvent.screenLocked
        ),
        (
            SystemNotificationProjector.screenUnlockedNotification,
            SystemEvent.screenUnlocked
        ),
    ]
)
func systemNotificationsProjectToCanonicalEvents(
    notificationName: Notification.Name,
    expected: SystemEvent
) {
    let projector = SystemNotificationProjector()

    #expect(projector.event(for: notificationName) == expected)
}

@Test
func lockNotificationsUseTheDistributedNotificationCenterNames() {
    #expect(
        SystemNotificationProjector.distributedNotificationNames == [
            Notification.Name("com.apple.screenIsLocked"),
            Notification.Name("com.apple.screenIsUnlocked"),
        ]
    )
    #expect(
        !SystemNotificationProjector.workspaceNotificationNames.contains(
            NSWorkspace.sessionDidResignActiveNotification
        )
    )
    #expect(
        !SystemNotificationProjector.workspaceNotificationNames.contains(
            NSWorkspace.sessionDidBecomeActiveNotification
        )
    )
}

@Test(arguments: SystemEvent.allCases)
func systemEventPayloadHasEmptyDetails(event: SystemEvent) throws {
    let builder = try CanonicalEventBuilder(instanceID: "stable-instance")
    let eventID = UUID(uuidString: "019C0000-0000-7000-8000-000000000002")!
    let data = try builder.build(
        systemEvent: event,
        occurredAt: Date(timeIntervalSince1970: 1_700_000_000),
        eventID: eventID
    )
    let payload = try #require(
        JSONSerialization.jsonObject(with: data) as? [String: Any]
    )
    let details = try #require(payload["details"] as? [String: Any])

    #expect(payload["type"] as? String == event.rawValue)
    #expect(payload["event_id"] as? String == eventID.uuidString.lowercased())
    #expect(payload["schema_version"] as? Int == 1)
    #expect(details.isEmpty)
}

@Test
func repeatedSystemNotificationsAreSafelyDeduplicated() {
    var deduplicator = SystemEventDeduplicator()

    for event in SystemEvent.allCases {
        #expect(!deduplicator.isDuplicate(event))
        deduplicator.record(event)
        #expect(deduplicator.isDuplicate(event))
        #expect(deduplicator.isDuplicate(event))
    }
}
