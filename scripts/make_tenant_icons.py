"""임차인 앱 바로가기 아이콘(PNG)을 순수 파이썬으로 만든다 — 파란 바탕 + 흰 집 모양.
실행: py -3 scripts/make_tenant_icons.py   → apps/rent_dist/rent3_tenant/frontend/icons/*.png
"""
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps", "rent_dist", "rent3_tenant", "frontend", "icons")
BLUE, BLUE2, WHITE, DOOR = (37, 99, 235), (29, 78, 216), (255, 255, 255), (37, 99, 235)


def png(w, h, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def in_round(x, y, size, r):
    if r <= 0:
        return True
    cx = min(max(x, r), size - 1 - r)
    cy = min(max(y, r), size - 1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def house(size, scale, radius):
    """scale: 집이 차지하는 비율(마스크용은 안전 영역 때문에 작게). radius: 모서리 둥글기(0=꽉 채움)."""
    rows = []
    s = size
    u = s * scale / 10000.0         # 집 그림 단위(0..100 좌표 1칸의 픽셀 수)
    ox, oy = s / 2 - 50 * u, s / 2 - 48 * u
    for y in range(s):
        row = []
        for x in range(s):
            if not in_round(x, y, s, radius):
                row += (255, 255, 255)
                continue
            t = y / (s - 1)
            col = tuple(int(BLUE[i] + (BLUE2[i] - BLUE[i]) * t) for i in range(3))
            gx, gy = (x - ox) / u, (y - oy) / u            # 0..100 좌표계
            roof = 4 <= gy <= 42 and abs(gx - 50) <= (gy - 4) * 1.32 + 0.5 and gy >= 4
            body = 40 <= gy <= 92 and 16 <= gx <= 84
            door = 62 <= gy <= 92 and 42 <= gx <= 58
            chim = 10 <= gy <= 30 and 70 <= gx <= 80
            if door:
                col = DOOR
            elif roof or body or chim:
                col = WHITE
            row += col
        rows.append(row)
    return png(s, s, rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    files = {
        "icon-192.png": house(192, 62, 40),
        "icon-512.png": house(512, 62, 104),
        "icon-maskable-512.png": house(512, 46, 0),   # 가장자리가 잘려도 되도록 집을 안쪽에
        "apple-touch-icon.png": house(180, 56, 0),    # iOS 가 모서리를 알아서 둥글게 한다
    }
    for name, data in files.items():
        with open(os.path.join(OUT, name), "wb") as f:
            f.write(data)
        print(name, len(data))


if __name__ == "__main__":
    main()
