import Foundation

// Mirrors the server posting responses. Everything the phone does not strictly need is optional so a
// half-filled record (status `new`, no summary yet) still decodes. Decoded with .convertFromSnakeCase.

struct Posting: Codable, Identifiable, Hashable {
    let id: String
    var company: String
    var title: String
    var url: String?
    var location: String?
    var source: String?
    var firstSeen: String?
    var status: String
    var posting: PostingBody?
    var summaryAi: AISummary?
    var companyInfo: CompanyInfo?
    var materials: Materials?
    var ruling: Ruling?
    var history: [HistoryItem]?
    /// Primary only: the same role posted other ways (other degree track, term or site).
    var variants: [Variant]?
    /// Folded record only (`status: variant`): id of the primary it belongs to.
    var variantOf: String?
    /// Computed by the enricher from the posting's dates (SCHEMA.md "Dates and urgency"). Absent until then.
    var urgency: Urgency?
    /// Written only by `career.py submit` (SCHEMA.md `submission`). Absent until then.
    var submission: Submission?
    var kind: String?
    /// serve.py adds this to a `ready` record that has a review.json (review.py summary() + submit_approved).
    var reviewSummary: ReviewSummary?
    /// serve.py adds this to a record from `applied` on: what the Applied tab shows (state, last event, timeline).
    var application: ApplicationState?


    /// A filled posting's next step for the user (serve.py `review_summary.action`); nil when not filled.
    var reviewAction: String? { status == "ready" ? reviewSummary?.action : nil }
    /// Listed at the top of the Questions tab: Ready to submit, Needs your answer or On hold.
    var needsReview: Bool { ["submit", "answer", "hold"].contains(reviewAction ?? "") }
    /// Waits on the user: Ready to submit or Needs your answer.
    var waitsOnUser: Bool { reviewAction == "submit" || reviewAction == "answer" }

    /// Folded into another record; never shown in the inbox or counted.
    var isVariant: Bool { status == "variant" }

    var postingURL: URL? { url.flatMap(URL.init(string:)) }
    var fit: Fit? { summaryAi?.fit.flatMap(Fit.init(rawValue:)) }
    var flagCount: Int { summaryAi?.flags?.count ?? 0 }
    var payRange: PayNormalized? { posting?.payNormalized }
    var images: [CompanyImage] { companyInfo?.images ?? [] }
    var logo: CompanyLogo? { companyInfo?.logo.flatMap { $0.filesPath == nil ? nil : $0 } }
    /// `company_info.website`, the company's (or organizer's) homepage; only http(s) URLs.
    var website: URL? {
        guard let s = companyInfo?.website, let u = URL(string: s), ["http", "https"].contains(u.scheme?.lowercased() ?? "") else { return nil }
        return u
    }
    var section: InboxSection { InboxSection(status: status) }
}

/// One entry of a primary record's `variants`. Every field optional; a malformed entry decodes empty.
struct Variant: Codable, Hashable {
    var id: String?
    var label: String?
    var url: String?
    var note: String?

    var displayLabel: String? {
        [label, id].compactMap { $0?.trimmingCharacters(in: .whitespaces) }.first { !$0.isEmpty }
    }
    var linkURL: URL? { url.flatMap(URL.init(string:)) }

    private enum CodingKeys: String, CodingKey { case id, label, url, note }

    init(from decoder: Decoder) throws {
        let c = try? decoder.container(keyedBy: CodingKeys.self)
        id = (try? c?.decodeIfPresent(String.self, forKey: .id)) ?? nil
        label = (try? c?.decodeIfPresent(String.self, forKey: .label)) ?? nil
        url = (try? c?.decodeIfPresent(String.self, forKey: .url)) ?? nil
        note = (try? c?.decodeIfPresent(String.self, forKey: .note)) ?? nil
    }
}

struct PostingBody: Codable, Hashable {
    var fetchedAt: String?
    var term: String?
    var duration: String?
    /// ISO `YYYY-MM-DD`; older records may carry trailing prose, so read with `DayMath.date`.
    var postedOn: String?
    var deadline: String?
    /// The posting's own words about timing when `deadline` is null or soft.
    var deadlineNote: String?
    var degree: String?
    var description: String?
    var responsibilities: [String]?
    var requirements: [String]?
    var preferred: [String]?
    var workAuthorization: String?
    var pay: String?
    /// Parsed from `pay` by tools/pay.py (SCHEMA.md "Pay"); nil when the text has no USD figure.
    var payNormalized: PayNormalized?
    var team: String?
    /// The employer's own page when supplied separately; otherwise the same as `url`.
    var applyUrl: String?
}

/// `posting.pay_normalized` (SCHEMA.md "Pay"): the posting's pay as USD per hour and per year, 40 h/week.
/// `unit` is how the posting states it (hour, week, month, year); the other side is converted.
struct PayNormalized: Codable, Hashable {
    var hourly: [Double]?
    var yearly: [Double]?
    var unit: String?
    var currency: String?
    var basis: String?

    var hourlyConverted: Bool { unit != "hour" }
    var yearlyConverted: Bool { unit != "year" }
    /// "$40-50/hr"
    var hourlyText: String? { Self.span(hourly) { "$\(Int($0.rounded()))" }.map { $0 + "/hr" } }
    /// "$83K-104K/yr"
    var yearlyText: String? { Self.span(yearly) { "$\(Int(($0 / 1000).rounded()))K" }.map { $0 + "/yr" } }

    var spoken: String {
        func say(_ r: [Double]?, _ scale: Double, _ suffix: String) -> String {
            guard let r, let a = r.first, let b = r.last else { return "" }
            let x = Int((a / scale).rounded()), y = Int((b / scale).rounded())
            return (x == y ? "\(x)" : "\(x) to \(y)") + suffix
        }
        return "Pay: " + (hourlyConverted ? "about " : "") + say(hourly, 1, " dollars an hour, ")
            + (yearlyConverted ? "about " : "") + say(yearly, 1000, " thousand a year")
    }

    private static func span(_ r: [Double]?, _ f: (Double) -> String) -> String? {
        guard let r, let a = r.first, let b = r.last else { return nil }
        let x = f(a), y = f(b)
        return x == y ? x : x + "-" + y.dropFirst()
    }
}

struct AISummary: Codable, Hashable {
    var label: String?
    var text: String?
    var fit: String?
    var fitReasons: [String]?
    var flags: [String]?
    var model: String?
    var generatedAt: String?
}

struct CompanyInfo: Codable, Hashable {
    var label: String?
    var whatTheyDo: String?
    var roboticsWork: String?
    var hq: String?
    var size: String?
    var ats: String?
    var careersUrl: String?
    /// The company's official homepage (SCHEMA.md `company_info.website`), or nil.
    var website: String?
    var recent: [String]?
    var people: [String]?
    var sources: [String]?
    var researchedAt: String?
    var logo: CompanyLogo?
    var images: [CompanyImage]?
}

/// `company_info.logo`: one mark per company, shared by all its records. Every field is optional and a
/// malformed value decodes as an empty logo, so a bad logo never costs the phone the whole record.
struct CompanyLogo: Codable, Hashable {
    /// Relative to the workspace, e.g. `media/example/logo.png`.
    var file: String?
    var sourceUrl: String?
    var background: String?

    enum Background { case light, dark, any }

    /// Which tile the mark needs: `dark` = the mark reads on dark, so it sits on a dark tile.
    var tile: Background {
        switch background?.lowercased() {
        case "light": .light
        case "dark": .dark
        default: .any
        }
    }

    /// Path under serve.py's `/files` root (career/), or nil when there is no file.
    var filesPath: String? {
        guard let f = file?.trimmingCharacters(in: .whitespaces), !f.isEmpty else { return nil }
        return "data/postings/" + f
    }

    private enum CodingKeys: String, CodingKey { case file, sourceUrl, background }

    init(file: String? = nil, sourceUrl: String? = nil, background: String? = nil) {
        self.file = file
        self.sourceUrl = sourceUrl
        self.background = background
    }

    init(from decoder: Decoder) throws {
        let c = try? decoder.container(keyedBy: CodingKeys.self)
        file = (try? c?.decodeIfPresent(String.self, forKey: .file)) ?? nil
        sourceUrl = (try? c?.decodeIfPresent(String.self, forKey: .sourceUrl)) ?? nil
        background = (try? c?.decodeIfPresent(String.self, forKey: .background)) ?? nil
    }
}

struct CompanyImage: Codable, Hashable, Identifiable {
    /// Relative to the workspace, e.g. `media/example/photo.jpg`.
    var file: String
    var caption: String?
    var sourceUrl: String?
    var source: String?
    var relevance: String?

    var id: String { file }
    /// Path under serve.py's `/files` root (career/).
    var filesPath: String { "data/postings/" + file }
    var sourceURL: URL? { sourceUrl.flatMap(URL.init(string:)) }
}

/// `urgency`: how soon to act on a posting. Every field optional and a malformed value decodes empty,
/// so a bad block never costs the phone the whole record.
struct Urgency: Codable, Hashable {
    /// `now | week | soon | rolling | none`
    var level: String?
    /// ISO date the level is measured against (deadline, stated date or mirror expiry).
    var by: String?
    var daysLeft: Int?
    var reason: String?
    var computedAt: String?

    var kind: UrgencyLevel { level.flatMap { UrgencyLevel(rawValue: $0.lowercased()) } ?? .none }

    private enum CodingKeys: String, CodingKey { case level, by, daysLeft, reason, computedAt }

    init(level: String? = nil, by: String? = nil, daysLeft: Int? = nil, reason: String? = nil, computedAt: String? = nil) {
        self.level = level
        self.by = by
        self.daysLeft = daysLeft
        self.reason = reason
        self.computedAt = computedAt
    }

    init(from decoder: Decoder) throws {
        let c = try? decoder.container(keyedBy: CodingKeys.self)
        level = (try? c?.decodeIfPresent(String.self, forKey: .level)) ?? nil
        by = (try? c?.decodeIfPresent(String.self, forKey: .by)) ?? nil
        daysLeft = ((try? c?.decodeIfPresent(Int.self, forKey: .daysLeft)) ?? nil)
            ?? ((try? c?.decodeIfPresent(Double.self, forKey: .daysLeft)) ?? nil).map { Int($0.rounded()) }
        reason = (try? c?.decodeIfPresent(String.self, forKey: .reason)) ?? nil
        computedAt = (try? c?.decodeIfPresent(String.self, forKey: .computedAt)) ?? nil
    }
}

enum UrgencyLevel: String {
    case now, week, soon, rolling, none

    /// Levels listed in the inbox's "Act soon" section.
    var actSoon: Bool { self == .now || self == .week }
}

struct Materials: Codable, Hashable {
    var resumePdf: String?
    var figurePng: String?
    var clipMp4: String?
    var coverLetterMd: String?
    var formAnswersMd: String?
    // Apply stage (career.py). Paths relative to the workspace, served at `/files/<path>`.
    var applicationDir: String?
    var formScreenshot: String?
    var formFillJson: String?
    var formQuestionsMd: String?
    var confirmationPng: String?
    var confirmationTxt: String?
    // Workday (apply_workday.py): one screenshot per wizard step, the last being the Review page, and its text.
    var workdaySteps: [String]?
    var reviewTxt: String?

    /// Display order for the materials card. `path` is nil when not yet generated.
    var rows: [(name: String, path: String?)] {
        [("Resume (PDF)", resumePdf), ("Figure (PNG)", figurePng), ("Clip (MP4)", clipMp4),
         ("Cover letter", coverLetterMd), ("Form answers", formAnswersMd)]
    }
}

struct Ruling: Codable, Hashable {
    var decision: String?
    var reason: String?
    var decidedAt: String?
    var by: String?
    /// Set by `POST /postings/<id>/approve-submit` (phone) or `career.py approve-submit` (typed yes).
    var submitApprovedAt: String? = nil
    var submitApprovedBy: String? = nil
    var submitApprovalNote: String? = nil
}

/// `submission`: what `career.py submit` saw after clicking Submit. Every field optional; a malformed
/// value decodes empty so it never costs the phone the whole record.
struct Submission: Codable, Hashable {
    var clickedAt: String?
    var confirmedAt: String?
    var submittedAt: String?
    var confirmation: String?
    var confirmationText: String?
    var url: String?
    var ats: String?

    /// The one line the confirmation page said, from whichever key the writer used.
    var line: String? {
        [confirmation, confirmationText].compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .first { !$0.isEmpty }
    }

    private enum CodingKeys: String, CodingKey {
        case clickedAt, confirmedAt, submittedAt, confirmation, confirmationText, url, ats
    }

    init(from decoder: Decoder) throws {
        let c = try? decoder.container(keyedBy: CodingKeys.self)
        func str(_ k: CodingKeys) -> String? { (try? c?.decodeIfPresent(String.self, forKey: k)) ?? nil }
        clickedAt = str(.clickedAt)
        confirmedAt = str(.confirmedAt)
        submittedAt = str(.submittedAt)
        confirmation = str(.confirmation)
        confirmationText = str(.confirmationText)
        url = str(.url)
        ats = str(.ats)
    }
}

/// `GET /postings/<id>/fill`: the Apply stage's `form_fill.json`. Only what the phone shows is decoded;
/// every field is optional and a malformed row decodes empty.
struct FormFill: Decodable, Hashable {
    var postingId: String?
    var filledAt: String?
    var dryRun: Bool?
    var screenshot: String?
    var submitControl: SubmitControl?
    var rows: [FormFillRow]
    /// Workday wizard steps (apply_workday.py); empty for one-page forms.
    var steps: [Step] = []

    struct SubmitControl: Decodable, Hashable {
        var text: String?
        var visible: Bool?
    }

    struct Step: Decodable, Hashable {
        var name: String?
        var screenshot: String?
    }

    var needsUser: [FormFillRow] { rows.filter(\.needsUser) }

    private enum CodingKeys: String, CodingKey { case postingId, filledAt, dryRun, screenshot, submitControl, rows, steps }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        postingId = (try? c.decodeIfPresent(String.self, forKey: .postingId)) ?? nil
        filledAt = (try? c.decodeIfPresent(String.self, forKey: .filledAt)) ?? nil
        dryRun = (try? c.decodeIfPresent(Bool.self, forKey: .dryRun)) ?? nil
        screenshot = (try? c.decodeIfPresent(String.self, forKey: .screenshot)) ?? nil
        submitControl = (try? c.decodeIfPresent(SubmitControl.self, forKey: .submitControl)) ?? nil
        rows = (try? c.decodeIfPresent([FormFillRow].self, forKey: .rows)) ?? []
        steps = (try? c.decodeIfPresent([Step].self, forKey: .steps)) ?? []
    }
}

struct FormFillRow: Decodable, Hashable {
    var label: String?
    var type: String?
    var required: Bool?
    var value: String?
    var source: String?
    var status: String?
    var verified: Bool?
    var question: Bool?

    /// career.py marks a row your agent could not fill with "needs user" in its status; such rows block the submit.
    var needsUser: Bool { (status ?? "").contains("needs user") }

    private enum CodingKeys: String, CodingKey { case label, type, required, value, source, status, verified, question }

    init(from decoder: Decoder) throws {
        let c = try? decoder.container(keyedBy: CodingKeys.self)
        func str(_ k: CodingKeys) -> String? { (try? c?.decodeIfPresent(String.self, forKey: k)) ?? nil }
        func bool(_ k: CodingKeys) -> Bool? { (try? c?.decodeIfPresent(Bool.self, forKey: k)) ?? nil }
        label = str(.label)
        type = str(.type)
        required = bool(.required)
        // `value` is usually a string; tolerate numbers and booleans.
        if let v = str(.value) {
            value = v
        } else if let n: Double = (try? c?.decodeIfPresent(Double.self, forKey: .value)) ?? nil {
            value = n == n.rounded() ? String(Int(n)) : String(n)
        } else if let b = bool(.value) {
            value = b ? "Yes" : "No"
        } else {
            value = nil
        }
        source = str(.source)
        status = str(.status)
        verified = bool(.verified)
        question = bool(.question)
    }
}

/// An application from the submit onward (serve.py `application_view`): status and recorded history, read.
struct ApplicationState: Codable, Hashable {
    var state: String
    var label: String
    var active: Bool
    var submittedAt: String?
    var daysSinceSubmitted: Int?
    var lastEvent: Event?
    var lastActivityAt: String?
    var timeline: [Event]?

    struct Event: Codable, Hashable {
        var at: String
        var text: String
        var kind: String?
        var subject: String?

        var date: Date? { try? Date(at, strategy: .iso8601) }
        var isEmail: Bool { kind == "email" }
    }

    var submittedDate: Date? { submittedAt.flatMap { try? Date($0, strategy: .iso8601) } }
    var activityDate: Date? { (lastActivityAt ?? submittedAt).flatMap { try? Date($0, strategy: .iso8601) } }
}

/// `GET /postings/<id>/preview` (tools/presentation.py): the filled form as the employer receives it.
struct PreviewDoc: Decodable {
    var postingId: String?
    var company: String?
    var title: String?
    var status: String?
    var deadline: String?
    var fillDigest: String?
    var readOnly: Bool?
    var submitted: Bool?
    var submitApprovedAt: String?
    var warnings: [String]?
    var sections: [Section]?
    var screenshots: [Shot]?
    var gate: [String]?
    var canSubmit: Bool?
    var submitQuestion: String?
    /// "Everything checked, ready to submit" / "1 answer needed" / "On hold: …" / "Submitted".
    var statusLine: String?
    /// serve.py review_action: submit, answer, hold or submitting. Decides the page, not the approval stamp.
    var action: String?
    /// Reviewer info notes, behind one toggle at the end.
    var infoNotes: [InfoNote]?
    /// His comments on the whole application, not resolved yet; each holds the submit.
    var openComments: [UserNote]?

    struct InfoNote: Decodable, Hashable {
        var label: String?
        var text: String?
    }

    struct Section: Decodable, Hashable {
        var title: String
        var groups: [Group]
    }
    struct Group: Decodable, Hashable {
        var title: String?
        var fields: [Field]
    }
    struct Field: Decodable, Hashable {
        var label: String
        var value: String
        var kind: String
        var file: String?
        /// How the form draws it: input, textarea, select, radio, check, checks, file, note.
        var control: String?
        var required: Bool?
        var options: [String]?
        /// Its review.json box: whether it waits on the user, warn/block notes, the question it is asked in.
        var box: Box?
        /// Present when he can edit it himself: his current text and the form's options.
        var edit: Edit?
    }
    struct Edit: Decodable, Hashable {
        var value: String?
        var options: [String]?
        var multi: Bool?
        var long: Bool?
    }
    struct Box: Decodable, Hashable {
        var key: String
        var state: String?
        var seen: String?
        var waits: Bool?
        var fixing: Bool?
        var notes: [Note]?
        var question: BoxQuestion?
        /// The user's own comments on this field, shown as "Your note".
        var comments: [UserNote]?
    }
    struct UserNote: Decodable, Hashable {
        var text: String?
        var at: String?
    }
    struct Note: Decodable, Hashable {
        var severity: String?
        var text: String?
    }
    struct BoxQuestion: Decodable, Hashable {
        var id: String
        var approve: String?
        var change: String?
    }
    struct Shot: Decodable, Hashable {
        var file: String
        var caption: String
    }
}

/// The per-box review in one line, as the postings list carries it (`review_summary`).
struct ReviewSummary: Codable, Hashable {
    var total: Int
    var approved: Int
    var pending: Int?
    var commented: Int?
    var blocked: Int?
    var allApproved: Bool?
    var submitApproved: Bool?
    /// What the user has to do (serve.py review_action): submit, answer, hold or submitting.
    var action: String?
    /// One plain line: "Ready to submit", "1 question: <label>", "Being fixed: role description", "Waiting for the reviewer".
    var why: String?
    /// Open questions for the boxes waiting on the user (Needs your answer).
    var questions: [BoxQuestion]?

    struct BoxQuestion: Codable, Hashable {
        var id: String
        var label: String?
    }
}

/// `GET /postings/<id>/review`: the per-box review (tools/career.py, review.json). Every filled value and every
/// drafted custom answer is one box the user approves or comments on; Approve submit needs every box approved.
struct ReviewDoc: Decodable, Hashable {
    var postingId: String?
    var boxes: [ReviewBox]
    var summary: Summary?

    struct Summary: Decodable, Hashable {
        var total: Int
        var approved: Int
        var pending: Int?
        var commented: Int?
        var blocked: Int?
        var allApproved: Bool?
    }

    var counts: (approved: Int, total: Int, blocked: Int) {
        if let s = summary { return (s.approved, s.total, s.blocked ?? 0) }
        return (boxes.filter { $0.state == "approved" }.count, boxes.count, boxes.filter(\.isBlocked).count)
    }

    var allApproved: Bool {
        let c = counts
        return c.total > 0 && c.approved == c.total && c.blocked == 0
    }

    private enum CodingKeys: String, CodingKey { case postingId, boxes, summary }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        postingId = (try? c.decodeIfPresent(String.self, forKey: .postingId)) ?? nil
        boxes = (try? c.decodeIfPresent([ReviewBox].self, forKey: .boxes)) ?? []
        summary = (try? c.decodeIfPresent(Summary.self, forKey: .summary)) ?? nil
    }
}

struct ReviewBox: Decodable, Hashable, Identifiable {
    var key: String
    var kind: String
    var label: String
    var value: String
    var state: String
    var flags: [Flag]
    var comments: [Comment]
    var valueChangedAt: String?
    var source: String?
    var required: Bool?

    var id: String { key }
    var activeFlags: [Flag] { flags.filter { $0.stale != true } }
    var isBlocked: Bool { activeFlags.contains { $0.severity == "block" } }

    struct Flag: Decodable, Hashable {
        var severity: String
        var text: String
        var by: String?
        var stale: Bool?
    }

    struct Comment: Decodable, Hashable {
        var text: String
        var by: String?
        var at: String?
        /// Written from one of the apps (the user), not by the coordinating session.
        var isMine: Bool { by == "phone" || by == "web" }
    }

    private enum CodingKeys: String, CodingKey { case key, kind, label, value, state, flags, comments, valueChangedAt, source, required }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        kind = (try? c.decodeIfPresent(String.self, forKey: .kind)) ?? "field"
        label = (try? c.decodeIfPresent(String.self, forKey: .label)) ?? "Field"
        value = (try? c.decodeIfPresent(String.self, forKey: .value)) ?? ""
        state = (try? c.decodeIfPresent(String.self, forKey: .state)) ?? "pending"
        flags = (try? c.decodeIfPresent([Flag].self, forKey: .flags)) ?? []
        comments = (try? c.decodeIfPresent([Comment].self, forKey: .comments)) ?? []
        valueChangedAt = (try? c.decodeIfPresent(String.self, forKey: .valueChangedAt)) ?? nil
        source = (try? c.decodeIfPresent(String.self, forKey: .source)) ?? nil
        required = (try? c.decodeIfPresent(Bool.self, forKey: .required)) ?? nil
    }
}

/// `GET /postings/<id>/approve-submit`: whether the phone may approve the submit, and what is missing.
struct SubmitGate: Decodable, Hashable {
    var allowed: Bool
    var missing: [String]

    private enum CodingKeys: String, CodingKey { case allowed, missing }

    init(allowed: Bool, missing: [String]) {
        self.allowed = allowed
        self.missing = missing
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        allowed = (try? c.decode(Bool.self, forKey: .allowed)) ?? false
        missing = (try? c.decodeIfPresent([String].self, forKey: .missing)) ?? []
    }
}

struct HistoryItem: Codable, Hashable {
    var at: String?
    var event: String?
}

enum Fit: String {
    case direct, adjacent
    case offTarget = "off-target"

    var title: String { rawValue }

    /// Inbox sort order: direct first.
    var rank: Int {
        switch self {
        case .direct: 0
        case .adjacent: 1
        case .offTarget: 2
        }
    }
}

/// Inbox grouping. Order of cases is display order.
enum InboxSection: Int, CaseIterable, Identifiable {
    case toReview, approved, skipped, applied, other

    init(status: String) {
        switch status {
        case "new", "enriched": self = .toReview
        case "approved", "tailored", "ready": self = .approved
        case "skipped": self = .skipped
        case "applied", "interview", "offer", "rejected", "closed": self = .applied
        default: self = .other
        }
    }

    var id: Int { rawValue }
    var title: String {
        switch self {
        case .toReview: "To review"
        case .approved: "Approved"
        case .skipped: "Skipped"
        case .applied: "Applied"
        case .other: "Other"
        }
    }
}

/// `GET /stamp`: changes when anything the app shows changes; polled instead of the 3 MB postings list.
struct DataStamp: Codable {
    var stamp: String
    /// The newest published iPhone build and its itms-services link (career/app/ota.sh, tools/ota.py).
    var build: String?
    var install: String?
}

struct Health: Codable { var ok: Bool; var postings: Int?; var version: String? }

/// `GET /batch`: the Today batch the coordinating session picked (tools/batch.py). Items come in batch
/// order, open ones first; each carries the whole record and a one-line `why`.
struct Batch: Decodable, Hashable {
    var createdAt: String?
    var note: String?
    var items: [Item]

    struct Item: Decodable, Hashable {
        var why: String?
        var record: Posting
    }

    static let empty = Batch(createdAt: nil, note: nil, items: [])
}

extension Posting {
    /// Still waiting for a ruling (`new`/`enriched`); any other status clears it from the Today batch.
    var isOpenForBatch: Bool { status == "new" || status == "enriched" }
}

enum Decision: String { case approved, skipped }

// MARK: - App chat (workspace data/chat.json)

/// One message in the support chat with the coordinating agent session (`GET /chat`, `POST /chat`).
struct ChatMessage: Codable, Identifiable, Hashable {
    let n: Int
    let at: String?
    let from: String
    let by: String?
    let text: String

    var deliveryError: DeliveryError?
    struct DeliveryError: Codable, Hashable { var code: String;var error: String }

    var id: Int { n }
    var isMine: Bool { from == "user" }
    var date: Date? { at.flatMap { try? Date($0, strategy: .iso8601) } }
}

/// `GET /chat?since=N`: the messages after N, oldest first, and the newest number on the server.
struct ChatPage: Codable {
    let messages: [ChatMessage]
    let last: Int
}

extension JSONDecoder {
    static let career: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
}

// MARK: - Questions inbox (workspace/data/questions/SCHEMA.md)

/// Something your agent or an agent needs the user to decide. Answered on the phone; serve.py runs
/// tools/ask.py, which writes the answer where the tools read it (profile.yml, form_answers.md, the ruling).
struct Question: Codable, Identifiable, Hashable {
    let id: String
    var askedAt: String?
    var by: String?
    var topic: String
    var postingId: String?
    var title: String
    var question: String
    var context: String?
    /// One shell command for the user to run (a sign-in, say), from the latest ask. Shown with Copy and Share.
    var command: String?
    var kind: String
    var options: [String]?
    var answer: String?
    var answeredAt: String?
    var answeredBy: String?
    var appliesTo: AppliesTo?
    var status: String
    var applied: Applied?
    var applyError: String?
    /// The user's latest free-text comment (any kind); sent with an answer or alone.
    var note: String?
    var noteAt: String?
    /// Answered with a comment alone: `answer` is nil, `applied` is false, the agent that asked reads the note.
    var needsReading: Bool?
    /// The conversation: turn 1 is the original ask; user turns are answers/comments; later ask turns are re-asks.
    /// The top-level question/kind/options/context always mirror the latest ask turn.
    var thread: [Turn]?
    /// Ids of old follow-up records folded into this thread (a link to one of them opens this thread).
    var mergedFrom: [String]?

    struct Turn: Codable, Hashable, Identifiable {
        var n: Int
        var at: String?
        var by: String?
        var role: String
        var text: String?
        var kind: String?
        var options: [String]?
        var context: String?
        /// This ask's shell command, when it has one (SCHEMA.md "Commands").
        var command: String?
        var answer: String?
        var title: String?

        var id: Int { n }
        var isAsk: Bool { role == "ask" }
        var date: Date? { at.flatMap { try? Date($0, strategy: .iso8601) } }
    }

    /// The thread, or a one-turn thread built from the top-level fields for a record from an older server.
    var turns: [Turn] {
        if let thread, !thread.isEmpty { return thread }
        return [Turn(n: 1, at: askedAt, by: by, role: "ask", text: question, kind: kind, options: options, context: context,
                     command: command)]
    }

    struct AppliesTo: Codable, Hashable {
        var profileKey: String?
        var postingId: String?
        var formQuestion: String?
        var ruling: String?
        var note: Bool?
        /// A submit asked as a question (ask.py apply_approve_submit): the posting id it approves.
        var approveSubmit: String?
        /// A review box asked as a question (ask.py ask_review_box).
        var reviewBox: ReviewBoxRef?
        var reviewBoxes: [ReviewBoxRef]?

        struct ReviewBoxRef: Codable, Hashable {
            var postingId: String
            var key: String
        }

        /// The one box this question stands for, if it stands for one.
        var singleBox: ReviewBoxRef? { reviewBox ?? (reviewBoxes?.count == 1 ? reviewBoxes?.first : nil) }
    }

    struct Applied: Codable, Hashable {
        var at: String?
        var result: String?

        private enum CodingKeys: String, CodingKey { case at, result }

        /// Tolerant: a comment-answered record stores `applied: false`, which must not break the whole list.
        init(from decoder: Decoder) throws {
            let c = try? decoder.container(keyedBy: CodingKeys.self)
            at = try? c?.decodeIfPresent(String.self, forKey: .at)
            result = try? c?.decodeIfPresent(String.self, forKey: .result)
        }
    }

    var isOpen: Bool { status == "open" }
    var answeredByComment: Bool { needsReading == true && answer == nil }
    var isDismissed: Bool { status == "dismissed" }
    var kindValue: QuestionKind { QuestionKind(rawValue: kind) ?? .text }
    var topicValue: QuestionTopic { QuestionTopic(rawValue: topic) ?? .other }
    var askedDate: Date? { askedAt.flatMap { try? Date($0, strategy: .iso8601) } }
    var answeredDate: Date? { answeredAt.flatMap { try? Date($0, strategy: .iso8601) } }

    /// One line on what the answer changes, shown above the controls.
    var effectLine: String {
        if let k = appliesTo?.profileKey { return "Your answer is written to profile.yml (\(k))." }
        if let f = appliesTo?.formQuestion {
            return kindValue == .confirm ? "Confirming makes this text the form's answer to “\(f)”."
                                         : "Your answer becomes the form's answer to “\(f)”."
        }
        if appliesTo?.ruling == "approve" { return "Yes approves this posting, the same as Approve in Listings. No changes nothing." }
        return "Your answer is kept with the question for your agent to act on."
    }
}

enum QuestionKind: String {
    case choice, multiselect, yesno, text, confirm

    var symbol: String {
        switch self {
        case .choice, .multiselect: "list.bullet.circle"
        case .yesno: "checkmark.circle"
        case .text: "text.bubble"
        case .confirm: "checkmark.seal"
        }
    }
}

enum QuestionTopic: String, CaseIterable {
    case application, profile, site, github, referral, other

    var title: String {
        switch self {
        case .application: "Applications"
        case .profile: "Profile"
        case .site: "Website"
        case .github: "GitHub"
        case .referral: "Referrals"
        case .other: "Other"
        }
    }

    var symbol: String {
        switch self {
        case .application: "paperplane"
        case .profile: "person.text.rectangle"
        case .site: "globe"
        case .github: "chevron.left.forwardslash.chevron.right"
        case .referral: "person.2"
        case .other: "questionmark.circle"
        }
    }
}
