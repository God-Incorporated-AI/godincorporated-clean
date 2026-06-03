//
//  ContentView.swift
//  Temple
//
//  v11.4J: Native Temple Gate, native support surface, Terms/EULA links.
//

import SwiftUI
import WebKit
import StoreKit

private enum TempleEnvironment {
#if DEBUG
    static let baseTempleURL = URL(string: "https://godincorporated-staging.onrender.com/temple")!
#else
    static let baseTempleURL = URL(string: "https://godincorporated.ai/temple")!
#endif

    static let privacyURL = URL(string: "https://godincorporated.ai/privacy")!
    static let termsURL = URL(string: "https://godincorporated.ai/terms")!
    static let seekerMonthlyProductID = "ai.godincorporated.seeker.monthly"

    static func templeURL(voice: String?) -> URL {
        guard let voice, !voice.isEmpty else {
            return baseTempleURL
        }

        var components = URLComponents(url: baseTempleURL, resolvingAgainstBaseURL: false)
        components?.queryItems = [
            URLQueryItem(name: "voice", value: voice.lowercased()),
            URLQueryItem(name: "native", value: "ios")
        ]
        return components?.url ?? baseTempleURL
    }
}

struct ContentView: View {
    @AppStorage("lastOracleVoice") private var lastOracleVoice: String = ""
    @State private var selectedTab = 0

    var body: some View {
        TabView(selection: $selectedTab) {
            TempleGateView(
                lastOracleVoice: $lastOracleVoice,
                selectedTab: $selectedTab
            )
            .tabItem {
                Label("Home", systemImage: "sparkles")
            }
            .tag(0)

            TempleWebView(
                url: TempleEnvironment.templeURL(voice: lastOracleVoice),
                selectedTab: $selectedTab
            )
            .tabItem {
                Label("Temple", systemImage: "bubble.left.and.bubble.right")
            }
            .tag(1)

            NativeSupportView()
                .tabItem {
                    Label("Support", systemImage: "heart")
                }
                .tag(2)

            NativeInfoView()
                .tabItem {
                    Label("Info", systemImage: "info.circle")
                }
                .tag(3)
        }
    }
}

struct TempleGateView: View {
    @Binding var lastOracleVoice: String
    @Binding var selectedTab: Int

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 22) {
                    VStack(spacing: 8) {
                        Text("God Incorporated")
                            .font(.largeTitle.weight(.bold))
                            .multilineTextAlignment(.center)

                        Text("A reflective AI conversation space for seekers.")
                            .font(.headline)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .padding(.top, 32)

                    if lastOracleVoice.isEmpty {
                        VStack(spacing: 14) {
                            Text("Enter the Temple")
                                .font(.title2.weight(.semibold))

                            Text("Choose your first Oracle voice.")
                                .foregroundStyle(.secondary)

                            HStack(spacing: 12) {
                                VoiceChoiceButton(title: "Hathor", subtitle: "Reflective and expansive") {
                                    lastOracleVoice = "Hathor"
                                    selectedTab = 1
                                }

                                VoiceChoiceButton(title: "Moses", subtitle: "Canonical and depth-oriented") {
                                    lastOracleVoice = "Moses"
                                    selectedTab = 1
                                }
                            }
                        }
                        .padding()
                        .background(.thinMaterial)
                        .clipShape(RoundedRectangle(cornerRadius: 24))
                    } else {
                        VStack(spacing: 14) {
                            Text("Continue with \(lastOracleVoice)")
                                .font(.title2.weight(.semibold))
                                .multilineTextAlignment(.center)

                            Text("\(lastOracleVoice) is ready to continue from your last path of inquiry.")
                                .foregroundStyle(.secondary)
                                .multilineTextAlignment(.center)

                            Button {
                                selectedTab = 1
                            } label: {
                                Text("Continue with \(lastOracleVoice)")
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.large)

                            Button("Change Oracle Voice") {
                                lastOracleVoice = ""
                            }
                            .buttonStyle(.bordered)
                        }
                        .padding()
                        .background(.thinMaterial)
                        .clipShape(RoundedRectangle(cornerRadius: 24))
                    }

                    VStack(spacing: 12) {
                        Button {
                            selectedTab = 2
                        } label: {
                            Label("Support with Apple", systemImage: "heart")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)

                        Button {
                            selectedTab = 3
                        } label: {
                            Label("Privacy and Terms", systemImage: "doc.text")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                    }
                }
                .padding()
            }
            .navigationTitle("Temple Gate")
        }
    }
}

struct VoiceChoiceButton: View {
    let title: String
    let subtitle: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 8) {
                Text(title)
                    .font(.headline)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity, minHeight: 96)
        }
        .buttonStyle(.borderedProminent)
    }
}

struct NativeSupportView: View {
    @State private var product: Product?
    @State private var isLoading = false
    @State private var message = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text("Seeker Monthly")
                        .font(.largeTitle.weight(.bold))

                    Text("Supports continued Oracle access at the Seeker level.")
                        .font(.headline)
                        .foregroundStyle(.secondary)

                    VStack(alignment: .leading, spacing: 8) {
                        Label("Auto-renewable monthly subscription", systemImage: "calendar")
                        Label("Length: 1 month", systemImage: "clock")
                        Label("Price: \(product?.displayPrice ?? "$0.99") / month", systemImage: "creditcard")
                    }
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
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(isLoading)

                    if !message.isEmpty {
                        Text(message)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }

                    Divider()

                    Text("Subscription renews monthly until canceled. You can manage or cancel subscriptions in your Apple account settings.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)

                    Link("Privacy Policy", destination: TempleEnvironment.privacyURL)
                    Link("Terms of Use (EULA)", destination: TempleEnvironment.termsURL)
                }
                .padding()
            }
            .navigationTitle("Support")
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
            List {
                Section("God Incorporated") {
                    Text("God Incorporated is a reflective AI conversation space for seekers.")
                    Link("Privacy Policy", destination: TempleEnvironment.privacyURL)
                    Link("Terms of Use (EULA)", destination: TempleEnvironment.termsURL)
                }

                Section("Seeker Privacy Promise") {
                    Text("Private seeker conversations, scrolls, reflections, and Oracle dialogue are treated as confidential and are not sold to advertisers or data brokers.")
                }
            }
            .navigationTitle("Info")
        }
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
                document.body.style.webkitTextSizeAdjust = '100%';
            })();
            """,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: false
        )

        configuration.userContentController.addUserScript(nativeBridgeScript)
        configuration.userContentController.addUserScript(viewportScript)
        configuration.userContentController.add(context.coordinator, name: "templeStoreKit")

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
                    self.selectedTab = 2
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
            guard message.name == "templeStoreKit" else {
                return
            }

            DispatchQueue.main.async {
                self.selectedTab = 2
            }
        }
    }
}

#Preview {
    ContentView()
}
