import SwiftUI
import UIKit

/// A shell command for the user to run (an ask's `command`, or one found in a context): monospace, with Copy
/// (UIPasteboard; Universal Clipboard carries it to the server) and Share (AirDrop or Messages to the server).
struct CommandBlock: View {
    let command: String
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(command)
                .font(.system(.footnote, design: .monospaced))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityLabel("Command: \(command)")
            HStack(spacing: 8) {
                Button {
                    UIPasteboard.general.string = command
                    copied = true
                } label: {
                    Label(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc")
                        .contentTransition(.symbolEffect(.replace))
                }
                .tint(copied ? .green : .accentColor)
                .accessibilityIdentifier("question.command.copy")
                ShareLink(item: command) {
                    Label("Share", systemImage: "square.and.arrow.up")
                }
                .accessibilityIdentifier("question.command.share")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .font(.subheadline.weight(.medium))
        }
        .padding(10)
        .background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .sensoryFeedback(.success, trigger: copied) { _, now in now }
        .task(id: copied) {
            guard copied else { return }
            try? await Task.sleep(for: .seconds(1.6))
            copied = false
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("question.command")
    }
}

/// Commands inside older context text (data/questions/SCHEMA.md "Commands"; same rule as career/web/app.js
/// `splitCommands`): a fenced ``` block, or a line starting with "$ " plus its backslash continuations. Display only.
enum CommandText {
    enum Part: Hashable {
        case text(String)
        case command(String)
    }

    static func split(_ text: String) -> [Part] {
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
            .components(separatedBy: "\n")
        var parts: [Part] = []
        var buf: [String] = []
        func flush() {
            let t = trimBlock(buf.joined(separator: "\n"))
            if !t.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { parts.append(.text(t)) }
            buf = []
        }
        var i = 0
        while i < lines.count {
            var body: [String]?
            let lead = lines[i].drop { $0 == " " || $0 == "\t" }
            if lead.hasPrefix("```") {
                var b: [String] = []
                while i + 1 < lines.count, !isFence(lines[i + 1]) {
                    i += 1
                    b.append(lines[i])
                }
                i += 1                                  // the closing fence (past the end when unclosed)
                body = b
            } else if lead.hasPrefix("$ ") {
                var b = [String(lead.dropFirst(2))]
                while b[b.count - 1].hasSuffix("\\"), i + 1 < lines.count {
                    i += 1
                    b.append(lines[i])
                }
                body = b
            }
            if let body {
                flush()
                let c = trimBlock(body.joined(separator: "\n"))
                if !c.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { parts.append(.command(c)) }
            } else {
                buf.append(lines[i])
            }
            i += 1
        }
        flush()
        return parts
    }

    private static func isFence(_ line: String) -> Bool {
        line.drop { $0 == " " || $0 == "\t" }.hasPrefix("```")
    }

    /// Leading blank lines and trailing white space off; indentation inside is kept.
    private static func trimBlock(_ s: String) -> String {
        var t = Substring(s)
        while t.first == "\n" { t = t.dropFirst() }
        while let last = t.last, last.isWhitespace { t = t.dropLast() }
        return String(t)
    }
}

/// The provenance line that heads every AI-written card. Small caps, secondary colour, never hidden.
struct AILabel: View {
    let text: String

    var body: some View {
        Label {
            Text(text)
                .font(.caption.weight(.medium).smallCaps())
        } icon: {
            Image(systemName: "cpu")
                .font(.caption2)
        }
        .foregroundStyle(.secondary)
        .accessibilityLabel("Provenance: \(text)")
    }
}

struct FitBadge: View {
    let fit: Fit?

    var body: some View {
        if let fit {
            Text(fit.title)
                .font(.caption.weight(.medium))
                .padding(.horizontal, 7)
                .padding(.vertical, 2)
                .background(color.opacity(0.14), in: Capsule())
                .foregroundStyle(color)
                .accessibilityLabel("Fit: \(fit.title)")
        }
    }

    private var color: Color {
        switch fit {
        case .direct: .green
        case .adjacent: .orange
        case .offTarget, .none: .secondary
        }
    }
}

extension PayNormalized {
    /// "$40-50/hr · ≈$83K-104K/yr" with a faint "≈" on the side the posting did not state.
    var line: Text? {
        guard let h = hourlyText, let y = yearlyText else { return nil }
        let approx = Text(verbatim: "≈").foregroundStyle(.tertiary)
        let none = Text(verbatim: "")
        return Text("\(hourlyConverted ? approx : none)\(h) · \(yearlyConverted ? approx : none)\(y)")
    }
}

/// Pay on a row: "$40-50/hr · ≈$83K-104K/yr" (SCHEMA.md "Pay"). Never wraps inside itself.
struct PayChip: View {
    let pay: PayNormalized

    var body: some View {
        if let line = pay.line {
            line
                .font(.caption)
                .monospacedDigit()
                .lineLimit(1)
                .fixedSize()
                .padding(.horizontal, 7)
                .padding(.vertical, 2)
                .background(Color(.tertiarySystemFill), in: Capsule())
                .accessibilityLabel(pay.spoken)
                .accessibilityIdentifier("row.pay")
        }
    }
}

struct StatusBadge: View {
    let status: String

    var body: some View {
        Text(status)
            .font(.caption.weight(.medium))
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(Color(.tertiarySystemFill), in: Capsule())
            .foregroundStyle(.secondary)
    }
}

/// Grouped-background card. Light/dark via the system colours.
struct Card<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct SectionHead: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(.secondary)
            .padding(.top, 2)
    }
}

struct BulletList: View {
    let items: [String]
    var symbol = "•"
    var tint: Color = .secondary

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(symbol).foregroundStyle(tint).accessibilityHidden(true)
                    Text(item)
                }
            }
        }
        .font(.callout)
    }
}

struct LinkList: View {
    let urls: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(urls, id: \.self) { s in
                if let u = URL(string: s) {
                    Link(destination: u) {
                        Label(s, systemImage: "arrow.up.right.square")
                            .font(.callout)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                } else {
                    Text(s).font(.callout).foregroundStyle(.secondary)
                }
            }
        }
    }
}

struct ToastView: View {
    let toast: Toast

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: toast.kind == .error ? "exclamationmark.triangle" : "checkmark.circle")
            Text(toast.text).font(.callout)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(toast.kind == .error ? Color.red.opacity(0.92) : Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .foregroundStyle(toast.kind == .error ? Color.white : Color.primary)
        .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
        .padding(.top, 4)
        .accessibilityAddTraits(.isStaticText)
    }
}

/// Small rounded capsule for a fit reason or flag. Neutral by default; `tint` colours the fill and glyph,
/// the text stays primary so it keeps contrast in both appearances.
/// `company_info.website` on a row: a small domain label ("apptronik.com"). A borderless Link takes its own tap inside
/// a List row, so it opens Safari without opening the posting.
struct WebsiteLink: View {
    let url: URL

    /// "apptronik.com" from "https://www.apptronik.com/"; Google Sites keeps its path, the host alone names no one.
    static func name(_ url: URL) -> String {
        var host = url.host() ?? url.absoluteString
        if host.hasPrefix("www.") { host.removeFirst(4) }
        if host == "sites.google.com" {
            let path = url.path().hasSuffix("/") ? String(url.path().dropLast()) : url.path()
            return host + path
        }
        return host
    }

    var body: some View {
        let name = Self.name(url)
        Link(destination: url) {
            HStack(spacing: 3) {
                Image(systemName: "globe").imageScale(.small)
                Text(name).lineLimit(1).truncationMode(.middle)
            }
            .font(.caption)
        }
        .buttonStyle(.borderless)
        .accessibilityLabel("\(name), company website")
        .accessibilityIdentifier("row.website")
    }
}

struct Chip: View {
    let text: String
    var tint: Color?
    var systemImage: String?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            if let systemImage {
                Image(systemName: systemImage)
                    .imageScale(.small)
                    .foregroundStyle(tint ?? .secondary)
                    .accessibilityHidden(true)
            }
            Text(text)
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .font(.caption)
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(tint.map { AnyShapeStyle($0.opacity(0.16)) } ?? AnyShapeStyle(Color(.tertiarySystemFill)),
                    in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

/// Left-aligned wrapping row layout for chips. A chip wider than the row is offered the full row width and wraps its text.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let r = arrange(maxWidth: proposal.width ?? .infinity, subviews)
        return CGSize(width: r.width, height: r.height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let r = arrange(maxWidth: bounds.width, subviews)
        for (view, frame) in zip(subviews, r.frames) {
            view.place(at: CGPoint(x: bounds.minX + frame.minX, y: bounds.minY + frame.minY),
                       proposal: ProposedViewSize(frame.size))
        }
    }

    /// Rows fill left to right; items are centred vertically within their row.
    private func arrange(maxWidth: CGFloat, _ subviews: Subviews) -> (frames: [CGRect], width: CGFloat, height: CGFloat) {
        var frames: [CGRect] = []
        var rowStart = 0
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0, width: CGFloat = 0
        func closeRow() {
            for i in rowStart..<frames.count { frames[i].origin.y = y + (rowHeight - frames[i].height) / 2 }
            rowStart = frames.count
        }
        for view in subviews {
            let size = view.sizeThatFits(ProposedViewSize(width: maxWidth, height: nil))
            if x > 0, x + size.width > maxWidth {
                closeRow()
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            frames.append(CGRect(origin: CGPoint(x: x, y: y), size: size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
            width = max(width, x - spacing)
        }
        closeRow()
        return (frames, width, y + rowHeight)
    }
}

// MARK: - Copy ref

extension Posting {
    /// One line that points your agent (terminal or app chat) at this posting.
    var ref: String { "job: \(id) · \(company) · \(title)" }
}

extension Question {
    var ref: String { "question: \(id) · \(title)" }
}

/// "Copy ref": copies a posting's or question's one-line reference so the user can paste it to your agent.
struct CopyRefButton: View {
    @Environment(AppStore.self) private var store
    let text: String
    /// Rows: the icon over a small label. Headers: icon and label side by side.
    var stacked = true

    var body: some View {
        Button {
            UIPasteboard.general.string = text
            store.flash(.info, "Copied")
        } label: {
            if stacked {
                VStack(spacing: 2) {
                    Image(systemName: "doc.on.doc").font(.footnote)
                    Text("Copy ref").font(.system(size: 9, weight: .medium))
                }
                .foregroundStyle(.secondary)
                .frame(minWidth: 40, minHeight: 36)
            } else {
                Label("Copy ref", systemImage: "doc.on.doc").font(.caption.weight(.medium))
            }
        }
        .buttonStyle(.borderless)
        .accessibilityLabel("Copy ref")
        .accessibilityHint(text)
    }
}

extension View {
    /// Hardware keyboard: Return sends, Shift+Return is a new line. Presses on the on-screen keyboard never reach
    /// onKeyPress, so there Return stays a new line and the Send button sends.
    func returnSends(_ send: @escaping () -> Void) -> some View {
        onKeyPress(keys: [.return], phases: .down) { press in
            if press.modifiers.contains(.shift) { return .ignored }
            send()
            return .handled
        }
    }

    /// A list row with its Copy ref button at the trailing edge.
    func withCopyRef(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            self.frame(maxWidth: .infinity, alignment: .leading)
            CopyRefButton(text: text)
        }
    }
}
