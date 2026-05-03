from pathlib import Path
import sqlite3

SOURCE_PATHS = [
    Path("xiandaihaiyuchangyongcibiao.txt"),
    Path("level-1.txt"),
    Path("level-2.txt"),
    Path("level-3.txt"),
]
DB_PATH = Path("input.db")


def extract_entries(path: Path) -> list[str]:
    entries: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue

        first_col = raw.split("\t", 1)[0].strip()
        if first_col:
            entries.append(first_col)

    # De-duplicate while preserving file order.
    return list(dict.fromkeys(entries))


def extract_entries_from_sources(paths: list[Path]) -> tuple[list[str], dict[str, int]]:
    merged: list[str] = []
    per_file_counts: dict[str, int] = {}

    for path in paths:
        if not path.exists():
            per_file_counts[str(path)] = 0
            continue
        file_entries = extract_entries(path)
        per_file_counts[str(path)] = len(file_entries)
        merged.extend(file_entries)

    # De-duplicate while preserving cross-file order.
    merged_unique = list(dict.fromkeys(merged))
    return merged_unique, per_file_counts


def ensure_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(
        "CREATE TABLE IF NOT EXISTS words ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "content TEXT NOT NULL, "
        "type TEXT NULL"
        ")"
    )


def remove_duplicate_rows(cur: sqlite3.Cursor) -> int:
    cur.execute(
        "DELETE FROM words "
        "WHERE id NOT IN ("
        "  SELECT MIN(id) FROM words GROUP BY content"
        ")"
    )
    return cur.rowcount if cur.rowcount is not None else 0


def ensure_unique_index(cur: sqlite3.Cursor) -> None:
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_words_content_unique ON words(content)"
    )


def import_entries(entries: list[str]) -> tuple[int, int, int]:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        ensure_schema(cur)

        removed = remove_duplicate_rows(cur)
        ensure_unique_index(cur)

        cur.executemany(
            "INSERT OR IGNORE INTO words(content, type) VALUES (?, NULL)",
            [(entry,) for entry in entries],
        )
        inserted = cur.rowcount if cur.rowcount is not None else 0

        cur.execute("SELECT COUNT(*) FROM words")
        total = int(cur.fetchone()[0])

        conn.commit()
        return removed, inserted, total
    finally:
        conn.close()


def main() -> None:
    existing_sources = [p for p in SOURCE_PATHS if p.exists()]
    if not existing_sources:
        raise FileNotFoundError("No source files found")

    entries, per_file_counts = extract_entries_from_sources(SOURCE_PATHS)
    removed, inserted, total = import_entries(entries)

    for file_name, count in per_file_counts.items():
        print(f"source_entries[{file_name}]={count}")
    print(f"source_unique_entries={len(entries)}")
    print(f"removed_existing_duplicates={removed}")
    print(f"inserted_new_rows={inserted}")
    print(f"db_total_rows={total}")


if __name__ == "__main__":
    main()
