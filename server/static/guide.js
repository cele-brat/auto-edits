// Walk-through guide for new photographers. Opens each time the app starts. Close hides it for
// this login; Close with "Don't show again" ticked hides it for good (POST /api/guide/close,
// kept per account on the server). "Show more" goes to the
// next step; the card moves beside the part of the screen that step is about, with an arrow
// pointing at it (bottom right when that part is not on screen, e.g. no wedding yet).
'use strict';

const GUIDE_STEPS = [
  { title: 'Welcome to Auto Edits', target: null,
    text: 'Cull, edit and export your wedding photos on our server, from this browser or from Lightroom Classic. Press “Show more” for a quick tour of the main steps.' },
  { title: 'Add your photos', target: 'add-photos',
    text: 'Click “Add photos…” and pick a wedding folder from this computer. Sub-folders (Haldi, Sangeet, Reception…) become event folders. Working in Lightroom? Use the plug-in from the Account menu instead.' },
  { title: 'Event folders', target: 'folders-box',
    text: 'Each event of the wedding is a folder here. Click one to see its photos; + adds more folders, single photos or a new wedding.' },
  { title: 'Cull', target: 'cull-open',
    text: '“Cull…” rates every photo Keep, Maybe or Reject: sharpness, closed eyes and bursts. Add your own stars and colour labels from the bar under the photos.' },
  { title: 'Import your presets', target: 'preset-plus',
    text: 'Click + to import your Lightroom presets (.xmp files, a folder or a zip), to save the settings on screen as a preset, or to “Learn my style” from photos you edited before.' },
  { title: 'Try presets', target: 'looks',
    text: 'Use the up and down arrow keys here to try looks on the folder. Star the ones you like for a Favourites group; Amount makes a preset stronger or softer.' },
  { title: 'Edit one photo', target: 'view-loupe',
    text: 'Press D (or click Develop) to open one photo large. G (Library) brings back the grid.' },
  { title: 'Apply to', target: 'scope-box',
    text: 'Choose whether your changes go to the whole folder or only to this photo.' },
  { title: 'Sliders', target: 'panels',
    text: 'Exposure, contrast, white balance, tone, colour and more, like Lightroom. Double-click a slider to put it back. Your original photo is never changed.' },
  { title: 'Crop and masks', target: 'toolstrip',
    text: 'Crop (R) with AI crop that frames the people, Masks (M) to change one part of the photo, Precision for finer slider steps.' },
  { title: 'Export', target: 'export-open',
    text: '“Export…” renders full-size JPEGs of the photos you choose. Each exported photo uses one credit; previews and trying presets are free.' },
  { title: 'Share with the couple', target: 'more',
    text: 'In the Account menu, “Share as gallery…” makes a private link where the couple pick favourites. Their picks show up here under Client picks.' },
  { title: 'Credits and help', target: 'credits',
    text: 'Your balance, buying credits, invoices and the Lightroom key are here. Any question later: Account menu › Help and questions, or press ?. That is the tour. Tick “Don’t show again” before Close if you do not want it next time.' },
];

const Guide = { step: 0, lit: null };

// The part of the screen a step talks about, or null when it is not showing right now.
function guideTarget(id) {
  const t = id && $(id);
  if (!t || t.offsetParent === null || getComputedStyle(t).visibility === 'hidden') return null;
  const r = t.getBoundingClientRect();
  return r.width && r.height && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth ? t : null;
}

function guideLight(t) {
  if (Guide.lit) Guide.lit.classList.remove('guide-lit');
  Guide.lit = t;
  if (t) t.classList.add('guide-lit');
}

// Put a card (the guide, or Help's "Show me") on the side of the target with the most room, its
// arrow pointing at the target's middle. No target (or a phone): docked at the bottom right.
function placeCard(d, arrow, t) {
  d.classList.toggle('docked', !t || innerWidth < 600);
  arrow.hidden = d.classList.contains('docked');
  if (arrow.hidden) { d.style.left = d.style.top = ''; return; }
  const vw = innerWidth, vh = innerHeight, gap = 14, edge = 12;
  const r = t.getBoundingClientRect();
  const v = { left: Math.max(r.left, 0), right: Math.min(r.right, vw), top: Math.max(r.top, 0), bottom: Math.min(r.bottom, vh) };
  const w = d.offsetWidth, h = d.offsetHeight;
  const room = { right: vw - v.right, left: v.left, below: vh - v.bottom, above: v.top };
  const need = { right: w, left: w, below: h, above: h };
  const side = ['right', 'left', 'below', 'above'].find((s) => room[s] >= need[s] + gap + edge)
    || Object.keys(room).sort((a, b) => room[b] - need[b] - (room[a] - need[a]))[0];
  const cx = (v.left + v.right) / 2, cy = (v.top + v.bottom) / 2;
  const clamp = (x, lo, hi) => Math.min(Math.max(x, lo), Math.max(lo, hi));
  let x, y;
  if (side === 'right' || side === 'left') {
    x = side === 'right' ? v.right + gap : v.left - gap - w;
    y = clamp(cy - h / 2, edge, vh - h - edge);
  } else {
    y = side === 'below' ? v.bottom + gap : v.top - gap - h;
    x = clamp(cx - w / 2, edge, vw - w - edge);
  }
  x = clamp(x, edge, vw - w - edge); y = clamp(y, edge, vh - h - edge);
  d.style.left = `${x}px`; d.style.top = `${y}px`;
  arrow.dataset.side = side;
  if (side === 'right' || side === 'left') { arrow.style.top = `${clamp(cy - y, 18, h - 18)}px`; arrow.style.left = ''; }
  else { arrow.style.left = `${clamp(cx - x, 18, w - 18)}px`; arrow.style.top = ''; }
}
function placeGuide() { placeCard($('guide-dialog'), $('guide-arrow'), Guide.lit && guideTarget(Guide.lit.id)); }

function drawGuide() {
  const s = GUIDE_STEPS[Guide.step];
  const last = Guide.step === GUIDE_STEPS.length - 1;
  $('guide-title').textContent = s.title;
  $('guide-text').textContent = s.text;
  $('guide-count').textContent = `${Guide.step + 1} of ${GUIDE_STEPS.length}`;
  $('guide-more').hidden = last;
  const t = s.target && $(s.target);
  if (t && t.offsetParent !== null) t.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  guideLight(guideTarget(s.target));
  placeGuide();
  (last ? $('guide-close') : $('guide-more')).focus({ preventScroll: true });
}

function openGuide() {
  Guide.step = 0;
  $('guide-never').checked = false;
  if (!$('guide-dialog').open) $('guide-dialog').show();       // not modal: the screen it points at stays usable
  drawGuide();                                                 // after show(), so focus lands on Show more, not Close
}

$('guide-more').onclick = () => { Guide.step = Math.min(Guide.step + 1, GUIDE_STEPS.length - 1); drawGuide(); };
$('guide-close').onclick = async () => {
  $('guide-dialog').close();
  try { await api('/api/guide/close', { method: 'POST', body: { forever: $('guide-never').checked } }); } catch (e) { /* it shows again next time; fine */ }
};
// Anything else that hides it only hides it for now: it comes back next time.
$('guide-dialog').addEventListener('close', () => guideLight(null));
addEventListener('resize', () => { if ($('guide-dialog').open) drawGuide(); });

(async function maybeGuide() {
  // wait until the app is on screen (start() unhides it once signed in)
  for (let i = 0; i < 100 && $('app').hidden; i++) await new Promise((ok) => setTimeout(ok, 100));
  if ($('app').hidden) return;
  try { if ((await api('/api/guide')).show) openGuide(); } catch (e) { /* no guide */ }
})();
