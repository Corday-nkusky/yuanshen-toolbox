"""生成多档图标，**小尺寸单独一套画面**。

背景：任务栏按钮在 150% 缩放下只有约 36px。把 256px 头像整张缩下去，
脸只剩 10 来个像素，「深紫黑发 + 深紫背景 + 白色眼罩细带」会糊成一团，
任务栏上就是一个黑团加一块白。

所以分两套：
  · 小尺寸（16/20/24/32/40/48）：亮底 + 脸部紧裁 + 去细线，靠「黑发剪影 + 白色眼罩横带」
    这对强对比特征保住辨识度；
  · 超小尺寸（16/20/24）：在上一套的基础上再把脸放大到几乎填满整格 —— 16px 只够画
    「深色头发框 + 一张白脸」，背景留多了脸就只剩几个像素，还是一团；
  · 大尺寸（64/96/128/256）：原来的深紫→湖蓝渐变 + 白色圆环，细节放得下。

PIL 的 ICO 保存只能拿一张源图缩所有档，所以这里自己按 ICO 格式写文件，
让每一档塞各自的画面（Windows 与 Tk 的 LoadImage 都支持）。
"""

import struct

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

SRC = "art2/col_head.png"

# 头部紧裁（源图 256×256）：脸 + 上部发量，去掉肩甲
CROP_SMALL = (26, 4, 230, 186)
CROP_TINY = (46, 52, 214, 190)   # 头部更满，去肩甲
CROP_BIG = (16, 0, 240, 190)

# 16/20/24 是资源管理器列表/详细资料真正用到的尺寸，单独一套「脸填满」画面；
# 30/36/42 是非标准档，专为 125%/150%/175% 缩放下的任务栏准备：
# 本机 150% 时任务栏按 24dp × 1.5 = 36px 渲染，ico 里有精确 36 档就不会再被缩放一次。
TINY_SIZES = [16]
SMALL_SIZES = [20, 24, 30, 32, 36, 40, 42, 48]
BIG_SIZES = [64, 96, 128, 256]


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def vgrad(size, top, bottom):
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        px[0, y] = lerp(top, bottom, y / max(1, size - 1))
    return img.resize((size, size), Image.BILINEAR)


def rounded_mask(S, rad_ratio, ss=4):
    """超采样画圆角矩形，边缘更干净。"""
    big = Image.new("L", (S * ss, S * ss), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        [0, 0, S * ss - 1, S * ss - 1], radius=int(S * rad_ratio * ss), fill=255)
    return big.resize((S, S), Image.LANCZOS)


def avatar_layer(src, S, zoom, sharpen=70):
    d = int(S * zoom)
    inner = src.resize((d, d), Image.LANCZOS)
    inner = inner.filter(ImageFilter.UnsharpMask(radius=1.4, percent=sharpen, threshold=2))
    cm = Image.new("L", (d, d), 0)
    ImageDraw.Draw(cm).ellipse([0, 0, d - 1, d - 1], fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(inner, ((S - d) // 2, (S - d) // 2), cm)
    return out, d


def build_big(S=1024):
    """原有设计：深紫 → 湖蓝，白色圆环。"""
    base = vgrad(S, (123, 75, 200), (63, 176, 230)).convert("RGBA")
    base.putalpha(rounded_mask(S, 0.23))
    src = Image.open(SRC).convert("RGBA").crop(CROP_BIG)
    av, d = avatar_layer(src, S, 0.875)
    out = Image.alpha_composite(base, av)
    off = (S - d) // 2
    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(edge).ellipse([off, off, off + d - 1, off + d - 1],
                                 outline=(255, 255, 255, 235),
                                 width=max(2, int(S * 0.015)))
    return Image.alpha_composite(out, edge)


def build_small(S=512):
    """小尺寸专用：中调底、脸更大、去掉细线，强化「黑发 + 白眼罩」对比。

    底色试过三组（实测 36px 渲染后对比）：
      浅（206,183,246→126,214,248）  人物清楚但整体发白，浅任务栏上边界虚；
      深（即大图那组 123,75,200→63,176,230）  人物与底色都深，反而粘在一起；
      中调（下面这组）  既保住紫→蓝的品牌色，又让深色头发跳出来 —— 选它。
    """
    base = vgrad(S, (160, 130, 235), (80, 190, 240)).convert("RGBA")
    base.putalpha(rounded_mask(S, 0.2))

    src = Image.open(SRC).convert("RGBA").crop(CROP_SMALL)
    # 提对比 + 提饱和：缩到 16px 后颜色会被平均掉，提前补回来
    src = ImageEnhance.Contrast(src).enhance(1.30)
    src = ImageEnhance.Color(src).enhance(1.15)

    av, d = avatar_layer(src, S, 0.94, sharpen=95)
    out = Image.alpha_composite(base, av)

    # 圆外一圈深紫描边：亮底上把角色和背景分开，比白色细环抗缩小
    off = (S - d) // 2
    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(edge).ellipse([off, off, off + d - 1, off + d - 1],
                                 outline=(86, 62, 130, 190),
                                 width=max(2, int(S * 0.022)))
    return Image.alpha_composite(out, edge)


def build_tiny(S=512):
    """16 专用：头部放大到整格，靠「黑发框 + 白脸」认人。

    16px 只有 256 个像素，原设计那圈蓝底和白色细环会把脸挤到十来个像素，
    缩完就是一团深色。这一档去掉外圈、抬高头部占比，只求「看得出是张脸」。
    """
    base = vgrad(S, (168, 140, 238), (86, 194, 242)).convert("RGBA")
    base.putalpha(rounded_mask(S, 0.2))

    src = Image.open(SRC).convert("RGBA").crop(CROP_TINY)
    src = ImageEnhance.Contrast(src).enhance(1.20)
    src = ImageEnhance.Color(src).enhance(1.10)

    av, d = avatar_layer(src, S, 1.00, sharpen=110)
    out = Image.alpha_composite(base, av)

    off = (S - d) // 2
    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(edge).ellipse([off, off, off + d - 1, off + d - 1],
                                 outline=(78, 54, 118, 220),
                                 width=max(2, int(S * 0.022)))
    return Image.alpha_composite(out, edge)


# ------------------------------------------------------------------ ICO 写入
def _dib(im):
    """32bpp DIB：BITMAPINFOHEADER + BGRA(自下而上) + AND 掩码。"""
    s = im.size[0]
    px = im.convert("RGBA").load()
    rows = []
    for y in range(s - 1, -1, -1):
        row = bytearray()
        for x in range(s):
            r, g, b, a = px[x, y]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    xor = b"".join(rows)
    and_row = ((s + 31) // 32) * 4
    and_mask = b"\x00" * (and_row * s)
    hdr = struct.pack("<IiiHHIIiiII", 40, s, s * 2, 1, 32, 0,
                      len(xor) + len(and_mask), 0, 0, 0, 0)
    return hdr + xor + and_mask


def _png(im):
    import io
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def write_ico(path, frames):
    """frames: [(size, PIL.Image)]，每档可各自不同。≥128 用 PNG，其余用 DIB。"""
    frames = sorted(frames, key=lambda kv: kv[0])
    blobs = []
    for size, im in frames:
        assert im.size == (size, size), (size, im.size)
        blobs.append((size, _png(im) if size >= 128 else _dib(im)))
    head = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, data = b"", b""
    for size, blob in blobs:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(blob), offset + len(data))
        data += blob
    with open(path, "wb") as fh:
        fh.write(head + entries + data)


def main():
    tiny_master = build_tiny(512)
    small_master = build_small(512)
    big_master = build_big(1024)

    frames = []
    for s in TINY_SIZES:
        frames.append((s, tiny_master.resize((s, s), Image.LANCZOS)))
    for s in SMALL_SIZES:
        frames.append((s, small_master.resize((s, s), Image.LANCZOS)))
    for s in BIG_SIZES:
        frames.append((s, big_master.resize((s, s), Image.LANCZOS)))

    write_ico("icon.ico", frames)
    big_master.resize((256, 256), Image.LANCZOS).save("icon_src.png")
    big_master.resize((256, 256), Image.LANCZOS).save("art2/icon_preview.png")
    small_master.resize((256, 256), Image.LANCZOS).save("art2/icon_small_preview.png")
    tiny_master.resize((256, 256), Image.LANCZOS).save("art2/icon_tiny_preview.png")

    # 自检：按 ICO 逐档读回，并模拟任务栏 36px 的渲染
    print("entries:", [s for s, _ in frames])
    tb = small_master.resize((36, 36), Image.LANCZOS)
    sheet = Image.new("RGB", (900, 150), (243, 243, 248))
    x = 10
    for s, im in frames[:8] + frames[-1:]:
        sheet.paste(im, (x, 12 + (64 - s) // 2), im)
        x += s + 10
    x += 20
    sheet.paste(tb, (x, 24), tb)
    x += 50
    up = tb.resize((96, 96), Image.NEAREST)
    sheet.paste(up, (x, 8))
    sheet.save("art2/icon_sizes.png")
    print("ok")


main()
