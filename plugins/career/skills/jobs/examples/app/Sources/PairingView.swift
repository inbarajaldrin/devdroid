import SwiftUI

struct PairingView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var host = ""
    @State private var token = ""
    @State private var showToken = false
    @State private var pasted = ""
    @State private var testing = false
    @State private var testResult: Result<Health, APIError>?

    private var draft: Pairing { Pairing(host: host, token: token) }

    /// Seconds left on a 429 from the paired server, or 0. A different host has its own limit, so it is not locked.
    private func lockedSeconds(_ now: Date) -> Int {
        guard case .rateLimited(let until)? = store.authProblem, sameHost(draft.host, store.pairing?.host) else { return 0 }
        return max(0, Int(until.timeIntervalSince(now).rounded(.up)))
    }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { ctx in
            form(locked: lockedSeconds(ctx.date) > 0, now: ctx.date)
        }
    }

    private func form(locked: Bool, now: Date) -> some View {
        Form {
            if let problem = store.authProblem {
                Section {
                    switch problem {
                    case .unauthorized:
                        Label("The server rejected this phone's token, so the app stopped asking: repeated tries would lock your network out. Pair again with a fresh link from `serve.py --pair` on the server.",
                              systemImage: "key.slash")
                            .foregroundStyle(.red)
                    case .rateLimited(let until):
                        if until > now {
                            Label("Too many failed sign-ins from this network. Try again in \(AuthGate.remainingText(until, now: now)).",
                                  systemImage: "hourglass")
                                .foregroundStyle(.orange)
                                .monospacedDigit()
                        } else {
                            Label("You can try again now.", systemImage: "checkmark.circle")
                        }
                    case .open:
                        EmptyView()
                    }
                }
                .accessibilityIdentifier("pair.problem")
            }
            Section {
                TextField("https://server.example.org/api", text: $host)
                    .textContentType(.URL)
                    .keyboardType(.URL)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .accessibilityIdentifier("pair.host")
                HStack {
                    Group {
                        if showToken { TextField("token", text: $token) } else { SecureField("token", text: $token) }
                    }
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .accessibilityIdentifier("pair.token")
                    Button { showToken.toggle() } label: {
                        Image(systemName: showToken ? "eye.slash" : "eye")
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel(showToken ? "Hide token" : "Show token")
                }
            } header: {
                Text("Server")
            } footer: {
                Text("Use the server address and a one-time pairing link from your agent. The token stays in the Keychain.")
            }

            Section {
                HStack {
                    TextField("careerapp://pair?host=…&code=…", text: $pasted)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .font(.footnote)
                        .accessibilityIdentifier("pair.url")
                    PasteButton(payloadType: String.self) { strings in
                        if let s = strings.first { pasted = s; applyPasted() }
                    }
                    .labelStyle(.iconOnly)
                    .buttonBorderShape(.capsule)
                }
                if !pasted.isEmpty {
                    Button("Use this link") { applyPasted() }
                        .accessibilityIdentifier("pair.useLink")
                }
            } header: {
                Text("Or paste the pairing link")
            } footer: {
                Text("Ask your agent for a one-time pairing link. Scanning the QR with Camera opens this screen.")
            }

            Section {
                Button {
                    Task { await test() }
                } label: {
                    HStack {
                        Text("Test connection")
                        Spacer()
                        if testing { ProgressView() }
                    }
                }
                .disabled(!draft.isComplete || testing || locked)
                .accessibilityIdentifier("pair.test")

                if let testResult {
                    switch testResult {
                    case .success(let h):
                        Label("Connected: \(h.postings ?? 0) postings on the server", systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                    case .failure(let e):
                        Label(e.localizedDescription, systemImage: "xmark.octagon.fill")
                            .foregroundStyle(.red)
                    }
                }
            }

            if store.pairing != nil { CareerSettingsSections() }
            Section {
                Button("Save and open Today") {
                    store.pair(draft)
                    dismiss()
                }
                .disabled(!draft.isComplete || locked)
                .accessibilityIdentifier("pair.save")

                if store.pairing != nil {
                    Button("Forget this server", role: .destructive) {
                        store.unpair()
                        host = ""; token = ""; testResult = nil
                    }
                }
            }
        }
        .navigationTitle("Pair with server")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if store.pairing != nil {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
            }
        }
        .onAppear(perform: prefill)
        .onChange(of: store.incomingPairing) { _, _ in prefill() }
    }

    private func prefill() {
        if let p = store.incomingPairing {
            host = p.host; token = p.token
            store.incomingPairing = nil
            testResult = nil
            Task { await test() }
        } else if host.isEmpty, let p = store.pairing {
            host = p.host; token = p.token
        }
    }

    private func applyPasted() {
        let s = pasted.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: s), url.scheme == "careerapp", url.host == "pair" else {
            testResult = .failure(.decoding("That is not a careerapp://pair link."))
            return
        }
        store.handle(url:url)
        pasted = ""
        testResult = nil
    }

    private func test() async {
        testing = true
        defer { testing = false }
        testResult = await store.testConnection(draft)
    }
}

private func sameHost(_ a: String, _ b: String?) -> Bool {
    func norm(_ s: String) -> String {
        var h = s.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        while h.hasSuffix("/") { h.removeLast() }
        return h
    }
    return b.map { norm($0) == norm(a) } ?? false
}
