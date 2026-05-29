//
//  ContentView.swift
//  Temple
//
//  Local iOS shell for God Incorporated.
//  v11.4I: Release/App Store uses production and supports first StoreKit product.
//

import SwiftUI
import WebKit
import StoreKit

private enum TempleEnvironment {
#if DEBUG
    static let webAppURL = URL(string: "https://godincorporated-staging.onrender.com/temple")!
#else
    static let webAppURL = URL(string: "https://godincorporated.ai/temple")!
#endif

    static let seekerMonthlyProductID = "ai.godincorporated.seeker.monthly"
}

struct ContentView: View {
    var body: some View {
        TempleWebView(url: TempleEnvironment.webAppURL)
            .ignoresSafeArea(.keyboard, edges: .bottom)
    }
}

struct TempleWebView: UIViewRepresentable {
    let url: URL

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
        if webView.url == nil {
            webView.load(URLRequest(url: url))
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    final class Coordinator: NSObject, WKNavigationDelegate, UIScrollViewDelegate, WKScriptMessageHandler {
        weak var webView: WKWebView?

        func viewForZooming(in scrollView: UIScrollView) -> UIView? {
            nil
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            webView.scrollView.setZoomScale(1.0, animated: false)
            webView.evaluateJavaScript("""
                window.GodIncNativeIOS = {
                    platform: "ios",
                    storeKit: true,
                    supportedProducts: ["ai.godincorporated.seeker.monthly"]
                };
                window.dispatchEvent(new Event("godIncNativeReady"));
                document.documentElement.style.overflowX = 'hidden';
                document.body.style.overflowX = 'hidden';
                document.body.style.maxWidth = '100vw';
            """)
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

            guard let body = message.body as? [String: Any] else {
                emitStoreKitEvent(status: "error", productId: "", message: "Invalid StoreKit request.")
                return
            }

            let productId = (body["product_id"] as? String) ?? ""

            guard productId == TempleEnvironment.seekerMonthlyProductID else {
                emitStoreKitEvent(status: "error", productId: productId, message: "This product is not available in the iOS app yet.")
                return
            }

            Task {
                await purchase(productId: productId)
            }
        }

        private func purchase(productId: String) async {
            do {
                let products = try await Product.products(for: [productId])

                guard let product = products.first else {
                    await emitStoreKitEventOnMain(status: "error", productId: productId, message: "Apple could not load this subscription product.")
                    return
                }

                let result = try await product.purchase()

                switch result {
                case .success(let verification):
                    switch verification {
                    case .verified(let transaction):
                        let transactionId = String(transaction.id)
                        await transaction.finish()
                        await emitStoreKitEventOnMain(
                            status: "success",
                            productId: productId,
                            message: "Apple purchase received.",
                            transactionId: transactionId
                        )

                    case .unverified(_, let error):
                        await emitStoreKitEventOnMain(status: "error", productId: productId, message: "Apple could not verify this purchase: \(error.localizedDescription)")
                    }

                case .userCancelled:
                    await emitStoreKitEventOnMain(status: "cancelled", productId: productId, message: "Purchase cancelled.")

                case .pending:
                    await emitStoreKitEventOnMain(status: "pending", productId: productId, message: "Purchase pending Apple approval.")

                @unknown default:
                    await emitStoreKitEventOnMain(status: "error", productId: productId, message: "Apple returned an unknown purchase result.")
                }
            } catch {
                await emitStoreKitEventOnMain(status: "error", productId: productId, message: error.localizedDescription)
            }
        }

        @MainActor
        private func emitStoreKitEventOnMain(
            status: String,
            productId: String,
            message: String,
            transactionId: String? = nil,
            signedTransaction: String? = nil
        ) {
            emitStoreKitEvent(
                status: status,
                productId: productId,
                message: message,
                transactionId: transactionId,
                signedTransaction: signedTransaction
            )
        }

        private func emitStoreKitEvent(
            status: String,
            productId: String,
            message: String,
            transactionId: String? = nil,
            signedTransaction: String? = nil
        ) {
            var detail: [String: Any] = [
                "status": status,
                "productId": productId,
                "message": message
            ]

            if let transactionId {
                detail["transactionId"] = transactionId
            }

            if let signedTransaction {
                detail["signedTransaction"] = signedTransaction
            }

            guard
                let data = try? JSONSerialization.data(withJSONObject: detail, options: []),
                let json = String(data: data, encoding: .utf8)
            else {
                return
            }

            DispatchQueue.main.async { [weak self] in
                self?.webView?.evaluateJavaScript("""
                    window.dispatchEvent(new CustomEvent("godIncStoreKitPurchase", { detail: \(json) }));
                """)
            }
        }
    }
}

#Preview {
    ContentView()
}
