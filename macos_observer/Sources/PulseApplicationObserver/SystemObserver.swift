import AppKit
import ApplicationObserverCore
import Foundation

final class SystemObserver: @unchecked Sendable {
    private let notificationCenter = NSWorkspace.shared.notificationCenter
    private let projector = SystemNotificationProjector()
    private let bridge: OutboxBridge
    private let builder: CanonicalEventBuilder
    private var deduplicator = SystemEventDeduplicator()
    private var tokens: [NSObjectProtocol] = []

    init(repositoryRoot: URL) throws {
        let bridge = OutboxBridge(repositoryRoot: repositoryRoot)
        self.bridge = bridge
        self.builder = try CanonicalEventBuilder(instanceID: bridge.instanceID())
    }

    func start() {
        let names: [Notification.Name] = [
            NSWorkspace.willSleepNotification,
            NSWorkspace.didWakeNotification,
            NSWorkspace.sessionDidResignActiveNotification,
            NSWorkspace.sessionDidBecomeActiveNotification,
        ]
        tokens = names.map { name in
            notificationCenter.addObserver(
                forName: name,
                object: nil,
                queue: .main
            ) { [weak self] notification in
                self?.observe(notification.name)
            }
        }
    }

    func stop() {
        for token in tokens {
            notificationCenter.removeObserver(token)
        }
        tokens.removeAll()
    }

    private func observe(_ notificationName: Notification.Name) {
        guard let event = projector.event(for: notificationName),
              !deduplicator.isDuplicate(event) else {
            return
        }
        do {
            let payload = try builder.build(systemEvent: event)
            try bridge.enqueue(payload: payload)
            deduplicator.record(event)
        } catch {
            FileHandle.standardError.write(
                Data("Pulse SystemObserver: \(error)\n".utf8)
            )
        }
    }

    deinit {
        stop()
    }
}
