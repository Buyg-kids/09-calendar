"""SNS 공유용 오픈그래프 대표 이미지(1200x630) 생성 -> icons/og-image.png

실행:  python tools/make_og_image.py   (Pillow 필요, Windows 맑은 고딕 Bold 폰트 사용)
디자인(2026-10-05 재디자인): 바이그 시그니처 딥 퍼플 단색 배경 + 정중앙에 흰색 'BUYG 바이그' 로고 텍스트만.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "icons" / "og-image.png"
FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"

W, H, S = 1200, 630, 2          # 2배로 그린 뒤 줄여서 글자 가장자리를 매끄럽게
BG = (95, 0, 128)                # #5f0080 시그니처 딥 퍼플
WHITE = (255, 255, 255)
TEXT = "BUYG 바이그"
FONT_SIZE = 150


def main():
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_BOLD, FONT_SIZE * S)
    # 글자 실제 잉크 영역 기준으로 정중앙 배치(기준선 오차 제거)
    l, t, r, b = d.textbbox((0, 0), TEXT, font=font)
    x = (W * S - (r - l)) / 2 - l
    y = (H * S - (b - t)) / 2 - t
    d.text((x, y), TEXT, font=font, fill=WHITE)
    img = img.resize((W, H), Image.LANCZOS)
    OUT.parent.mkdir(exist_ok=True)
    img.save(OUT, optimize=True)
    print("saved", OUT, OUT.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
