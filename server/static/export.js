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
    (e.skipped ? `, ${e.skipped} not made (credits given back)` : '') +
    (e.state === 'working' ? timeLeft(e) : '') + (e.bytes ? ` · ${mb(e.bytes)}` : '') +
    (e.seconds_each ? ` · ${e.seconds_each} s a photo` : '');
  const bar = document.createElement('progress'); bar.max = e.total; bar.value = e.done + e.failed;
  const row = document.createElement('div'); row.className = 'row';
  const button = (label, cls, fn) => { const b = document.createElement('button'); b.type = 'button'; b.className = cls; b.textContent = label; b.onclick = fn; row.append(b); };
  const after = () => { refreshExports(); if (window.refreshCredits) refreshCredits().then(exportCost); };
  if (e.state === 'waiting' || e.state === 'working') button('Stop', 'quiet', async () => { await api(`/api/exports/${e.id}/stop`, { method: 'POST' }); after(); });
  else button('Remove', 'quiet', async () => { await api(`/api/exports/${e.id}`, { method: 'DELETE' }); after(); });
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

// Ratings start at "Keep only" once any photo in the chosen part is rated (culled or by hand), else "Every photo".
function defaultRatings(photos, scope) {
  const inScope = photos.filter((p) => scope === '' || p.folder === +scope);
  return inScope.some((p) => p.pick || (p.cull && p.cull.r)) ? 'keep' : '';
}
$('export-open').onclick = () => {
  if (!S.wedding) return;
  const whole = new Option(`Whole wedding (${S.wedding.photos} photos)`, '');
  $('export-scope').replaceChildren(whole, ...S.wedding.folders.map((f) => new Option(`Only “${f.name}”`, f.id)));
  $('export-error').textContent = '';
  Ex.ratingsTouched = false;
  $('export-ratings').value = defaultRatings(S.photos, $('export-scope').value);
  exportCost();
  $('export').showModal();
  refreshExports();
};
function exportCount() {
  const scope = $('export-scope').value, ratings = $('export-ratings').value ? $('export-ratings').value.split(',') : [];
  return S.photos.filter((p) => (scope === '' || p.folder === +scope) &&
    (!ratings.length || ratings.includes(p.pick || (p.cull || {}).r))).length;
}
function exportCost() {
  const n = exportCount();
  $('export-cost').textContent = window.costLine ? costLine(n, `${n} photo${n === 1 ? '' : 's'} at 1 credit each`) : '';
  if (window.hideSpend) hideSpend('export');
  $('export-start').textContent = n ? `Export ${n} photo${n === 1 ? '' : 's'}` + (window.Acc && Acc.unlimited ? '' : ` · ${n} credit${n === 1 ? '' : 's'}`) : 'Nothing to export';
  $('export-start').disabled = !n;
}
$('export-scope').onchange = () => { if (!Ex.ratingsTouched) $('export-ratings').value = defaultRatings(S.photos, $('export-scope').value); exportCost(); };
$('export-ratings').onchange = () => { Ex.ratingsTouched = true; exportCost(); };
for (const id of ['export-size', 'export-quality', 'export-watermark']) $(id).addEventListener('input', () => { if (window.hideSpend) hideSpend('export'); });
$('export-close').onclick = () => { $('export').close(); clearTimeout(Ex.timer); };
$('export-start').onclick = () => {
  const n = exportCount();
  if (!n) return;
  askSpend('export', n, startExport);
};
async function startExport() {
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
  if (window.refreshCredits) refreshCredits().then(exportCost);
  refreshExports();
}
