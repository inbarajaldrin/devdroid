import SwiftUI

@main
struct CareerApp: App {
    @State private var store = AppStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(store)
                .onOpenURL { store.handle(url: $0) }
        }
    }
}

struct RootView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    /// While the app is in front the data is checked this often; `GET /stamp` costs next to nothing.
    private static let poll: Duration = .seconds(15)

    var body: some View {
        @Bindable var store = store
        Group {
            if store.pairing == nil {
                NavigationStack { PairingView() }
            } else {
                TabView(selection: $store.tab) {
                    TodayView()
                        .tabItem { Label("Today", systemImage: "checklist") }
                        .tag(AppStore.Tab.today)
                        .badge(store.todayOpenCount)
                        .accessibilityIdentifier("tab.today")
                    InboxView()
                        .tabItem { Label("Listings", systemImage: "list.bullet.rectangle") }
                        .tag(AppStore.Tab.inbox)
                        .accessibilityIdentifier("tab.inbox")
                    QuestionsView()
                        .tabItem { Label("Questions", systemImage: "questionmark.bubble") }
                        .tag(AppStore.Tab.questions)
                        .badge(store.openQuestionCount)
                        .accessibilityIdentifier("tab.questions")
                    AppliedView()
                        .tabItem { Label("Applied", systemImage: "paperplane") }
                        .tag(AppStore.Tab.applied)
                        .badge(store.activeApplicationCount)
                        .accessibilityIdentifier("tab.applied")
                }
                .sheet(isPresented: $store.showPairing) {
                    NavigationStack { PairingView() }
                }
                .sheet(isPresented: $store.showChat) {
                    ChatSheet()
                }
                .sheet(item: $store.previewTarget) { t in
                    ApplicationPreviewView(postingId: t.id)
                }
                // The badges are the point of the tabs: keep the counts fresh whenever the app comes forward.
                .safeAreaInset(edge: .top, spacing: 0) {
                    if let update = store.update { UpdateBanner(update: update) }
                }
                .task {
                    async let q: Void = self.store.refreshQuestions()
                    async let b: Void = self.store.refreshBatch()
                    async let c: Void = self.store.refreshChat()
                    _ = await (q, b, c)
                    await store.checkFresh()
                }
                .onChange(of: store.tab) { Task { await store.checkFresh() } }
                .task(id: scenePhase) {
                    while scenePhase == .active, !Task.isCancelled {
                        try? await Task.sleep(for: Self.poll)
                        if !Task.isCancelled { await store.checkFresh() }
                    }
                }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active {
                        Task {
                            // Postings too: the Questions badge counts postings ready to submit.
                            async let p: Void = self.store.refresh()
                            async let q: Void = self.store.refreshQuestions()
                            async let b: Void = self.store.refreshBatch()
                            async let c: Void = self.store.refreshChat()
                            _ = await (p, q, b, c)
                        }
                    }
                }
            }
        }
        .overlay(alignment: .top) {
            if let toast = store.toast {
                ToastView(toast: toast)
                    .padding(.horizontal)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.2), value: store.toast)
    }
}

/// "Update available (build N)": one tap opens the over-the-air install. Dismissed, it stays away until the next build.
struct UpdateBanner: View {
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    let update: AppUpdate

    var body: some View {
        HStack(spacing: 8) {
            Button { openURL(update.link) } label: {
                Label("Update available (build \(update.build))", systemImage: "arrow.down.circle.fill")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .accessibilityIdentifier("update.install")
            Button { store.dismissUpdate() } label: {
                Image(systemName: "xmark").font(.footnote.weight(.semibold)).frame(width: 36, height: 36)
            }
            .accessibilityLabel("Dismiss")
            .accessibilityIdentifier("update.dismiss")
        }
        .padding(.leading, 14)
        .padding(.trailing, 6)
        .padding(.vertical, 2)
        .background(.bar)
    }
}
