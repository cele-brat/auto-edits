// Culling: start a cull on the server, watch it, filter by rating, change ratings by hand.
const Cull = { timer: null, wasBusy: false };

function drawRatings() {
  const all = wholeFolder();
  const counts = { keep: 0, maybe: 0, reject: 0 };
  for (const p of all) { const r = ratingOf(p); if (r) counts[r] += 1; }
  const any = counts.keep + counts.maybe + counts.reject > 0;
  $('ratings').hidden = !any;
  if (!any) S.rating = null;
  for (const b of $('ratings').querySelectorAll('button')) {
    const r = b.dataset.rating;
    b.setAttribute('aria-pressed', String((S.rating || '') === r));
    b.replaceChildren(r ? RATING_NAMES[r] : 'All', Object.assign(document.createElement('small'), { textContent: r ? counts[r] : all.length }));
  }
}

function redrawAfterRating() {
  drawRatings();
  if (!inFolder().some((p) => p.id === S.photoId)) S.photoId = (inFolder()[0] || {}).id || null;
  if (S.view === 'loupe' && !photo()) S.view = 'grid';
  drawGrid(); drawCaption(); drawPanel(); drawStage();
}

for (const b of $('ratings').querySelectorAll('button')) {
  b.onclick = () => { S.rating = b.dataset.rating || null; redrawAfterRating(); };
}

// Keys 3 / 2 / 1 rate the selected photo, 0 goes back to the automatic rating.
Cull.key = (key) => {
  const pick = { 3: 'keep', 2: 'maybe', 1: 'reject', 0: null }[key];
  if (pick === undefined || document.querySelector('dialog[open]')) return false;
  const a = document.activeElement;
  if (a && a.matches('input, textarea, select')) return false;
  const p = photo();
  if (!p) return false;
  const list = inFolder();
  const next = list[list.findIndex((x) => x.id === p.id) + 1];
  p.pick = pick;
  api(`/api/weddings/${S.wedding.id}/photos/${p.id}/pick`, { method: 'PUT', body: { pick } })
    .catch((err) => { $('saved').textContent = err.message; });
  // when a filter is on and the photo no longer belongs, carry on with the next one
  if (S.rating && ratingOf(p) !== S.rating && next) S.photoId = next.id;
  redrawAfterRating();
  return true;
};

async function refreshCull() {
  clearTimeout(Cull.timer);
  if (!S.wedding) return;
  let culls;
  try { culls = (await api(`/api/weddings/${S.wedding.id}/cull`)).culls; } catch (err) { $('cull-progress').textContent = err.message; return; }
  const busy = culls.filter((c) => c.state === 'waiting' || c.state === 'working');
  const failed = culls.filter((c) => c.state === 'failed');
  const done = culls.reduce((n, c) => n + (c.state === 'done' ? c.total : c.done), 0);
  const total = culls.reduce((n, c) => n + c.total, 0);
  $('cull-progress').textContent = !culls.length ? ''
    : busy.length ? `Culling: ${done} of ${total} photos looked at…`
    : `Finished: ${total} photos rated.` + (failed.length ? `\n${failed.length} folder(s) failed: ${failed[0].error}` : '');
  $('cull-open').textContent = busy.length ? `Culling ${Math.round(100 * done / Math.max(1, total))}%` : 'Cull';
  if (busy.length) { Cull.wasBusy = true; Cull.timer = setTimeout(refreshCull, 2500); return; }
  if (Cull.wasBusy) {                                   // just finished: fetch the new ratings
    Cull.wasBusy = false;
    const fresh = (await api(`/api/weddings/${S.wedding.id}/photos?compact=1`)).photos;
    const by = new Map(fresh.map((p) => [p.id, p]));
    for (const p of S.photos) { const f = by.get(p.id); if (f) { p.cull = f.cull; p.pick = f.pick; } }
    redrawAfterRating();
  }
}

$('cull-open').onclick = () => {
  if (!S.wedding) return;
  const f = folder();
  $('cull-scope').replaceChildren(new Option(`Whole wedding (${S.wedding.photos} photos)`, ''),
    ...S.wedding.folders.map((x) => new Option(`Only “${x.name}”`, x.id)));
  if (f) $('cull-scope').value = '';
  $('cull').showModal();
  refreshCull();
};
$('cull-close').onclick = () => $('cull').close();
$('cull-start').onclick = async () => {
  const scope = $('cull-scope').value;
  $('cull-start').disabled = true;
  try {
    await api(`/api/weddings/${S.wedding.id}/cull`, { method: 'POST', body: {
      folder: scope === '' ? null : +scope, strictness: $('cull-strictness').value, best_only: $('cull-best').checked } });
    Cull.wasBusy = true;
  } catch (err) { $('cull-progress').textContent = err.message; }
  $('cull-start').disabled = false;
  refreshCull();
};
