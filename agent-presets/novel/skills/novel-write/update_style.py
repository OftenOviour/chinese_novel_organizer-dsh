#!/usr/bin/env python3
"""将根目录 style.db 的新增参考引例合并到 novel-cli/style.db。

合并逻辑：
- 只会处理 source_file IS NULL 的引例（extract 产出的参考数据）
- 比较 fragment 文本：工作区已存在相同文本 → 跳过
- 新的引例 → 插入，同时复制其所有标签关联
- 工作区自定义引例（source_file IS NOT NULL）→ 原样保留

用法：
  python update_style.py
"""

import os
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = SCRIPT_DIR.parent
ROOT_DB = PROJECT_ROOT / "style.db"
WORKING_DB = SCRIPT_DIR / "style.db"


def get_db(path):
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    return db


def ensure_tables(db):
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
    if not ROOT_DB.exists():
        print(f"[update-style] 根目录 style.db 不存在: {ROOT_DB}")
        print("[update-style] 请先运行 extract-writing-style 生成参考数据库")
        sys.exit(1)
    if not WORKING_DB.exists():
        print(f"[update-style] 工作区 style.db 不存在: {WORKING_DB}")
        print("[update-style] 请先运行 novel-cli init")
        sys.exit(1)

    root_db = get_db(ROOT_DB)
    work_db = get_db(WORKING_DB)
    ensure_tables(work_db)

    # 获取根目录中所有参考引例（source_file IS NULL）
    ref_fragments = root_db.execute(
        "SELECT id, fragment, dimension, position, note FROM style_fragments WHERE source_file IS NULL"
    ).fetchall()

    if not ref_fragments:
        print("[update-style] 根目录 style.db 中无参考引例（source_file IS NULL）")
        root_db.close()
        work_db.close()
        return

    # 获取工作区中已有的引例文本（用于去重）
    existing_texts = set()
    for row in work_db.execute("SELECT fragment FROM style_fragments").fetchall():
        existing_texts.add(row["fragment"])

    added = 0
    skipped = 0

    for ref in ref_fragments:
        if ref["fragment"] in existing_texts:
            skipped += 1
            continue

        # 插入引例
        work_db.execute(
            "INSERT INTO style_fragments (source_file, fragment, dimension, position, note) VALUES (NULL, ?, ?, ?, ?)",
            (ref["fragment"], ref["dimension"], ref["position"], ref["note"])
        )
        new_id = work_db.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 复制标签
        root_tags = root_db.execute(
            """SELECT t.name FROM style_tags t
               JOIN fragment_tags ft ON ft.tag_id = t.id
               WHERE ft.fragment_id = ?""",
            (ref["id"],)
        ).fetchall()

        for tag_row in root_tags:
            tag_name = tag_row["name"]
            tag = work_db.execute("SELECT id FROM style_tags WHERE name = ?", (tag_name,)).fetchone()
            if tag:
                tid = tag["id"]
            else:
                work_db.execute("INSERT INTO style_tags (name) VALUES (?)", (tag_name,))
                tid = work_db.execute("SELECT last_insert_rowid()").fetchone()[0]
            work_db.execute(
                "INSERT OR IGNORE INTO fragment_tags (fragment_id, tag_id) VALUES (?, ?)",
                (new_id, tid)
            )

        added += 1

    work_db.commit()
    root_db.close()
    work_db.close()

    print(f"[update-style] 合并完成: 新增 {added} 条, 跳过 {skipped} 条 (共 {len(ref_fragments)} 条参考引例)")


if __name__ == "__main__":
    main()
