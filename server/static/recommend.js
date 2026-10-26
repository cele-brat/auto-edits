// Recommended presets: the top three looks for the folder on screen, from what its photos look like
// (server/recommend.py), pinned above the packs in the Presets panel. Fetched when a folder opens
// and again while the worker is still measuring new photos.
const Rec = { folderId: null, count: -1, data: null, timer: null, loading: false };

async function loadRecommended(force = false) {
  const f = folder();
  if (!f || !S.wedding) { Rec.data = null; return; }
  if (!force && Rec.folderId === f.id && Rec.count === f.photos && Rec.data && !Rec.data.pending) return;
  if (Rec.loading) return;
  Rec.loading = true;
  const id = f.id;
  try {
    const d = await api(`/api/weddings/${S.wedding.id}/folders/${id}/recommended`);
    if (folder() && folder().id === id) {
      Rec.folderId = id; Rec.count = f.photos; Rec.data = d;
      drawLooks();
      clearTimeout(Rec.timer);
      if (d.pending > 0) Rec.timer = setTimeout(() => loadRecommended(true), 8000);   // more photos still being measured
    }
  } catch (e) {
    /* the panel just shows no recommendations */
  } finally {
    Rec.loading = false;
  }
}

// The pinned group, drawn by drawLooks (app.js) above Favourites.
function recommendedRows(look) {
  const f = folder();
  if (!f) return [];
  if (Rec.folderId !== f.id || Rec.count !== f.photos) setTimeout(() => loadRecommended(), 0);
  const d = Rec.folderId === f.id ? Rec.data : null;
  const picks = (d && d.recommended) || [];
  const q = S.query.trim().toLowerCase();
  if (q) return [];                                                                   // a search shows the packs only
  const expanded = !S.openPacks.has('__rec_closed');
  const head = el('button', { type: 'button', className: 'pack recs' }, ['✦ Recommended', el('small', { textContent: d && d.scene ? d.scene : (d && d.pending ? 'measuring…' : '') })]);
  head.setAttribute('aria-expanded', String(expanded));
  head.onclick = () => { if (expanded) S.openPacks.add('__rec_closed'); else S.openPacks.delete('__rec_closed'); drawLooks(); };
  const rows = [head];
  if (!expanded) return rows;
  if (!picks.length) {
    rows.push(el('div', { className: 'rec-note', textContent: d && d.pending ? 'Measuring the photos…' : (d && !d.n ? 'Add photos to see suggestions.' : 'No suggestions yet.') }));
    return rows;
  }
  picks.forEach((p, i) => {
    const row = el('button', { type: 'button', className: 'look rec' }, [el('span', { className: 'rec-n', textContent: String(i + 1) }), p.look]);
    row.dataset.preset = p.id;
    row.title = `${p.name} · ${p.pack}${p.why && p.why.length ? ' · ' + p.why.join(', ') : ''}`;
    row.setAttribute('aria-expanded', String(look.preset === p.id));
    row.onclick = () => { S.openLook = `${p.pack}|${p.look}`; S.openPacks.add(p.pack); change({ preset: p.id }); };
    row.append(el('small', { textContent: p.why && p.why.length ? p.why.slice(0, 2).join(' · ') : p.pack }));
    rows.push(row);
  });
  return rows;
}

// A new folder on screen means new recommendations.
const openFolderBefore = openFolder;
openFolder = function (id) {
  openFolderBefore(id);
  loadRecommended();
};
