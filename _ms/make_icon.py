"""生成正式图标：深紫→湖蓝渐变底 + 哥伦比娅头部圆形裁切。

图源：biligame 原神 wiki「哥伦比娅头像.png」原图 256×256 RGBA
（art2/col_head.png），不再从 106px 小图放大。
"""

from PIL import Image, ImageDraw, ImageFilter

SRC = "art2/col_head.png"
CROP = (16, 0, 240, 190)       # 只取头部，去掉肩甲，小尺寸更可辨
S = 1024                       # 工作画布
ZOOM = 0.875                   # 圆直径占画布比例


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def vgrad(size, top, bottom):
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        px[0, y] = lerp(top, bottom, y / max(1, size - 1))
    return img.resize((size, size), Image.BILINEAR)


def main():
    base = vgrad(S, (123, 75, 200), (63, 176, 230)).convert("RGBA")   # 薰衣草 → 湖蓝
    rad = int(S * 0.23)
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, S - 1, S - 1], radius=rad, fill=255)
    base.putalpha(m)

    d = int(S * ZOOM)
    off = (S - d) // 2
    src = Image.open(SRC).convert("RGBA").crop(CROP)
    inner = src.resize((d, d), Image.LANCZOS)
    inner = inner.filter(ImageFilter.UnsharpMask(radius=1.8, percent=70, threshold=2))
    cm = Image.new("L", (d, d), 0)
    ImageDraw.Draw(cm).ellipse([0, 0, d - 1, d - 1], fill=255)
    av = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    av.paste(inner, (off, off), cm)
    out = Image.alpha_composite(base, av)

    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(edge).ellipse([off, off, off + d - 1, off + d - 1],
                                 outline=(255, 255, 255, 235), width=max(2, int(S * 0.015)))
    out = Image.alpha_composite(out, edge)

    out.save("icon_src.png")
    out.resize((256, 256), Image.LANCZOS).save(
        "icon.ico", format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    out.resize((256, 256), Image.LANCZOS).save("art2/icon_preview.png")

    # 小尺寸自检
    sheet = Image.new("RGB", (420, 120), (250, 250, 252))
    x = 10
    big = out.resize((96, 96), Image.LANCZOS)
    sheet.paste(big, (x, 12), big)
    x += 106
    for ps in (48, 32, 24, 16):
        s = out.resize((ps, ps), Image.LANCZOS)
        sheet.paste(s, (x, 12 + (96 - ps) // 2), s)
        x += ps + 8
    sheet.save("art2/icon_sizes.png")
    print("ok")


main()
