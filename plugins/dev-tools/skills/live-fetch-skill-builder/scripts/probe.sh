#!/usr/bin/env bash
# Measure how a documentation site serves a page, so a docs skill uses the right retrieval rule.
# Usage: probe.sh <page-url>
set -uo pipefail
url="${1:?usage: probe.sh <page-url>}"
url="${url%%#*}"; url="${url%%\?*}"; url="${url%/}"   # probe the page itself: no fragment, query or trailing slash
url="${url%.md}"; url="${url%.json}"                     # accept an already-suffixed URL
origin="$(printf '%s' "$url" | cut -d/ -f1-3)"
ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

fetch() { # name url -> prints "status|type|bytes", body saved to $tmp/name
  local out; out=$(curl -sL -A "$ua" --max-time 20 -o "$tmp/$1" -w '%{http_code}|%{content_type}|%{size_download}' "$2" 2>/dev/null) || out="000|-|0"
  local t="${out#*|}"; t="${t%|*}"; printf '%s|%s|%s\n' "${out%%|*}" "${t%%;*}" "${out##*|}"
}
row() { printf '| %-16s | %-60s | %s |\n' "$1" "$2" "$3"; }

printf '| %-16s | %-60s | %s |\n|%s|%s|%s|\n' "check" "url" "status, type, bytes" "------------------" "--------------------------------------------------------------" "------"
IFS="|" read -r s_llms t_llms b_llms <<<"$(fetch llms "$origin/llms.txt")"; row "llms.txt index" "$origin/llms.txt" "$s_llms $t_llms ${b_llms}B"
IFS="|" read -r s_md t_md b_md <<<"$(fetch md "$url.md")";                   row "markdown page" "$url.md" "$s_md $t_md ${b_md}B"
IFS="|" read -r s_js t_js b_js <<<"$(fetch json "$url.json")";               row "json page" "$url.json" "$s_js $t_js ${b_js}B"
IFS="|" read -r s_ht t_ht b_ht <<<"$(fetch html "$url")";                    row "html page" "$url" "$s_ht $t_ht ${b_ht}B"
IFS="|" read -r s_sm t_sm b_sm <<<"$(fetch sitemap "$origin/sitemap.xml")"
locs=$(grep -o '<loc>' "$tmp/sitemap" 2>/dev/null | wc -l | tr -d ' ');  row "sitemap" "$origin/sitemap.xml" "$s_sm ${locs} entries"

markers=$(grep -oE '__NEXT_DATA__|__NUXT__|__INITIAL_STATE__|__APOLLO_STATE__' "$tmp/html" 2>/dev/null | sort -u | tr '\n' ' ')
text_bytes=$(sed -e 's/<script[^>]*>.*<\/script>//g' -e 's/<[^>]*>//g' "$tmp/html" 2>/dev/null | tr -s '[:space:]' ' ' | wc -c | tr -d ' ')
echo
echo "embedded json markers: ${markers:-none}"
echo "visible text in html: ~${text_bytes}B"

ok() { [[ "$1" == 200 ]]; }
is_llms=false; ok "$s_llms" && [[ "$t_llms" == text/plain || "$t_llms" == text/markdown ]] && head -c 400 "$tmp/llms" | grep -q '^#' && is_llms=true
is_md=false;   ok "$s_md" && [[ "$t_md" == text/markdown || "$t_md" == text/plain || "$t_md" == text/x-markdown ]] && ! head -c 200 "$tmp/md" | grep -qi '<!doctype\|<html' && is_md=true
is_json=false; ok "$s_js" && [[ "$t_js" == application/json ]] && is_json=true

echo
if $is_llms; then echo "verdict: llms.txt index present — start the catalog from it"; fi
if $is_md; then
  echo "verdict: markdown pages — rule: append .md to the page URL"
  if [[ "$b_md" -lt 2500 ]]; then
    echo "warning: this page is very short (${b_md}B) — it may only point elsewhere; read it before using it as an entry point"
  fi
elif $is_json; then echo "verdict: json pages — rule: append .json to the page URL; document the keys"
elif ok "$s_ht" && [[ "$text_bytes" -gt 2000 ]]; then echo "verdict: plain html — fetch the page directly"
elif [[ -n "$markers" ]]; then echo "verdict: json embedded in html — last resort; it breaks when the site's framework updates"
else echo "verdict: no rule found — the page may load its content from a JSON feed; find it in a browser's network panel"; fi
if ! $is_llms && [[ "$locs" -lt 10 ]]; then echo "note: no usable llms.txt or sitemap — build the catalog from the site's navigation"; fi
