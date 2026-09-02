import Foundation
import FoundationModels

struct PreparedOracleInferencePacket: Decodable {
    let interaction_id: String
    let deity: String
    let system_prompt: String
    let memory_block: String
    let question: String
    let max_output_tokens: Int?
}

enum ApplePCCAvailability {
    case available
    case unavailable(reason: String)
}

enum ApplePCCExecutionResult {
    case completed(answer: String)
    case unavailable(reason: String)
    case failed(message: String)

    var displayText: String {
        switch self {
        case .completed(let answer):
            return answer
        case .unavailable(let reason):
            return reason
        case .failed(let message):
            return message
        }
    }
}

enum ApplePCCInferenceAdapter {

    static func availability() -> ApplePCCAvailability {

        #if compiler(>=6.4)

        if #available(iOS 27.0, *) {
            let model = PrivateCloudComputeLanguageModel()

            switch model.availability {
            case .available:
                return .available

            case .unavailable(let reason):
                switch reason {
                case .deviceNotEligible:
                    return .unavailable(
                        reason: "PCC unavailable: device not eligible."
                    )

                case .systemNotReady:
                    return .unavailable(
                        reason: "PCC unavailable: system not ready."
                    )

                @unknown default:
                    return .unavailable(
                        reason: "PCC unavailable: unknown reason."
                    )
                }
            }
        } else {
            return .unavailable(
                reason: "PCC unavailable: requires iOS 27 or later."
            )
        }

        #else

        return .unavailable(
            reason: "PCC unavailable: requires Xcode 27 / Swift 6.4."
        )

        #endif
    }

    static func execute(
        packetJSON: String
    ) async -> ApplePCCExecutionResult {
        guard let data = packetJSON.data(using: .utf8) else {
            return .failed(
                message: "PCC packet could not be encoded."
            )
        }

        do {
            let packet = try JSONDecoder().decode(
                PreparedOracleInferencePacket.self,
                from: data
            )

            return await execute(packet: packet)
        } catch {
            return .failed(
                message: "PCC packet decode failed: \(error.localizedDescription)"
            )
        }
    }

    static func execute(
        packet: PreparedOracleInferencePacket
    ) async -> ApplePCCExecutionResult {
        var promptParts: [String] = []

        let memoryBlock = packet.memory_block
            .trimmingCharacters(in: .whitespacesAndNewlines)

        if !memoryBlock.isEmpty {
            promptParts.append(
                "Background memory supplied by God Incorporated:\n"
                + memoryBlock
            )
        }

        promptParts.append(packet.question)

        return await respond(
            instructions: packet.system_prompt,
            prompt: promptParts.joined(separator: "\n\n")
        )
    }

    static func executeStreaming(
        packet: PreparedOracleInferencePacket,
        onSnapshot: @escaping (String) async -> Bool
    ) async -> ApplePCCExecutionResult {
        var promptParts: [String] = []

        let memoryBlock = packet.memory_block
            .trimmingCharacters(in: .whitespacesAndNewlines)

        if !memoryBlock.isEmpty {
            promptParts.append(
                "Background memory supplied by God Incorporated:\n"
                + memoryBlock
            )
        }

        promptParts.append(packet.question)

        return await streamRespond(
            instructions: packet.system_prompt,
            prompt: promptParts.joined(separator: "\n\n"),
            onSnapshot: onSnapshot
        )
    }

    private static func streamRespond(
        instructions: String,
        prompt: String,
        onSnapshot: @escaping (String) async -> Bool
    ) async -> ApplePCCExecutionResult {

        #if compiler(>=6.4)

        if #available(iOS 27.0, *) {
            let model = PrivateCloudComputeLanguageModel()

            switch model.availability {
            case .available:
                break

            case .unavailable(let reason):
                switch reason {
                case .deviceNotEligible:
                    return .unavailable(
                        reason: "PCC unavailable: device not eligible."
                    )

                case .systemNotReady:
                    return .unavailable(
                        reason: "PCC unavailable: system not ready."
                    )

                @unknown default:
                    return .unavailable(
                        reason: "PCC unavailable: unknown reason."
                    )
                }
            }

            let quotaUsage = model.quotaUsage

            if quotaUsage.isLimitReached {
                return .unavailable(
                    reason: "PCC unavailable: daily user quota reached."
                )
            }

            do {
                let trimmedInstructions = instructions
                    .trimmingCharacters(in: .whitespacesAndNewlines)

                let session: LanguageModelSession

                if trimmedInstructions.isEmpty {
                    session = LanguageModelSession(model: model)
                } else {
                    session = LanguageModelSession(
                        model: model,
                        instructions: trimmedInstructions
                    )
                }

                let stream = session.streamResponse(
                    to: prompt
                )

                var latestContent = ""

                for try await snapshot in stream {
                    let currentContent = snapshot.content
                    latestContent = currentContent

                    let shouldContinue = await onSnapshot(
                        currentContent
                    )

                    guard shouldContinue else {
                        return .failed(
                            message: "PCC streaming request cancelled."
                        )
                    }
                }

                guard !latestContent
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                    .isEmpty else {
                    return .failed(
                        message: "PCC streaming request returned an empty answer."
                    )
                }

                return .completed(
                    answer: latestContent
                )
            } catch {
                return .failed(
                    message: "PCC streaming request failed: \(error.localizedDescription)"
                )
            }
        } else {
            return .unavailable(
                reason: "PCC unavailable: requires iOS 27 or later."
            )
        }

        #else

        return .unavailable(
            reason: "PCC unavailable: requires Xcode 27 / Swift 6.4."
        )

        #endif
    }

    private static func respond(
        instructions: String,
        prompt: String
    ) async -> ApplePCCExecutionResult {

        #if compiler(>=6.4)

        if #available(iOS 27.0, *) {
            let model = PrivateCloudComputeLanguageModel()

            switch model.availability {
            case .available:
                break

            case .unavailable(let reason):
                switch reason {
                case .deviceNotEligible:
                    return .unavailable(
                        reason: "PCC unavailable: device not eligible."
                    )

                case .systemNotReady:
                    return .unavailable(
                        reason: "PCC unavailable: system not ready."
                    )

                @unknown default:
                    return .unavailable(
                        reason: "PCC unavailable: unknown reason."
                    )
                }
            }

            do {
                let trimmedInstructions = instructions
                    .trimmingCharacters(in: .whitespacesAndNewlines)

                let session: LanguageModelSession

                if trimmedInstructions.isEmpty {
                    session = LanguageModelSession(model: model)
                } else {
                    session = LanguageModelSession(
                        model: model,
                        instructions: trimmedInstructions
                    )
                }

                let response = try await session.respond(
                    to: prompt
                )

                return .completed(answer: response.content)
            } catch {
                return .failed(
                    message: "PCC request failed: \(error.localizedDescription)"
                )
            }
        } else {
            return .unavailable(
                reason: "PCC unavailable: requires iOS 27 or later."
            )
        }

        #else

        return .unavailable(
            reason: "PCC unavailable: requires Xcode 27 / Swift 6.4."
        )

        #endif
    }
}
