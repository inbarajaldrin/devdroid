# Delivery Patterns — Getting Files To Disk in Safari

Safari MV3 has no `chrome.downloads`. This is the single biggest API gap for any extension whose job is "scrape something → save a file". Three workarounds in descending order of preference.

## 1. Blob + anchor-click via `scripting.executeScript` (preferred)

Inject a tiny function into the active tab. The function creates a Blob URL, appends a hidden `<a download>`, clicks it, and cleans up. The browser shows its native save dialog.

```js
// In your background service worker, after you've prepared `content` and `filename`:
await chrome.scripting.executeScript({
  target: { tabId: tab.id },
  func: triggerDownload,
  args: [content, filename],
});

// The injected function — must be self-contained, no closures.
function triggerDownload(content, filename) {
  const blob = new Blob([content], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 1000);
}
```

**Why this works on every site, including ones with strict CSP** (Gmail, Twitter, banks):
- Content scripts (and `scripting.executeScript`-injected functions) run in an **isolated world** — separate JS heap, **shared DOM**.
- The page's CSP does *not* apply to DOM mutations made by the isolated world. It's treated as browser-privileged code.
- `Blob` URLs bypass `connect-src`, `script-src`, and friends.

The pattern is the load-bearing workaround for Safari's missing `chrome.downloads`. Used by every download-emitting extension I've seen ship to Safari.

## 2. `tabs.create({ url: dataUrl })` (fallback)

When the active tab is a non-injectable URL (`safari-extension://...`, `about:blank`, the Safari start page), you can't inject. Fall back to opening the content as a `data:` URL in a new tab — the user uses ⌘S to save.

```js
function isInjectable(url) {
  if (!url) return false;
  return /^https?:|^file:/i.test(url);
}

async function deliver(content, filename, activeTab) {
  if (activeTab?.id != null && isInjectable(activeTab.url)) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId: activeTab.id },
        func: triggerDownload,
        args: [content, filename],
      });
      return;
    } catch (e) {
      console.warn("inject failed, falling back to data: tab", e);
    }
  }

  const base64 = btoa(unescape(encodeURIComponent(content)));    // utf-8 safe
  const dataUrl = "data:text/html;charset=utf-8;base64," + base64;
  await chrome.tabs.create({ url: dataUrl });
}
```

UX cost: an extra tab opens and the user has to ⌘S manually. Works on every page state.

## 3. Native `chrome.downloads` (NOT available in Safari)

```js
// DON'T do this in Safari-targeting code — chrome.downloads is undefined.
chrome.downloads.download({ url, filename });
```

For cross-browser extensions that also target Chrome/Firefox, feature-detect and prefer this when available:

```js
async function deliver(content, filename, activeTab) {
  if (typeof chrome.downloads !== "undefined") {
    // Chrome / Firefox path
    const blob = new Blob([content], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    await chrome.downloads.download({ url, filename });
    return;
  }
  // Safari path — fall through to (1) or (2)
  await safariDeliver(content, filename, activeTab);
}
```

## Tradeoffs

| Pattern | Reliability | UX | Constraints |
|---|---|---|---|
| Blob + anchor-click via inject | High | Native save dialog | Active tab must be `http://` / `https://` / `file://` |
| `tabs.create({ url: dataUrl })` | Always | User has to ⌘S | Works on every tab state including `about:blank` |
| `chrome.downloads` | High (Chrome/FF only) | Programmatic — no dialog if user has set "always ask" off | Not on Safari |

**Practical recipe**: try (1) first; on failure or non-injectable tab, fall back to (2). Skip (3) entirely if Safari is in scope.

## Image / asset inlining for portable HTML

If you're exporting HTML that should remain viewable offline forever (for example, an email export), inline every image as a base64 data URI inside the same injected function:

```js
async function inlineImages(root) {
  const imgs = Array.from(root.querySelectorAll("img"));
  await Promise.all(imgs.map(async (img) => {
    const src = img.getAttribute("src");
    if (!src || src.startsWith("data:")) return;
    try {
      const res = await fetch(src, { credentials: "include" });
      if (!res.ok) return;
      const blob = await res.blob();
      const dataUri = await new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(r.result);
        r.onerror = reject;
        r.readAsDataURL(blob);
      });
      img.setAttribute("src", dataUri);
      img.removeAttribute("srcset");    // important — otherwise browser may re-fetch responsive variants
    } catch (_e) {
      // Leave original URL; partial export beats no export.
    }
  }));
}
```

Run this **inside** `scripting.executeScript` — fetches inherit the tab's origin and cookies, so cookie-gated CDN images (Gmail's `ci.googleusercontent.com`, etc.) authenticate automatically. Running the same code from the service worker would lose the page's auth context.

## Filename hygiene

Subjects and titles arrive with `/`, `:`, control characters, and whatever else the user typed. Sanitize before using as a filename:

```js
const slug = (title || "default")
  .replace(/[\x00-\x1f\x7f]/g, "")          // strip control chars
  .replace(/[\\/:*?"<>|]/g, "-")             // Windows-forbidden chars (good cross-platform default)
  .replace(/\s+/g, "_")                       // whitespace → underscore
  .replace(/_+/g, "_")
  .replace(/^[_\-]+|[_\-]+$/g, "")
  .slice(0, 80) || "default";
const stamp = new Date().toISOString().replace(/[:.]/g, "-").replace(/-\d{3}Z$/, "Z");
const filename = `${slug}-${stamp}.html`;
```

Don't strip Unicode — modern OSes handle it fine. Don't lowercase — the user typed what they typed.

## Failure modes to handle

- **"Failed to load resource: cancelled"** during image inlining → catch and skip; partial export is fine.
- **Tab navigates during long export** → fetches get cancelled mid-stream. `Promise.allSettled` instead of `Promise.all` if you want to report partial failures.
- **Service worker dies mid-export** (long-running) → keep the heavy work inside the injected function (lives in the tab), not the worker.
- **User denies download in Safari dialog** → no error path; the anchor click just doesn't save. No way to detect from JS.
