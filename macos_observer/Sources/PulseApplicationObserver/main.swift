import ApplicationObserverCore
import Darwin
import Foundation

let configuredRoot = ProcessInfo.processInfo.environment["PULSE_CORE_REPO_ROOT"]
let repositoryRoot = URL(
    fileURLWithPath: configuredRoot ?? FileManager.default.currentDirectoryPath,
    isDirectory: true
)

do {
    let applicationObserver = try ApplicationObserver(repositoryRoot: repositoryRoot)
    let systemObserver = try SystemObserver(repositoryRoot: repositoryRoot)
    applicationObserver.start()
    systemObserver.start()

    signal(SIGINT, SIG_IGN)
    signal(SIGTERM, SIG_IGN)
    let interruptSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
    let terminateSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
    let stop: @Sendable () -> Void = {
        systemObserver.stop()
        applicationObserver.stop()
        exit(0)
    }
    interruptSource.setEventHandler(handler: stop)
    terminateSource.setEventHandler(handler: stop)
    interruptSource.resume()
    terminateSource.resume()

    RunLoop.main.run()
} catch {
    FileHandle.standardError.write(
        Data("Pulse ApplicationObserver: \(error)\n".utf8)
    )
    exit(1)
}
