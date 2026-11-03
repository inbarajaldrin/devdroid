# `safari-web-extension-converter` — Walkthrough

The Xcode-bundled CLI that wraps a WebExtension folder in a macOS or iOS app host. As of Xcode 15.4+ the binary is also aliased as `safari-web-extension-packager` — both names invoke the same tool.

## Locating the tool

```bash
xcrun --find safari-web-extension-converter
# or
xcrun --find safari-web-extension-packager
```

If neither exists, open Xcode at least once or run:
```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
```

## Full flag list

```
xcrun safari-web-extension-converter [options] <path-to-web-extension>

  --project-location    Where to put the generated Xcode project (default: cwd).
  --rebuild-project     Path to an existing Xcode project to rebuild with new options/platforms.
  --app-name            App display name. Also seeds the parent bundle identifier.
  --bundle-identifier   The bundle identifier for the generated app.
  --swift               Generate Swift host (default in recent versions).
  --objc                Generate Objective-C host.
  --ios-only            iOS-only project.
  --macos-only          macOS-only project.
  --copy-resources      Copy extension/ files into the Xcode project's Resources/ folder.
                        Without this, the project REFERENCES the original — edits propagate
                        but the Xcode tree becomes fragile to moves.
  --no-open             Don't open the project in Xcode after generation.
  --no-prompt           Don't show the "ready to open" confirmation prompt.
  --force               Overwrite if the output directory exists.
  --help                Print help.
```

## Recommended baseline invocation

```bash
xcrun safari-web-extension-converter \
  --project-location . \
  --app-name "MyExtension" \
  --bundle-identifier "com.you.MyExtension" \
  --swift \
  --macos-only \
  --copy-resources \
  --no-open --no-prompt --force \
  ./extension
```

Why each flag matters:

- **`--swift`**: forward-compatible; ObjC host is legacy.
- **`--macos-only`**: keeps the project small. Add iOS later via `--rebuild-project` if needed.
- **`--copy-resources`**: makes `extension/` the editable source of truth. The Xcode project's `Resources/` becomes a build-time copy. Without this, the project's file references can break when you move the extension folder.
- **`--no-open --no-prompt --force`**: scriptable. The default is interactive (opens Xcode, asks for confirmation) which is fine for one-off runs but blocks automation.

## The Bundle Identifier Case Rule

**This is the #1 build-breaking quirk.** The converter:

1. Derives the **parent app's** bundle id by appending `--app-name` (case-preserved) to the team prefix in the bundle id.
2. Uses `--bundle-identifier` literally for the **extension's** bundle id.

If `--app-name` and the last segment of `--bundle-identifier` differ in case, `embeddedBinaryValidationUtility` rejects the build:

> error: Embedded binary's bundle identifier is not prefixed with the parent app's bundle identifier.
>
> Embedded Binary Bundle Identifier:	com.you.gmailexporter.Extension
> Parent App Bundle Identifier:		com.you.GmailExporter

**Rule of thumb:** if `--app-name=Foo` then `--bundle-identifier=com.you.Foo`, exact case. Always. It has been a sharp edge since the converter's introduction and Apple has not fixed it.

If you're writing a wrapper script, validate before invoking:
```bash
validate_case_match() {
  local app_name="$1" bundle_id="$2"
  local last_seg="${bundle_id##*.}"
  [[ "$last_seg" == "$app_name" ]] || {
    echo "ERR: bundle id last segment '$last_seg' must equal app-name '$app_name' (case sensitive)" >&2
    return 1
  }
}
```

## What gets generated

```
<project-location>/<app-name>/
├── <app-name>.xcodeproj
│   ├── project.pbxproj
│   └── project.xcworkspace/
├── <app-name>/                       ← Host app target
│   ├── AppDelegate.swift
│   ├── ViewController.swift          ← Stub: "Click to enable in Safari"
│   ├── Info.plist
│   ├── Resources/
│   │   ├── Base.lproj/Main.storyboard
│   │   ├── Icon.png                  ← Generic; overwrite for your own icon
│   │   ├── Script.js / Style.css     ← Tiny embedded landing-page assets
│   └── Assets.xcassets/
│       ├── AppIcon.appiconset/
│       ├── LargeIcon.imageset/
│       └── AccentColor.colorset/
└── <app-name> Extension/             ← Safari Web Extension target
    ├── SafariWebExtensionHandler.swift   ← Native bridge (rarely edited)
    ├── Info.plist                        ← Extension manifest (bundle id, principal class)
    └── Resources/                        ← Copy of your extension/ folder
        ├── manifest.json
        ├── background.js
        ├── popup.html / popup.js
        └── images/
```

The host app's `ViewController.swift` displays a one-line "Open Safari and enable the extension" prompt — you can ignore it for personal-use builds, or replace the storyboard with a SwiftUI scene for a polished settings UI.

## Build

```bash
xcodebuild \
  -project "<app-name>.xcodeproj" \
  -scheme <app-name> \
  -destination 'platform=macOS' \
  -configuration Debug \
  -derivedDataPath ./build \
  build
```

Output: `build/Build/Products/Debug/<app-name>.app` and `<app-name> Extension.appex` embedded inside.

`open build/Build/Products/Debug/<app-name>.app` once so LaunchServices registers the appex.

## Re-sync after editing `extension/`

Because `--copy-resources` was used, `extension/` is the editable source but the Xcode project carries a static copy. After edits:

```bash
rsync -a --delete extension/ "<app-name>/<app-name> Extension/Resources/"
xcodebuild ...    # rebuild
```

Or use the converter's idempotent rebuild flow:

```bash
xcrun safari-web-extension-converter \
  --rebuild-project ./<app-name> \
  --copy-resources --no-open --no-prompt --force \
  ./extension
```

`--rebuild-project` preserves any project-file edits (target settings, additional source files) while refreshing the Resources folder.

## Adding iOS later

Start macOS-only. When iOS is needed:

```bash
xcrun safari-web-extension-converter \
  --rebuild-project ./<app-name> \
  --copy-resources \
  ./extension
# (omit --macos-only and --ios-only; the converter adds the missing target)
```

Be aware: iOS Safari extension distribution requires a signed build through App Store Connect. No dev-time "Allow Unsigned" toggle on iOS.

## Common automation patterns

- **Init + audit script**: validate `--app-name` / `--bundle-identifier` case match before calling the converter; stamp placeholders in `manifest.json`, `background.js`, `popup.html` from a Templates folder; generate icons via PIL.
- **Sync wrapper**: `rsync` extension/ → Xcode Resources/ in one command before `xcodebuild`.
- **CI build**: `xcodebuild` with `-quiet`, plus `codesign --verify --deep --strict --verbose=2` to catch the bundle-id mismatch before validation.

## When the converter is not the right tool

- **Native Safari App Extension** (the older `SFSafariApplicationDelegate`-based pattern) — different converter / different SDK; mostly deprecated and not covered here.
- **Embedded WKWebView with extensions injected by an app** — that's `WKContentWorld` / `WKUserScript`, also a different surface.
- **Cross-browser ship with no Safari host needed** — if you only ship to Chrome/Firefox, skip the converter entirely; deploy the `extension/` folder directly via each browser's web store.
