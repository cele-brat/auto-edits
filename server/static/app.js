/* Auto Edits: the app screen. No build step; plain browser JavaScript. */
'use strict';

const $ = (id) => document.getElementById(id);
const TOGGLES = [
  ['exposure', 'Exposure'], ['highlights', 'Highlights'], ['shadows', 'Shadows'], ['whites', 'Whites'],
  ['blacks', 'Blacks'], ['white_balance', 'White balance'], ['noise', 'Noise'], ['straighten', 'Straighten'],
  ['lens', 'Lens fix'],
];
const SHOWN_HERE = new Set(['exposure', 'highlights', 'shadows', 'whites', 'blacks', 'white_balance', 'straighten']);
const ALL_TOGGLES = TOGGLES.map((t) => t[0]);
const MODE_NOTES = {
  preset: 'The preset exactly as it is. Tick Auto on a slider to have it worked out per photo.',
  preset_auto: 'Sliders with Auto ticked are worked out per photo; the rest come from the preset.',
  auto: 'No preset: every setting is worked out for each photo.',
};

const S = {
  weddings: [], wedding: null, folderId: null, photos: [], presets: [], byPreset: new Map(), looks: [],
  photoId: null, view: 'grid', scope: 'folder', split: true, holdBefore: false, dividerAt: 0.5,
  rating: null, panels: [], tool: 'adjust', openPacks: new Set(), query: '', openLook: null, lastToggles: ['exposure', 'white_balance'], openMask: null, showMask: false,
};

// ---- talking to the server ------------------------------------------------
async function api(path, options = {}) {
  const init = { method: options.method || 'GET', headers: { 'X-Requested-With': 'AutoEdits' }, credentials: 'same-origin' };
  if ('body' in options) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(options.body);
  }
  const r = await fetch(path, init);
  if (r.status === 401) { showSignIn(); throw new Error('signed out'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(typeof data.detail === 'string' ? data.detail : `The server answered ${r.status}.`);
    e.status = r.status;
    throw e;
  }
  return data;
}

// ---- looks ----------------------------------------------------------------
const blankLook = () => ({ mode: 'preset', preset: null, amount: 1, toggles: [], ev: 0, temp: 0, tint: 0,
  hl: 0, sh: 0, wh: 0, bl: 0, noise: 0, angle: 0, masks: {}, preset_masks: true, adj: {}, shapes: [] });
// Parts of the photo that can be changed on their own (Lightroom's own mask kinds).
const MASKS = [
  ['subject', 'Subject', 'the people (Select Subject)'], ['background', 'Background', 'everything but the people'],
  ['face_skin', 'Face skin'], ['body_skin', 'Body skin'], ['eyes', 'Eyes'], ['hair', 'Hair'], ['clothes', 'Clothes'],
  ['sky', 'Sky', 'rough'],
];
const MASK_SLIDERS = [
  { key: 'ev', label: 'Exposure', min: -2, max: 2, step: 0.05, digits: 2 },
  { key: 'contrast', label: 'Contrast' }, { key: 'hl', label: 'Highlights' }, { key: 'sh', label: 'Shadows' },
  { key: 'texture', label: 'Texture' }, { key: 'clarity', label: 'Clarity' }, { key: 'sat', label: 'Saturation' },
  { key: 'temp', label: 'Warmth' }, { key: 'tint', label: 'Tint' },
];
// Names for the mask kinds and settings that can come inside a Lightroom preset.
const KIND_NAMES = { subject: 'Subject', whole_person: 'Whole person', background: 'Background', face_skin: 'Face skin',
  body_skin: 'Body skin', hair: 'Hair', clothes: 'Clothes', iris_pupil: 'Iris and pupil', eye_sclera: 'Eye whites',
  eyebrows: 'Eyebrows', lips: 'Lips', teeth: 'Teeth', facial_hair: 'Facial hair', sky: 'Sky',
  linear_gradient: 'Linear gradient', radial_gradient: 'Radial gradient', brush: 'Brush', range: 'Range' };
const ADJ_NAMES = { ev: 'exposure', contrast: 'contrast', hl: 'highlights', sh: 'shadows', wh: 'whites', bl: 'blacks',
  texture: 'texture', clarity: 'clarity', dehaze: 'dehaze', sat: 'saturation', temp: 'warmth', tint: 'tint',
  sharp: 'sharpness', noise: 'noise' };
const NOT_PREVIEWED = new Set(['sharp', 'noise', 'dehaze']);
// Every Lightroom slider, panel by panel, as the server lists them (see server/adjustments.py).
const allSliders = () => S.panels.flatMap((p) => p.groups.flatMap((g) => g.sliders));

function tidy(look) {
  const l = { ...blankLook(), ...look };
  if (l.mode === 'auto') { l.preset = null; l.toggles = [...ALL_TOGGLES]; }
  else if (l.mode === 'preset') l.toggles = [];
  else l.toggles = ALL_TOGGLES.filter((t) => l.toggles.includes(t));
  const masks = {};
  for (const [kind, values] of Object.entries(l.masks || {})) {
    const kept = Object.fromEntries(Object.entries(values || {}).filter(([, v]) => v));
    if (Object.keys(kept).length) masks[kind] = kept;
  }
  l.masks = masks;
  l.preset_masks = l.preset_masks !== false;
  l.adj = Object.fromEntries(Object.entries(l.adj || {}).filter(([, v]) => v));
  l.shapes = Array.isArray(l.shapes) ? l.shapes : [];
  return l;
}
const folder = () => (S.wedding ? S.wedding.folders.find((f) => f.id === S.folderId) : null);
const photo = () => S.photos.find((p) => p.id === S.photoId) || null;
const folderLook = (f = folder()) => tidy(f && f.settings && f.settings.mode ? f.settings : {});
const lookOf = (p) => (p.look ? tidy(p.look) : folderLook(S.wedding.folders.find((f) => f.id === p.folder)));
const editingPhoto = () => S.scope === 'photo' && !!photo();      // Library: the selected photo; Develop: the one on screen
const panelLook = () => (editingPhoto() ? lookOf(photo()) : folderLook());

function describe(look) {
  const masks = Object.keys(look.masks || {}).length || (look.shapes || []).length ? ' + masks' : '';
  if (look.mode === 'auto') return `Auto Correct${masks}`;
  const p = S.byPreset.get(look.preset);
  const own = ['ev', 'temp', 'tint', 'hl', 'sh', 'wh', 'bl', 'noise', 'angle'].some((k) => look[k]) || Object.keys(look.adj || {}).length;
  if (!p) return own || masks ? `Your own adjustment${masks}` : 'Not edited yet';
  return p.name + (look.mode === 'preset_auto' && look.toggles.length ? ' + auto' : '') + masks;
}

function hash(text) {
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}
const base = (p) => `/api/weddings/${S.wedding.id}/photos/${p.id}`;
const thumbUrl = (p) => `${base(p)}/image?size=360&k=${hash(JSON.stringify([lookOf(p), p.crop || 0]))}${p.v}`;
// A look goes in the picture's address; one too long for that (brush masks) is handed to the server
// once (POST /api/looks) and named by a short key. lookKeys must be filled (keyLook) before use.
const lookKeys = new Map();
function lookParam(look) {
  const text = JSON.stringify(look);
  if (text.length < 1500) return `look=${encodeURIComponent(text)}`;
  const key = lookKeys.get(text);
  return key ? `lk=${key}` : `look=${encodeURIComponent(text)}`;
}
async function keyLook(look) {
  const text = JSON.stringify(look);
  if (text.length < 1500 || lookKeys.has(text)) return;
  const r = await api('/api/looks', { method: 'POST', body: look });
  lookKeys.set(text, r.key);
  if (lookKeys.size > 300) lookKeys.delete(lookKeys.keys().next().value);
}
const showParam = () => (S.showShape != null ? `&show=shape${S.showShape}` : S.showMask && S.openMask ? `&show=${S.openMask}` : '');
const afterUrl = (p, size) => `${base(p)}/image?size=${size}&v=${p.v}&${lookParam(lookOf(p))}` +
  (S.cropping ? '&uncropped=1' : `&c=${hash(JSON.stringify(p.crop || 0))}`) + showParam();
const beforeUrl = (p) => `${base(p)}/image?size=1600&base=1&v=${p.v}` +
  (p.crop ? `&c=${hash(JSON.stringify(p.crop))}&${lookParam(lookOf(p))}` : '');

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

// live: a slider is being dragged. Only the numbers and the photo are redrawn (the photo at most
// every 80 ms), so the slider follows the hand; everything else is redrawn when it is let go.
let stageTimer = null, liveBusy = false, liveAgain = false;
// While dragging, one render at a time: the next starts when the last is on screen, always with
// the newest slider position. Asking for every position in between would queue up on the server.
function liveStage() {
  if (liveBusy) { liveAgain = true; return; }
  liveBusy = true;
  drawStage().finally(() => {
    liveBusy = false;
    if (liveAgain) { liveAgain = false; if (stageLive) liveStage(); }
  });
}
function change(patch, live = false) {
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
  if (!live) { clearTimeout(stageTimer); stageTimer = null; stageLive = false; drawPanel(); drawStage(); return; }
  drawSliders(look);
  stageLive = true;
  if (!stageTimer) stageTimer = setTimeout(() => { stageTimer = null; liveStage(); }, 60);
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
      el('small', { textContent: String(f.photos), title: `${f.photos} photo${f.photos === 1 ? '' : 's'}` }),
      el('small', { textContent: describe(folderLook(f)) }),
    ]);
    b.setAttribute('aria-current', String(f.id === S.folderId));
    b.onclick = () => openFolder(f.id);
    return b;
  }));
}

// A photo's rating: the photographer's own if set, else the automatic one, else none.
const ratingOf = (p) => p.pick || (p.cull ? p.cull.r : null);
const wholeFolder = () => S.photos.filter((p) => p.folder === S.folderId);
const inFolder = () => wholeFolder().filter((p) => !S.rating || ratingOf(p) === S.rating);
const RATING_NAMES = { keep: 'Keep', maybe: 'Maybe', reject: 'Reject' };

function thumb(p, withName, idx) {
  const b = el('button', { type: 'button', title: p.name });
  b.dataset.id = p.id;
  if (idx) b.append(el('span', { className: 'idx', textContent: String(idx) }));
  b.append(el('img', { loading: 'lazy', alt: p.name, src: thumbUrl(p) }));
  if (p.look) b.append(el('span', { className: 'own', title: 'Has its own settings' }));
  const r = ratingOf(p);
  if (r) b.append(el('span', { className: `rate ${r}${p.pick ? ' mine' : ''}`, textContent: RATING_NAMES[r],
    title: p.pick ? 'Set by you' : (p.cull.why.join(', ') || 'Nothing wrong found') }));
  if (withName) b.append(el('span', { className: 'name', textContent: p.name }));
  b.setAttribute('aria-current', String(p.id === S.photoId));
  return b;
}

function drawGrid() {
  const grid = $('grid');
  grid.replaceChildren(...inFolder().map((p, i) => {
    const b = thumb(p, true, i + 1);
    b.onclick = () => { S.photoId = p.id; markCurrent(); };
    b.ondblclick = () => openPhoto(p.id);
    return b;
  }));
  const strip = $('strip');
  strip.replaceChildren(...inFolder().map((p) => {
    const b = thumb(p, false);
    // as in Lightroom: in Develop the filmstrip changes the photo; in Library it selects one
    b.onclick = () => { if (S.view === 'loupe') openPhoto(p.id); else { S.photoId = p.id; markCurrent(true); } };
    b.ondblclick = () => openPhoto(p.id);
    return b;
  }));
  drawFilmInfo();
}

// The line above the filmstrip: which folder, how many photos, which one is selected.
function drawFilmInfo() {
  const f = folder();
  if (!f) { $('film-info').replaceChildren(); return; }
  const shown = inFolder().length, all = wholeFolder().length, p = photo();
  const bits = [el('b', { textContent: f.name }), ` · ${shown === all ? `${all} photo${all === 1 ? '' : 's'}` : `${shown} of ${all} photos shown`}`];
  if (p) bits.push(' · ', el('b', { textContent: p.name }));
  $('film-info').replaceChildren(...bits);
}

// Thumbnails after a change: the server has few cores, so asking for a whole folder of new
// thumbnails at once would make the big photo wait behind them. Instead: the big photo first,
// then only the thumbnails on screen, two at a time, the selected photo's first. A thumbnail
// scrolled into view later is fetched then. The old picture stays until the new one is ready.
const thumbs = { queue: [], running: 0 };
const onScreen = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) queueThumb(e.target);
}, { rootMargin: '120px' });
function queueThumb(b, first = false) {
  const img = b.querySelector('img');
  if (!img || !img.dataset.want || img.src.endsWith(img.dataset.want) || thumbs.queue.includes(b)) return;
  if (first) thumbs.queue.unshift(b); else thumbs.queue.push(b);
  pumpThumbs();
}
function pumpThumbs() {
  while (thumbs.running < 2 && thumbs.queue.length && !stageBusy) {
    const b = thumbs.queue.shift();
    const img = b.querySelector('img');
    const url = img && img.dataset.want;
    if (!url || img.src.endsWith(url) || !b.isConnected) continue;
    thumbs.running += 1;
    const pre = new Image();
    pre.src = url;
    pre.decode().catch(() => {}).then(() => {
      if (img.dataset.want === url) img.src = url;
      thumbs.running -= 1;
      pumpThumbs();
    });
  }
}
function refreshThumbs() {
  thumbs.queue = [];
  for (const b of document.querySelectorAll('#grid button, #strip button')) {
    const p = S.photos.find((x) => x.id === b.dataset.id);
    if (!p) continue;
    const img = b.querySelector('img');
    img.dataset.want = thumbUrl(p);
    onScreen.unobserve(b);
    onScreen.observe(b);                       // queued when (and if) it is on screen
    const dot = b.querySelector('.own');
    if (p.look && !dot) b.append(el('span', { className: 'own', title: 'Has its own settings' }));
    if (!p.look && dot) dot.remove();
  }
  const cur = document.querySelector('#strip button[aria-current=true]');
  if (cur) queueThumb(cur, true);
  drawNavigator();
}

function markCurrent(fromStrip = false) {
  for (const b of document.querySelectorAll('#grid button, #strip button')) {
    const on = b.dataset.id === S.photoId;
    b.setAttribute('aria-current', String(on));
    if (on && b.parentElement.id === 'strip') b.scrollIntoView({ block: 'nearest', inline: 'center' });
    if (on && fromStrip && b.parentElement.id === 'grid') b.scrollIntoView({ block: 'nearest' });
  }
  drawNavigator();
  drawFilmInfo();
  drawCaption();
  if (editingPhoto() && S.view === 'grid') drawPanel();      // the panel shows the selected photo's settings
}

// ---- navigator and histogram (top of each side panel, as in Lightroom) --------------------
function drawNavigator() {
  const p = photo(), img = $('nav-img');
  if (!p) { img.removeAttribute('src'); clearHistogram(); return; }
  if (S.view === 'loupe') return;                 // Develop: showAfter puts the photo on screen here too
  const url = thumbUrl(p);
  if (!img.src.endsWith(url)) img.src = url;
  else if (S.view !== 'loupe' && img.complete) drawHistogram(img);
}
$('nav-img').onload = () => { if (S.view !== 'loupe') drawHistogram($('nav-img')); };

const histoCanvas = document.createElement('canvas');
function clearHistogram() {
  const c = $('histo');
  c.getContext('2d').clearRect(0, 0, c.width, c.height);
}
// Red, green and blue drawn on top of one another and added, so where all three meet it shows grey
// to white, the way Lightroom's histogram does. Counted from a small copy of the picture.
function drawHistogram(img) {
  if (!img || !img.naturalWidth) return;
  const w = 240, h = Math.max(1, Math.round(w * img.naturalHeight / img.naturalWidth));
  histoCanvas.width = w; histoCanvas.height = h;
  const g = histoCanvas.getContext('2d', { willReadFrequently: true });
  let px;
  try { g.drawImage(img, 0, 0, w, h); px = g.getImageData(0, 0, w, h).data; } catch (e) { return; }
  const bins = [new Float32Array(256), new Float32Array(256), new Float32Array(256)];
  for (let i = 0; i < px.length; i += 4) { bins[0][px[i]]++; bins[1][px[i + 1]]++; bins[2][px[i + 2]]++; }
  // scale to the tallest bar away from the two ends, so a clipped end does not flatten the rest
  // smooth a little, then scale to a tall bar but not the very tallest few, so one spike (a clipped end,
  // a flat background) does not flatten the rest
  const smooth = bins.map((b) => b.map((_, i) => {
    let t = 0, n = 0;
    for (let k = -2; k <= 2; k++) { const v = b[i + k]; if (v !== undefined) { t += v * (3 - Math.abs(k)); n += 3 - Math.abs(k); } }
    return t / n;
  }));
  const heights = smooth.flatMap((b) => [...b.slice(2, 254)]).sort((x, y) => y - x);
  const top = Math.max(1, heights[Math.floor(heights.length * 0.03)] || 1);
  const c = $('histo'), ctx = c.getContext('2d'), W = c.width, H = c.height;
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = 'rgba(255,255,255,.06)'; ctx.lineWidth = 1;
  for (const x of [0.25, 0.5, 0.75]) { ctx.beginPath(); ctx.moveTo(Math.round(x * W) + 0.5, 0); ctx.lineTo(Math.round(x * W) + 0.5, H); ctx.stroke(); }
  ctx.globalCompositeOperation = 'lighter';
  const colours = ['rgba(210,60,60,.85)', 'rgba(60,180,70,.85)', 'rgba(60,100,230,.85)'];
  smooth.forEach((b, ch) => {
    ctx.fillStyle = colours[ch];
    ctx.beginPath(); ctx.moveTo(0, H);
    for (let i = 0; i < 256; i++) {
      const v = b[i];
      ctx.lineTo((i / 255) * W, H - Math.min(1, v / top) * (H - 4));
    }
    ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
  });
  ctx.globalCompositeOperation = 'source-over';
}

// ---- drawing: the big photo ---------------------------------------------------
let renderTurn = 0, busyTimer = null, stageLive = false, stageBusy = false;
// Loads a picture and has it fully decoded before it is handed back, so putting it on screen
// never shows a blank frame.
async function loadImage(url) {
  const i = new Image();
  i.decoding = 'sync';
  i.src = url;
  try { await i.decode(); } catch (e) { throw new Error('The photo could not be rendered.'); }
  return i;
}
// Swap the big photo for an already-decoded one in a single step (no flicker).
function showAfter(img) {
  const old = $('after');
  img.id = 'after'; img.alt = ''; img.draggable = false;
  img.onload = placeDivider;
  old.replaceWith(img);
  placeDivider();
  drawHistogram(img);
  if ($('nav-img').src !== img.src) $('nav-img').src = img.src;   // same picture, already loaded
}

async function drawStage() {
  const empty = !S.wedding || !inFolder().length;
  $('empty').hidden = !empty;
  $('grid').hidden = empty || S.view !== 'grid';
  $('loupe').hidden = empty || S.view !== 'loupe';
  $('view-grid').setAttribute('aria-pressed', String(S.view === 'grid'));
  $('view-loupe').setAttribute('aria-pressed', String(S.view === 'loupe'));
  $('view-loupe').disabled = !photo();
  $('toolbar').dataset.view = S.view;
  const p = photo();
  if (empty || S.view !== 'loupe' || !p) { stageBusy = false; pumpThumbs(); return; }

  try { await keyLook(lookOf(p)); } catch (e) { /* the long address is tried instead */ }
  const before = $('before');
  const wanted = beforeUrl(p);
  if (!before.src.endsWith(wanted)) before.src = wanted;

  const turn = ++renderTurn;
  stageBusy = true;
  // "Rendering" only shows when a render is slow, so it does not blink on every small move
  clearTimeout(busyTimer);
  busyTimer = setTimeout(() => { if (turn === renderTurn) $('busy').hidden = false; }, 600);
  try {
    // While a slider is being dragged only the quick size is made. Otherwise the quick and the sharp
    // one are asked for together: the quick one shows first, the sharp one replaces it when ready
    // (and if the sharp one comes first, the quick one is not shown at all).
    if (stageLive) {
      const img = await loadImage(afterUrl(p, 800));
      if (turn !== renderTurn) return;
      showAfter(img);
    } else {
      let sharpShown = false;
      const quick = loadImage(afterUrl(p, 800)).then((img) => { if (turn === renderTurn && !sharpShown) showAfter(img); }, () => {});
      const img = await loadImage(afterUrl(p, 1600));
      if (turn !== renderTurn) return;
      sharpShown = true;
      showAfter(img);
      quick.catch(() => {});
    }
  } catch (e) {
    if (turn === renderTurn && lookKeys.size && !drawStage.retried) {   // the server forgot a long look: hand it over again
      lookKeys.clear(); drawStage.retried = true;
      return drawStage().finally(() => { drawStage.retried = false; });
    }
    if (turn === renderTurn) $('saved').textContent = e.message;
  }
  if (turn === renderTurn) { clearTimeout(busyTimer); $('busy').hidden = true; stageBusy = false; pumpThumbs(); }
}

function drawCaption() {
  const p = photo();
  const r = p ? ratingOf(p) : null;
  for (const b of document.querySelectorAll('#toolbar .picks button')) {
    b.setAttribute('aria-pressed', String(!!p && r === b.dataset.pick));
    b.classList.toggle('auto-pick', !!p && r === b.dataset.pick && !p.pick);
    b.disabled = !p;
  }
  $('histo-info').textContent = p ? [p.camera, p.iso ? `ISO ${p.iso}` : '', p.crop && p.crop.w ? (p.crop.auto ? 'AI cropped' : 'Cropped') : '',
    p.crop && p.crop.turn ? `Turned ${p.crop.turn}°` : ''].filter(Boolean).join('  ·  ') : '';
  if (!p) { $('caption').replaceChildren(); return; }
  const bits = [el('b', { textContent: p.name })];
  if (p.edited) bits.push(el('span', { textContent: 'Came from Lightroom with edits already on it' }));
  if (r) bits.push(el('span', { textContent: p.pick ? `${RATING_NAMES[r]}: set by you` : `${RATING_NAMES[r]}: ${p.cull.why.join(', ') || 'nothing wrong found'}` }));
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
  const whole = S.holdBefore && !S.cropping;
  const split = S.split && !S.cropping;
  before.style.visibility = split || whole ? 'visible' : 'hidden';
  before.style.clipPath = whole ? 'none' : `inset(0 ${W - x}px 0 0)`;
  const d = $('divider');
  d.hidden = !split || whole;
  d.style.left = `${x}px`;
  d.style.top = `${top}px`;
  d.style.bottom = `${H - top - h}px`;
  d.setAttribute('aria-valuenow', String(Math.round(S.dividerAt * 100)));
  const tb = $('tag-before'), ta = $('tag-after');
  tb.hidden = !(split || whole);
  ta.hidden = !split || whole;
  tb.style.left = `${left + 8}px`;
  tb.style.top = ta.style.top = `${top + 8}px`;
  ta.style.right = `${W - left - w + 8}px`;
  $('split').setAttribute('aria-pressed', String(S.split));
  if (window.placeCrop) placeCrop({ left, top, w, h });
  if (window.placeShapes) placeShapes({ left, top, w, h });
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
  const packs = [...new Set(S.presets.map((p) => p.pack))].sort((a, b) => (b === 'My presets') - (a === 'My presets'));
  S.looks.sort((a, b) => (b.pack === 'My presets') - (a.pack === 'My presets'));
  S.packs = packs;
}

function shownLooks() {
  const q = S.query.trim().toLowerCase();
  return S.looks.filter((l) => (!q || l.pack.toLowerCase().includes(q) || l.look.toLowerCase().includes(q) || l.variants.some((v) => v.variant.toLowerCase().includes(q))));
}

// The Presets panel, as in Lightroom: one folding group per pack, its looks inside; a look with
// several strengths opens to show them. A search opens every group that has a match.
const lookKey = (p) => `${p.pack}|${p.look || p.name}`;
function drawLooks() {
  const look = panelLook();
  const chosen = S.byPreset.get(look.preset);
  // S.openLook: the look shown open; '' means all closed; null follows the preset in use
  const open = S.openLook !== null && S.openLook !== undefined ? S.openLook : (chosen ? lookKey(chosen) : null);
  const searching = !!S.query.trim();
  const box = $('looks');
  const none = el('button', { type: 'button', className: 'look none', textContent: 'No preset' });
  none.setAttribute('aria-pressed', String(!chosen));
  none.onclick = () => { S.openLook = null; change({ preset: null }); };
  const rows = [none];
  const shown = shownLooks();
  for (const pack of S.packs || []) {
    const looks = shown.filter((l) => l.pack === pack);
    if (!looks.length) continue;
    const expanded = searching || S.openPacks.has(pack);
    const head = el('button', { type: 'button', className: 'pack' }, [pack, el('small', { textContent: String(looks.length) })]);
    head.setAttribute('aria-expanded', String(expanded));
    head.onclick = () => { if (S.openPacks.has(pack)) S.openPacks.delete(pack); else S.openPacks.add(pack); drawLooks(); };
    rows.push(head);
    if (!expanded) continue;
    for (const l of looks) {
      const single = l.variants.length === 1;
      const row = el('button', { type: 'button', className: 'look' }, [single ? l.variants[0].variant : l.look]);
      if (single && l.variants[0].variant !== l.look && l.pack === 'My presets') row.append(el('small', { textContent: l.look }));
      if (l.variants.some((v) => v.masks.length)) row.append(el('small', { className: 'has', textContent: 'masks' }));
      if (l.variants.length > 1) row.append(el('small', { textContent: String(l.variants.length) }));
      row.setAttribute('aria-expanded', String(l.key === open));
      row.onclick = () => {
        if (l.key === open && !single) { S.openLook = ''; drawLooks(); return; }            // open: fold it closed
        S.openLook = l.key;
        if (l.variants.some((v) => v.id === look.preset)) drawLooks();                     // holds the preset in use: just open it
        else change({ preset: l.variants[0].id });
      };
      rows.push(row);
      if (l.key === open && l.variants.length > 1) {
        rows.push(el('div', { className: 'variants' }, l.variants.map((v) => {
          const b = el('button', { type: 'button', textContent: v.variant });
          b.dataset.preset = v.id;
          b.setAttribute('aria-pressed', String(v.id === look.preset));
          b.onclick = () => change({ preset: v.id });
          return b;
        })));
      } else if (l.key === open) row.dataset.preset = l.variants[0].id;
    }
  }
  if (!shown.length) rows.push(el('p', { className: 'empty', textContent: 'No preset matches that search.' }));
  const keep = box.scrollTop;
  box.replaceChildren(...rows);
  box.scrollTop = keep;
  $('preset-name').textContent = chosen ? chosen.name : 'No preset chosen';
  $('about').textContent = chosen ? chosen.about : '';
  $('mine-tools').hidden = !(chosen && chosen.mine);
  if (chosen && chosen.mine) $('download-preset').href = `/api/presets/${encodeURIComponent(chosen.id)}/xmp`;
  const maskBox = $('preset-masks');
  maskBox.hidden = !(chosen && (chosen.masks.length || (chosen.own_masks || []).length));
  if (!maskBox.hidden && !chosen.masks.length) {           // a preset made here: only its own mask changes
    $('use-preset-masks').checked = look.preset_masks;
    $('preset-mask-list').replaceChildren(...chosen.own_masks.map((k) => el('li', {}, [el('b', { textContent: KIND_NAMES[k] || k }), ': your mask changes, saved in this preset'])));
    $('preset-mask-note').textContent = 'Shown here and in exports; Lightroom gets the rest of the preset.';
  } else if (!maskBox.hidden) {
    $('use-preset-masks').checked = look.preset_masks;
    let hiddenParts = 0;
    $('preset-mask-list').replaceChildren(...chosen.masks.map((m) => {
      const where = m.parts.map((k) => KIND_NAMES[k] || k.replace(/_/g, ' ')).join(' + ');
      const what = Object.entries(m.adjust).map(([k, v]) => `${ADJ_NAMES[k] || k} ${signed(v, k === 'ev' ? 2 : 0)}`);
      if (m.curve) what.push('tone curve');
      if (!m.shown) hiddenParts += 1;
      return el('li', { className: m.shown ? '' : 'off' }, [el('b', { textContent: where }), `: ${what.join(', ') || 'no change'}${m.shown ? '' : ' (Lightroom only)'}`]);
    }));
    const soft = chosen.masks.some((m) => Object.keys(m.adjust).some((k) => NOT_PREVIEWED.has(k)));
    $('preset-mask-note').textContent = [
      hiddenParts ? `${hiddenParts} of these the app cannot find in a photo itself; Lightroom still applies them.` : '',
      soft ? 'Sharpness, noise and dehaze inside masks are applied in Lightroom, not shown here.' : '',
    ].filter(Boolean).join(' ');
  }
}

function stepPreset(by) {
  const flat = shownLooks().flatMap((l) => l.variants);
  if (!flat.length) return;
  const at = flat.findIndex((v) => v.id === panelLook().preset);
  const next = flat[Math.min(flat.length - 1, Math.max(0, at + by))] || flat[0];
  S.openLook = lookKey(next);
  S.openPacks.add(next.pack);
  change({ preset: next.id });
  const b = $('looks').querySelector(`[data-preset="${CSS.escape(next.id)}"]`);
  if (b) b.scrollIntoView({ block: 'nearest' });
}

// What a slider starts from: the preset's value, or Lightroom's default. null means the value is
// worked out per photo (Auto is ticked) or is relative (white balance), so only your change is shown.
function sliderBase(look, sl) {
  if (sl.relative || (sl.toggle && look.toggles.includes(sl.toggle))) return null;
  const preset = S.byPreset.get(look.preset);
  const own = preset && preset.s ? preset.s[sl.key] : undefined;
  return typeof own === 'number' ? own : (sl.neutral || 0);
}
const sliderDelta = (look, sl) => (sl.field ? look[sl.field] : (look.adj || {})[sl.key]) || 0;
function setDelta(sl, delta, live = false) {
  const d = Math.round(delta * 1000) / 1000;
  if (sl.field) change({ [sl.field]: d }, live);
  else change({ adj: { ...(panelLook().adj || {}), [sl.key]: d } }, live);
}
// Click a slider's number to type the exact value.
function typeValue(sl) {
  const base = sliderBase(panelLook(), sl);
  const box = el('input', { type: 'text', className: 'exact', inputMode: 'decimal', value: sl.input.value });
  box.setAttribute('aria-label', `${sl.label}, exact value`);
  let done = false;
  const finish = (keep) => {
    if (done) return; done = true;
    const v = Number(box.value.replace('−', '-').replace(',', '.'));
    box.replaceWith(sl.out);
    if (keep && box.value.trim() !== '' && Number.isFinite(v)) {
      const fits = Math.min(Number(sl.input.max), Math.max(Number(sl.input.min), v));
      setDelta(sl, fits - (base === null ? 0 : base));
    }
    sl.out.focus();
  };
  box.onkeydown = (e) => { e.stopPropagation(); if (e.key === 'Enter' || e.key === 'Escape') { e.preventDefault(); finish(e.key === 'Enter'); } };
  box.onblur = () => finish(true);
  sl.out.replaceWith(box);
  box.select();
}
const digitsOf = (sl) => { const st = Number((sl.input && sl.input.step) || sl.step); return st < 0.1 ? 2 : st < 1 ? 1 : 0; };

function buildPanels() {
  $('panels').replaceChildren(...S.panels.map((panel) => {
    const box = el('details', { className: 'panel', open: !!panel.open });
    panel.count = el('small');
    box.append(el('summary', {}, [panel.name, panel.count]));
    for (const group of panel.groups) {
      if (group.name) box.append(el('h3', { textContent: group.name }));
      for (const sl of group.sliders) {
        const row = el('div', { className: `adj${sl.switch ? ' switch' : ''}` });
        if (sl.toggle) {
          // In "Preset", ticking one moves to "Preset + auto". In "Auto Correct" all are on and fixed.
          sl.box = el('input', { type: 'checkbox' });
          sl.box.setAttribute('aria-label', sl.switch ? sl.label : `Auto ${sl.label.toLowerCase()}`);
          sl.box.onchange = () => {
            const now = new Set(panelLook().toggles);
            if (sl.box.checked) now.add(sl.toggle); else now.delete(sl.toggle);
            change({ mode: 'preset_auto', toggles: [...now] });
          };
          row.append(el('label', { className: 'auto' }, [sl.box, sl.switch ? 'On' : 'Auto']));
        }
        row.append(el('span', { className: `name${sl.shown ? '' : ' later'}${sl.switch ? ' wide' : ''}`, textContent: sl.label }));
        if (!sl.switch) {
          sl.input = el('input', { type: 'range', step: sl.step });
          sl.input.dataset.step0 = String(sl.step);
          if (S.precise) sl.input.step = String(sl.step < 1 ? sl.step / 5 : sl.relative ? 0.1 : sl.step);
          sl.input.setAttribute('aria-label', `${panel.name}, ${group.name ? group.name + ', ' : ''}${sl.label}`);
          sl.input.oninput = () => {
            const base = sliderBase(panelLook(), sl);
            setDelta(sl, Number(sl.input.value) - (base === null ? 0 : base), true);
          };
          sl.input.onchange = () => { stageLive = false; drawPanel(); drawStage(); };   // let go: redraw the rest
          sl.input.ondblclick = () => setDelta(sl, 0);          // back to the preset's own value
          sl.out = el('button', { type: 'button', className: 'value', title: 'Click to type a value' });
          sl.out.onclick = () => typeValue(sl);
          row.append(sl.out, sl.input);
        }
        box.append(row);
      }
    }
    return box;
  }));
}

function drawSliders(look) {
  for (const panel of S.panels) {
    let changed = 0;
    for (const group of panel.groups) {
      for (const sl of group.sliders) {
        if (sl.box) {
          sl.box.checked = look.toggles.includes(sl.toggle);
          sl.box.disabled = look.mode === 'auto';
          if (sl.box.checked) changed += 1;
        }
        if (!sl.input) continue;
        const base = sliderBase(look, sl);
        const delta = sliderDelta(look, sl);
        if (delta) changed += 1;
        const half = (sl.max - sl.min) / 2;
        const lo = base === null ? (sl.min < 0 ? sl.min : -half) : sl.min;
        const hi = base === null ? (sl.min < 0 ? sl.max : half) : sl.max;
        let value = base === null ? delta : base + delta;
        if (sl.hue && base !== null) value = ((value % 360) + 360) % 360;
        value = Math.min(hi, Math.max(lo, value));
        sl.input.min = lo; sl.input.max = hi;
        if (Number(sl.input.value) !== value) sl.input.value = value;
        paint(sl.input, base === null ? 0 : base);
        const d = digitsOf(sl);
        sl.out.textContent = base === null ? (sl.relative ? signed(value, d) : `auto ${signed(value, d)}`)
          : (sl.min < 0 ? signed(value, d) : value.toFixed(d));
        sl.out.classList.toggle('moved', !!delta);
      }
    }
    panel.count.textContent = changed ? `${changed}` : '';
  }
}

function maskValue(kind, key) {
  return ((panelLook().masks || {})[kind] || {})[key] || 0;
}
function changeMask(kind, patch, live = false) {
  const masks = { ...(panelLook().masks || {}) };
  masks[kind] = patch === null ? {} : { ...(masks[kind] || {}), ...patch };
  change({ masks }, live);
}

function drawMasks() {
  const look = panelLook();
  const loupe = S.view === 'loupe' && !!photo();
  const rows = [];
  for (const [kind, label, note] of MASKS) {
    const count = Object.keys((look.masks || {})[kind] || {}).length;
    const open = S.openMask === kind;
    const head = el('button', { type: 'button', className: 'part' }, [label]);
    if (note) head.append(el('small', { textContent: note }));
    if (count) head.append(el('span', { className: 'changed', textContent: `${count} change${count === 1 ? '' : 's'}` }));
    head.setAttribute('aria-expanded', String(open));
    head.onclick = () => { S.openMask = open ? null : kind; drawMasks(); if (S.showMask) drawStage(); };
    rows.push(head);
    if (!open) continue;
    const body = el('div', { className: 'body' });
    for (const sl of MASK_SLIDERS) {
      const value = maskValue(kind, sl.key);
      const input = el('input', { type: 'range', min: sl.min ?? -100, max: sl.max ?? 100, step: sl.step ?? 1, value });
      const out = el('output', { textContent: signed(value, sl.digits || 0) });
      input.dataset.mask = `${kind}.${sl.key}`;
      input.dataset.step0 = String(sl.step ?? 1);
      if (S.precise && (sl.step ?? 1) < 1) input.step = String((sl.step ?? 1) / 5);
      paint(input, 0);
      input.oninput = () => { paint(input, 0); out.textContent = signed(Number(input.value), sl.digits || 0); changeMask(kind, { [sl.key]: Number(input.value) }, true); };
      input.onchange = () => { stageLive = false; drawPanel(); drawStage(); };
      input.ondblclick = () => { changeMask(kind, { [sl.key]: 0 }); drawMasks(); };
      input.setAttribute('aria-label', `${label} mask, ${sl.label}`);
      body.append(el('div', { className: 'adj' }, [el('span', { className: 'name', textContent: sl.label }), input, out]));
    }
    const show = el('input', { type: 'checkbox', checked: S.showMask, disabled: !loupe });
    show.onchange = () => { S.showMask = show.checked; drawStage(); };
    const clear = el('button', { type: 'button', className: 'quiet', textContent: 'Clear this mask' });
    clear.onclick = () => { changeMask(kind, null); drawMasks(); };
    body.append(el('div', { className: 'tools' }, [
      el('label', { className: 'show' }, [show, loupe ? 'Show it in red' : 'Open one photo to see it in red']), clear]));
    rows.push(body);
  }
  $('masks').replaceChildren(...rows);
  $('mask-tool').dataset.n = (MASKS.reduce((n, [kind]) => n + Object.keys((look.masks || {})[kind] || {}).length, 0) + (look.shapes || []).length) || '';
  if (window.drawDrawn) drawDrawn();
}

// Colours a slider's track between where it starts (the preset's value, or zero) and the thumb,
// so a changed slider is visible at a glance.
function paint(input, from) {
  const lo = Number(input.min), hi = Number(input.max), span = hi - lo || 1;
  input.style.setProperty('--p', Math.min(1, Math.max(0, (Number(input.value) - lo) / span)));
  input.style.setProperty('--z', Math.min(1, Math.max(0, (from - lo) / span)));
}
function setSlider(id, value, text) {
  const input = $(id);
  if (document.activeElement !== input) input.value = value;
  input.nextElementSibling.textContent = text;
  paint(input, id === 'amount' ? 1 : 0);
}
const signed = (v, digits = 0) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(digits);

let autoTurn = 0;
async function drawAutoValues(look) {
  const out = $('auto-values');
  const p = photo();
  const turn = ++autoTurn;
  if (!p || S.view !== 'loupe' || !look.toggles.length) {
    out.textContent = '* Applied in Lightroom, not shown in this preview.';
    return;
  }
  try {
    await keyLook(lookOf(p));
    const d = await api(`${base(p)}/develop?${lookParam(lookOf(p))}`);
    if (turn !== autoTurn) return;
    const names = { Exposure2012: 'exposure', Highlights2012: 'highlights', Shadows2012: 'shadows', Whites2012: 'whites', Blacks2012: 'blacks', CropAngle: 'straighten' };
    const bits = Object.entries(names).filter(([k]) => k in d.auto)
      .map(([k, n]) => `${n} ${signed(d.auto[k], k === 'Exposure2012' ? 2 : k === 'CropAngle' ? 1 : 0)}`);
    if ('temp' in d.auto_wb) bits.push(`warmth ${signed(d.auto_wb.temp)}`, `tint ${signed(d.auto_wb.tint)}`);
    out.textContent = (bits.length ? `Auto chose for this photo: ${bits.join(', ')}. ` : '') +
      '* Applied in Lightroom, not shown in this preview.';
  } catch (e) { /* the photo itself will report the problem */ }
}

function drawPanel() {
  const f = folder();
  const p = photo();
  const loupe = S.view === 'loupe' && !!p;
  $('panel').style.visibility = f ? 'visible' : 'hidden';
  $('preset-box').hidden = !f;
  if (!f) return;
  $('scope-photo').disabled = !p;
  $('scope-folder').setAttribute('aria-pressed', String(!editingPhoto()));
  $('scope-photo').setAttribute('aria-pressed', String(editingPhoto()));
  const note = $('scope-note');
  note.replaceChildren();
  if (editingPhoto() && p.look) {
    const back = el('button', { type: 'button', className: 'quiet', textContent: 'Use the folder’s settings again' });
    back.onclick = async () => {
      p.look = null;
      drawPanel(); drawStage(); refreshThumbs();
      queueSave(`p${p.id}`, () => api(`${base(p)}/look`, { method: 'PUT', body: null }));
    };
    note.append('This photo has its own settings. ', back);
  } else if (editingPhoto()) {
    const from = S.byPreset.get(folderLook(f).preset);
    note.textContent = `Not edited yet: the sliders show ${from ? `the folder’s preset (${from.name})` : 'the folder’s settings'}. Change anything and only this photo gets it.`;
  } else if (loupe && p.look) {
    note.textContent = `Settings for every photo in “${f.name}”. The photo on screen has its own settings, so it will not change.`;
  } else {
    note.textContent = `Settings for every photo in “${f.name}”.`;
  }

  $('to-original').textContent = editingPhoto() ? 'Photo to original' : 'Folder to original';
  $('to-original').title = editingPhoto() ? 'Reset this photo to how it was uploaded' : `Reset every photo in “${f.name}” to how it was uploaded`;
  const look = panelLook();
  for (const b of document.querySelectorAll('[data-mode]')) b.setAttribute('aria-pressed', String(b.dataset.mode === look.mode));
  $('mode-note').textContent = MODE_NOTES[look.mode];
  $('preset-box').toggleAttribute('data-off', look.mode === 'auto');
  drawLooks();

  drawSliders(look);
  setSlider('amount', look.amount, `${Math.round(look.amount * 100)}%`);
  if (!(document.activeElement && document.activeElement.dataset && document.activeElement.dataset.mask)) drawMasks();
  drawAutoValues(look);
}

// ---- moving around --------------------------------------------------------------
function openFolder(id) {
  S.folderId = id;
  S.photoId = (inFolder()[0] || {}).id || null;
  S.scope = S.developScope;
  S.openLook = null;
  S.rating = null;
  if (window.drawRatings) drawRatings();
  drawFolders(); drawGrid(); drawPanel(); drawStage();
  markCurrent();
}

function openPhoto(id) {
  if (S.cropping && window.endCrop) endCrop(false);
  S.photoId = id;
  S.view = 'loupe';
  S.scope = (photo() && photo().look) || S.developScope !== 'folder' ? 'photo' : 'folder';   // this photo, as in Lightroom
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
  if (S.cropping && window.endCrop) endCrop(false);
  if (view === 'loupe') return openPhoto(S.photoId);
  S.view = 'grid';
  S.scope = S.developScope;
  drawPanel(); drawStage();
}

async function openWedding(id) {
  S.wedding = await api(`/api/weddings/${id}`);
  S.photos = (await api(`/api/weddings/${id}/photos?compact=1`)).photos;
  $('wedding').value = id;
  $('count').textContent = `${S.wedding.photos} photo${S.wedding.photos === 1 ? '' : 's'}`;
  $('menu-wedding-title').textContent = `This wedding · ${S.wedding.name}`;
  for (const id of ['menu-wedding-title', 'originals', 'delete-wedding']) $(id).hidden = false;
  S.view = 'grid';
  drawFolders();
  openFolder((S.wedding.folders[0] || {}).id || null);
  if (window.refreshCull) refreshCull();
}

function showSignIn() {
  location.href = '/?login=1';
}

async function start() {
  let session;
  try { session = await api('/api/session'); } catch (e) { return; }
  $('app').hidden = false;
  if (window.showAccount) showAccount(session);
  const [presets, weddings, panels] = await Promise.all([api('/api/presets'), api('/api/weddings'), api('/api/adjustments')]);
  S.panels = panels.panels;
  buildPanels();
  S.presets = presets.presets;
  S.byPreset = new Map(S.presets.map((p) => [p.id, p]));
  buildLooks();
  S.weddings = weddings.weddings;
  $('wedding').replaceChildren(...S.weddings.map((w) => el('option', { value: w.id, textContent: w.name })));
  $('wedding').hidden = !S.weddings.length;
  if (!S.weddings.length) {
    for (const id of ['menu-wedding-title', 'originals', 'delete-wedding']) $(id).hidden = true;
    S.wedding = null;
    $('folders').replaceChildren();
    $('count').textContent = '';
    $('panel').style.visibility = 'hidden';
    $('preset-box').hidden = true;
    $('toolbar').dataset.view = 'grid';
    drawFilmInfo(); drawNavigator();
    $('empty').replaceChildren(el('strong', { textContent: 'No photos here yet' }),
      'Click “Add photos…” at the bottom left to pick a folder from this computer. Or, in Lightroom, choose Library > Plug-in Extras > “Auto Edits: send photos to the server”.');
    $('empty').hidden = false;
    $('grid').hidden = $('loupe').hidden = true;
    return;
  }
  $('empty').replaceChildren(el('strong', { textContent: 'This folder is empty' }), 'Add photos to it with “Add photos…” at the bottom left, or send them from Lightroom.');
  // /?wedding=<id> opens that wedding (from the admin page); /?account=1 opens the account window
  const asked = new URLSearchParams(location.search);
  const want = asked.get('wedding');
  await openWedding(S.weddings.some((w) => w.id === want) ? want : S.weddings[0].id);
  if (asked.has('account') && typeof openAccount === 'function') openAccount();
  if (want || asked.has('account')) history.replaceState(null, '', '/');
}

// ---- wiring ---------------------------------------------------------------------
$('signout').onclick = async () => { await api('/api/logout', { method: 'POST' }); location.href = '/'; };
$('wedding').onchange = (e) => openWedding(e.target.value);
$('view-grid').onclick = () => setView('grid');
$('view-loupe').onclick = () => setView('loupe');
$('split').onclick = () => { S.split = !S.split; placeDivider(); };
// Apply to: This photo by default (in Library the selected photo); the choice is remembered from
// photo to photo, view to view, and in this browser.
function setDevelopScope(scope) {
  S.scope = scope;
  S.developScope = scope;
  try { localStorage.setItem('ae-scope', scope); } catch (e) { /* fine without */ }
  drawPanel();
}
$('scope-folder').onclick = () => setDevelopScope('folder');
$('scope-photo').onclick = () => setDevelopScope('photo');
try { S.developScope = localStorage.getItem('ae-scope') || 'photo'; } catch (e) { S.developScope = 'photo'; }
for (const b of document.querySelectorAll('[data-mode]')) {
  b.onclick = () => change({ mode: b.dataset.mode, toggles: b.dataset.mode === 'preset_auto' ? S.lastToggles : [] });
}
$('use-preset-masks').onchange = (e) => change({ preset_masks: e.target.checked });

async function loadPresets() {
  S.presets = (await api('/api/presets')).presets;
  S.byPreset = new Map(S.presets.map((p) => [p.id, p]));
  buildLooks();
}

$('upload').onchange = async (e) => {
  const files = [...e.target.files];
  if (!files.length) return;
  const note = $('upload-note');
  note.textContent = `Adding ${files.length} preset${files.length === 1 ? '' : 's'}…`;
  const form = new FormData();
  for (const f of files) form.append('files', f);
  try {
    const r = await fetch('/api/presets', { method: 'POST', body: form, credentials: 'same-origin', headers: { 'X-Requested-With': 'AutoEdits' } });
    if (r.status === 401) return showSignIn();
    const data = await r.json();
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `The server answered ${r.status}.`);
    const good = data.results.filter((x) => x.ok), bad = data.results.filter((x) => !x.ok);
    await loadPresets();
    S.openPacks.add('My presets'); S.query = ''; $('search').value = '';
    const withMasks = good.filter((x) => x.masks).length;
    note.textContent = `Added ${good.length} preset${good.length === 1 ? '' : 's'}` +
      (withMasks ? `, ${withMasks} with masks` : '') + '. They are in the “My presets” group.' +
      (bad.length ? ` Not added: ${bad.map((x) => `${x.file} (${x.why})`).join('; ')}` : '');
    if (good.length === 1 && folder()) { S.openLook = null; change({ preset: good[0].id, mode: panelLook().mode === 'auto' ? 'preset' : panelLook().mode }); }
    else drawLooks();
  } catch (err) {
    note.textContent = `The presets were not added. ${err.message}`;
  }
  e.target.value = '';
};
$('remove-preset').onclick = async () => {
  const id = panelLook().preset;
  const p = S.byPreset.get(id);
  if (!p || !p.mine) return;
  try {
    await api(`/api/presets/${encodeURIComponent(id)}`, { method: 'DELETE' });
    $('upload-note').textContent = `Removed “${p.name}” from the app. It is still in your Lightroom.`;
    change({ preset: null });
    await loadPresets();
    drawLooks(); drawFolders();
  } catch (err) { if (err.message !== 'signed out') $('upload-note').textContent = err.message; }
};
$('search').oninput = (e) => { S.query = e.target.value; drawLooks(); };
$('looks').onkeydown = (e) => {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); stepPreset(e.key === 'ArrowDown' ? 1 : -1); $('looks').focus(); }
};
for (const [id, key] of [['amount', 'amount']]) {
  $(id).oninput = (e) => { paint(e.target, 1); change({ [key]: Number(e.target.value) }, true); $(id).nextElementSibling.textContent = `${Math.round(e.target.value * 100)}%`; };
  $(id).onchange = () => { stageLive = false; drawPanel(); drawStage(); };
  $(id).ondblclick = () => change({ [key]: key === 'amount' ? 1 : 0 });
}
$('reset').onclick = () => change(blankLook());
// The right panel shows one tool at a time, as in Lightroom: the sliders, Crop or Masks.
function setTool(tool) {
  if (S.tool === 'masks' && tool !== 'masks' && S.showMask) { S.showMask = false; drawStage(); }
  S.tool = tool;
  $('panel').dataset.tool = tool;
  $('mask-tool').setAttribute('aria-pressed', String(tool === 'masks'));
  $('crop-tool').setAttribute('aria-pressed', String(tool === 'crop'));
  $('panel-body').scrollTop = 0;
}
$('mask-tool').onclick = () => setTool(S.tool === 'masks' ? 'adjust' : 'masks');
$('mask-back').onclick = () => setTool('adjust');

// Rating buttons under the photo: the same as keys 3, 2 and 1; clicking your own rating again
// puts it back to the automatic one.
for (const b of document.querySelectorAll('#toolbar .picks button')) {
  b.onclick = () => {
    const p = photo();
    if (!p) return;
    const again = p.pick === b.dataset.pick;
    Cull.key(again ? '0' : { keep: '3', maybe: '2', reject: '1' }[b.dataset.pick]);
  };
}

// Thumbnail size in Library, remembered in this browser.
function setCellSize(px) {
  $('grid').style.setProperty('--cell', `${px}px`);
  $('cell-size').value = px;
}
try { const kept = Number(localStorage.getItem('ae-cell')); if (kept) setCellSize(kept); } catch (e) { /* no storage: default size */ }
$('cell-size').oninput = (e) => {
  setCellSize(Number(e.target.value));
  try { localStorage.setItem('ae-cell', e.target.value); } catch (err) { /* fine without */ }
};

// Folding the side panels away (F7 left, F8 right; Lightroom's own keys), remembered too.
function sides(which, hide) {
  const app = $('app');
  app.classList.toggle(`no-${which}`, hide);
  try { localStorage.setItem(`ae-no-${which}`, hide ? '1' : ''); } catch (e) { /* fine without */ }
  requestAnimationFrame(placeDivider);
}
for (const which of ['left', 'right']) {
  try { if (localStorage.getItem(`ae-no-${which}`)) $('app').classList.add(`no-${which}`); } catch (e) { /* fine without */ }
  $(`hide-${which}`).onclick = () => sides(which, !$('app').classList.contains(`no-${which}`));
}

$('delete-wedding').onclick = () => {
  if (!S.wedding) return;
  $('more').open = false;
  $('confirm-text').textContent = `Delete “${S.wedding.name}” and its ${S.wedding.photos} photos from the server? ` +
    'Your Lightroom catalogue and the original files on your computer are not touched.';
  $('confirm').showModal();
};
// Reset to original: this photo (its own settings set to nothing, crop removed) or the whole folder
// (folder look cleared, every photo's own settings and crops removed). Only settings: photos never change.
$('to-original').onclick = () => {
  const p = photo(), f = folder();
  if (!f) return;
  const one = editingPhoto();
  $('original-title').textContent = one ? 'Reset this photo to original?' : `Reset all of “${f.name}” to original?`;
  $('original-text').textContent = one
    ? `“${p.name}” goes back to how it was uploaded: no preset, no adjustments, no masks and no crop. The rest of the folder is not changed.`
    : `All ${f.photos} photos in “${f.name}” go back to how they were uploaded: the folder's preset and adjustments, every photo's own settings and every crop are removed. This cannot be undone.`;
  $('original-dialog').showModal();
};
$('original-no').onclick = () => $('original-dialog').close();
$('original-yes').onclick = async () => {
  $('original-dialog').close();
  const f = folder();
  try {
    if (editingPhoto()) {
      const p = photo();
      p.look = tidy(blankLook());
      await api(`${base(p)}/look`, { method: 'PUT', body: p.look });
      await api(`${base(p)}/crop`, { method: 'PUT', body: null });
      p.crop = null;
    } else {
      await api(`/api/weddings/${S.wedding.id}/folders/${f.id}/reset`, { method: 'POST' });
      f.settings = {};
      for (const p of wholeFolder()) { p.look = null; p.crop = null; }
    }
    $('saved').textContent = 'Reset to original';
    refreshThumbs(); drawFolders(); drawPanel(); drawCaption(); drawStage();
  } catch (e) { if (e.message !== 'signed out') $('saved').textContent = e.message; }
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
  if ((e.key === 'F7' || e.key === 'F8') && !document.querySelector('dialog[open]')) {
    e.preventDefault(); const w = e.key === 'F7' ? 'left' : 'right'; sides(w, !$('app').classList.contains(`no-${w}`)); return;
  }
  if (typing || t.matches('input[type=range]') || e.metaKey || e.ctrlKey || e.altKey) return;
  if (window.cropKey && cropKey(e)) { e.preventDefault(); return; }
  if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
  else if (!typing && typeof Cull !== 'undefined' && Cull.key(e.key)) e.preventDefault();
  else if (e.key === 'g' || e.key === 'G' || e.key === 'Escape') setView('grid');
  else if (e.key === 'd' || e.key === 'D' || e.key === 'e' || e.key === 'E') setView('loupe');
  else if ((e.key === 'y' || e.key === 'Y') && S.view === 'loupe') { S.split = !S.split; placeDivider(); }
  else if (e.key === 'm' || e.key === 'M') setTool(S.tool === 'masks' ? 'adjust' : 'masks');
  else if (e.key === 'Enter' && S.view === 'grid' && !t.matches('button, summary')) setView('loupe');
});
document.addEventListener('keyup', (e) => { if (e.key === '\\' && S.holdBefore) { S.holdBefore = false; placeDivider(); } });

// The strip of small photos: a mouse wheel moves it sideways.
$('strip').addEventListener('wheel', (e) => {
  if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;         // a sideways swipe already scrolls it
  e.preventDefault();
  $('strip').scrollLeft += e.deltaY;
}, { passive: false });

// Every window: a cross in the top corner, and a click on the dark area outside closes it too.
for (const d of document.querySelectorAll('dialog')) {
  const x = el('button', { type: 'button', className: 'x', textContent: '×', title: 'Close (Esc)' });
  x.setAttribute('aria-label', 'Close');
  x.onclick = () => { const own = d.querySelector('[id$=-close], #confirm-no'); if (own) own.click(); else d.close(); };
  d.prepend(x);
  d.addEventListener('mousedown', (e) => { if (e.target === d) x.click(); });
}



// ---- Create preset (Lightroom's "Create Preset…") ---------------------------------------------
// The settings on screen become a preset of your own: the preset in use with your changes laid over it.
// Afterwards the folder (or photo) uses the new preset with those changes cleared, so nothing on screen moves.
function presetMenu(open) {
  $('preset-menu').hidden = !open;
  $('preset-plus').setAttribute('aria-expanded', String(open));
}
$('preset-plus').onclick = (e) => { e.preventDefault(); e.stopPropagation(); $('preset-box').open = true; presetMenu($('preset-menu').hidden); };
document.addEventListener('click', (e) => { if (!e.target.closest('#preset-menu, #preset-plus')) presetMenu(false); });
$('upload').addEventListener('click', () => presetMenu(false));

function lookHasChanges(look) {
  return ['ev', 'temp', 'tint', 'hl', 'sh', 'wh', 'bl', 'noise'].some((k) => look[k]) || Object.keys(look.adj || {}).length ||
    Object.keys(look.masks || {}).length || look.amount !== 1;
}
let presetReplace = null;
function openPresetDialog(replace) {
  presetMenu(false);
  if (!folder()) return;
  const look = panelLook();
  const chosen = S.byPreset.get(look.preset);
  presetReplace = replace || null;
  $('preset-dialog-title').textContent = replace ? 'Update preset' : 'Create preset';
  $('preset-new-save').textContent = replace ? 'Update' : 'Create';
  const groups = [...new Set(S.presets.filter((p) => p.mine && p.look && p.look !== p.name).map((p) => p.look))];
  $('preset-groups').replaceChildren(...groups.map((g) => el('option', { value: g })));
  if (replace) {
    $('preset-new-name').value = replace.name;
    $('preset-new-group').value = replace.look !== replace.name ? replace.look : '';
  } else {
    $('preset-new-name').value = chosen ? `${chosen.name} (mine)` : 'My preset';
    $('preset-new-group').value = '';
  }
  const notes = [];
  if (look.toggles.length) notes.push('Auto ticks are not saved in a preset: they are worked out for each photo, so keep them on the folder.');
  if (look.amount !== 1) notes.push(`The strength (${Math.round(look.amount * 100)}%) is built into the slider values.`);
  if (!chosen && !lookHasChanges(look)) notes.push('Nothing is changed yet, so this preset would do nothing.');
  $('preset-new-note').textContent = notes.join(' ');
  $('preset-new-masks').closest('label').hidden = !Object.keys(look.masks || {}).length && !(chosen && (chosen.own_masks || []).length);
  $('preset-new-wb').closest('label').hidden = !look.temp && !look.tint && !(chosen && chosen.wb);
  $('preset-new-error').textContent = '';
  $('preset-dialog').showModal();
  $('preset-new-name').select();
}
$('preset-create').onclick = () => openPresetDialog(null);
$('update-preset').onclick = () => { const p = S.byPreset.get(panelLook().preset); if (p && p.mine) openPresetDialog(p); };
$('preset-new-close').onclick = () => $('preset-dialog').close();
$('preset-form').onsubmit = async (e) => {
  e.preventDefault();
  const look = panelLook();
  const masks = $('preset-new-masks').checked, wb = $('preset-new-wb').checked;
  $('preset-new-save').disabled = true;
  try {
    const r = await api('/api/presets/new', { method: 'POST', body: {
      name: $('preset-new-name').value, group: $('preset-new-group').value, look, masks, white_balance: wb,
      replace: presetReplace ? presetReplace.id : undefined } });
    $('preset-dialog').close();
    await loadPresets();
    const made = S.byPreset.get(r.id);
    S.openPacks.add('My presets');
    S.openLook = made ? lookKey(made) : null;
    const patch = { preset: r.id, amount: 1, ev: 0, hl: 0, sh: 0, wh: 0, bl: 0, noise: 0, adj: {} };
    if (masks) patch.masks = {};
    if (wb) { patch.temp = 0; patch.tint = 0; }
    if (look.mode === 'auto') patch.mode = 'preset';
    change(patch);
    $('upload-note').textContent = `${r.replaced ? 'Updated' : 'Saved'} “${r.name}” in My presets. It is now in use here.`;
  } catch (err) {
    if (err.message !== 'signed out') $('preset-new-error').textContent = err.message;
  }
  $('preset-new-save').disabled = false;
};

// ---- Precision -----------------------------------------------------------------------------------
// One switch for every slider (and Shift while dragging, just for that drag): the slider moves at a
// fifth of the hand's speed, so every value can be landed on. Sliders whose value Lightroom keeps
// in decimals (Exposure, Strength, Radius) and the relative Temp and Tint also take finer steps.
const FINE_SPEED = 0.2;
function fineStep(input) {
  const own = Number(input.dataset.step0 || input.step || 1);
  if (own < 1) return own / 5;                                   // 0.05 → 0.01, 0.1 → 0.02
  const sl = allSliders().find((x) => x.input === input);
  return sl && sl.relative ? 0.1 : own;                          // Temp and Tint: tenths
}
function setPrecision(on) {
  S.precise = on;
  $('app').classList.toggle('precise', on);
  $('precision').setAttribute('aria-pressed', String(on));
  for (const input of document.querySelectorAll('#app input[type=range]')) {
    if (input.id === 'cell-size') continue;
    if (!input.dataset.step0) input.dataset.step0 = input.step || '1';
    input.step = on ? String(fineStep(input)) : input.dataset.step0;
  }
  try { localStorage.setItem('ae-precise', on ? '1' : ''); } catch (e) { /* fine without */ }
  if (folder()) drawSliders(panelLook());
}
$('precision').onclick = () => setPrecision(!S.precise);

// The slow drag: the slider no longer jumps to where it is clicked; it follows the hand, slowed down.
document.addEventListener('pointerdown', (e) => {
  const input = e.target;
  if (!(input instanceof HTMLInputElement) || input.type !== 'range' || input.id === 'cell-size' || e.button !== 0) return;
  if (!S.precise && !e.shiftKey) return;
  e.preventDefault();
  input.focus();
  input.setPointerCapture(e.pointerId);
  input.classList.add('fine-drag');
  const lo = Number(input.min), hi = Number(input.max), width = input.getBoundingClientRect().width || 1;
  const step = S.precise ? Number(input.step) : fineStep(input);
  const start = Number(input.value), x0 = e.clientX;
  let moved = false;
  const move = (m) => {
    const raw = start + (m.clientX - x0) / width * (hi - lo) * FINE_SPEED;
    const v = Math.min(hi, Math.max(lo, Math.round(raw / step) * step));
    const text = String(Math.round(v * 1000) / 1000);
    if (input.value === text) return;
    if (!S.precise) input.step = String(step);                     // Shift alone: the fine step for this drag
    input.value = text;
    moved = true;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  };
  const up = () => {
    input.removeEventListener('pointermove', move);
    input.removeEventListener('pointerup', up);
    input.removeEventListener('pointercancel', up);
    input.classList.remove('fine-drag');
    if (moved) input.dispatchEvent(new Event('change', { bubbles: true }));
    if (!S.precise && input.dataset.step0) input.step = input.dataset.step0;
  };
  input.addEventListener('pointermove', move);
  input.addEventListener('pointerup', up);
  input.addEventListener('pointercancel', up);
}, true);
try { if (localStorage.getItem('ae-precise')) S.precise = true; } catch (e) { /* fine without */ }

// ---- Rotate (this photo only) and Straighten (a look setting, like every slider) ----------------
async function turnPhoto(by) {
  const p = photo();
  if (!p) return;
  if (S.cropping && window.endCrop) await endCrop(true);   // finish the crop, then turn
  try {
    const r = await api(`${base(p)}/turn`, { method: 'PUT', body: { by } });
    p.crop = r.crop;
    $('saved').textContent = 'Saved';
    refreshThumbs(); drawCaption(); drawStage(); drawNavigator();
  } catch (e) { if (e.message !== 'signed out') $('saved').textContent = e.message; }
}
for (const b of document.querySelectorAll('[data-turn]')) b.onclick = () => turnPhoto(Number(b.dataset.turn));
document.addEventListener('keydown', (e) => {
  if (!(e.metaKey || e.ctrlKey) || e.altKey || e.target.matches('input, select, textarea')) return;
  if (e.key === '[' || e.key === ']') { e.preventDefault(); turnPhoto(e.key === ']' ? 90 : -90); }
});
function drawStraighten() {
  const look = panelLook();
  const v = Number(look.angle || 0);
  const input = $('straighten');
  if (document.activeElement !== input) input.value = v;
  $('straighten-value').textContent = `${v > 0 ? '+' : ''}${v.toFixed(1)}°`;
  paint(input, 0);
}
$('straighten').oninput = (e) => { paint(e.target, 0); $('straighten-value').textContent = `${Number(e.target.value).toFixed(1)}°`; change({ angle: Number(e.target.value) }, true); };
$('straighten').onchange = () => { stageLive = false; drawPanel(); drawStage(); };
$('straighten').ondblclick = () => change({ angle: 0 });
const _drawPanelBeforeTurn = drawPanel;
drawPanel = function () { _drawPanelBeforeTurn(); if (folder()) drawStraighten(); };   // eslint-disable-line no-global-assign

start().then(() => { if (S.precise) setPrecision(true); })
  .catch((e) => { if (e.message !== 'signed out') $('saved').textContent = e.message; });

