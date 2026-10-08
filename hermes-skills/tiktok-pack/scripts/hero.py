# /// script
# requires-python = ">=3.11"
# dependencies = ["rembg[cpu]>=2.0.60,<3", "pillow", "requests"]
# ///
"""Hero frame 9:16 buat TikTok: produk asli (dipotong dari foto seller) di atas background AI z.ai.

Pakai: uv run hero.py FOTO (--scene "SCENE BAHASA INGGRIS" | --backdrop FILE)
Output: baris "WARN: ..." lalu baris terakhir "HERO: <path>". Gagal: "ERROR: ..." dan exit 1.
"""
import argparse
import os
import re
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

W, H = 1080, 1920
# tinggi maks, lebar maks, posisi dasar, pusat x (relatif kanvas). Aman dari UI TikTok di bawah & kanan.
LAYOUTS = {
    "hero": (0.42, 0.70, 0.72, 0.50),   # berdiri di meja, tengah
    "close": (0.80, 0.80, 0.90, 0.50),  # close-up, produk hampir penuh layar
    "side": (0.46, 0.50, 0.73, 0.64),   # agak ke kanan, ruang teks di kiri (penutup/CTA)
    "hand": (0.36, 0.55, 0.64, 0.50),   # melayang di depan background blur, siap "dipegang" di Nano Banana
}
API = "https://api.z.ai/api/paas/v4/images/generations"
KEY_VARS = ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY")
SUFFIX = (
    ", 85mm lens look, the surface fills the lower third of the frame, the empty spot on the surface in the"
    " foreground center in sharp focus, background softly defocused, photorealistic, vertical composition,"
    " no products, no bottles, no containers, no people,"
    " no hands, no text, no letters, no logos, no watermark"
)
GLOSSY = re.compile(r"\b(glass|acrylic|mirror|marble|water|glossy|polished|lacquer|reflective)\b", re.I)
BANNED = re.compile(r"\b(perfumes?|parfum|fragrances?|colognes?|flacons?|vials?|atomizers?|bottles?|botol|products?"
                    r"|produk|logos?|labels?|text|brand|merek)\b", re.I)
ERRORS = {
    "1301": "z.ai nolak scene ini (filter konten). Ganti kata-kata scene-nya.",
    "1113": "Saldo z.ai habis. Top-up dulu di z.ai.",
    "1302": "Kena rate limit z.ai. Tunggu semenit lalu coba lagi.",
    "1305": "z.ai lagi sibuk/rate limit. Tunggu semenit lalu coba lagi.",
    "429": "Kena rate limit z.ai. Tunggu semenit lalu coba lagi.",
    "401": "API key z.ai ditolak. Cek GLM_API_KEY di file .env Hermes.",
}


class Fail(Exception):
    pass


def hermes_home():
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"]).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "hermes"
    return Path.home() / ".hermes"


def read_env(path):
    out = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = (s.strip() for s in line.split("=", 1))
        if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
            v = v[1:-1]
        else:
            v = v.split(" #", 1)[0].strip()
        out[k] = v
    return out


def api_key():
    env_file = hermes_home() / ".env"
    file_vars = read_env(env_file)
    for k in KEY_VARS:
        if os.environ.get(k) or file_vars.get(k):
            return os.environ.get(k) or file_vars[k]
    raise Fail(f"API key z.ai gak ketemu. Isi GLM_API_KEY di {env_file}")


def load(path):
    try:
        with Image.open(path) as im:
            return ImageOps.exif_transpose(im).convert("RGB")
    except OSError:
        raise Fail(f"Gambar gak bisa dibuka: {Path(path).name}. Kirim sebagai foto biasa (JPG/PNG).") from None


def clean(rgba):
    """Rapikan alpha hasil rembg (buang pinggiran putih), crop ke produk."""
    alpha = rgba.getchannel("A").filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1))
    rgba.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 32 else 0).getbbox()
    if not box or (box[2] - box[0]) * (box[3] - box[1]) < 0.02 * rgba.width * rgba.height:
        raise Fail("Produk gak kedeteksi di foto. Pakai foto yang produknya jelas dan utuh.")
    warns = []
    if box[0] <= 1 or box[1] <= 1 or box[2] >= rgba.width - 1 or box[3] >= rgba.height - 1:
        warns.append("produk nempel tepi foto, mungkin ada bagian yang kepotong")
    return rgba.crop(box), warns


def cutout(photo):
    from rembg import new_session, remove  # berat, import cuma pas dipakai

    # u2net: ±1 GB RAM, ±1 dtk. isnet motong sebagian tutup botol di tes; birefnet butuh ±7 GB RAM.
    return clean(remove(photo, session=new_session("u2net"), post_process_mask=True))


def place(cw, ch, layout="hero"):
    """Skala + kotak (x, y, w, h) produk di kanvas W x H."""
    hm, wm, base, cx = LAYOUTS[layout]
    s = min(hm * H / ch, wm * W / cw)
    w, h = round(cw * s), round(ch * s)
    return s, (round(cx * W - w / 2), round(base * H) - h, w, h)


def tint(prod, bg, k=0.12):
    """Kasih sedikit warna cahaya scene ke produk biar gak keliatan ditempel. k kecil: warna label tetap asli."""
    y, cb, cr = prod.convert("RGB").convert("YCbCr").split()
    m = ImageStat.Stat(bg.convert("YCbCr")).mean
    cb, cr = cb.point(lambda v: v + k * (m[1] - 128)), cr.point(lambda v: v + k * (m[2] - 128))
    out = Image.merge("YCbCr", (y, cb, cr)).convert("RGB")
    out.putalpha(prod.getchannel("A"))
    return out


def composite(bg, cut, layout="hero", glossy=False, grain=True):
    s, (x, y, w, h) = place(*cut.size, layout)
    canvas = ImageOps.fit(bg.convert("RGB"), (W, H), Image.LANCZOS)
    if layout == "hand":  # dipegang di udara: background blur, tanpa bayangan meja
        canvas = canvas.filter(ImageFilter.GaussianBlur(12))
    prod = tint(cut.resize((w, h), Image.LANCZOS), canvas)
    if layout != "hand":
        cx, foot, sw, sh = x + w // 2, y + h, round(w * 0.9), max(round(w * 0.12), 2)
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).ellipse((cx - sw // 2, foot - sh // 2, cx + sw // 2, foot + sh // 2), fill=115)
        mask = mask.filter(ImageFilter.GaussianBlur(max(1, w * 0.05)))
        line = Image.new("L", (W, H), 0)  # garis kontak tipis biar produk gak "melayang"
        ImageDraw.Draw(line).ellipse((cx - w * 0.42, foot - w * 0.015, cx + w * 0.42, foot + w * 0.015), fill=200)
        mask = ImageChops.lighter(mask, line.filter(ImageFilter.GaussianBlur(max(1, w * 0.01))))
        dark = ImageChops.multiply(canvas, Image.new("RGB", (W, H), (70, 64, 58)))  # bayangan ikut warna permukaan
        canvas = Image.composite(dark, canvas, mask)
        if glossy:  # pantulan samar di permukaan mengkilap
            ref = ImageOps.flip(prod).crop((0, 0, w, round(h * 0.35))).filter(ImageFilter.GaussianBlur(1.5))
            fade = ImageOps.invert(Image.linear_gradient("L").resize(ref.size))
            canvas.paste(ref.convert("RGB"), (x, foot), ImageChops.multiply(ref.getchannel("A"), fade).point(lambda v: v * 0.28))
    canvas.paste(prod, (x, y), prod)
    if grain:  # grain halus nyatuin tekstur foto asli dengan background AI
        canvas = ImageChops.add(canvas, Image.effect_noise((W, H), 3).convert("RGB"), 1.0, -128)
    return canvas, s


def reference(cut, size=1536):
    """Produk di latar abu polos (bukan transparan): referensi buat Nano Banana/Ingredients di Flow."""
    s = min(0.8 * size / cut.width, 0.8 * size / cut.height)
    prod = cut.resize((round(cut.width * s), round(cut.height * s)), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), (240, 240, 240))
    canvas.paste(prod, ((size - prod.width) // 2, (size - prod.height) // 2), prod)
    return canvas


def zai_backdrop(scene, key):
    import requests

    try:
        r = requests.post(
            API,
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "glm-image", "prompt": scene + SUFFIX, "size": "960x1728"},
            timeout=120,
        )
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or not body.get("data"):
            err = body.get("error") or {}
            code = str(err.get("code") or r.status_code)
            raise Fail(ERRORS.get(code) or ERRORS.get(str(r.status_code))
                       or f"z.ai error {code}: {err.get('message') or r.text[:200]}")
        img = requests.get(body["data"][0]["url"], timeout=60)
        img.raise_for_status()
        return Image.open(BytesIO(img.content)).convert("RGB")
    except requests.RequestException as e:
        raise Fail(f"Gagal konek ke z.ai ({type(e).__name__}). Cek internet lalu coba lagi.") from None
    except OSError:
        raise Fail("Gambar dari z.ai rusak. Coba lagi.") from None


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")  # console Windows cp1252 gak crash
    p = argparse.ArgumentParser(description="Hero frame 9:16: produk asli + background AI")
    p.add_argument("photo")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--scene", help="lingkungan aja, bahasa Inggris (tanpa produk/botol/teks)")
    src.add_argument("--backdrop", help="pakai file background yang udah ada (gak manggil API)")
    a = p.parse_args(argv)
    try:
        if a.scene and BANNED.search(a.scene):
            raise Fail("Scene gak boleh nyebut produk/botol/merek/teks, biar AI gak bikin botol palsu. Tulis lingkungannya aja.")
        key = None if a.backdrop else api_key()  # cek key dulu sebelum proses yang lama
        cut, warns = cutout(load(a.photo))  # cutout sebelum bayar API: foto jelek gak buang saldo
        bg = load(a.backdrop) if a.backdrop else zai_backdrop(a.scene, key)
        hero, s = composite(bg, cut, glossy=bool(a.scene and GLOSSY.search(a.scene)))
        if s > 1.5:
            warns.append(f"foto produk kecil (di-zoom {s:.1f}x), hasil bisa kurang tajam")
        root = hermes_home() / "cache" / "tiktok-pack"
        root.mkdir(parents=True, exist_ok=True)
        out = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=root))
        bg.save(out / "backdrop.jpg", quality=95)
        hero.save(out / "hero.jpg", quality=95)
    except Fail as e:
        print(f"ERROR: {e}")
        return 1
    for w in warns:
        print(f"WARN: {w}")
    print(f"HERO: {(out / 'hero.jpg').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
