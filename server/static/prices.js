// The home page pricing card reads the live prices (/admin can change them without code).
(async () => {
  const $ = (id) => document.getElementById(id);
  const rs = (paise, digits) => '₹' + (paise / 100).toLocaleString('en-IN', { minimumFractionDigits: digits || 0, maximumFractionDigits: digits == null ? 2 : digits });
  const rsNode = (paise) => { const s = document.createElement('span'); s.className = 'rs'; s.textContent = '₹'; return [s, document.createTextNode((paise / 100).toLocaleString('en-IN', { maximumFractionDigits: 2 }))]; };
  try {
    const r = await fetch('/api/prices');
    if (!r.ok) return;
    const p = await r.json();
    const now = p.credit_paise, usual = p.credit_after_paise;
    $('price-now').replaceChildren(...rsNode(now));
    $('price-usual').hidden = !p.launch;
    if (p.launch) { $('price-usual').replaceChildren(...rsNode(usual)); $('price-until').textContent = p.launch_ends; }
    else $('price-until').parentElement.textContent = '+18% GST';
    const line = (credits) => { const dd = []; if (p.launch) { const s = document.createElement('s'); s.textContent = rs(credits * usual); dd.push(s, ' '); } dd.push(`${rs(credits * now)} + GST`); return dd; };
    $('price-export').replaceChildren(...line(1000));
    $('price-cull').replaceChildren(...line(500));
    $('price-least').textContent = `${rs(Math.round(p.min_credits * now * 1.18), 0)} incl. GST`;
    const each = p.season_paise / p.season_credits;
    const b = document.createElement('b'); b.textContent = 'Season pack:';
    $('price-season').replaceChildren(b, ` ${p.season_credits.toLocaleString('en-IN')} credits for ${rs(p.season_paise)} + GST (${rs(each, 2)} a credit), one-time` + (p.launch ? `, until ${p.launch_ends}.` : '.'));
  } catch (err) { /* the page keeps its printed prices */ }
})();
