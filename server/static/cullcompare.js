// Compare view (Library, key C): two to four photos side by side with the same zoom and pan, each
// with a strip of its faces at 1:1 and their scores (eyes open, sharpness), to pick the best frame.
// Select photos in the grid with Shift or Cmd/Ctrl + click, or stand on a photo of a burst group
// from the cull. Left/Right moves between the panes, Enter (or K) makes the current one the winner:
// it goes to Keep, the others to Reject. "Next burst" goes on to the next group.
'use strict';

const Cmp = { open: false, ids: [], at: 0, zoom: 1, x: 0, y: 0, drag: null, group: null, selected: new Set(), loaded: new Map() };

// ---- selecting more than one photo in the grid ---------------------------------------------
$('grid').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-id]');
  if (!b) return;
  if (e.shiftKey && S.photoId && S.photoId !== b.dataset.id) {
    const list = inFolder().map((p) => p.id);
    const [a, z] = [list.indexOf(S.photoId), list.indexOf(b.dataset.id)].sort((x, y) => x - y);
    for (const id of list.slice(a, z + 1)) Cmp.selected.add(id);
    e.preventDefault(); e.stopPropagation();
  } else if (e.metaKey || e.ctrlKey) {
    if (Cmp.selected.has(b.dataset.id)) Cmp.selected.delete(b.dataset.id); else Cmp.selected.add(b.dataset.id);
    if (!Cmp.selected.has(S.photoId) && S.photoId) Cmp.selected.add(S.photoId);
    e.preventDefault(); e.stopPropagation();
  } else {
    Cmp.selected.clear();
  }
  drawSelection();
}, true);
function drawSelection() {
  for (const b of document.querySelectorAll('#grid button[data-id]')) b.classList.toggle('selected', Cmp.selected.has(b.dataset.id));
  const n = Cmp.selected.size;
  const hint = document.querySelector('#toolbar .hint-keys');
  if (hint) hint.innerHTML = n > 1 ? `<b>${n} selected</b> · press <b>C</b> to compare them side by side` :
    'Double-click a photo to edit it · <b>D</b> Develop · <b>G</b> Library · Shift+click to select, <b>C</b> to compare';
}

// ---- burst groups from the cull ------------------------------------------------------------
const groupOf = (p) => (p.cull && p.cull.group != null ? p.cull.group : null);
function burstMembers(g) { return wholeFolder().filter((p) => groupOf(p) === g); }
function bursts() {
  const seen = new Set(), out = [];
  for (const p of wholeFolder()) { const g = groupOf(p); if (g != null && !seen.has(g)) { seen.add(g); if (burstMembers(g).length > 1) out.push(g); } }
  return out;
}
function nextBurst(from) {
  const all = bursts();
  if (!all.length) return null;
  const i = all.indexOf(from);
  return all[(i + 1) % all.length];
}

// ---- opening and drawing -----------------------------------------------------------------------
function openCompare(ids, group = null) {
  ids = ids.filter((id) => S.photos.some((p) => p.id === id)).slice(0, 4);
  if (ids.length < 2) { $('saved').textContent = 'Select two to four photos (Shift+click) or stand on a photo of a burst, then press C.'; return; }
  Cmp.ids = ids; Cmp.at = Math.max(0, ids.indexOf(S.photoId)); Cmp.group = group;
  Cmp.zoom = 1; Cmp.x = 0; Cmp.y = 0; Cmp.open = true;
  let box = $('compare');
  if (!box) {
    box = el('div', { id: 'compare' });
    box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', 'Compare photos');
    const head = el('div', { id: 'compare-head' }, [
      el('h2', { textContent: 'Compare' }), el('span', { className: 'keys', id: 'compare-what' }), el('span', { className: 'grow' }),
      el('span', { className: 'keys', textContent: '← → move · Enter or K: this one wins (Keep, others Reject) · scroll: zoom · drag: pan · Esc: close' }),
      el('button', { type: 'button', className: 'quiet', textContent: 'Close', onclick: closeCompare }),
    ]);
    const panes = el('div', { id: 'compare-panes' });
    const foot = el('div', { id: 'compare-foot' }, [
      el('button', { type: 'button', className: 'primary', id: 'compare-win', textContent: 'This one wins (Enter)', onclick: () => pickWinner() }),
      el('button', { type: 'button', id: 'compare-fit', textContent: 'Fit (0)', onclick: () => { Cmp.zoom = 1; Cmp.x = Cmp.y = 0; placeAll(); } }),
      el('button', { type: 'button', id: 'compare-100', textContent: '1:1 (1)', onclick: () => zoomTo(100) }),
      el('span', { className: 'note', id: 'compare-note' }), el('span', { className: 'grow' }),
      el('button', { type: 'button', id: 'compare-next', textContent: 'Next burst (N)', onclick: () => goNextBurst() }),
    ]);
    box.append(head, panes, foot);
    document.body.append(box);
  }
  box.hidden = false;
  drawCompare();
}
function closeCompare() {
  Cmp.open = false;
  const box = $('compare'); if (box) box.hidden = true;
  Cmp.loaded.clear();
  markCurrent();
}

function drawCompare() {
  const panes = $('compare-panes');
  const list = Cmp.ids.map((id) => S.photos.find((p) => p.id === id)).filter(Boolean);
  panes.style.gridTemplateColumns = `repeat(${list.length}, minmax(0, 1fr))`;
  $('compare-what').textContent = Cmp.group != null ? `Burst group · ${list.length} frames` : `${list.length} selected photos`;
  $('compare-next').hidden = !bursts().length;
  panes.replaceChildren(...list.map((p, i) => {
    const pane = el('div', { className: 'cmp' });
    pane.dataset.id = p.id;
    const view = el('div', { className: 'view' });
    const img = el('img', { alt: p.name, draggable: false });
    img.src = afterUrl(p, 1600);
    img.onload = () => { Cmp.loaded.set(p.id, img); placeAll(); drawFaces(p, pane, img); };
    view.append(img);
    view.onpointerdown = (e) => { Cmp.drag = { x: e.clientX, y: e.clientY, ox: Cmp.x, oy: Cmp.y }; view.setPointerCapture(e.pointerId); Cmp.at = i; markPanes(); };
    view.onpointermove = (e) => { if (!Cmp.drag) return; Cmp.x = Cmp.drag.ox + (e.clientX - Cmp.drag.x); Cmp.y = Cmp.drag.oy + (e.clientY - Cmp.drag.y); placeAll(); };
    view.onpointerup = view.onpointercancel = () => { Cmp.drag = null; };
    view.addEventListener('wheel', (e) => { e.preventDefault(); zoomBy(Math.exp(-e.deltaY * 0.002), e, view); }, { passive: false });
    view.ondblclick = (e) => { if (Cmp.zoom > 1.5) { Cmp.zoom = 1; Cmp.x = Cmp.y = 0; placeAll(); } else zoomBy(3, e, view); };
    const r = ratingOf(p);
    const cap = el('div', { className: 'cap' }, [el('b', { textContent: p.name })]);
    if (p.cull && p.cull.score != null) cap.append(el('span', { textContent: `score ${p.cull.score.toFixed(2)}` }));
    if (p.cull && p.cull.why && p.cull.why.length) cap.append(el('span', { textContent: p.cull.why.join(', '), title: p.cull.why.join(', ') }));
    if (p.cull && p.cull.best) cap.append(el('span', { textContent: '· best of the burst by the cull' }));
    if (r) cap.append(el('span', { className: `rate ${r}${p.pick ? ' mine' : ''}`, textContent: RATING_NAMES[r] }));
    const faces = el('div', { className: 'faces' });
    pane.append(view, cap, faces);
    return pane;
  }));
  markPanes();
}
function markPanes() {
  document.querySelectorAll('#compare .cmp').forEach((pane, i) => pane.classList.toggle('current', i === Cmp.at));
  const p = S.photos.find((x) => x.id === Cmp.ids[Cmp.at]);
  $('compare-note').textContent = p ? `Current: ${p.name}` : '';
}

// the same zoom and pan on every pane: fit = 1; the picture is placed about its centre
function placeAll() {
  for (const pane of document.querySelectorAll('#compare .cmp')) {
    const view = pane.querySelector('.view'), img = pane.querySelector('img');
    if (!img.naturalWidth) continue;
    const W = view.clientWidth, H = view.clientHeight;
    const fit = Math.min(W / img.naturalWidth, H / img.naturalHeight);
    const s = fit * Cmp.zoom;
    const w = img.naturalWidth * s, h = img.naturalHeight * s;
    img.style.transform = `translate(${(W - w) / 2 + Cmp.x}px, ${(H - h) / 2 + Cmp.y}px) scale(${s})`;
  }
}
function zoomBy(k, e, view) {
  const r = view.getBoundingClientRect();
  const cx = e.clientX - r.left - r.width / 2, cy = e.clientY - r.top - r.height / 2;
  const z = Math.min(12, Math.max(1, Cmp.zoom * k)), real = z / Cmp.zoom;
  Cmp.x = cx - (cx - Cmp.x) * real; Cmp.y = cy - (cy - Cmp.y) * real; Cmp.zoom = z;
  if (Cmp.zoom === 1) Cmp.x = Cmp.y = 0;
  placeAll();
}
function zoomTo(percent) {
  const img = Cmp.loaded.get(Cmp.ids[Cmp.at]);
  const view = document.querySelector('#compare .cmp.current .view');
  if (!img || !view) return;
  const fit = Math.min(view.clientWidth / img.naturalWidth, view.clientHeight / img.naturalHeight);
  Cmp.zoom = Math.max(1, (percent / 100) / fit); Cmp.x = Cmp.y = 0;
  placeAll();
}

// ---- the face strip --------------------------------------------------------------------------
async function drawFaces(p, pane, img) {
  const strip = pane.querySelector('.faces');
  let data;
  try { data = await api(`/api/photos/${p.id}/faces`); } catch (e) { strip.classList.add('none'); return; }
  if (!Cmp.open || !pane.isConnected) return;
  strip.replaceChildren();
  if (!data.faces.length) { strip.classList.add('none'); return; }
  const judged = data.faces.filter((f) => f.judged).sort((a, b) => b.size - a.size);
  for (const f of (judged.length ? judged : data.faces).slice(0, 8)) {
    const [x0, y0, x1, y1] = f.box;
    const W = img.naturalWidth, H = img.naturalHeight;
    const bw = (x1 - x0) * W, bh = (y1 - y0) * H, side = Math.max(bw, bh) * 1.4;
    const cx = (x0 + x1) / 2 * W, cy = (y0 + y1) / 2 * H;
    const c = el('canvas', { width: 144, height: 144 });
    const g = c.getContext('2d');
    g.imageSmoothingQuality = 'high';
    const sx = Math.max(0, cx - side / 2), sy = Math.max(0, cy - side / 2);
    const sw = Math.min(W - sx, side), sh = Math.min(H - sy, side);
    const k = Math.max(1, 144 / side);                              // 1:1 at the preview's pixels, enlarged only when the face is small
    g.drawImage(img, sx, sy, sw, sh, 0, 0, sw * k, sh * k);
    const cell = el('div', { className: 'face' }, [c]);
    if (f.judged) {
      const eyes = f.eyes_open, sharp = f.sharpness;
      cell.append(el('span', { className: eyes >= 0.6 ? 'good' : eyes >= 0.3 ? 'so' : 'bad', textContent: eyes >= 0.6 ? 'Eyes open' : eyes >= 0.3 ? 'Eyes half' : 'Eyes closed' }));
      cell.append(el('span', { className: sharp >= 0.075 ? 'good' : sharp >= 0.05 ? 'so' : 'bad', textContent: `Sharp ${Math.round(sharp * 1000)}` }));
      cell.title = `eyes open ${Math.round(eyes * 100)}%, sharpness ${sharp}, smile ${Math.round((f.smile || 0) * 100)}%`;
    } else cell.append(el('span', { textContent: 'too small' }));
    strip.append(cell);
  }
}

// ---- picking the winner -------------------------------------------------------------------------
async function setPick(p, pick) {
  p.pick = pick;
  try { await api(`/api/weddings/${S.wedding.id}/photos/${p.id}/pick`, { method: 'PUT', body: { pick } }); }
  catch (err) { $('saved').textContent = err.message; }
}
async function pickWinner() {
  const winner = Cmp.ids[Cmp.at];
  for (const id of Cmp.ids) {
    const p = S.photos.find((x) => x.id === id);
    if (p) await setPick(p, id === winner ? 'keep' : 'reject');
  }
  S.photoId = winner;
  if (window.drawRatings) redrawAfterRating(); else drawGrid();
  $('compare-note').textContent = `Kept ${(S.photos.find((x) => x.id === winner) || {}).name}; the rest are Reject (1, 2, 3 keys change it).`;
  drawCompare();
  if (Cmp.group != null && bursts().length > 1) setTimeout(goNextBurst, 500);
}
function goNextBurst() {
  const g = nextBurst(Cmp.group);
  if (g == null) return;
  const ids = burstMembers(g).map((p) => p.id);
  S.photoId = ids[0];
  openCompare(ids, g);
}

// ---- keys -----------------------------------------------------------------------------------------
document.addEventListener('keydown', (e) => {
  if (e.target.matches('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
  if (Cmp.open) {
    if (e.key === 'Escape') { e.preventDefault(); closeCompare(); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); Cmp.at = (Cmp.at + (e.key === 'ArrowRight' ? 1 : Cmp.ids.length - 1)) % Cmp.ids.length; markPanes(); }
    else if (e.key === 'Enter' || e.key === 'k' || e.key === 'K') { e.preventDefault(); pickWinner(); }
    else if (e.key === 'n' || e.key === 'N') { e.preventDefault(); goNextBurst(); }
    else if (e.key === '0') { e.preventDefault(); Cmp.zoom = 1; Cmp.x = Cmp.y = 0; placeAll(); }
    else if (e.key === '1') { e.preventDefault(); zoomTo(100); }
    else if (e.key === '2') { e.preventDefault(); zoomTo(200); }
    else if (e.key === '3' && !e.repeat) { e.preventDefault(); const p = S.photos.find((x) => x.id === Cmp.ids[Cmp.at]); if (p) setPick(p, 'keep').then(drawCompare); }
    e.stopImmediatePropagation();
    return;
  }
  if ((e.key === 'c' || e.key === 'C') && S.view === 'grid' && S.wedding && !document.querySelector('dialog[open]')) {
    e.preventDefault();
    const p = photo();
    if (Cmp.selected.size >= 2) openCompare(inFolder().filter((x) => Cmp.selected.has(x.id)).map((x) => x.id));
    else if (p && groupOf(p) != null && burstMembers(groupOf(p)).length > 1) openCompare(burstMembers(groupOf(p)).map((x) => x.id), groupOf(p));
    else if (p) { const list = inFolder(), i = list.indexOf(p); if (list[i + 1]) openCompare([p.id, list[i + 1].id]); }
  }
}, true);
window.addEventListener('resize', () => { if (Cmp.open) placeAll(); });
const drawGridBefore = drawGrid;
drawGrid = function () { drawGridBefore(); drawSelection(); };
