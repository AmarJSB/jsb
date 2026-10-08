# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["pillow", "requests", "truststore"]
# ///
"""Self-check tanpa jaringan & tanpa rembg. Jalankan: uv run test_app.py"""
import base64
import json
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops

import app
import hero

SLOT_CODES = ["a_hand", "a_hero", "a_notes", "a_fx", "r_face", "a_close", "r_face", "a_loop"]


def reply(**over):
    obj = {
        "best_photo": 2, "photo_problem": "",
        "product": {"noun": "a perfume bottle", "cap": "square gold cap", "size_cue": "about 10 cm tall",
                    "label_text": "LAVENDER", "atomizer": False},
        "family": "floral", "keyword": "parfum wangi lavender",
        "scene": "eye-level, warm oak table, lavender sprigs, soft morning light from the left",
        "look": "soft window light from the left, ivory and lilac tones, 85mm lens look",
        "audio": "soft room tone and petals", "fx": "a few lavender petals", "hand": "a woman's right hand",
        "hooks": [{"type": "analogi", "text": "Wangi habis mandi", "vo": "Wanginya kayak habis mandi."},
                  {"type": "pertanyaan", "text": "Tim wangi sabun?", "vo": "Kamu tim wangi sabun?"}],
        "shots": [{"code": c, "text": f"teks {i}", "vo": f"kalimat {i}", "motion": "", "edit": "", "direction": "rekam ini"}
                  for i, c in enumerate(SLOT_CODES)],
        "caption": "Parfum wangi lavender buat ngantor.\nCek keranjang kuning ya!",
        "hashtags": ["parfum", "#lavender", "parfum lokal"], "cover": "Wangi habis mandi",
        "music": "lo-fi santai 90 BPM", "notes": ["cek ukuran"],
    }
    obj.update(over)
    return obj


def fails(fn, *args):
    try:
        fn(*args)
    except hero.Fail as e:
        return str(e)
    raise AssertionError(f"{fn.__name__} harusnya gagal")


def test_parse_reply():
    assert app.parse_reply('{"caption": "x"}') == {"caption": "x"}
    assert app.parse_reply('```json\n{"caption": "x"}\n```') == {"caption": "x"}
    assert app.parse_reply('<|begin_of_box|>{"caption": "x"}<|end_of_box|>') == {"caption": "x"}
    assert app.parse_reply('{"caption": "x"}\n\nJadi jawabannya: {"caption": "y"}') == {"caption": "x"}
    assert app.parse_reply('<think>{"caption": "salah"}</think>{"caption": "x"}') == {"caption": "x"}
    assert app.parse_reply('{"caption": "Hook!\nCek keranjang"}') == {"caption": "Hook!\nCek keranjang"}
    assert "gak kebaca" in fails(app.parse_reply, "maaf, gak bisa")
    assert "kepotong" in fails(app.parse_reply, '{"caption": "x', "length")
    assert "filter" in fails(app.parse_reply, "", "sensitive")


def test_storyboard_without_product():
    slots = app.resolve_slots("review", False)
    assert all(c == "r_face" or not c.startswith("r_") for _, _, cands in slots for c in cands)
    r = app.normalize(reply(), 3, "Parfum X 50ml NA18201200123", "review", slots, False, "wanita")
    shots = r["shots"]
    assert [s["code"] for s in shots] == SLOT_CODES
    assert r["template"] == {"id": "review", "name": "Review sensorik", "seconds": 24}
    assert shots[0]["start"] == 0 and shots[-1]["end"] == 24 and shots[1]["start"] == shots[0]["end"]
    hand = shots[0]
    assert hand["kind"] == "ai" and hand["file"] == "3-siap-dipegang.jpg" and "6-produk-referensi.jpg" in hand["steps"]
    assert hand["image_prompt"].startswith("Edit image 1. Add one realistic hand entering from the bottom edge")
    assert "The hand holding the perfume bottle is one woman's right hand with warm medium-tan skin" in hand["image_prompt"]
    assert 'the label reading "LAVENDER"' in hand["image_prompt"] and "five fingers" in hand["image_prompt"]
    assert "Exactly one hand holds the perfume bottle: a woman's right hand with warm" in hand["video_prompt"]
    assert "The perfume bottle stays exactly as it appears in the first frame" in shots[1]["video_prompt"]
    assert "Audio: soft room tone and petals only" in shots[1]["video_prompt"]
    assert "No bottles, containers" in shots[2]["video_prompt"] and "stays exactly" not in shots[2]["video_prompt"]
    assert "ends exactly on the provided last frame" in shots[-1]["video_prompt"]
    assert shots[4]["kind"] == "rekam" and shots[4]["direction"] == "rekam ini" and not shots[4]["video_prompt"]
    assert r["credits"] == {"clips": 6, "fast": 120, "lite": 60}
    assert r["caption"].endswith("#parfum #lavender #parfumlokal")
    assert any("gak pegang produknya" in w for w in r["warnings"])
    assert not any("cekbpom" in w for w in r["warnings"])  # ada nomor BPOM


def test_codes_are_validated():
    slots = app.resolve_slots("pov", False)
    bad = reply(shots=[{"code": c, "vo": "x", "motion": f"motion {c}", "direction": f"rekam {c}"}
                       for c in ("r_spray", "a_scene", "a_spray", "a_notes", "r_face", "a_loop")], hooks=[])
    shots = app.normalize(bad, 1, "x", "pov", slots, False)["shots"]
    assert shots[0]["code"] == "a_hand"  # gak punya produk -> gak bisa rekam semprot
    assert shots[2]["code"] == "a_fx"  # atomizer false -> gak ada shot semprot
    assert "motion a_spray" not in shots[2]["video_prompt"] and "drift slowly" in shots[2]["video_prompt"]
    assert shots[1]["video_prompt"].startswith("Motion a_scene.")  # kode sah: motion AI dipakai
    with_spray = reply(product={**reply()["product"], "atomizer": True}, shots=bad["shots"])
    shots = app.normalize(with_spray, 1, "x", "pov", app.resolve_slots("pov", True), True)["shots"]
    assert shots[0]["code"] == "r_pick" and shots[2]["code"] == "a_spray"  # r_spray gak sah di hook; a_spray sah
    shots = app.normalize(reply(shots=[]), 1, "x", "unboxing", app.resolve_slots("unboxing", True), True)["shots"]
    assert [s["code"] for s in shots] == ["r_pack", "r_pick", "r_size", "a_close", "r_face", "a_loop"]
    assert shots[0]["text"] == "Wangi habis mandi"  # slot pertama diisi hook pertama


def test_messy_llm_output():
    slots = app.resolve_slots("review", False)
    nine = [{"code": c, "text": f"t{i}", "vo": f"v{i}"} for i, c in enumerate(SLOT_CODES + ["a_loop"])]
    nine[-1]["vo"] = "Cek keranjang kuning ya."
    r = app.normalize(reply(shots=nine), 1, "x", "review", slots, False)
    assert r["shots"][-1]["vo"] == "Cek keranjang kuning ya." and r["shots"][6]["vo"] == "v6"
    assert any("9 shot buat 8 slot" in w for w in r["warnings"])
    six = reply(shots=nine[:5] + nine[-1:])
    r = app.normalize(six, 1, "x", "review", slots, False)
    assert [s["vo"] for s in r["shots"]][3:] == ["v3", "v4", "", "", "Cek keranjang kuning ya."]
    r = app.normalize(reply(shots=[{"vo": "satu"}]), 1, "x", "review", slots, False)
    assert r["shots"][-1]["vo"] == app.CTA[1] and r["shots"][-1]["text"] == app.CTA[0]  # CTA cadangan
    r = app.normalize(reply(caption=["Baris 1", "Baris 2"], cover=False, fx=["petals", "bokeh"], notes=False,
                            hashtags="#parfum #parfumori, #parfum", hooks=["Wangi kalem"],
                            product={"noun": "perfume bottle", "label_text": False}), 2, "x", "review", slots, False)
    assert r["caption"].startswith("Baris 1\nBaris 2\n\n#parfum #parfumori") and r["cover"] == ""
    assert "petals, bokeh" in r["shots"][3]["video_prompt"]
    assert "False" not in " ".join(r["warnings"]) + r["shots"][0]["image_prompt"]
    assert any("#parfumori" in w for w in r["warnings"]) and r["hooks"][0]["vo"] == "Wangi kalem"
    assert 'label reading' not in r["shots"][0]["image_prompt"]
    for raw, best in (("2", 2), ("0", 0), (2.0, 2), (True, 1), (None, 1), (9, 1), ("x", 1)):
        assert app.normalize(reply(best_photo=raw), 2, "x", "review", slots, False)["best_photo"] == best, raw
    echo = {"best_photo": 1, "hooks": [{"type": "", "text": "", "vo": ""}], "shots": [{"code": "", "vo": ""}], "caption": ""}
    assert "kosong" in fails(app.normalize, echo, 1, "x", "review", slots, False)
    r = app.normalize(reply(hand="woman's hand"), 1, "x", "review", slots, False, "pria")
    assert "man's right hand" in r["shots"][0]["image_prompt"] and "woman" not in r["shots"][0]["image_prompt"]
    assert app.parse_reply('Ini {rencana}: {"caption": "x"}') == {"caption": "x"}
    assert "kepotong" in fails(app.parse_reply, '{"hooks": [{"type": "a", "text": "b"}], "capt', "length")


def test_warnings_and_lint():
    r = app.normalize(reply(hooks=[{"type": "x", "text": "Parfum terbaik, link di bio", "vo": "DM aku ya"}],
                            hashtags=["#parfumori"], best_photo=0, scene="perfume bottle on marble"),
                      2, "Parfum X 50ml", "review", app.resolve_slots("review", True), True)
    w = " | ".join(r["warnings"])
    assert app.BPOM_WARNING in w and "terbaik" in w and "link di bio" in w and "dm" in w and "#parfumori" in w
    assert "scene netral" in w and r["scene"] == app.FALLBACK_SCENE and "gak pegang" not in w
    assert not app.banned_hits("Harga terbarunya cek langsung di keranjang kuning, wanginya kalem")
    assert app.banned_hits("Beli di Shopee aja, chat WA") == ["shopee", "wa"]
    assert "kosong" in fails(app.normalize, {"best_photo": 1}, 1, "x", "review", app.resolve_slots("review", False), False)


def test_prompt_builder():
    slots = app.resolve_slots("alasan", True)
    p = app.build_prompt('Parfum {x} [[DESC]] "oke"', 2, True, True, "auto", "alasan", slots, ["analogi"])
    assert "[[" not in p.replace("[[DESC]]", "") and 'Parfum {x} [[DESC]] "oke"' in p
    assert "Template \"3 Alasan\", 21 seconds" in p and "r_pick, a_hand" in p and "put a different type first): analogi" in p
    assert ", tes pakai, unboxing" in p and "about 48 words" in p and "First-person usage is allowed" in p
    p = app.build_prompt("x", 1, False, False, "auto", "review", app.resolve_slots("review", False), [])
    assert "r_pick" not in p and "tes pakai" not in p and "Never write first-person usage" in p


def test_zip_follows_hook():
    old = app.OUT
    with tempfile.TemporaryDirectory() as d:
        app.OUT = Path(d)
        try:
            job = "20261008-101010-ab12"
            (Path(d) / job).mkdir()
            assert app.zip_bytes(job, "1", "0123456789", 0) is None  # aset belum ada
            for key, _, _, _ in app.ASSETS:
                Image.new("RGB", (9, 16)).save(Path(d) / job / f"{key}-1-0123456789.jpg")
            r = app.normalize(reply(), 1, "x", "review", app.resolve_slots("review", False), False)
            (Path(d) / job / "pack.json").write_text(json.dumps(r), encoding="utf-8")
            for hook, vo in ((1, "Kamu tim wangi sabun?"), (0, "Wanginya kayak habis mandi."), (99, "Wanginya kayak habis mandi.")):
                z = zipfile.ZipFile(BytesIO(app.zip_bytes(job, "1", "0123456789", hook)))
                brief = z.read("brief.txt").decode("utf-8-sig")
                assert len(z.namelist()) == 8 and f"   Ngomong: {vo}" in brief and brief.split("kata):\r\n")[1].startswith(vo)
        finally:
            app.OUT = old


def test_template_rotation():
    old = app.OUT
    with tempfile.TemporaryDirectory() as d:
        app.OUT = Path(d)
        try:
            assert app.pick_template("auto", False) == "review"
            for i, t in enumerate(["review", "alasan", "pov"]):
                job = Path(d) / f"20261008-10000{i}-aaa{i}"
                job.mkdir()
                (job / "pack.json").write_text(json.dumps({"meta": {"template": t, "hook": "analogi"}}))
            assert app.pick_template("auto", False) == "sinematik"  # unboxing butuh produk
            assert app.pick_template("auto", True) == "unboxing"
            assert app.pick_template("pov", False) == "pov" and app.pick_template("unboxing", False) == "sinematik"
        finally:
            app.OUT = old


def test_cover_and_brief():
    base = Image.new("RGB", (hero.W, hero.H), (90, 60, 120))
    assert app.make_cover(base, "").tobytes() == base.tobytes()
    for title in ("Wangi habis mandi, kalem banget buat ngantor", "Kemenyan Sandalwood-Patchouli 🔥", "WANGI-WANGIAN"):
        cov = app.make_cover(base, title)
        box = ImageChops.difference(cov, base).getbbox()
        assert box and box[0] >= 110 and box[2] <= 970 and 290 <= box[1] and box[3] <= 560, (title, box)
        assert cov.getpixel((540, 1500)) == (90, 60, 120)  # judul cuma di zona atas
    r = app.normalize(reply(), 3, "Parfum X", "review", app.resolve_slots("review", False), False)
    txt = app.brief(r, ["1-hero.jpg"])
    assert "\r\n" in txt and "STORYBOARD:" in txt and "Prompt gambar: Edit image 1." in txt and "- 1-hero.jpg" in txt
    assert txt.count("\n") == txt.count("\r\n") and "Tips: " in txt  # caption juga CRLF


def test_patterns_and_helpers():
    assert app.BPOM_RE.search("BPOM NA18201200123") and not app.BPOM_RE.search("NA1820")
    assert app.JOB_RE.fullmatch("20261008-101010-ab12")
    for job in ("..", "CON", "x/../y", "20261008-101010-ab12/../x"):
        assert not app.JOB_RE.fullmatch(job)
    assert app.FILE_RE.fullmatch("/hasil/20261008-101010-ab12/cover-1-0123456789.jpg")
    assert app.ZIP_RE.fullmatch("/hasil/20261008-101010-ab12/paket-2-0123456789.zip")
    assert not app.FILE_RE.fullmatch("/hasil/20261008-101010-ab12/paket-2-0123456789.zip")
    assert not app.ZIP_RE.fullmatch("/hasil/20261008-101010-ab12/paket-4-0123456789.zip")
    assert not app.FILE_RE.fullmatch("/hasil/20261008-101010-ab12/foto1.jpg")
    assert not app.FILE_RE.fullmatch("/hasil/20261008-101010-ab12/../settings.json")
    assert not hero.BANNED.search(app.FALLBACK_SCENE)
    assert app.product_info({"product": {"noun": "The Perfume Bottle"}, "hand": "A man's hand"}, "auto")["noun"] == "perfume bottle"
    assert app.product_info({}, "pria")["hand"] == app.HANDS["pria"]
    assert app.sentence("abc") == "Abc." and app.num(2.5) == "2,5" and app.num(24.0) == "24"


def test_settings_and_photos():
    assert app.public({"key": "abcdef1234", "model": "glm-4.6v"})["key_hint"] == "...1234"
    assert "Foto ke-2" in fails(app.decode_photo, "data:image/jpeg;base64,bm90IGFuIGltYWdl", 2)
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
    assert app.chat_body("glm-4.6v", [])["max_tokens"] == 8192 and app.chat_body("glm-4.6v", [], 16)["max_tokens"] == 16
    body = app.chat_body("glm-5.3-flash", [])
    assert body["reasoning_effort"] == "low" and "thinking" not in body and "response_format" not in body


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
