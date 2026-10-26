"""Recommended presets for a folder, from what its photos look like.

The worker keeps a few facts beside every photo once its automatic values are worked out
(engine/match.py facts_of: brightness, warmth, colour) and the camera's ISO and as-shot white
balance give the kind of light (bright outdoor, indoor tungsten, dusk). Those, plus the folder's
name (Haldi, Sangeet, Reception...), score every look the photographer may use. The top three
looks are shown pinned at the top of the Presets panel (static/recommend.js), each at the
variant that suits the light.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

TOP = 3
EVENT_WORDS = ("haldi", "mehendi", "mehndi", "sangeet", "reception", "baraat", "mandap", "pheras", "pre-wedding",
               "prewedding", "portrait", "couple", "bride", "groom", "candid", "night", "beach", "temple", "palace",
               "heritage", "street", "studio", "fashion", "party", "sunset")
SCENE_TAGS = {"bright outdoor": ("daylight", "outdoor", "bright", "airy", "natural"),
              "indoor tungsten": ("night", "reception", "evening", "flash", "warm", "indoor"),
              "dusk": ("sunset", "golden hour", "evening", "romantic", "dusk")}
SCENE_VARIANTS = {"bright outdoor": ("daylight",), "dusk": ("golden hour", "blue hour"),
                  "indoor tungsten": ("reception night", "blue hour night", "night")}


def folder_words(name: str) -> list[str]:
    text = (name or "").lower().replace("_", " ").replace("-", " ")
    found = [w for w in EVENT_WORDS if w.replace("-", " ") in text or w in text]
    if "mehndi" in found and "mehendi" not in found:
        found.append("mehendi")
    if "prewedding" in found:
        found.append("pre-wedding")
    return found


def score_look(tags: set, words: list[str], summary: dict) -> tuple[float, list[str]]:
    """How well a look's words fit the folder; returns the score and the words that matched."""
    score, why = 0.0, []
    for w in words:
        if w in tags:
            score += 3
            why.append(w)
    scene = summary.get("scene")
    hits = [t for t in SCENE_TAGS.get(scene, ()) if t in tags]
    score += min(2, len(hits))
    why += hits[:1]
    warmth, median, colour = summary.get("warmth"), summary.get("median"), summary.get("colour")
    if warmth is not None:
        if warmth > 0.05 and "warm" in tags:
            score += 1; why.append("warm")
        elif warmth < -0.03 and "cool" in tags:
            score += 1; why.append("cool")
    if median is not None:
        if median < 0.3 and ("moody" in tags or "dark" in tags):
            score += 1; why.append("moody")
        elif median >= 0.45 and "bright" in tags:
            score += 0.5
    if colour is not None:
        if colour > 0.25 and ("vibrant" in tags or "colourful" in tags):
            score += 1; why.append("vibrant")
        elif colour <= 0.18 and ("soft" in tags or "muted" in tags):
            score += 0.5
    if "wedding" in tags or "couple" in tags:
        score += 0.5
    seen, out = set(), []
    for w in why:
        if w not in seen:
            seen.add(w); out.append(w)
    return score, out[:3]


def pick_variant(variants: list[dict], scene: str | None) -> dict:
    """The variant for the light: Daylight for bright outdoor, Reception Night indoors, Golden/Blue Hour at dusk."""
    for want in SCENE_VARIANTS.get(scene, ()):
        for v in variants:
            if want in (v.get("variant") or "").lower():
                return v
    for v in variants:
        if "signature" in (v.get("variant") or "").lower():
            return v
    return variants[0]


def recommend(catalogue: list[dict], folder_name: str, summary: dict, top: int = TOP) -> list[dict]:
    """The top looks for this folder, one entry per look, different packs first when scores tie."""
    words = folder_words(folder_name)
    scene = summary.get("scene")
    looks: dict[str, list[dict]] = {}
    for p in catalogue:
        looks.setdefault(f"{p['pack']}|{p.get('look') or p['name']}", []).append(p)
    scored = []
    for key, variants in looks.items():
        first = variants[0]
        tags = set(t for v in variants for t in (v.get("tags") or []))
        if first.get("learned"):                      # the photographer's own learned style for this light comes first
            name = " ".join(v["name"].lower() for v in variants)
            score, why = (6.0, ["your style"]) if scene and scene in name else (0.0, [])
        elif first.get("mine"):
            score, why = 0.0, []
        else:
            score, why = score_look(tags, words, summary)
        if score > 0:
            scored.append((score, key, variants, why))
    scored.sort(key=lambda s: (-s[0], s[1]))

    def entry(variants, why, score):
        v = pick_variant(variants, scene)
        return {"id": v["id"], "name": v["name"], "look": v.get("look") or v["name"], "variant": v.get("variant"),
                "pack": v["pack"], "why": why, "score": round(score, 1)}

    out, packs, have = [], set(), set()
    for score, key, variants, why in scored:          # first one look per pack, so the three are not all one pack
        if variants[0]["pack"] in packs or len(out) >= top:
            continue
        out.append(entry(variants, why, score)); packs.add(variants[0]["pack"]); have.add(key)
    for score, key, variants, why in scored:          # then the best of the rest
        if len(out) >= top:
            break
        if key not in have:
            out.append(entry(variants, why, score)); have.add(key)
    return out[:top]


def make_router(ctx: dict) -> APIRouter:
    signed_in, store, renderer, wedding_or_404 = ctx["signed_in"], ctx["store"], ctx["renderer"], ctx["wedding_or_404"]
    r = APIRouter()

    @r.get("/api/weddings/{wedding_id}/folders/{folder_id}/recommended")
    def recommended(wedding_id: str, folder_id: int, user: dict = Depends(signed_in)):
        """The top three presets for this folder's photos; "pending" counts photos not yet measured
        (the app asks again in a moment while it is above 0)."""
        w = wedding_or_404(wedding_id, user)
        folder = next((f for f in w.get("folders", []) if f["id"] == folder_id), None)
        if folder is None:
            raise HTTPException(404, "no such folder")
        summary = renderer.match.summary(folder_id) if renderer.match is not None else {"n": 0, "measured": 0, "pending": 0, "scene": None}
        picks = recommend(renderer.catalogue(user["id"]), folder["name"], summary) if summary.get("measured") else []
        return {"recommended": picks, "scene": summary.get("scene"), "measured": summary.get("measured", 0),
                "pending": summary.get("pending", 0), "n": summary.get("n", 0)}

    return r
