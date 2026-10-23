// Payments phase 2 in the app: the low-credit strip, auto-renew storage in the account window, credit notes.
const LOW_CREDITS = 50;

function drawLowCredits() {
  let hidden = false;
  try { hidden = !!sessionStorage.getItem('ae-low-hidden'); } catch (x) { /* no storage */ }
  const low = Acc.user && !Acc.unlimited && Acc.user.verified && Acc.credits < LOW_CREDITS && !hidden;
  $('low-credits').hidden = !low;
  if (low) $('low-credits-text').textContent = Acc.credits <= 0 ? 'You have no credits left: exports and culling will stop. '
    : `Only ${Acc.credits.toLocaleString('en-IN')} credit${Acc.credits === 1 ? '' : 's'} left. `;
}
$('low-credits-buy').onclick = () => { if (typeof openBuy === 'function') openBuy('credits'); };
$('low-credits-x').onclick = () => { try { sessionStorage.setItem('ae-low-hidden', '1'); } catch (x) { /* fine */ } $('low-credits').hidden = true; };
// the strip follows the balance: after every refresh of the credits (exports, culling, purchases)
for (const name of ['refreshCredits', 'showAccount']) {
  const was = window[name];
  if (typeof was === 'function') window[name] = async (...a) => { const r = await was(...a); drawLowCredits(); return r; };
}

async function drawRenew() {
  try {
    const p = await api('/api/billing/prices');
    Buy.prices = p;
    const live = (p.subscriptions || []).filter((s) => s.status !== 'cancelled');
    $('renew-box').hidden = !live.length;
    if (live.length) {
      const s = live[0];
      const next = s.next_charge ? new Date(s.next_charge * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
      $('renew-text').textContent = s.status === 'cancelling' ? `Auto-renew is stopping: your ${s.blocks * p.block_gb} GB stays until the end of the month already paid, then no more is charged.`
        : s.status === 'halted' ? `The last renewal of your ${s.blocks * p.block_gb} GB could not be charged. Razorpay will retry; if it keeps failing the extra storage ends.`
        : s.status === 'created' ? 'Auto-renew was started but the first payment has not gone through yet.'
        : `${s.blocks * p.block_gb} GB a month, ${inr(s.amount)} incl. GST, next charge on ${next}. We email you 3 days before.`;
      $('renew-stop').hidden = !(s.status === 'active' || s.status === 'created');
      $('renew-stop').onclick = async () => {
        $('renew-stop').disabled = true;
        try {
          const r = await api('/api/storage/cancel', { method: 'POST', body: { sub: s.id } });
          notice(r.status === 'cancelling' ? 'Auto-renew will stop at the end of the month already paid.' : 'Auto-renew stopped.');
          Buy.prices = null; drawRenew();
        } catch (err) { $('account-error').textContent = err.message; }
        $('renew-stop').disabled = false;
      };
    }
  } catch (err) { /* nothing to show */ }
  try {
    const { refunds } = await api('/api/refunds');
    $('refunds-box').hidden = !refunds.length;
    $('refunds-list').replaceChildren(...refunds.map((r) => {
      const li = document.createElement('li');
      const a = document.createElement('a'); a.href = `/credit-note/${r.id}`; a.target = '_blank'; a.textContent = r.note_no;
      li.append(a, ` · ${inr(r.amount)} refunded against ${r.invoice_no}` + (r.credits ? ` · ${r.credits.toLocaleString('en-IN')} credits taken back` : '') + ` · ${when(r.created)}`);
      return li;
    }));
  } catch (err) { /* nothing to show */ }
}
{
  const was = window.drawPurchases;
  if (typeof was === 'function') window.drawPurchases = async (...a) => { const r = await was(...a); drawRenew(); return r; };
}
