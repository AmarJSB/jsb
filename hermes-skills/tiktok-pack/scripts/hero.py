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

from PIL import Image, ImageDraw, ImageFilter, ImageOps

W, H = 1080, 1920
H_MAX, W_MAX, BASE_Y = 0.42, 0.70, 0.72  # produk di tengah, aman dari UI TikTok di bawah & kanan
API = "https://api.z.ai/api/paas/v4/images/generations"
KEY_VARS = ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY")
SUFFIX = (
    ", empty surface in the foreground center, eye-level straight-on camera, shallow depth of field,"
    " soft natural light, photorealistic, vertical composition, no products, no bottles, no containers,"
    " no people, no hands, no text, no letters, no logos, no watermark"
)
BANNED = re.compile(r"\b(perfumes?|parfum|bottles?|botol|products?|produk|logos?|labels?|text|brand|merek)\b", re.I)
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


def place(cw, ch):
    """Skala + kotak (x, y, w, h) produk di kanvas W x H."""
    s = min(H_MAX * H / ch, W_MAX * W / cw)
    w, h = round(cw * s), round(ch * s)
    return s, ((W - w) // 2, round(BASE_Y * H) - h, w, h)


def composite(bg, cut):
    s, (x, y, w, h) = place(*cut.size)
    canvas = ImageOps.fit(bg.convert("RGB"), (W, H), Image.LANCZOS)
    sw, sh = round(w * 0.9), max(round(w * 0.12), 2)
    shadow = Image.new("L", (W, H), 0)
    cx = x + w // 2
    ImageDraw.Draw(shadow).ellipse((cx - sw // 2, y + h - sh // 2, cx + sw // 2, y + h + sh // 2), fill=115)
    canvas.paste((0, 0, 0), (0, 0, W, H), shadow.filter(ImageFilter.GaussianBlur(max(1, w * 0.05))))
    prod = cut.resize((w, h), Image.LANCZOS)
    canvas.paste(prod, (x, y), prod)
    return canvas, s


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
        hero, s = composite(bg, cut)
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
