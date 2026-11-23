"""Draws the app icon (a fan of image cards) and writes icon.png and a multi-size icon.ico.
Run:  python make_icon.py     (needs Pillow)"""
from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vgrad(w, h, top, bottom):
    col = Image.new("RGB", (1, h))
    for y in range(h):
        col.putpixel((0, y), lerp(top, bottom, y / max(1, h - 1)))
    return col.resize((w, h))


def rounded_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return m


def card(w, h, top, bottom, glyph=False):
    """A photo print: white frame, gradient picture, optional sun-and-mountains."""
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    base.paste(Image.new("RGBA", (w, h), (250, 250, 252, 255)), (0, 0), rounded_mask(w, h, 46))
    pad = 30
    iw, ih = w - 2 * pad, h - 2 * pad - 36   # thicker white edge at the bottom, like a print
    pic = vgrad(iw, ih, top, bottom).convert("RGBA")
    if glyph:
        d = ImageDraw.Draw(pic)
        d.ellipse([iw * 0.60, ih * 0.14, iw * 0.60 + 120, ih * 0.14 + 120], fill=(255, 236, 170, 255))
        d.polygon([(0, ih), (iw * 0.34, ih * 0.40), (iw * 0.62, ih)], fill=(24, 36, 84, 255))
        d.polygon([(iw * 0.30, ih), (iw * 0.66, ih * 0.54), (iw, ih)], fill=(40, 62, 128, 255))
    base.paste(pic, (pad, pad), rounded_mask(iw, ih, 26))
    return base


def place(canvas, img, centre, angle, shadow=True):
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
    x, y = centre[0] - img.width // 2, centre[1] - img.height // 2
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sh.putalpha(img.getchannel("A").point(lambda a: int(a * 0.55)))
        sh = sh.filter(ImageFilter.GaussianBlur(22))
        canvas.alpha_composite(sh, (x + 8, y + 26))
    canvas.alpha_composite(img, (x, y))


def draw():
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bg = vgrad(S, S, (44, 54, 120), (14, 20, 48)).convert("RGBA")
    canvas.paste(bg, (0, 0), rounded_mask(S, S, 230))
    cards = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    w, h = 560, 470
    place(cards, card(w, h, (60, 200, 190), (20, 110, 140)), (430, 480), 15)
    place(cards, card(w, h, (255, 150, 110), (210, 70, 130)), (600, 500), -9)
    place(cards, card(w, h, (70, 90, 200), (250, 150, 90), glyph=True), (512, 560), 0)
    # keep everything inside the rounded square
    cards.putalpha(ImageChops.multiply(cards.getchannel("A"), rounded_mask(S, S, 230)))
    canvas.alpha_composite(cards)
    return canvas


if __name__ == "__main__":
    img = draw()
    img.resize((512, 512), Image.LANCZOS).save("icon.png")
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    img.save("icon.ico", format="ICO", sizes=sizes)
    print("wrote icon.png and icon.ico", sizes)
