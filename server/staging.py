"""A staging copy of the app (deploy/docker-compose.staging.yml): the same image, its own data volume,
AUTOEDITS_ENV=staging.

With that set:
  - the top bar shows a STAGING banner (the app reads "env" from /api/session);
  - only Razorpay TEST keys are accepted: the server refuses to start with a live key, so a
    staging test can never take real money;
  - no email leaves the machine except to the studio address: everyone else's is written to the log.
"""
from __future__ import annotations

import os

ENV = os.environ.get("AUTOEDITS_ENV", "").strip().lower() or "production"


def is_staging(env: str | None = None) -> bool:
    return (env if env is not None else ENV) == "staging"


def check(razorpay, env: str | None = None) -> None:
    """Raise before serving anything when a staging copy was given live Razorpay keys."""
    if is_staging(env) and razorpay.on and not razorpay.test_mode:
        raise RuntimeError("AUTOEDITS_ENV=staging but RAZORPAY_KEY_ID is not a test key (rzp_test_...). "
                           "Staging refuses to start with a live key.")


class StudioOnly:
    """Wraps a Mailer on staging: only the studio's address gets real email."""

    def __init__(self, mailer, studio: str):
        self.mailer, self.studio = mailer, studio.strip().lower()
        self.held: list[tuple[str, str]] = []

    @property
    def on(self) -> bool:
        return getattr(self.mailer, "on", False)

    def send(self, to: str, subject: str, text: str, html: str = "", wait: bool = False) -> None:
        if to.strip().lower() == self.studio and self.studio:
            return self.mailer.send(to, subject, text, html, wait)
        self.held.append((to, subject))
        del self.held[:-50]
        print(f"[staging: mail held] to {to}: {subject}\n{text}", flush=True)      # the link is in the log, for testing


def only_studio(mailer, studio: str, env: str | None = None):
    return StudioOnly(mailer, studio) if is_staging(env) else mailer
