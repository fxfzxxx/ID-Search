import argparse
import base64
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np


DB_NAME = "input.db"

SEARCH_INPUT = "tpl1777538707312.png"
SEARCH_BUTTON = "tpl1777535669662.png"
RESULT_CARD = "tpl1777535410888.png"
OPEN_CARD = "tpl1777536126935.png"
WX_MARK_1 = "tpl1777534267289.png"
WX_MARK_2 = "tpl1777548253105.png"
CLOSE_BUTTON = "tpl1777536316927.png"


def parse_device_uri(uri: str):
    # Supports: android://127.0.0.1:5037/R83Y80GC52R
    m = re.match(r"^android://[^:]+:(\d+)/(\S+)$", uri)
    if m:
        return int(m.group(1)), m.group(2)
    # Also allow direct serial
    return 5037, uri


class AdbClient:
    def __init__(self, serial: str, port: int = 5037):
        self.serial = serial
        self.port = port
        self._ime_cache = None

    def _base(self):
        cmd = ["adb"]
        if self.port != 5037:
            cmd += ["-P", str(self.port)]
        cmd += ["-s", self.serial]
        return cmd

    def run(self, args, check=True):
        cmd = self._base() + args
        p = subprocess.run(cmd, capture_output=True)
        if check and p.returncode != 0:
            raise RuntimeError(p.stderr.decode("utf-8", errors="ignore").strip())
        return p

    def screenshot(self):
        for _ in range(2):
            p = self.run(["exec-out", "screencap", "-p"], check=True)
            raw = p.stdout
            if not raw:
                continue

            candidates = [
                raw,
                raw.replace(b"\r\r\n", b"\n"),
                raw.replace(b"\r\n", b"\n"),
                raw.replace(b"\r", b""),
            ]
            for data in candidates:
                arr = np.frombuffer(data, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is not None:
                    return img

            time.sleep(0.2)

        raise RuntimeError("Failed to decode screenshot")

    def tap(self, x: int, y: int):
        self.run(["shell", "input", "tap", str(x), str(y)], check=True)

    def list_imes(self):
        if self._ime_cache is not None:
            return self._ime_cache
        p = self.run(["shell", "ime", "list", "-s"], check=False)
        out = (p.stdout + p.stderr).decode("utf-8", errors="ignore")
        self._ime_cache = [line.strip() for line in out.splitlines() if line.strip()]
        return self._ime_cache

    def has_unicode_bridge(self):
        # Common ADB keyboard identifiers.
        markers = ("adbkeyboard", "adb_ime", "adbime")
        for ime in self.list_imes():
            low = ime.lower()
            if any(m in low for m in markers):
                return True
        return False

    def text(self, value: str):
        value = str(value)
        if not value:
            return

        # Only use broadcast input when a known unicode-capable bridge exists.
        if self.has_unicode_bridge():
            payload = base64.b64encode(value.encode("utf-8")).decode("ascii")
            p = self.run(
                [
                    "shell",
                    "am",
                    "broadcast",
                    "-a",
                    "ADB_INPUT_B64",
                    "-p",
                    "com.android.adbkeyboard",
                    "--es",
                    "msg",
                    payload,
                ],
                check=False,
            )
            out = (p.stdout + p.stderr).decode("utf-8", errors="ignore")
            if p.returncode == 0 and "Broadcast" in out and "Exception" not in out:
                return

        # Fallback for ASCII text only.
        safe = re.sub(r"[^0-9A-Za-z_\-\.]", "", value)
        if safe != value:
            raise RuntimeError(
                "Cannot input non-ASCII text: install and enable ADBKeyboard, then retry"
            )
        self.run(["shell", "input", "text", safe], check=True)

    def clear_text_field(self, fallback_chars: int = 8):
        # Primary path: Ctrl+A + Delete.
        self.run(["shell", "input", "keycombination", "113", "29"], check=False)
        self.run(["shell", "input", "keyevent", "67"], check=False)

        # Fallback path: small number of deletes for devices where keycombination is ignored.
        self.run(["shell", "input", "keyevent", "123"], check=False)
        for _ in range(fallback_chars):
            self.run(["shell", "input", "keyevent", "67"], check=False)

        # Clear a few characters on the right side in case cursor did not move to end.
        self.run(["shell", "input", "keyevent", "122"], check=False)
        for _ in range(fallback_chars):
            self.run(["shell", "input", "keyevent", "112"], check=False)


def load_template(path: Path):
    # cv2.imread may fail on Windows paths containing non-ASCII characters.
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Template not found: {path}")
    return img


def match_best(screen, templ, threshold):
    res = cv2.matchTemplate(screen, templ, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val < threshold:
        return None
    th, tw = templ.shape[:2]
    cx = max_loc[0] + tw // 2
    cy = max_loc[1] + th // 2
    return (cx, cy, float(max_val))


def match_all(screen, templ, threshold, min_distance=30):
    res = cv2.matchTemplate(screen, templ, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= threshold)
    points = []
    th, tw = templ.shape[:2]
    for y, x in zip(ys, xs):
        cx = int(x + tw // 2)
        cy = int(y + th // 2)
        conf = float(res[y, x])
        points.append((cx, cy, conf))
    points.sort(key=lambda x: x[2], reverse=True)

    picked = []
    for p in points:
        if all(
            (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 >= min_distance**2 for q in picked
        ):
            picked.append(p)
    return picked


def wait_template(adb: AdbClient, templ, threshold=0.55, timeout=20.0, interval=0.5):
    end = time.time() + timeout
    while time.time() < end:
        screen = adb.screenshot()
        hit = match_best(screen, templ, threshold)
        if hit:
            return hit, screen
        time.sleep(interval)
    return None, None


def load_rows(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS words ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content TEXT NOT NULL, "
            "type TEXT NULL"
            ")"
        )
        conn.commit()
        cur.execute("SELECT id, content FROM words WHERE content <> '' ORDER BY id")
        return cur.fetchall()
    finally:
        conn.close()


def update_type(db_path: Path, row_id: int, value: str):
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("UPDATE words SET type = ? WHERE id = ?", (value, row_id))
        conn.commit()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Pure Python ADB automation without Airtest"
    )
    parser.add_argument(
        "--device", required=True, help="android://127.0.0.1:5037/SERIAL or SERIAL"
    )
    parser.add_argument("--db", default=DB_NAME)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--limit", type=int, default=0, help="0 means no limit")
    args = parser.parse_args()

    port, serial = parse_device_uri(args.device)
    adb = AdbClient(serial=serial, port=port)

    base = Path(__file__).resolve().parent
    db_path = base / args.db

    templ = {
        "search_input": load_template(base / SEARCH_INPUT),
        "search_button": load_template(base / SEARCH_BUTTON),
        "result_card": load_template(base / RESULT_CARD),
        "open_card": load_template(base / OPEN_CARD),
        "wx1": load_template(base / WX_MARK_1),
        "wx2": load_template(base / WX_MARK_2),
        "close": load_template(base / CLOSE_BUTTON),
    }

    rows = load_rows(db_path)
    if args.limit > 0:
        rows = rows[: args.limit]

    for idx, (row_id, game_id) in enumerate(rows, start=1):
        try:
            print(f"[{idx}/{len(rows)}] search: id={row_id}, content={game_id}")

            hit, _ = wait_template(
                adb, templ["search_input"], threshold=args.threshold, timeout=20
            )
            if not hit:
                print(f"skip {game_id}: search input not found")
                continue
            adb.tap(hit[0], hit[1])
            time.sleep(0.3)
            adb.clear_text_field()
            adb.text(str(game_id))

            hit_btn, _ = wait_template(
                adb, templ["search_button"], threshold=args.threshold, timeout=5
            )
            if not hit_btn:
                print(f"skip {game_id}: search button not found")
                continue
            adb.tap(hit_btn[0], hit_btn[1])
            time.sleep(2.0)

            screen = adb.screenshot()
            cards = match_all(
                screen, templ["result_card"], args.threshold, min_distance=50
            )
            if len(cards) != 2:
                print(f"skip {game_id}: result cards={len(cards)}")
                continue

            h, w = screen.shape[:2]
            adb.tap(int(w * 0.1), int(h * 0.5))
            time.sleep(1.5)

            hit_open, _ = wait_template(
                adb, templ["open_card"], threshold=args.threshold, timeout=5
            )
            if not hit_open:
                print(f"skip {game_id}: open card button not found")
                continue
            adb.tap(hit_open[0], hit_open[1])
            time.sleep(2.0)

            profile = adb.screenshot()
            wx = match_best(profile, templ["wx1"], args.threshold) or match_best(
                profile, templ["wx2"], args.threshold
            )
            update_type(db_path, row_id, "wx" if wx else "qq")

            hit_close, _ = wait_template(
                adb, templ["close"], threshold=args.threshold, timeout=5
            )
            time.sleep(4)
            if hit_close:
                adb.tap(hit_close[0], hit_close[1])
            time.sleep(0.5)
        except Exception as exc:
            print(f"skip {game_id}: {exc}")


if __name__ == "__main__":
    main()
