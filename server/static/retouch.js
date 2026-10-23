// Retouch: skin smoothing, blemish removal, eye brightening, teeth whitening and click-to-heal spots.
// Kept in the look under "retouch", so it follows Apply to (this photo or the whole folder) and
// goes into presets made from the look (healed spots excepted: they belong to one photo).
// Shown in previews above grid size and in exports; Lightroom does not get it yet.
'use strict';

const RETOUCH_SLIDERS = [
  ['smooth', 'Skin smoothing', 'Evens skin tone and keeps pores and fine hair (frequency separation)'],
  ['blemish', 'Blemish removal', 'Finds small spots inside the face skin and fills them from the skin around'],
  ['eyes', 'Eye brighten', 'A little more light and colour in the iris, cleaner whites'],
  ['teeth', 'Teeth whiten', 'Takes the yellow out of teeth'],
];
const Rt = { healing: false, radius: 0.012, inputs: {} };
const retouchNow = () => ({ smooth: 0, blemish: 0, eyes: 0, teeth: 0, spots: [], ...(panelLook().retouch || {}) });
function changeRetouch(patch, live = false) { change({ retouch: { ...retouchNow(), ...patch } }, live); }

function buildRetouchPanel() {
  const box = el('details', { className: 'panel', id: 'retouch-panel' });
  Rt.count = el('small');
  box.append(el('summary', {}, ['Retouch', Rt.count]));
  box.append(el('p', { className: 'hint', textContent: 'Faces are found in each photo. Shown in previews and exports; not yet sent to Lightroom.' }));
  for (const [key, label, title] of RETOUCH_SLIDERS) {
    const input = el('input', { type: 'range', min: 0, max: 100, step: 1, value: 0, title });
    input.setAttribute('aria-label', `Retouch, ${label}`);
    const out = el('button', { type: 'button', className: 'value', textContent: '0' });
    input.oninput = () => { paint(input, 0); out.textContent = input.value; out.classList.toggle('moved', +input.value > 0); changeRetouch({ [key]: +input.value }, true); };
    input.onchange = () => { stageLive = false; drawPanel(); drawStage(); };
    input.ondblclick = () => changeRetouch({ [key]: 0 });
    out.onclick = () => input.focus();
    Rt.inputs[key] = { input, out };
    box.append(el('div', { className: 'adj' }, [el('span', { className: 'name', textContent: label }), input, out]));
  }
  Rt.healButton = el('button', { type: 'button', textContent: 'Heal spots: click on the photo (H)', title: 'Each click removes a small spot where you click. Only in Develop.' });
  Rt.healButton.setAttribute('aria-pressed', 'false');
  Rt.healButton.onclick = () => setHealing(!Rt.healing);
  Rt.size = el('input', { type: 'range', min: 0.004, max: 0.04, step: 0.002, value: Rt.radius, title: 'Spot size' });
  Rt.size.setAttribute('aria-label', 'Heal spot size');
  Rt.size.oninput = () => { Rt.radius = +Rt.size.value; };
  Rt.clear = el('button', { type: 'button', className: 'quiet', textContent: 'Clear spots' });
  Rt.clear.onclick = () => changeRetouch({ spots: [] });
  box.append(el('div', { className: 'tools' }, [Rt.healButton, el('span', { className: 'name', textContent: 'Size' }), Rt.size, Rt.clear]));
  Rt.spotsNote = el('p', { className: 'spots' });
  box.append(Rt.spotsNote);
  $('panels').after(box);
}

function drawRetouch(look) {
  if (!Rt.inputs.smooth) return;
  const r = { smooth: 0, blemish: 0, eyes: 0, teeth: 0, spots: [], ...(look.retouch || {}) };
  let n = 0;
  for (const [key] of RETOUCH_SLIDERS) {
    const { input, out } = Rt.inputs[key];
    if (document.activeElement !== input) input.value = r[key];
    paint(input, 0);
    out.textContent = String(r[key]);
    out.classList.toggle('moved', r[key] > 0);
    if (r[key]) n += 1;
  }
  const spots = r.spots.length;
  if (spots) n += 1;
  Rt.count.textContent = n ? `${n}` : '';
  Rt.spotsNote.textContent = spots ? `${spots} healed spot${spots === 1 ? '' : 's'}${editingPhoto() ? '' : ' (on every photo in the folder: switch Apply to “This photo” for one photo)'}.` : '';
  Rt.clear.hidden = !spots;
  Rt.healButton.disabled = S.view !== 'loupe' || !photo();
  if (Rt.healing && Rt.healButton.disabled) setHealing(false);
  drawHealDots();
}

// ---- click to heal -----------------------------------------------------------------------
function setHealing(on) {
  Rt.healing = on && S.view === 'loupe' && !!photo();
  Rt.healButton.setAttribute('aria-pressed', String(Rt.healing));
  $('frame').classList.toggle('healing', Rt.healing);
  if (Rt.healing && S.scope !== 'photo' && window.setDevelopScope) setDevelopScope('photo');   // spots belong to one photo
  if (Rt.healing) $('adjust-box').scrollIntoView({ block: 'nearest' });
  drawHealDots();
}
function healLayer() {
  let layer = $('heal-layer');
  if (!layer) { layer = el('div', { id: 'heal-layer' }); layer.style.cssText = 'position:absolute;inset:0;pointer-events:none;'; $('frame').append(layer); }
  return layer;
}
function drawHealDots() {
  const layer = healLayer();
  layer.replaceChildren();
  if (!Rt.healing || S.view !== 'loupe' || !photo() || !window.toScreen || !D.rect) return;
  for (const s of retouchNow().spots) {
    const [x, y] = toScreen(s.x, s.y);
    const r = s.r * Math.max(fullW(), fullH());
    const dot = el('i', { className: 'heal-dot' });
    dot.style.cssText = `left:${x - r}px;top:${y - r}px;width:${2 * r}px;height:${2 * r}px;`;
    layer.append(dot);
  }
}
$('frame').addEventListener('click', (e) => {
  if (!Rt.healing || !window.toFrame || !D.rect) return;
  if (e.target.closest('#divider, #crop-box, button')) return;
  e.preventDefault(); e.stopPropagation();
  const [x, y] = toFrame(e.clientX, e.clientY);
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  const spots = retouchNow().spots;
  // a click on a spot already there removes it
  const hit = spots.findIndex((s) => Math.hypot((s.x - x) * fullW(), (s.y - y) * fullH()) <= Math.max(6, s.r * Math.max(fullW(), fullH())));
  changeRetouch({ spots: hit >= 0 ? spots.filter((_, i) => i !== hit) : [...spots, { x: +x.toFixed(4), y: +y.toFixed(4), r: Rt.radius }].slice(-60) });
}, true);
document.addEventListener('keydown', (e) => {
  if (e.target.matches('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey || document.querySelector('dialog[open]')) return;
  if ((e.key === 'h' || e.key === 'H') && S.view === 'loupe' && photo()) { e.preventDefault(); setHealing(!Rt.healing); }
  else if (e.key === 'Escape' && Rt.healing) setHealing(false);
});

// the dots follow the picture when it is resized or redrawn
const placeDividerBefore = placeDivider;
placeDivider = function () { placeDividerBefore(); drawHealDots(); };
const drawSlidersBefore = drawSliders;
drawSliders = function (look) { drawSlidersBefore(look); drawRetouch(look); };
const buildPanelsBefore = buildPanels;
buildPanels = function () { buildPanelsBefore(); buildRetouchPanel(); };
if ($('panels').children.length) buildRetouchPanel();
