"""Render out/dashboard.html: one self-contained file (inline CSS/JS/fonts, no network requests)."""

import base64
import json
from pathlib import Path

from . import config

ASSETS = Path(__file__).parent / "assets"


def _font(name: str) -> str:
    return "data:font/woff2;base64," + base64.b64encode((ASSETS / name).read_bytes()).decode()


def write_dashboard(state: dict) -> Path:
    html = (ASSETS / "dashboard_template.html").read_text(encoding="utf-8")
    # Escape <, >, & so page text can never close the <script> tag or open an HTML comment inside it.
    data = (json.dumps(state, ensure_ascii=False, default=str)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    html = (html.replace("__FONT_SANS__", _font("Geist-Variable.woff2"))
                .replace("__FONT_MONO__", _font("GeistMono-Variable.woff2"))
                .replace("__DATA__", data))
    config.DASHBOARD_HTML.parent.mkdir(parents=True, exist_ok=True)
    config.DASHBOARD_HTML.write_text(html, encoding="utf-8")
    return config.DASHBOARD_HTML
