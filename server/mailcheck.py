"""Are the sending domain's SPF, DKIM and DMARC records in place?  python -m server.mailcheck [domain]

Gmail, Outlook and the rest put mail without them in spam (or refuse it) once a sender grows. The
domain is taken from MAIL_FROM (or SMTP_USER); DKIM is looked up at the selector Google Workspace
uses, google._domainkey.<domain>. Lookups use dnspython when it is installed, else the system
`dig`, else `nslookup`, else a plain-Python query to the resolver in /etc/resolv.conf (the server image
has none of the others). Prints PASS/FAIL per record and the exact TXT values to add. A lookup that
fails is reported as "could not check", never as a missing record.

Also run by the daily health check (server/health.py): one line in the health email, only when
something is missing. Never prints secrets.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
from email.utils import parseaddr

SELECTOR = "google"
DNS_PORT = 53
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


def _nameservers() -> list[str]:
    try:
        with open("/etc/resolv.conf") as f:
            found = [ln.split()[1] for ln in f if ln.strip().startswith("nameserver") and len(ln.split()) > 1]
    except OSError:
        found = []
    return found or ["1.1.1.1", "8.8.8.8"]


def _skip_name(msg: bytes, i: int) -> int:
    while True:
        n = msg[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:                                           # compression pointer
            return i + 2
        i += n + 1


def parse_txt_answer(msg: bytes, qid: int) -> list[str] | None:
    """TXT strings from a DNS reply; [] for NXDOMAIN or no TXT; None for anything unusable."""
    if len(msg) < 12:
        return None
    rid, flags, qd, an = struct.unpack(">HHHH", msg[:8])
    if rid != qid or not flags & 0x8000:
        return None
    rcode = flags & 0xF
    if rcode == 3:
        return []
    if rcode != 0:
        return None
    i = 12
    for _ in range(qd):
        i = _skip_name(msg, i) + 4
    out = []
    for _ in range(an):
        i = _skip_name(msg, i)
        rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", msg[i:i + 10])
        i += 10
        if rtype == 16:
            data, j, parts = msg[i:i + rdlen], 0, []
            while j < len(data):
                parts.append(data[j + 1:j + 1 + data[j]].decode("utf-8", "replace"))
                j += 1 + data[j]
            out.append("".join(parts))
        i += rdlen
    return out


def _txt_socket(name: str) -> list[str] | None:
    qid = secrets.randbelow(65536)
    qname = b"".join(bytes([len(p)]) + p.encode("idna") for p in name.rstrip(".").split(".")) + b"\0"
    # one question (TXT, IN) plus an EDNS0 record so long DKIM keys fit in one UDP reply
    query = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 1) + qname + struct.pack(">HH", 16, 1) + \
        b"\0" + struct.pack(">HHIH", 41, 4096, 0, 0)
    for server in _nameservers()[:3]:
        try:
            family = socket.AF_INET6 if ":" in server else socket.AF_INET
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.settimeout(5)
                s.sendto(query, (server, DNS_PORT))
                got = parse_txt_answer(s.recv(8192), qid)
        except (OSError, ValueError, IndexError, struct.error):
            continue
        if got is not None:
            return got
    return None


def _unquote(line: str) -> str:
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', line)) or line.strip()


def lookup_txt(name: str, resolver=None) -> list[str] | None:
    """TXT values at name; [] when there are none; None when no lookup tool worked."""
    if resolver is not None:
        return resolver(name)
    for f in (_txt_dnspython, _txt_dig, _txt_nslookup, _txt_socket):
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
    """The names of the records not in place, for the health email (empty when all is well).
    A record whose lookup failed is not counted: see unchecked()."""
    return [c["record"] for c in check(domain, resolver) if not c["ok"] and not c["lookup_failed"]]


def status(domain: str, resolver=None) -> tuple[list[str], list[str]]:
    """(missing, unchecked) record names, from one round of lookups."""
    out = check(domain, resolver)
    return ([c["record"] for c in out if not c["ok"] and not c["lookup_failed"]],
            [c["record"] for c in out if c["lookup_failed"]])


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
