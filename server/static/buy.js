// Buying credits and extra storage with Razorpay Checkout (server/payments.py).
const Buy = { prices: null, kind: 'credits' };
const inr = (paise) => '₹' + (paise / 100).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

async function loadPrices() {
  if (!Buy.prices) Buy.prices = await api('/api/billing/prices');   // live prices, the Season pack, auto-renew
  return Buy.prices;
}

async function drawBuyButtons() {
  try {
    const p = await loadPrices();
    $('buy').disabled = !p.on; $('buy-storage').disabled = !p.on || Acc.unlimited;
    $('buy').textContent = p.on ? 'Buy credits' : 'Buy credits (coming soon)';
    $('buy').title = p.on ? '' : 'Payments are being added';
    $('buy-storage').hidden = !!Acc.unlimited;
  } catch (err) { /* stays "coming soon" */ }
}

async function drawPurchases() {
  try {
    const { purchases } = await api('/api/purchases');
    $('purchases-box').hidden = !purchases.length;
    $('purchases-none').hidden = !!purchases.length;
    $('purchases').tBodies[0].replaceChildren(...purchases.map((o) => {
      const tr = document.createElement('tr');
      tr.className = 'go'; tr.title = 'Open the invoice';
      tr.onclick = (e) => { if (!e.target.closest('a')) window.open(`/invoice/${o.id}`, '_blank'); };
      const what = o.kind === 'credits' ? `${o.credits.toLocaleString('en-IN')} credits` : `${o.blocks * 50} GB for ${o.months} month${o.months > 1 ? 's' : ''}`;
      const a = document.createElement('a'); a.href = `/invoice/${o.id}`; a.target = '_blank'; a.textContent = o.invoice_no;
      const cells = [when(o.paid), what, inr(o.total)].map((t, i) => { const td = document.createElement('td'); td.textContent = t; if (i === 2) td.className = 'n'; return td; });
      const inv = document.createElement('td'); inv.className = 'n'; inv.append(a);
      tr.append(...cells, inv);
      return tr;
    }));
  } catch (err) { /* nothing to show */ }
}

function buyTab(kind) {
  Buy.kind = kind;
  for (const b of document.querySelectorAll('#buy-dialog [data-kind]')) b.setAttribute('aria-selected', String(b.dataset.kind === kind));
  for (const d of document.querySelectorAll('#buy-dialog [data-for]')) d.hidden = d.dataset.for !== kind;
  $('buy-title').textContent = kind === 'credits' ? 'Buy credits' : kind === 'season' ? 'Season pack' : 'Add storage';
  quoteBuy();
}

async function openBuy(kind) {
  const p = await loadPrices();
  if (!p.on) return;
  $('buy-test').hidden = !p.test;
  if (!$('buy-state').options.length) {
    const first = new Option('Choose your state', ''); first.disabled = true; first.selected = true;
    $('buy-state').append(first, ...Object.entries(p.states).sort((a, b) => a[1].localeCompare(b[1])).map(([code, name]) => new Option(name, code)));
    try { const saved = localStorage.getItem('ae-state'); if (saved) $('buy-state').value = saved; } catch (x) { /* no storage */ }
    for (let b = 1; b <= p.max_blocks; b++) $('buy-blocks').append(new Option(`${b * p.block_gb} GB more`, b));
    for (const m of p.months) $('buy-months').append(new Option(`${m} month${m > 1 ? 's' : ''}`, m));
    $('buy-credits').min = p.min_credits; $('buy-credits').step = p.credit_step; $('buy-credits').value = p.min_credits;
  }
  const each = p.credit_paise * (100 + p.gst) / 100, least = p.min_credits * each;
  $('buy-credits-note').textContent = `${inr(p.credit_paise)} a credit` + (p.launch ? ` until ${p.launch_ends}` : '') +
    ` + ${p.gst}% GST (${inr(each)} with GST). From ${p.min_credits} credits (${inr(least)} incl. GST), in steps of ${p.credit_step}.`;
  $('buy-season-note').textContent = `${p.season_credits.toLocaleString('en-IN')} credits for ${inr(p.season_paise)} + ${p.gst}% GST: ` +
    `${inr(p.season_paise / p.season_credits)} a credit instead of ${inr(p.credit_paise)}.` + (p.launch ? ` This pack is on until ${p.launch_ends}.` : '');
  const renewing = (p.subscriptions || []).filter((x) => x.status === 'active' || x.status === 'created');
  $('buy-renew').checked = false; $('buy-renew').disabled = !!renewing.length;
  $('buy-grants').textContent = `${p.block_gb} GB for ${inr(p.block_paise_month)} a month, plus ${p.gst}% GST. ` +
    (p.grants.length ? 'Running now: ' + p.grants.map((g) => `${g.gb} GB until ${new Date(g.until * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}`).join(', ') + '.' : 'Starts as soon as you pay.') +
    (renewing.length ? ` Auto-renew is on (${renewing[0].blocks * p.block_gb} GB a month); stop it from the Purchases tab to change it.` : '');
  $('buy-error').textContent = '';
  buyTab(kind);
  $('buy-dialog').showModal();
}

function buyBody() {
  return { kind: Buy.kind, renew: Buy.kind === 'storage' && $('buy-renew').checked, credits: parseInt($('buy-credits').value || '0', 10), blocks: parseInt($('buy-blocks').value || '1', 10),
    months: parseInt($('buy-months').value || '1', 10), state: $('buy-state').value, gstin: $('buy-gstin').value.trim().toUpperCase(),
    name: $('buy-name').value.trim() };
}

let quoteTimer = 0;
function quoteBuy() {
  clearTimeout(quoteTimer);
  quoteTimer = setTimeout(async () => {
    $('buy-error').textContent = '';
    const body = buyBody();
    const p = Buy.prices || { min_credits: 500, credit_step: 100 };
    if (body.renew) { $('buy-months').value = 1; body.months = 1; $('buy-months').disabled = true; } else $('buy-months').disabled = false;
    const short = body.kind === 'credits' && (!body.credits || body.credits < p.min_credits);
    const odd = body.kind === 'credits' && !short && body.credits % p.credit_step;
    $('buy-min').hidden = !(short || odd);
    $('buy-min').textContent = short ? `Minimum ${p.min_credits} credits.` : odd ? `Credits come in steps of ${p.credit_step} (${body.credits - (body.credits % p.credit_step)} or ${body.credits - (body.credits % p.credit_step) + p.credit_step}).` : '';
    if (short || odd) { $('buy-sum').replaceChildren(); $('buy-go').disabled = true; $('buy-go').textContent = 'Pay'; return; }
    if (!body.state && !body.gstin) { $('buy-sum').replaceChildren(); $('buy-go').disabled = true; $('buy-min').hidden = false; $('buy-min').textContent = 'Choose your state to see the total with GST.'; return; }
    try {
      const q = await api('/api/buy/quote', { method: 'POST', body });
      const row = (a, b, cls) => { const tr = document.createElement('tr'); if (cls) tr.className = cls; for (const t of [a, b]) { const td = document.createElement('td'); td.textContent = t; tr.append(td); } return tr; };
      const rows = [row(q.kind === 'credits' ? `${q.credits.toLocaleString('en-IN')} credits × ${inr(q.unit)}` : q.kind === 'season' ? `Season pack: ${q.credits.toLocaleString('en-IN')} credits`
        : `${q.blocks * 50} GB × ${q.months} month${q.months > 1 ? 's' : ''}` + (body.renew ? ', every month' : ''), inr(q.taxable))];
      if (q.cgst) rows.push(row('CGST 9%', inr(q.cgst)), row('SGST 9%', inr(q.sgst))); else rows.push(row('IGST 18%', inr(q.igst)));
      rows.push(row('Total, incl. 18% GST', inr(q.total), 'total'));
      $('buy-sum').replaceChildren(...rows);
      $('buy-go').disabled = false;
      $('buy-go').textContent = body.renew ? `Pay ${inr(q.total)} a month` : `Pay ${inr(q.total)}`;
    } catch (err) { $('buy-sum').replaceChildren(); $('buy-go').disabled = true; $('buy-error').textContent = err.message; }
  }, 250);
}

// Razorpay's window cannot show above our open dialogs (they sit in the browser's top layer),
// so they close while it is open and come back if the payment is cancelled or fails.
function stepAside() {
  Buy.aside = ['account', 'buy-dialog'].filter((id) => $(id).open);
  for (const id of Buy.aside) $(id).close();
}
function comeBack() {
  for (const id of Buy.aside || []) if (!$(id).open) $(id).showModal();
  Buy.aside = [];
}

function checkoutScript() {
  return new Promise((resolve, reject) => {
    if (window.Razorpay) return resolve();
    const s = document.createElement('script');
    s.src = 'https://checkout.razorpay.com/v1/checkout.js';
    s.onload = resolve; s.onerror = () => reject(new Error('The payment window could not be loaded. Check your connection.'));
    document.head.append(s);
  });
}

$('buy-form').onsubmit = async (e) => {
  e.preventDefault();
  $('buy-error').textContent = '';
  $('buy-go').disabled = true;
  const label = $('buy-go').textContent;
  $('buy-go').textContent = 'Opening payment…';
  try {
    try { localStorage.setItem('ae-state', $('buy-state').value); } catch (x) { /* no storage */ }
    const body = buyBody();
    const renew = body.renew;
    const order = renew ? await api('/api/storage/subscribe', { method: 'POST', body }) : await api('/api/buy', { method: 'POST', body });
    await checkoutScript();
    const rzp = new window.Razorpay({
      key: order.key, ...(renew ? { subscription_id: order.subscription_id } : { order_id: order.rzp_order, amount: order.amount }), currency: 'INR',
      name: 'Auto Edits', description: order.what, prefill: { email: order.email, name: order.name },
      theme: { color: '#0d5b61' },
      handler: async (resp) => {
        try {
          const done = await api(renew ? '/api/storage/subscribe/verify' : '/api/buy/verify', { method: 'POST', body: resp });
          Buy.aside = [];
          $('buy-dialog').close();
          Buy.prices = null;
          notice(`Payment received, thank you. Invoice ${done.invoice} is in your account window and on its way by email.`);
          await openAccount();
        } catch (err) { comeBack(); $('buy-error').textContent = err.message; }
      },
      modal: { ondismiss: () => { comeBack(); $('buy-go').disabled = false; $('buy-go').textContent = label; } },
    });
    rzp.on('payment.failed', (r) => { comeBack(); $('buy-error').textContent = (r.error && r.error.description) || 'The payment did not go through.'; $('buy-go').disabled = false; });
    stepAside();
    rzp.open();
    $('buy-go').textContent = label;
  } catch (err) { $('buy-error').textContent = err.message; $('buy-go').disabled = false; $('buy-go').textContent = label; }
};

for (const b of document.querySelectorAll('#buy-dialog [data-kind]')) b.onclick = () => buyTab(b.dataset.kind);
for (const b of document.querySelectorAll('#buy-dialog .step')) b.onclick = () => {
  const v = Math.max(Buy.prices ? Buy.prices.min_credits : 500, (parseInt($('buy-credits').value || '0', 10) || 0) + parseInt(b.dataset.step, 10));
  $('buy-credits').value = v; quoteBuy();
};
for (const id of ['buy-credits', 'buy-blocks', 'buy-months', 'buy-state', 'buy-gstin', 'buy-renew']) $(id).addEventListener('input', quoteBuy);
$('buy').onclick = () => openBuy('credits');
$('buy-storage').onclick = () => openBuy('storage');
// the credits box and the storage line open the matching purchase
for (const card of document.querySelectorAll('#account .acc-card')) card.addEventListener('click', (e) => {
  if (e.target.closest('button, a')) return;
  if (card.contains($('account-storage'))) { if (!$('buy-storage').disabled) openBuy('storage'); }
  else if (!$('buy').disabled) openBuy('credits');
});
$('buy-close').onclick = () => $('buy-dialog').close();
