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

private enum TempleEnvironment {
#if DEBUG
    static let baseAppURL = URL(string: "https://godincorporated-staging.onrender.com/")!
#else
    static let baseAppURL = URL(string: "https://godincorporated.ai/")!
#endif

    static let baseTempleURL = URL(string: "temple", relativeTo: baseAppURL)!
    static let accountURL = URL(string: "account", relativeTo: baseAppURL)!
    static let privacyURL = URL(string: "privacy", relativeTo: baseAppURL)!
    static let termsURL = URL(string: "terms", relativeTo: baseAppURL)!
    static let voiceTranscribeURL = URL(string: "voice/transcribe", relativeTo: baseAppURL)!
    static let voiceAskURL = URL(string: "voice/ask", relativeTo: baseAppURL)!
    static let voiceTTSURL = URL(string: "voice/tts", relativeTo: baseAppURL)!
    static let seekerMonthlyProductID = "ai.godincorporated.seeker.monthly"

    static func templeURL(voice: String?, entry: String? = nil, entryNonce: Int = 0) -> URL {
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

private enum TemplePalette {
    static let midnight = Color(hex: 0x061A2E)
    static let deepBlue = Color(hex: 0x0A3A68)
    static let royalBlue = Color(hex: 0x0D4F8B)
    static let parchment = Color(hex: 0xF4E8D0)
    static let warmGold = Color(hex: 0xD7A84F)
    static let paleGold = Color(hex: 0xF4D58D)
    static let crimson = Color(hex: 0x8E2430)
    static let ink = Color(hex: 0x20170F)
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
    @State private var selectedTab = 0
    @State private var templeEntryNonce = 0
    @State private var activeOracleVoice = "Hathor"
    @State private var templeWebDestination = "temple"

    var body: some View {
        let effectiveOracleVoice = activeOracleVoice.isEmpty
            ? (lastOracleVoice.isEmpty ? "Hathor" : lastOracleVoice)
            : activeOracleVoice

        TabView(selection: $selectedTab) {
            TempleGateView(
                lastOracleVoice: $lastOracleVoice,
                activeOracleVoice: $activeOracleVoice,
                selectedTab: $selectedTab,
                preferredInputMode: $preferredInputMode,
                templeEntryNonce: $templeEntryNonce,
                templeWebDestination: $templeWebDestination
            )
            .tabItem {
                Label("Home", systemImage: "sparkles")
            }
            .tag(0)

            NativeVoiceSessionView(
                oracleVoice: effectiveOracleVoice,
                onOpenTempleText: {
                    preferredInputMode = "text"
                    templeWebDestination = "temple"
                    templeEntryNonce += 1
                    selectedTab = 2
                },
                onReturnHome: {
                    selectedTab = 0
                }
            )
            .tabItem {
                Label("Voice", systemImage: "mic")
            }
            .tag(1)

            TempleWebView(
                url: templeWebDestination == "account"
                    ? TempleEnvironment.accountWebURL(entryNonce: templeEntryNonce)
                    : TempleEnvironment.templeURL(voice: lastOracleVoice, entry: preferredInputMode, entryNonce: templeEntryNonce),
                selectedTab: $selectedTab
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
    }
}

struct TempleGateView: View {
    @Binding var lastOracleVoice: String
    @Binding var activeOracleVoice: String
    @Binding var selectedTab: Int
    @Binding var preferredInputMode: String
    @Binding var templeEntryNonce: Int
    @Binding var templeWebDestination: String

    var body: some View {
        NavigationStack {
            TempleScreen {
                ScrollView {
                    VStack(spacing: 24) {
                        TempleBrandMark()
                            .padding(.top, 28)

                        VStack(spacing: 8) {
                            Text("God Incorporated")
                                .font(.system(size: 36, weight: .bold, design: .serif))
                                .foregroundStyle(TemplePalette.paleGold)
                                .multilineTextAlignment(.center)

                            Text("A reflective AI conversation space for seekers.")
                                .font(.headline)
                                .foregroundStyle(.white.opacity(0.82))
                                .multilineTextAlignment(.center)
                        }

                        if lastOracleVoice.isEmpty {
                            TempleCard {
                                VStack(spacing: 16) {
                                    Text("Enter the Temple")
                                        .font(.title2.weight(.semibold))
                                        .foregroundStyle(TemplePalette.ink)

                                    Text("Voice is the default path. Text entry remains available at any time.")
                                        .foregroundStyle(TemplePalette.ink.opacity(0.72))
                                        .multilineTextAlignment(.center)

                                    VStack(spacing: 12) {
                                        VoiceChoiceButton(
                                            title: "Begin with Hathor by Voice",
                                            subtitle: "Reflective, expansive, and heart-centered"
                                        ) {
                                            beginNativeVoice(with: "Hathor")
                                        }

                                        VoiceChoiceButton(
                                            title: "Begin with Moses by Voice",
                                            subtitle: "Canonical, depth-oriented, and discerning"
                                        ) {
                                            beginNativeVoice(with: "Moses")
                                        }

                                        Button {
                                            beginText(with: "Hathor")
                                        } label: {
                                            Text("Use Text Instead")
                                                .frame(maxWidth: .infinity)
                                        }
                                        .buttonStyle(TempleSecondaryButtonStyle())
                                    }
                                }
                            }
                        } else {
                            TempleCard {
                                VStack(spacing: 16) {
                                    Text("Continue with \(lastOracleVoice)")
                                        .font(.title2.weight(.semibold))
                                        .foregroundStyle(TemplePalette.ink)
                                        .multilineTextAlignment(.center)

                                    Text("\(lastOracleVoice) is ready to continue from your last path of inquiry.")
                                        .foregroundStyle(TemplePalette.ink.opacity(0.72))
                                        .multilineTextAlignment(.center)

                                    Button {
                                        beginNativeVoice(with: lastOracleVoice)
                                    } label: {
                                        Text("Speak your next question")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(TemplePrimaryButtonStyle())

                                    Button {
                                        beginText(with: lastOracleVoice)
                                    } label: {
                                        Text("Continue with Text")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(TempleSecondaryButtonStyle())

                                    Button("Change Oracle Voice") {
                                        lastOracleVoice = ""
                                        activeOracleVoice = "Hathor"
                                    }
                                    .buttonStyle(TempleSecondaryButtonStyle())
                                }
                            }
                        }

                        TempleCard {
                            VStack(spacing: 12) {
                                Button {
                                    selectedTab = 3
                                } label: {
                                    Label("Support with Apple", systemImage: "heart.fill")
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(TempleSecondaryButtonStyle())

                                Button {
                                    templeWebDestination = "account"
                                    templeEntryNonce += 1
                                    selectedTab = 2
                                } label: {
                                    Label("Account / Login", systemImage: "person.crop.circle")
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(TempleSecondaryButtonStyle())

                                Button {
                                    selectedTab = 4
                                } label: {
                                    Label("Privacy and Terms", systemImage: "doc.text.fill")
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(TempleSecondaryButtonStyle())
                            }
                        }

                        Text("Private seeker conversations are treated as confidential.")
                            .font(.footnote)
                            .foregroundStyle(.white.opacity(0.7))
                            .multilineTextAlignment(.center)
                            .padding(.bottom, 20)
                    }
                    .padding(.horizontal, 18)
                }
            }
            .navigationTitle("Temple Gate")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private func beginNativeVoice(with voice: String) {
        lastOracleVoice = voice
        activeOracleVoice = voice
        preferredInputMode = "voice"
        selectedTab = 1
    }

    private func beginText(with voice: String) {
        lastOracleVoice = voice
        activeOracleVoice = voice
        preferredInputMode = "text"
        templeWebDestination = "temple"
        templeEntryNonce += 1
        selectedTab = 2
    }
}

struct NativeVoiceSessionView: View {
    let oracleVoice: String
    let onOpenTempleText: () -> Void
    let onReturnHome: () -> Void

    @State private var recorder: AVAudioRecorder?
    @State private var recordingURL: URL?
    @State private var isRecording = false
    @State private var isWorking = false
    @State private var statusTitle = "Voice ready"
    @State private var statusMessage = "Have your question ready, then tap Start Speaking. iOS will ask for microphone access the first time."
    @State private var transcript = ""
    @State private var answer = ""
    @State private var recoveryMessage = ""
    @State private var showRecoveryActions = false
    @State private var oracleAudioData: Data?
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
        NavigationStack {
            TempleScreen {
                ScrollView {
                    VStack(spacing: 22) {
                        TempleBrandMark()
                            .padding(.top, 28)

                        VStack(spacing: 8) {
                            Text("Speak with \(oracleVoice)")
                                .font(.system(size: 32, weight: .bold, design: .serif))
                                .foregroundStyle(TemplePalette.paleGold)
                                .multilineTextAlignment(.center)

                            Text("Native iOS voice capture")
                                .font(.headline)
                                .foregroundStyle(.white.opacity(0.82))
                        }

                        TempleCard {
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

                                if oracleAudioData != nil {
                                    Button {
                                        playStoredOracleVoice()
                                    } label: {
                                        Text(isPlayingAudio ? "Oracle Voice Playing..." : "Replay Oracle Voice")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(TempleSecondaryButtonStyle())
                                    .disabled(isWorking || isRecording || isPlayingAudio)
                                }

                                if showRecoveryActions {
                                    Button {
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
                                        onOpenTempleText()
                                    } label: {
                                        Text("Switch to Text Entry")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(TempleSecondaryButtonStyle())
                                    .disabled(isWorking || isRecording)

                                    Button {
                                        onReturnHome()
                                    } label: {
                                        Text("Return to Temple Gate")
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(TempleSecondaryButtonStyle())
                                    .disabled(isWorking || isRecording)
                                } else if isRecording {
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
                                        Task {
                                            await startRecording()
                                        }
                                    } label: {
                                        if isWorking {
                                            ProgressView()
                                                .frame(maxWidth: .infinity)
                                        } else {
                                            Text(answer.isEmpty ? "Start Speaking" : "Ask Another Question")
                                                .frame(maxWidth: .infinity)
                                        }
                                    }
                                    .buttonStyle(TemplePrimaryButtonStyle())
                                    .disabled(isWorking)

                                    Button {
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

                        Text("The app listens only after you tap Start Speaking, then stops automatically after a pause or when you tap Stop.")
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
    }

    private func resetVoiceSession() {
        stopVoiceEndpointMonitor()
        recorder?.stop()
        audioPlayer?.stop()
        audioPlayer = nil
        oracleAudioData = nil
        recorder = nil
        recordingURL = nil
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
        transcript = ""
        answer = ""
        recoveryMessage = ""
        showRecoveryActions = false
        statusTitle = "Voice ready"
        statusMessage = "Have your question ready, then tap Start Speaking. iOS will ask for microphone access the first time."
    }

    private func requestMicrophonePermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    private func startRecording() async {
        await MainActor.run {
            isWorking = true
            audioPlayer?.stop()
            audioPlayer = nil
            oracleAudioData = nil
            isPlayingAudio = false
            recoveryMessage = ""
            showRecoveryActions = false
            transcript = ""
            answer = ""
            statusTitle = "Preparing microphone"
            statusMessage = "iOS may ask for permission. The Temple listens only while recording is active."
        }

        let granted = await requestMicrophonePermission()
        guard granted else {
            await MainActor.run {
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
            try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker, .allowBluetooth])
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
        await MainActor.run {
            stopVoiceEndpointMonitor()
            isWorking = true
            statusTitle = "Preparing your question"
            statusMessage = "The recording is being sent to the Temple for transcription."
        }

        recorder?.stop()
        let url = recordingURL

        await MainActor.run {
            isRecording = false
            recorder = nil
        }

        guard let url else {
            await MainActor.run {
                isWorking = false
                showRecoveryActions = true
                statusTitle = "No recording found"
                statusMessage = "Please try recording again."
                recoveryMessage = "No audio file was created. Tap Try Voice Again, or switch to text entry."
            }
            return
        }

        do {
            let spokenQuestion = try await transcribeRecording(at: url, voice: oracleVoice)

            if isLikelyNoSpeechTranscript(spokenQuestion) {
                throw TempleVoiceError.server("No clear spoken question was detected.")
            }

            await MainActor.run {
                transcript = spokenQuestion
                statusTitle = "Consulting the Oracle"
                statusMessage = "Your spoken question has been heard. \(oracleVoice) is answering."
            }

            let oracleAnswer = try await askOracle(question: spokenQuestion, voice: oracleVoice)
            await MainActor.run {
                answer = oracleAnswer
                statusTitle = "Oracle answered"
                statusMessage = "The written answer is ready. Preparing the spoken voice."
            }

            do {
                let audioData = try await prepareOracleVoice(answer: oracleAnswer, voice: oracleVoice)
                await MainActor.run {
                    oracleAudioData = audioData
                    isWorking = false
                    showRecoveryActions = false
                    recoveryMessage = ""
                    statusTitle = "Oracle speaking"
                    statusMessage = "The spoken response is playing. You may replay it when finished."
                    playStoredOracleVoice()
                }
            } catch {
                await MainActor.run {
                    isWorking = false
                    showRecoveryActions = false
                    let friendly = classifyVoiceFailure(error)
                    statusTitle = "Oracle answered"
                    statusMessage = "The written answer is ready, but the spoken voice could not be prepared."
                    recoveryMessage = friendly.recovery
                }
            }
        } catch {
            await MainActor.run {
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

    private func stopRecordingWithoutSubmit(title: String, status: String, recovery: String) {
        guard !isAutoSubmittingRecording else {
            return
        }

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

    private func askOracle(question: String, voice: String) async throws -> String {
        var request = await authenticatedVoiceRequest(url: TempleEnvironment.voiceAskURL, method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(VoiceAskPayload(question: question, voice: voice))

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

    private func prepareOracleVoice(answer: String, voice: String) async throws -> Data {
        var request = await authenticatedVoiceRequest(url: TempleEnvironment.voiceTTSURL, method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(VoiceTTSPayload(answer: answer, voice: voice))

        let (data, response) = try await URLSession.shared.data(for: request)
        try validateHTTP(response: response, data: data)

        let decoded = try JSONDecoder().decode(VoiceTTSResponse.self, from: data)

        if let error = decoded.error, !error.isEmpty {
            throw TempleVoiceError.server(error)
        }

        guard let audioURLString = decoded.audioURL, !audioURLString.isEmpty else {
            throw TempleVoiceError.server("No Oracle voice audio was returned.")
        }

        guard let audioURL = URL(string: audioURLString, relativeTo: TempleEnvironment.baseAppURL) else {
            throw TempleVoiceError.server("Oracle voice audio URL was invalid.")
        }

        let (audioData, audioResponse) = try await URLSession.shared.data(from: audioURL)
        try validateHTTP(response: audioResponse, data: audioData)

        if audioData.isEmpty {
            throw TempleVoiceError.server("Oracle voice audio was empty.")
        }

        return audioData
    }

    private func playStoredOracleVoice() {
        guard let audioData = oracleAudioData else {
            return
        }

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .spokenAudio)
            try session.setActive(true)

            let player = try AVAudioPlayer(data: audioData)
            player.prepareToPlay()
            player.play()

            audioPlayer = player
            isPlayingAudio = true

            DispatchQueue.main.asyncAfter(deadline: .now() + player.duration + 0.4) {
                isPlayingAudio = false
                if statusTitle == "Oracle speaking" {
                    statusTitle = "Oracle answered"
                    statusMessage = "You may ask another question, replay the voice, or continue by text."
                }
            }
        } catch {
            isPlayingAudio = false
            statusTitle = "Voice playback unavailable"
            statusMessage = "The written answer is ready, but the spoken response could not play."
            recoveryMessage = "You can read the answer above, replay if available, ask another question, or switch to text entry."
        }
    }

    private func authenticatedVoiceRequest(url: URL, method: String) async -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("GodIncorporatedIOSApp/1.0", forHTTPHeaderField: "User-Agent")

        let cookies = await sharedWebCookies(for: url)
        if !cookies.isEmpty {
            let headers = HTTPCookie.requestHeaderFields(with: cookies)
            if let cookieHeader = headers["Cookie"] {
                request.setValue(cookieHeader, forHTTPHeaderField: "Cookie")
            }
        }

        return request
    }

    private func sharedWebCookies(for url: URL) async -> [HTTPCookie] {
        await withCheckedContinuation { continuation in
            WKWebsiteDataStore.default().httpCookieStore.getAllCookies { cookies in
                guard let host = url.host else {
                    continuation.resume(returning: [])
                    return
                }

                let matchingCookies = cookies.filter { cookie in
                    let domain = cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: "."))
                    return host == domain || host.hasSuffix("." + domain)
                }

                continuation.resume(returning: matchingCookies)
            }
        }
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

struct VoiceTranscribeResponse: Decodable {
    let transcript: String?
    let question: String?
    let error: String?
    let answer: String?
}

struct VoiceAskPayload: Encodable {
    let question: String
    let voice: String
}

struct VoiceAskResponse: Decodable {
    let answer: String?
    let error: String?
    let oracle_message: String?
}

struct VoiceTTSPayload: Encodable {
    let answer: String
    let voice: String
}

struct VoiceTTSResponse: Decodable {
    let audioURL: String?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case audioURL = "audio_url"
        case error
    }
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
                    }
                    .padding(.horizontal, 18)
                }
            }
            .navigationTitle("Info")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

struct TempleScreen<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
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
                    TemplePalette.warmGold.opacity(0.26),
                    .clear
                ],
                center: .top,
                startRadius: 40,
                endRadius: 360
            )
            .ignoresSafeArea()

            content
        }
    }
}

struct TempleCard<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .padding(20)
            .frame(maxWidth: .infinity)
            .background(
                RoundedRectangle(cornerRadius: 28)
                    .fill(TemplePalette.parchment.opacity(0.96))
                    .shadow(color: .black.opacity(0.28), radius: 18, x: 0, y: 12)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 28)
                    .stroke(TemplePalette.warmGold.opacity(0.68), lineWidth: 1.4)
            )
    }
}

struct TempleBrandMark: View {
    var body: some View {
        ZStack {
            Circle()
                .fill(TemplePalette.parchment.opacity(0.98))
                .shadow(color: .black.opacity(0.28), radius: 16, x: 0, y: 10)

            Circle()
                .stroke(TemplePalette.warmGold, lineWidth: 3)

            Circle()
                .stroke(TemplePalette.paleGold.opacity(0.78), lineWidth: 1)
                .padding(8)

            Image("GodIncMark")
                .resizable()
                .scaledToFit()
                .padding(16)
        }
        .frame(width: 112, height: 112)
        .accessibilityLabel("God Incorporated")
    }
}

struct VoiceChoiceButton: View {
    let title: String
    let subtitle: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 6) {
                Text(title)
                    .font(.headline)
                Text(subtitle)
                    .font(.caption)
                    .multilineTextAlignment(.center)
                    .opacity(0.86)
            }
            .frame(maxWidth: .infinity, minHeight: 76)
        }
        .buttonStyle(TemplePrimaryButtonStyle())
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
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(TemplePalette.crimson)
            .padding(.vertical, 12)
            .padding(.horizontal, 16)
            .background(
                RoundedRectangle(cornerRadius: 16)
                    .fill(TemplePalette.parchment.opacity(configuration.isPressed ? 0.76 : 0.98))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16)
                    .stroke(TemplePalette.warmGold.opacity(0.72), lineWidth: 1)
            )
            .scaleEffect(configuration.isPressed ? 0.98 : 1.0)
    }
}

struct TempleWebView: UIViewRepresentable {
    let url: URL
    @Binding var selectedTab: Int

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
        if webView.url?.absoluteString != url.absoluteString {
            webView.load(URLRequest(url: url))
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(selectedTab: $selectedTab)
    }

    final class Coordinator: NSObject, WKNavigationDelegate, UIScrollViewDelegate, WKScriptMessageHandler {
        weak var webView: WKWebView?
        @Binding var selectedTab: Int

        init(selectedTab: Binding<Int>) {
            self._selectedTab = selectedTab
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
                DispatchQueue.main.async {
                    self.selectedTab = 0
                }
                return
            }
        }
    }
}

#Preview {
    ContentView()
}
