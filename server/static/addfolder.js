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
const Add = { files: [], root: '', running: false, stop: false };

function describeChoice() {
  const folders = new Set(Add.files.map((f) => f.folder));
  $('add-summary').textContent = Add.files.length
    ? `${Add.files.length} photos in ${folders.size} folder${folders.size === 1 ? '' : 's'}: ${[...folders].slice(0, 6).join(', ')}${folders.size > 6 ? '…' : ''}`
    : 'No photos chosen yet.';
  $('add-start').disabled = !Add.files.length || Add.running;
}

$('add-photos').onclick = () => {
  Add.files = []; Add.stop = false;
  $('add-folder').value = '';
  $('add-progress').textContent = '';
  describeChoice();
  $('add').showModal();
};
$('add-close').onclick = () => { Add.stop = true; $('add').close(); };

$('add-folder').onchange = (e) => {
  const picked = [...e.target.files].filter((f) => RAW_TYPES.test(f.name) || JPEG_TYPES.test(f.name));
  // A raw file and a JPEG of the same shot: keep the raw's preview only.
  const raws = new Set(picked.filter((f) => RAW_TYPES.test(f.name)).map((f) => f.webkitRelativePath.replace(/\.[^.]+$/, '').toLowerCase()));
  Add.files = picked.filter((f) => RAW_TYPES.test(f.name) || !raws.has(f.webkitRelativePath.replace(/\.[^.]+$/, '').toLowerCase()))
    .map((file) => {
      const parts = file.webkitRelativePath.split('/');
      return { file, path: file.webkitRelativePath, folder: parts.length > 2 ? parts[1] : parts[0] };
    })
    .sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true }));
  Add.root = Add.files.length ? Add.files[0].path.split('/')[0] : '';
  if (Add.root && !$('add-name').value) $('add-name').value = Add.root;
  describeChoice();
};

$('add-start').onclick = async () => {
  const name = $('add-name').value.trim();
  if (!name) { $('add-progress').textContent = 'Give the wedding a name first.'; return; }
  const maxSide = Number($('add-size').value);
  Add.running = true; Add.stop = false;
  $('add-start').disabled = true;
  const say = (t) => { $('add-progress').textContent = t; };
  try {
    const wedding = await api('/api/weddings', { method: 'POST', body: { name, catalog: 'this computer' } });
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
          const meta = { uuid: item.uuid, fileName: item.file.name, folder: item.folder, path: item.path, taken: p.taken,
                         iso: p.iso, camera: p.camera, width: p.sourceWidth, height: p.sourceHeight, previewSize: Math.max(p.width, p.height),
                         source: 'folder', edited: [] };
          const form = new FormData();
          form.append('meta', JSON.stringify(meta));
          form.append('preview', p.blob, 'preview.jpg');
          const r = await fetch(`/api/weddings/${wedding.id}/photos`, { method: 'POST', body: form, credentials: 'same-origin' });
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
