import AppKit
import ApplicationObserverCore
import Foundation

// NSWorkspace delivers the registered callback on the main queue, and the
// executable also owns/stops this observer from that queue.
final class ApplicationObserver: @unchecked Sendable {
    private let notificationCenter = NSWorkspace.shared.notificationCenter
    private let bridge: OutboxBridge
    private let builder: CanonicalEventBuilder
    private let activationFilter = ApplicationActivationFilter()
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
            guard let notifiedApplication = notification.userInfo?[
                NSWorkspace.applicationUserInfoKey
            ] as? NSRunningApplication,
            let frontmostApplication = NSWorkspace.shared.frontmostApplication,
            self?.activationFilter.isFrontmostActivation(
                notifiedProcessID: notifiedApplication.processIdentifier,
                frontmostProcessID: frontmostApplication.processIdentifier
            ) == true else {
                return
            }
            self?.observe(frontmostApplication)
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
        deduplicator.record(context)

        do {
            let payload = try builder.build(context: context)
            try bridge.enqueue(payload: payload)
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
