#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「古汉语词典转换器」的 App 图标（.icns）。

设计概念：取「词典」二字的「词」，用一个箭头把它「变成」「词典」，
直观表达本工具做的事——把某种源（一个字/词）转换成 macOS 词典（.dictionary）。

用法：
    python3 make_icon.py [输出目录，默认脚本所在目录]

依赖：Pillow；系统字体 /System/Library/Fonts/Supplemental/Songti.ttc（宋体，衬线，书卷气）。
生成流程：渲染 1024×1024 母图 -> sips 缩放出各尺寸 -> iconutil 合成 app.icns。
"""
import os
import subprocess
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("需要 Pillow：pip install Pillow")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else SCRIPT_DIR
os.makedirs(OUT, exist_ok=True)
SIZE = 1024

FONT = "/System/Library/Fonts/Supplemental/Songti.ttc"
# 宋体是 ttc 合集，index 0 通常为 Regular
FONT_INDEX = 0

# 配色
C_TOP = (38, 138, 128)      # 上方青绿
C_BOT = (10, 58, 55)        # 下方墨绿
C_HI = (90, 190, 178)       # 高光
C_STROKE = (120, 210, 198)  # 内描边
C_TEXT = (245, 237, 214)    # 米白（字）
C_SHADOW = (6, 30, 28)      # 字阴影


def vgrad(w, h, top, bot):
    """竖向线性渐变图。"""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / (h - 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def radial_alpha(w, h, center, radius, color):
    """径向高光：返回 RGBA 图，alpha 为 中心->边缘 的衰减（色值恒为 color）。"""
    a = Image.new("L", (w, h), 0)
    pa = a.load()
    cx, cy = center
    r2 = radius * radius
    for y in range(h):
        for x in range(w):
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            if d2 < r2:
                pa[x, y] = int(255 * (1 - d2 / r2) ** 1.4)
    glow = Image.new("RGB", (w, h), color)
    glow.putalpha(a)
    return glow


def main():
    base = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # 1) 渐变底（不透明画布上先叠加高光，最后一次性按圆角矩形裁切，保证四角纯透明）
    canvas = vgrad(SIZE, SIZE, C_TOP, C_BOT)
    hi = radial_alpha(SIZE, SIZE, (SIZE * 0.38, SIZE * 0.28), SIZE * 0.62, C_HI)
    canvas = Image.composite(hi.convert("RGB"), canvas, hi.split()[3])

    # 2) 圆角矩形遮罩（四角透明）
    canvas = canvas.convert("RGBA")
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, SIZE - 1, SIZE - 1], radius=int(SIZE * 0.225), fill=255
    )
    bg = Image.composite(canvas, base, mask)

    # 3) 内描边
    d = ImageDraw.Draw(bg)
    d.rounded_rectangle(
        [3, 3, SIZE - 4, SIZE - 4], radius=int(SIZE * 0.222),
        outline=C_STROKE + (255,), width=5,
    )

    # 4) 文字：词（左，输入） + 箭头（转换） + 词典（右，输出）
    #    动态居中布局：先量宽，再把三段水平均分，保证箭头清晰、不挤压
    font_word = ImageFont.truetype(FONT, int(SIZE * 0.30), index=FONT_INDEX)
    font_pair = ImageFont.truetype(FONT, int(SIZE * 0.225), index=FONT_INDEX)

    w1 = d.textlength("词", font=font_word)
    w2 = d.textlength("词典", font=font_pair)
    arrow_w = SIZE * 0.17                      # 箭头预留宽度
    total = w1 + arrow_w + w2
    start = (SIZE - total) / 2
    yc = SIZE * 0.485

    x_word = start + w1 / 2
    x_pair = start + w1 + arrow_w + w2 / 2

    # 阴影
    d.text((x_word, yc + 9), "词", font=font_word, fill=C_SHADOW + (170,), anchor="mm")
    d.text((x_pair, yc + 9), "词典", font=font_pair, fill=C_SHADOW + (170,), anchor="mm")
    # 正文（输入「词」稍柔，输出「词典」更亮，突出"变成词典"的结果）
    d.text((x_word, yc), "词", font=font_word, fill=C_TEXT + (235,), anchor="mm")
    d.text((x_pair, yc), "词典", font=font_pair, fill=C_TEXT + (255,), anchor="mm")

    # 箭头：词右缘 -> 词典左缘
    x0 = start + w1 + SIZE * 0.012
    x1 = start + w1 + arrow_w - SIZE * 0.012
    ya = yc
    aw = int(SIZE * 0.026)
    d.line([(x0, ya), (x1, ya)], fill=C_TEXT + (255,), width=aw, joint="curve")
    ah = SIZE * 0.052
    d.polygon(
        [(x1, ya), (x1 - ah, ya - ah * 0.72), (x1 - ah, ya + ah * 0.72)],
        fill=C_TEXT + (255,),
    )

    master = os.path.join(OUT, "app_icon_1024.png")
    bg.save(master, "PNG")
    print("母图:", master)

    # 5) 生成 .iconset 各尺寸（sips 缩放）+ iconutil 合成
    iconset = os.path.join(OUT, "app.iconset")
    os.makedirs(iconset, exist_ok=True)
    sizes = [
        ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
    ]
    for name, s in sizes:
        out = os.path.join(iconset, name)
        subprocess.run(["sips", "-z", str(s), str(s), master, "--out", out],
                       check=True, capture_output=True)
    icns = os.path.join(OUT, "app.icns")
    subprocess.run(["iconutil", "--convert", "icns", "--output", icns, iconset],
                   check=True)
    print("图标:", icns)
    # 清理中间 iconset，只保留母图与 icns
    subprocess.run(["rm", "-rf", iconset], check=False)
    print("完成。")


if __name__ == "__main__":
    main()
