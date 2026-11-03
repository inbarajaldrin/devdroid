"""Archive, export and publish the workspace iPhone app. Invoked by app/ota.sh."""
import os
import plistlib
import subprocess
import tempfile
from pathlib import Path

import ota
from runtime import HOME, Refused, locked, read_json, write_text


def build(root=HOME, *, run=subprocess.run):
    root = Path(root).expanduser().resolve()
    settings = read_json(root / "settings.json", {})
    team, prefix = settings.get("apple_team_id"), settings.get("bundle_id_prefix")
    if not team or not prefix:
        raise Refused("APPLE_SIGNING", "Save apple_team_id and bundle_id_prefix in workspace settings.json.")
    base = settings.get("ota_base") or ota.FUNNEL_BASE
    ota.base_url("validate", base)
    env = dict(os.environ, APPLE_TEAM_ID=team, BUNDLE_ID_PREFIX=prefix)
    with locked(root / ".phone-build", blocking=False), tempfile.TemporaryDirectory(prefix="career-build-") as scratch:
        work = Path(scratch)
        directory = root / "data/ota"
        directory.mkdir(parents=True, exist_ok=True)
        number = directory / "build_number"
        build_number = max(int(number.read_text()) if number.exists() else 0,
                           int((ota.info(directory) or {}).get("build", 0))) + 1
        # Reserve before building; failed builds never reuse a number.
        write_text(number, str(build_number) + "\n")
        def command(argv):
            run(argv, cwd=root / "app", env=env, check=True)
        command(["xcodegen", "generate", "--spec", "project.yml"])
        archive = work / "Career.xcarchive"
        command(["xcodebuild", "-project", "Career.xcodeproj", "-scheme", "Career",
                 "-configuration", "Release", "-destination", "generic/platform=iOS",
                 "-derivedDataPath", str(work / "dd"), "-archivePath", str(archive),
                 "-allowProvisioningUpdates", f"CURRENT_PROJECT_VERSION={build_number}", "archive", "-quiet"])
        options = work / "ExportOptions.plist"
        options.write_bytes(plistlib.dumps({"method": "debugging", "teamID": team,
                                           "signingStyle": "automatic", "thinning": "<none>"}))
        export = work / "export"
        command(["xcodebuild", "-exportArchive", "-archivePath", str(archive),
                 "-exportPath", str(export), "-exportOptionsPlist", str(options),
                 "-allowProvisioningUpdates", "-quiet"])
        ipas = list(export.glob("*.ipa"))
        if len(ipas) != 1:
            raise Refused("IPA_EXPORT", "Expected exactly one exported .ipa.")
        metadata = plistlib.loads((archive / "Products/Applications/Career.app/Info.plist").read_bytes())
        if metadata["CFBundleIdentifier"] != prefix + ".careerapp":
            raise Refused("BUNDLE_ID", "Archive bundle differs from workspace settings.")
        with locked(root / ".phone-deploy", blocking=False):
            info = ota.publish(ipas[0], metadata["CFBundleShortVersionString"], str(build_number),
                               metadata["CFBundleIdentifier"], directory)
        token = ota.load_or_create_token(root / ".private/ota_token")
        return {"build": info, "url": ota.base_url(token, base)}


if __name__ == "__main__":
    import json
    print(json.dumps(build(), indent=2))
