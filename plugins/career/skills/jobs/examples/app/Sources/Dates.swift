import SwiftUI

/// Calendar-day math for the records' ISO dates (`YYYY-MM-DD`), read as local days.
/// Tolerates trailing prose ("2026-10-16 (stated minimum; req still live)") by reading the first ten characters.
enum DayMath {
    static func date(_ s: String?) -> Date? {
        guard let s = s?.trimmingCharacters(in: .whitespaces), s.count >= 10 else { return nil }
        let parts = s.prefix(10).split(separator: "-")
        guard parts.count == 3, let y = Int(parts[0]), let m = Int(parts[1]), let d = Int(parts[2]),
              (1...12).contains(m), (1...31).contains(d) else { return nil }
        return Calendar.current.date(from: DateComponents(year: y, month: m, day: d))
    }

    /// Whole days from today to `d`; negative once `d` has passed.
    static func daysUntil(_ d: Date, now: Date = .now) -> Int {
        let cal = Calendar.current
        return cal.dateComponents([.day], from: cal.startOfDay(for: now), to: cal.startOfDay(for: d)).day ?? 0
    }

    /// "Nov 8", or "Jul 16, 2027" outside the current year.
    static func short(_ d: Date, now: Date = .now) -> String {
        let sameYear = Calendar.current.isDate(d, equalTo: now, toGranularity: .year)
        return sameYear ? d.formatted(.dateTime.month(.abbreviated).day())
                        : d.formatted(.dateTime.month(.abbreviated).day().year())
    }

    /// "Nov 8, 2026"
    static func long(_ d: Date) -> String { d.formatted(.dateTime.month(.abbreviated).day().year()) }

    /// For VoiceOver: "November 8".
    static func spoken(_ d: Date) -> String { d.formatted(.dateTime.month(.wide).day()) }
}

/// Text for a date chip: `full` at normal sizes, `short` at accessibility sizes so it never wraps.
struct ChipText {
    var full: String
    var short: String
    var spoken: String
}

/// Everything the inbox and detail screen show about a posting's dates, derived once.
struct PostingDates {
    let level: UrgencyLevel
    let deadline: Date?
    let posted: Date?
    /// The date urgency is measured against: `urgency.by`, else the deadline.
    let actBy: Date?
    /// Days to `actBy`, recomputed against today when the date parses, else the record's `days_left`.
    let daysLeft: Int?
    let rawDeadline: String?
    let note: String?

    init(_ p: Posting, now: Date = .now) {
        level = p.urgency?.kind ?? .none
        rawDeadline = p.posting?.deadline.flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 }
        deadline = DayMath.date(rawDeadline)
        posted = DayMath.date(p.posting?.postedOn)
        actBy = DayMath.date(p.urgency?.by) ?? deadline
        daysLeft = actBy.map { DayMath.daysUntil($0, now: now) } ?? p.urgency?.daysLeft
        note = p.posting?.deadlineNote.flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 }
    }

    private static func daysAgo(_ d: Date, now: Date = .now) -> Int { max(0, -DayMath.daysUntil(d, now: now)) }

    /// Every inbox row: "by Nov 8", else "posted 28 d ago", else nothing.
    var rowChip: ChipText? {
        if let d = deadline {
            return ChipText(full: "by \(DayMath.short(d))", short: DayMath.short(d), spoken: "Deadline \(DayMath.spoken(d))")
        }
        if let d = posted {
            let n = Self.daysAgo(d)
            if n == 0 { return ChipText(full: "posted today", short: "today", spoken: "Posted today") }
            return ChipText(full: "posted \(n) d ago", short: "\(n) d ago", spoken: "Posted \(n) \(n == 1 ? "day" : "days") ago")
        }
        return nil
    }

    /// "Act soon" rows: "Oct 16 · passed, still open", "Nov 8 · 19 d".
    var actSoonChip: ChipText {
        let dateText = actBy.map { DayMath.short($0) }
        let spokenDate = actBy.map { DayMath.spoken($0) }
        guard let n = daysLeft else {
            let fallback = level == .now ? "act now" : "this week"
            return ChipText(full: dateText ?? fallback, short: dateText ?? fallback, spoken: spokenDate ?? fallback)
        }
        let (tail, shortTail, spokenTail): (String, String, String) =
            n < 0 ? ("passed, still open", "passed", "date passed, still open")
            : n == 0 ? ("today", "today", "today")
            : ("\(n) d", "\(n) d", "\(n) \(n == 1 ? "day" : "days") left")
        guard let dateText, let spokenDate else { return ChipText(full: tail, short: shortTail, spoken: spokenTail) }
        return ChipText(full: "\(dateText) · \(tail)", short: shortTail, spoken: "\(spokenDate), \(spokenTail)")
    }

    /// Detail screen: "Deadline Nov 8, 2026 — 19 days" or "Posted Sep 22 · rolling".
    var detailLine: String? {
        if let d = deadline {
            let n = DayMath.daysUntil(d)
            let tail = n < 0 ? (level == .now ? "passed, still open" : "passed")
                : n == 0 ? "today" : "\(n) \(n == 1 ? "day" : "days")"
            return "Deadline \(DayMath.long(d)) — \(tail)"
        }
        if let raw = rawDeadline { return "Deadline \(raw)" }
        if let d = posted { return "Posted \(DayMath.short(d)) · rolling" }
        return nil
    }
}

extension UrgencyLevel {
    /// Chip tint: now red, week orange; soon and below stay neutral.
    var tint: Color? {
        switch self {
        case .now: .red
        case .week: .orange
        case .soon, .rolling, .none: nil
        }
    }

    var symbol: String {
        switch self {
        case .now: "exclamationmark.circle.fill"
        case .week: "clock.fill"
        case .soon, .rolling, .none: "calendar"
        }
    }
}

/// Capsule date chip. Text stays primary or secondary for contrast; the tint goes on the fill and the glyph.
/// Never wraps: at accessibility sizes it switches to the short form.
struct DateChip: View {
    let text: ChipText
    let level: UrgencyLevel
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        HStack(spacing: 3) {
            Image(systemName: level.symbol)
                .imageScale(.small)
                .foregroundStyle(level.tint.map(AnyShapeStyle.init) ?? AnyShapeStyle(.secondary))
            Text(typeSize.isAccessibilitySize ? text.short : text.full)
                .foregroundStyle(textStyle)
                .monospacedDigit()
        }
        .font(.caption.weight(level == .rolling || level == .none ? .regular : .semibold))
        .lineLimit(1)
        .fixedSize()
        .padding(.horizontal, 7)
        .padding(.vertical, 2)
        .background(fill, in: Capsule())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text.spoken)
    }

    private var textStyle: AnyShapeStyle {
        switch level {
        case .now, .week, .soon: AnyShapeStyle(.primary)
        case .rolling, .none: AnyShapeStyle(.secondary)
        }
    }

    private var fill: AnyShapeStyle {
        if let tint = level.tint { return AnyShapeStyle(tint.opacity(0.2)) }
        return AnyShapeStyle(Color(.tertiarySystemFill))
    }
}
