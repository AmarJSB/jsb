# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["rembg[cpu]>=2.0.60,<3", "pillow", "requests", "truststore"]
# [tool.uv]
# exclude-newer = "2026-10-08T00:00:00Z"
# ///
"""TikTok Pack versi web lokal: foto produk + deskripsi seller -> storyboard video TikTok (shot rekam sendiri +
shot AI Google Flow), aset gambar dari produk asli, hook, naskah VO, caption, cover.

Windows: klik dua kali Mulai.bat. Lainnya: uv run app.py. Lalu buka http://127.0.0.1:8765
"""
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import truststore

truststore.inject_into_ssl()  # pakai sertifikat Windows, aman dari antivirus yang nyadap HTTPS

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent / "hermes-skills" / "tiktok-pack" / "scripts"))  # kalau jalan dari repo
import hero  # noqa: E402
from PIL import Image, ImageDraw, ImageFont, ImageOps  # noqa: E402

HOST, PORT = "127.0.0.1", 8765
BASE = os.environ.get("ZAI_BASE", "https://api.z.ai/api/paas/v4")
hero.API = BASE + "/images/generations"
hero.ERRORS["401"] = "API key z.ai ditolak. Cek di Pengaturan."
DATA = HERE / "data"
OUT = DATA / "hasil"
SETTINGS = DATA / "settings.json"
MODELS = [  # [id, nama, keterangan]
    ["glm-5.3-flash", "GLM-5.3 Flash", "Default. Cepat, murah, bisa baca foto."],
    ["glm-4.6v", "GLM-4.6V", "Model khusus foto. Lebih mahal."],
    ["glm-4.6v-flash", "GLM-4.6V Flash", "Gratis, tapi kualitas teks lebih rendah."],
]
MODEL_IDS = [m[0] for m in MODELS]
MAX_BODY = 60 * 1024 * 1024
JOB_RE = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")
FILE_RE = re.compile(r"/hasil/(\d{8}-\d{6}-[0-9a-f]{4})/((?:hero|close|hand|side|scene|product|cover)-[1-3]-[0-9a-f]{10}"
                     r"\.jpg|paket-[1-3]-[0-9a-f]{10}\.zip)")
BPOM_RE = re.compile(r"\bN[A-E][\s.-]?\d{11}\b", re.I)
# Kata/klaim yang bikin konten kena BPOM, aturan TikTok Shop (klaim, merek lain, arahin keluar platform), atau
# urgensi palsu. Cuma jadi peringatan: Amar yang mutusin.
BANNED_COPY = re.compile(
    r"100\s*(%|persen)|\brp\s?\d|\b\d+\s?(rb|ribu)\b|\bbest\s?seller|\bbikin\s+(\w+\s+){0,2}(nempel|tertarik|klepek)"
    r"|\blink\s+di\s+bio|\b(ampuh|dijamin|jaminan|pasti|paling|terbaik|terlaris|termurah|terwangi|juara|no\.?\s?1"
    r"|nomor\s?1|feromon|pheromone|dilirik|menggoda|memikat|lawan jenis|24\s?jam|48\s?jam|seharian|ori|original|asli"
    r"|murni|replika|aman|efek samping|tidak berbahaya|hipoalergenik|obat|menyembuhkan|dokter|apoteker|bumil"
    r"|kulit sensitif|mirip|dupe|kw|inspired|terinspirasi|alternatif|diskon|promo|murah|stok|kehabisan|terbatas"
    r"|dm|wa|whatsapp|shopee|lazada)(nya)?\b",
    re.I,
)
TAG_BAD = re.compile(r"murah|original|(ori|asli|kw)$|feromon|pheromone|24jam|seharian|dupe|inspired|diskon|promo"
                     r"|terlaris|terbaik|bestseller", re.I)  # hashtag gabungan kayak #parfumori, #parfummurah
FALLBACK_SCENE = ("eye-level, smooth pale stone surface, softly blurred warm beige wall behind, gentle window light "
                  "from the left")
BPOM_WARNING = "Nomor BPOM (NA/NB/NC/ND/NE...) gak ada di deskripsi seller. Cek dulu di cekbpom.pom.go.id sebelum promosi."
WORDS_PER_SECOND = 2.3  # ngomong santai-semangat; lebih dari ini naskahnya keburu-buru
CHAT_ERRORS = {
    **hero.ERRORS,
    "1301": "z.ai nolak konten ini (filter). Ubah deskripsi atau ganti foto.",
    "1210": "Model ini nolak permintaannya (foto/parameter gak didukung). Ganti model di Pengaturan.",
    "1211": "Model ini gak ada di akun z.ai kamu. Ganti model di Pengaturan.",
}

# Shot yang bisa dipakai. Rekam sendiri = bukti nyata + suara/muka Amar (TikTok Shop ngebatesin video yang cuma
# produk/tangan tanpa suara atau konteks pribadi). AI = klip Google Flow dari foto produk asli.
SHOTS = {  # kode: (nama, aset frame awal, mode, risiko produk berubah, tips rekam)
    "r_face": ("Kamu ngomong ke kamera", None, "rekam", "", "HP sejajar mata, hadap jendela biar muka terang. Ngomong "
               "santai pakai kata-katamu sendiri. Kalau ada produknya, pegang di samping muka."),
    "r_pick": ("POV ambil produk", None, "rekam", "", "HP setinggi dada. Ambil produk pelan-pelan, label hadap kamera, "
               "jari di bawah label. Gerak lebih pelan dari biasanya."),
    "r_spray": ("Semprot di pergelangan/leher", None, "rekam", "", "Cahaya dari samping atau belakang biar semprotannya "
                "kelihatan. Rekam suara 'psst'-nya."),
    "r_size": ("Ukuran di tangan", None, "rekam", "", "Pegang di depan kamera biar kelihatan ukuran aslinya. Jangan pakai "
               "lensa 0.5x (bikin ukuran menipu)."),
    "r_pack": ("Kemasan & nomor BPOM", None, "rekam", "", "Tunjukin kotak, segel, dan nomor BPOM di kemasan dari dekat, "
               "pelan-pelan."),
    "a_hero": ("Produk, kamera maju pelan", "hero", "frames", "rendah", ""),
    "a_fx": ("Produk + efek aroma", "hero", "frames", "rendah", ""),
    "a_close": ("Close-up detail", "close", "frames", "rendah", ""),
    "a_hand": ("Dipegang tangan (AI)", "hand", "nb", "sedang", ""),
    "a_spray": ("Disemprot (AI)", "hand", "nb", "tinggi", ""),
    "a_notes": ("B-roll notes aroma", None, "text", "rendah", ""),
    "a_scene": ("Suasana tanpa produk", "scene", "frames", "rendah", ""),
    "a_loop": ("Penutup loop + CTA", "side", "loop", "rendah", ""),
}
# Template storyboard: (nama, butuh produk fisik, [(beat, detik, kode shot yang boleh, urut prioritas)]).
# Hook selalu nampilin produk di frame pertama; tiap template ada momen suara/muka Amar.
TEMPLATES = {
    "review": ("Review sensorik", False, [
        ("Hook", 2, ["r_spray", "r_pick", "a_hand"]),
        ("Produk + jenis wangi", 2.5, ["a_hero"]),
        ("Notes aroma", 2.5, ["a_notes"]),
        ("Notes aroma", 2.5, ["a_fx", "a_notes"]),
        ("Kesan pribadi", 4, ["r_face"]),
        ("Fakta: ukuran & jenis", 3.5, ["r_size", "a_close"]),
        ("Jujur: cocok buat siapa", 4, ["r_face", "a_scene"]),
        ("CTA", 3, ["a_loop"]),
    ]),
    "alasan": ("3 Alasan", False, [
        ("Hook", 2, ["r_pick", "a_hand"]),
        ("Alasan 1: wanginya", 3, ["a_notes"]),
        ("Alasan 1: wanginya", 2.5, ["a_fx"]),
        ("Alasan 2: momen pakai", 4, ["r_face", "a_scene"]),
        ("Alasan 3: ukuran & kemasan", 3.5, ["r_size", "r_pack", "a_close"]),
        ("Produk", 3, ["a_hero"]),
        ("CTA", 3, ["a_loop"]),
    ]),
    "pov": ("POV momen", False, [
        ("Hook POV", 2, ["r_pick", "a_hand"]),
        ("Suasana momen", 3, ["a_scene"]),
        ("Pakai", 3, ["r_spray", "a_spray", "a_fx"]),
        ("Notes aroma", 2.5, ["a_notes"]),
        ("Kesan", 4, ["r_face"]),
        ("CTA", 3.5, ["a_loop"]),
    ]),
    "unboxing": ("Unboxing ASMR", True, [
        ("Hook: kemasan", 2.5, ["r_pack"]),
        ("Buka & ambil", 3.5, ["r_pick"]),
        ("Semprot pertama", 3, ["r_spray", "r_size"]),
        ("Detail", 2.5, ["a_close"]),
        ("Kesan pertama", 4, ["r_face"]),
        ("CTA", 3, ["a_loop"]),
    ]),
    "sinematik": ("Sinematik", False, [
        ("Hook: reveal", 2, ["a_fx"]),
        ("Produk", 3, ["a_hero"]),
        ("Detail", 2, ["a_close"]),
        ("Notes aroma", 2.5, ["a_notes"]),
        ("Dipegang", 3, ["r_pick", "a_hand"]),
        ("Suara kamu", 3.5, ["r_face"]),
        ("CTA", 3, ["a_loop"]),
    ]),
}
ASSETS = [  # kunci, nama file buat Flow/zip, label, kegunaan
    ("hero", "1-hero.jpg", "Hero", "Frame awal shot produk"),
    ("close", "2-closeup.jpg", "Close-up", "Frame awal shot detail"),
    ("hand", "3-siap-dipegang.jpg", "Siap dipegang", "Bahan Nano Banana buat shot tangan"),
    ("side", "4-ruang-teks.jpg", "Ruang teks", "Frame penutup + CTA"),
    ("scene", "5-scene-kosong.jpg", "Scene kosong", "Frame awal shot suasana"),
    ("product", "6-produk-referensi.jpg", "Produk referensi", "Referensi produk buat Nano Banana"),
    ("cover", "7-cover.jpg", "Cover TikTok", "Pasang sebagai cover pas posting"),
]
ASSET_FILE = {k: f for k, f, _, _ in ASSETS}
MODE_STEPS = {
    "frames": "Flow → Video → Frames → + Add start frame: {file} → 9:16, 8 detik, Veo 3.1 Fast (Lite buat coba-coba) → "
              "tempel prompt video → Generate. Pakai {sec} detik terbaiknya.",
    "loop": "Flow → Video → Frames → start frame DAN end frame: {file} (gambar yang sama, biar nyambung loop) → 9:16, "
            "8 detik → tempel prompt video → Generate. Pakai {sec} detik terbaiknya.",
    "nb": "1) Flow → Image (Nano Banana) → 9:16 → lampirkan {file} dulu, lalu 6-produk-referensi.jpg → tempel prompt "
          "gambar → pilih hasil yang label, tutup & jarinya bener (jari 5). 2) Flow → Video → Frames → start frame = "
          "gambar pilihan tadi → tempel prompt video → Generate. Pakai {sec} detik terbaiknya.",
    "text": "Flow → Video → Text to Video → 9:16, 8 detik → tempel prompt video → Generate. Pakai {sec} detik terbaiknya.",
}
DEFAULT_MOTION = {
    "a_hero": "Slow, smooth dolly-in toward the {noun}, from the current framing to a medium close-up. Only the camera "
              "moves. Slow motion.",
    "a_fx": "Locked-off static camera while {fx} drift slowly through the background and around the base of the {noun}, "
            "never passing in front of the label. Slow motion.",
    "a_close": "Macro lens look, shallow depth of field. Very slow push-in across the {cap} and the label; focus stays "
               "sharp on the label.",
    "a_hand": "The hand holds the {noun} steady, raises it a few centimeters toward the camera with a very slight tilt "
              "so light glints on the {cap}, then holds still. Slow, subtle dolly-in.",
    "a_spray": "Locked-off camera. The index finger presses the nozzle once; a fine, soft mist sprays sideways onto the "
               "wrist and drifts gently in the light. The {noun} stays upright and still; the hand barely moves.",
    "a_notes": "Macro shot of {fx} in slow motion, shallow depth of field, gentle movement.",
    "a_scene": "Locked-off static camera while {fx} drift slowly through the scene and the soft light shifts gently. "
               "Slow motion.",
    "a_loop": "The camera slowly pushes in a little toward the {noun}, then eases back to the exact starting framing, "
              "creating a seamless loop. Only the camera moves.",
}
DEFAULT_EDIT = {
    "a_hand": "Add one realistic {hand} entering from the bottom edge and holding the {noun} in mid-air at chest height, "
              "like a casual smartphone product photo; the {noun} is {size}.",
    "a_spray": "Add one realistic {hand} holding the {noun} upright with the index finger resting on top of the spray "
               "nozzle, and the bare wrist of the other arm entering from the side about 10 cm from the nozzle.",
}
HANDS = {
    "wanita": "woman's right hand with warm medium-tan skin and short natural nude nails",
    "pria": "man's right hand with warm medium-tan skin and short clean nails",
}
CHECKLIST = [
    "Nyalain label konten AI (More options → AI-generated content) sebelum posting, gak bisa diubah setelahnya.",
    "Nyalain pengungkapan konten komersial (Content disclosure) karena ini video afiliasi.",
    "Link produk di keranjang kuning = produk & varian yang sama dengan di video.",
    "Produk kelihatan di detik pertama; gak ada harga, diskon, atau screenshot halaman produk.",
    "Musik dari Commercial Music Library TikTok, suara utama = suara kamu sendiri (bukan TTS).",
    "Cek tiap klip AI: label, tutup, warna, dan ukuran sama dengan foto asli; jari tangan AI harus 5. Ada yang berubah → jangan dipakai.",
    "Nomor BPOM dicek di cekbpom.pom.go.id: merek & nama produk cocok.",
    "Pasang 7-cover.jpg sebagai cover.",
    "Jangan posting video yang mirip-mirip berturut-turut; ganti template & hook tiap video.",
]
CAPCUT_STEPS = [
    "Generate semua klip AI di Flow (8 detik), download, lalu rekam shot 'rekam sendiri' pakai HP.",
    "CapCut → proyek baru 9:16 → masukin klip sesuai urutan storyboard, potong tiap klip sesuai detiknya.",
    "Rekam voice over pakai suara kamu sendiri (naskah VO di bawah), taruh di bawah semua klip.",
    "Tambah teks layar tiap shot di area tengah-atas (jangan di 1/4 bawah & pinggir kanan: ketutup tombol TikTok).",
    "Musik pelan di bawah suara kamu; pilih lagunya nanti di TikTok (Commercial Music Library).",
    "Export 1080p, upload, pasang cover, nyalain label AI + konten komersial, pasang link produk.",
]

# Aturan di PROMPT ini lebih lengkap dari hermes-skills/tiktok-pack/SKILL.md (skill Hermes versi lama yang lebih simpel).
PROMPT = """You plan a TikTok Shop affiliate video for Amar, an Indonesian affiliate who mostly sells perfume. Goal: a professional, high-retention, compliant 9:16 video that he assembles in CapCut from (a) shots he films himself with his phone (codes r_*) and (b) Google Flow (Veo) clips made from the REAL product photo (codes a_*). Accuracy beats hype: TikTok Shop Indonesia penalizes AI visuals that do not match the real product and videos that only show the product or hands with no voice or personal context; BPOM polices cosmetic claims.

INPUT
- The seller description is between <<< and >>> at the end.
- [[N]] product photo(s) are attached in order: photo 1 is the first image.
- Amar has used this product: [[TRIED]]. Amar physically has the product to film: [[HAS]].
- Hand preference for AI hand shots: [[HAND]].
- Template "[[TEMPLATE]]", [[SECONDS]] seconds. Slots in order (beat, seconds, allowed shot codes in order of preference):
[[SLOTS]]
- Recently used first-hook types (put a different type first): [[RECENT]].

FACTS
- From the seller TEXT only: brand, product name, variant, size (ml), concentration (EDP/EDT), scent notes, the seller's own claims (skip comparisons to other brands). Never take a BPOM number from a photo.
- From the PHOTOS only appearance: colors, cap, bottle shape, material, spray atomizer or not.
- Never invent a fact. Unknown -> leave it out.
- One product per pack: if the photos or the text cover several products/variants, use only the one in best_photo, take only its facts, and say so in notes.

best_photo: number of the cleanest photo: a single product, fully visible including the cap, sharp, no "ORI 100%" badge, price banner, watermark, text overlay or other products. Use 0 if none qualifies and explain why in photo_problem (Indonesian, one sentence).

product: noun (short English noun, e.g. "perfume bottle"), cap (English, e.g. "square gold cap"), size_cue (English, from the ml if known, e.g. "about 10 cm tall", else "fits in one palm"), label_text (the large words printed on the front label exactly as seen, only if clearly legible, else ""), atomizer (true only if a spray nozzle is visible).

family: one of floral, fruity, citrus, fresh, green, woody, amber, gourmand, musky, spicy, oud (from the notes; "fresh" if unknown). Visual language per family (palette / props / light):
- floral: blush pink, ivory, soft gold / petals of the listed flowers, silk / soft window light, high-key
- fruity: coral, berry red, pastel pink / sliced fruit from the notes, water droplets / bright, crisp
- citrus: lemon yellow, orange, fresh green, white / citrus slices, zest, leaves / bright soft daylight
- fresh: aqua, sea blue, white / water surface, wet stones, rippled silk / cool light with water caustics
- green: sage, leaf green, cream / leaves, herb sprigs, moss / dappled soft light
- woody: walnut brown, sand, charcoal / raw wood, stone, linen / warm low-key side light, light haze
- amber: amber, gold, deep burgundy / resin chunks, brass dish, velvet / warm golden light through the liquid
- gourmand: vanilla cream, caramel, cocoa / vanilla pods, coffee beans, caramel drizzle / warm soft window light
- musky: ivory, beige, soft lilac / white linen, satin, soft knit / high-key, low contrast
- spicy: cinnamon, saffron, deep red / cinnamon sticks, star anise, terracotta / warm directional light
- oud: cognac brown, black, antique gold / agarwood chips in a brass dish, leather, dark wood / dark background with rim lights
Never use smoke, cigars, candles, alcohol or drinks as props; use haze, resin, honey or caramel instead.

scene (English, one sentence, max 45 words): the empty set for the product photo. Start with the camera height that matches the photo ("eye-level" if the cap top is not visible, "slightly high angle" if it is), then the surface (it fills the lower third of the frame), the background and the light (key-light side and color temperature as in the photo). At most 3 props, only from the family list and the listed notes. If the photo was shot on a white or light background and the bottle is clear glass, keep the scene light and high-key. Never mention perfume, bottle, product, brand, label, logo or text, not even negated.
look (English, max 25 words): one consistent look for every AI clip: light direction, palette words, lens, finish. Example: "soft window light from the left, warm ivory and blush tones, 85mm lens look, shallow depth of field, clean commercial finish".
audio (English, max 12 words): ambient sound effects only for the AI clips, e.g. "soft room tone and a gentle rustle of petals".
fx (English, max 12 words): small moving elements from the notes for ambient shots, e.g. "a few rose petals and soft bokeh".
hand (English, no leading article): the hand for AI hand shots following the hand preference, e.g. "woman's right hand with warm medium-tan skin and short natural nude nails". If the preference is "auto", choose from the product's target (unisex -> woman's hand).

hooks: exactly 5, each a different type from: analogi, pertanyaan, masalah, 3 alasan, notes, ukuran, momen, POV, jujur[[HOOK_EXTRA]]. Each has type, text (on-screen, max 6 words) and vo (spoken by Amar, max 10 words). Every hook names the product or its scent within the first 3 seconds and fits the product. Examples of the style: "Wanginya kayak baru keluar kamar mandi hotel." / "Kamu tim wangi sabun atau tim wangi manis?" / "Naik motor siang-siang, wangimu masih ada?" / "Tiga alasan parfum ini selalu ada di tasku." (the last one only if Amar has used it).

shots: exactly one entry per slot, in the same order. For each:
- code: the FIRST allowed code of that slot that fits (a_spray and r_spray only if atomizer is true).
- text: on-screen text, max 6 words, that repeats the spoken keyword ("" allowed except slot 1 and the last slot).
- vo: the spoken line for this slot in Amar's own voice. Slot 1 uses the first hook's vo.
- motion (only for a_* codes, English, max 40 words): what moves and how the camera moves. The product never moves by itself; use slow motion and small moves. For a_notes describe the scent-note ingredients in macro motion with no product and no people.
- edit (only for a_hand and a_spray, English, max 35 words): how the hand holds the product in the still image.
- direction (only for r_* codes, Indonesian, max 30 words): exactly what Amar films and says in that shot.
Pacing: a new visual every 1.5-3 seconds and at least 2 visual events in the first 3 seconds.
Voice-over: about [[WORDS]] words in total. Flow: hook -> product name + scent family -> notes as everyday Indonesian analogies ("wangi habis mandi", "kemeja baru dicuci") -> when and where to wear it (weather, office, motorbike, kondangan) -> one honest "bukan buat kamu kalau ..." line -> CTA. Say the main keyword in the first 5 seconds.
The last slot is the CTA: its text and vo point to the "keranjang kuning", e.g. "Ukuran sama varian lengkapnya ada di keranjang kuning." or "Harga terbarunya cek langsung di keranjang kuning, ya."

keyword: the main TikTok search keyword (Indonesian, 2-4 words, e.g. "parfum wangi sabun"), no claim words.
caption: max 300 characters. Line 1 = keyword + benefit within the first 70 characters. Line 2 = scent and occasion (personal test details only if Amar has used it). Line 3 = CTA to the keranjang kuning. No hashtags inside.
hashtags: 3-5 (category, scent family, use case, this product's brand only if given); no claim words, no other brands.
cover: 2-5 word cover title in Indonesian (no price, no claim).
music: one Indonesian line: mood + tempo to pick in TikTok's Commercial Music Library, e.g. "lo-fi santai 85-95 BPM".
notes: Indonesian warnings for Amar (missing info, risky seller claims you left out), or []. Skip the BPOM number: the app checks it.

COPY RULES (casual, polite Indonesian, Amar's own voice)
- [[FIRST_PERSON]]
- Longevity or effect claims only if the seller text states them, never exaggerated; never give hour numbers unless Amar tested it.
- Never use: 100%, ampuh, dijamin, pasti, paling/terbaik/no.1/juara/terlaris, feromon/pheromone, attraction or seduction claims in any wording ("bikin orang tertarik/nempel", "dilirik", "menggoda", "memikat", "lawan jenis", "klepek"), 24 jam/48 jam/seharian, "ori/original/asli/murni" claims, safety claims ("aman", "tanpa efek samping", "tidak berbahaya", "hipoalergenik", "aman buat bumil/kulit sensitif"), medical claims (obat, menyembuhkan), doctors or pharmacists, other brands' or perfumes' names and comparisons ("inspired by", "terinspirasi", "mirip", "dupe", "KW", "replika", "alternatif X"), prices or discounts, "link di bio", DM, WA, other marketplaces, fake testimonials, fake reactions, fake stories, fake urgency ("stok tinggal 3!", "terbatas").

OUTPUT: reply with ONLY one JSON object, no markdown, exactly these keys:
{"best_photo": 1, "photo_problem": "", "product": {"noun": "", "cap": "", "size_cue": "", "label_text": "", "atomizer": false}, "family": "", "keyword": "", "scene": "", "look": "", "audio": "", "fx": "", "hand": "", "hooks": [{"type": "", "text": "", "vo": ""}], "shots": [{"code": "", "text": "", "vo": "", "motion": "", "edit": "", "direction": ""}], "caption": "", "hashtags": ["#..."], "cover": "", "music": "", "notes": []}

<<<
[[DESC]]
>>>"""

Fail = hero.Fail
_rembg_lock = threading.Lock()
_rembg = None
_rembg_crash = False  # True kalau load rembg bikin Python crash (biasanya VC++ runtime lama)


def text(v):
    return v.strip() if isinstance(v, str) else "" if v is None else str(v).strip()


def lines(v):
    items = v if isinstance(v, list) else text(v).splitlines()
    return [t for t in (text(x) for x in items) if t]


def load_settings():
    try:
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    s = s if isinstance(s, dict) else {}
    return {"key": text(s.get("key")), "model": s.get("model") if s.get("model") in MODEL_IDS else MODEL_IDS[0]}


def public(s):
    return {"has_key": bool(s["key"]), "key_hint": "..." + s["key"][-4:] if s["key"] else "",
            "model": s["model"], "models": MODELS,
            "templates": [[k, t[0], t[1]] for k, t in TEMPLATES.items()]}


def save_settings(body):
    s = load_settings()
    key = text(body.get("key"))
    if key:
        if not re.fullmatch(r"[\x21-\x7e]+", key):
            raise Fail("API key gak valid (ada spasi atau karakter aneh). Copy ulang dari z.ai.")
        s["key"] = key
    if body.get("model") in MODEL_IDS:
        s["model"] = body["model"]
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(s), encoding="utf-8")
    os.replace(tmp, SETTINGS)
    return public(s)


def decode_photo(url, i):
    try:
        with Image.open(BytesIO(base64.b64decode(str(url).split(",", 1)[1]))) as im:
            return ImageOps.exif_transpose(im).convert("RGB")
    except Exception:
        raise Fail(f"Foto ke-{i} gak bisa dibuka. Pakai JPG/PNG/WebP (HEIC gak bisa).") from None


def shrink(img, px):
    img = img.copy()
    img.thumbnail((px, px))
    return img


def jpeg_url(img):
    buf = BytesIO()
    shrink(img, 1280).save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def chat_body(model, content, max_tokens=8192):
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]}
    if model.startswith("glm-5"):
        body["reasoning_effort"] = "low"  # GLM-5.x selalu "mikir"; low biar cepat & hemat
    return body


def zai_chat(model, content, key, max_tokens=8192):
    import requests

    try:
        r = requests.post(BASE + "/chat/completions", headers={"Authorization": f"Bearer {key}"},
                          json=chat_body(model, content, max_tokens), timeout=(10, 170))
    except requests.RequestException as e:
        raise Fail(f"Gagal konek ke z.ai ({type(e).__name__}). Cek internet lalu coba lagi.") from None
    try:
        data = r.json()
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    choice = (data.get("choices") or [None])[0]
    if r.status_code != 200 or not isinstance(choice, dict):
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        code = str(err.get("code") or r.status_code)
        raise Fail(CHAT_ERRORS.get(code) or CHAT_ERRORS.get(str(r.status_code))
                   or f"z.ai error {code}: {err.get('message') or r.text[:200]}")
    return text((choice.get("message") or {}).get("content")), choice.get("finish_reason")


def parse_reply(reply, finish=None):
    s = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S)
    s = s.replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "")
    try:  # strict=False: AI kadang nulis enter mentah di dalam string; jawaban yang diulang di akhir diabaikan
        obj = json.JSONDecoder(strict=False).raw_decode(s, s.index("{"))[0]
    except ValueError:
        obj = None
    if not isinstance(obj, dict):
        raise Fail("Jawaban AI kepotong, klik Bikin paket lagi." if finish == "length"
                   else CHAT_ERRORS["1301"] if finish == "sensitive"
                   else "Jawaban AI gak kebaca, klik Bikin paket lagi.")
    return obj


def banned_hits(s):
    return sorted({m.group(0).lower() for m in BANNED_COPY.finditer(s)})


def sentence(s):
    s = text(s)
    s = s[:1].upper() + s[1:]
    return s if not s or s[-1] in ".!?" else s + "."


def num(x):
    """Angka buat dibaca Amar: koma desimal ala Indonesia (2,5 bukan 2.5)."""
    return f"{x:g}".replace(".", ",")


def available(code, has_product, atomizer=True):
    if code.startswith("r_") and code != "r_face" and not has_product:
        return False
    return atomizer or code not in ("r_spray", "a_spray")


def resolve_slots(template, has_product):
    """Kode shot yang boleh per slot, sesuai punya produk fisik atau gak (atomizer baru ketahuan dari foto)."""
    return [(beat, sec, [c for c in cands if available(c, has_product)]) for beat, sec, cands in TEMPLATES[template][2]]


def recent_packs(limit=10):
    try:
        jobs = sorted((d for d in OUT.iterdir() if JOB_RE.fullmatch(d.name)), reverse=True)
    except OSError:
        return []
    packs = []
    for d in jobs[:limit]:
        try:
            packs.append(json.loads((d / "pack.json").read_text(encoding="utf-8")).get("meta", {}))
        except (OSError, ValueError, AttributeError):
            pass
    return packs


def pick_template(style, has_product):
    """Template pilihan Amar, atau yang paling lama gak dipakai (biar video gak mirip-mirip)."""
    allowed = [k for k, t in TEMPLATES.items() if has_product or not t[1]]
    if style in allowed:
        return style
    recent = [p.get("template") for p in recent_packs()]
    return max(allowed, key=lambda k: recent.index(k) if k in recent else len(recent) + 1)


def build_prompt(desc, n, tried, has_product, hand, template, slots, recent_hooks):
    seconds = sum(sec for _, sec, _ in slots)
    slot_lines = "\n".join(f"  {i}. {beat}, {sec} s: {', '.join(c)}" for i, (beat, sec, c) in enumerate(slots, 1))
    extra = (", tes pakai" if tried and has_product else "") + (", unboxing" if has_product else "")
    first = ("First-person usage is allowed, but stay factual." if tried
             else 'Never write first-person usage ("aku udah pakai..."): describe the look, the notes and who it suits.')
    repl = {
        "[[N]]": str(n), "[[TRIED]]": "yes" if tried else "no", "[[HAS]]": "yes" if has_product else "no",
        "[[HAND]]": hand, "[[TEMPLATE]]": TEMPLATES[template][0], "[[SECONDS]]": f"{seconds:g}",
        "[[SLOTS]]": slot_lines, "[[RECENT]]": ", ".join(recent_hooks) or "none", "[[HOOK_EXTRA]]": extra,
        "[[WORDS]]": str(round(seconds * WORDS_PER_SECOND)), "[[FIRST_PERSON]]": first, "[[DESC]]": desc,
    }
    return re.sub(r"\[\[[A-Z_]+\]\]", lambda m: repl[m.group(0)], PROMPT)


def rules(code, p):
    """Blok aturan yang ditempel di tiap prompt video Flow, biar produk gak berubah (hasil riset Veo/Flow)."""
    noun, cap = p["noun"], p["cap"]
    clean = (f"Single continuous shot with no cuts. Audio: {p['audio']} only, with no voices and no music. "
             "No on-screen text, subtitles, captions or watermarks.")
    if code in ("a_notes", "a_scene"):
        return "No bottles, containers, packaging or products anywhere in the frame, and no people or faces. " + clean
    out = (f"The {noun} stays exactly as it appears in the first frame: same shape, same {cap}, same colors, same label "
           f"text and artwork, same size and position in the frame. Its front label faces the camera the entire time. "
           f"The {noun} never rotates, bends, morphs or changes, and it is the only {noun} in the scene.")
    if code in ("a_hand", "a_spray"):
        out += (f" One {p['hand']} with five natural fingers. The fingers hold the {noun} by its sides and back, the "
                "thumb rests on its side, and no finger covers the front label. The grip stays exactly the same from "
                "start to finish; the hand moves only slightly and slowly.")
    if code == "a_loop":
        out += " The clip ends exactly on the provided last frame."
    return f"{out} {clean}"


def image_prompt(edit, p):
    """Prompt Nano Banana buat foto 'tangan megang produk' dari aset siap-dipegang + produk referensi."""
    label = (f'the label reading "{p["label"]}" with the same font, layout and artwork' if p["label"]
             else "the same label artwork")
    edit = re.sub(r"^edit image 1\.?\s*", "", edit, flags=re.I)
    return (f"Edit image 1. {sentence(edit)} Natural anatomy: five fingers with visible knuckles and nail beds, natural "
            f"skin texture, soft contact shadows where skin touches the {p['noun']}. Fingers wrap the sides and back; "
            "the thumb rests on the side; no finger, jewelry or shadow covers the front label. Relaxed, steady grip. "
            f"Keep the {p['noun']} exactly as in image 1 and image 2: same shape, same {p['cap']}, same colors, {label}, "
            "same size. Keep the background and lighting of image 1 unchanged. Change only what is described above. "
            "Photorealistic smartphone-style photo, 9:16 vertical, no added text, stickers or watermark.")


def product_info(obj, hand_pref):
    pd = obj.get("product") if isinstance(obj.get("product"), dict) else {}
    noun = re.sub(r"^(a|an|the)\s+", "", text(pd.get("noun")).lower())[:40] or "product"
    hand = re.sub(r"^(a|an|one)\s+", "", text(obj.get("hand")), flags=re.I) or HANDS.get(hand_pref, HANDS["wanita"])
    return {
        "noun": noun, "cap": text(pd.get("cap"))[:40] or "cap", "size": text(pd.get("size_cue"))[:40] or "fits in one palm",
        "label": text(pd.get("label_text")).replace('"', "")[:60], "atomizer": pd.get("atomizer") is True,
        "look": text(obj.get("look")).rstrip(".") or "soft natural light, 85mm lens look, shallow depth of field, clean "
                                                     "commercial finish",
        "audio": text(obj.get("audio")).rstrip(".") or "soft room tone",
        "fx": text(obj.get("fx")).rstrip(".") or "soft bokeh light", "hand": hand,
    }


def build_shots(obj, slots, p, has_product):
    raw = obj.get("shots") if isinstance(obj.get("shots"), list) else []
    hooks = obj["hooks"]
    shots, t = [], 0.0
    for i, (beat, sec, cands) in enumerate(slots):
        s = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        ok = [c for c in cands if available(c, has_product, p["atomizer"])] or ["a_hero"]
        code = s.get("code") if s.get("code") in ok else ok[0]
        name, asset, mode, risk, tips = SHOTS[code]
        overlay, vo = text(s.get("text")), text(s.get("vo"))
        if i == 0 and hooks:  # slot pertama = hook pertama (pilihan hook di UI juga ganti slot ini)
            overlay, vo = hooks[0]["text"] or overlay, hooks[0]["vo"] or vo
        shot = {"beat": beat, "code": code, "name": name, "kind": "rekam" if mode == "rekam" else "ai", "risk": risk,
                "start": t, "end": t + sec, "seconds": sec, "text": overlay, "vo": vo, "asset": asset or "",
                "file": ASSET_FILE.get(asset, ""), "steps": tips, "direction": text(s.get("direction")),
                "image_prompt": "", "video_prompt": ""}
        if mode != "rekam":
            fill = {"noun": p["noun"], "cap": p["cap"], "fx": p["fx"], "hand": p["hand"], "size": p["size"]}
            motion = text(s.get("motion")) or DEFAULT_MOTION[code].format(**fill)
            shot["video_prompt"] = f"{sentence(motion)} {sentence(p['look'])} {rules(code, p)}"
            shot["steps"] = MODE_STEPS[mode].format(file=shot["file"], sec=num(sec))
            if mode == "nb":
                shot["image_prompt"] = image_prompt(text(s.get("edit")) or DEFAULT_EDIT[code].format(**fill), p)
        shots.append(shot)
        t += sec
    return shots


def normalize(obj, n, desc, template, slots, has_product, hand_pref="auto"):
    hooks = []
    for h in obj.get("hooks") if isinstance(obj.get("hooks"), list) else []:
        if isinstance(h, dict) and (text(h.get("text")) or text(h.get("vo"))):
            hooks.append({"type": text(h.get("type")) or "hook", "text": text(h.get("text")), "vo": text(h.get("vo"))})
    obj["hooks"] = hooks[:5]
    caption = text(obj.get("caption"))
    if not caption and not obj["hooks"] and not obj.get("shots"):
        raise Fail("Jawaban AI kosong, klik Bikin paket lagi.")
    best = obj.get("best_photo")
    best = best if isinstance(best, int) and 0 <= best <= n else 1
    p = product_info(obj, hand_pref)
    shots = build_shots(obj, slots, p, has_product)
    tag_list = [t for t in (h.lstrip("#").replace(" ", "") for h in lines(obj.get("hashtags"))[:5]) if t]
    tags = " ".join("#" + t for t in tag_list)
    vo_script = " ".join(s["vo"] for s in shots if s["vo"])
    seconds = shots[-1]["end"] if shots else 0
    words = len(vo_script.split())
    keyword, cover = text(obj.get("keyword")), text(obj.get("cover"))
    warnings = [] if BPOM_RE.search(desc) else [BPOM_WARNING]
    copy = [caption, cover, keyword, *(h["text"] + "\n" + h["vo"] for h in obj["hooks"]),
            *(s["text"] + "\n" + s["vo"] + "\n" + s["direction"] for s in shots)]
    hits = banned_hits("\n".join(copy)) + ["#" + t.lower() for t in tag_list if TAG_BAD.search(t)]
    if hits:
        warnings.append(f"Kata berisiko ketemu: {', '.join(hits)}. Ubah dulu sebelum posting.")
    if len(caption) > 300:
        warnings.append(f"Caption {len(caption)} karakter, kepanjangan. Pendekin jadi maksimal 300.")
    if words > seconds * (WORDS_PER_SECOND + 0.4):
        warnings.append(f"Naskah VO {words} kata buat {num(seconds)} detik, bakal keburu-buru. Pendekin dikit.")
    scene = text(obj.get("scene"))
    if not scene or hero.BANNED.search(scene):
        if scene:
            warnings.append("Scene dari AI nyebut produk/teks, jadi diganti scene netral. Boleh kamu edit.")
        scene = FALLBACK_SCENE
    if best == 0:
        warnings.append(text(obj.get("photo_problem")) or "Gak ada foto yang cukup bersih buat aset gambar.")
    if not has_product:
        warnings.append("Kamu gak pegang produknya, jadi shot tangan pakai AI. Tetap rekam muka atau suara kamu sendiri: "
                        "video yang cuma produk/tangan tanpa suara bisa kena limit konten TikTok Shop.")
    warnings += lines(obj.get("notes"))
    ai = [s for s in shots if s["kind"] == "ai"]
    return {
        "best_photo": best, "scene": scene,
        "template": {"id": template, "name": TEMPLATES[template][0], "seconds": seconds},
        "product": {k: p[k] for k in ("noun", "cap", "size", "label", "atomizer")},
        "keyword": keyword, "hooks": obj["hooks"], "shots": shots,
        "vo_script": vo_script, "vo_words": words, "caption": caption + (f"\n\n{tags}" if tags else ""),
        "cover": cover, "music": text(obj.get("music")) or "Pilih lagu santai dari Commercial Music Library.",
        "credits": {"clips": len(ai), "fast": 20 * len(ai), "lite": 10 * len(ai)},
        "checklist": CHECKLIST, "capcut": CAPCUT_STEPS, "warnings": warnings,
    }


def analyze(body):
    s = load_settings()
    if not s["key"]:
        raise Fail("Isi API key z.ai dulu di Pengaturan.")
    desc = text(body.get("description"))
    if not desc:
        raise Fail("Deskripsi seller wajib diisi (copy dari listing seller).")
    urls = body.get("photos")
    if not isinstance(urls, list) or not 1 <= len(urls) <= 3:
        raise Fail("Upload 1 sampai 3 foto.")
    photos = [decode_photo(u, i) for i, u in enumerate(urls, 1)]
    tried, has_product = bool(body.get("tried")), bool(body.get("has_product"))
    hand = body.get("hand") if body.get("hand") in HANDS else "auto"
    template = pick_template(text(body.get("style")), has_product)
    slots = resolve_slots(template, has_product)
    recent_hooks = [p.get("hook") for p in recent_packs(5) if p.get("hook")]
    job = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    out = OUT / job
    out.mkdir(parents=True)
    for i, ph in enumerate(photos, 1):
        shrink(ph, 2048).save(out / f"foto{i}.jpg", quality=92)
    prompt = build_prompt(desc, len(photos), tried, has_product, HANDS.get(hand, "auto"), template, slots, recent_hooks)
    content = [{"type": "text", "text": prompt}]
    content += [{"type": "image_url", "image_url": {"url": jpeg_url(ph)}} for ph in photos]
    obj = parse_reply(*zai_chat(s["model"], content, s["key"]))
    pack = {"job": job, "photos": len(photos), **normalize(obj, len(photos), desc, template, slots, has_product, hand)}
    meta = {"template": template, "hook": pack["hooks"][0]["type"] if pack["hooks"] else ""}
    (out / "pack.json").write_text(json.dumps({**pack, "meta": meta}, ensure_ascii=False), encoding="utf-8")
    return pack


VC_REDIST = "https://aka.ms/vs/17/release/vc_redist.x64.exe"


def rembg_tools():
    global _rembg
    if _rembg_crash:
        raise Fail(f"Pemotong background crash pas dimuat. Install {VC_REDIST}, restart PC, lalu buka Mulai.bat lagi.")
    if _rembg is None:
        try:
            from rembg import new_session, remove

            _rembg = remove, new_session("u2net")  # pertama kali download model ±170 MB
        except Exception as e:
            raise Fail(f"Pemotong background gagal dimuat ({type(e).__name__}: {e}). Cek internet. Kalau ada"
                       f" tulisan 'DLL load failed', install {VC_REDIST} lalu buka Mulai.bat lagi.") from None
    return _rembg


def probe_rembg():
    """Load rembg di proses terpisah dulu: VC++ runtime yang ketuaan bikin onnxruntime crash tanpa pesan,
    dan crash itu jangan sampai ikut matiin server (paket teks tetap harus jalan)."""
    global _rembg_crash
    rc = subprocess.run([sys.executable, "-c", "from rembg import new_session; new_session('u2net')"]).returncode
    _rembg_crash = rc not in (0, 1)  # 1 = error Python biasa (misal internet), ditangani rembg_tools


def cutout(img):
    with _rembg_lock:  # satu-satu: hemat RAM, model gak di-load dobel
        remove, session = rembg_tools()
        return hero.clean(remove(img, session=session, post_process_mask=True))


def check_key(body):
    """Panggilan teks super kecil buat mastiin key + model jalan (biayanya hampir nol)."""
    s = load_settings()
    if not s["key"]:
        raise Fail("Isi API key z.ai dulu.")
    zai_chat(s["model"], [{"type": "text", "text": "Balas satu kata: ok"}], s["key"], max_tokens=16)
    return {"ok": True}


FONTS = ("seguibl.ttf", "arialbd.ttf", "Inter-ExtraBold.otf", "DejaVuSans-Bold.ttf")  # Windows dulu, lalu Linux


def font(size):
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def make_cover(img, title):
    """Cover TikTok: hero (produk asli) + judul besar di zona aman atas (gak ketutup UI & crop grid profil)."""
    img, words = img.copy(), text(title).upper().split()[:6]
    if not words:
        return img
    draw = ImageDraw.Draw(img)
    for size in (104, 92, 80, 68):
        f, rows = font(size), [[]]
        for w in words:
            if rows[-1] and draw.textlength(" ".join(rows[-1] + [w]), font=f) > 840:
                rows.append([])
            rows[-1].append(w)
        if len(rows) <= 2:
            break
    y = 300
    for row in rows:
        line = " ".join(row)
        draw.text(((hero.W - draw.textlength(line, font=f)) / 2, y), line, font=f, fill="white",
                  stroke_width=max(4, size // 14), stroke_fill=(20, 20, 20))
        y += round(size * 1.15)
    return img


def brief(pack, files):
    """Brief teks lengkap buat dibuka di HP/PC lain (ikut di zip)."""
    t, out = pack.get("template", {}), []
    out += [f"TIKTOK PACK — {t.get('name', '')} ({num(t.get('seconds', 0))} detik)", f"Keyword: {pack.get('keyword', '')}", ""]
    out.append("HOOK (pilih satu, taruh di shot 1):")
    out += [f"- [{h['type']}] Teks: {h['text']} | Ngomong: {h['vo']}" for h in pack.get("hooks", [])]
    out += ["", "STORYBOARD:"]
    for i, s in enumerate(pack.get("shots", []), 1):
        kind = "REKAM SENDIRI" if s["kind"] == "rekam" else "AI (Google Flow)"
        out.append(f"{i}. {num(s['start'])}-{num(s['end'])} dtk | {s['beat']} | {s['name']} | {kind}")
        if s["text"]:
            out.append(f"   Teks layar: {s['text']}")
        if s["vo"]:
            out.append(f"   Ngomong: {s['vo']}")
        if s["kind"] == "rekam":
            out.append(f"   Cara rekam: {s['steps']} {s['direction']}".rstrip())
        else:
            out.append(f"   Langkah: {s['steps']}")
            if s["image_prompt"]:
                out.append(f"   Prompt gambar: {s['image_prompt']}")
            out.append(f"   Prompt video: {s['video_prompt']}")
    out += ["", f"NASKAH VO ({pack.get('vo_words', 0)} kata):", pack.get("vo_script", ""), "",
            "CAPTION:", pack.get("caption", ""), "", f"COVER: {pack.get('cover', '')}", f"MUSIK: {pack.get('music', '')}",
            "", "CARA EDIT DI CAPCUT:", *(f"- {c}" for c in CAPCUT_STEPS), "", "CHECKLIST SEBELUM POSTING:",
            *(f"- {c}" for c in CHECKLIST), "", "PERINGATAN:", *(f"- {w}" for w in pack.get("warnings", [])), "",
            "ASET:", *(f"- {f}" for f in files)]
    return "\r\n".join(out)


def make_assets(body):
    job = text(body.get("job"))
    if not JOB_RE.fullmatch(job) or not (OUT / job).is_dir():
        raise Fail("Paketnya gak ketemu. Klik Bikin paket lagi.")
    n = body.get("photo")
    photo = OUT / job / f"foto{n}.jpg"
    if not isinstance(n, int) or not 1 <= n <= 3 or not photo.is_file():
        raise Fail("Pilih foto yang ada.")
    scene = text(body.get("scene"))
    if not scene:
        raise Fail("Scene background gak boleh kosong.")
    if hero.BANNED.search(scene):
        raise Fail("Scene gak boleh nyebut produk/botol/merek/teks, biar AI gak bikin botol palsu. Tulis lingkungannya aja.")
    cut, warnings = cutout(hero.load(photo))  # cutout dulu: foto jelek gak buang saldo
    h = hashlib.sha1(scene.encode()).hexdigest()[:10]
    bg_path = OUT / job / f"bg-{h}.jpg"
    if bg_path.is_file():
        bg = hero.load(bg_path)  # scene sama = background lama, gratis
    else:
        s = load_settings()
        if not s["key"]:
            raise Fail("Isi API key z.ai dulu di Pengaturan.")
        bg = hero.zai_backdrop(scene, s["key"])
        bg.save(bg_path, quality=95)
    try:
        pack = json.loads((OUT / job / "pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pack = {}
    glossy, imgs, zoom = bool(hero.GLOSSY.search(scene)), {}, 0
    for key in ("hero", "close", "hand", "side"):
        imgs[key], scale = hero.composite(bg, cut, key, glossy=glossy)
        zoom = max(zoom, scale if key in ("hero", "close") else 0)
    if zoom > 2:  # foto seller 800-1000 px biasanya masih oke di HP; di atas 2x close-up mulai blur
        warnings.append(f"Foto produk kecil (di-zoom {num(round(zoom, 1))}x), close-up bisa kurang tajam. Pakai foto yang lebih "
                        "besar kalau ada.")
    imgs["scene"] = ImageOps.fit(bg, (hero.W, hero.H), Image.LANCZOS)
    imgs["product"] = hero.reference(cut)
    imgs["cover"] = make_cover(imgs["hero"], pack.get("cover", ""))
    assets, files = [], []
    for key, fname, label, use in ASSETS:
        name = f"{key}-{n}-{h}.jpg"
        imgs[key].save(OUT / job / name, quality=95)
        assets.append({"key": key, "file": fname, "label": label, "use": use, "url": f"/hasil/{job}/{name}"})
        files.append(fname)
    zip_name = f"paket-{n}-{h}.zip"
    with zipfile.ZipFile(OUT / job / zip_name, "w", zipfile.ZIP_DEFLATED) as z:
        for a in assets:
            z.write(OUT / job / a["url"].rsplit("/", 1)[1], a["file"])
        z.writestr("brief.txt", brief(pack, files))
    return {"assets": assets, "zip": f"/hasil/{job}/{zip_name}", "warnings": warnings}


class Handler(BaseHTTPRequestHandler):
    routes = {"/api/settings": save_settings, "/api/check": check_key, "/api/analyze": analyze, "/api/assets": make_assets}

    def log_message(self, *args):
        pass  # console Windows bisa nge-freeze kalau di-klik (mode Select); jangan banyak print

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, {"error": "Host gak dikenal."})
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/settings":
            return self.send(200, public(load_settings()))
        m = FILE_RE.fullmatch(path)
        if m and (OUT / m[1] / m[2]).is_file():
            return self.send(200, (OUT / m[1] / m[2]).read_bytes(),
                             "application/zip" if m[2].endswith(".zip") else "image/jpeg")
        self.send(404, {"error": "Gak ketemu."})

    def do_POST(self):
        if not self.host_ok():
            return self.send(403, {"error": "Host gak dikenal."})
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.send(415, {"error": "Harus JSON."})
        route = self.routes.get(self.path)
        if not route:
            return self.send(404, {"error": "Gak ketemu."})
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if not 0 <= size <= MAX_BODY:
            return self.send(413, {"error": "Data kegedean. Maksimal 15 MB per foto."})
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.send(400, {"error": "Data rusak, refresh halaman lalu coba lagi."})
        try:
            code, out = 200, route(body)
        except Fail as e:
            code, out = 400, {"error": str(e)}
        except Exception as e:
            traceback.print_exc()
            code, out = 500, {"error": f"Error tak terduga ({type(e).__name__}). Lihat jendela hitam."}
        self.send(code, out)


class Server(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"  # di Windows ini bikin dua aplikasi bisa rebutan port yang sama

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):  # tab ditutup/refresh di tengah proses
            super().handle_error(request, client_address)


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    url = f"http://{HOST}:{PORT}"
    browser = "--no-browser" not in sys.argv
    try:
        server = Server((HOST, PORT), Handler)
    except OSError:
        print(f"Aplikasi kayaknya udah jalan di jendela lain. Buka {url} di browser.")
        if browser:
            webbrowser.open(url)
        return 0
    print("Menyiapkan pemotong background (pertama kali download model sekitar 170 MB)...")
    probe_rembg()
    try:
        rembg_tools()
    except Fail as e:
        print(f"PERINGATAN: {e}\nPaket teks tetap bisa dibuat, aset gambar belum bisa.")
    print(f"\nTikTok Pack siap: {url}")
    print("Jangan tutup jendela ini selama dipakai. Tutup jendela = aplikasi berhenti.")
    print("Kalau judul jendela ada tulisan 'Select', tekan Esc.\n")
    if browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
