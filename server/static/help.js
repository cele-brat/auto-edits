// Help: type a question and get the matching answers from the help below (searched here in the
// browser, nothing is sent anywhere), with "Show me" to point at the place on screen; the
// frequently asked questions when nothing is typed; the tour again. Account menu › Help, or ?.
'use strict';

// t: the part of the screen "Show me" points at. open: a menu to open first. faq: listed when nothing is typed.
// k: extra words people may use for it. a: trusted text written here (it may hold <b>).
const HELP = [
  // ---- getting started
  { q: 'What is Auto Edits?', faq: true, k: 'about start begin new overview',
    a: 'An online tool for wedding photographers: it culls a whole wedding, applies your presets with corrections worked out for each photo, and exports the finished JPEGs on our server, so your own computer stays free. Use it on its own in this browser, or with the Lightroom Classic plug-in.' },
  { q: 'How do I add photos?', faq: true, t: 'add-photos', k: 'upload import add photos folder pictures images wedding start new',
    a: 'Click <b>Add photos…</b> at the bottom left and pick the wedding folder on this computer. Sub-folders (Haldi, Sangeet, Reception…) become event folders. Only the preview inside each raw file is uploaded, so it is quick. From Lightroom, use the plug-in instead (see “How do I install the Lightroom plug-in?”).' },
  { q: 'Are my raw files uploaded?', faq: true, k: 'raw nef cr3 arw upload original files privacy',
    a: 'No. Raw files stay on your computer. Only the preview stored inside each raw file is uploaded, which keeps uploads fast and your originals at home.' },
  { q: 'How do I add more photos or folders to a wedding?', t: 'folder-plus', k: 'add more folder event photos plus missing',
    a: 'Use <b>+</b> next to Folders: “Add a folder…” adds a folder (its sub-folders become event folders), “Add photos…” adds single photos into an event folder you name, “New wedding…” starts another wedding.' },
  { q: 'How do I switch between weddings?', t: 'wedding', k: 'switch change other wedding open choose dropdown',
    a: 'Pick the wedding from the list at the top, next to Auto Edits. Each wedding keeps its own folders, edits and ratings.' },
  { q: 'Can I see the tour again?', k: 'tour guide walkthrough again tutorial intro welcome',
    a: 'Yes: press <b>Take the tour again</b> at the bottom of this window.' },

  // ---- culling and rating
  { q: 'How does the automatic culling decide?', faq: true, t: 'cull-open', k: 'cull select pick reject keep maybe sort choose best blur blurry focus out-of-focus eyes closed blink duplicate burst bad',
    a: 'Click <b>Cull…</b>. Each photo is checked for focus, motion blur, exposure, closed eyes, awkward expressions and near-identical shots, then rated Keep, Maybe or Reject with the reason shown. You can change any rating yourself, and nothing is ever deleted. Culling costs 1 credit per 10 photos.' },
  { q: 'How do I give stars or colour labels?', t: 'star-bar', k: 'star stars rating rate colour color label red yellow green flag',
    a: 'Use the stars and colours in the bar under the photos, or press 0 to 5 for stars. Show only some photos with the star and label filter above the grid.' },
  { q: 'How do I compare two similar photos?', k: 'compare side by side two similar burst choose',
    a: 'In Library select the photos and press <b>C</b> for Compare view: they show side by side with faces, sharpness and open eyes, so you can keep the best one.' },

  // ---- presets
  { q: 'Can I use my own presets?', faq: true, t: 'preset-plus', k: 'import presets xmp lightroom own my upload pack zip folder',
    a: 'Yes. Click <b>+</b> next to Presets › “Import from Lightroom…” for .xmp files (masks included), or “Import a folder or zip…” for whole preset packs. Auto Edits also has 500 presets built in: 100 looks, each in five variations.' },
  { q: 'How do I try presets quickly?', t: 'looks', k: 'try preview preset look apply arrow keys browse',
    a: 'Click a preset, then use the up and down arrow keys to step through them. The photo on screen updates each time. Trying presets is free.' },
  { q: 'How do I make a preset stronger or softer?', t: 'amount', k: 'amount strength strong weak soft intensity opacity',
    a: 'Move <b>Amount</b> above the preset list: 100% is the preset as made, lower is softer, up to 200% is stronger.' },
  { q: 'How do I mark favourite presets?', t: 'looks', k: 'favourite favorite star preset pin top',
    a: 'Click the ☆ next to a preset. Starred presets show in a Favourites group at the top of the list, with the Amount you last used.' },
  { q: 'Which preset is recommended for this folder?', t: 'looks', k: 'recommended suggest best preset folder haldi sangeet reception',
    a: 'The <b>Recommended</b> group at the top of the presets shows the three that best suit the folder: its event name, the light and the colours.' },
  { q: 'Can it learn my editing style?', faq: true, t: 'preset-plus', k: 'learn style my edits sidecar xmp copy',
    a: 'Yes, and it is free. Click <b>+</b> next to Presets › “Learn my style…” and give it the .xmp sidecars of photos you edited before (in Lightroom: select them, Metadata › Save Metadata to File). It makes up to five “My style” presets.' },
  { q: 'How do I save my settings as a preset?', t: 'preset-plus', k: 'create save new preset make own settings',
    a: 'Edit a photo the way you like, then <b>+</b> next to Presets › “Create preset…”. You can also download it for Lightroom.' },

  // ---- editing
  { q: 'How do I edit one photo?', t: 'view-loupe', k: 'edit develop one single photo open large loupe',
    a: 'Double-click a photo, or press <b>D</b> (Develop). <b>G</b> goes back to the grid (Library). The arrow keys move to the next or previous photo.' },
  { q: 'Do my changes go to one photo or the whole folder?', t: 'scope-box', k: 'apply whole folder this photo all every scope batch',
    a: 'Choose at <b>Apply to</b> at the top of the right panel: “Whole folder” changes every photo in the folder, “This photo” only the one on screen.' },
  { q: 'How do the sliders work?', t: 'panels', k: 'slider sliders exposure contrast highlights shadows whites blacks temp tint white balance warm cool tone colour hsl brightness brighter darker bright dark lighten darken saturation vibrance',
    a: 'The same panels and order as Lightroom. Each slider starts at the preset’s value. Tick <b>Auto</b> next to a slider to have it worked out for each photo. Double-click a slider to put it back; click the number to type one.' },
  { q: 'What does Auto do?', t: 'mode-box', k: 'auto automatic exposure correct fix match even consistent same light',
    a: 'Auto works out exposure, white balance and tone for each photo (led by faces), matched across the folder so the light looks even. Choose “Preset + auto” or “Auto” at <b>Edit with</b>, or tick Auto on single sliders.' },
  { q: 'How do I crop, straighten or rotate?', t: 'crop-tool', k: 'crop straighten rotate turn angle frame ratio ai crop',
    a: 'Click <b>Crop</b> (or press R). Pick a shape, drag the frame, or use <b>AI crop</b> to frame the people. Straighten is below; [ and ] turn the photo. Your photo itself is never changed.' },
  { q: 'How do masks work?', t: 'mask-tool', k: 'mask masks sky face skin background subject eyes hair brush radial linear local',
    a: 'Click <b>Masks</b> (or press M). Draw radial, linear or brush masks, or pick an AI part (subject, background, face, skin, eyes, hair, clothes, sky). Each mask has its own sliders, on top of the preset.' },
  { q: 'How do I see before and after?', t: 'split', k: 'before after compare original side split',
    a: 'Click <b>Before / after</b> (or press Y) in Develop for side by side. Hold <b>\\</b> to see the whole photo before.' },
  { q: 'How do I zoom in?', k: 'zoom 100 1:1 detail sharp close',
    a: 'In Develop press <b>Z</b> to switch between fit and 1:1, and drag to move around.' },
  { q: 'How do I undo a change?', t: 'history-panel', k: 'undo redo back revert mistake history snapshot',
    a: 'Press Cmd/Ctrl+Z to undo, Cmd/Ctrl+Shift+Z to redo, or open <b>History</b> in the right panel to go back to any step. Snapshots save a state you can come back to.' },
  { q: 'How do I copy settings to other photos?', t: 'menu-copy', open: 'more', k: 'copy paste sync settings same other photos match',
    a: 'In the Account menu use <b>Copy settings…</b> (Cmd/Ctrl+Shift+C), select photos, then <b>Paste settings</b> (Cmd/Ctrl+Shift+V). Or <b>Sync settings…</b> from this photo to every selected photo.' },
  { q: 'How do I reset a photo?', k: 'reset original remove edits start again clear',
    a: 'Use <b>Reset sliders</b> or <b>Photo to original</b> under the sliders. Your uploaded photo is never changed by editing.' },
  { q: 'Can I retouch skin?', k: 'retouch skin smooth blemish face portrait',
    a: 'Yes: open the <b>Retouch</b> panel in Develop. It finds faces and smooths skin, with its own strength.' },

  // ---- export and delivery
  { q: 'How do I export the edited photos?', faq: true, t: 'export-open', k: 'export download save jpeg jpg final deliver output finished edited',
    a: 'Click <b>Export…</b> at the bottom left. Choose which photos (all, Keep, or Keep and Maybe), the size and quality, and an optional watermark. You get an email and a download when they are ready. Each exported photo is 1 credit.' },
  { q: 'What sizes can I export?', faq: true, t: 'export-open', k: 'size resolution pixels px quality full 2560 4000 watermark',
    a: '2560 px for proofs and social media, 4000 px, or full size (as large as the uploaded preview), at JPEG quality 80, 90 or 95. You can add a text watermark.' },
  { q: 'Are exports as sharp as Lightroom’s?', faq: true, k: 'sharp quality raw resolution lightroom final print',
    a: 'Exports from the app come from the uploaded JPEG preview, not the raw file: right for proofs, social media and quick delivery. For the sharpest finals, the Lightroom plug-in brings your edits back into Lightroom Classic, which applies them to the raw file.' },
  { q: 'Can my clients pick their favourites?', faq: true, t: 'share-open', open: 'more', k: 'client couple share gallery link favourites picks select bride groom',
    a: 'Yes. Account menu › <b>Share as gallery…</b> makes a private link (optional password) where the couple sees the edited previews, hearts favourites, comments and sends the selection. Their picks show under the Client picks filter. Galleries are free.' },
  { q: 'How do I download my original photos?', t: 'originals', open: 'more', k: 'download originals back zip uploaded',
    a: 'Account menu › <b>Download original photos</b>: the photos you uploaded for this wedding, as one zip.' },

  // ---- Lightroom
  { q: 'Does it work with Lightroom Classic?', faq: true, k: 'lightroom lr classic catalogue catalog adobe',
    a: 'Yes. The plug-in sends selected photos to the app and brings the edits back into your catalogue as normal develop settings, with a “Before Auto Edits” snapshot on every photo. You can also use Auto Edits without Lightroom.' },
  { q: 'How do I install the Lightroom plug-in?', faq: true, t: 'plugin-open', open: 'more', k: 'install plugin plug-in key lightroom setup connect',
    a: 'Account menu › <b>Lightroom plug-in</b>: download it and unzip it. In Lightroom Classic choose File › Plug-in Manager › Add and pick the AutoEdits.lrplugin folder. Make your key in the same window and paste it in Library › Plug-in Extras › Auto Edits: server settings.' },

  // ---- credits and account
  { q: 'How much does Auto Edits cost?', faq: true, t: 'credits', k: 'price cost pay money rupees credits charge gst plan',
    a: 'You pay with credits. Each exported photo, or photo sent back to Lightroom with its edit, is 1 credit; culling is 1 credit per 10 photos. A credit is ₹1 until 31 December 2026 and ₹3 after, plus 18% GST, from 500 credits. Trying presets, editing and galleries are free.' },
  { q: 'How do I buy credits?', t: 'credits', k: 'buy credits recharge top up pay razorpay upi card balance',
    a: 'Click <b>Credits</b> at the top and choose how many. Payment is by Razorpay (UPI, cards, net banking) and the GST invoice is emailed to you.' },
  { q: 'Is there a free trial?', faq: true, t: 'credits', k: 'free trial credits sign up 50',
    a: 'Yes. New accounts get 50 free credits once the email address is confirmed, no card needed. Free credits can be used for 15 days; credits you buy never expire.' },
  { q: 'Where are my invoices?', t: 'account-open', open: 'more', k: 'invoice bill receipt gst statement history',
    a: 'Account menu › <b>Account and credits</b>: your invoices, every credit added or spent, and a statement you can download.' },
  { q: 'How much storage do I get, and how long are photos kept?', k: 'storage space gb limit keep delete expire 180 days retention full',
    a: 'Up to 50 GB of previews and exports per photographer (more for ₹150/month + GST). A wedding nobody opens for 180 days is removed; you get an email two weeks before, and opening it or clicking “Keep for 180 more days” in Account keeps it.' },
  { q: 'How do I delete a wedding?', t: 'delete-wedding', open: 'more', k: 'delete remove wedding erase clear space',
    a: 'Account menu › <b>Delete this wedding</b>. It is removed from Auto Edits only, not from your computer or Lightroom.' },
  { q: 'How do I change my password or email?', t: 'account-open', open: 'more', k: 'password email change account delete account profile',
    a: 'Account menu › <b>Account and credits</b>: change your password or email there, or delete your account.' },
  { q: 'Are my photos private?', faq: true, k: 'private privacy safe secure security who see',
    a: 'Yes. Nothing is visible without logging in, each photographer sees only their own weddings, and the connection is encrypted.' },
  { q: 'Does it work on a phone?', k: 'phone mobile tablet ipad android iphone',
    a: 'Editing works best on a laptop. On a phone you can view, rate and export; the sliders and presets are further down the page.' },
  { q: 'How do I contact support?', k: 'contact support help email phone whatsapp problem bug issue',
    a: 'Use <a href="/contact" target="_blank">Help and support</a> (email, phone and WhatsApp) in the Account menu.' },
];

// ---- search: words, endings and the other words people use -----------------------------------
const HELP_STOP = new Set('a an the i my me we to of in on at for and or is are be can do does how what where which why when it this that with from into by you your there get want need please photo wedding make look like way'.split(' '));
const HELP_SAME = { pics: 'photo', pic: 'photo', picture: 'photo', image: 'photo', images: 'photo', photos: 'photo',
  jpg: 'jpeg', lr: 'lightroom', lrc: 'lightroom', plugin: 'plugin', 'plug': 'plugin', colour: 'color', favorite: 'favourite',
  erase: 'delete', remove: 'delete', rupee: 'price', cost: 'price', pay: 'price', money: 'price', charge: 'price', paid: 'price',
  bride: 'couple', groom: 'couple', client: 'couple', clients: 'couple', revert: 'undo', saving: 'save', downloads: 'download' };
function helpWords(text) {
  return String(text).toLowerCase().replace(/<[^>]+>/g, ' ').replace(/[^a-z0-9₹:\s-]/g, ' ').split(/[\s-]+/)
    .map((w) => HELP_SAME[w] || w)
    .map((w) => (w.length > 4 ? w.replace(/(ing|ed|er|es|s)$/, '') : w))
    .filter((w) => w && !HELP_STOP.has(w));
}
for (const h of HELP) h.words = { k: new Set(helpWords(h.k || '')), q: new Set(helpWords(h.q)), a: new Set(helpWords(h.a)) };

function helpSearch(query) {
  const words = [...new Set(helpWords(query))];
  if (!words.length) return [];
  const hit = (set, w) => set.has(w) ? 1 : (w.length >= 4 && [...set].some((x) => x.startsWith(w) || (x.length >= 4 && w.startsWith(x)))) ? 0.5 : 0;
  const scored = HELP.map((h) => ({ h, score: words.reduce((n, w) => n + 3 * hit(h.words.k, w) + 2 * hit(h.words.q, w) + hit(h.words.a, w), 0) }))
    .filter((x) => x.score >= 2).sort((a, b) => b.score - a.score);
  if (!scored.length) return [];
  return scored.filter((x) => x.score >= scored[0].score * 0.45).slice(0, 6).map((x) => x.h);
}

// ---- the window ----------------------------------------------------------------------------
function helpItem(h, open) {
  const d = el('details', { className: 'help-item' });
  d.open = open;
  const body = el('div', { className: 'help-a' });
  body.innerHTML = h.a;                                  // written above in this file, not from anyone else
  d.append(el('summary', { textContent: h.q }), body);
  if (h.t) {
    const b = el('button', { type: 'button', className: 'small', textContent: 'Show me' });
    b.onclick = (e) => { e.stopPropagation(); helpShow(h, body); };   // not to the Account menu's click-outside-closes
    body.append(el('div', { className: 'row' }, [b]));
  }
  return d;
}

function drawHelp() {
  const q = $('help-q').value.trim();
  const list = $('help-list');
  if (!q) {
    $('help-head').textContent = 'Frequently asked questions';
    list.replaceChildren(...HELP.filter((h) => h.faq).map((h) => helpItem(h, false)));
    return;
  }
  const found = helpSearch(q);
  $('help-head').textContent = found.length ? (found.length === 1 ? 'The answer' : 'Best answers') : '';
  if (!found.length) {
    const none = el('p', { className: 'dim' });
    none.innerHTML = 'No answer for that yet. Try other words (for example “export”, “presets”, “credits”), or <a href="/contact" target="_blank">contact us</a>: email, phone or WhatsApp.';
    list.replaceChildren(none);
    return;
  }
  list.replaceChildren(...found.map((h, i) => helpItem(h, i === 0)));
}

function openHelp(question) {
  $('help-q').value = question || '';
  drawHelp();
  if (!$('help-dialog').open) $('help-dialog').showModal();
  $('help-q').focus();
}

// "Show me": close Help and point at the place with a small card (the guide's look), or say why not.
let helpOpenedMenu = null;
function helpShow(h, body) {
  if (h.open && $(h.open) && !$(h.open).open) { $(h.open).open = true; helpOpenedMenu = $(h.open); }
  const t = guideTarget(h.t);
  if (!t) {
    if (helpOpenedMenu) { helpOpenedMenu.open = false; helpOpenedMenu = null; }
    const note = body.querySelector('.help-note') || body.appendChild(el('p', { className: 'help-note dim small' }));
    note.textContent = 'That is not on screen right now: open a wedding with photos first (or show the side panels with F7 / F8).';
    return;
  }
  $('help-dialog').close();
  t.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  const card = $('help-point');
  $('help-point-title').textContent = h.q;
  if (!card.open) card.show();
  guideLight(t);
  placeCard(card, $('help-point-arrow'), t);
  $('help-point-ok').focus({ preventScroll: true });
}
$('help-point-ok').onclick = () => $('help-point').close();
$('help-point-back').onclick = () => { $('help-point').close(); openHelp($('help-q').value); };
$('help-point').addEventListener('close', () => {
  guideLight(null);
  if (helpOpenedMenu) { helpOpenedMenu.open = false; helpOpenedMenu = null; }
});

$('help-q').oninput = drawHelp;
$('help-q').onkeydown = (e) => { e.stopPropagation(); if (e.key === 'Escape') $('help-dialog').close(); };
$('help-tour').onclick = () => { $('help-dialog').close(); if (typeof openGuide === 'function') openGuide(); };
$('help-close').onclick = () => $('help-dialog').close();
$('help-open').onclick = () => { $('more').open = false; openHelp(); };
// ? opens Help from anywhere that is not a text box
document.addEventListener('keydown', (e) => {
  if (e.key !== '?' || e.metaKey || e.ctrlKey || e.altKey || e.target.matches('input, textarea, select, [contenteditable]')) return;
  if (document.querySelector('dialog[open]:not(#guide-dialog):not(#help-point)')) return;
  e.preventDefault();
  openHelp();
});
