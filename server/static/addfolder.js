/* Auto Edits: add photos straight from a folder on this computer, without Lightroom.

   Raw files are not uploaded. Every camera stores a ready-made JPEG preview inside
   its raw file; this finds the largest one, turns it upright, makes it the size
   asked for, and uploads only that. JPEG files are used as they are. */
'use strict';

const RAW_TYPES = /\.(nef|nrw|cr2|cr3|arw|sr2|dng|raf|orf|rw2|pef|srw)$/i;
const JPEG_TYPES = /\.(jpe?g)$/i;

// ---- reading what the camera wrote ---------------------------------------------
function tiffFacts(view, start) {
  // Maker, model, orientation, ISO and capture time from a TIFF/EXIF block starting at `start`.
  const out = {};
  try {
    const mark = view.getUint16(start);
    if (mark !== 0x4949 && mark !== 0x4d4d) return out;
    const le = mark === 0x4949;
    const u16 = (o) => view.getUint16(o, le), u32 = (o) => view.getUint32(o, le);
    const text = (o, n) => {
      let s = '';
      for (let i = 0; i < n && o + i < view.byteLength; i++) { const c = view.getUint8(o + i); if (!c) break; s += String.fromCharCode(c); }
      return s.trim();
    };
    const readDir = (dir, want) => {
      const n = u16(dir);
      for (let i = 0; i < n && i < 200; i++) {
        const e = dir + 2 + i * 12, tag = u16(e), type = u16(e + 2), count = u32(e + 4);
        if (!(tag in want)) continue;
        const size = [0, 1, 1, 2, 4, 8, 1, 1, 2, 4, 8][type] || 1;
        const at = size * count > 4 ? start + u32(e + 8) : e + 8;
        want[tag](type === 2 ? text(at, count) : type === 3 ? u16(at) : u32(at));
      }
    };
    let exif = 0;
    readDir(start + u32(start + 4), {
      0x010f: (v) => { out.make = v; }, 0x0110: (v) => { out.model = v; }, 0x0112: (v) => { out.orientation = v; },
      0x8769: (v) => { exif = v; },
    });
    if (exif) readDir(start + exif, { 0x8827: (v) => { out.iso = v; }, 0x9003: (v) => { out.taken = v; } });
  } catch (e) { /* a file we cannot read facts from still uploads */ }
  return out;
}

function jpegAt(bytes, view, at) {
  // If a real picture starts at `at`, its size in pixels and where it ends. Raw sensor data
  // stored as "lossless JPEG" is skipped: it is not a picture a browser can show.
  let p = at + 2, facts = null;
  while (p + 4 < bytes.length && bytes[p] === 0xff) {
    const m = bytes[p + 1];
    if (m === 0xd8 || (m >= 0xd0 && m <= 0xd7)) { p += 2; continue; }
    const len = view.getUint16(p + 2);
    if (m === 0xe1 && !facts && bytes[p + 4] === 0x45 && bytes[p + 5] === 0x78) facts = tiffFacts(view, p + 10);
    if (m === 0xc0 || m === 0xc1 || m === 0xc2) {
      const h = view.getUint16(p + 5), w = view.getUint16(p + 7);
      let q = p + 2 + len;
      for (; q + 1 < bytes.length; q++) if (bytes[q] === 0xff && bytes[q + 1] === 0xd9) return { w, h, end: q + 2, facts: facts || {} };
      return null;
    }
    if (m === 0xc3 || m === 0xda || m === 0xd9) return null;
    p += 2 + len;
  }
  return null;
}

function largestPreview(buffer) {
  const bytes = new Uint8Array(buffer), view = new DataView(buffer);
  let best = null;
  for (let i = 0; i + 3 < bytes.length; i++) {
    if (bytes[i] !== 0xff || bytes[i + 1] !== 0xd8 || bytes[i + 2] !== 0xff) continue;
    const j = jpegAt(bytes, view, i);
    if (j && (!best || j.w * j.h > best.w * best.h)) best = { ...j, start: i };
    if (j && j.w * j.h > 8e6) i = j.end - 1;                    // nothing bigger hides inside a full-size preview
  }
  return best;
}

async function previewOf(file, maxSide) {
  const buffer = await file.arrayBuffer();
  const view = new DataView(buffer);
  let source, facts, turn = 1;
  if (JPEG_TYPES.test(file.name)) {
    const j = jpegAt(new Uint8Array(buffer), view, 0);
    source = new Blob([buffer], { type: 'image/jpeg' });
    facts = (j && j.facts) || {};
  } else {
    const j = largestPreview(buffer);
    if (!j || j.w < 600) throw new Error('no preview inside this file');
    source = new Blob([buffer.slice(j.start, j.end)], { type: 'image/jpeg' });
    facts = tiffFacts(view, 0);
    if (!j.facts.orientation && facts.orientation) turn = facts.orientation;   // the preview itself does not say which way is up
    facts = { ...j.facts, ...facts };
  }
  const bitmap = await createImageBitmap(source);
  const quarter = { 3: 2, 6: 1, 8: 3 }[turn] || 0;              // quarter turns clockwise
  const sw = bitmap.width, sh = bitmap.height;
  const scale = Math.min(1, maxSide / Math.max(sw, sh));
  const w = Math.round(sw * scale), h = Math.round(sh * scale);
  const canvas = new OffscreenCanvas(quarter % 2 ? h : w, quarter % 2 ? w : h);
  const g = canvas.getContext('2d');
  g.imageSmoothingQuality = 'high';
  g.translate(canvas.width / 2, canvas.height / 2);
  g.rotate(quarter * Math.PI / 2);
  g.drawImage(bitmap, -w / 2, -h / 2, w, h);
  bitmap.close();
  const blob = await canvas.convertToBlob({ type: 'image/jpeg', quality: 0.88 });
  const taken = /^\d{4}:\d\d:\d\d \d\d:\d\d:\d\d/.test(facts.taken || '')
    ? facts.taken.replace(/^(\d{4}):(\d\d):(\d\d) /, '$1-$2-$3T') : '';
  return { blob, width: canvas.width, height: canvas.height, sourceWidth: quarter % 2 ? sh : sw, sourceHeight: quarter % 2 ? sw : sh,
           camera: cameraName(facts.make, facts.model), iso: facts.iso || null, taken };
}

function cameraName(make, model) {
  // "NIKON CORPORATION" + "NIKON D810" reads better as just "NIKON D810".
  if (!model) return make || '';
  const brand = (make || '').split(' ')[0];
  return brand && !model.toLowerCase().includes(brand.toLowerCase()) ? `${brand} ${model}` : model;
}

function nameHash(text) {
  let a = 0x811c9dc5, b = 0x1b873593;
  for (let i = 0; i < text.length; i++) { a = Math.imul(a ^ text.charCodeAt(i), 0x01000193); b = Math.imul(b + text.charCodeAt(i), 0x85ebca6b) ^ (b >>> 13); }
  return (a >>> 0).toString(16).padStart(8, '0') + (b >>> 0).toString(16).padStart(8, '0');
}

// ---- the "Add photos" window -----------------------------------------------------
// Like adding in Finder: a whole folder (its sub-folders become event folders), single photos,
// or anything dragged in from Finder, into a new wedding or the one that is open.
const Add = { files: [], root: '', running: false, stop: false, where: 'new' };
const FOLDER_ICON = '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/></svg>';
const LOOSE = '';                                   // single photos: they go in the event folder named below

function folderOf(path) {
  const parts = path.split('/');
  if (parts.length === 1) return LOOSE;
  return parts.length > 2 ? parts[1] : parts[0];
}

// Adds files to the list: drops repeats, and a JPEG beside a raw file of the same shot.
function takeFiles(items) {
  const seen = new Set(Add.files.map((f) => f.path.toLowerCase()));
  for (const { file, path } of items) {
    if (!(RAW_TYPES.test(file.name) || JPEG_TYPES.test(file.name)) || seen.has(path.toLowerCase())) continue;
    seen.add(path.toLowerCase());
    Add.files.push({ file, path, folder: folderOf(path) });
  }
  const raws = new Set(Add.files.filter((f) => RAW_TYPES.test(f.file.name)).map((f) => f.path.replace(/\.[^.]+$/, '').toLowerCase()));
  Add.files = Add.files.filter((f) => RAW_TYPES.test(f.file.name) || !raws.has(f.path.replace(/\.[^.]+$/, '').toLowerCase()))
    .sort((x, y) => x.path.localeCompare(y.path, undefined, { numeric: true }));
  const roots = Add.files.filter((f) => f.path.includes('/')).map((f) => f.path.split('/')[0]);
  Add.root = roots[0] || '';
  if (Add.where === 'new' && Add.root && !$('add-name').value) $('add-name').value = Add.root;
  describeChoice();
}

function describeChoice() {
  const groups = new Map();
  for (const f of Add.files) groups.set(f.folder, (groups.get(f.folder) || 0) + 1);
  const loose = Add.files.some((f) => f.folder === LOOSE);
  $('add-list').replaceChildren(...[...groups].map(([name, n]) => {
    const li = document.createElement('li');
    li.innerHTML = FOLDER_ICON;
    li.append(name === LOOSE ? `Single photos → ${$('add-event').value.trim() || 'Photos'}` : name,
      Object.assign(document.createElement('small'), { textContent: `${n} photo${n === 1 ? '' : 's'}` }));
    const x = Object.assign(document.createElement('button'), { type: 'button', textContent: '×', title: 'Leave these out' });
    x.onclick = () => { Add.files = Add.files.filter((f) => f.folder !== name); describeChoice(); };
    li.append(x);
    return li;
  }));
  $('add-summary').textContent = Add.files.length ? `${Add.files.length} photo${Add.files.length === 1 ? '' : 's'} chosen.` : 'Nothing chosen yet.';
  $('add-event-row').hidden = !loose;
  $('add-name-row').hidden = Add.where !== 'new';
  for (const b of document.querySelectorAll('#add-where button')) b.setAttribute('aria-pressed', String(b.dataset.where === Add.where));
  $('add-start').disabled = !Add.files.length || Add.running;
  $('add-start').textContent = Add.where === 'this' && S.wedding ? `Add to “${S.wedding.name}”` : 'Add these photos';
}

function openAdd(where) {
  Add.files = []; Add.stop = false;
  Add.where = where || (S.wedding ? 'this' : 'new');
  $('add-folder').value = ''; $('add-files').value = '';
  $('add-progress').textContent = '';
  $('add-where-this').disabled = !S.wedding;
  $('add-where-this').textContent = S.wedding ? `This wedding (${S.wedding.name})` : 'This wedding';
  if (Add.where === 'new') $('add-name').value = '';
  $('add-events').replaceChildren(...(S.wedding ? S.wedding.folders : []).map((f) => Object.assign(document.createElement('option'), { value: f.name })));
  $('add-event').value = Add.where === 'this' && folder() ? folder().name : 'Photos';
  describeChoice();
  if (!$('add').open) $('add').showModal();
}
$('add-photos').onclick = () => openAdd();
$('add-close').onclick = () => { Add.stop = true; $('add').close(); };
for (const b of document.querySelectorAll('#add-where button')) {
  b.onclick = () => { if (b.disabled) return; Add.where = b.dataset.where; if (Add.where === 'new' && Add.root && !$('add-name').value) $('add-name').value = Add.root; describeChoice(); };
}
$('add-event').oninput = describeChoice;
$('add-folder').onchange = (e) => { takeFiles([...e.target.files].map((file) => ({ file, path: file.webkitRelativePath || file.name }))); e.target.value = ''; };
$('add-files').onchange = (e) => { takeFiles([...e.target.files].map((file) => ({ file, path: file.name }))); e.target.value = ''; };

// ---- dragging in from Finder: folders are walked, sub-folders included ----------------------
async function readDropped(dataTransfer) {
  const out = [];
  const entries = [...dataTransfer.items].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  const fileOf = (entry) => new Promise((ok) => entry.file(ok, () => ok(null)));
  const listOf = (reader) => new Promise((ok) => reader.readEntries(ok, () => ok([])));
  async function walk(entry, path) {
    if (entry.isFile) {
      const file = await fileOf(entry);
      if (file) out.push({ file, path: path ? `${path}/${file.name}` : file.name });
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      for (let batch = await listOf(reader); batch.length; batch = await listOf(reader)) {
        for (const child of batch) await walk(child, path ? `${path}/${entry.name}` : entry.name);
      }
    }
  }
  if (entries.length) for (const e of entries) await walk(e, '');
  else for (const file of dataTransfer.files) out.push({ file, path: file.name });
  return out;
}
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
let dragDepth = 0;
window.addEventListener('dragenter', (e) => { if (!hasFiles(e) || $('app').hidden) return; dragDepth += 1; if (!$('add').open) $('drop-veil').hidden = false; });
window.addEventListener('dragleave', (e) => { if (!hasFiles(e)) return; dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('drop-veil').hidden = true; });
window.addEventListener('dragover', (e) => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } });
window.addEventListener('drop', async (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0; $('drop-veil').hidden = true; $('add-drop').classList.remove('over');
  if ($('app').hidden || Add.running) return;
  if (!$('add').open) openAdd();
  $('add-summary').textContent = 'Reading what you dropped…';
  takeFiles(await readDropped(e.dataTransfer));
});
$('add-drop').addEventListener('dragover', () => $('add-drop').classList.add('over'));
$('add-drop').addEventListener('dragleave', () => $('add-drop').classList.remove('over'));

$('add-start').onclick = async () => {
  const name = $('add-name').value.trim();
  const into = Add.where === 'this' && S.wedding ? S.wedding : null;
  if (!into && !name) { $('add-progress').textContent = 'Give the wedding a name first.'; return; }
  const looseFolder = $('add-event').value.trim() || 'Photos';
  const maxSide = Number($('add-size').value);
  Add.running = true; Add.stop = false;
  $('add-start').disabled = true;
  const say = (t) => { $('add-progress').textContent = t; };
  try {
    const wedding = into || await api('/api/weddings', { method: 'POST', body: { name, catalog: 'this computer' } });
    const have = new Set((await api(`/api/weddings/${wedding.id}/received`)).uuids);
    const todo = Add.files.map((f) => ({ ...f, uuid: `f-${nameHash(`${f.path}|${f.file.size}|${f.file.lastModified}`)}` }))
      .filter((f) => !have.has(f.uuid));
    const skipped = Add.files.length - todo.length;
    let done = 0, failed = [];
    const started = performance.now();
    const worker = async () => {
      while (todo.length && !Add.stop) {
        const item = todo.shift();
        try {
          const p = await previewOf(item.file, maxSide);
          const meta = { uuid: item.uuid, fileName: item.file.name, folder: item.folder === LOOSE ? looseFolder : item.folder, path: item.path, taken: p.taken,
                         iso: p.iso, camera: p.camera, width: p.sourceWidth, height: p.sourceHeight, previewSize: Math.max(p.width, p.height),
                         source: 'folder', edited: [] };
          const form = new FormData();
          form.append('meta', JSON.stringify(meta));
          form.append('preview', p.blob, 'preview.jpg');
          const r = await fetch(`/api/weddings/${wedding.id}/photos`, { method: 'POST', body: form, credentials: 'same-origin', headers: { 'X-Requested-With': 'AutoEdits' } });
          if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `server answered ${r.status}`);
          done += 1;
        } catch (e) {
          failed.push(`${item.file.name}: ${e.message}`);
        }
        const each = (performance.now() - started) / 1000 / Math.max(1, done + failed.length);
        const left = Math.round(each * todo.length / 60);
        say(`Added ${done} of ${done + failed.length + todo.length}` + (left >= 1 ? `, about ${left} min left` : '') +
            (failed.length ? `. ${failed.length} could not be read.` : '.'));
      }
    };
    say(todo.length ? 'Starting…' : 'Nothing new to add.');
    await Promise.all([worker(), worker(), worker()]);
    say(`${Add.stop ? 'Stopped. ' : ''}Added ${done} photo${done === 1 ? '' : 's'}` +
        (skipped ? `, ${skipped} were already there` : '') +
        (failed.length ? `. Not added: ${failed.slice(0, 5).join('; ')}${failed.length > 5 ? '…' : ''}` : '.'));
    await start();
    if (S.weddings.some((w) => w.id === wedding.id)) await openWedding(wedding.id);
  } catch (e) {
    if (e.message !== 'signed out') say(`Could not add the photos. ${e.message}`);
  }
  Add.running = false;
  describeChoice();
};

// The + on the Folders panel: add a folder or photos to the open wedding, or start a new wedding.
function folderMenu(open) { $('folder-menu').hidden = !open; $('folder-plus').setAttribute('aria-expanded', String(open)); }
$('folder-plus').onclick = (e) => { e.preventDefault(); e.stopPropagation(); $('folders-box').open = true; folderMenu($('folder-menu').hidden); };
document.addEventListener('click', (e) => { if (!e.target.closest('#folder-menu, #folder-plus')) folderMenu(false); });
$('folder-add-folder').onclick = () => { folderMenu(false); openAdd(S.wedding ? 'this' : 'new'); $('add-folder').click(); };
$('folder-add-photos').onclick = () => { folderMenu(false); openAdd(S.wedding ? 'this' : 'new'); $('add-files').click(); };
$('folder-new-wedding').onclick = () => { folderMenu(false); openAdd('new'); };
