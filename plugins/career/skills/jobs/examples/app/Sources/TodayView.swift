import SwiftUI

/// The Today tab: the batch the coordinating agent session picked (tools/batch.py, `GET /batch`), about 20
/// postings by urgency and fit. Rows open the Inbox's DetailView, so approve and skip work exactly as there; a
/// ruling clears the row at once (it moves under "Show cleared"). The batch itself never changes a record.
struct TodayView: View {
    @Environment(AppStore.self) private var store
    @State private var path: [String] = []
    @State private var showCleared = false

    var body: some View {
        let items = store.todayItems
        let open = items.filter { !$0.cleared }
        let cleared = items.filter(\.cleared)
        let waiting = store.readyForReview
        NavigationStack(path: $path) {
            List {
                if !waiting.isEmpty { actionsLine(waiting) }
                if !items.isEmpty {
                    Section {
                        progress(cleared: cleared.count, total: items.count)
                    }
                    if open.isEmpty {
                        Section {
                            Label("Batch cleared. Ask your agent for the next batch.", systemImage: "checkmark.circle")
                                .foregroundStyle(.secondary)
                        }
                    } else {
                        Section {
                            ForEach(open) { item in
                                NavigationLink(value: item.id) { TodayRow(item: item).withCopyRef(item.posting.ref) }
                                    .accessibilityIdentifier("today.row.\(item.id)")
                            }
                        } header: {
                            Label("To clear · \(open.count)", systemImage: "list.bullet")
                        }
                    }
                    if !cleared.isEmpty {
                        Section {
                            Button { withAnimation(.snappy) { showCleared.toggle() } } label: {
                                HStack {
                                    Label("\(showCleared ? "Hide" : "Show") cleared · \(cleared.count)", systemImage: "checkmark.circle")
                                        .font(.subheadline.weight(.medium))
                                    Spacer()
                                    Image(systemName: "chevron.right")
                                        .font(.footnote.weight(.semibold))
                                        .rotationEffect(.degrees(showCleared ? 90 : 0))
                                        .foregroundStyle(.secondary)
                                }
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityValue(showCleared ? "expanded" : "collapsed")
                            .accessibilityIdentifier("today.showCleared")
                            if showCleared {
                                ForEach(cleared) { item in
                                    NavigationLink(value: item.id) { TodayRow(item: item).withCopyRef(item.posting.ref) }
                                        .accessibilityIdentifier("today.cleared.\(item.id)")
                                }
                            }
                        } footer: {
                            updatedFooter
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .overlay { emptyState(items.isEmpty && waiting.isEmpty) }
            .chatButton()
            .refreshable { await refresh() }
            .navigationTitle("Today")
            .navigationDestination(for: String.self) { id in DetailView(id: id) }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { store.showPairing = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Pairing")
                }
            }
            .task {
                // DetailView and the ruling read store.postings; load them with the batch the first time.
                if store.postings.isEmpty || store.batch == nil { await refresh() }
            }
        }
    }

    private func refresh() async {
        async let a: Void = store.refresh()
        async let b: Void = store.refreshBatch()
        _ = await (a, b)
    }

    private func progress(cleared: Int, total: Int) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("\(cleared) of \(total) cleared")
                .font(.headline)
                .monospacedDigit()
            ProgressView(value: Double(cleared), total: Double(max(total, 1)))
                .tint(.green)
                .accessibilityHidden(true)
            if let note = store.batch?.note, !note.isEmpty {
                Text(note)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("today.progress")
    }

    @ViewBuilder
    private var updatedFooter: some View {
        if let t = store.lastRefreshed { Text("Updated \(t, style: .time) · pull to refresh") }
    }

    /// Today points at the Questions tab, where Ready to submit, Needs your answer and On hold live.
    private func actionsLine(_ rows: [Posting]) -> some View {
        let count = { (a: String) in rows.filter { $0.reviewAction == a }.count }
        let parts = [(count("submit"), "ready to submit"), (count("answer"), "need your answer"),
                     (count("hold"), "on hold")]
            .filter { $0.0 > 0 }.map { "\($0.0) \($0.1)" }
        return Section {
            Button { store.tab = .questions } label: {
                HStack {
                    Label(parts.joined(separator: " · "), systemImage: "paperplane")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.primary)
                    Spacer(minLength: 8)
                    Text("Questions").font(.subheadline).foregroundStyle(.tint)
                    Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("today.actions")
        }
    }

    @ViewBuilder
    private func emptyState(_ noItems: Bool) -> some View {
        switch store.batchState {
        case .loading where store.batch == nil:
            ProgressView("Loading the batch…")
        case .failed(let msg) where store.batch == nil:
            ContentUnavailableView {
                Label("Can't reach the server", systemImage: "wifi.exclamationmark")
            } description: {
                Text(msg)
            } actions: {
                Button("Retry") { Task { await refresh() } }
                Button("Pairing") { store.showPairing = true }
            }
        case .loaded where noItems:
            ContentUnavailableView("No batch.", systemImage: "checklist",
                                   description: Text("Ask your agent for the next batch."))
        default:
            EmptyView()
        }
    }
}

/// One batch item: the Act-soon row's logo, title and date chip, the company, fit, and the `why` line beneath.
/// A cleared item shows its ruling and is dimmed.
struct TodayRow: View {
    let item: AppStore.TodayItem
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let p = item.posting
        HStack(alignment: .top, spacing: 10) {
            CompanyLogoView(logo: p.logo, company: p.company, size: 28, cornerRadius: 6)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 4) {
                Text(p.title)
                    .font(.subheadline.weight(.medium))
                    .lineLimit(typeSize.isAccessibilitySize ? 4 : 2)
                Text([p.company, p.location].compactMap { $0?.isEmpty == false ? $0 : nil }.joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                FlowLayout(spacing: 6) {
                    if item.cleared { ruling(p) }
                    dateChip(p)
                    if let pay = p.payRange { PayChip(pay: pay) }
                    FitBadge(fit: p.fit).lineLimit(1).fixedSize()
                    if let w = p.website { WebsiteLink(url: w) }
                }
                if let why = item.why {
                    Text(why)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(typeSize.isAccessibilitySize ? 6 : 3)
                        .padding(.leading, 8)
                        .overlay(alignment: .leading) {
                            Rectangle()
                                .fill(item.cleared ? Color(.separator) : Color.accentColor)
                                .frame(width: 2)
                        }
                        .padding(.top, 1)
                        .accessibilityLabel("Why: \(why)")
                }
            }
        }
        .padding(.vertical, 2)
        .opacity(item.cleared ? 0.55 : 1)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private func dateChip(_ p: Posting) -> some View {
            let d = PostingDates(p)
            if d.level.actSoon {
                DateChip(text: d.actSoonChip, level: d.level)
            } else if let chip = d.rowChip {
                DateChip(text: chip, level: d.level)
            }
    }

    @ViewBuilder
    private func ruling(_ p: Posting) -> some View {
        switch p.status {
        case "skipped":
            Chip(text: "Skipped", systemImage: "xmark")
        case "approved", "tailored", "ready":
            Chip(text: "Approved", tint: .green, systemImage: "checkmark")
        case "applied", "interview", "offer":
            Chip(text: "Applied", tint: .green, systemImage: "paperplane")
        default:
            Chip(text: p.status.capitalized)
        }
    }
}
