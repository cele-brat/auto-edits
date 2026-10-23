"""The public policy pages Razorpay and the law ask for: Terms, Privacy, Refunds, Contact, Delivery.

Business details live here, in one place (Bharat, Oct 2026):
  - no postal address on the site; the Ajmer address goes only on invoices
  - support by email (support@cele-brat.com) and by phone / WhatsApp
Change TERMS_VERSION whenever the Terms or Privacy text changes in substance: new sign-ups record
which version they agreed to.
"""
from __future__ import annotations

import html

BUSINESS = "Celebrat Ventures"
BRAND = "Auto Edits by Cele-Brat"
SITE = "https://edits.cele-brat.solutions"
SUPPORT_EMAIL = "support@cele-brat.com"
GSTIN = "08ARTPK7888A1Z5"
SUPPORT_PHONE = "+91 96022 12620"
GRIEVANCE_OFFICER = "Bharat"
COURTS = "Ajmer, Rajasthan"
UPDATED = "23 October 2026"
TERMS_VERSION = "2026-10-23"
REFUND_DAYS = 7
POWERED_BY = "Powered by Cele-Brat.Tech | info@cele-brat.tech"


def _mail() -> str:
    return f'<a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>'


def _phone() -> str:
    if not SUPPORT_PHONE:
        return ""
    digits = "".join(c for c in SUPPORT_PHONE if c.isdigit())
    return (f'<a href="tel:+{digits}">{html.escape(SUPPORT_PHONE)}</a> '
            f'(calls and <a href="https://wa.me/{digits}">WhatsApp</a>)')


def _reach() -> str:
    phone = _phone()
    return f"email {_mail()}" + (f" or call / WhatsApp {phone}" if phone else "")


def _terms() -> str:
    return f"""
<p>These terms apply when you use Auto Edits at {SITE} and its Lightroom Classic plug-in. Auto Edits is run by
{BUSINESS} (“we”, “us”), a sole proprietorship registered in India. By making an account you agree to these terms
and to our <a href="/privacy">Privacy policy</a>.</p>

<h2>1. The service</h2>
<p>Auto Edits helps wedding photographers cull, edit and export photos. You upload previews of your photos (the JPEG
stored inside each raw file, not the raw file itself), choose looks and presets, and the server makes the finished
pictures. Automatic ratings and corrections are suggestions: always check your work before you deliver it to a client.</p>

<h2>2. Your account</h2>
<ol>
<li>You must be at least 18 and give a real email address that you can confirm.</li>
<li>Keep your password and your Lightroom key private. You are responsible for what is done with your account.</li>
<li>One free trial per person. We may refuse free credits to accounts that look like repeat sign-ups.</li>
<li>Free trial credits can be used for 15 days from when they are added; whatever is left after that ends. Credits you buy do not expire.</li>
</ol>

<h2>3. Credits and prices</h2>
<ol>
<li>The service is paid for with credits. Exporting a photo, or sending its edit back to Lightroom, costs 1 credit each
time. Culling costs 1 credit for every 10 photos, each time it runs. Current prices are on the
<a href="/#pricing">home page</a>.</li>
<li>Credits do not expire while your account is open, cannot be transferred to another account and have no cash value,
except as set out in our <a href="/refunds">Refund policy</a>.</li>
<li>Free credits have no cash value and cannot be refunded.</li>
<li>Taxes (GST) are charged as shown at checkout, and a tax invoice is issued for every purchase.</li>
<li>Credits for photos that could not be exported, or for exports you stop or remove before they finish, are given
back automatically.</li>
<li>We may change prices for future purchases. Credits you already hold are not affected.</li>
<li>Packs (such as the Season pack) are credits bought at a pack price: they work exactly like other credits and are
refunded in the same share as the money, as set out in the Refund policy.</li>
</ol>

<h2>3A. Storage plans</h2>
<ol>
<li>Every account has a storage allowance. Extra storage is sold in blocks of 50 GB a month, either <b>prepaid</b> for
1, 3, 6 or 12 months, or on <b>auto-renew</b>, where the same amount is charged every month to the payment method
you gave Razorpay until you cancel.</li>
<li>Auto-renew can be cancelled at any time from the account window in the app, or by writing to us. The storage stays
until the end of the month already paid for; no further charge is made after that. We email you 3 days before each
renewal charge and 7 days before prepaid storage ends.</li>
<li>If a renewal charge fails, Razorpay retries it; if it keeps failing the auto-renew stops and the extra storage ends.
When your photos and exports then take more room than your allowance, new uploads stop until you make space or add
storage again. Nothing of yours is deleted because of that alone.</li>
<li>Prepaid storage is not refunded for the months already started, except as in the Refund policy.</li>
</ol>

<h2>4. Your photos and presets</h2>
<ol>
<li>Your photos, presets and edits stay yours. You give us only the permission we need to store, process and show them
to you in order to run the service.</li>
<li>Only upload work you have the right to use. You are responsible for having your clients' consent where it is
needed.</li>
<li>The Auto Edits team can view your weddings and download your uploaded and exported photos only to help when you
ask for support, to fix a problem with your account or the service, or to keep the service safe. Every such access is
recorded. We never sell your photos, never share them or show them to other photographers or anyone else, and never use
them in our own marketing without your written permission.</li>
<li>Keep your own copies. Auto Edits is a working tool, not an archive: we may set a period after which old uploads
and exports are removed, and we will email you at least 14 days before anything of yours is removed under such a rule.</li>
</ol>

<h2>5. Fair use</h2>
<p>Do not upload unlawful material, try to reach other people's accounts, overload or attack the service, resell it,
or copy the built-in presets to offer them elsewhere. We may suspend accounts that do, and will tell you why.</p>

<h2>6. Availability and liability</h2>
<ol>
<li>We work to keep Auto Edits running and your data safe (encrypted connections, daily backups of account data), but
the service is provided “as is” and may sometimes be unavailable.</li>
<li>To the extent the law allows, we are not liable for indirect losses such as lost business or missed deadlines, and
our total liability to you is limited to the amount you paid us in the 3 months before the claim.</li>
</ol>

<h2>7. Closing your account</h2>
<p>You can stop using Auto Edits at any time. To close your account and remove your photos, {_reach()}. We may close
accounts that break these terms. Unused bought credits are handled as in the <a href="/refunds">Refund policy</a>.</p>

<h2>8. Changes and law</h2>
<p>We may update these terms; for important changes we will email you before they take effect. These terms are
governed by the laws of India, and the courts at {COURTS} have jurisdiction.</p>

<h2>9. Contact</h2>
<p>Questions about these terms: {_reach()}.</p>
"""


def _privacy() -> str:
    return f"""
<p>This policy explains what {BUSINESS} collects when you use Auto Edits, why, and your choices. It is written to meet
India's Digital Personal Data Protection Act, 2023.</p>

<h2>1. What we collect</h2>
<ol>
<li><b>Account:</b> your email address, name or studio name, a scrambled (hashed) form of your password, and the
internet address you signed up from (used to give one free trial per connection).</li>
<li><b>Photos and edits:</b> the previews you upload, their file names, event folders, capture time, camera and lens
details, ratings, the looks and settings you choose, presets you upload and the pictures we export for you.</li>
<li><b>Credits and payments:</b> every credit added or spent, and for purchases the payment reference, amount and tax
details. Card and bank details are handled by our payment provider (Razorpay) and never reach us.</li>
<li><b>Sign-in:</b> a cookie that keeps you logged in, and the time you were last active. We use no advertising or
tracking cookies.</li>
</ol>

<h2>2. Why we use it</h2>
<p>To run the service (store, cull, edit and export your photos), keep your account secure, count credits, issue tax
invoices, answer support requests and meet legal duties. We do not sell personal data and do not use your photos for
advertising.</p>

<h2>3. Who can see your photos</h2>
<ol>
<li>Each photographer sees only their own weddings.</li>
<li>The Auto Edits team can view your account read-only and download your uploaded and exported photos only to help
when you ask for support, to fix a problem, or to keep the service safe. Every view and download is recorded in an
access log. Your photos are never shared, never shown to anyone else and never used for marketing.</li>
</ol>

<h2>4. Where it is kept and who helps us</h2>
<ol>
<li>Our application server (Hostinger) and photo storage (Cloudflare R2) keep the data, on encrypted connections.</li>
<li>Google Workspace sends our emails; Razorpay processes payments; the pages load fonts from Google Fonts. Each only
receives what it needs for that job.</li>
<li>Some of these providers may store data outside India.</li>
</ol>

<h2>5. How long we keep it</h2>
<ol>
<li>Photos and account data: while your account is open. When you ask us to close your account, we remove your photos
and personal data within 30 days. Daily backups of account data are kept for 30 days and then deleted.</li>
<li>Credit, payment and invoice records: as long as tax law requires (currently 8 years), even after an account is
closed.</li>
</ol>

<h2>6. Your rights</h2>
<p>You can ask to see the personal data we hold about you, correct it, or erase it, and you can withdraw consent by
closing your account. Your credit history can be downloaded at any time from the account window in the app.
To use these rights, {_reach()}.</p>

<h2>7. Grievance officer</h2>
<p>{GRIEVANCE_OFFICER}, {BUSINESS}: {_reach()}. We reply within 7 days and resolve complaints within 30 days.</p>

<h2>8. Changes</h2>
<p>We will email you before any important change to this policy takes effect.</p>
"""


def _refunds() -> str:
    return f"""
<p>Auto Edits is paid for with credits, and extra storage can be bought prepaid or on auto-renew. Credits have no
subscription: you only pay when you buy them.</p>

<h2>1. Credits given back automatically</h2>
<p>If a photo cannot be exported, or you stop or remove an export before it finishes, the credits for the photos that
were not made are returned to your balance straight away. You can see each one in your credit history.</p>

<h2>2. Refunds of bought credits</h2>
<ol>
<li>Unused credits you bought can be refunded if you ask within {REFUND_DAYS} days of the purchase.</li>
<li>Credits that have already been used, and free credits, cannot be refunded.</li>
<li>If a payment was taken twice, or taken without credits being added, we refund it in full whenever you tell us.</li>
</ol>

<h2>2A. Storage plans and auto-renew</h2>
<ol>
<li><b>Cancelling auto-renew:</b> open the account window in the app and choose “Stop auto-renew”, or write to us.
It takes effect at the end of the month already paid for; that month is not refunded, and nothing more is charged.</li>
<li><b>Prepaid storage:</b> can be refunded for the months not yet started if you ask within {REFUND_DAYS} days of the
purchase and the extra space is not in use.</li>
<li>A renewal charge taken after you cancelled, or taken twice, is refunded in full.</li>
</ol>

<h2>3. How to ask</h2>
<p>{_reach()}, from the email address of your account, with the payment reference (it is in the receipt email and on
your invoice). We confirm within 2 working days.</p>

<h2>4. When the money arrives</h2>
<p>Approved refunds are sent to the original payment method within 5 to 7 working days. Your bank may take a few more
days to show it. The refunded credits are removed from your balance and a credit note is issued.</p>
"""


def _shipping() -> str:
    return f"""
<p>Auto Edits is an online service. Nothing is shipped by post or courier.</p>
<ol>
<li><b>Credits:</b> added to your account automatically as soon as the payment succeeds, usually within a minute. The
tax invoice is emailed to you and is also available in the app.</li>
<li><b>Edited photos:</b> exports are made on our server and delivered as downloads (a zip file) inside the app, or
applied to your Lightroom Classic catalogue by the plug-in.</li>
<li>If credits do not appear within 1 hour of a successful payment, {_reach()} with the payment reference and we will
fix it the same working day.</li>
</ol>
"""


def _contact() -> str:
    phone = _phone()
    return f"""
<p>{BRAND} is run by {BUSINESS}, India.</p>
<dl>
<dt>Email</dt><dd>{_mail()}</dd>
{f"<dt>Phone and WhatsApp</dt><dd>{phone}</dd>" if phone else ""}
<dt>Replies</dt><dd>Within 1 working day (Monday to Saturday, India time)</dd>
<dt>Grievance officer</dt><dd>{GRIEVANCE_OFFICER}, at the email above</dd>
</dl>
<p>For help with refunds see the <a href="/refunds">Refund policy</a>; for your data see the
<a href="/privacy">Privacy policy</a>.</p>
"""


PAGES = {
    "terms": ("Terms of use", "The terms for using Auto Edits, the online culling and editing service for wedding photographers.", _terms),
    "privacy": ("Privacy policy", "What Auto Edits collects, why, who can see your photos, and your rights.", _privacy),
    "refunds": ("Refund and cancellation policy", "When Auto Edits credits are given back or refunded, and how to ask.", _refunds),
    "shipping": ("Delivery policy", "How Auto Edits delivers credits and edited photos: online, straight away.", _shipping),
    "contact": ("Contact us", "How to reach the Auto Edits team for help, refunds and privacy questions.", _contact),
}


def page(slug: str) -> str | None:
    if slug not in PAGES:
        return None
    title, description, body = PAGES[slug]
    links = " · ".join(f'<a href="/{s}"{" aria-current=page" if s == slug else ""}>{t.split(" policy")[0].split(" of use")[0]}</a>'
                       for s, (t, _, _) in PAGES.items())
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Auto Edits</title>
<meta name="description" content="{html.escape(description)}">
<link rel="canonical" href="{SITE}/{slug}">
<meta name="theme-color" content="#083f44">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500&family=Hanken+Grotesk:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root {{ --glass: #e9edeb; --paper: #f7f9f8; --ink: #17201f; --soft: #3f4a48; --rule: #cbd3d0; --peacock: #0d5b61;
  --display: "Playfair Display", Georgia, serif; --body: "Hanken Grotesk", -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--glass); color: var(--ink); font: 400 18px/1.65 var(--body); -webkit-font-smoothing: antialiased; }}
a {{ color: var(--peacock); }}
header, main, footer {{ max-width: 760px; margin: 0 auto; padding: 0 20px; }}
header {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-top: 22px; padding-bottom: 22px; }}
.brand {{ font: italic 500 26px/1 var(--display); color: var(--ink); text-decoration: none; }}
main {{ background: var(--paper); border: 1px solid var(--rule); border-radius: 10px; padding: 34px clamp(20px, 5vw, 46px) 40px; }}
h1 {{ font: 500 clamp(32px, 5vw, 46px)/1.1 var(--display); margin: 0 0 6px; }}
.updated {{ color: var(--soft); font-size: 15px; margin: 0 0 24px; }}
h2 {{ font: 700 19px/1.3 var(--body); margin: 28px 0 8px; }}
p, li, dd {{ max-width: 66ch; }}
ol {{ padding-left: 1.3em; }} li {{ margin: 6px 0; }}
dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 8px 22px; }} dt {{ font-weight: 600; }} dd {{ margin: 0; }}
@media (max-width: 800px) {{ main {{ margin: 0 12px; }} }}
@media (max-width: 520px) {{ dl {{ grid-template-columns: 1fr; gap: 2px; }} dd {{ margin-bottom: 10px; }} }}
footer {{ padding-top: 22px; padding-bottom: 40px; font-size: 15px; color: var(--soft); }}
footer a {{ color: var(--soft); }} footer a[aria-current] {{ color: var(--ink); font-weight: 600; text-decoration: none; }}
</style>
</head>
<body>
<header><a class="brand" href="/">Auto Edits</a><a href="/">Back to Auto Edits</a></header>
<main>
<h1>{title}</h1>
<p class="updated">Last updated {UPDATED}</p>
{body()}
</main>
<footer><p>{links}</p><p>{BRAND} · {BUSINESS}</p><p>{POWERED_BY}</p></footer>
</body>
</html>
"""
