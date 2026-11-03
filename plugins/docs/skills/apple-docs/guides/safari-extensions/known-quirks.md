# Safari Web Extension — Known Quirks

Running list of Safari-specific gotchas that aren't documented anywhere on `developer.apple.com`. Each entry: symptom / root cause / fix / first-hit context.

---

## Bundle identifier case-matching (build-breaking)

**Symptom**: `xcodebuild` fails with:
> error: Embedded binary's bundle identifier is not prefixed with the parent app's bundle identifier.
>
> Embedded Binary Bundle Identifier:	com.you.gmailexporter.Extension
> Parent App Bundle Identifier:		com.you.GmailExporter

**Root cause**: `safari-web-extension-converter` derives the parent app's bundle id from `--app-name` (case-preserved), but uses `--bundle-identifier` literally. If their cases don't match, `embeddedBinaryValidationUtility` rejects the build because the extension's bundle id isn't a case-sensitive prefix of the parent app's.

**Fix**: Make `--app-name` exactly equal to the last `.`-segment of `--bundle-identifier`, case included.
- ✅ `--app-name=GmailExporter --bundle-identifier=com.you.GmailExporter`
- ❌ `--app-name=GmailExporter --bundle-identifier=com.you.gmailexporter`

In automation, validate before invoking the converter.

---

## "Allow Unsigned Extensions" resets on every Safari launch

**Symptom**: Extension disappears from **Safari → Settings → Extensions** after quitting and reopening Safari.

**Root cause**: Safari only honours unsigned extensions per-session, as a security measure. The Develop-menu toggle is intentionally non-persistent.

**Fix**: Re-toggle **Safari → Develop → Allow Unsigned Extensions** at the start of every dev session. To skip this permanently, sign the host app with a Mac Developer certificate — local-only, but persists across restarts.

---

## Service worker dies silently mid-operation

**Symptom**: A long operation (multi-image fetch, big scrape) finishes in the worker but the popup gets "Receiving end does not exist" instead of the result.

**Root cause**: Safari's MV3 service worker is more aggressive about idle tear-down than Chrome's. If the worker handler awaits long enough without DOM-side activity, Safari can suspend the worker mid-promise.

**Fix**:
- Front-load slow work into `scripting.executeScript` injected functions — those run inside the tab (worker-independent).
- Send periodic "heartbeat" messages back to the popup for very long operations.
- Persist intermediate state in `chrome.storage.session`.
- Avoid heavy `setTimeout` chains in the worker — they don't keep it alive on Safari.

---

## `chrome.downloads` doesn't exist

**Symptom**: `TypeError: chrome.downloads is undefined`.

**Root cause**: Safari has not implemented the `chrome.downloads` namespace.

**Fix**: Use Blob + anchor-click via `scripting.executeScript` injection — see `delivery-patterns.md`. Works on every site, even with strict CSP.

---

## Popup width misbehaves on first paint

**Symptom**: Popup opens at default narrow width, then snaps wider as content loads.

**Root cause**: Safari uses the `<body>` width on first paint, before stylesheets fully apply.

**Fix**: Set explicit width in `<body>` inline style:
```css
body { width: 260px; }
```
`min-width` alone is ignored for the first measurement pass.

---

## Popup origin is its own `safari-extension://` scheme

**Symptom**: `fetch('https://target.example.com/api')` from `popup.js` gets a CORS error even though manifest has `host_permissions` for that origin.

**Root cause**: The popup runs from `safari-extension://<extension-uuid>/popup.html` — its own origin. CORS preflight applies regardless of `host_permissions`.

**Fix**: Do credentialed/CORS-sensitive fetches inside `scripting.executeScript` injected into the target tab — the request inherits the tab's origin and cookies.

---

## Service worker console isn't where you think

**Symptom**: `console.log` from `background.js` doesn't appear in DevTools.

**Root cause**: Three different consoles, three different DevTools entry points.

**Fix**: Open the right one:
1. **Popup**: right-click the open popup → Inspect Element (popup must be open).
2. **Service worker**: **Safari → Develop → Web Extension Background Pages → \<your extension\>**.
3. **Injected scripts** (via `scripting.executeScript` or `content_scripts`): open the target tab's DevTools (⌘⌥I).

Pages may not appear under Develop → Web Extension Background Pages until the worker has been invoked at least once.

---

## `windows.getCurrent` ≠ "the window the popup was clicked from"

**Symptom**: Extension reads tabs from "the wrong window" — e.g., the user clicked the toolbar button in a private window, but the export contains tabs from the normal window.

**Root cause**: `chrome.windows.getCurrent()` returns the most recently active **normal** window in Safari, which is not always the one that owns the popup.

**Fix**: Use `chrome.windows.getLastFocused({ populate: false })` instead. The popup is owned by the focused window, so `getLastFocused` is the click's true origin across normal/private/multi-monitor cases.

---

## Image fetches fail with "cancelled"

**Symptom**: `fetch()` for image URLs inside `scripting.executeScript` returns ok but `blob()` is tiny / corrupted, console shows "cancelled".

**Root cause**: Some sites issue `Content-Disposition: attachment` or `Cross-Origin-Resource-Policy` headers that confuse fetch in the isolated world. Or Safari kills the request when the originating tab navigates.

**Fix**: Wrap each image fetch in `try/catch` and continue. Partial inlining beats failed export. If you want to report which images failed, `Promise.allSettled` instead of `Promise.all`.

---

## `content_scripts.world: "MAIN"` is supported but rarely needed

**Symptom**: Documentation conflict — some sources say Safari doesn't support MAIN world.

**Root cause**: Safari 16.4+ supports `world: "MAIN"` for `content_scripts` AND `scripting.executeScript`. Older Safari did not. Most extensions don't need MAIN world — they need DOM access, and that works fine in ISOLATED world.

**Fix**: Default to ISOLATED. Only set `world: "MAIN"` if you need to read page-level JS variables (e.g., scraping a SPA that exposes state on `window.__APP_STATE__`).

---

## Extension not appearing in Safari → Settings → Extensions

**Symptom**: Built the host app, but the extension toggle never shows up in Safari's settings.

**Root cause**: Safari discovers extensions via macOS's LaunchServices, which only registers them when the host `.app` runs at least once.

**Fix**: `open ./build/Build/Products/Debug/<AppName>.app` after building. If still missing, try:
```bash
/System/Library/Frameworks/CoreServices.framework/Versions/Current/Frameworks/LaunchServices.framework/Versions/Current/Support/lsregister -f -R -trusted <AppName>.app
```

---

## Multiple copies of the same extension appear

**Symptom**: **Safari → Settings → Extensions** shows two or three entries with the same name.

**Root cause**: macOS LaunchServices indexed multiple copies of the host app — usually from `~/Downloads`, the build folder, and an older copy in `/Applications`.

**Fix**: Quit Safari. Find every copy of the `.app`:
```bash
mdfind "kMDItemDisplayName == '<AppName>.app'"
```
Delete or move all but one. Then:
```bash
/System/Library/Frameworks/CoreServices.framework/Versions/Current/Frameworks/LaunchServices.framework/Versions/Current/Support/lsregister -kill -r -domain local -domain user
```
Then re-run the canonical copy. Safari should now show a single entry.

---

## Add new quirks below this line

(Append as you hit them. Title / symptom / root cause / fix / first-hit project + date.)
