// Crop tool and AI crop (server: engine/crop.py). A crop is a setting on each photo, in fractions of the
// picture; the uploaded photo is never changed, and Lightroom gets an ordinary crop it can still move.
'use strict';

const CROP_RATIOS = [['original', 'Original'], ['free', 'Free'], ['1:1', '1:1 square'], ['4:5', '4:5 portrait'], ['5:4', '5:4'],
  ['2:3', '2:3 portrait'], ['3:2', '3:2'], ['5:7', '5:7 print'], ['7:5', '7:5 print'], ['9:16', '9:16 story'], ['16:9', '16:9 wide']];
const C = { rect: null, drag: null };

$('crop-ratio').replaceChildren(...CROP_RATIOS.map(([v, t]) => new Option(t, v)));

// The chosen shape as a width/height ratio of the fractions (1 = the photo's own shape), or null for free.
function fracRatio(ratio) {
  if (ratio === 'free') return null;
  if (ratio === 'original') return 1;
  const [a, b] = ratio.split(':').map(Number);
  const img = $('after');
  const pic = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : 1.5;
  return (a / b) / pic;
}

// The largest box of this shape inside the current one, kept centred: used when the shape changes.
function fitShape(box, ratio) {
  const r = fracRatio(ratio);
  if (!r) return box;
  let w = box.w, h = box.w / r;
  if (h > box.h) { h = box.h; w = h * r; }
  if (w > 1) { w = 1; h = w / r; }
  if (h > 1) { h = 1; w = h * r; }
  const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
  return clampBox({ x: cx - w / 2, y: cy - h / 2, w, h });
}

function clampBox(b) {
  const w = Math.min(1, Math.max(0.05, b.w)), h = Math.min(1, Math.max(0.05, b.h));
  return { x: Math.min(1 - w, Math.max(0, b.x)), y: Math.min(1 - h, Math.max(0, b.y)), w, h };
}

function placeCrop(rect) {
  if (rect) C.rect = rect;
  const box = $('crop-box');
  box.hidden = !S.cropping;
  if (!S.cropping || !C.rect) return;
  const b = S.cropping.box, r = C.rect;
  box.style.left = `${r.left + b.x * r.w}px`; box.style.top = `${r.top + b.y * r.h}px`;
  box.style.width = `${b.w * r.w}px`; box.style.height = `${b.h * r.h}px`;
  box.classList.toggle('fixed', S.cropping.ratio !== 'free');
}

function drawCropBar() {
  const on = !!S.cropping;
  $('crop-bar').hidden = !on;
  $('crop-open').hidden = on;
  $('crop-open').disabled = !photo();
  $('crop-tool').setAttribute('aria-pressed', String(S.tool === 'crop'));
  if (on) $('crop-ratio').value = S.cropping.ratio;
}

function startCrop() {
  if (S.view !== 'loupe' && photo()) openPhoto(S.photoId);       // R in Library: open the photo and crop it
  const p = photo();
  if (!p || S.view !== 'loupe' || S.cropping) return;
  setTool('crop');
  const c = p.crop;
  S.cropping = { box: c ? { x: c.x, y: c.y, w: c.w, h: c.h } : { x: 0, y: 0, w: 1, h: 1 }, ratio: c ? c.ratio : 'original', auto: !!(c && c.auto) };
  $('crop-note').textContent = '';
  drawCropBar(); placeDivider(); drawStage();
}

async function endCrop(save) {
  const p = photo();
  const was = S.cropping;
  S.cropping = null;
  if (S.tool === 'crop') setTool('adjust');                       // as in Lightroom: Done closes the crop tool
  drawCropBar();
  if (save && p && was) {
    const b = was.box;
    const whole = b.w >= 0.999 && b.h >= 0.999;
    const body = whole ? null : { ...b, ratio: was.ratio, auto: was.auto };
    try {
      const r = await api(`${base(p)}/crop`, { method: 'PUT', body });
      p.crop = r.crop;
      $('saved').textContent = 'Saved';
    } catch (e) { if (e.message !== 'signed out') $('saved').textContent = `Crop not saved. ${e.message}`; }
    refreshThumbs(); drawCaption();
  }
  placeDivider(); drawStage();
}

function cropKey(e) {
  if (S.cropping) {
    if (e.key === 'Escape') { endCrop(false); return true; }
    if (e.key === 'Enter') { endCrop(true); return true; }
    if (e.key === 'r' || e.key === 'R') { endCrop(true); return true; }
    return e.key === 'ArrowLeft' || e.key === 'ArrowRight';   // no moving to another photo mid-crop
  }
  if ((e.key === 'r' || e.key === 'R') && photo()) { startCrop(); return true; }
  return false;
}

// ---- dragging the box ------------------------------------------------------------------
const cropBox = $('crop-box');
cropBox.addEventListener('pointerdown', (e) => {
  if (!S.cropping || !C.rect) return;
  e.preventDefault();
  cropBox.setPointerCapture(e.pointerId);
  C.drag = { h: e.target.dataset.h || 'move', x: e.clientX, y: e.clientY, box: { ...S.cropping.box } };
});
cropBox.addEventListener('pointermove', (e) => {
  if (!C.drag || !S.cropping) return;
  const d = C.drag, r = C.rect;
  const dx = (e.clientX - d.x) / r.w, dy = (e.clientY - d.y) / r.h;
  let { x, y, w, h } = d.box;
  if (d.h === 'move') {
    S.cropping.box = clampBox({ x: x + dx, y: y + dy, w, h });
  } else {
    const right = x + w, bottom = y + h;
    if (d.h.includes('w')) { x = Math.min(right - 0.05, Math.max(0, x + dx)); w = right - x; }
    if (d.h.includes('e')) { w = Math.min(1 - x, Math.max(0.05, w + dx)); }
    if (d.h.includes('n')) { y = Math.min(bottom - 0.05, Math.max(0, y + dy)); h = bottom - y; }
    if (d.h.includes('s')) { h = Math.min(1 - y, Math.max(0.05, h + dy)); }
    const ratio = fracRatio(S.cropping.ratio);
    if (ratio) {                                   // corners only: keep the shape, the opposite corner stays put
      if (w / h > ratio) w = h * ratio; else h = w / ratio;
      if (d.h.includes('w')) x = right - w;
      if (d.h.includes('n')) y = bottom - h;
    }
    S.cropping.box = clampBox({ x, y, w, h });
  }
  S.cropping.auto = false;
  $('crop-note').textContent = '';
  placeCrop();
});
cropBox.addEventListener('pointerup', () => { C.drag = null; });
cropBox.addEventListener('pointercancel', () => { C.drag = null; });

// ---- the bar ------------------------------------------------------------------------------
// The Crop button in the tool strip: crops the photo on screen; in Library it shows the folder's crop tools.
$('crop-tool').onclick = () => {
  if (S.cropping) return endCrop(true);
  if (S.tool === 'crop') { setTool('adjust'); drawCropBar(); return; }
  if (S.view === 'loupe' && photo()) return startCrop();
  setTool('crop'); drawCropBar();
};
$('crop-open').onclick = startCrop;
$('crop-done').onclick = () => endCrop(true);
$('crop-cancel').onclick = () => endCrop(false);
$('crop-whole').onclick = () => { S.cropping.box = { x: 0, y: 0, w: 1, h: 1 }; S.cropping.auto = false; $('crop-note').textContent = ''; placeCrop(); };
$('crop-ratio').onchange = (e) => {
  S.cropping.ratio = e.target.value;
  S.cropping.box = fitShape(S.cropping.box, S.cropping.ratio);
  placeCrop();
  e.target.blur();
};
$('crop-ai').onclick = async () => {
  const p = photo();
  if (!p || !S.cropping) return;
  const ratio = S.cropping.ratio === 'free' ? 'original' : S.cropping.ratio;
  $('crop-ai').disabled = true;
  $('crop-note').textContent = 'Finding the people…';
  try {
    const r = await api(`${base(p)}/auto-crop?ratio=${encodeURIComponent(ratio)}`, { method: 'POST' });
    if (!S.cropping) return;
    if (r.crop) {
      S.cropping.box = { x: r.crop.x, y: r.crop.y, w: r.crop.w, h: r.crop.h };
      S.cropping.ratio = r.crop.ratio; S.cropping.auto = true;
      $('crop-ratio').value = r.crop.ratio;
      $('crop-note').textContent = `AI crop: ${r.why}. Drag to adjust, then Done.`;
    } else {
      $('crop-note').textContent = r.why === 'no people found' ? 'No people found, so nothing to frame. Crop it by hand.' : 'This photo is already well framed.';
    }
    placeCrop();
  } catch (e) { if (e.message !== 'signed out') $('crop-note').textContent = e.message; }
  $('crop-ai').disabled = false;
};

// ---- a whole folder -------------------------------------------------------------------------
let cropRun = 0;
$('crop-folder').onclick = async () => {
  if (cropRun) { cropRun = 0; return; }               // the button stops a run
  const list = wholeFolder();
  if (!list.length) return;
  const run = cropRun = Date.now();
  $('crop-folder').textContent = 'Stop';
  let done = 0, framed = 0, kept = 0, none = 0;
  for (const p of list) {
    if (run !== cropRun) break;
    try {
      const r = await api(`${base(p)}/auto-crop?save=1&keep_own=1`, { method: 'POST' });
      p.crop = r.crop;
      if (!r.changed) kept += 1; else if (r.crop) framed += 1; else none += 1;
    } catch (e) { if (e.message === 'signed out') return; }
    done += 1;
    $('crop-progress').textContent = `AI crop: ${done} of ${list.length}…`;
    if (done % 12 === 0) refreshThumbs();
  }
  cropRun = 0;
  $('crop-folder').textContent = 'AI crop every photo in this folder';
  $('crop-progress').textContent = `AI cropped ${framed} photo${framed === 1 ? '' : 's'}` +
    (none ? `; ${none} left whole (no people, or already well framed)` : '') +
    (kept ? `; ${kept} kept your own crop` : '') + (done < list.length ? ` (stopped at ${done} of ${list.length})` : '') + '.';
  refreshThumbs(); drawCaption(); drawStage();
};
$('crop-clear').onclick = async () => {
  const f = folder();
  if (!f) return;
  try {
    const r = await api(`/api/weddings/${S.wedding.id}/folders/${f.id}/crops`, { method: 'DELETE' });
    for (const p of wholeFolder()) p.crop = null;
    $('crop-progress').textContent = r.cleared ? `Removed ${r.cleared} crop${r.cleared === 1 ? '' : 's'}. The photos show whole again.` : 'No photos here are cropped.';
    refreshThumbs(); drawCaption(); drawStage();
  } catch (e) { if (e.message !== 'signed out') $('crop-progress').textContent = e.message; }
};

// keep the buttons right as the view changes
const _drawPanelBeforeCrop = drawPanel;
drawPanel = function () { _drawPanelBeforeCrop(); drawCropBar(); };   // eslint-disable-line no-global-assign
drawCropBar();
