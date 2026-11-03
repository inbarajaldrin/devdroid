# Windows documentation entry points

Use the retrieval rule in [SKILL.md](SKILL.md). `markdown` rows give normal
Learn URLs; add the representation query when reading them. `json` rows are
fetched as linked. Follow observed article links rather than constructing slugs.

| Topic | Entry point | Format | Read for |
|---|---|---|---|
| Windows apps | [Windows app development documentation](https://learn.microsoft.com/en-us/windows/apps/) | markdown | Framework choices, getting started, design and desktop features; follow a task to its article. |
| Windows App SDK | [Windows App SDK](https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/) | markdown | Runtime deployment, desktop APIs and release channels; distinguish the SDK from the Windows SDK. |
| WinUI | [WinUI 3](https://learn.microsoft.com/en-us/windows/apps/winui/winui3/) | markdown | XAML UI, controls, setup and migration; follow the task or control article. |
| WinUI API reference | [Windows App SDK namespaces](https://learn.microsoft.com/en-us/windows/windows-app-sdk/api/winrt/) | markdown | Microsoft.UI and related namespaces; follow namespace, type and member links and check their monikers. |
| Win32 | [Build desktop Windows apps using the Win32 API](https://learn.microsoft.com/en-us/windows/win32/) | markdown | Native desktop programming, tutorials and API references. |
| Windowing and input | [Winuser.h header](https://learn.microsoft.com/en-us/windows/win32/api/winuser/) | markdown | Functions, structures, enums and callbacks; follow the exact symbol link including its A/W suffix. |
| Win32 subsystems | [Windows API index](https://learn.microsoft.com/en-us/windows/win32/apiindex/windows-api-list) | markdown | COM, services, security, graphics, shell and other native API families. |
| WPF | [Windows Presentation Foundation documentation](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/) | markdown | XAML, controls, binding and migration; distinguish modern .NET from .NET Framework. |
| WinForms | [Windows Forms documentation](https://learn.microsoft.com/en-us/dotnet/desktop/winforms/) | markdown | Forms, controls, designer, deployment and migration. |
| .NET | [.NET documentation](https://learn.microsoft.com/en-us/dotnet/) | markdown | Runtime, SDK, language and library reference, deployment and supported versions. |
| C# | [C# language documentation](https://learn.microsoft.com/en-us/dotnet/csharp/) | markdown | Language reference, compiler features and version-specific changes. |
| UWP and system WinRT | [Windows Runtime (WinRT) namespaces](https://learn.microsoft.com/en-us/uwp/api/) | markdown | Windows.* system APIs, contracts and availability; distinguish these from Microsoft.UI APIs. |
| C++/WinRT | [C++/WinRT](https://learn.microsoft.com/en-us/windows/apps/develop/cpp-winrt/) | markdown | Projections, authoring, lifetime, interop and XAML integration. |
| C++ and MSVC | [Microsoft C++, C, and Assembler documentation](https://learn.microsoft.com/en-us/cpp/) | markdown | Compiler, language, standard library, build tooling and target toolset monikers. |
| PowerShell | [PowerShell Documentation](https://learn.microsoft.com/en-us/powershell/) | markdown | Language, scripting, module references and supported versions. |
| Cmdlet discovery | [Get-Command](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/get-command) | markdown | Command discovery, parameter sets and examples; preserve a Windows PowerShell or PowerShell version target. |
| MSIX | [MSIX documentation](https://learn.microsoft.com/en-us/windows/msix/) | markdown | Create, package, distribute and update applications. |
| Package signing | [Sign an app package using SignTool](https://learn.microsoft.com/en-us/windows/msix/package/sign-app-package-using-signtool) | markdown | Certificate prerequisites, hash algorithm and signing command. |
| Windows Package Manager | [WinGet](https://learn.microsoft.com/en-us/windows/package-manager/) | markdown | CLI commands, package manifests and submission. |
| Code signing | [Code signing options for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options) | markdown | Executable and package distribution, certificates and reputation; stay within Windows signing guidance. |
| WSL | [Windows Subsystem for Linux Documentation](https://learn.microsoft.com/en-us/windows/wsl/) | markdown | Installation, configuration, filesystems, networking and interoperability. |
| Drivers | [Windows hardware developer documentation](https://learn.microsoft.com/en-us/windows-hardware/drivers/) | markdown | WDK, driver models, development, debugging and hardware certification. |
| Visual Studio | [Visual Studio product family documentation](https://learn.microsoft.com/en-us/visualstudio/) | markdown | IDE, debugging, build tools and current release-note links. |
| Design | [Design Windows apps](https://learn.microsoft.com/en-us/windows/apps/design/) | markdown | Layout, accessibility, navigation and Windows UI guidelines. |
| Developer updates | [What's new](https://learn.microsoft.com/en-us/windows/apps/whats-new/whats-new-for-developers) | markdown | Current SDK and WinUI announcements; follow the relevant release and channel. |
| App SDK release notes | [Windows App SDK release channels](https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/release-channels) | markdown | Stable, preview and experimental release links plus servicing policy; follow a published release link rather than inventing its version slug. |
| Windows SDK release notes | [Windows SDK](https://learn.microsoft.com/en-us/windows/apps/windows-sdk/release-notes) | markdown | SDK build changes, fixes, additions and removals. |
| Windows OS changes | [What's new in Windows](https://learn.microsoft.com/en-us/windows/whats-new/) | markdown | Version-specific Windows changes and release information; follow the release matching the user's build. |
| Symbol lookup | [Learn search](https://learn.microsoft.com/api/search?search=RegisterHotKey%20winuser&locale=en-us&%24top=3) | json | Replace `search` with an encoded symbol and namespace or header; inspect `results[].title`, `url` and `description`, then fetch the relevant page. |

## Reading the sources

Resolve root-relative and article-relative links against the fetched page's
effective URL, keeping its trailing slash. Preserve the link's query when adding
`accept=text/markdown`; do not append `.md`. Learn also accepts the
`Accept: text/markdown` HTTP header in curl, but the query form needs no custom
headers from a fetch tool. A tool that rejects this MIME type can still use curl
or read the article body in normal HTML.

Search is discovery: URL-encode `search`, supply `locale=en-us` and `%24top=3`,
then read the relevant `results[].url`. Results can include unrelated products;
inspect their title, path and description. Do not assume a `scope` parameter
guarantees filtering. Never answer from the snippets.

Bare versioned pages can redirect to a default `view`; explicit versions must
survive retrieval. The response's `monikers`, `config_moniker_range` and API
availability are more useful for checking the selected content than
`canonicalUrl`, which can still point to the default. Read the applicable
release section and its channel: a `pivots=stable` page can contain preview and
experimental sections too. Follow release links from the hubs above.

When source inspection helps, the HTML metadata's
`github_feedback_content_git_url` can identify a public GitHub file. For a
`github.com` file URL containing `/blob/`, change the host to
`raw.githubusercontent.com` and remove `/blob`, preserving the repository,
branch and path. Fetch and inspect it before relying on it. The
`original_content_git_url` can point to a private build system; not all source
links are public, and source can be XML or YAML rather than Markdown. Source
files may contain unresolved includes or version directives; Learn's rendered
article remains the citation and the version-specific reading surface.
