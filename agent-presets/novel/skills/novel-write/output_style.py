#!/usr/bin/env python3
"""从工作区 style.db 提炼主要引例，输出并覆盖根目录 style.db。

- 所有 source_file IS NULL 的引例自动保留
- 通过 --ids 指定要提升为参考的自定义引例
- 输出的引例全部转为无源（source_file=NULL, position=NULL）
- 覆盖模式：根目录 style.db 被完全替换

用法：
  python output_style.py                 仅导出所有无源引例
  python output_style.py --ids 6,7,9     无源引例 + 指定 ID 的自定义引例
  python output_style.py --all            全部引例（无源 + 所有自定义）
"""

import os
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = SCRIPT_DIR.parent
WORKING_DB = SCRIPT_DIR / "style.db"
ROOT_DB = PROJECT_ROOT / "style.db"


def get_db(path):
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    return db


def create_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS style_fragments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT,
            fragment    TEXT NOT NULL,
            dimension   TEXT,
            position    TEXT,
            note        TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_fragments_dim ON style_fragments(dimension);

        CREATE TABLE IF NOT EXISTS style_tags (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS fragment_tags (
            fragment_id INTEGER NOT NULL REFERENCES style_fragments(id) ON DELETE CASCADE,
            tag_id      INTEGER NOT NULL REFERENCES style_tags(id) ON DELETE CASCADE,
            PRIMARY KEY (fragment_id, tag_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ft_tag ON fragment_tags(tag_id);
        CREATE INDEX IF NOT EXISTS idx_ft_frag ON fragment_tags(fragment_id);
    """)
    db.commit()


def main():
    if not WORKING_DB.exists():
        print(f"[output-style] 工作区 style.db 不存在: {WORKING_DB}")
        sys.exit(1)

    args = sys.argv[1:]
    include_ids = set()
    include_all = False

    it = iter(args)
    for a in it:
        if a == "--ids":
            ids_str = next(it)
            include_ids = {int(x.strip()) for x in ids_str.split(",") if x.strip()}
        elif a == "--all":
            include_all = True

    work_db = get_db(WORKING_DB)

    # 收集要导出的引例
    fragments_to_export = []

    # 1) 所有无源引例（自动保留）
    source_less = work_db.execute(
        "SELECT * FROM style_fragments WHERE source_file IS NULL ORDER BY id"
    ).fetchall()
    fragments_to_export.extend(source_less)

    # 2) 指定的自定义引例或全部自定义引例
    if include_all:
        custom = work_db.execute(
            "SELECT * FROM style_fragments WHERE source_file IS NOT NULL ORDER BY id"
        ).fetchall()
        fragments_to_export.extend(custom)
    elif include_ids:
        placeholders = ",".join("?" * len(include_ids))
        custom = work_db.execute(
            f"SELECT * FROM style_fragments WHERE id IN ({placeholders}) ORDER BY id",
            list(include_ids)
        ).fetchall()
        fragments_to_export.extend(custom)

    if not fragments_to_export:
        print("[output-style] 没有要导出的引例")
        work_db.close()
        return

    # 准备根目录 style.db（覆盖）
    if ROOT_DB.exists():
        ROOT_DB.unlink()

    root_db = get_db(ROOT_DB)
    create_tables(root_db)

    # 收集所有涉及的标签（从工作区复制）
    tag_map = {}  # old_tag_id → new_tag_id
    for frag in fragments_to_export:
        tags = work_db.execute(
            "SELECT t.id, t.name FROM style_tags t "
            "JOIN fragment_tags ft ON ft.tag_id = t.id "
            "WHERE ft.fragment_id = ?", (frag["id"],)
        ).fetchall()
        for t in tags:
            if t["id"] not in tag_map:
                row = root_db.execute(
                    "SELECT id FROM style_tags WHERE name = ?", (t["name"],)
                ).fetchone()
                if row:
                    tag_map[t["id"]] = row["id"]
                else:
                    root_db.execute("INSERT INTO style_tags (name) VALUES (?)", (t["name"],))
                    tag_map[t["id"]] = root_db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 写入引例（全部转为无源）
    exported = 0
    for frag in fragments_to_export:
        root_db.execute(
            "INSERT INTO style_fragments (source_file, fragment, dimension, position, note) "
            "VALUES (NULL, ?, ?, NULL, ?)",
            (frag["fragment"], frag["dimension"], frag["note"])
        )
        new_id = root_db.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 复制标签关联
        tags = work_db.execute(
            "SELECT tag_id FROM fragment_tags WHERE fragment_id = ?", (frag["id"],)
        ).fetchall()
        for t in tags:
            new_tid = tag_map.get(t["tag_id"])
            if new_tid:
                root_db.execute(
                    "INSERT OR IGNORE INTO fragment_tags (fragment_id, tag_id) VALUES (?, ?)",
                    (new_id, new_tid)
                )
        exported += 1

    root_db.commit()
    work_db.close()
    root_db.close()

    print(f"[output-style] 已导出 {exported} 条引例 → {ROOT_DB}")
    print(f"            无源引例: {len(source_less)} 条（全部保留）")
    print(f"            自定义引例: {exported - len(source_less)} 条")
    print(f"            全部已转为无源")


if __name__ == "__main__":
    main()
