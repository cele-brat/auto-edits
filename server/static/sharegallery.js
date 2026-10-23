// Client galleries, the photographer's side: "Share as gallery…" (Account menu and the Export window),
// the list of galleries with their picks and a Revoke button, and the "Client picks" filter in the filmstrip.
'use strict';

const Sh = { timer: null };

function openShare() {
  if (!S.wedding) return;
  $('more').open = false;
  if ($('export').open) $('export').close();
  $('share-scope').replaceChildren(new Option(`Whole wedding (${S.wedding.photos} photos)`, ''),
    ...S.wedding.folders.map((f) => new Option(`Only “${f.name}”`, f.id)));
  const rated = S.photos.some((p) => p.pick || (p.cull && p.cull.r));
  $('share-ratings').value = rated ? 'keep' : '';
  $('share-name').value = S.wedding.name;
  $('share-password').value = '';
  $('share-days').value = '30';
  $('share-downloads').checked = false;
  $('share-error').textContent = '';
  $('share-done').hidden = true;
  shareCount();
  $('share-dialog').showModal();
  refreshGalleries();
}
function shareCount() {
  const scope = $('share-scope').value, ratings = $('share-ratings').value ? $('share-ratings').value.split(',') : [];
  const n = S.photos.filter((p) => (scope === '' || p.folder === +scope) && (!ratings.length || ratings.includes(p.pick || (p.cull || {}).r))).length;
  $('share-count').textContent = n ? `${n} photo${n === 1 ? '' : 's'} will be in the gallery. Free.` : 'No photos match: choose “Every photo” or rate some Keep first.';
  $('share-make').disabled = !n;
}
$('share-scope').onchange = shareCount;
$('share-ratings').onchange = shareCount;
$('share-close').onclick = () => $('share-dialog').close();
$('share-make').onclick = async () => {
  $('share-make').disabled = true;
  $('share-error').textContent = '';
  try {
    const scope = $('share-scope').value;
    const r = await api(`/api/weddings/${S.wedding.id}/galleries`, { method: 'POST', body: {
      name: $('share-name').value.trim(), folder: scope === '' ? null : +scope,
      ratings: $('share-ratings').value ? $('share-ratings').value.split(',') : [],
      password: $('share-password').value, days: +$('share-days').value, downloads: $('share-downloads').checked } });
    $('share-url').value = r.url;
    $('share-done').hidden = false;
    $('share-url').select();
    refreshGalleries();
  } catch (err) { $('share-error').textContent = err.message; }
  $('share-make').disabled = false;
};
$('share-copy').onclick = async () => {
  try { await navigator.clipboard.writeText($('share-url').value); $('share-copy').textContent = 'Copied'; setTimeout(() => { $('share-copy').textContent = 'Copy link'; }, 1500); }
  catch (e) { $('share-url').select(); }
};

async function refreshGalleries() {
  clearTimeout(Sh.timer);
  if (!$('share-dialog').open) return;
  let galleries;
  try { galleries = (await api('/api/galleries')).galleries; } catch (err) { $('share-error').textContent = err.message; return; }
  const list = $('galleries-list');
  if (!galleries.length) { list.replaceChildren(el('li', { className: 'how', textContent: 'No galleries yet.' })); return; }
  list.replaceChildren(...galleries.map((g) => {
    const li = el('li', { className: g.open ? '' : 'off' });
    const w = S.weddings.find((x) => x.id === g.wedding_id);
    li.append(el('b', { textContent: g.name + (w && w.name !== g.name ? ` · ${w.name}` : '') }));
    const ends = new Date(g.expires * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
    li.append(el('div', { className: 'how', textContent:
      `${g.photos} photos · ${g.views} view${g.views === 1 ? '' : 's'} · ${g.hearts} favourite${g.hearts === 1 ? '' : 's'}` +
      (g.comments ? ` · ${g.comments} comment${g.comments === 1 ? '' : 's'}` : '') + (g.sends ? ` · selection sent ${g.sends}×` : '') +
      (g.has_password ? ' · password' : '') + (g.downloads ? ' · downloads on' : '') +
      (g.revoked ? ' · revoked' : g.open ? ` · open until ${ends}` : ' · expired') }));
    const row = el('div', { className: 'row' });
    if (g.open) {
      const b = el('button', { type: 'button', className: 'quiet', textContent: 'Revoke' });
      b.onclick = async () => { if (!confirm('Revoke this gallery? The link stops working for the couple.')) return; await api(`/api/galleries/${g.id}`, { method: 'DELETE' }); refreshGalleries(); };
      row.append(b);
    }
    if (g.hearts || g.comments) {
      const b = el('button', { type: 'button', textContent: 'See picks' });
      b.onclick = async () => {
        const { photos } = await api(`/api/galleries/${g.id}/picks`);
        const old = li.querySelector('.picks'); if (old) old.remove();
        li.append(el('p', { className: 'picks', textContent: photos.map((p) => `${p.heart ? '♥ ' : ''}${p.name}${p.comment ? ` — “${p.comment}”` : ''}`).join(' · ') || 'Nothing yet.' }));
        if (g.wedding_id === (S.wedding || {}).id) { S.clientPicks = true; drawPicksFilter(); redrawAfterRating(); }
      };
      row.append(b);
    }
    li.append(row);
    return li;
  }));
  Sh.timer = setTimeout(refreshGalleries, 15000);
}

// ---- the "Client picks" filter in the filmstrip ------------------------------------------------
function drawPicksFilter() {
  let b = $('rating-client');
  const any = S.photos.some((p) => p.client_pick);
  if (!b) {
    b = el('button', { type: 'button', id: 'rating-client', textContent: 'Client picks', title: 'Photos the couple marked as favourites in their gallery' });
    b.onclick = () => { S.clientPicks = !S.clientPicks; if (S.clientPicks) S.rating = null; drawPicksFilter(); redrawAfterRating(); };
    $('ratings').append(b);
  }
  b.hidden = !any;
  if (any) { $('ratings').hidden = false; b.setAttribute('aria-pressed', String(!!S.clientPicks)); b.replaceChildren('Client picks', el('small', { textContent: String(wholeFolder().filter((p) => p.client_pick).length) })); }
  else S.clientPicks = false;
}
// inFolder() in app.js honours S.clientPicks; the other rating buttons switch it off
for (const b of $('ratings').querySelectorAll('button[data-rating]')) b.addEventListener('click', () => { S.clientPicks = false; drawPicksFilter(); });
const drawRatingsBefore = window.drawRatings;
if (drawRatingsBefore) drawRatings = function () { drawRatingsBefore(); drawPicksFilter(); if (S.clientPicks) $('ratings').hidden = false; };

// Lightroom-style entry points: Account menu, and a button in the Export window.
$('share-open').onclick = openShare;
$('share-from-export').onclick = openShare;
