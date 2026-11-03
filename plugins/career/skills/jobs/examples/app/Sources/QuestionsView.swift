import SwiftUI

/// Navigation targets inside the Questions tab: a question, or the posting a question belongs to.
enum QuestionRoute: Hashable {
    case question(String)
    case posting(String)
}

/// The Questions tab: open questions grouped by topic, then a collapsed "Answered" section.
struct QuestionsView: View {
    @Environment(AppStore.self) private var store
    @State private var path: [QuestionRoute] = []
    @State private var showClosed = false

    /// Open questions, minus the ones a Ready to submit or Needs your answer row already stands for.
    private var open: [Question] {
        let hidden = store.questionsShownAsRows
        return store.questions.filter { $0.isOpen && !hidden.contains($0.id) }
    }
    private var closed: [Question] { store.questions.filter { !$0.isOpen } }

    private var groups: [(QuestionTopic, [Question])] {
        QuestionTopic.allCases.compactMap { t in
            let rows = open.filter { $0.topicValue == t }
            return rows.isEmpty ? nil : (t, rows)
        }
    }

    var body: some View {
        NavigationStack(path: $path) {
            List {
                ReadySections(postings: store.readyForReview.filter{($0.reviewSummary?.questions ?? []).isEmpty}, part: .answers)
                if open.isEmpty, !closed.isEmpty, !store.postings.contains(where: \.waitsOnUser) {
                    Section {
                        Label("Nothing is waiting on you.", systemImage: "checkmark.circle")
                            .foregroundStyle(.secondary)
                    }
                }
                ForEach(groups, id: \.0) { topic, rows in
                    Section {
                        ForEach(rows) { q in
                            NavigationLink(value: QuestionRoute.question(q.id)) { QuestionRow(question: q).withCopyRef(q.ref) }
                                .accessibilityIdentifier("questions.row.\(q.id)")
                        }
                    } header: {
                        Label("\(topic.title) · \(rows.count)", systemImage: topic.symbol)
                    }
                }
                ReadySections(postings: store.readyForReview, part: .submitAndHold)
                NavigationLink("Saved answers") { SavedAnswersView() }
                if !closed.isEmpty {
                    Section {
                        Button {
                            withAnimation(.snappy) { showClosed.toggle() }
                        } label: {
                            HStack {
                                Label("Answered · \(closed.count)", systemImage: "tray.full")
                                    .font(.headline)
                                    .foregroundStyle(.primary)
                                Spacer(minLength: 8)
                                Image(systemName: "chevron.right")
                                    .font(.footnote.weight(.semibold))
                                    .foregroundStyle(.tertiary)
                                    .rotationEffect(.degrees(showClosed ? 90 : 0))
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityValue(showClosed ? "Expanded" : "Collapsed")
                        .accessibilityIdentifier("questions.answeredToggle")
                        if showClosed {
                            ForEach(closed) { q in
                                NavigationLink(value: QuestionRoute.question(q.id)) { QuestionRow(question: q, showsTopic: true).withCopyRef(q.ref) }
                                    .accessibilityIdentifier("questions.row.\(q.id)")
                            }
                        }
                    } footer: {
                        Text("Pull to refresh.")
                    }
                }
            }
            .listStyle(.insetGrouped)
            .overlay { emptyState }
            .chatButton()
            .refreshable { await store.refreshQuestions() }
            .navigationTitle("Questions")
            .navigationDestination(for: QuestionRoute.self) { route in
                switch route {
                case .question(let id): QuestionDetailView(id: id)
                case .posting(let id): DetailView(id: id)
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { store.showPairing = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Pairing")
                }
            }
            .task {
                openPending()
                if store.questions.isEmpty { await store.refreshQuestions() }
                // Posting cards and company names come from the Inbox's list; load it if this tab opened first.
                if store.postings.isEmpty { await store.refresh() }
            }
            .onChange(of: store.pendingQuestion) { openPending() }
        }
    }

    private func openPending() {
        guard let id = store.pendingQuestion else { return }
        store.pendingQuestion = nil
        path = [.question(id)]
    }

    @ViewBuilder
    private var emptyState: some View {
        switch store.questionsState {
        case .loading where store.questions.isEmpty:
            ProgressView("Loading questions…")
        case .failed(let msg) where store.questions.isEmpty:
            ContentUnavailableView {
                Label("Can't reach the server", systemImage: "wifi.exclamationmark")
            } description: {
                Text(msg)
            } actions: {
                Button("Retry") { Task { await store.refreshQuestions() } }
            }
        case .loaded where store.questions.isEmpty && store.readyForReview.isEmpty:
            ContentUnavailableView("No questions", systemImage: "checkmark.bubble",
                                   description: Text("Nothing from your agent is waiting on you."))
        default:
            EmptyView()
        }
    }
}

struct QuestionRow: View {
    @Environment(AppStore.self) private var store
    let question: Question
    var showsTopic = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Image(systemName: question.kindValue.symbol)
                    .imageScale(.small)
                    .foregroundStyle(question.isOpen ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                    .accessibilityHidden(true)
                Text(question.title)
                    .font(.body.weight(question.isOpen ? .medium : .regular))
            }
            if question.isOpen {
                Text(question.question)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            } else {
                Text(answerLine)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            meta
        }
        .padding(.vertical, 2)
    }

    private var answerLine: String {
        if question.isDismissed { return "Dismissed" }
        if question.answeredByComment { return "Comment: " + (question.note ?? "—") }
        return "Answer: " + (question.answer ?? "—")
    }

    @ViewBuilder
    private var meta: some View {
        let company = question.postingId.flatMap { store.posting($0)?.company }
        let parts = [showsTopic ? question.topicValue.title : nil, company,
                     question.askedDate.map { "asked " + $0.formatted(.relative(presentation: .named)) }].compactMap { $0 }
        if !parts.isEmpty {
            Text(parts.joined(separator: " · "))
                .font(.caption)
                .foregroundStyle(.tertiary)
                .lineLimit(1)
        }
    }
}

// MARK: - Detail

struct QuestionDetailView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var typeSize
    let id: String

    @State private var draft = ""
    @State private var choice: String?
    @State private var selections: Set<String> = []
    /// Confirm kind: editing the proposed text before sending it.
    @State private var editing = false
    /// Answered or dismissed: the controls are shown again to change the answer.
    @State private var changing = false
    @State private var sending = false
    /// Ask turns whose very long context the user folded with "Show less".
    @State private var foldedContexts: Set<Int> = []
    /// Answered threads start collapsed to the last exchange.
    @State private var showEarlier = false
    @State private var confirmDismiss = false
    @State private var boxEdit: BoxEdit?

    private struct BoxEdit: Identifiable {
        let ref: Question.AppliesTo.ReviewBoxRef
        let field: PreviewDoc.Field
        var id: String { ref.key }
    }
    /// Optional free-text comment, any kind: sent with the answer, or alone with "Send comment only".
    @State private var comment = ""
    @FocusState private var fieldFocused: Bool
    @FocusState private var commentFocused: Bool

    /// Contexts (drafted essays) always open in full. Only one longer than `foldableLength` offers "Show less",
    /// which folds it to `foldedLines` until the user opens it again; nothing folds on its own.
    private static let foldedLines = 8
    private static let foldableLength = 1500

    var body: some View {
        if let q = store.question(id) {
            content(q)
        } else {
            ContentUnavailableView("Question not loaded", systemImage: "questionmark.bubble")
        }
    }

    private func content(_ q: Question) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header(q)
                if let pid = q.postingId ?? q.appliesTo?.postingId { postingCard(pid) }
                threadView(q)
                if let pid = q.appliesTo?.approveSubmit, q.isOpen {
                    // A submit is approved only at the end of the Application preview, after every field.
                    Card {
                        Text("See every field as the employer will receive it, then approve the submit at the end.")
                            .font(.callout).foregroundStyle(.secondary)
                        Button { store.openPreview(pid) } label: {
                            Label("Open the application preview", systemImage: "doc.text.magnifyingglass").frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .accessibilityIdentifier("question.openPreview")
                    }
                } else if q.isOpen || changing { answerCard(q) } else { resultCard(q) }
                if q.isOpen, let rb = q.appliesTo?.singleBox {
                    Button { Task { await openEditor(rb) } } label: {
                        Label("Edit it myself", systemImage: "pencil").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("question.editMyself")
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 4)
            .padding(.bottom, 16)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(Color(.systemGroupedBackground))
        .chatButton()
        .sheet(item: $boxEdit) { e in
            FieldEditSheet(label: e.field.label, edit: e.field.edit) { text in
                Task {
                    await store.editBox(e.ref.postingId, key: e.ref.key, text: text)
                    await store.refresh()
                }
            }
        }
        .navigationTitle("Question")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if q.isOpen {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Dismiss question", systemImage: "xmark.bin", role: .destructive) { confirmDismiss = true }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .accessibilityLabel("More")
                    .accessibilityIdentifier("question.more")
                }
            }
        }
        .confirmationDialog("Dismiss this question?", isPresented: $confirmDismiss, titleVisibility: .visible) {
            Button("Dismiss", role: .destructive) { Task { await store.dismissQuestion(id) } }
        } message: {
            Text("It moves to Answered without an answer. Nothing is written.")
        }
    }

    // MARK: sections

    private func header(_ q: Question) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            FlowLayout(spacing: 6) {
                Chip(text: q.topicValue.title, systemImage: q.topicValue.symbol)
                if !q.isOpen {
                    Chip(text: q.isDismissed ? "Dismissed" : "Answered", tint: q.isDismissed ? .secondary : .green,
                         systemImage: q.isDismissed ? "xmark.circle" : "checkmark.circle")
                }
            }
            Text(q.title)
                .font(.title3.bold())
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            CopyRefButton(text: q.ref, stacked: false)
            if let line = askedLine(q) {
                Text(line)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func askedLine(_ q: Question) -> String? {
        let who: String? = switch q.by {
        case nil, "": nil
        case "career": "your agent"
        case "session": "a session"
        case let by?: by.hasPrefix("agent:") ? "agent " + String(by.dropFirst(6)) : by
        }
        let when = q.askedDate.map { $0.formatted(.relative(presentation: .named)) }
        switch (who, when) {
        case let (w?, t?): return "Asked by \(w), \(t)"
        case let (w?, nil): return "Asked by \(w)"
        case let (nil, t?): return "Asked \(t)"
        default: return nil
        }
    }

    // MARK: thread

    /// The conversation: agent asks on the left, the user's answers and comments on the right. An answered thread
    /// starts collapsed to its last exchange.
    @ViewBuilder
    private func threadView(_ q: Question) -> some View {
        let turns = q.turns
        let collapsed = !(q.isOpen || changing) && !showEarlier && turns.count > 2
        let shown = collapsed ? Array(turns.suffix(2)) : turns
        VStack(alignment: .leading, spacing: 10) {
            if collapsed {
                Button("Show \(turns.count - shown.count) earlier message\(turns.count - shown.count == 1 ? "" : "s")") {
                    withAnimation(.snappy) { showEarlier = true }
                }
                .font(.callout.weight(.medium))
                .frame(maxWidth: .infinity)
                .accessibilityIdentifier("question.showEarlier")
            }
            ForEach(shown) { t in
                if t.isAsk { askBubble(t, isLatest: t.n == turns.last(where: \.isAsk)?.n) } else { userBubble(t) }
            }
        }
        .accessibilityIdentifier("question.thread")
    }

    private func askBubble(_ t: Question.Turn, isLatest: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            VStack(alignment: .leading, spacing: 8) {
                if let title = t.title, t.n > 1 {
                    Text(title).font(.subheadline.weight(.semibold))
                }
                Text(t.text ?? "")
                    .font(.body)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier(isLatest ? "question.text" : "question.turn.\(t.n)")
                if let cmd = t.command?.trimmingCharacters(in: .whitespacesAndNewlines), !cmd.isEmpty {
                    CommandBlock(command: cmd)
                }
                if let c = t.context?.trimmingCharacters(in: .whitespacesAndNewlines), !c.isEmpty {
                    turnContext(c, turn: t)
                }
            }
            .padding(12)
            .background(Color(.secondarySystemGroupedBackground),
                        in: UnevenRoundedRectangle(topLeadingRadius: 4, bottomLeadingRadius: 16,
                                                   bottomTrailingRadius: 16, topTrailingRadius: 16, style: .continuous))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.trailing, 28)
            turnMeta(t, alignment: .leading)
        }
    }

    private func userBubble(_ t: Question.Turn) -> some View {
        VStack(alignment: .trailing, spacing: 4) {
            VStack(alignment: .leading, spacing: 4) {
                if let a = t.answer, !a.isEmpty {
                    Text(a).font(.body.weight(.semibold))
                }
                if let c = t.text, !c.isEmpty {
                    Text(c).font(.body)
                }
                if (t.answer ?? "").isEmpty && (t.text ?? "").isEmpty {
                    Text("—").font(.body)
                }
            }
            .foregroundStyle(.white)
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
            .padding(12)
            .background(Color.accentColor,
                        in: UnevenRoundedRectangle(topLeadingRadius: 16, bottomLeadingRadius: 16,
                                                   bottomTrailingRadius: 16, topTrailingRadius: 4, style: .continuous))
            .padding(.leading, 48)
            turnMeta(t, alignment: .trailing)
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("question.userTurn.\(t.n)")
    }

    private func turnMeta(_ t: Question.Turn, alignment: HorizontalAlignment) -> some View {
        let who: String? = t.isAsk ? nil : (t.by == "phone" || t.by == "web" || t.by == nil ? "You" : t.by)
        let when = t.date.map(Self.turnTime)
        return Text([who, when].compactMap { $0 }.joined(separator: " · "))
            .font(.caption2)
            .foregroundStyle(.secondary)
            .padding(.horizontal, 4)
    }

    static func turnTime(_ d: Date) -> String {
        Calendar.current.isDateInToday(d)
            ? d.formatted(date: .omitted, time: .shortened)
            : d.formatted(.dateTime.month(.abbreviated).day().hour().minute())
    }

    private func turnContext(_ text: String, turn t: Question.Turn) -> some View {
        let isConfirm = t.kind == QuestionKind.confirm.rawValue
        // A confirm context is the proposed answer itself, so it is never split into commands.
        let parts = isConfirm ? [] : CommandText.split(text)
        let hasCommand = parts.contains { if case .command = $0 { true } else { false } }
        let long = text.count > Self.foldableLength && !hasCommand
        let open = !foldedContexts.contains(t.n)
        return VStack(alignment: .leading, spacing: 6) {
            Divider()
            Text(isConfirm ? "Proposed answer" : "Context")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            if hasCommand {
                ForEach(Array(parts.enumerated()), id: \.offset) { _, part in
                    switch part {
                    case .text(let s):
                        Text(Self.linkified(s))
                            .font(.callout)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    case .command(let c):
                        CommandBlock(command: c)
                    }
                }
                .accessibilityIdentifier("question.context")
            } else {
                Text(Self.linkified(text))
                    .font(.callout)
                    .lineLimit(long && !open ? Self.foldedLines : nil)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("question.context")
            }
            if long {
                Button(open ? "Show less" : "Show more") {
                    withAnimation(.snappy) {
                        if open { foldedContexts.insert(t.n) } else { foldedContexts.remove(t.n) }
                    }
                }
                .font(.callout.weight(.medium))
                .accessibilityIdentifier("question.contextToggle")
            }
        }
    }

    @ViewBuilder
    private func postingCard(_ pid: String) -> some View {
        if let p = store.posting(pid) {
            NavigationLink(value: QuestionRoute.posting(pid)) {
                HStack(spacing: 12) {
                    CompanyLogoView(logo: p.logo, company: p.company, size: 36, cornerRadius: 8)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(p.title)
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(.primary)
                            .multilineTextAlignment(.leading)
                        Text("\(p.company) · \(p.status)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.right")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.tertiary)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens the posting")
            .accessibilityIdentifier("question.posting")
        } else {
            Label("Posting \(pid)", systemImage: "doc.text")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: answering

    private func answerCard(_ q: Question) -> some View {
        Card {
            SectionHead(text: changing ? "Change your answer" : "Your answer")
            Text(q.effectLine)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            switch q.kindValue {
            case .yesno: yesNo
            case .choice: choices(q.options ?? [])
            case .multiselect: choices(q.options ?? [], multi:true)
            case .text: textEntry(placeholder: "Type your answer")
            case .confirm:
                if editing { textEntry(placeholder: "Edit the answer") } else { confirmButtons(q) }
            }
            commentSection(q)
            if changing {
                Button("Cancel") { changing = false; editing = false }
                    .font(.callout)
            }
        }
        .disabled(sending)
        .overlay { if sending { ProgressView().controlSize(.large) } }
    }

    /// Side by side; stacked at accessibility sizes so the labels never truncate.
    private var buttonRow: AnyLayout {
        typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(spacing: 10)) : AnyLayout(HStackLayout(spacing: 10))
    }

    private var yesNo: some View {
        buttonRow {
            Button { send("Yes") } label: {
                Label("Yes", systemImage: "checkmark").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .accessibilityIdentifier("question.yes")
            Button { send("No") } label: {
                Label("No", systemImage: "xmark").frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .accessibilityIdentifier("question.no")
        }
        .controlSize(.large)
    }

    private func choices(_ options: [String], multi: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            FlowLayout(spacing: 8) {
                ForEach(options, id: \.self) { o in
                    let selected = multi ? selections.contains(o) : choice == o
                    Button { if multi { if selected {selections.remove(o)} else {selections.insert(o)} } else {choice = o} } label: {
                        HStack(spacing: 4) {
                            if selected { Image(systemName: "checkmark").imageScale(.small) }
                            Text(o)
                        }
                        .font(.callout.weight(selected ? .semibold : .regular))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .foregroundStyle(selected ? Color.white : Color.primary)
                        .background(selected ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(Color(.tertiarySystemFill)),
                                    in: Capsule())
                        .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
                    .accessibilityIdentifier("question.option.\(o)")
                }
            }
            Button { if multi {send(options.filter(selections.contains).joined(separator:"; "))} else if let choice {send(choice)} } label: {
                Text(multi ? "Save choices" : "Send").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(multi ? selections.isEmpty : choice == nil)
            .accessibilityIdentifier("question.send")
        }
    }

    private func textEntry(placeholder: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            TextField(placeholder, text: $draft, axis: .vertical)
                .lineLimit(3...12)
                .focused($fieldFocused)
                .returnSends { if !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { send(draft) } }
                .padding(10)
                .background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .accessibilityIdentifier("question.field")
            Button { send(draft) } label: {
                Label("Send", systemImage: "paperplane.fill").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .accessibilityIdentifier("question.send")
        }
    }

    /// A review question's draft opens the same editor as the preview's Edit.
    private func openEditor(_ rb: Question.AppliesTo.ReviewBoxRef) async {
        do {
            let doc = try await store.preview(rb.postingId)
            let f = (doc.sections ?? []).flatMap(\.groups).flatMap(\.fields).first { $0.box?.key == rb.key }
            if let f, f.edit != nil {
                boxEdit = BoxEdit(ref: rb, field: f)
            } else {
                store.flash(.error, "This field cannot be edited here. Comment instead.")
            }
        } catch {
            store.flash(.error, error.localizedDescription)
        }
    }

    private var trimmedComment: String { comment.trimmingCharacters(in: .whitespacesAndNewlines) }

    private func commentSection(_ q: Question) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider().padding(.vertical, 2)
            Text("Add a comment")
                .font(.subheadline.weight(.semibold))
            TextField("Optional. Sent with your answer.", text: $comment, axis: .vertical)
                .lineLimit(2...8)
                .focused($commentFocused)
                .returnSends { if !trimmedComment.isEmpty { sendCommentOnly() } }
                .padding(10)
                .background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .accessibilityIdentifier("question.comment")
            let alreadyAnswered = q.status == "answered"
            Button { sendCommentOnly() } label: {
                Label(alreadyAnswered ? "Add comment" : "Answer with comment", systemImage: "text.bubble")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(trimmedComment.isEmpty)
            .accessibilityHint(alreadyAnswered ? "Adds the comment; your answer stays as it is"
                                               : "Answers the question with your comment")
            .accessibilityIdentifier("question.commentOnly")
        }
    }

    private func confirmButtons(_ q: Question) -> some View {
        buttonRow {
            Button { send("Confirmed") } label: {
                Label("Confirm", systemImage: "checkmark.seal").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .accessibilityIdentifier("question.confirm")
            Button {
                draft = q.context ?? ""
                editing = true
                fieldFocused = true
            } label: {
                Label("Edit first", systemImage: "pencil").frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .accessibilityIdentifier("question.edit")
        }
        .controlSize(.large)
    }

    /// Under an answered thread: status only. The answer and comments are in the thread above.
    private func resultCard(_ q: Question) -> some View {
        Card {
            SectionHead(text: q.isDismissed ? "Dismissed" : (q.answeredByComment ? "Answered with a comment" : "Answered"))
            if q.answeredByComment {
                Text("The agent that asked reads your comment and replies in this thread if needed.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if q.isDismissed {
                Text("No answer was recorded.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                Text(q.kindValue == .confirm && q.answer == "Confirmed" ? "Confirmed" : (q.answer ?? "—"))
                    .font(.body.weight(.medium))
                    .textSelection(.enabled)
                    .accessibilityIdentifier("question.answer")
                if let err = q.applyError {
                    Label("Not applied yet: \(err)", systemImage: "exclamationmark.triangle")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                } else if let r = q.applied?.result {
                    Label(r, systemImage: "checkmark.circle")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if let d = q.answeredDate {
                Text("Answered \(d.formatted(.relative(presentation: .named)))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Button(q.isDismissed ? "Answer anyway" : "Change answer or comment") {
                draft = q.kindValue == .text ? (q.answer ?? "") : ""
                choice = q.kindValue == .choice ? q.answer : nil
                selections = q.kindValue == .multiselect ? Set((q.answer ?? "").components(separatedBy:"; ").filter{!$0.isEmpty}) : []
                editing = false
                changing = true
            }
            .font(.callout.weight(.medium))
            .accessibilityIdentifier("question.change")
        }
    }

    private func send(_ answer: String) {
        let text = answer.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        fieldFocused = false
        commentFocused = false
        sending = true
        let note = trimmedComment.isEmpty ? nil : trimmedComment
        Task {
            let ok = await store.answer(id, text, note: note)
            sending = false
            if ok {
                changing = false
                editing = false
                draft = ""
                comment = ""
            }
        }
    }

    private func sendCommentOnly() {
        let note = trimmedComment
        guard !note.isEmpty, !sending else { return }
        fieldFocused = false
        commentFocused = false
        sending = true
        Task {
            let ok = await store.comment(id, note)
            sending = false
            if ok {
                comment = ""
                changing = false
            }
        }
    }

    /// Plain text with every URL made tappable.
    static func linkified(_ s: String) -> AttributedString {
        var out = AttributedString(s)
        guard let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue) else { return out }
        for m in detector.matches(in: s, range: NSRange(s.startIndex..., in: s)) {
            guard let url = m.url, let r = Range(m.range, in: s), let ar = Range(r, in: out) else { continue }
            out[ar][AttributeScopes.FoundationAttributes.LinkAttribute.self] = url
        }
        return out
    }
}
