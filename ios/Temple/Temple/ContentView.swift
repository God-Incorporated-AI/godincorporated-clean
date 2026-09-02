//
//  ContentView.swift
//  Temple
//
//  v11.4L: Native Temple Gate, native iOS voice capture,
//  StoreKit support, Terms/EULA links.
//

import SwiftUI
import WebKit
import StoreKit
import AVFoundation
import Speech

private enum TempleEnvironment {
#if DEBUG
    static let baseAppURL = URL(string: "https://godincorporated-staging.onrender.com/")!
#else
    static let baseAppURL = URL(string: "https://godincorporated.ai/")!
#endif

    static let baseTempleURL = URL(string: "temple", relativeTo: baseAppURL)!
    static let accountURL = URL(string: "account", relativeTo: baseAppURL)!
    static let meURL = URL(string: "me", relativeTo: baseAppURL)!
    static let oraclePreferenceURL = URL(string: "me/oracle", relativeTo: baseAppURL)!
    static let privacyURL = URL(string: "privacy", relativeTo: baseAppURL)!
    static let termsURL = URL(string: "terms", relativeTo: baseAppURL)!
    static let voiceTranscribeURL = URL(string: "voice/transcribe", relativeTo: baseAppURL)!
    static let oracleAskURL = URL(string: "ask", relativeTo: baseAppURL)!
    static let voiceAskURL = URL(string: "voice/ask", relativeTo: baseAppURL)!
    static let voiceTTSURL = URL(string: "voice/tts", relativeTo: baseAppURL)!
    static let oracleInferencePrepareURL = URL(string: "oracle/inference/prepare", relativeTo: baseAppURL)!
    static let oracleInferenceCompleteURL = URL(string: "oracle/inference/complete", relativeTo: baseAppURL)!
    static let oracleInferenceAbandonURL = URL(string: "oracle/inference/abandon", relativeTo: baseAppURL)!
    static let seekerMonthlyProductID = "ai.godincorporated.seeker.monthly"

    static func templeURL(
        voice: String?,
        entry: String? = nil,
        auth: String? = nil,
        entryNonce: Int = 0
    ) -> URL {
        var components = URLComponents(url: baseTempleURL.absoluteURL, resolvingAgainstBaseURL: true)
        var queryItems: [URLQueryItem] = [
            URLQueryItem(name: "native", value: "ios")
        ]

        if let voice, !voice.isEmpty {
            queryItems.append(URLQueryItem(name: "voice", value: voice.lowercased()))
        }

        if let entry, !entry.isEmpty {
            queryItems.append(URLQueryItem(name: "entry", value: entry.lowercased()))
        }

        if let auth, !auth.isEmpty {
            queryItems.append(URLQueryItem(name: "auth", value: auth.lowercased()))
        }

        if entryNonce > 0 {
            queryItems.append(URLQueryItem(name: "entry_nonce", value: String(entryNonce)))
        }

        components?.queryItems = queryItems
        return components?.url ?? baseTempleURL.absoluteURL
    }

    static func accountWebURL(entryNonce: Int = 0) -> URL {
        var components = URLComponents(url: accountURL.absoluteURL, resolvingAgainstBaseURL: true)
        if entryNonce > 0 {
            components?.queryItems = [
                URLQueryItem(name: "native", value: "ios"),
                URLQueryItem(name: "entry_nonce", value: String(entryNonce))
            ]
        } else {
            components?.queryItems = [
                URLQueryItem(name: "native", value: "ios")
            ]
        }
        return components?.url ?? accountURL.absoluteURL
    }
}

private enum NativeAnonymousIdentity {
    private static let storageKey = "godinc_anon_id"

    static var currentID: String {
        let defaults = UserDefaults.standard

        if let existing = defaults.string(forKey: storageKey),
           UUID(uuidString: existing) != nil {
            return existing.lowercased()
        }

        let created = UUID().uuidString.lowercased()
        defaults.set(created, forKey: storageKey)
        return created
    }
}

private func normalizedNativeOracleVoice(
    _ value: String?
) -> String? {
    let normalized = (value ?? "")
        .trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        .lowercased()

    switch normalized {
    case "hathor":
        return "Hathor"
    case "moses":
        return "Moses"
    default:
        return nil
    }
}

struct NativeSessionIdentity: Decodable {
    let authenticated: Bool
    let display_name: String?
    let role: String?
    let preferred_oracle: String?
}

private struct NativeOraclePreferencePayload: Encodable {
    let preferred_oracle: String
}

private struct NativeOraclePreferenceResponse: Decodable {
    let preferred_oracle: String?
}

private enum TempleSessionHTTP {
    static func authenticatedRequest(
        url: URL,
        method: String
    ) async -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue(
            "GodIncorporatedIOSApp/1.0",
            forHTTPHeaderField: "User-Agent"
        )
        request.setValue(
            NativeAnonymousIdentity.currentID,
            forHTTPHeaderField: "X-Anonymous-User-Id"
        )

        let cookies = await sharedWebCookies(for: url)

        if !cookies.isEmpty {
            let headers = HTTPCookie.requestHeaderFields(with: cookies)

            if let cookieHeader = headers["Cookie"] {
                request.setValue(
                    cookieHeader,
                    forHTTPHeaderField: "Cookie"
                )
            }
        }

        return request
    }

    static func currentIdentity() async throws -> NativeSessionIdentity {
        let request = await authenticatedRequest(
            url: TempleEnvironment.meURL,
            method: "GET"
        )

        let (data, response) = try await URLSession.shared.data(for: request)

        guard
            let http = response as? HTTPURLResponse,
            (200..<300).contains(http.statusCode)
        else {
            throw URLError(.badServerResponse)
        }

        return try JSONDecoder().decode(
            NativeSessionIdentity.self,
            from: data
        )
    }

    static func updateOraclePreference(
        _ voice: String
    ) async throws -> String {
        guard
            let selected =
                normalizedNativeOracleVoice(voice)
        else {
            throw URLError(.cannotParseResponse)
        }

        var request = await authenticatedRequest(
            url: TempleEnvironment.oraclePreferenceURL,
            method: "PATCH"
        )

        request.setValue(
            "application/json",
            forHTTPHeaderField: "Content-Type"
        )

        request.setValue(
            "application/json",
            forHTTPHeaderField: "Accept"
        )

        request.httpBody = try JSONEncoder().encode(
            NativeOraclePreferencePayload(
                preferred_oracle: selected
            )
        )

        let (data, response) =
            try await URLSession.shared.data(
                for: request
            )

        guard
            let http =
                response as? HTTPURLResponse,
            (200..<300).contains(
                http.statusCode
            )
        else {
            throw URLError(.badServerResponse)
        }

        let result = try JSONDecoder().decode(
            NativeOraclePreferenceResponse.self,
            from: data
        )

        guard
            let stored =
                normalizedNativeOracleVoice(
                    result.preferred_oracle
                )
        else {
            throw URLError(.cannotParseResponse)
        }

        return stored
    }

    private static func sharedWebCookies(
        for url: URL
    ) async -> [HTTPCookie] {
        await withCheckedContinuation { continuation in
            WKWebsiteDataStore.default()
                .httpCookieStore
                .getAllCookies { cookies in

                    guard let host = url.host else {
                        continuation.resume(returning: [])
                        return
                    }

                    let matchingCookies = cookies.filter { cookie in
                        let domain = cookie.domain.trimmingCharacters(
                            in: CharacterSet(charactersIn: ".")
                        )

                        return host == domain ||
                            host.hasSuffix("." + domain)
                    }

                    continuation.resume(
                        returning: matchingCookies
                    )
                }
        }
    }
}

private enum TemplePalette {
    static let midnight = Color(hex: 0x061A2E)
    static let deepBlue = Color(hex: 0x0A3A68)
    static let royalBlue = Color(hex: 0x0D4F8B)
    static let parchment = Color(hex: 0xF4E8D0)
    static let warmGold = Color(hex: 0xD7A84F)
    static let paleGold = Color(hex: 0xF4D58D)
    static let crimson = Color(hex: 0x8E2430)
    static let ink = Color(hex: 0x20170F)

    // Native Sanctuary of Hathor.
    static let malachite = Color(hex: 0x6F8A74)
    static let malachiteDeep = Color(hex: 0x42594A)
    static let alabaster = Color(hex: 0xF1E5CC)

    // Approved sacred-luxe native materials.
    static let polishedGold = Color(hex: 0xE7BB5D)
    static let antiqueGold = Color(hex: 0xB47A2A)
    static let emerald = Color(hex: 0x07533F)
    static let emeraldDeep = Color(hex: 0x03372D)
    static let templeBlack = Color(hex: 0x02070D)
    static let templeNavy = Color(hex: 0x031526)
    static let cloudIvory = Color(hex: 0xF5EBDD)
    static let steel = Color(hex: 0x637180)
    static let steelDeep = Color(hex: 0x344554)
    static let templeCrimson = Color(hex: 0x7D1D28)
}

enum NativeTempleIdentity {
    case hathor
    case moses

    init(oracleVoice: String) {
        self = normalizedNativeOracleVoice(oracleVoice) == "Moses"
            ? .moses
            : .hathor
    }

    var oracleVoice: String {
        switch self {
        case .hathor:
            return "Hathor"
        case .moses:
            return "Moses"
        }
    }

    var title: String {
        switch self {
        case .hathor:
            return "Sanctuary of Hathor"
        case .moses:
            return "Tabernacle of Moses"
        }
    }

    var displayTitle: String {
        switch self {
        case .hathor:
            return "SANCTUARY\nOF HATHOR"
        case .moses:
            return "TABERNACLE\nOF MOSES"
        }
    }

    var destinationOracleVoice: String {
        switch self {
        case .hathor:
            return "Moses"
        case .moses:
            return "Hathor"
        }
    }

    var visitDestinationTitle: String {
        switch self {
        case .hathor:
            return "Visit the Tabernacle of Moses"
        case .moses:
            return "Visit the Sanctuary of Hathor"
        }
    }

    var screenGradientColors: [Color] {
        switch self {
        case .hathor:
            return [
                TemplePalette.templeNavy,
                TemplePalette.midnight,
                TemplePalette.templeBlack
            ]
        case .moses:
            return [
                TemplePalette.templeBlack,
                TemplePalette.midnight,
                Color(hex: 0x111117)
            ]
        }
    }

    var glowColor: Color {
        switch self {
        case .hathor:
            return TemplePalette.paleGold
        case .moses:
            return TemplePalette.warmGold
        }
    }

    var cardFillColor: Color {
        switch self {
        case .hathor:
            return TemplePalette.alabaster
        case .moses:
            return TemplePalette.parchment
        }
    }

    var accentColor: Color {
        switch self {
        case .hathor:
            return TemplePalette.malachiteDeep
        case .moses:
            return TemplePalette.paleGold
        }
    }

    var secondaryButtonTextColor: Color {
        switch self {
        case .hathor:
            return TemplePalette.malachiteDeep
        case .moses:
            return TemplePalette.crimson
        }
    }

    var cardCornerRadius: CGFloat {
        switch self {
        case .hathor:
            return 18
        case .moses:
            return 28
        }
    }


    var oracleArtworkAssetName: String {
        switch self {
        case .hathor:
            return "HathorOracle"
        case .moses:
            return "MosesOracle"
        }
    }

    var oracleArtworkAccessibilityLabel: String {
        switch self {
        case .hathor:
            return "Hathor in the Sanctuary of Hathor"
        case .moses:
            return "Moses in the Tabernacle of Moses"
        }
    }

}

extension Color {
    init(hex: UInt, opacity: Double = 1.0) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xff) / 255.0,
            green: Double((hex >> 8) & 0xff) / 255.0,
            blue: Double(hex & 0xff) / 255.0,
            opacity: opacity
        )
    }
}

struct ContentView: View {
    @AppStorage("lastOracleVoice") private var lastOracleVoice: String = ""
    @AppStorage("preferredInputMode") private var preferredInputMode: String = "voice"
    @State private var selectedTab: Int
    @State private var templeEntryNonce = 0
    @State private var activeOracleVoice = ""
    @State private var templeWebDestination = "temple"
    @State private var nativeSession: NativeSessionIdentity?
    @State private var nativeSessionChecked = false
    @State private var authRefreshNonce = 0
    @State private var pendingExplicitOracleVoice = ""
    @State private var oracleSelectionInProgress = false

    init() {
        _selectedTab = State(
            initialValue: 1
        )
    }

    var body: some View {
        let effectiveOracleVoice = activeOracleVoice.isEmpty
            ? (lastOracleVoice.isEmpty ? "Hathor" : lastOracleVoice)
            : activeOracleVoice

        let establishedOracleVoice =
            normalizedNativeOracleVoice(
                activeOracleVoice
            )
            ?? normalizedNativeOracleVoice(
                lastOracleVoice
            )

        let entryIdentity =
            NativeTempleIdentity(
                oracleVoice:
                    establishedOracleVoice
                    ?? "Hathor"
            )

        TabView(selection: $selectedTab) {
            NativeVoiceSessionView(
                oracleVoice: effectiveOracleVoice,
                onOracleVoiceChange: { voice in
                    await applyExplicitOracleSelection(
                        voice,
                        refreshTempleEntry: true
                    )
                }
            )
            .tabItem {
                Label("Voice", systemImage: "mic")
            }
            .tag(1)

            TempleWebView(
                url: templeWebDestination == "account"
                    ? TempleEnvironment.accountWebURL(entryNonce: templeEntryNonce)
                    : TempleEnvironment.templeURL(
                        voice: lastOracleVoice,
                        entry: preferredInputMode,
                        auth: templeWebDestination == "login" ? "login" : nil,
                        entryNonce: templeEntryNonce
                    ),
                selectedTab: $selectedTab,
                onAuthChanged: {
                    nativeSessionChecked = false
                    authRefreshNonce += 1
                }
            )
            .tabItem {
                Label("Temple", systemImage: "bubble.left.and.bubble.right")
            }
            .tag(2)

            NativeSupportView()
                .tabItem {
                    Label("Support", systemImage: "heart")
                }
                .tag(3)

            NativeInfoView()
                .tabItem {
                    Label("Info", systemImage: "info.circle")
                }
                .tag(4)
        }
        .tint(TemplePalette.polishedGold)
        .toolbarBackground(
            TemplePalette.midnight.opacity(0.98),
            for: .tabBar
        )
        .toolbarBackground(
            .visible,
            for: .tabBar
        )
        .overlay {
            if !nativeSessionChecked {
                NativeEntryResolutionView(
                    identity: entryIdentity
                )
            }
        }
        .task(id: authRefreshNonce) {
            await refreshNativeSessionAndResume()
        }
    }

    @MainActor
    private func refreshNativeSessionAndResume() async {
        do {
            let identity =
                try await TempleSessionHTTP.currentIdentity()

            nativeSession = identity
            nativeSessionChecked = true

            let pendingExplicit =
                normalizedNativeOracleVoice(
                    pendingExplicitOracleVoice
                )

            guard identity.authenticated else {
                pendingExplicitOracleVoice = ""

                if pendingExplicit == nil {
                    selectedTab = 1
                }

                return
            }

            if let pendingExplicit {
                pendingExplicitOracleVoice = ""

                _ = await applyExplicitOracleSelection(
                    pendingExplicit
                )

                templeWebDestination = "temple"
                selectedTab = 1

                // Preserve a destination the seeker already chose
                // while /me was still resolving.
                return
            }

            if
                let serverPreference =
                    normalizedNativeOracleVoice(
                        identity.preferred_oracle
                    )
            {
                lastOracleVoice = serverPreference
                activeOracleVoice = serverPreference
                templeWebDestination = "temple"

                // Authenticated account preference is authoritative
                // across devices and launches.
                selectedTab = 1
                return
            }

            if
                let savedOracle =
                    normalizedNativeOracleVoice(
                        lastOracleVoice
                    )
            {
                // Legacy/local continuity remains usable while the
                // authenticated account has no explicit preference.
                // Do not backfill the database from AppStorage.
                lastOracleVoice = savedOracle
                activeOracleVoice = savedOracle
                templeWebDestination = "temple"
                selectedTab = 1
                return
            }

            // Fresh/no-established-choice presentation remains
            // Hathor-first through effectiveOracleVoice, without
            // manufacturing a durable account preference.
            lastOracleVoice = ""
            activeOracleVoice = ""
            templeWebDestination = "temple"
            selectedTab = 1

        } catch {
            nativeSession = nil
            nativeSessionChecked = true

            print(
                "Native session refresh failed: \(error.localizedDescription)"
            )
        }
    }

    @MainActor
    private func applyExplicitOracleSelection(
        _ voice: String,
        refreshTempleEntry: Bool = false
    ) async -> String {
        let fallback =
            normalizedNativeOracleVoice(
                activeOracleVoice
            )
            ?? normalizedNativeOracleVoice(
                lastOracleVoice
            )
            ?? "Hathor"

        guard
            let selected =
                normalizedNativeOracleVoice(voice)
        else {
            return fallback
        }

        // Serialize explicit choices so rapid taps cannot allow
        // an older PATCH to become the final account preference.
        while oracleSelectionInProgress {
            try? await Task.sleep(
                nanoseconds: 20_000_000
            )
        }

        oracleSelectionInProgress = true

        defer {
            oracleSelectionInProgress = false
        }

        func applyLocal(_ resolved: String) {
            let previous =
                normalizedNativeOracleVoice(
                    activeOracleVoice
                )
                ?? normalizedNativeOracleVoice(
                    lastOracleVoice
                )

            lastOracleVoice = resolved
            activeOracleVoice = resolved

            if
                refreshTempleEntry &&
                previous != resolved
            {
                templeEntryNonce += 1
            }
        }

        // The click itself is genuine authority, even if /me has
        // not resolved yet. Keep it pending so authentication can
        // establish the same account preference once known.
        guard nativeSessionChecked else {
            pendingExplicitOracleVoice = selected
            applyLocal(selected)
            return selected
        }

        guard
            let identity = nativeSession,
            identity.authenticated
        else {
            pendingExplicitOracleVoice = ""
            applyLocal(selected)
            return selected
        }

        let authoritativePreference =
            normalizedNativeOracleVoice(
                identity.preferred_oracle
            )

        // Clicking an already-visible Oracle still establishes the
        // preference when the server currently has NULL.
        if authoritativePreference == selected {
            pendingExplicitOracleVoice = ""
            applyLocal(selected)
            return selected
        }

        applyLocal(selected)

        do {
            let stored =
                try await TempleSessionHTTP
                    .updateOraclePreference(
                        selected
                    )

            nativeSession = NativeSessionIdentity(
                authenticated:
                    identity.authenticated,
                display_name:
                    identity.display_name,
                role:
                    identity.role,
                preferred_oracle:
                    stored
            )

            pendingExplicitOracleVoice = ""
            applyLocal(stored)

            return stored

        } catch {
            print(
                "Native Oracle preference update failed: \(error.localizedDescription)"
            )

            // If the server already had an established preference,
            // retain that authority on a failed write. If it was NULL,
            // preserve the seeker's explicit local choice without
            // pretending the account write succeeded.
            if let authoritativePreference {
                applyLocal(
                    authoritativePreference
                )
                return authoritativePreference
            }

            applyLocal(selected)
            return selected
        }
    }
}

struct NativeVoiceSessionView: View {
    let oracleVoice: String
    let onOracleVoiceChange: (String) async -> String

    @State private var recorder: AVAudioRecorder?
    @State private var recordingURL: URL?
    @State private var isRecording = false
    @State private var isWorking = false
    @State private var statusTitle = "Voice ready"
    @State private var statusMessage = "Have your question ready, then tap Start Conversation. iOS will ask for microphone access the first time."
    @State private var transcript = ""
    @State private var answer = ""
    @State private var recoveryMessage = ""
    @State private var showRecoveryActions = false
    @State private var lastSpokenOracleAnswer = ""
    @State private var speechSynthesizer = AVSpeechSynthesizer()
    @State private var audioPlayer: AVAudioPlayer?
    @State private var isPlayingAudio = false
    @State private var voiceMonitorTask: Task<Void, Never>?
    @State private var recordingStartTime: Date?
    @State private var speechDetectedTime: Date?
    @State private var lastSpeechTime: Date?
    @State private var isAutoSubmittingRecording = false
    @State private var quietTickCount = 0
    @State private var speechCandidateTickCount = 0
    @State private var strongestSpeechPowerDB: Float = -160.0
    @State private var currentSpeechPowerDB: Float = -160.0
    @State private var isContinuousConversationActive = false
    @State private var activePlaybackOrigin: VoicePlaybackOrigin?
    @State private var pendingRearmTask: Task<Void, Never>?
    @State private var voiceSessionGeneration = 0
    @State private var showNativeTextConversation = false

    private enum VoicePlaybackOrigin: Equatable {
        case liveTurn
        case replay
    }

    private enum OracleInputMode: String {
        case text
        case voice
    }

    private struct LiveVoiceOracleResult {
        let answer: String
        let usedPCCStreamingSpeech: Bool
        let remainingSpeechText: String
    }

    private let noSpeechTimeoutSeconds: TimeInterval = 5.0
    private let silenceSubmitSeconds: TimeInterval = 4.0
    private let backupSubmitAfterSpeechSeconds: TimeInterval = 18.0
    private let hardMaxRecordingSeconds: TimeInterval = 24.0
    private let speechPowerThresholdDB: Float = -42.0
    private let requiredSpeechStartTicks = 2
    private let absoluteQuietThresholdDB: Float = -48.0
    private let quietDropFromSpeechDB: Float = 10.0
    private let meterTickSeconds: TimeInterval = 0.25

    // Keep the PCC first-sentence speech machinery available for
    // future latency experiments. Production iOS live turns currently
    // use one provider-TTS render of the completed answer for voice quality.
    private let useProviderTTSForLiveTurns = true

    var body: some View {
        let templeIdentity =
            NativeTempleIdentity(
                oracleVoice: oracleVoice
            )

        NavigationStack {
            TempleScreen(identity: templeIdentity) {
                ScrollView {
                    VStack(spacing: 17) {
                        TempleBrandMark(identity: templeIdentity)
                            .padding(.top, 28)

                        Text("Welcome to your Temple.")
                            .font(
                                .system(
                                    size: 16,
                                    weight: .medium,
                                    design: .serif
                                )
                            )
                            .tracking(0.6)
                            .foregroundStyle(
                                TemplePalette.polishedGold
                            )

                        TempleOrnamentDivider()

                        Text(templeIdentity.displayTitle)
                            .font(
                                .system(
                                    size: 37,
                                    weight: .medium,
                                    design: .serif
                                )
                            )
                            .tracking(1.6)
                            .lineSpacing(1)
                            .foregroundStyle(
                                TemplePalette.polishedGold
                            )
                            .multilineTextAlignment(.center)
                            .minimumScaleFactor(0.76)

                        NativeOracleArtwork(
                            identity: templeIdentity
                        )

                        NativeSacredIdentityLine(
                            identity: templeIdentity
                        )

                        nativeTempleModeActions

                        oracleVoiceSwitcher

                        if shouldShowConversationChamber {
                            TempleConversationChamber(
                                identity: templeIdentity
                            ) {
                                VStack(spacing: 14) {
                                    Text(statusTitle)
                                        .font(
                                            .headline.weight(.semibold)
                                        )
                                        .foregroundStyle(
                                            TemplePalette.paleGold
                                        )
                                        .multilineTextAlignment(.center)

                                    Text(statusMessage)
                                        .font(.subheadline)
                                        .foregroundStyle(
                                            .white.opacity(0.78)
                                        )
                                        .multilineTextAlignment(.center)

                                    if isWorking && !isRecording {
                                        ProgressView()
                                            .tint(
                                                TemplePalette.polishedGold
                                            )
                                    }

                                    if !transcript.isEmpty
                                        || !answer.isEmpty
                                        || !recoveryMessage.isEmpty {

                                        ScrollView {
                                            VStack(
                                                alignment: .leading,
                                                spacing: 12
                                            ) {
                                                if !transcript.isEmpty {
                                                    VStack(
                                                        alignment: .leading,
                                                        spacing: 5
                                                    ) {
                                                        Text("You said")
                                                            .font(
                                                                .caption.weight(.bold)
                                                            )
                                                            .foregroundStyle(
                                                                TemplePalette.polishedGold
                                                            )

                                                        Text(
                                                            compactVoiceMessage(
                                                                transcript,
                                                                limit: 700
                                                            )
                                                        )
                                                        .foregroundStyle(
                                                            .white.opacity(0.90)
                                                        )
                                                    }
                                                }

                                                if !answer.isEmpty {
                                                    VStack(
                                                        alignment: .leading,
                                                        spacing: 5
                                                    ) {
                                                        Text(
                                                            "\(oracleVoice) answered"
                                                        )
                                                        .font(
                                                            .caption.weight(.bold)
                                                        )
                                                        .foregroundStyle(
                                                            TemplePalette.polishedGold
                                                        )

                                                        Text(answer)
                                                            .foregroundStyle(
                                                                TemplePalette.cloudIvory
                                                            )
                                                    }
                                                }

                                                if !recoveryMessage.isEmpty {
                                                    VStack(
                                                        alignment: .leading,
                                                        spacing: 5
                                                    ) {
                                                        Text("What happened")
                                                            .font(
                                                                .caption.weight(.bold)
                                                            )
                                                            .foregroundStyle(
                                                                TemplePalette.polishedGold
                                                            )

                                                        Text(
                                                            compactVoiceMessage(
                                                                recoveryMessage,
                                                                limit: 700
                                                            )
                                                        )
                                                        .foregroundStyle(
                                                            .white.opacity(0.78)
                                                        )
                                                    }
                                                }
                                            }
                                            .frame(
                                                maxWidth: .infinity,
                                                alignment: .leading
                                            )
                                        }
                                        .frame(maxHeight: 240)
                                        .padding(11)
                                        .background(
                                            RoundedRectangle(
                                                cornerRadius: 11
                                            )
                                            .fill(
                                                Color.black.opacity(0.22)
                                            )
                                        )
                                    }

                                    if !lastSpokenOracleAnswer.isEmpty {
                                        Button {
                                            Task {
                                                await speakOracleAnswerProviderFirst(
                                                    lastSpokenOracleAnswer,
                                                    deity: oracleVoice,
                                                    origin: .replay
                                                )
                                            }
                                        } label: {
                                            Text(
                                                isPlayingAudio
                                                    ? "Oracle Voice Speaking..."
                                                    : "Replay Oracle Voice"
                                            )
                                            .frame(maxWidth: .infinity)
                                        }
                                        .buttonStyle(
                                            TempleChamberActionStyle(
                                                identity: templeIdentity,
                                                emphasized: false
                                            )
                                        )
                                        .disabled(
                                            isPlayingAudio
                                                || (isWorking && !isRecording)
                                        )
                                    }

                                    if isContinuousConversationActive {
                                        Button {
                                            endContinuousConversation()
                                        } label: {
                                            Text("End Conversation")
                                                .frame(maxWidth: .infinity)
                                        }
                                        .buttonStyle(
                                            TempleChamberActionStyle(
                                                identity: templeIdentity,
                                                emphasized: false
                                            )
                                        )
                                    }

                                    if showRecoveryActions {
                                        Button {
                                            isContinuousConversationActive = true

                                            Task {
                                                await startRecording()
                                            }
                                        } label: {
                                            Text("Try Voice Again")
                                                .frame(maxWidth: .infinity)
                                        }
                                        .buttonStyle(
                                            TempleChamberActionStyle(
                                                identity: templeIdentity,
                                                emphasized: true
                                            )
                                        )
                                        .disabled(
                                            isWorking || isRecording
                                        )

                                        Button {
                                            resetVoiceSession()
                                        } label: {
                                            Text("Reset Voice Session")
                                                .frame(maxWidth: .infinity)
                                        }
                                        .buttonStyle(
                                            TempleChamberActionStyle(
                                                identity: templeIdentity,
                                                emphasized: false
                                            )
                                        )
                                        .disabled(
                                            isWorking || isRecording
                                        )

                                        Button {
                                            stopVoiceSessionActivity(
                                                clearExchange: false
                                            )
                                            showNativeTextConversation = true
                                        } label: {
                                            Text("Switch to Text Entry")
                                                .frame(maxWidth: .infinity)
                                        }
                                        .buttonStyle(
                                            TempleChamberActionStyle(
                                                identity: templeIdentity,
                                                emphasized: false
                                            )
                                        )
                                        .disabled(
                                            isWorking || isRecording
                                        )

                                    } else if isRecording {
                                        listeningIndicator

                                        Button {
                                            Task {
                                                await stopAndSubmitRecording()
                                            }
                                        } label: {
                                            Text(
                                                "Stop and Consult the Oracle"
                                            )
                                            .frame(maxWidth: .infinity)
                                        }
                                        .buttonStyle(
                                            TempleChamberActionStyle(
                                                identity: templeIdentity,
                                                emphasized: true
                                            )
                                        )
                                        .disabled(isWorking)
                                    }
                                }
                            }
                        }

                    }
                    .padding(.horizontal, 18)
                }
            }
            .toolbar(
                .hidden,
                for: .navigationBar
            )
        }
        .fullScreenCover(
            isPresented: $showNativeTextConversation
        ) {
            NativeTempleTextConversationView(
                identity: templeIdentity,
                onSubmit: { question in
                    try await askOracle(
                        question: question,
                        voice: oracleVoice,
                        inputMode: .text
                    )
                },
                onClose: {
                    showNativeTextConversation = false
                }
            )
        }
        .onDisappear {
            stopVoiceSessionActivity(clearExchange: false)
        }
    }

    private var shouldShowConversationChamber: Bool {
        isRecording
            || isWorking
            || isPlayingAudio
            || isContinuousConversationActive
            || showRecoveryActions
            || !transcript.isEmpty
            || !answer.isEmpty
            || !recoveryMessage.isEmpty
            || !lastSpokenOracleAnswer.isEmpty
    }

    private var nativeTempleModeActions: some View {
        let templeIdentity =
            NativeTempleIdentity(
                oracleVoice: oracleVoice
            )

        return HStack(spacing: 10) {
            Button {
                guard
                    !isWorking,
                    !isRecording,
                    !isPlayingAudio,
                    !isContinuousConversationActive
                else {
                    return
                }

                isContinuousConversationActive = true

                Task {
                    await startRecording()
                }
            } label: {
                TempleModeTile(
                    identity: templeIdentity,
                    kind: .voice
                )
            }
            .buttonStyle(.plain)
            .disabled(
                isWorking
                    || isRecording
                    || isPlayingAudio
                    || isContinuousConversationActive
            )

            Button {
                stopVoiceSessionActivity(
                    clearExchange: false
                )
                showNativeTextConversation = true
            } label: {
                TempleModeTile(
                    identity: templeIdentity,
                    kind: .text
                )
            }
            .buttonStyle(.plain)
            .disabled(
                isWorking
                    || isRecording
            )
        }
        .frame(maxWidth: 318)
    }

    private var oracleVoiceSwitcher: some View {
        let templeIdentity =
            NativeTempleIdentity(
                oracleVoice: oracleVoice
            )

        let visitWidth: CGFloat

        switch templeIdentity {
        case .hathor:
            visitWidth = 326

        case .moses:
            visitWidth = 306
        }

        return Button {
            Task {
                await changeOracleVoice(
                    to:
                        templeIdentity
                            .destinationOracleVoice
                )
            }
        } label: {
            HStack(spacing: 10) {
                TempleVisitIcon(
                    identity: templeIdentity
                )

                Text(
                    templeIdentity
                        .visitDestinationTitle
                        .uppercased()
                )
                .font(
                    .system(
                        size: 14,
                        weight: .semibold,
                        design: .serif
                    )
                )
                .tracking(0.55)
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .minimumScaleFactor(0.72)
                .frame(maxWidth: .infinity)

                Image(systemName: "chevron.right")
                    .font(
                        .system(
                            size: 15,
                            weight: .semibold
                        )
                    )
            }
            .frame(maxWidth: .infinity)
        }
        .frame(maxWidth: visitWidth)
        .buttonStyle(
            TempleVisitButtonStyle(
                identity: templeIdentity
            )
        )
        .disabled(
            isWorking
                || isRecording
                || isPlayingAudio
        )
        .opacity(
            (
                isWorking
                    || isRecording
                    || isPlayingAudio
            )
                ? 0.52
                : 1.0
        )
    }

    @MainActor
    private func changeOracleVoice(
        to voice: String
    ) async {
        guard
            let selectedVoice =
                normalizedNativeOracleVoice(
                    voice
                )
        else {
            return
        }

        let currentVoice =
            normalizedNativeOracleVoice(
                oracleVoice
            ) ?? "Hathor"

        let isActualSwitch =
            selectedVoice != currentVoice

        if isActualSwitch {
            // Real Oracle changes must terminate the old native
            // voice/session authority before the new Oracle is applied.
            stopVoiceSessionActivity(
                clearExchange: true
            )
        }

        let resolvedVoice =
            await onOracleVoiceChange(
                selectedVoice
            )

        let displayedVoice =
            normalizedNativeOracleVoice(
                resolvedVoice
            ) ?? currentVoice

        recoveryMessage = ""
        showRecoveryActions = false
        statusTitle = "Voice ready"
        statusMessage =
            "\(NativeTempleIdentity(oracleVoice: displayedVoice).title) is ready. Have your question ready, then tap Start Conversation."
    }

    private var currentListeningMeterLevel: CGFloat {
        let clippedPower = min(max(Double(currentSpeechPowerDB), -60.0), -20.0)
        return CGFloat((clippedPower + 60.0) / 40.0)
    }

    private var listeningIndicator: some View {
        VStack(spacing: 10) {
            ZStack {
                Circle()
                    .fill(TemplePalette.warmGold.opacity(0.24))
                    .frame(width: 78, height: 78)

                Circle()
                    .stroke(TemplePalette.warmGold.opacity(0.72), lineWidth: 2)
                    .frame(width: 78, height: 78)
                    .scaleEffect(isRecording ? 1.16 : 1.0)
                    .opacity(isRecording ? 0.35 : 0.18)
                    .animation(
                        isRecording
                            ? .easeInOut(duration: 0.9).repeatForever(autoreverses: true)
                            : .default,
                        value: isRecording
                    )

                Image(systemName: "mic.fill")
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundStyle(TemplePalette.warmGold)
            }

            Text(speechDetectedTime == nil ? "Listening for your question" : "Voice detected")
                .font(.headline)
                .foregroundStyle(TemplePalette.paleGold)

            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(TemplePalette.midnight.opacity(0.72))

                    Capsule()
                        .fill(TemplePalette.warmGold)
                        .frame(width: max(10, geometry.size.width * currentListeningMeterLevel))
                }
            }
            .frame(height: 8)

            Text("Mic is open. Speak naturally, then pause.")
                .font(.caption)
                .foregroundStyle(.white.opacity(0.72))
                .multilineTextAlignment(.center)
        }
        .padding(16)
        .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(TemplePalette.warmGold.opacity(0.42), lineWidth: 1)
        )
    }

    private func invalidateContinuousConversation() {
        isContinuousConversationActive = false
        pendingRearmTask?.cancel()
        pendingRearmTask = nil
        voiceSessionGeneration += 1
        activePlaybackOrigin = nil
    }

    private func stopVoiceSessionActivity(clearExchange: Bool) {
        invalidateContinuousConversation()
        stopVoiceEndpointMonitor()

        recorder?.stop()
        recorder = nil
        recordingURL = nil

        speechSynthesizer.stopSpeaking(at: .immediate)
        audioPlayer?.stop()
        audioPlayer = nil

        isRecording = false
        isWorking = false
        isPlayingAudio = false
        isAutoSubmittingRecording = false

        recordingStartTime = nil
        speechDetectedTime = nil
        lastSpeechTime = nil
        quietTickCount = 0
        speechCandidateTickCount = 0
        strongestSpeechPowerDB = -160.0
        currentSpeechPowerDB = -160.0

        if clearExchange {
            transcript = ""
            answer = ""
            lastSpokenOracleAnswer = ""
        }
    }

    private func endContinuousConversation() {
        stopVoiceSessionActivity(clearExchange: false)
        recoveryMessage = ""
        showRecoveryActions = false
        statusTitle = "Conversation ended"
        statusMessage = "The microphone is off. Your most recent exchange remains available."
    }

    private func resetVoiceSession() {
        stopVoiceSessionActivity(clearExchange: true)
        recoveryMessage = ""
        showRecoveryActions = false
        statusTitle = "Voice ready"
        statusMessage = "Have your question ready, then tap Start Conversation. iOS will ask for microphone access the first time."
    }

    private func requestMicrophonePermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    private func startRecording(
        preserveCurrentExchange: Bool = false,
        expectedGeneration: Int? = nil
    ) async {
        let generation: Int? = await MainActor.run {
            if let expectedGeneration,
               expectedGeneration != voiceSessionGeneration {
                return nil
            }

            guard !isRecording, !isWorking, !isPlayingAudio else {
                return nil
            }

            voiceSessionGeneration += 1
            let generation = voiceSessionGeneration

            isWorking = true
            speechSynthesizer.stopSpeaking(at: .immediate)
            audioPlayer?.stop()
            audioPlayer = nil
            activePlaybackOrigin = nil
            isPlayingAudio = false
            recoveryMessage = ""
            showRecoveryActions = false

            if !preserveCurrentExchange {
                lastSpokenOracleAnswer = ""
                transcript = ""
                answer = ""
            }

            statusTitle = "Preparing microphone"
            statusMessage = "iOS may ask for permission. The Temple listens only while recording is active."

            return generation
        }

        guard let generation else {
            return
        }

        let granted = await requestMicrophonePermission()

        let isCurrentGeneration = await MainActor.run {
            generation == voiceSessionGeneration
        }

        guard isCurrentGeneration else {
            return
        }

        guard granted else {
            await MainActor.run {
                guard generation == voiceSessionGeneration else {
                    return
                }

                invalidateContinuousConversation()
                isWorking = false
                showRecoveryActions = true
                statusTitle = "Microphone access needed"
                statusMessage = "Allow microphone access in iOS Settings, or switch to text entry."
                recoveryMessage = "The app cannot hear your question until microphone access is allowed."
            }
            return
        }

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker, .allowBluetoothHFP])
            try session.setActive(true)

            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("godinc_voice_\(UUID().uuidString).m4a")

            let settings: [String: Any] = [
                AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
                AVSampleRateKey: 16000,
                AVNumberOfChannelsKey: 1,
                AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue
            ]

            let newRecorder = try AVAudioRecorder(url: url, settings: settings)
            newRecorder.isMeteringEnabled = true
            newRecorder.prepareToRecord()
            newRecorder.record()

            await MainActor.run {
                guard generation == voiceSessionGeneration else {
                    newRecorder.stop()
                    return
                }

                recorder = newRecorder
                recordingURL = url
                isRecording = true
                isWorking = false
                statusTitle = "Listening"
                statusMessage = "Speak naturally. Pause when you are finished; the app will consult the Oracle automatically, or you can tap Stop."
                startVoiceEndpointMonitor()
            }
        } catch {
            await MainActor.run {
                guard generation == voiceSessionGeneration else {
                    return
                }

                invalidateContinuousConversation()
                isRecording = false
                isWorking = false
                showRecoveryActions = true
                statusTitle = "Microphone could not start"
                statusMessage = "The microphone could not be opened."
                recoveryMessage = userFacingVoiceError(error)
            }
        }
    }

    private func stopAndSubmitRecording() async {
        let generation: Int? = await MainActor.run {
            guard !isWorking else {
                return nil
            }

            stopVoiceEndpointMonitor()
            isWorking = true
            statusTitle = "Preparing your question"
            statusMessage = "Native iOS speech recognition is transcribing your recording."

            return voiceSessionGeneration
        }

        guard let generation else {
            return
        }

        recorder?.stop()
        let url = recordingURL

        await MainActor.run {
            guard generation == voiceSessionGeneration else {
                return
            }

            isRecording = false
            recorder = nil
        }

        guard let url else {
            await MainActor.run {
                guard generation == voiceSessionGeneration else {
                    return
                }

                invalidateContinuousConversation()
                isWorking = false
                showRecoveryActions = true
                statusTitle = "No recording found"
                statusMessage = "Please try recording again."
                recoveryMessage = "No audio file was created. Tap Try Voice Again, or switch to text entry."
            }
            return
        }

        do {
            let spokenQuestion = try await transcribeRecordingNativeFirst(
                at: url,
                voice: oracleVoice
            )

            let transcriptionIsCurrent = await MainActor.run {
                generation == voiceSessionGeneration
            }

            guard transcriptionIsCurrent else {
                return
            }

            if isLikelyNoSpeechTranscript(spokenQuestion) {
                throw TempleVoiceError.server(
                    "No clear spoken question was detected."
                )
            }

            await MainActor.run {
                transcript = spokenQuestion
                statusTitle = "Consulting the Oracle"
                statusMessage = "Your spoken question has been heard. \(oracleVoice) is answering."
            }

            let voiceResult = try await askOracleForLiveVoice(
                question: spokenQuestion,
                voice: oracleVoice,
                generation: generation
            )

            let oracleAnswer = voiceResult.answer

            let inferenceIsCurrent = await MainActor.run {
                generation == voiceSessionGeneration
            }

            guard inferenceIsCurrent else {
                return
            }

            let answerIsCurrent = await MainActor.run {
                guard generation == voiceSessionGeneration else {
                    return false
                }

                answer = oracleAnswer
                lastSpokenOracleAnswer = oracleAnswer
                isWorking = false
                showRecoveryActions = false
                recoveryMessage = ""
                statusTitle = "Oracle speaking"

                if voiceResult.usedPCCStreamingSpeech {
                    statusMessage = "The full written answer is ready. Apple voice is continuing the response."
                } else {
                    statusMessage = "The written answer is ready. Provider voice is being prepared."
                }

                return true
            }

            guard answerIsCurrent else {
                return
            }

            if voiceResult.usedPCCStreamingSpeech {
                await finishPCCStreamingVoice(
                    remainingText: voiceResult.remainingSpeechText,
                    deity: oracleVoice,
                    generation: generation
                )
            } else {
                await speakOracleAnswerProviderFirst(
                    oracleAnswer,
                    deity: oracleVoice,
                    origin: .liveTurn,
                    expectedGeneration: generation
                )
            }
        } catch {
            await MainActor.run {
                guard generation == voiceSessionGeneration else {
                    return
                }

                invalidateContinuousConversation()
                isWorking = false
                showRecoveryActions = true

                let friendly = classifyVoiceFailure(error)
                statusTitle = friendly.title
                statusMessage = friendly.status
                recoveryMessage = friendly.recovery
            }
        }
    }

    private func startVoiceEndpointMonitor() {
        stopVoiceEndpointMonitor()

        recordingStartTime = Date()
        speechDetectedTime = nil
        lastSpeechTime = nil
        quietTickCount = 0
        speechCandidateTickCount = 0
        strongestSpeechPowerDB = -160.0
        currentSpeechPowerDB = -160.0
        isAutoSubmittingRecording = false

        voiceMonitorTask = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(meterTickSeconds * 1_000_000_000))
                await MainActor.run {
                    monitorVoiceEndpointing()
                }
            }
        }
    }

    private func stopVoiceEndpointMonitor() {
        voiceMonitorTask?.cancel()
        voiceMonitorTask = nil
    }

    private func monitorVoiceEndpointing() {
        guard isRecording, !isWorking, let activeRecorder = recorder, let started = recordingStartTime else {
            stopVoiceEndpointMonitor()
            return
        }

        activeRecorder.updateMeters()

        let now = Date()
        let elapsed = now.timeIntervalSince(started)
        let averagePower = activeRecorder.averagePower(forChannel: 0)
        currentSpeechPowerDB = averagePower.isFinite ? averagePower : -160.0
        let peakPower = activeRecorder.peakPower(forChannel: 0)
        let absoluteSpeechIsPresent = averagePower > speechPowerThresholdDB || peakPower > (speechPowerThresholdDB + 8.0)

        if absoluteSpeechIsPresent {
            strongestSpeechPowerDB = max(strongestSpeechPowerDB, averagePower)
        }

        let relativeQuietThreshold = max(absoluteQuietThresholdDB, strongestSpeechPowerDB - quietDropFromSpeechDB)
        let quietIsPresent = speechDetectedTime != nil && averagePower <= relativeQuietThreshold


        if speechDetectedTime == nil {
            if absoluteSpeechIsPresent {
                speechCandidateTickCount += 1
                quietTickCount = 0

                if speechCandidateTickCount >= requiredSpeechStartTicks {
                    speechDetectedTime = now
                    lastSpeechTime = now
                    statusMessage = "I hear you. Keep speaking naturally, then pause when you are finished."
                }
            } else {
                speechCandidateTickCount = 0
            }
        } else if absoluteSpeechIsPresent && !quietIsPresent {
            speechCandidateTickCount = requiredSpeechStartTicks
            quietTickCount = 0
            lastSpeechTime = now
        } else if quietIsPresent {
            quietTickCount += 1
        }

        if elapsed >= hardMaxRecordingSeconds {
            if speechDetectedTime == nil {
                stopRecordingWithoutSubmit(
                    title: "No clear question heard",
                    status: "The recording limit was reached before a clear question was detected.",
                    recovery: "Tap Try Voice Again and speak clearly after the listening state appears, or switch to text entry."
                )
            } else {
                autoSubmitRecording(
                    title: "Recording limit reached",
                    message: "The recording limit was reached. Your question is being sent to the Oracle."
                )
            }
            return
        }

        if speechDetectedTime == nil && elapsed >= noSpeechTimeoutSeconds {
            stopRecordingWithoutSubmit(
                title: "No clear question heard",
                status: "The Temple did not detect speech.",
                recovery: "Tap Try Voice Again and speak clearly after the listening state appears, or switch to text entry."
            )
            return
        }

        if let firstSpeechTime = speechDetectedTime, let lastSpeechTime {
            let silenceDuration = now.timeIntervalSince(lastSpeechTime)
            let speechWindowDuration = now.timeIntervalSince(firstSpeechTime)
            let requiredQuietTicks = max(1, Int(silenceSubmitSeconds / meterTickSeconds))

            if silenceDuration >= silenceSubmitSeconds && quietTickCount >= requiredQuietTicks {
                autoSubmitRecording(
                    title: "Question heard",
                    message: "Your pause was detected. The recording is being sent to the Oracle."
                )
                return
            }

            if speechWindowDuration >= backupSubmitAfterSpeechSeconds
                && silenceDuration >= silenceSubmitSeconds {
                autoSubmitRecording(
                    title: "Question captured",
                    message: "Your spoken question is being sent to the Oracle."
                )
                return
            }
        }
    }

    private func autoSubmitRecording(title: String, message: String) {
        guard isRecording, !isWorking, !isAutoSubmittingRecording else {
            return
        }

        isAutoSubmittingRecording = true
        statusTitle = title
        statusMessage = message

        Task {
            await stopAndSubmitRecording()
        }
    }

    private func stopRecordingWithoutSubmit(
        title: String,
        status: String,
        recovery: String
    ) {
        guard !isAutoSubmittingRecording else {
            return
        }

        invalidateContinuousConversation()

        isAutoSubmittingRecording = true
        stopVoiceEndpointMonitor()

        recorder?.stop()
        recorder = nil
        recordingURL = nil

        isRecording = false
        isWorking = false
        isAutoSubmittingRecording = false

        quietTickCount = 0
        speechCandidateTickCount = 0
        strongestSpeechPowerDB = -160.0
        currentSpeechPowerDB = -160.0

        statusTitle = title
        statusMessage = status
        recoveryMessage = recovery
        showRecoveryActions = true
    }

    private func classifyVoiceFailure(_ error: Error) -> (title: String, status: String, recovery: String) {
        let raw = error.localizedDescription.trimmingCharacters(in: .whitespacesAndNewlines)
        let lower = raw.lowercased()

        if lower.contains("no transcript")
            || lower.contains("no clear spoken question")
            || lower.contains("whisper")
            || lower.contains("could not transcribe")
            || lower.contains("transcription failed") {
            return (
                "No clear question heard",
                "The Temple could not detect a clear spoken question.",
                "Have your question ready, tap Try Voice Again, speak clearly, then tap Stop and Consult the Oracle. You can also switch to text entry."
            )
        }

        if lower.contains("timed out")
            || lower.contains("network")
            || lower.contains("offline")
            || lower.contains("lost connection") {
            return (
                "Connection needs attention",
                "The voice request could not complete.",
                "Check the connection and tap Try Voice Again, or switch to text entry."
            )
        }

        if lower.contains("microphone") || lower.contains("permission") {
            return (
                "Microphone needs attention",
                "The app could not use the microphone.",
                "Allow microphone access in iOS Settings, then tap Try Voice Again. You can also switch to text entry."
            )
        }

        return (
            "Voice request needs attention",
            raw.isEmpty ? "The voice request could not complete." : compactVoiceMessage(raw),
            "Tap Try Voice Again, reset the voice session, or switch to text entry."
        )
    }

    private func userFacingVoiceError(_ error: Error) -> String {
        classifyVoiceFailure(error).recovery
    }

    private func compactVoiceMessage(_ value: String, limit: Int = 420) -> String {
        let cleaned = value
            .replacingOccurrences(of: "\\n", with: " ")
            .replacingOccurrences(of: "\n", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)

        if cleaned.count <= limit {
            return cleaned
        }

        let index = cleaned.index(cleaned.startIndex, offsetBy: limit)
        return String(cleaned[..<index]) + "…"
    }

    private func isLikelyNoSpeechTranscript(_ value: String) -> Bool {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            return true
        }

        let lower = trimmed.lowercased()
        let letterCount = lower.filter { $0.isLetter }.count
        let digitCount = lower.filter { $0.isNumber }.count
        let percentCount = lower.filter { $0 == "%" }.count
        let words = lower
            .split { !$0.isLetter && !$0.isNumber }
            .map(String.init)

        if letterCount < 3 && digitCount > 0 {
            return true
        }

        if percentCount > 0 && letterCount < 5 {
            return true
        }

        if words.count <= 2 && trimmed.count < 8 {
            return true
        }

        let repeatedNoiseTokens = ["1.5", "1.5%", "%"]
        let noiseHits = repeatedNoiseTokens.reduce(0) { count, token in
            count + lower.components(separatedBy: token).count - 1
        }

        if noiseHits >= 2 && letterCount < 10 {
            return true
        }

        return false
    }

    private func requestSpeechRecognitionPermission() async -> Bool {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
    }

    private func normalizeSelectedOracleInvocation(
        _ transcript: String,
        voice: String
    ) -> String {
        let selectedOracle = voice
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()

        let pattern: String

        switch selectedOracle {
        case "hathor":
            // The GUI has already selected Hathor. Correct only the observed
            // opening invocation forms; do not rewrite Arthur elsewhere.
            pattern = #"(?i)^\s*(?:(?:hello|hi|hey)\s*[,!.\-]?\s+)?(?:hathor|heather|arthur|hazard|hatter)\s*[,!?.:\-]?\s*"#

        case "moses":
            // Moses is already authoritative when selected in the GUI.
            // No speculative recognition aliases are added until observed.
            pattern = #"(?i)^\s*(?:(?:hello|hi|hey)\s*[,!.\-]?\s+)?moses\s*[,!?.:\-]?\s*"#

        default:
            return transcript
        }

        guard let regex = try? NSRegularExpression(pattern: pattern) else {
            return transcript
        }

        let fullRange = NSRange(
            transcript.startIndex..<transcript.endIndex,
            in: transcript
        )

        let normalized = regex.stringByReplacingMatches(
            in: transcript,
            range: fullRange,
            withTemplate: ""
        )
        .trimmingCharacters(in: .whitespacesAndNewlines)

        // Never turn a valid utterance into an empty question.
        return normalized.isEmpty ? transcript : normalized
    }

    private func transcribeRecordingNativeFirst(at url: URL, voice: String) async throws -> String {
        // If our own recorder meter never detected speech, do not trust Apple Speech
        // or backend transcription. Empty-room audio can produce junk tokens.
        guard speechDetectedTime != nil else {
            throw TempleVoiceError.server("No clear spoken question was detected.")
        }

        do {
            let nativeTranscript = try await transcribeRecordingWithAppleSpeech(
                at: url,
                voice: voice
            )
            let trimmed = nativeTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty && !isLikelyNoSpeechTranscript(trimmed) {
                return normalizeSelectedOracleInvocation(
                    trimmed,
                    voice: voice
                )
            }
        } catch {
            // Keep the existing backend transcription path as a fallback when native
            // recognition is unavailable, denied, or returns no useful transcript.
        }

        let backendTranscript = try await transcribeRecording(at: url, voice: voice)
            .trimmingCharacters(in: .whitespacesAndNewlines)

        guard !backendTranscript.isEmpty && !isLikelyNoSpeechTranscript(backendTranscript) else {
            throw TempleVoiceError.server("No clear spoken question was detected.")
        }

        return normalizeSelectedOracleInvocation(
            backendTranscript,
            voice: voice
        )
    }

    private func transcribeRecordingWithAppleSpeech(
        at url: URL,
        voice: String
    ) async throws -> String {
        guard let recognizer = SFSpeechRecognizer() else {
            throw TempleVoiceError.server("Native speech recognition was unavailable.")
        }

        guard recognizer.isAvailable else {
            throw TempleVoiceError.server("Native speech recognition was not available.")
        }

        guard recognizer.supportsOnDeviceRecognition else {
            throw TempleVoiceError.server("On-device speech recognition was not available.")
        }

        let speechAllowed = await requestSpeechRecognitionPermission()
        guard speechAllowed else {
            throw TempleVoiceError.server("Speech recognition access was not allowed.")
        }

        let request = SFSpeechURLRecognitionRequest(url: url)

        var contextualVocabulary = [
            "Hathor",
            "Moses",
            "God Incorporated",
            "Essene",
            "Essenes",
            "Nag Hammadi",
            "Nag Hammadi texts",
            "Nag Hammadi library",
            "Gnostic",
            "Gnosticism",
            "Coptic",
            "Qumran",
            "Dead Sea Scrolls",
            "Gospel of Thomas",
            "Gospel of Mary",
            "Apocryphon of John",
            "Valentinus",
            "Valentinian",
            "Sethian",
            "Sophia",
            "Pleroma",
            "Demiurge",
            "Yaldabaoth",
            "Hermetic",
            "Hermetica",
            "Rosicrucian",
            "Templar"
        ]

        let selectedOracle = voice
            .trimmingCharacters(in: .whitespacesAndNewlines)

        if !selectedOracle.isEmpty &&
            !contextualVocabulary.contains(selectedOracle) {
            contextualVocabulary.insert(selectedOracle, at: 0)
        }

        request.contextualStrings = contextualVocabulary
        request.taskHint = .dictation
        request.shouldReportPartialResults = false
        request.requiresOnDeviceRecognition = true

        return try await withCheckedThrowingContinuation { continuation in
            var didResume = false

            let _ = recognizer.recognitionTask(with: request) { result, error in
                if didResume {
                    return
                }

                if let result, result.isFinal {
                    let transcript = result.bestTranscription.formattedString
                        .trimmingCharacters(in: .whitespacesAndNewlines)

                    didResume = true
                    if transcript.isEmpty {
                        continuation.resume(throwing: TempleVoiceError.server("Native speech recognition returned no transcript."))
                    } else {
                        continuation.resume(returning: transcript)
                    }
                    return
                }

                if let error {
                    didResume = true
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    private func transcribeRecording(at url: URL, voice: String) async throws -> String {
        let boundary = "Boundary-\(UUID().uuidString)"
        var request = await authenticatedVoiceRequest(url: TempleEnvironment.voiceTranscribeURL, method: "POST")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        let audioData = try Data(contentsOf: url)
        if audioData.count < 1024 {
            throw TempleVoiceError.server("No transcript was returned.")
        }

        var body = Data()

        body.appendMultipartText(name: "voice", value: voice, boundary: boundary)
        body.appendMultipartFile(
            name: "file",
            filename: "voice_input.m4a",
            mimeType: "audio/mp4",
            data: audioData,
            boundary: boundary
        )
        body.appendString("--\(boundary)--\r\n")

        let (data, response) = try await URLSession.shared.upload(for: request, from: body)
        try validateHTTP(response: response, data: data)

        let decoded = try JSONDecoder().decode(VoiceTranscribeResponse.self, from: data)
        if let error = decoded.error, !error.isEmpty {
            throw TempleVoiceError.server(error)
        }

        let result = (decoded.transcript ?? decoded.question ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if result.isEmpty {
            throw TempleVoiceError.server("No transcript was returned.")
        }

        return result
    }

    private func prepareOracleInference(
        question: String,
        voice: String,
        inputMode: OracleInputMode
    ) async throws -> PreparedOracleInferencePacket {
        let nativeAnonymousUserID = NativeAnonymousIdentity.currentID

        var request = await authenticatedVoiceRequest(
            url: TempleEnvironment.oracleInferencePrepareURL,
            method: "POST"
        )
        request.setValue(
            "application/json",
            forHTTPHeaderField: "Content-Type"
        )

        request.httpBody = try JSONEncoder().encode(
            OracleInferencePreparePayload(
                question: question,
                deity: voice,
                seeker_id: nil,
                anonymous_user_id: nativeAnonymousUserID,
                input_mode: inputMode.rawValue,
                execution_target: "apple_pcc"
            )
        )

        let (data, response) = try await URLSession.shared.data(for: request)
        try validateHTTP(response: response, data: data)

        let decoded = try JSONDecoder().decode(
            OracleInferencePrepareResponse.self,
            from: data
        )

        if let error = decoded.error, !error.isEmpty {
            throw TempleVoiceError.server(error)
        }

        guard decoded.status == "prepared",
              let interactionID = decoded.interaction_id,
              !interactionID.isEmpty,
              let deity = decoded.deity,
              !deity.isEmpty,
              let systemPrompt = decoded.system_prompt,
              !systemPrompt.isEmpty,
              let preparedQuestion = decoded.question,
              !preparedQuestion.isEmpty else {
            throw TempleVoiceError.server(
                "Oracle inference preparation returned an incomplete packet."
            )
        }

        return PreparedOracleInferencePacket(
            interaction_id: interactionID,
            deity: deity,
            system_prompt: systemPrompt,
            memory_block: decoded.memory_block ?? "",
            question: preparedQuestion,
            max_output_tokens: decoded.max_output_tokens
        )
    }

    private func abandonOracleInference(
        interactionID: String,
        fallbackCode: String
    ) async throws {
        var request = await authenticatedVoiceRequest(
            url: TempleEnvironment.oracleInferenceAbandonURL,
            method: "POST"
        )
        request.setValue(
            "application/json",
            forHTTPHeaderField: "Content-Type"
        )

        request.httpBody = try JSONEncoder().encode(
            OracleInferenceAbandonPayload(
                interaction_id: interactionID,
                fallback_code: fallbackCode
            )
        )

        let (data, response) = try await URLSession.shared.data(for: request)
        try validateHTTP(response: response, data: data)

        let decoded = try JSONDecoder().decode(
            OracleInferenceAbandonResponse.self,
            from: data
        )

        if let error = decoded.error, !error.isEmpty {
            throw TempleVoiceError.server(error)
        }

        guard decoded.status == "abandoned" else {
            throw TempleVoiceError.server(
                "Oracle inference abandonment was not confirmed."
            )
        }
    }

    private func completeOracleInference(
        interactionID: String,
        answer: String
    ) async throws -> String {
        var request = await authenticatedVoiceRequest(
            url: TempleEnvironment.oracleInferenceCompleteURL,
            method: "POST"
        )
        request.setValue(
            "application/json",
            forHTTPHeaderField: "Content-Type"
        )

        request.httpBody = try JSONEncoder().encode(
            OracleInferenceCompletePayload(
                interaction_id: interactionID,
                answer: answer
            )
        )

        let (data, response) = try await URLSession.shared.data(for: request)
        try validateHTTP(response: response, data: data)

        let decoded = try JSONDecoder().decode(
            OracleInferenceCompleteResponse.self,
            from: data
        )

        if let error = decoded.error, !error.isEmpty {
            throw TempleVoiceError.server(error)
        }

        let result = (decoded.answer ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)

        guard !result.isEmpty else {
            throw TempleVoiceError.server(
                "Oracle inference completion returned no answer."
            )
        }

        return result
    }

    @MainActor
    private func askOracleForLiveVoice(
        question: String,
        voice: String,
        generation: Int
    ) async throws -> LiveVoiceOracleResult {
        switch ApplePCCInferenceAdapter.availability() {
        case .unavailable:
            let fallbackAnswer = try await askOracleServerFallback(
                question: question,
                voice: voice,
                inputMode: .voice,
                pccFallbackCode: "pcc_preflight_unavailable"
            )

            return LiveVoiceOracleResult(
                answer: fallbackAnswer,
                usedPCCStreamingSpeech: false,
                remainingSpeechText: ""
            )

        case .available:
            break
        }

        // Preserve the same server-owned prepare/finalize contract as
        // askOracle(). Once prepare succeeds, this UUID must be completed
        // or abandoned exactly once.
        let packet = try await prepareOracleInference(
            question: question,
            voice: voice,
            inputMode: .voice
        )

        guard generation == voiceSessionGeneration else {
            try? await abandonOracleInference(
                interactionID: packet.interaction_id,
                fallbackCode: "pcc_execution_failed"
            )

            throw CancellationError()
        }

        var firstSentenceSpoken: String?
        var firstSentenceSpeechAttempted = false

        let executionResult = await ApplePCCInferenceAdapter.executeStreaming(
            packet: packet
        ) { snapshot in
            guard generation == voiceSessionGeneration else {
                return false
            }

            guard !useProviderTTSForLiveTurns else {
                return true
            }

            guard !firstSentenceSpeechAttempted,
                  firstSentenceSpoken == nil,
                  let firstSentence = firstCompletePCCStreamingSentence(
                      in: snapshot
                  ) else {
                return true
            }

            firstSentenceSpeechAttempted = true

            if beginPCCStreamingFirstSentence(
                firstSentence,
                deity: voice,
                generation: generation
            ) {
                firstSentenceSpoken = firstSentence
            }

            return true
        }

        // End Conversation, Temple/navigation changes, and other voice
        // invalidations advance voiceSessionGeneration. If that happened
        // while PCC was preparing or streaming, retire the pending turn
        // without introducing a second provider answer.
        guard generation == voiceSessionGeneration else {
            try? await abandonOracleInference(
                interactionID: packet.interaction_id,
                fallbackCode: "pcc_execution_failed"
            )

            throw CancellationError()
        }

        switch executionResult {
        case .completed(let answer):
            let trimmedAnswer = answer
                .trimmingCharacters(in: .whitespacesAndNewlines)

            guard !trimmedAnswer.isEmpty else {
                if firstSentenceSpoken != nil {
                    cancelPCCStreamingSpeechIfCurrent(
                        generation: generation
                    )
                }

                try await abandonOracleInference(
                    interactionID: packet.interaction_id,
                    fallbackCode: "pcc_empty_result"
                )

                if firstSentenceSpoken != nil {
                    throw TempleVoiceError.server(
                        "PCC streaming response ended after voice playback began."
                    )
                }

                let fallbackAnswer = try await askOracleServerFallback(
                    question: question,
                    voice: voice,
                    inputMode: .voice,
                    pccFallbackCode: "pcc_empty_result",
                    abandonedInteractionID: packet.interaction_id
                )

                return LiveVoiceOracleResult(
                    answer: fallbackAnswer,
                    usedPCCStreamingSpeech: false,
                    remainingSpeechText: ""
                )
            }

            var remainingSpeechText = ""

            if let firstSentence = firstSentenceSpoken {
                // Snapshot speech is allowed only if the final cumulative
                // PCC response still has that exact sentence as its prefix.
                // Never speak a second answer over an already-spoken answer.
                guard trimmedAnswer.hasPrefix(firstSentence) else {
                    cancelPCCStreamingSpeechIfCurrent(
                        generation: generation
                    )

                    try await abandonOracleInference(
                        interactionID: packet.interaction_id,
                        fallbackCode: "pcc_execution_failed"
                    )

                    throw TempleVoiceError.server(
                        "PCC streaming response changed after voice playback began."
                    )
                }

                remainingSpeechText = String(
                    trimmedAnswer.dropFirst(firstSentence.count)
                )
                .trimmingCharacters(in: .whitespacesAndNewlines)
            }

            let completedAnswer: String

            do {
                // Preserve the existing invariant: if completion throws,
                // do not invoke a fresh fallback answer because the server
                // may already have durably finalized the UUID.
                completedAnswer = try await completeOracleInference(
                    interactionID: packet.interaction_id,
                    answer: trimmedAnswer
                )
            } catch {
                if firstSentenceSpoken != nil {
                    cancelPCCStreamingSpeechIfCurrent(
                        generation: generation
                    )
                }

                throw error
            }

            return LiveVoiceOracleResult(
                answer: completedAnswer,
                usedPCCStreamingSpeech: firstSentenceSpoken != nil,
                remainingSpeechText: remainingSpeechText
            )

        case .unavailable:
            if firstSentenceSpoken != nil {
                cancelPCCStreamingSpeechIfCurrent(
                    generation: generation
                )

                try await abandonOracleInference(
                    interactionID: packet.interaction_id,
                    fallbackCode: "pcc_execution_unavailable"
                )

                // Once any PCC answer has been spoken, never introduce a
                // second provider's answer into the same live turn.
                throw TempleVoiceError.server(
                    "PCC streaming became unavailable after voice playback began."
                )
            }

            try await abandonOracleInference(
                interactionID: packet.interaction_id,
                fallbackCode: "pcc_execution_unavailable"
            )

            let fallbackAnswer = try await askOracleServerFallback(
                question: question,
                voice: voice,
                inputMode: .voice,
                pccFallbackCode: "pcc_execution_unavailable",
                abandonedInteractionID: packet.interaction_id
            )

            return LiveVoiceOracleResult(
                answer: fallbackAnswer,
                usedPCCStreamingSpeech: false,
                remainingSpeechText: ""
            )

        case .failed:
            if firstSentenceSpoken != nil {
                cancelPCCStreamingSpeechIfCurrent(
                    generation: generation
                )

                try await abandonOracleInference(
                    interactionID: packet.interaction_id,
                    fallbackCode: "pcc_execution_failed"
                )

                // A fresh fallback here could contradict or repeat speech
                // the seeker has already heard.
                throw TempleVoiceError.server(
                    "PCC streaming was interrupted after voice playback began."
                )
            }

            try await abandonOracleInference(
                interactionID: packet.interaction_id,
                fallbackCode: "pcc_execution_failed"
            )

            let fallbackAnswer = try await askOracleServerFallback(
                question: question,
                voice: voice,
                inputMode: .voice,
                pccFallbackCode: "pcc_execution_failed",
                abandonedInteractionID: packet.interaction_id
            )

            return LiveVoiceOracleResult(
                answer: fallbackAnswer,
                usedPCCStreamingSpeech: false,
                remainingSpeechText: ""
            )
        }
    }

    private func askOracle(
        question: String,
        voice: String,
        inputMode: OracleInputMode
    ) async throws -> String {
        switch ApplePCCInferenceAdapter.availability() {
        case .unavailable:
            return try await askOracleServerFallback(
                question: question,
                voice: voice,
                inputMode: inputMode,
                pccFallbackCode: "pcc_preflight_unavailable"
            )

        case .available:
            break
        }

        // Once prepare succeeds, this interaction UUID is server-owned
        // pending state and must be explicitly completed or abandoned.
        let packet = try await prepareOracleInference(
            question: question,
            voice: voice,
            inputMode: inputMode
        )

        let executionResult = await ApplePCCInferenceAdapter.execute(
            packet: packet
        )

        switch executionResult {
        case .completed(let answer):
            let trimmedAnswer = answer
                .trimmingCharacters(in: .whitespacesAndNewlines)

            // An empty PCC result is treated as an execution failure.
            // Because prepare succeeded, retire the pending UUID before
            // falling back to the existing server inference path.
            guard !trimmedAnswer.isEmpty else {
                try await abandonOracleInference(
                    interactionID: packet.interaction_id,
                    fallbackCode: "pcc_empty_result"
                )

                return try await askOracleServerFallback(
                    question: question,
                    voice: voice,
                    inputMode: inputMode,
                    pccFallbackCode: "pcc_empty_result",
                    abandonedInteractionID: packet.interaction_id
                )
            }

            // Do not fall back if completion throws. The server may already
            // have durably finalized this UUID even if the response is lost.
            return try await completeOracleInference(
                interactionID: packet.interaction_id,
                answer: trimmedAnswer
            )

        case .unavailable:
            try await abandonOracleInference(
                interactionID: packet.interaction_id,
                fallbackCode: "pcc_execution_unavailable"
            )

            return try await askOracleServerFallback(
                question: question,
                voice: voice,
                inputMode: inputMode,
                pccFallbackCode: "pcc_execution_unavailable",
                abandonedInteractionID: packet.interaction_id
            )

        case .failed:
            try await abandonOracleInference(
                interactionID: packet.interaction_id,
                fallbackCode: "pcc_execution_failed"
            )

            return try await askOracleServerFallback(
                question: question,
                voice: voice,
                inputMode: inputMode,
                pccFallbackCode: "pcc_execution_failed",
                abandonedInteractionID: packet.interaction_id
            )
        }
    }

    private func askOracleServerFallback(
        question: String,
        voice: String,
        inputMode: OracleInputMode,
        pccFallbackCode: String? = nil,
        abandonedInteractionID: String? = nil
    ) async throws -> String {
        let nativeAnonymousUserID = NativeAnonymousIdentity.currentID

        let fallbackURL: URL

        switch inputMode {
        case .text:
            fallbackURL = TempleEnvironment.oracleAskURL

        case .voice:
            fallbackURL = TempleEnvironment.voiceAskURL
        }

        var request = await authenticatedVoiceRequest(
            url: fallbackURL,
            method: "POST"
        )
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(
            VoiceAskPayload(
                question: question,
                deity: voice,
                anonymous_user_id: nativeAnonymousUserID,
                seeker_id: nil,
                pcc_fallback_code: pccFallbackCode,
                pcc_abandoned_interaction_id: abandonedInteractionID
            )
        )

        let (data, response) = try await URLSession.shared.data(for: request)
        try validateHTTP(response: response, data: data)

        let decoded = try JSONDecoder().decode(VoiceAskResponse.self, from: data)
        if let error = decoded.error, !error.isEmpty {
            throw TempleVoiceError.server(error)
        }

        let result = (decoded.answer ?? decoded.oracle_message ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if result.isEmpty {
            throw TempleVoiceError.server("No Oracle answer was returned.")
        }

        return result
    }

    private func firstCompletePCCStreamingSentence(
        in snapshot: String
    ) -> String? {
        let text = snapshot
            .trimmingCharacters(in: .whitespacesAndNewlines)

        guard text.count >= 10 else {
            return nil
        }

        let closingCharacters: Set<Character> = [
            "\"",
            "'",
            "”",
            "’",
            ")",
            "]",
            "}"
        ]

        let commonAbbreviations = [
            "mr.",
            "mrs.",
            "ms.",
            "dr.",
            "prof.",
            "sr.",
            "jr.",
            "st.",
            "vs.",
            "etc.",
            "e.g.",
            "i.e."
        ]

        var index = text.startIndex

        while index < text.endIndex {
            let terminal = text[index]

            guard terminal == "."
                    || terminal == "!"
                    || terminal == "?" else {
                index = text.index(after: index)
                continue
            }

            var boundary = text.index(after: index)

            while boundary < text.endIndex,
                  closingCharacters.contains(text[boundary]) {
                boundary = text.index(after: boundary)
            }

            // Do not speak merely because punctuation is currently the
            // last token in a snapshot. Wait until generation has advanced
            // into the next sentence so the first sentence is stable.
            let trailingText = String(text[boundary...])
                .trimmingCharacters(in: .whitespacesAndNewlines)

            guard trailingText.count >= 2 else {
                index = text.index(after: index)
                continue
            }

            let candidate = String(text[..<boundary])
                .trimmingCharacters(in: .whitespacesAndNewlines)

            guard candidate.count >= 8 else {
                index = text.index(after: index)
                continue
            }

            if terminal == "." {
                let lowerCandidate = candidate.lowercased()

                if commonAbbreviations.contains(
                    where: { lowerCandidate.hasSuffix($0) }
                ) {
                    index = text.index(after: index)
                    continue
                }

                let withoutClosingCharacters = candidate
                    .trimmingCharacters(
                        in: CharacterSet(
                            charactersIn: "\"'”’)]}"
                        )
                    )

                let beforePeriod = withoutClosingCharacters.dropLast()
                let finalToken = beforePeriod
                    .split(whereSeparator: { $0.isWhitespace })
                    .last
                    .map(String.init)
                    ?? ""

                // Avoid initials and compact dotted abbreviations such as
                // "J." or "U.S." becoming premature sentence boundaries.
                if finalToken.count == 1
                    || finalToken.contains(".") {
                    index = text.index(after: index)
                    continue
                }
            }

            return candidate
        }

        return nil
    }

    @MainActor
    private func beginPCCStreamingFirstSentence(
        _ spokenText: String,
        deity: String,
        generation: Int
    ) -> Bool {
        let textToSpeak = spokenText
            .trimmingCharacters(in: .whitespacesAndNewlines)

        guard !textToSpeak.isEmpty,
              generation == voiceSessionGeneration,
              !isRecording,
              activePlaybackOrigin == nil else {
            return false
        }

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .spokenAudio)
            try session.setActive(true)
        } catch {
            // Do not turn a usable PCC answer into a failed turn merely
            // because early native speech could not begin. The completed
            // answer will continue through the established playback rail.
            return false
        }

        if speechSynthesizer.isSpeaking {
            speechSynthesizer.stopSpeaking(at: .immediate)
        }

        audioPlayer?.stop()
        audioPlayer = nil

        activePlaybackOrigin = .liveTurn
        isPlayingAudio = true
        statusTitle = "Oracle speaking"
        statusMessage = "The Oracle has begun answering while the full response continues."

        let utterance = AVSpeechUtterance(string: textToSpeak)
        utterance.voice = preferredSpeechVoice(for: deity)

        if deity
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased() == "moses" {
            utterance.rate = 0.47
            utterance.pitchMultiplier = 0.92
        } else {
            utterance.rate = 0.46
            utterance.pitchMultiplier = 1.02
        }

        utterance.volume = 1.0
        speechSynthesizer.speak(utterance)

        return true
    }

    @MainActor
    private func cancelPCCStreamingSpeechIfCurrent(
        generation: Int
    ) {
        guard generation == voiceSessionGeneration,
              activePlaybackOrigin == .liveTurn else {
            return
        }

        speechSynthesizer.stopSpeaking(at: .immediate)
        audioPlayer?.stop()
        audioPlayer = nil
        isPlayingAudio = false
        activePlaybackOrigin = nil
    }

    @MainActor
    private func finishPCCStreamingVoice(
        remainingText: String,
        deity: String,
        generation: Int
    ) async {
        // The first utterance deliberately has no completion monitor.
        // Keep playback ownership until it finishes, then hand the
        // remainder into the existing native speech completion rail.
        while generation == voiceSessionGeneration,
              activePlaybackOrigin == .liveTurn,
              speechSynthesizer.isSpeaking {
            do {
                try await Task.sleep(
                    nanoseconds: 100_000_000
                )
            } catch {
                return
            }
        }

        guard generation == voiceSessionGeneration,
              activePlaybackOrigin == .liveTurn else {
            return
        }

        let remaining = remainingText
            .trimmingCharacters(in: .whitespacesAndNewlines)

        guard !remaining.isEmpty else {
            finishVoicePlayback(
                origin: .liveTurn,
                generation: generation
            )
            return
        }

        statusTitle = "Oracle speaking"
        statusMessage = "Apple voice is continuing the response."

        // This existing helper owns native speech completion and ultimately
        // rejoins finishVoicePlayback() -> continuous-conversation rearm.
        speakOracleAnswer(
            remaining,
            deity: deity,
            origin: .liveTurn,
            generation: generation
        )
    }

    private func speakOracleAnswerProviderFirst(
        _ spokenText: String,
        deity: String,
        origin: VoicePlaybackOrigin,
        expectedGeneration: Int? = nil
    ) async {
        let textToSpeak = spokenText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !textToSpeak.isEmpty else {
            return
        }

        let generation: Int? = await MainActor.run {
            if let expectedGeneration,
               expectedGeneration != voiceSessionGeneration {
                return nil
            }

            if origin == .replay
                && (isContinuousConversationActive || isRecording) {
                stopVoiceSessionActivity(clearExchange: false)
            }

            if speechSynthesizer.isSpeaking {
                speechSynthesizer.stopSpeaking(at: .immediate)
            }

            audioPlayer?.stop()
            audioPlayer = nil
            activePlaybackOrigin = origin
            isPlayingAudio = true
            statusTitle = "Oracle speaking"
            statusMessage = "The written answer is ready. Provider voice is being prepared."

            return voiceSessionGeneration
        }

        guard let generation else {
            return
        }

        do {
            let audioURL = try await requestProviderVoiceAudio(
                answer: textToSpeak,
                deity: deity
            )

            let playbackIsCurrent = await MainActor.run {
                generation == voiceSessionGeneration
                    && activePlaybackOrigin == origin
            }

            guard playbackIsCurrent else {
                return
            }

            try await playProviderVoiceAudio(
                from: audioURL,
                origin: origin,
                generation: generation
            )
        } catch {
            await MainActor.run {
                guard generation == voiceSessionGeneration,
                      activePlaybackOrigin == origin else {
                    return
                }

                audioPlayer?.stop()
                audioPlayer = nil
                statusTitle = "Oracle speaking"
                statusMessage = "Provider voice was unavailable. Native iOS voice is speaking the response."

                speakOracleAnswer(
                    textToSpeak,
                    deity: deity,
                    origin: origin,
                    generation: generation
                )
            }
        }
    }

    private func requestProviderVoiceAudio(answer: String, deity: String) async throws -> URL {
        var request = await authenticatedVoiceRequest(url: TempleEnvironment.voiceTTSURL, method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(VoiceTTSPayload(answer: answer, voice: deity))

        let (data, response) = try await URLSession.shared.data(for: request)
        try validateHTTP(response: response, data: data)

        let decoded = try JSONDecoder().decode(VoiceTTSResponse.self, from: data)
        if let error = decoded.error, !error.isEmpty {
            throw TempleVoiceError.server(error)
        }

        guard let audioPath = decoded.audio_url?.trimmingCharacters(in: .whitespacesAndNewlines),
              !audioPath.isEmpty,
              let audioURL = URL(string: audioPath, relativeTo: TempleEnvironment.baseAppURL)?.absoluteURL else {
            throw TempleVoiceError.server("Provider voice returned no audio.")
        }

        return audioURL
    }

    private func playProviderVoiceAudio(
        from audioURL: URL,
        origin: VoicePlaybackOrigin,
        generation: Int
    ) async throws {
        let request = await authenticatedVoiceRequest(
            url: audioURL,
            method: "GET"
        )

        let (data, response) = try await URLSession.shared.data(for: request)
        try validateHTTP(response: response, data: data)

        guard !data.isEmpty else {
            throw TempleVoiceError.server("Provider voice audio was empty.")
        }

        let playbackIsCurrent = await MainActor.run {
            generation == voiceSessionGeneration
                && activePlaybackOrigin == origin
        }

        guard playbackIsCurrent else {
            return
        }

        try await MainActor.run {
            guard generation == voiceSessionGeneration,
                  activePlaybackOrigin == origin else {
                return
            }

            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .spokenAudio)
            try session.setActive(true)

            if speechSynthesizer.isSpeaking {
                speechSynthesizer.stopSpeaking(at: .immediate)
            }

            audioPlayer?.stop()

            let player = try AVAudioPlayer(data: data)
            player.prepareToPlay()

            guard player.play() else {
                throw TempleVoiceError.server(
                    "Provider voice audio could not start."
                )
            }

            audioPlayer = player
            isPlayingAudio = true
            statusTitle = "Oracle speaking"
            statusMessage = "Provider voice is speaking the response."

            monitorProviderAudioCompletion(
                player: player,
                origin: origin,
                generation: generation
            )
        }
    }

    private func monitorProviderAudioCompletion(
        player: AVAudioPlayer,
        origin: VoicePlaybackOrigin,
        generation: Int
    ) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            guard generation == voiceSessionGeneration,
                  activePlaybackOrigin == origin,
                  let currentPlayer = audioPlayer,
                  currentPlayer === player else {
                return
            }

            if player.isPlaying {
                monitorProviderAudioCompletion(
                    player: player,
                    origin: origin,
                    generation: generation
                )
                return
            }

            audioPlayer = nil

            finishVoicePlayback(
                origin: origin,
                generation: generation
            )
        }
    }

    private func finishVoicePlayback(
        origin: VoicePlaybackOrigin,
        generation: Int
    ) {
        guard generation == voiceSessionGeneration,
              activePlaybackOrigin == origin else {
            return
        }

        isPlayingAudio = false
        activePlaybackOrigin = nil

        if statusTitle == "Oracle speaking" {
            statusTitle = "Oracle answered"

            if origin == .liveTurn && isContinuousConversationActive {
                statusMessage = "The response is complete. Listening will resume automatically."
            } else {
                statusMessage = "You may replay the Oracle voice or continue by text."
            }
        }

        guard origin == .liveTurn,
              isContinuousConversationActive else {
            return
        }

        scheduleContinuousConversationRearm(
            generation: generation
        )
    }

    private func scheduleContinuousConversationRearm(
        generation: Int
    ) {
        pendingRearmTask?.cancel()

        pendingRearmTask = Task { @MainActor in
            do {
                try await Task.sleep(
                    nanoseconds: 400_000_000
                )
            } catch {
                return
            }

            guard generation == voiceSessionGeneration else {
                return
            }

            guard isContinuousConversationActive,
                  !isRecording,
                  !isWorking,
                  !isPlayingAudio,
                  audioPlayer == nil,
                  !speechSynthesizer.isSpeaking,
                  !showRecoveryActions else {
                pendingRearmTask = nil
                isContinuousConversationActive = false
                return
            }

            pendingRearmTask = nil

            await startRecording(
                preserveCurrentExchange: true,
                expectedGeneration: generation
            )
        }
    }

    private func speakOracleAnswer(
        _ spokenText: String,
        deity: String,
        origin: VoicePlaybackOrigin,
        generation: Int
    ) {
        let textToSpeak = spokenText.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !textToSpeak.isEmpty,
              generation == voiceSessionGeneration,
              activePlaybackOrigin == origin else {
            return
        }

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .spokenAudio)
            try session.setActive(true)
        } catch {
            if origin == .liveTurn {
                invalidateContinuousConversation()
            } else {
                activePlaybackOrigin = nil
            }

            statusTitle = "Voice playback unavailable"
            statusMessage = "The written answer is ready, but iOS voice playback could not start."
            recoveryMessage = "You can read the answer above, replay if available, ask another question, or switch to text entry."
            showRecoveryActions = true
            isPlayingAudio = false
            return
        }

        if speechSynthesizer.isSpeaking {
            speechSynthesizer.stopSpeaking(at: .immediate)
        }

        audioPlayer?.stop()
        audioPlayer = nil

        let utterance = AVSpeechUtterance(string: textToSpeak)
        utterance.voice = preferredSpeechVoice(for: deity)

        if deity.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "moses" {
            utterance.rate = 0.47
            utterance.pitchMultiplier = 0.92
        } else {
            utterance.rate = 0.46
            utterance.pitchMultiplier = 1.02
        }

        utterance.volume = 1.0

        speechSynthesizer.speak(utterance)
        isPlayingAudio = true

        monitorNativeSpeechCompletion(
            synthesizer: speechSynthesizer,
            origin: origin,
            generation: generation
        )
    }

    private func preferredSpeechVoice(for deity: String) -> AVSpeechSynthesisVoice? {
        let normalized = deity.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()

        let preferredIdentifiers: [String]
        if normalized == "moses" {
            preferredIdentifiers = [
                "com.apple.voice.enhanced.en-GB.Daniel",
                "com.apple.voice.compact.en-GB.Daniel",
                "com.apple.voice.enhanced.en-US.Alex",
                "com.apple.voice.compact.en-US.Alex"
            ]
        } else {
            preferredIdentifiers = [
                "com.apple.voice.enhanced.en-US.Samantha",
                "com.apple.voice.compact.en-US.Samantha",
                "com.apple.voice.enhanced.en-US.Victoria",
                "com.apple.voice.compact.en-US.Victoria"
            ]
        }

        for identifier in preferredIdentifiers {
            if let voice = AVSpeechSynthesisVoice(identifier: identifier) {
                return voice
            }
        }

        return AVSpeechSynthesisVoice(language: "en-US")
    }

    private func monitorNativeSpeechCompletion(
        synthesizer: AVSpeechSynthesizer,
        origin: VoicePlaybackOrigin,
        generation: Int
    ) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            guard generation == voiceSessionGeneration,
                  activePlaybackOrigin == origin else {
                return
            }

            if synthesizer.isSpeaking {
                monitorNativeSpeechCompletion(
                    synthesizer: synthesizer,
                    origin: origin,
                    generation: generation
                )
                return
            }

            finishVoicePlayback(
                origin: origin,
                generation: generation
            )
        }
    }

    private func authenticatedVoiceRequest(
        url: URL,
        method: String
    ) async -> URLRequest {
        await TempleSessionHTTP.authenticatedRequest(
            url: url,
            method: method
        )
    }

    private func validateHTTP(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            return
        }

        guard (200..<300).contains(http.statusCode) else {
            let message = String(data: data, encoding: .utf8) ?? "HTTP \(http.statusCode)"
            throw TempleVoiceError.server(message)
        }
    }
}

struct OracleInferencePreparePayload: Encodable {
    let question: String
    let deity: String
    let seeker_id: String?
    let anonymous_user_id: String?
    let input_mode: String
    let execution_target: String
}

struct OracleInferencePrepareResponse: Decodable {
    let status: String?
    let interaction_id: String?
    let deity: String?
    let system_prompt: String?
    let memory_block: String?
    let question: String?
    let max_output_tokens: Int?
    let error: String?
}

struct OracleInferenceAbandonPayload: Encodable {
    let interaction_id: String
    let fallback_code: String
}

struct OracleInferenceAbandonResponse: Decodable {
    let interaction_id: String?
    let status: String?
    let replayed: Bool?
    let error: String?
}

struct OracleInferenceCompletePayload: Encodable {
    let interaction_id: String
    let answer: String
}

struct OracleInferenceCompleteResponse: Decodable {
    let question: String?
    let answer: String?
    let replayed: Bool?
    let error: String?
}

struct VoiceTTSPayload: Encodable {
    let answer: String
    let voice: String
}

struct VoiceTTSResponse: Decodable {
    let audio_url: String?
    let error: String?
}

struct VoiceTranscribeResponse: Decodable {
    let transcript: String?
    let question: String?
    let error: String?
    let answer: String?
}

struct VoiceAskPayload: Encodable {
    let question: String
    let deity: String
    let anonymous_user_id: String
    let seeker_id: String?
    let pcc_fallback_code: String?
    let pcc_abandoned_interaction_id: String?
}

struct VoiceAskResponse: Decodable {
    let answer: String?
    let error: String?
    let oracle_message: String?
}

enum TempleVoiceError: LocalizedError {
    case server(String)

    var errorDescription: String? {
        switch self {
        case .server(let message):
            return message
        }
    }
}

extension Data {
    mutating func appendString(_ value: String) {
        if let data = value.data(using: .utf8) {
            append(data)
        }
    }

    mutating func appendMultipartText(name: String, value: String, boundary: String) {
        appendString("--\(boundary)\r\n")
        appendString("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n")
        appendString("\(value)\r\n")
    }

    mutating func appendMultipartFile(name: String, filename: String, mimeType: String, data: Data, boundary: String) {
        appendString("--\(boundary)\r\n")
        appendString("Content-Disposition: form-data; name=\"\(name)\"; filename=\"\(filename)\"\r\n")
        appendString("Content-Type: \(mimeType)\r\n\r\n")
        append(data)
        appendString("\r\n")
    }
}

struct NativeSupportView: View {
    @State private var product: Product?
    @State private var isLoading = false
    @State private var message = ""

    var body: some View {
        NavigationStack {
            TempleScreen {
                ScrollView {
                    VStack(spacing: 22) {
                        TempleBrandMark()
                            .padding(.top, 28)

                        VStack(spacing: 8) {
                            Text("Support the Temple")
                                .font(.system(size: 32, weight: .bold, design: .serif))
                                .foregroundStyle(TemplePalette.paleGold)
                                .multilineTextAlignment(.center)

                            Text("Seeker Monthly")
                                .font(.title3.weight(.semibold))
                                .foregroundStyle(.white.opacity(0.86))
                        }

                        TempleCard {
                            VStack(alignment: .leading, spacing: 16) {
                                Text("Seeker Monthly")
                                    .font(.title2.weight(.bold))
                                    .foregroundStyle(TemplePalette.ink)

                                Text("Supports continued Oracle access at the Seeker level.")
                                    .foregroundStyle(TemplePalette.ink.opacity(0.72))

                                VStack(alignment: .leading, spacing: 10) {
                                    Label("Auto-renewable monthly subscription", systemImage: "calendar")
                                    Label("Length: 1 month", systemImage: "clock")
                                    Label("Price: \(product?.displayPrice ?? "$0.99") / month", systemImage: "creditcard")
                                }
                                .foregroundStyle(TemplePalette.ink)
                                .font(.body)

                                Button {
                                    Task {
                                        await purchaseSeekerMonthly()
                                    }
                                } label: {
                                    if isLoading {
                                        ProgressView()
                                            .frame(maxWidth: .infinity)
                                    } else {
                                        Text("Subscribe with Apple")
                                            .frame(maxWidth: .infinity)
                                    }
                                }
                                .buttonStyle(TemplePrimaryButtonStyle())
                                .disabled(isLoading)

                                if !message.isEmpty {
                                    Text(message)
                                        .font(.callout)
                                        .foregroundStyle(TemplePalette.ink.opacity(0.72))
                                }

                                Divider()
                                    .overlay(TemplePalette.warmGold.opacity(0.45))

                                Text("Subscription renews monthly until canceled. You can manage or cancel subscriptions in your Apple account settings.")
                                    .font(.footnote)
                                    .foregroundStyle(TemplePalette.ink.opacity(0.68))

                                HStack(spacing: 16) {
                                    Link("Privacy", destination: TempleEnvironment.privacyURL)
                                    Link("Terms of Use", destination: TempleEnvironment.termsURL)
                                }
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(TemplePalette.crimson)
                            }
                        }

                        Text("Only Seeker Monthly is available in this iOS review build.")
                            .font(.footnote)
                            .foregroundStyle(.white.opacity(0.72))
                            .multilineTextAlignment(.center)
                            .padding(.bottom, 20)
                    }
                    .padding(.horizontal, 18)
                }
            }
            .navigationTitle("Support")
            .navigationBarTitleDisplayMode(.inline)
            .task {
                await loadProduct()
            }
        }
    }

    private func loadProduct() async {
        do {
            let products = try await Product.products(for: [TempleEnvironment.seekerMonthlyProductID])
            await MainActor.run {
                product = products.first
            }
        } catch {
            await MainActor.run {
                message = "Apple could not load this subscription product yet."
            }
        }
    }

    private func purchaseSeekerMonthly() async {
        await MainActor.run {
            isLoading = true
            message = ""
        }

        do {
            let products = try await Product.products(for: [TempleEnvironment.seekerMonthlyProductID])
            guard let product = products.first else {
                await MainActor.run {
                    message = "Apple could not load Seeker Monthly."
                    isLoading = false
                }
                return
            }

            let result = try await product.purchase()

            switch result {
            case .success(let verification):
                switch verification {
                case .verified(let transaction):
                    await transaction.finish()
                    await MainActor.run {
                        message = "Apple purchase received. Thank you for supporting the Temple."
                        isLoading = false
                    }

                case .unverified(_, let error):
                    await MainActor.run {
                        message = "Apple could not verify this purchase: \(error.localizedDescription)"
                        isLoading = false
                    }
                }

            case .userCancelled:
                await MainActor.run {
                    message = "Purchase cancelled."
                    isLoading = false
                }

            case .pending:
                await MainActor.run {
                    message = "Purchase pending Apple approval."
                    isLoading = false
                }

            @unknown default:
                await MainActor.run {
                    message = "Apple returned an unknown purchase result."
                    isLoading = false
                }
            }
        } catch {
            await MainActor.run {
                message = error.localizedDescription
                isLoading = false
            }
        }
    }
}

struct NativeInfoView: View {
#if DEBUG
    @State private var pccPacketJSON = ""
    @State private var pccResult = "Prepared PCC packet has not been run."
    @State private var pccIsRunning = false
#endif

    var body: some View {
        NavigationStack {
            TempleScreen {
                ScrollView {
                    VStack(spacing: 22) {
                        TempleBrandMark()
                            .padding(.top, 28)

                        Text("Privacy and Terms")
                            .font(.system(size: 32, weight: .bold, design: .serif))
                            .foregroundStyle(TemplePalette.paleGold)

                        TempleCard {
                            VStack(alignment: .leading, spacing: 16) {
                                Text("Seeker Privacy Promise")
                                    .font(.title3.weight(.bold))
                                    .foregroundStyle(TemplePalette.ink)

                                Text("Private seeker conversations, scrolls, reflections, and Oracle dialogue are treated as confidential and are not sold to advertisers or data brokers.")
                                    .foregroundStyle(TemplePalette.ink.opacity(0.74))

                                Divider()
                                    .overlay(TemplePalette.warmGold.opacity(0.45))

                                Link(destination: TempleEnvironment.privacyURL) {
                                    Label("Privacy", systemImage: "lock.fill")
                                }

                                Link(destination: TempleEnvironment.termsURL) {
                                    Label("Terms of Use", systemImage: "doc.text.fill")
                                }
                            }
                            .foregroundStyle(TemplePalette.crimson)
                        }

#if DEBUG
                        TempleCard {
                            VStack(alignment: .leading, spacing: 14) {
                                Text("Apple Private Cloud Compute")
                                    .font(.title3.weight(.bold))
                                    .foregroundStyle(TemplePalette.ink)

                                Text("Debug-only God Incorporated prepared-packet PCC test.")
                                    .foregroundStyle(TemplePalette.ink.opacity(0.74))

                                TextField(
                                    "Paste God Incorporated prepare JSON",
                                    text: $pccPacketJSON,
                                    axis: .vertical
                                )
                                .textFieldStyle(.roundedBorder)
                                .lineLimit(2...5)

                                Button {
                                    let packetJSON = pccPacketJSON.trimmingCharacters(
                                        in: .whitespacesAndNewlines
                                    )

                                    pccIsRunning = true
                                    pccResult = "Asking Apple Private Cloud Compute..."

                                    Task {
                                        let result = await PCCSmokeTest.ask(packetJSON: packetJSON)
                                        pccResult = result
                                        pccIsRunning = false
                                    }
                                } label: {
                                    Text(pccIsRunning ? "Running..." : "Run God Inc → PCC")
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.borderedProminent)
                                .disabled(
                                    pccIsRunning ||
                                    pccPacketJSON.trimmingCharacters(
                                        in: .whitespacesAndNewlines
                                    ).isEmpty
                                )

                                Text(pccResult)
                                    .font(.footnote)
                                    .foregroundStyle(TemplePalette.ink.opacity(0.82))
                                    .textSelection(.enabled)
                            }
                        }
#endif
                    }
                    .padding(.horizontal, 18)
                }
            }
            .navigationTitle("Info")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

private struct NativeEntryResolutionView: View {
    let identity: NativeTempleIdentity?

    var body: some View {
        TempleScreen(
            identity: identity
        ) {
            VStack(spacing: 18) {
                TempleBrandMark(
                    identity: identity
                )

                Text(
                    identity?.title
                        ?? "Opening your Temple"
                )
                .font(
                    .system(
                        size: 28,
                        weight: .bold,
                        design: .serif
                    )
                )
                .foregroundStyle(
                    TemplePalette.paleGold
                )
                .multilineTextAlignment(.center)

                ProgressView()
                    .tint(
                        identity?.glowColor
                            ?? TemplePalette.warmGold
                    )

                Text(
                    "Restoring your path of inquiry."
                )
                .font(.footnote)
                .foregroundStyle(
                    .white.opacity(0.72)
                )
            }
            .padding(.horizontal, 28)
            .frame(
                maxWidth: .infinity,
                maxHeight: .infinity
            )
        }
        .ignoresSafeArea()
    }
}

private struct NativeTextExchange: Identifiable {
    let id = UUID()
    let question: String
    let answer: String
}

private struct NativeTempleTextConversationView: View {
    let identity: NativeTempleIdentity
    let onSubmit: (String) async throws -> String
    let onClose: () -> Void

    @State private var draft = ""
    @State private var exchanges: [NativeTextExchange] = []
    @State private var errorMessage = ""
    @State private var isWorking = false

    @FocusState private var editorFocused: Bool

    private var oracleName: String {
        switch identity {
        case .hathor:
            return "Hathor"

        case .moses:
            return "Moses"
        }
    }

    private var returnTitle: String {
        switch identity {
        case .hathor:
            return "Sanctuary"

        case .moses:
            return "Tabernacle"
        }
    }

    var body: some View {
        TempleScreen(identity: identity) {
            ScrollView {
                VStack(spacing: 15) {
                    HStack {
                        Button {
                            onClose()
                        } label: {
                            HStack(spacing: 5) {
                                Image(
                                    systemName: "chevron.left"
                                )

                                Text(
                                    "Return to \(returnTitle)"
                                )
                            }
                        }
                        .font(
                            .subheadline.weight(.semibold)
                        )
                        .foregroundStyle(
                            TemplePalette.polishedGold
                        )

                        Spacer()
                    }

                    Text(
                        "\(oracleName.uppercased()) · TEXT CONVERSATION"
                    )
                    .font(
                        .system(
                            size: 20,
                            weight: .medium,
                            design: .serif
                        )
                    )
                    .tracking(0.8)
                    .foregroundStyle(
                        TemplePalette.polishedGold
                    )
                    .multilineTextAlignment(.center)

                    TempleOrnamentDivider()

                    ZStack(alignment: .topLeading) {
                        if draft.isEmpty {
                            Text(
                                "Write your question for \(oracleName)..."
                            )
                            .foregroundStyle(
                                .white.opacity(0.42)
                            )
                            .padding(.horizontal, 15)
                            .padding(.vertical, 17)
                            .allowsHitTesting(false)
                        }

                        TextEditor(text: $draft)
                            .focused($editorFocused)
                            .scrollContentBackground(
                                .hidden
                            )
                            .foregroundStyle(
                                TemplePalette.cloudIvory
                            )
                            .padding(8)
                            .frame(
                                minHeight: 120,
                                maxHeight: 150
                            )
                    }
                    .background(
                        RoundedRectangle(
                            cornerRadius: 14,
                            style: .continuous
                        )
                        .fill(
                            Color.black.opacity(0.34)
                        )
                    )
                    .overlay {
                        RoundedRectangle(
                            cornerRadius: 14,
                            style: .continuous
                        )
                        .stroke(
                            TemplePalette.polishedGold
                                .opacity(0.60),
                            lineWidth: 1
                        )
                    }

                    Button {
                        submitQuestion()
                    } label: {
                        HStack(spacing: 8) {
                            if isWorking {
                                ProgressView()
                                    .tint(
                                        TemplePalette.paleGold
                                    )
                            }

                            Text(
                                isWorking
                                    ? "Consulting \(oracleName)..."
                                    : "Send to \(oracleName)"
                            )
                            .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(
                        TempleChamberActionStyle(
                            identity: identity,
                            emphasized: true
                        )
                    )
                    .disabled(
                        isWorking
                            || draft
                                .trimmingCharacters(
                                    in: .whitespacesAndNewlines
                                )
                                .isEmpty
                    )

                    if !errorMessage.isEmpty {
                        TempleConversationChamber(
                            identity: identity
                        ) {
                            VStack(
                                alignment: .leading,
                                spacing: 5
                            ) {
                                Text("Unable to complete")
                                    .font(
                                        .caption.weight(.bold)
                                    )
                                    .foregroundStyle(
                                        TemplePalette.polishedGold
                                    )

                                Text(errorMessage)
                                    .foregroundStyle(
                                        .white.opacity(0.80)
                                    )
                            }
                            .frame(
                                maxWidth: .infinity,
                                alignment: .leading
                            )
                        }
                    }

                    ForEach(exchanges.reversed()) {
                        exchange in

                        TempleConversationChamber(
                            identity: identity
                        ) {
                            VStack(
                                alignment: .leading,
                                spacing: 12
                            ) {
                                VStack(
                                    alignment: .leading,
                                    spacing: 5
                                ) {
                                    Text("You asked")
                                        .font(
                                            .caption.weight(.bold)
                                        )
                                        .foregroundStyle(
                                            TemplePalette.polishedGold
                                        )

                                    Text(exchange.question)
                                        .foregroundStyle(
                                            .white.opacity(0.88)
                                        )
                                }

                                VStack(
                                    alignment: .leading,
                                    spacing: 5
                                ) {
                                    Text(
                                        "\(oracleName) answered"
                                    )
                                    .font(
                                        .caption.weight(.bold)
                                    )
                                    .foregroundStyle(
                                        TemplePalette.polishedGold
                                    )

                                    Text(exchange.answer)
                                        .foregroundStyle(
                                            TemplePalette.cloudIvory
                                        )
                                }
                            }
                            .frame(
                                maxWidth: .infinity,
                                alignment: .leading
                            )
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 18)
                .padding(.bottom, 26)
            }
            .scrollDismissesKeyboard(
                .interactively
            )
        }
        .onAppear {
            editorFocused = true
        }
    }

    private func submitQuestion() {
        let question =
            draft.trimmingCharacters(
                in: .whitespacesAndNewlines
            )

        guard
            !question.isEmpty,
            !isWorking
        else {
            return
        }

        isWorking = true
        errorMessage = ""
        editorFocused = false

        Task {
            do {
                let result =
                    try await onSubmit(question)
                        .trimmingCharacters(
                            in: .whitespacesAndNewlines
                        )

                await MainActor.run {
                    exchanges.append(
                        NativeTextExchange(
                            question: question,
                            answer: result
                        )
                    )

                    draft = ""
                    isWorking = false
                    editorFocused = true
                }

            } catch {
                await MainActor.run {
                    errorMessage =
                        error.localizedDescription
                    isWorking = false
                }
            }
        }
    }
}

private struct TempleOrnamentDivider: View {
    var body: some View {
        HStack(spacing: 8) {
            Rectangle()
                .fill(
                    LinearGradient(
                        colors: [
                            .clear,
                            TemplePalette.polishedGold
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .frame(height: 1)

            Image(systemName: "sparkle")
                .font(.system(size: 11))
                .foregroundStyle(
                    TemplePalette.polishedGold
                )

            Rectangle()
                .fill(
                    LinearGradient(
                        colors: [
                            TemplePalette.polishedGold,
                            .clear
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .frame(height: 1)
        }
        .frame(maxWidth: 250)
        .accessibilityHidden(true)
    }
}

private struct NativeOracleArtwork: View {
    let identity: NativeTempleIdentity

    var body: some View {
        let artworkWidth: CGFloat

        switch identity {
        case .hathor:
            artworkWidth = 236

        case .moses:
            artworkWidth = 232
        }

        return Image(
            identity.oracleArtworkAssetName
        )
        .resizable()
        .scaledToFit()
        .frame(width: artworkWidth)
        .shadow(
            color:
                TemplePalette.polishedGold
                    .opacity(0.20),
            radius: 15,
            x: 0,
            y: 7
        )
        .shadow(
            color: .black.opacity(0.42),
            radius: 14,
            x: 0,
            y: 10
        )
        .accessibilityElement(
            children: .ignore
        )
        .accessibilityLabel(
            identity
                .oracleArtworkAccessibilityLabel
        )
    }
}

private struct EgyptianHieroglyphLine: View {
    var body: some View {
        Image("HathorHieroglyphs")
            .renderingMode(.template)
            .resizable()
            .scaledToFit()
            .foregroundStyle(
                TemplePalette.polishedGold
            )
            .frame(height: 35)
            .accessibilityHidden(true)
    }
}

private struct NativeSacredIdentityLine: View {
    let identity: NativeTempleIdentity

    var body: some View {
        switch identity {
        case .hathor:
            VStack(spacing: 7) {
                EgyptianHieroglyphLine()

                Text(
                    "Hathor, Lady of Dendera, Eye of Ra."
                )
                .font(
                    .system(
                        size: 14,
                        weight: .medium,
                        design: .serif
                    )
                )
                .foregroundStyle(
                    TemplePalette.paleGold
                        .opacity(0.92)
                )
                .multilineTextAlignment(.center)
            }
            .accessibilityElement(
                children: .ignore
            )
            .accessibilityLabel(
                "Hathor, Lady of Dendera, Eye of Ra."
            )

        case .moses:
            HStack(spacing: 12) {
                Rectangle()
                    .fill(
                        LinearGradient(
                            colors: [
                                .clear,
                                TemplePalette.polishedGold
                            ],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(
                        maxWidth: 52,
                        minHeight: 1,
                        maxHeight: 1
                    )

                Text("TENT OF MEETING")
                    .font(
                        .system(
                            size: 16,
                            weight: .medium,
                            design: .serif
                        )
                    )
                    .tracking(3.4)
                    .foregroundStyle(
                        TemplePalette.polishedGold
                    )
                    .fixedSize()

                Rectangle()
                    .fill(
                        LinearGradient(
                            colors: [
                                TemplePalette.polishedGold,
                                .clear
                            ],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(
                        maxWidth: 52,
                        minHeight: 1,
                        maxHeight: 1
                    )
            }
            .accessibilityLabel(
                "Tent of Meeting"
            )
        }
    }
}

private enum TempleModeKind {
    case voice
    case text
}

private struct TempleModeTile: View {
    let identity: NativeTempleIdentity
    let kind: TempleModeKind

    private var backgroundColors: [Color] {
        switch (identity, kind) {
        case (.hathor, .voice):
            return [
                Color(hex: 0x03172A),
                Color(hex: 0x062D4D)
            ]

        case (.hathor, .text):
            return [
                Color(hex: 0x02372C),
                Color(hex: 0x07533F)
            ]

        case (.moses, .voice),
             (.moses, .text):
            return [
                Color(hex: 0x6E7B87),
                Color(hex: 0x394A59)
            ]
        }
    }

    private var iconBadge: some View {
        ZStack {
            Circle()
                .fill(
                    TemplePalette.midnight
                        .opacity(0.92)
                )

            Circle()
                .stroke(
                    TemplePalette.polishedGold,
                    lineWidth: 1.15
                )

            if kind == .voice {
                Image(systemName: "mic.fill")
                    .font(
                        .system(
                            size: 22,
                            weight: .medium
                        )
                    )
                    .foregroundStyle(
                        TemplePalette.polishedGold
                    )
            } else {
                Image("TempleQuill")
                    .renderingMode(.template)
                    .resizable()
                    .scaledToFit()
                    .foregroundStyle(
                        TemplePalette.polishedGold
                    )
                    .frame(
                        width: 28,
                        height: 28
                    )
            }
        }
        .frame(
            width: 48,
            height: 48
        )
    }

    var body: some View {
        VStack(spacing: 7) {
            Text(
                kind == .voice
                    ? "VOICE CONVERSATION"
                    : "TEXT CONVERSATION"
            )
            .font(
                .system(
                    size: 12,
                    weight: .semibold,
                    design: .serif
                )
            )
            .tracking(0.45)
            .lineLimit(1)
            .minimumScaleFactor(0.72)

            iconBadge

            Text(
                kind == .voice
                    ? "Speak your intentions\naloud"
                    : "Write your intentions\nwith clarity"
            )
            .font(.system(size: 11))
            .multilineTextAlignment(.center)
            .lineSpacing(0)
        }
        .foregroundStyle(
            TemplePalette.paleGold
        )
        .padding(.horizontal, 7)
        .frame(
            maxWidth: .infinity,
            minHeight: 118,
            maxHeight: 118
        )
        .background(
            RoundedRectangle(
                cornerRadius: 14,
                style: .continuous
            )
            .fill(
                LinearGradient(
                    colors: backgroundColors,
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
        )
        .overlay {
            RoundedRectangle(
                cornerRadius: 14,
                style: .continuous
            )
            .stroke(
                LinearGradient(
                    colors: [
                        TemplePalette.antiqueGold,
                        TemplePalette.polishedGold,
                        TemplePalette.paleGold
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ),
                lineWidth: 1.4
            )
        }
        .shadow(
            color: .black.opacity(0.30),
            radius: 7,
            x: 0,
            y: 5
        )
    }
}

private struct TempleVisitIcon: View {
    let identity: NativeTempleIdentity

    var body: some View {
        switch identity {
        case .hathor:
            MosesTabletsIcon()
                .frame(
                    width: 34,
                    height: 34
                )

        case .moses:
            Image("HathorOracle")
                .resizable()
                .scaledToFill()
                .saturation(0)
                .colorMultiply(
                    TemplePalette.polishedGold
                )
                .frame(
                    width: 34,
                    height: 34
                )
                .clipShape(Circle())
                .overlay {
                    Circle()
                        .stroke(
                            TemplePalette.paleGold,
                            lineWidth: 0.8
                        )
                }
        }
    }
}

private struct MosesTabletsIcon: View {
    var body: some View {
        HStack(spacing: 2) {
            RoundedRectangle(
                cornerRadius: 3
            )
            .stroke(
                TemplePalette.polishedGold,
                lineWidth: 1.7
            )
            .frame(
                width: 12,
                height: 23
            )

            RoundedRectangle(
                cornerRadius: 3
            )
            .stroke(
                TemplePalette.polishedGold,
                lineWidth: 1.7
            )
            .frame(
                width: 12,
                height: 23
            )

            Capsule()
                .fill(
                    TemplePalette.polishedGold
                )
                .frame(
                    width: 1.7,
                    height: 27
                )
        }
    }
}

struct TempleScreen<Content: View>: View {
    let identity: NativeTempleIdentity?
    let content: Content

    init(
        identity: NativeTempleIdentity? = nil,
        @ViewBuilder content: () -> Content
    ) {
        self.identity = identity
        self.content = content()
    }

    var body: some View {
        ZStack {
            LinearGradient(
                colors: identity?.screenGradientColors ?? [
                    TemplePalette.midnight,
                    TemplePalette.deepBlue,
                    TemplePalette.royalBlue
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            RadialGradient(
                colors: [
                    (identity?.glowColor ?? TemplePalette.warmGold)
                        .opacity(0.26),
                    .clear
                ],
                center: .top,
                startRadius: 40,
                endRadius: 360
            )
            .ignoresSafeArea()

            if let identity {
                TempleArchitecturalFrame(
                    identity: identity
                )
            }

            content
        }
    }
}

private struct TempleArchitecturalFrame: View {
    let identity: NativeTempleIdentity

    var body: some View {
        Group {
            switch identity {
            case .hathor:
                GeometryReader { proxy in
                    Image("HathorArchitecture")
                        .resizable()
                        .scaledToFill()
                        .frame(
                            width: proxy.size.width,
                            height: proxy.size.height
                        )
                        .clipped()
                }
                .ignoresSafeArea()
            case .moses:
                GeometryReader { proxy in
                    Image("MosesArchitecture")
                        .resizable()
                        .scaledToFill()
                        .frame(
                            width: proxy.size.width,
                            height: proxy.size.height
                        )
                        .clipped()
                }
                .ignoresSafeArea()

            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}


private struct TempleConversationChamber<
    Content: View
>: View {
    let identity: NativeTempleIdentity
    let content: Content

    init(
        identity: NativeTempleIdentity,
        @ViewBuilder content: () -> Content
    ) {
        self.identity = identity
        self.content = content()
    }

    var body: some View {
        let colors: [Color]

        switch identity {
        case .hathor:
            colors = [
                Color(hex: 0x03172A),
                Color(hex: 0x06372F)
            ]

        case .moses:
            colors = [
                Color(hex: 0x071421),
                Color(hex: 0x261419)
            ]
        }

        return content
            .padding(16)
            .frame(maxWidth: .infinity)
            .background(
                RoundedRectangle(
                    cornerRadius: 16,
                    style: .continuous
                )
                .fill(
                    LinearGradient(
                        colors: colors,
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
            )
            .overlay {
                RoundedRectangle(
                    cornerRadius: 16,
                    style: .continuous
                )
                .stroke(
                    TemplePalette.polishedGold
                        .opacity(0.72),
                    lineWidth: 1
                )
            }
            .shadow(
                color: .black.opacity(0.28),
                radius: 10,
                x: 0,
                y: 6
            )
    }
}

struct TempleCard<Content: View>: View {
    let identity: NativeTempleIdentity?
    let content: Content

    init(
        identity: NativeTempleIdentity? = nil,
        @ViewBuilder content: () -> Content
    ) {
        self.identity = identity
        self.content = content()
    }

    var body: some View {
        let cornerRadius =
            identity?.cardCornerRadius ?? 28

        content
            .padding(20)
            .frame(maxWidth: .infinity)
            .background(
                RoundedRectangle(
                    cornerRadius: cornerRadius
                )
                .fill(
                    (identity?.cardFillColor ?? TemplePalette.parchment)
                        .opacity(0.96)
                )
                .shadow(
                    color: .black.opacity(0.28),
                    radius: 18,
                    x: 0,
                    y: 12
                )
            )
            .overlay(
                RoundedRectangle(
                    cornerRadius: cornerRadius
                )
                .stroke(
                    (identity?.glowColor ?? TemplePalette.warmGold)
                        .opacity(0.68),
                    lineWidth: 1.4
                )
            )
    }
}

struct TempleBrandMark: View {
    let identity: NativeTempleIdentity?

    init(
        identity: NativeTempleIdentity? = nil
    ) {
        self.identity = identity
    }

    var body: some View {
        Image("GodIncMark")
            .renderingMode(.original)
            .resizable()
            .scaledToFit()
            .frame(
                width: 108,
                height: 108
            )
            .shadow(
                color:
                    TemplePalette.warmGold
                        .opacity(0.30),
                radius: 12,
                x: 0,
                y: 5
            )
            .accessibilityLabel(
                "God Incorporated"
            )
    }
}

private struct TempleVisitButtonStyle: ButtonStyle {
    let identity: NativeTempleIdentity

    func makeBody(
        configuration: Configuration
    ) -> some View {
        let colors: [Color]
        let cornerRadius: CGFloat

        switch identity {
        case .hathor:
            colors = [
                TemplePalette.emeraldDeep,
                TemplePalette.emerald
            ]
            cornerRadius = 27

        case .moses:
            colors = [
                Color(hex: 0x741A24),
                TemplePalette.crimson
            ]
            cornerRadius = 16
        }

        return configuration.label
            .foregroundStyle(
                TemplePalette.paleGold
            )
            .padding(.vertical, 9)
            .padding(.horizontal, 13)
            .background {
                RoundedRectangle(
                    cornerRadius: cornerRadius,
                    style: .continuous
                )
                .fill(
                    LinearGradient(
                        colors: colors,
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
            }
            .overlay {
                RoundedRectangle(
                    cornerRadius: cornerRadius,
                    style: .continuous
                )
                .stroke(
                    TemplePalette.polishedGold,
                    lineWidth: 1.45
                )
            }
            .shadow(
                color:
                    TemplePalette.polishedGold
                        .opacity(0.12),
                radius: 6,
                x: 0,
                y: 4
            )
            .scaleEffect(
                configuration.isPressed
                    ? 0.985
                    : 1.0
            )
    }
}

private struct TempleChamberActionStyle: ButtonStyle {
    let identity: NativeTempleIdentity
    let emphasized: Bool

    func makeBody(
        configuration: Configuration
    ) -> some View {
        let fill: Color

        if emphasized {
            switch identity {
            case .hathor:
                fill =
                    TemplePalette.emeraldDeep

            case .moses:
                fill =
                    TemplePalette.templeCrimson
            }
        } else {
            fill =
                TemplePalette.midnight
                    .opacity(0.88)
        }

        return configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(
                TemplePalette.paleGold
            )
            .padding(.vertical, 10)
            .padding(.horizontal, 12)
            .background(
                RoundedRectangle(
                    cornerRadius: 11,
                    style: .continuous
                )
                .fill(
                    fill.opacity(
                        configuration.isPressed
                            ? 0.72
                            : 0.96
                    )
                )
            )
            .overlay {
                RoundedRectangle(
                    cornerRadius: 11,
                    style: .continuous
                )
                .stroke(
                    TemplePalette.polishedGold
                        .opacity(0.58),
                    lineWidth: 0.9
                )
            }
    }
}

struct TemplePrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(.white)
            .padding(.vertical, 13)
            .padding(.horizontal, 16)
            .background(
                RoundedRectangle(cornerRadius: 16)
                    .fill(TemplePalette.crimson.opacity(configuration.isPressed ? 0.72 : 1.0))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16)
                    .stroke(TemplePalette.warmGold.opacity(0.75), lineWidth: 1)
            )
            .scaleEffect(configuration.isPressed ? 0.98 : 1.0)
    }
}

struct TempleSecondaryButtonStyle: ButtonStyle {
    let identity: NativeTempleIdentity?

    init(
        identity: NativeTempleIdentity? = nil
    ) {
        self.identity = identity
    }

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(
                identity?.secondaryButtonTextColor
                    ?? TemplePalette.crimson
            )
            .padding(.vertical, 12)
            .padding(.horizontal, 16)
            .background(
                RoundedRectangle(cornerRadius: 16)
                    .fill(
                        (identity?.cardFillColor ?? TemplePalette.parchment)
                            .opacity(
                                configuration.isPressed
                                    ? 0.76
                                    : 0.98
                            )
                    )
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16)
                    .stroke(
                        (identity?.glowColor ?? TemplePalette.warmGold)
                            .opacity(0.72),
                        lineWidth: 1
                    )
            )
            .scaleEffect(
                configuration.isPressed
                    ? 0.98
                    : 1.0
            )
    }
}

struct TempleWebView: UIViewRepresentable {
    let url: URL
    @Binding var selectedTab: Int
    let onAuthChanged: () -> Void

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.applicationNameForUserAgent = "GodIncorporatedIOSApp/1.0"

        let nativeBridgeScript = WKUserScript(
            source: """
            (function() {
                window.GodIncNativeIOS = {
                    platform: "ios",
                    storeKit: true,
                    nativeSupport: true,
                    nativeNavigation: true,
                    supportedProducts: ["ai.godincorporated.seeker.monthly"]
                };
                window.dispatchEvent(new Event("godIncNativeReady"));
            })();
            """,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: false
        )

        let viewportScript = WKUserScript(
            source: """
            (function() {
                var meta = document.querySelector('meta[name="viewport"]');
                if (!meta) {
                    meta = document.createElement('meta');
                    meta.name = 'viewport';
                    document.head.appendChild(meta);
                }
                meta.setAttribute('content', 'width=device-width, initial-scale=1.0, maximum-scale=1.0, viewport-fit=cover');
                document.documentElement.style.webkitTextSizeAdjust = '100%';
                document.body.style.webkitTextSizeAdjust = '100%';
                document.documentElement.style.overflowX = 'hidden';
                document.body.style.overflowX = 'hidden';
            })();
            """,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: false
        )

        configuration.userContentController.addUserScript(nativeBridgeScript)
        configuration.userContentController.addUserScript(viewportScript)
        configuration.userContentController.add(context.coordinator, name: "templeStoreKit")
        configuration.userContentController.add(context.coordinator, name: "templeNativeNav")

        let webView = WKWebView(frame: .zero, configuration: configuration)
        context.coordinator.webView = webView

        webView.allowsBackForwardNavigationGestures = true
        webView.navigationDelegate = context.coordinator
        webView.scrollView.delegate = context.coordinator
        webView.scrollView.minimumZoomScale = 1.0
        webView.scrollView.maximumZoomScale = 1.0
        webView.scrollView.zoomScale = 1.0
        webView.scrollView.bouncesZoom = false
        webView.scrollView.contentInsetAdjustmentBehavior = .automatic

        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        func navigationComparisonURL(_ candidate: URL?) -> String? {
            guard let candidate else {
                return nil
            }

            guard var components = URLComponents(
                url: candidate,
                resolvingAgainstBaseURL: false
            ) else {
                return candidate.absoluteString
            }

            // "auth" is a one-time native navigation instruction.
            // The web page removes it after opening the requested auth modal,
            // so it must not trigger a SwiftUI WebView reload afterward.
            components.queryItems = components.queryItems?.filter {
                $0.name != "auth"
            }

            return components.url?.absoluteString ?? candidate.absoluteString
        }

        if navigationComparisonURL(webView.url) != navigationComparisonURL(url) {
            webView.load(URLRequest(url: url))
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(
            selectedTab: $selectedTab,
            onAuthChanged: onAuthChanged
        )
    }

    final class Coordinator: NSObject, WKNavigationDelegate, UIScrollViewDelegate, WKScriptMessageHandler {
        weak var webView: WKWebView?
        @Binding var selectedTab: Int
        let onAuthChanged: () -> Void

        init(
            selectedTab: Binding<Int>,
            onAuthChanged: @escaping () -> Void
        ) {
            self._selectedTab = selectedTab
            self.onAuthChanged = onAuthChanged
        }

        func viewForZooming(in scrollView: UIScrollView) -> UIView? {
            nil
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            webView.scrollView.setZoomScale(1.0, animated: false)
            webView.evaluateJavaScript("""
                window.GodIncNativeIOS = {
                    platform: "ios",
                    storeKit: true,
                    nativeSupport: true,
                    nativeNavigation: true,
                    supportedProducts: ["ai.godincorporated.seeker.monthly"]
                };
                window.dispatchEvent(new Event("godIncNativeReady"));
                document.documentElement.style.overflowX = 'hidden';
                document.body.style.overflowX = 'hidden';
                document.body.style.maxWidth = '100vw';
            """)
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let requestURL = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }

            if requestURL.path == "/support" {
                DispatchQueue.main.async {
                    self.selectedTab = 3
                }
                decisionHandler(.cancel)
                return
            }

            decisionHandler(.allow)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            print("TempleWebView navigation failed: \(error.localizedDescription)")
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            print("TempleWebView provisional navigation failed: \(error.localizedDescription)")
        }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            if message.name == "templeStoreKit" {
                DispatchQueue.main.async {
                    self.selectedTab = 3
                }
                return
            }

            if message.name == "templeNativeNav" {
                let payload = message.body as? [String: Any]
                let destination = payload?["destination"] as? String

                if destination == "authChanged" {
                    DispatchQueue.main.async {
                        self.onAuthChanged()
                    }
                    return
                }

                DispatchQueue.main.async {
                    self.selectedTab = 1
                }
                return
            }
        }
    }
}

#Preview {
    ContentView()
}
