# HOW TO USE - Cele-Brat Lightroom Presets v1.0

## 1. The 3-step workflow (every image)
1. Set White Balance first (As-Shot, a grey-card value, or your own eyedropper). The presets never touch WB, so this stays yours.
2. Apply the preset. Pick the look (studio/photographer), then the variation that matches the light (see section 2).
3. Fix Exposure only if needed (+/- 0.3 to 0.7 is normal). Done. Do not stack two looks on one image.

Sync the same preset across the whole event with Sync / Auto Sync, then fix exposure per frame.

## 2. Which variation for which light
| Variation | Use it on |
|---|---|
| 01 Signature | The core look - general purpose, start here |
| 02 Daylight Bright | Outdoor day, haldi / mehendi, bright venues - lifts exposure, tames highlights |
| 03 Reception Night | Indoor LED / tungsten / low light - opens shadows, protects highlights, pulls saturation off orange/yellow so skin does not go orange |
| 04 Soft Matte | Gentle faded version - couple portraits, bride getting ready, soft window light |
| 05 Rich | Punchier, deeper version - editorial hero frames, decor, colourful outfits |

Rule of thumb: one look per wedding, 01 for most frames, 02 for bright outdoor, 03 for receptions, 04 for soft portraits, 05 for hero frames.

## 3. Which look for which wedding
Studio Looks (Indian studios)

- Full-day mixed coverage, client-safe colours: KnotsbyAMP, Shutterink.
- Bright destination / lawn / beach / pastel decor: The Wedding Story (day), KnotsbyAMP.
- Bold, saturated, magazine-style hero frames: Joseph Radhik, Gautam Khullar.
- Night receptions, fairy lights, low-key drama: Fourfold Pictures, Gautam Khullar (03 at night).
- South-Indian silk / temple / gold: Studio A, WeddingNama.
- Soft romantic couple portraits and pre-weddings: Vivek Krishnan, Ramit Batra (04).
- Palace / heritage / golden hour editorial: Ramit Batra, Fourfold Pictures.

World Looks (fine-art photographers)

- Safe all-day "house" looks: Pablo Laguia, John Dolan, David Bastianoni, Abby Jiu.
- Bright, airy destination / pastel: KT Merry, Joel Serrato, Corbin Gurkin (strongest), James x Schulze (gardens).
- Golden warm outdoor and sunset: Jose Villa, Cinzia Bruschini, Norman and Blake, Bottega53.
- Moody, dramatic, low light and night: Erich McVey, Nirav Patel, Mili Ghosh (03 at receptions).
- Documentary / candid: Samm Blake, John Dolan, Heather Waraksa.
- Editorial and fashion-led: Kristen Marie Parker, Anni Graham, Greg Finck.
- Reds, maroons, sindoor outfits: Nirav Patel, Mili Ghosh, Jose Villa (05).

Glamour Looks (cover photographers) - for portraits and hero frames, not full documentary coverage

- Safest glamour on Indian skin: Alexi Lubomirski, Zoey Grossman, Greg Williams.
- Sunny / golden-hour cover portraits: Mario Testino, Zoey Grossman.
- Studio-style and flash-lit portraits, black-tie: Inez and Vinoodh, Luigi and Iango.
- Stylised colour (pink, lilac, neon, bold decor): Nick Knight, Txema Yeste, Emma Summerton (use Amount 50-70%).
- Moody cinematic night / grooms: Norman Jean Roy (03 at receptions).
- Rose / blush weddings, bridal makeup close-ups: Luigi and Iango, Nick Knight.

## 4. The Amount slider
Lightroom Classic 11+ shows an Amount slider above the preset list. 100% = as designed. 60-80% softens a strong look (Glamour, Joseph Radhik,
Gautam Khullar, Txema Yeste, Emma Summerton); 120-150% pushes a gentle look (KT Merry, Pablo Laguia) harder.

## 5. Skin tone

- The presets were tuned so skin stays natural on Indian skin in every light. If skin still looks orange, the cause is almost always white balance
  or a tungsten/LED mix: fix WB first, then use variation 03.

- Do not add Vibrance on top; use Exposure and, if needed, a small Orange luminance change in HSL.
- Glamour looks target slightly rosier, cleaner skin and soften skin texture a little (Texture -4 to -8). For a true cover finish add light
  retouching and a +10 Texture local brush on hair, eyes, jewellery and fabric.

## 6. Mixing stills and film
Every look also exists as a Sony S-Log3 LUT (CeleBrat_All_LUTs.zip) with the same name and variation. Preset and LUT were verified to land
within +/-2 RGB of each other on the same scene, so photos and video from one wedding match when you use the same look and variation.

## 7. Troubleshooting

- Preset looks flat / different: check the profile is Adobe Color (Basic panel > Profile). Camera Matching or Neutral profiles change the result.
- Preset does nothing on some sliders: Lightroom older than v13 ignores Process Version 15.4 colour grading; update Lightroom.
- Colours too strong on JPEGs: JPEGs already carry camera contrast; use 60-70% Amount or variation 04.
- Blown highlights at receptions: apply 03, then pull Highlights a further -20 if needed.
- Want B&W: apply the preset first, then press V (convert to B&W); the tone curve and grain carry over.

Support and licensing: info@cele-brat.com

## Landscape Looks (LL) - which look for which scene
- Safe all-round outdoor looks: Daniel Kordan, Thomas Heaton, Charlie Waite, Albert Dros.
- Mountains, snow, Ladakh / Spiti / Himalaya: Colin Prior, Marc Adamus, Max Rive, Erin Babnik (blue hour).
- Golden hour, deserts, dunes, Rajasthan sunsets: Ryan Dyar, Art Wolfe, Sean Bagshaw.
- Forests, waterfalls, monsoon green, mist: Enrico Fossati, Sean Bagshaw, Ted Gore, Alex Noriega (soft matte).
- Coast, sea, night sky: Michael Shainblum, Joe Cornish, Hans Strand (muted).
- Travel / cityscape / forts against blue sky: Elia Locardi, Peter Lik (hero prints), Albert Dros.
- Bright natural daylight: Nigel Danson, Thomas Heaton, Joe Cornish.
Landscape variations: 01 Signature, 02 Golden Hour, 03 Blue Hour Night, 04 Overcast Mist, 05 Dramatic.
These looks never change Exposure, Temperature or Tint - set exposure and WB first, then apply.

## Street Looks (ST) - which look for which frame
- Safe all-day documentary: Nick Turpin, Pau Buscato, Nils Jorgensen, Joel Meyerowitz (soft).
- Colourful Indian street, markets, processions, Holi: Steve McCurry, Maciej Dakowicz, Alex Webb, Harry Gruyaert.
- Bright daylight, sun, beaches, crowds: Martin Parr, Matt Stuart, Melissa O'Shaughnessy, Jesse Marlow.
- Night, neon, rain, blue hour: Joshua K Jackson, Craig Whitehead, Rammy Narula, Gueorgui Pinkhassov.
- Hard light and graphic frames: Siegfried Hansen, Mark Fearnley, Jesse Marlow. Flash candids / parties: Dougie Wallace, Martin Parr, Constantine Manos.
Street variations: 01 Signature, 02 Harsh Sun, 03 Night Neon, 04 Overcast Flat, 05 Punchy. These looks never change Exposure, Temperature or Tint.

## Portrait Looks (PT) - which look for which portrait
- Safe all-round: Mark Seliger, Art Streiber, Lara Jade, Rohan Shrestha.
- Bridal beauty / window light: Sue Bryce, Jingna Zhang, Brandon Woelfel (night glow), Prasad Naik (clean).
- Studio / white background / headshots: Peter Hurley, Rankin, Prasad Naik, Lindsay Adler (black background).
- Dramatic, cinematic grooms and couples: Dan Winters, Marco Grob, Gregory Heisler, Joey L.
- Outdoor environmental / golden hour: Miller Mobley, Joey L, Jimmy Nelson, Nadav Kander (muted).
- Character faces: Martin Schoeller, Jimmy Nelson. Coloured light / LED stages: Jake Hicks, Gregory Heisler.
Portrait variations: 01 Signature, 02 Window Light, 03 Studio Flash, 04 Soft Beauty, 05 Editorial Punch. Never change Exposure, Temperature or Tint.
