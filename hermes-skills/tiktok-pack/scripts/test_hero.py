# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Self-check tanpa jaringan & tanpa rembg. Jalankan: uv run test_hero.py"""
import tempfile
from pathlib import Path

from PIL import Image

import hero

BASE = round(hero.BASE_Y * hero.H)


def test_place():
    for cw, ch in ((300, 900), (1000, 300)):  # botol tinggi, kotak lebar
        _, (x, y, w, h) = hero.place(cw, ch)
        assert h <= hero.H_MAX * hero.H + 1 and w <= hero.W_MAX * hero.W + 1, (w, h)
        assert y + h == BASE and abs(x + w / 2 - hero.W / 2) <= 1


def test_composite_keeps_real_pixels():
    bg_color, prod_color = (40, 120, 200), (200, 30, 90)
    img, _ = hero.composite(Image.new("RGB", (960, 1728), bg_color), Image.new("RGBA", (200, 600), prod_color + (255,)))
    _, (x, y, w, h) = hero.place(200, 600)
    assert img.size == (hero.W, hero.H)
    assert img.getpixel((x + w // 2, y + h // 2)) == prod_color
    assert img.getpixel((5, 5)) == bg_color
    assert sum(img.getpixel((x + w // 2, y + h + 3))) < sum(bg_color)  # bayangan di bawah dasar


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
