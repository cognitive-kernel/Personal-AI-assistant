from __future__ import annotations

import re
from pathlib import Path


CSS_PATH = Path(__file__).parents[1] / "app" / "static" / "dashboard.css"
HEX_COLOR = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _rgb(value: str) -> tuple[float, float, float]:
    match = HEX_COLOR.fullmatch(value.strip())
    assert match, f"Expected a CSS hex color, got {value!r}"
    raw = match.group(1)
    if len(raw) == 3:
        raw = "".join(character * 2 for character in raw)
    return tuple(int(raw[index:index + 2], 16) / 255 for index in (0, 2, 4))


def _luminance(value: str) -> float:
    channels = _rgb(value)
    linear = tuple(
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    )
    return sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _tokens(css: str, selector: str) -> dict[str, str]:
    pattern = re.compile(re.escape(selector) + r"\s*\{([^}]*)\}", re.DOTALL)
    match = pattern.search(css)
    assert match, f"Missing theme token block: {selector}"
    return dict(re.findall(r"--([a-z-]+)\s*:\s*(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?)\s*;", match.group(1)))


def test_core_theme_tokens_meet_wcag_text_contrast():
    css = CSS_PATH.read_text(encoding="utf-8")
    themes = {
        "light": _tokens(css, ":root"),
        "dark": _tokens(css, ':root[data-theme="dark"]'),
    }

    for theme_name, tokens in themes.items():
        for foreground_name in ("text", "muted"):
            for background_name in ("page", "surface"):
                ratio = _contrast(tokens[foreground_name], tokens[background_name])
                assert ratio >= 4.5, (
                    f"{theme_name} {foreground_name} on {background_name} "
                    f"has contrast {ratio:.2f}:1; expected at least 4.5:1"
                )

        for foreground_name in ("primary", "accent"):
            ratio = _contrast(tokens[foreground_name], tokens["surface"])
            assert ratio >= 4.5, (
                f"{theme_name} {foreground_name} on surface has contrast "
                f"{ratio:.2f}:1; expected at least 4.5:1"
            )

        focus_ratio = _contrast(tokens["focus"], tokens["surface"])
        assert focus_ratio >= 3.0, (
            f"{theme_name} focus indicator on surface has contrast "
            f"{focus_ratio:.2f}:1; expected at least 3:1"
        )
