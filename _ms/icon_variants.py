"""对比几版图标构图，输出拼图预览。"""

from PIL import Image, ImageDraw, ImageFilter

SRC = "art2/col_head.png"


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def vgrad(size, top, bottom):
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        px[0, y] = lerp(top, bottom, y / max(1, size - 1))
    return img.resize((size, size), Image.BILINEAR)


def build(size, top, bottom, crop, zoom, ring_alpha=235):
    S = size
    base = vgrad(S, top, bottom).convert("RGBA")
    rad = int(S * 0.23)
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, S - 1, S - 1], radius=rad, fill=255)
    base.putalpha(m)

    src = Image.open(SRC).convert("RGBA")
    src = src.crop(crop) if crop else src
    d = int(S * zoom)
    off = (S - d) // 2
    inner = src.resize((d, d), Image.LANCZOS)
    inner = inner.filter(ImageFilter.UnsharpMask(radius=1.8, percent=70, threshold=2))
    cm = Image.new("L", (d, d), 0)
    ImageDraw.Draw(cm).ellipse([0, 0, d - 1, d - 1], fill=255)
    av = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    av.paste(inner, (off, off), cm)
    out = Image.alpha_composite(base, av)

    e = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(e).ellipse([off, off, off + d - 1, off + d - 1],
                              outline=(255, 255, 255, ring_alpha), width=max(2, int(S * 0.015)))
    return Image.alpha_composite(out, e)


variants = [
    ("A 粉紫·整图", (255, 111, 164), (155, 111, 227), None, 0.855),
    ("B 深紫蓝·整图", (123, 75, 200), (63, 176, 230), None, 0.855),
    ("C 粉紫·头部", (255, 111, 164), (155, 111, 227), (18, 0, 238, 190), 0.90),
    ("D 深紫蓝·头部", (123, 75, 200), (63, 176, 230), (18, 0, 238, 190), 0.90),
]

S = 512
sheet = Image.new("RGB", (S * len(variants), S + 96), (250, 250, 252))
dr = ImageDraw.Draw(sheet)
for i, (name, top, bottom, crop, zoom) in enumerate(variants):
    img = build(S, top, bottom, crop, zoom)
    sheet.paste(img, (i * S, 0), img)
    # 下方贴 48 / 32 / 16 三档，检查小尺寸可辨性
    x = i * S + 10
    for ps in (48, 32, 16):
        small = img.resize((ps, ps), Image.LANCZOS)
        sheet.paste(small, (x, S + 24), small)
        x += ps + 10
    dr.text((i * S + 10, S + 2), name, fill=(30, 30, 40))
sheet.save("art2/icon_variants.png")
print("ok")
