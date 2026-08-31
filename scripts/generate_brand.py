"""Generate deterministic PNG brand assets from the original vector design."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "custom_components" / "solaredge_one_bridge" / "brand"


def _points(scale: float, values: list[tuple[float, float]]):
    return [(round(x * scale), round(y * scale)) for x, y in values]


def render(size: int) -> Image.Image:
    """Render the bridge mark at one square size."""
    scale = size / 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1),
        radius=round(52 * scale),
        fill="#102a43",
    )
    draw.ellipse((44 * scale, 44 * scale, 92 * scale, 92 * scale), fill="#f6c945")
    ray_pairs = (
        ((68, 24), (68, 36)),
        ((68, 100), (68, 112)),
        ((24, 68), (36, 68)),
        ((100, 68), (112, 68)),
        ((37, 37), (46, 46)),
        ((90, 90), (99, 99)),
        ((99, 37), (90, 46)),
        ((46, 90), (37, 99)),
    )
    for start, end in ray_pairs:
        draw.line(
            _points(scale, [start, end]),
            fill="#f6c945",
            width=round(8 * scale),
        )

    curve = []
    p0, p1, p2, p3 = (42, 166), (75, 112), (111, 96), (150, 119)
    for step in range(31):
        t = step / 30
        x = (
            (1 - t) ** 3 * p0[0]
            + 3 * (1 - t) ** 2 * t * p1[0]
            + 3 * (1 - t) * t**2 * p2[0]
            + t**3 * p3[0]
        )
        y = (
            (1 - t) ** 3 * p0[1]
            + 3 * (1 - t) ** 2 * t * p1[1]
            + 3 * (1 - t) * t**2 * p2[1]
            + t**3 * p3[1]
        )
        curve.append((x, y))
    p0, p1, p2, p3 = (150, 119), (174, 133), (189, 131), (214, 107)
    for step in range(1, 21):
        t = step / 20
        curve.append(
            (
                (1 - t) ** 3 * p0[0]
                + 3 * (1 - t) ** 2 * t * p1[0]
                + 3 * (1 - t) * t**2 * p2[0]
                + t**3 * p3[0],
                (1 - t) ** 3 * p0[1]
                + 3 * (1 - t) ** 2 * t * p1[1]
                + 3 * (1 - t) * t**2 * p2[1]
                + t**3 * p3[1],
            )
        )
    draw.line(
        _points(scale, curve),
        fill="#4db6ac",
        width=round(14 * scale),
        joint="curve",
    )
    draw.line(
        _points(scale, [(45, 166), (211, 166)]),
        fill="#d9e2ec",
        width=round(10 * scale),
    )
    draw.rounded_rectangle(
        (95 * scale, 176 * scale, 175 * scale, 220 * scale),
        radius=round(8 * scale),
        outline="#d9e2ec",
        width=round(8 * scale),
    )
    draw.rectangle((175 * scale, 190 * scale, 185 * scale, 206 * scale), fill="#d9e2ec")
    draw.line(
        _points(scale, [(107, 198), (158, 198)]),
        fill="#4db6ac",
        width=round(12 * scale),
    )
    return image


def main() -> None:
    """Write standard Home Assistant local-brand raster sizes."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    render(256).save(OUTPUT / "icon.png", optimize=True)
    render(512).save(OUTPUT / "icon@2x.png", optimize=True)


if __name__ == "__main__":
    main()
