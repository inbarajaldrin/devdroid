# A Mac app from a SwiftPM project

A Mac app does not need an Xcode project. SwiftPM builds the executable; a short script wraps it in the bundle macOS expects, signs it, and for distribution notarizes it.

## What the bundle needs

`MyApp.app/Contents/` holds the executable in `MacOS/`, resources in `Resources/`, embedded frameworks in `Frameworks/`, and an `Info.plist`. The plist carries the app's identity, and macOS reads nothing else to launch it: the bundle identifier, name, executable name, package type `APPL`, the marketing version and build number, the minimum macOS version, and the icon name. `LSUIElement` set to true makes a menu-bar app with no Dock icon.

SwiftPM writes resource bundles next to the built binary, not into the app; copy every `*.bundle` from the build folder into `Contents/Resources/`, or `Bundle.module` finds nothing at run time. A universal build compiles once per architecture and joins the results with `lipo`; check the joined binary's architectures before signing.

## Signing

Ad-hoc signing (`codesign -s -`) runs on your own Mac, but macOS treats every rebuild as a new app, so privacy permissions such as Screen Recording or Accessibility reset each time. A stable identity avoids that: an `Apple Development` certificate if you have one, otherwise a self-signed code-signing certificate (`scripts/setup_dev_signing.sh`). Distribution outside the App Store needs a `Developer ID Application` certificate, the hardened runtime, and a secure timestamp.

## Notarization

Zip the signed app, submit it with `xcrun notarytool submit --wait` using an App Store Connect API key, then staple the ticket to the app with `xcrun stapler staple` so it opens offline. `spctl -a -vv MyApp.app` shows whether Gatekeeper accepts the result. When notarization fails, `xcrun notarytool log <submission-id>` names the file and the reason.

## The scripts

Copy them into the project's `Scripts/` folder; they read `APP_NAME`, `BUNDLE_ID` and the version from the environment or a `version.env` file at the project root.

| Script | Does |
|---|---|
| `package_app.sh [release\|debug]` | Builds with SwiftPM, assembles the `.app`, writes `Info.plist`, copies resources and frameworks, signs |
| `compile_and_run.sh` | Stops a running copy, packages, relaunches, and confirms it stayed up |
| `setup_dev_signing.sh` | Creates a self-signed development certificate in the login keychain |
| `sign-and-notarize.sh` | Signs with Developer ID, notarizes, staples, and zips the release |
