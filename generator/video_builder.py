"""[Agent 7: Buyg 고정 템플릿 쇼츠 영상 빌더 - 프로토타입]

'쉬운육아' 스타일 레퍼런스 구조의 1080x1920 / 24fps 세로 쇼츠를 한 편 렌더링한다.

    상단 헤더(0~400px)  : 크림/옐로 배경 + 'Buyg | 9월 19일 마감'(실행일 기준) 뱃지 + 훅 타이틀 (PIL로 이미지 생성 -> ImageClip)
    중앙 미디어(1000x1000): Buyg_Assets(GIF/MP4) 또는 실제 상품 이미지(없으면 플레이스홀더 카드), 씬별 슬라이드쇼
    하단 자막           : 씬별 자막 (PIL 렌더, ImageMagick 불필요)
    음성                : edge-tts (VOICE 상수, rate +25% / pitch +5Hz) - 씬별로 따로 합성해 씬 길이를 실제 음성 길이에 맞춤
    씬 구성             : 훅 -> [제품 카드 -> 반응 짤] x 3상품 -> 엔딩(프로필 링크/댓글 '달력')

결과: reels/test_preview.mp4 (reels/는 .gitignore 대상)

프로토타입이라 씬/대본은 아래 SCENES 상수에 고정돼 있다 (마감일 표기만 오늘 날짜로 자동). 야간 파이프라인에는 연결하지 않았고,
run()은 예외를 던지지 않고 한 줄 로그 후 None을 반환한다.

실행:
    python -m generator.video_builder
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageSequence

from config import BASE_DIR

logger = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 24
HEADER_H = 400
MEDIA_SIZE = 1000
MEDIA_XY = ((W - MEDIA_SIZE) // 2, 440)
CAPTION_XY = (0, 1480)
CAPTION_H = 300
TAIL_SEC = 0.4

ASSETS_DIR = BASE_DIR / "Buyg_Assets"
OUTPUT_PATH = BASE_DIR / "reels" / "test_preview.mp4"
VOICE = "ko-KR-SunHiNeural"  # 대안: ko-KR-InJoonNeural (build(voice=...)로도 교체 가능)
TTS_RATE = "+25%"   # 숏폼 템포
TTS_PITCH = "+5Hz"  # 살짝 톤업된 밝은 말투

BG_COLOR = (255, 251, 235)
HEADER_COLOR = (255, 233, 160)
INK = (59, 42, 26)
ACCENT = (255, 122, 26)

TITLE_LINE1, TITLE_ACCENT = "오늘 마감 육아템", "BEST 3"
FOOTER_TEXT = "프로필 링크에서 실시간 공구 달력 확인"
SCENE_GAP_SEC = 0.15  # 씬 끝 여백 (음성 길이 + 여백 = 씬 길이)
NO_VOICE_SCENE_SEC = 2.5

# {d} = 마감일 표기(예: '9월 19일', 실행일 기준). asset이 없으면 product 플레이스홀더 카드, kind=='end'면 엔딩 카드.
# narration: 씬별 TTS 대사 (씬 길이는 이 음성 길이에 맞춰 자동 결정, min_dur는 최소 노출 시간)
SCENES = [
    {"asset": "01_충격.gif", "caption": "{d} 마감 육아템 놓치지 마세요!",
     "narration": "{d} 마감 육아템 놓치지 마세요!", "min_dur": 2.5},
    {"asset": None, "product": ("와일드알프\n베이비워터", "18,900원", (200, 230, 255)),
     "item": 1, "keyword": "와일드알프",
     "caption": "1. 와일드알프 베이비워터", "narration": "첫 번째 와일드알프 베이비워터!", "min_dur": 1.8},
    {"asset": "03_지갑텅텅.gif", "caption": "오늘 마감 놓치면 정가 구매!",
     "narration": "놓치면 정가로 사야 해요!", "min_dur": 1.8},
    {"asset": None, "product": ("라온킴\n채소팩", "31,900원", (210, 240, 200)),
     "item": 2, "keyword": "라온킴",
     "caption": "2. 라온킴 채소팩", "narration": "두 번째 라온킴 채소팩!", "min_dur": 1.8},
    {"asset": "02_허리아픔.mp4", "caption": "이유식 손목 통증 해방템",
     "narration": "이유식 손목 통증 이제 해방!", "min_dur": 1.8},
    {"asset": None, "product": ("복떡복떡\n송편 키트", "19,900원~", (255, 215, 225)),
     "item": 3, "keyword": "복떡복떡",
     "caption": "3. 복떡복떡 송편 키트", "narration": "세 번째 복떡복떡 송편 키트!", "min_dur": 1.8},
    {"asset": "04_신남.gif", "caption": "아이랑 집콕놀이 종결템",
     "narration": "아이랑 집콕놀이 이걸로 끝!", "min_dur": 1.8},
    {"kind": "end", "asset": None, "caption": "프로필 링크 · 댓글 '달력'",
     "narration": "프로필 링크 확인하고 댓글로 달력 남겨주세요!", "min_dur": 2.5},
]


def _deadline_label(d: date) -> str:
    return f"{d.month}월 {d.day}일"


# ───────────────────────── 폰트 / 이미지 유틸 ─────────────────────────

def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    names = ["malgunbd.ttf", "malgun.ttf"] if bold else ["malgun.ttf", "malgunbd.ttf"]
    names += ["NanumGothicBold.ttf", "NanumGothic.ttf", "gulim.ttc"]
    for n in names:
        try:
            return ImageFont.truetype(str(fonts_dir / n), size)
        except OSError:
            continue
    raise RuntimeError("한글 폰트를 찾지 못함 (malgun.ttf 등)")


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_w: int, start: int, min_size: int = 40) -> ImageFont.FreeTypeFont:
    size = start
    while size > min_size:
        f = _font(size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return _font(min_size)


def _make_body_bg() -> np.ndarray:
    img = Image.new("RGB", (W, H), BG_COLOR)
    d = ImageDraw.Draw(img)
    f = _fit_font(d, FOOTER_TEXT, W - 120, 40, 28)
    d.text((W // 2, 1860), FOOTER_TEXT, font=f, fill=(140, 110, 70), anchor="mm")
    return np.array(img)


def _make_header(badge: str) -> np.ndarray:
    img = Image.new("RGB", (W, HEADER_H), HEADER_COLOR)
    d = ImageDraw.Draw(img)
    # Buyg 뱃지
    bf = _font(42)
    bw = int(d.textlength(badge, font=bf)) + 80
    x0 = (W - bw) // 2
    d.rounded_rectangle((x0, 36, x0 + bw, 116), radius=40, fill=INK)
    d.text((W // 2, 76), badge, font=bf, fill=(255, 233, 160), anchor="mm")
    # 훅 타이틀 (2줄: 본문 + 강조)
    f1 = _fit_font(d, TITLE_LINE1, W - 100, 96)
    d.text((W // 2, 205), TITLE_LINE1, font=f1, fill=INK, anchor="mm")
    f2 = _fit_font(d, TITLE_ACCENT, W - 100, 132)
    d.text((W // 2, 315), TITLE_ACCENT, font=f2, fill=ACCENT, anchor="mm", stroke_width=3, stroke_fill=ACCENT)
    return np.array(img)


def _make_caption(text: str) -> np.ndarray:
    """투명 배경 RGBA 자막 (흰 글씨 + 두꺼운 어두운 외곽선 대신, 크림 배경 위 진한 글씨 + 강조 밑줄)."""
    img = Image.new("RGBA", (W, CAPTION_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = _fit_font(d, text, W - 100, 84, 44)
    d.text((W // 2, CAPTION_H // 2 - 10), text, font=f, fill=INK, anchor="mm", stroke_width=2, stroke_fill=INK)
    tw = d.textlength(text, font=f)
    y = CAPTION_H // 2 + 60
    d.rounded_rectangle((W // 2 - tw // 2, y, W // 2 + tw // 2, y + 12), radius=6, fill=ACCENT)
    return np.array(img)


def _make_placeholder(name: str, price: str, color: tuple[int, int, int], tag: str = "") -> Image.Image:
    S = MEDIA_SIZE
    img = Image.new("RGB", (S, S), color)
    d = ImageDraw.Draw(img)
    d.ellipse((S // 2 - 330, S // 2 - 380, S // 2 + 330, S // 2 + 280), fill=(255, 255, 255))
    lines = name.split("\n")
    f = _font(120)
    y = S // 2 - 200 if len(lines) > 1 else S // 2 - 120
    for ln in lines:
        d.text((S // 2, y), ln, font=_fit_font(d, ln, 600, 120, 60), fill=INK, anchor="mm")
        y += 140
    d.text((S // 2, S // 2 + 130), price, font=_font(90), fill=ACCENT, anchor="mm", stroke_width=2, stroke_fill=ACCENT)
    if tag:
        tf = _font(44)
        tw = int(d.textlength(tag, font=tf)) + 60
        d.rounded_rectangle((S // 2 - tw // 2, 40, S // 2 + tw // 2, 116), radius=38, fill=ACCENT)
        d.text((S // 2, 78), tag, font=tf, fill=(255, 255, 255), anchor="mm")
    return img


# ───────────────────────── 상품 이미지 조회 ─────────────────────────
# 우선순위: Buyg_Assets/item_0N.(jpg|png|webp) 수동 지정 -> data/video_assets/ 캐시 -> gonggu.db에 크롤링된
# image_url 다운로드(후보 중 가장 해상도 큰 것, 성공 시 캐시) -> 없으면 텍스트 플레이스홀더 카드.

_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
IMAGE_CACHE_DIR = BASE_DIR / "data" / "video_assets"
IMAGE_CACHE_TTL_SEC = 7 * 24 * 3600
GOOD_ENOUGH_PX = 700
MAX_CANDIDATES = 8


def _open_rgb(path: Path) -> Image.Image | None:
    try:
        im = Image.open(path)
        im.load()
        return _to_rgb(im)
    except Exception:
        return None


def _candidate_urls(keyword: str) -> list[str]:
    import sqlite3
    from urllib.parse import parse_qs, unquote, urlparse

    from config import GONGGU_DB_PATH

    try:
        conn = sqlite3.connect(f"file:{Path(GONGGU_DB_PATH).as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT image_url FROM gonggu WHERE (brand LIKE ? OR product_name LIKE ?) AND image_url != '' "
                "ORDER BY updated_at DESC LIMIT ?", (f"%{keyword}%", f"%{keyword}%", MAX_CANDIDATES)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    urls: list[str] = []
    for (u,) in rows:
        if "search.pstatic.net/common" in u:  # 네이버 검색 150px 썸네일 -> 원본 src를 먼저 시도
            src = parse_qs(urlparse(u).query).get("src", [""])[0]
            if src:
                urls.append(unquote(src).replace("http://", "https://", 1))
        urls.append(u)
    return list(dict.fromkeys(urls))[: MAX_CANDIDATES * 2]


def _download_image(url: str) -> Image.Image | None:
    import io

    import requests

    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or not r.content or len(r.content) > 15 * 1024 * 1024:
            return None
        im = Image.open(io.BytesIO(r.content))
        im.load()
        return _to_rgb(im)
    except Exception:
        return None


def _find_product_image(item_no: int, keyword: str) -> Image.Image | None:
    for ext in _IMG_EXTS:
        p = ASSETS_DIR / f"item_{item_no:02d}{ext}"
        if p.exists():
            im = _open_rgb(p)
            if im is not None:
                logger.info("상품 이미지 %d: 수동 지정 %s", item_no, p.name)
                return im
    cache = IMAGE_CACHE_DIR / f"{keyword}.jpg"
    if cache.exists() and time.time() - cache.stat().st_mtime < IMAGE_CACHE_TTL_SEC:
        im = _open_rgb(cache)
        if im is not None:
            logger.info("상품 이미지 %d: 캐시 %s", item_no, cache.name)
            return im
    best = None
    for url in _candidate_urls(keyword):
        im = _download_image(url)
        if im is None:
            continue
        if best is None or min(im.size) > min(best.size):
            best = im
        if min(best.size) >= GOOD_ENOUGH_PX:
            break
    if best is None:
        logger.warning("상품 이미지 %d(%s): 찾지 못함 -> 플레이스홀더", item_no, keyword)
        return None
    try:
        IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        best.save(cache, quality=92)
    except Exception:
        pass
    logger.info("상품 이미지 %d(%s): DB 크롤링 이미지 사용 %dx%d", item_no, keyword, *best.size)
    return best


def _decorate_product_image(img: Image.Image, price: str, tag: str) -> Image.Image:
    """상품 사진 위에 마감일 태그(상단)와 가격 pill(하단)을 얹는다."""
    img = img.copy()
    d = ImageDraw.Draw(img)
    S = img.width
    if tag:
        tf = _font(44)
        tw = int(d.textlength(tag, font=tf)) + 60
        d.rounded_rectangle((36, 36, 36 + tw, 112), radius=38, fill=ACCENT)
        d.text((36 + tw // 2, 74), tag, font=tf, fill=(255, 255, 255), anchor="mm")
    if price:
        pf = _font(76)
        pw = int(d.textlength(price, font=pf)) + 80
        d.rounded_rectangle((S // 2 - pw // 2, S - 170, S // 2 + pw // 2, S - 50), radius=60, fill=(255, 255, 255))
        d.text((S // 2, S - 110), price, font=pf, fill=ACCENT, anchor="mm", stroke_width=2, stroke_fill=ACCENT)
    return img


def _make_end_card() -> Image.Image:
    S = MEDIA_SIZE
    img = Image.new("RGB", (S, S), ACCENT)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 60, S - 60, S - 60), radius=60, fill=(255, 255, 255))
    d.text((S // 2, 260), "Buyg", font=_font(150), fill=ACCENT, anchor="mm", stroke_width=3, stroke_fill=ACCENT)
    t1 = "프로필 링크 확인!"
    d.text((S // 2, 470), t1, font=_fit_font(d, t1, 800, 110), fill=INK, anchor="mm")
    d.rounded_rectangle((130, 600, S - 130, 780), radius=50, fill=(255, 233, 160))
    t2 = "댓글로 '달력' 남기면"
    t3 = "실시간 공구 달력 링크를 DM으로!"
    d.text((S // 2, 660), t2, font=_fit_font(d, t2, 700, 66), fill=INK, anchor="mm")
    d.text((S // 2, 730), t3, font=_fit_font(d, t3, 700, 52), fill=INK, anchor="mm")
    return img


def _to_rgb(im: Image.Image) -> Image.Image:
    if im.mode in ("RGBA", "LA", "P"):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return im.convert("RGB")


def _fit_square(src: Image.Image, size: int = MEDIA_SIZE) -> Image.Image:
    """정사각형 미디어 박스에 맞춤. 정사각형에 가깝거나 세로 영상은 꽉 채워 크롭(cover),
    가로로 넓은 영상(16:9 등)은 흐릿한 배경 위에 전체를 보여준다(contain)."""
    sw, sh = src.size
    aspect = sw / sh
    if aspect <= 1.34:
        scale = size / min(sw, sh)
        r = src.resize((max(size, round(sw * scale)), max(size, round(sh * scale))), Image.LANCZOS)
        l, t = (r.width - size) // 2, (r.height - size) // 2
        return r.crop((l, t, l + size, t + size))
    scale = size / max(sw, sh)
    fg = src.resize((round(sw * scale), round(sh * scale)), Image.LANCZOS)
    cs = size / min(sw, sh)
    bg = src.resize((max(size, round(sw * cs)), max(size, round(sh * cs))), Image.BILINEAR)
    l, t = (bg.width - size) // 2, (bg.height - size) // 2
    bg = bg.crop((l, t, l + size, t + size)).filter(ImageFilter.GaussianBlur(28))
    bg.paste(fg, ((size - fg.width) // 2, (size - fg.height) // 2))
    return bg


def _rounded_mask(size: int, radius: int = 48) -> np.ndarray:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return np.array(m, dtype=float) / 255.0


# ───────────────────────── 미디어 소스 ─────────────────────────

class _FrameSource:
    """t(초) -> 정사각형 RGB numpy 프레임. GIF/MP4는 반복 재생, 정지 이미지는 그대로."""

    def __init__(self, asset: str | None, product: tuple[str, str, tuple[int, int, int]] | None,
                 static_image: Image.Image | None = None, tag: str = ""):
        self._frames: list[Image.Image] = []
        self._ends: list[float] = []
        self._video = None
        self._static: np.ndarray | None = None

        if static_image is not None:
            self._static = np.array(static_image)
            return
        path = ASSETS_DIR / asset if asset else None
        try:
            if path and path.exists():
                self._load(path)
        except Exception as e:  # 손상된 에셋은 플레이스홀더로 대체
            logger.warning("에셋 로드 실패 -> 플레이스홀더 사용: %s (%s)", asset, type(e).__name__)
            self._frames, self._ends, self._video = [], [], None
        if not self._frames and self._video is None:
            name, price, color = product or ("Buyg", "", (230, 230, 230))
            self._static = np.array(_make_placeholder(name, price, color, tag))

    def _load(self, path: Path) -> None:
        if path.suffix.lower() in (".mp4", ".mov", ".webm"):
            from moviepy import VideoFileClip

            # 4K 원본은 디코딩 단계에서 축소해 메모리/시간 절약 (세로 영상 -> 폭 1000)
            self._video = VideoFileClip(str(path), audio=False, target_resolution=(MEDIA_SIZE, 1778))  # (폭, 높이)
            return
        im = Image.open(path)
        if getattr(im, "is_animated", False):
            acc = 0.0
            for fr in ImageSequence.Iterator(im):
                acc += max(fr.info.get("duration", 80), 20) / 1000.0
                self._frames.append(_fit_square(_to_rgb(fr.copy())))
                self._ends.append(acc)
        else:
            self._static = np.array(_fit_square(_to_rgb(im)))

    def get(self, t: float) -> np.ndarray:
        if self._static is not None:
            return self._static
        if self._video is not None:
            fr = Image.fromarray(self._video.get_frame(t % max(self._video.duration - 0.05, 0.1)))
            return np.array(_fit_square(fr))
        t = t % self._ends[-1]
        for fr, end in zip(self._frames, self._ends):
            if t < end:
                return np.array(fr)
        return np.array(self._frames[-1])

    def close(self) -> None:
        if self._video is not None:
            self._video.close()


# ───────────────────────── TTS ─────────────────────────

def _synthesize_all(texts: list[str], paths: list[Path], voice: str) -> list[bool]:
    """씬별 대사를 한 번의 이벤트 루프에서 합성. 개별 실패는 해당 씬만 False(무음)."""
    try:
        import edge_tts
    except Exception as e:
        logger.warning("edge-tts 로드 실패 -> 무음 영상으로 진행 (%s)", type(e).__name__)
        return [False] * len(texts)

    async def _one(text: str, path: Path) -> bool:
        try:
            await edge_tts.Communicate(text, voice, rate=TTS_RATE, pitch=TTS_PITCH).save(str(path))
            return path.exists() and path.stat().st_size > 0
        except Exception as e:
            logger.warning("TTS 실패 -> 해당 씬 무음 (%s)", type(e).__name__)
            return False

    async def _all() -> list[bool]:
        return list(await asyncio.gather(*[_one(t, p) for t, p in zip(texts, paths)]))

    try:
        return asyncio.run(_all())
    except Exception as e:
        logger.warning("TTS 실행 실패 -> 무음 영상으로 진행 (%s)", type(e).__name__)
        return [False] * len(texts)


# ───────────────────────── 조립 ─────────────────────────

def build(out_path: Path | None = None, voice: str = VOICE, deadline: date | None = None) -> Path:
    from moviepy import AudioFileClip, CompositeAudioClip, CompositeVideoClip, ImageClip, VideoClip

    out_path = Path(out_path or OUTPUT_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    label = _deadline_label(deadline or date.today())
    tmp_dir = Path(tempfile.mkdtemp(prefix="buyg_video_"))
    sources: list[_FrameSource] = []
    audios: list = []
    final = None
    try:
        # 1) 씬별 음성 합성 -> 씬 길이 = 음성 길이 + 여백 (최소 노출 시간 보장)
        texts = [sc["narration"].format(d=label) for sc in SCENES]
        mp3s = [tmp_dir / f"voice_{i}.mp3" for i in range(len(SCENES))]
        ok = _synthesize_all(texts, mp3s, voice)
        durations, voice_clips = [], []
        for sc, mp3, has in zip(SCENES, mp3s, ok):
            clip = None
            if has:
                try:
                    clip = AudioFileClip(str(mp3))
                    audios.append(clip)
                except Exception:
                    clip = None
            voice_clips.append(clip)
            base = clip.duration + SCENE_GAP_SEC if clip else NO_VOICE_SCENE_SEC
            durations.append(max(sc["min_dur"], base))
        durations[-1] += TAIL_SEC
        total = sum(durations)
        logger.info("씬 %d개, 음성 %d/%d, 총 %.2fs, 마감일 표기 '%s'", len(SCENES), sum(ok), len(SCENES), total, label)

        # 2) 정적 레이어: 배경 + 헤더 (헤더는 PIL 이미지 -> ImageClip)
        layers = [
            ImageClip(_make_body_bg()).with_duration(total),
            ImageClip(_make_header(f"Buyg | {label} 마감")).with_position((0, 0)).with_duration(total),
        ]

        # 3) 씬별 미디어 + 자막 + 음성 배치
        mask_arr = _rounded_mask(MEDIA_SIZE)
        start = 0.0
        placed = []
        for i, (sc, dur, vclip) in enumerate(zip(SCENES, durations, voice_clips)):
            static = _make_end_card() if sc.get("kind") == "end" else None
            if sc.get("item") and sc.get("product"):  # 상품 카드 씬: 실제 상품 이미지 우선
                photo = _find_product_image(sc["item"], sc["keyword"])
                if photo is not None:
                    static = _decorate_product_image(_fit_square(photo), sc["product"][1], f"{label} 마감")
            src = _FrameSource(sc.get("asset"), sc.get("product"), static_image=static, tag=f"{label} 마감")
            sources.append(src)
            media = (
                VideoClip(lambda t, s=src: s.get(t), duration=dur)
                .with_mask(ImageClip(mask_arr, is_mask=True).with_duration(dur))
                .with_start(start)
                .with_position(MEDIA_XY)
            )
            caption_text = sc["caption"].format(d=label)
            caption = ImageClip(_make_caption(caption_text)).with_start(start).with_duration(dur).with_position(CAPTION_XY)
            layers += [media, caption]
            if vclip is not None:
                placed.append(vclip.with_start(start + 0.05))
            logger.info("Scene %d: %.2f~%.2fs %s | %s", i + 1, start, start + dur,
                        sc.get("asset") or ("end-card" if sc.get("kind") == "end" else "product-card"), caption_text)
            start += dur

        final = CompositeVideoClip(layers, size=(W, H)).with_duration(total)
        if placed:
            final = final.with_audio(CompositeAudioClip(placed).with_duration(total))

        # 4) 렌더 (yuv420p + faststart: 인스타/휴대폰 재생 호환)
        final.write_videofile(
            str(out_path), fps=FPS, codec="libx264", audio_codec="aac", preset="medium", threads=4,
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"], logger=None,
        )
        return out_path
    finally:
        for s in sources:
            s.close()
        for a in audios:
            a.close()
        if final is not None:
            final.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def run(out_path: Path | None = None) -> Path | None:
    """성공 시 mp4 경로, 실패 시 None (트레이스백 없이 한 줄 로그 - 야간 로그 'Traceback' 판정 방지)."""
    try:
        path = build(out_path)
        logger.info("쇼츠 영상 생성 완료: %s", path)
        return path
    except Exception as e:
        logger.error("쇼츠 영상 생성 실패 (%s: %s)", type(e).__name__, str(e)[:200])
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run()
    print(result if result else "영상 생성 실패 (로그 확인)")
