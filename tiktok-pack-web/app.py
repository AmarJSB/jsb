# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["rembg[cpu]>=2.0.60,<3", "pillow", "requests", "truststore"]
# [tool.uv]
# exclude-newer = "2026-10-08T00:00:00Z"
# ///
"""TikTok Pack versi web lokal: foto produk + deskripsi seller -> hero 9:16, prompt Flow, caption.

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import truststore

truststore.inject_into_ssl()  # pakai sertifikat Windows, aman dari antivirus yang nyadap HTTPS

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent / "hermes-skills" / "tiktok-pack" / "scripts"))  # kalau jalan dari repo
import hero  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

HOST, PORT = "127.0.0.1", 8765
BASE = os.environ.get("ZAI_BASE", "https://api.z.ai/api/paas/v4")
hero.API = BASE + "/images/generations"
hero.ERRORS["401"] = "API key z.ai ditolak. Cek di Pengaturan."
DATA = HERE / "data"
OUT = DATA / "hasil"
SETTINGS = DATA / "settings.json"
MODELS = [
    ["glm-5.3-flash", "GLM-5.3 Flash (default, murah)"],
    ["glm-4.6v", "GLM-4.6V (vision, lebih mahal)"],
    ["glm-4.6v-flash", "GLM-4.6V Flash (gratis, kualitas lebih rendah)"],
]
MODEL_IDS = [m[0] for m in MODELS]
MAX_BODY = 60 * 1024 * 1024
JOB_RE = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")
FILE_RE = re.compile(r"/hasil/(\d{8}-\d{6}-[0-9a-f]{4})/(hero-[1-3]-[0-9a-f]{10}\.jpg)")
BPOM_RE = re.compile(r"\bN[A-E][\s.-]?\d{11}\b", re.I)
BANNED_COPY = re.compile(
    r"100\s*(%|persen)|\brp\s?\d|\b\d+\s?(rb|ribu)\b|\bbest\s?seller|\bbikin\s+(\w+\s+){0,2}(nempel|tertarik|klepek)"
    r"|\b(ampuh|dijamin|jaminan|pasti|paling|terlaris|no\.?\s?1|nomor\s?1|feromon|pheromone|dilirik|menggoda"
    r"|memikat|24\s?jam|seharian|ori|original|asli|murni|aman|efek samping|bumil|kulit sensitif|mirip|dupe|kw"
    r"|inspired|terinspirasi|alternatif|diskon|promo|harga|murah|stok|kehabisan)(nya)?\b",
    re.I,
)
TAG_BAD = re.compile(r"murah|original|(ori|asli|kw)$|feromon|pheromone|24jam|seharian|dupe|inspired|diskon|promo"
                     r"|terlaris|bestseller", re.I)  # hashtag gabungan kayak #parfumori, #parfummurah
FLOW_END = (
    "The product stays exactly as in the first frame: same shape, cap, colors and label. Gentle camera; the product"
    " keeps facing the camera the whole time. No hands, no people. No speech, no voiceover, no on-screen text."
    " Soft ambient music only."
)
FALLBACK_SCENE = "smooth pale stone surface, softly blurred warm beige wall behind, gentle window light from the left"
BPOM_WARNING = "Nomor BPOM (NA/NB/NC/ND/NE...) gak ada di deskripsi seller. Cek dulu di cekbpom.pom.go.id sebelum promosi."
CHAT_ERRORS = {
    **hero.ERRORS,
    "1301": "z.ai nolak konten ini (filter). Ubah deskripsi atau ganti foto.",
    "1210": "Model ini nolak permintaannya (foto/parameter gak didukung). Ganti model di Pengaturan.",
    "1211": "Model ini gak ada di akun z.ai kamu. Ganti model di Pengaturan.",
}

# Aturan di PROMPT ini dan di hermes-skills/tiktok-pack/SKILL.md harus dijaga sinkron.
PROMPT = """You prepare a TikTok Shop affiliate content pack for Amar, an Indonesian affiliate who mostly sells perfume. Accuracy beats hype: TikTok Shop Indonesia requires AI visuals to match the real product, and BPOM polices cosmetic claims.

INPUT
- The seller description is between <<< and >>> at the end.
- {n} product photo(s) are attached in order: photo 1 is the first image.
- Amar has personally used this product: {tried}.

FACTS
- From the seller TEXT only: brand, product name, variant, size (ml), scent notes, the seller's own claims (skip comparisons to other brands). Never take a BPOM number from a photo.
- From the PHOTOS only appearance: colors, cap, bottle shape, material, spray atomizer or not.
- Never invent a fact. Unknown -> leave it out.
- One product per pack: if the photos or the text cover several products/variants, use only the one in best_photo, take only its facts, and say so in notes.

best_photo: number of the cleanest photo: a single product, fully visible including the cap, sharp, no "ORI 100%" badge, price banner, watermark, text overlay or other products. Use 0 if none qualifies and explain why in photo_problem (Indonesian, one sentence).

scene (English, one sentence, max 40 words): environment only (surface, background, light) for a vertical product photo. Props only from the listed scent notes (rose petals for rose, citrus slices for citrus). Tobacco, smoke or alcohol notes (tobacco, cigar, rum, whiskey, cognac, wine, champagne): no props for them; use warm wood, amber light or vanilla pods instead. Colors that complement the bottle; light direction like the photo. Never mention perfume, bottle, product, brand, label, logo or text, not even negated like "no text".

flow_a, flow_b (English, max 55 words each): camera and background motion for Google Flow "Frames to Video" (8 seconds, the hero image is the first frame). Veo redraws every frame after the first, so keep motion small.
- flow_a: slow push-in with a soft light sweep across the glass.
- flow_b: subtle parallax with gentle background elements from the scent notes (petals, mist, bokeh); never smoke, cigars or drinks.
- Spray mist only if the photos show an atomizer.
- Do not add rules about keeping the product unchanged, people, speech or text: the app appends that sentence itself.

COPY (casual, polite Indonesian)
- caption: max 300 characters, the first line is the hook, 1-2 facts from the seller text, ends with a CTA to the yellow basket ("cek keranjang kuning"). No hashtags inside.
- hashtags: 3-5 relevant ones (category, scent, this product's brand only if given).
- onscreen: exactly 3 lines, max 6 words each.
- voiceover: 35-40 words for 15 seconds, describing the look and scent notes. {first_person}
- Longevity or effect claims only if the seller text states them, never exaggerated.
- Never use: 100%, ampuh, dijamin, pasti, paling/no.1/terlaris, feromon/pheromone, attraction or seduction claims in any wording ("bikin orang tertarik/nempel", "dilirik", "menggoda", "memikat"), 24 jam/seharian, "ori/original/asli/murni" claims, safety claims ("aman", "tanpa efek samping", "aman buat bumil/kulit sensitif"), medical or skin-treatment claims, other brands' or perfumes' names and comparisons ("inspired by", "terinspirasi", "mirip", "dupe", "KW", "alternatif X"), prices or discounts, fake testimonials, fake stories, fake urgency ("stok tinggal 3!").
- notes: Indonesian warnings for Amar (missing info, risky seller claims you left out), or []. Skip the BPOM number: the app checks it.

OUTPUT: reply with ONLY one JSON object, no markdown, exactly these keys:
{{"best_photo": 1, "photo_problem": "", "scene": "", "flow_a": "", "flow_b": "", "caption": "", "hashtags": ["#..."], "onscreen": ["", "", ""], "voiceover": "", "notes": []}}

<<<
{desc}
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
            "model": s["model"], "models": MODELS}


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


def chat_body(model, content):
    body = {"model": model, "max_tokens": 8192, "messages": [{"role": "user", "content": content}]}
    if model.startswith("glm-5"):
        body["reasoning_effort"] = "low"  # GLM-5.x selalu "mikir"; low biar cepat & hemat
    return body


def zai_chat(model, content, key):
    import requests

    try:
        r = requests.post(BASE + "/chat/completions", headers={"Authorization": f"Bearer {key}"},
                          json=chat_body(model, content), timeout=(10, 170))
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


def with_end(prompt):
    prompt = prompt.split("The product stays exactly", 1)[0].strip().rstrip(".")
    return f"{prompt}. {FLOW_END}" if prompt else FLOW_END


def banned_hits(s):
    return sorted({m.group(0).lower() for m in BANNED_COPY.finditer(s)})


def normalize(obj, n, desc):
    caption, flow_a = text(obj.get("caption")), text(obj.get("flow_a"))
    if not caption and not flow_a:
        raise Fail("Jawaban AI kosong, klik Bikin paket lagi.")
    best = obj.get("best_photo")
    best = best if isinstance(best, int) and 0 <= best <= n else 1
    tag_list = [t for t in (h.lstrip("#").replace(" ", "") for h in lines(obj.get("hashtags"))[:5]) if t]
    tags = " ".join("#" + t for t in tag_list)
    onscreen, voiceover = lines(obj.get("onscreen"))[:3], text(obj.get("voiceover"))
    warnings = [] if BPOM_RE.search(desc) else [BPOM_WARNING]
    hits = banned_hits("\n".join([caption, *onscreen, voiceover]))
    hits += ["#" + t.lower() for t in tag_list if TAG_BAD.search(t)]
    if hits:
        warnings.append(f"Kata berisiko ketemu: {', '.join(hits)}. Ubah dulu sebelum posting.")
    if len(caption) > 300:
        warnings.append(f"Caption {len(caption)} karakter, kepanjangan. Pendekin jadi maksimal 300.")
    scene = text(obj.get("scene"))
    if not scene or hero.BANNED.search(scene):
        if scene:
            warnings.append("Scene dari AI nyebut produk/teks, jadi diganti scene netral. Boleh kamu edit.")
        scene = FALLBACK_SCENE
    if best == 0:
        warnings.append(text(obj.get("photo_problem")) or "Gak ada foto yang cukup bersih buat hero image.")
    warnings += lines(obj.get("notes"))
    pack = {"flow_a": with_end(flow_a), "flow_b": with_end(text(obj.get("flow_b"))),
            "caption": caption + (f"\n\n{tags}" if tags else ""), "onscreen": onscreen, "voiceover": voiceover}
    return {"best_photo": best, "scene": scene, "pack": pack, "warnings": warnings}


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
    job = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    out = OUT / job
    out.mkdir(parents=True)
    for i, p in enumerate(photos, 1):
        shrink(p, 2048).save(out / f"foto{i}.jpg", quality=92)
    tried = bool(body.get("tried"))
    prompt = PROMPT.format(
        n=len(photos), desc=desc, tried="yes" if tried else "no",
        first_person="First-person usage is allowed, but stay factual." if tried
        else 'Never write first-person usage ("aku udah pakai...").',
    )
    content = [{"type": "text", "text": prompt}]
    content += [{"type": "image_url", "image_url": {"url": jpeg_url(p)}} for p in photos]
    obj = parse_reply(*zai_chat(s["model"], content, s["key"]))
    return {"job": job, "photos": len(photos), **normalize(obj, len(photos), desc)}


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


def make_hero(body):
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
    img, scale = hero.composite(bg, cut)
    if scale > 1.5:
        warnings.append(f"Foto produk kecil (di-zoom {scale:.1f}x), hasil bisa kurang tajam.")
    name = f"hero-{n}-{h}.jpg"
    img.save(OUT / job / name, quality=95)
    return {"url": f"/hasil/{job}/{name}", "warnings": warnings}


class Handler(BaseHTTPRequestHandler):
    routes = {"/api/settings": save_settings, "/api/analyze": analyze, "/api/hero": make_hero}

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
            return self.send(200, (OUT / m[1] / m[2]).read_bytes(), "image/jpeg")
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
        print(f"PERINGATAN: {e}\nPaket teks tetap bisa dibuat, hero image belum bisa.")
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
