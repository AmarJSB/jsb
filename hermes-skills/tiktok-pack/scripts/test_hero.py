# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Self-check tanpa jaringan & tanpa rembg. Jalankan: uv run test_hero.py"""
import tempfile
from pathlib import Path

from PIL import Image

import hero

BG, PROD = (40, 120, 200), (200, 30, 90)


def near(a, b, tol):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_place():
    for layout, (hm, wm, base, cx) in hero.LAYOUTS.items():
        for cw, ch in ((300, 900), (1000, 300)):  # botol tinggi, kotak lebar
            _, (x, y, w, h) = hero.place(cw, ch, layout)
            assert h <= hm * hero.H + 1 and w <= wm * hero.W + 1, (layout, w, h)
            assert y + h == round(base * hero.H) and abs(x + w / 2 - cx * hero.W) <= 1, layout
            assert 0 <= x and x + w <= hero.W and 0 <= y, layout


def test_composite_keeps_real_pixels():
    cut = Image.new("RGBA", (200, 600), PROD + (255,))
    img, _ = hero.composite(Image.new("RGB", (960, 1728), BG), cut, grain=False)
    _, (x, y, w, h) = hero.place(200, 600)
    assert img.size == (hero.W, hero.H)
    assert near(img.getpixel((x + w // 2, y + h // 2)), PROD, 12)  # cuma sedikit tint, warna asli tetap
    assert img.getpixel((5, 5)) == BG
    assert sum(img.getpixel((x + w // 2, y + h + 3))) < sum(BG)  # bayangan di bawah dasar
    shiny, _ = hero.composite(Image.new("RGB", (960, 1728), BG), cut, glossy=True, grain=False)
    assert shiny.getpixel((x + w // 2, y + h + 25)) != img.getpixel((x + w // 2, y + h + 25))  # ada pantulan
    noisy, _ = hero.composite(Image.new("RGB", (960, 1728), BG), cut)
    assert near(noisy.getpixel((5, 5)), BG, 25)  # grain halus, gak ngubah warna


def test_hand_layout_and_reference():
    bg = Image.new("RGB", (960, 1728), BG)
    img, _ = hero.composite(bg, Image.new("RGBA", (200, 600), PROD + (255,)), "hand", grain=False)
    _, (x, y, w, h) = hero.place(200, 600, "hand")
    assert img.getpixel((x + w // 2, y + h + 20)) == BG  # melayang: tanpa bayangan meja
    ref = hero.reference(Image.new("RGBA", (200, 600), PROD + (255,)))
    assert ref.size == (1536, 1536) and ref.getpixel((5, 5)) == (240, 240, 240) and ref.getpixel((768, 768)) == PROD


def test_clean():
    im = Image.new("RGBA", (400, 400), (255, 255, 255, 0))
    im.paste((10, 20, 30, 255), (100, 50, 300, 350))
    cut, warns = hero.clean(im)
    assert abs(cut.width - 200) <= 4 and abs(cut.height - 300) <= 4 and not warns
    edge = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    edge.paste((10, 20, 30, 255), (100, 0, 300, 350))
    assert hero.clean(edge)[1]  # nempel tepi atas -> warning
    try:
        hero.clean(Image.new("RGBA", (400, 400), (0, 0, 0, 0)))
        raise AssertionError("foto kosong harus gagal")
    except hero.Fail:
        pass


def test_scene_guard():
    assert hero.BANNED.search("perfume bottle on marble")
    assert hero.BANNED.search("Botol di meja")
    assert not hero.BANNED.search("white marble tabletop with rose petals, warm sunset light")
    assert hero.GLOSSY.search("polished black acrylic surface") and not hero.GLOSSY.search("rough linen cloth")


def test_env_and_key():
    with tempfile.TemporaryDirectory() as d:
        env = Path(d) / ".env"
        env.write_text('# c\nexport GLM_API_KEY="abc 123"\nOTHER=x # note\nEMPTY=\n', encoding="utf-8")
        assert hero.read_env(env) == {"GLM_API_KEY": "abc 123", "OTHER": "x", "EMPTY": ""}
        old = {k: hero.os.environ.pop(k, None) for k in hero.KEY_VARS + ("HERMES_HOME",)}
        try:
            hero.os.environ["HERMES_HOME"] = d
            assert hero.api_key() == "abc 123"
            env.write_text("", encoding="utf-8")
            try:
                hero.api_key()
                raise AssertionError("tanpa key harus gagal")
            except hero.Fail:
                pass
        finally:
            for k, v in old.items():
                hero.os.environ.pop(k, None)
                if v is not None:
                    hero.os.environ[k] = v


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
