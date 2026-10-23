// The Account window's Usage and Weddings tabs, "Delete my account", "Change email", and the STAGING banner.
// Plain browser JavaScript; the bar chart is hand-made SVG (no library).
'use strict';

function dashWhen(t) {
  return new Date(t * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}
function dashMB(bytes) {
  return bytes >= 1073741824 ? `${(bytes / 1073741824).toFixed(1)} GB` : `${Math.round(bytes / 1048576)} MB`;
}

// ---- Usage tab: a table with totals and a bar chart of credits per wedding ----
function usageChart(rows) {
  const box = $('usage-chart');
  box.replaceChildren();
  const top = rows.filter((r) => r.credits > 0).slice(0, 12);
  if (!top.length) { box.hidden = true; return; }
  box.hidden = false;
  const NS = 'http://www.w3.org/2000/svg';
  const W = 560, barH = 18, gap = 6, left = 170, right = 50;
  const H = top.length * (barH + gap) + gap;
  const max = Math.max(...top.map((r) => r.credits));
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Credits spent per wedding');
  top.forEach((r, i) => {
    const y = gap + i * (barH + gap);
    const label = document.createElementNS(NS, 'text');
    label.setAttribute('x', left - 8); label.setAttribute('y', y + barH * 0.72); label.setAttribute('text-anchor', 'end');
    label.setAttribute('class', 'usage-label');
    label.textContent = r.name.length > 24 ? r.name.slice(0, 23) + '…' : r.name;
    const bar = document.createElementNS(NS, 'rect');
    const w = Math.max(2, (W - left - right) * r.credits / max);
    bar.setAttribute('x', left); bar.setAttribute('y', y); bar.setAttribute('width', w); bar.setAttribute('height', barH);
    bar.setAttribute('rx', 3); bar.setAttribute('class', 'usage-bar');
    const title = document.createElementNS(NS, 'title');
    title.textContent = `${r.name}: ${r.credits} credits`;
    bar.append(title);
    const n = document.createElementNS(NS, 'text');
    n.setAttribute('x', left + w + 6); n.setAttribute('y', y + barH * 0.72); n.setAttribute('class', 'usage-num');
    n.textContent = r.credits.toLocaleString('en-IN');
    svg.append(label, bar, n);
  });
  box.append(svg);
}

async function drawUsage() {
  const table = $('usage');
  try {
    const { weddings, totals } = await api('/api/usage');
    const cell = (text, cls) => { const td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; return td; };
    const rows = weddings.map((w) => {
      const tr = document.createElement('tr');
      tr.append(cell(w.name + (w.archived ? ' (photos removed)' : '')), cell(w.photos.toLocaleString('en-IN'), 'n'),
        cell(w.exports.toLocaleString('en-IN'), 'n'), cell(w.lightroom.toLocaleString('en-IN'), 'n'), cell(String(w.culls), 'n'),
        cell(w.credits.toLocaleString('en-IN'), 'n'), cell(dashMB(w.bytes), 'n'), cell(dashWhen(w.last_activity)));
      if (!w.archived && S.weddings.some((x) => x.id === w.id)) {
        tr.className = 'go'; tr.title = 'Open this wedding';
        tr.onclick = () => { $('account').close(); openWedding(w.id); };
      }
      return tr;
    });
    if (!rows.length) { const tr = document.createElement('tr'); const td = cell('Nothing yet.'); td.colSpan = 8; tr.append(td); rows.push(tr); }
    table.tBodies[0].replaceChildren(...rows);
    const tf = document.createElement('tr');
    tf.append(cell(`Total · ${totals.weddings} wedding${totals.weddings === 1 ? '' : 's'}`), cell(totals.photos.toLocaleString('en-IN'), 'n'),
      cell(totals.exports.toLocaleString('en-IN'), 'n'), cell(totals.lightroom.toLocaleString('en-IN'), 'n'), cell(String(totals.culls), 'n'),
      cell(totals.credits.toLocaleString('en-IN'), 'n'), cell(dashMB(totals.bytes), 'n'), cell(''));
    table.tFoot.replaceChildren(tf);
    usageChart(weddings);
  } catch (err) { $('account-error').textContent = err.message; }
}

// ---- Weddings tab: expiry dates and "Keep for 180 more days" ----
async function drawWeddingList() {
  const table = $('wedding-list');
  try {
    const { weddings, keep_days, exempt } = await api('/api/account/weddings');
    $('weddings-note').hidden = exempt;
    const cell = (text, cls) => { const td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; return td; };
    const rows = weddings.map((w) => {
      const tr = document.createElement('tr');
      const soon = w.expires && (w.expires - Date.now() / 1000) < 15 * 86400;
      const action = document.createElement('td');
      if (w.archived) {
        tr.append(cell(w.name), cell(w.photos.toLocaleString('en-IN'), 'n'), cell(dashWhen(w.last_used)), cell('Photos removed', 'dim'), action);
      } else {
        tr.append(cell(w.name), cell(w.photos.toLocaleString('en-IN'), 'n'), cell(dashWhen(w.last_used)),
          cell(exempt ? 'Never (studio)' : dashWhen(w.expires), soon ? 'warn' : ''), action);
        if (!exempt) {
          const b = document.createElement('button');
          b.type = 'button'; b.textContent = `Keep for ${keep_days} more days`;
          b.onclick = async () => {
            b.disabled = true;
            try {
              const r = await api(`/api/weddings/${w.id}/keep`, { method: 'POST' });
              tr.children[2].textContent = dashWhen(Date.now() / 1000);
              tr.children[3].textContent = dashWhen(r.expires); tr.children[3].className = '';
              b.textContent = 'Kept';
            } catch (err) { $('account-error').textContent = err.message; b.disabled = false; }
          };
          action.append(b);
        }
      }
      return tr;
    });
    if (!rows.length) { const tr = document.createElement('tr'); const td = cell('No weddings yet.'); td.colSpan = 5; tr.append(td); rows.push(tr); }
    table.tBodies[0].replaceChildren(...rows);
  } catch (err) { $('account-error').textContent = err.message; }
}

// the tabs draw themselves when opened (the table is only fetched when looked at)
for (const b of document.querySelectorAll('#account [data-acc]')) {
  b.addEventListener('click', () => {
    if (b.dataset.acc === 'usage') drawUsage();
    if (b.dataset.acc === 'weddings') drawWeddingList();
  });
}
// /?account=weddings opens the window on that tab (the link in the warning email)
(() => {
  const asked = new URLSearchParams(location.search);
  if (asked.get('account') === 'weddings') {
    const open = () => { if (typeof openAccount === 'function' && Acc.user) { openAccount('weddings'); drawWeddingList(); } else setTimeout(open, 300); };
    setTimeout(open, 600);
  }
})();

// ---- Change email ----
$('email-form').onsubmit = async (e) => {
  e.preventDefault();
  $('account-error').textContent = ''; $('email-note').textContent = '';
  try {
    const r = await api('/api/account/email', { method: 'POST', body: { email: $('email-new').value.trim(), password: $('email-password').value } });
    $('email-note').textContent = `Check ${$('email-new').value.trim()}: we sent a link there. The link works for ${r.hours} hours. Your address stays as it is until you open it.`;
    $('email-password').value = '';
  } catch (err) { $('account-error').textContent = err.message; }
};

// ---- Delete my account ----
$('delete-form').onsubmit = async (e) => {
  e.preventDefault();
  $('account-error').textContent = ''; $('delete-note').textContent = '';
  if ($('delete-confirm').value.trim() !== 'DELETE') { $('account-error').textContent = 'Type DELETE (in capitals) to confirm.'; $('delete-confirm').focus(); return; }
  try {
    const r = await api('/api/account/delete', { method: 'POST', body: { password: $('delete-password').value, confirm: $('delete-confirm').value.trim() } });
    $('delete-note').textContent = `We sent a link to ${Acc.user.email}. Open it within ${r.hours} hours to delete the account. Nothing is deleted until you do.`;
    $('delete-password').value = ''; $('delete-confirm').value = '';
  } catch (err) { $('account-error').textContent = err.message; }
};

// ---- STAGING banner (the server says which copy this is) ----
(async () => {
  try {
    const s = await api('/api/session');
    $('env-banner').hidden = s.env !== 'staging';
    if (s.env === 'staging') document.title = 'STAGING · Auto Edits';
  } catch (x) { /* not signed in: nothing to show */ }
})();
