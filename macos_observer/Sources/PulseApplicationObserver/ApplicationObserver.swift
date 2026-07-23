import AppKit
import ApplicationObserverCore
import Foundation

// NSWorkspace delivers the registered callback on the main queue, and the
// executable also owns/stops this observer from that queue.
final class ApplicationObserver: @unchecked Sendable {
    private let notificationCenter = NSWorkspace.shared.notificationCenter
    private let bridge: OutboxBridge
    private let builder: CanonicalEventBuilder
    private var deduplicator = ApplicationDeduplicator()
    private var activationToken: NSObjectProtocol?

    init(repositoryRoot: URL) throws {
        let bridge = OutboxBridge(repositoryRoot: repositoryRoot)
        self.bridge = bridge
        self.builder = try CanonicalEventBuilder(instanceID: bridge.instanceID())
    }

    func start() {
        activationToken = notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let application = notification.userInfo?[
                NSWorkspace.applicationUserInfoKey
            ] as? NSRunningApplication else {
                return
            }
            self?.observe(application)
        }

        if let current = NSWorkspace.shared.frontmostApplication {
            observe(current)
        }
    }

    func stop() {
        if let activationToken {
            notificationCenter.removeObserver(activationToken)
            self.activationToken = nil
        }
    }

    private func observe(_ application: NSRunningApplication) {
        guard let context = ApplicationContext(
            name: application.localizedName,
            bundleID: application.bundleIdentifier
        ) else {
            return
        }
        guard !deduplicator.isDuplicate(context) else {
            return
        }

        do {
            let payload = try builder.build(context: context)
            try bridge.enqueue(payload: payload)
            deduplicator.record(context)
        } catch {
            FileHandle.standardError.write(
                Data("Pulse ApplicationObserver: \(error)\n".utf8)
            )
        }
    }

    deinit {
        stop()
    }
}
