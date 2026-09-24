"""Generate the Carinaa PWA icon set: the knowledge-flow mark on a violet field.

Three connected nodes flowing into a central intelligence point - the brand
motif, drawn directly with PIL. No external asset, no rasterised font.

Usage (from frontend/):  python scripts/gen_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "public"

# Brand tokens, from src/index.css (light theme).
VIOLET = (124, 58, 237, 255)
VIOLET_TOP = (151, 106, 250, 255)
WHITE = (255, 255, 255, 255)
TEAL = (13, 148, 136, 255)


def draw_field(size: int, *, rounded: bool) -> Image.Image:
    """The violet background: a soft vertical gradient, optionally rounded."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(size - 1, 1)
        # smoothstep for a gentler fall-off than a linear blend
        t = t * t * (3 - 2 * t)
        col = tuple(int(VIOLET[c] * (1 - t) + VIOLET_TOP[c] * t) for c in range(3)) + (255,)
        d.line([(0, y), (size, y)], fill=col)

    if not rounded:
        return img

    radius = int(size * 0.22)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def draw_mark(canvas: Image.Image) -> Image.Image:
    """The three-node flow mark, sized to the canvas it is drawn on."""
    size = canvas.size[0]
    d = ImageDraw.Draw(canvas)
    cx = cy = size // 2

    nodes = [
        (size * 0.50, size * 0.21),  # top
        (size * 0.21, size * 0.72),  # bottom-left
        (size * 0.79, size * 0.72),  # bottom-right
    ]

    stroke = max(4, int(size * 0.045))
    for nx, ny in nodes:
        d.line([(int(nx), int(ny)), (cx, cy)], fill=WHITE, width=stroke, joint="curve")

    r = max(8, int(size * 0.085))
    for (nx, ny), col in zip(nodes, (TEAL, WHITE, WHITE)):
        x, y = int(nx), int(ny)
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)

    ring = max(12, int(size * 0.115))
    d.ellipse([cx - ring, cy - ring, cx + ring, cy + ring], fill=WHITE)
    core = max(6, int(size * 0.062))
    d.ellipse([cx - core, cy - core, cx + core, cy + core], fill=VIOLET)
    return canvas


def make_icon(size: int, path: Path, *, maskable: bool) -> None:
    # Maskable icons must fill the whole square (the launcher crops the edges),
    # and the artwork must stay inside the central 80% safe zone. A regular icon
    # gets rounded corners and uses more of the canvas.
    if maskable:
        field = draw_field(size, rounded=False)
        inner = int(size * 0.76)
        mark = draw_mark(Image.new("RGBA", (inner, inner), (0, 0, 0, 0)))
        offset = (size - inner) // 2
        field.alpha_composite(mark, (offset, offset))
    else:
        field = draw_mark(draw_field(size, rounded=True))
    field.save(path, "PNG")
    print("wrote", path.name, f"({size}x{size})")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    make_icon(192, OUT / "icon-192.png", maskable=False)
    make_icon(512, OUT / "icon-512.png", maskable=False)
    make_icon(180, OUT / "apple-touch-icon.png", maskable=False)
    make_icon(192, OUT / "icon-maskable-192.png", maskable=True)
    make_icon(512, OUT / "icon-maskable-512.png", maskable=True)
    print("done. icons in", OUT)
