from __future__ import annotations

import html
import re
from html import escape


_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def draw_svg(seed: int, palette: tuple[str, ...], subject: str) -> str:
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    if not palette or any(not isinstance(c, str) or not _COLOR.fullmatch(c) for c in palette):
        raise ValueError("palette must contain #RRGGBB colors")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("subject must be non-empty")
    state = seed & 0xFFFFFFFF

    def rand(limit: int) -> int:
        nonlocal state
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        return state % limit

    if subject == "orbit":
        nodes = [(540, 540, 120), (290, 360, 44), (790, 380, 52), (330, 760, 38), (760, 750, 46)]
    elif subject == "csw":
        nodes = [(540, 520, 116), (270, 350, 48), (810, 350, 48), (280, 760, 44), (800, 760, 44)]
    else:
        nodes = [(540, 540, 110), (300, 380, 46), (780, 700, 46)]
    shift = [(rand(41) - 20, rand(41) - 20) for _ in nodes]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080" role="img" aria-label="{escape(subject)}">']
    parts.append(f'<rect width="1080" height="1080" fill="{palette[0]}"/>')
    parts.append(f'<circle cx="540" cy="540" r="380" fill="none" stroke="{palette[1 % len(palette)]}" stroke-width="18" opacity="0.35"/>')
    for i, ((x, y, radius), (dx, dy)) in enumerate(zip(nodes, shift)):
        x += dx
        y += dy
        color = palette[(i + 1) % len(palette)]
        if i:
            parts.append(f'<path d="M 540 540 L {x} {y}" fill="none" stroke="{color}" stroke-width="12" opacity="0.7"/>')
        parts.append(f'<circle cx="{x}" cy="{y}" r="{radius}" fill="{color}" stroke="{palette[0]}" stroke-width="10"/>')
        parts.append(f'<circle cx="{x}" cy="{y}" r="{max(8, radius // 4)}" fill="{palette[(i + 2) % len(palette)]}"/>')
    label = 'CSW / ASEAN' if subject == 'csw' else subject.upper()
    parts.append(f'<text x="540" y="990" text-anchor="middle" fill="{palette[-1]}" font-family="sans-serif" font-size="44" letter-spacing="8">{escape(label)}</text>')
    parts.append('</svg>')
    return ''.join(parts) + '\n'
