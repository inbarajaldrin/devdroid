import SwiftUI

/// The support chat: a round button at the bottom right of every screen opens one ongoing thread with the
/// coordinating agent session (serve.py `GET/POST /chat`, tools/career.py chat). The server posts each message to the session
/// and optionally copies it to the dedicated career WhatsApp group; replies come back here.

extension View {
    /// Floats the chat button over the bottom-right corner and keeps scrolled content clear of it.
    func chatButton() -> some View {
        contentMargins(.bottom, 72, for: .scrollContent)
            .overlay(alignment: .bottomTrailing) {
                ChatButton()
                    .padding(.trailing, 16)
                    .padding(.bottom, 12)
            }
    }
}

struct ChatButton: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        let unread = store.chatUnread
        Button { store.showChat = true } label: {
            Image(systemName: "bubble.left.and.bubble.right.fill")
                .font(.title3)
                .foregroundStyle(.white)
                .frame(width: 56, height: 56)
                .background(Color.accentColor, in: Circle())
                .shadow(color: .black.opacity(0.2), radius: 8, y: 3)
                .overlay(alignment: .topTrailing) {
                    if unread > 0 {
                        Text("\(unread)")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 6)
                            .frame(minWidth: 20, minHeight: 20)
                            .background(.red, in: Capsule())
                            .overlay(Capsule().stroke(Color(.systemBackground), lineWidth: 2))
                            .offset(x: 4, y: -4)
                    }
                }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(unread > 0 ? "Ask your agent, \(unread) new \(unread == 1 ? "reply" : "replies")" : "Ask your agent")
        .accessibilityIdentifier("chat.button")
    }
}

struct ChatSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var draft = ""
    @State private var sending = false
    @State private var attaching = false
    @FocusState private var focused: Bool

    /// While the sheet is up the thread is fetched this often; closed, it refreshes when the app comes forward.
    private static let poll: Duration = .seconds(4)

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 10) {
                        if store.chatMessages.isEmpty {
                            emptyState
                        }
                        ForEach(store.chatMessages) { m in
                            bubble(m).id(m.n)
                        }
                        ForEach(store.notificationIssues,id:\.self) { issue in
                            Label("WhatsApp: "+issue,systemImage:"exclamationmark.triangle").font(.footnote).foregroundStyle(.orange)
                        }
                        if let err = store.chatError {
                            Label(err, systemImage: "exclamationmark.triangle")
                                .font(.footnote)
                                .foregroundStyle(.orange)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                }
                .defaultScrollAnchor(.bottom)
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: store.chatMessages.last?.n) { _, last in
                    guard let last else { return }
                    withAnimation(.snappy) { proxy.scrollTo(last, anchor: .bottom) }
                    store.markChatSeen()
                }
            }
            .background(Color(.systemGroupedBackground))
            .safeAreaInset(edge: .bottom) { composer }
            .navigationTitle("Ask your agent")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .task {
                store.markChatSeen()
                while !Task.isCancelled {
                    await store.refreshChat()
                    store.markChatSeen()
                    try? await Task.sleep(for: Self.poll)
                }
            }
        }
        .accessibilityIdentifier("chat.sheet")
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Text("Ask anything").font(.headline)
            Text("Questions, changes, or something that looks wrong. Your agent replies here, with a copy in your career group when WhatsApp notifications are enabled.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(.top, 60)
        .padding(.horizontal, 24)
    }

    private func bubble(_ m: ChatMessage) -> some View {
        VStack(alignment: m.isMine ? .trailing : .leading, spacing: 4) {
            Text(m.text)
                .font(.body)
                .foregroundStyle(m.isMine ? Color.white : Color.primary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 12)
                .padding(.vertical, 9)
                .background(m.isMine ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(Color(.secondarySystemGroupedBackground)),
                            in: UnevenRoundedRectangle(topLeadingRadius: m.isMine ? 16 : 4, bottomLeadingRadius: 16,
                                                       bottomTrailingRadius: 16, topTrailingRadius: m.isMine ? 4 : 16,
                                                       style: .continuous))
            if let error = m.deliveryError { Text("Saved; agent delivery failed: "+error.error).font(.footnote).foregroundStyle(.orange) }
            Text([m.isMine ? "You" : "Agent", m.date.map(QuestionDetailView.turnTime)].compactMap { $0 }.joined(separator: " · "))
                .font(.caption2)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 4)
        }
        .frame(maxWidth: .infinity, alignment: m.isMine ? .trailing : .leading)
        .padding(m.isMine ? .leading : .trailing, 48)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("chat.message.\(m.n)")
    }

    private var composer: some View {
        let empty = draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return HStack(alignment: .bottom, spacing: 8) {
            Button { attaching = true } label: {
                Image(systemName: "link.badge.plus").font(.system(size: 22))
            }
            .padding(.bottom, 6)
            .accessibilityLabel("Attach job")
            .accessibilityIdentifier("chat.attach")
            .sheet(isPresented: $attaching) {
                AttachJobSheet { p in
                    let ref = p.ref
                    let base = draft.trimmingCharacters(in: .whitespacesAndNewlines)
                    draft = base.isEmpty ? ref + "\n" : base + "\n" + ref + "\n"
                    focused = true
                }
            }
            TextField("Message", text: $draft, axis: .vertical)
                .lineLimit(1...5)
                .focused($focused)
                .returnSends { Task { await send() } }
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .accessibilityIdentifier("chat.input")
            Button {
                Task { await send() }
            } label: {
                if sending {
                    ProgressView().frame(width: 34, height: 34)
                } else {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 34))
                        .symbolRenderingMode(.hierarchical)
                }
            }
            .disabled(empty || sending)
            .accessibilityLabel("Send")
            .accessibilityIdentifier("chat.send")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.bar)
    }

    private func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        sending = true
        if await store.sendChat(text) { draft = "" }
        sending = false
    }
}

/// "Attach job": pick a posting and put its ref (the same line as Copy ref) into the chat message.
struct AttachJobSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let onPick: (Posting) -> Void
    @State private var query = ""

    private var rows: [Posting] {
        let q = query.trimmingCharacters(in: .whitespaces)
        func rank(_ p: Posting) -> Int {
            p.waitsOnUser ? 0 : p.needsReview ? 1 : p.application != nil ? 2 : ["approved", "tailored"].contains(p.status) ? 3 : 4
        }
        return store.postings
            .filter { !$0.isVariant && (q.isEmpty || "\($0.company) \($0.title)".localizedCaseInsensitiveContains(q)) }
            .sorted { rank($0) != rank($1) ? rank($0) < rank($1) : $0.company.localizedStandardCompare($1.company) == .orderedAscending }
            .prefix(60).map { $0 }
    }

    var body: some View {
        NavigationStack {
            List(rows) { p in
                Button {
                    onPick(p)
                    dismiss()
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(p.title).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                        Text(p.company).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .searchable(text: $query, prompt: "Company or title")
            .navigationTitle("Attach job")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }
}
