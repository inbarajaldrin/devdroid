import SwiftUI

/// An image under serve.py's `/files` root, loaded through the paired (bearer-token) client.
/// Fills or fits whatever frame the caller gives it; the caller clips.
struct RemoteImage: View {
    @Environment(AppStore.self) private var store
    let rel: String
    var maxPixel = 900
    var contentMode: ContentMode = .fill

    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        ZStack {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: contentMode)
            } else if failed {
                Image(systemName: "photo")
                    .foregroundStyle(.tertiary)
            } else {
                ProgressView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .task(id: rel) {
            failed = false
            do { image = try await store.image(rel, maxPixel: maxPixel) }
            catch { if !Task.isCancelled { failed = true } }
        }
    }
}

/// Horizontal strip of 4:3 cards with a one-line caption; tap opens the full-screen pager.
struct ImageStrip: View {
    let images: [CompanyImage]
    let onOpen: (Int) -> Void

    var body: some View {
        ScrollView(.horizontal) {
            LazyHStack(alignment: .top, spacing: 10) {
                ForEach(Array(images.enumerated()), id: \.element.id) { i, img in
                    Button { onOpen(i) } label: { card(img) }
                        .buttonStyle(.plain)
                        .containerRelativeFrame(.horizontal, count: images.count == 1 ? 1 : 4,
                                                span: images.count == 1 ? 1 : 3, spacing: 10)
                        .accessibilityLabel(img.caption ?? "Company image \(i + 1)")
                        .accessibilityHint("Opens full screen")
                        .accessibilityIdentifier("detail.image.\(i)")
                }
            }
            .scrollTargetLayout()
        }
        .scrollIndicators(.hidden)
        .scrollTargetBehavior(.viewAligned)
        .contentMargins(.horizontal, 16, for: .scrollContent)
        .padding(.horizontal, -16)   // bleed to the screen edges inside the 16 pt-padded detail column
        .accessibilityIdentifier("detail.images")
    }

    private func card(_ img: CompanyImage) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Color(.tertiarySystemFill)
                .aspectRatio(4.0 / 3.0, contentMode: .fit)
                .overlay { RemoteImage(rel: img.filesPath, maxPixel: 900) }
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            if let c = img.caption, !c.isEmpty {
                Text(c)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .contentShape(Rectangle())
    }
}

/// Full-screen pager: image fitted, caption, why it matters, source link.
struct ImagePager: View {
    let images: [CompanyImage]
    @State var selection: Int
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            TabView(selection: $selection) {
                ForEach(Array(images.enumerated()), id: \.element.id) { i, img in
                    page(img).tag(i)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: images.count > 1 ? .always : .never))
            .background(Color.black.ignoresSafeArea())
            .navigationTitle(images.count > 1 ? "\(selection + 1) of \(images.count)" : "")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .accessibilityIdentifier("pager.done")
                }
            }
        }
        .environment(\.colorScheme, .dark)
    }

    private func page(_ img: CompanyImage) -> some View {
        VStack(spacing: 16) {
            RemoteImage(rel: img.filesPath, maxPixel: 2048, contentMode: .fit)
            VStack(alignment: .leading, spacing: 8) {
                if let c = img.caption, !c.isEmpty {
                    Text(c).font(.body.weight(.semibold))
                }
                if let r = img.relevance, !r.isEmpty {
                    Text("Why it matters: ").font(.callout.weight(.semibold)) + Text(r).font(.callout)
                }
                if let u = img.sourceURL {
                    Link(destination: u) {
                        Label([img.source, u.host()].compactMap { $0 }.joined(separator: " · "), systemImage: "arrow.up.right.square")
                            .lineLimit(1)
                    }
                    .font(.footnote)
                }
            }
            .foregroundStyle(.primary)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 20)
            .padding(.bottom, images.count > 1 ? 44 : 16)   // clear the page dots
        }
    }
}

struct PagerSelection: Identifiable {
    let id = UUID()
    let images: [CompanyImage]
    let start: Int
}

/// A company's mark in a fixed square. Tile rule from `company_info.logo.background`: `dark` puts the mark on a
/// dark tile, `light` on a light tile, `any` on nothing. With no logo (or a failed load) it shows a monogram.
struct CompanyLogoView: View {
    @Environment(AppStore.self) private var store
    let logo: CompanyLogo?
    let company: String
    var size: CGFloat = 40
    var cornerRadius: CGFloat = 8

    @State private var image: UIImage?
    @State private var failed = false

    /// One decode size for every placement so the header and the detail title share the cache entry.
    private static let maxPixel = 144

    var body: some View {
        content
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay {
                if hasTile {
                    RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                        .strokeBorder(Color.primary.opacity(0.1), lineWidth: 0.5)
                }
            }
            .task(id: logo?.filesPath) {
                image = nil
                failed = false
                guard let rel = logo?.filesPath else { return }
                do { image = try await store.image(rel, maxPixel: Self.maxPixel) }
                catch { if !Task.isCancelled { failed = true } }
            }
            .accessibilityHidden(true)
    }

    private var hasTile: Bool { image == nil || logo?.tile != .any }

    @ViewBuilder
    private var content: some View {
        if let image, let tile = logo?.tile {
            Image(uiImage: image)
                .resizable()
                .interpolation(.high)
                .aspectRatio(contentMode: .fit)
                .padding(tile == .any ? 0 : size * 0.12)
                .frame(width: size, height: size)
                .background {
                    switch tile {
                    case .dark: Color(white: 0.12)
                    case .light: Color.white
                    case .any: Color.clear
                    }
                }
        } else if logo?.filesPath != nil && !failed {
            Color(.tertiarySystemFill)
        } else {
            Text(Self.monogram(company))
                .font(.system(size: size * 0.4, weight: .semibold, design: .rounded))
                .minimumScaleFactor(0.5)
                .foregroundStyle(.secondary)
                .frame(width: size, height: size)
                .background(Color(.tertiarySystemFill))
        }
    }

    static func monogram(_ name: String) -> String {
        let words = name.split(whereSeparator: { !$0.isLetter && !$0.isNumber })
        if words.count == 1, let w = words.first, w.count <= 3 { return w.uppercased() }   // "1X", "HP"
        let letters = words.prefix(2).compactMap(\.first)
        return String(letters).uppercased()
    }
}
