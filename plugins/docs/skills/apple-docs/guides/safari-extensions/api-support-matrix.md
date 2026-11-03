# Safari WebExtension API Support Matrix

What `chrome.*` / `browser.*` namespaces Safari implements as of Safari 16+. Live-fetch the current state from Apple's docs if you're targeting a specific Safari version — this matrix tracks what's been stable across recent versions.

## Namespace-by-namespace

| Namespace | Safari | Chrome | Notes |
|---|---|---|---|
| `runtime` | ✅ | ✅ | Message passing, `onMessage`, `sendMessage`. Identical surface. |
| `tabs` | ✅ | ✅ | `query`, `create`, `update`, `remove`. MV3 removed `tabs.executeScript` (use `scripting.executeScript`). |
| `windows` | ✅ | ✅ | `getCurrent`, `getLastFocused`, `getAll`, `create`. Behavior of `getCurrent` differs from `getLastFocused` — see `known-quirks.md` if writing multi-window code. |
| `scripting` | ✅ | ✅ | `executeScript({ target, func, args, world })`. The MV3 way. Returns Promise; awaits user-supplied async `func`. |
| `storage` | ✅ | ✅ | `local`, `session`, `sync`. Safari's `sync` uses iCloud (per-Apple-ID), Chrome's uses Google account. |
| `action` | ✅ | ✅ | Toolbar button, `setBadgeText`, `setBadgeBackgroundColor`, `setTitle`, `setIcon`. |
| `permissions` | ✅ | ✅ | `request`, `remove`, `getAll`. Use to add hosts at runtime. |
| `alarms` | ✅ | ✅ | But Safari's service worker tear-down is aggressive — alarm callbacks resurrect the worker briefly. |
| `contextMenus` | ✅ | ✅ | Works in MV3. |
| `commands` | ✅ | ✅ | Keyboard shortcuts. |
| `declarativeNetRequest` | ✅ | ✅ | Preferred path for content blockers / redirect rules in MV3. |
| `webNavigation` | ✅ | ✅ | `onCommitted`, `onCompleted` etc. |
| `notifications` | ✅ | ✅ | macOS shows them via the user's Notification Center settings for Safari. |
| `i18n` | ✅ | ✅ | `_locales/<locale>/messages.json` works identically. |
| **`downloads`** | ❌ | ✅ | **Safari does not implement.** Use Blob+anchor-click — see `delivery-patterns.md`. |
| **`bookmarks`** | ❌ | ✅ | Not exposed. |
| **`history`** | ❌ | ✅ | Not exposed. |
| `identity` | ⚠️ | ✅ | Available but Apple-flavoured. `getAuthToken` doesn't work like Chrome's. For OAuth, use a popup window with `tabs.create` against the OAuth URL. |
| `cookies` | ⚠️ | ✅ | Requires explicit per-site user consent in Safari. |
| `webRequest` | ⚠️ | ⚠️ | Limited in Safari; for blockers/redirects prefer `declarativeNetRequest`. Chrome also discourages this for MV3. |
| `proxy` | ❌ | ✅ | Not exposed in Safari. |
| `devtools.*` | ⚠️ | ✅ | Limited DevTools panel support. |
| `printerProvider` | ❌ | ❌ | Not in MV3 spec. |
| `system.cpu` / `system.memory` / `system.storage` | ❌ | ✅ | Not in Safari. |
| `tts` / `ttsEngine` | ❌ | ✅ | Use `SpeechSynthesisUtterance` instead. |
| `desktopCapture` / `tabCapture` | ❌ | ✅ | Not in Safari MV3. |
| `omnibox` | ❌ | ✅ | Safari has no omnibox API. |
| `topSites` | ❌ | ✅ | Not exposed. |
| `idle` | ✅ | ✅ | Works. |
| `power` | ❌ | ✅ | Not exposed. |

## API style differences

- **Promises vs callbacks**: `browser.*` returns promises natively (Safari, Firefox). `chrome.*` returns promises in MV3 but historically used callbacks. Always use `await` and the shim:
  ```js
  const api = (typeof browser !== "undefined") ? browser : chrome;
  ```

- **Async listeners**: `onMessage` listeners that want to call `sendResponse` asynchronously **must return `true`** to keep the channel open. Identical in Safari and Chrome.

- **Service worker, not background page**: MV3 forbids persistent background pages. Worker tears down on idle. Safari is more aggressive than Chrome about this — design handlers as short-lived, persist state in `chrome.storage.session` or `local`.

## Permissions and host_permissions

Safari treats `host_permissions` as **user-grantable** rather than install-time. The first `scripting.executeScript` against a listed host may surface a Safari permission dialog ("Always Allow" / "Allow for One Day" / "Don't Allow"). Users can also revoke later via **Safari → Settings → Websites → Extensions**.

Design implication: handle injection failures gracefully. Show a clear "Permission denied — re-grant in Safari Settings" message rather than a generic error.

## Manifest features

| Feature | Safari | Notes |
|---|---|---|
| `background.service_worker` | ✅ | Required for MV3 |
| `background.scripts` | ❌ in MV3 | MV2-only |
| `background.persistent: true` | ❌ in MV3 | Not allowed |
| `content_scripts[].matches` | ✅ | Standard URL-pattern matching |
| `content_scripts[].run_at: document_start \| document_end \| document_idle` | ✅ | All three supported |
| `content_scripts[].world: ISOLATED \| MAIN` | ✅ | Both supported; default is ISOLATED |
| `web_accessible_resources` | ✅ | Required to expose extension files to page contexts |
| `content_security_policy.extension_pages` | ✅ | Restricts what your popup/options pages can do |
| `host_permissions` | ✅ | Per-host scoping (preferred over `<all_urls>`) |
| `optional_permissions` / `optional_host_permissions` | ✅ | Runtime-grantable via `permissions.request` |
| `action.default_popup` / `default_icon` / `default_title` | ✅ | All work |
| `commands` | ✅ | Define keyboard shortcuts |
| `omnibox` | ❌ | Safari doesn't have an omnibox API |
| `oauth2` | ⚠️ | Manifest field accepted but `identity.getAuthToken` doesn't work the same way |

## Version drift

Safari adds (and occasionally removes) WebExtension support across releases:
- Safari 14: initial Web Extension support (MV2).
- Safari 16: MV3 support.
- Safari 17+: incremental coverage of `scripting`, `declarativeNetRequest`, `storage.session`.

When in doubt about the **current** state of a specific API:
1. Live-fetch `https://developer.apple.com/documentation/safariservices/safari_web_extensions` via the JSON rewrite rule.
2. Check Safari's release notes for the user's Safari version.
3. Test feature-detect at runtime: `if (chrome.downloads) { ... }`.
