---
name: tiktok-pack
description: Turn seller product photos + description sent in chat into a TikTok Shop affiliate content pack - real-product 9:16 hero image, Google Flow video prompts, Indonesian caption, hashtags, voiceover script, posting checklist.
version: 1.0.0
---

# TikTok Pack

Amar sells as a TikTok Shop affiliate (mostly perfume). He sends seller photos + the seller's
description; you return a ready-to-use content pack. He makes the video himself in Google Flow and
uploads to TikTok himself. Accuracy beats hype: TikTok Shop Indonesia requires AI visuals to match
the real product, and BPOM polices cosmetic claims.

## When to use
- Amar types `/tiktok-pack`, or sends product photos and asks for content to sell.
- Input = every image he sent since the last pack (album photos arrive as separate messages, each as
  "[User sent an image: <path>]") + the seller description (photo caption or text). Use at most 3.
- No description → ask once for it (copy from the seller listing), then wait. No photo → ask for one.
- One product per pack. Photos of different products → ask which one.

## 1. Facts
- From the seller TEXT only: brand, product name, variant, size (ml), scent notes, BPOM NA number,
  the seller's own claims. Never take the NA number from a photo (digits get misread).
- From the PHOTOS only appearance: colors, cap, bottle shape, material, spray atomizer or not.
- Never invent a fact. Unknown → leave it out.
- No NA number in the text → add the warning line in the reply.

## 2. Hero image
1. Pick the cleanest photo: a single product, fully visible including the cap, sharp, no "ORI 100%"
   badge, watermark, text overlay or other products. None qualifies → skip steps 2-5, say why in one
   line, and still send the text pack.
2. Write the scene: English, one sentence, at most 40 words, environment only (surface, background,
   light). Props only from the listed scent notes (rose petals for rose, citrus slices for citrus).
   Colors that complement the bottle; light direction like the photo. Never mention perfume, bottle,
   product, brand, label, logo or text (the script rejects them). Plain words only: no quotes, `$`
   or backticks.
3. Run exactly one command, paths quoted:
   `uv run "${HERMES_SKILL_DIR}/scripts/hero.py" "<photo path>" --scene "<scene>"`
   - `uv` not found → use `~/.local/bin/uv` (Windows: `%USERPROFILE%\.local\bin\uv.exe`).
   - The first run downloads ~300 MB and can take minutes. If the terminal moves it to the
     background, wait for it. Never start a second copy.
   - Never print or `cat` the `.env` file or the API key. The script reads the key itself.
4. Read the output: `WARN:` lines (pass them on), last line `HERO: <path>`. An `ERROR:` line is
   already in Indonesian: pass it on and continue with the text pack. Do not retry balance or key
   errors.
5. Look at the hero with vision: exactly one product, no extra bottles or products, no letters added
   in the background, nothing cut off, looks natural. Fails → run once more with a different scene.
   Fails again → skip the hero and say so. Each run costs about $0.015; max 2 runs unless Amar asks.

## 3. Reply (Indonesian, this order)
The hero goes out as a file through a `MEDIA:` line. Rules: the line is plain text on its own line
(never inside backticks or a code block), the hero path appears exactly once, and no other image
path (input photos, backdrop) appears anywhere in the reply, or it gets sent too.

```
MEDIA:<the HERO path>

**Video di Google Flow**
1. Flow → Frames to Video → upload gambar di atas sebagai frame awal, rasio 9:16, durasi 8 detik.
2. Clip A (copy):
<code block: prompt A>
3. Clip B (copy):
<code block: prompt B>
4. Gabung A+B di CapCut/editor TikTok.
Kalau botol/label berubah: ulang klipnya pakai foto asli seller sebagai frame awal.

**Caption + hashtag (copy)**
<code block: caption, empty line, hashtags>

**Teks layar**
1. ... 2. ... 3. ...

**Naskah VO 15 detik (rekam pakai suara kamu)**
...

<warning lines, if any>

**Checklist posting**
- [ ] Nyalain label konten AI pas posting
- [ ] Pasang link produk dari Showcase (keranjang kuning)
- [ ] Cek tiap frame: label, tutup, warna sama dengan foto asli
- [ ] Ada yang berubah → jangan posting, ulang klipnya
- [ ] Nomor NA BPOM ada (cek di cekbpom.pom.go.id)
- [ ] Pakai suara/footage kamu sendiri, jangan cuma teks + musik
```

Warning lines: no NA number → "Nomor NA BPOM gak ada di deskripsi seller. Cek dulu di
cekbpom.pom.go.id sebelum promosi." Plus every `WARN:` from the script and any skipped-hero reason.

## 4. Flow prompts (English, at most 80 words each)
Frames to Video only locks the first frame; Veo redraws the rest, so keep motion small.
- Clip A: slow push-in with a soft light sweep across the glass.
- Clip B: subtle parallax with gentle background elements from the scent notes (petals, mist, bokeh).
- Every prompt ends with: "The product stays exactly as in the first frame: same shape, cap, colors
  and label. Gentle camera, orbit under 15 degrees. No hands, no people. No speech, no voiceover,
  no on-screen text. Soft ambient music only."
- Spray mist only if the photos show an atomizer.

## 5. Copy rules
- Casual, polite Indonesian. Caption at most 300 characters, the first line is the hook, 1-2 facts
  from the seller text, ends with a CTA to the yellow basket ("cek keranjang kuning").
- 3-5 relevant hashtags (category, scent, brand if given).
- On-screen text: 3 lines, at most 6 words each.
- Voiceover: about 35-40 words for 15 seconds, describing look and scent notes. First-person usage
  ("aku udah pakai...") only if Amar says he has tried the product.
- Longevity or effect claims only if the seller text states them, never exaggerated.
- Never use: 100%, ampuh, dijamin, pasti, paling/no.1/terlaris, feromon/pheromone, "bikin orang
  tertarik/nempel", 24 jam/seharian, "ori/original" claims, medical or skin-treatment claims,
  prices or discounts, fake testimonials, fake stories, fake urgency ("stok tinggal 3!").
