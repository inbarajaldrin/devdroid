---
name: windows-docs
description: Fetch current Microsoft documentation when building for Windows instead of answering from memory. Use for ordinary questions about Windows apps, Win32 APIs, Windows App SDK and WinUI, WPF, WinForms, UWP and WinRT, .NET and C#, C++ and MSVC, PowerShell scripts and cmdlets, MSIX installers, winget manifests, code signing, WSL, drivers and WDK, Visual Studio and MSBuild, API availability, release notes or what's new. Questions such as "how do I add a tray icon?", "why does my XAML binding fail?" or "how do I sign this exe?" qualify even without asking for documentation. Excludes consumer Windows support and Azure, Microsoft 365 and Graph.
---

Windows APIs, SDKs and tools evolve independently; remembered signatures and examples can target a different release. Fetch the current documentation before making API, compatibility or tooling claims, and check it against the user's Windows build, framework and SDK versions, including preview status.

Microsoft Learn serves readable Markdown by adding `accept=text/markdown` to the page's query: `https://learn.microsoft.com/en-us/windows/apps/?accept=text/markdown`. Preserve existing query parameters, especially `view` and `pivots`, and remove the fragment for retrieval. Start at a hub in [catalog.md](catalog.md) and follow its links to the relevant article or API; use the catalog's search endpoint when the path is unknown instead of guessing slugs.

Versioned pages can redirect to a default `view`, which need not be the user's target. Check the response's monikers and availability notes: `canonicalUrl` can name the default even when an older version was requested, and a release page can contain stable, preview and experimental sections together. The catalog explains link resolution and source discovery.

A fetch counts only when the response contains the article's prose, declarations or substantive hub links. A search snippet, successful HEAD, navigation shell or sign-in page is not documentation. If a fetch tool refuses Markdown, use `curl -fLsS -- '<url>'`; Learn's normal HTML also contains readable content, and some pages expose public source links. If no representation can be read, state what could not be verified instead of filling the gap from memory.

Cite the normal Learn page URL with its relevant section and version query, omitting `accept`; search results are discovery, not evidence. [tests/README.md](tests/README.md) explains portable verification. Azure, Microsoft 365 and Graph are outside this skill's scope.
