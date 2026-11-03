#!/usr/bin/env python3
# Reference: https://github.com/cli/cli/blob/trunk/internal/attachments/client.go (gh's --attach upload)
"""Upload a local image or video to GitHub's user-attachments store and print its URL.

A `https://github.com/user-attachments/assets/<id>` URL on its own line in a README renders
inline (a video plays in place); a file committed to the repo does not. This sends the bytes the
way `gh`'s --attach flag does: POST to uploads.github.com/user-attachments/assets for a numeric
repository id, authenticated with the caller's existing `gh` login. The token is read from
`gh auth token` into memory and never written anywhere.

An upload cannot be undone, so the script is idempotent: it writes a receipt beside the file
(URL, sha256, size, repo) and, run again on the same bytes, prints the receipt instead of
uploading a second copy.

    upload_readme_media.py OWNER/REPO path/to/demo.mp4

GitHub's inline limit on a free account is 10 MB per video; re-encode larger files first.
"""
import argparse, datetime, hashlib, json, mimetypes, subprocess, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

UPLOAD_ENDPOINT = "https://uploads.github.com/user-attachments/assets"
ASSET_PREFIX = "https://github.com/user-attachments/assets/"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repo", help="OWNER/REPO the asset is uploaded against")
    parser.add_argument("path", type=Path)
    parser.add_argument("--receipt", type=Path, help="default: <path>.github-attachment.json")
    arguments = parser.parse_args()

    body = arguments.path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    receipt = arguments.receipt or arguments.path.with_name(arguments.path.name + ".github-attachment.json")
    if receipt.exists():
        recorded = json.loads(receipt.read_text())
        if recorded.get("sha256") == digest and recorded.get("repository") == arguments.repo:
            print(json.dumps({**recorded, "already_uploaded": True}))
            return 0
        sys.exit(f"receipt {receipt} names different bytes or repo; move it aside to upload again")

    content_type = mimetypes.guess_type(arguments.path.name)[0] or "application/octet-stream"
    repository_id = _gh("api", f"repos/{arguments.repo}", "--jq", ".id")
    query = urllib.parse.urlencode(
        {"name": arguments.path.name, "content_type": content_type, "repository_id": repository_id})
    request = urllib.request.Request(
        f"{UPLOAD_ENDPOINT}?{query}", data=body, method="POST",
        headers={"Content-Type": "application/octet-stream", "Accept": "application/vnd.github+json",
                 "Authorization": "token " + _gh("auth", "token")})
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=300) as response:
            url = json.load(response).get("url", "")
    except urllib.error.HTTPError as error:
        sys.exit(f"upload refused: HTTP {error.code}: {error.read().decode(errors='replace')[:600]}")
    if not url.startswith(ASSET_PREFIX):
        sys.exit(f"unexpected upload answer: {url!r}")

    recorded = {"url": url, "sha256": digest, "size_bytes": len(body), "repository": arguments.repo,
                "content_type": content_type,
                "uploaded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    receipt.write_text(json.dumps(recorded, indent=2) + "\n")
    print(json.dumps(recorded))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
