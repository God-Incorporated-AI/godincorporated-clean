import Foundation
import FoundationModels

#if DEBUG

enum PCCSmokeTest {

    static func run() async -> String {
        await respond(to: "Reply with exactly: PCC smoke test successful.")
    }

    static func ask(prompt: String) async -> String {
        await respond(to: prompt)
    }

    private static func respond(to prompt: String) async -> String {

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
                let session = LanguageModelSession(model: model)

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
