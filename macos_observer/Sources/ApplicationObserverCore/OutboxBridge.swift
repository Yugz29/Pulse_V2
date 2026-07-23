import Foundation

public struct OutboxBridge: Sendable {
    private let repositoryRoot: URL
    private let pythonExecutable: URL

    public init(
        repositoryRoot: URL,
        pythonExecutable: URL? = nil
    ) {
        self.repositoryRoot = repositoryRoot
        let bundledPython = repositoryRoot
            .appendingPathComponent(".venv/bin/python")
        if let pythonExecutable {
            self.pythonExecutable = pythonExecutable
        } else if FileManager.default.isExecutableFile(atPath: bundledPython.path) {
            self.pythonExecutable = bundledPython
        } else {
            self.pythonExecutable = URL(fileURLWithPath: "/usr/bin/python3")
        }
    }

    public func instanceID() throws -> String {
        let output = try run(command: "instance-id", input: nil)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !output.isEmpty else {
            throw BridgeError.emptyOutput
        }
        return output
    }

    public func enqueue(payload: Data) throws {
        _ = try run(command: "enqueue-json", input: payload)
    }

    private func run(command: String, input: Data?) throws -> String {
        let process = Process()
        process.executableURL = pythonExecutable
        process.arguments = [
            "-m",
            "daemon_v2.producer_outbox",
            command,
        ]
        process.currentDirectoryURL = repositoryRoot
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = repositoryRoot.path
        process.environment = environment

        let standardInput = Pipe()
        let standardOutput = Pipe()
        let standardError = Pipe()
        process.standardInput = standardInput
        process.standardOutput = standardOutput
        process.standardError = standardError

        try process.run()
        if let input {
            standardInput.fileHandleForWriting.write(input)
        }
        try standardInput.fileHandleForWriting.close()
        process.waitUntilExit()

        let output = standardOutput.fileHandleForReading.readDataToEndOfFile()
        let error = standardError.fileHandleForReading.readDataToEndOfFile()
        guard process.terminationStatus == 0 else {
            let message = String(data: error, encoding: .utf8) ?? "unknown error"
            throw BridgeError.commandFailed(message)
        }
        return String(data: output, encoding: .utf8) ?? ""
    }

    public enum BridgeError: Error {
        case emptyOutput
        case commandFailed(String)
    }
}
