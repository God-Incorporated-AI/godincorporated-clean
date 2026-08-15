import Foundation

#if DEBUG

enum PCCSmokeTest {

    static func ask(packetJSON: String) async -> String {
        await ApplePCCInferenceAdapter
            .execute(packetJSON: packetJSON)
            .displayText
    }
}

#endif
