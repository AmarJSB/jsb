# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["requests", "truststore"]
# [tool.uv]
# exclude-newer = "2026-10-08T00:00:00Z"
# ///
"""TikTok Pack versi web lokal: info produk (link, foto, deskripsi seller) -> storyboard video TikTok + prompt Google
Flow: frame Nano Banana dari foto produk asli, lalu klip Veo dari frame itu. Plus hook, naskah VO, caption, checklist.

Windows: klik dua kali Mulai.bat. Lainnya: uv run app.py. Lalu buka http://127.0.0.1:8765
"""
import base64
import json
import os
import re
import secrets
import sys
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import truststore

truststore.inject_into_ssl()  # pakai sertifikat Windows, aman dari antivirus yang nyadap HTTPS

HERE = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8765
BASE = os.environ.get("ZAI_BASE", "https://api.z.ai/api/paas/v4")
DATA = HERE / "data"
OUT = DATA / "hasil"
SETTINGS = DATA / "settings.json"
MODELS = [  # [id, nama, keterangan, bisa baca foto]
    ["glm-5.3-flash", "GLM-5.3 Flash", "Default. Cepat, murah, bisa baca foto.", True],
    ["glm-4.6v", "GLM-4.6V", "Model khusus foto. Lebih mahal.", True],
    ["glm-4.6v-flash", "GLM-4.6V Flash", "Gratis, tapi kualitas teks lebih rendah.", True],
]
MODEL_IDS = [m[0] for m in MODELS]
VISION = {m[0] for m in MODELS if m[3]}
DURATIONS = (10, 15, 20, 30, 45, 60)
MAX_BODY = 25 * 1024 * 1024
MAX_PHOTO = 6 * 1024 * 1024  # foto dari browser udah JPEG <=1280 px, biasanya <500 KB
JOB_RE = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")
BRIEF_RE = re.compile(r"/hasil/(\d{8}-\d{6}-[0-9a-f]{4})/brief\.txt")
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
# Scene buat frame hero gak boleh nyebut produk/teks: Nano Banana bisa nambah botol palsu atau tulisan.
SCENE_BANNED = re.compile(r"\b(perfumes?|parfum|fragrances?|colognes?|flacons?|vials?|atomizers?|bottles?|botol"
                          r"|products?|produk|logos?|labels?|text|brand|merek)\b", re.I)
FALLBACK_SCENE = ("eye-level, smooth pale stone surface, softly blurred warm beige wall behind, gentle window light "
                  "from the left")
BPOM_WARNING = "Nomor BPOM (NA/NB/NC/ND/NE...) gak ada di deskripsi seller. Cek dulu di cekbpom.pom.go.id sebelum promosi."
WORDS_PER_SECOND = 2.3  # ngomong santai-semangat; lebih dari ini naskahnya keburu-buru
ERRORS = {
    "1301": "z.ai nolak konten ini (filter). Ubah deskripsi atau ganti foto.",
    "1113": "Saldo z.ai habis. Top-up dulu di z.ai.",
    "1302": "Kena rate limit z.ai. Tunggu semenit lalu coba lagi.",
    "1305": "z.ai lagi sibuk/rate limit. Tunggu semenit lalu coba lagi.",
    "429": "Kena rate limit z.ai. Tunggu semenit lalu coba lagi.",
    "401": "API key z.ai ditolak. Cek di Pengaturan.",
    "1210": "Model ini nolak permintaannya (foto/parameter gak didukung). Ganti model di Pengaturan.",
    "1211": "Model ini gak ada di akun z.ai kamu. Ganti model di Pengaturan.",
}


class Fail(Exception):
    pass


# Shot yang bisa dipakai. Rekam sendiri = bukti nyata + suara/muka Amar (TikTok Shop ngebatesin video yang cuma
# produk/tangan tanpa suara atau konteks pribadi). AI = klip Veo di Flow dari frame Nano Banana (foto produk asli).
SHOTS = {  # kode: (nama, frame awal, mode, risiko produk berubah, tips rekam)
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
    "a_hero": ("Produk, kamera bergerak pelan", "hero", "frames", "rendah", ""),
    "a_fx": ("Produk + efek aroma", "hero", "frames", "rendah", ""),
    "a_close": ("Close-up detail", "close", "frames", "rendah", ""),
    "a_hand": ("Dipegang tangan (AI)", "hand", "frames", "sedang", ""),
    "a_spray": ("Disemprot (AI)", "spray", "frames", "tinggi", ""),
    "a_notes": ("B-roll notes aroma", None, "text", "rendah", ""),
    "a_scene": ("Suasana tanpa produk", "scene", "frames", "rendah", ""),
    "a_loop": ("Penutup loop + CTA", "side", "loop", "rendah", ""),
}
# Template: (nama, butuh produk fisik, [(beat, detik, kode shot yang boleh urut prioritas, dipakai mulai durasi)]).
# Beat dipilih sesuai durasi video; detik beat yang kepilih selalu pas sama durasinya (dicek test_app). Hook selalu
# nampilin produk di frame pertama; video 15 detik ke atas selalu ada momen muka/suara Amar.
TEMPLATES = {
    "review": ("Review sensorik", False, [
        ("Hook", 2, ["r_spray", "r_pick", "a_hand"], 10),
        ("Produk + jenis wangi", 2.5, ["a_hero"], 10),
        ("Dipegang", 3, ["r_pick", "a_hand"], 45),
        ("Notes utama", 2.5, ["a_notes"], 10),
        ("Notes kedua", 2.5, ["a_fx", "a_notes"], 20),
        ("Notes dasar", 3, ["a_notes", "a_fx"], 45),
        ("Kesan pertama", 3, ["r_face"], 15),
        ("Notes favorit", 3, ["a_fx", "a_notes"], 60),
        ("Detail botol", 2, ["a_close"], 15),
        ("Ukuran & jenis", 3, ["r_size", "a_close"], 30),
        ("Kemasan", 3, ["r_pack", "a_close"], 60),
        ("Momen pakai", 3.5, ["a_scene", "r_spray"], 30),
        ("Suasana", 3, ["a_scene"], 45),
        ("Tips pakai", 3, ["r_spray", "a_spray", "a_fx"], 45),
        ("Momen kedua", 3, ["a_scene", "r_spray"], 60),
        ("Kesan lanjutan", 3.5, ["r_face"], 30),
        ("Cocok buat siapa", 2.5, ["r_face", "a_scene"], 20),
        ("Bukan buat kamu kalau...", 3, ["r_face"], 45),
        ("Rangkuman", 3, ["r_face"], 60),
        ("Produk + efek", 3, ["a_fx", "a_hero"], 60),
        ("CTA", 3, ["a_loop"], 10),
    ]),
    "alasan": ("3 Alasan", False, [
        ("Hook", 2, ["r_pick", "a_hand"], 15),
        ("Produk", 2.5, ["a_hero"], 15),
        ("Dipegang", 3, ["r_pick", "a_hand"], 30),
        ("Alasan 1: wanginya", 2.5, ["a_notes"], 15),
        ("Alasan 1: efek aroma", 2.5, ["a_fx", "a_notes"], 20),
        ("Alasan 1: notes dasar", 2.5, ["a_notes"], 30),
        ("Alasan 1: notes favorit", 3, ["a_fx", "a_notes"], 60),
        ("Alasan 2: momen pakai", 3, ["r_face", "a_scene"], 15),
        ("Alasan 2: suasana", 2.5, ["a_scene", "r_spray"], 20),
        ("Alasan 2: cerita", 3, ["r_face"], 45),
        ("Alasan 2: momen kedua", 3, ["a_scene", "r_spray"], 60),
        ("Alasan 3: ukuran & kemasan", 2, ["r_size", "r_pack", "a_close"], 15),
        ("Alasan 3: detail", 2, ["a_close"], 30),
        ("Alasan 3: kemasan", 3, ["r_pack", "a_close"], 45),
        ("Alasan 3: ukuran di tangan", 3, ["r_size", "a_hand"], 60),
        ("Bonus: tips pakai", 3, ["r_spray", "a_spray", "a_fx"], 45),
        ("Kesan", 2.5, ["r_face"], 30),
        ("Cocok buat siapa", 3, ["r_face", "a_scene"], 45),
        ("Bukan buat kamu kalau...", 3, ["r_face"], 60),
        ("Rangkuman 3 alasan", 3, ["r_face", "a_hero"], 60),
        ("Produk + efek", 3, ["a_fx", "a_hero"], 45),
        ("CTA", 3, ["a_loop"], 15),
    ]),
    "pov": ("POV momen", False, [
        ("Hook POV", 2, ["r_pick", "a_hand"], 10),
        ("Suasana momen", 2.5, ["a_scene"], 10),
        ("Persiapan", 3, ["r_face", "a_scene"], 45),
        ("Produk", 2.5, ["a_hero"], 20),
        ("Dipegang", 2, ["r_pick", "a_hand"], 30),
        ("Pakai", 2.5, ["r_spray", "a_spray", "a_fx"], 10),
        ("Tips pakai", 3, ["r_spray", "a_spray", "a_fx"], 60),
        ("Notes aroma", 2.5, ["a_notes"], 15),
        ("Notes kedua", 2.5, ["a_fx", "a_notes"], 30),
        ("Notes dasar", 3, ["a_notes"], 60),
        ("Momen 2: di jalan", 3, ["a_scene", "r_face"], 30),
        ("Momen 3: sampai tujuan", 3, ["a_scene"], 45),
        ("Detail", 2.5, ["a_close"], 20),
        ("Ukuran", 3, ["r_size", "a_close"], 45),
        ("Kemasan", 3, ["r_pack", "a_close"], 60),
        ("Kesan", 2.5, ["r_face"], 15),
        ("Kesan lanjutan", 2.5, ["r_face"], 30),
        ("Cocok buat siapa", 3, ["r_face"], 45),
        ("Bukan buat kamu kalau...", 3, ["r_face"], 60),
        ("Rangkuman", 3, ["r_face"], 60),
        ("Produk + efek", 3, ["a_fx", "a_hero"], 45),
        ("CTA", 3, ["a_loop"], 10),
    ]),
    "unboxing": ("Unboxing ASMR", True, [
        ("Hook: kemasan", 2.5, ["r_pack"], 15),
        ("Isi kotak", 3, ["r_pack"], 30),
        ("Segel & BPOM", 3, ["r_pack"], 45),
        ("Buka & ambil", 3, ["r_pick"], 15),
        ("Tutup & nozzle", 3, ["r_pick", "a_close"], 60),
        ("Ukuran di tangan", 2.5, ["r_size"], 30),
        ("Semprot pertama", 3, ["r_spray", "r_size"], 15),
        ("Tips pakai", 3, ["r_spray", "r_face"], 45),
        ("Detail", 2.5, ["a_close"], 20),
        ("Produk", 2, ["a_hero"], 30),
        ("Notes", 2.5, ["a_notes"], 20),
        ("Notes kedua", 3, ["a_fx", "a_notes"], 45),
        ("Notes dasar", 3, ["a_notes"], 60),
        ("Momen pakai", 3, ["a_scene"], 60),
        ("Kesan pertama", 3.5, ["r_face"], 15),
        ("Kesan lanjutan", 2.5, ["r_face"], 30),
        ("Cocok buat siapa", 3, ["r_face"], 45),
        ("Bukan buat kamu kalau...", 3, ["r_face"], 60),
        ("Rangkuman", 3, ["r_face"], 60),
        ("Produk + efek", 3, ["a_fx"], 45),
        ("CTA", 3, ["a_loop"], 15),
    ]),
    "sinematik": ("Sinematik", False, [
        ("Hook: reveal", 2, ["a_fx"], 10),
        ("Produk", 2.5, ["a_hero"], 10),
        ("Detail", 2, ["a_close"], 15),
        ("Notes", 2.5, ["a_notes"], 10),
        ("Notes kedua", 2.5, ["a_fx", "a_notes"], 30),
        ("Notes dasar", 3, ["a_notes"], 45),
        ("Notes favorit", 3, ["a_fx"], 60),
        ("Dipegang", 2.5, ["r_pick", "a_hand"], 20),
        ("Semprot", 3, ["r_spray", "a_spray", "a_fx"], 45),
        ("Suasana", 2.5, ["a_scene"], 20),
        ("Momen", 2.5, ["a_scene", "r_spray"], 30),
        ("Suasana kedua", 3, ["a_scene"], 45),
        ("Momen kedua", 3, ["a_scene"], 60),
        ("Detail kedua", 2.5, ["a_close"], 30),
        ("Ukuran", 3, ["r_size", "a_close"], 60),
        ("Produk sudut lain", 3, ["a_hero"], 45),
        ("Suara kamu", 3, ["r_face"], 15),
        ("Kesan", 2.5, ["r_face"], 30),
        ("Cocok buat siapa", 3, ["r_face"], 45),
        ("Bukan buat kamu kalau...", 3, ["r_face"], 60),
        ("Produk + efek", 3, ["a_fx", "a_hero"], 60),
        ("CTA", 3, ["a_loop"], 10),
    ]),
}
# Frame = gambar diam yang Amar bikin di Flow pakai Nano Banana dari foto produk asli; jadi frame awal klip Veo.
# Frame Hero dibikin pertama dan jadi patokan scene frame lain, biar semua shot kelihatan satu set.
FRAMES = {  # kunci: (nama, kegunaan)
    "hero": ("Frame Hero", "Produk berdiri di scene, label hadap kamera. Bikin ini duluan: jadi patokan frame lain."),
    "close": ("Frame Close-up", "Detail tutup & label"),
    "hand": ("Frame Dipegang", "Tangan megang produk"),
    "spray": ("Frame Siap Semprot", "Jari di nozzle, pergelangan tangan lain di samping"),
    "side": ("Frame Ruang Teks", "Produk di kanan, kiri kosong buat teks CTA (frame awal & akhir loop)"),
    "scene": ("Frame Scene Kosong", "Scene yang sama tanpa produk"),
    "cover": ("Frame Cover", "Cover TikTok: produk besar, sepertiga atas kosong buat judul"),
}
VIDEO_STEPS = {
    "frames": "Flow → Video → Frames to Video → start frame: {frame} → 9:16, 8 detik, Veo 3.1 Fast (Lite buat "
              "coba-coba) → tempel prompt video → Generate. Pakai {sec} detik terbaiknya.",
    "loop": "Flow → Video → Frames to Video → start frame DAN end frame: {frame} (gambar yang sama, biar nyambung loop) → "
            "9:16, 8 detik → tempel prompt video → Generate. Pakai {sec} detik terbaiknya.",
    "text": "Flow → Video → Text to Video → 9:16, 8 detik → tempel prompt video → Generate. Pakai {sec} detik terbaiknya.",
}
FRAME_STEPS = ("Flow → Image (Nano Banana) → 9:16 → upload {attach} → tempel prompt → Generate (2-4 variasi) → pilih "
               "yang produknya paling persis aslinya: label, tutup, warna, ukuran. Download, kasih nama \"{label}\".")
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
    "hand": "Add one realistic hand entering from the bottom edge and holding the {noun} in mid-air at chest height, "
            "like a casual smartphone product photo; the {noun} is {size}.",
    "spray": "Add one realistic hand holding the {noun} upright with the index finger resting on top of the spray "
             "nozzle, and the bare wrist of the other arm entering from the side about 10 cm from the nozzle.",
}
HANDS = {
    "wanita": "woman's right hand with warm medium-tan skin and short natural nude nails",
    "pria": "man's right hand with warm medium-tan skin and short clean nails",
}
ANATOMY = ("Natural anatomy: five fingers with visible knuckles and nail beds, natural skin texture, soft contact shadows "
           "where skin touches the {noun}. Fingers wrap the sides and back; the thumb rests on the side; no finger, "
           "jewelry or shadow covers the front label. Relaxed, steady grip.")
CHECKLIST = [
    "Nyalain label konten AI (More options → AI-generated content) sebelum posting, gak bisa diubah setelahnya.",
    "Nyalain pengungkapan konten komersial (Content disclosure) karena ini video afiliasi.",
    "Link produk di keranjang kuning = produk & varian yang sama dengan di video.",
    "Produk kelihatan di detik pertama; gak ada harga, diskon, atau screenshot halaman produk.",
    "Musik dari Commercial Music Library TikTok, suara utama = suara kamu sendiri (bukan TTS).",
    "Cek tiap frame & klip AI: label, tutup, warna, dan ukuran sama dengan foto asli; jari tangan AI harus 5. "
    "Ada yang berubah → generate ulang, jangan dipakai.",
    "Nomor BPOM dicek di cekbpom.pom.go.id: merek & nama produk cocok.",
    "Pasang Frame Cover (plus judul cover) sebagai cover.",
    "Jangan posting video yang mirip-mirip berturut-turut; ganti template & hook tiap video.",
]
CTA = ("Cek keranjang kuning", "Ukuran sama varian lengkapnya ada di keranjang kuning, ya.")  # kalau CTA dari AI gak ada
CAPCUT_STEPS = [
    "Bikin semua frame dulu di Flow (Nano Banana), mulai dari Frame Hero. Lalu bikin klip video tiap shot AI dari "
    "frame-nya (8 detik), download. Rekam shot 'rekam sendiri' pakai HP.",
    "CapCut → proyek baru 9:16 → masukin klip sesuai urutan storyboard, potong tiap klip sesuai detiknya.",
    "Shot 3,5 detik ke atas: kasih zoom pelan atau potong di tengah (jump cut) biar gambar ganti tiap 2-3 detik.",
    "Rekam voice over pakai suara kamu sendiri (naskah VO di bawah), taruh di bawah semua klip.",
    "Tambah teks layar tiap shot di area tengah-atas (jangan di 1/4 bawah & pinggir kanan: ketutup tombol TikTok).",
    "Musik pelan di bawah suara kamu; pilih lagunya nanti di TikTok (Commercial Music Library).",
    "Export 1080p, upload, pasang cover, nyalain label AI + konten komersial, pasang link produk.",
]

PROMPT = """You plan a TikTok Shop affiliate video for Amar, an Indonesian affiliate who mostly sells perfume. Goal: a professional, high-retention, compliant 9:16 video of [[SECONDS]] seconds that he assembles in CapCut from (a) shots he films himself with his phone (codes r_*) and (b) Google Flow clips (codes a_*): he first makes still frames with Nano Banana from the REAL product photo, then animates each frame with Veo. Accuracy beats hype: TikTok Shop Indonesia penalizes AI visuals that do not match the real product and videos that only show the product or hands with no voice or personal context; BPOM polices cosmetic claims.

INPUT
- The product information is between <<< and >>> at the end (seller description, or text from the product page).
- [[PHOTOS]]
- Amar has used this product: [[TRIED]]. Amar physically has the product to film: [[HAS]].
- Hand preference for AI hand shots: [[HAND]].
- Template "[[TEMPLATE]]", [[SECONDS]] seconds. Slots in order (beat, seconds, allowed shot codes in order of preference):
[[SLOTS]]
- Recently used first-hook types (put a different type first): [[RECENT]].

FACTS
- From the TEXT only: brand, product name, variant, size (ml), concentration (EDP/EDT), scent notes, the seller's own claims (skip comparisons to other brands). Never take a BPOM number from a photo.
- From the PHOTOS only appearance: colors, cap, bottle shape, material, spray atomizer or not. Without photos, take appearance only from the text and leave unknown appearance fields empty.
- Never invent a fact. Unknown -> leave it out.
- One product per pack: if the photos or the text cover several products/variants, use only the main one (the one in best_photo), take only its facts, and say so in notes.

best_photo: number of the photo Amar should upload to Flow as the product reference: a single product, fully visible including the cap, sharp, no "ORI 100%" badge, price banner, watermark, text overlay or other products. Use 0 if no photo qualifies or there are no photos, and explain why in photo_problem (Indonesian, one sentence; "" when there are no photos).

product: noun (short English noun, e.g. "perfume bottle"), cap (English, e.g. "square gold cap"), size_cue (English, from the ml if known, e.g. "about 10 cm tall", else "fits in one palm"), label_text (the large words printed on the front label exactly as seen, only if clearly legible in a photo, else ""), atomizer (true only if a spray nozzle is visible or the text says it is a spray).

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

scene (English, one sentence, max 45 words): the set for the hero frame, where Nano Banana places the real product. Start with the camera height ("eye-level" if the cap top is not visible in the photo, "slightly high angle" if it is; "eye-level" without photos), then the surface (it fills the lower third of the frame), the background and the light (key-light side and color temperature). At most 3 props, only from the family list and the listed notes. If the bottle is clear glass on a light background in the photo, keep the scene light and high-key. Never mention perfume, bottle, product, brand, label, logo or text, not even negated.
look (English, max 25 words): one consistent look for every frame and clip: light direction, palette words, lens, finish. Example: "soft window light from the left, warm ivory and blush tones, 85mm lens look, shallow depth of field, clean commercial finish".
audio (English, max 12 words): ambient sound effects only for the AI clips, e.g. "soft room tone and a gentle rustle of petals".
fx (English, max 12 words): small moving elements from the notes for ambient shots, e.g. "a few rose petals and soft bokeh".
hand (English, no leading article): the hand for AI hand shots following the hand preference, e.g. "woman's right hand with warm medium-tan skin and short natural nude nails". If the preference is "auto", choose from the product's target (unisex -> woman's hand).

hooks: exactly 5, each a different type from: analogi, pertanyaan, masalah, 3 alasan, notes, ukuran, momen, POV, jujur[[HOOK_EXTRA]]. Each has type, text (on-screen, max 6 words) and vo (spoken by Amar, max 7 words, said in 2-3 seconds). Every hook names the product or its scent and fits the product. Examples of the style: "Wanginya kayak baru keluar kamar mandi hotel." / "Tim wangi sabun atau wangi manis?" / "Naik motor siang-siang, wangimu masih ada?" / "Tiga alasan parfum ini selalu kubawa." (the last one only if Amar has used it).

shots: exactly one entry per slot, in the same order. For each:
- code: the FIRST allowed code of that slot that fits (a_spray and r_spray only if atomizer is true).
- text: on-screen text, max 6 words, that repeats the spoken keyword ("" allowed except slot 1 and the last slot).
- vo: the spoken line for this slot in Amar's own voice. Slot 1 uses the first hook's vo.
- motion (only for a_* codes, English, max 40 words): what moves and how the camera moves in the Veo clip. The product never moves by itself; use slow motion and small moves. Vary the camera move between shots that use the same frame. For a_notes describe the scent-note ingredients in macro motion with no product and no people.
- edit (only for a_hand and a_spray, English, max 30 words): how the hand holds the product in the still frame (where it enters, height, angle); do not describe the hand's look, the app adds it.
- direction (only for r_* codes, Indonesian, max 30 words): exactly what Amar films and says in that shot.
Pacing: keep exactly the given slots (never add, merge or drop one), one idea per slot. Amar adds a punch-in or jump cut inside slots of 3.5 seconds or more, so the visual still changes every 1.5-3 seconds.
Voice-over: about [[WORDS]] words in total. Flow: hook -> product name + scent family -> notes as everyday Indonesian analogies ("wangi habis mandi", "kemeja baru dicuci") -> when and where to wear it (weather, office, motorbike, kondangan) -> one honest "bukan buat kamu kalau ..." line -> CTA. Say the main keyword in the first 5 seconds. Short videos keep only the strongest points.
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
- Never use: 100%, ampuh, dijamin, pasti, paling/terbaik/no.1/juara/terlaris, feromon/pheromone, attraction or seduction claims in any wording ("bikin orang tertarik/nempel", "dilirik", "menggoda", "memikat", "lawan jenis", "klepek"), 24 jam/48 jam/seharian, "ori/original/asli/murni" claims, safety claims ("aman", "tanpa efek samping", "tidak berbahaya", "hipoalergenik", "aman buat bumil/kulit sensitif"), medical claims (obat, menyembuhkan), doctors or pharmacists, other brands' or perfumes' names and comparisons ("inspired by", "terinspirasi", "mirip", "dupe", "KW", "replika", "alternatif X"), prices or discounts (even if the text shows them), "link di bio", DM, WA, other marketplaces, fake testimonials, fake reactions, fake stories, fake urgency ("stok tinggal 3!", "terbatas").

OUTPUT: reply with ONLY one JSON object, no markdown, exactly these keys:
{"best_photo": 1, "photo_problem": "", "product": {"noun": "", "cap": "", "size_cue": "", "label_text": "", "atomizer": false}, "family": "", "keyword": "", "scene": "", "look": "", "audio": "", "fx": "", "hand": "", "hooks": [{"type": "", "text": "", "vo": ""}], "shots": [{"code": "", "text": "", "vo": "", "motion": "", "edit": "", "direction": ""}], "caption": "", "hashtags": ["#..."], "cover": "", "music": "", "notes": []}

<<<
[[DESC]]
>>>"""


def text(v):
    """String bersih dari jawaban AI: list digabung, None/bool/dict jadi kosong (repr Python jangan sampai bocor)."""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return ", ".join(lines(v))
    return "" if v is None or isinstance(v, (bool, dict)) else str(v).strip()


def lines(v):
    items = v if isinstance(v, list) else text(v).splitlines()
    return [t for t in (text(x) for x in items) if t]


def template_min(t):
    """Durasi terpendek yang masuk akal buat template ini (beat paling awal yang ikut)."""
    return min(b[3] for b in TEMPLATES[t][2])


def load_settings():
    try:
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    s = s if isinstance(s, dict) else {}
    return {"key": text(s.get("key")), "model": s.get("model") if s.get("model") in MODEL_IDS else MODEL_IDS[0]}


def public(s):
    return {"has_key": bool(s["key"]), "key_hint": "..." + s["key"][-4:] if s["key"] else "",
            "model": s["model"], "models": MODELS, "durations": DURATIONS,
            "templates": [[k, t[0], t[1], template_min(k)] for k, t in TEMPLATES.items()]}


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


def check_photo(url, i):
    """Foto dari browser: data URL JPEG (udah dikecilin ke <=1280 px di browser). Balikin data URL yang sama."""
    head, _, b64 = str(url).partition(",")
    try:
        data = base64.b64decode(b64, validate=True)
    except ValueError:
        data = b""
    if head != "data:image/jpeg;base64" or not data.startswith(b"\xff\xd8\xff") or len(data) > MAX_PHOTO:
        raise Fail(f"Foto ke-{i} gak bisa dibuka. Pakai JPG/PNG/WebP (HEIC gak bisa).")
    return f"{head},{b64}"


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
        raise Fail(ERRORS.get(code) or ERRORS.get(str(r.status_code))
                   or f"z.ai error {code}: {err.get('message') or r.text[:200]}")
    return text((choice.get("message") or {}).get("content")), choice.get("finish_reason")


def parse_reply(reply, finish=None, keys=("shots", "hooks", "caption")):
    s = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S)
    s = s.replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "")
    dec = json.JSONDecoder(strict=False)  # AI kadang nulis enter mentah di dalam string
    for m in re.finditer("{", s):  # objek jawaban pertama; kalimat pembuka & jawaban yang diulang di akhir diabaikan
        try:
            obj = dec.raw_decode(s, m.start())[0]
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.keys() & set(keys):
            return obj
    raise Fail("Jawaban AI kepotong, klik lagi." if finish == "length"
               else ERRORS["1301"] if finish == "sensitive"
               else "Jawaban AI gak kebaca, klik lagi.")


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


def resolve_slots(template, has_product, seconds):
    """Beat yang ikut buat durasi ini + kode shot yang boleh (atomizer baru ketahuan dari jawaban AI)."""
    return [(beat, sec, [c for c in cands if available(c, has_product)])
            for beat, sec, cands, start in TEMPLATES[template][2] if seconds >= start]


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


def pick_template(style, has_product, seconds):
    """Template pilihan Amar, atau yang paling lama gak dipakai (biar video gak mirip-mirip)."""
    allowed = [k for k, t in TEMPLATES.items() if (has_product or not t[1]) and seconds >= template_min(k)]
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
    photos = (f"{n} product photo(s) are attached in order: photo 1 is the first image." if n
              else "No photos are attached: take every fact, including appearance, from the text only.")
    repl = {
        "[[PHOTOS]]": photos, "[[TRIED]]": "yes" if tried else "no", "[[HAS]]": "yes" if has_product else "no",
        "[[HAND]]": hand, "[[TEMPLATE]]": TEMPLATES[template][0], "[[SECONDS]]": f"{seconds:g}",
        "[[SLOTS]]": slot_lines, "[[RECENT]]": ", ".join(recent_hooks) or "none", "[[HOOK_EXTRA]]": extra,
        "[[WORDS]]": str(round(seconds * WORDS_PER_SECOND)), "[[FIRST_PERSON]]": first, "[[DESC]]": desc,
    }
    return re.sub(r"\[\[[A-Z_]+\]\]", lambda m: repl[m.group(0)], PROMPT)


def rules(code, p):
    """Blok aturan yang ditempel di tiap prompt video Veo, biar produk gak berubah (hasil riset Veo/Flow)."""
    noun, cap = p["noun"], p["cap"]
    clean = (f"Single continuous shot with no cuts. Audio: {p['audio']} only, with no voices and no music. "
             "No on-screen text, subtitles, captions or watermarks.")
    if code in ("a_notes", "a_scene"):
        return "No bottles, containers, packaging or products anywhere in the frame, and no people or faces. " + clean
    out = (f"The {noun} stays exactly as it appears in the first frame: same shape, same {cap}, same colors, same label "
           f"text and artwork, same size and position in the frame. Its front label faces the camera the entire time. "
           f"The {noun} never rotates, bends, morphs or changes, and it is the only {noun} in the scene.")
    if code in ("a_hand", "a_spray"):
        out += (f" Exactly one hand holds the {noun}: a {p['hand']}, with five natural fingers. The fingers hold it "
                "by its sides and back, the thumb rests on its side, and no finger covers the front label. The grip stays "
                "exactly the same from start to finish; the hand moves only slightly and slowly.")
    if code == "a_loop":
        out += " The clip ends exactly on the provided last frame."
    return f"{out} {clean}"


def product_lock(p):
    label = (f'the label reading "{p["label"]}" with the same font, layout and artwork' if p["label"]
             else "the same label artwork")
    return (f"Keep the {p['noun']} exactly as in image 1: same shape and proportions, same {p['cap']}, same colors, "
            f"{label}, same material, realistic size ({p['size']}). Exactly one {p['noun']}. No added text, stickers, "
            "logos or watermark. Photorealistic, 9:16 vertical.")


def frame_prompt(key, p, scene, edit=""):
    """Prompt Nano Banana per frame. Image 1 = foto produk asli; image 2 = Frame Hero (patokan scene)."""
    noun, same = p["noun"], "in the same set, surface, props, camera angle and light as image 2"
    two = "Image 1 is the real product photo; image 2 is the hero photo."
    edit = re.sub(r"^edit image \d\.?\s*", "", text(edit), flags=re.I)
    if key == "scene":  # cuma butuh Frame Hero
        return (f"Image 1 is the hero photo. Recreate the exact same set, surface, props, camera angle and light, but "
                f"with no {noun}, no products, no bottles, no people and no text: only the empty set. "
                "Photorealistic, 9:16 vertical.")
    body = {
        "hero": f"Image 1 is the real product photo. Create a photorealistic product photo: the {noun} from image 1 "
                f"stands upright on the surface of this set: {scene}. It sits in the lower middle of the frame with its "
                f"front label facing the camera and a soft, natural contact shadow under its base. {sentence(p['look'])}",
        "close": f"{two} Create a macro close-up of the same {noun} {same}: the {p['cap']} and the front label fill most "
                 "of the frame, the label sharp and readable, the background softly blurred.",
        "hand": f"{two} Create a photo {same}. {sentence(edit)} The hand holding the {noun} is one {p['hand']}. "
                + ANATOMY.format(noun=noun),
        "spray": f"{two} Create a photo {same}. {sentence(edit)} The hand holding the {noun} is one {p['hand']}. "
                 + ANATOMY.format(noun=noun),
        "side": f"{two} Recreate image 2 with the same camera angle, set and light, but place the {noun} in the right "
                "third of the frame and leave calm, empty space on the left half for text.",
        "cover": f"{two} Create a TikTok cover photo {same}: the {noun} large in the lower two thirds with its front "
                 "label facing the camera, and a clean, empty area in the top third for a title.",
    }[key]
    return f"{body} {product_lock(p)}"


def product_info(obj, hand_pref):
    pd = obj.get("product") if isinstance(obj.get("product"), dict) else {}
    noun = re.sub(r"^(a|an|the)\s+", "", text(pd.get("noun")).lower())[:40] or "product"
    # pilihan tangan Amar menang; "auto" = tangan pilihan AI
    hand = HANDS.get(hand_pref) or re.sub(r"^(a|an|one)\s+", "", text(obj.get("hand")), flags=re.I) or HANDS["wanita"]
    return {
        "noun": noun, "cap": text(pd.get("cap"))[:40] or "cap", "size": text(pd.get("size_cue"))[:40] or "fits in one palm",
        "label": text(pd.get("label_text")).replace('"', "")[:60], "atomizer": pd.get("atomizer") is True,
        "look": text(obj.get("look")).rstrip(".") or "soft natural light, 85mm lens look, shallow depth of field, clean "
                                                     "commercial finish",
        "audio": text(obj.get("audio")).rstrip(".") or "soft room tone",
        "fx": text(obj.get("fx")).rstrip(".") or "soft bokeh light", "hand": hand,
    }


def build_shots(obj, slots, p, has_product):
    raw = [s if isinstance(s, dict) else {} for s in obj["shots"]] if isinstance(obj.get("shots"), list) else []
    if raw and len(raw) != len(slots):  # AI salah hitung slot: isi urut, shot CTA-nya tetap di slot terakhir
        cta = next((s for s in reversed(raw) if "keranjang" in text(s.get("vo")).lower()), {})
        body = [s for s in raw if s is not cta][:len(slots) - 1]
        raw = body + [{}] * (len(slots) - 1 - len(body)) + [cta]
    hooks = obj["hooks"]
    shots, t = [], 0.0
    for i, (beat, sec, cands) in enumerate(slots):
        s = raw[i] if i < len(raw) else {}
        ok = [c for c in cands if available(c, has_product, p["atomizer"])] or ["a_hero"]
        code = s.get("code") if s.get("code") in ok else ok[0]
        if code != s.get("code"):  # kode diganti: motion/edit/direction AI itu buat shot lain, pakai default
            s = {"text": s.get("text"), "vo": s.get("vo")}
        name, frame, mode, risk, tips = SHOTS[code]
        overlay, vo = text(s.get("text")), text(s.get("vo"))
        if i == 0 and hooks:  # slot pertama = hook pertama (pilihan hook di UI juga ganti slot ini)
            overlay, vo = hooks[0]["text"] or overlay, hooks[0]["vo"] or vo
        shot = {"beat": beat, "code": code, "name": name, "kind": "rekam" if mode == "rekam" else "ai", "risk": risk,
                "start": t, "end": t + sec, "seconds": sec, "text": overlay, "vo": vo, "frame": frame or "",
                "frame_label": FRAMES[frame][0] if frame else "", "steps": tips, "direction": text(s.get("direction")),
                "edit": text(s.get("edit")), "video_prompt": ""}
        if mode != "rekam":
            fill = {"noun": p["noun"], "cap": p["cap"], "fx": p["fx"]}
            motion = text(s.get("motion")) or DEFAULT_MOTION[code].format(**fill)
            shot["video_prompt"] = f"{sentence(motion)} {sentence(p['look'])} {rules(code, p)}"
            shot["steps"] = VIDEO_STEPS[mode].format(frame=shot["frame_label"], sec=num(sec))
        shots.append(shot)
        t += sec
    if shots and "keranjang" not in shots[-1]["vo"].lower():  # CTA wajib ngarah ke keranjang kuning
        shots[-1]["text"], shots[-1]["vo"] = shots[-1]["text"] or CTA[0], CTA[1]
    return shots


def build_frames(shots, p, scene):
    """Frame yang dipakai storyboard (Hero selalu pertama, Cover selalu terakhir) + prompt & langkah Nano Banana."""
    used = [s["frame"] for s in shots if s["frame"]]
    keys = [k for k in FRAMES if k in ("hero", "cover") or k in used]
    edits = {s["frame"]: s["edit"] for s in reversed(shots) if s["frame"] in DEFAULT_EDIT and s["edit"]}
    frames = []
    for k in keys:
        attach = ("foto produk asli" if k == "hero" else "Frame Hero" if k == "scene"
                  else "foto produk asli (gambar 1) + Frame Hero (gambar 2)")
        fill = {"noun": p["noun"], "size": p["size"]}
        edit = edits.get(k) or DEFAULT_EDIT.get(k, "").format(**fill)
        frames.append({"key": k, "label": FRAMES[k][0], "use": FRAMES[k][1], "attach": attach,
                       "steps": FRAME_STEPS.format(attach=attach, label=FRAMES[k][0]),
                       "prompt": frame_prompt(k, p, scene, edit)})
    return frames


def normalize(obj, n, desc, template, slots, has_product, hand_pref="auto"):
    hooks = []
    for h in obj.get("hooks") if isinstance(obj.get("hooks"), list) else []:
        h = {"text": h, "vo": h} if isinstance(h, str) else h
        if isinstance(h, dict) and (text(h.get("text")) or text(h.get("vo"))):
            hooks.append({"type": text(h.get("type")) or "hook", "text": text(h.get("text")), "vo": text(h.get("vo"))})
    obj["hooks"] = hooks[:5]
    caption = "\n".join(lines(obj.get("caption"))) if isinstance(obj.get("caption"), list) else text(obj.get("caption"))
    raw = obj.get("shots") if isinstance(obj.get("shots"), list) else []
    if not obj["hooks"] and not any(text(s.get("vo")) for s in raw if isinstance(s, dict)):
        raise Fail("Jawaban AI kosong, klik Bikin paket lagi.")
    try:
        best = 1 if isinstance(obj.get("best_photo"), bool) else int(obj.get("best_photo"))
    except (TypeError, ValueError):
        best = 1 if n else 0
    best = best if 0 <= best <= n else (1 if n else 0)
    p = product_info(obj, hand_pref)
    shots = build_shots(obj, slots, p, has_product)
    parts = (re.sub(r"\W", "", x) for item in lines(obj.get("hashtags")) for x in re.split(r"[#,]", item))
    tag_list = list(dict.fromkeys(t for t in parts if t))[:5]  # "#a #b" dalam satu string juga dipecah
    tags = " ".join("#" + t for t in tag_list)
    vo_script = " ".join(s["vo"] for s in shots if s["vo"])
    seconds = shots[-1]["end"] if shots else 0
    words = len(vo_script.split())
    keyword, cover = text(obj.get("keyword")), text(obj.get("cover"))
    warnings = [] if BPOM_RE.search(desc) else [BPOM_WARNING]
    if raw and len(raw) != len(slots):
        warnings.append(f"AI ngasih {len(raw)} shot buat {len(slots)} slot, jadi urutannya disesuaikan. Cek teks & VO tiap shot.")
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
    if not scene or SCENE_BANNED.search(scene):
        if scene:
            warnings.append("Scene dari AI nyebut produk/teks, jadi diganti scene netral.")
        scene = FALLBACK_SCENE
    if n and best == 0:
        warnings.append(text(obj.get("photo_problem")) or "Gak ada foto yang cukup bersih buat referensi produk di Flow. "
                                                          "Cari foto lain yang produknya utuh, tanpa watermark.")
    if not has_product:
        warnings.append("Kamu gak pegang produknya, jadi shot tangan pakai AI. Tetap rekam muka atau suara kamu sendiri: "
                        "video yang cuma produk/tangan tanpa suara bisa kena limit konten TikTok Shop.")
    warnings += lines(obj.get("notes"))
    ai = [s for s in shots if s["kind"] == "ai"]
    frames = build_frames(shots, p, scene)
    return {
        "best_photo": best, "scene": scene,
        "template": {"id": template, "name": TEMPLATES[template][0], "seconds": seconds},
        "product": {k: p[k] for k in ("noun", "cap", "size", "label", "atomizer")},
        "keyword": keyword, "hooks": obj["hooks"], "frames": frames, "shots": shots,
        "vo_script": vo_script, "vo_words": words, "caption": caption + (f"\n\n{tags}" if tags else ""),
        "cover": cover, "music": text(obj.get("music")) or "Pilih lagu santai dari Commercial Music Library.",
        "credits": {"frames": len(frames), "clips": len(ai), "fast": 20 * len(ai), "lite": 10 * len(ai)},
        "checklist": CHECKLIST, "capcut": CAPCUT_STEPS, "warnings": warnings,
    }


def analyze(body):
    s = load_settings()
    if not s["key"]:
        raise Fail("Isi API key z.ai dulu di Pengaturan.")
    desc = text(body.get("description"))
    if not desc:
        raise Fail("Deskripsi produk wajib diisi (tempel dari listing seller, atau ambil dari link).")
    urls = body.get("photos") or []
    if not isinstance(urls, list) or len(urls) > 3:
        raise Fail("Maksimal 3 foto.")
    photos = [check_photo(u, i) for i, u in enumerate(urls, 1)]
    warnings = []
    if photos and s["model"] not in VISION:
        warnings.append("Model yang dipilih gak bisa baca foto, jadi storyboard dibikin dari teks aja.")
        photos = []
    seconds = body.get("duration") if body.get("duration") in DURATIONS else 20
    tried, has_product = bool(body.get("tried")), bool(body.get("has_product"))
    hand = body.get("hand") if body.get("hand") in HANDS else "auto"
    template = pick_template(text(body.get("style")), has_product, seconds)
    slots = resolve_slots(template, has_product, seconds)
    recent_hooks = [p.get("hook") for p in recent_packs(5) if p.get("hook")]
    prompt = build_prompt(desc, len(photos), tried, has_product, HANDS.get(hand, "auto"), template, slots, recent_hooks)
    content = [{"type": "text", "text": prompt}] + [{"type": "image_url", "image_url": {"url": u}} for u in photos]
    obj = parse_reply(*zai_chat(s["model"], content, s["key"]))
    job = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    pack = {"job": job, "photos": len(photos), **normalize(obj, len(photos), desc, template, slots, has_product, hand)}
    pack["warnings"] = warnings + pack["warnings"]
    pack["source"] = text(body.get("source"))[:500]
    meta = {"template": template, "hook": pack["hooks"][0]["type"] if pack["hooks"] else ""}
    (OUT / job).mkdir(parents=True)
    (OUT / job / "pack.json").write_text(json.dumps({**pack, "meta": meta}, ensure_ascii=False), encoding="utf-8")
    return pack


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 "
      "Safari/537.36")
PAGE_MAX = 3 * 1024 * 1024
IMG_MAX = 8 * 1024 * 1024
TAG_RE = re.compile(r"<(script|style|noscript|svg|template)\b.*?</\1\s*>|<!--.*?-->", re.S | re.I)
EXTRACT_PROMPT = """Extract the product information from this e-commerce product page data (TikTok Shop / Tokopedia, Indonesia). Copy facts exactly as written on the page; never invent anything; leave a field "" when it is not on the page.
Reply with ONLY one JSON object, no markdown:
{"is_product_page": true, "name": "", "brand": "", "variant": "", "size": "", "price": "", "original_price": "", "discount": "", "shop": "", "notes": "", "bpom": "", "description": "", "images": []}
- is_product_page: false if this is a login wall, captcha, error page, or not a single product.
- price: the current selling price as shown (e.g. "Rp179.000"); original_price: the strikethrough price if shown; discount: as shown (e.g. "28%").
- notes: the scent notes if listed (top/middle/base). bpom: the BPOM number if it is printed in the text (e.g. NA18201200123).
- description: the seller's own product description, max 1500 characters, keep its line breaks; drop shipping info, reviews, other products and page navigation.
- images: up to 3 URLs from the IMAGE CANDIDATES that show this product (main photo first), copied exactly.

PAGE DATA:
"""


def page_from_html(url, html):
    """Data mentah halaman produk dari HTML: judul, meta, JSON-LD, teks yang kelihatan, URL gambar."""
    import html as htmllib

    meta = {}
    for m in re.finditer(r"<meta\b[^>]*>", html, re.I):
        tag = m.group(0)
        k = re.search(r'(?:property|name)\s*=\s*["\']([^"\']+)', tag, re.I)
        v = re.search(r'content\s*=\s*["\']([^"\']*)', tag, re.I)
        if k and v and re.match(r"(og:|product:|twitter:|description$)", k[1], re.I):
            meta[k[1].lower()] = htmllib.unescape(v[1])[:1000]
    ld = "\n".join(re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I))[:20000]
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    images = re.findall(r'<img\b[^>]*?\ssrc\s*=\s*["\'](https?://[^"\']+)', html, re.I)
    body = htmllib.unescape(re.sub(r"<[^>]+>", " ", TAG_RE.sub(" ", html)))
    return {"url": url, "title": htmllib.unescape(title[1].strip()) if title else "", "meta": meta, "ld": ld,
            "text": re.sub(r"\s+", " ", body).strip()[:15000], "images": images}


def fetch_page(url):
    import requests

    if not re.fullmatch(r"https?://\S+", url):
        raise Fail("Link-nya harus diawali https:// (copy dari tombol Bagikan di TikTok Shop / Tokopedia).")
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "id-ID,id;q=0.9,en;q=0.8"},
                         timeout=(10, 25))
    except requests.RequestException as e:
        raise Fail(f"Halaman produk gak bisa dibuka ({type(e).__name__}). Cek link/internet, atau pakai tombol "
                   "bookmark \"Kirim ke TikTok Pack\".") from None
    if r.status_code != 200 or "html" not in r.headers.get("Content-Type", ""):
        raise Fail(f"Halaman produk nolak dibaca otomatis (HTTP {r.status_code}). Pakai tombol bookmark "
                   "\"Kirim ke TikTok Pack\" di halaman produknya.")
    return page_from_html(r.url, r.content[:PAGE_MAX].decode("utf-8", "replace"))


def fetch_images(urls, referer, limit=3):
    """Download gambar produk (https, <=8 MB). Balikin data URL apa adanya; browser yang ngecilin & jadiin JPEG."""
    import requests

    out = []
    for u in dict.fromkeys(urls):
        if len(out) >= limit:
            break
        if not re.fullmatch(r"https?://\S+", u):
            continue
        try:
            r = requests.get(u, headers={"User-Agent": UA, "Referer": referer}, timeout=(5, 15))
        except requests.RequestException:
            continue
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if r.status_code == 200 and ctype in ("image/jpeg", "image/png", "image/webp") and len(r.content) <= IMG_MAX:
            out.append(f"data:{ctype};base64,{base64.b64encode(r.content).decode()}")
    return out


def import_product(body):
    """Info produk dari link (diambil server) atau dari bookmark di browser Amar (halaman udah kebuka di sana)."""
    s = load_settings()
    if not s["key"]:
        raise Fail("Isi API key z.ai dulu di Pengaturan.")
    page = body.get("page")
    if isinstance(page, dict):  # dari bookmark: teks halaman udah dikirim browser
        page = {"url": text(page.get("url"))[:2000], "title": text(page.get("title"))[:500],
                "meta": page.get("meta") if isinstance(page.get("meta"), dict) else {},
                "ld": text(page.get("ld"))[:20000], "text": text(page.get("text"))[:15000],
                "images": [u for u in page.get("images") or [] if isinstance(u, str)][:20]}
    else:
        page = fetch_page(text(body.get("url")))
    meta_images = [v for k, v in page["meta"].items() if k in ("og:image", "twitter:image")]
    candidates = list(dict.fromkeys(meta_images + page["images"]))[:20]
    data = (f"URL: {page['url']}\nTITLE: {page['title']}\nMETA: {json.dumps(page['meta'], ensure_ascii=False)[:3000]}\n"
            f"JSON-LD: {page['ld'][:8000]}\nIMAGE CANDIDATES:\n" + "\n".join(candidates) +
            f"\nVISIBLE TEXT:\n{page['text']}")
    reply, finish = zai_chat(s["model"], [{"type": "text", "text": EXTRACT_PROMPT + data}], s["key"], max_tokens=4096)
    info = parse_reply(reply, finish, keys=("is_product_page", "name"))
    if info.get("is_product_page") is False or not text(info.get("name")):
        raise Fail("Halaman itu kayaknya bukan halaman produk (atau minta login/captcha). Buka produknya di browser, "
                   "lalu klik bookmark \"Kirim ke TikTok Pack\", atau tempel deskripsinya manual.")
    fields = {k: text(info.get(k))[:2000] for k in ("name", "brand", "variant", "size", "price", "original_price",
                                                      "discount", "shop", "notes", "bpom", "description")}
    picked = [u for u in lines(info.get("images")) if u in candidates] or candidates
    return {**fields, "url": page["url"], "images": fetch_images(picked, page["url"])}


def check_key(body):
    """Panggilan teks super kecil buat mastiin key + model jalan (biayanya hampir nol)."""
    s = load_settings()
    if not s["key"]:
        raise Fail("Isi API key z.ai dulu.")
    zai_chat(s["model"], [{"type": "text", "text": "Balas satu kata: ok"}], s["key"], max_tokens=16)
    return {"ok": True}


def load_pack(job):
    try:
        return json.loads((OUT / job / "pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def with_hook(pack, i):
    """Pack dengan hook ke-i di shot 1, sama kayak pilihan hook di UI."""
    hooks, shots = pack.get("hooks") or [], pack.get("shots") or []
    if not 0 <= i < len(hooks) or not shots:
        return pack
    first = {**shots[0], "text": hooks[i]["text"] or shots[0]["text"], "vo": hooks[i]["vo"] or shots[0]["vo"]}
    shots = [first, *shots[1:]]
    vo = " ".join(s["vo"] for s in shots if s["vo"])
    return {**pack, "shots": shots, "vo_script": vo, "vo_words": len(vo.split())}


def brief(pack):
    """Brief teks lengkap buat dibuka di HP/PC lain: frame dulu, lalu storyboard."""
    t, out = pack.get("template", {}), []
    out += [f"TIKTOK PACK — {t.get('name', '')} ({num(t.get('seconds', 0))} detik)", f"Keyword: {pack.get('keyword', '')}"]
    if pack.get("source"):
        out.append(f"Produk: {pack['source']}")
    if pack.get("best_photo"):
        out.append(f"Foto produk buat Flow: foto ke-{pack['best_photo']}")
    out += ["", "HOOK (pilih satu, taruh di shot 1):"]
    out += [f"- [{h['type']}] Teks: {h['text']} | Ngomong: {h['vo']}" for h in pack.get("hooks", [])]
    out += ["", "LANGKAH 1 - FRAME DI FLOW (Nano Banana):"]
    for f in pack.get("frames", []):
        out += [f"* {f['label']}: {f['use']}", f"   Langkah: {f['steps']}", f"   Prompt: {f['prompt']}"]
    out += ["", "LANGKAH 2 - STORYBOARD:"]
    for i, s in enumerate(pack.get("shots", []), 1):
        kind = "REKAM SENDIRI" if s["kind"] == "rekam" else f"AI (Veo) dari {s['frame_label']}" if s["frame"] else "AI (Veo)"
        out.append(f"{i}. {num(s['start'])}-{num(s['end'])} dtk | {s['beat']} | {s['name']} | {kind}")
        if s["text"]:
            out.append(f"   Teks layar: {s['text']}")
        if s["vo"]:
            out.append(f"   Ngomong: {s['vo']}")
        if s["kind"] == "rekam":
            if s["direction"]:
                out.append(f"   Rekam: {s['direction']}")
            out.append(f"   Tips: {s['steps']}")
        else:
            out += [f"   Langkah: {s['steps']}", f"   Prompt video: {s['video_prompt']}"]
    out += ["", f"NASKAH VO ({pack.get('vo_words', 0)} kata):", pack.get("vo_script", ""), "",
            "CAPTION:", pack.get("caption", ""), "", f"COVER: {pack.get('cover', '')}", f"MUSIK: {pack.get('music', '')}",
            "", "CARA EDIT DI CAPCUT:", *(f"- {c}" for c in CAPCUT_STEPS), "", "CHECKLIST SEBELUM POSTING:",
            *(f"- {c}" for c in CHECKLIST), "", "PERINGATAN:", *(f"- {w}" for w in pack.get("warnings", []))]
    return "\n".join(out).replace("\r\n", "\n").replace("\n", "\r\n")  # caption juga CRLF, biar Notepad rapi


class Handler(BaseHTTPRequestHandler):
    routes = {"/api/settings": save_settings, "/api/check": check_key, "/api/analyze": analyze,
              "/api/import": import_product}

    def log_message(self, *args):
        pass  # console Windows bisa nge-freeze kalau di-klik (mode Select); jangan banyak print

    def send(self, code, body, ctype="application/json; charset=utf-8", headers=()):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, {"error": "Host gak dikenal."})
        path, _, query = self.path.partition("?")
        if path == "/":
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/settings":
            return self.send(200, public(load_settings()))
        m = BRIEF_RE.fullmatch(path)
        pack = load_pack(m[1]) if m else {}
        if pack:
            hook = parse_qs(query).get("hook", ["0"])[0]
            data = brief(with_hook(pack, int(hook) if hook.isdigit() else 0)).encode("utf-8-sig")  # BOM: Notepad lama
            return self.send(200, data, "text/plain; charset=utf-8",
                             [("Content-Disposition", f'attachment; filename="tiktok-pack-{m[1]}.txt"')])
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
            return self.send(413, {"error": "Data kegedean."})
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
