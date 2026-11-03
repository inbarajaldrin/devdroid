"""Live contracts: Python 3 standard library, curl and public internet only."""

import json
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import unittest
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://learn.microsoft.com"
HOSTS = {"learn.microsoft.com", "raw.githubusercontent.com"}


def representation(url):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "accept"]
    query.append(("accept", "text/markdown"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def fetch(url, accept=None):
    headers = ["--header", "Accept: " + accept] if accept else []
    result = subprocess.run(
        ["curl", "-fLsS", "--connect-timeout", "10", "--max-time", "40",
         "--retry", "2", "--write-out",
         "\n%{http_code}|%{content_type}|%{url_effective}", *headers, "--", url],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise AssertionError(f"{url}: curl {result.returncode}: {result.stderr.decode()}")
    body, separator, trailer = result.stdout.rpartition(b"\n")
    if not separator:
        raise AssertionError(f"{url}: missing response metadata")
    status, mime, final = trailer.decode().split("|", 2)
    if status != "200" or urlsplit(final).hostname not in HOSTS:
        raise AssertionError(f"{url}: unexpected {trailer.decode()}")
    mime = mime.split(";")[0]
    print(f"GET {status} {mime} {len(body)}B {final}", flush=True)
    return body.decode("utf-8"), mime, final


def split_metadata(body):
    if body.startswith("---\n"):
        front, separator, content = body[4:].partition("\n---\n")
        if not separator:
            raise AssertionError("Unterminated Markdown metadata")
        return front, content
    return "", body


def metadata_value(front, key):
    match = re.search(r"^" + re.escape(key) + r":\s*(.+)$", front, re.M)
    if not match:
        raise AssertionError(f"Missing metadata: {key}")
    return match.group(1).strip().strip("\"'")


def markdown(url):
    body, mime, final = fetch(representation(url))
    if mime != "text/markdown":
        raise AssertionError(f"{url}: expected Markdown, got {mime}")
    _, content = split_metadata(body)
    if not re.search(r"^# .+", content, re.M) or len(content.split()) < 80:
        raise AssertionError(f"{url}: missing title or substantive content")
    if re.search(r"<!doctype|<html|^# (?:404|Page Not Found)", content, re.I):
        raise AssertionError(f"{url}: HTML/error instead of documentation")
    return content, final


def links(content, base):
    return [urljoin(base, value) for value in re.findall(r"\]\(([^\s)]+)\)", content)]


class LearnHTML(HTMLParser):
    """Read the main content, not scripts, menus or the surrounding shell."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_main = False
        self.hidden = []
        self.text = []
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name"):
            self.meta[attrs["name"]] = attrs.get("content", "")
        if tag == "main":
            self.in_main = True
        if tag in {"script", "style", "nav", "footer", "svg", "template"}:
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if tag == "main":
            self.in_main = False
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()

    def handle_data(self, data):
        if self.in_main and not self.hidden:
            self.text.append(data)


def html(url):
    body, mime, final = fetch(url)
    if mime != "text/html":
        raise AssertionError(f"{url}: expected HTML, got {mime}")
    parsed = LearnHTML()
    parsed.feed(body)
    if len(" ".join(parsed.text).split()) < 80:
        raise AssertionError(f"{url}: HTML shell or unreadable main content")
    return parsed, final


def catalog_entries():
    source = (ROOT / "catalog.md").read_text()
    entries = re.findall(
        r"^\|[^\n]+\| \[([^\]]+)\]\((https://[^)]+)\) \| (markdown|json) \|",
        source, re.M,
    )
    all_urls = set(re.findall(r"https://[^\s)\]>`]+", source))
    if not entries or {url for _, url, _ in entries} != all_urls:
        raise AssertionError("Every catalog URL must have a typed table row")
    if len(entries) != len(all_urls):
        raise AssertionError("Catalog entry points must not be duplicated")
    return entries


class LiveFetchTests(unittest.TestCase):
    def test_every_catalog_url(self):
        for label, url, kind in catalog_entries():
            with self.subTest(url=url):
                if kind == "markdown":
                    page, _ = html(url)
                    self.assertTrue(label.lower() in " ".join(page.text).lower(), f"HTML lacks topic: {label}")
                    content, _ = markdown(url)
                    self.assertTrue(label.lower() in content.lower(), f"Markdown lacks topic: {label}")
                    self.assertTrue(links(content, url))
                else:
                    body, mime, _ = fetch(url)
                    self.assertEqual(mime, "application/json")
                    results = json.loads(body)["results"]
                    self.assertEqual(len(results), 3)
                    self.assertTrue(all(r["title"] and r["description"] and r["url"] for r in results))
                    matches = [r for r in results if urlsplit(r["url"]).path.endswith("/nf-winuser-registerhotkey")]
                    self.assertTrue(matches, "Search did not find the named Win32 API")
                    content, _ = markdown(matches[0]["url"])
                    self.assertIn("RegisterHotKey(", content)
                    self.assertIn("## Parameters", content)

    def test_hub_to_leaf(self):
        hub = ORIGIN + "/en-us/windows/apps/"
        content, final = markdown(hub)
        leaves = [u for u in links(content, final) if urlsplit(u).path.endswith("/get-started/start-here")]
        self.assertTrue(leaves, "Hub no longer links to its getting-started article")
        leaf, _ = markdown(leaves[0])
        self.assertIn("## Creating your first app", leaf)
        self.assertIn("## Troubleshooting", leaf)

    def test_win32_header_to_declarations(self):
        hub = ORIGIN + "/en-us/windows/win32/api/winuser/"
        content, final = markdown(hub)
        targets = [
            ("nf-winuser-createwindowexw", "HWND CreateWindowExW(", "## Parameters"),
            ("ns-winuser-accel", "typedef struct tagACCEL", "## Members"),
            ("ne-winuser-ar_state", "typedef enum tagAR_STATE", "## Constants"),
            ("nc-winuser-dlgproc", "DLGPROC Dlgproc", "## Parameters"),
        ]
        for suffix, declaration, section in targets:
            with self.subTest(symbol=suffix):
                found = [u for u in links(content, final) if urlsplit(u).path.endswith("/" + suffix)]
                self.assertTrue(found, f"Header has no link for {suffix}")
                leaf, _ = markdown(found[0])
                self.assertTrue(declaration in leaf, f"Missing declaration: {declaration}")
                self.assertIn(section, leaf)
        # Interface references use a different prefix, discovered from their header.
        content, final = markdown(ORIGIN + "/en-us/windows/win32/api/unknwn/")
        found = [u for u in links(content, final) if urlsplit(u).path.endswith("/nn-unknwn-iunknown")]
        self.assertTrue(found)
        leaf, _ = markdown(found[0])
        self.assertIn("## Methods", leaf)
        self.assertIn("QueryInterface", leaf)

    def test_release_hubs_to_current_linked_notes(self):
        cases = [
            ("/en-us/windows/apps/windows-app-sdk/release-channels", "/release-notes/", "release notes"),
            ("/en-us/visualstudio/", "/releases/", "release notes"),
            ("/en-us/windows/whats-new/", "/whats-new-windows-", "What's new"),
        ]
        for path, needle, expected in cases:
            with self.subTest(hub=path):
                content, final = markdown(ORIGIN + path)
                found = [u for u in links(content, final) if needle in urlsplit(u).path]
                self.assertTrue(found, "No published release link found")
                if path == "/en-us/visualstudio/":
                    # This hub lists multiple supported product years, not newest first.
                    found.sort(key=lambda u: int(re.search(r"/releases/(\d{4})/", u).group(1)), reverse=True)
                leaf, leaf_final = markdown(found[0])
                for key, value in parse_qsl(urlsplit(found[0]).query):
                    self.assertEqual(dict(parse_qsl(urlsplit(leaf_final).query))[key], value)
                self.assertIn(expected.lower(), leaf.lower())
                self.assertRegex(leaf, r"(?m)^## .+")

    def test_version_redirect_and_explicit_target(self):
        url = ORIGIN + "/en-us/powershell/module/microsoft.powershell.core/get-command"
        page, final = html(url)
        default = page.meta["default_moniker"]
        self.assertEqual(dict(parse_qsl(urlsplit(final).query))["view"], default)
        target = url + "?view=powershell-5.1#examples"
        body, mime, final = fetch(representation(target))
        self.assertEqual(mime, "text/markdown")
        query = dict(parse_qsl(urlsplit(final).query))
        self.assertEqual(query["view"], "powershell-5.1")
        self.assertEqual(query["accept"], "text/markdown")
        self.assertFalse(urlsplit(final).fragment)
        front, content = split_metadata(body)
        self.assertEqual(metadata_value(front, "config_moniker_range"), "powershell-5.1")
        self.assertRegex(front, r"(?m)^monikers:\n- powershell-5\.1$")
        self.assertIn("reference/5.1/", metadata_value(front, "source_path"))
        self.assertIn("## Syntax", content)
        # A canonical URL is not evidence of which version was actually rendered.
        canonical = metadata_value(front, "canonicalUrl")
        self.assertEqual(dict(parse_qsl(urlsplit(canonical).query))["view"], default)

    def test_html_and_header_representations(self):
        url = ORIGIN + "/en-us/windows/win32/api/winuser/nf-winuser-createwindowexw"
        page, _ = html(url)
        content = " ".join(page.text)
        self.assertIn("HWND CreateWindowExW(", content)
        self.assertIn("Parameters", content)
        self.assertIn("Remarks", content)
        body, mime, _ = fetch(url, accept="text/markdown")
        self.assertEqual(mime, "text/markdown")
        _, content = split_metadata(body)
        self.assertIn("HWND CreateWindowExW(", content)

    def test_discovered_public_source(self):
        cases = [
            ("windows/win32/api/winuser/nf-winuser-createwindowexw", "# CreateWindowExW function", "## -parameters"),
            ("powershell/module/microsoft.powershell.core/get-command", "# Get-Command", "## SYNTAX"),
        ]
        for path, title, section in cases:
            with self.subTest(path=path):
                page, _ = html(ORIGIN + "/en-us/" + path)
                source = page.meta["github_feedback_content_git_url"]
                parts = urlsplit(source)
                self.assertEqual(parts.hostname, "github.com")
                self.assertIn("/blob/", parts.path)
                raw = urlunsplit(("https", "raw.githubusercontent.com", parts.path.replace("/blob/", "/", 1), "", ""))
                body, mime, _ = fetch(raw)
                self.assertEqual(mime, "text/plain")
                self.assertIn(title, body)
                self.assertIn(section, body)
                self.assertGreater(len(body.split()), 100)

    def test_failed_fetch_raises(self):
        url = ORIGIN + "/en-us/windows/apps/__windows_docs_contract_missing__"
        with self.assertRaisesRegex(AssertionError, r"curl \d+:.*404"):
            fetch(url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
