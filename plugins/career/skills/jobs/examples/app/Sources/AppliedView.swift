import SwiftUI

/// The Applied tab: every application from the submit onward (serve.py `application` on each record from `applied`
/// on), so it never empties. Active (submitted, under review, interview, offer) or closed (rejected, closed), most
/// recent activity first. Rows open the Inbox's DetailView, which starts with the application's timeline.
struct AppliedView: View {
    @Environment(AppStore.self) private var store
    @State private var path: [String] = []
    @AppStorage("applied.filter") private var filter: Filter = .active

    enum Filter: String, CaseIterable, Identifiable {
        case active, closed
        var id: String { rawValue }
        var title: String { self == .active ? "Active" : "Closed" }
    }

    var body: some View {
        let all = store.applications
        let rows = all.filter { ($0.application?.active ?? false) == (filter == .active) }
        NavigationStack(path: $path) {
            List {
                if !all.isEmpty {
                    Section {
                        Picker("Show", selection: $filter) {
                            ForEach(Filter.allCases) { f in
                                Text("\(f.title) · \(all.filter { ($0.application?.active ?? false) == (f == .active) }.count)").tag(f)
                            }
                        }
                        .pickerStyle(.segmented)
                        .accessibilityIdentifier("applied.filter")
                    }
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 0, trailing: 0))
                    Section {
                        ForEach(rows) { p in
                            HStack(spacing: 8) {
                                AppliedRow(posting: p)
                                CopyRefButton(text: p.ref)
                                Button { store.openPreview(p.id) } label: {
                                    Text("What was sent").font(.footnote.weight(.semibold))
                                }
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                                .accessibilityIdentifier("applied.sent.\(p.id)")
                            }
                            // The row opens the detail; the styled button keeps its own tap.
                            .background(NavigationLink(value: p.id) { EmptyView() }.opacity(0))
                            .accessibilityIdentifier("applied.row.\(p.id)")
                        }
                    } footer: {
                        if rows.isEmpty {
                            Text(filter == .active ? "No active applications." : "No closed applications.")
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .overlay { emptyState(all.isEmpty) }
            .chatButton()
            .refreshable { await store.refresh() }
            .navigationTitle("Applied")
            .navigationDestination(for: String.self) { id in DetailView(id: id) }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { store.showPairing = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Pairing")
                }
            }
            .task { if store.postings.isEmpty { await store.refresh() } }
        }
    }

    @ViewBuilder
    private func emptyState(_ none: Bool) -> some View {
        switch store.loadState {
        case .loading where store.postings.isEmpty:
            ProgressView("Loading applications…")
        case .failed(let msg) where store.postings.isEmpty:
            ContentUnavailableView {
                Label("Can't reach the server", systemImage: "wifi.exclamationmark")
            } description: {
                Text(msg)
            } actions: {
                Button("Retry") { Task { await store.refresh() } }
            }
        case .loaded where none:
            ContentUnavailableView("Nothing submitted yet.", systemImage: "paperplane",
                                   description: Text("Applications appear here after you approve the submit."))
        default:
            EmptyView()
        }
    }
}

extension ApplicationState {
    /// Chip colour by state: submitted neutral, under review orange, interview blue, offer green, rejected red.
    var tint: Color? {
        switch state {
        case "under_review": .orange
        case "submitting", "interview": .blue
        case "offer": .green
        case "rejected": .red
        default: nil
        }
    }

    var symbol: String {
        switch state {
        case "under_review": "clock.fill"
        case "interview": "person.2.fill"
        case "offer": "checkmark.seal.fill"
        case "rejected", "closed": "xmark.circle.fill"
        default: "paperplane.fill"
        }
    }
}

struct StateChip: View {
    let application: ApplicationState

    var body: some View {
        Chip(text: application.label, tint: application.tint, systemImage: application.symbol)
            .fontWeight(.semibold)
    }
}

struct AppliedRow: View {
    let posting: Posting
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        if let a = posting.application {
            HStack(alignment: .top, spacing: 10) {
                CompanyLogoView(logo: posting.logo, company: posting.company, size: 28, cornerRadius: 6)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 4) {
                    Text(posting.title)
                        .font(.subheadline.weight(.medium))
                        .lineLimit(typeSize.isAccessibilitySize ? 4 : 2)
                    Text(subline(a))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                    let layout = typeSize.isAccessibilitySize
                        ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
                        : AnyLayout(FlowLayout(spacing: 8))
                    layout {
                        StateChip(application: a)
                        if let e = a.lastEvent {
                            Text([e.text, e.date.map { DayMath.short($0) }].compactMap { $0 }.joined(separator: ", "))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }
                }
            }
            .padding(.vertical, 2)
            .accessibilityElement(children: .combine)
        }
    }

    private func subline(_ a: ApplicationState) -> String {
        var parts = [posting.company]
        if let d = a.submittedDate { parts.append("submitted \(DayMath.short(d))") }
        if let n = a.daysSinceSubmitted { parts.append(n == 0 ? "today" : "\(n) d ago") }
        return parts.joined(separator: " · ")
    }
}

/// Top of the posting detail for an application: its state and every event, newest first.
struct ApplicationTimelineCard: View {
    @Environment(AppStore.self) private var store
    let application: ApplicationState
    var postingId: String?

    var body: some View {
        let a = application
        Card {
            HStack {
                Text("Application").font(.subheadline.weight(.semibold))
                Spacer()
                StateChip(application: a)
            }
            if let pid = postingId {
                Button { store.openPreview(pid) } label: { Label("What was sent", systemImage: "doc.text") }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }
            if a.state == "submitting" {
                Text("Submit approved. Your agent is submitting it.").font(.caption).foregroundStyle(.secondary)
            } else if let d = a.submittedDate {
                Text("Submitted \(QuestionDetailView.turnTime(d))" + (a.daysSinceSubmitted.map { " · \($0) d ago" } ?? ""))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            let events = a.timeline ?? []
            ForEach(Array(events.enumerated()), id: \.offset) { i, e in
                HStack(alignment: .top, spacing: 10) {
                    Circle()
                        .fill(i == 0 ? Color.accentColor : (e.isEmail ? Color.orange : Color(.tertiaryLabel)))
                        .frame(width: 9, height: 9)
                        .padding(.top, 5)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(e.text).font(.callout)
                        if let s = e.subject, !s.isEmpty {
                            Text(s).font(.caption).foregroundStyle(.secondary)
                        }
                        if let d = e.date {
                            Text(QuestionDetailView.turnTime(d)).font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                }
                .accessibilityElement(children: .combine)
            }
        }
        .accessibilityIdentifier("detail.applicationTimeline")
    }
}
