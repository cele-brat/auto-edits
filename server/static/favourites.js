// Preset favourites: a star on each preset, a "Favourites" group pinned at the top of the Presets
// panel, and the Amount remembered per favourite. Kept on the server per account (PUT
// /api/presets/{id}/favourite), so they follow the photographer to any browser.
'use strict';

const Fav = { map: new Map(), loaded: false, timer: null };

async function loadFavourites() {
  try {
    const r = await api('/api/presets/favourites');
    Fav.map = new Map(Object.entries(r.favourites || {}));
  } catch (e) { Fav.map = new Map(); }
  Fav.loaded = true;
  if (typeof drawLooks === 'function' && S.wedding) drawLooks();
}

async function setFavourite(id, on, amount) {
  if (on) Fav.map.set(id, amount == null ? (Fav.map.get(id) ?? 1) : amount); else Fav.map.delete(id);
  drawLooks();
  try { await api(`/api/presets/${encodeURIComponent(id)}/favourite`, { method: 'PUT', body: { on, amount } }); }
  catch (e) { if (e.message !== 'signed out') $('upload-note').textContent = e.message; }
}

// The star itself, used on a look row (its first variant) and on each variant chip.
function starFor(id) {
  const on = Fav.map.has(id);
  const b = el('button', { type: 'button', className: 'star', textContent: on ? '★' : '☆',
    title: on ? 'Favourite. Click to remove it from Favourites' : 'Add to Favourites' });
  b.setAttribute('aria-pressed', String(on));
  b.setAttribute('aria-label', on ? 'Remove from favourites' : 'Add to favourites');
  b.onclick = (e) => { e.stopPropagation(); setFavourite(id, !on, on ? undefined : panelLook().amount); };
  return b;
}

// Called by drawLooks for every look row: the star goes before the counts.
function decorateLook(row, l) {
  const id = l.variants.length === 1 ? l.variants[0].id : (l.variants.find((v) => Fav.map.has(v.id)) || l.variants[0]).id;
  const star = starFor(id);
  const first = row.querySelector('small');
  if (first) row.insertBefore(star, first); else row.append(star);
  if (Fav.map.has(id) && Fav.map.get(id) !== 1) row.append(el('small', { className: 'fav-amt', textContent: `${Math.round(Fav.map.get(id) * 100)}%` }));
}
function decorateVariant(chip, v) {
  const wrap = el('span', { className: 'fav' });
  wrap.append(chip, starFor(v.id));
  return wrap;
}

// The pinned group: every starred preset, in the order they were starred.
function favouriteRows(look) {
  if (!Fav.map.size) return [];
  const expanded = !S.openPacks.has('__favs_closed');
  const head = el('button', { type: 'button', className: 'pack favs' }, ['★ Favourites', el('small', { textContent: String(Fav.map.size) })]);
  head.setAttribute('aria-expanded', String(expanded));
  head.onclick = () => { if (expanded) S.openPacks.add('__favs_closed'); else S.openPacks.delete('__favs_closed'); drawLooks(); };
  const rows = [head];
  if (!expanded) return rows;
  const q = S.query.trim().toLowerCase();
  for (const [id, amount] of Fav.map) {
    const p = S.byPreset.get(id);
    if (!p || (q && !`${p.pack} ${p.name} ${(p.tags || []).join(' ')}`.toLowerCase().includes(q))) continue;
    const row = el('button', { type: 'button', className: 'look' }, [p.name]);
    row.dataset.preset = id;
    row.setAttribute('aria-expanded', String(look.preset === id));
    row.onclick = () => { S.openLook = `${p.pack}|${p.look || p.name}`; S.openPacks.add(p.pack); change({ preset: id }); };
    row.append(starFor(id));
    if (amount !== 1) row.append(el('small', { className: 'fav-amt', textContent: `${Math.round(amount * 100)}%` }));
    row.append(el('small', { textContent: p.pack === 'My presets' || p.pack === 'My style' ? p.pack : p.look && p.look !== p.name ? p.variant : '' }));
    rows.push(row);
  }
  return rows;
}

// Choosing a favourite brings its remembered Amount; moving Amount while a favourite is in use remembers it.
const changeBefore = change;
change = function (patch, live = false) {
  if (patch && 'preset' in patch && patch.preset && !('amount' in patch) && Fav.map.has(patch.preset) && patch.preset !== panelLook().preset) {
    patch = { ...patch, amount: Fav.map.get(patch.preset) };
  }
  const id = patch && 'amount' in patch && !('preset' in patch) ? panelLook().preset : null;
  changeBefore(patch, live);
  if (id && Fav.map.has(id)) {
    Fav.map.set(id, patch.amount);
    clearTimeout(Fav.timer);
    Fav.timer = setTimeout(() => api(`/api/presets/${encodeURIComponent(id)}/favourite`, { method: 'PUT', body: { on: true, amount: patch.amount } }).catch(() => {}), 700);
  }
};

loadFavourites();
