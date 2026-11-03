import NaturalLanguage
import SwiftUI

struct DetailView: View {
    @Environment(AppStore.self) private var store
    let id: String

    @State private var sheet: RulingSheet?
    @State private var confirmApplied = false
    @State private var showPostingText = true    // the job description is open by default (user, 2026-11-01)
    @State private var showMoreCompany = false
    @State private var pager: PagerSelection?
    @State private var preview: PreviewItem?
    @State private var downloading: String?
    @State private var review: ApplicationReview?
    @State private var fullScreen: PreviewItem?

    enum RulingSheet: String, Identifiable { case approve, skip; var id: String { rawValue } }

    var body: some View {
        if let p = store.posting(id) {
            content(p)
        } else {
            ContentUnavailableView("Posting not loaded", systemImage: "doc.questionmark")
        }
    }

    private func content(_ p: Posting) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header(p)
                if let a = p.application { ApplicationTimelineCard(application: a, postingId: p.id) }
                // The Application preview replaced the per-box review cards (user, 2026-11-01): one card points at it.
                if Self.showsReview(p) { ApplicationLinkCard(posting: p) }
                if !p.images.isEmpty {
                    ImageStrip(images: p.images) { pager = PagerSelection(images: p.images, start: $0) }
                }
                if let s = p.summaryAi { summaryCard(s) } else { pendingCard("Summary not generated yet") }
                postingCard(p)
                if let c = p.companyInfo { companyCard(c, company: p.company) } else { pendingCard("Company research not generated yet") }
                materialsCard(p)
                if let h = p.history, !h.isEmpty { historyCard(h) }
            }
            .padding(.horizontal, 16)
            .padding(.top, 4)
            .padding(.bottom, 16)
        }
        .background(Color(.systemGroupedBackground))
        .chatButton()
        .navigationTitle(p.company)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            // Same title text as before, with the company mark beside it. navigationTitle stays for the back menu.
            ToolbarItem(placement: .principal) {
                HStack(spacing: 8) {
                    CompanyLogoView(logo: p.logo, company: p.company, size: 24, cornerRadius: 5)
                    Text(p.company)
                        .font(.headline)
                        .lineLimit(1)
                }
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.isHeader)
                .accessibilityIdentifier("detail.title")
            }
        }
        .safeAreaInset(edge: .bottom) { rulingBar(p) }
        .sheet(item: $sheet) { which in
            RulingReasonSheet(kind: which) { reason in
                Task { await store.rule(p.id, which == .approve ? .approved : .skipped, reason: reason) }
            }
            .presentationDetents([.height(260)])
        }
        .sheet(item: $preview) { item in QuickLookSheet(url: item.url, title: item.title) }
        .fullScreenCover(item: $fullScreen) { item in QuickLookSheet(url: item.url, title: item.title) }
        // Reload the review whenever the record moves (ready → approved → applied) or points at a new fill.
        .task(id: Self.reviewKey(p)) {
            guard Self.showsReview(p) else { review = nil; return }
            review = await ApplicationReview.load(p, store: store)
        }
        .fullScreenCover(item: $pager) { sel in ImagePager(images: sel.images, selection: sel.start) }
    }

    // MARK: cards

    private func header(_ p: Posting) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(p.title)
                .font(.title3.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
            CopyRefButton(text: p.ref, stacked: false)
            // The full homepage URL under the title (user, 2026-11-01); the rows keep the short domain label.
            if let u = p.website {
                Link(destination: u) {
                    Label(p.companyInfo?.website ?? u.absoluteString, systemImage: "globe")
                        .font(.subheadline)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .accessibilityLabel("Company website, \(WebsiteLink.name(u))")
                .accessibilityIdentifier("detail.headerWebsite")
            }
            if let loc = p.location, !loc.isEmpty {
                Label(loc, systemImage: "mappin.and.ellipse")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            if let raw = p.posting?.pay, !raw.isEmpty { payLine(raw, p.payRange) }
            if let line = Self.variantsLine(p.variants ?? []) {
                Text(line)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("detail.variants")
            }
            HStack(spacing: 8) {
                StatusBadge(status: p.status)
                FitBadge(fit: p.fit)
                Spacer()
                if let u = p.postingURL {
                    Link(destination: u) { Label("Open posting", systemImage: "safari") }
                        .font(.subheadline)
                }
            }
            if let r = p.ruling, let d = r.decision {
                Text("Ruled \(d)" + (r.reason.map { " — \($0)" } ?? "") + (r.by.map { " (\($0))" } ?? ""))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }

    /// Pay per hour and per year (SCHEMA.md "Pay"), the converted side marked "≈", with the posting's own words beneath.
    private func payLine(_ raw: String, _ n: PayNormalized?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Label {
                (n?.line ?? Text(raw)).monospacedDigit().fontWeight(n == nil ? .regular : .semibold)
            } icon: {
                Image(systemName: "banknote").foregroundStyle(.secondary)
            }
            .font(.subheadline)
            if n != nil {
                Text("Posting says: \(raw)")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("detail.pay")
    }

    /// "Deadline Nov 8, 2026 — 19 days" or "Posted Sep 22 · rolling", tinted by urgency, with the posting's
    /// own words about timing (`deadline_note`) beneath.
    @ViewBuilder
    private func dateLine(_ d: PostingDates) -> some View {
        if d.detailLine != nil || d.note != nil {
            VStack(alignment: .leading, spacing: 3) {
                if let line = d.detailLine {
                    Label {
                        Text(line)
                            .font(.subheadline.weight(d.deadline != nil ? .semibold : .regular))
                            .foregroundStyle(d.deadline != nil ? .primary : .secondary)
                    } icon: {
                        Image(systemName: d.level.symbol)
                            .foregroundStyle(d.level.tint.map(AnyShapeStyle.init) ?? AnyShapeStyle(.secondary))
                    }
                    .font(.subheadline)
                }
                if let note = d.note {
                    Text(note)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, d.level.tint == nil ? 0 : 10)
            .padding(.vertical, d.level.tint == nil ? 0 : 7)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(d.level.tint.map { AnyShapeStyle($0.opacity(0.14)) } ?? AnyShapeStyle(.clear),
                        in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("detail.dates")
        }
    }

    private func summaryCard(_ s: AISummary) -> some View {
        Card {
            AILabel(text: s.label ?? "AI-generated summary")
                .accessibilityIdentifier("detail.aiLabel.summary")
            if let t = s.text { Text(Self.firstSentences(t, 3)).font(.body).fixedSize(horizontal: false, vertical: true) }
            let reasons = s.fitReasons ?? []
            let fit = s.fit.flatMap(Fit.init(rawValue:))
            if fit != nil || !reasons.isEmpty {
                FlowLayout {
                    FitBadge(fit: fit)
                    ForEach(Array(reasons.enumerated()), id: \.offset) { _, r in Chip(text: r) }
                }
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("detail.fitChips")
            }
            if let f = s.flags, !f.isEmpty {
                FlowLayout {
                    ForEach(Array(f.enumerated()), id: \.offset) { _, flag in
                        Chip(text: flag, tint: .orange, systemImage: Self.isF1(flag) ? "flag.fill" : nil)
                            .accessibilityLabel("Flag: \(flag)")
                    }
                }
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("detail.flagChips")
            }
            if let m = s.model {
                Text("model \(m)" + (s.generatedAt.map { " · \($0)" } ?? ""))
                    .font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }

    private func companyCard(_ c: CompanyInfo, company: String) -> some View {
        Card {
            AILabel(text: c.label ?? "AI-researched")
                .accessibilityIdentifier("detail.aiLabel.company")
            if let s = c.website, let u = URL(string: s), ["http", "https"].contains(u.scheme?.lowercased() ?? "") {
                Link(destination: u) {
                    Label(s, systemImage: "globe")
                        .font(.callout)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .accessibilityLabel("Company website, \(WebsiteLink.name(u))")
                .accessibilityIdentifier("detail.website")
            }
            if let t = c.whatTheyDo { Text(t).font(.callout).fixedSize(horizontal: false, vertical: true) }
            if let t = c.roboticsWork { Text(t).font(.callout).fixedSize(horizontal: false, vertical: true) }
            let facts = [c.hq.map { "HQ \($0)" }, c.size.map { "size \($0)" }, c.ats.map { "ATS \($0)" }].compactMap { $0 }
            let links = sourceLinks(c)
            let hasMore = !facts.isEmpty || !(c.recent ?? []).isEmpty || !(c.people ?? []).isEmpty || !links.isEmpty
            if hasMore {
                DisclosureGroup(isExpanded: $showMoreCompany) {
                    VStack(alignment: .leading, spacing: 10) {
                        if !facts.isEmpty {
                            Text(facts.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                        }
                        if let r = c.recent, !r.isEmpty { SectionHead(text: "Recent"); BulletList(items: r) }
                        if let r = c.people, !r.isEmpty { SectionHead(text: "People"); BulletList(items: r) }
                        if !links.isEmpty { SectionHead(text: "Sources"); LinkList(urls: links) }
                    }
                    .padding(.top, 8)
                } label: {
                    Text("More about \(company)").font(.subheadline.weight(.semibold))
                }
                .accessibilityIdentifier("detail.moreCompany")
            }
        }
    }

    /// The summary is meant to be at most 3 sentences; older records run longer, so cap it here.
    static func firstSentences(_ text: String, _ n: Int) -> String {
        let tokenizer = NLTokenizer(unit: .sentence)
        tokenizer.string = text
        var end = text.endIndex
        var count = 0
        tokenizer.enumerateTokens(in: text.startIndex..<text.endIndex) { range, _ in
            count += 1
            end = range.upperBound
            return count < n
        }
        return count < n ? text : String(text[..<end]).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// "Also posted as: <label>[, <label>]" with each label linking to its posting; nil when there are none.
    static func variantsLine(_ variants: [Variant]) -> AttributedString? {
        let items = variants.compactMap { v in v.displayLabel.map { ($0, v.linkURL) } }
        guard !items.isEmpty else { return nil }
        var out = AttributedString("Also posted as: ")
        for (i, item) in items.enumerated() {
            if i > 0 { out += AttributedString(", ") }
            var label = AttributedString(item.0)
            if let u = item.1 { label[AttributeScopes.FoundationAttributes.LinkAttribute.self] = u }
            out += label
        }
        return out
    }

    static func isF1(_ flag: String) -> Bool {
        flag.hasPrefix("F-1") || flag.hasPrefix("F1")
    }

    private func sourceLinks(_ c: CompanyInfo) -> [String] {
        var links = c.sources ?? []
        if let cu = c.careersUrl, !links.contains(cu) { links.append(cu) }
        return links
    }

    private func postingCard(_ p: Posting) -> some View {
        Card {
            DisclosureGroup(isExpanded: $showPostingText) {
                VStack(alignment: .leading, spacing: 10) {
                    if let b = p.posting {
                        let facts = [b.term.map { "Term \($0)" }, b.duration, b.degree.map { "Degree \($0)" },
                                     b.deadline.map { "Deadline \($0)" } ?? "Deadline not stated", b.team].compactMap { $0 }
                        Text(facts.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                        if let d = b.description {
                            Text(d).font(.callout).fixedSize(horizontal: false, vertical: true)
                        }
                        if let r = b.responsibilities, !r.isEmpty { SectionHead(text: "Responsibilities"); BulletList(items: r) }
                        if let r = b.requirements, !r.isEmpty { SectionHead(text: "Requirements"); BulletList(items: r) }
                        if let r = b.preferred, !r.isEmpty { SectionHead(text: "Preferred"); BulletList(items: r) }
                        SectionHead(text: "Work authorization")
                        if let wa = b.workAuthorization, !wa.isEmpty {
                            Text("“\(wa)”").font(.callout.italic()).fixedSize(horizontal: false, vertical: true)
                        } else {
                            Text("Posting says nothing.").font(.callout).foregroundStyle(.secondary)
                        }
                        if let pay = b.pay { SectionHead(text: "Pay"); Text(pay).font(.callout) }
                        if let f = b.fetchedAt { Text("fetched \(f)").font(.caption2).foregroundStyle(.tertiary) }
                        if let s = b.applyUrl ?? p.url, let u = URL(string: s) {
                            Link(destination: u) { Label("Open the original posting", systemImage: "arrow.up.right.square") }.font(.callout)
                        }
                    } else {
                        Text("Posting text not fetched yet.").font(.callout).foregroundStyle(.secondary)
                    }
                }
                .padding(.top, 8)
            } label: {
                Label("Job description", systemImage: "doc.plaintext")
                    .font(.subheadline.weight(.semibold))
            }
            .accessibilityIdentifier("detail.postingText")
        }
    }

    private func materialsCard(_ p: Posting) -> some View {
        Card {
            Text("Materials").font(.subheadline.weight(.semibold))
            let rows = (p.materials ?? Materials()).rows
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                HStack {
                    Text(row.name).font(.callout)
                    Spacer()
                    if let path = row.path {
                        Button {
                            Task { await open(path, title: row.name) }
                        } label: {
                            if downloading == path { ProgressView() } else { Label("Open", systemImage: "arrow.up.forward.app") }
                        }
                        .font(.callout)
                        .disabled(downloading != nil)
                    } else {
                        Text("not yet generated").font(.callout).foregroundStyle(.tertiary)
                    }
                }
            }
        }
    }

    private func historyCard(_ h: [HistoryItem]) -> some View {
        Card {
            Text("History").font(.subheadline.weight(.semibold))
            ForEach(Array(h.enumerated()), id: \.offset) { _, item in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(item.at ?? "").font(.caption2.monospaced()).foregroundStyle(.tertiary)
                    Text(item.event ?? "").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func pendingCard(_ text: String) -> some View {
        Card { Text(text).font(.callout).foregroundStyle(.secondary) }
    }

    // MARK: ruling bar

    @ViewBuilder
    private func rulingBar(_ p: Posting) -> some View {
        VStack(spacing: 0) {
            Divider()
            HStack(spacing: 12) {
                switch p.status {
                case "new", "enriched", "approved", "skipped":
                    Button(role: .destructive) { sheet = .skip } label: {
                        Label("Skip", systemImage: "xmark").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .disabled(p.status == "skipped")
                    .accessibilityIdentifier("ruling.skip")
                    Button { sheet = .approve } label: {
                        Label("Approve", systemImage: "checkmark").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(p.status == "approved")
                    .accessibilityIdentifier("ruling.approve")
                case "ready":
                    readyBar(p)
                case "tailored":
                    Button { confirmApplied = true } label: {
                        Label("Confirm applied", systemImage: "paperplane").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("ruling.applied")
                    // Attached to the button so the iOS 26 popover anchors to it.
                    .confirmationDialog("Mark as applied?", isPresented: $confirmApplied, titleVisibility: .visible) {
                        Button("Confirm applied") { Task { await store.confirmApplied(p.id) } }
                    } message: {
                        Text("Only do this after the application is actually submitted on the company site.")
                    }
                default:
                    Text("Status: \(p.status)").font(.footnote).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity)
                }
            }
            .controlSize(.large)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
        }
        .background(.bar)
    }

    /// `ready`: approve the submit (gated by serve.py), or confirm an application made outside your agent.
    @ViewBuilder
    private func readyBar(_ p: Posting) -> some View {
        let approval = review?.approval(p) ?? .loading
        VStack(spacing: 8) {
            switch approval {
            case .blocked(let reason):
                Label(reason, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .lineLimit(3)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityIdentifier("ruling.approveSubmit.reason")
            case .approved(let at, _):
                Label("Approved \(ApplicationReviewCard.short(at)): your agent will submit", systemImage: "checkmark.seal.fill")
                    .font(.caption)
                    .foregroundStyle(.green)
                    .frame(maxWidth: .infinity, alignment: .leading)
            case .loading, .allowed:
                EmptyView()
            }
            HStack(spacing: 12) {
                Button { confirmApplied = true } label: {
                    Text("Confirm applied").lineLimit(1).minimumScaleFactor(0.75).frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("ruling.applied")
                .accessibilityHint("For an application you submitted yourself, outside your agent")
                .confirmationDialog("Mark as applied?", isPresented: $confirmApplied, titleVisibility: .visible) {
                    Button("Confirm applied") { Task { await store.confirmApplied(p.id) } }
                } message: {
                    Text("Only for an application you already submitted yourself. Your agent will not press Submit.")
                }
                // The submit is approved only at the end of the Application preview, after every field.
                Button { store.openPreview(p.id) } label: {
                    Group {
                        if case .approved = approval {
                            Label("Submit approved", systemImage: "checkmark.seal")
                        } else {
                            Label("Review and submit", systemImage: "doc.text.magnifyingglass")
                        }
                    }
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("ruling.reviewAndSubmit")
            }
        }
    }

    static func showsReview(_ p: Posting) -> Bool {
        if p.status == "ready" { return true }
        let m = p.materials ?? Materials()
        return p.section == .applied && (m.confirmationPng != nil || m.confirmationTxt != nil || p.submission != nil)
    }

    static func reviewKey(_ p: Posting) -> String {
        [p.status, p.ruling?.submitApprovedAt ?? "", p.materials?.formFillJson ?? "", p.materials?.confirmationTxt ?? ""]
            .joined(separator: "|")
    }

    private func openFullScreen(_ rel: String, title: String) async {
        do { fullScreen = PreviewItem(url: try await store.download(rel), title: title) }
        catch { store.flash(.error, error.localizedDescription) }
    }

    private func open(_ path: String, title: String) async {
        downloading = path
        defer { downloading = nil }
        do { preview = PreviewItem(url: try await store.download(path), title: title) }
        catch { store.flash(.error, error.localizedDescription) }
    }
}

struct RulingReasonSheet: View {
    let kind: DetailView.RulingSheet
    let onSubmit: (String?) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @FocusState private var focused: Bool

    private var isSkip: Bool { kind == .skip }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField(isSkip ? "Why skip? (one line)"
                                     : "Note for the tailoring step (optional)",
                              text: $text, axis: .vertical)
                        .lineLimit(1...3)
                        .focused($focused)
                        .accessibilityIdentifier("ruling.reason")
                } footer: {
                    Text(isSkip ? "The reason is stored in the record so your agent stops re-surfacing similar postings."
                                : "Goes into ruling.reason; the tailoring step reads it.")
                }
            }
            .navigationTitle(isSkip ? "Skip posting"
                                    : "Approve posting")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSkip ? "Skip" : "Approve") {
                        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
                        onSubmit(t.isEmpty ? nil : t)
                        dismiss()
                    }
                    .disabled(isSkip && text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityIdentifier("ruling.submit")
                }
            }
            .onAppear { focused = true }
        }
    }
}
