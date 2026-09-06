"""[Agent 3: 웹 대시보드] FastAPI + FullCalendar.

web/static/index.html 을 정적으로 서빙하고, 대시보드가 쓰는 REST API를 제공한다.

실행:
    uvicorn web.main:app --reload --port 8000
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import gonggu_db
from config import CATEGORIES, OUTPUT_DIR

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="영유아 공구 캘린더")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")

gonggu_db.init_db()


@app.get("/")
def dashboard():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/categories")
def api_categories():
    return [
        {"name": name, "color": cat["color"], "priority": cat["priority"]}
        for name, cat in CATEGORIES.items()
    ]


@app.get("/api/events")
def api_events(
    start: str = Query(None, description="FullCalendar 조회 시작일 (YYYY-MM-DD)"),
    end: str = Query(None, description="FullCalendar 조회 종료일 (YYYY-MM-DD)"),
    category: str | None = Query(None, description="특정 카테고리만 필터링 (전체 탭이면 생략)"),
):
    rows = gonggu_db.list_gonggu(start_date=start, end_date=end, category=category)
    events = []
    for gb in rows:
        if not gb.get("start_date"):
            continue
        end_date = gb.get("end_date") or gb["start_date"]
        # FullCalendar의 end는 exclusive이므로 +1일
        end_exclusive = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        color = CATEGORIES.get(gb["category"], {}).get("color", "#999999")
        events.append(
            {
                "id": gb["id"],
                "title": f"{gb['product_name']} · {gb['influencer_name']}",
                "start": gb["start_date"],
                "end": end_exclusive,
                "color": color,
                "extendedProps": {
                    "category": gb["category"],
                    "product_name": gb["product_name"],
                    "influencer_name": gb["influencer_name"],
                    "brand": gb.get("brand") or "",
                    "purchase_link": gb.get("purchase_link") or "",
                    "key_benefit": gb.get("key_benefit") or "",
                    "start_date": gb["start_date"],
                    "end_date": gb.get("end_date") or "",
                },
            }
        )
    return events


@app.get("/api/cards")
def api_cards():
    """generator/card_news.py(Agent 4)가 생성한 가장 최신 카드뉴스 세트."""
    if not OUTPUT_DIR.exists():
        return []
    sets = sorted(
        (d for d in OUTPUT_DIR.iterdir() if d.is_dir() and d.name.startswith("cardnews_")),
        reverse=True,
    )
    if not sets:
        return []
    latest = sets[0]
    files = sorted(latest.glob("*.png"))
    return [f"/output/{latest.name}/{f.name}" for f in files]
