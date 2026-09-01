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

    var subtitle: String? {
        switch self {
        case .hathor:
            return nil
        case .moses:
            return "Tent of Meeting"
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
                TemplePalette.midnight,
                TemplePalette.malachiteDeep,
                TemplePalette.malachite
            ]
        case .moses:
            return [
                TemplePalette.midnight,
                TemplePalette.deepBlue,
                TemplePalette.royalBlue
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
                },
                onOpenTempleText: {
                    preferredInputMode = "text"
                    templeWebDestination = "temple"
                    templeEntryNonce += 1
                    selectedTab = 2
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
        .tint(TemplePalette.warmGold)
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
    let onOpenTempleText: () -> Void

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

    private enum VoicePlaybackOrigin: Equatable {
        case liveTurn
        case replay
    }

    private let noSpeechTimeoutSeconds: TimeInterval = 8.0
    private let silenceSubmitSeconds: TimeInterval = 4.0
    private let backupSubmitAfterSpeechSeconds: TimeInterval = 18.0
    private let hardMaxRecordingSeconds: TimeInterval = 24.0
    private let speechPowerThresholdDB: Float = -42.0
    private let requiredSpeechStartTicks = 2
    private let absoluteQuietThresholdDB: Float = -48.0
    private let quietDropFromSpeechDB: Float = 10.0
    private let meterTickSeconds: TimeInterval = 0.25

    var body: some View {
        let templeIdentity =
            NativeTempleIdentity(
                oracleVoice: oracleVoice
            )

        NavigationStack {
            TempleScreen(identity: templeIdentity) {
                ScrollView {
                    VStack(spacing: 22) {
                        TempleBrandMark(identity: templeIdentity)
                            .padding(.top, 28)

                        VStack(spacing: 8) {
                            Text(templeIdentity.title)
                                .font(.system(size: 32, weight: .bold, design: .serif))
                                .foregroundStyle(TemplePalette.paleGold)
                                .multilineTextAlignment(.center)

                            if let subtitle = templeIdentity.subtitle {
                                Text(subtitle)
                                    .font(.headline)
                                    .foregroundStyle(.white.opacity(0.82))
                            }
                        }

                        NativeOracleArtwork(identity: templeIdentity)

                        oracleVoiceSwitcher

                        TempleCard(identity: templeIdentity) {
                            VStack(spacing: 16) {
                                Text(statusTitle)
                                    .font(.title3.weight(.bold))
                                    .foregroundStyle(TemplePalette.ink)
                                    .multilineTextAlignment(.center)

                                Text(statusMessage)
                                    .foregroundStyle(TemplePalette.ink.opacity(0.72))
                                    .multilineTextAlignment(.center)

                                if isRecording {
                                }

                                if !transcript.isEmpty || !answer.isEmpty || !recoveryMessage.isEmpty {
                                    ScrollView {
                                        VStack(alignment: .leading, spacing: 12) {
                                            if !transcript.isEmpty {
                                                VStack(alignment: .leading, spacing: 6) {
                                                    Text("You said")
                                                        .font(.caption.weight(.bold))
                                                        .foregroundStyle(TemplePalette.crimson)
                                                    Text(compactVoiceMessage(transcript, limit: 700))
                                                        .foregroundStyle(TemplePalette.ink)
                                                }
                                            }

                                            if !answer.isEmpty {
                                                VStack(alignment: .leading, spacing: 6) {
                                                    Text("\(oracleVoice) answered")
                                                        .font(.caption.weight(.bold))
                                                        .foregroundStyle(TemplePalette.crimson)
                                                    Text(answer)
                                                        .foregroundStyle(TemplePalette.ink)
                                                }
                                            }

                                            if !recoveryMessage.isEmpty {
                                                VStack(alignment: .leading, spacing: 6) {
                                                    Text("What happened")
                                                        .font(.caption.weight(.bold))
                                                        .foregroundStyle(TemplePalette.crimson)
                                                    Text(compactVoiceMessage(recoveryMessage, limit: 700))
                                                        .foregroundStyle(TemplePalette.ink.opacity(0.74))
                                                }
                                            }
                                        }
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                    }
                                    .frame(maxHeight: 260)
                                    .padding(12)
                                    .background(
                                        RoundedRectangle(cornerRadius: 16)
                                            .fill(Color.white.opacity(0.34))
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
                                        Text(isPlayingAudio ? "Oracle Voice Speaking..." : "Replay Oracle Voice")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(TempleSecondaryButtonStyle())
                                    .disabled(
                                        isWorking
                                            || isRecording
                                            || isPlayingAudio
                                            || isContinuousConversationActive
                                    )
                                }

                                if isContinuousConversationActive {
                                    Button {
                                        endContinuousConversation()
                                    } label: {
                                        Text("End Conversation")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(TempleSecondaryButtonStyle())
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
                                    .buttonStyle(TemplePrimaryButtonStyle())
                                    .disabled(isWorking || isRecording)

                                    Button {
                                        resetVoiceSession()
                                    } label: {
                                        Text("Reset Voice Session")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(TempleSecondaryButtonStyle())
                                    .disabled(isWorking || isRecording)

                                    Button {
                                        stopVoiceSessionActivity(clearExchange: false)
                                        onOpenTempleText()
                                    } label: {
                                        Text("Switch to Text Entry")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(TempleSecondaryButtonStyle())
                                    .disabled(isWorking || isRecording)

                                } else if isRecording {
                                    listeningIndicator

                                    Button {
                                        Task {
                                            await stopAndSubmitRecording()
                                        }
                                    } label: {
                                        Text("Stop and Consult the Oracle")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(TemplePrimaryButtonStyle())
                                    .disabled(isWorking)
                                } else {
                                    Button {
                                        isContinuousConversationActive = true

                                        Task {
                                            await startRecording()
                                        }
                                    } label: {

                                        if isWorking {
                                            ProgressView()
                                                .frame(maxWidth: .infinity)
                                        } else {
                                            Text("Start Conversation")
                                                .frame(maxWidth: .infinity)
                                        }
                                    }
                                    .buttonStyle(TemplePrimaryButtonStyle())
                                    .disabled(
                                        isWorking
                                            || isPlayingAudio
                                            || isContinuousConversationActive
                                    )

                                    Button {
                                        stopVoiceSessionActivity(clearExchange: false)
                                        onOpenTempleText()
                                    } label: {
                                        Text("Switch to Text Entry")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(TempleSecondaryButtonStyle())
                                    .disabled(isWorking || isRecording)
                                }
                            }
                        }

                        Text("Start Conversation opens the microphone for your first question. End Conversation stops the active voice session.")
                            .font(.footnote)
                            .foregroundStyle(.white.opacity(0.72))
                            .multilineTextAlignment(.center)
                            .padding(.bottom, 20)
                    }
                    .padding(.horizontal, 18)
                }
            }
            .navigationTitle("Voice")
            .navigationBarTitleDisplayMode(.inline)
        }
        .onDisappear {
            stopVoiceSessionActivity(clearExchange: false)
        }
    }

    private var oracleVoiceSwitcher: some View {
        let templeIdentity =
            NativeTempleIdentity(
                oracleVoice: oracleVoice
            )

        return Button {
            Task {
                await changeOracleVoice(
                    to: templeIdentity.destinationOracleVoice
                )
            }
        } label: {
            Text(templeIdentity.visitDestinationTitle)
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(
            TempleSecondaryButtonStyle(
                identity: templeIdentity
            )
        )
        .disabled(
            isWorking
                || isRecording
                || isPlayingAudio
        )
        .opacity(
            (isWorking || isRecording || isPlayingAudio)
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

            let oracleAnswer = try await askOracle(
                question: spokenQuestion,
                voice: oracleVoice
            )

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
                statusMessage = "The written answer is ready. Provider voice is being prepared."
                return true
            }

            guard answerIsCurrent else {
                return
            }

            await speakOracleAnswerProviderFirst(
                oracleAnswer,
                deity: oracleVoice,
                origin: .liveTurn,
                expectedGeneration: generation
            )
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
        voice: String
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
                input_mode: "voice",
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

    private func askOracle(question: String, voice: String) async throws -> String {
        switch ApplePCCInferenceAdapter.availability() {
        case .unavailable:
            return try await askOracleServerFallback(
                question: question,
                voice: voice,
                pccFallbackCode: "pcc_preflight_unavailable"
            )

        case .available:
            break
        }

        // Once prepare succeeds, this interaction UUID is server-owned
        // pending state and must be explicitly completed or abandoned.
        let packet = try await prepareOracleInference(
            question: question,
            voice: voice
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
                pccFallbackCode: "pcc_execution_failed",
                abandonedInteractionID: packet.interaction_id
            )
        }
    }

    private func askOracleServerFallback(
        question: String,
        voice: String,
        pccFallbackCode: String? = nil,
        abandonedInteractionID: String? = nil
    ) async throws -> String {
        let nativeAnonymousUserID = NativeAnonymousIdentity.currentID
        var request = await authenticatedVoiceRequest(url: TempleEnvironment.voiceAskURL, method: "POST")
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

            if origin == .replay && isContinuousConversationActive {
                return nil
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

private struct NativeOracleArtwork: View {
    let identity: NativeTempleIdentity

    var body: some View {
        ZStack(alignment: .bottom) {
            Image(
                identity.oracleArtworkAssetName
            )
            .resizable()
            .scaledToFill()
            .frame(
                width: 226,
                height: 226
            )
            .clipped()

            LinearGradient(
                colors: [
                    .clear,
                    identity.screenGradientColors[1]
                        .opacity(0.76)
                ],
                startPoint: .center,
                endPoint: .bottom
            )
            .frame(
                width: 226,
                height: 82
            )
        }
        .background(
            identity.cardFillColor
                .opacity(0.18)
        )
        .clipShape(
            RoundedRectangle(
                cornerRadius:
                    identity.cardCornerRadius,
                style: .continuous
            )
        )
        .overlay {
            RoundedRectangle(
                cornerRadius:
                    identity.cardCornerRadius,
                style: .continuous
            )
            .stroke(
                identity.glowColor
                    .opacity(0.86),
                lineWidth: 1.6
            )
        }
        .overlay(alignment: .top) {
            VStack(spacing: 3) {
                Rectangle()
                    .fill(
                        TemplePalette.warmGold
                            .opacity(0.78)
                    )
                    .frame(height: 2)

                Rectangle()
                    .fill(
                        identity.accentColor
                            .opacity(0.46)
                    )
                    .frame(height: 5)
            }
            .padding(.horizontal, 10)
            .padding(.top, 7)
        }
        .shadow(
            color: .black.opacity(0.30),
            radius: 16,
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
                ZStack {
                    HStack {
                        sanctuaryRail
                        Spacer()
                        sanctuaryRail
                    }
                    .padding(.horizontal, 7)
                    .padding(.vertical, 66)

                    VStack(spacing: 4) {
                        Rectangle()
                            .fill(
                                TemplePalette.warmGold
                                    .opacity(0.62)
                            )
                            .frame(height: 2)

                        Rectangle()
                            .fill(
                                TemplePalette.malachite
                                    .opacity(0.34)
                            )
                            .frame(height: 7)
                            .overlay(
                                Rectangle()
                                    .stroke(
                                        TemplePalette.paleGold
                                            .opacity(0.42),
                                        lineWidth: 0.75
                                    )
                            )

                        Spacer()

                        Rectangle()
                            .fill(
                                TemplePalette.malachiteDeep
                                    .opacity(0.58)
                            )
                            .frame(height: 10)
                            .overlay(
                                Rectangle()
                                    .stroke(
                                        TemplePalette.warmGold
                                            .opacity(0.46),
                                        lineWidth: 0.75
                                    )
                            )
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 7)
                }

            case .moses:
                EmptyView()
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private var sanctuaryRail: some View {
        VStack(spacing: 0) {
            RoundedRectangle(cornerRadius: 2)
                .fill(
                    TemplePalette.warmGold.opacity(0.72)
                )
                .frame(width: 18, height: 7)

            Rectangle()
                .fill(
                    LinearGradient(
                        colors: [
                            TemplePalette.warmGold.opacity(0.66),
                            TemplePalette.malachite.opacity(0.30),
                            TemplePalette.warmGold.opacity(0.48)
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: 8)
                .overlay(
                    Rectangle()
                        .stroke(
                            TemplePalette.paleGold.opacity(0.44),
                            lineWidth: 0.75
                        )
                )

            RoundedRectangle(cornerRadius: 2)
                .fill(
                    TemplePalette.warmGold.opacity(0.62)
                )
                .frame(width: 20, height: 8)
        }
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
