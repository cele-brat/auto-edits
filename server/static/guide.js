// Walk-through guide for new photographers. Opens each time the app starts until they press
// Close (POST /api/guide/close, kept per account), then never again. "Show more" goes to the
// next step and points at the part of the screen it talks about.
'use strict';

const GUIDE_STEPS = [
  { title: 'Welcome to Auto Edits', target: null,
    text: 'Cull, edit and export your wedding photos on our server, from this browser or from Lightroom Classic. Press “Show more” for a quick tour of the main steps.' },
  { title: '1. Add your photos', target: 'add-photos',
    text: 'Click “Add photos…” at the bottom left and pick a wedding folder from this computer. Sub-folders (Haldi, Sangeet, Reception…) become event folders. Working in Lightroom? Use the plug-in from the Account menu instead.' },
  { title: '2. Cull', target: 'cull-open',
    text: '“Cull…” rates every photo Keep, Maybe or Reject: sharpness, closed eyes and bursts. Add your own stars and colour labels from the bar under the photos.' },
  { title: '3. Pick a preset', target: 'preset-box',
    text: 'Use the up and down arrow keys in the Presets panel to try looks on the folder. Star the ones you like for a Favourites group, and use Amount to make a preset stronger or softer. Import your Lightroom presets with +.' },
  { title: '4. Fine-tune in Develop', target: 'view-loupe',
    text: 'Press D (or click Develop) to edit one photo. Choose “Whole folder” or “This photo” at the top of the right panel, then use the sliders, Crop, Masks and Auto. Your original photo is never changed.' },
  { title: '5. Export', target: 'export-open',
    text: '“Export…” renders full-size JPEGs of the photos you choose. Each exported photo uses one credit; previews and trying presets are free.' },
  { title: '6. Share with the couple', target: 'more',
    text: 'In the Account menu, “Share as gallery…” makes a private link where the couple pick favourites. Their picks show up here under Client picks.' },
  { title: 'Credits and help', target: 'credits',
    text: 'Your balance, buying credits, invoices and the Lightroom key are under Credits. That is the tour. Press Close and this guide will not open again.' },
];

const Guide = { step: 0, lit: null };

function guideLight(id) {
  if (Guide.lit) Guide.lit.classList.remove('guide-lit');
  const t = id && $(id);
  Guide.lit = t && t.offsetParent !== null ? t : null;      // only point at what is on screen
  if (Guide.lit) Guide.lit.classList.add('guide-lit');
}

function drawGuide() {
  const s = GUIDE_STEPS[Guide.step];
  const last = Guide.step === GUIDE_STEPS.length - 1;
  $('guide-title').textContent = s.title;
  $('guide-text').textContent = s.text;
  $('guide-count').textContent = `${Guide.step + 1} of ${GUIDE_STEPS.length}`;
  $('guide-more').hidden = last;
  guideLight(s.target);
  (last ? $('guide-close') : $('guide-more')).focus();
}

function openGuide() {
  Guide.step = 0;
  if (!$('guide-dialog').open) $('guide-dialog').show();       // not modal: the screen it points at stays visible
  drawGuide();                                                 // after show(), so focus lands on Show more, not Close
}

$('guide-more').onclick = () => { Guide.step = Math.min(Guide.step + 1, GUIDE_STEPS.length - 1); drawGuide(); };
$('guide-close').onclick = async () => {
  $('guide-dialog').close();
  try { await api('/api/guide/close', { method: 'POST' }); } catch (e) { /* it shows again next time; fine */ }
};
// Esc or anything else that hides it only hides it for now: it comes back next time.
$('guide-dialog').addEventListener('close', () => guideLight(null));

(async function maybeGuide() {
  // wait until the app is on screen (start() unhides it once signed in)
  for (let i = 0; i < 100 && $('app').hidden; i++) await new Promise((ok) => setTimeout(ok, 100));
  if ($('app').hidden) return;
  try { if ((await api('/api/guide')).show) openGuide(); } catch (e) { /* no guide */ }
})();
