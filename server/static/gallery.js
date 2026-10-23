/* The public client gallery (/g/<token>): the couple's photos, hearts, comments, "Send my selection".
   No account data reaches this page: only what /api/g/<token> gives. */
'use strict';

const $ = (id) => document.getElementById(id);
const token = location.pathname.split('/').pop();
const G = { photos: [], at: -1, onlyHearts: false, downloads: false };

async function call(path, options = {}) {
  const init = { method: options.method || 'GET', headers: { 'X-Requested-With': 'AutoEdits' }, credentials: 'same-origin' };
  if ('body' in options) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(options.body); }
  const r = await fetch(path, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `The server answered ${r.status}.`);
  return data;
}
const imageUrl = (p, size) => `/api/g/${token}/photos/${p.id}/image?size=${size}`;

function shown() { return G.onlyHearts ? G.photos.filter((p) => p.heart) : G.photos; }

function drawGrid() {
  const list = shown();
  $('g-grid').replaceChildren(...list.map((p) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'g-cell'; b.dataset.id = p.id;
    if (p.w && p.h) b.style.aspectRatio = `${p.w} / ${p.h}`;
    const img = document.createElement('img'); img.loading = 'lazy'; img.alt = ''; img.src = imageUrl(p, 360);
    b.append(img);
    const h = document.createElement('span'); h.className = `g-h${p.heart ? ' on' : ''}`; h.textContent = '♥';
    h.title = p.heart ? 'A favourite' : 'Mark as a favourite';
    h.onclick = (e) => { e.stopPropagation(); toggleHeart(p); };
    b.append(h);
    if (p.comment) { const c = document.createElement('span'); c.className = 'g-c'; c.textContent = '💬'; c.title = p.comment; b.append(c); }
    b.onclick = () => openLight(G.photos.indexOf(p));
    return b;
  }));
  $('g-empty').hidden = !!list.length;
  $('g-empty').textContent = G.onlyHearts ? 'No favourites yet. Tap the heart on a photo you love.' : 'Nothing to show.';
  const hearts = G.photos.filter((p) => p.heart).length;
  $('g-count').textContent = `${G.photos.length} photo${G.photos.length === 1 ? '' : 's'} · ${hearts} favourite${hearts === 1 ? '' : 's'}`;
  $('g-only-hearts').setAttribute('aria-pressed', String(G.onlyHearts));
}

async function toggleHeart(p) {
  p.heart = !p.heart;
  drawGrid();
  if (G.at >= 0 && G.photos[G.at] === p) drawLightTools();
  try { await call(`/api/g/${token}/photos/${p.id}/heart`, { method: 'PUT', body: { on: p.heart } }); }
  catch (e) { p.heart = !p.heart; drawGrid(); alert(e.message); }
}

// ---- the lightbox ---------------------------------------------------------------------------
function openLight(i) {
  if (i < 0 || i >= G.photos.length) return;
  G.at = i;
  const p = G.photos[i];
  $('g-big').src = imageUrl(p, 1600);
  $('g-light').hidden = false;
  document.body.classList.add('light-open');
  drawLightTools();
  for (const k of [i - 1, i + 1]) if (G.photos[k]) new Image().src = imageUrl(G.photos[k], 1600);
}
function drawLightTools() {
  const p = G.photos[G.at];
  if (!p) return;
  $('g-heart').setAttribute('aria-pressed', String(!!p.heart));
  $('g-heart').querySelector('span').textContent = p.heart ? 'Favourite' : 'Add to favourites';
  $('g-comment').value = p.comment || '';
  $('g-saved').textContent = '';
  $('g-download').hidden = !G.downloads;
  $('g-download').href = `/api/g/${token}/photos/${p.id}/download`;
  $('g-prev').disabled = G.at <= 0;
  $('g-next').disabled = G.at >= G.photos.length - 1;
}
function closeLight() { $('g-light').hidden = true; document.body.classList.remove('light-open'); G.at = -1; }
$('g-close').onclick = closeLight;
$('g-prev').onclick = () => openLight(G.at - 1);
$('g-next').onclick = () => openLight(G.at + 1);
$('g-heart').onclick = () => toggleHeart(G.photos[G.at]);
let commentTimer = null;
$('g-comment').oninput = () => {
  const p = G.photos[G.at];
  clearTimeout(commentTimer);
  $('g-saved').textContent = 'Saving…';
  commentTimer = setTimeout(async () => {
    try {
      const r = await call(`/api/g/${token}/photos/${p.id}/comment`, { method: 'PUT', body: { text: $('g-comment').value } });
      p.comment = r.comment;
      $('g-saved').textContent = 'Saved';
      drawGrid();
    } catch (e) { $('g-saved').textContent = e.message; }
  }, 500);
};
document.addEventListener('keydown', (e) => {
  if ($('g-light').hidden || e.target.matches('input, textarea')) return;
  if (e.key === 'Escape') closeLight();
  else if (e.key === 'ArrowLeft') openLight(G.at - 1);
  else if (e.key === 'ArrowRight') openLight(G.at + 1);
  else if (e.key === 'h' || e.key === 'H') toggleHeart(G.photos[G.at]);
});
$('g-light').addEventListener('click', (e) => { if (e.target === $('g-light')) closeLight(); });
let touchX = null;
$('g-light').addEventListener('touchstart', (e) => { touchX = e.touches[0].clientX; }, { passive: true });
$('g-light').addEventListener('touchend', (e) => {
  if (touchX == null) return;
  const dx = e.changedTouches[0].clientX - touchX; touchX = null;
  if (Math.abs(dx) > 50) openLight(G.at + (dx < 0 ? 1 : -1));
});

// ---- sending the selection ------------------------------------------------------------------
$('g-only-hearts').onclick = () => { G.onlyHearts = !G.onlyHearts; drawGrid(); };
$('g-send').onclick = () => {
  const hearts = G.photos.filter((p) => p.heart).length, comments = G.photos.filter((p) => p.comment).length;
  $('g-send-text').textContent = `${hearts} favourite${hearts === 1 ? '' : 's'}` + (comments ? ` and ${comments} comment${comments === 1 ? '' : 's'}` : '') +
    ' will be sent to your photographer. You can change them and send again later.';
  $('g-send-error').textContent = '';
  $('g-send-dialog').showModal();
};
$('g-send-cancel').onclick = () => $('g-send-dialog').close();
$('g-send-go').onclick = async () => {
  $('g-send-go').disabled = true;
  try {
    await call(`/api/g/${token}/send`, { method: 'POST', body: { email: $('g-email').value.trim(), note: $('g-note').value.trim() } });
    $('g-send-dialog').close();
    $('g-sub').textContent = 'Your selection has been sent. Thank you!';
  } catch (e) { $('g-send-error').textContent = e.message; }
  $('g-send-go').disabled = false;
};

// ---- the password, and loading ---------------------------------------------------------------
$('g-lock').onsubmit = async (e) => {
  e.preventDefault();
  $('g-lock-error').textContent = '';
  try {
    await call(`/api/g/${token}/unlock`, { method: 'POST', body: { password: $('g-password').value } });
    await load();
  } catch (err) { $('g-lock-error').textContent = err.message; }
};

async function load() {
  let data;
  try { data = await call(`/api/g/${token}`); }
  catch (e) { $('g-name').textContent = 'This gallery is not available'; $('g-sub').textContent = e.message; return; }
  $('g-name').textContent = data.name;
  document.title = `${data.name} · photos`;
  if (data.locked) { $('g-lock').hidden = false; $('g-password').focus(); return; }
  $('g-lock').hidden = true;
  G.photos = data.photos; G.downloads = data.downloads;
  const ends = new Date(data.expires * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
  $('g-sub').textContent = `Tap the heart on the photos you love, add a comment if you like, then “Send my selection”. This gallery is open until ${ends}.`;
  $('g-bar').hidden = false;
  $('g-grid').hidden = false;
  drawGrid();
}
load();
