"""Are the sending domain's SPF, DKIM and DMARC records in place?  python -m server.mailcheck [domain]

Gmail, Outlook and the rest put mail without them in spam (or refuse it) once a sender grows. The
domain is taken from MAIL_FROM (or SMTP_USER); DKIM is looked up at the selector Google Workspace
uses, google._domainkey.<domain>. Lookups use dnspython when it is installed, else the system
`dig`, else `nslookup`. Prints PASS/FAIL per record and the exact TXT values to add.

Also run by the daily health check (server/health.py): one line in the health email, only when
something is missing. Never prints secrets.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from email.utils import parseaddr

SELECTOR = "google"
RECOMMENDED = {
    "spf": ("@", "v=spf1 include:_spf.google.com ~all"),
    "dkim": (f"{SELECTOR}._domainkey", "(the v=DKIM1; k=rsa; p=... value from Google Admin > Apps > Google Workspace > Gmail > "
                                        "Authenticate email > Generate new record)"),
    "dmarc": ("_dmarc", "v=DMARC1; p=quarantine; rua=mailto:dmarc@{domain}; adkim=s; aspf=s"),
}


def sending_domain(env=None) -> str:
    env = os.environ if env is None else env
    addr = parseaddr(env.get("MAIL_FROM", ""))[1] or env.get("SMTP_USER", "")
    return addr.rsplit("@", 1)[-1].strip().lower() if "@" in addr else ""


def _txt_dnspython(name: str) -> list[str] | None:
    try:
        import dns.resolver
    except ImportError:
        return None
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=8)
    except Exception as e:                                             # NXDOMAIN, NoAnswer, timeout...
        if type(e).__name__ in ("NXDOMAIN", "NoAnswer", "NoNameservers"):
            return []
        return None
    return ["".join(part.decode() if isinstance(part, bytes) else str(part) for part in r.strings) for r in answers]


def _txt_dig(name: str) -> list[str] | None:
    tool = shutil.which("dig")
    if not tool:
        return None
    try:
        out = subprocess.run([tool, "+short", "+time=5", "+tries=1", "TXT", name], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [_unquote(line) for line in out.splitlines() if line.strip()]


def _txt_nslookup(name: str) -> list[str] | None:
    tool = shutil.which("nslookup")
    if not tool:
        return None
    try:
        out = subprocess.run([tool, "-type=TXT", name], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if "can't find" in out or "NXDOMAIN" in out:
        return []
    return [_unquote(m) for m in re.findall(r'text = (.+)', out)]


def _unquote(line: str) -> str:
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', line)) or line.strip()


def lookup_txt(name: str, resolver=None) -> list[str] | None:
    """TXT values at name; [] when there are none; None when no lookup tool worked."""
    if resolver is not None:
        return resolver(name)
    for f in (_txt_dnspython, _txt_dig, _txt_nslookup):
        got = f(name)
        if got is not None:
            return got
    return None


def check(domain: str, resolver=None) -> list[dict]:
    """One entry per record: {record, name, ok, found, want}."""
    out = []
    for key, (host, want) in RECOMMENDED.items():
        name = domain if host == "@" else f"{host}.{domain}"
        found = lookup_txt(name, resolver)
        tag = {"spf": "v=spf1", "dkim": "v=dkim1", "dmarc": "v=dmarc1"}[key]
        hits = [v for v in (found or []) if v.strip().lower().startswith(tag)]
        ok = bool(hits)
        if key == "spf" and ok and "google.com" not in hits[0].lower():
            ok = False                                                 # an SPF that does not allow Google's servers
        out.append({"record": key.upper(), "name": name, "ok": ok, "found": hits[0] if hits else "",
                    "want": want.replace("{domain}", domain), "lookup_failed": found is None})
    return out


def missing(domain: str, resolver=None) -> list[str]:
    """The names of the records not in place, for the health email (empty when all is well)."""
    return [c["record"] for c in check(domain, resolver) if not c["ok"]]


def report(domain: str, resolver=None) -> str:
    lines = [f"Email records for {domain}:"]
    for c in check(domain, resolver):
        state = "PASS" if c["ok"] else ("FAIL (lookup failed)" if c["lookup_failed"] else "FAIL")
        lines.append(f"  {c['record']:<5} {state:<20} {c['name']}")
        if c["ok"]:
            lines.append(f"        found: {c['found']}")
        else:
            lines.append(f"        add TXT at {c['name']}:")
            lines.append(f"        {c['want']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    domain = argv[0] if argv else sending_domain()
    if not domain:
        print("No sending domain: set MAIL_FROM (or SMTP_USER) or give the domain: python -m server.mailcheck cele-brat.solutions")
        return 2
    text = report(domain)
    print(text)
    return 0 if "FAIL" not in text else 1


if __name__ == "__main__":
    sys.exit(main())
