"""Live contract checks: Python 3 standard library, curl, public internet only."""

import json
from pathlib import Path
import re
import subprocess
import unittest
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://developer.apple.com"
HOSTS = {"developer.apple.com", "docs.swift.org", "download.swift.org", "webkit.org", "raw.githubusercontent.com"}


def representation(url):
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if path.lower().startswith(("/documentation/", "/design/human-interface-guidelines")):
        if not path.endswith(".md"):
            path += ".md"
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return url


def fetch(url):
    result = subprocess.run(
        [
            "curl", "-fLsS", "--connect-timeout", "10", "--max-time", "30",
            "--retry", "2", "-A", "Mozilla/5.0", "--write-out",
            "\n%{http_code}|%{content_type}|%{url_effective}", "--", url,
        ],
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
    print(f"GET {status} {mime.split(';')[0]} {len(body)}B {final}", flush=True)
    return body.decode("utf-8"), mime.split(";")[0]


def markdown(url):
    body, mime = fetch(representation(url))
    if mime not in {"text/markdown", "text/plain", "text/x-markdown"}:
        raise AssertionError(f"{url}: expected Markdown, got {mime}")
    content = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
    if not re.search(r"^# .+", content, re.M) or len(content.split()) < 80:
        raise AssertionError(f"{url}: missing title or substantive content")
    if re.search(r"<!doctype|<html|^# (?:404|Page Not Found)", content, re.I):
        raise AssertionError(f"{url}: HTML/error instead of documentation")
    return content


def md_links(content):
    # These tests select article paths without parentheses, not overloaded symbols.
    return re.findall(r"\]\((/[^\s)]+)\)", content)


class ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.text = []
        self.links = []
        self.title = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag in {"script", "style", "svg", "template", "head", "nav", "footer"}:
            self.hidden.append(tag)
        if tag == "a" and not self.hidden:
            href = dict(attrs).get("href")
            if href:
                self.links.append(urljoin(ORIGIN, href))

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if not self.hidden:
            self.text.append(data)


def html(url):
    body, mime = fetch(url)
    if mime != "text/html":
        raise AssertionError(f"{url}: expected HTML, got {mime}")
    parsed = ReadableHTML()
    parsed.feed(body)
    text = " ".join(" ".join(parsed.text).split())
    if len(text.split()) < 100:
        raise AssertionError(f"{url}: HTML shell or unreadable page")
    return text + " " + " ".join(parsed.title), parsed.links


def catalog_entries():
    source = (ROOT / "catalog.md").read_text()
    entries = re.findall(
        r"^\|[^\n]+\| \[([^\]]+)\]\((https://[^)]+)\) \| (markdown|html|json) \|",
        source, re.M,
    )
    all_urls = set(re.findall(r"\]\((https://[^)]+)\)", source))  # linked URLs; backticked patterns in notes are not entries
    if not entries or {url for _, url, _ in entries} != all_urls:
        raise AssertionError("Every catalog URL must be covered by a typed table row")
    return entries


class LiveFetchTests(unittest.TestCase):
    def test_markdown_rule_and_deep_navigation(self):
        cases = [
            ("/documentation/swiftui", "/documentation/swiftui/view", "# View"),
            ("/design/human-interface-guidelines", "/design/human-interface-guidelines/", "##"),
            ("/documentation/ios-ipados-release-notes", "/documentation/ios-ipados-release-notes/", "Release Notes"),
            ("/documentation/safariservices/safari-web-extensions", "/documentation/safariservices/packaging-", "Safari"),
        ]
        for hub, prefix, expected in cases:
            with self.subTest(hub=hub):
                content = markdown(ORIGIN + hub)
                links = [link for link in md_links(content) if link.lower().startswith(prefix)]
                self.assertTrue(links, f"No relevant deep link in {hub}")
                leaf = ORIGIN + links[0]
                self.assertIn(expected.lower(), markdown(leaf).lower())

    def test_every_catalog_url_and_its_readable_representation(self):
        entries = catalog_entries()
        self.assertEqual({kind for _, _, kind in entries}, {"markdown", "html", "json"})
        for label, url, kind in entries:
            with self.subTest(url=url):
                if kind == "markdown":
                    fetch(url)  # Check the canonical URL too, even if it is a JS shell.
                    content = markdown(url)
                    self.assertRegex(content, re.compile(r"^# " + re.escape(label) + r"$", re.I | re.M))
                elif kind == "html":
                    text, links = html(url)
                    self.assertTrue(links)
                    self.assertIn("WWDC" if "/videos/" in url else label, text)
                elif "swift-evolution" in url:
                    body, mime = fetch(url)
                    self.assertIn(mime, {"application/json", "application/octet-stream"})
                    proposals = json.loads(body)["proposals"]
                    self.assertGreater(len(proposals), 100)
                    latest = proposals[-1]
                    self.assertTrue(latest["id"].startswith("SE-") and latest["title"] and latest["status"]["state"])
                    text, _ = fetch("https://raw.githubusercontent.com/swiftlang/swift-evolution/main/proposals/" + latest["link"])
                    self.assertIn("# " + latest["title"], text)
                elif "swift-book" in url:
                    body, mime = fetch(url)
                    self.assertEqual(mime, "application/json")
                    data = json.loads(body)
                    self.assertTrue(data["metadata"]["title"].startswith("The Swift Programming Language"))
                    chapters = [ref for ref in data["references"].values() if ref.get("url", "").startswith("/documentation/the-swift-programming-language/")]
                    self.assertTrue(chapters)
                    chapter, _ = fetch("https://docs.swift.org/swift-book/data" + chapters[0]["url"] + ".json")
                    self.assertTrue(json.loads(chapter)["primaryContentSections"])
                else:
                    body, mime = fetch(url)
                    self.assertEqual(mime, "application/json")
                    data = json.loads(body)
                    self.assertEqual(data["metadata"]["title"], "Technologies")
                    technologies = [
                        tech for section in data["sections"]
                        for group in section.get("groups", [])
                        for tech in group.get("technologies", [])
                    ]
                    self.assertTrue(technologies)
                    destination = technologies[0]["destination"]["identifier"]
                    path = data["references"][destination]["url"]
                    self.assertTrue(path.startswith("/documentation/"))
                    markdown(urljoin(ORIGIN, path))

    def test_docc_fallback_and_reference_resolution(self):
        for path in ["/documentation/swiftui", "/design/human-interface-guidelines"]:
            with self.subTest(path=path):
                body, mime = fetch(ORIGIN + "/tutorials/data" + path + ".json")
                self.assertEqual(mime, "application/json")
                data = json.loads(body)
                self.assertTrue(data["metadata"]["title"])
                self.assertTrue(data["abstract"])
                self.assertTrue(data["primaryContentSections"])
                identifier = data["topicSections"][0]["identifiers"][0]
                self.assertTrue(data["references"][identifier]["url"].startswith("/"))

    def test_html_review_and_discovered_wwdc_transcript(self):
        review, _ = html(ORIGIN + "/app-store/review/guidelines/")
        self.assertIn("App Review Guidelines", review)
        self.assertIn("1. Safety", review)
        self.assertIn("5. Legal", review)
        _, links = html(ORIGIN + "/videos/")
        sessions = [url for url in links if "/videos/play/wwdc" in urlsplit(url).path]
        self.assertTrue(sessions, "No WWDC sessions discovered from the current hub")
        session, _ = html(sessions[0])
        self.assertIn("Transcript", session)
        # A tab label alone is not proof that a transcript was published.
        raw, _ = fetch(sessions[0])
        transcript = re.search(r'<section\b[^>]*id="transcript-content"[^>]*>(.*?)</section>', raw, re.S)
        self.assertIsNotNone(transcript, "Session has no transcript container")
        parsed = ReadableHTML()
        parsed.feed(transcript.group(1))
        self.assertGreater(len(" ".join(parsed.text).split()), 100)

    def test_local_guide_routes_exist(self):
        skill = (ROOT / "SKILL.md").read_text()
        for path in [
            "catalog.md", "guides/safari-extensions/_index.md",
            "guides/mac-app-from-swiftpm.md", "scripts/", "tests/README.md",
        ]:
            with self.subTest(path=path):
                self.assertIn(f"]({path})", skill)
                self.assertTrue((ROOT / path).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
