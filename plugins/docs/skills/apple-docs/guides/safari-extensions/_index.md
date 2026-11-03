# Safari Web Extensions — Router

Synthesized guide tree for building Safari Web Extensions (Manifest V3) wrapped in Xcode app hosts via `safari-web-extension-converter`. Covers the non-obvious quirks that aren't documented in any single place on `developer.apple.com`.

**Use this guide tree when** the user wants to build any of:
- A Safari Web Extension (toolbar button, content script, popup, background service worker)
- A Chrome/Firefox WebExtension that also needs to ship to Safari
- A macOS host app whose primary purpose is bundling a Web Extension

**Don't use this tree when** the user wants to build a native Safari App Extension (`SFSafariApplicationDelegate`, the older AppKit-based pattern) — those are different, mostly deprecated, and need different docs. If unsure, ask.

## Which file to read

| User's question | Read |
|---|---|
| "What is a Safari Web Extension, and how does it differ from a Chrome extension?" | `overview.md` |
| "How do I scaffold one from scratch?" | `overview.md` then `converter-walkthrough.md` |
| "What does `xcrun safari-web-extension-converter` do, and what flags?" | `converter-walkthrough.md` |
| "Why does my build fail with 'Embedded binary's bundle identifier is not prefixed'?" | `known-quirks.md` § Bundle identifier case-matching |
| "Why doesn't `chrome.downloads` work in Safari?" | `delivery-patterns.md` + `api-support-matrix.md` |
| "How do I trigger a file download from a content script?" | `delivery-patterns.md` |
| "Does Safari support `chrome.<NAMESPACE>`?" | `api-support-matrix.md` |
| "Why does my service worker keep dying mid-operation?" | `known-quirks.md` § Service worker tear-down |
| "My extension disappeared after Safari restarted" | `known-quirks.md` § Allow Unsigned Extensions resets |
| "How do I see service worker logs?" | `known-quirks.md` § Service worker console lives elsewhere |
| "Distribution: dev / signed / App Store?" | `overview.md` § Distribution |

## Files in this tree

- `_index.md` — this router
- `overview.md` — what Safari Web Extensions are, MV3 vs MV2, build flow at a glance, distribution
- `converter-walkthrough.md` — `safari-web-extension-converter` command, flags, the case rule, rebuild flow
- `api-support-matrix.md` — what `chrome.*` / `browser.*` namespaces work in Safari, what doesn't
- `delivery-patterns.md` — how to get files to disk without `chrome.downloads`; CSP bypass via content scripts
- `known-quirks.md` — running list of build-breaking + behavior-changing quirks, each with symptom / root cause / fix

## Relationship to live-fetch

Apple's Safari Web Extensions documentation is fetchable with the retrieval rule in `SKILL.md`, but the synthesized content here is more complete than what Apple publishes — Apple's docs cover the happy path; the quirks are folk knowledge accumulated from actually shipping extensions. Live-fetch Apple's docs when you need the **current** state of a specific API namespace (Safari may add/remove support between versions); read this tree for everything else.
