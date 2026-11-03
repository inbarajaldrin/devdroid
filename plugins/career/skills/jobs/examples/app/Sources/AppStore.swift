import Foundation
import Observation
import UIKit

struct Toast: Equatable {
    enum Kind { case info, error }
    var kind: Kind
    var text: String
}

@MainActor
@Observable
final class AppStore {
    enum LoadState: Equatable { case idle, loading, loaded, failed(String) }

    private(set) var pairing: Pairing?
    private var client: APIClient?

    var postings: [Posting] = []
    var loadState: LoadState = .idle

    enum Tab: Hashable { case today, inbox, questions, applied }
    var tab: Tab = .today

    /// The Today batch (`GET /batch`); nil until loaded.
    var batch: Batch?
    var batchState: LoadState = .idle

    var questions: [Question] = []
    var questionsState: LoadState = .idle
    /// Set by a `careerapp://questions/<id>` link; QuestionsView pushes it and clears it.
    var pendingQuestion: String?
    /// Questions badge: open questions (minus the ones a top row stands for) plus Ready to submit.
    var openQuestionCount: Int {
        let hidden = questionsShownAsRows
        return questions.filter { $0.isOpen && !hidden.contains($0.id) }.count
            + postings.filter { $0.reviewAction == "submit" }.count
    }

    /// Listings badge: new listings waiting for approve or skip.
    var newListingCount: Int {
        postings.filter { !$0.isVariant && $0.section == .toReview }.count
    }

    static let submitYes = "Submit it"

    /// The open submit question for a posting (applies_to.approve_submit); the row's Approve submit answers it.
    func submitQuestion(for postingId: String) -> Question? {
        questions.first { $0.isOpen && $0.appliesTo?.approveSubmit == postingId }
    }

    /// Questions a top row already stands for, so each application shows once in the Questions tab.
    var questionsShownAsRows: Set<String> {
        var out = Set<String>()
        for p in postings {
            switch p.reviewAction {
            case "submit", "submitting": if let q = submitQuestion(for: p.id) { out.insert(q.id) }
            case "answer": for q in p.reviewSummary?.questions ?? [] where question(q.id)?.appliesTo?.formQuestion == nil { out.insert(q.id) }
            default: break
            }
        }
        return out
    }

    /// The Application preview on screen (a sheet over every tab); the one place a submit is approved from.
    struct PreviewTarget: Identifiable, Hashable { let id: String }
    var previewTarget: PreviewTarget?
    func openPreview(_ postingId: String) { previewTarget = PreviewTarget(id: postingId) }

    func preview(_ id: String) async throws -> PreviewDoc {
        guard let client else { throw APIError.notPaired }
        return try await client.preview(id)
    }

    /// Approve only the exact read-back digest the person saw at the end of the preview.
    /// The coordinator is notified through the normal workflow entrance.
    @discardableResult
    func approveSubmitFromPreview(_ id: String, fillDigest: String) async -> Bool {
        return await approveSubmit(id, fillDigest:fillDigest)
    }
    var lastRefreshed: Date?
    var toast: Toast?

    /// The support chat thread (oldest first) and whether its sheet is up.
    var chatMessages: [ChatMessage] = []
    var chatError: String?
    var showChat = false
    /// Highest agent message shown with the chat open; newer ones are unread (badge on the chat button).
    private(set) var chatSeen = 0
    private var chatSeenKey: String { "chat.seen."+(pairing?.host ?? "")+"."+(pairing?.token.suffix(12) ?? "") }
    var chatUnread: Int { chatMessages.filter { !$0.isMine && $0.n > chatSeen }.count }

    /// A pairing that arrived via `careerapp://pair` and should prefill the Pairing screen.
    var incomingPairing: Pairing?
    var showPairing = false

    /// Set when the server rejected the token (401) or the network is rate-limited (429). Every token-checked request
    /// stops until the phone pairs again (or Retry-After passes); the Pairing screen says why.
    var authProblem: AuthGate.State?

    init() {
        pairing = PairingStore.load()
        chatSeen = UserDefaults.standard.integer(forKey:chatSeenKey)
        client = pairing.map { makeClient($0) }
    }

    /// The main client, with a gate that remembers a rejected token across launches.
    private func makeClient(_ p: Pairing) -> APIClient {
        let gate = AuthGate(persistFor: p) { [weak self] state in
            Task { @MainActor in self?.authChanged(state) }
        }
        if gate.state != .open { authProblem = gate.state; showPairing = true }
        return .live(p, gate: gate)
    }

    private func authChanged(_ state: AuthGate.State) {
        authProblem = state == .open ? nil : state
        if state != .open, pairing != nil { showPairing = true }
    }

    // MARK: pairing

    func pair(_ p: Pairing) {
        if !PairingStore.save(p) {
            flash(.error, "Keychain refused the token; you will have to pair again after the next launch.")
        }
        AuthGate.forget()
        authProblem = nil
        pairing = p
        chatSeen = UserDefaults.standard.integer(forKey:chatSeenKey)
        client = makeClient(p)
        postings = []
        loadState = .idle
        questions = []
        questionsState = .idle
        batch = nil
        batchState = .idle
        chatMessages = []
        chatError = nil
        showPairing = false
        // The inbox's .task already ran against the previous pairing (or none); fetch again now
        // so a re-pair shows the new server's postings without a relaunch or pull-to-refresh.
        Task {
            async let a: Void = refresh()
            async let b: Void = refreshQuestions()
            async let c: Void = refreshBatch()
            async let d: Void = refreshChat()
            _ = await (a, b, c, d)
        }
    }

    func unpair() {
        PairingStore.clear()
        AuthGate.forget()
        authProblem = nil
        pairing = nil
        client = nil
        postings = []
        loadState = .idle
        questions = []
        questionsState = .idle
        batch = nil
        batchState = .idle
        chatMessages = []
        chatError = nil
        showChat = false
    }

    /// One probe with a throwaway gate (it never trips the main client). A 429 here still blocks the screen.
    func testConnection(_ p: Pairing) async -> Result<Health, APIError> {
        if case .rateLimited(let until)? = authProblem, until > .now, p.host == pairing?.host {
            return .failure(.rateLimited(until: until))
        }
        do { return .success(try await APIClient.live(p).health()) }
        catch APIError.rateLimited(let until) {
            if p.host == pairing?.host { authProblem = .rateLimited(until: until) }
            return .failure(.rateLimited(until: until))
        }
        catch let e as APIError { return .failure(e) }
        catch { return .failure(.transport(error.localizedDescription)) }
    }

    func handle(url: URL) {
        if url.scheme == "careerapp", url.host() == "pair",
           let items = URLComponents(url:url,resolvingAgainstBaseURL:false)?.queryItems,
           let host = items.first(where:{$0.name == "host"})?.value,
           let code = items.first(where:{$0.name == "code"})?.value,
           let base = Pairing(host:host,token:"").baseURL {
            Task {
                do {
                    var request = URLRequest(url:base.appending(path:"pair"))
                    request.httpMethod = "POST"
                    request.setValue("application/json",forHTTPHeaderField:"Content-Type")
                    request.httpBody = try JSONSerialization.data(withJSONObject:["code":code])
                    let (data,response) = try await URLSession.shared.data(for:request)
                    guard (response as? HTTPURLResponse)?.statusCode == 200,
                          let payload = try JSONSerialization.jsonObject(with:data) as? [String:String],
                          let token = payload["token"], !token.isEmpty else { throw APIError.decoding("Pairing link expired") }
                    incomingPairing = Pairing(host:host,token:token)
                    showPairing = true
                } catch { flash(.error,"Could not redeem this pairing code. Ask your agent for another link.") }
            }
            return
        }
        // careerapp://questions[/<id>] opens the Questions tab (and that question).
        if url.scheme == "careerapp", url.host() == "questions" {
            tab = .questions
            let id = url.pathComponents.dropFirst().first
            if let id, !id.isEmpty { pendingQuestion = id }
            Task { await refreshQuestions() }
            return
        }

    }

    // MARK: data

    func posting(_ id: String) -> Posting? {
        postings.first { $0.id == id } ?? batch?.items.first { $0.record.id == id }?.record
    }

    // MARK: today (the batch)

    struct TodayItem: Identifiable {
        let posting: Posting
        let why: String?
        var id: String { posting.id }
        var cleared: Bool { !posting.isOpenForBatch }
    }

    /// Batch items with each record's freshest copy, so a ruling made in the app clears its row at once.
    /// Open first, then cleared, each in batch order.
    var todayItems: [TodayItem] {
        let all = (batch?.items ?? []).map { item in
            TodayItem(posting: postings.first { $0.id == item.record.id } ?? item.record,
                      why: item.why.flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 })
        }
        return all.filter { !$0.cleared } + all.filter(\.cleared)
    }

    var todayOpenCount: Int { todayItems.filter { !$0.cleared }.count }

    /// Filled postings waiting for the user's per-box review or submit approval, soonest deadline first.
    var readyForReview: [Posting] {
        postings.filter(\.needsReview).sorted { a, b in
            let da = PostingDates(a).daysLeft ?? .max, db = PostingDates(b).daysLeft ?? .max
            if da != db { return da < db }
            return a.company.localizedStandardCompare(b.company) == .orderedAscending
        }
    }
    var readyCount: Int { postings.filter(\.waitsOnUser).count }

    /// Every application from the submit onward, most recent activity first (the Applied tab).
    var applications: [Posting] {
        postings.filter { $0.application != nil }.sorted { a, b in
            let sa = a.application?.state == "submitting", sb = b.application?.state == "submitting"
            if sa != sb { return sa }                                  // being sent now: on top
            let da = a.application?.activityDate ?? .distantPast, db = b.application?.activityDate ?? .distantPast
            if da != db { return da > db }
            return a.company.localizedStandardCompare(b.company) == .orderedAscending
        }
    }
    var activeApplicationCount: Int { postings.filter { $0.application?.active == true }.count }

    func refreshBatch() async {
        guard let client else { batchState = .failed(APIError.notPaired.localizedDescription); return }
        if batch == nil { batchState = .loading }
        do {
            batch = try await client.batch()
            batchState = .loaded
        } catch {
            batchState = .failed(error.localizedDescription)
            if batch != nil { flash(.error, error.localizedDescription) }
        }
    }

    func refresh() async {
        guard let client else { loadState = .failed(APIError.notPaired.localizedDescription); return }
        if postings.isEmpty { loadState = .loading }
        do {
            postings = try await client.list()
            loadState = .loaded
            lastRefreshed = .now
        } catch {
            loadState = .failed(error.localizedDescription)
            if !postings.isEmpty { flash(.error, error.localizedDescription) }
        }
    }

    /// Optimistic: the row flips immediately, rolls back with a toast on failure.
    func rule(_ id: String, _ decision: Decision, reason: String?) async {
        guard let client, let idx = postings.firstIndex(where: { $0.id == id }) else { return }
        let backup = postings[idx]
        var optimistic = backup
        optimistic.status = decision.rawValue
        optimistic.ruling = Ruling(decision: decision.rawValue, reason: reason, decidedAt: nil, by: "phone")
        postings[idx] = optimistic
        do {
            let updated = try await client.rule(id, decision, reason)
            replace(updated)
            flash(.info, decision == .approved ? "Approved. Your agent will prepare the application." : "Skipped.")
        } catch {
            replace(backup)
            flash(.error, error.localizedDescription)
        }
    }

    func confirmApplied(_ id: String) async {
        guard let client, let idx = postings.firstIndex(where: { $0.id == id }) else { return }
        let backup = postings[idx]
        var optimistic = backup
        optimistic.status = "applied"
        postings[idx] = optimistic
        do {
            replace(try await client.applied(id))
            flash(.info, "Marked applied.")
        } catch {
            replace(backup)
            flash(.error, error.localizedDescription)
        }
    }

    func savedAnswers() async throws -> [SavedAnswer] {
        guard let client else { throw APIError.notPaired }
        return try await client.savedAnswers()
    }

    func changeSavedAnswer(_ id: String, value: String) async throws {
        guard let client else { throw APIError.notPaired }
        _ = try await client.changeSavedAnswer(id, value)
    }

    var roleSettings: RoleSettings?
    func loadRoles() async {
        guard let client else { return }
        do { roleSettings = try await client.roleSettings() } catch { flash(.error,error.localizedDescription) }
    }
    func modelChoices() async throws -> ModelCatalog {
        guard let client else { throw APIError.notPaired };return try await client.modelChoices()
    }
    func saveRole(_ role: String, _ choice: ModelOption) async -> Bool {
        guard let client else { return false }
        do { try await client.saveRole(role,choice);await loadRoles();return true } catch { flash(.error,error.localizedDescription);return false }
    }
    var notificationIssues: [String] = []
    var notificationStatus: NotificationStatus?
    func setNotifications(_ enabled: Bool) async {
        guard let client else { return }
        do { notificationStatus = try await client.setNotifications(enabled);notificationIssues = notificationStatus?.issues ?? [] }
        catch { flash(.error,error.localizedDescription) }
    }

    // MARK: questions

    /// By id, or by an old follow-up id that was merged into a thread.
    func question(_ id: String) -> Question? {
        questions.first { $0.id == id } ?? questions.first { $0.mergedFrom?.contains(id) == true }
    }

    func refreshQuestions() async {
        guard let client else { questionsState = .failed(APIError.notPaired.localizedDescription); return }
        if questions.isEmpty { questionsState = .loading }
        do {
            questions = try await client.questions()
            questionsState = .loaded
        } catch {
            questionsState = .failed(error.localizedDescription)
            if !questions.isEmpty { flash(.error, error.localizedDescription) }
        }
    }

    /// Not optimistic: the server validates the answer and applies it, so the question only changes once it
    /// says yes. Returns true when the answer was stored.
    @discardableResult
    func answer(_ id: String, _ answer: String, note: String? = nil) async -> Bool {
        guard let client else { return false }
        do {
            let q = try await client.answerQuestion(id, answer, note)
            replaceQuestion(q)
            if let err = q.applyError {
                flash(.error, "Saved, but your agent could not apply it: \(err)")
            } else if q.appliesTo?.ruling == "approve", q.answer == "Yes" || q.answer == "Confirmed" {
                flash(.info, "Answered · posting approved.")
            } else {
                flash(.info, "Answered.")
            }
            // A ruling or a form answer changed the posting record; fetch it so Listings and the detail agree.
            if q.postingId != nil || q.appliesTo?.postingId != nil { await refresh() }
            return true
        } catch {
            flash(.error, error.localizedDescription)
            return false
        }
    }

    /// A comment without an answer: the question keeps its status.
    @discardableResult
    func comment(_ id: String, _ note: String) async -> Bool {
        guard let client else { return false }
        do {
            let q = try await client.answerQuestion(id, nil, note)
            let wasAnswered = question(id).map { !$0.isOpen && !$0.isDismissed } ?? false
            replaceQuestion(q)
            flash(.info, q.answeredByComment ? "Answered with a comment." : (wasAnswered ? "Comment added." : "Answered."))
            return true
        } catch {
            flash(.error, error.localizedDescription)
            return false
        }
    }

    func dismissQuestion(_ id: String) async {
        guard let client else { return }
        do {
            replaceQuestion(try await client.dismissQuestion(id))
            flash(.info, "Dismissed.")
        } catch {
            flash(.error, error.localizedDescription)
        }
    }

    private func replaceQuestion(_ q: Question) {
        if let i = questions.firstIndex(where: { $0.id == q.id }) { questions[i] = q } else { questions.append(q) }
    }

    /// "Needs your answer": open the question behind a review box in the Questions tab.
    func openQuestion(_ id: String) {
        tab = .questions
        pendingQuestion = id
    }

    // MARK: a newer build of this app

    /// iOS never installs silently: this offers the over-the-air link, and he taps it. `tools/phone_deploy.py` is what
    /// installs by itself, and only when the phone is on the server's network and unlocked.
    private(set) var update: AppUpdate?
    private static let ownBuild = Int(Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "") ?? 0

    private func noteBuild(_ s: DataStamp) {
        update = AppUpdate.available(build:s.build,install:s.install,ownBuild:Self.ownBuild,dismissedBuild:UserDefaults.standard.integer(forKey:"update.dismissed"))
    }

    /// Hidden until the next build.
    func dismissUpdate() {
        if let update { UserDefaults.standard.set(update.build, forKey: "update.dismissed") }
        update = nil
    }

    // MARK: staying current

    private var dataStamp: String?
    /// Moves after a refresh that found new data; an open preview reloads on it.
    private(set) var dataGeneration = 0

    /// Asks the server whether anything changed and refetches only then. Quiet on failure: the next poll catches up.
    func checkFresh() async {
        guard let client, authProblem == nil, let s = try? await client.stamp() else { return }
        noteBuild(s)
        guard s.stamp != dataStamp else { return }
        let first = dataStamp == nil
        dataStamp = s.stamp
        if first && !postings.isEmpty && !questions.isEmpty { return }   // the launch refresh just read this
        async let p: Void = refresh()
        async let q: Void = refreshQuestions()
        async let b: Void = refreshBatch()
        async let c: Void = refreshChat()
        _ = await (p, q, b, c)
        dataGeneration += 1
    }

    // MARK: chat

    /// Fetches only what is new since the last message held. Quiet on failure: the sheet shows `chatError`.
    func refreshChat() async {
        guard let client else { return }
        do {
            let page = try await client.chat(chatMessages.last?.n ?? 0)
            mergeChat(page.messages)
            chatError = nil
        } catch {
            chatError = error.localizedDescription
        }
        if let status = try? await client.notifications() { notificationStatus = status;notificationIssues = status.issues }
    }

    /// Returns true when the server stored the message.
    @discardableResult
    func sendChat(_ text: String) async -> Bool {
        guard let client else { return false }
        do {
            mergeChat([try await client.sendChat(text, nil)])
            chatError = nil
            return true
        } catch {
            flash(.error, error.localizedDescription)
            return false
        }
    }

    func markChatSeen() {
        let top = chatMessages.filter { !$0.isMine }.map(\.n).max() ?? 0
        guard top > chatSeen else { return }
        chatSeen = top
        UserDefaults.standard.set(top, forKey: chatSeenKey)
    }

    private func mergeChat(_ new: [ChatMessage]) {
        let have = Set(chatMessages.map(\.n))
        let fresh = new.filter { !have.contains($0.n) }
        guard !fresh.isEmpty else { return }
        chatMessages = (chatMessages + fresh).sorted { $0.n < $1.n }
    }

    // MARK: apply stage

    func fill(_ id: String) async throws -> FormFill? {
        guard let client else { throw APIError.notPaired }
        return try await client.fill(id)
    }

    func submitGate(_ id: String) async throws -> SubmitGate {
        guard let client else { throw APIError.notPaired }
        return try await client.submitGate(id)
    }

    func review(_ id: String) async throws -> ReviewDoc? {
        guard let client else { throw APIError.notPaired }
        return try await client.review(id)
    }

    /// Not optimistic: returns the server's review after the change, or nil (with a toast) when it refused.
    func approveBox(_ id: String, key: String, seen: String?) async -> ReviewDoc? {
        guard let client else { return nil }
        do {
            let doc = try await client.approveBox(id, key, seen)
            noteReview(id, doc)
            return doc
        } catch {
            flash(.error, error.localizedDescription)
            return nil
        }
    }

    /// "Comment on this application": reaches the session with the job ref and holds the application until resolved.
    @discardableResult
    func commentOnApplication(_ id: String, _ text: String) async -> Bool {
        guard let client else { return false }
        do {
            mergeChat([try await client.sendChat(text, id)])
            replace(try await client.get(id))
            flash(.info, "Comment sent to your agent.")
            return true
        } catch {
            flash(.error, error.localizedDescription)
            return false
        }
    }

    /// A box change can move the posting between Ready to submit, Needs your answer and On hold: fetch it again.
    private func noteReview(_ id: String, _ doc: ReviewDoc) {
        guard let client else { return }
        Task { if let p = try? await client.get(id) { replace(p) } }
    }

    /// "Use my text". Returns true when it was saved.
    @discardableResult
    func editBox(_ id: String, key: String, text: String) async -> Bool {
        guard let client else { return false }
        do {
            let doc = try await client.editBox(id, key, text)
            noteReview(id, doc)
            flash(.info, "Saved. Your agent updates the form with your text.")
            await refreshQuestions()
            return true
        } catch {
            flash(.error, error.localizedDescription)
            return false
        }
    }

    func commentBox(_ id: String, key: String, text: String) async -> ReviewDoc? {
        guard let client else { return nil }
        do {
            let doc = try await client.commentBox(id, key, text)
            noteReview(id, doc)
            flash(.info, "Comment sent to your agent.")
            return doc
        } catch {
            flash(.error, error.localizedDescription)
            return nil
        }
    }

    func data(_ rel: String) async throws -> Data {
        guard let client else { throw APIError.notPaired }
        return try await client.fetchData(rel)
    }

    /// Not optimistic: the server re-checks the gate, so the record only changes once it says yes.
    /// Returns true when the approval was stored.
    @discardableResult
    func approveSubmit(_ id: String, note: String? = nil, fillDigest: String) async -> Bool {
        guard let client else { return false }
        do {
            replace(try await client.approveSubmit(id, note, fillDigest))
            flash(.info, "Submit approved. Your agent submits it and tells you.")
            return true
        } catch {
            flash(.error, error.localizedDescription)
            return false
        }
    }

    func download(_ rel: String) async throws -> URL {
        guard let client else { throw APIError.notPaired }
        return try await client.download(rel)
    }

    func image(_ rel: String, maxPixel: Int) async throws -> UIImage {
        guard let client else { throw APIError.notPaired }
        return try await client.fetchImage(rel, maxPixel)
    }

    // MARK: helpers

    private func replace(_ p: Posting) {
        if let i = postings.firstIndex(where: { $0.id == p.id }) { postings[i] = p } else { postings.append(p) }
    }

    private var toastTask: Task<Void, Never>?
    func flash(_ kind: Toast.Kind, _ text: String) {
        toast = Toast(kind: kind, text: text)
        toastTask?.cancel()
        toastTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(kind == .error ? 5 : 2.5))
            if !Task.isCancelled { self?.toast = nil }
        }
    }
}
