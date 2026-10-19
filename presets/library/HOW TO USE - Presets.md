# HOW TO USE - Cele-Brat Lightroom Presets v1.0

## 1. The 3-step workflow (every image)
1. Set White Balance first (As-Shot, a grey-card value, or your own eyedropper). The presets never touch WB, so this stays yours.
2. Apply the preset. Pick the look, then the variation that matches the light (see section 2).
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
Studio Looks (Indian weddings)

- Full-day mixed coverage, client-safe colours: True Colour, Ritual Story.
- Bright destination / lawn / beach / pastel decor: Lawn Pastel (day), True Colour.
- Bold, saturated, magazine-style hero frames: Lehenga Red, Phera Fire.
- Night receptions, fairy lights, low-key drama: Candlelight Reception, Phera Fire (03 at night).
- South-Indian silk / temple / gold: Temple Silk, Mehendi Pop.
- Soft romantic couple portraits and pre-weddings: Soft Sangeet, Palace Matte (04).
- Palace / heritage / golden hour editorial: Palace Matte, Candlelight Reception.

World Looks (fine-art weddings)

- Safe all-day "house" looks: Easy Classic, Pure Reportage, Black Tie, Quiet Luxe.
- Bright, airy destination / pastel: Sea Breeze, Ivory Morning, Veil White (strongest), Spring Estate (gardens).
- Golden warm outdoor and sunset: Golden Mandap, Tuscan Sun, Backlit Bliss, Villa Stone.
- Moody, dramatic, low light and night: Forest Vows, Lantern Night, Sindoor (03 at receptions).
- Documentary / candid: Baraat Energy, Pure Reportage, Timeless Window.
- Editorial and fashion-led: Poolside Edit, Desert Film, Olive Grove.
- Reds, maroons, sindoor outfits: Lantern Night, Sindoor, Golden Mandap (05).

Glamour Looks (cover-style portraits) - for portraits and hero frames, not full documentary coverage

- Safest glamour on Indian skin: Velvet Court, Haldi Sun, Backstage Gold.
- Sunny / golden-hour cover portraits: Sunlit Cover, Haldi Sun.
- Studio-style and flash-lit portraits, black-tie: Ink and Ivory, Rosewater.
- Stylised colour (pink, lilac, neon, bold decor): Candy Chrome, Colour Riot, Pop Bloom (use Amount 50-70%).
- Moody cinematic night / grooms: Midnight Teal (03 at receptions).
- Rose / blush weddings, bridal makeup close-ups: Rosewater, Candy Chrome.

## 4. The Amount slider
Lightroom Classic 11+ shows an Amount slider above the preset list. 100% = as designed. 60-80% softens a strong look (Glamour, Lehenga Red,
Phera Fire, Colour Riot, Pop Bloom); 120-150% pushes a gentle look (Sea Breeze, Easy Classic) harder.

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
- Safe all-round outdoor looks: Clear Lake, Honest Hills, Quiet Meadow, Mustard Fields.
- Mountains, snow, Ladakh / Spiti / Himalaya: Glacier Air, Thunder Ridge, Storm Peak, Twilight Pastel (blue hour).
- Golden hour, deserts, dunes, Rajasthan sunsets: Ember Sunset, Wild Earth, Fern Gorge.
- Forests, waterfalls, monsoon green, mist: Monsoon Castle, Fern Gorge, Dark Cascade, Blue Mist (soft matte).
- Coast, sea, night sky: Night Tide, Heather Coast, Nordic Frost (muted).
- Travel / cityscape / forts against blue sky: Fort Skyline, Gallery Canyon (hero prints), Mustard Fields.
- Bright natural daylight: Autumn Moor, Honest Hills, Heather Coast.
Landscape variations: 01 Signature, 02 Golden Hour, 03 Blue Hour Night, 04 Overcast Mist, 05 Dramatic.
These looks never change Exposure, Temperature or Tint - set exposure and WB first, then apply.

## Street Looks (ST) - which look for which frame
- Safe all-day documentary: Bus Window, Street Wit, Small Moments, Seventies Walk (soft).
- Colourful Indian street, markets, processions, Holi: Pushkar Colour, Market Night, Layer Bazaar, Evening Block.
- Bright daylight, sun, beaches, crowds: Pool Flash, Sunny Side, Pastel Avenue, Noon Graphic.
- Night, neon, rain, blue hour: Rain Neon, Red Umbrella, Platform Glow, Prism Street.
- Hard light and graphic frames: Hard Lines, Light Shaft, Noon Graphic. Flash candids / parties: Flash Riot, Pool Flash, Fair Day.
Street variations: 01 Signature, 02 Harsh Sun, 03 Night Neon, 04 Overcast Flat, 05 Punchy. These looks never change Exposure, Temperature or Tint.

## Portrait Looks (PT) - which look for which portrait
- Safe all-round: Grand Room, Grand Ensemble, Cream Couture, Filmi Glam.
- Bridal beauty / window light: Window Glow, Silk Canvas, Fairy Lights (night glow), Cover Story (clean).
- Studio / white background / headshots: Clean Frame, Seamless Gloss, Cover Story, Red Lip Noir (black background).
- Dramatic, cinematic grooms and couples: Oil Shadow, Blue Hour Power, Sculpted Light, Rembrandt Dust.
- Outdoor environmental / golden hour: Ranch Gold, Rembrandt Dust, Heritage Plate, Still Matte (muted).
- Character faces: Close Focus, Heritage Plate. Coloured light / LED stages: Neon Gel, Sculpted Light.
Portrait variations: 01 Signature, 02 Window Light, 03 Studio Flash, 04 Soft Beauty, 05 Editorial Punch. Never change Exposure, Temperature or Tint.
