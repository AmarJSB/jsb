# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["pillow", "requests", "truststore"]
# ///
"""Self-check tanpa jaringan & tanpa rembg. Jalankan: uv run test_app.py"""
import base64
from io import BytesIO

from PIL import Image

import app
import hero

GOOD = {"best_photo": 2, "scene": "warm oak table, lavender sprigs, soft morning light", "flow_a": "Slow push-in.",
        "flow_b": "Subtle parallax", "caption": "Wangi lavender lembut buat harian.\nCek keranjang kuning ya!",
        "hashtags": ["parfum", "#lavender", "parfum lokal"], "onscreen": ["a", "b", "c", "d"], "voiceover": "VO.",
        "notes": "cek ukuran"}


def fails(fn, *args):
    try:
        fn(*args)
    except hero.Fail as e:
        return str(e)
    raise AssertionError(f"{fn.__name__} harusnya gagal")


def test_parse_reply():
    plain = '{"caption": "x"}'
    assert app.parse_reply(plain) == {"caption": "x"}
    assert app.parse_reply('```json\n{"caption": "x"}\n```') == {"caption": "x"}
    assert app.parse_reply('<|begin_of_box|>{"caption": "x"}<|end_of_box|>') == {"caption": "x"}
    assert app.parse_reply('{"caption": "x"}\n\nJadi jawabannya: {"caption": "y"}') == {"caption": "x"}
    assert app.parse_reply('<think>{"caption": "salah"}</think>{"caption": "x"}') == {"caption": "x"}
    assert app.parse_reply('{"caption": "Hook!\nCek keranjang"}') == {"caption": "Hook!\nCek keranjang"}  # enter mentah
    assert "gak kebaca" in fails(app.parse_reply, "maaf, gak bisa")
    assert "kepotong" in fails(app.parse_reply, '{"caption": "x', "length")
    assert "filter" in fails(app.parse_reply, "", "sensitive")


def test_normalize():
    r = app.normalize(GOOD, 3, "Parfum X 50ml NA18201200123")
    assert r["best_photo"] == 2 and r["scene"] == GOOD["scene"]
    assert r["pack"]["caption"].endswith("\n\n#parfum #lavender #parfumlokal")
    assert r["pack"]["onscreen"] == ["a", "b", "c"]
    assert r["pack"]["flow_a"].count(app.FLOW_END) == 1 and r["pack"]["flow_a"].startswith("Slow push-in. The product")
    assert app.with_end("Push in. " + app.FLOW_END).count(app.FLOW_END) == 1
    assert r["warnings"] == ["cek ukuran"]  # ada nomor BPOM, caption bersih
    bad = {**GOOD, "best_photo": 7, "scene": "perfume bottle on marble", "caption": "Dijamin 100% ori, mirip X!"}
    r = app.normalize(bad, 2, "Parfum X 50ml")
    assert r["best_photo"] == 1 and r["scene"] == app.FALLBACK_SCENE
    w = " | ".join(r["warnings"])
    assert app.BPOM_WARNING in w and "dijamin" in w and "100%" in w and "mirip" in w and "scene netral" in w
    assert "kosong" in fails(app.normalize, {"best_photo": 1}, 1, "x")
    tags = {**GOOD, "hashtags": ["#parfumori", "#parfummurah", "#parfumlokal", "#original"], "notes": []}
    w = app.normalize(tags, 1, "NA18201200123")["warnings"]
    assert len(w) == 1 and "#parfumori" in w[0] and "#parfummurah" in w[0] and "#original" in w[0]
    assert "#parfumlokal" not in w[0]


def test_patterns():
    assert app.BPOM_RE.search("BPOM NA18201200123") and app.BPOM_RE.search("nc 14191200123")
    assert not app.BPOM_RE.search("NA1820")
    assert not app.banned_hits("Kenyamanan dipakai harian, pastikan cek keranjang kuning")
    assert app.banned_hits("tahan 24 jam, bikin dilirik") == ["24 jam", "dilirik"]
    for risky in ("Cuma Rp59rb aja", "Best seller di toko", "Buruan sebelum kehabisan", "stoknya tinggal dikit",
                  "100 persen", "bikin cewek auto tertarik"):
        assert app.banned_hits(risky), risky
    assert not app.banned_hits("Wanginya nempel di baju. Tertarik? Cek keranjang kuning, 50ml")
    assert hero.BANNED.search("marble vanity for a luxury fragrance")
    assert not hero.BANNED.search(app.FALLBACK_SCENE)
    for job in ("20261008-101010-ab12",):
        assert app.JOB_RE.fullmatch(job)
    for job in ("..", "CON", "x/../y", "20261008-101010-ab12/../x"):
        assert not app.JOB_RE.fullmatch(job)
    assert app.FILE_RE.fullmatch("/hasil/20261008-101010-ab12/hero-1-0123456789.jpg")
    assert not app.FILE_RE.fullmatch("/hasil/20261008-101010-ab12/../settings.json")


def test_settings_and_photos():
    assert app.public({"key": "abcdef1234", "model": "glm-4.6v"})["key_hint"] == "...1234"
    assert app.public({"key": "", "model": "glm-4.6v"})["key_hint"] == ""
    assert "Foto ke-2" in fails(app.decode_photo, "data:image/jpeg;base64,bm90IGFuIGltYWdl", 2)
    assert "Foto ke-1" in fails(app.decode_photo, "tanpa koma", 1)
    buf = BytesIO()
    Image.new("RGB", (4000, 3000), (9, 9, 9)).save(buf, "PNG")
    img = app.decode_photo("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), 1)
    sent = Image.open(BytesIO(base64.b64decode(app.jpeg_url(img).split(",", 1)[1])))
    assert img.size == (4000, 3000) and max(sent.size) == 1280


def test_rembg_crash_probe():
    real = app.subprocess.run
    try:
        for rc, crash in ((0, False), (1, False), (3221225477, True), (-11, True)):  # 3221225477 = 0xC0000005
            app.subprocess.run = lambda *a, **k: type("R", (), {"returncode": rc})()
            app.probe_rembg()
            assert app._rembg_crash is crash, rc
        assert app.VC_REDIST in fails(app.rembg_tools)
    finally:
        app.subprocess.run, app._rembg_crash = real, False


def test_chat_body():
    assert "reasoning_effort" not in app.chat_body("glm-4.6v", [])
    body = app.chat_body("glm-5.3-flash", [])
    assert body["reasoning_effort"] == "low" and "thinking" not in body and "response_format" not in body


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
