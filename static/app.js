'use strict';
/* Grandscores Studio front end (v5.40) */
const $ = id => document.getElementById(id);
const RESULT_FONT_SIZE = 42, MAX_TEAM_CHARS = 20, MAX_ROWS = 12;
const ENGLISH = ['Premier League', 'Championship', 'League One', 'League Two'];
const BASE_COMPETITIONS = [...ENGLISH, 'International Football'];
const LIVE_REFRESH_MS = 60000;

const state = {
  mode: 'results',          // results | table
  format: 'video',          // video | printer
  filter: 'english',        // english | all
  range: { live: true },    // {live:true} or {start,end}
  fixtures: [],
  feedLoaded: false,
  added: new Set(),         // fixture ids already on a page
  previewPage: 1,
  printerPage: 0,
  liveTimer: null,
};

function esc(s) { return String(s ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }
function testMode() { return $('testMode').checked; }
function setStatus(msg, kind = 'ok') { const el = $('status'); el.textContent = msg; el.className = 'status ' + (msg ? kind : ''); }
async function readJson(r, context) {
  const raw = await r.text(); let j;
  try { j = JSON.parse(raw); } catch (e) { throw new Error(`${context} returned an unexpected response (HTTP ${r.status}). Refresh the page and try again.`); }
  if (!r.ok) throw new Error(j.error || `${context} failed (HTTP ${r.status}).`);
  return j;
}
function canonicalLeague(name) {
  const n = String(name || '').trim().toLowerCase();
  if (n === 'premier league') return 'Premier League';
  if (n === 'championship' || n === 'football league championship') return 'Championship';
  if (n === 'league one' || n === 'football league one') return 'League One';
  if (n === 'league two' || n === 'football league two') return 'League Two';
  return String(name || '').trim();
}
function leagueOrder(name) { const i = ENGLISH.indexOf(canonicalLeague(name)); return i < 0 ? 99 : i; }

/* ---------------- dates ---------------- */
function isoDay(d) { const y = d.getFullYear(), m = String(d.getMonth() + 1).padStart(2, '0'), dd = String(d.getDate()).padStart(2, '0'); return `${y}-${m}-${dd}`; }
function daysAgo(n) { const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() - n); return d; }
function buildDateStrip() {
  const strip = $('dateStrip'); strip.innerHTML = '';
  const chips = [{ key: 'live', title: 'Live', sub: 'Today', live: true }];
  for (let i = 1; i <= 6; i++) {
    const d = daysAgo(i);
    chips.push({ key: isoDay(d), title: i === 1 ? 'Yesterday' : d.toLocaleDateString('en-GB', { weekday: 'short' }), sub: d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) });
  }
  chips.push({ key: 'range', title: 'Range', sub: 'Pick dates' });
  chips.forEach(c => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'chip' + (c.live ? ' live' : ''); b.dataset.key = c.key; b.setAttribute('role', 'radio');
    b.innerHTML = `<b>${esc(c.title)}</b><small>${esc(c.sub)}</small>`;
    b.onclick = () => chooseDay(c.key);
    strip.appendChild(b);
  });
  const today = isoDay(new Date());
  $('rangeStart').max = today; $('rangeEnd').max = today;
  $('rangeStart').value = isoDay(daysAgo(6)); $('rangeEnd').value = today;
}
function markChip(key) {
  document.querySelectorAll('.chip').forEach(c => { const on = c.dataset.key === key; c.classList.toggle('active', on); c.setAttribute('aria-checked', on); });
}
function chooseDay(key) {
  markChip(key);
  $('rangeRow').hidden = key !== 'range';
  if (key === 'range') return; // wait for "Load range"
  state.range = key === 'live' ? { live: true } : { start: key, end: key };
  loadFeed();
}
$('rangeLoad').onclick = () => {
  const s = $('rangeStart').value, e = $('rangeEnd').value;
  if (!s || !e) { feedMeta('Choose both a start and an end date.', true); return; }
  state.range = { start: s, end: e }; loadFeed();
};

/* ---------------- fixture feed ---------------- */
function feedMeta(text, err = false) { const m = $('feedMeta'); m.textContent = text; m.classList.toggle('err', err); }
function stopLiveTimer() { if (state.liveTimer) { clearInterval(state.liveTimer); state.liveTimer = null; } }
async function loadFeed({ quiet = false } = {}) {
  stopLiveTimer();
  const p = new URLSearchParams();
  if (!state.range.live) { p.set('start', state.range.start); p.set('end', state.range.end); }
  if (testMode()) p.set('mode', 'test');
  if (!quiet) { feedMeta(testMode() ? 'Loading test data…' : 'Contacting Sportmonks…'); }
  try {
    const j = await readJson(await fetch('/api/fixtures?' + p, { cache: 'no-store' }), 'Fixtures');
    state.fixtures = j.fixtures || []; state.feedLoaded = true;
    state.rangeLabel = j.label; state.multiDay = j.start !== j.end;
    renderFeed();
    const live = state.fixtures.filter(f => f.kind === 'live').length, fin = state.fixtures.filter(f => f.kind === 'finished').length;
    const bits = [`${j.label}: ${state.fixtures.length} matches`, `${fin} finished`];
    if (live) bits.push(`${live} live`);
    bits.push(`updated ${j.updated_at.split(' ').pop()}`);
    feedMeta(bits.join(', ') + (state.range.live ? '. Refreshes every minute.' : '.'));
    $('refreshFeed').hidden = false;
    if (state.range.live) state.liveTimer = setInterval(() => loadFeed({ quiet: true }), LIVE_REFRESH_MS);
  } catch (e) {
    feedMeta(e.message, true);
    if (!quiet) { state.fixtures = []; renderFeed(); }
  }
}
$('refreshFeed').onclick = () => loadFeed();
document.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => {
  state.filter = b.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(x => x.classList.toggle('active', x === b));
  renderFeed(); // filter locally; no new request
});

function visibleFixtures() {
  let arr = state.fixtures.slice();
  if (state.filter === 'english') arr = arr.filter(f => ENGLISH.includes(canonicalLeague(f.league)));
  return arr;
}
function fixtureRow(f) {
  const done = f.kind === 'finished';
  return { home: f.home, away: f.away, homeScore: done ? f.home_score : null, awayScore: done ? f.away_score : null, scorers: done ? (f.scorers || []) : [], _id: String(f.id) };
}
function whenCell(f) {
  const label = f.kind === 'live' ? 'LIVE' : f.kind === 'finished' ? 'FT' : (f.time || 'TBC');
  const mins = f.kind === 'live' && f.elapsed_minutes != null ? `<span class="mins">${esc(f.elapsed_minutes)}'</span>` : '';
  let day = '';
  if (state.multiDay && f.date) day = new Date(f.date + 'T12:00').toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric' }) + '<br>';
  return `${day}<span class="state ${esc(f.kind)}">${label}</span>${mins}`;
}
function renderFeed() {
  const feed = $('feed'); feed.innerHTML = '';
  if (!state.feedLoaded) return;
  const arr = visibleFixtures();
  if (!arr.length) {
    feed.innerHTML = `<div class="feed-empty">No ${state.filter === 'english' ? 'English league ' : ''}matches for ${esc(state.rangeLabel || 'this day')}.${state.filter === 'english' ? ' Switch to All leagues to see everything Sportmonks covers.' : ''}</div>`;
    return;
  }
  const groups = new Map();
  arr.sort((a, b) => leagueOrder(a.league) - leagueOrder(b.league) || canonicalLeague(a.league).localeCompare(canonicalLeague(b.league)) || (a.date || '').localeCompare(b.date || '') || (a.home || '').localeCompare(b.home || ''));
  arr.forEach(f => { const k = canonicalLeague(f.league) || 'Other'; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(f); });
  groups.forEach((list, league) => {
    const g = document.createElement('div'); g.className = 'group';
    const remaining = list.filter(f => !state.added.has(String(f.id)));
    const finished = list.filter(f => f.kind === 'finished').length;
    g.innerHTML = `<div class="group-head"><h3>${esc(league)}</h3><span class="hint">${finished} of ${list.length} finished</span></div>`;
    const all = document.createElement('button'); all.type = 'button'; all.className = 'btn'; all.textContent = remaining.length ? `Add all ${remaining.length}` : 'All added';
    all.disabled = !remaining.length;
    all.onclick = () => addFixtures(league, remaining);
    g.querySelector('.group-head').appendChild(all);
    list.forEach(f => {
      const added = state.added.has(String(f.id));
      const sc = f.home_score == null || f.away_score == null ? 'v' : `${f.home_score}-${f.away_score}`;
      const r = document.createElement('div'); r.className = 'fx' + (added ? ' added' : '');
      r.innerHTML = `<div class="when">${whenCell(f)}</div><div class="tm h">${esc(f.home)}</div><div class="sc">${esc(sc)}</div><div class="tm">${esc(f.away)}</div>`;
      const b = document.createElement('button'); b.type = 'button'; b.className = 'btn' + (added ? ' ghost' : ' primary');
      b.textContent = added ? 'Added' : 'Add'; b.disabled = added;
      b.onclick = () => addFixtures(league, [f]);
      r.appendChild(b); g.appendChild(r);
    });
    feed.appendChild(g);
  });
}

/* ---------------- page routing ---------------- */
function rowsEl(p) { return $(p === 1 ? 'rows1' : 'rows2'); }
function compEl(p) { return $(p === 1 ? 'competition1' : 'competition2'); }
function filledRows(p) { return [...rowsEl(p).children].filter(d => d.querySelector('.home').value.trim() || d.querySelector('.away').value.trim()); }
function pageEnabled(p) { return p === 1 || $('include2').checked; }
function ensureCompetitionOption(name) {
  [1, 2].forEach(p => { const s = compEl(p); if (![...s.options].some(o => o.value === name)) s.add(new Option(name, name)); });
}
/** Decide which page a league's results belong on. */
function routeLeague(league) {
  for (const p of [1, 2]) if (pageEnabled(p) && compEl(p).value === league && filledRows(p).length < MAX_ROWS) return p;
  for (const p of [1, 2]) if (pageEnabled(p) && !filledRows(p).length) { ensureCompetitionOption(league); compEl(p).value = league; syncPreviewLabels(); return p; }
  return null;
}
function addFixtures(league, list) {
  if (!list.length) return;
  const page = routeLeague(league);
  if (!page) { setStatus(`Both pages already hold other competitions. Clear a page, or change its competition to ${league}.`, 'err'); return; }
  const res = addRowsToPage(page, list.map(fixtureRow));
  const late = list.slice(0, res.added).filter(f => f.kind !== 'finished').length;
  let msg = `Added ${res.added} ${league} result${res.added === 1 ? '' : 's'} to page ${page}`;
  if (late) msg += ` (${late} still in progress, marked late)`;
  msg += '.';
  if (res.skipped) msg += ` ${res.skipped} didn't fit: a page holds ${MAX_ROWS} results.`;
  setStatus(msg, res.skipped ? 'err' : 'ok');
  showPreviewPage(page);
  renderFeed();
}

/* ---------------- result rows ---------------- */
let measureCanvas = null;
function teamInputWarning(input) {
  const row = input.closest('.row'); let w = row.querySelector('.char-warning');
  const bad = input.value.length > MAX_TEAM_CHARS; input.classList.toggle('text-too-long', bad);
  const anyBad = row.querySelector('.home.text-too-long, .away.text-too-long');
  if (anyBad) {
    if (!w) { w = document.createElement('div'); w.className = 'char-warning'; row.appendChild(w); }
    const n = anyBad.value.length; w.textContent = `Team name is ${n} characters. The graphic fits ${MAX_TEAM_CHARS}.`;
  } else if (w) w.remove();
}
function refreshAllWarnings() { document.querySelectorAll('.row .home,.row .away').forEach(teamInputWarning); }
function addRow(p, r = {}) {
  const target = rowsEl(p); if (target.children.length >= MAX_ROWS) return null;
  const d = document.createElement('div'); d.className = 'row';
  d.dataset.scorers = JSON.stringify(r.scorers || []);
  if (r._id) d.dataset.fixtureId = r._id;
  const hasTeams = !!(r.home || r.away);
  const late = hasTeams && (r.homeScore == null || r.awayScore == null);
  d.innerHTML = `<input class="home" maxlength="40" placeholder="Home team" aria-label="Home team" value="${esc(r.home || '')}">`
    + `<input class="score" placeholder="1-2" aria-label="Score" value="${late || r.homeScore == null ? '' : r.homeScore + '-' + r.awayScore}">`
    + `<input class="away" maxlength="40" placeholder="Away team" aria-label="Away team" value="${esc(r.away || '')}">`
    + `<input class="latebox" type="checkbox" aria-label="Late result" ${late ? 'checked' : ''}>`
    + `<button type="button" class="del" title="Remove result" aria-label="Remove result">×</button>`;
  d.classList.toggle('late-on', late);
  target.appendChild(d);
  d.querySelectorAll('input:not(.latebox)').forEach(x => x.addEventListener('input', () => { if (x.matches('.home,.away')) teamInputWarning(x); updateCounts(); queuePreview(); }));
  d.querySelector('.latebox').addEventListener('change', e => { d.classList.toggle('late-on', e.target.checked); if (e.target.checked) d.querySelector('.score').value = ''; queuePreview(); });
  d.querySelector('.del').addEventListener('click', () => { if (d.dataset.fixtureId) state.added.delete(d.dataset.fixtureId); d.remove(); if (!rowsEl(p).children.length) addRow(p); updateCounts(); renderFeed(); queuePreview(); });
  teamInputWarning(d.querySelector('.home')); teamInputWarning(d.querySelector('.away'));
  return d;
}
/** Sort filled rows by home team; empty rows always go to the bottom. */
function sortRows(p) {
  const t = rowsEl(p);
  [...t.children].sort((a, b) => {
    const ha = a.querySelector('.home').value.trim(), hb = b.querySelector('.home').value.trim();
    if (!ha !== !hb) return ha ? -1 : 1;
    return ha.localeCompare(hb, undefined, { sensitivity: 'base' });
  }).forEach(r => t.appendChild(r));
}
function fillRow(d, r) {
  d.querySelector('.home').value = r.home || ''; d.querySelector('.away').value = r.away || '';
  const late = r.homeScore == null || r.awayScore == null;
  d.querySelector('.latebox').checked = late; d.classList.toggle('late-on', late);
  d.querySelector('.score').value = late ? '' : `${r.homeScore}-${r.awayScore}`;
  d.dataset.scorers = JSON.stringify(r.scorers || []);
  if (r._id) d.dataset.fixtureId = r._id;
  teamInputWarning(d.querySelector('.home')); teamInputWarning(d.querySelector('.away'));
}
function addRowsToPage(p, arr) {
  let added = 0;
  const empties = [...rowsEl(p).children].filter(d => !d.querySelector('.home').value.trim() && !d.querySelector('.away').value.trim());
  for (const r of arr) {
    const d = empties.shift() || addRow(p);
    if (!d) break;
    fillRow(d, r); if (r._id) state.added.add(r._id); added++;
  }
  sortRows(p); updateCounts(); queuePreview();
  return { added, skipped: arr.length - added };
}
function clearPage(p) {
  [...rowsEl(p).children].forEach(d => d.dataset.fixtureId && state.added.delete(d.dataset.fixtureId));
  rowsEl(p).innerHTML = ''; for (let i = 0; i < 3; i++) addRow(p);
  updateCounts(); renderFeed(); queuePreview();
}
function updateCounts() { [1, 2].forEach(p => { const n = filledRows(p).length; $('count' + p).textContent = n ? `${n} of ${MAX_ROWS}` : 'Empty'; }); }
function leagueData(p) {
  return {
    competition: compEl(p).value,
    results: [...rowsEl(p).children].map(d => {
      const late = d.querySelector('.latebox').checked, m = d.querySelector('.score').value.trim().match(/^(\d+)\s*[-:]\s*(\d+)$/);
      let scorers = []; try { scorers = JSON.parse(d.dataset.scorers || '[]'); } catch (_) { }
      return { home: d.querySelector('.home').value.trim(), away: d.querySelector('.away').value.trim(), homeScore: late || !m ? null : Number(m[1]), awayScore: late || !m ? null : Number(m[2]), scorers };
    }).filter(r => r.home || r.away)
  };
}
function allLeagues() { const ls = [leagueData(1)]; if ($('include2').checked) ls.push(leagueData(2)); return ls; }

[1, 2].forEach(p => {
  $('add' + p).onclick = () => { if (!addRow(p)) setStatus(`Page ${p} already has ${MAX_ROWS} rows.`, 'err'); };
  $('clear' + p).onclick = () => clearPage(p);
  compEl(p).onchange = () => { syncPreviewLabels(); showPreviewPage(p); };
});
$('include2').onchange = () => { $('page2Card').classList.toggle('off', !$('include2').checked); queuePreview(); };

/* ---------------- testing tools ---------------- */
const SAMPLE_TEAMS = ['Arsenal', 'Aston Villa', 'Bournemouth', 'Brentford', 'Chelsea', 'Crystal Palace', 'Everton', 'Fulham', 'Leeds United', 'Liverpool', 'Manchester City', 'Manchester United', 'Newcastle United', 'Nottingham Forest', 'Sunderland', 'Tottenham Hotspur', 'Blackburn Rovers', 'Birmingham City', 'Bristol City', 'Derby County', 'Middlesbrough', 'Millwall', 'Norwich City', 'Preston North End', 'Sheffield United', 'Stoke City', 'Watford', 'Wrexham'];
const SCORELINES = [[0, 0], [1, 0], [0, 1], [1, 1], [2, 0], [0, 2], [2, 1], [1, 2], [2, 2], [3, 0], [3, 1], [1, 3], [3, 2], [4, 1]];
function randomSample(n = 9) { const t = [...SAMPLE_TEAMS].sort(() => Math.random() - .5); return Array.from({ length: n }, (_, i) => { const s = SCORELINES[Math.floor(Math.random() * SCORELINES.length)]; return { home: t[i * 2], away: t[i * 2 + 1], homeScore: s[0], awayScore: s[1] }; }); }
[1, 2].forEach(p => $('sample' + p).onclick = () => { clearPage(p); rowsEl(p).innerHTML = ''; addRowsToPage(p, randomSample(9)); setStatus(`Filled page ${p} with 9 random results.`); showPreviewPage(p); });
function scorerList(text, side) { return String(text || '').split(',').map(x => x.trim()).filter(Boolean).map(name => ({ name, team: side })); }
$('manualAdd').onclick = () => {
  const hs = parseInt($('manualHomeScore').value, 10), as = parseInt($('manualAwayScore').value, 10);
  const home = $('manualHome').value.trim(), away = $('manualAway').value.trim();
  if (!home || !away || !(hs >= 0) || !(as >= 0)) { setStatus('Enter both teams and a score of 0 or more for each.', 'err'); return; }
  const p = Number($('manualTarget').value);
  const r = { home, away, homeScore: hs, awayScore: as, scorers: [...scorerList($('manualHomeScorers').value, 'home'), ...scorerList($('manualAwayScorers').value, 'away')] };
  const res = addRowsToPage(p, [r]);
  if (!res.added) { setStatus(`Page ${p} is full (${MAX_ROWS} results).`, 'err'); return; }
  setStatus(`Added ${home} ${hs}-${as} ${away} to page ${p}.`); showPreviewPage(p);
};
$('testMode').onchange = () => {
  $('testBanner').hidden = !testMode();
  if (state.mode === 'results') loadFeed();
};

/* ---------------- output format ---------------- */
document.querySelectorAll('[data-format]').forEach(b => b.onclick = () => {
  state.format = b.dataset.format; state.printerPage = 0;
  document.querySelectorAll('[data-format]').forEach(x => x.classList.toggle('active', x === b));
  $('introOpt').hidden = state.format !== 'video';
  $('generate').textContent = state.format === 'video' ? 'Generate results video' : 'Generate vidi printer';
  updatePreviewChrome(); queuePreview();
});

/* ---------------- preview ---------------- */
let previewSeq = 0;
function showImage(src) { const img = $('preview'); if (src) { img.src = src; img.hidden = false; $('previewEmpty').hidden = true; } else { img.hidden = true; img.removeAttribute('src'); $('previewEmpty').hidden = false; } }
function syncPreviewLabels() { document.querySelectorAll('#previewPages [data-page]').forEach(b => { b.textContent = compEl(Number(b.dataset.page)).value; }); }
function updatePreviewChrome() {
  const results = state.mode === 'results';
  $('previewPages').hidden = !(results && state.format === 'video');
  if (!(results && state.format === 'printer')) { $('printerPages').hidden = true; $('printerNote').hidden = true; }
  $('previewScenes').hidden = results;
  $('previewTitle').textContent = results ? (state.format === 'printer' ? 'Vidi printer' : 'Preview') : 'Table preview';
}
function showPreviewPage(p) { state.previewPage = p; document.querySelectorAll('#previewPages [data-page]').forEach(b => b.classList.toggle('active', Number(b.dataset.page) === p)); queuePreview(); }
document.querySelectorAll('#previewPages [data-page]').forEach(b => b.onclick = () => showPreviewPage(Number(b.dataset.page)));
function queuePreview() { clearTimeout(window.__pt); window.__pt = setTimeout(updatePreview, 120); }
async function updatePreview() {
  if (state.mode !== 'results') return;
  const seq = ++previewSeq;
  try {
    let url, body;
    if (state.format === 'printer') {
      const ls = allLeagues(); if (!ls.some(l => l.results.length)) { $('previewEmpty').textContent = 'Add a result to see the vidi printer.'; showImage(null); return; }
      url = '/preview-printer'; body = { leagues: ls, mid90sLoopBackground: $('mid90sBg').checked, page: state.printerPage };
    } else {
      const d = leagueData(state.previewPage); if (!d.results.length) { $('previewEmpty').textContent = `Page ${state.previewPage} is empty. Add a result to see the graphic.`; showImage(null); return; }
      url = '/preview'; body = d;
    }
    const j = await readJson(await fetch(url, { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), 'Preview');
    if (seq !== previewSeq) return;
    showImage(j.image);
    if (state.format === 'printer') renderPrinterPager(j);
  } catch (e) { if (seq === previewSeq) { showImage(null); $('previewEmpty').textContent = e.message; } }
}

function renderPrinterPager(j) {
  state.printerPage = j.page;
  const wrap = $('printerPages'); wrap.innerHTML = '';
  wrap.hidden = j.page_count < 2;
  for (let i = 0; i < j.page_count; i++) {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'seg-btn' + (i === j.page ? ' active' : '');
    b.textContent = String(i + 1); b.title = `${j.pages[i].competition}: ${j.pages[i].results} result${j.pages[i].results === 1 ? '' : 's'}`;
    b.setAttribute('aria-label', `Page ${i + 1}`);
    b.onclick = () => { state.printerPage = i; updatePreview(); };
    wrap.appendChild(b);
  }
  const note = $('printerNote');
  note.hidden = j.page_count < 2;
  note.textContent = `The video prints ${j.page_count} pages: ${j.pages.map(p => p.results).join(', ')} results. Late results are left out.`;
}

/* ---------------- render jobs ---------------- */
function pollJob(id, ui) {
  const tick = async () => {
    try {
      const j = await readJson(await fetch('/status/' + id), 'Render status');
      $(ui.bar).style.width = (j.progress || 0) + '%'; $(ui.text).textContent = j.message || 'Rendering…';
      if (j.status === 'done') { $(ui.bar).style.width = '100%'; const a = $(ui.dl); a.href = '/download/' + id; a.hidden = false; return; }
      if (j.status === 'error') { ui.onError && ui.onError(j.message || 'Render failed.'); return; }
      setTimeout(tick, 1000);
    } catch (e) { setTimeout(tick, 2000); }
  };
  tick();
}
$('generate').onclick = async () => {
  refreshAllWarnings();
  const leagues = allLeagues();
  if (!leagues.some(l => l.results.length)) { setStatus('Add at least one result first.', 'err'); return; }
  if (state.format === 'video') {
    if (!leagues[0].results.length) { setStatus('Page 1 is empty. Add results to it, or move page 2’s results across.', 'err'); return; }
    if (leagues.length === 2 && !leagues[1].results.length) { setStatus('Page 2 is included but empty. Add results or switch Include off.', 'err'); return; }
  }
  const long = leagues.flatMap((l, i) => l.results.flatMap(r => [r.home, r.away].filter(t => t.length > MAX_TEAM_CHARS).map(t => `${t} (page ${i + 1})`)));
  setStatus(long.length ? `Long team name may be squeezed: ${long[0]}. Rendering anyway.` : '', 'err');
  const isVideo = state.format === 'video';
  const body = isVideo ? { leagues, includeIntroMusic: $('includeMusic').checked, mid90sLoopBackground: $('mid90sBg').checked } : { leagues, mid90sLoopBackground: $('mid90sBg').checked };
  $('job').hidden = false; $('download').hidden = true; $('bar').style.width = '3%'; $('jobtext').textContent = 'Starting render…';
  try {
    const j = await readJson(await fetch(isVideo ? '/generate' : '/generate-printer', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), 'Render');
    pollJob(j.id, { bar: 'bar', text: 'jobtext', dl: 'download', onError: m => setStatus(m, 'err') });
  } catch (e) { $('jobtext').textContent = 'Render didn’t start.'; setStatus(e.message, 'err'); }
};

/* ---------------- league table mode ---------------- */
const tableState = { league: null, loaded: false, voiceoverConfirmed: false, scene: 'top' };
function tableHalfCount() { return Math.ceil((tableState.league?.rows?.length || 0) / 2); }
function voTeamNameJS(name) { const m = { 'wycombe wanderers': 'Wickam Wanderers', 'reading': 'Redding', 'gillingham': 'Jillingham', 'salford': 'Solford', 'salford city': 'Solford City', 'tottenham': 'Totteringham', 'tottenham hotspur': 'Totteringham Hotspur', 'wolves': 'Wolverhampton Wanderers', 'west brom albion': 'West Bromwich Albion', 'milton keynes dons': 'Milton Keens Dons' }; return m[String(name || '').trim().toLowerCase()] || String(name || ''); }
function ordinal(n) { n = Number(n); if (n % 100 >= 11 && n % 100 <= 13) return n + 'th'; return n % 10 === 1 ? n + 'st' : n % 10 === 2 ? n + 'nd' : n % 10 === 3 ? n + 'rd' : n + 'th'; }
function tableScriptText() {
  const t = tableState.league; if (!t || !t.rows || !t.rows.length) return 'Load a table to generate the voiceover script.';
  const half = tableHalfCount(), top = t.rows.slice(0, half), bottom = t.rows.slice(half), mention = $('mentionPts').checked;
  const maxPlayed = Math.max(...t.rows.map(r => Number(r.played) || 0));
  const gamesNote = r => { const p = Number(r.played) || 0; if (!p || !maxPlayed || p === maxPlayed) return ''; const d = maxPlayed - p; return d === 1 ? ' They have a game in hand.' : ` They have ${d} games in hand.`; };
  let s = `The top half of the ${t.name} table. ${voTeamNameJS(top[0].name)} lead the way`; if (mention) s += ` with ${top[0].points} points from ${top[0].played} games`; s += gamesNote(top[0]); s += '.';
  if (top.length > 1) { s += ` ${voTeamNameJS(top[1].name)} are second`; if (mention) s += `, with ${top[1].points} points`; s += '.'; }
  if (top.length > 2) { s += ` ${voTeamNameJS(top[2].name)} are third`; if (mention) s += `, on ${top[2].points} points`; s += '.'; }
  if (bottom.length) {
    s += ` And here's the bottom half of the table.`; const b0 = bottom[bottom.length - 1];
    s += ` ${voTeamNameJS(b0.name)} are ${ordinal(b0.position)} and sit at the bottom`; if (mention) s += `, on ${b0.points} points`; s += gamesNote(b0); s += '.';
    if (bottom.length > 1) { const b1 = bottom[bottom.length - 2]; s += ` ${voTeamNameJS(b1.name)} are ${ordinal(b1.position)}`; if (mention) s += `, with ${b1.points} points`; s += '.'; }
    if (bottom.length > 2) { const b2 = bottom[bottom.length - 3]; s += ` ${voTeamNameJS(b2.name)} are ${ordinal(b2.position)}`; if (mention) s += `, with ${b2.points} points`; s += '.'; }
  }
  return s;
}
function voStatus(msg, ok) { const el = $('tableVOStatus'); el.textContent = msg; el.style.color = ok ? 'var(--ok)' : 'var(--ceefax)'; }
function refreshTableScript() { $('tableScriptEditor').value = tableScriptText(); tableState.voiceoverConfirmed = false; voStatus(tableState.loaded ? 'Check the script, then confirm it.' : '', false); }
$('mentionPts').addEventListener('change', () => { refreshTableScript(); });
['showStats', 'tableEndLogo'].forEach(id => $(id).addEventListener('change', () => tableState.loaded && updateTablePreview()));
$('tableScriptEditor').addEventListener('input', () => { tableState.voiceoverConfirmed = false; voStatus('Changes not confirmed yet.', false); });
$('confirmTableVO').onclick = () => { tableState.voiceoverConfirmed = true; voStatus('Script confirmed.', true); };
document.querySelectorAll('#previewScenes [data-scene]').forEach(b => b.onclick = () => { tableState.scene = b.dataset.scene; document.querySelectorAll('#previewScenes [data-scene]').forEach(x => x.classList.toggle('active', x === b)); updateTablePreview(); });
$('loadTable').onclick = async () => {
  const mode = testMode() ? 'test' : 'live';
  $('tableMeta').textContent = mode === 'test' ? 'Loading test table…' : 'Contacting Sportmonks…'; $('tableMeta').classList.remove('err');
  try {
    const j = await readJson(await fetch(`/api/league-table?league_id=${$('tableLeague').value}&mode=${mode}`, { cache: 'no-store' }), 'League table');
    tableState.league = j; tableState.loaded = true;
    $('tableMeta').textContent = `${j.name}: ${j.rows.length} teams, updated ${j.updated_at}${j.source === 'mock' ? ' (test data)' : ''}.`;
    refreshTableScript(); updateTablePreview();
  } catch (e) { $('tableMeta').textContent = e.message; $('tableMeta').classList.add('err'); }
};
async function updateTablePreview() {
  if (!tableState.loaded) { $('previewEmpty').textContent = 'Load a table to see the graphic.'; showImage(null); return; }
  try {
    const half = tableHalfCount();
    const j = await readJson(await fetch('/preview-table', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ league: tableState.league, topCount: half, bottomCount: half, scene: tableState.scene, showStats: $('showStats').checked, endLogo: $('tableEndLogo').checked }) }), 'Table preview');
    showImage(j.image);
  } catch (e) { $('previewEmpty').textContent = e.message; showImage(null); }
}
$('generateTable').onclick = async () => {
  if (!tableState.loaded) { $('tableMeta').textContent = 'Load a table first.'; $('tableMeta').classList.add('err'); return; }
  if ($('tableVO').checked && !tableState.voiceoverConfirmed) { voStatus('Confirm the script before rendering.', false); $('tableScriptEditor').focus(); return; }
  const half = tableHalfCount();
  const d = { league: tableState.league, topCount: half, bottomCount: half, includeVoiceover: $('tableVO').checked, mentionPoints: $('mentionPts').checked, includeIntroMusic: $('tableMusic').checked, endLogo: $('tableEndLogo').checked, voiceoverText: $('tableScriptEditor').value.trim(), mid90sLoopBackground: $('tableMid90sBg').checked };
  $('tableJob').hidden = false; $('tableDownload').hidden = true; $('tableBar').style.width = '3%'; $('tableJobText').textContent = 'Starting league table render…'; $('tableJobSmall').textContent = '';
  try {
    const j = await readJson(await fetch('/generate-table', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) }), 'Table render');
    pollJob(j.id, { bar: 'tableBar', text: 'tableJobText', dl: 'tableDownload', onError: m => { $('tableJobSmall').textContent = m; } });
  } catch (e) { $('tableJobText').textContent = 'Render didn’t start.'; $('tableJobSmall').textContent = e.message; }
};

/* ---------------- modes ---------------- */
function setMode(mode) {
  state.mode = mode;
  $('resultsArea').hidden = mode !== 'results'; $('tableArea').hidden = mode !== 'table';
  [['resultsTab', 'results'], ['tableTab', 'table']].forEach(([id, m]) => { $(id).classList.toggle('active', m === mode); $(id).setAttribute('aria-selected', m === mode); });
  updatePreviewChrome();
  if (mode === 'results') { queuePreview(); } else { stopLiveTimer(); updateTablePreview(); }
  if (mode === 'results' && state.range.live && state.feedLoaded) loadFeed({ quiet: true });
}
$('resultsTab').onclick = () => setMode('results');
$('tableTab').onclick = () => setMode('table');

/* ---------------- start up ---------------- */
[1, 2].forEach(p => { const s = compEl(p); BASE_COMPETITIONS.forEach(c => s.add(new Option(c, c))); });
compEl(2).value = 'Championship';
[1, 2].forEach(p => { for (let i = 0; i < 3; i++) addRow(p); });
buildDateStrip(); syncPreviewLabels(); updateCounts(); setMode('results');
chooseDay('live');
if (document.fonts && document.fonts.load) document.fonts.load(`italic 700 ${RESULT_FONT_SIZE}px GrandscoresResult`).then(refreshAllWarnings).catch(() => { });
