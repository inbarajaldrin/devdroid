import SwiftUI

/// Filled postings in the Questions tab, sorted by what the user has to do (serve.py `review_summary.action`):
/// Needs your answer (opens the question) sits with the open questions; Ready to submit (opens the Application preview)
/// and On hold (the agent is fixing something, or his comment is open) go below them (user, 2026-11-02). Rows open the
/// application; commenting lives in its preview (user, 2026-11-01). Use inside a List.
struct ReadySections: View {
    enum Part { case answers, submitAndHold }

    @Environment(AppStore.self) private var store
    let postings: [Posting]
    let part: Part
    @State private var commentOn: Posting?

    var body: some View {
        let submit = part == .submitAndHold ? postings.filter { $0.reviewAction == "submit" } : []
        let answer = part == .answers ? postings.filter { $0.reviewAction == "answer" } : []
        let hold = part == .submitAndHold ? postings.filter { $0.reviewAction == "hold" } : []
        Group {
            if !answer.isEmpty {
                Section {
                    ForEach(answer) { p in answerRow(p) }
                } header: {
                    Label("Needs your answer · \(answer.count)", systemImage: "questionmark.bubble").foregroundStyle(.tint)
                }
            }
            if !submit.isEmpty {
                Section {
                    ForEach(submit) { p in submitRow(p) }
                } header: {
                    Label("Ready to submit · \(submit.count)", systemImage: "paperplane").foregroundStyle(.tint)
                }
            }
            if !hold.isEmpty {
                Section {
                    ForEach(hold) { p in
                        // Readable while on hold: the preview shows what is filled; its button stays off.
                        row(p, line: p.reviewSummary?.why, muted: true) { store.openPreview(p.id) }
                            .accessibilityIdentifier("ready.hold.\(p.id)")
                    }
                } header: {
                    Label("On hold · \(hold.count)", systemImage: "clock")
                }
            }
        }
        .sheet(item: $commentOn) { p in
            FieldCommentSheet(label: "\(p.company): \(p.title)") { text in
                Task { await store.commentOnApplication(p.id, text) }
            }
            .presentationDetents([.height(300)])
        }
    }

    /// The row opens the Application preview; the submit is approved only at its end, after every field.
    private func submitRow(_ p: Posting) -> some View {
        row(p, line: nil, reviewAndSubmit: true) { store.openPreview(p.id) }
            .accessibilityIdentifier("ready.row.\(p.id)")
    }

    private func answerRow(_ p: Posting) -> some View {
        row(p, line: p.reviewSummary?.why ?? "A question for you", lineTint: .orange) {
            if let q = p.reviewSummary?.questions?.first { store.openQuestion(q.id) } else { store.openPreview(p.id) }
        }
        .accessibilityIdentifier("ready.answer.\(p.id)")
    }

    /// The posting, then its actions under it. Styled buttons keep their own tap inside a List row.
    private func row(_ p: Posting, line: String?, muted: Bool = false, lineTint: Color? = nil, reviewAndSubmit: Bool = false,
                     open: @escaping () -> Void) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Button(action: open) {
                HStack {
                    ReadyRowBody(posting: p, line: line, muted: muted, lineTint: lineTint)
                    Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            HStack(spacing: 8) {
                if reviewAndSubmit {
                    Button("Review and submit", action: open)
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small)
                        .font(.footnote.weight(.semibold))
                }
                CopyRefButton(text: p.ref, stacked: false)
            }
            .padding(.leading, 38)
        }
    }
}

/// Logo, title, company, deadline, and one plain line saying where the posting stands.
struct ReadyRowBody: View {
    let posting: Posting
    var line: String?
    var muted = false
    var lineTint: Color?
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let d = PostingDates(posting)
        HStack(alignment: .top, spacing: 10) {
            CompanyLogoView(logo: posting.logo, company: posting.company, size: 28, cornerRadius: 6)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 4) {
                Text(posting.title)
                    .font(.subheadline.weight(muted ? .regular : .medium))
                    .foregroundStyle(muted ? .secondary : .primary)
                    .lineLimit(typeSize.isAccessibilitySize ? 4 : 2)
                Text(posting.company)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                let layout = typeSize.isAccessibilitySize
                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
                    : AnyLayout(FlowLayout(spacing: 8))
                layout {
                    if !muted {
                        if d.level.actSoon {
                            DateChip(text: d.actSoonChip, level: d.level)
                        } else if let chip = d.rowChip {
                            DateChip(text: chip, level: d.level)
                        }
                    }
                    if let line, !line.isEmpty {
                        Text(line)
                            .font(.caption.weight(lineTint == nil ? .regular : .semibold))
                            .foregroundStyle(lineTint.map { AnyShapeStyle($0) } ?? AnyShapeStyle(.secondary))
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

/// Everything the "Review application" card shows, loaded together for a `ready` (or `applied`) record:
/// the Apply stage's form_fill.json (`/postings/<id>/fill`), the submit gate (`/postings/<id>/approve-submit`),
/// the free-text questions (`materials.form_questions_md`) and the confirmation text after the submit.
struct ApplicationReview: Equatable {
    var fill: FormFill?
    var gate: SubmitGate?
    var questions: [FormQuestion] = []
    var confirmationText: String?
    var reviewText: String?
    /// The per-box review (review.json); nil until the session runs `career.py fill`.
    var boxes: ReviewDoc?
    var error: String?

    static func load(_ p: Posting, store: AppStore) async -> ApplicationReview {
        var r = ApplicationReview()
        let m = p.materials ?? Materials()
        if p.status == "ready" {
            do { r.fill = try await store.fill(p.id) } catch { r.error = error.localizedDescription }
            r.boxes = try? await store.review(p.id)
            // The server owns the gate; fall back to what the phone can see when it cannot answer.
            r.gate = (try? await store.submitGate(p.id)) ?? localGate(p, fill: r.fill)
            if let rel = m.formQuestionsMd, let data = try? await store.data(rel) {
                r.questions = FormQuestion.parse(String(decoding: data, as: UTF8.self))
            }
            if let rel = m.reviewTxt, let data = try? await store.data(rel) {
                r.reviewText = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }
        if let rel = m.confirmationTxt, let data = try? await store.data(rel) {
            r.confirmationText = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return r
    }

    /// Same rules as serve.py's submit gate, for when the server's dry check is unreachable.
    static func localGate(_ p: Posting, fill: FormFill?) -> SubmitGate {
        var missing: [String] = []
        if p.status != "ready" { missing.append("status is '\(p.status)', must be 'ready'") }
        if p.ruling?.decision != "approved" { missing.append("the posting is not approved") }
        if let fill {
            if fill.dryRun == true { missing.append("the form was only filled as a rehearsal (--dry-run)") }
            let nu = fill.needsUser
            if !nu.isEmpty {
                missing.append("\(nu.count) field(s) still need the user: " + nu.map { "\"\($0.label ?? "?")\"" }.joined(separator: "; "))
            }
        } else {
            missing.append("no form_fill.json yet")
        }
        return SubmitGate(allowed: missing.isEmpty, missing: missing)
    }

    /// Where the "Approve submit" button stands.
    enum Approval: Equatable {
        case loading
        case blocked(String)
        case approved(at: String, by: String?)
        case allowed
    }

    func approval(_ p: Posting) -> Approval {
        if let at = p.ruling?.submitApprovedAt, !at.isEmpty, p.reviewAction != "hold" {   // a comment after it holds it
            // An approval older than the current fill does not cover it (career.py re-asks after a re-fill).
            if let filled = fill?.filledAt, at < filled {
                // fall through to the gate: the user approves the new fill
            } else {
                return .approved(at: at, by: p.ruling?.submitApprovedBy)
            }
        }
        guard let gate else { return .loading }
        if let boxes, !boxes.allApproved {
            let c = boxes.counts
            return .blocked("\(c.approved) of \(c.total) boxes approved" + (c.blocked > 0 ? ", \(c.blocked) flagged block" : ""))
        }
        if gate.allowed { return .allowed }
        return .blocked(gate.missing.first.map { first in
            gate.missing.count > 1 ? "\(first) (+\(gate.missing.count - 1) more)" : first
        } ?? "The server refused the approval.")
    }
}

/// One `### ` question of form_questions.md and whatever answer sits under it.
struct FormQuestion: Equatable, Identifiable {
    var question: String
    var answer: String?
    var id: String { question }

    static func parse(_ md: String) -> [FormQuestion] {
        var out: [FormQuestion] = []
        var current: String?
        var body: [String] = []
        func flush() {
            guard let q = current else { return }
            let a = body.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            out.append(FormQuestion(question: q, answer: a.isEmpty ? nil : a))
        }
        for line in md.components(separatedBy: .newlines) {
            if line.hasPrefix("### ") {
                flush()
                current = String(line.dropFirst(4)).trimmingCharacters(in: .whitespaces)
                body = []
            } else if current != nil {
                let t = line.trimmingCharacters(in: .whitespaces)
                if t.hasPrefix("<!--") && t.hasSuffix("-->") { continue }   // "optional · textarea" hints
                body.append(line)
            }
        }
        flush()
        return out
    }
}

/// The "Review application" card on a `ready` record, or the "Submitted" card once your agent has
/// pressed Submit (`applied` with a confirmation).
struct ApplicationReviewCard: View {
    let posting: Posting
    let review: ApplicationReview?
    /// Opens `/files/<rel>` full screen.
    let onOpen: (_ rel: String, _ title: String) -> Void
    /// A box was approved or commented: the server's review after the change.
    var onBoxes: (ReviewDoc) -> Void = { _ in }

    private var materials: Materials { posting.materials ?? Materials() }

    var body: some View {
        Card {
            if posting.status == "ready" {
                readyContent
            } else {
                submittedContent
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("review.card")
    }

    // MARK: ready

    @ViewBuilder
    private var readyContent: some View {
        Label("Review application", systemImage: "doc.text.magnifyingglass")
            .font(.headline)
        Text("Your agent filled the form and stopped before Submit. Check it, then approve the submit below.")
            .font(.footnote)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        approvalLine
        if let rel = materials.formScreenshot ?? review?.fill?.screenshot {
            ScreenshotThumb(rel: rel, caption: "Filled form: Submit not pressed") {
                onOpen(rel, "Filled form")
            }
            .accessibilityIdentifier("review.screenshot")
        }
        workdayStepList
        if let text = review?.reviewText, !text.isEmpty {
            ConfirmationText(text: text, title: "Review page text (what will be sent)")
                .accessibilityIdentifier("review.reviewText")
        }
        if let review, let boxes = review.boxes {
            if review.fill?.dryRun == true {
                Label("Rehearsal fill (--dry-run). Run apply for real before approving.", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
            BoxList(postingId: posting.id, doc: boxes, onBoxes: onBoxes)
                .accessibilityIdentifier("review.boxes")
        } else if let review {
            if let fill = review.fill {
                fillMeta(fill)
                FieldTable(rows: fill.rows)
                    .accessibilityIdentifier("review.fields")
            } else if let e = review.error {
                Text("Could not load the field list: \(e)").font(.footnote).foregroundStyle(.orange)
            } else {
                Text("No form_fill.json for this posting yet.").font(.footnote).foregroundStyle(.secondary)
            }
            if !review.questions.isEmpty {
                QuestionList(questions: review.questions)
                    .accessibilityIdentifier("review.questions")
            }
        } else {
            ProgressView().frame(maxWidth: .infinity)
        }
    }

    /// Workday: every wizard step as filled, so the whole application can be seen; the last is the Review page.
    @ViewBuilder
    private var workdayStepList: some View {
        let steps = materials.workdaySteps ?? []
        if !steps.isEmpty {
            let names = (review?.fill?.steps ?? []).map { $0.name }
            DisclosureGroup {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(Array(steps.enumerated()), id: \.offset) { i, rel in
                        let name = i < names.count ? names[i] : nil
                        let title = "Step \(i + 1) of \(steps.count)" + (name.map { ": \($0)" } ?? "")
                        Button { onOpen(rel, title) } label: {
                            Label(title, systemImage: "doc.richtext").font(.subheadline)
                        }
                    }
                }
                .padding(.top, 6)
            } label: {
                Text("Every step (\(steps.count))").font(.subheadline.weight(.semibold))
            }
            .accessibilityIdentifier("review.steps")
        }
    }

    @ViewBuilder
    private var approvalLine: some View {
        if let at = posting.ruling?.submitApprovedAt, !at.isEmpty {
            Label {
                Text("Submit approved \(Self.short(at))" + (posting.ruling?.submitApprovedBy.map { " by \($0)" } ?? ""))
            } icon: {
                Image(systemName: "checkmark.seal.fill").foregroundStyle(.green)
            }
            .font(.subheadline.weight(.semibold))
            .accessibilityIdentifier("review.approvedAt")
        }
    }

    private func fillMeta(_ fill: FormFill) -> some View {
        let parts = [fill.filledAt.map { "filled \(Self.short($0))" },
                     fill.submitControl?.text.map { "stops before “\($0)”" }].compactMap { $0 }
        return VStack(alignment: .leading, spacing: 4) {
            if !parts.isEmpty {
                Text(parts.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
            }
            if fill.dryRun == true {
                Label("Rehearsal fill (--dry-run). Run apply for real before approving.", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
    }

    // MARK: applied

    @ViewBuilder
    private var submittedContent: some View {
        Label("Application submitted", systemImage: "paperplane.fill")
            .font(.headline)
        if let s = posting.submission {
            let when = s.confirmedAt ?? s.submittedAt ?? s.clickedAt
            if let when {
                Text("Submitted by your agent \(Self.short(when))").font(.caption).foregroundStyle(.secondary)
            }
            if let line = s.line {
                Text("“\(line)”").font(.callout.italic()).fixedSize(horizontal: false, vertical: true)
            }
        }
        if let rel = materials.confirmationPng {
            ScreenshotThumb(rel: rel, caption: "Confirmation page") { onOpen(rel, "Confirmation") }
                .accessibilityIdentifier("review.confirmation")
        }
        if let text = review?.confirmationText, !text.isEmpty {
            ConfirmationText(text: text)
        }
    }

    /// "2026-10-20T01:56:21Z" → "Oct 20, 6:56 PM" in the phone's time zone; the raw string if it does not parse.
    static func short(_ iso: String) -> String {
        guard let d = try? Date(iso, strategy: .iso8601) else { return iso }
        return d.formatted(.dateTime.month(.abbreviated).day().hour().minute())
    }
}

/// A tall full-page screenshot, cropped to its top and fetched uncached. Tap opens it full screen.
private struct ScreenshotThumb: View {
    @Environment(AppStore.self) private var store
    let rel: String
    let caption: String
    let onTap: () -> Void

    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                Color(.tertiarySystemFill)
                    .frame(height: 260)
                    .overlay(alignment: .top) {
                        if let image {
                            Image(uiImage: image)
                                .resizable()
                                .aspectRatio(contentMode: .fill)
                                .frame(maxWidth: .infinity, alignment: .top)
                        } else if failed {
                            Label("Screenshot unavailable", systemImage: "photo").foregroundStyle(.tertiary)
                                .frame(maxHeight: .infinity)
                        } else {
                            ProgressView().frame(maxHeight: .infinity)
                        }
                    }
                    .overlay(alignment: .bottomTrailing) {
                        Label("Full screen", systemImage: "arrow.up.left.and.arrow.down.right")
                            .font(.caption.weight(.semibold))
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(.ultraThinMaterial, in: Capsule())
                            .padding(8)
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Color(.separator)))
                Text(caption).font(.footnote).foregroundStyle(.secondary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(caption)
        .accessibilityHint("Opens full screen")
        .task(id: rel) {
            failed = false
            do {
                let data = try await store.data(rel)
                image = ImageCache.downsample(data, maxPixel: 1400)
                failed = image == nil
            } catch {
                if !Task.isCancelled { failed = true }
            }
        }
    }
}

/// The per-box review: every filled value and drafted answer with the reviewer's flags, its state, and
/// Approve / Comment. Approve is off while a box carries an active block flag (comment so your agent fixes it).
private struct BoxList: View {
    @Environment(AppStore.self) private var store
    let postingId: String
    let doc: ReviewDoc
    let onBoxes: (ReviewDoc) -> Void

    @State private var commenting: ReviewBox?
    @State private var busy: String?

    var body: some View {
        let c = doc.counts
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("Review every box").font(.subheadline.weight(.semibold))
                Spacer()
                Text("\(c.approved) of \(c.total) boxes approved")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(doc.allApproved ? AnyShapeStyle(.green) : AnyShapeStyle(.secondary))
                    .accessibilityIdentifier("review.boxCount")
            }
            ProgressView(value: Double(c.approved), total: Double(max(c.total, 1)))
                .tint(.green)
                .accessibilityHidden(true)
            Text("Approve each value, or comment on it. A comment goes to your agent, who fixes it and shows the box again.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(doc.boxes) { box in
                BoxRow(box: box, busy: busy == box.key,
                       onApprove: { Task { await approve(box) } },
                       onComment: { commenting = box })
            }
        }
        .sheet(item: $commenting) { box in
            BoxCommentSheet(box: box) { text in
                Task {
                    busy = box.key
                    if let d = await store.commentBox(postingId, key: box.key, text: text) { onBoxes(d) }
                    busy = nil
                }
            }
            .presentationDetents([.medium, .large])
        }
    }

    private func approve(_ box: ReviewBox) async {
        busy = box.key
        if let d = await store.approveBox(postingId, key: box.key, seen: box.valueChangedAt) { onBoxes(d) }
        busy = nil
    }
}

private struct BoxRow: View {
    let box: ReviewBox
    let busy: Bool
    let onApprove: () -> Void
    let onComment: () -> Void

    private var stateTint: Color {
        switch box.state {
        case "approved": .green
        case "commented": .accentColor
        default: Color(.separator)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(box.label).font(.callout.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                Chip(text: Self.stateText(box.state), tint: box.state == "pending" ? nil : stateTint,
                     systemImage: box.state == "approved" ? "checkmark" : (box.state == "commented" ? "bubble.left" : "circle.dotted"))
            }
            Text(box.kind == "answer" ? "Custom answer" : "Field" + (box.required == true ? " · required" : ""))
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
            Group {
                if box.value.isEmpty {
                    Text(box.kind == "answer" ? "Not drafted yet" : "blank").foregroundStyle(.tertiary)
                } else {
                    Text(box.value).textSelection(.enabled)
                }
            }
            .font(.callout)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .fixedSize(horizontal: false, vertical: true)
            ForEach(Array(box.flags.enumerated()), id: \.offset) { _, f in
                FlagLine(flag: f)
            }
            ForEach(Array(box.comments.enumerated()), id: \.offset) { _, c in
                VStack(alignment: .leading, spacing: 2) {
                    Text((c.isMine ? "You" : "Agent") + (c.at.map { " · \(ApplicationReviewCard.short($0))" } ?? ""))
                        .font(.caption2).foregroundStyle(.secondary)
                    Text(c.text).font(.caption).fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(7)
                .background(Color(c.isMine ? .tertiarySystemFill : .quaternarySystemFill), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
            HStack(spacing: 10) {
                Spacer()
                Button(action: onComment) { Label("Comment", systemImage: "bubble.left") }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("review.box.comment")
                Button(action: onApprove) {
                    if busy { ProgressView() } else {
                        Label(box.state == "approved" ? "Approved" : "Approve", systemImage: "checkmark")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(box.state == "approved" || box.isBlocked || busy)
                .accessibilityIdentifier("review.box.approve")
                .accessibilityHint(box.isBlocked ? "Flagged block: comment so your agent fixes it" : "")
            }
            .controlSize(.small)
        }
        .padding(10)
        .overlay(alignment: .leading) {
            UnevenRoundedRectangle(topLeadingRadius: 10, bottomLeadingRadius: 10).fill(stateTint).frame(width: 3)
        }
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Color(.separator)))
        .accessibilityElement(children: .contain)
    }

    static func stateText(_ s: String) -> String {
        switch s {
        case "approved": "Approved"
        case "commented": "Commented"
        default: "Pending"
        }
    }
}

/// One reviewer or career flag, coloured by severity (block red, warning orange, note grey); a flag on an earlier
/// value is dimmed.
private struct FlagLine: View {
    let flag: ReviewBox.Flag

    private var tint: Color {
        switch flag.severity {
        case "block": .red
        case "warn": .orange
        default: .secondary
        }
    }

    var body: some View {
        let name = flag.severity == "block" ? "Block" : (flag.severity == "warn" ? "Warning" : "Note")
        let who = (flag.by == "career" ? "career check" : "reviewer") + (flag.stale == true ? ", on an earlier value" : "")
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Image(systemName: flag.severity == "info" ? "info.circle" : "exclamationmark.triangle.fill")
                .foregroundStyle(tint)
                .accessibilityHidden(true)
            (Text("\(name): ").bold() + Text(flag.text) + Text(" (\(who))").foregroundStyle(.secondary))
                .fixedSize(horizontal: false, vertical: true)
        }
        .font(.caption)
        .foregroundStyle(flag.severity == "info" ? AnyShapeStyle(.secondary) : AnyShapeStyle(tint))
        .padding(7)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint.opacity(flag.severity == "info" ? 0.08 : 0.14), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .opacity(flag.stale == true ? 0.55 : 1)
    }
}

private struct BoxCommentSheet: View {
    let box: ReviewBox
    let onSend: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(box.value.isEmpty ? "(blank)" : box.value).font(.callout).foregroundStyle(.secondary)
                } header: {
                    Text(box.label)
                }
                Section {
                    TextField("What should change?", text: $text, axis: .vertical)
                        .lineLimit(3...8)
                        .focused($focused)
                        .returnSends {
                            let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
                            if !t.isEmpty { onSend(t); dismiss() }
                        }
                        .accessibilityIdentifier("review.box.commentText")
                } footer: {
                    Text("Your agent reads it, applies it, and shows the box again.")
                }
            }
            .navigationTitle("Comment")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Send") {
                        onSend(text.trimmingCharacters(in: .whitespacesAndNewlines))
                        dismiss()
                    }
                    .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityIdentifier("review.box.commentSend")
                }
            }
            .onAppear { focused = true }
        }
    }
}

/// form_fill.json as a table: label | value | source. Rows your agent could not fill are orange.
private struct FieldTable: View {
    let rows: [FormFillRow]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            let nu = rows.filter(\.needsUser).count
            HStack {
                Text("Fields").font(.subheadline.weight(.semibold))
                Spacer()
                Text(nu == 0 ? "\(rows.count) filled" : "\(nu) of \(rows.count) need you")
                    .font(.caption.weight(nu == 0 ? .regular : .semibold))
                    .foregroundStyle(nu == 0 ? AnyShapeStyle(.secondary) : AnyShapeStyle(.orange))
            }
            Grid(alignment: .topLeading, horizontalSpacing: 8, verticalSpacing: 7) {
                GridRow {
                    Text("Field")
                    Text("Value")
                    Text("Source")
                }
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
                Divider().gridCellUnsizedAxes(.horizontal)
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    GridRow {
                        Text(row.label ?? "—")
                            .font(.caption.weight(.medium))
                            .frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)
                        valueCell(row)
                            .frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)
                        Text(Self.source(row))
                            .font(.caption2)
                            .foregroundStyle(row.needsUser ? AnyShapeStyle(.orange) : AnyShapeStyle(.secondary))
                            .frame(width: 84, alignment: .leading)
                    }
                    .foregroundStyle(row.needsUser ? AnyShapeStyle(.orange) : AnyShapeStyle(.primary))
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(Self.a11y(row))
                }
            }
        }
    }

    @ViewBuilder
    private func valueCell(_ row: FormFillRow) -> some View {
        if row.needsUser {
            Label(row.value ?? "needs you", systemImage: "exclamationmark.circle.fill")
                .font(.caption.weight(.semibold))
                .labelStyle(TightLabel())
        } else if let v = row.value, !v.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(row.type == "file" ? (v as NSString).lastPathComponent : v).font(.caption)
                if row.verified == true {
                    Image(systemName: "checkmark").font(.caption2.weight(.bold)).foregroundStyle(.green)
                        .accessibilityLabel("verified on the page")
                }
            }
        } else {
            Text("blank").font(.caption).foregroundStyle(.tertiary)
        }
    }

    /// "profile: answers.requires_sponsorship" → "profile · requires sponsorship" (wraps on spaces in the narrow
    /// column); a needs-user row says where to fix it.
    static func source(_ row: FormFillRow) -> String {
        if row.needsUser { return row.question == true ? "answer in questions file" : "needs user" }
        guard let s = row.source, !s.isEmpty else { return "—" }
        guard let colon = s.range(of: ": ") else { return s }
        let kind = s[..<colon.lowerBound]
        let key = s[colon.upperBound...].split(separator: ".").last.map(String.init) ?? ""
        return "\(kind) · " + key.replacingOccurrences(of: "_", with: " ")
    }

    static func a11y(_ row: FormFillRow) -> String {
        let v = row.needsUser ? "needs you" : (row.value ?? "blank")
        return "\(row.label ?? "Field"): \(v). Source \(row.needsUser ? source(row) : (row.source ?? "none"))."
    }
}

private struct TightLabel: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) { configuration.icon; configuration.title }
    }
}

private struct QuestionList: View {
    let questions: [FormQuestion]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Questions on the form").font(.subheadline.weight(.semibold))
            ForEach(questions) { q in
                VStack(alignment: .leading, spacing: 3) {
                    Text(q.question).font(.callout.weight(.medium)).fixedSize(horizontal: false, vertical: true)
                    if let a = q.answer {
                        Text(a).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    } else {
                        Text("Not answered yet: answer its question in Questions.")
                            .font(.caption).foregroundStyle(.orange)
                    }
                }
            }
        }
    }
}

private struct ConfirmationText: View {
    let text: String
    var title = "Confirmation page text"
    @State private var expanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            Text(text)
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 6)
        } label: {
            Text(title).font(.subheadline.weight(.semibold))
        }
        .accessibilityIdentifier("review.confirmationText")
    }
}
