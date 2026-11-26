import AppKit
import CardboardCADKit
import SwiftUI
import WebKit

/// The engine's live preview page (http://127.0.0.1:<port>/preview): the 3D
/// model with the assembled parts, and the flat cut sheets. It refreshes
/// itself after every step the assistant takes.
struct PreviewPane: View {
    let engine: EngineManager

    var body: some View {
        if case .running(let port) = engine.state {
            let url = EngineClient(port: port).previewURL
            PreviewWebView(url: url)
                .overlay(alignment: .topTrailing) {
                    Button {
                        NSWorkspace.shared.open(url)
                    } label: {
                        Image(systemName: "safari")
                    }
                    .buttonStyle(.borderless)
                    .help("Open the preview in your web browser")
                    .padding(10)
                }
        } else {
            ContentUnavailableView(
                "Preview",
                systemImage: "cube.transparent",
                description: Text("The preview appears when the engine is running.")
            )
        }
    }
}

struct PreviewWebView: NSViewRepresentable {
    let url: URL

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.allowsMagnification = false
        context.coordinator.url = url
        view.load(URLRequest(url: url))
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        if context.coordinator.url != url {
            context.coordinator.url = url
            view.load(URLRequest(url: url))
        }
    }

    /// Retries when the page cannot load yet (the engine is still starting).
    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate {
        var url: URL?

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            retry(webView)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            retry(webView)
        }

        private func retry(_ webView: WKWebView) {
            guard let url else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak webView] in
                webView?.load(URLRequest(url: url))
            }
        }
    }
}
