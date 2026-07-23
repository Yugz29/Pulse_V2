import AppKit
import Foundation

public enum SystemEvent: String, CaseIterable, Sendable {
    case systemSleep = "system_sleep"
    case systemWake = "system_wake"
    case screenLocked = "screen_locked"
    case screenUnlocked = "screen_unlocked"
}

public struct SystemNotificationProjector: Sendable {
    // macOS emits these through DistributedNotificationCenter, but AppKit
    // does not expose strongly typed constants for their names.
    public static let screenLockedNotification = Notification.Name(
        "com.apple.screenIsLocked"
    )
    public static let screenUnlockedNotification = Notification.Name(
        "com.apple.screenIsUnlocked"
    )
    public static let workspaceNotificationNames: [Notification.Name] = [
        NSWorkspace.willSleepNotification,
        NSWorkspace.didWakeNotification,
    ]
    public static let distributedNotificationNames: [Notification.Name] = [
        screenLockedNotification,
        screenUnlockedNotification,
    ]

    public init() {}

    public func event(for notificationName: Notification.Name) -> SystemEvent? {
        switch notificationName {
        case NSWorkspace.willSleepNotification:
            return .systemSleep
        case NSWorkspace.didWakeNotification:
            return .systemWake
        case Self.screenLockedNotification:
            return .screenLocked
        case Self.screenUnlockedNotification:
            return .screenUnlocked
        default:
            return nil
        }
    }
}

public struct SystemEventDeduplicator: Sendable {
    private var lastEvent: SystemEvent?

    public init() {}

    public func isDuplicate(_ event: SystemEvent) -> Bool {
        lastEvent == event
    }

    public mutating func record(_ event: SystemEvent) {
        lastEvent = event
    }
}
