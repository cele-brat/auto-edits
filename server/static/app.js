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
  preset: 'Every photo gets the preset exactly as it is. Tick Auto on a setting below to have it worked out per photo instead.',
  preset_auto: 'Settings with Auto ticked are worked out for each photo. Everything else comes from the preset.',
  auto: 'No preset. Every setting below is worked out for each photo, so the Auto ticks are fixed.',
};

const S = {
  weddings: [], wedding: null, folderId: null, photos: [], presets: [], byPreset: new Map(), looks: [],
  photoId: null, view: 'grid', scope: 'folder', split: true, holdBefore: false, dividerAt: 0.5,
  rating: null, panels: [], pack: '', query: '', openLook: null, lastToggles: ['exposure', 'white_balance'], openMask: null, showMask: false,
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
const blankLook = () => ({ mode: 'preset', preset: null, amount: 1, toggles: [], ev: 0, temp: 0, tint: 0,
  hl: 0, sh: 0, wh: 0, bl: 0, noise: 0, angle: 0, masks: {}, preset_masks: true, adj: {} });
// Parts of the photo that can be changed on their own (Lightroom's own mask kinds).
const MASKS = [
  ['face_skin', 'Face skin'], ['body_skin', 'Body skin'], ['eyes', 'Eyes'], ['hair', 'Hair'], ['clothes', 'Clothes'],
  ['subject', 'People', 'the whole person'], ['background', 'Background', 'everything but the people'], ['sky', 'Sky', 'rough'],
];
const MASK_SLIDERS = [
  { key: 'ev', label: 'Exposure', min: -2, max: 2, step: 0.05, digits: 2 },
  { key: 'contrast', label: 'Contrast' }, { key: 'hl', label: 'Highlights' }, { key: 'sh', label: 'Shadows' },
  { key: 'texture', label: 'Texture' }, { key: 'clarity', label: 'Clarity' }, { key: 'sat', label: 'Saturation' },
  { key: 'temp', label: 'Warmth' }, { key: 'tint', label: 'Tint' },
];
// Names for the mask kinds and settings that can come inside a Lightroom preset.
const KIND_NAMES = { subject: 'People', whole_person: 'Whole person', background: 'Background', face_skin: 'Face skin',
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
  return l;
}
const folder = () => (S.wedding ? S.wedding.folders.find((f) => f.id === S.folderId) : null);
const photo = () => S.photos.find((p) => p.id === S.photoId) || null;
const folderLook = (f = folder()) => tidy(f && f.settings && f.settings.mode ? f.settings : {});
const lookOf = (p) => (p.look ? tidy(p.look) : folderLook(S.wedding.folders.find((f) => f.id === p.folder)));
const editingPhoto = () => S.scope === 'photo' && S.view === 'loupe' && !!photo();
const panelLook = () => (editingPhoto() ? lookOf(photo()) : folderLook());

function describe(look) {
  const masks = Object.keys(look.masks || {}).length ? ' + masks' : '';
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
const thumbUrl = (p) => `${base(p)}/image?size=360&k=${hash(JSON.stringify(lookOf(p)))}${p.v}`;
const afterUrl = (p, size) => `${base(p)}/image?size=${size}&v=${p.v}&look=${encodeURIComponent(JSON.stringify(lookOf(p)))}` +
  (S.showMask && S.openMask ? `&show=${S.openMask}` : '');
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

// live: a slider is being dragged. Only the numbers and the photo are redrawn (the photo at most
// every 80 ms), so the slider follows the hand; everything else is redrawn when it is let go.
let stageTimer = null;
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
  if (!live) { clearTimeout(stageTimer); stageTimer = null; drawPanel(); drawStage(); return; }
  drawSliders(look);
  if (!stageTimer) stageTimer = setTimeout(() => { stageTimer = null; drawStage(); }, 80);
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

// A photo's rating: the photographer's own if set, else the automatic one, else none.
const ratingOf = (p) => p.pick || (p.cull ? p.cull.r : null);
const wholeFolder = () => S.photos.filter((p) => p.folder === S.folderId);
const inFolder = () => wholeFolder().filter((p) => !S.rating || ratingOf(p) === S.rating);
const RATING_NAMES = { keep: 'Keep', maybe: 'Maybe', reject: 'Reject' };

function thumb(p, withName) {
  const b = el('button', { type: 'button', title: p.name });
  b.dataset.id = p.id;
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
  const r = ratingOf(p);
  if (r) {
    const why = p.pick ? 'set by you' : (p.cull.why.join(', ') || 'nothing wrong found');
    bits.push(el('span', { className: `rate ${r}`, textContent: RATING_NAMES[r] }), el('span', { textContent: why }));
  }
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
  const packs = [...new Set(S.presets.map((p) => p.pack))].sort((a, b) => (b === 'My presets') - (a === 'My presets'));
  S.looks.sort((a, b) => (b.pack === 'My presets') - (a.pack === 'My presets'));
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
    if (l.variants.some((v) => v.masks.length)) head.append(el('small', { className: 'has', textContent: 'with masks' }));
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
  $('remove-preset').hidden = !(chosen && chosen.mine);
  const maskBox = $('preset-masks');
  maskBox.hidden = !(chosen && chosen.masks.length);
  if (!maskBox.hidden) {
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
  S.openLook = `${next.pack}|${next.look || next.name}`;
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
const digitsOf = (sl) => (sl.step < 0.1 ? 2 : sl.step < 1 ? 1 : 0);

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
          sl.input.setAttribute('aria-label', `${panel.name}, ${group.name ? group.name + ', ' : ''}${sl.label}`);
          sl.input.oninput = () => {
            const base = sliderBase(panelLook(), sl);
            setDelta(sl, Number(sl.input.value) - (base === null ? 0 : base), true);
          };
          sl.input.onchange = () => { drawPanel(); drawStage(); };   // let go: redraw the rest
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
        const d = digitsOf(sl);
        sl.out.textContent = base === null ? (sl.relative ? signed(value, d) : `auto ${signed(value, d)}`)
          : (sl.min < 0 ? signed(value, d) : value.toFixed(d));
        sl.out.classList.toggle('moved', !!delta);
      }
    }
    panel.count.textContent = changed ? `${changed} set` : '';
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
      input.oninput = () => { out.textContent = signed(Number(input.value), sl.digits || 0); changeMask(kind, { [sl.key]: Number(input.value) }, true); };
      input.onchange = () => { drawPanel(); drawStage(); };
      input.ondblclick = () => { changeMask(kind, { [sl.key]: 0 }); drawMasks(); };
      body.append(el('label', { className: 'slider' }, [el('span', { textContent: sl.label }), input, out]));
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
    out.textContent = '* Applied in Lightroom, not shown in this preview.';
    return;
  }
  try {
    const d = await api(`${base(p)}/develop?look=${encodeURIComponent(JSON.stringify(lookOf(p)))}`);
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
  S.scope = 'folder';
  S.openLook = null;
  S.rating = null;
  if (window.drawRatings) drawRatings();
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
  if (window.refreshCull) refreshCull();
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
  const [presets, weddings, panels] = await Promise.all([api('/api/presets'), api('/api/weddings'), api('/api/adjustments')]);
  S.panels = panels.panels;
  buildPanels();
  S.presets = presets.presets;
  S.byPreset = new Map(S.presets.map((p) => [p.id, p]));
  buildLooks();
  $('pack').value = S.pack = '';
  S.weddings = weddings.weddings;
  $('wedding').replaceChildren(...S.weddings.map((w) => el('option', { value: w.id, textContent: w.name })));
  if (!S.weddings.length) {
    S.wedding = null;
    $('folders').replaceChildren();
    $('count').textContent = '';
    $('panel').style.visibility = 'hidden';
    $('empty').replaceChildren(el('strong', { textContent: 'No photos here yet' }),
      'Click “Add photos” at the top to pick a folder from this computer. Or, in Lightroom, choose Library > Plug-in Extras > “Auto Edits: send photos to the server”.');
    $('empty').hidden = false;
    $('grid').hidden = $('loupe').hidden = true;
    return;
  }
  $('empty').replaceChildren(el('strong', { textContent: 'This folder is empty' }), 'Add photos to it with “Add photos” at the top, or send them from Lightroom.');
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
$('signout').onclick = async () => { await api('/api/logout', { method: 'POST' }); location.href = '/'; };
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
$('use-preset-masks').onchange = (e) => change({ preset_masks: e.target.checked });

async function loadPresets() {
  S.presets = (await api('/api/presets')).presets;
  S.byPreset = new Map(S.presets.map((p) => [p.id, p]));
  const keep = S.pack;
  buildLooks();
  $('pack').value = [...$('pack').options].some((o) => o.value === keep) ? keep : '';
  S.pack = $('pack').value;
}

$('upload').onchange = async (e) => {
  const files = [...e.target.files];
  if (!files.length) return;
  const note = $('upload-note');
  note.textContent = `Adding ${files.length} preset${files.length === 1 ? '' : 's'}…`;
  const form = new FormData();
  for (const f of files) form.append('files', f);
  try {
    const r = await fetch('/api/presets', { method: 'POST', body: form, credentials: 'same-origin' });
    if (r.status === 401) return showSignIn();
    const data = await r.json();
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `The server answered ${r.status}.`);
    const good = data.results.filter((x) => x.ok), bad = data.results.filter((x) => !x.ok);
    await loadPresets();
    S.pack = 'My presets'; $('pack').value = S.pack; S.query = ''; $('search').value = '';
    const withMasks = good.filter((x) => x.masks).length;
    note.textContent = `Added ${good.length} preset${good.length === 1 ? '' : 's'}` +
      (withMasks ? `, ${withMasks} with masks` : '') + '. They are under “My presets”.' +
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
  $(id).oninput = (e) => { change({ [key]: Number(e.target.value) }, true); $(id).nextElementSibling.textContent = `${Math.round(e.target.value * 100)}%`; };
  $(id).onchange = () => { drawPanel(); drawStage(); };
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
  else if (!typing && typeof Cull !== 'undefined' && Cull.key(e.key)) e.preventDefault();
  else if (e.key === 'g' || e.key === 'G' || e.key === 'Escape') setView('grid');
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

start().catch((e) => { if (e.message !== 'signed out') $('saved').textContent = e.message; });
