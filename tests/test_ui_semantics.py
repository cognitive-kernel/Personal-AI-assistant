from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


INDEX_PATH = Path(__file__).parents[1] / "app" / "static" / "index.html"
VALID_BUTTON_TYPES = {"button", "submit", "reset"}


class _ButtonTypeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.button_types: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "button":
            return
        attributes = dict(attrs)
        value = attributes.get("type")
        self.button_types.append(value.lower() if isinstance(value, str) else None)


def test_static_dashboard_buttons_declare_explicit_valid_types():
    parser = _ButtonTypeParser()
    parser.feed(INDEX_PATH.read_text(encoding="utf-8"))
    parser.close()

    assert parser.button_types, "Expected the dashboard to contain static button controls"
    missing = [index for index, value in enumerate(parser.button_types, start=1) if value is None]
    invalid = [
        (index, value)
        for index, value in enumerate(parser.button_types, start=1)
        if value is not None and value not in VALID_BUTTON_TYPES
    ]

    assert not missing, f"Static dashboard buttons missing an explicit type: {missing}"
    assert not invalid, f"Static dashboard buttons use invalid types: {invalid}"
