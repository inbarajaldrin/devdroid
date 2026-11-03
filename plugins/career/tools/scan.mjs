#!/usr/bin/env node
// Public company-feed scanner. External notices: ../THIRD_PARTY_NOTICES.md.
import { readFileSync, writeFileSync, appendFileSync, existsSync, mkdirSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
import { createHash } from 'crypto';
import { homedir } from 'os';
import { publicFetch } from './public-feed.mjs';
import { payFields } from './pay.mjs';
import yaml from 'js-yaml';

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE = resolve(process.env.CAREER_HOME || resolve(homedir(), '.devdroid/career')); 
const COMPANIES_PATH = resolve(WORKSPACE, 'companies.yml');
const DATA_DIR = resolve(WORKSPACE, 'data');
const SCAN_HISTORY_PATH = resolve(DATA_DIR, 'scan-history.tsv');
const PIPELINE_PATH = resolve(DATA_DIR, 'pipeline.md');
const TRACKER_PATH = resolve(WORKSPACE, 'data', 'applications', 'TRACKER.md');

const CONCURRENCY = 8;
const FETCH_TIMEOUT_MS = 15_000;
const WORKDAY_PAGE = 20;
const WORKDAY_MAX_PAGES = 15;

// ── ATS detection ───────────────────────────────────────────────────
// A company resolves to {type, url} (or {type:'workday', ...}) from, in order:
// an explicit `board:` slug + `ats:`, or a slug parsed out of careers_url.

function detectApi(c) {
  const url = c.careers_url || '';
  const ats = (c.ats || '').toLowerCase();

  if (ats === 'workday' && c.workday?.host && c.workday?.tenant && c.workday?.site) {
    return { type: 'workday', ...c.workday };
  }

  if (ats === 'custom' && c.feed?.url) return {type: 'custom', ...c.feed};

  const slug =
    c.board ||
    url.match(/job-boards(?:\.eu)?\.greenhouse\.io\/([^/?#]+)/)?.[1] ||
    url.match(/boards\.greenhouse\.io\/([^/?#]+)/)?.[1] ||
    url.match(/jobs\.ashbyhq\.com\/([^/?#]+)/)?.[1] ||
    url.match(/jobs\.lever\.co\/([^/?#]+)/)?.[1];
  if (!slug) return null;

  const type =
    ats ||
    (url.includes('greenhouse.io') ? 'greenhouse' : url.includes('ashbyhq.com') ? 'ashby' : url.includes('lever.co') ? 'lever' : '');

  switch (type) {
    case 'greenhouse': return { type, url: `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs?content=true&pay_transparency=true` };
    case 'ashby':      return { type, url: `https://api.ashbyhq.com/posting-api/job-board/${slug}?includeCompensation=true` };
    case 'lever':      return { type, url: `https://api.lever.co/v0/postings/${slug}?mode=json` };
    case 'gem':        return { type, url: `https://api.gem.com/job_board/v0/${slug}/job_posts/` };
    default:           return null;
  }
}

// ── Parsers → {title, url, location} ────────────────────────────────

export function countryFromLocation(location) {
  const countries=[['US', /\b(?:United States|USA|U\.S\.A\.)\b/i],['GB', /\b(?:United Kingdom|UK)\b/i],['DE', /\bGermany\b/i],['CA', /\bCanada\b/i],['IN', /\bIndia\b/i]];
  const matches=countries.filter(([,pattern])=>pattern.test(location || ''));
  return matches.length===1 ? matches[0][0] : null;
}

// Board APIs return the description as HTML (Greenhouse escapes it once more); records keep readable text.
export function htmlToText(html) {
  if (!html) return null;
  const entities = s => s.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (_, e) =>
    e[0] === '#' ? String.fromCodePoint(e[1].toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : +e.slice(1))
      : ({amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' '})[e.toLowerCase()]);
  let text = /&lt;[a-z/]/i.test(html) ? entities(html) : html;
  text = text.replace(/<(br|\/p|\/div|\/li|\/h[1-6])[^>]*>/gi, '\n').replace(/<li[^>]*>/gi, '- ').replace(/<[^>]+>/g, '');
  return entities(text).replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n').trim() || null;
}

// Greenhouse states pay ranges in a separate field, in cents; the description often omits them.
export function greenhousePay(ranges) {
  const usd = (ranges || []).filter(r => r && r.currency_type === 'USD' && r.min_cents && r.max_cents);
  const dollars = cents => '$' + Math.round(cents / 100).toLocaleString('en-US');
  return usd.length ? usd.map(r => `${dollars(r.min_cents)} - ${dollars(r.max_cents)} USD ${r.title || 'salary'}`).join('; ') : null;
}

const PARSERS = {
  greenhouse: (json) => (json.jobs || []).map(j => ({
    title: j.title || '', url: j.absolute_url || '', location: j.location?.name || '', description: htmlToText(j.content || j.description),
    posted_on: (j.first_published || '').slice(0, 10) || null, updated_on: (j.updated_at || '').slice(0, 10) || null,
    pay: greenhousePay(j.pay_input_ranges),
  })),
  ashby: (json) => (json.jobs || []).map(j => ({
    title: j.title || '', url: j.jobUrl || '', location: j.location || '', country: j.address?.postalAddress?.addressCountry || j.country || null, description: j.descriptionPlain || htmlToText(j.descriptionHtml),
  })),
  lever: (json) => (Array.isArray(json) ? json : []).map(j => ({
    title: j.text || '', url: j.hostedUrl || j.applyUrl || '', location: j.categories?.location || '', country:j.country || null, description: j.descriptionPlain || htmlToText(j.description),
  })),
  // Gem (api.gem.com) is Greenhouse-shaped; accept either key name.
  gem: (json) => (json.jobs || json.job_posts || (Array.isArray(json) ? json : [])).map(j => ({
    title: j.title || '', url: j.absolute_url || j.url || '', location: j.location?.name || j.location || '',
  })),
};

async function fetchJson(url, init) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await publicFetch(url, { ...init, signal: controller.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

// Workday: POST the same search the careers page makes, page by offset.
async function fetchWorkday({ host, tenant, site, searchText = '' }) {
  const api = `https://${host}/wday/cxs/${tenant}/${site}/jobs`;
  const jobs = [];
  for (let page = 0; page < WORKDAY_MAX_PAGES; page++) {
    const body = JSON.stringify({ appliedFacets: {}, limit: WORKDAY_PAGE, offset: page * WORKDAY_PAGE, searchText });
    const json = await fetchJson(api, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body,
    });
    const posts = json.jobPostings || [];
    for (const j of posts) {
      // "/job/US-WA-Seattle/Title_JR123" — the path names the primary site even when
      // locationsText only says "2 Locations".
      const primary = (j.externalPath || '').match(/\/job\/([^/]+)\//)?.[1]?.replace(/-/g, ' ') || '';
      const loc = j.locationsText || '';
      jobs.push({
        title: j.title || '',
        url: `https://${host}/en-US/${site}${j.externalPath || ''}`,
        location: /^\d+ locations$/i.test(loc) && primary ? `${primary} +${loc}` : loc || primary,
      });
    }
    if (posts.length < WORKDAY_PAGE || jobs.length >= (json.total || Infinity)) break;
  }
  return jobs;
}

// ── Title filter ────────────────────────────────────────────────────
// positive: at least one must appear (the intern-ness terms)
// negative: none may appear
// domain:   optional — reported as a boost flag, not required, so "Engineering Intern" still surfaces

function buildTitleFilter(f, allTitles) {
  const lower = (xs) => (xs || []).map(k => String(k).toLowerCase());
  const positive = lower(f?.positive), negative = lower(f?.negative), domain = lower(f?.domain);
  return (title, requireDomain = false) => {
    const t = title.toLowerCase();
    const domainHit = domain.some(k => t.includes(k));
    if (allTitles) return { pass: true, domainHit };
    const pos = positive.length === 0 || positive.some(k => t.includes(k));
    const neg = negative.some(k => t.includes(k));
    return { pass: pos && !neg && (!requireDomain || domainHit), domainHit };
  };
}

// ── Location filter ─────────────────────────────────────────────────
// Drop postings whose location names an excluded place. Multi-site ("3 Locations") and empty
// locations pass with a "?" flag — the row may still be a US posting.

function buildLocationFilter(f) {
  const exclude = (f?.exclude || []).map(k => String(k).toLowerCase());
  return (location) => {
    const l = (location || '').toLowerCase();
    if (!l || /^\d+ locations$/.test(l)) return { pass: true, unknown: true };
    return { pass: !exclude.some(k => l.includes(k)), unknown: /\+\d+ locations$/.test(l) };
  };
}

// ── Variants ────────────────────────────────────────────────────────
// One role is often posted once per degree track (PhD / BS-MS), per term (intern / co-op,
// Summer / Spring) or per site. Strip those tokens from the title; same company + same key
// → the later one is a variant of the first (see data/postings/SCHEMA.md "Variants").

const VARIANT_TOKENS = /\b(phd|ph\.d\.?|ms|bs|bs\/ms|msc|bsc|masters?|bachelors?|intern(ship)?|co-?op|student|summer|spring|fall|winter|autumn|20\d\d|usa?|remote)\b/g;
function variantKey(company, title) {
  // Strip tokens before AND after splitting punctuation: "co-op" must match as one token first,
  // then "ms" inside "bs/ms" after the slash becomes a space.
  const t = title.toLowerCase().replace(VARIANT_TOKENS, ' ').replace(/[-–—:,()\[\]/]+/g, ' ').replace(VARIANT_TOKENS, ' ').replace(/\s+/g, ' ').trim();
  return `${company.toLowerCase()}::${t}`;
}

// ── Dedup ───────────────────────────────────────────────────────────

function loadSeenUrls() {
  const seen = new Set();
  if (existsSync(SCAN_HISTORY_PATH)) {
    for (const line of readFileSync(SCAN_HISTORY_PATH, 'utf-8').split('\n').slice(1)) {
      const url = line.split('\t')[0];
      if (url) seen.add(url);
    }
  }
  for (const p of [PIPELINE_PATH, TRACKER_PATH]) {
    if (!existsSync(p)) continue;
    for (const m of readFileSync(p, 'utf-8').matchAll(/https?:\/\/[^\s|)>\]]+/g)) seen.add(m[0]);
  }
  return seen;
}

// ── Writers ─────────────────────────────────────────────────────────

function appendToPipeline(offers, date) {
  mkdirSync(DATA_DIR, { recursive: true });
  if (!existsSync(PIPELINE_PATH)) {
    writeFileSync(PIPELINE_PATH, '# Pipeline\n\nNew postings from scans, waiting for a ruling (apply / skip + reason).\n\n## Pending\n\n## Processed\n', 'utf-8');
  }
  let text = readFileSync(PIPELINE_PATH, 'utf-8');
  const marker = '## Pending';
  const idx = text.indexOf(marker);
  const after = idx + marker.length;
  const next = text.indexOf('\n## ', after);
  const insertAt = next === -1 ? text.length : next;
  const block = '\n' + offers.map(o =>
    `- [ ] ${o.url} | ${o.company} | ${o.title} | ${o.location || '—'} | ${date}${o.domainHit ? ' | domain' : ''}${o.variantOf ? ` | variant-of ${o.variantOf}` : ''}`
  ).join('\n') + '\n';
  text = text.slice(0, insertAt) + block + text.slice(insertAt);
  writeFileSync(PIPELINE_PATH, text, 'utf-8');
}

function appendToScanHistory(offers, date) {
  mkdirSync(DATA_DIR, { recursive: true });
  if (!existsSync(SCAN_HISTORY_PATH)) {
    writeFileSync(SCAN_HISTORY_PATH, 'url\tfirst_seen\tsource\ttitle\tcompany\tstatus\n', 'utf-8');
  }
  appendFileSync(SCAN_HISTORY_PATH, offers.map(o =>
    `${o.url}\t${date}\t${o.source}\t${o.title}\t${o.company}\tadded`).join('\n') + '\n', 'utf-8');
}

async function parallel(tasks, limit) {
  let i = 0;
  const run = async () => { while (i < tasks.length) await tasks[i++](); };
  await Promise.all(Array.from({ length: Math.min(limit, tasks.length) }, run));
}

// ── Main ────────────────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);
  if (args.includes('--help') || args.includes('-h')) {
    console.log('Usage: node tools/scan.mjs [--dry-run] [--all-titles] [--company NAME]\nScans the public feeds of enabled companies in companies.yml and writes new postings to the inbox.');
    return;
  }
  const dryRun = args.includes('--dry-run');
  const allTitles = args.includes('--all-titles');
  const ci = args.indexOf('--company');
  const only = ci !== -1 ? args[ci + 1]?.toLowerCase() : null;

  const config = yaml.load(readFileSync(COMPANIES_PATH, 'utf-8'));
  const companies = config.companies || [];
  const titleFilter = buildTitleFilter(config.title_filter, allTitles);
  const locationFilter = buildLocationFilter(config.location_filter);

  const targets = companies
    .filter(c => c.enabled !== false)
    .filter(c => !only || c.name.toLowerCase().includes(only))
    .map(c => ({ ...c, _api: detectApi(c) }));
  const noApi = targets.filter(c => !c._api).map(c => c.name);
  const scanned = targets.filter(c => c._api);
  if (!scanned.length && targets.length) throw new Error('No configured target has a supported public feed; classify it first.');

  const seen = loadSeenUrls();
  const date = new Date().toISOString().slice(0, 10);
  const rows = [];     // per-company summary
  const offers = [];   // new, filtered, deduped
  const firstByKey = new Map();   // variantKey → first offer url seen (this scan + history)
  if (existsSync(SCAN_HISTORY_PATH)) {
    for (const line of readFileSync(SCAN_HISTORY_PATH, 'utf-8').split('\n').slice(1)) {
      const [url, , , title, company] = line.split('\t');
      if (url && title && company) { const k = variantKey(company, title); if (!firstByKey.has(k)) firstByKey.set(k, url); }
    }
  }

  await parallel(scanned.map(c => async () => {
    const api = c._api;
    const row = { company: c.name, ats: api.type, found: 0, matched: 0, abroad: 0, added: 0, error: '' };
    try {
      const jobs = api.type === 'workday'
        ? await fetchWorkday(api)
        : api.type === 'custom' ? parseCustom(await fetchJson(api.url), api)
        : PARSERS[api.type](await fetchJson(api.url));
      row.found = jobs.length;
      for (const j of jobs) {
        const { pass, domainHit } = titleFilter(j.title, c.require_domain === true);
        if (!pass) continue;
        row.matched++;
        const loc = locationFilter(j.location);
        if (!loc.pass) { row.abroad++; continue; }
        if (seen.has(j.url)) { if (!dryRun) backfillPosting(j); continue; }
        seen.add(j.url);
        row.added++;
        const k = variantKey(c.name, j.title);
        const variantOf = firstByKey.has(k) ? firstByKey.get(k) : null;
        if (!variantOf) firstByKey.set(k, j.url);
        offers.push({ ...j, country: j.country || c.country || countryFromLocation(j.location), company: c.name, ats:api.type, site:c.sign_in_site || (['greenhouse','lever','ashby'].includes(api.type) ? new URL(j.url).hostname : null), domainHit, locUnknown: loc.unknown, variantOf, source: `${api.type}-api` });
      }
    } catch (e) {
      row.error = e.message;
    }
    rows.push(row);
  }), CONCURRENCY);

  if (!dryRun && offers.length) {
    savePostings(offers, date);
    appendToPipeline(offers, date);
    appendToScanHistory(offers, date);
  }

  rows.sort((a, b) => a.company.localeCompare(b.company));
  console.log(`## Scan ${date}${dryRun ? ' (dry run)' : ''}${allTitles ? ' — all titles' : ''}\n`);
  console.log('| Company | ATS | Jobs on board | Title matches | Abroad (dropped) | New | Error |');
  console.log('|---|---|---:|---:|---:|---:|---|');
  for (const r of rows) console.log(`| ${r.company} | ${r.ats} | ${r.found} | ${r.matched} | ${r.abroad} | ${r.added} | ${r.error} |`);
  if (noApi.length) console.log(`\nNo ATS configured (tier 2 or classify first): ${noApi.join(', ')}`);

  if (offers.length) {
    console.log(`\n### New postings (${offers.length})\n`);
    console.log('| Company | Title | Location | Domain | Variant | URL |');
    console.log('|---|---|---|:-:|:-:|---|');
    for (const o of offers.sort((a, b) => a.company.localeCompare(b.company) || a.title.localeCompare(b.title))) {
      console.log(`| ${o.company} | ${o.title} | ${o.location || '—'}${o.locUnknown ? ' ?' : ''} | ${o.domainHit ? '✓' : ''} | ${o.variantOf ? '↳' : ''} | ${o.url} |`);
    }
    if (!dryRun) console.log(`\nSaved to ${PIPELINE_PATH} and ${SCAN_HISTORY_PATH}`);
  } else {
    console.log('\nNo new postings.');
  }
  if (rows.some(row => row.error)) throw new Error('One or more company feeds failed; see the scan table.');
}

export { detectApi, PARSERS, fetchWorkday, parseCustom, main };
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(err => { console.error('Fatal:', err.message); process.exitCode = 1; });
}

function parseCustom(json, api) {
  const at = (obj, key) => String(key || '').split('.').filter(Boolean).reduce((v, part) => v?.[part], obj);
  const rows = api.items ? at(json, api.items) : json;
  if (!Array.isArray(rows) || !api.title || !api.link) throw new Error('custom feed needs items, title and link mappings');
  return rows.map(row => ({title: at(row, api.title) || '', url: at(row, api.link) || '', location: at(row, api.location) || ''}));
}
// A posting saved before its feed details were kept gains them on the next scan; nothing else changes.
function backfillPosting(job) {
  const path = resolve(DATA_DIR, 'postings', 'job-' + createHash('sha256').update(job.url).digest('hex').slice(0, 24) + '.json');
  if (!existsSync(path)) return;
  const rec = JSON.parse(readFileSync(path, 'utf-8'));
  const posting = rec.posting || {};
  let changed = false;
  for (const key of ['description', 'posted_on', 'updated_on', 'pay']) {
    if (posting[key] == null && job[key]) { posting[key] = job[key]; changed = true; }
  }
  const pay = payFields(posting);
  if (JSON.stringify(posting.pay_normalized) !== JSON.stringify(pay.pay_normalized) || posting.pay !== pay.pay) {
    Object.assign(posting, pay); changed = true;
  }
  if (changed) writeFileSync(path, JSON.stringify({...rec, posting}, null, 2) + '\n', {mode: 0o600});
}

function savePostings(offers, date) {
  const postings = resolve(DATA_DIR, 'postings');
  mkdirSync(postings, {recursive: true});
  for (const offer of offers) {
    const id = 'job-' + createHash('sha256').update(offer.url).digest('hex').slice(0, 24);
    const path = resolve(postings, id + '.json');
    if (existsSync(path)) continue;
    const rec = {id, kind: 'job', company: offer.company, title: offer.title, url: offer.url,
      status: 'new', ats:offer.ats, site:offer.site || null, country:offer.country || null, source: offer.source, first_seen: date, location: offer.location,
      posting: {apply_url: offer.url, description: offer.description || null, posted_on: offer.posted_on || null, updated_on: offer.updated_on || null, pay: offer.pay || null}, variant_of: offer.variantOf || null, ruling: {decision: null}, history: []};
    Object.assign(rec.posting, payFields(rec.posting));
    writeFileSync(path, JSON.stringify(rec, null, 2) + '\n', {flag:'wx', mode:0o600});
  }
}
