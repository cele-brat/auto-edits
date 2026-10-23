// "Import a folder or zip…" in the Presets + menu: whole preset packs at once (server/billing.py, /api/presets/bulk).
// Folder names become packs ("Pack / Sub"); a zip is unpacked on the server. Drop a folder or zip on the app too.
const BulkPresets = { running: false };

function bulkNote(text) { $('upload-note').textContent = text; }

async function sendBulk(items) {
  // items: [{ file, path }]; .xmp files and .zip files only, up to 50 MB in one go
  const take = items.filter((x) => /\.(xmp|zip)$/i.test(x.path));
  if (!take.length) { bulkNote('No .xmp presets or zip files were found there.'); return; }
  const bytes = take.reduce((n, x) => n + x.file.size, 0);
  if (bytes > 50 * 1024 * 1024) { bulkNote('That is more than 50 MB. Import it in smaller parts.'); return; }
  if (BulkPresets.running) return;
  BulkPresets.running = true;
  presetMenu(false);
  const form = new FormData();
  for (const x of take) form.append('files', x.file, x.path.split('/').pop());
  form.append('paths', JSON.stringify(take.map((x) => x.path)));
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/presets/bulk');
  xhr.setRequestHeader('X-Requested-With', 'AutoEdits');
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) bulkNote(`Uploading ${take.length} file${take.length === 1 ? '' : 's'}… ${Math.round(100 * e.loaded / e.total)}%`); };
  bulkNote(`Uploading ${take.length} file${take.length === 1 ? '' : 's'}…`);
  xhr.onreadystatechange = async () => {
    if (xhr.readyState !== 4) return;
    BulkPresets.running = false;
    if (xhr.status === 401) return showSignIn();
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (x) { /* no body */ }
    if (xhr.status !== 200) { bulkNote(`The presets were not imported. ${typeof data.detail === 'string' ? data.detail : `The server answered ${xhr.status}.`}`); return; }
    bulkNote('Reading the presets…');
    try { await loadPresets(); } catch (x) { /* shown below */ }
    const packs = Object.keys(data.packs || {});
    for (const pk of packs) S.openPacks.add(pk);
    S.query = ''; $('search').value = '';
    const n = data.added + data.replaced;
    let text = `Imported ${n} preset${n === 1 ? '' : 's'} into ${packs.length} pack${packs.length === 1 ? '' : 's'}` +
      (packs.length && packs.length <= 6 ? ` (${packs.join(', ')})` : '') + (data.replaced ? `, ${data.replaced} updated` : '');
    const skipped = data.skipped || [];
    if (skipped.length) {
      const why = new Map();
      for (const s of skipped) why.set(s.why, (why.get(s.why) || 0) + 1);
      text += `, ${data.skipped_total || skipped.length} skipped: ` + [...why].map(([w, c]) => `${c} × ${w}`).join('; ');
    }
    bulkNote(text + '.');
    if (typeof drawLooks === 'function') drawLooks();
  };
  xhr.send(form);
}

// the menu item: a hidden picker that takes a folder (webkitdirectory) or zip files
const bulkPick = document.createElement('input');
bulkPick.type = 'file'; bulkPick.multiple = true; bulkPick.accept = '.zip,.xmp'; bulkPick.hidden = true;
bulkPick.setAttribute('webkitdirectory', '');
document.body.append(bulkPick);
const bulkZip = document.createElement('input');
bulkZip.type = 'file'; bulkZip.multiple = true; bulkZip.accept = '.zip,.xmp'; bulkZip.hidden = true;
document.body.append(bulkZip);
// A folder picker cannot take a zip, so a small window offers both (or a drop on the Presets panel).
$('preset-bulk').onclick = () => { presetMenu(false); $('bulk-dialog').showModal(); };
$('bulk-folder').onclick = () => { $('bulk-dialog').close(); bulkPick.click(); };
$('bulk-zip').onclick = () => { $('bulk-dialog').close(); bulkZip.click(); };
$('bulk-close').onclick = () => $('bulk-dialog').close();
bulkPick.onchange = (e) => { sendBulk([...e.target.files].map((file) => ({ file, path: file.webkitRelativePath || file.name }))); e.target.value = ''; };
bulkZip.onchange = (e) => { sendBulk([...e.target.files].map((file) => ({ file, path: file.name }))); e.target.value = ''; };

// drops of .xmp folders or zips go to presets, not to the photo adding window (addfolder.js handles photos)
window.addEventListener('drop', async (e) => {
  if (!e.dataTransfer || !$('preset-box').contains(e.target)) return;
  e.preventDefault(); e.stopImmediatePropagation();
  $('drop-veil').hidden = true;
  const items = typeof readDropped === 'function' ? await readDropped(e.dataTransfer) : [...e.dataTransfer.files].map((file) => ({ file, path: file.name }));
  sendBulk(items);
}, true);
