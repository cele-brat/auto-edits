# Auto Edits

Server-side culling, preset editing and proof export for Lightroom Classic catalogues.
Plan: "Auto Edits: Build Plan" (Claude doc). This repository is built in the stages listed there.

## Status

Stage 1 (accuracy and speed test) is in progress; first versions of auto correction and culling are in place.

| Part | State |
| --- | --- |
| `engine/presets.py` | Reads preset .xmp files; 500 Cele-Brat presets indexed by Product ID in `data/presets.json`; toggle rule implemented |
| `engine/render.py` | Preview engine v0: exposure, white balance, calibration, contrast, highlights, shadows, whites, blacks, texture, clarity, point curves, colour mixer, vibrance, saturation, colour grading, vignette, grain |
| `engine/auto.py` | Auto values for the nine toggles (exposure, highlights, shadows, whites, blacks, white balance, noise, straighten, lens) in Lightroom units |
| `engine/faces.py` | Face finding: YuNet detector (MIT) for all faces, MediaPipe Face Landmarker (Apache 2.0) for closed eyes, smile, gaze |
| `engine/cull.py` | Culling: focus, motion blur, exposure, closed eyes, group eyes, expression, framing, accidental frames, similar frames; 3 / 2 / 1 stars with reasons |
| `tools/cull_folder.py` | Culls a RAW folder using the camera previews stored inside the files |
| `tools/accuracy.py` | Compares the engine with real Lightroom exports (CIEDE2000) and writes side-by-side sheets |
| `tools/fit_params.py` | Fits the engine's strength constants to Lightroom exports |
| `tools/bench.py` | Render timing on the current machine |
| `tools/contact_sheet.py` | One photo in many presets on one sheet |

The engine is our own approximation of Lightroom, not Adobe's code. Its strength constants are
first guesses until `tools/fit_params.py` has been run on real Lightroom exports.

## Accounts and credits

| Part | State |
| --- | --- |
| `server/accounts.py` | Sign-up with email and password, sessions, confirm-email and reset links, one Lightroom key per photographer, credit ledger |
| `server/mail.py` | Account emails through Google Workspace SMTP (`SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`); printed to the log when not set |
| Separation | Every wedding, photo, export, cull and uploaded preset belongs to one account; others get 404 |
| Credits | 50 free on confirming the email; export or apply in Lightroom 1 per photo every time; culling 1 per 10 photos every run; failed, stopped or removed exports give their credits back |
| Studio account | `AUTOEDITS_ADMIN_EMAIL`; owns everything from before accounts; never stopped for credits, previews unmarked. Password of 12+ characters, two-step login (authenticator app + 10 backup codes), `/admin` asks for a code every 12 hours. `AUTOEDITS_KEY` (old shared key) retires for good once password, two-step and its own Lightroom key are set. Admin activity log on `/admin`. Lost phone: `python -m server.security --reset-two-step` |
| Admin dashboard | `/admin`: every photographer with credits and usage, add or take back credits, "View as" (read-only), download their uploads and exports |

## Commands

    python -m pytest -q tests
    python -m engine.presets presets/library data/presets.json
    python -m tools.bench
    python -m tools.accuracy TESTSET data/out/accuracy
    python -m tools.fit_params TESTSET data/params.json

## Test set layout

    TESTSET/
      base/                          Lightroom JPEG exports with no preset
      Cele-Brat MG 01 Signature/     the same photos exported with that preset
      ...
