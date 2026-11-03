# Apple documentation entry points

Use the retrieval rule in [SKILL.md](SKILL.md). `markdown` rows give canonical
page URLs; `html` and `json` rows are fetched as linked. Read the appropriate hub
and follow its current links rather than guessing a symbol or release slug.

## Frameworks, APIs and developer tools

| Topic | Entry point | Format | Read for |
|---|---|---|---|
| All technologies | [Technology directory](https://developer.apple.com/tutorials/data/documentation/technologies.json) | json | Every framework and technology, grouped and tagged; use `sections[].groups[].technologies[]` and resolve destinations through `references`. Covers frameworks beyond the common starting points below. |
| Swift | [Swift](https://developer.apple.com/documentation/swift) | markdown | Standard library types, protocols, collections and concurrency APIs. |
| Declarative UI | [SwiftUI](https://developer.apple.com/documentation/swiftui) | markdown | App structure, views, state, data flow, layout, navigation and platform integration. |
| iOS and iPadOS UI | [UIKit](https://developer.apple.com/documentation/uikit) | markdown | View controllers, application lifecycle, events and UIKit integration. |
| macOS UI | [AppKit](https://developer.apple.com/documentation/appkit) | markdown | Windows, menus, events, controls and native Mac application behavior. |
| Shared services | [Foundation](https://developer.apple.com/documentation/foundation) | markdown | Networking, files, data, dates, localization and foundational types. |
| Build and distribution | [Xcode](https://developer.apple.com/documentation/xcode) | markdown | Building, testing, debugging, capabilities, signing, packaging and distribution; follow the relevant task links. |
| Official examples | [Sample Code Library](https://developer.apple.com/documentation/samplecode) | markdown | Apple's sample projects by topic; open a sample's page for what it demonstrates and its download link. |

## Swift language

| Topic | Entry point | Format | Read for |
|---|---|---|---|
| The language guide and reference | [The Swift Programming Language](https://docs.swift.org/swift-book/data/documentation/the-swift-programming-language.json) | json | Chapters on the language itself: concurrency, generics, macros, ownership and the rest. Read `topicSections`; open a chapter at `https://docs.swift.org/swift-book/data` + its `references[id].url` + `.json`. The title says when the published edition is a beta. |
| Language changes | [Swift Evolution proposals](https://download.swift.org/swift-evolution/v1/evolution.json) | json | Every proposal with `id` (SE-NNNN), `title` and `status.state` (implemented, accepted, active review, rejected); read one as markdown at `https://raw.githubusercontent.com/swiftlang/swift-evolution/main/proposals/` + its `link`. Served as `application/octet-stream`; parse it as JSON. |

## Design

| Topic | Entry point | Format | Read for |
|---|---|---|---|
| Human Interface Guidelines | [Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines) | markdown | Platform design, foundations, patterns, components, inputs, accessibility and technology-specific guidance. |

## Updates and release notes

These hubs follow the newest published documentation without pinning an OS,
SDK or Xcode version. Check whether the selected leaf describes a beta release.

| Topic | Entry point | Format | Read for |
|---|---|---|---|
| Framework changes | [Updates](https://developer.apple.com/documentation/updates) | markdown | What's new across frameworks and tools; follow technology updates for API changes and adoption guidance. |
| iOS and iPadOS | [iOS & iPadOS release notes](https://developer.apple.com/documentation/ios-ipados-release-notes) | markdown | SDK changes, fixes, known issues, workarounds and deprecations for each release. |
| macOS | [macOS release notes](https://developer.apple.com/documentation/macos-release-notes) | markdown | Mac SDK changes, fixes, known issues and migration concerns. |
| watchOS | [watchOS release notes](https://developer.apple.com/documentation/watchos-release-notes) | markdown | Watch SDK changes, known issues and fixes. |
| tvOS | [tvOS release notes](https://developer.apple.com/documentation/tvos-release-notes) | markdown | Apple TV SDK changes, known issues and fixes. |
| visionOS | [visionOS release notes](https://developer.apple.com/documentation/visionos-release-notes) | markdown | Spatial computing SDK changes, known issues and fixes. |
| Xcode | [Xcode release notes](https://developer.apple.com/documentation/xcode-release-notes) | markdown | Toolchain changes, SDK contents, compatibility, known issues and fixes. |
| Safari | [Safari release notes](https://developer.apple.com/documentation/safari-release-notes) | markdown | Browser and Web Extension changes, compatibility, fixes and known issues. |
| Announcements | [Latest News](https://developer.apple.com/news/) | html | Developer announcements, policy and App Store deadlines, beta releases. |
| Web platform in Safari | [WebKit](https://webkit.org/blog/) | html | Web features, CSS and JavaScript APIs shipping in each Safari release; Web Inspector and WKWebView-relevant changes. |

## App Store review

| Topic | Entry point | Format | Read for |
|---|---|---|---|
| App Review Guidelines | [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/) | html | Current numbered review rules, exceptions and applicability; retain section anchors in citations. |

## Community troubleshooting

| Topic | Entry point | Format | Read for |
|---|---|---|---|
| Developer reports | [Apple Developer Forums](https://developer.apple.com/forums/) | html | Known problems, workarounds and occasional answers from Apple engineers; one developer's report is not documentation. |

## WWDC

| Topic | Entry point | Format | Read for |
|---|---|---|---|
| Session discovery | [Developer videos](https://developer.apple.com/videos/) | html | Latest WWDC collection, topics and session links; open a session for its transcript, chapters, explanation and documentation resources. |

## Safari Web Extensions

| Topic | Entry point | Format | Read for |
|---|---|---|---|
| Web Extensions | [Safari web extensions](https://developer.apple.com/documentation/safariservices/safari-web-extensions) | markdown | Creating, converting, packaging, browser compatibility, JavaScript APIs, permissions, messaging, testing and distribution; follow the specific task or compatibility article. |

## How to read each source

- **Links inside Apple pages** are root-relative or `doc://<bundle>/...` identifiers. Resolve root-relative paths against `https://developer.apple.com`; for `doc://`, keep the path from `/documentation/`, `/design/` or `/tutorials/`. Keep spelling, overload suffixes, parentheses and underscores exactly; quote URLs in shell commands. The Swift view is the default; ask for the Objective-C variant explicitly when needed.
- **The JSON feed** (`/tutorials/data<path>.json`, lowercase path) has `metadata` (including `platforms`), `abstract`, `primaryContentSections`, `topicSections` and `references`; resolve topic identifiers through `references[id].url`, and read `deprecationSummary` when present. Plain `<page>.json` does not exist.
- **The technology index** lists every framework: iterate `sections[].groups[].technologies[]` for `title`, `tags` and `destination.identifier`, then resolve the identifier through `references` to its `url`. This is the route to frameworks the hubs above do not name (RealityKit, WidgetKit, WatchKit, CarPlay and the rest).
- **Release notes:** open the platform's hub and follow the newest release linked there, checking whether it is a beta; never construct a versioned URL.
- **WWDC:** start at the videos hub, find the session, and read its description, chapters, transcript and linked documentation. If no transcript is published, say so.
- **App Review:** read the whole numbered section and its linked exceptions before summarising.
- Apple publishes no usable `llms.txt` or sitemap; the hubs and the technology index are the way in.

