// Learn my style: upload past edits (Lightroom .xmp sidecars, a folder or zip of them, or a CSV/JSON
// of develop settings), and the server makes "My style" presets from them, plus a plain summary.
// With the original camera previews alongside (JPEGs named like the sidecars), one preset per kind of light.
'use strict';

const St = { files: [], running: false };
const STYLE_TYPES = /\.(xmp|zip|csv|json|jpe?g)$/i;

function styleDescribe() {
  const xmp = St.files.filter((f) => /\.xmp$/i.test(f.name)).length;
  const zips = St.files.filter((f) => /\.zip$/i.test(f.name)).length;
  const tables = St.files.filter((f) => /\.(csv|json)$/i.test(f.name)).length;
  const jpgs = St.files.filter((f) => /\.jpe?g$/i.test(f.name)).length;
  const bits = [];
  if (xmp) bits.push(`${xmp} .xmp sidecar${xmp === 1 ? '' : 's'}`);
  if (zips) bits.push(`${zips} zip${zips === 1 ? '' : 's'}`);
  if (tables) bits.push(`${tables} CSV/JSON file${tables === 1 ? '' : 's'}`);
  if (jpgs) bits.push(`${jpgs} preview${jpgs === 1 ? '' : 's'} (for learning by light)`);
  $('style-summary-files').textContent = bits.length ? bits.join(', ') + '.' : 'Nothing chosen yet.';
  $('style-scenes').closest('label').hidden = !jpgs;
  const mb = St.files.reduce((n, f) => n + f.size, 0) / 1e6;
  $('style-start').disabled = St.running || !(xmp || zips || tables);
  $('style-error').textContent = mb > 60 ? `That is ${mb.toFixed(0)} MB; the most is 60 MB. Leave the photos out, or send fewer.` : '';
}
function styleTake(files) {
  const seen = new Set(St.files.map((f) => `${f.name}|${f.size}`));
  for (const f of files) {
    if (!STYLE_TYPES.test(f.name) || seen.has(`${f.name}|${f.size}`)) continue;
    seen.add(`${f.name}|${f.size}`);
    St.files.push(f);
  }
  styleDescribe();
}

function openStyle() {
  if (window.presetMenu) presetMenu(false);
  St.files = [];
  $('style-result').hidden = true;
  $('style-progress').textContent = '';
  styleDescribe();
  $('style-dialog').showModal();
}
$('preset-learn').onclick = openStyle;
$('style-close').onclick = () => $('style-dialog').close();
$('style-files').onchange = (e) => { styleTake([...e.target.files]); e.target.value = ''; };
$('style-folder').onchange = (e) => { styleTake([...e.target.files]); e.target.value = ''; };
$('style-drop').addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); $('style-drop').classList.add('over'); });
$('style-drop').addEventListener('dragleave', () => $('style-drop').classList.remove('over'));
$('style-drop').addEventListener('drop', async (e) => {
  e.preventDefault(); e.stopPropagation();
  $('style-drop').classList.remove('over');
  $('drop-veil').hidden = true;                               // the app's own drop veil (addfolder.js) was shown on drag
  if (typeof dragDepth !== 'undefined') dragDepth = 0;
  const items = window.readDropped ? await readDropped(e.dataTransfer) : [...e.dataTransfer.files].map((file) => ({ file }));
  styleTake(items.map((i) => i.file));
});

$('style-start').onclick = async () => {
  if (St.running) return;
  St.running = true;
  styleDescribe();
  $('style-progress').textContent = 'Reading your edits and grouping them… this takes a few seconds.';
  const form = new FormData();
  for (const f of St.files) form.append('files', f, f.name);
  form.append('scenes', $('style-scenes').checked ? '1' : '0');
  form.append('replace', $('style-replace').checked ? '1' : '0');
  try {
    const r = await fetch('/api/style/learn', { method: 'POST', body: form, credentials: 'same-origin', headers: { 'X-Requested-With': 'AutoEdits' } });
    if (r.status === 401) return showSignIn();
    const data = await r.json();
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `The server answered ${r.status}.`);
    $('style-progress').textContent = '';
    $('style-summary').textContent = data.summary;
    $('style-made').replaceChildren(...data.presets.map((p) => el('li', {}, [
      el('b', { textContent: p.name }), ` · from ${p.photos} edit${p.photos === 1 ? '' : 's'}` +
      (p.scene ? '' : ` (${Math.round(p.share * 100)}% of them)`) + `. ${p.about}`])));
    $('style-result').hidden = false;
    $('style-count').textContent = `${data.edits} edits read` + (data.previews ? `, ${data.previews} previews` : '') + '.';
    await loadPresets();
    S.openPacks.add('My style'); S.query = ''; $('search').value = '';
    if (data.presets.length && folder()) { S.openLook = null; change({ preset: data.presets[0].id, mode: panelLook().mode === 'auto' ? 'preset' : panelLook().mode }); }
    else drawLooks();
    $('upload-note').textContent = `Made ${data.presets.length} preset${data.presets.length === 1 ? '' : 's'} in the “My style” group.`;
  } catch (err) {
    $('style-progress').textContent = '';
    $('style-error').textContent = err.message;
  }
  St.running = false;
  styleDescribe();
};
