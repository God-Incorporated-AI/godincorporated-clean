import Foundation
import FoundationModels

#if DEBUG

private struct PreparedInferencePacket: Decodable {
    let interaction_id: String
    let deity: String
    let system_prompt: String
    let memory_block: String
    let question: String
    let max_output_tokens: Int?
}

enum PCCSmokeTest {

    static func ask(packetJSON: String) async -> String {
        guard let data = packetJSON.data(using: .utf8) else {
            return "PCC packet could not be encoded."
        }

        do {
            let packet = try JSONDecoder().decode(
                PreparedInferencePacket.self,
                from: data
            )

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
        } catch {
            return "PCC packet decode failed: \(error.localizedDescription)"
        }
    }

    private static func respond(
        instructions: String? = nil,
        prompt: String
    ) async -> String {

        #if compiler(>=6.4)

        if #available(iOS 27.0, *) {
            let model = PrivateCloudComputeLanguageModel()

            switch model.availability {
            case .available:
                break

            case .unavailable(let reason):
                switch reason {
                case .deviceNotEligible:
                    return "PCC unavailable: device not eligible."

                case .systemNotReady:
                    return "PCC unavailable: system not ready."

                @unknown default:
                    return "PCC unavailable: unknown reason."
                }
            }

            do {
                let trimmedInstructions = (instructions ?? "")
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

                return response.content
            } catch {
                return "PCC request failed: \(error.localizedDescription)"
            }
        } else {
            return "PCC unavailable: requires iOS 27 or later."
        }

        #else

        return "PCC unavailable: requires Xcode 27 / Swift 6.4."

        #endif
    }
}

#endif
