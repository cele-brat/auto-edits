// Masks you draw (engine/shapes.py): Radial Gradient, Linear Gradient and Brush, as in Lightroom.
// Shapes live in the look (look.shapes), so they follow Apply to: this photo or the whole folder.
// Positions are fractions of the picture before cropping (after straightening), so a shape stays put
// at every size; on screen they are mapped through the photo's crop.
'use strict';

const D = { sel: null, rect: null, drag: null, stroke: null, brush: { size: 0.06, feather: 60, flow: 100, erase: false } };
const SHAPE_NAMES = { radial: 'Radial', linear: 'Linear', brush: 'Brush' };
const NS = 'http://www.w3.org/2000/svg';
const shapesNow = () => panelLook().shapes || [];
const newId = () => Math.random().toString(36).slice(2, 10);

function changeShapes(shapes, live = false) { change({ shapes }, live); }
function updateShape(i, patch, live = false) {
  const list = shapesNow().map((s, k) => (k === i ? { ...s, ...patch } : s));
  changeShapes(list, live);
}

// ---- where the picture is on screen, and the photo's crop -----------------------------------
// The picture on screen is the crop of the full (straightened) frame, unless the crop tool is open.
function frameCrop() {
  const p = photo();
  return !S.cropping && p && p.crop && p.crop.w ? p.crop : { x: 0, y: 0, w: 1, h: 1 };
}
function toScreen(X, Y) {
  const r = D.rect, c = frameCrop();
  return [r.left + (X - c.x) / c.w * r.w, r.top + (Y - c.y) / c.h * r.h];
}
function toFrame(clientX, clientY) {
  const box = $('frame').getBoundingClientRect(), r = D.rect, c = frameCrop();
  return [c.x + (clientX - box.left - r.left) / r.w * c.w, c.y + (clientY - box.top - r.top) / r.h * c.h];
}
const fullW = () => D.rect.w / frameCrop().w;       // the whole frame's width on screen, in pixels
const fullH = () => D.rect.h / frameCrop().h;

function placeShapes(rect) {
  if (rect) D.rect = rect;
  const layer = $('shape-layer');
  const on = S.tool === 'masks' && S.view === 'loupe' && !!photo() && !!D.rect;
  layer.toggleAttribute('hidden', !on);          // an <svg> has no .hidden property
  if (!on) return;
  layer.setAttribute('width', $('frame').clientWidth);
  layer.setAttribute('height', $('frame').clientHeight);
  layer.classList.toggle('painting', D.sel != null && (shapesNow()[D.sel] || {}).type === 'brush');
  drawLayer();
}
window.placeShapes = placeShapes;

function svg(tag, attrs, parent) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.append(n);
  return n;
}

// ---- drawing the shapes and their handles --------------------------------------------------
function drawLayer() {
  const layer = $('shape-layer');
  layer.replaceChildren();
  const list = shapesNow();
  list.forEach((s, i) => {
    const sel = i === D.sel;
    const g = svg('g', { class: `shape ${sel ? 'sel' : ''}`, 'data-i': i }, layer);
    if (s.type === 'radial') {
      const [x, y] = toScreen(s.cx, s.cy);
      const rx = s.rx * fullW(), ry = s.ry * fullH(), f = (s.feather ?? 50) / 100;
      const turn = `rotate(${s.angle || 0} ${x} ${y})`;
      svg('ellipse', { cx: x, cy: y, rx, ry, transform: turn, class: 'edge' }, g);
      svg('ellipse', { cx: x, cy: y, rx, ry, transform: turn, class: 'hit', 'data-h': sel ? 'move' : 'pick' }, g);
      if (sel) {
        svg('ellipse', { cx: x, cy: y, rx: rx * (1 - f), ry: ry * (1 - f), transform: turn, class: 'inner' }, g);
        const a = (s.angle || 0) * Math.PI / 180;
        handle(g, x + rx * Math.cos(a), y + rx * Math.sin(a), 'rx');
        handle(g, x - ry * Math.sin(a), y + ry * Math.cos(a), 'ry');
        handle(g, x - rx * Math.cos(a), y - rx * Math.sin(a), 'rx');
        handle(g, x + ry * Math.sin(a), y - ry * Math.cos(a), 'ry');
        handle(g, x, y, 'move', 'pin');
      } else svg('circle', { cx: x, cy: y, r: 5, class: 'pin', 'data-h': 'pick' }, g);
    } else if (s.type === 'linear') {
      const [x0, y0] = toScreen(s.x0, s.y0), [x1, y1] = toScreen(s.x1, s.y1);
      let dx = x1 - x0, dy = y1 - y0;
      const len = Math.hypot(dx, dy) || 1, L = 4000;
      const px = -dy / len * L, py = dx / len * L;                 // along each line, across the photo
      svg('line', { x1: x0 - px, y1: y0 - py, x2: x0 + px, y2: y0 + py, class: 'edge full' }, g);
      svg('line', { x1: x1 - px, y1: y1 - py, x2: x1 + px, y2: y1 + py, class: 'edge' }, g);
      svg('line', { x1: x0, y1: y0, x2: x1, y2: y1, class: 'inner' }, g);
      svg('line', { x1: x0 - px, y1: y0 - py, x2: x0 + px, y2: y0 + py, class: 'hit', 'data-h': sel ? 'p0' : 'pick' }, g);
      svg('line', { x1: x1 - px, y1: y1 - py, x2: x1 + px, y2: y1 + py, class: 'hit', 'data-h': sel ? 'p1' : 'pick' }, g);
      if (sel) { handle(g, x0, y0, 'p0'); handle(g, x1, y1, 'p1'); handle(g, (x0 + x1) / 2, (y0 + y1) / 2, 'move', 'pin'); }
      else svg('circle', { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, r: 5, class: 'pin', 'data-h': 'pick' }, g);
    } else if (s.type === 'brush') {
      const strokes = [...(s.strokes || []), ...(sel && D.stroke ? [D.stroke] : [])];
      for (const st of strokes) {
        const pts = [];
        for (let k = 0; k < st.pts.length; k += 2) pts.push(toScreen(st.pts[k], st.pts[k + 1]).join(','));
        if (!pts.length) continue;
        if (pts.length === 1) pts.push(pts[0]);
        svg('polyline', { points: pts.join(' '), class: `paint ${st.erase ? 'erase' : ''}`, 'stroke-width': Math.max(2, st.size * fullW()), opacity: sel ? 0.35 : 0.12 }, g);
      }
      if (!sel && s.strokes && s.strokes.length && s.strokes[0].pts.length) {
        const [x, y] = toScreen(s.strokes[0].pts[0], s.strokes[0].pts[1]);
        svg('circle', { cx: x, cy: y, r: 5, class: 'pin', 'data-h': 'pick' }, g);
      }
    }
  });
  if (D.sel != null && (list[D.sel] || {}).type === 'brush') svg('circle', { class: 'cursor', id: 'brush-cursor', r: D.brush.size * fullW() / 2, cx: -100, cy: -100 }, layer);
}
function handle(g, x, y, h, cls = 'knob') { svg('circle', { cx: x, cy: y, r: cls === 'pin' ? 6 : 5.5, class: cls, 'data-h': h }, g); }

// ---- dragging handles, and painting with the brush -------------------------------------------
const layerEl = $('shape-layer');
layerEl.addEventListener('pointerdown', (e) => {
  const list = shapesNow();
  const t = e.target.closest('[data-h]');
  const g = e.target.closest('g.shape');
  const shape = list[D.sel];
  if (shape && shape.type === 'brush' && (!t || +g.dataset.i === D.sel)) {     // paint (or erase)
    e.preventDefault();
    layerEl.setPointerCapture(e.pointerId);
    const [X, Y] = toFrame(e.clientX, e.clientY);
    const b = D.brush;
    D.stroke = { size: b.size, feather: b.feather, flow: b.flow, erase: b.erase || e.altKey, pts: [r4(X), r4(Y)] };
    D.drag = { kind: 'paint' };
    drawLayer();
    return;
  }
  if (!t) return;
  const i = +g.dataset.i;
  if (t.dataset.h === 'pick' || i !== D.sel) { selectShape(i); return; }
  e.preventDefault();
  layerEl.setPointerCapture(e.pointerId);
  D.drag = { kind: t.dataset.h, i, start: toFrame(e.clientX, e.clientY), shape: { ...list[i] } };
});
const r4 = (v) => Math.round(v * 10000) / 10000;
let shapeLiveTimer = null;
layerEl.addEventListener('pointermove', (e) => {
  const cur = $('brush-cursor');
  if (cur) { const box = $('frame').getBoundingClientRect(); cur.setAttribute('cx', e.clientX - box.left); cur.setAttribute('cy', e.clientY - box.top); }
  if (!D.drag) return;
  const [X, Y] = toFrame(e.clientX, e.clientY);
  if (D.drag.kind === 'paint') {
    const p = D.stroke.pts, lx = p[p.length - 2], ly = p[p.length - 1];
    if (Math.hypot((X - lx) * fullW(), (Y - ly) * fullH()) < 3) return;    // a point every 3 screen pixels
    p.push(r4(X), r4(Y));
    drawLayer();
    return;
  }
  const s = D.drag.shape, [sx, sy] = D.drag.start, dx = X - sx, dy = Y - sy;
  let patch = {};
  if (s.type === 'radial') {
    if (D.drag.kind === 'move') patch = { cx: r4(s.cx + dx), cy: r4(s.cy + dy) };
    else {
      // distance from the centre along the handle's axis, in fractions of width or height
      const a = (s.angle || 0) * Math.PI / 180;
      const ux = (X - s.cx) * fullW(), uy = (Y - s.cy) * fullH();
      if (D.drag.kind === 'rx') patch = { rx: r4(Math.max(0.01, Math.abs(ux * Math.cos(a) + uy * Math.sin(a)) / fullW())) };
      else patch = { ry: r4(Math.max(0.01, Math.abs(-ux * Math.sin(a) + uy * Math.cos(a)) / fullH())) };
    }
  } else if (s.type === 'linear') {
    if (D.drag.kind === 'move') patch = { x0: r4(s.x0 + dx), y0: r4(s.y0 + dy), x1: r4(s.x1 + dx), y1: r4(s.y1 + dy) };
    else if (D.drag.kind === 'p0') patch = { x0: r4(X), y0: r4(Y) };
    else patch = { x1: r4(X), y1: r4(Y) };
  }
  D.drag.last = patch;
  // the outline follows at once; the photo itself every ~120 ms
  const list = shapesNow().map((x, k) => (k === D.drag.i ? { ...x, ...patch } : x));
  const look = panelLook();
  if (editingPhoto()) photo().look = tidy({ ...look, shapes: list }); else folder().settings = tidy({ ...look, shapes: list });
  drawLayer();
  if (!shapeLiveTimer) shapeLiveTimer = setTimeout(() => { shapeLiveTimer = null; if (D.drag) updateShape(D.drag.i, D.drag.last, true); }, 120);
});
function endDrag() {
  if (!D.drag) return;
  const drag = D.drag;
  D.drag = null;
  clearTimeout(shapeLiveTimer); shapeLiveTimer = null;
  if (drag.kind === 'paint') {
    const st = D.stroke;
    D.stroke = null;
    const s = shapesNow()[D.sel];
    if (s && st) updateShape(D.sel, { strokes: [...(s.strokes || []), st] });
  } else if (drag.last) updateShape(drag.i, drag.last);
}
layerEl.addEventListener('pointerup', endDrag);
layerEl.addEventListener('pointercancel', endDrag);
layerEl.addEventListener('pointerleave', () => { const cur = $('brush-cursor'); if (cur) cur.setAttribute('cx', -100); });
// brush size with the mouse wheel, as in Lightroom
layerEl.addEventListener('wheel', (e) => {
  const s = shapesNow()[D.sel];
  if (!s || s.type !== 'brush') return;
  e.preventDefault();
  D.brush.size = Math.min(0.5, Math.max(0.005, D.brush.size * (e.deltaY > 0 ? 0.9 : 1.1)));
  drawLayer(); drawDrawn();
}, { passive: false });

// ---- adding, choosing and removing ---------------------------------------------------------------
function addShape(type) {
  if (!photo() || S.view !== 'loupe') { if (photo()) openPhoto(S.photoId); else return; }
  setTool('masks');
  const c = frameCrop();
  const cx = c.x + c.w / 2, cy = c.y + c.h / 2;
  const base = { id: newId(), type, invert: false, adj: {} };
  const shape = type === 'radial' ? { ...base, cx: r4(cx), cy: r4(cy), rx: r4(c.w * 0.22), ry: r4(c.h * 0.22), angle: 0, feather: 50 }
    : type === 'linear' ? { ...base, x0: r4(cx), y0: r4(c.y + c.h * 0.15), x1: r4(cx), y1: r4(c.y + c.h * 0.5) }
    : { ...base, strokes: [] };
  const list = [...shapesNow(), shape];
  D.sel = list.length - 1;
  S.showShape = null;
  changeShapes(list);
  placeShapes();
}
function selectShape(i) {
  D.sel = D.sel === i ? null : i;
  if (S.showShape != null) { S.showShape = D.sel; drawStage(); }
  placeShapes(); drawDrawn();
}
function removeShape(i) {
  const list = shapesNow().filter((_, k) => k !== i);
  D.sel = null;
  if (S.showShape != null) S.showShape = null;
  changeShapes(list);
  placeShapes();
}
for (const b of document.querySelectorAll('.mask-add [data-add]')) b.onclick = () => addShape(b.dataset.add);

// ---- the list of drawn masks, and the selected one's settings ------------------------------------
function drawDrawn() {
  const box = $('drawn');
  const list = shapesNow();
  const counts = {};
  const rows = [];
  list.forEach((s, i) => {
    counts[s.type] = (counts[s.type] || 0) + 1;
    const name = `${SHAPE_NAMES[s.type]} ${counts[s.type]}`;
    const changes = Object.keys(s.adj || {}).length;
    const head = el('button', { type: 'button', className: 'part' }, [name]);
    if (s.invert) head.append(el('small', { textContent: 'inverted' }));
    if (changes) head.append(el('span', { className: 'changed', textContent: `${changes} change${changes === 1 ? '' : 's'}` }));
    head.setAttribute('aria-expanded', String(i === D.sel));
    head.onclick = () => selectShape(i);
    rows.push(head);
    if (i !== D.sel) return;
    const body = el('div', { className: 'body' });
    if (s.type === 'brush') {
      const b = D.brush;
      const mode = el('div', { className: 'seg wide brush-mode' });
      for (const [val, label] of [[false, 'Paint'], [true, 'Erase']]) {
        const btn = el('button', { type: 'button', textContent: label });
        btn.setAttribute('aria-pressed', String(b.erase === val));
        btn.onclick = () => { b.erase = val; drawDrawn(); };
        mode.append(btn);
      }
      body.append(mode, el('p', { className: 'dim small', textContent: 'Paint on the photo. Mouse wheel: brush size. Hold Alt to erase.' }));
      body.append(row('Size', 0.5, 50, 0.5, b.size * 100, (v) => { b.size = v / 100; drawLayer(); }));
      body.append(row('Feather', 0, 100, 1, b.feather, (v) => { b.feather = v; }));
      body.append(row('Flow', 1, 100, 1, b.flow, (v) => { b.flow = v; }));
      if ((s.strokes || []).length) {
        const undo = el('button', { type: 'button', className: 'quiet small', textContent: 'Undo last stroke' });
        undo.onclick = () => updateShape(i, { strokes: s.strokes.slice(0, -1) });
        body.append(el('div', { className: 'tools' }, [undo, el('span', { className: 'dim small', textContent: `${s.strokes.length} stroke${s.strokes.length === 1 ? '' : 's'}` })]));
      }
    }
    if (s.type === 'radial') {
      body.append(row('Feather', 0, 100, 1, s.feather ?? 50, (v, live) => updateShape(i, { feather: v }, live)));
      body.append(row('Angle', -90, 90, 1, s.angle || 0, (v, live) => updateShape(i, { angle: v }, live)));
    }
    body.append(el('h3', { className: 'sub', textContent: 'Changes inside the mask' }));
    for (const sl of MASK_SLIDERS) {
      const value = (s.adj || {})[sl.key] || 0;
      body.append(row(sl.label, sl.min ?? -100, sl.max ?? 100, sl.step ?? 1, value, (v, live) => {
        const adj = { ...(shapesNow()[i].adj || {}), [sl.key]: v };
        if (!v) delete adj[sl.key];
        updateShape(i, { adj }, live);
      }, true, sl.digits || 0));
    }
    const invert = el('input', { type: 'checkbox', checked: !!s.invert });
    invert.onchange = () => updateShape(i, { invert: invert.checked });
    const show = el('input', { type: 'checkbox', checked: S.showShape === i });
    show.onchange = () => { S.showShape = show.checked ? i : null; S.showMask = false; drawStage(); };
    const del = el('button', { type: 'button', className: 'quiet small danger-text', textContent: 'Delete mask' });
    del.onclick = () => removeShape(i);
    body.append(el('div', { className: 'tools' }, [el('label', { className: 'show' }, [invert, 'Invert']),
      el('label', { className: 'show' }, [show, 'Show in red']), del]));
    rows.push(body);
  });
  if (!list.length) rows.push(el('p', { className: 'dim small empty-drawn', textContent: 'None yet. Add a radial, linear or brush mask above.' }));
  box.replaceChildren(...rows);
}
window.drawDrawn = drawDrawn;

// one slider row: name · track · number, the same as the main sliders
function row(label, min, max, step, value, onChange, signedValue = false, digits = 0) {
  const input = el('input', { type: 'range', min, max, step, value });
  input.dataset.step0 = String(step);
  if (S.precise && step < 1) input.step = String(step / 5);
  const out = el('output', { textContent: signedValue ? signed(value, digits) : String(Math.round(value * 10) / 10) });
  const zero = signedValue ? 0 : min;
  paint(input, signedValue ? 0 : zero);
  input.oninput = () => {
    const v = Number(input.value);
    out.textContent = signedValue ? signed(v, digits) : String(Math.round(v * 10) / 10);
    paint(input, signedValue ? 0 : zero);
    onChange(v, true);
  };
  input.onchange = () => { onChange(Number(input.value), false); };
  if (signedValue) input.ondblclick = () => { input.value = 0; input.oninput(); onChange(0, false); };
  input.setAttribute('aria-label', label);
  return el('div', { className: 'adj' }, [el('span', { className: 'name', textContent: label }), input, out]);
}

// keep the layer and list right as things change
const _drawPanelBeforeDrawn = drawPanel;
drawPanel = function () { _drawPanelBeforeDrawn(); placeShapes(); };   // eslint-disable-line no-global-assign
const _setToolBeforeDrawn = setTool;
setTool = function (tool) {                                             // eslint-disable-line no-global-assign
  _setToolBeforeDrawn(tool);
  if (tool !== 'masks' && S.showShape != null) { S.showShape = null; drawStage(); }
  if (tool !== 'masks') D.sel = null;
  placeShapes();
};
const _openPhotoBeforeDrawn = openPhoto;
openPhoto = function (id) { D.sel = null; S.showShape = null; _openPhotoBeforeDrawn(id); };   // eslint-disable-line no-global-assign
document.addEventListener('keydown', (e) => {
  if (e.target.matches('input, select, textarea') || e.metaKey || e.ctrlKey) return;
  if ((e.key === 'b' || e.key === 'B') && S.view === 'loupe' && photo()) { e.preventDefault(); addShape('brush'); }
  else if ((e.key === 'Delete' || e.key === 'Backspace') && D.sel != null && S.tool === 'masks') { e.preventDefault(); removeShape(D.sel); }
});
