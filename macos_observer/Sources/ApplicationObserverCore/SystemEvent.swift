import AppKit
import Foundation

public enum SystemEvent: String, CaseIterable, Sendable {
    case systemSleep = "system_sleep"
    case systemWake = "system_wake"
    case screenLocked = "screen_locked"
    case screenUnlocked = "screen_unlocked"
}

public struct SystemNotificationProjector: Sendable {
    public init() {}

    public func event(for notificationName: Notification.Name) -> SystemEvent? {
        switch notificationName {
        case NSWorkspace.willSleepNotification:
            return .systemSleep
        case NSWorkspace.didWakeNotification:
            return .systemWake
        case NSWorkspace.sessionDidResignActiveNotification:
            return .screenLocked
        case NSWorkspace.sessionDidBecomeActiveNotification:
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
