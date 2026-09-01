"""Genere les icones PNG de la PWA (192 et 512 px) sans dependance externe.

Meme dessin que public/icons/icon.svg, rasterise a la main : Pillow n'est pas
installe et on veut que le projet reste en `python server.py` sans rien poser.

    python make_icons.py
"""

import os
import struct
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
ICONS_DIR = os.path.join(ROOT, "public", "icons")

BLUE = (31, 95, 208)
DARK_BLUE = (15, 63, 156)
WHITE = (255, 255, 255)
PALE = (219, 230, 251)
SOFT = (157, 184, 234)

# (x, y, largeur, hauteur, rayon, couleur) dans un repere 512x512
SHAPES = [
    (0, 0, 512, 512, 112, BLUE),
    (160, 102, 26, 72, 13, DARK_BLUE),
    (326, 102, 26, 72, 13, DARK_BLUE),
    (104, 132, 304, 268, 34, WHITE),
    (104, 132, 304, 66, 20, PALE),
    (146, 238, 94, 26, 9, BLUE),
    (264, 238, 104, 26, 9, SOFT),
    (146, 292, 130, 26, 9, SOFT),
    (300, 292, 68, 26, 9, BLUE),
    (146, 346, 80, 26, 9, SOFT),
]


def inside_rounded(px, py, x, y, w, h, r):
    if not (x <= px < x + w and y <= py < y + h):
        return False
    if r <= 0:
        return True
    # coin le plus proche
    cx = min(max(px, x + r), x + w - r)
    cy = min(max(py, y + r), y + h - r)
    dx, dy = px - cx, py - cy
    return dx * dx + dy * dy <= r * r


def render(size):
    scale = size / 512.0
    shapes = [
        (x * scale, y * scale, w * scale, h * scale, r * scale, color)
        for (x, y, w, h, r, color) in SHAPES
    ]

    rows = []
    for py in range(size):
        row = bytearray([0])  # filtre PNG "None" en tete de ligne
        cy = py + 0.5
        for px in range(size):
            cx = px + 0.5
            pixel = (11, 18, 32)  # fond, invisible sous l'icone arrondie
            alpha = 0
            for (x, y, w, h, r, color) in shapes:
                if inside_rounded(cx, cy, x, y, w, h, r):
                    pixel = color
                    alpha = 255
            row += bytes((pixel[0], pixel[1], pixel[2], alpha))
        rows.append(bytes(row))
    return b"".join(rows)


def chunk(tag, payload):
    body = tag + payload
    return (struct.pack(">I", len(payload)) + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))


def write_png(path, size):
    raw = render(size)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # RGBA 8 bits
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", header)
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(png)
    print("%s (%d octets)" % (os.path.basename(path), len(png)))


def main():
    os.makedirs(ICONS_DIR, exist_ok=True)
    for size in (192, 512):
        write_png(os.path.join(ICONS_DIR, "icon-%d.png" % size), size)


if __name__ == "__main__":
    main()
