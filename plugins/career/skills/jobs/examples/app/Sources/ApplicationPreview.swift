import SwiftUI

/// The Application preview (serve.py `GET /postings/<id>/preview`, tools/presentation.py): every field as the employer
/// receives it, by the form's own sections, essays in full, files that open, your agent's screenshots at the end.
/// It is the one place a submit is approved from: the button sits after every field (user, 2026-11-01). For an
/// application already sent it is the read-only "What was sent".
struct ApplicationPreviewView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let postingId: String

    @State private var doc: PreviewDoc?
    @State private var error: String?
    @State private var confirm = false
    @State private var approvalDigest = ""
    @State private var approving = false
    @State private var file: PreviewItem?
    @State private var shot: PreviewItem?
    @State private var opening: String?
    @State private var commenting: PreviewDoc.Field?
    @State private var commentingApp = false
    @State private var editing: PreviewDoc.Field?
    @State private var loadedGeneration = 0
    @State private var showInfo = false
    @State private var busyBox: String?

    var body: some View {
        NavigationStack {
            Group {
                if let doc {
                    content(doc)
                } else if let error {
                    ContentUnavailableView {
                        Label("Could not load the preview", systemImage: "exclamationmark.triangle")
                    } description: { Text(error) } actions: {
                        Button("Retry") { Task { await load() } }
                    }
                } else {
                    ProgressView("Loading the application…")
                }
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle(doc?.submitted == true ? "What was sent" : "Application preview")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .task { await load() }
        .onChange(of: store.dataGeneration) { reloadIfIdle() }
        .onChange(of: commenting) { reloadIfIdle() }
        .onChange(of: editing) { reloadIfIdle() }
        .onChange(of: commentingApp) { reloadIfIdle() }
        .sheet(item: $file) { item in QuickLookSheet(url: item.url, title: item.title) }
        .sheet(item: $commenting) { f in
            FieldCommentSheet(label: f.label) { text in Task { await comment(f, text) } }
                .presentationDetents([.height(300)])
        }
        .sheet(item: $editing) { f in
            FieldEditSheet(label: f.label, edit: f.edit) { text in
                Task {
                    if let key = f.box?.key { await store.editBox(postingId, key: key, text: text) }
                    await load()
                }
            }
        }
        .sheet(isPresented: $commentingApp) {
            FieldCommentSheet(label: "This application") { text in Task { await commentOnApplication(text) } }
                .presentationDetents([.height(300)])
        }
        .fullScreenCover(item: $shot) { item in QuickLookSheet(url: item.url, title: item.title) }
    }

    /// New data never lands under an open sheet; it waits until he closes it.
    private func reloadIfIdle() {
        guard loadedGeneration != store.dataGeneration, commenting == nil, editing == nil, !commentingApp, !confirm else { return }
        Task { await load() }
    }

    private func load() async {
        error = nil
        loadedGeneration = store.dataGeneration
        do { doc = try await store.preview(postingId) } catch { self.error = error.localizedDescription }
    }

    private var posting: Posting? { store.posting(postingId) }

    private func content(_ doc: PreviewDoc) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header(doc)
                ForEach(doc.sections ?? [], id: \.self) { section(doc, $0) }
                if (doc.sections ?? []).isEmpty {
                    Card { Text("Nothing was filled for this posting yet.").foregroundStyle(.secondary) }
                }
                if let info = doc.infoNotes, !info.isEmpty { infoNotes(info) }
                if let shots = doc.screenshots, !shots.isEmpty { screenshots(shots, submitted: doc.submitted == true) }
                footer(doc)
                applicationCommentButton
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
    }

    // MARK: header

    private func header(_ doc: PreviewDoc) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                CompanyLogoView(logo: posting?.logo, company: doc.company ?? "", size: 28, cornerRadius: 6)
                Text(doc.company ?? "").font(.headline)
                Spacer()
                CopyRefButton(text: "job: \(postingId) · \(doc.company ?? "") · \(doc.title ?? "")", stacked: false)
            }
            Text(doc.title ?? "").font(.title3.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            statusLine(doc)
            if let p = posting {
                let d = PostingDates(p)
                if d.level.actSoon {
                    DateChip(text: d.actSoonChip, level: d.level)
                } else if let chip = d.rowChip {
                    DateChip(text: chip, level: d.level)
                }
            }
            if let w = doc.warnings, !w.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Label("Read before you submit", systemImage: "exclamationmark.triangle.fill")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.orange)
                    ForEach(w, id: \.self) { line in
                        Text("• " + line).font(.callout).fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.orange.opacity(0.15), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .accessibilityIdentifier("preview.warnings")
            }
            if let open = doc.openComments, !open.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Label("Your comment: being fixed", systemImage: "clock").font(.subheadline.weight(.semibold))
                    ForEach(open, id: \.self) { c in
                        Text(c.text ?? "").font(.callout).fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .accessibilityIdentifier("preview.openComments")
            }
            applicationCommentButton
        }
    }

    private var applicationCommentButton: some View {
        Button { commentingApp = true } label: { Label("Comment on this application", systemImage: "text.bubble") }
            .buttonStyle(.bordered)
            .accessibilityIdentifier("preview.commentApplication")
    }

    // MARK: fields

    private func statusLine(_ doc: PreviewDoc) -> some View {
        let line = doc.submitted == true ? "What was sent" : (doc.statusLine ?? "Application preview")
        let (tint, symbol): (Color, String) = doc.submitted == true || line.hasPrefix("Everything")
            ? (.green, line.hasPrefix("Everything") ? "checkmark.seal.fill" : "checkmark.circle.fill")
            : line.hasPrefix("On hold") ? (.secondary, "clock") : (.orange, "questionmark.circle.fill")
        return Label(line, systemImage: symbol)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(tint)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(tint.opacity(0.14), in: Capsule())
            .accessibilityIdentifier("preview.status")
    }

    private func section(_ doc: PreviewDoc, _ s: PreviewDoc.Section) -> some View {
        Card {
            Text(s.title).font(.title3.weight(.semibold))
            Divider()
            ForEach(Array(s.groups.enumerated()), id: \.offset) { _, g in
                if let t = g.title {
                    // A repeating block (Work experience 1, Education 2) framed like the form's own block.
                    VStack(alignment: .leading, spacing: 12) {
                        Text(t).font(.subheadline.weight(.semibold))
                        ForEach(Array(g.fields.enumerated()), id: \.offset) { _, f in field(doc, f) }
                    }
                    .padding(12)
                    .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Color(.separator)))
                } else {
                    ForEach(Array(g.fields.enumerated()), id: \.offset) { _, f in field(doc, f) }
                }
            }
        }
    }

    /// One field drawn like the employer's form, with a comment icon at the right of its label. A field that waits on
    /// the user is highlighted, with Approve and Comment.
    @ViewBuilder
    private func field(_ doc: PreviewDoc, _ f: PreviewDoc.Field) -> some View {
        let waits = doc.submitted != true && doc.readOnly != true && f.box?.waits == true
        let canComment = doc.submitted != true
        let canEdit = canComment && doc.submitApprovedAt == nil && f.box != nil && f.edit != nil
        VStack(alignment: .leading, spacing: 6) {
            if f.control != "check" {
                (Text(f.label).font(.footnote.weight(.semibold))
                 + Text(f.required == true ? " *" : "").font(.footnote.weight(.bold)).foregroundColor(.red))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.trailing, canEdit ? 84 : canComment ? 36 : 0)
            }
            control(f)
                .padding(.trailing, canComment && f.control == "check" ? 36 : 0)
            ForEach(f.box?.comments ?? [], id: \.self) { c in
                (Text("Your note: ").bold() + Text(c.text ?? ""))
                    .font(.caption)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 5)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(f.box?.notes ?? [], id: \.self) { n in
                Label(n.text ?? "", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(n.severity == "block" ? .red : .orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if waits, let box = f.box {
                HStack(spacing: 8) {
                    Button { Task { await approve(f, box) } } label: {
                        if busyBox == box.key { ProgressView() } else {
                            Label(box.question?.approve == "Leave it empty" ? "Leave it empty" : "Approve", systemImage: "checkmark")
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    Button { commenting = f } label: { Label("Comment", systemImage: "text.bubble") }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(waits ? 10 : 0)
        .background(waits ? Color.orange.opacity(0.14) : .clear, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(alignment: .topTrailing) {
            if canComment {
                HStack(spacing: 0) {
                    if canEdit {
                        Button { editing = f } label: {
                            Text("Edit")
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(.tint)
                                .frame(width: 44, height: 44)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Edit \(f.label)")
                        .accessibilityIdentifier("preview.edit")
                    }
                    Button { commenting = f } label: {
                        Image(systemName: "text.bubble")
                            .font(.body)
                            .foregroundStyle(.secondary)
                            .frame(width: 44, height: 44)  // the tap target; the glyph stays small
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Comment on \(f.label)")
                }
                .offset(x: waits ? 0 : 12, y: waits ? -4 : -14)
            }
        }
    }

    @ViewBuilder
    private func control(_ f: PreviewDoc.Field) -> some View {
        let empty = f.kind == "empty"
        switch f.control {
        case "file":
            if empty {
                box(Text("No file attached").italic().foregroundStyle(.tertiary))
            } else if let rel = f.file {
                Button { Task { await open(rel, title: f.value) } } label: {
                    Label(f.value, systemImage: opening == rel ? "hourglass" : "paperclip")
                        .font(.callout.weight(.medium))
                        .lineLimit(1)
                }
                .buttonStyle(.bordered)
                .buttonBorderShape(.capsule)
                .controlSize(.small)
            } else {
                Label(f.value, systemImage: "paperclip").font(.callout)
            }
        case "check":
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: empty ? "square" : "checkmark.square.fill")
                    .foregroundStyle(empty ? AnyShapeStyle(.tertiary) : AnyShapeStyle(.tint))
                (Text(f.label) + Text(f.required == true ? " *" : "").foregroundColor(.red))
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
            }
        case "checks":
            VStack(alignment: .leading, spacing: 4) {
                ForEach(f.options ?? [f.value], id: \.self) { o in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "checkmark.square.fill").foregroundStyle(.tint)
                        Text(o).font(.callout)
                    }
                }
            }
        case "radio":
            FlowLayout(spacing: 14) {
                ForEach(f.options ?? [f.value], id: \.self) { o in
                    HStack(spacing: 6) {
                        Image(systemName: o == f.value ? "largecircle.fill.circle" : "circle")
                            .foregroundStyle(o == f.value ? AnyShapeStyle(.tint) : AnyShapeStyle(.tertiary))
                        Text(o).font(.callout.weight(o == f.value ? .semibold : .regular))
                            .foregroundStyle(o == f.value ? .primary : .secondary)
                    }
                }
            }
        case "note":
            Text(f.value).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        case "select":
            box(HStack {
                Text(f.value).italic(empty).foregroundStyle(empty ? .tertiary : .primary)
                Spacer(minLength: 4)
                Image(systemName: "chevron.up.chevron.down").font(.caption2).foregroundStyle(.tertiary)
            })
        default:
            box(Text(f.value)
                .italic(empty)
                .foregroundStyle(empty ? .tertiary : .primary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true), tall: f.control == "textarea")
        }
    }

    /// A read-only input: a light bordered box like the form's.
    private func box(_ content: some View, tall: Bool = false) -> some View {
        content
            .font(.body)
            .frame(maxWidth: .infinity, minHeight: tall ? 70 : nil, alignment: .topLeading)
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .background(Color(.tertiarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(Color(.separator), lineWidth: 0.5))
    }

    private func infoNotes(_ notes: [PreviewDoc.InfoNote]) -> some View {
        Card {
            Button { withAnimation(.snappy) { showInfo.toggle() } } label: {
                HStack {
                    Text("Reviewer notes (\(notes.count))").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
                    Spacer()
                    Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
                        .rotationEffect(.degrees(showInfo ? 90 : 0))
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if showInfo {
                ForEach(Array(notes.enumerated()), id: \.offset) { _, n in
                    (Text((n.label ?? "") + ": ").bold() + Text(n.text ?? ""))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: box actions (Approve and Comment answer the box's question when it has one)

    private func approve(_ f: PreviewDoc.Field, _ box: PreviewDoc.Box) async {
        busyBox = box.key
        defer { busyBox = nil }
        if let q = box.question, let a = q.approve {
            _ = await store.answer(q.id, a)
        } else {
            _ = await store.approveBox(postingId, key: box.key, seen: box.seen)
        }
        await load()
    }

    private func commentOnApplication(_ text: String) async {
        await store.commentOnApplication(postingId, text)
        await load()
    }

    private func comment(_ f: PreviewDoc.Field, _ text: String) async {
        guard let box = f.box else {               // no review box (the skills summary): a comment on the application
            await commentOnApplication("\(f.label): \(text)")
            return
        }
        if let q = box.question, let change = q.change {
            _ = await store.answer(q.id, change, note: text)
        } else {
            _ = await store.commentBox(postingId, key: box.key, text: text)
        }
        await load()
    }

    // MARK: screenshots

    private func screenshots(_ shots: [PreviewDoc.Shot], submitted: Bool) -> some View {
        Card {
            Text(submitted ? "Screenshots" : "Screenshots of the filled form").font(.headline)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(shots, id: \.self) { s in
                        Button { Task { await zoom(s) } } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                ShotThumb(rel: s.file)
                                Text(s.caption).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                            .frame(width: 150)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Open \(s.caption)")
                    }
                }
            }
        }
    }

    // MARK: footer: the one Approve submit

    @ViewBuilder
    private func footer(_ doc: PreviewDoc) -> some View {
        if doc.submitted == true {
            let at = posting?.application?.submittedDate
            Label(at.map { "Submitted \(QuestionDetailView.turnTime($0))." } ?? "Submitted.", systemImage: "checkmark.seal.fill")
                .foregroundStyle(.green)
                .padding(.vertical, 8)
        } else if doc.action == "submitting" {
            Label("Submit approved. Your agent is submitting it and will tell you.", systemImage: "paperplane.fill")
                .foregroundStyle(.tint)
                .padding(.vertical, 8)
        } else if doc.status == "ready" {
            Card {
                Text("You have reached the end of the application.").font(.footnote).foregroundStyle(.secondary)
                if doc.canSubmit != true {
                    Label("Not ready to submit yet: " + (doc.gate ?? []).joined(separator: "; "), systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Button { approvalDigest = doc.fillDigest ?? "";confirm = true } label: {
                    Group {
                        if approving { ProgressView() } else { Label("Approve submit", systemImage: "paperplane.fill") }
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(doc.canSubmit != true || approving)
                .accessibilityIdentifier("preview.approveSubmit")
                .alert("Submit \(doc.title ?? "this application") at \(doc.company ?? "the company")?", isPresented: $confirm) {
                    Button("Approve submit") {
                        Task {
                            approving = true
                            if await store.approveSubmitFromPreview(postingId, fillDigest:approvalDigest) { await load() }
                            approving = false
                        }
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("I'll submit it and tell you.")
                }
            }
        }
    }

    private func open(_ rel: String, title: String) async {
        opening = rel
        defer { opening = nil }
        do { file = PreviewItem(url: try await store.download(rel), title: title) }
        catch { store.flash(.error, error.localizedDescription) }
    }

    private func zoom(_ s: PreviewDoc.Shot) async {
        do { shot = PreviewItem(url: try await store.download(s.file), title: s.caption) }
        catch { store.flash(.error, error.localizedDescription) }
    }
}

/// A screenshot thumbnail, cropped from the top.
private struct ShotThumb: View {
    @Environment(AppStore.self) private var store
    let rel: String
    @State private var image: UIImage?

    var body: some View {
        ZStack {
            Color(.tertiarySystemFill)
            if let image {
                Image(uiImage: image).resizable().scaledToFill()
            }
        }
        .frame(width: 150, height: 110, alignment: .top)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .task { image = try? await store.image(rel, maxPixel: 600) }
    }
}

/// Comment on one field of the preview: your agent reads it, fixes the field and shows it again.
struct FieldCommentSheet: View {
    let label: String
    let onSend: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 10) {
                Text(label).font(.footnote.weight(.semibold)).foregroundStyle(.secondary).lineLimit(3)
                TextField("What should change?", text: $text, axis: .vertical)
                    .lineLimit(3...6)
                    .focused($focused)
                    .returnSends(submit)
                    .padding(10)
                    .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                Spacer()
            }
            .padding()
            .navigationTitle("Comment")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Send", action: submit)
                        .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .onAppear { focused = true }
        }
    }

    private func submit() {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        onSend(t)
        dismiss()
    }
}

extension PreviewDoc.Field: Identifiable {
    var id: String { (box?.key ?? "") + "|" + label }
}

/// On the posting detail, in place of the old per-box review: one line on where the application stands and the
/// way into the Application preview (or "What was sent" once applied).
struct ApplicationLinkCard: View {
    @Environment(AppStore.self) private var store
    let posting: Posting

    var body: some View {
        let r = posting.reviewSummary
        let done = posting.application != nil && posting.application?.state != "submitting"
        let line: String = done ? "Submitted. See every field as it was sent."
            : r?.action == "submit" ? "Everything checked, ready to submit"
            : r?.action == "answer" ? (r?.why ?? "An answer is needed")
            : r?.action == "hold" ? "On hold: \(r?.why ?? "")"
            : r?.action == "submitting" ? "Submit approved. Your agent is submitting it."
            : "The filled form, as the employer will receive it"
        Card {
            Text(done ? "What was sent" : "Application").font(.subheadline.weight(.semibold))
            Text(line).font(.callout).foregroundStyle(.secondary)
            Button { store.openPreview(posting.id) } label: {
                Label(done ? "What was sent" : "Open the application", systemImage: "doc.text.magnifyingglass")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .accessibilityIdentifier("detail.openApplication")
        }
    }
}

/// "Edit": he writes an answer himself. The form's options when it has them, else a text box. "Use my text" saves it
/// (POST .../review/<box>/edit); the session then refills the form.
struct FieldEditSheet: View {
    let label: String
    let edit: PreviewDoc.Edit?
    let onUse: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var ticks: Set<String> = []
    @FocusState private var focused: Bool

    private var options: [String] { edit?.options ?? [] }
    private var multi: Bool { edit?.multi == true && !options.isEmpty }
    private var result: String {
        multi ? options.filter(ticks.contains).joined(separator: "; ") : text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    if multi {
                        ForEach(options, id: \.self) { o in
                            Toggle(o, isOn: Binding(get: { ticks.contains(o) },
                                                    set: { if $0 { ticks.insert(o) } else { ticks.remove(o) } }))
                        }
                    } else if !options.isEmpty {
                        Picker("Answer", selection: $text) {
                            ForEach(options.contains(text) || text.isEmpty ? options : [text] + options, id: \.self) { Text($0).tag($0) }
                        }
                        .pickerStyle(.inline)
                        .labelsHidden()
                    } else {
                        TextEditor(text: $text)
                            .focused($focused)
                            .frame(minHeight: edit?.long == true ? 320 : 100)
                            .accessibilityIdentifier("edit.text")
                    }
                } header: {
                    Text(label).textCase(nil)
                } footer: {
                    Text("Your text goes on the form as written. Your agent updates the form, and it can be submitted after that.")
                }
            }
            .navigationTitle("Edit")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Use my text") { onUse(result); dismiss() }
                        .disabled(result.isEmpty)
                        .accessibilityIdentifier("edit.use")
                }
            }
            .onAppear {
                text = edit?.value ?? ""
                ticks = Set(text.split(separator: ";").map { $0.trimmingCharacters(in: .whitespaces) })
                focused = true
            }
        }
    }
}
