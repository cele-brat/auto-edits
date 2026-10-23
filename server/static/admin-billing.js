// /admin: refunds (with credit notes) and the Prices box (server/billing.py).
'use strict';
const PRICE_KEYS = ['credit_price_paise', 'launch_price_until', 'credit_price_after_paise', 'season_pack_credits', 'season_pack_paise',
  'later_pack_credits', 'later_pack_paise', 'block_paise_month'];

async function loadPrices() {
  try {
    const { settings, live } = await api('/api/admin/prices');
    for (const k of PRICE_KEYS) $(`pr-${k}`).value = settings[k] || '';
    $('prices-note').textContent = `Now: ${inr(live.credit_paise)} a credit` + (live.launch ? ` until ${live.launch_ends}` : '') +
      ` · Season pack ${fmt(live.season_credits)} for ${inr(live.season_paise)} · 50 GB ${inr(live.block_paise_month)} a month (all + GST)`;
  } catch (err) { /* shown by the gate */ }
}
$('prices').onsubmit = async (e) => {
  e.preventDefault();
  $('prices-error').textContent = '';
  const body = {};
  for (const k of PRICE_KEYS) body[k] = $(`pr-${k}`).value.trim();
  try {
    const r = await api('/api/admin/prices', { method: 'POST', body });
    $('prices-note').textContent = r.changed.length ? `Saved: ${r.changed.join(', ')} changed.` : 'Nothing changed.';
    await loadPrices();
    loadActivity();
  } catch (err) { $('prices-error').textContent = err.message; }
};

let refundOrder = null;
async function openRefund(o) {
  refundOrder = o;
  $('refund-error').textContent = '';
  $('refund-reason').value = '';
  $('refund-title').textContent = `Refund ${o.invoice_no}`;
  $('refund-who').textContent = `${o.email} · ${o.kind === 'storage' ? `${o.blocks * 50} GB × ${o.months} mo` : `${fmt(o.credits)} credits`} · paid ${inr(o.total)} on ${day(o.paid)}` + (o.test ? ' · TEST payment' : '');
  $('refund-list').replaceChildren();
  try {
    const d = await api(`/api/admin/purchases/${o.id}/refunds`);
    const can = d.refundable;
    $('refund-amount').max = (can.money / 100).toFixed(2);
    $('refund-amount').value = (can.money / 100).toFixed(2);
    $('refund-go').disabled = can.money <= 0;
    const note = can.credits ? ` ${fmt(can.credits)} credits were sold on it, ${fmt(can.balance)} are still in the account` + (can.spent ? ` (${fmt(can.spent)} already spent: a full refund is not possible)` : '') + '.' : '';
    $('refund-list').replaceChildren(el('li', { textContent: `Up to ${inr(can.money)} can still be refunded.${note}` }),
      ...d.refunds.map((r) => el('li', {}, el('a', { className: 'dl', href: `/credit-note/${r.id}`, target: '_blank', textContent: r.note_no }),
        document.createTextNode(` · ${inr(r.amount)} · ${r.credits ? fmt(r.credits) + ' credits back · ' : ''}${when(r.created)} · ${r.reason}`))));
  } catch (err) { $('refund-error').textContent = err.message; }
  $('refund').showModal();
}
$('refund-form').onsubmit = async (e) => {
  e.preventDefault();
  $('refund-error').textContent = '';
  const amount = Math.round(parseFloat($('refund-amount').value || '0') * 100);
  const reason = $('refund-reason').value.trim();
  if (!amount || amount < 1) { $('refund-error').textContent = 'Type the amount to refund, in rupees.'; return; }
  if (!reason) { $('refund-error').textContent = 'Give a reason: it goes on the credit note.'; return; }
  if (!window.confirm(`Refund ${inr(amount)} of ${refundOrder.invoice_no} to ${refundOrder.email}? This goes to Razorpay straight away.`)) return;
  $('refund-go').disabled = true;
  try {
    const r = await api(`/api/admin/purchases/${refundOrder.id}/refund`, { method: 'POST', body: { amount, reason } });
    $('refund-list').prepend(el('li', { textContent: `Done: ${inr(r.amount)} refunded, credit note ${r.note}` + (r.credits ? `, ${fmt(r.credits)} credits taken back (balance now ${fmt(r.balance)})` : '') + '. The credit note was emailed.' }));
    loadPurchases(); loadActivity();
  } catch (err) { $('refund-error').textContent = err.message; }
  $('refund-go').disabled = false;
};
$('refund-close').onclick = () => $('refund').close();
loadPrices();
