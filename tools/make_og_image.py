"""SNS 공유용 오픈그래프 대표 이미지(1200x630) 생성 -> icons/og-image.png

실행:  python tools/make_og_image.py   (Pillow 필요, Windows Arial Black 폰트 사용)
디자인(2026-10-05 재디자인 2차): 딥 퍼플 단색 배경 + 정중앙에 영문 'BUYG'만 단독 배치.
두꺼운 산세리프(Arial Black) + 넓은 자간. 한글 '바이그'는 이미지에 넣지 않고 공유 카드 제목(og:title)이 맡는다.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "icons" / "og-image.png"
FONT_PATH = "C:/Windows/Fonts/ariblk.ttf"   # Arial Black (없으면 Segoe UI Bold로 대체)
FONT_FALLBACK = "C:/Windows/Fonts/segoeuib.ttf"

W, H, S = 1200, 630, 2          # 2배로 그린 뒤 줄여서 글자 가장자리를 매끄럽게
BG = (79, 0, 107)                # #4f006b 매트한 딥 퍼플(브랜드 #5f0080보다 살짝 어둡게)
WHITE = (255, 255, 255)
TEXT = "BUYG"
FONT_SIZE = 210
TRACKING = 36                    # 글자 사이 추가 간격(px, 1x 기준)


def load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.truetype(FONT_FALLBACK, size)


def main():
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)
    font = load_font(FONT_SIZE * S)
    track = TRACKING * S
    # 글자별 잉크 박스로 전체 폭/높이를 재서 자간을 적용한 채 정중앙에 맞춘다
    boxes = [d.textbbox((0, 0), ch, font=font) for ch in TEXT]
    advances = [d.textlength(ch, font=font) for ch in TEXT]
    total_w = sum(advances) + track * (len(TEXT) - 1)
    # 첫 글자의 왼쪽 여백과 마지막 글자의 오른쪽 여백을 빼서 시각적 폭을 정확히 맞춘다
    left_pad = boxes[0][0]
    right_pad = advances[-1] - boxes[-1][2]
    ink_w = total_w - left_pad - right_pad
    top = min(b[1] for b in boxes)
    bottom = max(b[3] for b in boxes)
    x = (W * S - ink_w) / 2 - left_pad
    y = (H * S - (bottom - top)) / 2 - top
    for ch, adv in zip(TEXT, advances):
        d.text((x, y), ch, font=font, fill=WHITE)
        x += adv + track
    img = img.resize((W, H), Image.LANCZOS)
    OUT.parent.mkdir(exist_ok=True)
    img.save(OUT, optimize=True)
    print("saved", OUT, OUT.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
