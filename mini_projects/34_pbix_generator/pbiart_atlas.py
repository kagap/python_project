"""Draws the pictures of the `vintage` style: an aged-paper page, a hand-lettered title, ink icons for the pages, a small satellite and a dotted orbit.
Everything is drawn here with Pillow and numpy, there are no downloaded images. Usage: python pbiart_atlas.py OUT_DIR "Title" "Second line"."""
import math
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

INK = (22, 19, 17, 255)
SS = 3                      # supersampling for the ink drawings
FONTS = Path("C:/Windows/Fonts")


def paper(path, w=2560, h=1440, seed=7):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.zeros((h, w, 3), dtype=np.float32)
    base[:] = (241, 238, 229)
    cloud = np.zeros((h, w), dtype=np.float32)
    for size, amp in ((5, 3.5), (14, 3), (40, 3.5), (120, 3.5), (400, 3)):
        small = rng.random((max(h // size, 2), max(w // size, 2))).astype(np.float32)
        cloud += (np.asarray(Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), dtype=np.float32) / 255 - 0.5) * amp * 2
    base += cloud[..., None] * np.array([1.0, 1.0, 1.1], dtype=np.float32)
    # stains
    for _ in range(9):
        cx, cy, r = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(150, 520)
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
        m = np.clip(1 - d, 0, 1) ** 1.6 * rng.uniform(0.05, 0.13)
        base -= m[..., None] * np.array([26, 34, 46], dtype=np.float32)
    # a worn edge
    nx, ny = (xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)
    edge = np.clip((np.maximum(np.abs(nx), np.abs(ny)) - 0.7) / 0.3, 0, 1) ** 2.4
    grain = np.asarray(Image.fromarray((rng.random((h // 4, w // 4)) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), dtype=np.float32) / 255
    edge = edge * (0.7 + 0.5 * grain)
    base -= edge[..., None] * np.array([30, 38, 52], dtype=np.float32)
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(70):      # scratches and hairs
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        ang, ln = rng.uniform(0, math.tau), rng.uniform(60, 420)
        pts = []
        for k in range(8):
            pts.append((x + math.cos(ang + rng.normal(0, 0.18)) * ln * k / 7, y + math.sin(ang + rng.normal(0, 0.18)) * ln * k / 7))
        light = rng.random() < 0.6
        d.line(pts, fill=(255, 252, 244, int(rng.uniform(60, 120))) if light else (110, 94, 76, int(rng.uniform(25, 55))), width=1 if rng.random() < 0.8 else 2)
    for _ in range(420):     # foxing: small brown speckles
        x, y, r = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(1, 4)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(120, 92, 62, int(rng.uniform(20, 70))))
    img = img.filter(ImageFilter.GaussianBlur(1.0))
    img.save(path, "JPEG", quality=88)


def wob(points, amp=1.6, seed=0):
    rnd = random.Random(seed)
    return [(x + rnd.uniform(-amp, amp), y + rnd.uniform(-amp, amp)) for x, y in points]


def circle_pts(cx, cy, r, n=90, a0=0.0, a1=math.tau, ry=None, rot=0.0):
    ry = r if ry is None else ry
    pts = []
    for i in range(n + 1):
        a = a0 + (a1 - a0) * i / n
        x, y = r * math.cos(a), ry * math.sin(a)
        pts.append((cx + x * math.cos(rot) - y * math.sin(rot), cy + x * math.sin(rot) + y * math.cos(rot)))
    return pts


class Ink:
    """A transparent square or rectangle drawn at SS times the size; lines are wobbled a little so that they look like pen strokes."""

    def __init__(self, w, h, seed=1):
        self.w, self.h, self.seed = w, h, seed
        self.im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.im)

    def line(self, pts, width=7, amp=1.3, closed=False):
        self.seed += 1
        p = [(x * SS, y * SS) for x, y in pts]
        if closed:
            p = p + [p[0]]
        p = wob(p, amp * SS, self.seed)
        self.d.line(p, fill=INK, width=int(width * SS), joint="curve")
        for x, y in (p[0], p[-1]):
            r = width * SS / 2
            self.d.ellipse((x - r, y - r, x + r, y + r), fill=INK)

    def circle(self, cx, cy, r, width=7, ry=None, rot=0.0, a0=0.0, a1=math.tau):
        self.line(circle_pts(cx, cy, r, 96, a0, a1, ry, rot), width)

    def dot(self, x, y, r):
        self.d.ellipse(((x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS), fill=INK)

    def sparkle(self, x, y, r, width=5):
        self.line([(x, y - r), (x, y + r)], width, 0.4)
        self.line([(x - r, y), (x + r, y)], width, 0.4)

    def poly(self, pts, width=7, closed=True):
        self.line(pts, width, 1.0, closed)

    def result(self):
        return self.im.resize((self.w, self.h), Image.LANCZOS)


def ring_icon(ink):
    cx, cy = 150, 150
    ink.circle(cx, cy, 58, 8)
    # the ring: the front half in full, the back half only outside the planet
    ink.line(circle_pts(cx, cy, 118, 60, 0.05, math.pi - 0.05, 30, -0.35), 8)
    back = circle_pts(cx, cy, 118, 90, math.pi + 0.05, math.tau - 0.05, 30, -0.35)
    seg = []
    for x, y in back:
        if math.hypot(x - cx, y - cy) > 62:
            seg.append((x, y))
        elif len(seg) > 1:
            ink.line(seg, 8)
            seg = []
        else:
            seg = []
    if len(seg) > 1:
        ink.line(seg, 8)
    ink.line([(cx - 40, cy - 12), (cx + 40, cy - 28)], 4, 0.4)
    ink.sparkle(70, 68, 15)
    ink.sparkle(236, 76, 11)
    ink.sparkle(224, 232, 13)


def telescope_icon(ink):
    ink.poly([(70, 150), (92, 118), (212, 176), (192, 210)], 8)
    ink.line([(212, 176), (232, 186)], 8)
    ink.line([(76, 140), (60, 128)], 8)
    ink.line([(150, 175), (122, 252)], 8)
    ink.line([(150, 175), (178, 252)], 8)
    ink.line([(150, 175), (150, 252)], 8)
    ink.line([(112, 126), (124, 160)], 5, 0.4)
    ink.sparkle(60, 70, 14)
    ink.sparkle(236, 84, 11)


def worlds_icon(ink):
    ink.circle(78, 200, 30, 8)
    ink.circle(150, 184, 46, 8)
    ink.circle(208, 128, 22, 8)
    ink.line([(110, 170), (190, 168)], 4, 0.5)
    ink.line([(106, 188), (194, 186)], 4, 0.5)
    ink.line([(112, 204), (188, 202)], 4, 0.5)
    ink.sparkle(70, 70, 15)
    ink.sparkle(236, 214, 11)


def star_icon(ink):
    pts = []
    for i in range(10):
        r = 76 if i % 2 == 0 else 32
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((178 + r * math.cos(a) * 0.9, 120 + r * math.sin(a) * 0.9))
    ink.poly(pts, 8)
    for t in range(6):
        ink.dot(60 + t * 16, 238 - t * 18, 5)
    ink.sparkle(62, 62, 13)


def table_icon(ink):
    ink.poly([(62, 78), (238, 78), (238, 222), (62, 222)], 8)
    ink.line([(62, 118), (238, 118)], 6)
    ink.line([(62, 160), (238, 160)], 5)
    ink.line([(130, 78), (130, 222)], 5)
    ink.line([(190, 78), (190, 222)], 5)


def info_icon(ink):
    ink.dot(150, 78, 13)
    ink.line([(150, 112), (150, 222)], 16, 0.8)
    ink.line([(124, 112), (150, 112)], 10, 0.5)
    ink.line([(120, 222), (180, 222)], 10, 0.5)


def icon(path, draw, seed):
    ink = Ink(300, 300, seed)
    ink.circle(150, 150, 140, 9)
    draw(ink)
    ink.result().save(path)


def satellite(path):
    ink = Ink(260, 260, 40)
    ink.poly([(98, 110), (138, 82), (170, 128), (130, 156)], 8)
    for k in range(3):    # the solar panels
        ink.poly([(30 + k * 6, 156 + k * 10 - 36), (84 + k * 6, 126 + k * 10 - 36), (100 + k * 6, 150 + k * 10 - 36), (46 + k * 6, 180 + k * 10 - 36)], 5)
    ink.poly([(150, 150), (204, 118), (230, 160), (176, 194)], 6)
    ink.line([(176, 140), (190, 172)], 4, 0.4)
    ink.line([(118, 96), (88, 52)], 6)
    ink.circle(80, 44, 16, 6, 9, 0.5)
    ink.dot(80, 44, 3)
    ink.sparkle(224, 60, 14)
    ink.sparkle(40, 220, 12)
    ink.result().save(path)


def trail(path):
    ink = Ink(560, 150, 60)
    pts = circle_pts(280, 150, 270, 40, math.pi + 0.35, math.tau - 0.12, 118)
    for i, (x, y) in enumerate(pts):
        ink.dot(x, y, 4 + 3.4 * (i / len(pts)))
    x, y = pts[-1]
    ink.circle(x + 6, y - 6, 20, 6)
    ink.line([(x - 14, y - 6), (x + 26, y - 18)], 4, 0.4)
    ink.result().save(path)


def title(path, line1, line2):
    w, h = 1300, 520
    mask = Image.new("L", (w * 2, h * 2), 0)
    d = ImageDraw.Draw(mask)
    f1 = ImageFont.truetype(str(FONTS / "gabriola.ttf"), 400)
    f2 = ImageFont.truetype(str(FONTS / "gabriola.ttf"), 170)
    b1 = d.textbbox((0, 0), line1, font=f1)
    b2 = d.textbbox((0, 0), line2, font=f2)
    y1 = 20
    d.text(((w * 2 - (b1[2] - b1[0])) / 2 - b1[0], y1 - b1[1]), line1, font=f1, fill=255)
    y2 = y1 + (b1[3] - b1[1]) + 30
    d.text(((w * 2 - (b2[2] - b2[0])) / 2 - b2[0], y2 - b2[1]), line2, font=f2, fill=255)
    # a small flourish under the second line
    ly = y2 + (b2[3] - b2[1]) + 40
    for k, off in enumerate((0, 22)):
        d.line([(w * 0.62 - off * 3, ly + off), (w * 1.38 + off * 3, ly + off)], fill=255, width=9 - 3 * k)
    mask = mask.filter(ImageFilter.GaussianBlur(2.2)).point(lambda v: 255 if v > 120 else int(v * 2.1))
    mask = mask.resize((w, h), Image.LANCZOS)
    im = Image.new("RGBA", (w, h), (22, 19, 17, 0))
    im.putalpha(mask)
    bbox = im.getbbox()
    im.crop((max(bbox[0] - 10, 0), max(bbox[1] - 6, 0), min(bbox[2] + 10, w), min(bbox[3] + 6, h))).save(path)


def main(out, line1="Exoplanets", line2="A Star Atlas"):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    paper(out / "paper.jpg")
    title(out / "title.png", line1, line2)
    for i, (name, fn) in enumerate((("atlas", ring_icon), ("methods", telescope_icon), ("worlds", worlds_icon), ("neighbours", star_icon), ("details", table_icon), ("about", info_icon))):
        icon(out / f"icon_{name}.png", fn, 10 + i)
    satellite(out / "mascot.png")
    trail(out / "trail.png")
    print("wrote", sorted(p.name for p in out.iterdir()))


if __name__ == "__main__":
    main(sys.argv[1], *(sys.argv[2:4] if len(sys.argv) > 2 else ()))
