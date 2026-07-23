import AppKit
import ApplicationObserverCore
import Foundation

final class SystemObserver: @unchecked Sendable {
    private let workspaceCenter = NSWorkspace.shared.notificationCenter
    private let distributedCenter = DistributedNotificationCenter.default()
    private let projector = SystemNotificationProjector()
    private let bridge: OutboxBridge
    private let builder: CanonicalEventBuilder
    private var deduplicator = SystemEventDeduplicator()
    private var workspaceTokens: [NSObjectProtocol] = []
    private var distributedTokens: [NSObjectProtocol] = []

    init(repositoryRoot: URL) throws {
        let bridge = OutboxBridge(repositoryRoot: repositoryRoot)
        self.bridge = bridge
        self.builder = try CanonicalEventBuilder(instanceID: bridge.instanceID())
    }

    func start() {
        workspaceTokens =
            SystemNotificationProjector.workspaceNotificationNames.map { name in
                workspaceCenter.addObserver(
                    forName: name,
                    object: nil,
                    queue: .main
                ) { [weak self] notification in
                    self?.observe(notification.name)
                }
            }
        distributedTokens =
            SystemNotificationProjector.distributedNotificationNames.map { name in
                distributedCenter.addObserver(
                    forName: name,
                    object: nil,
                    queue: .main
                ) { [weak self] notification in
                    self?.observe(notification.name)
                }
            }
    }

    func stop() {
        for token in workspaceTokens {
            workspaceCenter.removeObserver(token)
        }
        for token in distributedTokens {
            distributedCenter.removeObserver(token)
        }
        workspaceTokens.removeAll()
        distributedTokens.removeAll()
    }

    private func observe(_ notificationName: Notification.Name) {
        guard let event = projector.event(for: notificationName) else {
            return
        }
        logReceived(event)
        guard !deduplicator.isDuplicate(event) else { return }
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

    private func logReceived(_ event: SystemEvent) {
        let message: String
        switch event {
        case .screenLocked:
            message = "[macos-observer] received screen lock notification\n"
        case .screenUnlocked:
            message = "[macos-observer] received screen unlock notification\n"
        default:
            return
        }
        FileHandle.standardError.write(Data(message.utf8))
    }

    deinit {
        stop()
    }
}
