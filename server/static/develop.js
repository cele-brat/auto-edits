// More of Lightroom's Develop module (server/editing.py): zoom to 1:1 and 2:1, Copy / Paste / Sync
// settings, History and Snapshots with undo and redo, star ratings and colour labels, multi-select
// in Library. Loaded after app.js and cull.js, and hooks into them without changing them.
'use strict';

const Dv = { zoom: 'fit', centre: { x: 0.5, y: 0.5 }, full: null, tile: null, tileTurn: 0, copied: null, selected: new Set(),
  history: null, snapshots: [], stars: 0, label: '', histTurn: 0, busy: false };
const LABEL_COLOURS = { red: '#e5484d', yellow: '#f2c200', green: '#4fc46a', blue: '#4a90e2', purple: '#a66be8' };
const LABEL_KEYS = { 6: 'red', 7: 'yellow', 8: 'green', 9: 'blue' };

// ---- the selection in Library (Shift / Cmd click, as in Lightroom) ---------------------------------
const selectedPhotos = () => (S.view === 'grid' ? inFolder().filter((p) => Dv.selected.has(p.id) || p.id === S.photoId) : [photo()].filter(Boolean));
function paintSelection() {
  for (const b of document.querySelectorAll('#grid button, #strip button')) b.classList.toggle('picked', Dv.selected.has(b.dataset.id) && b.dataset.id !== S.photoId);
  const n = selectedPhotos().length;
  $('sel-count').textContent = S.view === 'grid' && n > 1 ? `${n} selected` : '';
}
function clickGrid(e, p) {
  const list = inFolder();
  if (e.shiftKey && S.photoId) {
    const a = list.findIndex((x) => x.id === S.photoId), b = list.findIndex((x) => x.id === p.id);
    for (const x of list.slice(Math.min(a, b), Math.max(a, b) + 1)) Dv.selected.add(x.id);
    Dv.selected.add(S.photoId);
  } else if (e.metaKey || e.ctrlKey) {
    if (S.photoId) Dv.selected.add(S.photoId);
    if (Dv.selected.has(p.id)) {                     // take it out; the current photo moves to another selected one
      Dv.selected.delete(p.id);
      if (p.id === S.photoId) S.photoId = [...Dv.selected][0] || p.id;
    } else { Dv.selected.add(p.id); S.photoId = p.id; }
    if (Dv.selected.size === 1) Dv.selected.clear();
  } else { Dv.selected.clear(); S.photoId = p.id; }
  markCurrent();
  paintSelection();
}
const _drawGridBefore = drawGrid;
drawGrid = function () {                                                     // eslint-disable-line no-global-assign
  _drawGridBefore();
  for (const b of $('grid').querySelectorAll('button')) {
    const p = S.photos.find((x) => x.id === b.dataset.id);
    if (!p) continue;
    b.onclick = (e) => clickGrid(e, p);
    decorate(b, p);
  }
  for (const b of $('strip').querySelectorAll('button')) { const p = S.photos.find((x) => x.id === b.dataset.id); if (p) decorate(b, p); }
  paintSelection();
};
const _refreshThumbsBefore = refreshThumbs;
refreshThumbs = function () {                                                // eslint-disable-line no-global-assign
  _refreshThumbsBefore();
  for (const b of document.querySelectorAll('#grid button, #strip button')) { const p = S.photos.find((x) => x.id === b.dataset.id); if (p) decorate(b, p); }
};
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && (e.key === 'a' || e.key === 'A') && S.view === 'grid' && !e.target.matches('input, textarea, select')) {
    e.preventDefault(); for (const p of inFolder()) Dv.selected.add(p.id); paintSelection();
  }
}, true);

// ---- stars and colour labels on the thumbnails ---------------------------------------------------
function decorate(b, p) {
  let s = b.querySelector('.stars');
  if (!s) { s = el('span', { className: 'stars' }); b.append(s); }
  s.textContent = p.stars ? '★'.repeat(p.stars) : '';
  s.title = p.stars ? `${p.stars} star${p.stars === 1 ? '' : 's'}` : '';
  b.style.setProperty('--label', p.label ? LABEL_COLOURS[p.label] : 'transparent');
  b.classList.toggle('labelled', !!p.label);
}
async function setMarks(patch, list = selectedPhotos()) {
  if (!list.length) return;
  for (const p of list) Object.assign(p, patch);
  refreshThumbs(); drawMarks();
  try {
    for (const p of list) await api(`${base(p)}/marks`, { method: 'PUT', body: patch });
    $('saved').textContent = patch.stars !== undefined ? (patch.stars ? `${patch.stars} star${patch.stars === 1 ? '' : 's'}` : 'No stars') : (patch.label ? `Label: ${patch.label}` : 'No label');
  } catch (e) { if (e.message !== 'signed out') $('saved').textContent = e.message; }
  if (window.drawFilters) drawFilters();
}
function drawMarks() {
  const p = photo();
  for (const b of $('star-bar').querySelectorAll('[data-star]')) { const n = +b.dataset.star; b.classList.toggle('on', !!p && n <= (p.stars || 0)); b.disabled = !p; }
  for (const b of $('star-bar').querySelectorAll('[data-label]')) { b.setAttribute('aria-pressed', String(!!p && p.label === b.dataset.label)); b.disabled = !p; }
}
$('star-bar').onclick = (e) => {
  const t = e.target.closest('[data-star], [data-label]');
  if (!t || !photo()) return;
  if (t.dataset.star !== undefined) { const n = +t.dataset.star; setMarks({ stars: photo().stars === n ? 0 : n }); }
  else setMarks({ label: photo().label === t.dataset.label ? '' : t.dataset.label });
};

// Keys: 0-5 stars, 6-9 colour labels (as in Lightroom). Keep / Maybe / Reject move to P, U and X;
// the Keep / Maybe / Reject buttons under the photo still work as they did.
const _cullKey = Cull.key;
Cull.key = (key) => {
  if (document.querySelector('dialog[open]')) return false;
  const a = document.activeElement;
  if (a && a.matches('input, textarea, select')) return false;
  if (/^[0-5]$/.test(key)) { if (!photo()) return false; setMarks({ stars: +key }); return true; }
  if (LABEL_KEYS[key]) { if (!photo()) return false; const l = LABEL_KEYS[key]; setMarks({ label: photo().label === l ? '' : l }); return true; }
  const pick = { p: '3', P: '3', u: '2', U: '2', x: '1', X: '1' }[key];
  return pick ? _cullKey(pick) : false;
};
for (const b of document.querySelectorAll('#toolbar .picks button')) {
  b.title = b.title.replace(/\(\d\)/, { keep: '(P)', maybe: '(U)', reject: '(X)' }[b.dataset.pick]);
  b.onclick = () => { const p = photo(); if (!p) return; _cullKey(p.pick === b.dataset.pick ? '0' : { keep: '3', maybe: '2', reject: '1' }[b.dataset.pick]); };
}
document.querySelector('#cull .keys').innerHTML = 'Change a rating yourself: select a photo and press <b>P</b> Keep, <b>U</b> Maybe, <b>X</b> Reject. Stars: <b>0</b> to <b>5</b>; colour labels: <b>6</b> red, <b>7</b> yellow, <b>8</b> green, <b>9</b> blue.';

// ---- filters: stars (at least N) and colour label, beside Keep / Maybe / Reject ------------------
const _inFolder = inFolder;
inFolder = () => _inFolder().filter((p) => (p.stars || 0) >= Dv.stars && (!Dv.label || p.label === Dv.label));   // eslint-disable-line no-global-assign
function drawFilters() {
  const all = wholeFolder();
  $('filters').hidden = !all.some((p) => p.stars || p.label) && !Dv.stars && !Dv.label;
  for (const b of $('filters').querySelectorAll('[data-stars]')) b.setAttribute('aria-pressed', String(+b.dataset.stars === Dv.stars));
  for (const b of $('filters').querySelectorAll('[data-flabel]')) b.setAttribute('aria-pressed', String(b.dataset.flabel === Dv.label));
}
window.drawFilters = drawFilters;
$('filters').onclick = (e) => {
  const t = e.target.closest('[data-stars], [data-flabel]');
  if (!t) return;
  if (t.dataset.stars !== undefined) Dv.stars = +t.dataset.stars; else Dv.label = Dv.label === t.dataset.flabel ? '' : t.dataset.flabel;
  drawFilters();
  if (!inFolder().some((p) => p.id === S.photoId)) S.photoId = (inFolder()[0] || {}).id || null;
  if (S.view === 'loupe' && !photo()) S.view = 'grid';
  drawGrid(); drawCaption(); drawPanel(); drawStage();
};
const _openFolderBefore = openFolder;
openFolder = function (id) { Dv.selected.clear(); Dv.stars = 0; Dv.label = ''; _openFolderBefore(id); drawFilters(); };   // eslint-disable-line no-global-assign
$('export-stars').onchange = () => { Ex.ratingsTouched = true; exportCost(); };
const _exportCount = exportCount;
exportCount = () => {                                                        // eslint-disable-line no-global-assign
  const scope = $('export-scope').value, ratings = $('export-ratings').value ? $('export-ratings').value.split(',') : [], stars = +$('export-stars').value;
  return S.photos.filter((p) => (scope === '' || p.folder === +scope) && (!ratings.length || ratings.includes(p.pick || (p.cull || {}).r)) && (p.stars || 0) >= stars).length;
};
const _startExport = startExport;
startExport = async function () {                                            // eslint-disable-line no-global-assign
  const stars = +$('export-stars').value;
  if (!stars) return _startExport();
  $('export-error').textContent = ''; $('export-start').disabled = true;
  try {
    const scope = $('export-scope').value;
    await api(`/api/weddings/${S.wedding.id}/exports`, { method: 'POST', body: {
      size: +$('export-size').value, quality: +$('export-quality').value, watermark: $('export-watermark').value.trim(),
      folder: scope === '' ? null : +scope, ratings: $('export-ratings').value ? $('export-ratings').value.split(',') : [], stars } });
  } catch (err) { $('export-error').textContent = err.message; }
  $('export-start').disabled = false;
  if (window.refreshCredits) refreshCredits().then(exportCost);
  refreshExports();
};

// ---- history: a row after every save, and the panel ---------------------------------------------
// queueSave runs a save; afterwards the server is asked to note what changed (it works out the label).
const _queueSave = queueSave;
queueSave = function (key, run) {                                            // eslint-disable-line no-global-assign
  _queueSave(key, async () => { await run(); await markHistory(key); });
};
async function markHistory(key, label) {
  try {
    const who = key[0] === 'p' ? `photos/${key.slice(1)}` : `folders/${key.slice(1)}`;
    const r = await api(`/api/weddings/${S.wedding.id}/${who}/history/mark`, { method: 'POST', body: label ? { label } : {} });
    if (historyKey() === key) { Dv.history = r; drawHistory(); }
  } catch (e) { /* the save itself went through; the step is noted next time */ }
}
window.markHistory = markHistory;
const historyKey = () => (editingPhoto() ? `p${S.photoId}` : folder() ? `f${folder().id}` : null);
const historyPath = () => (editingPhoto() ? `${base(photo())}/history` : `/api/weddings/${S.wedding.id}/folders/${folder().id}/history`);
async function loadHistory() {
  const key = historyKey(), turn = ++Dv.histTurn;
  if (!key) { Dv.history = null; Dv.snapshots = []; drawHistory(); return; }
  try {
    const [h, s] = await Promise.all([api(historyPath()), editingPhoto() ? api(`${base(photo())}/snapshots`) : { snapshots: [] }]);
    if (turn !== Dv.histTurn) return;
    Dv.history = h; Dv.snapshots = s.snapshots;
  } catch (e) { if (turn === Dv.histTurn) { Dv.history = null; Dv.snapshots = []; } }
  drawHistory();
}
const whenAt = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
function drawHistory() {
  const h = Dv.history, box = $('history-list');
  $('history-note').textContent = editingPhoto() ? '' : `The folder’s own steps. Open “This photo” for a photo’s steps.`;
  if (!h || !h.steps.length) { box.replaceChildren(el('p', { className: 'empty', textContent: 'No steps yet.' })); $('history-panel').querySelector('summary small').textContent = ''; }
  else {
    const at = h.steps.findIndex((s) => s.id === h.at);
    box.replaceChildren(...[...h.steps].reverse().map((s, i) => {
      const idx = h.steps.length - 1 - i;
      const b = el('button', { type: 'button', className: 'step' }, [el('span', { textContent: s.label }), el('small', { textContent: whenAt(s.ts) })]);
      b.setAttribute('aria-current', String(s.id === h.at));
      b.classList.toggle('later', idx > at);
      b.onclick = () => restoreStep(s.id);
      return b;
    }));
    $('history-panel').querySelector('summary small').textContent = String(h.steps.length);
  }
  $('undo').disabled = !h || !h.steps.length || h.steps.findIndex((s) => s.id === h.at) <= 0;
  $('redo').disabled = !h || !h.steps.length || h.steps.findIndex((s) => s.id === h.at) >= h.steps.length - 1;
  const sb = $('snapshot-list');
  $('snapshots-panel').hidden = !editingPhoto();
  sb.replaceChildren(...Dv.snapshots.map((s) => {
    const b = el('button', { type: 'button', className: 'step' }, [el('span', { textContent: s.name }), el('small', { textContent: whenAt(s.ts) })]);
    b.onclick = () => restoreSnapshot(s.id);
    const x = el('button', { type: 'button', className: 'quiet x-small', textContent: '×', title: 'Delete this snapshot' });
    x.onclick = (e) => { e.stopPropagation(); deleteSnapshot(s.id); };
    return el('div', { className: 'snap' }, [b, x]);
  }));
  if (!Dv.snapshots.length) sb.replaceChildren(el('p', { className: 'empty', textContent: 'Save the state of this photo to come back to it later.' }));
}
function applyRestored(r) {
  if (editingPhoto()) { const p = photo(); p.look = r.look ? tidy(r.look) : null; p.crop = r.crop || null; }
  else { folder().settings = r.look || {}; }
  Dv.history = { steps: r.steps, at: r.at };
  $('saved').textContent = `Back to: ${r.label || r.name}`;
  drawHistory(); drawPanel(); drawCaption(); drawStage(); refreshThumbs(); drawFolders();
}
async function restoreStep(id) {
  try { applyRestored(await api(`${historyPath()}/${id}/restore`, { method: 'POST' })); } catch (e) { if (e.message !== 'signed out') $('saved').textContent = e.message; }
}
async function stepHistory(which) {
  if (!historyKey() || pending.size) return;
  try { applyRestored(await api(`${historyPath()}/${which}`, { method: 'POST' })); }
  catch (e) { if (e.status === 404) $('saved').textContent = which === 'undo' ? 'Nothing to undo' : 'Nothing to redo'; else if (e.message !== 'signed out') $('saved').textContent = e.message; }
}
$('undo').onclick = () => stepHistory('undo');
$('redo').onclick = () => stepHistory('redo');
$('snapshot-add').onclick = async () => {
  const p = photo();
  if (!editingPhoto()) return;
  const name = $('snapshot-name').value.trim();
  try {
    const r = await api(`${base(p)}/snapshots`, { method: 'POST', body: { name } });
    Dv.snapshots = r.snapshots; $('snapshot-name').value = ''; drawHistory();
    $('saved').textContent = `Snapshot “${r.snapshot.name}” saved`;
  } catch (e) { if (e.message !== 'signed out') $('saved').textContent = e.message; }
};
$('snapshot-name').onkeydown = (e) => { e.stopPropagation(); if (e.key === 'Enter') { e.preventDefault(); $('snapshot-add').click(); } };
async function restoreSnapshot(id) {
  try { const r = await api(`${base(photo())}/snapshots/${id}/restore`, { method: 'POST' }); applyRestored(r); }
  catch (e) { if (e.message !== 'signed out') $('saved').textContent = e.message; }
}
async function deleteSnapshot(id) {
  try { Dv.snapshots = (await api(`${base(photo())}/snapshots/${id}`, { method: 'DELETE' })).snapshots; drawHistory(); }
  catch (e) { if (e.message !== 'signed out') $('saved').textContent = e.message; }
}
// Reset to original and the folder reset save outside queueSave: note them too.
$('original-yes').addEventListener('click', () => {
  const key = historyKey();
  if (!key) return;
  setTimeout(() => markHistory(key, 'Reset to original'), 800);
});
// The panel follows the photo / folder on screen.
let lastHistoryKey = null;
const _drawPanelBeforeDv = drawPanel;
drawPanel = function () {                                                    // eslint-disable-line no-global-assign
  _drawPanelBeforeDv();
  const key = historyKey();
  if (key !== lastHistoryKey) { lastHistoryKey = key; loadHistory(); }
  drawMarks();
};

// ---- copy, paste, sync ----------------------------------------------------------------------------
let copyPanels = [];
async function loadCopyPanels() {
  try { copyPanels = (await api('/api/copy-panels')).panels; } catch (e) { copyPanels = ['Preset', 'Basic', 'Tone Curve', 'Color Mixer', 'Color Grading', 'Detail', 'Lens Corrections', 'Transform', 'Effects', 'Calibration', 'Masks', 'Crop', 'Rotate']; }
  $('copy-list').replaceChildren(...copyPanels.map((name) => {
    const box = el('input', { type: 'checkbox', value: name, checked: !['Crop', 'Rotate'].includes(name) });
    return el('label', { className: 'check' }, [box, ' ', name]);
  }));
}
loadCopyPanels();
const tickedPanels = () => [...$('copy-list').querySelectorAll('input:checked')].map((i) => i.value);
function openCopy(sync) {
  const p = photo();
  if (!p) return;
  Dv.copyMode = sync ? 'sync' : 'copy';
  $('copy-title').textContent = sync ? 'Sync settings' : 'Copy settings';
  const n = selectedPhotos().filter((x) => x.id !== p.id).length;
  $('copy-text').textContent = sync
    ? `From “${p.name}” onto the ${n} other selected photo${n === 1 ? '' : 's'}: tick what to carry over.`
    : `From “${p.name}”: tick what to copy. Then select photos and paste with Cmd/Ctrl+Shift+V.`;
  $('copy-go').textContent = sync ? `Sync ${n} photo${n === 1 ? '' : 's'}` : 'Copy';
  $('copy-go').disabled = sync && !n;
  $('copy-error').textContent = '';
  $('copy-dialog').showModal();
}
$('copy-all').onclick = () => { for (const i of $('copy-list').querySelectorAll('input')) i.checked = true; };
$('copy-none').onclick = () => { for (const i of $('copy-list').querySelectorAll('input')) i.checked = false; };
$('copy-close').onclick = () => $('copy-dialog').close();
$('copy-form').onsubmit = async (e) => {
  e.preventDefault();
  const p = photo(), panels = tickedPanels();
  if (!panels.length) { $('copy-error').textContent = 'Tick at least one panel.'; return; }
  const look = { ...lookOf(p), crop: p.crop || null };
  if (Dv.copyMode === 'copy') {
    Dv.copied = { look, panels, from: p.name };
    $('copy-dialog').close();
    $('saved').textContent = `Copied ${panels.length} panel${panels.length === 1 ? '' : 's'} from ${p.name}. Select photos and press Cmd/Ctrl+Shift+V.`;
    $('menu-paste').disabled = false;
    return;
  }
  const targets = selectedPhotos().filter((x) => x.id !== p.id);
  $('copy-go').disabled = true;
  try { await pasteOnto(targets, look, panels, 'Sync settings'); $('copy-dialog').close(); }
  catch (err) { if (err.message !== 'signed out') $('copy-error').textContent = err.message; }
  $('copy-go').disabled = false;
};
async function pasteOnto(targets, look, panels, label) {
  if (!targets.length) { $('saved').textContent = 'Select a photo to paste onto first.'; return; }
  $('saved').textContent = `${label === 'Sync settings' ? 'Syncing' : 'Pasting'} onto ${targets.length} photo${targets.length === 1 ? '' : 's'}…`;
  const r = await api('/api/photos/look-bulk', { method: 'PUT', body: { photo_ids: targets.map((p) => p.id), panels, look, label } });
  for (const got of r.photos) { const p = S.photos.find((x) => x.id === got.id); if (p) { p.look = tidy(got.look); p.crop = got.crop; } }
  $('saved').textContent = `${label === 'Sync settings' ? 'Synced' : 'Pasted'}: ${r.photos.length} photo${r.photos.length === 1 ? '' : 's'} (${panels.join(', ')})`;
  lastHistoryKey = null;
  refreshThumbs(); drawPanel(); drawCaption(); drawStage(); drawFolders();
}
function pasteSettings() {
  if (!Dv.copied) { $('saved').textContent = 'Nothing copied yet: Cmd/Ctrl+Shift+C copies the settings of the photo on screen.'; return; }
  pasteOnto(selectedPhotos(), Dv.copied.look, Dv.copied.panels, 'Paste settings').catch((e) => { if (e.message !== 'signed out') $('saved').textContent = e.message; });
}
$('menu-copy').onclick = () => { $('more').open = false; openCopy(false); };
$('menu-paste').onclick = () => { $('more').open = false; pasteSettings(); };
$('menu-sync').onclick = () => { $('more').open = false; openCopy(true); };
$('sync-settings').onclick = () => openCopy(true);
document.addEventListener('keydown', (e) => {
  if (!(e.metaKey || e.ctrlKey) || e.altKey || e.target.matches('input, textarea, select') || document.querySelector('dialog[open]')) return;
  const k = e.key.toLowerCase();
  if (e.shiftKey && k === 'c') { e.preventDefault(); openCopy(false); }
  else if (e.shiftKey && k === 'v') { e.preventDefault(); pasteSettings(); }
  else if (k === 'z') { e.preventDefault(); stepHistory(e.shiftKey ? 'redo' : 'undo'); }
});

// ---- zoom: Fit, 1:1 and 2:1 --------------------------------------------------------------------------
// At 1:1 the part of the photo on screen is fetched from the server at full preview size (a "tile")
// around the centre; dragging pans and fetches the next piece. 2:1 shows that piece twice as large.
const fitRect = () => {
  const frame = $('frame'), img = $('after');
  const W = frame.clientWidth, H = frame.clientHeight;
  const a = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : W / H;
  const w = Math.min(W, H * a), h = w / a;
  return { left: (W - w) / 2, top: (H - h) / 2, w, h, W, H };
};
function setZoom(z, at) {
  if (S.view !== 'loupe' || !photo()) return;
  if (S.cropping) return;
  Dv.zoom = z;
  if (at) Dv.centre = at;
  for (const b of document.querySelectorAll('[data-zoom]')) b.setAttribute('aria-pressed', String(b.dataset.zoom === z));
  $('loupe').classList.toggle('zoomed', z !== 'fit');
  if (z === 'fit') { $('zoom-layer').hidden = true; $('nav-rect').hidden = true; placeDivider(); return; }
  drawZoom();
}
let zoomTurn = 0;
async function drawZoom() {
  const p = photo();
  if (!p || Dv.zoom === 'fit') return;
  const scale = Dv.zoom === '2:1' ? 2 : 1;
  const turn = ++zoomTurn;
  const frame = $('frame');
  const W = frame.clientWidth, H = frame.clientHeight;
  const layer = $('zoom-layer');
  layer.hidden = false;
  // the full picture's size is known after the first tile; until then ask for a generous piece
  const full = Dv.full && Dv.full.id === p.id ? Dv.full : null;     // the size is known after the first piece
  const want = { w: Math.min(1, (W / scale) / (full ? full.w : 1600)), h: Math.min(1, (H / scale) / (full ? full.h : 1200)) };
  const x = Math.min(1 - want.w, Math.max(0, Dv.centre.x - want.w / 2)), y = Math.min(1 - want.h, Math.max(0, Dv.centre.y - want.h / 2));
  Dv.centre = { x: x + want.w / 2, y: y + want.h / 2 };
  try { await keyLook(lookOf(p)); } catch (e) { /* the long address is tried instead */ }
  const url = `${base(p)}/tile?x=${x.toFixed(4)}&y=${y.toFixed(4)}&w=${want.w.toFixed(4)}&h=${want.h.toFixed(4)}&v=${p.v}&${lookParam(lookOf(p))}&c=${hash(JSON.stringify(p.crop || 0))}`;
  $('busy').hidden = false;
  try {
    const r = await fetch(url, { credentials: 'same-origin' });
    if (r.status === 401) { showSignIn(); return; }
    if (!r.ok) throw new Error(`The server answered ${r.status}.`);
    const [fw, fh] = r.headers.get('X-Full-Size').split(',').map(Number);
    const [tx, ty, tw, th] = r.headers.get('X-Tile-Rect').split(',').map(Number);
    const blob = await r.blob();
    if (turn !== zoomTurn) return;
    Dv.full = { id: p.id, k: zoomKey(p), w: fw, h: fh };
    const img = $('zoom-img');
    const old = img.src;
    img.src = URL.createObjectURL(blob);
    if (old.startsWith('blob:')) URL.revokeObjectURL(old);
    await img.decode().catch(() => {});
    if (turn !== zoomTurn) return;
    Dv.tile = { x: tx, y: ty, w: tw, h: th, fw, fh, scale };
    placeZoom();
    drawHistogram(img);
    if (!full || full.w !== fw || full.h !== fh) drawZoom();   // the first guess was made without the size: fetch the right piece
  } catch (e) { if (turn === zoomTurn) $('saved').textContent = e.message; }
  if (turn === zoomTurn) $('busy').hidden = true;
}
const zoomKey = (p) => hash(JSON.stringify([lookOf(p), p.crop || 0, p.v]));
function placeZoom() {
  const t = Dv.tile, img = $('zoom-img');
  if (!t) return;
  const frame = $('frame');
  const W = frame.clientWidth, H = frame.clientHeight;
  const s = t.scale;
  // the centre of the picture that should sit in the middle of the frame, in full-picture pixels
  const cx = Dv.centre.x * t.fw, cy = Dv.centre.y * t.fh;
  const left = W / 2 - (cx - t.x) * s, top = H / 2 - (cy - t.y) * s;
  img.style.left = `${Math.round(left)}px`; img.style.top = `${Math.round(top)}px`;
  img.style.width = `${t.w * s}px`; img.style.height = `${t.h * s}px`;
  // the navigator's rectangle: what the frame shows, as fractions of the whole picture
  const r = $('nav-rect'), nav = $('nav-img');
  const vw = Math.min(1, W / s / t.fw), vh = Math.min(1, H / s / t.fh);
  const nx = Math.min(1 - vw, Math.max(0, Dv.centre.x - vw / 2)), ny = Math.min(1 - vh, Math.max(0, Dv.centre.y - vh / 2));
  const box = nav.getBoundingClientRect(), host = $('nav').getBoundingClientRect();
  const a = nav.naturalWidth / nav.naturalHeight || 1.5, bw = Math.min(box.width, box.height * a), bh = bw / a;
  const bl = box.left - host.left + (box.width - bw) / 2, bt = box.top - host.top + (box.height - bh) / 2;
  r.hidden = false;
  r.style.left = `${bl + nx * bw}px`; r.style.top = `${bt + ny * bh}px`; r.style.width = `${vw * bw}px`; r.style.height = `${vh * bh}px`;
}
// Click the photo: Fit <-> 1:1 around the point clicked. Z does the same at the centre.
$('frame').addEventListener('click', (e) => {
  if (S.view !== 'loupe' || !photo() || S.cropping || S.tool === 'masks') return;
  if (e.target.closest('#divider, #crop-box, #shape-layer')) return;
  if (Dv.zoom !== 'fit') { if (Dv.dragged) { Dv.dragged = false; return; } setZoom('fit'); return; }
  const r = fitRect(), box = $('frame').getBoundingClientRect();
  const x = (e.clientX - box.left - r.left) / r.w, y = (e.clientY - box.top - r.top) / r.h;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  setZoom('1:1', { x, y });
});
// Drag to pan while zoomed in.
$('zoom-layer').addEventListener('pointerdown', (e) => {
  if (Dv.zoom === 'fit' || !Dv.tile) return;
  e.preventDefault();
  const layer = $('zoom-layer');
  layer.setPointerCapture(e.pointerId);
  const start = { x: e.clientX, y: e.clientY, cx: Dv.centre.x, cy: Dv.centre.y };
  let moved = false;
  const move = (m) => {
    const t = Dv.tile;
    const dx = (m.clientX - start.x) / t.scale / t.fw, dy = (m.clientY - start.y) / t.scale / t.fh;
    if (Math.abs(m.clientX - start.x) + Math.abs(m.clientY - start.y) > 3) moved = true;
    Dv.centre = { x: Math.min(1, Math.max(0, start.cx - dx)), y: Math.min(1, Math.max(0, start.cy - dy)) };
    placeZoom();
  };
  const up = () => {
    layer.removeEventListener('pointermove', move); layer.removeEventListener('pointerup', up); layer.removeEventListener('pointercancel', up);
    Dv.dragged = moved;
    if (moved) {                                   // the piece on screen may have run out: fetch around the new centre
      const t = Dv.tile, W = $('frame').clientWidth, H = $('frame').clientHeight;
      const vx0 = Dv.centre.x * t.fw - W / 2 / t.scale, vy0 = Dv.centre.y * t.fh - H / 2 / t.scale;
      if (vx0 < t.x || vy0 < t.y || vx0 + W / t.scale > t.x + t.w || vy0 + H / t.scale > t.y + t.h) drawZoom();
    }
  };
  layer.addEventListener('pointermove', move); layer.addEventListener('pointerup', up); layer.addEventListener('pointercancel', up);
});
$('nav').addEventListener('click', (e) => {              // click the navigator while zoomed: go there
  if (Dv.zoom === 'fit' || !Dv.tile || S.view !== 'loupe') return;
  const nav = $('nav-img'), box = nav.getBoundingClientRect();
  const a = nav.naturalWidth / nav.naturalHeight || 1.5, bw = Math.min(box.width, box.height * a), bh = bw / a;
  const x = (e.clientX - box.left - (box.width - bw) / 2) / bw, y = (e.clientY - box.top - (box.height - bh) / 2) / bh;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  Dv.centre = { x, y }; placeZoom(); drawZoom();
});
for (const b of document.querySelectorAll('[data-zoom]')) b.onclick = () => setZoom(b.dataset.zoom, Dv.zoom === 'fit' ? { x: 0.5, y: 0.5 } : null);
document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.target.matches('input, textarea, select') || document.querySelector('dialog[open]')) return;
  if ((e.key === 'z' || e.key === 'Z') && S.view === 'loupe' && photo()) { e.preventDefault(); setZoom(Dv.zoom === 'fit' ? '1:1' : 'fit', Dv.zoom === 'fit' ? { x: 0.5, y: 0.5 } : null); }
});
// The zoom follows the photo: a new photo, a change to the look or the crop, or leaving Develop puts it back to Fit.
const _drawStageBeforeDv = drawStage;
drawStage = async function () {                                              // eslint-disable-line no-global-assign
  if (Dv.zoom !== 'fit' && (S.view !== 'loupe' || !photo() || !Dv.full || Dv.full.id !== S.photoId || S.cropping)) setZoom('fit');
  await _drawStageBeforeDv();
  if (Dv.zoom === 'fit' || !photo() || stageLive) return;        // while a slider is dragged only the quick picture moves
  if (Dv.full && Dv.full.k !== zoomKey(photo())) drawZoom();     // the look changed: the piece is drawn again
  else placeZoom();
};
window.addEventListener('resize', () => { if (Dv.zoom !== 'fit') placeZoom(); });
const _placeDividerBeforeDv = placeDivider;
placeDivider = function () { _placeDividerBeforeDv(); if (Dv.zoom !== 'fit') { $('divider').hidden = true; $('tag-before').hidden = $('tag-after').hidden = true; } };   // eslint-disable-line no-global-assign
