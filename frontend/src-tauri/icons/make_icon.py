"""Genere l'icone source (PNG 1024) : barres de forme d'onde sur fond degrade.

    uv run --with pillow python make_icon.py
    npx tauri icon icons/source.png

La seconde commande derive tout le jeu d'icones de `source.png`. Outil ponctuel :
Pillow n'est volontairement pas une dependance du projet, qui n'en a pas besoin
pour tourner.
"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
RADIUS = 232
TOP = (124, 92, 255)
BOTTOM = (58, 160, 255)
BARS = [0.30, 0.52, 0.78, 1.00, 0.78, 0.46, 0.66, 0.34]


def main() -> None:
    gradient = Image.new("RGB", (1, SIZE))
    for y in range(SIZE):
        t = y / (SIZE - 1)
        gradient.putpixel(
            (0, y),
            tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)),
        )
    base = gradient.resize((SIZE, SIZE)).convert("RGBA")

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), RADIUS, fill=255)

    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(base, (0, 0), mask)

    draw = ImageDraw.Draw(icon)
    bar_w, gap, max_h = 62, 42, 470
    total = len(BARS) * bar_w + (len(BARS) - 1) * gap
    x = (SIZE - total) / 2
    cy = SIZE / 2

    for amp in BARS:
        h = max_h * amp
        draw.rounded_rectangle(
            (x, cy - h / 2, x + bar_w, cy + h / 2), bar_w / 2, fill=(255, 255, 255, 242)
        )
        x += bar_w + gap

    out = Path(__file__).parent / "source.png"
    icon.save(out)
    print(f"ecrit : {out}")


if __name__ == "__main__":
    main()
