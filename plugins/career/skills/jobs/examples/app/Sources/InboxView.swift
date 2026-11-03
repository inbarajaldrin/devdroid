import SwiftUI

struct InboxView: View {
    @Environment(AppStore.self) private var store
    @State private var query = ""
    @State private var path: [String] = []
    /// Groups opened this session, keyed `<section title>|<company>`. Every group starts collapsed on launch
    /// and nothing persists across launches.
    @State private var opened: Set<String> = []
    /// While searching every matching group starts open; taps then toggle this throwaway set instead.
    @State private var searchCollapsed: Set<String> = []
    /// Earlier builds saved open/closed groups here; cleared so a stale value never lingers.
    private static let legacyCollapsedKey = "inbox.collapsedGroups"

    private var searching: Bool { !query.trimmingCharacters(in: .whitespaces).isEmpty }

    /// Folded variant records (`status: variant`) never appear in the inbox or its counts.
    private var visible: [Posting] { store.postings.filter { !$0.isVariant } }

    private var filtered: [Posting] {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return visible }
        return visible.filter {
            $0.company.localizedCaseInsensitiveContains(q) || $0.title.localizedCaseInsensitiveContains(q)
        }
    }

    private var jobs: [Posting] { filtered }
    private var modeRows: [Posting] { jobs }

    private var actSoon: [(Posting, PostingDates)] {
        jobs.map { ($0, PostingDates($0)) }
            .filter { $0.1.level.actSoon }
            .sorted { a, b in
                switch (a.1.actBy, b.1.actBy) {
                case let (x?, y?) where x != y: return x < y
                case (.some, nil): return true
                case (nil, .some): return false
                default: break
                }
                let da = a.1.daysLeft ?? .max, db = b.1.daysLeft ?? .max
                if da != db { return da < db }
                return a.0.company.localizedStandardCompare(b.0.company) == .orderedAscending
            }
    }

    private var sections: [(InboxSection, [Posting])] {
        InboxSection.allCases.compactMap { s in
            let rows = jobs.filter { $0.section == s }
            return rows.isEmpty ? nil : (s, rows)
        }
    }

    var body: some View {
        @Bindable var store = store
        NavigationStack(path: $path) {
            List {
                jobsContent
            }
            .listStyle(.insetGrouped)
            .overlay { emptyState }
            .chatButton()
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always),
                        prompt: "Company or title")
            .onChange(of: query) { searchCollapsed = [] }
            .refreshable { await store.refresh() }
            .navigationTitle("Listings")
            .navigationDestination(for: String.self) { id in DetailView(id: id) }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { store.showPairing = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Pairing")
                        .accessibilityIdentifier("inbox.pairing")
                }
            }
            .task {
                UserDefaults.standard.removeObject(forKey: Self.legacyCollapsedKey)
                if store.postings.isEmpty { await store.refresh() }
            }
        }
    }

    // MARK: jobs

    @ViewBuilder
    private var jobsContent: some View {
        let urgent = actSoon
        if !urgent.isEmpty {
            Section {
                ForEach(urgent, id: \.0.id) { p, dates in
                    NavigationLink(value: p.id) { ActSoonRow(posting: p, dates: dates).withCopyRef(p.ref) }
                        .accessibilityIdentifier("inbox.actSoon.\(p.id)")
                }
            } header: {
                Label("Act soon · \(urgent.count)", systemImage: "alarm")
            }
        }
        ForEach(sections, id: \.0) { section, rows in
            Section {
                let groups = CompanyGroup.make(rows)
                ForEach(Array(groups.enumerated()), id: \.element.id) { index, group in
                    let open = isExpanded(section, group)
                    CompanyGroupHeader(group: group, expanded: open) { toggle(section, group, open) }
                        .listRowBackground(CompanyHeaderBand(showsTopRule: index > 0))
                        .listRowSeparator(.hidden)
                    if open {
                        ForEach(group.rows) { p in
                            NavigationLink(value: p.id) {
                                PostingRow(posting: p)
                                    .withCopyRef(p.ref)
                                    .padding(.leading, CompanyGroupHeader.rowIndent)
                            }
                            .listRowSeparator(.hidden)
                            .accessibilityIdentifier("inbox.row.\(p.id)")
                        }
                    }
                }
            } header: {
                Text("\(section.title) · \(rows.count)")
            } footer: {
                if section == sections.last?.0 { updatedFooter }
            }
        }
    }

    @ViewBuilder
    private var updatedFooter: some View {
        if let t = store.lastRefreshed { Text("Updated \(t, style: .time) · pull to refresh") }
    }

    private func foldHeader(_ title: String, symbol: String, open: Bool, toggle: @escaping () -> Void) -> some View {
        Button { withAnimation(.snappy) { toggle() } } label: {
            HStack {
                Label(title, systemImage: symbol).font(.subheadline.weight(.medium))
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .rotationEffect(.degrees(open ? 90 : 0))
                    .foregroundStyle(.secondary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityValue(open ? "expanded" : "collapsed")
    }

    private func groupKey(_ section: InboxSection, _ group: CompanyGroup) -> String {
        "\(section.title)|\(group.company)"
    }

    private func isExpanded(_ section: InboxSection, _ group: CompanyGroup) -> Bool {
        let key = groupKey(section, group)
        return searching ? !searchCollapsed.contains(key) : opened.contains(key)
    }

    private func toggle(_ section: InboxSection, _ group: CompanyGroup, _ isOpen: Bool) {
        let key = groupKey(section, group)
        withAnimation(.snappy) {
            if searching {
                if isOpen { searchCollapsed.insert(key) } else { searchCollapsed.remove(key) }
            } else {
                if isOpen { opened.remove(key) } else { opened.insert(key) }
            }
        }
    }

    @ViewBuilder
    private var emptyState: some View {
        switch store.loadState {
        case .loading where store.postings.isEmpty:
            ProgressView("Loading postings…")
        case .failed(let msg) where store.postings.isEmpty:
            ContentUnavailableView {
                Label("Can't reach the server", systemImage: "wifi.exclamationmark")
            } description: {
                Text(msg)
            } actions: {
                Button("Retry") { Task { await store.refresh() } }
                Button("Pairing") { store.showPairing = true }
            }
        case .loaded where modeRows.isEmpty && !searching:
            ContentUnavailableView("No postings yet",
                                   systemImage: "tray",
                                   description: Text("Your agent has not found anything to review."))
        case .loaded where modeRows.isEmpty:
            ContentUnavailableView.search(text: query)
        default:
            EmptyView()
        }
    }
}

struct PostingRow: View {
    let posting: Posting
    @ScaledMetric(relativeTo: .body) private var thumbSize: CGFloat = 56
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // At accessibility sizes the thumbnail sits above the text so the title keeps the full row width.
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
        layout {
            if let first = posting.images.first {
                RemoteImage(rel: first.filesPath, maxPixel: 240)
                    .frame(width: min(thumbSize, 84), height: min(thumbSize, 84))
                    .background(Color(.tertiarySystemFill))
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .padding(.top, 2)
                    .accessibilityHidden(true)
            }
            text
        }
    }

    private var text: some View {
        let dates = PostingDates(posting)
        let chip = dates.rowChip
        return VStack(alignment: .leading, spacing: 4) {
            Text(posting.title)
                .font(.body)
                .lineLimit(typeSize.isAccessibilitySize ? 4 : 2)
            if let loc = posting.location, !loc.isEmpty {
                Text(loc)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            // Badges never compress; when the row is too narrow (accessibility sizes) they wrap as whole chips.
            FlowLayout(spacing: 6) {
                if let chip { DateChip(text: chip, level: dates.level) }
                if let pay = posting.payRange { PayChip(pay: pay) }
                FitBadge(fit: posting.fit)
                    .lineLimit(1)
                    .fixedSize()
                if posting.flagCount > 0 {
                    HStack(spacing: 2) {
                        Image(systemName: "flag").imageScale(.small)
                        Text("\(posting.flagCount)").monospacedDigit()
                    }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .fixedSize()
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("\(posting.flagCount) flags")
                }
                if let w = posting.website { WebsiteLink(url: w) }
            }
        }
        .padding(.vertical, 2)
    }
}

/// One company's postings inside an inbox section.
struct CompanyGroup: Identifiable {
    let company: String
    let rows: [Posting]

    var id: String { company }
    var logo: CompanyLogo? { rows.lazy.compactMap(\.logo).first }
    var directCount: Int { rows.filter { $0.fit == .direct }.count }

    /// Companies by direct-fit count (desc), then name; postings by fit (direct, adjacent, off-target, none), then title.
    static func make(_ postings: [Posting]) -> [CompanyGroup] {
        Dictionary(grouping: postings, by: \.company)
            .map { company, rows in
                CompanyGroup(company: company, rows: rows.sorted { a, b in
                    let ra = a.fit?.rank ?? 3, rb = b.fit?.rank ?? 3
                    if ra != rb { return ra < rb }
                    return a.title.localizedStandardCompare(b.title) == .orderedAscending
                })
            }
            .sorted { a, b in
                let da = a.directCount, db = b.directCount
                if da != db { return da > db }
                return a.company.localizedStandardCompare(b.company) == .orderedAscending
            }
    }
}

/// Tappable header row for a company group: logo, name, count, disclosure chevron.
struct CompanyGroupHeader: View {
    let group: CompanyGroup
    let expanded: Bool
    let toggle: () -> Void

    static let logoSize: CGFloat = 40
    static let spacing: CGFloat = 12
    /// Leading inset of the posting rows beneath a header, so they visibly belong to it.
    static let rowIndent: CGFloat = 24

    var body: some View {
        Button(action: toggle) {
            HStack(spacing: Self.spacing) {
                CompanyLogoView(logo: group.logo, company: group.company, size: Self.logoSize)
                Text(group.company)
                    .font(.headline.bold())
                    .foregroundStyle(.primary)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 8)
                Text("\(group.rows.count)")
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 2)
                    .background(Color(.tertiarySystemFill), in: Capsule())
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.tertiary)
                    .rotationEffect(.degrees(expanded ? 90 : 0))
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)   // the default list button tints the whole label with the accent colour
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(group.company), \(group.rows.count) \(group.rows.count == 1 ? "posting" : "postings")")
        .accessibilityValue(expanded ? "Expanded" : "Collapsed")
        .accessibilityHint(expanded ? "Hides this company's postings" : "Shows this company's postings")
        .accessibilityAddTraits(.isButton)
        .accessibilityIdentifier("inbox.group.\(group.company)")
    }
}

/// Background for a company header row: a band one step darker than the posting rows' card, with a hairline
/// on top that divides it from the company above. Posting rows have no separators of their own.
struct CompanyHeaderBand: View {
    let showsTopRule: Bool
    @Environment(\.displayScale) private var displayScale

    var body: some View {
        ZStack(alignment: .top) {
            Color(.secondarySystemGroupedBackground)
            Color(.tertiarySystemFill)
            if showsTopRule {
                Rectangle()
                    .fill(Color(.separator))
                    .frame(height: 1 / displayScale)
            }
        }
    }
}

/// Row in the "Act soon" section: small logo, title, date chip, company.
struct ActSoonRow: View {
    let posting: Posting
    let dates: PostingDates
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            CompanyLogoView(logo: posting.logo, company: posting.company, size: 28, cornerRadius: 6)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 4) {
                Text(posting.title)
                    .font(.subheadline.weight(.medium))
                    .lineLimit(typeSize.isAccessibilitySize ? 4 : 2)
                // A flow, not a row: with the website label a single row truncated the company to "NVI...".
                let layout = typeSize.isAccessibilitySize
                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
                    : AnyLayout(FlowLayout(spacing: 8))
                layout {
                    DateChip(text: dates.actSoonChip, level: dates.level)
                    if let pay = posting.payRange { PayChip(pay: pay) }
                    Text(posting.company)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    if let w = posting.website { WebsiteLink(url: w) }
                }
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}
