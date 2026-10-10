// The "Export" window: start an export on the server, watch it, download the zip.
const Ex = { timer: null };

const sizeName = (s) => (s === 0 ? 'full size' : `${s} px`);
const mb = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(b / 1e6))} MB`);
function timeLeft(e) {
  if (!e.seconds_each || !e.left) return '';
  const elapsed = Date.now() / 1000 - e.created, rate = (e.done + e.failed) / Math.max(1, elapsed);
  if (!rate) return '';
  const m = Math.ceil(e.left / rate / 60);
  return m >= 90 ? `, about ${(m / 60).toFixed(1)} hours left` : `, about ${m} min left`;
}

function exportRow(e) {
  const li = document.createElement('li');
  const what = document.createElement('div'); what.className = 'what';
  what.textContent = `${e.scope || 'Whole wedding'} · ${sizeName(e.size)} · quality ${e.quality}${e.watermark ? ' · watermark' : ''}`;
  const how = document.createElement('div'); how.className = 'how';
  const states = { waiting: 'Waiting to start', working: 'Working', done: 'Finished', stopped: 'Stopped' };
  how.textContent = `${states[e.state]}: ${e.done} of ${e.total} done` + (e.failed ? `, ${e.failed} failed` : '') +
    (e.state === 'working' ? timeLeft(e) : '') + (e.bytes ? ` · ${mb(e.bytes)}` : '') +
    (e.seconds_each ? ` · ${e.seconds_each} s a photo` : '');
  const bar = document.createElement('progress'); bar.max = e.total; bar.value = e.done + e.failed;
  const row = document.createElement('div'); row.className = 'row';
  const button = (label, cls, fn) => { const b = document.createElement('button'); b.type = 'button'; b.className = cls; b.textContent = label; b.onclick = fn; row.append(b); };
  if (e.state === 'waiting' || e.state === 'working') button('Stop', 'quiet', async () => { await api(`/api/exports/${e.id}/stop`, { method: 'POST' }); refreshExports(); });
  else button('Remove', 'quiet', async () => { await api(`/api/exports/${e.id}`, { method: 'DELETE' }); refreshExports(); });
  if (e.done && e.state !== 'working' && e.state !== 'waiting') {
    const a = document.createElement('a'); a.className = 'button'; a.href = `/api/exports/${e.id}/zip`; a.textContent = `Download ${e.done} photos (zip)`;
    row.append(a);
  }
  li.append(what, how, bar, row);
  return li;
}

async function refreshExports() {
  clearTimeout(Ex.timer);
  if (!S.wedding || !$('export').open) return;
  try {
    const { exports } = await api(`/api/weddings/${S.wedding.id}/exports`);
    if (exports.length) $('export-list').replaceChildren(...exports.map(exportRow));
    else { const li = document.createElement('li'); li.className = 'how'; li.textContent = 'None yet.'; $('export-list').replaceChildren(li); }
    if (exports.some((e) => e.state === 'working' || e.state === 'waiting')) Ex.timer = setTimeout(refreshExports, 3000);
  } catch (err) { $('export-error').textContent = err.message; }
}

$('export-open').onclick = () => {
  if (!S.wedding) return;
  const whole = new Option(`Whole wedding (${S.wedding.photos} photos)`, '');
  $('export-scope').replaceChildren(whole, ...S.wedding.folders.map((f) => new Option(`Only “${f.name}”`, f.id)));
  $('export-error').textContent = '';
  $('export').showModal();
  refreshExports();
};
$('export-close').onclick = () => { $('export').close(); clearTimeout(Ex.timer); };
$('export-start').onclick = async () => {
  $('export-error').textContent = '';
  $('export-start').disabled = true;
  try {
    const scope = $('export-scope').value;
    await api(`/api/weddings/${S.wedding.id}/exports`, { method: 'POST', body: {
      size: +$('export-size').value, quality: +$('export-quality').value,
      watermark: $('export-watermark').value.trim(), folder: scope === '' ? null : +scope,
      ratings: $('export-ratings').value ? $('export-ratings').value.split(',') : [] } });
  } catch (err) { $('export-error').textContent = err.message; }
  $('export-start').disabled = false;
  refreshExports();
};
