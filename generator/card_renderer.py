"""Jinja2 HTML 템플릿을 렌더링해 Playwright로 인스타그램 카드뉴스 PNG를 생성한다."""
from __future__ import annotations

import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

# body 는 540x675 CSS px 로 만들고 device_scale_factor=2 를 줘서
# 실제 출력은 1080x1350 (인스타그램 4:5 피드 카드 권장 비율)
CARD_CSS_WIDTH = 540
CARD_CSS_HEIGHT = 675
SCALE_FACTOR = 2


def render_card_html(context: dict, template_name: str) -> str:
    template = _env.get_template(template_name)
    return template.render(**context)


def render_card_png(context: dict, output_path: Path, template_name: str) -> Path:
    html = render_card_html(context, template_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        tmp_html_path = f.name

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": CARD_CSS_WIDTH, "height": CARD_CSS_HEIGHT},
            device_scale_factor=SCALE_FACTOR,
        )
        page.goto(f"file:///{tmp_html_path}")
        page.screenshot(path=str(output_path))
        browser.close()

    Path(tmp_html_path).unlink(missing_ok=True)
    return output_path
