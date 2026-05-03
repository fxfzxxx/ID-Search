from pathlib import Path
import sqlite3

DB_PATH = Path("input.db")


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute("BEGIN")

        # Ensure source table exists.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS words ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content TEXT NOT NULL, "
            "type TEXT NULL"
            ")"
        )

        # Create a fresh table and reinsert rows in desired order.
        cur.execute("DROP TABLE IF EXISTS words_reordered")
        cur.execute(
            "CREATE TABLE words_reordered ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content TEXT NOT NULL, "
            "type TEXT NULL"
            ")"
        )

        cur.execute(
            "INSERT INTO words_reordered(content, type) "
            "SELECT content, type FROM words "
            "ORDER BY LENGTH(COALESCE(content, '')), id"
        )

        cur.execute("DROP TABLE words")
        cur.execute("ALTER TABLE words_reordered RENAME TO words")
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_words_content_unique ON words(content)"
        )

        cur.execute("SELECT COUNT(*) FROM words")
        total = int(cur.fetchone()[0])

        cur.execute(
            "SELECT id, content, LENGTH(COALESCE(content, '')) AS n "
            "FROM words ORDER BY id LIMIT 10"
        )
        head = cur.fetchall()

        conn.commit()

        print(f"reordered_rows={total}")
        for row in head:
            print(f"head_row={row}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
