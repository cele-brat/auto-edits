/* Auto Edits: the app screen. No build step; plain browser JavaScript. */
'use strict';

const $ = (id) => document.getElementById(id);
const TOGGLES = [
  ['exposure', 'Exposure'], ['highlights', 'Highlights'], ['shadows', 'Shadows'], ['whites', 'Whites'],
  ['blacks', 'Blacks'], ['white_balance', 'White balance'], ['noise', 'Noise'], ['straighten', 'Straighten'],
  ['lens', 'Lens fix'],
];
const SHOWN_HERE = new Set(['exposure', 'highlights', 'shadows', 'whites', 'blacks', 'white_balance']);
const ALL_TOGGLES = TOGGLES.map((t) => t[0]);
const MODE_NOTES = {
  preset: 'Every photo gets the preset exactly as it is.',
  preset_auto: 'The ticked settings are worked out for each photo. Everything else comes from the preset.',
  auto: 'No preset. Every setting below is worked out for each photo.',
};

const S = {
  weddings: [], wedding: null, folderId: null, photos: [], presets: [], byPreset: new Map(), looks: [],
  photoId: null, view: 'grid', scope: 'folder', split: true, holdBefore: false, dividerAt: 0.5,
  pack: '', query: '', openLook: null, lastToggles: ['exposure', 'white_balance'],
};

// ---- talking to the server ------------------------------------------------
async function api(path, options = {}) {
  const init = { method: options.method || 'GET', headers: {}, credentials: 'same-origin' };
  if ('body' in options) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(options.body);
  }
  const r = await fetch(path, init);
  if (r.status === 401) { showSignIn(); throw new Error('signed out'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `The server answered ${r.status}.`);
  return data;
}

// ---- looks ----------------------------------------------------------------
const blankLook = () => ({ mode: 'preset', preset: null, amount: 1, toggles: [], ev: 0, temp: 0, tint: 0 });

function tidy(look) {
  const l = { ...blankLook(), ...look };
  if (l.mode === 'auto') { l.preset = null; l.toggles = [...ALL_TOGGLES]; }
  else if (l.mode === 'preset') l.toggles = [];
  else l.toggles = ALL_TOGGLES.filter((t) => l.toggles.includes(t));
  return l;
}
const folder = () => (S.wedding ? S.wedding.folders.find((f) => f.id === S.folderId) : null);
const photo = () => S.photos.find((p) => p.id === S.photoId) || null;
const folderLook = (f = folder()) => tidy(f && f.settings && f.settings.mode ? f.settings : {});
const lookOf = (p) => (p.look ? tidy(p.look) : folderLook(S.wedding.folders.find((f) => f.id === p.folder)));
const editingPhoto = () => S.scope === 'photo' && S.view === 'loupe' && !!photo();
const panelLook = () => (editingPhoto() ? lookOf(photo()) : folderLook());

function describe(look) {
  if (look.mode === 'auto') return 'Auto Correct';
  const p = S.byPreset.get(look.preset);
  if (!p) return look.ev || look.temp || look.tint ? 'Your own adjustment' : 'Not edited yet';
  return p.name + (look.mode === 'preset_auto' && look.toggles.length ? ' + auto' : '');
}

function hash(text) {
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}
const base = (p) => `/api/weddings/${S.wedding.id}/photos/${p.id}`;
const thumbUrl = (p) => `${base(p)}/image?size=360&k=${hash(JSON.stringify(lookOf(p)))}${p.v}`;
const afterUrl = (p, size) => `${base(p)}/image?size=${size}&v=${p.v}&look=${encodeURIComponent(JSON.stringify(lookOf(p)))}`;
const beforeUrl = (p) => `${base(p)}/image?size=1600&base=1&v=${p.v}`;

// ---- saving ---------------------------------------------------------------
const pending = new Map();
function queueSave(key, run) {
  clearTimeout((pending.get(key) || {}).timer);
  $('saved').textContent = 'Saving…';
  const timer = setTimeout(async () => {
    pending.delete(key);
    try {
      await run();
      if (!pending.size) $('saved').textContent = 'Saved';
      refreshThumbs();
      drawFolders();
    } catch (e) {
      if (e.message !== 'signed out') $('saved').textContent = `Not saved. ${e.message}`;
    }
  }, 450);
  pending.set(key, { timer });
}

function change(patch) {
  const look = tidy({ ...panelLook(), ...patch });
  if (look.mode === 'preset_auto') S.lastToggles = look.toggles;
  if (editingPhoto()) {
    const p = photo();
    p.look = look;
    queueSave(`p${p.id}`, () => api(`${base(p)}/look`, { method: 'PUT', body: look }));
  } else {
    const f = folder();
    f.settings = look;
    queueSave(`f${f.id}`, () => api(`/api/weddings/${S.wedding.id}/folders/${f.id}/look`, { method: 'PUT', body: look }));
  }
  drawPanel();
  drawStage();
}

// ---- drawing: folders, grid, strip ------------------------------------------
function el(tag, props = {}, children = []) {
  const node = Object.assign(document.createElement(tag), props);
  for (const c of [].concat(children)) node.append(c);
  return node;
}

function drawFolders() {
  const nav = $('folders');
  nav.replaceChildren(...S.wedding.folders.map((f) => {
    const b = el('button', { type: 'button' }, [
      el('b', { textContent: f.name }),
      el('small', { textContent: `${f.photos} photo${f.photos === 1 ? '' : 's'}` }),
      el('small', { textContent: describe(folderLook(f)) }),
    ]);
    b.setAttribute('aria-current', String(f.id === S.folderId));
    b.onclick = () => openFolder(f.id);
    return b;
  }));
}

const inFolder = () => S.photos.filter((p) => p.folder === S.folderId);

function thumb(p, withName) {
  const b = el('button', { type: 'button', title: p.name });
  b.dataset.id = p.id;
  b.append(el('img', { loading: 'lazy', alt: p.name, src: thumbUrl(p) }));
  if (p.look) b.append(el('span', { className: 'own', title: 'Has its own settings' }));
  if (withName) b.append(el('span', { className: 'name', textContent: p.name }));
  b.setAttribute('aria-current', String(p.id === S.photoId));
  return b;
}

function drawGrid() {
  const grid = $('grid');
  grid.replaceChildren(...inFolder().map((p) => {
    const b = thumb(p, true);
    b.onclick = () => { S.photoId = p.id; markCurrent(); };
    b.ondblclick = () => openPhoto(p.id);
    return b;
  }));
  const strip = $('strip');
  strip.replaceChildren(...inFolder().map((p) => {
    const b = thumb(p, false);
    b.onclick = () => openPhoto(p.id);
    return b;
  }));
}

function refreshThumbs() {
  for (const b of document.querySelectorAll('#grid button, #strip button')) {
    const p = S.photos.find((x) => x.id === b.dataset.id);
    if (!p) continue;
    const img = b.querySelector('img');
    const url = thumbUrl(p);
    if (!img.src.endsWith(url)) img.src = url;
    const dot = b.querySelector('.own');
    if (p.look && !dot) b.append(el('span', { className: 'own', title: 'Has its own settings' }));
    if (!p.look && dot) dot.remove();
  }
}

function markCurrent() {
  for (const b of document.querySelectorAll('#grid button, #strip button')) {
    const on = b.dataset.id === S.photoId;
    b.setAttribute('aria-current', String(on));
    if (on && b.parentElement.id === 'strip') b.scrollIntoView({ block: 'nearest', inline: 'center' });
  }
}

// ---- drawing: the big photo ---------------------------------------------------
let renderTurn = 0;
const loadImage = (url) => new Promise((resolve, reject) => {
  const i = new Image();
  i.onload = () => resolve(url);
  i.onerror = () => reject(new Error('The photo could not be rendered.'));
  i.src = url;
});

async function drawStage() {
  const empty = !S.wedding || !inFolder().length;
  $('empty').hidden = !empty;
  $('grid').hidden = empty || S.view !== 'grid';
  $('loupe').hidden = empty || S.view !== 'loupe';
  $('view-grid').setAttribute('aria-pressed', String(S.view === 'grid'));
  $('view-loupe').setAttribute('aria-pressed', String(S.view === 'loupe'));
  $('split').hidden = S.view !== 'loupe';
  const p = photo();
  if (empty || S.view !== 'loupe' || !p) return;

  const before = $('before');
  const wanted = beforeUrl(p);
  if (!before.src.endsWith(wanted)) before.src = wanted;

  const turn = ++renderTurn;
  $('busy').hidden = false;
  try {
    for (const size of [800, 1600]) {
      const url = await loadImage(afterUrl(p, size));
      if (turn !== renderTurn) return;
      $('after').src = url;
      placeDivider();
    }
  } catch (e) {
    if (turn === renderTurn) $('saved').textContent = e.message;
  }
  if (turn === renderTurn) $('busy').hidden = true;
}

function drawCaption() {
  const p = photo();
  if (!p) return;
  const bits = [el('b', { textContent: p.name })];
  if (p.camera) bits.push(el('span', { textContent: p.camera }));
  if (p.iso) bits.push(el('span', { textContent: `ISO ${p.iso}` }));
  if (p.edited) bits.push(el('span', { textContent: 'Came from Lightroom with edits already on it' }));
  $('caption').replaceChildren(...bits);
}

function placeDivider() {
  const frame = $('frame');
  const img = $('after');
  const W = frame.clientWidth, H = frame.clientHeight;
  const a = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : W / H;
  const w = Math.min(W, H * a), h = w / a, left = (W - w) / 2, top = (H - h) / 2;
  const x = left + S.dividerAt * w;
  const before = $('before');
  const whole = S.holdBefore;
  before.style.visibility = S.split || whole ? 'visible' : 'hidden';
  before.style.clipPath = whole ? 'none' : `inset(0 ${W - x}px 0 0)`;
  const d = $('divider');
  d.hidden = !S.split || whole;
  d.style.left = `${x}px`;
  d.style.top = `${top}px`;
  d.style.bottom = `${H - top - h}px`;
  d.setAttribute('aria-valuenow', String(Math.round(S.dividerAt * 100)));
  const tb = $('tag-before'), ta = $('tag-after');
  tb.hidden = !(S.split || whole);
  ta.hidden = !S.split || whole;
  tb.style.left = `${left + 8}px`;
  tb.style.top = ta.style.top = `${top + 8}px`;
  ta.style.right = `${W - left - w + 8}px`;
  $('split').setAttribute('aria-pressed', String(S.split));
}

// ---- drawing: the look panel --------------------------------------------------
function buildLooks() {
  const map = new Map();
  for (const p of S.presets) {
    const key = `${p.pack}|${p.look || p.name}`;
    if (!map.has(key)) map.set(key, { key, pack: p.pack, look: p.look || p.name, variants: [] });
    map.get(key).variants.push(p);
  }
  S.looks = [...map.values()];
  const packs = [...new Set(S.presets.map((p) => p.pack))];
  $('pack').replaceChildren(el('option', { value: '', textContent: 'All packs' }),
    ...packs.map((k) => el('option', { value: k, textContent: k })));
}

function shownLooks() {
  const q = S.query.trim().toLowerCase();
  return S.looks.filter((l) => (!S.pack || l.pack === S.pack) &&
    (!q || l.look.toLowerCase().includes(q) || l.variants.some((v) => v.variant.toLowerCase().includes(q))));
}

function drawLooks() {
  const look = panelLook();
  const chosen = S.byPreset.get(look.preset);
  const open = S.openLook || (chosen ? `${chosen.pack}|${chosen.look || chosen.name}` : null);
  const box = $('looks');
  const none = el('button', { type: 'button', className: 'look none', textContent: 'No preset' });
  none.onclick = () => { S.openLook = null; change({ preset: null }); };
  const rows = [none];
  for (const l of shownLooks()) {
    const head = el('button', { type: 'button', className: 'look' }, [l.look, el('small', { textContent: l.pack.replace(' Looks', '') })]);
    head.setAttribute('aria-expanded', String(l.key === open));
    head.onclick = () => { S.openLook = l.key; change({ preset: l.variants[0].id }); };
    rows.push(head);
    if (l.key === open) {
      rows.push(el('div', { className: 'variants' }, l.variants.map((v) => {
        const b = el('button', { type: 'button', textContent: v.variant });
        b.dataset.preset = v.id;
        b.setAttribute('aria-pressed', String(v.id === look.preset));
        b.onclick = () => change({ preset: v.id });
        return b;
      })));
    }
  }
  const keep = box.scrollTop;
  box.replaceChildren(...rows);
  box.scrollTop = keep;
  $('preset-name').textContent = chosen ? chosen.name : '';
  $('about').textContent = chosen ? chosen.about : (shownLooks().length ? '' : 'No look matches that search.');
}

function stepPreset(by) {
  const flat = shownLooks().flatMap((l) => l.variants);
  if (!flat.length) return;
  const at = flat.findIndex((v) => v.id === panelLook().preset);
  const next = flat[Math.min(flat.length - 1, Math.max(0, at + by))] || flat[0];
  S.openLook = `${next.pack}|${next.look || next.name}`;
  change({ preset: next.id });
  const b = $('looks').querySelector(`[data-preset="${CSS.escape(next.id)}"]`);
  if (b) b.scrollIntoView({ block: 'nearest' });
}

function setSlider(id, value, text) {
  const input = $(id);
  if (document.activeElement !== input) input.value = value;
  input.nextElementSibling.textContent = text;
}
const signed = (v, digits = 0) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(digits);

let autoTurn = 0;
async function drawAutoValues(look) {
  const out = $('auto-values');
  const p = photo();
  const turn = ++autoTurn;
  if (!p || S.view !== 'loupe' || !look.toggles.length) {
    out.textContent = look.toggles.some((t) => !SHOWN_HERE.has(t)) ? '* Applied in Lightroom, not shown in this preview.' : '';
    return;
  }
  try {
    const d = await api(`${base(p)}/develop?look=${encodeURIComponent(JSON.stringify(lookOf(p)))}`);
    if (turn !== autoTurn) return;
    const names = { Exposure2012: 'exposure', Highlights2012: 'highlights', Shadows2012: 'shadows', Whites2012: 'whites', Blacks2012: 'blacks' };
    const bits = Object.entries(names).filter(([k]) => k in d.auto)
      .map(([k, n]) => `${n} ${signed(d.auto[k], k === 'Exposure2012' ? 2 : 0)}`);
    if ('temp' in d.auto_wb) bits.push(`warmth ${signed(d.auto_wb.temp)}`, `tint ${signed(d.auto_wb.tint)}`);
    out.textContent = (bits.length ? `For this photo: ${bits.join(', ')}. ` : '') +
      (look.toggles.some((t) => !SHOWN_HERE.has(t)) ? '* Applied in Lightroom, not shown in this preview.' : '');
  } catch (e) { /* the photo itself will report the problem */ }
}

function drawPanel() {
  const f = folder();
  const p = photo();
  const loupe = S.view === 'loupe' && !!p;
  $('panel').style.visibility = f ? 'visible' : 'hidden';
  if (!f) return;
  $('scope-photo').disabled = !loupe;
  $('scope-folder').setAttribute('aria-pressed', String(!editingPhoto()));
  $('scope-photo').setAttribute('aria-pressed', String(editingPhoto()));
  const note = $('scope-note');
  note.replaceChildren();
  if (editingPhoto() && p.look) {
    const back = el('button', { type: 'button', className: 'quiet', textContent: 'Use the folder’s settings again' });
    back.onclick = async () => {
      p.look = null;
      S.scope = 'folder';
      drawPanel(); drawStage();
      queueSave(`p${p.id}`, () => api(`${base(p)}/look`, { method: 'PUT', body: null }));
    };
    note.append('This photo has its own settings. ', back);
  } else if (editingPhoto()) {
    note.textContent = 'Change anything below and only this photo gets it.';
  } else if (loupe && p.look) {
    note.textContent = `Settings for every photo in “${f.name}”. The photo on screen has its own settings, so it will not change.`;
  } else {
    note.textContent = `Settings for every photo in “${f.name}”.`;
  }

  const look = panelLook();
  for (const b of document.querySelectorAll('[data-mode]')) b.setAttribute('aria-pressed', String(b.dataset.mode === look.mode));
  $('mode-note').textContent = MODE_NOTES[look.mode];
  $('preset-box').toggleAttribute('data-off', look.mode === 'auto');
  $('auto-box').toggleAttribute('data-off', look.mode === 'preset');
  drawLooks();

  $('toggles').replaceChildren(...TOGGLES.map(([key, label]) => {
    const box = el('input', { type: 'checkbox', checked: look.toggles.includes(key), disabled: look.mode !== 'preset_auto' });
    box.onchange = () => {
      const now = new Set(panelLook().toggles);
      if (box.checked) now.add(key); else now.delete(key);
      change({ toggles: [...now] });
    };
    return el('label', { className: SHOWN_HERE.has(key) ? '' : 'later' }, [box, label]);
  }));
  setSlider('amount', look.amount, `${Math.round(look.amount * 100)}%`);
  setSlider('ev', look.ev, signed(look.ev, 2));
  setSlider('temp', look.temp, signed(look.temp));
  setSlider('tint', look.tint, signed(look.tint));
  drawAutoValues(look);
}

// ---- moving around --------------------------------------------------------------
function openFolder(id) {
  S.folderId = id;
  S.photoId = (inFolder()[0] || {}).id || null;
  S.scope = 'folder';
  S.openLook = null;
  drawFolders(); drawGrid(); drawPanel(); drawStage();
}

function openPhoto(id) {
  S.photoId = id;
  S.view = 'loupe';
  S.scope = photo() && photo().look ? 'photo' : 'folder';
  S.openLook = null;
  $('after').removeAttribute('src');
  markCurrent(); drawCaption(); drawPanel(); drawStage();
}

function step(by) {
  const list = inFolder();
  const at = list.findIndex((p) => p.id === S.photoId);
  const next = list[at + by];
  if (!next) return;
  if (S.view === 'loupe') openPhoto(next.id);
  else { S.photoId = next.id; markCurrent(); }
}

function setView(view) {
  if (view === 'loupe' && !photo()) return;
  if (view === 'loupe') return openPhoto(S.photoId);
  S.view = 'grid';
  S.scope = 'folder';
  drawPanel(); drawStage();
}

async function openWedding(id) {
  S.wedding = await api(`/api/weddings/${id}`);
  S.photos = (await api(`/api/weddings/${id}/photos?compact=1`)).photos;
  $('wedding').value = id;
  $('count').textContent = `${S.wedding.photos} photo${S.wedding.photos === 1 ? '' : 's'}`;
  S.view = 'grid';
  drawFolders();
  openFolder((S.wedding.folders[0] || {}).id || null);
}

function showSignIn() {
  $('app').hidden = true;
  $('signin').hidden = false;
  $('key').focus();
}

async function start() {
  try { await api('/api/session'); } catch (e) { return; }
  $('signin').hidden = true;
  $('app').hidden = false;
  const [presets, weddings] = await Promise.all([api('/api/presets'), api('/api/weddings')]);
  S.presets = presets.presets;
  S.byPreset = new Map(S.presets.map((p) => [p.id, p]));
  buildLooks();
  S.weddings = weddings.weddings;
  $('wedding').replaceChildren(...S.weddings.map((w) => el('option', { value: w.id, textContent: w.name })));
  if (!S.weddings.length) {
    S.wedding = null;
    $('folders').replaceChildren();
    $('count').textContent = '';
    $('panel').style.visibility = 'hidden';
    $('empty').replaceChildren(el('strong', { textContent: 'No photos here yet' }),
      'In Lightroom, select the photos, then choose Library > Plug-in Extras > “Auto Edits: send photos to the server”.');
    $('empty').hidden = false;
    $('grid').hidden = $('loupe').hidden = true;
    return;
  }
  $('empty').replaceChildren(el('strong', { textContent: 'This folder is empty' }), 'Send photos to it from Lightroom.');
  await openWedding(S.weddings[0].id);
}

// ---- wiring ---------------------------------------------------------------------
$('signin-form').onsubmit = async (e) => {
  e.preventDefault();
  $('signin-error').textContent = '';
  const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: $('key').value }), credentials: 'same-origin' });
  if (!r.ok) { $('signin-error').textContent = 'That key is not right. Copy it again and paste it here.'; return; }
  $('key').value = '';
  start();
};
$('signout').onclick = async () => { await api('/api/logout', { method: 'POST' }); showSignIn(); };
$('wedding').onchange = (e) => openWedding(e.target.value);
$('view-grid').onclick = () => setView('grid');
$('view-loupe').onclick = () => setView('loupe');
$('split').onclick = () => { S.split = !S.split; placeDivider(); };
$('scope-folder').onclick = () => { S.scope = 'folder'; drawPanel(); };
$('scope-photo').onclick = () => { S.scope = 'photo'; drawPanel(); };
for (const b of document.querySelectorAll('[data-mode]')) {
  b.onclick = () => change({ mode: b.dataset.mode, toggles: b.dataset.mode === 'preset_auto' ? S.lastToggles : [] });
}
$('pack').onchange = (e) => { S.pack = e.target.value; drawLooks(); };
$('search').oninput = (e) => { S.query = e.target.value; drawLooks(); };
$('looks').onkeydown = (e) => {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); stepPreset(e.key === 'ArrowDown' ? 1 : -1); $('looks').focus(); }
};
for (const [id, key] of [['amount', 'amount'], ['ev', 'ev'], ['temp', 'temp'], ['tint', 'tint']]) {
  $(id).oninput = (e) => change({ [key]: Number(e.target.value) });
  $(id).ondblclick = () => change({ [key]: key === 'amount' ? 1 : 0 });
}
$('reset').onclick = () => change(blankLook());

$('delete-wedding').onclick = () => {
  if (!S.wedding) return;
  $('more').open = false;
  $('confirm-text').textContent = `Delete “${S.wedding.name}” and its ${S.wedding.photos} photos from the server? ` +
    'Your Lightroom catalogue and the original files on your computer are not touched.';
  $('confirm').showModal();
};
$('confirm-no').onclick = () => $('confirm').close();
$('confirm-yes').onclick = async () => {
  $('confirm').close();
  await api(`/api/weddings/${S.wedding.id}`, { method: 'DELETE' });
  start();
};

const divider = $('divider');
function dragTo(clientX) {
  const frame = $('frame').getBoundingClientRect();
  const img = $('after');
  const a = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : frame.width / frame.height;
  const w = Math.min(frame.width, frame.height * a), left = frame.left + (frame.width - w) / 2;
  S.dividerAt = Math.min(1, Math.max(0, (clientX - left) / w));
  placeDivider();
}
divider.onpointerdown = (e) => { divider.setPointerCapture(e.pointerId); divider.onpointermove = (m) => dragTo(m.clientX); };
divider.onpointerup = divider.onpointercancel = () => { divider.onpointermove = null; };
divider.onkeydown = (e) => {
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  e.preventDefault(); e.stopPropagation();
  S.dividerAt = Math.min(1, Math.max(0, S.dividerAt + (e.key === 'ArrowLeft' ? -0.05 : 0.05)));
  placeDivider();
};
$('after').onload = placeDivider;
window.addEventListener('resize', placeDivider);

document.addEventListener('keydown', (e) => {
  const t = e.target;
  const typing = t.matches('input[type=search], input[type=password], select') || t.closest('#looks');
  if (e.key === '\\' && !typing) { if (!S.holdBefore) { S.holdBefore = true; placeDivider(); } return; }
  if (typing || t.matches('input[type=range]') || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
  else if (e.key === 'g' || e.key === 'G' || e.key === 'Escape') setView('grid');
  else if (e.key === 'Enter' && S.view === 'grid' && !t.matches('button, summary')) setView('loupe');
});
document.addEventListener('keyup', (e) => { if (e.key === '\\' && S.holdBefore) { S.holdBefore = false; placeDivider(); } });

start().catch((e) => { if (e.message !== 'signed out') $('saved').textContent = e.message; });
