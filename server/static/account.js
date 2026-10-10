// The account: credits in the top bar, the "Your account" window, and messages about the email address.
const Acc = { user: null, credits: 0, unlimited: false };

const KINDS = { welcome: 'Free credits', cull: 'Culling', export: 'Export', refund: 'Given back', lightroom: 'Sent to Lightroom', purchase: 'Bought' };

function creditLabel() {
  return Acc.unlimited ? 'Studio · unlimited' : `${Acc.credits.toLocaleString('en-IN')} credit${Acc.credits === 1 ? '' : 's'}`;
}

function costLine(credits, what) {
  if (Acc.unlimited) return `${what}: ${credits} credit${credits === 1 ? '' : 's'} (studio account, not limited).`;
  if (!Acc.user || !Acc.user.verified) return `${what}: ${credits} credits. Confirm your email first.`;
  const short = credits > Acc.credits;
  return `${what}: ${credits} credit${credits === 1 ? '' : 's'}. You have ${Acc.credits}.` + (short ? ' That is not enough yet.' : '');
}

function notice(text, action, run) {
  $('notice-text').textContent = text;
  $('notice-action').hidden = !action;
  if (action) { $('notice-action').textContent = action; $('notice-action').onclick = run; }
  $('notice').hidden = false;
}
$('notice-x').onclick = () => { $('notice').hidden = true; };

function showAccount(session) {
  Acc.user = session.user;
  Acc.credits = session.credits;
  Acc.unlimited = !!(session.user && session.user.admin);
  $('credits').textContent = creditLabel();
  const asked = new URLSearchParams(location.search);
  if (asked.has('verified')) {
    notice('Your email is confirmed. Your free credits are in your account.');
  } else if (Acc.user && !Acc.user.verified) {
    notice(asked.has('welcome') ? `Welcome. We sent a link to ${Acc.user.email}: open it to confirm your email and get 50 free credits.`
      : `Confirm your email (${Acc.user.email}) to start culling and exporting.`, 'Send the link again', sendAgain);
  }
  if (asked.has('verified') || asked.has('welcome')) history.replaceState(null, '', '/');
}

async function sendAgain() {
  try {
    const r = await api('/api/verify/again', { method: 'POST' });
    notice(r.verified ? 'Your email is already confirmed.' : `Sent. Check the inbox (and spam folder) of ${Acc.user.email}.`);
  } catch (err) { notice(err.message); }
}

async function refreshCredits() {
  try {
    const me = await api('/api/me');
    Acc.user = me.user; Acc.credits = me.credits; Acc.unlimited = me.user.admin;
    $('credits').textContent = creditLabel();
  } catch (err) { /* the next action will say what is wrong */ }
}

function when(t) {
  return new Date(t * 1000).toLocaleString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

async function openAccount() {
  $('more').open = false;
  $('account-error').textContent = '';
  $('key-shown').hidden = true;
  $('account').showModal();
  await refreshCredits();
  $('account-who').textContent = `${Acc.user.name ? Acc.user.name + ' · ' : ''}${Acc.user.email}` + (Acc.user.verified ? '' : ' · email not confirmed yet');
  $('account-balance').textContent = Acc.unlimited ? 'Unlimited' : Acc.credits.toLocaleString('en-IN');
  $('password-title').textContent = Acc.user.has_password ? 'Change password' : 'Set a password';
  $('password-current').hidden = !Acc.user.has_password;
  try {
    const { history } = await api('/api/credits');
    const rows = history.map((h) => {
      const tr = document.createElement('tr');
      const n = document.createElement('td'); n.className = 'n ' + (h.delta < 0 ? 'out' : 'in');
      n.textContent = (h.delta > 0 ? '+' : '') + h.delta;
      const what = document.createElement('td'); what.textContent = h.note || KINDS[h.kind] || h.kind;
      const d = document.createElement('td'); d.textContent = when(h.created);
      tr.append(d, what, n);
      return tr;
    });
    if (!rows.length) { const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = 3; td.textContent = 'Nothing yet.'; tr.append(td); rows.push(tr); }
    $('history').tBodies[0].replaceChildren(...rows);
  } catch (err) { $('account-error').textContent = err.message; }
}

$('credits').onclick = openAccount;
$('account-open').onclick = openAccount;
$('account-close').onclick = () => $('account').close();
$('key-new').onclick = async () => {
  try {
    const { key } = await api('/api/plugin-key', { method: 'POST' });
    $('key-value').value = key;
    $('key-shown').hidden = false;
    $('key-value').select();
  } catch (err) { $('account-error').textContent = err.message; }
};
$('key-copy').onclick = async () => {
  try { await navigator.clipboard.writeText($('key-value').value); $('key-copy').textContent = 'Copied'; }
  catch (x) { $('key-value').select(); document.execCommand('copy'); $('key-copy').textContent = 'Copied'; }
  setTimeout(() => { $('key-copy').textContent = 'Copy'; }, 2000);
};
$('password-form').onsubmit = async (e) => {
  e.preventDefault();
  $('account-error').textContent = '';
  const password = $('password-new').value;
  if (password.length < 8) { $('account-error').textContent = 'The new password needs at least 8 characters.'; return; }
  try {
    await api('/api/password', { method: 'POST', body: { current: $('password-current').value, password } });
    location.href = '/?login=1';                  // a new password signs every browser out, this one too
  } catch (err) { $('account-error').textContent = err.message; }
};
