# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["requests", "truststore"]
# ///
"""Self-check tanpa jaringan. Jalankan: uv run test_app.py"""
import base64
import json
import tempfile
from pathlib import Path

import app

SLOT_CODES = ["a_hand", "a_hero", "a_notes", "a_fx", "r_face", "a_close", "r_face", "a_loop"]  # review 20 dtk
JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"\0" * 64).decode()


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
    except app.Fail as e:
        return str(e)
    raise AssertionError(f"{fn.__name__} harusnya gagal")


def review(seconds=20, has_product=False, **over):
    slots = app.resolve_slots("review", has_product, seconds)
    return app.normalize(reply(**over), 3, "Parfum X 50ml NA18201200123", "review", slots, has_product, "wanita")


def test_parse_reply():
    assert app.parse_reply('{"caption": "x"}') == {"caption": "x"}
    assert app.parse_reply('```json\n{"caption": "x"}\n```') == {"caption": "x"}
    assert app.parse_reply('<|begin_of_box|>{"caption": "x"}<|end_of_box|>') == {"caption": "x"}
    assert app.parse_reply('{"caption": "x"}\n\nJadi jawabannya: {"caption": "y"}') == {"caption": "x"}
    assert app.parse_reply('<think>{"caption": "salah"}</think>{"caption": "x"}') == {"caption": "x"}
    assert app.parse_reply('{"caption": "Hook!\nCek keranjang"}') == {"caption": "Hook!\nCek keranjang"}
    assert app.parse_reply('Ini {rencana}: {"caption": "x"}') == {"caption": "x"}
    assert app.parse_reply('{"name": "A", "is_product_page": true}', None, ("name",))["name"] == "A"
    assert "gak kebaca" in fails(app.parse_reply, "maaf, gak bisa")
    assert "kepotong" in fails(app.parse_reply, '{"hooks": [{"type": "a"}], "capt', "length")
    assert "filter" in fails(app.parse_reply, "", "sensitive")


def test_templates_fit_every_duration():
    for t, (name, needs, beats) in app.TEMPLATES.items():
        assert beats[-1][2] == ["a_loop"] and beats[-1][3] == app.template_min(t), t
        for d in app.DURATIONS:
            if d < app.template_min(t):
                continue
            slots = app.resolve_slots(t, True, d)
            assert sum(sec for _, sec, _ in slots) == d, (t, d)
            assert not set(slots[0][2]) & {"a_notes", "a_scene"}, (t, d)  # frame pertama nampilin produk
            assert d < 15 or any("r_face" in c for _, _, c in slots), (t, d)  # ada muka/suara Amar
    assert app.template_min("alasan") == 15 and app.template_min("review") == 10
    assert [len(app.resolve_slots("review", False, d)) for d in app.DURATIONS] == [4, 6, 8, 11, 16, 21]


def test_storyboard_and_frames():
    r = review()
    shots, frames = r["shots"], r["frames"]
    assert [s["code"] for s in shots] == SLOT_CODES
    assert r["template"] == {"id": "review", "name": "Review sensorik", "seconds": 20}
    assert shots[0]["start"] == 0 and shots[-1]["end"] == 20 and shots[1]["start"] == shots[0]["end"]
    assert [f["key"] for f in frames] == ["hero", "close", "hand", "side", "cover"]
    hero, hand = frames[0], frames[2]
    assert hero["prompt"].startswith("Image 1 is the real product photo. Create a photorealistic product photo")
    assert "warm oak table" in hero["prompt"] and 'the label reading "LAVENDER"' in hero["prompt"]
    assert hero["attach"] == "foto produk asli" and "Frame Hero (gambar 2)" in hand["attach"]
    assert "The hand holding the perfume bottle is one woman's right hand with warm" in hand["prompt"]
    assert "five fingers" in hand["prompt"] and "Exactly one perfume bottle" in hand["prompt"]
    first = shots[0]
    assert first["kind"] == "ai" and first["frame_label"] == "Frame Dipegang" and "start frame: Frame Dipegang" in first["steps"]
    assert "Exactly one hand holds the perfume bottle: a woman's right hand with warm" in first["video_prompt"]
    assert "The perfume bottle stays exactly as it appears in the first frame" in shots[1]["video_prompt"]
    assert "Audio: soft room tone and petals only" in shots[1]["video_prompt"]
    assert "No bottles, containers" in shots[2]["video_prompt"] and shots[2]["frame"] == "" and "Text to Video" in shots[2]["steps"]
    assert "ends exactly on the provided last frame" in shots[-1]["video_prompt"] and "DAN end frame: Frame Ruang Teks" in shots[-1]["steps"]
    assert shots[4]["kind"] == "rekam" and shots[4]["direction"] == "rekam ini" and not shots[4]["video_prompt"]
    assert r["credits"] == {"frames": 5, "clips": 6, "fast": 120, "lite": 60}
    assert r["caption"].endswith("#parfum #lavender #parfumlokal")
    assert any("gak pegang produknya" in w for w in r["warnings"]) and not any("cekbpom" in w for w in r["warnings"])
    scene = app.build_frames([{"frame": "scene", "edit": ""}], app.product_info(reply(), "auto"), "x")[1]
    assert scene["key"] == "scene" and scene["attach"] == "Frame Hero" and "no products" in scene["prompt"]
    assert "Exactly one" not in scene["prompt"]
    r10 = review(10, shots=[])
    assert [s["code"] for s in r10["shots"]] == ["a_hand", "a_hero", "a_notes", "a_loop"] and r10["vo_words"] > 0


def test_codes_are_validated():
    slots = app.resolve_slots("pov", False, 10)  # Hook POV, Suasana, Pakai, CTA
    bad = reply(shots=[{"code": c, "vo": "x", "motion": f"motion {c}", "direction": f"rekam {c}"}
                       for c in ("r_spray", "a_scene", "a_spray", "a_loop")], hooks=[])
    shots = app.normalize(bad, 1, "x", "pov", slots, False)["shots"]
    assert shots[0]["code"] == "a_hand"  # gak punya produk -> gak bisa rekam semprot
    assert shots[2]["code"] == "a_fx" and "motion a_spray" not in shots[2]["video_prompt"]  # atomizer false
    assert shots[1]["video_prompt"].startswith("Motion a_scene.")  # kode sah: motion AI dipakai
    with_spray = reply(product={**reply()["product"], "atomizer": True}, shots=bad["shots"])
    shots = app.normalize(with_spray, 1, "x", "pov", app.resolve_slots("pov", True, 10), True)["shots"]
    assert shots[0]["code"] == "r_pick" and shots[2]["code"] == "a_spray"  # r_spray gak sah di hook; a_spray sah
    r = app.normalize(with_spray, 1, "x", "pov", app.resolve_slots("pov", True, 10), True)
    assert "spray" in [f["key"] for f in r["frames"]]
    shots = app.normalize(reply(shots=[]), 1, "x", "unboxing", app.resolve_slots("unboxing", True, 15), True)["shots"]
    assert [s["code"] for s in shots] == ["r_pack", "r_pick", "r_size", "r_face", "a_loop"]  # atomizer false
    assert shots[0]["text"] == "Wangi habis mandi"  # slot pertama diisi hook pertama


def test_messy_llm_output():
    slots = app.resolve_slots("review", False, 20)
    nine = [{"code": c, "text": f"t{i}", "vo": f"v{i}"} for i, c in enumerate(SLOT_CODES + ["a_loop"])]
    nine[-1]["vo"] = "Cek keranjang kuning ya."
    r = app.normalize(reply(shots=nine), 1, "x", "review", slots, False)
    assert r["shots"][-1]["vo"] == "Cek keranjang kuning ya." and r["shots"][6]["vo"] == "v6"
    assert any("9 shot buat 8 slot" in w for w in r["warnings"])
    r = app.normalize(reply(shots=nine[:5] + nine[-1:]), 1, "x", "review", slots, False)
    assert [s["vo"] for s in r["shots"]][3:] == ["v3", "v4", "", "", "Cek keranjang kuning ya."]
    r = app.normalize(reply(shots=[{"vo": "satu"}]), 1, "x", "review", slots, False)
    assert r["shots"][-1]["vo"] == app.CTA[1] and r["shots"][-1]["text"] == app.CTA[0]  # CTA cadangan
    r = app.normalize(reply(caption=["Baris 1", "Baris 2"], cover=False, fx=["petals", "bokeh"], notes=False,
                            hashtags="#parfum #parfumori, #parfum", hooks=["Wangi kalem"],
                            product={"noun": "perfume bottle", "label_text": False}), 2, "x", "review", slots, False)
    assert r["caption"].startswith("Baris 1\nBaris 2\n\n#parfum #parfumori") and r["cover"] == ""
    assert "petals, bokeh" in r["shots"][3]["video_prompt"]
    assert "False" not in " ".join(r["warnings"]) + r["frames"][0]["prompt"] and "label reading" not in r["frames"][0]["prompt"]
    assert any("#parfumori" in w for w in r["warnings"]) and r["hooks"][0]["vo"] == "Wangi kalem"
    for raw, best in (("2", 2), ("0", 0), (2.0, 2), (True, 1), (None, 1), (9, 1), ("x", 1)):
        assert app.normalize(reply(best_photo=raw), 2, "x", "review", slots, False)["best_photo"] == best, raw
    assert app.normalize(reply(best_photo=None), 0, "x", "review", slots, False)["best_photo"] == 0  # tanpa foto
    echo = {"best_photo": 1, "hooks": [{"type": "", "text": "", "vo": ""}], "shots": [{"code": "", "vo": ""}], "caption": ""}
    assert "kosong" in fails(app.normalize, echo, 1, "x", "review", slots, False)
    r = app.normalize(reply(hand="woman's hand"), 1, "x", "review", slots, False, "pria")
    hand = next(f for f in r["frames"] if f["key"] == "hand")
    assert "man's right hand" in hand["prompt"] and "woman" not in hand["prompt"]


def test_warnings_and_lint():
    r = app.normalize(reply(hooks=[{"type": "x", "text": "Parfum terbaik, link di bio", "vo": "DM aku ya"}],
                            hashtags=["#parfumori"], best_photo=0, scene="perfume bottle on marble"),
                      2, "Parfum X 50ml", "review", app.resolve_slots("review", True, 20), True)
    w = " | ".join(r["warnings"])
    assert app.BPOM_WARNING in w and "terbaik" in w and "link di bio" in w and "dm" in w and "#parfumori" in w
    assert "scene netral" in w and r["scene"] == app.FALLBACK_SCENE and "gak pegang" not in w
    assert app.FALLBACK_SCENE in r["frames"][0]["prompt"] and "Gak ada foto yang cukup bersih" in w
    assert not app.banned_hits("Harga terbarunya cek langsung di keranjang kuning, wanginya kalem")
    assert app.banned_hits("Beli di Shopee aja, chat WA") == ["shopee", "wa"]
    assert "kosong" in fails(app.normalize, {"best_photo": 1}, 1, "x", "review", app.resolve_slots("review", False, 20), False)


def test_prompt_builder():
    slots = app.resolve_slots("alasan", True, 20)
    p = app.build_prompt('Parfum {x} [[DESC]] "oke"', 2, True, True, "auto", "alasan", slots, ["analogi"])
    assert "[[" not in p.replace("[[DESC]]", "") and 'Parfum {x} [[DESC]] "oke"' in p
    assert 'Template "3 Alasan", 20 seconds' in p and "r_pick, a_hand" in p and "put a different type first): analogi" in p
    assert ", tes pakai, unboxing" in p and "about 46 words" in p and "First-person usage is allowed" in p
    assert "2 product photo(s) are attached" in p and "video of 20 seconds" in p
    p = app.build_prompt("x", 0, False, False, "auto", "review", app.resolve_slots("review", False, 10), [])
    assert "r_pick" not in p and "tes pakai" not in p and "Never write first-person usage" in p
    assert "No photos are attached" in p and "10 seconds" in p and "about 23 words" in p


def test_template_rotation():
    old = app.OUT
    with tempfile.TemporaryDirectory() as d:
        app.OUT = Path(d)
        try:
            assert app.pick_template("auto", False, 20) == "review"
            for i, t in enumerate(["review", "alasan", "pov"]):
                job = Path(d) / f"20261008-10000{i}-aaa{i}"
                job.mkdir()
                (job / "pack.json").write_text(json.dumps({"meta": {"template": t, "hook": "analogi"}}))
            assert app.pick_template("auto", False, 20) == "sinematik"  # unboxing butuh produk
            assert app.pick_template("auto", True, 20) == "unboxing"
            assert app.pick_template("auto", True, 10) == "sinematik"  # unboxing minimal 15 detik
            assert app.pick_template("pov", False, 20) == "pov" and app.pick_template("unboxing", False, 20) == "sinematik"
            assert app.pick_template("alasan", False, 10) == "sinematik"
        finally:
            app.OUT = old


def test_brief_follows_hook():
    r = {"job": "20261008-101010-ab12", "source": "Parfum X · Rp99.000", **review()}
    txt = app.brief(app.with_hook(r, 1))
    assert "\r\n" in txt and txt.count("\n") == txt.count("\r\n")
    assert "LANGKAH 1 - FRAME DI FLOW" in txt and "* Frame Hero:" in txt and "Prompt: Image 1 is the real product photo" in txt
    assert "Produk: Parfum X · Rp99.000" in txt and "Foto produk buat Flow: foto ke-2" in txt
    assert "   Ngomong: Kamu tim wangi sabun?" in txt and txt.split("kata):\r\n")[1].startswith("Kamu tim wangi sabun?")
    assert app.with_hook(r, 99) is r and "Tips: " in txt


def test_page_extraction():
    html = """<html><head><title>Parfum Lavender 50ml | Tokopedia</title>
    <meta property="og:title" content="Parfum Lavender &amp; Musk 50ml"><meta name="description" content="Wangi kalem">
    <meta property="og:image" content="https://images.example/p1.jpg"><meta name="viewport" content="x">
    <script type="application/ld+json">{"@type": "Product", "name": "Parfum Lavender"}</script>
    <script>var junk = "jangan kebaca";</script><style>.a{}</style></head>
    <body><h1>Parfum Lavender</h1><p>Rp179.000 <s>Rp250.000</s> 28%</p><img src="https://images.example/p2.webp"></body></html>"""
    p = app.page_from_html("https://shop.example/p", html)
    assert p["title"] == "Parfum Lavender 50ml | Tokopedia" and p["meta"]["og:title"] == "Parfum Lavender & Musk 50ml"
    assert "viewport" not in p["meta"] and p["meta"]["description"] == "Wangi kalem" and '"Product"' in p["ld"]
    assert "Rp179.000 Rp250.000 28%" in p["text"] and "junk" not in p["text"] and p["images"] == ["https://images.example/p2.webp"]
    assert "https://" in fails(app.fetch_page, "vt.tokopedia.com/t/abc")


def test_import_uses_ai_extraction():
    calls = []
    real_chat, real_imgs, real_settings = app.zai_chat, app.fetch_images, app.load_settings
    try:
        app.load_settings = lambda: {"key": "k", "model": "glm-4.6v"}
        app.fetch_images = lambda urls, ref: calls.append(urls) or ["data:image/jpeg;base64,AAAA"]
        app.zai_chat = lambda *a, **k: ('{"is_product_page": true, "name": "Parfum Lavender", "price": "Rp179.000", '
                                        '"discount": "28%", "images": ["https://images.example/p2.webp", "https://evil/x"]}', "stop")
        page = {"url": "https://shop.example/p", "title": "t", "meta": {"og:image": "https://images.example/p1.jpg"},
                "ld": "", "text": "Rp179.000", "images": ["https://images.example/p2.webp"]}
        r = app.import_product({"page": page})
        assert r["name"] == "Parfum Lavender" and r["price"] == "Rp179.000" and r["discount"] == "28%"
        assert calls == [["https://images.example/p2.webp"]] and r["images"] and r["url"] == "https://shop.example/p"
        app.zai_chat = lambda *a, **k: ('{"is_product_page": false, "name": ""}', "stop")
        assert "bukan halaman produk" in fails(app.import_product, {"page": page})
        app.load_settings = lambda: {"key": "", "model": "glm-4.6v"}
        assert "API key" in fails(app.import_product, {"page": page})
    finally:
        app.zai_chat, app.fetch_images, app.load_settings = real_chat, real_imgs, real_settings


def test_patterns_and_helpers():
    assert app.BPOM_RE.search("BPOM NA18201200123") and not app.BPOM_RE.search("NA1820")
    assert app.JOB_RE.fullmatch("20261008-101010-ab12")
    for job in ("..", "CON", "x/../y", "20261008-101010-ab12/../x"):
        assert not app.JOB_RE.fullmatch(job)
    assert app.BRIEF_RE.fullmatch("/hasil/20261008-101010-ab12/brief.txt")
    assert not app.BRIEF_RE.fullmatch("/hasil/20261008-101010-ab12/pack.json")
    assert not app.BRIEF_RE.fullmatch("/hasil/../settings.json")
    assert not app.SCENE_BANNED.search(app.FALLBACK_SCENE) and app.SCENE_BANNED.search("a Bottle on marble")
    assert app.product_info({"product": {"noun": "The Perfume Bottle"}, "hand": "A man's hand"}, "auto")["noun"] == "perfume bottle"
    assert app.product_info({}, "pria")["hand"] == app.HANDS["pria"]
    assert app.sentence("abc") == "Abc." and app.num(2.5) == "2,5" and app.num(24.0) == "24"


def test_settings_and_photos():
    assert app.public({"key": "abcdef1234", "model": "glm-4.6v"})["key_hint"] == "...1234"
    pub = app.public({"key": "", "model": "glm-4.6v"})
    assert pub["durations"] == app.DURATIONS and ["alasan", "3 Alasan", False, 15] in pub["templates"]
    assert app.check_photo(JPEG, 1) == JPEG
    for bad in ("data:image/png;base64,iVBORw0KGgo=", "data:image/jpeg;base64,bm90IGFuIGltYWdl", "x", JPEG + "!"):
        assert "Foto ke-2" in fails(app.check_photo, bad, 2), bad


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
