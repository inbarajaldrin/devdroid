// Career web app: Today, Listings (the internal route and code still say inbox), posting detail, Questions,
// Applied and Pair, for a laptop (or phone) browser.
// Reference: career/app/Sources/*.swift (the SwiftUI phone app; screens and rules are ported from it).
// Reference: icon paths "settings" and "rotate-cw" from Feather icons, https://github.com/feathericons/feather (MIT).
//
// Talks only to its own origin: GET/POST /web/api/<endpoint> with `Authorization: Bearer <token>` (serve.py strips
// the /web/api prefix and runs the same token-checked API the phone uses). The token lives in localStorage after
// a one-time paste on the Pair screen; it is never put in a URL. Record data is always inserted as text nodes.
(() => {
  'use strict';

  const API = '/web/api';
  // Where the page is published. The API is always this page's own origin, so either address works unchanged.
  const PUBLIC_WEB = new URL('/web/', location.origin).href;
  const TOKEN_KEY = 'careerapp.token';
  const BY = 'web';
  const ID_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,200}$/;
  const WIDE = window.matchMedia('(min-width: 920px)');

  // ---------- state ----------
  const S = {
    token: readToken(),
    authRefused: false,
    postings: [], byId: new Map(),
    questions: [], qById: new Map(),
    batch: null,                  // GET /batch: {created_at, note, items: [{why, record}], open, cleared, missing}
    loaded: { postings: false, questions: false, batch: false },
    errors: { postings: null, questions: null, batch: null },
    loading: false,
    lastRefreshed: null,
    query: '',
    opened: new Set(),            // inbox groups opened this session: "<section>|<company>"
    searchCollapsed: new Set(),   // while searching every group starts open; this tracks the ones closed
    actOpen: new Set(),           // folded Act-soon companies opened
    showAnswered: false,
    showCleared: false,           // Today: cleared batch rows listed under their toggle
    appliedFilter: 'active',
    previewDocs: new Map(),       // posting id -> {data|null, error|null}: GET /postings/<id>/preview
    previewBack: null,            // where the preview's Back goes (the page it was opened from)      // Applied: 'active' (submitted, under review, interview, offer) | 'closed'
    drafts: new Map(),            // question id -> {draft, comment, choice, editing, changing, expanded, ctxOpen (folded by the user)}
    reviews: new Map(),           // posting id -> {key, data|null, loading}
    previews: new Map(),          // "<posting id>|<path>" -> true when the inline preview is open
    route: { tab: 'today', id: null },
    view: null,                   // which list the split view shows: 'today' | 'inbox' | 'questions'
    detailSig: null,
    busy: false,
    revealPending: false,
    blockedUntil: 0,              // after a 429: no token-checked request before this time (ms)
  };

  function readToken() { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; } }
  function saveToken(t) { try { localStorage.setItem(TOKEN_KEY, t); return true; } catch { return false; } }
  function dropToken() { try { localStorage.removeItem(TOKEN_KEY); } catch { /* storage blocked */ } }

  // ---------- DOM helpers ----------
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v == null || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k === 'style') el.style.cssText = v;
        else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else if (v === true) el.setAttribute(k, '');
        else el.setAttribute(k, String(v));
      }
    }
    add(el, kids);
    return el;
  }
  function add(el, kids) {
    for (const k of kids.flat(Infinity)) {
      if (k == null || k === false || k === '') continue;
      el.append(k instanceof Node ? k : String(k));
    }
    return el;
  }
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const str = (v) => (typeof v === 'string' && v.trim() ? v.trim() : null);
  const arr = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string' && x.trim()) : []);
  const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u.trim()) ? u.trim() : null);
  // Provenance labels come from records written before the no-em-dash ruling; show them with a colon instead.
  const noDash = (s) => String(s).replace(/\s*[\u2014\u2013]\s*/g, ': ');

  // ---------- icons (24x24 stroke paths) ----------
  const circ = (cx, cy, r) => `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
  const ICONS = {
    chevron: 'M9 6l6 6-6 6',
    check: 'M5 12.5l4.5 4.5L19 7',
    x: 'M6 6l12 12M18 6L6 18',
    flag: 'M5 21V4M5 4h11l-2 4 2 4H5',
    alarm: circ(12, 13, 8) + 'M12 9v4l2.5 2M5 3L2 6M19 3l3 3',
    alert: circ(12, 12, 9) + 'M12 7.5v5.5M12 16.5v.5',
    clock: circ(12, 12, 9) + 'M12 7v5l3 2',
    calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
    pin: 'M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0113 0c0 5.4-6.5 11-6.5 11z' + circ(12, 10, 2.2),
    gear: circ(12, 12, 3) + 'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
    refresh: 'M23 4v6h-6M20.49 15a9 9 0 1 1-2.12-9.36L23 10',
    keyboard: 'M3 6h18v12H3zM7 10h.01M11 10h.01M15 10h.01M19 10h.01M7 14h10',
    cpu: 'M7 7h10v10H7zM10 10h4v4h-4zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4',
    external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
    doc: 'M6 3h9l4 4v14H6zM14 3v5h5M9 13h6M9 17h6',
    plane: 'M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z',
    seal: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM8.5 12l2.5 2.5 4.5-5',
    tray: 'M3 13h5l2 3h4l2-3h5M5.5 5h13L21 13v6H3v-6z',
    bubble: 'M4 5h16v11H9l-5 4z',
    list: circ(5, 7, 1) + circ(5, 12, 1) + circ(5, 17, 1) + 'M9 7h11M9 12h11M9 17h11',
    checkCircle: circ(12, 12, 9) + 'M8 12.5l3 3 5-6',
    turn: 'M5 4v7a4 4 0 004 4h10M15 11l4 4-4 4',
    uturn: 'M9 14L4 9l5-5M4 9h11a5 5 0 010 10h-3',
    person: 'M4 5h16v14H4zM8 16c.6-1.6 1.8-2.5 3-2.5s2.4.9 3 2.5' + circ(11, 10, 2) + 'M16 9h2M16 12h2',
    globe: circ(12, 12, 9) + 'M3 12h18M12 3c2.6 3 2.6 15 0 18M12 3c-2.6 3-2.6 15 0 18',
    code: 'M9 7l-5 5 5 5M15 7l5 5-5 5',
    people: circ(9, 8, 3) + 'M3 20c0-3 2.7-5 6-5s6 2 6 5M16 5.5a3 3 0 010 5.5M18 15c2 .6 3 2.3 3 5',
    question: circ(12, 12, 9) + 'M9.5 9.5a2.5 2.5 0 114 2c-1 .6-1.5 1.2-1.5 2.5M12 17v.5',
    pencil: 'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
    expand: 'M4 10V4h6M20 14v6h-6M4 4l6 6M20 20l-6-6',
    download: 'M12 4v11M7 10l5 5 5-5M4 20h16',
    eye: 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z' + circ(12, 12, 3),
    link: 'M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1',
    copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
    cash: 'M2 6h20v12H2z' + circ(12, 12, 2.5) + 'M6 9.5v.01M18 14.5v.01',
  };
  function icon(name, cls) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    if (cls) svg.setAttribute('class', cls);
    const p = document.createElementNS(ns, 'path');
    p.setAttribute('d', ICONS[name] || ICONS.question);
    svg.append(p);
    return svg;
  }
  const kbd = (k) => h('kbd', null, k);

  // ---------- API ----------
  class ApiError extends Error {
    constructor(msg, status, data) { super(msg); this.status = status; this.data = data; }
  }

  // serve.py answers 429 after 20 failed sign-ins from one address in 10 minutes. The page never feeds that counter:
  // after one refusal it sends nothing token-checked until the user pairs again, and after a 429 it waits it out.
  const blockedMessage = () => `Too many failed sign-ins from this network. Try again in ${Math.max(1, Math.ceil((S.blockedUntil - Date.now()) / 60000))} min.`;

  async function api(path, opts = {}) {
    const token = opts.token != null ? opts.token : S.token;
    if (!token) throw new ApiError('This browser is not paired yet.', 401);
    if (S.blockedUntil > Date.now()) throw new ApiError(blockedMessage(), 429);
    if (S.authRefused && opts.token == null) throw new ApiError('The server refused the saved token.', 401);
    const init = { method: opts.method || 'GET', headers: { Authorization: 'Bearer ' + token }, cache: 'no-store' };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    let r;
    try {
      r = await fetch(API + path, init);
    } catch {
      throw new ApiError("Can't reach the server. Is serve.py running?", 0);
    }
    if (r.ok && opts.raw) return r;
    let data = null;
    if ((r.headers.get('Content-Type') || '').includes('json')) {
      try { data = await r.json(); } catch { data = null; }
    }
    if (r.status === 429) {
      const wait = parseInt(r.headers.get('Retry-After') || '600', 10);
      S.blockedUntil = Date.now() + (Number.isFinite(wait) ? wait : 600) * 1000;
      throw new ApiError(blockedMessage(), 429, data);
    }
    if (!r.ok) {
      if (r.status === 401 && opts.token == null) onUnauthorized();
      const msg = (data && data.error) || `HTTP ${r.status}`;
      throw new ApiError(r.status === 401 ? 'The server refused the token.' : msg, r.status, data);
    }
    return data;
  }

  function onUnauthorized() {
    if (S.authRefused) return;
    S.authRefused = true;
    toast('The server refused the saved token. Paste a new pairing link.', 'error');
    render();
  }

  // Files come through the token-checked API, so <img>/<iframe> get blob: URLs made from an authorised fetch.
  const blobs = new Map();
  const filePath = (rel) => '/files/' + rel.split('/').map(encodeURIComponent).join('/');
  function fetchFile(rel, fresh) {
    if (!fresh && blobs.has(rel)) return blobs.get(rel);
    const p = api(filePath(rel), { raw: true }).then(async (r) => {
      const blob = await r.blob();
      return { blob, url: URL.createObjectURL(blob), type: blob.type || '' };
    });
    blobs.set(rel, p);
    p.catch(() => { if (blobs.get(rel) === p) blobs.delete(rel); });
    return p;
  }
  async function fileText(rel) { return (await fetchFile(rel, true)).blob.text(); }
  function dropBlobs() {
    for (const p of blobs.values()) p.then((f) => URL.revokeObjectURL(f.url)).catch(() => {});
    blobs.clear();
  }

  function remoteImg(rel, alt, cls, fresh) {
    const img = h('img', { class: cls, alt: alt || '', decoding: 'async' });
    if (rel) fetchFile(rel, fresh).then((f) => { img.src = f.url; }).catch(() => img.classList.add('failed'));
    return img;
  }

  // ---------- data ----------
  function setPostings(list) {
    S.postings = Array.isArray(list) ? list.filter((p) => p && typeof p.id === 'string') : [];
    S.byId = new Map(S.postings.map((p) => [p.id, p]));
  }
  function upsertPosting(rec) {
    const i = S.postings.findIndex((p) => p.id === rec.id);
    if (i >= 0) S.postings[i] = rec; else S.postings.push(rec);
    S.byId.set(rec.id, rec);
  }
  function setQuestions(list) {
    S.questions = Array.isArray(list) ? list.filter((q) => q && typeof q.id === 'string') : [];
    S.qById = new Map(S.questions.map((q) => [q.id, q]));
  }
  function upsertQuestion(q) {
    const i = S.questions.findIndex((x) => x.id === q.id);
    if (i >= 0) S.questions[i] = q; else S.questions.push(q);
    S.qById.set(q.id, q);
  }

  async function refreshAll(opts = {}) {
    if (!S.token || S.authRefused || S.blockedUntil > Date.now()) return;
    if (S.loading) { S.refreshAgain = true; return; }
    S.loading = true;
    if (!opts.quiet) spin(true);
    try { S.dataStamp = (await api('/stamp')).stamp; } catch { S.dataStamp = null; }   // read first: a later change is caught
    const [p, q, b] = await Promise.allSettled([api('/postings'), api('/questions'), api('/batch')]);
    if (p.status === 'fulfilled') { setPostings(p.value); S.loaded.postings = true; S.errors.postings = null; }
    else S.errors.postings = p.reason.message;
    if (q.status === 'fulfilled') { setQuestions(q.value); S.loaded.questions = true; S.errors.questions = null; }
    else S.errors.questions = q.reason.message;
    if (b.status === 'fulfilled') { S.batch = b.value && Array.isArray(b.value.items) ? b.value : null; S.loaded.batch = true; S.errors.batch = null; }
    else S.errors.batch = b.reason.message;
    if (p.status === 'fulfilled' || q.status === 'fulfilled') {
      S.lastRefreshed = new Date();
      S.authRefused = false;
      // A re-fill or a new status changes the review; reload it on the next render.
      for (const [id, r] of S.reviews) { if (!S.byId.has(id) || r.key !== reviewKey(S.byId.get(id))) S.reviews.delete(id); }
    }
    S.loading = false;
    spin(false);
    if (p.status === 'rejected' || q.status === 'rejected' || b.status === 'rejected') S.dataStamp = null;   // try again
    if (opts.announce) {
      if (p.status === 'rejected' && q.status === 'rejected') toast(p.reason.message, 'error');
      else toast('Refreshed');
    }
    if (opts.quiet && editing()) { S.repaint = true; updateChrome(); } else render({ data: true });
    chatPoll();
    if (S.refreshAgain) { S.refreshAgain = false; refreshAll({ quiet: true }); }
  }

  // ---------- staying current without a reload ----------
  // GET /stamp costs a few milliseconds and changes when anything the app shows changes, so the page asks it every
  // POLL_MS while visible, on focus and on every navigation, and fetches the 3 MB postings list only when it moved. A
  // preview is always fetched fresh on arrival. Fresh data never lands on top of what he is doing: while a dialog or a
  // text box in the page is open it waits, and paints when he leaves it. A new build of the page reloads it, same rule.
  const POLL_MS = 12000;
  const editing = () => $('#dialog').open || $('#viewer').open || S.busy
    || (document.activeElement instanceof HTMLTextAreaElement && !!document.activeElement.closest('#main'));
  const unsent = () => [...S.drafts.values()].some((d) => (d.comment || '').trim() || (d.draft || '').trim())
    || !!($('#chat-input') && $('#chat-input').value.trim());

  async function checkFresh(force) {
    if (!S.token || S.authRefused || S.blockedUntil > Date.now() || document.visibilityState !== 'visible') return;
    let st;
    try { st = await api('/stamp'); } catch { return; }           // a missed poll is fine; the next one catches up
    if (st.web) {
      if (S.webBuild && st.web !== S.webBuild) S.newBuild = st.web;
      else if (!S.webBuild) S.webBuild = st.web;
    }
    const moved = S.dataStamp !== st.stamp;
    S.dataStamp = st.stamp;
    if (moved || force) {
      if (S.loaded.postings || force) refreshAll({ quiet: true });
      if (S.route.tab === 'preview' && S.route.id) refreshPreview(S.route.id);
    }
    settle();
  }

  // Whatever had to wait for him: a held-back paint, then a new build.
  function settle() {
    if (editing()) return;
    if (S.repaint) { S.repaint = false; render({ data: true }); }
    if (S.newBuild && !unsent()) {
      let done = null;
      try { done = sessionStorage.getItem('careerapp.reloadedFor'); sessionStorage.setItem('careerapp.reloadedFor', S.newBuild); } catch { /* storage blocked */ }
      if (done !== S.newBuild) location.reload();                 // once per build, so a bad deploy cannot loop
      S.newBuild = null;
    }
  }

  function spin(on) { const b = $('#btn-refresh'); if (b) b.classList.toggle('spin', on); }

  // ---------- dates (port of Dates.swift) ----------
  const fmtShort = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
  const fmtLong = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  const fmtStamp = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  const fmtTime = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

  function parseDay(s) {
    if (typeof s !== 'string') return null;
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s.trim());
    if (!m) return null;
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    return isNaN(d) ? null : d;
  }
  const dayStart = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const daysUntil = (d) => Math.round((dayStart(d) - dayStart(new Date())) / 86400000);
  const shortDay = (d) => (d.getFullYear() === new Date().getFullYear() ? fmtShort : fmtLong).format(d);
  function stamp(iso) { const d = new Date(iso); return isNaN(d) ? String(iso) : fmtStamp.format(d); }
  function relative(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return null;
    const s = (d - new Date()) / 1000;
    const units = [['year', 31536000], ['month', 2592000], ['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]];
    for (const [u, n] of units) if (Math.abs(s) >= n) return rtf.format(Math.round(s / n), u);
    return 'just now';
  }

  const LEVELS = ['now', 'week', 'soon', 'rolling', 'none'];
  function datesOf(p) {
    const u = (p.urgency && typeof p.urgency === 'object') ? p.urgency : {};
    const lv = String(u.level || '').toLowerCase();
    const level = LEVELS.includes(lv) ? lv : 'none';
    const body = p.posting || {};
    const rawDeadline = str(body.deadline);
    const deadline = parseDay(rawDeadline);
    const posted = parseDay(body.posted_on);
    const actBy = parseDay(u.by) || deadline;
    const daysLeft = actBy ? daysUntil(actBy) : (Number.isFinite(u.days_left) ? Math.round(u.days_left) : null);
    return { level, rawDeadline, deadline, posted, actBy, daysLeft, note: str(body.deadline_note), reason: str(u.reason) };
  }
  const actSoon = (d) => d.level === 'now' || d.level === 'week';
  function rowChipText(d) {
    if (d.deadline) return 'by ' + shortDay(d.deadline);
    if (d.posted) { const n = Math.max(0, -daysUntil(d.posted)); return n === 0 ? 'posted today' : `posted ${n} d ago`; }
    return null;
  }
  function actChipText(d) {
    const dt = d.actBy ? shortDay(d.actBy) : null;
    if (d.daysLeft == null) return dt || (d.level === 'now' ? 'act now' : 'this week');
    const n = d.daysLeft;
    const tail = n < 0 ? 'passed, still open' : n === 0 ? 'today' : `${n} d`;
    return dt ? `${dt} · ${tail}` : tail;
  }
  function detailLine(d) {
    if (d.deadline) {
      const n = daysUntil(d.deadline);
      const tail = n < 0 ? (d.level === 'now' ? 'passed, still open' : 'passed') : n === 0 ? 'today' : `${n} ${n === 1 ? 'day' : 'days'} left`;
      return { text: `Deadline ${fmtLong.format(d.deadline)} · ${tail}`, hard: true };
    }
    if (d.rawDeadline) return { text: 'Deadline ' + d.rawDeadline, hard: true };
    if (d.actBy && actSoon(d)) {
      const n = d.daysLeft;
      const tail = n == null ? '' : n < 0 ? ' · passed, still open' : n === 0 ? ' · today' : ` · ${n} ${n === 1 ? 'day' : 'days'} left`;
      return { text: `Act by ${fmtLong.format(d.actBy)}${tail}`, hard: true };
    }
    if (d.posted) return { text: `Posted ${shortDay(d.posted)} · rolling`, hard: false };
    return null;
  }
  const levelIcon = (lv) => (lv === 'now' ? 'alert' : lv === 'week' ? 'clock' : 'calendar');
  function dateChip(text, level) {
    const strong = level === 'now' || level === 'week' || level === 'soon';
    return h('span', { class: `chip num ${level === 'now' || level === 'week' ? level : ''} ${strong ? 'strong' : ''}` },
      icon(levelIcon(level)), text);
  }

  // ---------- pay (data/postings/SCHEMA.md "Pay": posting.pay_normalized, parsed by tools/pay.py) ----------
  // "$40-50/hr · ≈$83K-104K/yr": both forms, the side the posting did not state marked with a faint "≈".
  function payOf(p) {
    const n = p.posting && p.posting.pay_normalized;
    return n && Array.isArray(n.hourly) && Array.isArray(n.yearly) ? n : null;
  }
  function paySpan([a, b], fmt) { return a === b ? fmt(a) : `${fmt(a)}-${fmt(b).slice(1)}`; }
  function payParts(n) {
    const approx = (on) => (on ? h('span', { class: 'approx', title: 'converted at 40 h/week' }, '≈') : null);
    return [approx(n.unit !== 'hour'), paySpan(n.hourly, (v) => `$${v}`) + '/hr', ' · ',
      approx(n.unit !== 'year'), paySpan(n.yearly, (v) => `$${Math.round(v / 1000)}K`) + '/yr'];
  }
  function payChip(p) {
    const n = payOf(p);
    return n ? h('span', { class: 'chip num pay', title: str(p.posting.pay) }, h('span', null, payParts(n))) : null;
  }

  // ---------- small views ----------
  const FIT_RANK = { direct: 0, adjacent: 1, 'off-target': 2 };
  const fitOf = (p) => { const f = p.summary_ai && p.summary_ai.fit; return f in FIT_RANK ? f : null; };
  const fitBadge = (fit) => (fit ? h('span', { class: `chip fit-${fit}`, title: 'Fit' }, fit) : null);
  const statusBadge = (s) => h('span', { class: 'chip' }, s);

  function monogram(name) {
    const words = String(name || '?').split(/[^\p{L}\p{N}]+/u).filter(Boolean);
    if (words.length === 1 && words[0].length <= 3) return words[0].toUpperCase();
    return words.slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?';
  }
  function logoOf(p) {
    const l = p && p.company_info && p.company_info.logo;
    return l && typeof l === 'object' && str(l.file) ? l : null;
  }
  function logoView(logo, company, size = 40, radius = 8) {
    const box = h('span', { class: 'logo', 'aria-hidden': 'true' });
    box.style.cssText = `width:${size}px;height:${size}px;border-radius:${radius}px;font-size:${Math.round(size * 0.4)}px`;
    const mono = () => { box.className = 'logo'; box.replaceChildren(monogram(company)); };
    if (!logo) { mono(); return box; }
    const bg = String(logo.background || '').toLowerCase();
    const tile = bg === 'light' ? 'tile-light' : bg === 'dark' ? 'tile-dark' : 'tile-any';
    fetchFile('data/postings/' + str(logo.file)).then((f) => {
      const img = h('img', { alt: '' });
      img.addEventListener('error', mono);
      img.src = f.url;
      box.className = `logo ${tile} has-img`;
      box.replaceChildren(img);
    }).catch(mono);
    return box;
  }

  function linkify(text) {
    const out = [];
    const re = /https?:\/\/[^\s<>"'”)\]]+/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      let url = m[0].replace(/[.,;:!?]+$/, '');
      if (m.index > last) out.push(text.slice(last, m.index));
      out.push(h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, url));
      last = m.index + url.length;
      re.lastIndex = last;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }
  // Shell commands (data/questions/SCHEMA.md "Commands"). An ask's own `command`, and commands found in a context
  // (a fenced ``` block, or a line starting with "$ " plus its backslash continuations), become a monospace block
  // with a Copy button. Display only: the stored text never changes.
  function splitCommands(text) {
    const parts = [];
    const lines = String(text).replace(/\r\n?/g, '\n').split('\n');
    let buf = [];
    const flush = () => {
      const t = buf.join('\n').replace(/^\n+|\s+$/g, '');
      if (t.trim()) parts.push({ text: t });
      buf = [];
    };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      let body = null;
      if (/^\s*```/.test(line)) {
        body = [];
        while (i + 1 < lines.length && !/^\s*```/.test(lines[i + 1])) body.push(lines[++i]);
        i++;                                  // the closing fence (past the end when unclosed)
      } else if (/^\s*\$ /.test(line)) {
        body = [line.replace(/^\s*\$ /, '')];
        while (/\\$/.test(body[body.length - 1]) && i + 1 < lines.length) body.push(lines[++i]);
      }
      if (body == null) { buf.push(line); continue; }
      const cmd = body.join('\n').replace(/^\n+|\s+$/g, '');
      flush();
      if (cmd.trim()) parts.push({ cmd });
    }
    flush();
    return parts;
  }
  // The context as nodes: text keeps its links, commands get a Copy button.
  function richText(text) {
    return splitCommands(text).map((p) => (p.cmd != null ? commandBlock(p.cmd) : h('div', { class: 'pre' }, linkify(p.text))));
  }
  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
    } catch { /* denied or unavailable: fall back below */ }
    // Plain http (the tailnet IP) has no async clipboard; a selected off-screen textarea and execCommand still work.
    const ta = h('textarea', { class: 'clip-buffer', readonly: true, 'aria-hidden': 'true', tabindex: '-1' });
    ta.value = text;
    const back = document.activeElement;
    document.body.append(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    if (back && typeof back.focus === 'function') back.focus();
    return ok;
  }
  function commandBlock(cmd) {
    const code = h('code', null, cmd);
    const label = h('span', null, 'Copy');
    let timer = 0;
    const btn = h('button', { type: 'button', class: 'btn small cmd-copy', title: 'Copy the command', 'aria-label': 'Copy the command' },
      icon('copy'), label);
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const ok = await copyText(cmd);
      if (!ok) {                                // let the user copy by hand: select the command
        const r = document.createRange();
        r.selectNodeContents(code);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(r);
      }
      label.textContent = ok ? 'Copied' : 'Selected, press Cmd+C';
      btn.classList.toggle('done', ok);
      clearTimeout(timer);
      timer = setTimeout(() => { label.textContent = 'Copy'; btn.classList.remove('done'); }, ok ? 1600 : 4000);
    });
    return h('div', { class: 'cmd' }, h('pre', { class: 'cmd-text' }, code), btn);
  }

  function extLink(url, label) {
    const u = safeUrl(url);
    return u ? h('a', { href: u, target: '_blank', rel: 'noopener noreferrer' }, label || u) : h('span', { class: 'muted' }, label || String(url));
  }
  const bullets = (items) => h('ul', { class: 'bullets' }, items.map((t) => h('li', null, t)));

  // Company homepage (company_info.website, data/postings/SCHEMA.md). Rows are links to the posting, so the row label is
  // a span with link semantics: it opens the site in a new tab and keeps the row from opening.
  const websiteOf = (p) => safeUrl(p && p.company_info && p.company_info.website);
  function siteName(url) {
    try {
      const u = new URL(url);
      const host = u.hostname.replace(/^www\./, '');
      return host === 'sites.google.com' ? host + u.pathname.replace(/\/$/, '') : host;
    } catch { return url; }
  }
  function siteLabel(p) {
    const url = websiteOf(p);
    if (!url) return null;
    const name = siteName(url);
    const open = (e) => { e.preventDefault(); e.stopPropagation(); window.open(url, '_blank', 'noopener,noreferrer'); };
    return h('span', { class: 'site', role: 'link', tabindex: '0', title: url, 'aria-label': `${name}, company website, opens in a new tab`,
      onclick: open, onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') open(e); } }, icon('globe'), name);
  }

  function firstSentences(text, n) {
    if (typeof Intl.Segmenter === 'function') {
      const seg = new Intl.Segmenter(undefined, { granularity: 'sentence' });
      const parts = Array.from(seg.segment(text), (s) => s.segment);
      return parts.length <= n ? text : parts.slice(0, n).join('').trim();
    }
    const parts = text.match(/[^.!?]+[.!?]+(\s|$)|[^.!?]+$/g) || [text];
    return parts.length <= n ? text : parts.slice(0, n).join('').trim();
  }

  // A small, safe Markdown renderer for form answers and cover letters: headings, paragraphs, lists, quotes,
  // fenced code, **bold**, *italic*, `code`, [text](https://...) and bare URLs. Everything becomes text nodes.
  function renderMarkdown(md) {
    const root = h('div', { class: 'md' });
    const lines = md.replace(/\r\n?/g, '\n').replace(/<!--[\s\S]*?-->/g, '').split('\n');
    let para = [], list = null, i = 0;
    const flush = () => {
      if (para.length) { root.append(h('p', null, inline(para.join(' ')))); para = []; }
      list = null;
    };
    while (i < lines.length) {
      const line = lines[i];
      let m;
      if (/^```/.test(line)) {
        flush();
        const buf = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
        root.append(h('pre', null, h('code', null, buf.join('\n'))));
      } else if ((m = /^(#{1,6})\s+(.*)$/.exec(line))) {
        flush();
        root.append(h('h' + Math.min(4, m[1].length), null, inline(m[2])));
      } else if ((m = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line))) {
        const ordered = /\d/.test(m[1]);
        if (para.length) { root.append(h('p', null, inline(para.join(' ')))); para = []; }
        if (!list || list.ordered !== ordered) { list = { el: h(ordered ? 'ol' : 'ul'), ordered }; root.append(list.el); }
        list.el.append(h('li', null, inline(m[2])));
      } else if ((m = /^>\s?(.*)$/.exec(line))) {
        flush();
        root.append(h('blockquote', null, inline(m[1])));
      } else if (!line.trim()) {
        flush();
      } else {
        list = null;
        para.push(line.trim());
      }
      i++;
    }
    flush();
    return root;
  }
  function inline(text) {
    const out = [];
    const re = /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*([^*\s][^*]*)\*|https?:\/\/[^\s<>)]+)/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      if (m[2]) out.push(h('strong', null, m[2]));
      else if (m[3]) out.push(h('code', null, m[3]));
      else if (m[4]) out.push(extLink(m[5], m[4]));
      else if (m[6]) out.push(h('em', null, m[6]));
      else out.push(extLink(m[0]));
      last = re.lastIndex;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  // Every text box: Enter sends, Shift + Enter is a new line. A phone's on-screen Return has no Shift, so there it stays a
  // new line and the Send button sends. Input-method composition (e.isComposing) is left alone.
  const TOUCH_KEYBOARD = window.matchMedia('(hover: none) and (pointer: coarse)');
  const sendKey = (e) => e.key === 'Enter' && !e.shiftKey && !e.isComposing && (!TOUCH_KEYBOARD.matches || e.metaKey || e.ctrlKey);
  const enterHint = () => (TOUCH_KEYBOARD.matches ? '' : 'Enter sends, Shift + Enter makes a new line.');

  // ---------- toasts and dialogs ----------
  function toast(text, kind = 'ok', ms) {
    const el = h('div', { class: `toast ${kind}`, role: kind === 'error' ? 'alert' : 'status' },
      icon(kind === 'error' ? 'alert' : 'checkCircle'), h('span', null, text));
    $('#toasts').append(el);
    setTimeout(() => el.remove(), ms || (kind === 'error' ? 6000 : 2600));
  }

  // One modal at a time. Resolves {ok, value}. `field` adds a text box; `required` keeps Confirm disabled until filled.
  function ask({ title, message, field, confirm = 'OK', danger = false }) {
    const dlg = $('#dialog');
    return new Promise((resolve) => {
      let done = false;
      const finish = (ok) => {
        if (done) return;
        done = true;
        const value = input ? input.value.trim() : '';
        dlg.close();
        resolve({ ok, value });
      };
      const input = field ? h(field.multiline ? 'textarea' : 'input', {
        class: field.multiline ? '' : 'text', placeholder: field.placeholder || '', rows: field.multiline ? 4 : null,
        'aria-label': field.placeholder || title, autocomplete: 'off',
      }) : null;
      if (input && field.value) input.value = field.value;
      const ok = h('button', { type: 'button', class: `btn ${danger ? 'danger' : 'primary'}`, onclick: () => finish(true) }, confirm);
      const sync = () => { ok.disabled = !!(field && field.required && !input.value.trim()); };
      if (input) {
        input.addEventListener('input', sync);
        input.addEventListener('keydown', (e) => {
          if (field.multiline ? sendKey(e) : e.key === 'Enter') { e.preventDefault(); if (!ok.disabled) finish(true); }
        });
      }
      sync();
      dlg.replaceChildren(h('div', { class: 'dlg' },
        h('h2', { id: 'dialog-title' }, title),
        message ? h('p', null, message) : null,
        input,
        h('div', { class: 'btn-row' }, h('button', { type: 'button', class: 'btn', onclick: () => finish(false) }, 'Cancel'), ok)));
      // `close` is queued as a task: a late one from the previous dialog must not close this one.
      dlg.onclose = () => { if (!dlg.open) finish(false); };
      dlg.showModal();
      (input || ok).focus();
    });
  }

  function showKeys() {
    const rows = [
      ['1', 'Today'], ['2', 'Listings'], ['3', 'Questions'], ['4', 'Applied'], ['j / k', 'Next / previous row'], ['Enter', 'Open the row or group'],
      ['/', 'Search the inbox'], ['Esc', 'Back to the list, or leave a text box'], ['r', 'Refresh'],
      ['a / s', 'Approve / Skip the open posting'], ['y / n', 'Yes / No on a yes-no question'],
      ['c', 'Write a comment on the open question'], ['Enter', 'Send from a text box'], ['Shift + Enter', 'New line in a text box'],
      ['← / →', 'Previous / next image in the viewer'], ['?', 'This list'],
    ];
    const dlg = $('#dialog');
    dlg.replaceChildren(h('div', { class: 'dlg' }, h('h2', { id: 'dialog-title' }, 'Keyboard shortcuts'),
      h('div', { class: 'keys' }, rows.map(([k, t]) => [h('span', null, kbd(k)), h('span', null, t)])),
      h('div', { class: 'btn-row' }, h('button', { type: 'button', class: 'btn primary', onclick: () => dlg.close() }, 'Done'))));
    dlg.onclose = null;
    dlg.showModal();
  }

  // Full-screen image viewer: [{rel, caption, source_url, source, relevance}], arrows page, Esc closes.
  function openViewer(items, start = 0, opts = {}) {
    const dlg = $('#viewer');
    let i = start;
    const draw = () => {
      const it = items[i];
      const imgBox = h('div', { class: `vimg ${opts.tall ? 'tall' : ''}` }, remoteImg(it.rel, it.caption || 'Image', '', opts.fresh));
      const src = safeUrl(it.source_url);
      dlg.replaceChildren(h('div', { class: 'vwrap' },
        h('div', { class: 'vtop' },
          h('span', { class: 'small' }, items.length > 1 ? `${i + 1} of ${items.length}` : (opts.title || '')),
          h('span', { class: 'grow' }),
          items.length > 1 ? h('button', { type: 'button', class: 'vbtn', 'aria-label': 'Previous image', onclick: () => go(-1) }, '←') : null,
          items.length > 1 ? h('button', { type: 'button', class: 'vbtn', 'aria-label': 'Next image', onclick: () => go(1) }, '→') : null,
          h('button', { type: 'button', class: 'vbtn', onclick: () => dlg.close() }, 'Close')),
        imgBox,
        (it.caption || it.relevance || src) ? h('div', { class: 'vcap' },
          it.caption ? h('div', null, it.caption) : null,
          it.relevance ? h('div', { class: 'r' }, it.relevance) : null,
          src ? h('div', { class: 'r' }, 'Source: ', extLink(src, it.source || src)) : null) : null));
    };
    const go = (d) => { i = (i + d + items.length) % items.length; draw(); };
    dlg.onkeydown = (e) => {
      if (e.key === 'ArrowRight' && items.length > 1) { e.preventDefault(); go(1); }
      if (e.key === 'ArrowLeft' && items.length > 1) { e.preventDefault(); go(-1); }
    };
    draw();
    dlg.showModal();
  }

  // ---------- routing ----------
  function parseRoute() {
    const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
    const id = parts[1] && ID_RE.test(parts[1]) ? parts[1] : null;
    if (parts[0] === 'questions') return { tab: 'questions', id };
    if (parts[0] === 'posting') return { tab: 'inbox', id };
    if (parts[0] === 'inbox' || parts[0] === 'listings') return { tab: 'inbox', id: null };
    if (parts[0] === 'pair') return { tab: 'pair', id: null };
    if (parts[0] === 'applied') return { tab: 'applied', id };
    if (parts[0] === 'preview') return { tab: 'preview', id };
    return { tab: 'today', id: parts[0] === 'today' ? id : null };
  }
  const go = (hash) => { if (location.hash !== hash) location.hash = hash; else render(); };
  const postingHash = (id) => '#/posting/' + encodeURIComponent(id);
  const todayHash = (id) => '#/today/' + encodeURIComponent(id);
  const appliedHash = (id) => '#/applied/' + encodeURIComponent(id);
  const previewHash = (id) => '#/preview/' + encodeURIComponent(id);
  // The list a posting detail goes back to: Today's rows open postings under #/today/<id>, the Inbox's under #/posting/,
  // the Applied tab's under #/applied/<id>.
  const LIST_OF = { today: ['#/today', 'Today'], applied: ['#/applied', 'Applied'], questions: ['#/questions', 'Questions'] };
  const listHash = () => (LIST_OF[S.route.tab] || ['#/inbox'])[0];
  const listName = () => (S.route.tab === 'today' || S.route.tab === 'applied' ? LIST_OF[S.route.tab][1] : 'Listings');
  const isPostingTab = (tab) => tab === 'inbox' || tab === 'today' || tab === 'applied';
  const questionHash = (id) => '#/questions/' + encodeURIComponent(id);

  // ---------- render ----------
  function render(opts = {}) {
    const prev = S.route;
    S.route = parseRoute();
    // A merged follow-up id (an old WhatsApp link) shows the thread that holds it; the URL is fixed in place.
    if (S.route.tab === 'questions' && S.route.id && S.loaded.questions) {
      const shown = displayId(S.route.id);
      if (shown !== S.route.id) { history.replaceState(null, '', questionHash(shown)); S.route.id = shown; }
    }
    const routeChanged = !prev || prev.tab !== S.route.tab || prev.id !== S.route.id;
    if (S.route.tab === 'preview' && prev && prev.tab !== 'preview') S.previewBack = routeHash(prev);
    if (routeChanged && prev) setTimeout(() => { if (S.route.tab === 'preview' && S.route.id) refreshPreview(S.route.id); checkFresh(); }, 0);
    updateChrome();
    const main = $('#main');
    if (S.token && !S.authRefused && S.route.tab === 'preview') {
      S.view = 'preview';
      S.detailSig = null;
      renderPreviewPage(main, S.route.id, opts);
      if (routeChanged) window.scrollTo(0, 0);
      return;
    }
    if (!S.token || S.authRefused || S.route.tab === 'pair') {
      S.view = 'pair';
      S.detailSig = null;
      return renderPair(main);
    }
    const kind = S.route.tab;
    let split = $('.split', main);
    const fresh = !split || S.view !== kind;
    if (fresh) {
      split = h('div', { class: 'split' }, h('section', { class: 'pane-list', 'aria-label': kind === 'today' ? 'Today' : kind === 'inbox' ? 'Postings' : kind === 'applied' ? 'Applications' : 'Questions' }),
        h('section', { class: 'pane-detail', 'aria-label': 'Detail' }));
      main.replaceChildren(split);
      S.view = kind;
      S.detailSig = null;
    }
    split.classList.toggle('has-detail', !!S.route.id);
    const list = $('.pane-list', split), detail = $('.pane-detail', split);
    if (fresh || opts.data || opts.list) {
      if (kind === 'today') renderToday(list); else if (kind === 'inbox') renderInbox(list);
      else if (kind === 'applied') renderApplied(list); else renderQuestionList(list);
    }
    if (routeChanged) S.revealPending = !!S.route.id;
    if (S.revealPending && S.route.id) revealCurrent(list, kind);
    markCurrent(list);
    const sig = detailSignature(kind, S.route.id);
    if (fresh || opts.detail || sig !== S.detailSig || routeChanged) {
      S.detailSig = sig;
      const scroll = routeChanged ? 0 : (WIDE.matches ? detail.scrollTop : window.scrollY);
      if (isPostingTab(kind)) renderPostingDetail(detail, S.route.id); else renderQuestionDetail(detail, S.route.id);
      // A thread opens at its end (the latest ask, its controls and the composer), like any chat; so does a fresh reply.
      const toEnd = kind === 'questions' && S.route.id && (routeChanged || S.scrollThreadEnd);
      S.scrollThreadEnd = false;
      if (toEnd) { if (WIDE.matches) detail.scrollTop = detail.scrollHeight; else window.scrollTo(0, document.documentElement.scrollHeight); }
      else if (WIDE.matches) detail.scrollTop = scroll; else if (routeChanged) window.scrollTo(0, 0);
    }
  }

  function detailSignature(kind, id) {
    if (!id) return `${kind}|none|${S.loaded.postings}|${S.loaded.questions}|${S.loaded.batch && JSON.stringify(todayCounts())}`;
    const rec = isPostingTab(kind) ? S.byId.get(id) : S.qById.get(id);
    const extra = kind === 'questions' && rec ? JSON.stringify([conversation(rec).turns, rec.posting_id && S.byId.has(rec.posting_id)]) : '';
    const rv = isPostingTab(kind) && S.reviews.get(id);
    return `${kind}|${id}|${rec ? JSON.stringify(rec) : 'missing'}|${extra}|${rv ? (rv.data ? 'r' : 'l') : ''}|${S.loaded.postings}|${S.loaded.questions}`;
  }

  function updateChrome() {
    const tab = S.route.tab;
    for (const a of $$('.tabs a')) a.toggleAttribute('aria-current', false);
    const cur = $(`.tabs a[data-tab="${tab}"]`);
    if (cur) cur.setAttribute('aria-current', 'page');
    // Questions: open questions (minus the ones a top row already stands for) plus Ready to submit.
    const hidden = questionsShownAsRows();
    const open = visibleQuestions().filter((q) => q.status === 'open' && !hidden.has(q.id)).length
      + S.postings.filter((p) => ACTION_OF(p) === 'submit').length;
    const badge = $('#qbadge');
    badge.hidden = !open;
    badge.textContent = String(open);
    const left = S.loaded.batch ? todayCounts().open : 0;
    const tbadge = $('#tbadge');
    tbadge.hidden = !left;
    tbadge.textContent = String(left);
    const active = S.postings.filter((p) => p.application && p.application.active).length;
    const abadge = $('#abadge');
    abadge.hidden = !active;
    abadge.textContent = String(active);
    // Listings carries no badge: badges are for things that wait on him (user, 2026-11-01).
    $('#btn-pair').toggleAttribute('aria-current', tab === 'pair');
    chatChrome();
    document.title = open ? `(${open}) Career` : 'Career';
  }

  function markCurrent(list) {
    for (const el of $$('[data-id]', list)) el.setAttribute('aria-current', String(el.dataset.id === S.route.id));
    decorateCopy(list);
  }

  // ---------- "Copy ref": one line that points your agent (terminal or app chat) at a posting or a question ----------
  const jobRef = (p) => `job: ${p.id} · ${p.company} · ${p.title}`;
  const questionRef = (q) => `question: ${q.id} · ${q.title}`;
  function copyRef(text, cls) {
    // A span, not a button: it sits inside row links; it never follows the link.
    return h('span', { class: `copy-ref ${cls || ''}`, role: 'button', tabindex: '0', title: text, 'aria-label': 'Copy ref',
      onclick: (e) => { e.preventDefault(); e.stopPropagation(); copyText(text).then((ok) => toast(ok ? 'Copied' : 'Could not copy', ok ? 'ok' : 'error', 1600)); },
      onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); e.currentTarget.click(); } },
    }, icon('copy'), 'Copy ref');
  }
  // Every list row carries one, added after the list renders: rows know only their data-id.
  function decorateCopy(root) {
    for (const row of $$('a.row[data-id]', root)) {
      if ($(':scope > .copy-ref', row)) continue;
      const id = row.dataset.id;
      const q = S.qById.get(id);
      const p = q ? null : S.byId.get(id);
      if (q) row.append(copyRef(questionRef(q)));
      else if (p) row.append(copyRef(jobRef(p)));
    }
  }

  // Deep links (WhatsApp, j/k) can land on a row inside a closed group: open that group so the row shows.
  function revealCurrent(list, kind) {
    if (kind === 'today') {
      const it = todayItems().find((x) => x.p.id === S.route.id);
      if (!it) return;
      S.revealPending = false;
      if (it.cleared && !S.showCleared) { S.showCleared = true; renderToday(list); }
    } else if (kind === 'inbox') {
      const p = S.byId.get(S.route.id);
      if (!p) return;                         // not loaded yet: try again when the data arrives
      S.revealPending = false;
      if (!$(`.group [data-id="${CSS.escape(p.id)}"]`, list)) {
        const key = sectionOf(p.status) + '|' + p.company;
        if (S.query.trim()) S.searchCollapsed.delete(key); else S.opened.add(key);
        renderInbox(list);
      }
    } else {
      const q = S.qById.get(S.route.id);
      if (!q) return;
      S.revealPending = false;
      if (q.status !== 'open' && !S.showAnswered) { S.showAnswered = true; renderQuestionList(list); }
    }
    const row = $(`[data-id="${CSS.escape(S.route.id)}"]`, list);
    if (row && WIDE.matches) row.scrollIntoView({ block: 'nearest' });
  }

  function loadState(which, label) {
    if (S.errors[which] && !S.loaded[which]) {
      return h('div', { class: 'empty' }, h('div', { class: 'big' }, "Can't reach the server"), h('div', null, S.errors[which]),
        h('div', { class: 'btn-row' }, h('button', { type: 'button', class: 'btn', onclick: () => refreshAll({ announce: true }) }, 'Retry'),
          h('a', { class: 'btn', href: '#/pair' }, 'Pairing')));
    }
    if (!S.loaded[which]) return h('div', { class: 'empty' }, `Loading ${label}…`);
    return null;
  }

  // ---------- today (the batch the coordinating session picked; serve.py GET /batch, tools/batch.py) ----------
  // Rows read the freshest copy of each record (a ruling made here updates S.byId at once), so an item clears the
  // moment it is approved or skipped. Open = status new or enriched, as batch.py counts it.
  const isOpenStatus = (st) => st === 'new' || st === 'enriched';
  function todayItems() {
    const items = (S.batch && Array.isArray(S.batch.items) ? S.batch.items : []).filter((it) => it && it.record && typeof it.record.id === 'string');
    const all = items.map((it) => {
      const p = S.byId.get(it.record.id) || it.record;
      return { p, why: str(it.why), cleared: !isOpenStatus(p.status) };
    });
    return [...all.filter((x) => !x.cleared), ...all.filter((x) => x.cleared)];
  }
  function todayCounts() {
    const items = todayItems();
    const cleared = items.filter((x) => x.cleared).length;
    return { open: items.length - cleared, cleared, total: items.length };
  }
  function nextOpenAfter(id) {
    const items = (S.batch && Array.isArray(S.batch.items) ? S.batch.items : []).map((it) => it && it.record && it.record.id).filter(Boolean);
    const open = items.filter((x) => x !== id && isOpenStatus((S.byId.get(x) || {}).status));
    const i = items.indexOf(id);
    return open.find((x) => items.indexOf(x) > i) || open[0] || null;
  }

  function renderToday(pane) {
    const state = loadState('batch', 'the batch');
    if (state) return pane.replaceChildren(state);
    const items = todayItems();
    const c = todayCounts();
    const out = [];
    const acts = readyList(S.postings);
    if (acts.length) {
      const n = (a) => acts.filter((p) => a.includes(ACTION_OF(p))).length;
      const parts = [[n(['submit']), 'ready to submit'], [n(['answer']), 'need your answer'], [n(['hold']), 'on hold']]
        .filter(([k]) => k).map(([k, t]) => `${k} ${t}`);
      out.push(h('a', { class: 'card hstack today-actions', href: '#/questions' }, icon('plane', 'inline-icon'),
        h('span', { class: 'grow' }, parts.join(' · ')), h('span', { class: 'small' }, 'Questions'), icon('chevron', 'chev')));
    }
    if (!items.length) {
      out.push(h('div', { class: 'empty' }, h('div', { class: 'big' }, 'No batch.'), 'Ask your agent for the next batch.'));
    } else {
      const pct = Math.round((100 * c.cleared) / c.total);
      out.push(h('div', { class: 'today-head' },
        h('div', { class: 'today-count' }, h('span', { class: 'num' }, `${c.cleared} of ${c.total}`), ' cleared'),
        h('div', { class: 'meter', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': String(c.total), 'aria-valuenow': String(c.cleared), 'aria-label': 'Batch cleared' },
          meterFill(pct)),
        str(S.batch.note) ? h('div', { class: 'today-note' }, S.batch.note) : null));
      const open = items.filter((x) => !x.cleared);
      const done = items.filter((x) => x.cleared);
      if (open.length) {
        out.push(h('div', { class: 'sec-head' }, icon('list'), `To clear · ${open.length}`),
          h('div', { class: 'group' }, open.map((x) => todayRow(x))));
      } else {
        out.push(h('div', { class: 'card muted hstack today-done' }, icon('checkCircle', 'inline-icon'), 'Batch cleared. Ask your agent for the next batch.'));
      }
      if (done.length) {
        const toggle = h('button', { type: 'button', class: 'company cleared-toggle', 'aria-expanded': String(S.showCleared),
          onclick: () => { S.showCleared = !S.showCleared; renderToday(pane); markCurrent(pane); const t = $('.cleared-toggle', pane); if (t) t.focus(); },
        }, icon('checkCircle', 'inline-icon'), h('span', { class: 'name' }, `${S.showCleared ? 'Hide' : 'Show'} cleared · ${done.length}`), icon('chevron', 'chev'));
        out.push(h('div', { class: 'sec-head' }, ''), h('div', { class: 'group' }, toggle, S.showCleared ? done.map((x) => todayRow(x)) : null));
      }
    }
    if (S.lastRefreshed) out.push(h('div', { class: 'sec-foot' }, `Updated ${fmtTime.format(S.lastRefreshed)} · press r to refresh`));
    pane.replaceChildren(...out);
  }

  // CSP has no 'unsafe-inline' for styles, so the meter's width is set through the CSSOM, never a style attribute.
  function meterFill(pct) {
    const bar = h('span', { class: 'meter-fill' });
    bar.style.width = `${pct}%`;
    return bar;
  }

  function todayRow({ p, why, cleared }) {
    const d = datesOf(p);
    const chip = actSoon(d) ? dateChip(actChipText(d), d.level) : rowChipText(d) ? dateChip(rowChipText(d), d.level) : null;
    const decided = cleared ? todayStatusChip(p) : null;
    return h('a', { class: `row today-row ${cleared ? 'cleared' : ''}`, href: todayHash(p.id), 'data-id': p.id },
      logoView(logoOf(p), p.company, 28, 6),
      h('span', { class: 'body' },
        h('span', { class: 'title' }, p.title),
        h('span', { class: 'sub' }, [p.company, str(p.location)].filter(Boolean).join(' · ')),
        h('span', { class: 'chips' }, decided, chip, payChip(p), fitBadge(fitOf(p)), siteLabel(p)),
        why ? h('span', { class: 'why' }, why) : null));
  }

  function todayStatusChip(p) {
    const st = p.status;
    if (st === 'skipped') return h('span', { class: 'chip' }, icon('x'), 'skipped');
    if (['approved', 'tailored', 'ready'].includes(st)) return h('span', { class: 'chip green' }, icon('check'), 'approved');
    if (['applied', 'interview', 'offer'].includes(st)) return h('span', { class: 'chip green' }, icon('plane'), 'applied');
    return h('span', { class: 'chip' }, st);
  }

  // ---------- filled postings, sorted by what the user has to do (serve.py review_summary.action) ----------
  // submit: the gate approve-submit enforces is empty, so the row carries an Approve submit button. answer: a box waits
  // on him; the row opens its question. hold: the agent is fixing something or the reviewer has not read the current
  // fill; nothing to do. submitting: approved, so it has left here for the Applied tab. Approved postings that are not filled yet
  // stay under "Approved"; once submitted, a posting moves to the Applied tab.
  const ACTION_OF = (p) => (p && p.status === 'ready' && p.review_summary && typeof p.review_summary === 'object' ? p.review_summary.action : null);
  const needsReview = (p) => ['submit', 'answer', 'hold'].includes(ACTION_OF(p));
  const waitsOnUser = (p) => ['submit', 'answer'].includes(ACTION_OF(p));
  const SUBMIT_YES = 'Submit it';
  const reviewBoxOf = (q) => {
    const to = (q && q.applies_to) || {};
    if (to.review_box) return to.review_box;
    return Array.isArray(to.review_boxes) && to.review_boxes.length === 1 ? to.review_boxes[0] : null;
  };
  const submitTarget = (q) => (q && q.applies_to && typeof q.applies_to.approve_submit === 'string' ? q.applies_to.approve_submit : null);
  // The open submit question for a posting (ask.py applies_to.approve_submit), answered by the row's button.
  const submitQuestionOf = (p) => S.questions.find((q) => q.status === 'open' && q.applies_to && q.applies_to.approve_submit === p.id);
  // Questions a top row already stands for, so each application shows once: the submit question of a Ready to
  // submit row, and the questions a Needs your answer row opens.
  function questionsShownAsRows() {
    const out = new Set();
    for (const p of S.postings) {
      const a = ACTION_OF(p);
      if (a === 'submit' || a === 'submitting') { const q = submitQuestionOf(p); if (q) out.add(q.id); }
      if (a === 'answer') for (const q of p.review_summary.questions || []) if (q && q.id && !S.qById.get(q.id)?.applies_to?.form_question) out.add(q.id);
    }
    return out;
  }
  function readyList(rows) {
    const left = (p) => { const d = datesOf(p).daysLeft; return d == null ? Infinity : d; };
    return rows.filter(needsReview).sort((a, b) => left(a) - left(b) || cmp(a.company, b.company) || cmp(a.title, b.title));
  }
  // A comment on the whole application: an app chat message tied to the posting (POST /chat with posting_id). It
  // reaches the session with the job ref and keeps the application On hold until the session resolves it.
  async function commentOnApplication(p, about) {
    const res = await ask({ title: `Comment on ${p.company}`, message: `${p.title}. Your agent reads it and fixes the application. It stays on hold until then.`,
      field: { multiline: true, required: true, placeholder: 'What should change?' }, confirm: 'Send comment' });
    if (!res.ok || !res.value) return;
    try {
      const text = about ? `${about}: ${res.value}` : res.value;
      chatMerge([await api('/chat', { method: 'POST', body: { text, by: BY, posting_id: p.id } })]);
      toast('Comment sent to your agent');
    } catch (e) { toast(e.message, 'error', 7000); }
    afterPreviewChange(p);
  }
  function dueChip(p) {
    const d = datesOf(p);
    return actSoon(d) ? dateChip(actChipText(d), d.level) : rowChipText(d) ? dateChip(rowChipText(d), d.level) : null;
  }
  function submitItem(p) {
    return h('div', { class: 'ready-item' },
      h('a', { class: 'row', href: previewHash(p.id), 'data-id': p.id },
        logoView(logoOf(p), p.company, 28, 6),
        h('span', { class: 'body' },
          h('span', { class: 'title' }, p.title),
          h('span', { class: 'sub' }, p.company),
          h('span', { class: 'chips' }, dueChip(p)))),
      h('div', { class: 'ready-actions' },
        h('a', { class: 'btn small submit-now', href: previewHash(p.id) }, icon('eye'), 'Review and submit')));
  }
  function answerItem(p) {
    const r = p.review_summary;
    const q = Array.isArray(r.questions) && r.questions[0];
    return h('div', { class: 'ready-item' }, h('a', { class: 'row', href: q && q.id ? questionHash(q.id) : previewHash(p.id), 'data-id': p.id },
      logoView(logoOf(p), p.company, 28, 6),
      h('span', { class: 'body' },
        h('span', { class: 'title' }, p.title),
        h('span', { class: 'sub' }, p.company),
        h('span', { class: 'chips' }, dueChip(p), h('span', { class: 'chip strong state orange' }, icon('question'), r.why || 'A question for you')))));
  }
  function holdItem(p, href) {
    const r = p.review_summary;
    return h('div', { class: 'ready-item' }, h('a', { class: 'row hold-row', href, 'data-id': p.id },
      logoView(logoOf(p), p.company, 28, 6),
      h('span', { class: 'body' },
        h('span', { class: 'title' }, p.title),
        h('span', { class: 'sub' }, [p.company, r.why].filter(Boolean).join(' · ')))));
  }
  // Questions first (user, 2026-11-02): Needs your answer sits with the open questions, the rest goes below them.
  function answerSection(rows) {
    const answer = rows.filter((p) => ACTION_OF(p) === 'answer');
    return answer.length ? [h('div', { class: 'sec-head ready-head' }, icon('question'), `Needs your answer · ${answer.length}`),
      h('div', { class: 'group' }, answer.map((p) => answerItem(p)))] : [];
  }
  function submitAndHoldSections(rows) {
    const by = (a) => rows.filter((p) => ACTION_OF(p) === a);
    const submit = by('submit'), hold = by('hold');
    const out = [];
    if (submit.length) {
      out.push(h('div', { class: 'sec-head ready-head' }, icon('plane'), `Ready to submit · ${submit.length}`),
        h('div', { class: 'group' }, submit.map((p) => submitItem(p))));
    }
    if (hold.length) {
      out.push(h('div', { class: 'sec-head' }, icon('clock'), `On hold · ${hold.length}`),
        h('div', { class: 'group' }, hold.map((p) => holdItem(p, previewHash(p.id)))));
    }
    return out;
  }

  // ---------- application preview: the filled form as the employer receives it (serve.py /postings/<id>/preview) ----
  // The one place a submit is approved from; the button sits after every field. Also the Applied tab's "What was sent".
  function routeHash(r) {
    if (!r) return '#/questions';
    if (r.tab === 'applied') return r.id ? appliedHash(r.id) : '#/applied';
    if (r.tab === 'questions') return r.id ? questionHash(r.id) : '#/questions';
    if (r.tab === 'today') return r.id ? todayHash(r.id) : '#/today';
    if (r.tab === 'inbox') return r.id ? postingHash(r.id) : '#/inbox';
    return '#/questions';
  }

  // The last copy shows at once; a fresh GET replaces it when it lands, and only a real change repaints.
  // `force`: he just changed something, so an answer already on its way is out of date.
  function refreshPreview(id, force) {
    let entry = S.previewDocs.get(id);
    if (!entry) { entry = { data: null, error: null, loading: false, req: 0 }; S.previewDocs.set(id, entry); }
    if (entry.loading && !force) return entry;
    entry.loading = true;
    const req = ++entry.req;
    api(`/postings/${encodeURIComponent(id)}/preview`).then((d) => {
      if (req !== entry.req) return false;
      const changed = JSON.stringify(d) !== JSON.stringify(entry.data);
      entry.data = d;
      entry.error = null;
      return changed;
    }).catch((e) => {
      if (entry.data || req !== entry.req) return false;           // keep showing what he has
      entry.error = e.message;
      return true;
    }).then((changed) => {
      if (req === entry.req) entry.loading = false;
      if (!changed || S.route.tab !== 'preview' || S.route.id !== id) return;
      if (editing()) S.repaint = true; else render();
    });
    return entry;
  }

  function renderPreviewPage(main, id, opts) {
    if (opts && opts.data && S.previewDocs.get(id) && !S.previewDocs.get(id).data) S.previewDocs.delete(id);
    const entry = id ? (S.previewDocs.get(id) || refreshPreview(id)) : null;
    const back = h('button', { type: 'button', class: 'back', onclick: () => go(S.previewBack || '#/questions') }, icon('chevron'), 'Back');
    if (!entry || (!entry.data && !entry.error)) return main.replaceChildren(h('div', { class: 'single stack preview-page' }, back, h('div', { class: 'empty' }, 'Loading the application…')));
    if (entry.error) return main.replaceChildren(h('div', { class: 'single stack preview-page' }, back, h('div', { class: 'empty' }, h('div', { class: 'big' }, 'Could not load the preview'), entry.error)));
    const doc = entry.data;
    if (S.previewShown === doc && $('.preview-page .pv-section', main)) return;   // same document: leave the page alone
    S.previewShown = doc;
    const p = S.byId.get(id) || { id, company: doc.company, title: doc.title };
    const d = datesOf(p);
    const line = detailLine(d);
    const head = h('div', { class: 'preview-head' },
      h('div', { class: 'd-company' }, logoView(logoOf(p), doc.company, 28, 6), doc.company, copyRef(jobRef({ ...p, company: doc.company, title: doc.title }), 'inline')),
      h('h1', { class: 'd-title' }, doc.title),
      h('div', { class: `pv-status ${doc.submitted || /^Everything/.test(doc.status_line || '') ? 'ok' : /^On hold/.test(doc.status_line || '') ? 'hold' : 'act'}` },
        icon(doc.submitted ? 'checkCircle' : /^Everything/.test(doc.status_line || '') ? 'seal' : /^On hold/.test(doc.status_line || '') ? 'clock' : 'question'),
        doc.submitted ? 'What was sent' : noDash(doc.status_line || 'Application preview')),
      line ? h('div', { class: `datebox ${line.hard ? d.level : ''}` }, h('div', { class: `main ${line.hard ? 'hard' : 'soft'}` }, icon(levelIcon(d.level)), line.text)) : null,
      (doc.warnings || []).length ? h('div', { class: 'card warn-card' }, h('div', { class: 'warn-title' }, icon('alert'), 'Read before you submit'),
        h('ul', { class: 'bullets' }, doc.warnings.map((w) => h('li', null, noDash(w))))) : null,
      (doc.open_comments || []).length ? h('div', { class: 'card pv-open' }, h('div', { class: 'warn-title' }, icon('clock'), 'Your comment: being fixed'),
        doc.open_comments.map((c) => h('div', { class: 'ff-your pre' }, c.text))) : null,
      appCommentRow(p));
    const sections = (doc.sections || []).map((sec) => h('section', { class: 'card pv-section' },
      h('h2', null, sec.title),
      sec.groups.map((g) => h('div', { class: `pv-group ${g.title ? 'block' : ''}` },
        g.title ? h('h3', null, g.title) : null,
        g.fields.map((f) => formField(p, doc, f))))));
    if (!sections.length) sections.push(h('div', { class: 'card muted' }, 'Nothing was filled for this posting yet.'));
    const info = doc.info_notes || [];
    if (info.length) {
      sections.push(h('details', { class: 'card pv-info', open: S.previewInfoOpen, ontoggle: (e) => { S.previewInfoOpen = e.currentTarget.open; } }, h('summary', null, `Reviewer notes (${info.length})`),
        h('ul', { class: 'bullets' }, info.map((n) => h('li', null, h('strong', null, n.label + ': '), noDash(n.text || ''))))));
    }
    const shots = doc.screenshots || [];
    const strip = shots.length ? h('section', { class: 'card' }, h('h2', null, doc.submitted ? 'Screenshots' : 'Screenshots of the filled form'),
      h('div', { class: 'pv-shots' }, shots.map((sh, i) => h('button', { type: 'button', class: 'pv-shot', 'aria-label': `Open ${sh.caption}`,
        onclick: () => openViewer(shots.map((x) => ({ rel: x.file, caption: x.caption })), i, { tall: true, fresh: true }) },
      remoteImg(sh.file, sh.caption, '', true), h('span', { class: 'cap' }, sh.caption))))) : null;
    main.replaceChildren(h('div', { class: 'single stack preview-page' }, back, head, sections, strip, previewFooter(p, doc), appCommentRow(p)));
  }

  // One field drawn like the employer's form: label (asterisk when required), then a read-only control. Only a field
  // that waits on him is highlighted with Approve and Comment; any other field takes a comment when tapped.
  function formField(p, doc, f) {
    const box = f.box || null;
    const label = h('div', { class: 'ff-label' }, f.label, f.required ? h('span', { class: 'ff-req', 'aria-label': 'required' }, ' *') : null);
    let control;
    if (f.control === 'file') {
      control = f.kind === 'empty' ? h('div', { class: 'ff-input blank' }, 'No file attached')
        : f.file ? h('button', { type: 'button', class: 'ff-attach', onclick: (e) => { e.stopPropagation(); openBlob(f.file, false); } }, icon('doc'), f.value)
          : h('span', { class: 'ff-attach static' }, icon('doc'), f.value);
    } else if (f.control === 'check') {
      return fieldWrap(p, doc, f, box, [h('div', { class: 'ff-check' }, h('span', { class: `ff-box ${f.kind === 'empty' ? '' : 'on'}` }, f.kind === 'empty' ? '' : '✓'),
        h('span', null, f.label, f.required ? h('span', { class: 'ff-req' }, ' *') : null))]);
    } else if (f.control === 'checks') {
      control = h('div', { class: 'ff-checks' }, (f.options || []).map((o) => h('div', { class: 'ff-check' }, h('span', { class: 'ff-box on' }, '✓'), h('span', null, o))));
    } else if (f.control === 'radio') {
      control = h('div', { class: 'ff-radios' }, (f.options || [f.value]).map((o) => h('span', { class: `ff-radio ${o === f.value ? 'on' : ''}` }, h('span', { class: 'dot' }), o)));
    } else if (f.control === 'note') {
      control = h('div', { class: 'ff-note-text' }, f.value);
    } else {
      control = h('div', { class: `ff-input ${f.control === 'textarea' ? 'area' : ''} ${f.control === 'select' ? 'select' : ''} ${f.kind === 'empty' ? 'blank' : ''}` },
        f.value, f.control === 'select' ? h('span', { class: 'chev' }, '▾') : null);
    }
    return fieldWrap(p, doc, f, box, [label, control]);
  }

  function fieldWrap(p, doc, f, box, kids) {
    const waits = !doc.submitted && !doc.read_only && box && box.waits;
    const notes = box && Array.isArray(box.notes) ? box.notes : [];
    const yours = box && Array.isArray(box.comments) ? box.comments : [];
    // Every field has a visible comment icon. A field with no review box (the skills summary) comments on the application.
    const icn = doc.submitted || doc.read_only ? null : h('button', { type: 'button', class: 'ff-comment', title: 'Comment on this field', 'aria-label': `Comment on ${f.label}`,
      onclick: () => (box && box.key ? boxComment(p, f, box) : commentOnApplication(p, f.label)) }, icon('bubble'));
    // "Edit": he writes the answer himself (review.py use_own_text). Not offered where a refill could not use it.
    const editBtn = !doc.submitted && !doc.read_only && !doc.submit_approved_at && box && f.edit ? h('button', { type: 'button', class: 'ff-edit', title: 'Edit this answer yourself',
      'aria-label': `Edit ${f.label}`, onclick: () => editField(p, f, box) }, icon('pencil'), 'Edit') : null;
    const el = h('div', { class: `ff ${waits ? 'waits' : ''} ${editBtn ? 'editable' : ''}` },
      icn, editBtn, kids,
      yours.map((c) => h('div', { class: 'ff-your pre' }, h('strong', null, 'Your note: '), c.text || '')),
      notes.map((n) => h('div', { class: `ff-flag ${n.severity}` }, icon('alert'), noDash(n.text || ''))),
      waits ? h('div', { class: 'ff-actions' },
        h('button', { type: 'button', class: 'btn small primary', onclick: (e) => { e.stopPropagation(); boxApprove(p, f, box); } }, icon('check'), box.question && box.question.approve === 'Leave it empty' ? 'Leave it empty' : 'Approve'),
        h('button', { type: 'button', class: 'btn small', onclick: (e) => { e.stopPropagation(); boxComment(p, f, box); } }, icon('bubble'), 'Comment')) : null);
    return el;
  }

  // The editor: the form's own options when it has them, else a text box. Enter is a new line here (essays have
  // paragraphs); Ctrl or Cmd + Enter uses the text.
  function editorDialog(f) {
    const dlg = $('#dialog');
    const e = f.edit || {};
    const opts = Array.isArray(e.options) ? e.options : [];
    return new Promise((resolve) => {
      let done = false;
      const finish = (value) => { if (done) return; done = true; dlg.close(); resolve(value); };
      let read, control;
      if (opts.length && e.multi) {
        const have = new Set(String(e.value || '').split(';').map((x) => x.trim()));
        const ticks = opts.map((o) => { const c = h('input', { type: 'checkbox', checked: have.has(o) }); return [o, c, h('label', { class: 'ed-tick' }, c, h('span', null, o))]; });
        control = h('div', { class: 'ed-ticks' }, ticks.map((t) => t[2]));
        read = () => ticks.filter((t) => t[1].checked).map((t) => t[0]).join('; ');
      } else if (opts.length) {
        const sel = h('select', { class: 'ed-select', 'aria-label': f.label }, (opts.includes(e.value) ? opts : [e.value, ...opts]).filter((o) => o !== '').map((o) => h('option', { value: o, selected: o === e.value }, o)));
        control = sel;
        read = () => sel.value;
      } else {
        const ta = h('textarea', { class: 'ed-text', rows: e.long ? 14 : 3, 'aria-label': f.label });
        ta.value = e.value || '';
        ta.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); finish(ta.value.trim()); } });
        control = ta;
        read = () => ta.value.trim();
      }
      dlg.replaceChildren(h('div', { class: 'dlg editor' },
        h('h2', { id: 'dialog-title' }, f.label),
        control,
        h('p', { class: 'small muted' }, 'Your text goes on the form as written. Your agent updates the form, and it can be submitted after that.'),
        h('div', { class: 'btn-row' }, h('button', { type: 'button', class: 'btn', onclick: () => finish(null) }, 'Cancel'),
          h('button', { type: 'button', class: 'btn primary', onclick: () => finish(read()) }, 'Use my text'))));
      dlg.onclose = () => { if (!dlg.open) finish(null); };
      dlg.showModal();
      control.focus();
    });
  }

  async function editField(p, f, box) {
    const text = await editorDialog(f);
    if (text == null) return;
    if (!text) return toast('An answer cannot be empty. Comment if it should be left empty.', 'error', 6000);
    try {
      await api(`/postings/${encodeURIComponent(p.id)}/review/${encodeURIComponent(box.key)}/edit`, { method: 'POST', body: { text, by: BY } });
      toast('Saved. Your agent updates the form with your text.');
    } catch (e) { toast(e.message, 'error', 7000); }
    afterPreviewChange(p);
  }

  // A review question's draft: the same editor, reached from the question.
  async function editFromQuestion(q) {
    const rb = reviewBoxOf(q);
    if (!rb) return;
    try {
      const doc = await api(`/postings/${encodeURIComponent(rb.posting_id)}/preview`);
      const f = (doc.sections || []).flatMap((s) => s.groups).flatMap((g) => g.fields).find((x) => x.box && x.box.key === rb.key);
      if (!f || !f.edit) return toast('This field cannot be edited here. Comment instead.', 'error', 6000);
      await editField(S.byId.get(rb.posting_id) || { id: rb.posting_id, company: doc.company, title: doc.title }, f, f.box);
    } catch (e) { toast(e.message, 'error', 7000); }
  }

  async function afterPreviewChange(p) {
    refreshPreview(p.id, true);
    S.reviews.delete(p.id);
    try { upsertPosting(await api(`/postings/${encodeURIComponent(p.id)}`)); } catch { /* the preview reload says enough */ }
    try { setQuestions(await api('/questions')); } catch { /* next refresh */ }
    render({ data: true });
  }

  async function boxApprove(p, f, box) {
    if (S.busy) return;
    S.busy = true;
    try {
      if (box.question && box.question.approve) {
        const nq = await api(`/questions/${encodeURIComponent(box.question.id)}/answer`, { method: 'POST', body: { answer: box.question.approve, by: BY } });
        if (nq.apply_error) throw new ApiError(nq.apply_error, 409);
      } else {
        await api(`/postings/${encodeURIComponent(p.id)}/review/${encodeURIComponent(box.key)}/approve`, { method: 'POST', body: { by: BY, seen: box.seen || null } });
      }
      toast(`Approved: ${f.label.length > 40 ? f.label.slice(0, 40) + '…' : f.label}`);
    } catch (e) { toast(e.message, 'error', 7000); }
    S.busy = false;
    afterPreviewChange(p);
  }

  async function boxComment(p, f, box) {
    const res = await ask({ title: `Comment on "${f.label.length > 60 ? f.label.slice(0, 60) + '…' : f.label}"`, message: 'Your agent reads it, fixes the field, and shows it again.',
      field: { multiline: true, required: true, placeholder: 'What should change?' }, confirm: 'Send comment' });
    if (!res.ok || !res.value) return;
    try {
      if (box.question && box.question.change) {
        await api(`/questions/${encodeURIComponent(box.question.id)}/answer`, { method: 'POST', body: { answer: box.question.change, note: res.value, by: BY } });
      } else {
        await api(`/postings/${encodeURIComponent(p.id)}/review/${encodeURIComponent(box.key)}/comment`, { method: 'POST', body: { text: res.value, by: BY } });
      }
      toast('Comment sent to your agent');
    } catch (e) { toast(e.message, 'error', 7000); }
    afterPreviewChange(p);
  }

  // The posting detail's pointer to the Application preview (which replaced the per-box "Review every box" cards).
  function applicationCard(p) {
    const r = p.review_summary || {};
    const done = !!p.application && p.application.state !== 'submitting';
    const line = done ? 'Submitted. See every field as it was sent.'
      : r.action === 'submit' ? 'Everything checked, ready to submit'
        : r.action === 'answer' ? (r.why || 'An answer is needed')
          : r.action === 'hold' ? `On hold: ${r.why || ''}`
            : r.action === 'submitting' ? 'Submit approved. Your agent is submitting it.' : 'The filled form, as the employer will receive it';
    return h('div', { class: 'card' }, h('h2', null, done ? 'What was sent' : 'Application'),
      h('div', { class: 'small muted' }, noDash(line)),
      h('a', { class: 'btn primary', href: previewHash(p.id) }, icon('eye'), done ? 'What was sent' : 'Open the application'));
  }

  const appCommentRow = (p) => h('div', { class: 'pv-comment-row' },
    h('button', { type: 'button', class: 'btn', onclick: () => commentOnApplication(p) }, icon('bubble'), 'Comment on this application'));

  function previewFooter(p, doc) {
    if (doc.submitted) {
      const a = p.application;
      return h('div', { class: 'card ok-line' }, icon('checkCircle'), a && a.submitted_at ? `Submitted ${stamp(a.submitted_at)}.` : 'Submitted.');
    }
    if (doc.action === 'submitting') return h('div', { class: 'card ok-line' }, icon('plane'), 'Submit approved. Your agent is submitting it and will tell you.');
    if (doc.status !== 'ready') return h('div', { class: 'card muted' }, `Not filled yet (status ${doc.status}).`);
    const btn = h('button', { type: 'button', class: 'btn primary pv-submit', disabled: !doc.can_submit || S.busy,
      onclick: () => approveSubmit(p, doc).then(() => refreshPreview(p.id, true)) }, icon('plane'), 'Approve submit');
    return h('div', { class: 'card pv-end' },
      h('div', { class: 'small muted' }, 'You have reached the end of the application.'),
      doc.can_submit ? null : h('div', { class: 'warn-line' }, icon('alert'), 'Not ready to submit yet: ' + (doc.gate || []).map(noDash).join('; ')),
      btn);
  }

  // ---------- applied: every application from the submit onward (serve.py `application`) ----------
  // State chip colours: submitted neutral, under review orange, interview blue, offer green, rejected red, closed grey.
  const STATE_CHIP = { submitting: 'accent', submitted: '', under_review: 'orange', interview: 'accent', offer: 'green', rejected: 'red', closed: '' };
  const STATE_ICON = { submitting: 'plane', submitted: 'plane', under_review: 'clock', interview: 'people', offer: 'seal', rejected: 'x', closed: 'x' };
  const actAt = (p) => { const d = new Date(p.application.last_activity_at || p.application.submitted_at || 0); return isNaN(d) ? 0 : +d; };
  function appliedList() {
    const first = (p) => (p.application.state === 'submitting' ? 0 : 1);        // being sent now: on top
    return S.postings.filter((p) => p.application && typeof p.application === 'object')
      .sort((a, b) => first(a) - first(b) || actAt(b) - actAt(a) || cmp(a.company, b.company));
  }
  function stateChip(a) {
    return h('span', { class: `chip strong state ${STATE_CHIP[a.state] || ''}` }, icon(STATE_ICON[a.state] || 'plane'), a.label || a.state);
  }
  const dayOf = (iso) => { const d = iso ? new Date(iso) : null; return d && !isNaN(d) ? shortDay(d) : null; };

  // The row opens the application, whose page carries "What was sent"; a side button here starved the title of width.
  function appliedRow(p) {
    return appliedLink(p);
  }

  function appliedLink(p) {
    const a = p.application;
    const sub = dayOf(a.submitted_at);
    const days = a.days_since_submitted;
    const last = a.last_event && str(a.last_event.text);
    return h('a', { class: 'row applied-row', href: appliedHash(p.id), 'data-id': p.id },
      logoView(logoOf(p), p.company, 28, 6),
      h('span', { class: 'body' },
        h('span', { class: 'title' }, p.title),
        h('span', { class: 'sub' }, [p.company, sub ? `submitted ${sub}` : null,
          days != null ? (days === 0 ? 'today' : `${days} d ago`) : null].filter(Boolean).join(' · ')),
        h('span', { class: 'chips' }, stateChip(a),
          last && !/^submitted\b/i.test(last) ? h('span', { class: 'small muted' }, noDash(last) + (dayOf(a.last_event.at) ? `, ${dayOf(a.last_event.at)}` : '')) : null)));
  }

  function renderApplied(pane) {
    const state = loadState('postings', 'postings');
    if (state) return pane.replaceChildren(state);
    const all = appliedList();
    const active = all.filter((p) => p.application.active), closed = all.filter((p) => !p.application.active);
    const want = S.appliedFilter === 'closed' ? 'closed' : 'active';
    const seg = h('div', { class: 'segmented', role: 'group', 'aria-label': 'Applications shown' },
      [['active', 'Active', active.length], ['closed', 'Closed', closed.length]].map(([v, label, n]) => h('button', {
        type: 'button', class: 'seg', 'aria-pressed': String(want === v),
        onclick: () => { S.appliedFilter = v; renderApplied(pane); markCurrent(pane); },
      }, label, h('span', { class: 'seg-count num' }, String(n)))));
    const rows = want === 'closed' ? closed : active;
    const out = [seg];
    if (!all.length) {
      out.push(h('div', { class: 'empty' }, h('div', { class: 'big' }, 'Nothing submitted yet.'),
        'Applications appear here after you approve the submit.'));
    } else if (!rows.length) {
      out.push(h('div', { class: 'empty' }, want === 'closed' ? 'No closed applications.' : 'No active applications.'));
    } else {
      out.push(h('div', { class: 'sec-head' }, icon('plane'), `${want === 'closed' ? 'Closed' : 'Active'} · ${rows.length}`),
        h('div', { class: 'group' }, rows.map((p) => appliedRow(p))));
    }
    if (S.lastRefreshed) out.push(h('div', { class: 'sec-foot' }, `Updated ${fmtTime.format(S.lastRefreshed)} · press r to refresh`));
    pane.replaceChildren(...out);
  }

  function timelineCard(p) {
    const a = p.application;
    const items = Array.isArray(a.timeline) ? a.timeline : [];
    return h('div', { class: 'card' },
      h('div', { class: 'hstack timeline-head' }, h('h2', null, 'Application'), stateChip(a),
        h('a', { class: 'btn small', href: previewHash(p.id) }, icon('doc'), 'What was sent')),
      h('div', { class: 'small muted' }, [a.state === 'submitting' ? 'Submit approved. Your agent is submitting it.' : a.submitted_at ? `Submitted ${stamp(a.submitted_at)}` : 'Submitted',
        a.days_since_submitted != null ? (a.days_since_submitted === 0 ? 'today' : `${a.days_since_submitted} d ago`) : null].filter(Boolean).join(' · ')),
      items.length ? h('ol', { class: 'timeline' }, items.map((e) => h('li', { class: e.kind === 'email' ? 'email' : '' },
        h('span', { class: 'dot' }), h('span', { class: 'at' }, stamp(e.at)),
        h('span', { class: 'ev' }, noDash(e.text || '')),
        str(e.subject) ? h('span', { class: 'subj' }, e.subject) : null))) : null);
  }

  // ---------- inbox ----------
  const SECTIONS = [['review', 'To review'], ['approved', 'Approved'], ['skipped', 'Skipped'], ['applied', 'Applied'], ['other', 'Other']];
  function sectionOf(status) {
    if (status === 'new' || status === 'enriched') return 'review';
    if (status === 'approved' || status === 'tailored' || status === 'ready') return 'approved';
    if (status === 'skipped') return 'skipped';
    if (['applied', 'interview', 'offer', 'rejected', 'closed'].includes(status)) return 'applied';
    return 'other';
  }
  const cmp = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });

  function groupByCompany(rows) {
    const m = new Map();
    for (const p of rows) { if (!m.has(p.company)) m.set(p.company, []); m.get(p.company).push(p); }
    const rank = (p) => { const f = fitOf(p); return f ? FIT_RANK[f] : 3; };
    return Array.from(m, ([company, list]) => ({
      company,
      rows: list.sort((a, b) => rank(a) - rank(b) || cmp(a.title, b.title)),
      direct: list.filter((p) => fitOf(p) === 'direct').length,
      logo: list.map(logoOf).find(Boolean) || null,
    })).sort((a, b) => b.direct - a.direct || cmp(a.company, b.company));
  }

  function renderInbox(pane) {
    let search = $('input.search', pane);
    let body = $('.list-body', pane);
    if (!search) {
      search = h('input', { class: 'search', type: 'search', placeholder: 'Company or title  ( / )', 'aria-label': 'Search postings', autocomplete: 'off' });
      search.value = S.query;
      search.addEventListener('input', () => {
        S.query = search.value;
        S.searchCollapsed.clear();
        renderInbox(pane);
        markCurrent(pane);
      });
      body = h('div', { class: 'list-body' });
      pane.replaceChildren(search, body);
    }
    const state = loadState('postings', 'postings');
    if (state) return body.replaceChildren(state);

    const q = S.query.trim().toLowerCase();
    const searching = !!q;
    const match = (p) => String(p.company).toLowerCase().includes(q) || String(p.title).toLowerCase().includes(q);
    const all = S.postings.filter((p) => p.status !== 'variant');
    const mine = q ? all.filter(match) : all;
    const out = [];

    if (!all.length) {
      out.push(h('div', { class: 'empty' }, h('div', { class: 'big' }, 'No postings yet'), 'Your agent has not found anything to review.'));
    } else if (!mine.length) {
      out.push(h('div', { class: 'empty' }, h('div', { class: 'big' }, searching ? 'No matches here' : 'No job postings'), searching ? `Nothing matches “${S.query.trim()}”.` : ''));
    }

      // Act soon: urgency now + week across companies, soonest first. Three or more from one company fold into one line.
      const urgent = mine.map((p) => [p, datesOf(p)]).filter(([, d]) => actSoon(d)).sort(([pa, a], [pb, b]) => {
        if (a.actBy && b.actBy && +a.actBy !== +b.actBy) return a.actBy - b.actBy;
        if (a.actBy && !b.actBy) return -1;
        if (!a.actBy && b.actBy) return 1;
        const da = a.daysLeft == null ? Infinity : a.daysLeft, db = b.daysLeft == null ? Infinity : b.daysLeft;
        return da - db || cmp(pa.company, pb.company);
      });
      if (urgent.length) {
        const dates = new Map(urgent);
        out.push(h('div', { class: 'sec-head' }, icon('alarm'), `Act soon · ${urgent.length}`));
        out.push(foldedGroup(urgent.map(([p]) => p), searching, pane, (p, indent) => actRow(p, dates.get(p), indent),
          (p) => dateChip(actChipText(dates.get(p)), dates.get(p).level), 'reqs'));
      }
      for (const [key, title] of SECTIONS) {
        const rows = mine.filter((p) => sectionOf(p.status) === key);
        if (!rows.length) continue;
        out.push(h('div', { class: 'sec-head' }, `${title} · ${rows.length}`));
        const box = h('div', { class: 'group' });
        for (const g of groupByCompany(rows)) {
          const gk = key + '|' + g.company;
          const open = searching ? !S.searchCollapsed.has(gk) : S.opened.has(gk);
          const hot = g.rows.map(datesOf).filter(actSoon);
          const level = hot.some((d) => d.level === 'now') ? 'now' : 'week';
          box.append(h('button', {
            type: 'button', class: 'company', 'aria-expanded': String(open), 'data-gk': gk,
            'aria-label': `${g.company}, ${g.rows.length} ${g.rows.length === 1 ? 'posting' : 'postings'}`,
            onclick: () => {
              if (searching) { if (open) S.searchCollapsed.add(gk); else S.searchCollapsed.delete(gk); }
              else if (open) S.opened.delete(gk); else S.opened.add(gk);
              renderInbox(pane);
              markCurrent(pane);
              const again = $$('button.company', pane).find((b) => b.dataset.gk === gk);
              if (again) again.focus();
            },
          }, logoView(g.logo, g.company, 40, 8), h('span', { class: 'name' }, g.company),
          hot.length ? h('span', { class: `chip strong ${level}`, title: 'Postings with urgency now or this week' }, icon('alarm'), `${hot.length} act soon`) : null,
          h('span', { class: 'pill' }, String(g.rows.length)), icon('chevron', 'chev')));
          if (open) for (const p of g.rows) box.append(postingRow(p));
        }
        out.push(box);
      }
    if (S.lastRefreshed) out.push(h('div', { class: 'sec-foot' }, `Updated ${fmtTime.format(S.lastRefreshed)} · press r to refresh`));
    body.replaceChildren(...out);
  }

  // An Act-soon list where three or more rows from one company or organizer fold into one line that opens.
  function foldedGroup(rows, searching, pane, rowFor, chipFor, noun) {
    const byCo = new Map();
    for (const p of rows) { if (!byCo.has(p.company)) byCo.set(p.company, []); byCo.get(p.company).push(p); }
    const group = h('div', { class: 'group' });
    const done = new Set();
    for (const p of rows) {
      const mates = byCo.get(p.company);
      if (mates.length < 3) { group.append(rowFor(p, false)); continue; }
      if (done.has(p.company)) continue;
      done.add(p.company);
      const key = S.inboxView + '|' + p.company;
      const open = searching || S.actOpen.has(key);
      group.append(h('button', { type: 'button', class: 'company', 'aria-expanded': String(open), 'data-gk': 'act|' + key,
        onclick: () => {
          if (S.actOpen.has(key)) S.actOpen.delete(key); else S.actOpen.add(key);
          renderInbox(pane);
          markCurrent(pane);
          const again = $$('button.company', pane).find((b) => b.dataset.gk === 'act|' + key);
          if (again) again.focus();
        },
      }, logoView(logoOf(p), p.company, 28, 6), h('span', { class: 'name' }, `${p.company} · ${mates.length} ${noun}`),
      chipFor(p), icon('chevron', 'chev')));
      if (open) for (const m of mates) group.append(rowFor(m, true));
    }
    return group;
  }

  function actRow(p, d, indent) {
    return h('a', { class: `row ${indent ? 'indent' : ''}`, href: postingHash(p.id), 'data-id': p.id },
      logoView(logoOf(p), p.company, 28, 6),
      h('span', { class: 'body' }, h('span', { class: 'title' }, p.title),
        h('span', { class: 'chips' }, dateChip(actChipText(d), d.level), payChip(p), h('span', { class: 'small muted' }, p.company), siteLabel(p))));
  }

  function postingRow(p) {
    const d = datesOf(p);
    const chip = rowChipText(d);
    const img = Array.isArray(p.company_info && p.company_info.images) && p.company_info.images.find((x) => x && str(x.file));
    const flags = arr(p.summary_ai && p.summary_ai.flags).length;
    const variants = (Array.isArray(p.variants) ? p.variants : []).map((v) => v && str(v.label || v.id)).filter(Boolean);
    return h('a', { class: 'row indent', href: postingHash(p.id), 'data-id': p.id },
      img ? remoteImg('data/postings/' + str(img.file), '', 'thumb') : null,
      h('span', { class: 'body' },
        h('span', { class: 'title' }, p.title),
        str(p.location) ? h('span', { class: 'sub' }, p.location) : null,
        variants.length ? h('span', { class: 'sub' }, 'Also posted as: ' + variants.join(', ')) : null,
        h('span', { class: 'chips' }, chip ? dateChip(chip, d.level) : null, payChip(p), fitBadge(fitOf(p)),
          flags ? h('span', { class: 'chip', title: `${flags} flags` }, icon('flag'), String(flags)) : null, siteLabel(p))));
  }

  // ---------- posting detail ----------
  function backButton(hash, label) {
    return h('button', { type: 'button', class: 'back', onclick: () => go(hash) }, icon('chevron'), label);
  }

  function renderPostingDetail(pane, id) {
    if (!id && S.route.tab === 'today') {
      const c = todayCounts();
      return pane.replaceChildren(h('div', { class: 'placeholder' }, !S.loaded.batch ? '' : !c.total ? 'No batch. Ask your agent for the next batch.'
        : c.open ? `Pick a posting. ${c.open} of ${c.total} left in this batch.` : 'Batch cleared. Ask your agent for the next batch.'));
    }
    if (!id && S.route.tab === 'applied') {
      const n = appliedList().length;
      return pane.replaceChildren(h('div', { class: 'placeholder' }, !S.loaded.postings ? '' : n ? 'Pick an application to see its timeline.'
        : 'Nothing submitted yet. Applications appear here after you approve the submit.'));
    }
    if (!id) {
      const n = S.postings.filter((p) => sectionOf(p.status) === 'review').length;
      return pane.replaceChildren(h('div', { class: 'placeholder' }, S.loaded.postings ? `Pick a posting. ${n} postings to review.` : ''));
    }
    const p = S.byId.get(id);
    if (!p) {
      return pane.replaceChildren(backButton(listHash(), listName()),
        S.loaded.postings ? h('div', { class: 'empty' }, h('div', { class: 'big' }, 'Posting not found'), id) : h('div', { class: 'empty' }, 'Loading…'));
    }
    const images = (Array.isArray(p.company_info && p.company_info.images) ? p.company_info.images : []).filter((x) => x && str(x.file));
    const showReview = showsReview(p);
    const review = showReview ? reviewFor(p) : null;
    const inner = h('div', { class: 'detail-inner stack' },
      h('div', null, backButton(listHash(), listName()), postingHeader(p)),
      p.application ? timelineCard(p) : null,
      showReview ? applicationCard(p) : null,
      images.length ? imageGrid(images) : null,
      p.summary_ai ? summaryCard(p.summary_ai) : pendingCard('Summary not generated yet'),
      postingCard(p),
      p.company_info ? companyCard(p.company_info, p.company) : pendingCard('Company research not generated yet'),
      materialsCard(p),
      Array.isArray(p.history) && p.history.length ? historyCard(p.history) : null);
    pane.replaceChildren(inner, actionBar(p, review));
  }

  function postingHeader(p) {
    const d = datesOf(p);
    const line = detailLine(d);
    const variants = (Array.isArray(p.variants) ? p.variants : []).filter((v) => v && str(v.label || v.id));
    const r = p.ruling || {};
    return h('div', null,
      h('div', { class: 'd-company' }, logoView(logoOf(p), p.company, 28, 6), p.company, copyRef(jobRef(p), 'inline')),
      h('h1', { class: 'd-title' }, p.title),
      websiteOf(p) ? h('div', { class: 'line website' }, icon('globe'), extLink(websiteOf(p))) : null,
      str(p.location) ? h('div', { class: 'line' }, icon('pin'), p.location) : null,
      payLine(p),
      (line || d.note) ? h('div', { class: `datebox ${line && line.hard ? d.level : ''}` },
        line ? h('div', { class: `main ${line.hard ? 'hard' : 'soft'}` }, icon(levelIcon(d.level)), line.text) : null,
        d.note ? h('div', { class: 'note' }, d.note) : null,
        !d.note && d.reason && actSoon(d) ? h('div', { class: 'note' }, d.reason) : null) : null,
      variants.length ? h('div', { class: 'line' }, 'Also posted as: ',
        variants.map((v, i) => [i ? ', ' : '', safeUrl(v.url) ? extLink(v.url, str(v.label || v.id)) : str(v.label || v.id)])) : null,
      h('div', { class: 'hstack', style: 'margin-top:8px' }, statusBadge(p.status), fitBadge(fitOf(p)), h('span', { class: 'grow' }),
        safeUrl(p.url) ? h('a', { href: safeUrl(p.url), target: '_blank', rel: 'noopener noreferrer', class: 'small' }, 'Open posting ', icon('external', 'inline-icon')) : null),
      r.decision ? h('div', { class: 'small muted', style: 'margin-top:6px' },
        `Ruled ${r.decision}` + (str(r.reason) ? `: ${r.reason}` : '') + (r.by ? ` (${r.by})` : '')) : null);
  }

  function payLine(p) {
    const raw = str(p.posting && p.posting.pay);
    if (!raw) return null;
    const n = payOf(p);
    return h('div', { class: 'payline' },
      h('div', { class: 'line' }, icon('cash'), n ? h('span', { class: 'num strong' }, payParts(n)) : raw),
      n ? h('div', { class: 'note' }, `Posting says: ${raw}`) : null);
  }

  function imageGrid(images) {
    const items = images.map((x) => ({ rel: 'data/postings/' + str(x.file), caption: str(x.caption), source_url: x.source_url, source: str(x.source), relevance: str(x.relevance) }));
    return h('div', { class: `images ${images.length === 1 ? 'one' : ''}` }, items.map((it, i) =>
      h('button', { type: 'button', class: 'imgcard', 'aria-label': it.caption || `Company image ${i + 1}`, onclick: () => openViewer(items, i) },
        h('div', { class: 'frame' }, remoteImg(it.rel, '', '')),
        it.caption ? h('div', { class: 'cap' }, it.caption) : null)));
  }

  function aiLabel(text) { return h('div', { class: 'ai-label' }, icon('cpu'), noDash(text)); }
  const pendingCard = (t) => h('div', { class: 'card muted' }, t);

  function summaryCard(s) {
    const reasons = arr(s.fit_reasons), flags = arr(s.flags);
    const fit = s.fit in FIT_RANK ? s.fit : null;
    return h('div', { class: 'card' },
      aiLabel(str(s.label) || 'AI-generated summary'),
      str(s.text) ? h('p', null, firstSentences(s.text.trim(), 3)) : null,
      fit || reasons.length ? h('div', { class: 'chips' }, fitBadge(fit), reasons.map((r) => h('span', { class: 'chip wrap' }, r))) : null,
      flags.length ? h('div', { class: 'chips' }, flags.map((f) => h('span', { class: 'chip wrap flag' }, /^F-?1/.test(f) ? icon('flag') : null, f))) : null,
      s.model ? h('div', { class: 'tiny faint' }, `model ${s.model}` + (s.generated_at ? ` · ${s.generated_at}` : '')) : null);
  }

  function companyCard(c, company) {
    const facts = [c.hq && `HQ ${c.hq}`, c.size && `size ${c.size}`, c.ats && `ATS ${c.ats}`].filter(Boolean);
    const links = arr(c.sources);
    if (str(c.careers_url) && !links.includes(c.careers_url)) links.push(c.careers_url);
    const recent = arr(c.recent), people = arr(c.people);
    const more = facts.length || recent.length || people.length || links.length;
    const site = safeUrl(c.website);
    return h('div', { class: 'card' },
      aiLabel(str(c.label) || 'AI-researched'),
      site ? h('div', { class: 'line website' }, icon('globe'), extLink(site)) : null,
      str(c.what_they_do) ? h('p', null, c.what_they_do) : null,
      str(c.robotics_work) ? h('p', null, c.robotics_work) : null,
      more ? h('details', null, h('summary', null, `More about ${company}`, icon('chevron', 'chev')),
        h('div', { class: 'inside' },
          facts.length ? h('div', { class: 'small muted' }, facts.join(' · ')) : null,
          recent.length ? [h('div', { class: 'sub-head' }, 'Recent'), bullets(recent)] : null,
          people.length ? [h('div', { class: 'sub-head' }, 'People'), bullets(people)] : null,
          links.length ? [h('div', { class: 'sub-head' }, 'Sources'), h('div', { class: 'linklist' }, links.map((u) => extLink(u)))] : null)) : null);
  }

  function postingCard(p) {
    const b = p.posting;
    let inside;
    if (b && typeof b === 'object') {
      const facts = [b.term && `Term ${b.term}`, b.duration, b.degree && `Degree ${b.degree}`, b.deadline ? `Deadline ${b.deadline}` : 'Deadline not stated', b.team].filter((x) => typeof x === 'string' && x);
      inside = [
        h('div', { class: 'small muted' }, facts.join(' · ')),
        str(b.description) ? h('div', { class: 'pre' }, b.description) : null,
        arr(b.responsibilities).length ? [h('div', { class: 'sub-head' }, 'Responsibilities'), bullets(arr(b.responsibilities))] : null,
        arr(b.requirements).length ? [h('div', { class: 'sub-head' }, 'Requirements'), bullets(arr(b.requirements))] : null,
        arr(b.preferred).length ? [h('div', { class: 'sub-head' }, 'Preferred'), bullets(arr(b.preferred))] : null,
        h('div', { class: 'sub-head' }, 'Work authorization'),
        str(b.work_authorization) ? h('blockquote', { class: 'quote' }, `“${b.work_authorization}”`) : h('div', { class: 'muted' }, 'Posting says nothing.'),
        str(b.pay) ? [h('div', { class: 'sub-head' }, 'Pay'), h('div', null, b.pay)] : null,
        b.fetched_at ? h('div', { class: 'tiny faint' }, `fetched ${b.fetched_at}`) : null,
      ];
    } else {
      inside = [h('div', { class: 'muted' }, 'Posting text not fetched yet.')];
    }
    const orig = safeUrl((b && b.apply_url) || p.url);
    return h('div', { class: 'card' }, h('details', { open: '' },
      h('summary', null, icon('doc', 'inline-icon'), 'Job description', icon('chevron', 'chev')),
      h('div', { class: 'inside' }, inside,
        orig ? h('a', { href: orig, target: '_blank', rel: 'noopener noreferrer', class: 'small' }, 'Open the original posting ', icon('external')) : null)));
  }

  const MATERIAL_ROWS = [
    ['resume_pdf', 'Resume (PDF)'], ['figure_png', 'Figure (PNG)'], ['clip_mp4', 'Clip (MP4)'],
    ['cover_letter_md', 'Cover letter'], ['form_answers_md', 'Form answers'],
  ];
  const APPLY_ROWS = [
    ['form_screenshot', 'Filled form (screenshot)'], ['form_questions_md', 'Form questions'], ['form_fill_json', 'Form fill (JSON)'],
    ['confirmation_png', 'Confirmation page'], ['confirmation_txt', 'Confirmation text'],
  ];

  function materialsCard(p) {
    const m = (p.materials && typeof p.materials === 'object') ? p.materials : {};
    const rows = MATERIAL_ROWS.map(([k, n]) => [n, str(m[k])]).concat(APPLY_ROWS.filter(([k]) => str(m[k])).map(([k, n]) => [n, str(m[k])]));
    return h('div', { class: 'card' }, h('h2', null, 'Materials'), h('div', null, rows.map(([name, rel]) => materialRow(p, name, rel))));
  }

  function materialRow(p, name, rel) {
    if (!rel) return h('div', { class: 'mat' }, h('span', { class: 'name' }, name), h('span', { class: 'small faint' }, 'not yet generated'));
    const key = p.id + '|' + rel;
    const box = h('div', { class: 'preview', hidden: !S.previews.get(key) });
    const toggle = h('button', { type: 'button', class: 'btn small', 'aria-expanded': String(!!S.previews.get(key)) }, icon('eye'), S.previews.get(key) ? 'Hide' : 'Preview');
    const fill = () => { box.replaceChildren(h('div', { class: 'small muted', style: 'padding:12px' }, 'Loading…')); fillPreview(box, rel, name); };
    toggle.addEventListener('click', () => {
      const open = !S.previews.get(key);
      S.previews.set(key, open);
      box.hidden = !open;
      toggle.setAttribute('aria-expanded', String(open));
      toggle.replaceChildren(icon('eye'), open ? 'Hide' : 'Preview');
      if (open) fill();
    });
    if (S.previews.get(key)) fill();
    const file = rel.split('/').pop();
    return h('div', { class: 'mat' },
      h('span', { class: 'name' }, name, h('span', { class: 'file' }, file)),
      toggle,
      h('button', { type: 'button', class: 'btn small', onclick: () => openBlob(rel, false) }, icon('external'), 'Open'),
      h('button', { type: 'button', class: 'btn small', 'aria-label': `Download ${file}`, onclick: () => openBlob(rel, true) }, icon('download')),
      box);
  }

  async function openBlob(rel, download) {
    try {
      const f = await fetchFile(rel, true);
      const a = h('a', { href: f.url, target: '_blank', rel: 'noopener' });
      if (download) a.setAttribute('download', rel.split('/').pop());
      document.body.append(a);
      a.click();
      a.remove();
    } catch (e) { toast(`Could not open ${rel.split('/').pop()}: ${e.message}`, 'error'); }
  }

  async function fillPreview(box, rel, title) {
    const ext = (rel.split('.').pop() || '').toLowerCase();
    try {
      const f = await fetchFile(rel, true);
      let el;
      if (ext === 'pdf') el = h('iframe', { src: f.url, title });
      else if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(ext)) {
        el = h('img', { src: f.url, alt: title, style: 'cursor:zoom-in' });
        el.addEventListener('click', () => openViewer([{ rel, caption: title }], 0, { tall: true, title }));
      } else if (['mp4', 'mov', 'm4v', 'webm'].includes(ext)) el = h('video', { src: f.url, controls: true, preload: 'metadata' });
      else if (['md', 'markdown'].includes(ext)) el = renderMarkdown(await f.blob.text());
      else if (ext === 'json') {
        let t = await f.blob.text();
        try { t = JSON.stringify(JSON.parse(t), null, 2); } catch { /* show as is */ }
        el = h('div', { class: 'txt' }, t);
      } else if (['txt', 'yml', 'yaml', 'csv', 'log', 'tex'].includes(ext)) el = h('div', { class: 'txt' }, await f.blob.text());
      else el = h('div', { class: 'small muted', style: 'padding:12px' }, 'No inline preview for this file type. Use Open or Download.');
      box.replaceChildren(el);
    } catch (e) {
      box.replaceChildren(h('div', { class: 'warn-line', style: 'padding:12px' }, icon('alert'), `Could not load the file: ${e.message}`));
    }
  }

  function historyCard(items) {
    return h('div', { class: 'card' }, h('h2', null, 'History'),
      h('div', { class: 'history' }, items.map((it) => [h('span', { class: 'at' }, (it && it.at) || ''), h('span', { class: 'ev' }, (it && it.event) || '')])));
  }

  // ---------- review card (port of ApplicationReview.swift) ----------
  function showsReview(p) {
    if (p.status === 'ready') return true;
    const m = p.materials || {};
    return sectionOf(p.status) === 'applied' && !!(m.confirmation_png || m.confirmation_txt || p.submission);
  }
  const reviewKey = (p) => [p.status, (p.ruling || {}).submit_approved_at || '', (p.materials || {}).form_fill_json || '', (p.materials || {}).confirmation_txt || ''].join('|');

  function reviewFor(p) {
    const key = reviewKey(p);
    const cur = S.reviews.get(p.id);
    if (cur && cur.key === key) return cur.data;
    const entry = { key, data: null };
    S.reviews.set(p.id, entry);
    loadReview(p).then((data) => {
      if (S.reviews.get(p.id) !== entry) return;
      entry.data = data;
      if (isPostingTab(S.route.tab) && S.route.id === p.id) render({ detail: true });
    });
    return null;
  }

  async function loadReview(p) {
    const r = { fill: null, gate: null, questions: [], confirmationText: null, reviewText: null, error: null, boxes: null };
    const m = p.materials || {};
    if (p.status === 'ready') {
      try { r.fill = await api(`/postings/${encodeURIComponent(p.id)}/fill`); } catch (e) { r.error = e.message; }
      // The per-box review (tools/review.py); 404 until the session runs `review.py build`.
      try { r.boxes = await api(`/postings/${encodeURIComponent(p.id)}/review`); } catch (e) { if (e.status !== 404) r.boxesError = e.message; }
      try { r.gate = await api(`/postings/${encodeURIComponent(p.id)}/approve-submit`); } catch { r.gate = localGate(p, r.fill); }
      if (str(m.form_questions_md)) { try { r.questions = parseFormQuestions(await fileText(m.form_questions_md)); } catch { /* optional */ } }
      if (str(m.review_txt)) { try { r.reviewText = (await fileText(m.review_txt)).trim(); } catch { /* optional */ } }
    }
    if (str(m.confirmation_txt)) { try { r.confirmationText = (await fileText(m.confirmation_txt)).trim(); } catch { /* optional */ } }
    return r;
  }

  const needsUser = (row) => String((row && row.status) || '').includes('needs user');
  const fillRows = (fill) => (fill && Array.isArray(fill.rows) ? fill.rows.filter((x) => x && typeof x === 'object') : []);

  function localGate(p, fill) {
    const missing = [];
    if (p.status !== 'ready') missing.push(`status is '${p.status}', must be 'ready'`);
    if ((p.ruling || {}).decision !== 'approved') missing.push('the posting is not approved');
    if (fill) {
      if (fill.dry_run) missing.push('the form was only filled as a rehearsal (--dry-run)');
      const nu = fillRows(fill).filter(needsUser);
      if (nu.length) missing.push(`${nu.length} field(s) still need the user: ` + nu.map((x) => `"${x.label || '?'}"`).join('; '));
    } else missing.push('no form_fill.json yet');
    return { allowed: !missing.length, missing };
  }

  function approval(p, review) {
    const at = (p.ruling || {}).submit_approved_at;
    if (at && ACTION_OF(p) !== 'hold') {                    // a comment after the approval holds it
      const filled = review && review.fill && review.fill.filled_at;
      if (!(filled && at < filled)) return { kind: 'approved', at, by: (p.ruling || {}).submit_approved_by };
    }
    if (!review || !review.gate) return { kind: 'loading' };
    const missing = Array.isArray(review.gate.missing) ? review.gate.missing : [];
    const sm = review.boxes && review.boxes.summary;
    if (sm && !sm.all_approved) {
      return { kind: 'blocked', reason: `${sm.approved} of ${sm.total} boxes approved` + (sm.blocked ? `, ${sm.blocked} flagged block` : ''), missing };
    }
    if (review.gate.allowed) return { kind: 'allowed' };
    return { kind: 'blocked', reason: missing.length ? missing[0] + (missing.length > 1 ? ` (+${missing.length - 1} more)` : '') : 'The server refused the approval.', missing };
  }

  function parseFormQuestions(md) {
    const out = [];
    let cur = null, body = [];
    const flush = () => { if (cur != null) { const a = body.join('\n').trim(); out.push({ question: cur, answer: a || null }); } };
    for (const line of md.split(/\r?\n/)) {
      if (line.startsWith('### ')) { flush(); cur = line.slice(4).trim(); body = []; }
      else if (cur != null) { const t = line.trim(); if (t.startsWith('<!--') && t.endsWith('-->')) continue; body.push(line); }
    }
    flush();
    return out;
  }

  function sourceText(row) {
    if (needsUser(row)) return row.question ? 'answer in questions file' : 'needs user';
    const s = str(row.source);
    if (!s) return 'none';
    const i = s.indexOf(': ');
    if (i < 0) return s;
    const key = s.slice(i + 2).split('.').pop() || '';
    return `${s.slice(0, i)} · ${key.replace(/_/g, ' ')}`;
  }

  function fieldValue(row) {
    if (needsUser(row)) return h('span', { class: 'warn' }, '● ', row.value != null && row.value !== '' ? String(row.value) : 'needs you');
    let v = row.value;
    if (typeof v === 'boolean') v = v ? 'Yes' : 'No';
    if (v == null || v === '') return h('span', { class: 'faint' }, 'blank');
    v = String(v);
    if (row.type === 'file') v = v.split('/').pop();
    return [v, row.verified ? h('span', { class: 'ok', title: 'verified on the page', 'aria-label': 'verified on the page' }, ' ✓') : null];
  }

  function reviewCard(p, review) {
    const m = p.materials || {};
    const card = h('div', { class: 'card' });
    if (p.status === 'ready') {
      const r = p.ruling || {};
      card.append(h('h2', { class: 'hstack' }, icon('doc', 'inline-icon'), 'Review application'),
        h('p', { class: 'small muted' }, 'Your agent filled the form and stopped before Submit. Check it, then approve the submit below.'));
      if (r.submit_approved_at) card.append(h('div', { class: 'ok-line' }, icon('seal'), `Submit approved ${stamp(r.submit_approved_at)}` + (r.submit_approved_by ? ` by ${r.submit_approved_by}` : '')));
      const shotRel = str(m.form_screenshot) || (review && review.fill && str(review.fill.screenshot));
      if (shotRel) {
        card.append(h('button', { type: 'button', class: 'shot', 'aria-label': 'Filled form, open full size', onclick: () => openViewer([{ rel: shotRel, caption: 'Filled form: Submit not pressed' }], 0, { tall: true, fresh: true, title: 'Filled form' }) },
          remoteImg(shotRel, '', '', true), h('span', { class: 'badge' }, 'Full size')),
        h('div', { class: 'small muted' }, 'Filled form: Submit not pressed'));
      }
      // Workday: every wizard step as filled (the last is the Review page), so the whole application can be seen.
      const steps = (Array.isArray(m.workday_steps) ? m.workday_steps : []).map(str).filter(Boolean);
      if (steps.length) {
        const names = ((review && review.fill && review.fill.steps) || []).map((s) => str(s && s.name));
        const items = steps.map((rel, i) => ({ rel, caption: `Step ${i + 1} of ${steps.length}` + (names[i] ? `: ${names[i]}` : '') }));
        card.append(h('div', { class: 'hstack', style: 'margin:6px 0 10px' },
          h('button', { type: 'button', class: 'btn', onclick: () => openViewer(items, 0, { tall: true, fresh: true, title: 'Application steps' }) },
            `View every step (${steps.length})`),
          h('span', { class: 'small muted' }, 'each page of the application as filled; the last is Workday\'s Review page')));
      }
      if (review && review.reviewText) {
        card.append(h('details', null, h('summary', null, 'Review page text (what will be sent)', icon('chevron', 'chev')),
          h('div', { class: 'inside' }, h('div', { class: 'txt mono small muted pre' }, review.reviewText))));
      }
      if (!review) card.append(h('div', { class: 'small muted' }, 'Loading the field list…'));
      else if (review.boxes) card.append(boxesSection(p, review));
      else {
        if (review.boxesError) card.append(h('div', { class: 'warn-line' }, icon('alert'), `Could not load the box review: ${review.boxesError}`));
        if (review.fill) {
          const f = review.fill;
          const meta = [f.filled_at && `filled ${stamp(f.filled_at)}`, f.submit_control && f.submit_control.text && `stops before “${f.submit_control.text}”`].filter(Boolean);
          if (meta.length) card.append(h('div', { class: 'small muted' }, meta.join(' · ')));
          if (f.dry_run) card.append(h('div', { class: 'warn-line' }, icon('alert'), 'Rehearsal fill (--dry-run). Run apply for real before approving.'));
          const rows = fillRows(f);
          const nu = rows.filter(needsUser).length;
          card.append(h('div', { class: 'hstack' }, h('h2', null, 'Fields'), h('span', { class: 'grow' }),
            h('span', { class: nu ? 'small warn' : 'small muted' }, nu ? `${nu} of ${rows.length} need you` : `${rows.length} filled`)),
          h('table', { class: 'fields' },
            h('thead', null, h('tr', null, h('th', null, 'Field'), h('th', null, 'Value'), h('th', null, 'Source'))),
            h('tbody', null, rows.map((row) => h('tr', { class: needsUser(row) ? 'needs' : '' },
              h('td', { class: 'label' }, row.label || 'Field'), h('td', null, fieldValue(row)), h('td', { class: 'src' }, sourceText(row)))))));
        } else if (review.error) card.append(h('div', { class: 'warn-line' }, icon('alert'), `Could not load the field list: ${review.error}`));
        else card.append(h('div', { class: 'small muted' }, 'No form_fill.json for this posting yet.'));
        if (review.questions.length) {
          card.append(h('h2', null, 'Questions on the form'), h('div', null, review.questions.map((q) => h('div', { style: 'margin:6px 0 10px' },
            h('div', { style: 'font-weight:500' }, q.question),
            q.answer ? h('div', { class: 'muted pre' }, q.answer) : h('div', { class: 'small warn' }, 'Not answered yet: write it in form_questions.md on the server.')))));
        }
      }
    } else {
      const s = p.submission || {};
      card.append(h('h2', { class: 'hstack' }, icon('plane', 'inline-icon'), 'Application submitted'));
      const when = s.confirmed_at || s.submitted_at || s.clicked_at;
      if (when) card.append(h('div', { class: 'small muted' }, `Submitted by your agent ${stamp(when)}`));
      const line = str(s.confirmation) || str(s.confirmation_text);
      if (line) card.append(h('blockquote', { class: 'quote' }, `“${line}”`));
      if (str(m.confirmation_png)) {
        card.append(h('button', { type: 'button', class: 'shot', 'aria-label': 'Confirmation page, open full size', onclick: () => openViewer([{ rel: m.confirmation_png, caption: 'Confirmation page' }], 0, { tall: true, fresh: true, title: 'Confirmation' }) },
          remoteImg(m.confirmation_png, '', '', true), h('span', { class: 'badge' }, 'Full size')));
      }
      if (review && review.confirmationText) {
        card.append(h('details', null, h('summary', null, 'Confirmation page text', icon('chevron', 'chev')),
          h('div', { class: 'inside' }, h('div', { class: 'txt mono small muted pre' }, review.confirmationText))));
      }
    }
    return card;
  }

  // ---------- per-box review (review.json, tools/review.py) ----------
  const SEVERITY = { block: 'Block', warn: 'Warning', info: 'Note' };
  const STATE = { pending: 'Pending', approved: 'Approved', commented: 'Commented' };
  const activeBlocks = (b) => (Array.isArray(b.flags) ? b.flags : []).filter((f) => f && f.severity === 'block' && !f.stale);

  function boxesSection(p, review) {
    const rv = review.boxes;
    const boxes = Array.isArray(rv.boxes) ? rv.boxes.filter((b) => b && typeof b.key === 'string') : [];
    const sm = rv.summary || { total: boxes.length, approved: boxes.filter((b) => b.state === 'approved').length, blocked: 0 };
    const pct = sm.total ? Math.round((100 * sm.approved) / sm.total) : 0;
    const wrap = h('div', { class: 'boxes' },
      h('div', { class: 'hstack' }, h('h2', null, 'Review every box'), h('span', { class: 'grow' }),
        h('span', { class: sm.all_approved ? 'small ok' : 'small muted', id: 'box-count' }, `${sm.approved} of ${sm.total} boxes approved`)),
      h('span', { class: 'meter', 'aria-hidden': 'true' }, meterFill(pct)),
      h('p', { class: 'small muted' }, 'Approve each value, or comment on it. A comment goes to your agent, who fixes it and shows the box again. Approve submit opens once every box is approved.'));
    if (review.fill && review.fill.dry_run) wrap.append(h('div', { class: 'warn-line' }, icon('alert'), 'Rehearsal fill (--dry-run). Run apply for real before approving.'));
    for (const b of boxes) wrap.append(boxView(p, b));
    return wrap;
  }

  function boxView(p, b) {
    const st = STATE[b.state] ? b.state : 'pending';
    const flags = (Array.isArray(b.flags) ? b.flags : []).filter((f) => f && SEVERITY[f.severity]);
    const blocks = activeBlocks(b).length;
    const comments = (Array.isArray(b.comments) ? b.comments : []).filter((c) => c && str(c.text));
    let value = b.value == null ? '' : String(b.value);
    return h('div', { class: `rbox ${st}`, id: `box-${b.key}` },
      h('div', { class: 'rbox-head' },
        h('span', { class: 'rbox-label' }, b.label || 'Field'), h('span', { class: 'grow' }),
        h('span', { class: `chip state-${st}` }, st === 'approved' ? icon('check') : st === 'commented' ? icon('bubble') : null, STATE[st])),
      h('div', { class: 'chips' }, h('span', { class: 'chip' }, b.kind === 'answer' ? 'Custom answer' : 'Field'),
        b.kind !== 'answer' && str(b.source) ? h('span', { class: 'chip' }, sourceText(b)) : null,
        b.required ? h('span', { class: 'chip' }, 'required') : null),
      h('div', { class: 'rbox-value pre' }, value || h('span', { class: 'faint' }, b.kind === 'answer' ? 'Not drafted yet' : 'blank')),
      flags.length ? h('ul', { class: 'rflags' }, flags.map((f) => h('li', { class: `rflag ${f.severity}${f.stale ? ' stale' : ''}` },
        icon(f.severity === 'info' ? 'question' : 'alert'),
        h('span', null, h('b', null, `${SEVERITY[f.severity]}: `), noDash(f.text || ''),
          h('span', { class: 'faint' }, ` (${f.by === 'career' ? 'career check' : 'reviewer'}${f.stale ? ', on an earlier value' : ''})`))))) : null,
      comments.length ? h('div', { class: 'rcomments' }, comments.map((c) => h('div', { class: `rcomment${c.by === 'phone' || c.by === 'web' ? ' mine' : ''}` },
        h('div', { class: 'small muted' }, `${c.by === 'phone' || c.by === 'web' ? 'You' : 'Agent'} · ${stamp(c.at)}`),
        h('div', { class: 'pre' }, c.text)))) : null,
      h('div', { class: 'btn-row rbox-actions' },
        h('button', { type: 'button', class: 'btn small', onclick: () => commentBox(p, b) }, icon('bubble'), 'Comment'),
        h('button', { type: 'button', class: 'btn small primary', disabled: st === 'approved' || blocks > 0 || S.busy,
          title: blocks ? 'Flagged block: comment, and your agent fixes it or clears the flag' : null, onclick: () => approveBox(p, b) },
          icon('check'), st === 'approved' ? 'Approved' : 'Approve')));
  }

  async function afterBoxAction(p, rv) {
    // A box change can move the posting between Ready to submit, Needs your answer and On hold: fetch it again.
    api(`/postings/${encodeURIComponent(p.id)}`).then((rec) => { upsertPosting(rec); render({ list: true }); }).catch(() => {});
    const entry = S.reviews.get(p.id);
    if (entry && entry.data) {
      entry.data.boxes = rv;
      try { entry.data.gate = await api(`/postings/${encodeURIComponent(p.id)}/approve-submit`); } catch { /* keep the last gate */ }
    }
    render({ detail: true });
  }

  async function approveBox(p, b) {
    S.busy = true;
    try {
      const rv = await api(`/postings/${encodeURIComponent(p.id)}/review/${encodeURIComponent(b.key)}/approve`,
        { method: 'POST', body: { by: BY, seen: b.value_changed_at || null } });
      await afterBoxAction(p, rv);
    } catch (e) {
      toast(e.message, 'error', 7000);
      S.reviews.delete(p.id);
      render({ detail: true });
    } finally { S.busy = false; }
  }

  async function commentBox(p, b) {
    const res = await ask({ title: `Comment on "${b.label}"`, message: 'Your agent reads it, applies it, and shows the box again.',
      field: { multiline: true, required: true, placeholder: 'What should change?' }, confirm: 'Send comment' });
    if (!res.ok || !res.value) return;
    try {
      const rv = await api(`/postings/${encodeURIComponent(p.id)}/review/${encodeURIComponent(b.key)}/comment`,
        { method: 'POST', body: { text: res.value, by: BY } });
      toast('Comment sent to your agent');
      await afterBoxAction(p, rv);
    } catch (e) { toast(e.message, 'error', 7000); }
  }

  // ---------- action bar ----------
  function actionBar(p, review) {
    const bar = h('div', { class: 'actionbar' });
    const inner = h('div', { class: 'inner' });
    bar.append(inner);
    const st = p.status;
    if (['new', 'enriched', 'approved', 'skipped'].includes(st)) {
      inner.append(h('div', { class: 'btns' },
        h('button', { type: 'button', class: 'btn danger', 'data-key': 's', disabled: st === 'skipped', onclick: () => rule(p, 'skipped') }, icon('x'), 'Skip ', kbd('s')),
        h('button', { type: 'button', class: 'btn primary', 'data-key': 'a', disabled: st === 'approved', onclick: () => rule(p, 'approved') }, icon('check'), 'Approve ', kbd('a'))));
    } else if (st === 'ready') {
      const a = approval(p, review);
      if (a.kind === 'blocked') inner.append(h('div', { class: 'warn-line gate', title: a.missing.join('\n') }, icon('alert'), a.reason));
      if (a.kind === 'approved') inner.append(h('div', { class: 'ok-line gate' }, icon('seal'), `Approved ${stamp(a.at)}: your agent will submit`));
      inner.append(h('div', { class: 'btns' },
        h('button', { type: 'button', class: 'btn', title: 'For an application you submitted yourself, outside your agent', onclick: () => confirmApplied(p, true) }, 'Confirm applied'),
        // The submit is approved only at the end of the Application preview, after every field.
        h('a', { class: 'btn primary', 'data-key': 'a', href: previewHash(p.id) },
          icon('eye'), a.kind === 'approved' ? 'Submit approved' : 'Review and submit')));
    } else if (st === 'tailored') {
      inner.append(h('div', { class: 'btns' }, h('button', { type: 'button', class: 'btn primary', onclick: () => confirmApplied(p, false) }, icon('plane'), 'Confirm applied')));
    } else {
      inner.append(h('div', { class: 'status' }, `Status: ${st}`));
    }
    return bar;
  }

  async function rule(p, decision) {
    const skip = decision === 'skipped';
    const res = await ask({
      title: skip ? 'Skip posting' : 'Approve posting',
      message: skip ? 'The reason is stored in the record so your agent stops surfacing similar postings.' : 'Optional note. It goes into ruling.reason; the tailoring step reads it.',
      field: { placeholder: skip ? 'Why skip? One line.' : 'Note for the tailoring step (optional)', required: skip },
      confirm: skip ? 'Skip' : 'Approve', danger: skip,
    });
    if (!res.ok) return;
    const inToday = S.route.tab === 'today' && S.route.id === p.id;
    try {
      const rec = await api(`/postings/${encodeURIComponent(p.id)}/ruling`, { method: 'POST', body: { decision, reason: res.value || null, by: BY } });
      upsertPosting(rec);
      toast(skip ? `Skipped: ${p.title}` : `Approved: ${p.title}`);
      // Clearing a batch: open the next open item, so the batch is worked through without going back to the list.
      if (inToday && S.route.id === p.id) {
        const next = nextOpenAfter(p.id);
        if (next) { render({ data: true }); return go(todayHash(next)); }
        if (todayItems().length) toast('Batch cleared. Ask your agent for the next batch.');
      }
      render({ data: true });
    } catch (e) { toast(e.message, 'error'); }
  }

  async function confirmApplied(p, ready) {
    const res = await ask({
      title: 'Mark as applied?',
      message: ready ? 'Only for an application you already submitted yourself. Your agent will not press Submit.' : 'Only do this after the application is actually submitted on the company site.',
      confirm: 'Confirm applied',
    });
    if (!res.ok) return;
    try {
      upsertPosting(await api(`/postings/${encodeURIComponent(p.id)}/applied`, { method: 'POST', body: { by: BY } }));
      toast('Marked as applied');
      render({ data: true });
    } catch (e) { toast(e.message, 'error'); }
  }

  // Only the Application preview calls this, from its button at the end of the form (user, 2026-11-01).
  async function approveSubmit(p, doc) {
    const res = await ask({ title: `Submit ${p.title} at ${p.company}?`, message: "I'll submit it and tell you.", confirm: 'Approve submit' });
    if (!res.ok) return;
    S.busy = true;
    try {
      // The coordinator may ask the submit as a question too (applies_to.approve_submit); answering it is the same
      // approval through the same gate, and the answer reaches the session like any other.
      upsertPosting(await api(`/postings/${encodeURIComponent(p.id)}/approve-submit`, { method: 'POST', body: { by: BY, from: 'preview', fill_digest: doc.fill_digest } }));
      S.reviews.delete(p.id);
      toast('Submit approved. Your agent submits it and tells you.');
    } catch (e) {
      const missing = e.data && Array.isArray(e.data.missing) && e.data.missing.length ? e.data.missing.join('; ') : e.message;
      toast(`Not approved: ${missing}`, 'error', 9000);
      S.reviews.delete(p.id);
    } finally {
      S.busy = false;
      render({ data: true, detail: true });
    }
  }

  // ---------- questions ----------
  // A question is one thread (user ruling, 2026-10-20). Records with `thread` carry the conversation; older records
  // are read as a chain (`follow_up_of` / `superseded_by`) plus their `events`, so both shapes render the same way.
  const TOPICS = [
    ['application', 'Applications', 'plane'], ['profile', 'Profile', 'person'], ['site', 'Website', 'globe'],
    ['github', 'GitHub', 'code'], ['referral', 'Referrals', 'people'], ['other', 'Other', 'question'],
  ];
  const topicOf = (q) => TOPICS.find((t) => t[0] === q.topic) || TOPICS[5];
  const KIND_ICON = { multiselect: 'list', choice: 'list', yesno: 'checkCircle', text: 'bubble', confirm: 'seal' };
  const kindOf = (q) => (q && q.kind in KIND_ICON ? q.kind : 'text');
  const answeredByComment = (q) => q.needs_reading === true && q.answer == null;

  // Merged follow-ups are hidden. The server leaves `merged_into` records out of the list and names them in the
  // thread's `merged_from`; until a pair is merged, an original whose follow-up is still listed stands for it.
  function mergedInto(q) {
    const t = str(q.merged_into);
    if (t && t !== q.id) return t;
    if (q.superseded_by && q.superseded_by !== q.id && S.qById.has(q.superseded_by)) return q.superseded_by;
    return null;
  }
  // The record an id stands for: a merged id resolves through `merged_from`, then along the chain to the visible end.
  function displayId(id) {
    let cur = id;
    if (!S.qById.has(cur)) {
      const holder = S.questions.find((q) => Array.isArray(q.merged_from) && q.merged_from.includes(cur));
      if (holder) cur = holder.id;
    }
    for (let i = 0; i < 20; i++) {
      const q = S.qById.get(cur);
      const next = q && mergedInto(q);
      if (!next || !S.qById.has(next)) return cur;
      cur = next;
    }
    return cur;
  }
  const visibleQuestions = () => S.questions.filter((q) => !mergedInto(q));

  // Follow-up contexts written before threads quote the earlier question and the reply; the thread shows both already.
  const stripEcho = (ctx) => (ctx ? ctx.replace(/^Earlier question:[\s\S]*?You said:\s*“[\s\S]*?”\s*/, '').trim() || null : null);

  function userTurnsOf(q) {
    const out = [];
    if (Array.isArray(q.events) && q.events.length) {
      for (const e of q.events) {
        if (!e || (e.answer == null && !str(e.note))) continue;
        out.push({ role: 'user', at: e.at, by: e.by, answer: e.answer == null ? null : String(e.answer), note: str(e.note) });
      }
    } else {
      const notes = Array.isArray(q.notes) ? q.notes.filter((n) => n && str(n.text)) : [];
      for (const n of notes) out.push({ role: 'user', at: n.at, by: n.by, answer: n.with_answer || null, note: n.text });
      if (q.answer != null && !notes.some((n) => n.with_answer === q.answer)) {
        out.push({ role: 'user', at: q.answered_at, by: q.answered_by, answer: String(q.answer), note: null });
      }
      if (!notes.length && q.answer == null && str(q.note)) out.push({ role: 'user', at: q.note_at, by: q.note_by, answer: null, note: q.note });
    }
    if (q.status === 'dismissed') out.push({ role: 'system', at: q.dismissed_at, text: 'Dismissed' });
    return out.sort((a, b) => String(a.at || '').localeCompare(String(b.at || '')));
  }

  // {target, turns, latestAsk}: `target` is the record answers go to and whose status counts. A record that still
  // points at an unmerged original (`follow_up_of`) shows that original's turns first.
  function conversation(q, depth = 0) {
    let turns;
    if (Array.isArray(q.thread) && q.thread.length) {
      turns = q.thread.filter((t) => t && typeof t === 'object').map((t) => {
        if (t.role === 'ask') return { role: 'ask', at: t.at, by: t.by, text: str(t.text) || '', kind: t.kind || q.kind, options: t.options, context: str(t.context), title: str(t.title), command: str(t.command) };
        if (t.role === 'user') {
          const answer = t.answer == null ? null : String(t.answer);
          const note = str(t.note) || str(t.text);
          return { role: 'user', at: t.at, by: t.by, answer, note: note && note !== answer ? note : (answer == null ? note : null) };
        }
        return { role: 'system', at: t.at, text: str(t.text) || String(t.role || '') };
      });
      if (q.status === 'dismissed' && !turns.some((t) => t.role === 'system')) turns.push({ role: 'system', at: q.dismissed_at, text: 'Dismissed' });
    } else {
      turns = [askOf(q, false), ...userTurnsOf(q)];
    }
    const orig = q.follow_up_of && depth < 10 && S.qById.get(q.follow_up_of);
    if (orig && orig.id !== q.id && !(Array.isArray(q.merged_from) && q.merged_from.includes(orig.id))) {
      const first = turns.find((t) => t.role === 'ask');
      if (first) { first.context = stripEcho(first.context); if (!first.title) first.title = q.title; }
      turns = conversation(orig, depth + 1).turns.concat(turns);
    }
    const asks = turns.filter((t) => t.role === 'ask');
    return { target: q, turns, latestAsk: asks[asks.length - 1] || askOf(q, false) };
  }
  function askOf(rec, followUp) {
    return { role: 'ask', at: rec.asked_at, by: rec.by, text: rec.question || '', kind: rec.kind, options: rec.options,
      context: followUp ? stripEcho(str(rec.context)) : str(rec.context), title: followUp ? rec.title : null, command: str(rec.command) };
  }

  function whoAsked(by) {
    if (by === 'career') return 'Your agent';
    if (by === 'session' || !str(by)) return 'A session';
    return by.startsWith('agent:') ? 'Agent ' + by.slice(6) : by;
  }
  const fmtClock = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
  function turnTime(iso) {
    const d = new Date(iso);
    if (!iso || isNaN(d)) return '';
    return dayStart(d).getTime() === dayStart(new Date()).getTime() ? fmtClock.format(d) : fmtStamp.format(d);
  }
  const via = (by) => (by === 'web' ? 'web' : by === 'phone' ? 'phone' : by === 'session' ? 'a session' : str(by) || '');

  function renderQuestionList(pane) {
    const state = loadState('questions', 'questions');
    if (state) return pane.replaceChildren(state);
    const hidden = questionsShownAsRows();
    const shown = visibleQuestions();
    const open = shown.filter((q) => q.status === 'open' && !hidden.has(q.id));
    const closed = shown.filter((q) => q.status !== 'open');
    const filled = readyList(S.postings);
    const out = answerSection(filled.filter(p => !p.review_summary?.questions?.length));
    if (!shown.length && !filled.length) {
      out.push(h('div', { class: 'empty' }, h('div', { class: 'big' }, 'No questions'), 'Nothing from your agent is waiting on you.'));
    } else if (!open.length && !S.postings.some(waitsOnUser)) {
      out.push(h('div', { class: 'card muted hstack', style: 'margin-top:12px' }, icon('checkCircle', 'inline-icon'), 'Nothing is waiting on you.'));
    }
    for (const [key, title, ic] of TOPICS) {
      const rows = open.filter((q) => topicOf(q)[0] === key);
      if (!rows.length) continue;
      out.push(h('div', { class: 'sec-head' }, icon(ic), `${title} · ${rows.length}`),
        h('div', { class: 'group' }, rows.map((q) => questionRow(q, false))));
    }
    out.push(...submitAndHoldSections(filled));
    const answers = h('div');
    out.push(h('details', null, h('summary', {onclick:async()=>{
      try {
        const rows=await api('/answers');
        answers.replaceChildren(...rows.map(row=>{
          const input=h('input',{value:row.value,'aria-label':row.name || 'Saved answer'});
          const error=h('p',{class:'warn'});
          return h('section',null,h('h3',null,row.name || 'Saved answer'),h('p',null,row.scope || 'Earlier saved answer'),input,
            h('button',{type:'button',class:'btn',onclick:async()=>{
              try {await api('/answers/'+row.id,{method:'POST',body:{value:input.value,by:BY}});error.textContent='Saved. Resume an active application to use the change.';}catch(e){error.textContent=e.message;}
            }},'Save answer'),h('button',{type:'button',class:'btn',onclick:()=>{input.value='Ask me every time';}},'Ask me every time'),error);
        }));
      }catch(e){answers.textContent=e.message;}
    }},'Saved answers'),answers));

    if (closed.length) {
      const toggle = h('button', { type: 'button', class: 'company answered-toggle', 'aria-expanded': String(S.showAnswered),
        onclick: () => { S.showAnswered = !S.showAnswered; renderQuestionList(pane); markCurrent(pane); const t = $('.answered-toggle', pane); if (t) t.focus(); },
      }, icon('tray', 'inline-icon'), h('span', { class: 'name' }, `Answered · ${closed.length}`), icon('chevron', 'chev'));
      out.push(h('div', { class: 'sec-head' }, ''), h('div', { class: 'group' }, toggle, S.showAnswered ? closed.map((q) => questionRow(q, true)) : null));
    }
    if (S.lastRefreshed) out.push(h('div', { class: 'sec-foot' }, `Updated ${fmtTime.format(S.lastRefreshed)} · press r to refresh`));
    pane.replaceChildren(...out);
  }

  function questionRow(q, closedRow) {
    const isOpen = q.status === 'open';
    const c = conversation(q);
    const company = q.posting_id && S.byId.get(q.posting_id) && S.byId.get(q.posting_id).company;
    const last = c.turns[c.turns.length - 1];
    const asks = c.turns.filter((t) => t.role === 'ask').length;
    const lastAt = (last && last.at) || q.asked_at;
    const when = lastAt && relative(lastAt);
    const meta = [closedRow ? topicOf(q)[1] : null, company, asks > 1 ? `${asks} asks` : null,
      when && (last && last.role === 'user' ? `you replied ${when}` : `asked ${when}`)].filter(Boolean).join(' · ');
    let sub;
    if (isOpen) sub = c.latestAsk.text;
    else if (q.status === 'dismissed') sub = 'Dismissed';
    else {
      const u = c.turns.filter((t) => t.role === 'user').pop();
      sub = u ? 'You: ' + [u.answer, u.note].filter(Boolean).join(' · ') : 'Answered';
    }
    const href = isOpen && submitTarget(q) ? previewHash(submitTarget(q)) : questionHash(q.id);
    return h('a', { class: `row ${closedRow ? 'indent' : ''}`, href, 'data-id': q.id },
      h('span', { style: 'margin-top:2px;' + (isOpen ? 'color:var(--accent)' : 'color:var(--fg3)') }, icon(KIND_ICON[kindOf(c.latestAsk)], 'kind')),
      h('span', { class: 'body' },
        h('span', { class: 'title', style: isOpen ? '' : 'font-weight:400' }, q.title),
        h('span', { class: 'clamp2' }, sub),
        meta ? h('span', { class: 'meta' }, meta) : null));
  }

  function draftOf(id) {
    if (!S.drafts.has(id)) S.drafts.set(id, { draft: '', comment: '', choice: null, editing: false, changing: false, expanded: false, ctxOpen: {} });
    return S.drafts.get(id);
  }

  function effectLine(q, kind) {
    const to = q.applies_to || {};
    if (to.profile_key) return `Your answer is written to profile.yml (${to.profile_key}).`;
    if (to.form_question) return kind === 'confirm' ? `Confirming makes this text the form's answer to “${to.form_question}”.` : `Your answer becomes the form's answer to “${to.form_question}”.`;
    if (to.ruling === 'approve') return 'Yes approves this posting, the same as Approve in Listings. No changes nothing.';
    return 'Your answer is kept with the question for your agent to act on.';
  }

  function renderQuestionDetail(pane, id) {
    if (!id) {
      const hidden = questionsShownAsRows();
      const n = visibleQuestions().filter((q) => q.status === 'open' && !hidden.has(q.id)).length;
      const k = S.postings.filter((p) => ACTION_OF(p) === 'submit').length;
      const parts = [k ? `${k} ready to submit` : null, n ? `${n} open ${n === 1 ? 'question' : 'questions'}` : null].filter(Boolean);
      return pane.replaceChildren(h('div', { class: 'placeholder' }, S.loaded.questions ? (parts.length ? `${parts.join(', ')}.` : 'Nothing is waiting on you.') : ''));
    }
    const q = S.qById.get(id);
    if (!q) {
      return pane.replaceChildren(backButton('#/questions', 'Questions'),
        S.loaded.questions ? h('div', { class: 'empty' }, h('div', { class: 'big' }, 'Question not found'), id) : h('div', { class: 'empty' }, 'Loading…'));
    }
    const d = draftOf(id);
    const isOpen = q.status === 'open';
    const topic = topicOf(q);
    const c = conversation(q);
    const pid = q.posting_id || (q.applies_to && q.applies_to.posting_id);
    const controls = isOpen || d.changing;
    const inner = h('div', { class: 'detail-inner stack' },
      h('div', null, backButton('#/questions', 'Questions'),
        h('div', { class: 'chips' }, h('span', { class: 'chip' }, icon(topic[2]), topic[1]),
          !isOpen ? h('span', { class: `chip ${q.status === 'dismissed' ? '' : 'green'}` }, icon(q.status === 'dismissed' ? 'x' : 'checkCircle'), q.status === 'dismissed' ? 'Dismissed' : 'Answered') : null),
        h('h1', { class: 'd-title', style: 'margin-top:8px' }, q.title), copyRef(questionRef(q), 'inline')),
      pid ? postingLinkCard(pid) : null,
      threadView(q, c, d, !controls),
      // A submit question is answered only at the end of the Application preview, after every field.
      submitTarget(q) && isOpen ? h('div', { class: 'card' },
        h('div', { class: 'small muted' }, 'See every field as the employer will receive it, then approve the submit at the end.'),
        h('a', { class: 'btn primary', href: previewHash(submitTarget(q)) }, icon('eye'), 'Open the application preview'))
        : (controls ? answerPanel(q, c, d) : resultPanel(q, c, d)),
      isOpen && reviewBoxOf(q) ? h('div', { class: 'btn-row' }, h('button', { type: 'button', class: 'btn', onclick: () => editFromQuestion(q) }, icon('pencil'), 'Edit it myself')) : null,
      submitTarget(q) && isOpen ? null : composer(q, c, d));
    pane.replaceChildren(inner);
  }

  // The conversation: agent asks on the left, the user's replies on the right. A closed thread shows its last
  // exchange with the earlier turns folded behind "Show the whole thread".
  function threadView(q, c, d, collapsible) {
    let turns = c.turns;
    let hiddenCount = 0;
    if (collapsible && !d.expanded) {
      const lastAsk = turns.map((t) => t.role).lastIndexOf('ask');
      hiddenCount = Math.max(0, lastAsk);
      turns = turns.slice(lastAsk < 0 ? 0 : lastAsk);
    }
    const box = h('div', { class: 'thread', role: 'log', 'aria-label': 'Conversation' });
    if (hiddenCount) {
      box.append(h('button', { type: 'button', class: 'btn link thread-more', onclick: () => { d.expanded = true; render({ detail: true }); } },
        `Show the whole thread (${hiddenCount} earlier ${hiddenCount === 1 ? 'message' : 'messages'})`));
    } else if (collapsible && d.expanded && c.turns.length > 2) {
      box.append(h('button', { type: 'button', class: 'btn link thread-more', onclick: () => { d.expanded = false; render({ detail: true }); } }, 'Fold the thread'));
    }
    const lastAskIdx = turns.map((t) => t.role).lastIndexOf('ask');
    turns.forEach((t, i) => {
      if (t.role === 'ask') box.append(askBubble(q, t, d, i, i === lastAskIdx));
      else if (t.role === 'user') box.append(userBubble(t));
      else box.append(h('div', { class: 'turn-sys' }, [t.text, turnTime(t.at)].filter(Boolean).join(' · ')));
    });
    return box;
  }

  const CTX_FOLDABLE = 1500;     // characters; below this a context has no fold control at all
  function askBubble(q, t, d, i, latest) {
    const kind = kindOf(t);
    const ctxKey = `${i}|${t.at || ''}`;
    let ctx = null;
    if (t.context) {
      // a confirm context is the proposed answer itself, so it is never split into commands
      const parts = kind === 'confirm' ? [h('div', { class: 'pre' }, linkify(t.context))] : richText(t.context);
      // Drafted essays live here: always shown in full at first. Only a very long one offers "Show less", which the
      // user chooses; nothing folds on its own.
      const long = t.context.length > CTX_FOLDABLE && !parts.some((n) => n.classList.contains('cmd'));
      const open = !long || !d.ctxOpen[ctxKey];
      ctx = h('div', { class: 'bubble-ctx' },
        h('div', { class: 'ctx-head' }, kind === 'confirm' ? 'Proposed answer' : 'Context'),
        h('div', { class: 'ctx-body', style: open ? '' : 'max-height:9.5em;overflow:hidden' }, parts),
        long ? h('button', { type: 'button', class: 'btn link', onclick: () => { d.ctxOpen[ctxKey] = !d.ctxOpen[ctxKey]; render({ detail: true }); } }, open ? 'Show less' : 'Show more') : null);
    }
    return h('div', { class: `turn ask ${latest ? 'latest' : ''}` },
      h('div', { class: 'turn-meta' }, whoAsked(t.by), t.at ? ` · ${turnTime(t.at)}` : ''),
      h('div', { class: 'bubble' },
        t.title && t.title !== q.title ? h('div', { class: 'bubble-title' }, t.title) : null,
        h('div', { class: 'pre' }, linkify(t.text)),
        t.command ? commandBlock(t.command) : null,
        ctx));
  }

  function userBubble(t) {
    return h('div', { class: 'turn user' },
      h('div', { class: 'turn-meta' }, 'You', via(t.by) ? ` · ${via(t.by)}` : '', t.at ? ` · ${turnTime(t.at)}` : ''),
      h('div', { class: 'bubble' },
        t.answer != null ? h('div', { class: 'bubble-answer' }, t.answer) : null,
        t.note ? h('div', { class: 'pre' }, t.note) : null));
  }

  function postingLinkCard(pid) {
    const p = S.byId.get(pid);
    if (!p) return h('div', { class: 'line small' }, icon('doc'), `Posting ${pid}`);
    return h('a', { class: 'linkcard', href: postingHash(pid) },
      logoView(logoOf(p), p.company, 36, 8),
      h('span', { class: 'body', style: 'flex:1;min-width:0' }, h('div', { class: 't' }, p.title), h('div', { class: 's' }, `${p.company} · ${p.status}`)),
      icon('chevron', 'chev'));
  }

  // Controls for the latest ask, under the thread. A text question is answered from the composer itself.
  function answerPanel(q, c, d) {
    const ask = c.latestAsk;
    const kind = kindOf(ask);
    const panel = h('div', { class: `answer-panel ${S.busy ? 'busy' : ''}` });
    panel.append(h('div', { class: 'small muted' }, (d.changing ? 'Change your answer. ' : '') + effectLine(q, kind)));
    if (kind === 'yesno') {
      panel.append(h('div', { class: 'btn-row stack-narrow' },
        h('button', { type: 'button', class: 'btn primary', 'data-key': 'y', onclick: () => sendAnswer(c.target, 'Yes') }, icon('check'), 'Yes ', kbd('y')),
        h('button', { type: 'button', class: 'btn', 'data-key': 'n', onclick: () => sendAnswer(c.target, 'No') }, icon('x'), 'No ', kbd('n'))));
    } else if (kind === 'choice' || kind === 'multiselect') {
      const opts = arr(ask.options), multi = kind === 'multiselect';
      d.selections ||= [];
      const selected = o => multi ? d.selections.includes(o) : d.choice === o;
      const chosen = () => multi ? opts.filter(selected).join('; ') : d.choice;
      const send = h('button', { type: 'button', class: 'btn primary', disabled: !chosen(), onclick: () => chosen() && sendAnswer(c.target, chosen()) }, multi ? 'Save choices' : 'Send');
      panel.append(h('div', { class: 'options', role: 'group', 'aria-label': multi ? 'Choose all that apply' : 'Options' }, opts.map((o) => h('button', {
        type: 'button', class: 'opt', 'aria-pressed': String(selected(o)), 'data-label': o,
        onclick: (e) => {
          if (multi) d.selections = selected(o) ? d.selections.filter(v => v !== o) : [...d.selections,o];
          else d.choice = o;
          for (const b of $$('.opt', e.currentTarget.parentNode)) { const sel = selected(b.dataset.label); b.setAttribute('aria-pressed', String(sel)); b.replaceChildren(sel ? icon('check') : '', b.dataset.label); }
          send.disabled = !chosen();
        },
      }, selected(o) ? icon('check') : null, o))), h('div', { class: 'btn-row' }, send));
    } else if (kind === 'confirm') {
      if (d.editing) {
        const ta = h('textarea', { class: 'answer-field', rows: 5, 'aria-label': 'Edit the answer' });
        ta.value = d.draft;
        const send = h('button', { type: 'button', class: 'btn primary', disabled: !d.draft.trim(), onclick: () => sendAnswer(c.target, d.draft) }, icon('plane'), 'Send the edited answer');
        ta.addEventListener('input', () => { d.draft = ta.value; send.disabled = !ta.value.trim(); });
        ta.addEventListener('keydown', (e) => { if (sendKey(e)) { e.preventDefault(); if (ta.value.trim()) sendAnswer(c.target, ta.value); } });
        panel.append(ta, h('div', { class: 'btn-row' }, h('button', { type: 'button', class: 'btn', onclick: () => { d.editing = false; render({ detail: true }); } }, 'Cancel edit'), send));
      } else {
        panel.append(h('div', { class: 'btn-row stack-narrow' },
          h('button', { type: 'button', class: 'btn primary', 'data-key': 'y', onclick: () => sendAnswer(c.target, 'Confirmed') }, icon('seal'), 'Confirm ', kbd('y')),
          h('button', { type: 'button', class: 'btn', onclick: () => { d.draft = ask.context || ''; d.editing = true; render({ detail: true }); focusField('.answer-field'); } }, icon('pencil'), 'Edit first')));
      }
    } else {
      panel.append(h('div', { class: 'small muted' }, 'Write your answer in the box below.'));
    }
    const foot = h('div', { class: 'hstack' }, h('span', { class: 'grow' }));
    if (d.changing) foot.append(h('button', { type: 'button', class: 'btn small', onclick: () => { d.changing = false; d.editing = false; render({ detail: true }); } }, 'Cancel'));
    else if (q.status === 'open') foot.append(h('button', { type: 'button', class: 'btn small danger', 'data-dismiss': c.target.id, title: 'Moves it to Answered without an answer' }, icon('x'), 'Dismiss question'));
    panel.append(foot);
    return panel;
  }

  function resultPanel(q, c, d) {
    const panel = h('div', { class: 'answer-panel quiet' });
    const t = c.target;
    if (answeredByComment(t)) panel.append(h('div', { class: 'small muted' }, 'Answered with a comment. The agent that asked reads it and asks again in this thread if needed.'));
    if (t.apply_error) panel.append(h('div', { class: 'warn-line' }, icon('alert'), `Not applied yet: ${t.apply_error}`));
    else if (t.applied && typeof t.applied === 'object' && t.applied.result) panel.append(h('div', { class: 'small muted hstack' }, icon('checkCircle', 'inline-icon'), t.applied.result));
    const nextOpen = visibleQuestions().find((x) => x.status === 'open' && x.id !== q.id);
    panel.append(h('div', { class: 'hstack' },
      h('button', { type: 'button', class: 'btn small', onclick: () => {
        const kind = kindOf(c.latestAsk);
        d.choice = kind === 'choice' ? t.answer : null;
        d.selections = kind === 'multiselect' ? (t.answer || '').split('; ').filter(Boolean) : [];
        d.editing = false;
        d.changing = true;
        render({ detail: true });
      } }, q.status === 'dismissed' ? 'Answer anyway' : 'Change the answer'),
      h('span', { class: 'grow' }),
      nextOpen ? h('a', { class: 'btn small', href: questionHash(nextOpen.id) }, 'Next open question ', icon('chevron')) : null));
    return panel;
  }

  // The comment box is the composer. Sent alone it answers an open question (a text question takes it as the answer);
  // on an answered one it adds a comment. Pressing an answer button above sends whatever is typed here with it.
  function composer(q, c, d) {
    const t = c.target;
    const kind = kindOf(c.latestAsk);
    const open = t.status === 'open' || d.changing;
    const textAnswer = open && kind === 'text';
    const label = textAnswer ? 'Send answer' : t.status === 'answered' && !d.changing ? 'Add comment' : open ? 'Answer with comment' : 'Send comment';
    const placeholder = textAnswer ? 'Type your answer' : open ? 'Comment (optional). Sent with your answer, or alone.' : 'Add a comment to this thread';
    const ta = h('textarea', { class: 'comment-field', rows: 2, placeholder, 'aria-label': textAnswer ? 'Your answer' : 'Comment' });
    ta.value = d.comment;
    const send = () => (textAnswer ? sendAnswer(t, d.comment) : sendComment(t));
    const btn = h('button', { type: 'button', class: 'btn primary', disabled: !d.comment.trim(), onclick: send }, icon('plane'), label);
    const grow = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight + 2, 320) + 'px'; };
    ta.addEventListener('input', () => { d.comment = ta.value; btn.disabled = !ta.value.trim(); grow(); });
    ta.addEventListener('keydown', (e) => { if (sendKey(e)) { e.preventDefault(); if (ta.value.trim()) send(); } });
    setTimeout(grow, 0);
    const hint = [textAnswer ? null : open ? 'Goes with the answer you pick above, or alone.' : 'Your answer stays as it is.', enterHint()].filter(Boolean).join(' ');
    return h('div', { class: `composer ${S.busy ? 'busy' : ''}` }, ta,
      h('div', { class: 'composer-foot' }, h('span', { class: 'hint' }, kbd('c'), ' ', hint), btn));
  }

  function focusField(sel) { setTimeout(() => { const el = $('.pane-detail ' + sel); if (el) { el.focus(); el.setSelectionRange && el.setSelectionRange(el.value.length, el.value.length); } }, 0); }

  async function sendAnswer(q, answer) {
    const text = String(answer || '').trim();
    if (!text || S.busy) return;
    const d = draftOf(S.route.id || q.id);
    const kind = q.kind;
    const note = kind === 'text' && text === d.comment.trim() ? '' : d.comment.trim();
    S.busy = true;
    render({ detail: true });
    try {
      const nq = await api(`/questions/${encodeURIComponent(q.id)}/answer`, { method: 'POST', body: note ? { answer: text, note, by: BY } : { answer: text, by: BY } });
      upsertQuestion(nq);
      Object.assign(d, { draft: '', comment: '', choice: null, editing: false, changing: false });
      toast(nq.apply_error ? `Saved, not applied yet: ${nq.apply_error}` : `Sent: ${text.length > 40 ? text.slice(0, 40) + '…' : text}`, nq.apply_error ? 'error' : 'ok');
      refreshPostingsQuietly(q);
    } catch (e) { toast(e.message, 'error'); }
    S.busy = false;
    S.scrollThreadEnd = true;
    render({ data: true, detail: true });
  }

  async function sendComment(q) {
    const d = draftOf(S.route.id || q.id);
    const note = d.comment.trim();
    if (!note || S.busy) return;
    S.busy = true;
    render({ detail: true });
    try {
      upsertQuestion(await api(`/questions/${encodeURIComponent(q.id)}/answer`, { method: 'POST', body: { note, by: BY } }));
      Object.assign(d, { comment: '', changing: false });
      toast('Comment sent');
    } catch (e) { toast(e.message, 'error'); }
    S.busy = false;
    S.scrollThreadEnd = true;
    render({ data: true, detail: true });
  }

  async function dismissQuestion(q) {
    const res = await ask({ title: 'Dismiss this question?', message: 'It moves to Answered without an answer. Nothing is written.', confirm: 'Dismiss', danger: true });
    if (!res.ok) return;
    try {
      upsertQuestion(await api(`/questions/${encodeURIComponent(q.id)}/dismiss`, { method: 'POST', body: { by: BY } }));
      toast('Dismissed');
      render({ data: true, detail: true });
    } catch (e) { toast(e.message, 'error'); }
  }

  // An answer can approve a posting or write its form answers: pull that posting again so the Inbox matches.
  function refreshPostingsQuietly(q) {
    const pid = q.posting_id || (q.applies_to && q.applies_to.posting_id);
    if (!pid) return;
    api(`/postings/${encodeURIComponent(pid)}`).then((rec) => { upsertPosting(rec); render({ data: true }); }).catch(() => {});
  }

  // ---------- pair ----------
  function parsePairing(text) {
    const t = String(text || '').trim();
    if (!t) return null;
    if (/^careerapp:/i.test(t)) {
      try {
        const u = new URL(t), code = u.searchParams.get('code');
        if (u.searchParams.has('token')) return null;
        if (u.hostname === 'pair' && code && /^[A-Za-z0-9_.~-]{16,200}$/.test(code)) return { code };
      } catch { return null; }
      return null;
    }
    return /^[A-Za-z0-9_.~-]{16,200}$/.test(t) ? { token: t } : null;
  }

  function renderPair(main) {
    const paired = !!S.token && !S.authRefused;
    const notifications = h('section',{class:'card notification-settings'});
    if(paired) renderNotificationSettings(notifications);
    const roles=h('section',{class:'card role-settings'});if(paired)renderRoleSettings(roles);
    const status = h('div', { class: 'small muted' });
    const ta = h('textarea', { rows: 3, placeholder: 'careerapp://pair?host=…&code=…  or the token alone', 'aria-label': 'Pairing link or token', autocomplete: 'off', spellcheck: 'false' });
    const pairBtn = h('button', { type: 'button', class: 'btn primary', disabled: true }, icon('link'), 'Pair');
    ta.addEventListener('input', () => { pairBtn.disabled = !ta.value.trim(); status.textContent = ''; });
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!pairBtn.disabled) pairBtn.click(); } });
    pairBtn.addEventListener('click', async () => {
      const pairing = parsePairing(ta.value);
      if (!pairing) { status.className = 'warn-line'; status.replaceChildren(icon('alert'), 'That is not a pairing link or a token.'); return; }
      pairBtn.disabled = true;
      status.className = 'small muted';
      status.textContent = 'Testing the connection…';
      try {
        const token = pairing.code ? (await api('/pair', { method: 'POST', token: pairing.code, body: { code: pairing.code } })).token : pairing.token;
        const health = await api('/health', { token });
        S.token = token;
        S.authRefused = false;
        if (!saveToken(token)) toast('This browser blocks local storage; the pairing lasts until the tab closes.', 'error');
        ta.value = '';
        toast(`Paired: ${health.postings} postings, ${health.questions_open || 0} open questions`);
        if (location.hash === '#/pair' || !location.hash) location.hash = '#/today';
        else render();
        refreshAll();
      } catch (e) {
        pairBtn.disabled = false;
        status.className = 'warn-line';
        status.replaceChildren(icon('alert'), e.status === 401 ? 'The server refused this token. Print a fresh link with serve.py --pair-tailnet, or --pair for configured HTTPS.' : e.message);
      }
    });
    const forget = h('button', { type: 'button', class: 'btn danger', onclick: () => {
      dropToken();
      S.token = '';
      S.authRefused = false;
      dropBlobs();
      setPostings([]); setQuestions([]);
      C.messages=[];C.last=0;C.loaded=false;C.seen=0;
      S.batch = null; S.showCleared = false;
      S.loaded = { postings: false, questions: false, batch: false };
      S.reviews.clear();
      toast('This browser forgot the token.');
      render();
    } }, 'Forget');
    const conn = h('div', { class: 'small muted' });
    if (paired) {
      conn.textContent = 'Checking the connection…';
      api('/health').then((hl) => { conn.className = 'ok-line'; conn.replaceChildren(icon('checkCircle'), `Connected: ${hl.postings} postings, ${hl.questions_open || 0} open questions.`); })
        .catch((e) => { conn.className = 'warn-line'; conn.replaceChildren(icon('alert'), e.message); });
    }
    main.replaceChildren(h('div', { class: 'single stack' },
      h('h1', { class: 'd-title' }, paired ? 'Pairing' : 'Pair this browser'),
      paired ? h('div', { class: 'card' }, h('div', { class: 'pair-status' }, icon('checkCircle'), 'This browser is paired.'), conn,
        h('div', { class: 'btn-row' }, h('a', { class: 'btn primary', href: '#/today' }, 'Go to Today'), forget)) : null,
      S.authRefused ? h('div', { class: 'card warn-line' }, icon('alert'), 'The server refused the saved token. It was probably rotated. Paste a new pairing link below.') : null,
      h('div', { class: 'card' },
        h('h2', null, paired ? 'Pair again' : 'Paste the pairing link'),
        h('ol', { class: 'steps' },
          h('li', null, 'On the server, in ', h('code', null, 'your career workspace'), ', run ', h('code', null, '.venv/bin/python tools/serve.py --pair-tailnet'), '.'),
          h('li', null, 'Copy the ', h('code', null, 'careerapp://pair?…'), ' link with its one-time code.'),
          h('li', null, 'Paste it here and press Pair.')),
        ta, status,
        h('div', { class: 'btn-row' }, pairBtn, S.token && !paired ? forget : null),
        h('div', { class: 'hint' }, 'The token stays in this browser’s local storage and is sent only to this page’s own server. It never goes into a URL. Forget removes it.')),
      h('div', { class: 'card small muted' },
        h('div', { class: 'sub-head', style: 'margin-top:0' }, 'About this page'),
        h('p', null, 'The Career web app: Today, Listings, Questions and Applied, using the same server as the phone app.'),
        h('p', null, 'Server: ', extLink(PUBLIC_WEB)),
        h('p', null, `You are on ${location.host}. Each address keeps its own pairing, so pair once per address, with the link from serve.py --pair. The token never goes into a URL. Question links from WhatsApp open here on a laptop and in the phone app on an iPhone.`))));
    if (!paired) setTimeout(() => ta.focus(), 0);
    if(paired)main.append(notifications,roles);
  }

  async function renderRoleSettings(box) {
    try {
      const settings=await api('/settings');
      box.replaceChildren(h('h2',null,'Writing and review'),...['drafter','reviewer'].map(role=>{
        const choice=settings[role], options=h('div');
        return h('section',null,h('h3',null,role==='drafter'?'Drafter':'Reviewer'),
          h('p',null,choice?choice.agent+' / '+(choice.model || 'choose a model'):'Choose an agent and model'),
          h('p',{class:'small muted'},role==='drafter'?'Recommended: Claude / Opus':'Recommended: Claude / Sonnet'),
          h('button',{type:'button',class:'btn',onclick:async()=>{
            try {
              const catalog=await api('/models');
              options.replaceChildren(...catalog.models.map(model=>h('button',{type:'button',class:'btn',onclick:async()=>{
                try{await api('/settings',{method:'POST',body:{role,choice:{agent:model.agent,model:model.model}}});renderRoleSettings(box);}catch(e){toast(e.message,'error');}
              }},model.agent+' / '+model.label)),...catalog.errors.map(error=>h('p',{class:'warn'},error.agent+': '+error.error)));
            }catch(e){options.textContent=e.message;}
          }},'Choose '+role),options);
      }));
    }catch(e){box.textContent=e.message;}
  }

  async function renderNotificationSettings(box) {
    try {
      const status=await api('/notifications');
      box.replaceChildren(h('h2',null,'WhatsApp notifications'),h('p',null,status.choice==null?'Would you like WhatsApp notifications for this search?':status.enabled?'On: '+(status.group?.name || 'group setup needs attention'):'Off: everything stays in the app.'),
        ...status.issues.map(issue=>h('p',{class:'warn'},issue)),
        ...[[true,'Yes'],[false,'No']].map(([enabled,label])=>h('button',{type:'button',class:'btn',onclick:async()=>{
          try{await api('/notifications',{method:'POST',body:{enabled}});await renderNotificationSettings(box);}catch(e){toast(e.message,'error');}
        }},label)));
    }catch(e){box.textContent=e.message;}
  }

  // ---------- keyboard ----------
  function moveRow(delta) {
    const pane = $('.pane-list');
    if (!pane) return;
    const items = $$('a.row, button.company', pane).filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    let i = items.indexOf(document.activeElement);
    if (i < 0) i = items.findIndex((el) => el.getAttribute('aria-current') === 'true');
    const next = items[Math.max(0, Math.min(items.length - 1, i < 0 ? (delta > 0 ? 0 : items.length - 1) : i + delta))];
    next.focus();
    next.scrollIntoView({ block: 'nearest' });
    if (WIDE.matches && next.matches('a.row')) location.hash = next.getAttribute('href');
  }

  document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || $('#dialog').open || $('#viewer').open) return;
    const t = e.target;
    const typing = t instanceof Element && t.closest('input, textarea, select, [contenteditable="true"]');
    if (typing) {
      if (e.key === 'Escape') { t.blur(); e.preventDefault(); }
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const detailKey = (k) => { const b = $(`.pane-detail [data-key="${k}"]`); if (b && !b.disabled) { e.preventDefault(); b.click(); return true; } return false; };
    switch (e.key) {
      case '1': e.preventDefault(); go('#/today'); break;
      case '2': e.preventDefault(); go('#/inbox'); break;
      case '3': e.preventDefault(); go('#/questions'); break;
      case '4': e.preventDefault(); go('#/applied'); break;
      case '/': { const s = $('input.search'); if (s) { e.preventDefault(); if (S.route.id && !WIDE.matches) go('#/inbox'); s.focus(); s.select(); } break; }
      case 'r': e.preventDefault(); refreshAll({ announce: true }); break;
      case 'j': e.preventDefault(); moveRow(1); break;
      case 'k': e.preventDefault(); moveRow(-1); break;
      case '?': e.preventDefault(); showKeys(); break;
      case 'c': { const c = $('.pane-detail .comment-field'); if (c) { e.preventDefault(); c.focus(); } break; }
      case 'a': case 's': case 'y': case 'n': detailKey(e.key); break;
      case 'Escape':
        if (S.route.tab === 'preview') { e.preventDefault(); go(S.previewBack || '#/questions'); }
        else if (S.route.id) { e.preventDefault(); go(listHash()); }
        break;
      default: break;
    }
  });

  // ---------- chat: the round button at the bottom right ----------
  // One ongoing thread with the coordinating agent session (tools/career.py chat, GET/POST /chat). A message is stored on
  // the server, posted to the session and optionally copied to the dedicated career WhatsApp group; replies come back here on the next poll.
  // Unread = agent messages newer than the highest one this browser has shown with the panel open.
  const CHAT_SEEN_KEY = 'careerapp.chat.seen';
  const CHAT_POLL_OPEN = 4000, CHAT_POLL_CLOSED = 30000;
  const C = { messages: [], last: 0, loaded: false, open: false, sending: false, error: null, timer: null, seen: readSeen() };

  function readSeen() { try { return parseInt(localStorage.getItem(CHAT_SEEN_KEY + ':' + S.token.slice(-12)) || '0', 10) || 0; } catch { return 0; } }
  function saveSeen(n) { C.seen = n; try { localStorage.setItem(CHAT_SEEN_KEY + ':' + S.token.slice(-12), String(n)); } catch { /* storage blocked */ } }
  const chatReady = () => {
    if (C.context !== S.token) { C.context = S.token;C.messages = [];C.last = 0;C.loaded = false;C.seen = readSeen();C.notifications = null; }
    return !!S.token && !S.authRefused;
  };
  const chatUnread = () => C.messages.filter((m) => m.from === 'agent' && m.n > C.seen).length;

  function chatBuild() {
    const fab = h('button', { type: 'button', class: 'chat-fab', id: 'chat-fab', 'aria-label': 'Ask your agent', title: 'Ask your agent',
      'aria-expanded': 'false', 'aria-controls': 'chat-panel', hidden: true }, icon('bubble'), h('span', { class: 'chat-badge', id: 'chat-badge', hidden: true }));
    const ta = h('textarea', { rows: 1, placeholder: 'Message', 'aria-label': 'Message', id: 'chat-input', maxlength: 4000 });
    const send = h('button', { type: 'button', class: 'btn primary chat-send', id: 'chat-send', disabled: true }, 'Send');
    const panel = h('section', { class: 'chat-panel', id: 'chat-panel', role: 'dialog', 'aria-label': 'Ask your agent', hidden: true },
      h('header', { class: 'chat-head' },
        h('div', { class: 'chat-title' }, h('div', { class: 't' }, 'Ask your agent'),
          h('div', { class: 's' }, 'Goes to your agent. Optional WhatsApp copies go only to your career group.')),
        h('button', { type: 'button', class: 'icon-btn', id: 'chat-close', 'aria-label': 'Close chat', title: 'Close (Esc)' }, icon('x'))),
      h('div', { class: 'chat-body', id: 'chat-body', 'aria-live': 'polite' }),
      h('div', { class: 'chat-attach', id: 'chat-attach', hidden: true }),
      h('div', { class: 'chat-foot' },
        h('button', { type: 'button', class: 'icon-btn chat-attach-btn', id: 'chat-attach-btn', title: 'Attach job', 'aria-label': 'Attach job',
          'aria-expanded': 'false', onclick: () => chatAttachToggle() }, icon('link')),
        ta, send));
    document.body.append(fab, panel);
    fab.addEventListener('click', () => chatToggle(!C.open));
    $('#chat-close', panel).addEventListener('click', () => chatToggle(false));
    const grow = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'; };
    ta.addEventListener('input', () => { send.disabled = C.sending || !ta.value.trim(); grow(); });
    ta.addEventListener('keydown', (e) => {
      if (sendKey(e)) { e.preventDefault(); if (!send.disabled) chatSend(); }
    });
    send.addEventListener('click', chatSend);
    panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); chatToggle(false); } });
  }

  // "Attach job": put a posting's ref (the same line as Copy ref) into the message without copying.
  function chatAttachToggle(force) {
    const box = $('#chat-attach'), btn = $('#chat-attach-btn');
    const open = force != null ? force : box.hidden;
    box.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    if (!open) return;
    const search = h('input', { class: 'text', type: 'search', placeholder: 'Company or title', 'aria-label': 'Find a job' });
    const list = h('div', { class: 'chat-attach-list' });
    const rank = (p) => (waitsOnUser(p) ? 0 : ACTION_OF(p) ? 1 : p.application ? 2 : ['approved', 'tailored'].includes(p.status) ? 3 : 4);
    const fill = () => {
      const q = search.value.trim().toLowerCase();
      const rows = S.postings.filter((p) => p.status !== 'variant' && (!q || `${p.company} ${p.title}`.toLowerCase().includes(q)))
        .sort((a, b) => rank(a) - rank(b) || cmp(a.company, b.company)).slice(0, 25);
      list.replaceChildren(...(rows.length ? rows.map((p) => h('button', { type: 'button', class: 'chat-attach-row', onclick: () => {
        const ta = $('#chat-input');
        const ref = jobRef(p);
        ta.value = ta.value.trim() ? `${ta.value.replace(/\s+$/, '')}\n${ref}\n` : `${ref}\n`;
        ta.dispatchEvent(new Event('input'));
        chatAttachToggle(false);
        ta.focus();
      } }, h('span', { class: 't' }, p.title), h('span', { class: 's' }, p.company))) : [h('div', { class: 'small muted' }, 'No match.')]));
    };
    search.addEventListener('input', fill);
    search.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); chatAttachToggle(false); } });
    box.replaceChildren(search, list);
    fill();
    setTimeout(() => search.focus(), 0);
  }

  function chatChrome() {
    const fab = $('#chat-fab');
    if (!fab) return;
    const ready = chatReady();
    fab.hidden = !ready;
    if (!ready && C.open) chatToggle(false);
    const n = C.open ? 0 : chatUnread();
    const badge = $('#chat-badge');
    badge.hidden = !n;
    badge.textContent = String(n);
    fab.setAttribute('aria-label', n ? `Ask your agent, ${n} new ${n === 1 ? 'reply' : 'replies'}` : 'Ask your agent');
  }

  function chatToggle(open) {
    C.open = open;
    const panel = $('#chat-panel'), fab = $('#chat-fab');
    panel.hidden = !open;
    fab.setAttribute('aria-expanded', String(open));
    fab.classList.toggle('open', open);
    document.body.classList.toggle('chat-open', open);
    if (open) {
      chatRender(true);
      chatPoll();
      setTimeout(() => $('#chat-input').focus(), 0);
    } else if (panel.contains(document.activeElement)) {
      fab.focus();
    }
    chatSchedule();
    chatChrome();
  }

  function chatSchedule() {
    clearTimeout(C.timer);
    C.timer = setTimeout(async () => {
      if (document.visibilityState === 'visible') await chatPoll();
      chatSchedule();
    }, C.open ? CHAT_POLL_OPEN : CHAT_POLL_CLOSED);
  }

  function chatMerge(list) {
    const have = new Set(C.messages.map((m) => m.n));
    let added = false;
    for (const m of Array.isArray(list) ? list : []) {
      if (!m || typeof m.n !== 'number' || have.has(m.n)) continue;
      C.messages.push(m);
      have.add(m.n);
      added = true;
    }
    if (added) C.messages.sort((a, b) => a.n - b.n);
    C.last = C.messages.length ? C.messages[C.messages.length - 1].n : 0;
    return added;
  }

  async function chatPoll() {
    if (!chatReady() || S.blockedUntil > Date.now()) return;
    try {
      const r = await api('/chat' + (C.last ? `?since=${C.last}` : ''));
      const added = chatMerge(Array.isArray(r) ? r : r && r.messages);
      C.loaded = true;
      C.error = null;
      try { C.notifications = await api('/notifications'); } catch { /* Optional notification diagnostics. */ }
      if (added || C.open) chatRender(added);
    } catch (e) {
      C.error = e.message;
      if (C.open) chatRender(false);
    }
    chatChrome();
  }

  async function chatSend() {
    const ta = $('#chat-input'), btn = $('#chat-send');
    const text = ta.value.trim();
    if (!text || C.sending) return;
    C.sending = true;
    btn.disabled = true;
    btn.textContent = 'Sending…';
    try {
      const m = await api('/chat', { method: 'POST', body: { text, by: BY } });
      chatMerge([m]);
      ta.value = '';
      ta.style.height = 'auto';
      C.error = null;
      chatRender(true);
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      C.sending = false;
      btn.textContent = 'Send';
      btn.disabled = !ta.value.trim();
      ta.focus();
    }
  }

  function chatWhen(at) {
    const d = at ? new Date(at) : null;
    if (!d || Number.isNaN(d.getTime())) return '';
    return (Date.now() - d.getTime() < 20 * 3600 * 1000 ? fmtTime : fmtStamp).format(d);
  }

  function chatRender(scroll) {
    const body = $('#chat-body');
    if (!body || !C.open) return;
    const nearEnd = body.scrollHeight - body.scrollTop - body.clientHeight < 80;
    const kids = [];
    if (!C.loaded && !C.error) kids.push(h('div', { class: 'chat-empty' }, 'Loading…'));
    else if (!C.messages.length) {
      kids.push(h('div', { class: 'chat-empty' }, h('div', { class: 'big' }, 'Ask anything'),
        'Questions, changes, or something that looks wrong. Your agent replies here.'));
    }
    for (const m of C.messages) {
      const mine = m.from === 'user';
      kids.push(h('div', { class: `turn ${mine ? 'user' : 'ask latest'}`, 'data-n': m.n },
        h('div', { class: 'bubble pre' }, String(m.text || '')),
        m.delivery_error ? h('div',{class:'warn-line small'},'Saved; agent delivery failed: '+m.delivery_error.error) : null,
        h('div', { class: 'turn-meta' }, [mine ? 'You' : 'Agent', chatWhen(m.at)].filter(Boolean).join(' · '))));
    }
    for (const issue of C.notifications?.issues || []) kids.push(h('div',{class:'warn-line small'},'WhatsApp: '+issue));
    if (C.error) kids.push(h('div', { class: 'warn-line small' }, icon('alert'), C.error));
    body.replaceChildren(...kids);
    if (scroll || nearEnd) body.scrollTop = body.scrollHeight;
    const top = C.messages.filter((m) => m.from === 'agent').reduce((a, m) => Math.max(a, m.n), 0);
    if (top > C.seen) saveSeen(top);
  }

  // ---------- boot ----------
  function boot() {
    $('#btn-refresh').append(icon('refresh'));
    $('#btn-keys').append(icon('keyboard'));
    $('#btn-pair').append(icon('gear'));
    $('#btn-refresh').addEventListener('click', () => refreshAll({ announce: true }));
    $('#btn-keys').addEventListener('click', showKeys);
    chatBuild();
    chatSchedule();
    window.addEventListener('hashchange', () => render());
    WIDE.addEventListener('change', () => render({ data: true, detail: true }));
    // Stay current without a manual refresh: on return to the tab, and every two minutes while it is visible.
    // Stopped while hidden: checkFresh and chatPoll return at once. Coming back looks straight away.
    const back = () => { if (document.visibilityState === 'visible') { checkFresh(); chatPoll(); } };
    document.addEventListener('visibilitychange', back);
    window.addEventListener('focus', back);
    window.addEventListener('pageshow', (e) => { if (e.persisted) back(); });
    setInterval(() => checkFresh(), POLL_MS);
    // A dialog closing or a text box losing focus is when held-back work can land.
    $('#dialog').addEventListener('close', () => setTimeout(settle, 0));
    $('#viewer').addEventListener('close', () => setTimeout(settle, 0));
    document.addEventListener('focusout', () => setTimeout(settle, 0));
    const opening = location.hash.match(/^#\/open\/([A-Za-z0-9_-]+)$/);
    if (opening) return openWithCode(opening[1]);
    if (S.token) { render(); refreshAll(); return; }
    render();
  }

  // Dismiss lives in the question detail; wire it from a delegated listener so the result card stays simple.
  document.addEventListener('click', (e) => {
    const b = e.target instanceof Element && e.target.closest('[data-dismiss]');
    if (!b) return;
    const q = S.qById.get(b.getAttribute('data-dismiss'));
    if (q) dismissQuestion(q);
  });

  async function openWithCode(code) {
    history.replaceState(null, '', location.pathname + '#/today');
    try {
      const res = await fetch('/web/open', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                             body: JSON.stringify({ code }) });
      const data = res.ok ? await res.json() : null;
      if (data && data.token) {
        S.token = data.token;
        S.authRefused = false;
        if (!saveToken(data.token)) toast('This browser blocks local storage; the pairing lasts until the tab closes.', 'error');
      } else {
        toast('This link has expired. Run career open again.', 'error');
      }
    } catch {
      toast('Could not reach your agent server.', 'error');
    }
    render();
    if (S.token) refreshAll();
  }

  boot();
})();
