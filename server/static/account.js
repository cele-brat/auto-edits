// The account: credits in the top bar, the "Your account" window, and messages about the email address.
const Acc = { user: null, credits: 0, unlimited: false };

const KINDS = { welcome: 'Free credits', cull: 'Culling', export: 'Export', refund: 'Given back', lightroom: 'Sent to Lightroom', purchase: 'Bought', admin: 'From Cele-Brat' };

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
  if (session.viewing_as) {
    $('viewing-text').textContent = `Viewing as ${session.user.email} (read-only). Previews look exactly as they see them.`;
    $('viewing').hidden = false;
    document.body.classList.add('viewing');
  }
  $('admin-link').hidden = !(session.user && session.user.admin) || !!session.viewing_as;
  Acc.user = session.user;
  Acc.credits = session.credits;
  Acc.unlimited = !!(session.user && session.user.admin);
  $('credits').textContent = creditLabel();
  const asked = new URLSearchParams(location.search);
  if (session.viewing_as) {
    /* the admin is looking: no messages meant for the photographer */
  } else if (asked.get('verified') === '1') {
    notice('Your email is confirmed. Your free credits are in your account.');
  } else if (asked.get('verified') === '0') {
    notice('Your email is confirmed. Free credits were already given to an account from this internet connection, so this one starts at 0. Buy credits to begin.');
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
  $('password-new').placeholder = Acc.user.admin ? 'New password (12 or more characters)' : 'New password (8 or more characters)';
  $('code-needed').hidden = true;
  drawTwoStep();
  try {
    const { history } = await api('/api/credits');
    const rows = history.map((h) => {
      const tr = document.createElement('tr');
      const n = document.createElement('td'); n.className = 'n ' + (h.delta < 0 ? 'out' : 'in');
      n.textContent = (h.delta > 0 ? '+' : '') + h.delta;
      const what = document.createElement('td'); what.textContent = h.note || KINDS[h.kind] || h.kind;
      const d = document.createElement('td'); d.textContent = when(h.created);
      const b = document.createElement('td'); b.className = 'n dim'; b.textContent = Acc.unlimited ? '' : h.balance.toLocaleString('en-IN');
      tr.append(d, what, n, b);
      return tr;
    });
    if (!rows.length) { const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = 4; td.textContent = 'Nothing yet.'; tr.append(td); rows.push(tr); }
    $('history').tBodies[0].replaceChildren(...rows);
  } catch (err) { $('account-error').textContent = err.message; }
}

$('credits').onclick = openAccount;
$('account-open').onclick = openAccount;
$('account-close').onclick = () => $('account').close();
$('account').addEventListener('close', () => {      // backup codes and the setup QR never stay on screen
  $('twostep-codes').hidden = true; $('twostep-list').textContent = '';
  $('twostep-setup').hidden = true; $('twostep-qr').innerHTML = '';
});
// The studio account: some steps need the 6-digit code once per 12 hours on this sign-in.
function askCode(then) {
  $('account-error').textContent = 'Enter the 6-digit code from your authenticator app to carry on.';
  $('code-needed').hidden = false;
  $('code-needed-input').value = '';
  $('code-needed-input').focus();
  $('code-needed').onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api('/api/admin/confirm', { method: 'POST', body: { code: $('code-needed-input').value.trim() } });
      $('code-needed').hidden = true;
      $('account-error').textContent = '';
      then();
    } catch (err) { $('account-error').textContent = err.message; $('code-needed-input').select(); }
  };
}

async function makeKey() {
  $('account-error').textContent = '';
  try {
    const { key } = await api('/api/plugin-key', { method: 'POST' });
    $('key-value').value = key;
    $('key-shown').hidden = false;
    $('key-value').select();
    drawTwoStep();
  } catch (err) {
    if (err.status === 428) askCode(makeKey);
    else $('account-error').textContent = err.message;
  }
}
$('key-new').onclick = makeKey;

// ---- two-step login (studio account) ----
async function drawTwoStep() {
  $('twostep').hidden = !(Acc.user && Acc.user.admin);
  if ($('twostep').hidden) return;
  let t;
  try { t = await api('/api/two-step'); } catch (err) { return; }
  const st = t.studio || {};
  const left = st.studio_key_retired ? 'The old shared studio key is retired.'
    : 'The old shared studio key stops working once all three are done: '
      + [['password', 'a password'], ['two_step', 'two-step login'], ['lightroom_key', 'your own Lightroom key']]
        .map(([k, label]) => (st[k] ? '✓ ' : '✗ ') + label).join(' · ') + '.';
  $('twostep-state').textContent = (t.on ? `On. Backup codes left: ${t.backup_codes_left}. ` : 'Off. With it on, logging in needs your password and a code from your phone. ') + left;
  $('twostep-actions').hidden = t.on;
  $('twostep-renew').hidden = !t.on;
}

$('twostep-start').onclick = async () => {
  $('account-error').textContent = '';
  try {
    const r = await api('/api/two-step/start', { method: 'POST' });
    $('twostep-qr').innerHTML = r.qr;                 // SVG made by our own server
    $('twostep-secret').textContent = r.secret.replace(/(.{4})/g, '$1 ').trim();
    $('twostep-setup').hidden = false;
    $('twostep-actions').hidden = true;
    $('twostep-code').focus();
  } catch (err) { $('account-error').textContent = err.message; }
};

function showBackupCodes(codes) {
  $('twostep-list').textContent = codes.join('\n');
  $('twostep-codes').hidden = false;
  $('twostep-copy').onclick = async () => {
    try { await navigator.clipboard.writeText(codes.join('\n')); $('twostep-copy').textContent = 'Copied'; } catch (x) { /* select by hand */ }
  };
  $('twostep-save').onclick = () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([`Auto Edits backup codes for ${Acc.user.email}\nEach code works once.\n\n${codes.join('\n')}\n`], { type: 'text/plain' }));
    a.download = 'auto-edits-backup-codes.txt';
    a.click();
  };
}

$('twostep-form').onsubmit = async (e) => {
  e.preventDefault();
  $('account-error').textContent = '';
  try {
    const r = await api('/api/two-step/confirm', { method: 'POST', body: { code: $('twostep-code').value.trim() } });
    $('twostep-setup').hidden = true;
    $('twostep-qr').innerHTML = '';
    showBackupCodes(r.backup_codes);
    drawTwoStep();
  } catch (err) { $('account-error').textContent = err.message; $('twostep-code').select(); }
};

$('twostep-renew').onsubmit = async (e) => {
  e.preventDefault();
  $('account-error').textContent = '';
  try {
    const r = await api('/api/two-step/backup-codes', { method: 'POST', body: { code: $('twostep-renew-code').value.trim() } });
    $('twostep-renew-code').value = '';
    showBackupCodes(r.backup_codes);
    drawTwoStep();
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
  const min = Acc.user && Acc.user.admin ? 12 : 8;
  if (password.length < min) { $('account-error').textContent = `The new password needs at least ${min} characters.`; return; }
  try {
    await api('/api/password', { method: 'POST', body: { current: $('password-current').value, password } });
    location.href = '/?login=1';                  // a new password signs every browser out, this one too
  } catch (err) { $('account-error').textContent = err.message; }
};

$('viewing-stop').onclick = async () => { await api('/api/admin/view-as-stop', { method: 'POST' }).catch(() => {}); location.href = '/admin'; };
$('originals').onclick = () => {
  $('more').open = false;
  if (!S.wedding) return;
  location.href = `/api/weddings/${S.wedding.id}/originals.zip`;
};
