# -*- encoding=utf8 -*-
__author__ = "GGPC"

from airtest.core.api import (
    Template,
    auto_setup,
    exists,
    find_all,
    sleep,
    text,
    touch,
    wait,
)
import sqlite3
from pathlib import Path

auto_setup(__file__)

SEARCH_INPUT = Template(r"tpl1777538707312.png", threshold=0.55)
SEARCH_BUTTON = Template(r"tpl1777535669662.png", threshold=0.55)
RESULT_CARD = Template(r"tpl1777535410888.png", threshold=0.55)
OPEN_CARD = Template(r"tpl1777536126935.png", threshold=0.55)
WX_MARK_1 = Template(r"tpl1777534267289.png", threshold=0.55)
WX_MARK_2 = Template(r"tpl1777534573509.png", threshold=0.55)
CLOSE_BUTTON = Template(r"tpl1777536316927.png", threshold=0.55)


def load_ids(db_name="input.db"):
    db_path = Path(__file__).with_name(db_name)
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


def update_type(row_id, value, db_name="input.db"):
    db_path = Path(__file__).with_name(db_name)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("UPDATE words SET type = ? WHERE id = ?", (value, row_id))
        conn.commit()
    finally:
        conn.close()


def searchID(row_id, game_id):
    touch(SEARCH_INPUT)
    text(str(game_id))
    touch(SEARCH_BUTTON)
    sleep(2.0)

    results = find_all(RESULT_CARD)
    if results and len(results) == 1:
        touch((0.1, 0.5))
        sleep(2.0)
        touch(OPEN_CARD)
        sleep(2.0)

        if exists(WX_MARK_1) or exists(WX_MARK_2):
            update_type(row_id, "wx")
        else:
            update_type(row_id, "qq")

        touch(CLOSE_BUTTON)
        sleep(0.5)


def main():
    rows = load_ids()
    for idx, (row_id, game_id) in enumerate(rows, start=1):
        try:
            print(f"[{idx}/{len(rows)}] search: id={row_id}, content={game_id}")
            searchID(row_id, game_id)
        except Exception as exc:
            print(f"skip {game_id}: {exc}")


if __name__ == "__main__":
    main()




