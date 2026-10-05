"""SNS 공유용 오픈그래프 대표 이미지(1200x630) 생성 -> icons/og-image.png

실행:  python tools/make_og_image.py   (Pillow 필요, Windows 맑은 고딕 폰트 사용)
디자인: 바이그 브랜드 보라 그라데이션 + 파스텔 원, 좌측 타이포(카카오/모바일 미리보기에서 잘리지 않게
중앙 안전영역 x 110~1090, y 120~510 안에 배치), 우측 캘린더 + 쇼핑백 그래픽.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "icons" / "og-image.png"
FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"
FONT_REG = "C:/Windows/Fonts/malgun.ttf"

W, H, S = 1200, 630, 2  # 2배로 그린 뒤 줄여서 가장자리를 매끄럽게
PURPLE_DARK, PURPLE, PURPLE_LIGHT = (67, 0, 92), (95, 0, 128), (146, 70, 186)
LAVENDER, CORAL, WHITE = (237, 229, 243), (255, 107, 107), (255, 255, 255)


def font(path, size):
    return ImageFont.truetype(path, size * S)


def rr(d, box, r, fill, outline=None, width=0):
    d.rounded_rectangle([v * S for v in box], r * S, fill=fill, outline=outline, width=width * S)


def main():
    img = Image.new("RGB", (W * S, H * S))
    px = img.load()
    # 대각 그라데이션 배경
    for y in range(H * S):
        for x in range(0, W * S):
            t = (x / (W * S) * 0.65 + y / (H * S) * 0.35)
            px[x, y] = tuple(int(PURPLE_DARK[i] + (PURPLE_LIGHT[i] - PURPLE_DARK[i]) * t) for i in range(3))
    d = ImageDraw.Draw(img, "RGBA")
    # 파스텔 장식 원
    for cx, cy, r, a in [(1090, 70, 230, 40), (60, 600, 190, 34), (640, 690, 150, 26)]:
        d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], fill=LAVENDER + (a,))

    # ---- 타이포 (좌측) ----
    x0 = 110
    rr(d, (x0, 150, x0 + 262, 202), 26, WHITE + (40,))
    d.text(((x0 + 22) * S, 176 * S), "0~7세 · 매일 자정 업데이트", font=font(FONT_BOLD, 22), fill=WHITE, anchor="lm")
    d.text((x0 * S, 285 * S), "Buyg | 바이그", font=font(FONT_BOLD, 98), fill=WHITE, anchor="ls")
    d.text((x0 * S, 358 * S), "인스타그램 실시간", font=font(FONT_BOLD, 46), fill=LAVENDER, anchor="ls")
    d.text((x0 * S, 420 * S), "육아 공구 캘린더", font=font(FONT_BOLD, 46), fill=LAVENDER, anchor="ls")
    d.text((x0 * S, 482 * S), "buyg.kr", font=font(FONT_REG, 30), fill=WHITE + (190,), anchor="ls")

    # ---- 캘린더 그래픽 (우측) ----
    cx0, cy0, cx1, cy1 = 790, 140, 1090, 480
    rr(d, (cx0 + 8, cy0 + 14, cx1 + 8, cy1 + 14), 30, (0, 0, 0, 60))  # 그림자
    rr(d, (cx0, cy0, cx1, cy1), 30, WHITE)
    # 상단 헤더 밴드(위쪽 모서리만 둥글게)
    rr(d, (cx0, cy0, cx1, cy0 + 84), 30, CORAL)
    d.rectangle([cx0 * S, (cy0 + 50) * S, cx1 * S, (cy0 + 84) * S], fill=CORAL)
    for rx in (cx0 + 70, cx1 - 70):  # 바인더 고리
        rr(d, (rx - 9, cy0 - 18, rx + 9, cy0 + 30), 9, WHITE)
        rr(d, (rx - 5, cy0 - 14, rx + 5, cy0 + 26), 5, PURPLE_DARK)
    d.text(((cx0 + cx1) / 2 * S, (cy0 + 44) * S), "BUYG", font=font(FONT_BOLD, 36), fill=WHITE, anchor="mm")
    # 날짜 그리드
    gx0, gy0, gw, gh = cx0 + 34, cy0 + 118, 46, 44
    hot = {(1, 2), (2, 4), (3, 3)}      # 하이라이트(마감 임박/오픈 느낌)
    pastel = {(0, 1), (2, 1), (3, 2), (1, 4)}
    for row in range(4):
        for col in range(5):
            x, y = gx0 + col * (gw + 8), gy0 + row * (gh + 10)
            if (row, col) in hot:
                rr(d, (x, y, x + gw, y + gh), 12, CORAL)
                d.text(((x + gw / 2) * S, (y + gh / 2) * S), "%", font=font(FONT_BOLD, 26), fill=WHITE, anchor="mm")
            elif (row, col) in pastel:
                rr(d, (x, y, x + gw, y + gh), 12, LAVENDER)
            else:
                rr(d, (x, y, x + gw, y + gh), 12, (244, 241, 248))

    # ---- 쇼핑백 (캘린더 좌하단에 겹치게) ----
    bx0, by0, bx1, by1 = 735, 370, 855, 500
    rr(d, (bx0 + 6, by0 + 10, bx1 + 6, by1 + 10), 18, (0, 0, 0, 60))
    rr(d, (bx0, by0, bx1, by1), 18, (255, 214, 102))
    d.arc([(bx0 + 28) * S, (by0 - 38) * S, (bx1 - 28) * S, (by0 + 38) * S], 180, 360, fill=PURPLE_DARK, width=9 * S)
    d.text(((bx0 + bx1) / 2 * S, (by0 + 64) * S), "B", font=font(FONT_BOLD, 64), fill=PURPLE_DARK, anchor="mm")

    img = img.resize((W, H), Image.LANCZOS)
    OUT.parent.mkdir(exist_ok=True)
    img.save(OUT, optimize=True)
    print("saved", OUT, OUT.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
