#!/usr/bin/env python3
"""novel-cli — 小说项目管理工具

所有结构化数据存 SQLite，agent 通过子命令操作，避免手动文件 I/O 出错。

用法:
  python cli.py init [项目路径]                          初始化项目骨架
  python cli.py entry add <type> <name>                 创建条目
  python cli.py entry get <name> [--type TYPE]          查看条目属性
  python cli.py entry set <name> <dim> <key> <value>    设置/追加属性
  python cli.py entry del <name> <dim> [--key KEY]      删除维度或属性
  python cli.py entry list [--type TYPE]                列出条目
  python cli.py entry search <keyword>                  搜索条目
  python cli.py plot create <L1-L4> <title> [opts]      创建剧情节点
  python cli.py plot get <id>                           查看节点
  python cli.py plot list [--level X] [--parent ID]     列出节点
  python cli.py plot status <id> <status>               更新状态
  python cli.py publish <id>                            发布正文到 contents/
  python cli.py log append <level> <op> <target> <note> 追加日志
  python cli.py log show [--tail N] [--date YMD]        查看日志
  python cli.py log rotate [--force]                    日志轮转
  python cli.py validate                                完整性检查
  python cli.py export [entries|plots|all]              导出 Markdown
  python cli.py import <旧项目路径>                      迁移旧数据
"""

import json
import os
import re
import sqlite3
import sys
import textwrap
from datetime import date, datetime
from pathlib import Path


# ── 路径解析 ─────────────────────────────────────────────

def _cli_dir():
    return Path(os.path.dirname(os.path.abspath(__file__)))


def _project_root():
    """cli.py 在 novel-cli/ 下，项目根目录是上级。"""
    return _cli_dir().parent


def _nodes_db_path():
    return _cli_dir() / "nodes.db"


def _materials_db_path():
    return _cli_dir() / "materials.db"


def _log_dir():
    return _cli_dir() / "log"


def _plots_dir():
    return _cli_dir() / "plots"


def _entries_dir():
    return _cli_dir() / "entries"


def _contents_dir():
    return _project_root() / "contents"


# ── 数据库 ────────────────────────────────────────────────

def get_nodes_db():
    """节点库连接（剧情树/引用/事实——高频状态变更）。"""
    db = sqlite3.connect(str(_nodes_db_path()))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def get_materials_db():
    """素材库连接（条目/维度/类型字典——低频设定维护）。"""
    db = sqlite3.connect(str(_materials_db_path()))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


# 兼容旧名：指向素材库（新代码应使用 get_nodes_db / get_materials_db）
def get_db():
    return get_materials_db()


def init_materials_db(db):
    """素材库（materials.db）：类型字典 + 条目 + 维度。"""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS types (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT NOT NULL CHECK(kind IN ('entry','dimension')),
            type        TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(kind, type)
        );

        CREATE TABLE IF NOT EXISTS entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_type  TEXT NOT NULL,
            name        TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(entry_type, name)
        );
        CREATE INDEX IF NOT EXISTS idx_entries_type ON entries(entry_type);
        CREATE INDEX IF NOT EXISTS idx_entries_name ON entries(name);

        CREATE TABLE IF NOT EXISTS dimensions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id    INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            dim_name    TEXT NOT NULL,
            properties  TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(entry_id, dim_name)
        );
        CREATE INDEX IF NOT EXISTS idx_dimensions_entry ON dimensions(entry_id);
    """)
    db.commit()


def init_nodes_db(db):
    """节点库（nodes.db）：剧情树 + 引用关系 + 事实账本。"""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS plot_nodes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            level       TEXT NOT NULL CHECK(level IN ('L1','L2','L3','L4')),
            title       TEXT NOT NULL,
            parent_id   INTEGER REFERENCES plot_nodes(id),
            volume_num  INTEGER,
            sort_order  INTEGER,
            file_path   TEXT,
            status      TEXT DEFAULT 'draft' CHECK(status IN ('draft','confirmed','revised','affected','published')),
            affected_reason TEXT,
            content_hash TEXT,
            content_size INTEGER,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_plot_level ON plot_nodes(level);
        CREATE INDEX IF NOT EXISTS idx_plot_parent ON plot_nodes(parent_id);

        -- 引用关系表：L4 的主引用（与 parent_id 冗余）与次引用都存这里；
        -- 从 L3 反向查所有关联 L4 只查此表（kind=main|secondary）
        CREATE TABLE IF NOT EXISTS "references" (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id     INTEGER NOT NULL REFERENCES plot_nodes(id) ON DELETE CASCADE,
            to_id       INTEGER NOT NULL REFERENCES plot_nodes(id) ON DELETE CASCADE,
            kind        TEXT NOT NULL CHECK(kind IN ('main','secondary')),
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(from_id, to_id, kind)
        );
        CREATE INDEX IF NOT EXISTS idx_refs_to ON "references"(to_id);
        CREATE INDEX IF NOT EXISTS idx_refs_from ON "references"(from_id);

        -- 事实账本：节点确认/发布时写"已完成事项"；source_node_id 记录"何时成了既成事实"
        CREATE TABLE IF NOT EXISTS facts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            text        TEXT NOT NULL,
            category    TEXT NOT NULL DEFAULT 'completed' CHECK(category IN ('completed','revealed','state_changed')),
            source_node_id INTEGER REFERENCES plot_nodes(id) ON DELETE SET NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source_node_id);

        -- 事实消费追踪：context 命令注入 facts 时记录"该节点消费了哪些事实"
        -- 改动级联第二跳：改某节点 → fact list --source → 查消费这些事实的节点 → 精准标 affected
        CREATE TABLE IF NOT EXISTS fact_consumption (
            fact_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
            node_id INTEGER NOT NULL REFERENCES plot_nodes(id) ON DELETE CASCADE,
            PRIMARY KEY (fact_id, node_id)
        );

        -- 文本替换映射（通用：角色名/地名/道具名/专有名词等用户可能改名的实体）
        -- 写作时用 {{key}} 占位符，发布时替换为 display；改名只改这条记录
        CREATE TABLE IF NOT EXISTS translation (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT NOT NULL UNIQUE,
            display     TEXT NOT NULL,
            note        TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
    """)
    # 兼容已运行项目：为旧库补 content_hash / content_size 列（幂等）
    _ensure_column(db, "plot_nodes", "content_hash", "TEXT")
    _ensure_column(db, "plot_nodes", "content_size", "INTEGER")
    db.commit()


def _ensure_column(db, table, column, ddl):
    """幂等补列：旧库升级时使用。"""
    # 用位置索引 r[1]（cid, name, type, notnull, dflt_value, pk）：不依赖连接是否设置
    # row_factory=sqlite3.Row——cmd_init 用裸连接，r["name"] 会 TypeError（tuple indices）
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _file_hash(path):
    """内容指纹（md5，原始字节）——用于检测文件是否被外部修改（比 mtime 可靠，不受打开影响）。"""
    import hashlib
    try:
        return hashlib.md5(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _refresh_node_hash(db, node_id):
    """用当前文件内容刷新节点的 content_hash / content_size（状态变更/创建时"认可当前内容"）。"""
    row = db.execute("SELECT file_path FROM plot_nodes WHERE id = ?", (node_id,)).fetchone()
    if not row or not row["file_path"]:
        return
    path = _cli_dir() / row["file_path"]
    size = path.stat().st_size if path.exists() else None
    h = _file_hash(path)
    if h:
        db.execute(
            "UPDATE plot_nodes SET content_hash = ?, content_size = ? WHERE id = ?",
            (h, size, node_id)
        )


def _content_drift(node):
    """对比节点记录与当前文件：文件被外部修改返回 True。

    性能优化：大小先行——stat 比较（O(1)、不读内容），大小变了直接判漂移；
    只有等大小微调（改几个字）才走 md5 全文件确认。频繁编辑路径成本降至 stat。
    """
    if not node["file_path"]:
        return False
    path = _cli_dir() / node["file_path"]
    if not path.exists():
        return False
    if node["content_size"] is not None and path.stat().st_size != node["content_size"]:
        return True
    if not node["content_hash"]:
        return False
    h = _file_hash(path)
    return h is not None and h != node["content_hash"]


def init_db(db):
    """兼容旧入口：同时初始化两个库。"""
    init_materials_db(db)
    init_nodes_db(db)


# ── 日志 ──────────────────────────────────────────────────

def _today_str():
    return date.today().isoformat()


def _now_str():
    # 日志行只带时分（日期由 latest 头部时间戳/归档文件名承载）
    return datetime.now().strftime("%H:%M")


def _latest_log():
    return _log_dir() / "latest.log"


def _rotate_log(force=False):
    """轮转日志：读 latest 头部的时间戳日期（★ 不用文件修改时间），
    非今天则把 latest 归档为 {日期}.log 并新建带日期头的 latest。

    日期头只在创建 latest 时写一次（如 [2026-08-17]），日志行不再逐行带日期。
    """
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    latest = _latest_log()

    if latest.exists() and latest.stat().st_size > 0:
        # 从头部第一行提取日期（打开/触摸文件不会影响判断）
        first_line = latest.read_text(encoding="utf-8-sig").strip().split("\n")[0]
        m = re.match(r"^\[(\d{4}-\d{2}-\d{2})\]", first_line)
        log_date = date.fromisoformat(m.group(1)) if m else None
        if not force and log_date is not None and log_date >= date.today():
            return
        # 归档名：头部日期（读不到则用今天，force 时兜底）
        archive_date = log_date if log_date is not None else date.today()
        archive = log_dir / f"{archive_date.isoformat()}.log"
        if archive.exists():
            ts = datetime.now().strftime("%H%M%S")
            archive = log_dir / f"{archive_date.isoformat()}-{ts}.log"
        latest.rename(archive)

    # 新建 latest：写一次日期头
    latest.write_text(f"[{_today_str()}]\n", encoding="utf-8")


def log_append(level, op_type, target, note):
    """追加一行日志，自动轮转。"""
    _rotate_log()
    line = f"[{_now_str()}] {level} | {op_type} | {target} | {note}\n"
    with open(str(_latest_log()), "a", encoding="utf-8") as f:
        f.write(line)


def log_show(tail=0, date_str=None, today=False):
    """查看日志。today=True 读当日日志（latest.log）；date_str 读指定日期轮转文件。"""
    log_dir = _log_dir()
    if today:
        log_file = _latest_log()
    elif date_str:
        log_file = log_dir / f"{date_str}.log"
    else:
        log_file = _latest_log()

    if not log_file.exists():
        print(f"[novel-cli] 日志文件不存在: {log_file}")
        return

    lines = log_file.read_text(encoding="utf-8-sig").strip().split("\n")
    if tail and tail > 0:
        lines = lines[-tail:]
    for line in lines:
        print(line)


# ── 条目操作 ──────────────────────────────────────────────

ENTRY_TYPES = {"character": "角色", "location": "地点", "item": "道具", "concept": "概念", "scene": "场景模型", "foreshadow": "伏笔"}


def type_add(kind, type_name, description):
    """登记一个 entry/dimension 类型的含义（types 表 = 类型字典）。"""
    if kind not in ("entry", "dimension"):
        print(f"[novel-cli] 无效 kind: {kind}（允许: entry, dimension）")
        sys.exit(1)
    db = get_materials_db()
    try:
        db.execute("INSERT INTO types (kind, type, description) VALUES (?, ?, ?)", (kind, type_name, description))
        db.commit()
        print(f"[novel-cli] 已登记 {kind} 类型: {type_name} — {description}")
        log_append("素材库", "创建", f"type.{kind}.{type_name}", f"登记类型: {description}")
    except sqlite3.IntegrityError:
        db.execute("UPDATE types SET description = ? WHERE kind = ? AND type = ?", (description, kind, type_name))
        db.commit()
        print(f"[novel-cli] 更新 {kind} 类型描述: {type_name} — {description}")
    finally:
        db.close()


def type_list():
    """列出全部已登记类型。"""
    db = get_materials_db()
    rows = db.execute("SELECT kind, type, description FROM types ORDER BY kind, type").fetchall()
    if not rows:
        print("[novel-cli] (类型字典为空)")
        db.close()
        return
    print("类型字典")
    print("=" * 60)
    for r in rows:
        print(f"  [{r['kind']}] {r['type']} — {r['description']}")
    db.close()


def _warn_unregistered_type(db, kind, type_name):
    """类型未登记时提示（不强制）。"""
    row = db.execute("SELECT 1 FROM types WHERE kind = ? AND type = ?", (kind, type_name)).fetchone()
    if not row:
        print(f"[novel-cli] 提示: 类型 '{type_name}'（{kind}）尚未登记，建议: cli.py type add {kind} {type_name} \"含义说明\"")


def entry_add(entry_type, name):
    """类型自由：agent 可创建任意条目类型，新类型建议先 type add 登记含义。"""
    db = get_materials_db()
    try:
        db.execute("INSERT INTO entries (entry_type, name) VALUES (?, ?)", (entry_type, name))
        db.commit()
        label = ENTRY_TYPES.get(entry_type, entry_type)
        print(f"[novel-cli] 已创建{label}: {name}")
        log_append("素材库", "创建", f"{entry_type}.{name}", f"新建{label}")
        _warn_unregistered_type(db, "entry", entry_type)
    except sqlite3.IntegrityError:
        label = ENTRY_TYPES.get(entry_type, entry_type)
        print(f"[novel-cli] {label} '{name}' 已存在")
        sys.exit(1)
    finally:
        db.close()


def entry_get(name, entry_type=None):
    db = get_materials_db()
    query = "SELECT id, entry_type, name, updated_at FROM entries WHERE name = ?"
    params = [name]
    if entry_type:
        query += " AND entry_type = ?"
        params.append(entry_type)
    row = db.execute(query, params).fetchone()
    if not row:
        print(f"[novel-cli] 未找到条目: {name}" + (f" (类型: {entry_type})" if entry_type else ""))
        db.close()
        return

    print(f"{'='*60}")
    print(f"  {ENTRY_TYPES.get(row['entry_type'], row['entry_type'])}: {row['name']}")
    print(f"  最后更新: {row['updated_at']}")
    print(f"{'='*60}")

    dims = db.execute(
        "SELECT dim_name, properties FROM dimensions WHERE entry_id = ? ORDER BY dim_name",
        (row["id"],)
    ).fetchall()
    if not dims:
        print("  (暂无属性)")
    for d in dims:
        props = json.loads(d["properties"])
        print(f"\n  [{d['dim_name']}]")
        for k, v in props.items():
            if isinstance(v, list):
                for i, item in enumerate(v, 1):
                    print(f"    {k} [{i}]: {item}")
            else:
                print(f"    {k}: {v}")
    print()
    db.close()


def entry_append(name, dim_name, key, value):
    """同一 key 追加一条描述（多行）；有效区间等语义写进 value 文本。"""
    db = get_materials_db()
    row = db.execute("SELECT id, entry_type FROM entries WHERE name = ?", (name,)).fetchone()
    if not row:
        print(f"[novel-cli] 未找到条目: {name}")
        db.close()
        sys.exit(1)

    entry_id = row["id"]
    dim = db.execute(
        "SELECT id, properties FROM dimensions WHERE entry_id = ? AND dim_name = ?",
        (entry_id, dim_name)
    ).fetchone()

    if dim:
        props = json.loads(dim["properties"])
        current = props.get(key)
        if isinstance(current, list):
            current.append(value)
        elif current is None:
            current = [value]
        else:
            current = [current, value]
        props[key] = current
        db.execute(
            "UPDATE dimensions SET properties = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (json.dumps(props, ensure_ascii=False), dim["id"])
        )
    else:
        props = {key: [value]}
        db.execute(
            "INSERT INTO dimensions (entry_id, dim_name, properties) VALUES (?, ?, ?)",
            (entry_id, dim_name, json.dumps(props, ensure_ascii=False))
        )

    db.commit()
    print(f"[novel-cli] 已追加 {name}.{dim_name}.{key}: {value}")
    log_append("素材库", "修改", f"{row['entry_type']}.{name}.{dim_name}", f"追加: {key}")
    db.close()


def entry_set(name, dim_name, key, value):
    db = get_materials_db()
    row = db.execute("SELECT id, entry_type FROM entries WHERE name = ?", (name,)).fetchone()
    if not row:
        print(f"[novel-cli] 未找到条目: {name}")
        db.close()
        sys.exit(1)

    entry_id = row["id"]
    dim = db.execute(
        "SELECT id, properties FROM dimensions WHERE entry_id = ? AND dim_name = ?",
        (entry_id, dim_name)
    ).fetchone()

    if dim:
        props = json.loads(dim["properties"])
        is_new_key = key not in props
        props[key] = value
        db.execute(
            "UPDATE dimensions SET properties = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (json.dumps(props, ensure_ascii=False), dim["id"])
        )
    else:
        is_new_key = True
        db.execute(
            "INSERT INTO dimensions (entry_id, dim_name, properties) VALUES (?, ?, ?)",
            (entry_id, dim_name, json.dumps({key: value}, ensure_ascii=False))
        )

    db.commit()
    action = "新增" if is_new_key else "更新"
    print(f"[novel-cli] {action} {name}.{dim_name}.{key} = {value}")
    log_append("素材库", "修改", f"{row['entry_type']}.{name}.{dim_name}", f"{action}: {key}")
    db.close()


def entry_del(name, dim_name, key=None):
    db = get_materials_db()
    row = db.execute("SELECT id, entry_type FROM entries WHERE name = ?", (name,)).fetchone()
    if not row:
        print(f"[novel-cli] 未找到条目: {name}")
        db.close()
        sys.exit(1)

    if key:
        dim = db.execute(
            "SELECT id, properties FROM dimensions WHERE entry_id = ? AND dim_name = ?",
            (row["id"], dim_name)
        ).fetchone()
        if not dim:
            print(f"[novel-cli] 维度不存在: {name}.{dim_name}")
            db.close()
            sys.exit(1)
        props = json.loads(dim["properties"])
        if key not in props:
            print(f"[novel-cli] 属性不存在: {name}.{dim_name}.{key}")
            db.close()
            sys.exit(1)
        del props[key]
        db.execute(
            "UPDATE dimensions SET properties = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (json.dumps(props, ensure_ascii=False), dim["id"])
        )
        db.commit()
        print(f"[novel-cli] 已删除 {name}.{dim_name}.{key}")
        log_append("素材库", "删除", f"{row['entry_type']}.{name}.{dim_name}", f"删除属性: {key}")
    else:
        db.execute("DELETE FROM dimensions WHERE entry_id = ? AND dim_name = ?", (row["id"], dim_name))
        db.commit()
        print(f"[novel-cli] 已删除维度: {name}.{dim_name}")
        log_append("素材库", "删除", f"{row['entry_type']}.{name}", f"删除维度: {dim_name}")
    db.close()


def entry_list(entry_type=None, dim=None):
    db = get_materials_db()
    query = "SELECT id, entry_type, name, updated_at FROM entries"
    params = []
    if entry_type:
        query += " WHERE entry_type = ?"
        params.append(entry_type)
    query += " ORDER BY entry_type, name"
    rows = db.execute(query, params).fetchall()

    if not rows:
        print("[novel-cli] (暂无条目)")
        db.close()
        return

    for r in rows:
        dims = db.execute(
            "SELECT dim_name FROM dimensions WHERE entry_id = ? ORDER BY dim_name", (r["id"],)
        ).fetchall()
        dim_names = [d["dim_name"] for d in dims]
        if dim and dim not in dim_names:
            continue
        type_cn = ENTRY_TYPES.get(r["entry_type"], r["entry_type"])
        print(f"  [{type_cn}] {r['name']}  — {', '.join(dim_names) if dim_names else '无属性'}")
    db.close()


def entry_search(keyword):
    db = get_materials_db()
    rows = db.execute(
        """SELECT e.id, e.entry_type, e.name, d.dim_name, d.properties
           FROM entries e
           JOIN dimensions d ON d.entry_id = e.id
           WHERE e.name LIKE ? OR d.properties LIKE ?
           ORDER BY e.entry_type, e.name""",
        (f"%{keyword}%", f"%{keyword}%")
    ).fetchall()

    if not rows:
        print(f"[novel-cli] 未找到与 '{keyword}' 相关的条目")
        db.close()
        return

    shown = set()
    for r in rows:
        key = (r["entry_type"], r["name"])
        if key in shown:
            continue
        shown.add(key)
        type_cn = ENTRY_TYPES.get(r["entry_type"], r["entry_type"])
        print(f"  [{type_cn}] {r['name']}")
    db.close()


# ── 剧情操作 ──────────────────────────────────────────────

def plot_create(level, title, parent_id=None, volume_num=None, sort_order=None):
    if level not in ("L1", "L2", "L3", "L4"):
        print(f"[novel-cli] 无效层级: {level}（允许: L1, L2, L3, L4）")
        sys.exit(1)

    db = get_nodes_db()

    # ── 树状门禁：层级链 + 父节点已确认 ──
    level_order = {"L1": 0, "L2": 1, "L3": 2, "L4": 3}
    if level == "L1" and parent_id is not None:
        print("[novel-cli] 创建被拒绝: L1 是唯一根节点，不能指定父节点")
        db.close()
        sys.exit(1)
    if level in ("L2", "L3", "L4") and parent_id is None:
        print(f"[novel-cli] 创建被拒绝: {level} 必须指定 --parent（父节点 id）")
        db.close()
        sys.exit(1)
    if parent_id is not None:
        parent = db.execute("SELECT id, level, status, title, volume_num FROM plot_nodes WHERE id = ?", (parent_id,)).fetchone()
        if not parent:
            print(f"[novel-cli] 父节点不存在: {parent_id}")
            db.close()
            sys.exit(1)
        want_parent_level = {"L2": "L1", "L3": "L2", "L4": "L3"}[level]
        if parent["level"] != want_parent_level:
            print(f"[novel-cli] 创建被拒绝: 父节点 #{parent_id} 是 {parent['level']}，{level} 的父节点必须是 {want_parent_level}")
            db.close()
            sys.exit(1)
        if parent["status"] not in ("confirmed", "published"):
            print(f"[novel-cli] 创建被拒绝: 父节点 #{parent_id} '{parent['title']}' 状态为 {parent['status']}，未确认前不能创建子节点")
            print(f"          请先确认父节点: cli.py plot status {parent_id} confirmed")
            db.close()
            sys.exit(1)
        if parent["volume_num"] is not None and volume_num is None:
            volume_num = parent["volume_num"]

    # L2 必须指定卷序号，L1 不需要；未指定时自动分配（已有卷数+1）
    if level == "L2" and volume_num is None:
        row = db.execute("SELECT COALESCE(MAX(volume_num), 0) + 1 FROM plot_nodes WHERE level = 'L2'").fetchone()
        volume_num = row[0]
        print(f"[novel-cli] L2 卷号自动分配为 {volume_num}（已有卷数+1）")

    # L4 同卷排序唯一校验（L4 自身 sort 卷内唯一，章序依据）
    if level == "L4" and sort_order is not None and volume_num is not None:
        clash = db.execute(
            "SELECT id, title FROM plot_nodes WHERE level = 'L4' AND volume_num = ? AND sort_order = ?",
            (volume_num, sort_order)
        ).fetchone()
        if clash:
            print(f"[novel-cli] 创建被拒绝: 卷{volume_num} 已有排序 {sort_order} 的 L4 节点 #{clash['id']} '{clash['title']}'")
            print(f"          同卷 L4 排序必须唯一（L4 自身 sort 卷内唯一，作为章序依据）")
            db.close()
            sys.exit(1)

    # 构建 file_path：L3/L4 按卷分目录存储（plots/L3/{卷}/{排序}_{标题}.md）
    safe_title = title.replace("/", "-").replace("\\", "-")
    if level in ("L3", "L4"):
        vol = volume_num if volume_num is not None else 0
        name_part = f"{sort_order}_{safe_title}" if sort_order is not None else safe_title
        file_path = f"plots/{level}/{vol}/{name_part}.md"
    else:
        file_path = f"plots/{level}/{safe_title}.md"

    db.execute(
        """INSERT INTO plot_nodes (level, title, parent_id, volume_num, sort_order, file_path, status)
           VALUES (?, ?, ?, ?, ?, ?, 'draft')""",
        (level, title, parent_id, volume_num, sort_order, file_path)
    )
    db.commit()
    node_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # L4 主引用写入 references 表（与 parent_id 冗余；从 L3 反向查所有关联 L4 只查此表）
    if level == "L4" and parent_id is not None:
        db.execute(
            'INSERT INTO "references" (from_id, to_id, kind) VALUES (?, ?, ?)',
            (node_id, parent_id, 'main')
        )
        db.commit()

    # 创建内容文件
    plots_dir = _plots_dir()
    md_file = plots_dir / file_path[len("plots/"):]
    md_file.parent.mkdir(parents=True, exist_ok=True)
    md_file.write_text(f"# {title}\n\n(待创作)\n", encoding="utf-8")
    # 记录初始内容指纹（创建即认可初始内容）
    _refresh_node_hash(db, node_id)
    db.commit()

    print(f"[novel-cli] 已创建 {level} 节点 #{node_id}: {title}")
    if volume_num is not None:
        print(f"            卷序号={volume_num}, 排序={sort_order}")
    print(f"            文件: {file_path}")
    log_append(level, "创建", file_path, f"新建{level}节点: {title}")
    db.close()


def plot_get(node_id):
    db = get_nodes_db()
    row = db.execute("SELECT * FROM plot_nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        print(f"[novel-cli] 节点不存在: {node_id}")
        db.close()
        return

    print(f"{'='*60}")
    print(f"  [{row['level']}] #{row['id']} {row['title']}")
    print(f"  状态: {row['status']}  |  卷序号: {row['volume_num']}  |  排序: {row['sort_order']}")
    if row["affected_reason"]:
        print(f"  ⚠ 受影响: {row['affected_reason']}")
    if row["parent_id"]:
        parent = db.execute("SELECT title FROM plot_nodes WHERE id = ?", (row["parent_id"],)).fetchone()
        print(f"  父节点: #{row['parent_id']} {parent['title'] if parent else '?'}")
    print(f"  文件: {row['file_path']}")
    print(f"  更新: {row['updated_at']}")
    print(f"{'='*60}")

    # 读取内容文件（显示层应用文本替换：{{key}} → display，文件底层保持键；检测内容漂移）
    file_path = _cli_dir() / row["file_path"]
    if file_path.exists():
        if _content_drift(row):
            print(f"  ⚠ 内容漂移: 文件在最近一次认可后被外部修改 — 建议 plot status {node_id} revised 后重新确认")
        content = file_path.read_text(encoding="utf-8-sig")
        print(_apply_translation(content))
    else:
        print("  (内容文件不存在)")
    db.close()


def plot_list(level=None, parent_id=None):
    db = get_nodes_db()
    query = "SELECT id, level, title, volume_num, sort_order, status, affected_reason, file_path, content_hash, content_size FROM plot_nodes WHERE 1=1"
    params = []
    if level:
        query += " AND level = ?"
        params.append(level)
    if parent_id is not None:
        query += " AND parent_id = ?"
        params.append(parent_id)
    query += " ORDER BY volume_num NULLS FIRST, sort_order NULLS FIRST, id"
    rows = db.execute(query, params).fetchall()

    if not rows:
        print("[novel-cli] (暂无剧情节点)")
        db.close()
        return

    for r in rows:
        status_icon = {"draft": "◇", "confirmed": "○", "revised": "◎", "affected": "⚠", "published": "●"}.get(r["status"], "?")
        vol_info = f"V{r['volume_num']}" if r["volume_num"] is not None else ""
        sort_info = f"#{r['sort_order']}" if r["sort_order"] is not None else ""
        reason = f" — {r['affected_reason']}" if r["status"] == "affected" and r["affected_reason"] else ""
        print(f"  {status_icon} [{r['level']}] #{r['id']} {r['title']}  {vol_info} {sort_info}  ({r['status']}){reason}")

    # 内容漂移汇总：已确认/已发布节点文件被外部修改（novel_status 解析时忽略此非节点行）
    drift_count = 0
    for r in rows:
        if r["status"] in ("confirmed", "published") and _content_drift(r):
            drift_count += 1
    if drift_count:
        print(f"⚠ 提示: {drift_count} 个已确认/已发布节点内容与记录不一致（文件可能被外部修改）— 用 plot status <id> revised 标记")
    db.close()


def _unconfirmed_ancestors(db, node_id):
    """返回从根到父的未确认祖先链（按根→近序）；全确认则返回空列表。"""
    bad = []
    cur = db.execute("SELECT parent_id FROM plot_nodes WHERE id = ?", (node_id,)).fetchone()
    while cur and cur["parent_id"] is not None:
        parent = db.execute("SELECT id, title, status, parent_id FROM plot_nodes WHERE id = ?", (cur["parent_id"],)).fetchone()
        if not parent:
            break
        if parent["status"] != "confirmed":
            bad.append(parent)
        cur = parent
    return list(reversed(bad))


def _collect_subtree(db, node_id):
    """返回 node_id 的全部后代 id（不含自身），任意深度。"""
    result = []
    stack = [node_id]
    while stack:
        cur = stack.pop()
        children = db.execute("SELECT id FROM plot_nodes WHERE parent_id = ?", (cur,)).fetchall()
        for c in children:
            result.append(c["id"])
            stack.append(c["id"])
    return result


def _cascade_invalidate(db, node_id):
    """节点被置 revised 后级联：树传播（子树+后续同级）+ 引用网络闭包（references 双向）→ affected。"""
    row = db.execute("SELECT id, title, parent_id, sort_order FROM plot_nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        return
    target_ids = []
    # 1. 树传播：全部子树
    target_ids.extend(_collect_subtree(db, node_id))
    # 2. 树传播：同父后续同级（sort 更大，或相同但 id 更大）及其子树
    if row["parent_id"] is not None:
        peers = db.execute(
            "SELECT id FROM plot_nodes WHERE parent_id = ? AND (sort_order > ? OR (sort_order = ? AND id > ?))",
            (row["parent_id"], row["sort_order"], row["sort_order"], node_id)
        ).fetchall()
        for p in peers:
            target_ids.append(p["id"])
            target_ids.extend(_collect_subtree(db, p["id"]))
    # 3. 引用网络闭包（references 双向 BFS）：
    #    L3 修改 → 引用它的所有 L4；这些 L4 又引用其他 L3 → 再波及那些 L3 的 L4……直到闭包稳定
    visited = set([node_id])
    queue = [node_id]
    while queue:
        cur = queue.pop(0)
        # cur 作为被引用方（L3）：谁引用了我 → from_id
        for r in db.execute('SELECT from_id FROM "references" WHERE to_id = ?', (cur,)).fetchall():
            o = r["from_id"]
            if o not in visited:
                visited.add(o)
                queue.append(o)
                target_ids.append(o)
        # cur 作为引用方（L4）：我引用了谁 → to_id
        for r in db.execute('SELECT to_id FROM "references" WHERE from_id = ?', (cur,)).fetchall():
            o = r["to_id"]
            if o not in visited:
                visited.add(o)
                queue.append(o)
                target_ids.append(o)
    reason = f"因 #{node_id} '{row['title']}' 内容修改，后续剧情衔接需核查"
    seen = set()
    for tid in target_ids:
        if tid in seen:
            continue
        seen.add(tid)
        cur = db.execute("SELECT status FROM plot_nodes WHERE id = ?", (tid,)).fetchone()
        if cur and cur["status"] in ("confirmed", "published"):
            db.execute(
                "UPDATE plot_nodes SET status = 'affected', affected_reason = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (reason, tid)
            )


def plot_status(node_id, status):
    valid = ("draft", "confirmed", "revised")
    if status not in valid:
        print(f"[novel-cli] 无效状态: {status}")
        print(f"          手动允许: draft | confirmed | revised")
        print(f"          affected 由级联自动产生，不可手动设置")
        print(f"          published 通过 publish 命令产生，不可直接设置")
        sys.exit(1)
    db = get_nodes_db()
    row = db.execute("SELECT title, level, status FROM plot_nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        print(f"[novel-cli] 节点不存在: {node_id}")
        db.close()
        sys.exit(1)

    if row["status"] == "confirmed" and status == "draft":
        print(f"[novel-cli] 拒绝: confirmed → draft 不允许；如需修改请先置 revised（会触发级联）")
        db.close()
        sys.exit(1)

    if status == "confirmed":
        bad = _unconfirmed_ancestors(db, node_id)
        if bad:
            chain = " → ".join(f"#{a['id']} {a['title']} ({a['status']})" for a in bad)
            print(f"[novel-cli] 确认被拒绝: 节点 #{node_id} '{row['title']}' 的祖先链存在未确认节点")
            print(f"          未确认祖先: {chain}")
            print(f"          请先确认这些祖先，再重新确认本节点")
            db.close()
            sys.exit(1)

    if status == "revised":
        _cascade_invalidate(db, node_id)

    db.execute(
        "UPDATE plot_nodes SET status = ?, affected_reason = NULL, updated_at = datetime('now','localtime') WHERE id = ?",
        (status, node_id)
    )
    # 状态变更即"认可当前内容"：刷新内容指纹（此后文件被改会检测出漂移）
    _refresh_node_hash(db, node_id)
    db.commit()
    print(f"[novel-cli] 节点 #{node_id} '{row['title']}' 状态 → {status}")
    if status == "revised":
        print(f"[novel-cli] 已触发级联: 沿树与引用网络闭包标记 affected（需核查衔接，见 plot list）")
    log_append(row["level"], "修改", row["title"], f"状态 → {status}")
    db.close()


# ── 发布 ─────────────────────────────────────────────────

def plot_ref_add(l4_id, l3_id):
    """给 L4 添加一个次引用（被引用 L3 必须 confirmed）。"""
    db = get_nodes_db()
    l4 = db.execute("SELECT id, title, level FROM plot_nodes WHERE id = ?", (l4_id,)).fetchone()
    if not l4 or l4["level"] != "L4":
        print(f"[novel-cli] 引用方必须是 L4 节点（当前: #{l4_id}）")
        db.close()
        sys.exit(1)
    l3 = db.execute("SELECT id, title, level, status FROM plot_nodes WHERE id = ?", (l3_id,)).fetchone()
    if not l3 or l3["level"] != "L3":
        print(f"[novel-cli] 被引用方必须是 L3 事件（当前: #{l3_id}）")
        db.close()
        sys.exit(1)
    if l3["status"] not in ("confirmed", "published"):
        print(f"[novel-cli] 引用被拒绝: L3 #{l3_id} '{l3['title']}' 状态为 {l3['status']}，被引用的事件须已确认")
        db.close()
        sys.exit(1)
    existing = db.execute('SELECT kind FROM "references" WHERE from_id = ? AND to_id = ?', (l4_id, l3_id)).fetchone()
    if existing:
        print(f"[novel-cli] L4 #{l4_id} 已引用 L3 #{l3_id}（{existing['kind']}），无需重复添加")
        db.close()
        sys.exit(1)
    db.execute('INSERT INTO "references" (from_id, to_id, kind) VALUES (?, ?, ?)', (l4_id, l3_id, 'secondary'))
    db.commit()
    print(f"[novel-cli] 已添加次引用: L4 #{l4_id} '{l4['title']}' → L3 #{l3_id} '{l3['title']}'")
    db.close()


def plot_ref_list(node_id):
    """列出节点的所有引用关系（作为 L4 的引用 + 作为 L3 被引用）。"""
    db = get_nodes_db()
    row = db.execute("SELECT id, title, level FROM plot_nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        print(f"[novel-cli] 节点不存在: {node_id}")
        db.close()
        sys.exit(1)
    print(f"[{row['level']}] #{row['id']} {row['title']}")
    outs = db.execute('SELECT to_id, kind FROM "references" WHERE from_id = ? ORDER BY kind', (node_id,)).fetchall()
    if outs:
        print("  引用（→）:")
        for r in outs:
            t = db.execute("SELECT title, level FROM plot_nodes WHERE id = ?", (r["to_id"],)).fetchone()
            print(f"    → #{r['to_id']} [{t['level'] if t else '?'}] {t['title'] if t else '?'} ({r['kind']})")
    else:
        print("  引用（→）: 无")
    ins = db.execute('SELECT from_id, kind FROM "references" WHERE to_id = ? ORDER BY kind', (node_id,)).fetchall()
    if ins:
        print("  被引用（←）:")
        for r in ins:
            f = db.execute("SELECT title, level FROM plot_nodes WHERE id = ?", (r["from_id"],)).fetchone()
            print(f"    ← #{r['from_id']} [{f['level'] if f else '?'}] {f['title'] if f else '?'} ({r['kind']})")
    else:
        print("  被引用（←）: 无")
    db.close()


def plot_ref_del(l4_id, l3_id):
    """删除 L4 的次引用（主引用与树结构绑定，不通过此命令删除）。"""
    db = get_nodes_db()
    row = db.execute('SELECT id, kind FROM "references" WHERE from_id = ? AND to_id = ?', (l4_id, l3_id)).fetchone()
    if not row:
        print(f"[novel-cli] 引用不存在: L4 #{l4_id} → L3 #{l3_id}")
        db.close()
        sys.exit(1)
    if row["kind"] == "main":
        print(f"[novel-cli] 主引用不可用 ref del 删除（它与树结构 parent_id 绑定，如需修改请调整父节点）")
        db.close()
        sys.exit(1)
    db.execute('DELETE FROM "references" WHERE id = ?', (row["id"],))
    db.commit()
    print(f"[novel-cli] 已删除次引用: L4 #{l4_id} → L3 #{l3_id}")
    db.close()


# ── 事实账本 ───────────────────────────────────────────────

def fact_add(text, category="completed", source_node_id=None):
    """记录一条"已发生事实"（只记验收/确认后的不可逆事实；source_node_id 溯源"何时成了既成事实"）。"""
    if category not in ("completed", "revealed", "state_changed"):
        print(f"[novel-cli] 无效类别: {category}（允许: completed, revealed, state_changed）")
        sys.exit(1)
    db = get_nodes_db()
    if source_node_id is not None:
        node = db.execute("SELECT id, title, level FROM plot_nodes WHERE id = ?", (source_node_id,)).fetchone()
        if not node:
            print(f"[novel-cli] 来源节点不存在: {source_node_id}")
            db.close()
            sys.exit(1)
    db.execute(
        "INSERT INTO facts (text, category, source_node_id) VALUES (?, ?, ?)",
        (text, category, source_node_id)
    )
    db.commit()
    fact_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    cat_cn = {"completed": "已完成", "revealed": "已揭示", "state_changed": "状态变化"}[category]
    src = f"（来源: #{source_node_id}）" if source_node_id else ""
    print(f"[novel-cli] 已记录事实 #{fact_id} [{cat_cn}]: {text} {src}")
    db.close()


def fact_list(category=None, source_node_id=None):
    """列出事实账本（可过滤类别/来源节点）。写作前查看"已完成事项勿重复写出"。"""
    db = get_nodes_db()
    query = ("SELECT f.id, f.text, f.category, f.source_node_id, f.created_at, "
             "n.title AS source_title, n.level AS source_level "
             "FROM facts f LEFT JOIN plot_nodes n ON n.id = f.source_node_id WHERE 1=1")
    params = []
    if category:
        query += " AND f.category = ?"
        params.append(category)
    if source_node_id is not None:
        query += " AND f.source_node_id = ?"
        params.append(source_node_id)
    query += " ORDER BY f.id"
    rows = db.execute(query, params).fetchall()
    if not rows:
        print("[novel-cli] (事实账本为空 — 节点确认/发布后记一条事实，防止重复写出)")
        db.close()
        return
    print("事实账本（已完成事项 — 写作时勿重复写出）")
    print("=" * 60)
    for r in rows:
        cat_cn = {"completed": "已完成", "revealed": "已揭示", "state_changed": "状态变化"}.get(r["category"], r["category"])
        src = f" ← {r['source_level']} #{r['source_node_id']} {r['source_title']}" if r["source_node_id"] else ""
        print(f"  #{r['id']} [{cat_cn}] {r['text']}{src}")
    db.close()


def translation_add(key, display, note=None):
    """登记一条文本替换：写作时用 {{key}}，发布时替换为 display。"""
    if not key or not display:
        print("[novel-cli] key 与 display 必填")
        sys.exit(1)
    db = get_nodes_db()
    try:
        db.execute("INSERT INTO translation (key, display, note) VALUES (?, ?, ?)", (key, display, note))
        db.commit()
        print("[novel-cli] 已登记替换: {{" + key + "}} → " + display)
    except sqlite3.IntegrityError:
        print(f"[novel-cli] 键已存在: {key}（用 translation set 修改显示名）")
        db.close()
        sys.exit(1)
    db.close()


def translation_set(key, display):
    """改显示名（★ 一键改名核心：此后 publish / translation apply 呈现新名）。"""
    db = get_nodes_db()
    row = db.execute("SELECT id FROM translation WHERE key = ?", (key,)).fetchone()
    if not row:
        print(f"[novel-cli] 键不存在: {key}（先 translation add）")
        db.close()
        sys.exit(1)
    db.execute(
        "UPDATE translation SET display = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (display, row["id"])
    )
    db.commit()
    print("[novel-cli] 已更新: {{" + key + "}} → " + display + "（重跑 translation apply 更新已发布正文）")
    db.close()


def translation_list():
    """列出全部替换映射。"""
    db = get_nodes_db()
    rows = db.execute("SELECT key, display, note FROM translation ORDER BY key").fetchall()
    if not rows:
        print("[novel-cli] (暂无替换映射 — 写作时用 {{键}} 占位符，translation add 登记)")
        db.close()
        return
    print("文本替换映射（写作时用 {{key}}，发布时替换为 display）")
    print("=" * 60)
    for r in rows:
        line = "  {{" + r["key"] + "}} → " + r["display"]
        if r["note"]:
            line += f"  ({r['note']})"
        print(line)
    db.close()


def translation_del(key):
    """删除一条替换映射。"""
    db = get_nodes_db()
    row = db.execute("SELECT id FROM translation WHERE key = ?", (key,)).fetchone()
    if not row:
        print(f"[novel-cli] 键不存在: {key}")
        db.close()
        sys.exit(1)
    db.execute("DELETE FROM translation WHERE id = ?", (row["id"],))
    db.commit()
    print(f"[novel-cli] 已删除替换: {key}")
    db.close()


def _chapter_numbers(db, node_id, volume_num, sort_order):
    """计算卷内章号与全书章号（按 L4 同卷排序；sort 卷内唯一，id 作 tiebreaker）。"""
    so = sort_order or 0
    chap_in_vol = db.execute(
        """SELECT COUNT(*) + 1 FROM plot_nodes
           WHERE level = 'L4' AND volume_num = ?
             AND (sort_order < ? OR (sort_order = ? AND id < ?))""",
        (volume_num, so, so, node_id)
    ).fetchone()[0]
    global_chap = db.execute(
        """SELECT COUNT(*) + 1 FROM plot_nodes
           WHERE level = 'L4'
             AND (volume_num < ?
               OR (volume_num = ? AND (sort_order < ? OR (sort_order = ? AND id < ?))))""",
        (volume_num, volume_num, so, so, node_id)
    ).fetchone()[0]
    return chap_in_vol, global_chap


def translation_apply(node_id=None):
    """重跑已发布 L4 的替换输出（覆盖 contents/ 对应文件，不改节点状态）。

    改名后执行：translation set → translation apply → 全部已发布正文批量更新。
    """
    db = get_nodes_db()
    if node_id is not None:
        rows = db.execute(
            "SELECT id, title, file_path, volume_num, sort_order FROM plot_nodes WHERE id = ? AND level = 'L4' AND status = 'published'",
            (node_id,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, title, file_path, volume_num, sort_order FROM plot_nodes WHERE level = 'L4' AND status = 'published' ORDER BY volume_num, sort_order"
        ).fetchall()
    if not rows:
        print("[novel-cli] 没有已发布的 L4 节点" + (f"（#{node_id} 不存在或未发布）" if node_id is not None else ""))
        db.close()
        return
    count = 0
    for row in rows:
        md_path = _cli_dir() / row["file_path"]
        if not md_path.exists():
            continue
        md_content = md_path.read_text(encoding="utf-8-sig")
        md_content = _strip_l4_meta(md_content)
        md_content = _apply_translation(md_content)
        txt = _md_to_txt(md_content)
        if "{{" in txt:
            print(f"[novel-cli] 提示: 正文含未登记的替换占位符（{{{{...}}}}），可用 translation add 登记")
        chap_in_vol, global_chap = _chapter_numbers(db, row["id"], row["volume_num"], row["sort_order"])
        safe_title = row["title"].replace("/", "-").replace("\\", "-")
        txt_name = f"{global_chap}_{chap_in_vol}_{safe_title}.txt"
        vol_dir = _contents_dir() / str(row["volume_num"])
        vol_dir.mkdir(parents=True, exist_ok=True)
        (vol_dir / txt_name).write_text(txt, encoding="utf-8")
        count += 1
    db.close()
    print(f"[novel-cli] 已重跑 {count} 个已发布 L4 的替换输出")


def context(node_id):
    """组装写作上下文（L4：引用事件+事实账本+风格规则+前后章；L3：被引用情况+事实）。

    context 注入 facts 时记录消费关系（fact_consumption）——改动级联反向定位的依据。
    """
    db = get_nodes_db()
    row = db.execute("SELECT * FROM plot_nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        print(f"[novel-cli] 节点不存在: {node_id}")
        db.close()
        sys.exit(1)

    print(f"# [{row['level']}] #{row['id']} {row['title']} ({row['status']})")
    print(f"  卷{row['volume_num']} · 排序{row['sort_order']} · 文件: {row['file_path']}")
    if _content_drift(row):
        print(f"  ⚠ 内容漂移: 文件在最近一次认可后被外部修改 — 建议 plot status {row['id']} revised 后重新确认")

    # 父链
    chain = []
    cur = row
    while cur["parent_id"]:
        p = db.execute("SELECT id, title, level, status, parent_id FROM plot_nodes WHERE id = ?", (cur["parent_id"],)).fetchone()
        if not p:
            break
        chain.append(f"#{p['id']} [{p['level']}] {p['title']} ({p['status']})")
        cur = p
    if chain:
        print("父链: " + " ← ".join(reversed(chain)))

    # 引用关系（L4 正向 / L3 反向）
    if row["level"] == "L4":
        refs = db.execute('SELECT to_id, kind FROM "references" WHERE from_id = ? ORDER BY kind', (node_id,)).fetchall()
        print("引用事件:")
        for r in refs:
            t = db.execute("SELECT title, status FROM plot_nodes WHERE id = ?", (r["to_id"],)).fetchone()
            print(f"  → #{r['to_id']} {t['title'] if t else '?'} ({r['kind']}, {t['status'] if t else '?'})")
    elif row["level"] == "L3":
        refs = db.execute('SELECT from_id FROM "references" WHERE to_id = ?', (node_id,)).fetchall()
        if refs:
            print("被以下章节引用:")
            for r in refs:
                f = db.execute("SELECT title FROM plot_nodes WHERE id = ?", (r["from_id"],)).fetchone()
                print(f"  ← #{r['from_id']} {f['title'] if f else '?'}")

    # 事实账本（注入 + 记录消费关系）
    facts = db.execute("SELECT id, text, category FROM facts").fetchall()
    if facts:
        print("事实账本（已完成事项 — 勿重复写出）:")
        for f in facts:
            cat = {"completed": "已完成", "revealed": "已揭示", "state_changed": "状态变化"}.get(f["category"], f["category"])
            print(f"  #{f['id']} [{cat}] {f['text']}")
        for f in facts:
            db.execute("INSERT OR IGNORE INTO fact_consumption (fact_id, node_id) VALUES (?, ?)", (f["id"], node_id))
        db.commit()

    # 风格规则（style.db 跨库读取）
    try:
        sdb = _style_get_db()
        rules = sdb.execute("SELECT id, action, content FROM style_rules ORDER BY action, id").fetchall()
        if rules:
            print("风格规则（KEEP 做 / WEAKEN 避）:")
            for r in rules:
                kind = "KEEP" if r["action"] == "keep" else "WEAKEN"
                print(f"  [{kind}] #{r['id']} {r['content']}")
        sdb.close()
    except Exception:
        pass

    # 前后章（L4：前一章衔接 + 下一章前瞻）
    if row["level"] == "L4":
        vol = row["volume_num"]
        so = row["sort_order"] or 0
        prev = db.execute(
            "SELECT id, title, status FROM plot_nodes WHERE level='L4' AND volume_num=? AND (sort_order < ? OR (sort_order = ? AND id < ?)) ORDER BY sort_order DESC, id DESC LIMIT 1",
            (vol, so, so, node_id)
        ).fetchone()
        if prev:
            print(f"前一章（衔接）: #{prev['id']} {prev['title']} ({prev['status']})")
        nxt = db.execute(
            "SELECT id, title, status FROM plot_nodes WHERE level='L4' AND volume_num=? AND (sort_order > ? OR (sort_order = ? AND id > ?)) ORDER BY sort_order, id LIMIT 1",
            (vol, so, so, node_id)
        ).fetchone()
        if nxt:
            print(f"下一章（前瞻）: #{nxt['id']} {nxt['title']} ({nxt['status']})")
    db.close()

def _apply_translation(text):
    """把文本中的 {{key}} 占位符替换为 translation 表的 display（通用文本替换）。

    无映射的 {{xxx}} 原样保留（publish 会提示）。
    """
    db = get_nodes_db()
    rows = db.execute("SELECT key, display FROM translation").fetchall()
    db.close()
    for r in rows:
        text = text.replace("{{" + r["key"] + "}}", r["display"])
    return text


def _strip_l4_meta(md_text):
    """剥离 L4 md 中的写作元数据区块（旧模板遗留：上下文声明/主事件/引用事件）。

    引用关系等元数据已入数据库（references 表），由 context 命令组装；正文文件应只含正文。
    此函数作发布兜底：跳过元数据标题行及其内容（`- @` 列表行、`[xxx]` 占位行），
    遇到普通内容行即视为正文开始并保留，防止误删正文。
    """
    meta_sections = ("上下文声明", "主事件", "引用事件")
    lines = md_text.split("\n")
    out = []
    skip = False
    for line in lines:
        if line.startswith("## "):
            section = line[3:].strip()
            skip = section in meta_sections
            if skip:
                continue
        if skip:
            stripped = line.strip()
            # 元数据区内的空行 / `- @...` 列表 / `[占位]` 行 → 跳过
            if stripped == "" or stripped.startswith("- ") or (stripped.startswith("[") and stripped.endswith("]")):
                continue
            # 普通内容行 → 正文开始，结束剥离
            skip = False
        out.append(line)
    return "\n".join(out)


def _md_to_txt(md_text):
    """将 Markdown 转为纯文本。"""
    text = md_text.lstrip("\ufeff")
    # 去掉标题标记 #
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 去掉粗体/斜体标记
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # 去掉行内代码
    text = re.sub(r'`(.+?)`', r'\1', text)
    # 去掉删除线
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # 去掉水平线
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # 去掉链接 [text](url)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    # 压缩连续空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip() + "\n"


def publish(node_id):
    db = get_nodes_db()
    row = db.execute("SELECT * FROM plot_nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        print(f"[novel-cli] 节点不存在: {node_id}")
        db.close()
        sys.exit(1)
    if row["level"] != "L4":
        print(f"[novel-cli] 只能发布 L4 节点（当前: {row['level']}）")
        db.close()
        sys.exit(1)
    if row["status"] == "published":
        print(f"[novel-cli] 节点 #{node_id} 已发布")
        db.close()
        sys.exit(1)
    if row["status"] != "confirmed":
        print(f"[novel-cli] 发布被拒绝: 节点 #{node_id} '{row['title']}' 状态为 {row['status']}，只有 confirmed 才能发布")
        if row["status"] in ("revised", "affected"):
            print(f"          内容或前置需重新确认: 核查后 cli.py plot status {node_id} confirmed")
        else:
            print(f"          请先确认: cli.py plot status {node_id} confirmed")
        db.close()
        sys.exit(1)
    # 内容漂移门禁：confirmed 后文件被外部修改 → 拒绝发布（杜绝绕过确认直接发布）
    if _content_drift(row):
        print(f"[novel-cli] 发布被拒绝: 文件在确认后被外部修改（内容与确认时不一致）")
        print(f"          请先 plot status {node_id} revised 标记修改，重新确认后再发布")
        db.close()
        sys.exit(1)

    # 读取 md 文件
    md_path = _cli_dir() / row["file_path"]
    if not md_path.exists():
        print(f"[novel-cli] 内容文件不存在: {md_path}")
        print("[novel-cli] 请先在 plots/L4/ 下完成正文创作")
        db.close()
        sys.exit(1)

    volume_num = row["volume_num"]
    title = row["title"]

    # 计算章号（卷内/全书，按 L4 同卷排序）
    chap_in_vol, global_chap = _chapter_numbers(db, node_id, volume_num, row["sort_order"])

    # 转换内容（剥离元数据 → 应用文本替换 {{key}} → 转纯文本；保证发布正文干净且呈现最终名）
    md_content = md_path.read_text(encoding="utf-8-sig")
    md_content = _strip_l4_meta(md_content)
    md_content = _apply_translation(md_content)
    txt_content = _md_to_txt(md_content)
    if "{{" in txt_content:
        print(f"[novel-cli] 提示: 正文含未登记的替换占位符（{{{{...}}}}），可用 translation add 登记")

    # 写入 contents/
    contents_dir = _contents_dir()
    vol_dir = contents_dir / str(volume_num)
    vol_dir.mkdir(parents=True, exist_ok=True)

    safe_title = title.replace("/", "-").replace("\\", "-")
    # 命名规则: {全书章号}_{卷内章号}_{标题}.txt（如 98_5_xxx.txt）
    txt_name = f"{global_chap}_{chap_in_vol}_{safe_title}.txt"
    txt_path = vol_dir / txt_name
    txt_path.write_text(txt_content, encoding="utf-8")

    # 更新状态
    db.execute(
        "UPDATE plot_nodes SET status = 'published', updated_at = datetime('now','localtime') WHERE id = ?",
        (node_id,)
    )
    db.commit()

    rel_path = f"contents/{volume_num}/{txt_name}"
    print(f"[novel-cli] 已发布: {rel_path}")
    print(f"            卷{volume_num} · 第{chap_in_vol}章 · 全书第{global_chap}章")
    log_append("L4", "发布", rel_path, f"发布正文: {title}")
    db.close()


# ── 初始化 ────────────────────────────────────────────────

def cmd_init(project_path="."):
    root = Path(project_path).resolve()
    cli_dir = root / "novel-cli"

    # 创建目录骨架
    for sub in [
        "entries/characters", "entries/locations", "entries/items", "entries/concepts",
        "plots/L1", "plots/L2", "plots/L3", "plots/L4",
        "log"
    ]:
        (cli_dir / sub).mkdir(parents=True, exist_ok=True)

    (root / "contents").mkdir(parents=True, exist_ok=True)

    # 初始化数据库（拆分：素材库 materials.db + 节点库 nodes.db）
    md = sqlite3.connect(str(cli_dir / "materials.db"))
    init_materials_db(md)
    md.close()
    nd = sqlite3.connect(str(cli_dir / "nodes.db"))
    init_nodes_db(nd)
    nd.close()

    # 初始化日志
    log_dir = cli_dir / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    latest = log_dir / "latest.log"
    if not latest.exists():
        # 新建 latest 时写一次日期头（轮转依据，不用文件修改时间）
        latest.write_text(f"[{_today_str()}]\n", encoding="utf-8")

    # 如果当前目录不是项目目录，复制 cli.py
    current_cli = Path(__file__).resolve()
    target_cli = cli_dir / "cli.py"
    if current_cli != target_cli:
        import shutil
        shutil.copy2(str(current_cli), str(target_cli))

    # 初始化 style.db
    style_path = cli_dir / "style.db"
    root_style = root / "style.db"
    if not style_path.exists():
        if root_style.exists():
            import shutil
            shutil.copy2(str(root_style), str(style_path))
            print(f"           风格库: 已从 ../style.db 拷贝到 novel-cli/style.db")
        else:
            db = sqlite3.connect(str(style_path))
            _style_init_tables(db)
            db.close()
            print(f"           风格库: 已创建空的 novel-cli/style.db")
    else:
        print(f"           风格库: novel-cli/style.db 已存在，跳过")

    print(f"[novel-cli] 项目已初始化: {root}")
    print(f"           数据目录: {cli_dir}")
    print(f"           正文目录: {root / 'contents'}")


# ── 类型映射 ──────────────────────────────────────────────

TYPE_TO_DIR = {"character": "characters", "location": "locations", "item": "items", "concept": "concepts"}


# ── 校验 ──────────────────────────────────────────────────

def cmd_validate():
    mdb = get_materials_db()
    ndb = get_nodes_db()
    issues = []

    # 检查空条目类型（素材库）
    for etype, cn in ENTRY_TYPES.items():
        count = mdb.execute("SELECT COUNT(*) FROM entries WHERE entry_type = ?", (etype,)).fetchone()[0]
        if count == 0:
            issues.append(f"⚠ 空表: {cn}表（无任何条目）")

    # 检查缺少常用维度的角色（素材库）
    common_dims = ["身份", "外貌", "性格"]
    chars = mdb.execute("SELECT id, name FROM entries WHERE entry_type = 'character'").fetchall()
    for c in chars:
        dims = [r["dim_name"] for r in mdb.execute(
            "SELECT dim_name FROM dimensions WHERE entry_id = ?", (c["id"],)
        ).fetchall()]
        missing = [d for d in common_dims if d not in dims]
        if missing:
            issues.append(f"⚠ 角色 '{c['name']}' 缺少维度: {', '.join(missing)}")

    # 检查孤儿剧情节点（节点库）
    orphans = ndb.execute(
        """SELECT id, title, level FROM plot_nodes
           WHERE parent_id IS NOT NULL
             AND parent_id NOT IN (SELECT id FROM plot_nodes)"""
    ).fetchall()
    for o in orphans:
        issues.append(f"⚠ 孤儿节点: [{o['level']}] #{o['id']} '{o['title']}' 的父节点不存在")

    # 检查未发布的 L4 节点（节点库）
    unpublished = ndb.execute(
        "SELECT COUNT(*) FROM plot_nodes WHERE level = 'L4' AND status != 'published'"
    ).fetchone()[0]
    if unpublished > 0:
        issues.append(f"ℹ 有 {unpublished} 个 L4 节点未发布")

    # 检查受影响（affected）节点（节点库）
    affected = ndb.execute("SELECT COUNT(*) FROM plot_nodes WHERE status = 'affected'").fetchone()[0]
    if affected > 0:
        issues.append(f"⚠ 有 {affected} 个节点处于 affected（前置内容已修改，需核查衔接）— 用 plot list 查看")

    # 检查重复条目名（素材库）
    dups = mdb.execute(
        "SELECT name, entry_type, COUNT(*) as cnt FROM entries GROUP BY name, entry_type HAVING cnt > 1"
    ).fetchall()
    for d in dups:
        issues.append(f"⚠ 重复条目: {ENTRY_TYPES.get(d['entry_type'], d['entry_type'])} '{d['name']}'")

    if issues:
        print("完整性检查发现问题:\n")
        for i in issues:
            print(f"  {i}")
    else:
        print("[novel-cli] 未发现问题。")

    mdb.close()
    ndb.close()


# ── 导出 ──────────────────────────────────────────────────

def cmd_export(what="all"):
    mdb = get_materials_db()
    ndb = get_nodes_db()

    if what in ("entries", "all"):
        entries_dir = _entries_dir()
        for row in mdb.execute("SELECT * FROM entries ORDER BY entry_type, name").fetchall():
            type_dir_name = TYPE_TO_DIR.get(row["entry_type"], f"{row['entry_type']}s")
            type_dir = entries_dir / type_dir_name
            type_dir.mkdir(parents=True, exist_ok=True)
            safe_name = row["name"].replace("/", "-").replace("\\", "-")
            md = type_dir / f"{safe_name}.md"

            lines = [f"# {ENTRY_TYPES.get(row['entry_type'], row['entry_type'])}: {row['name']}", ""]
            dims = mdb.execute(
                "SELECT dim_name, properties FROM dimensions WHERE entry_id = ? ORDER BY dim_name",
                (row["id"],)
            ).fetchall()
            for d in dims:
                lines.append(f"## {d['dim_name']}")
                props = json.loads(d["properties"])
                for k, v in props.items():
                    if isinstance(v, list):
                        for i, item in enumerate(v, 1):
                            lines.append(f"- **{k}** [{i}]: {item}")
                    else:
                        lines.append(f"- **{k}**: {v}")
                lines.append("")
            md.write_text("\n".join(lines), encoding="utf-8")
        print("[novel-cli] 条目已导出到 entries/")

    if what in ("plots", "all"):
        # 确保 plot 内容文件存在（节点库）
        for row in ndb.execute("SELECT * FROM plot_nodes ORDER BY level, volume_num, sort_order").fetchall():
            md_path = _cli_dir() / row["file_path"]
            if not md_path.exists():
                md_path.parent.mkdir(parents=True, exist_ok=True)
                md_path.write_text(f"# {row['title']}\n\n(待创作)\n", encoding="utf-8")

        # 导出剧情元数据 manifest（保证 import 能重建完整的 plot_nodes 表）
        export_dir = _cli_dir() / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        nodes = []
        for row in ndb.execute("SELECT * FROM plot_nodes ORDER BY id").fetchall():
            nodes.append({
                "id": row["id"],
                "level": row["level"],
                "title": row["title"],
                "parent_id": row["parent_id"],
                "volume_num": row["volume_num"],
                "sort_order": row["sort_order"],
                "file_path": row["file_path"],
                "status": row["status"],
            })
        manifest = export_dir / "plot_manifest.json"
        manifest.write_text(json.dumps({"plot_nodes": nodes}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[novel-cli] 剧情节点文件已就绪，元数据已导出到 export/plot_manifest.json")

    mdb.close()
    ndb.close()


# ── style.db 操作 ─────────────────────────────────────────

def _style_db():
    """返回工作区 style.db 路径：novel-cli/style.db"""
    return _cli_dir() / "style.db"


def _root_style_db():
    """返回根目录 style.db 路径（参考数据）"""
    return _project_root() / "style.db"


def _style_get_db():
    db = sqlite3.connect(str(_style_db()))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _style_init_tables(db):
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

        -- 风格特征规则层：引例 → keep/weaken 可执行规则（写作时注入，比仅列引例更直接）
        CREATE TABLE IF NOT EXISTS style_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            action      TEXT NOT NULL CHECK(action IN ('keep','weaken')),
            content     TEXT NOT NULL,
            fragment_ref INTEGER REFERENCES style_fragments(id) ON DELETE SET NULL,
            note        TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_rules_action ON style_rules(action);
    """)
    db.commit()


def style_init():
    """创建空的 style.db（含表结构）。"""
    style_path = _style_db()
    if style_path.exists():
        print(f"[novel-cli] style.db 已存在: {style_path}")
        return
    db = _style_get_db()
    _style_init_tables(db)
    db.close()
    print(f"[novel-cli] 已创建 style.db: {style_path}")


def style_rule_add(action, content, fragment_ref=None, note=None):
    """添加一条风格特征规则：keep=该做什么 / weaken=该削弱什么。fragment_ref 关联证据引例。"""
    if action not in ("keep", "weaken"):
        print(f"[novel-cli] 无效 action: {action}（允许: keep, weaken）")
        sys.exit(1)
    db = _style_get_db()
    _style_init_tables(db)
    db.execute(
        "INSERT INTO style_rules (action, content, fragment_ref, note) VALUES (?, ?, ?, ?)",
        (action, content, fragment_ref, note)
    )
    db.commit()
    rule_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    kind = "保持" if action == "keep" else "削弱"
    print(f"[novel-cli] 已添加 {kind}规则 #{rule_id}: {content}")
    if fragment_ref:
        print(f"            证据引例: #{fragment_ref}")
    db.close()


def style_rule_list(action=None):
    """列出全部风格特征规则（可只按 keep/weaken 过滤）。写作前注入。"""
    db = _style_get_db()
    _style_init_tables(db)
    query = "SELECT r.id, r.action, r.content, r.note, r.fragment_ref, f.fragment AS evidence FROM style_rules r LEFT JOIN style_fragments f ON f.id = r.fragment_ref"
    params = []
    if action:
        query += " WHERE r.action = ?"
        params.append(action)
    query += " ORDER BY r.action, r.id"
    rows = db.execute(query, params).fetchall()
    if not rows:
        print("[novel-cli] (暂无风格规则 — 用 style rule add 添加，或由 extract-writing-style 产出)")
        db.close()
        return
    print("风格特征规则")
    print("=" * 60)
    for r in rows:
        kind = "KEEP" if r["action"] == "keep" else "WEAKEN"
        line = f"  [{kind}] #{r['id']} {r['content']}"
        if r["fragment_ref"]:
            line += f"（证据: #{r['fragment_ref']}）"
        print(line)
        if r["note"]:
            print(f"          注: {r['note']}")
    print("=" * 60)
    print("用法: 写 L4 前按 KEEP 规则做、按 WEAKEN 规则避")
    db.close()


def style_rule_del(rule_id):
    """删除一条风格规则。"""
    db = _style_get_db()
    _style_init_tables(db)
    row = db.execute("SELECT id, action, content FROM style_rules WHERE id = ?", (rule_id,)).fetchone()
    if not row:
        print(f"[novel-cli] 规则不存在: {rule_id}")
        db.close()
        sys.exit(1)
    db.execute("DELETE FROM style_rules WHERE id = ?", (rule_id,))
    db.commit()
    print(f"[novel-cli] 已删除规则 #{rule_id} [{row['action']}] {row['content']}")
    db.close()


def style_add(source_file, dimension, tags_str, fragment, position=None, note=None):
    """添加单条引例（agent 创作时使用）。source_file 和 position 必填。"""
    if not source_file:
        print("[novel-cli] 错误: --source 必填")
        sys.exit(1)
    db = _style_get_db()
    _style_init_tables(db)

    db.execute(
        "INSERT INTO style_fragments (source_file, fragment, dimension, position, note) VALUES (?, ?, ?, ?, ?)",
        (source_file, fragment, dimension, position, note)
    )
    fid = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
    for tag_name in tags:
        row = db.execute("SELECT id FROM style_tags WHERE name = ?", (tag_name,)).fetchone()
        if row:
            tid = row["id"]
        else:
            db.execute("INSERT INTO style_tags (name) VALUES (?)", (tag_name,))
            tid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT OR IGNORE INTO fragment_tags (fragment_id, tag_id) VALUES (?, ?)", (fid, tid))

    db.commit()
    db.close()
    print(f"[novel-cli] 已添加引例 #{fid}，标签: {', '.join(tags)}")
    log_append("风格库", "新增", f"style.db:#{fid}", f"来源: {source_file}, 维度: {dimension}")


def style_import(json_file, db_path=None):
    """批量导入 JSON 引例。JSON 中若无 source_file/position 则入库为 NULL。

    db_path: 默认工作区 novel-cli/style.db；传 'root' 导入根目录 style.db（参考库）。
    """
    path = Path(json_file)
    if not path.exists():
        print(f"[novel-cli] JSON 文件不存在: {json_file}")
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8-sig"))
    fragments = data.get("fragments", [])
    if not fragments:
        print("[novel-cli] JSON 中无引例数据")
        return

    if db_path == "root":
        target = _root_style_db()
    elif db_path is not None:
        target = Path(db_path)
    else:
        target = _style_db()
    db = sqlite3.connect(str(target))
    db.row_factory = sqlite3.Row
    _style_init_tables(db)
    count = 0

    for item in fragments:
        source_file = item.get("source_file") or None
        fragment = item.get("fragment", "")
        dimension = item.get("dimension") or None
        position = item.get("position") or None
        note = item.get("note") or None
        tags = item.get("tags", [])

        if not fragment:
            continue

        db.execute(
            "INSERT INTO style_fragments (source_file, fragment, dimension, position, note) VALUES (?, ?, ?, ?, ?)",
            (source_file, fragment, dimension, position, note)
        )
        fid = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        for tag_name in tags:
            tag_name = tag_name.strip()
            if not tag_name:
                continue
            row = db.execute("SELECT id FROM style_tags WHERE name = ?", (tag_name,)).fetchone()
            if row:
                tid = row["id"]
            else:
                db.execute("INSERT INTO style_tags (name) VALUES (?)", (tag_name,))
                tid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT OR IGNORE INTO fragment_tags (fragment_id, tag_id) VALUES (?, ?)", (fid, tid))

        count += 1

    db.commit()
    db.close()
    print(f"[novel-cli] 已导入 {count} 条引例")
    log_append("风格库", "导入", f"style.db", f"批量导入 {count} 条引例")


def style_search(tags_str, match_any=False, limit=0, dimension=None):
    """按标签搜索引例。"""
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
    if not tags:
        print("[novel-cli] 请指定 --tags")
        return

    db = _style_get_db()
    _style_init_tables(db)

    if match_any:
        placeholders = ",".join("?" * len(tags))
        query = f"""
            SELECT DISTINCT f.id, f.fragment, f.dimension, f.note, f.source_file, f.created_at
            FROM style_fragments f
            JOIN fragment_tags ft ON ft.fragment_id = f.id
            JOIN style_tags t ON t.id = ft.tag_id
            WHERE t.name IN ({placeholders})
        """
        params = tags
    else:
        query = """
            SELECT f.id, f.fragment, f.dimension, f.note, f.source_file, f.created_at
            FROM style_fragments f
            WHERE f.id IN (
                SELECT ft.fragment_id FROM fragment_tags ft
                JOIN style_tags t ON t.id = ft.tag_id
                WHERE t.name IN ({})
                GROUP BY ft.fragment_id
                HAVING COUNT(DISTINCT t.name) = ?
            )
        """.format(",".join("?" * len(tags)))
        params = tags + [len(tags)]

    if dimension:
        query += " AND f.dimension = ?"
        params.append(dimension)

    query += " ORDER BY f.id"
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = db.execute(query, params).fetchall()

    if not rows:
        mode = "OR" if match_any else "AND"
        print(f"[novel-cli] 未找到匹配标签（{mode}）: {tags_str}")
        if dimension:
            print(f"            维度筛选: {dimension}")
        db.close()
        return

    for r in rows:
        frag_tags = db.execute(
            """SELECT t.name FROM style_tags t
               JOIN fragment_tags ft ON ft.tag_id = t.id
               WHERE ft.fragment_id = ?
               ORDER BY t.name""",
            (r["id"],)
        ).fetchall()
        tag_names = [t["name"] for t in frag_tags]
        source = f" [{r['source_file']}]" if r["source_file"] else ""
        print(f"#{r['id']} [{r['dimension'] or '未分类'}]{source}")
        print(f"  标签: {', '.join(tag_names)}")
        preview = r["fragment"][:120].replace("\n", " ")
        print(f"  引文: {preview}{'...' if len(r['fragment']) > 120 else ''}")
        print()
    db.close()


def style_expand(fragment_id, limit=0):
    """从一条引例的标签出发，找共享标签的其他引例，按共享数排序。"""
    db = _style_get_db()
    _style_init_tables(db)

    # 获取该引例的标签
    tags = db.execute(
        "SELECT tag_id FROM fragment_tags WHERE fragment_id = ?", (fragment_id,)
    ).fetchall()
    if not tags:
        print(f"[novel-cli] 引例 #{fragment_id} 无标签或不存在")
        db.close()
        return

    tag_ids = [t["tag_id"] for t in tags]
    placeholders = ",".join("?" * len(tag_ids))

    query = f"""
        SELECT f.id, f.fragment, f.dimension, f.note, f.source_file,
               COUNT(ft2.tag_id) AS shared_tags
        FROM style_fragments f
        JOIN fragment_tags ft2 ON ft2.fragment_id = f.id
        WHERE ft2.tag_id IN ({placeholders})
          AND f.id != ?
        GROUP BY f.id
        ORDER BY shared_tags DESC, f.id
    """
    params = tag_ids + [fragment_id]
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = db.execute(query, params).fetchall()

    if not rows:
        print(f"[novel-cli] 未找到与 #{fragment_id} 共享标签的引例")
        db.close()
        return

    # 显示原始引例
    orig = db.execute("SELECT fragment, dimension FROM style_fragments WHERE id = ?", (fragment_id,)).fetchone()
    orig_tags = [t["name"] for t in db.execute(
        "SELECT t.name FROM style_tags t JOIN fragment_tags ft ON ft.tag_id = t.id WHERE ft.fragment_id = ?",
        (fragment_id,)
    ).fetchall()]
    print(f"▸ #{fragment_id} [{orig['dimension'] or '未分类'}] 标签: {', '.join(orig_tags)}")
    preview = orig["fragment"][:100].replace("\n", " ")
    print(f"  {preview}{'...' if len(orig['fragment']) > 100 else ''}")
    print(f"\n共享标签的引例 ({len(rows)} 条):\n")

    for r in rows:
        frag_tags = db.execute(
            "SELECT t.name FROM style_tags t JOIN fragment_tags ft ON ft.tag_id = t.id WHERE ft.fragment_id = ?",
            (r["id"],)
        ).fetchall()
        tag_names = [t["name"] for t in frag_tags]
        source = f" [{r['source_file']}]" if r["source_file"] else ""
        print(f"  #{r['id']} [共享 {r['shared_tags']} 标签] [{r['dimension'] or '未分类'}]{source}")
        print(f"  标签: {', '.join(tag_names)}")
        preview = r["fragment"][:100].replace("\n", " ")
        print(f"  引文: {preview}{'...' if len(r['fragment']) > 100 else ''}")
        print()
    db.close()


def style_tags(dimension=None):
    """列出所有标签及使用频次。"""
    db = _style_get_db()
    _style_init_tables(db)

    query = """
        SELECT t.name, COUNT(ft.fragment_id) AS cnt
        FROM style_tags t
        JOIN fragment_tags ft ON ft.tag_id = t.id
    """
    params = []
    if dimension:
        query += """ JOIN style_fragments f ON f.id = ft.fragment_id
                      WHERE f.dimension = ?"""
        params.append(dimension)
    query += " GROUP BY t.id ORDER BY cnt DESC, t.name"

    rows = db.execute(query, params).fetchall()
    if not rows:
        print("[novel-cli] (暂无标签)")
    else:
        for r in rows:
            print(f"  {r['name']}  ({r['cnt']})")
    db.close()


def style_get(fragment_id):
    """查看单条引例详情+全部标签。"""
    db = _style_get_db()
    _style_init_tables(db)

    row = db.execute("SELECT * FROM style_fragments WHERE id = ?", (fragment_id,)).fetchone()
    if not row:
        print(f"[novel-cli] 引例不存在: {fragment_id}")
        db.close()
        return

    tags = db.execute(
        "SELECT t.name FROM style_tags t JOIN fragment_tags ft ON ft.tag_id = t.id WHERE ft.fragment_id = ?",
        (fragment_id,)
    ).fetchall()
    tag_names = [t["name"] for t in tags]

    print(f"{'='*60}")
    print(f"  引例 #{row['id']}")
    print(f"  维度: {row['dimension'] or '未分类'}")
    print(f"  来源: {row['source_file'] or '(参考数据，无来源)'}")
    if row["position"]:
        print(f"  位置: {row['position']}")
    print(f"  标签: {', '.join(tag_names) if tag_names else '(无标签)'}")
    if row["note"]:
        print(f"  分析: {row['note']}")
    print(f"  创建: {row['created_at']}")
    print(f"{'='*60}")
    print()
    print(row["fragment"])
    db.close()


def style_overview():
    """各维度统计 + Top 标签。"""
    db = _style_get_db()
    _style_init_tables(db)

    dims = db.execute(
        """SELECT dimension, COUNT(*) as cnt FROM style_fragments
           GROUP BY dimension ORDER BY dimension"""
    ).fetchall()

    total = db.execute("SELECT COUNT(*) FROM style_fragments").fetchone()[0]
    ref_count = db.execute("SELECT COUNT(*) FROM style_fragments WHERE source_file IS NULL").fetchone()[0]
    custom_count = db.execute("SELECT COUNT(*) FROM style_fragments WHERE source_file IS NOT NULL").fetchone()[0]

    print(f"风格库概览")
    print(f"{'='*40}")
    print(f"  总引例: {total}  (参考: {ref_count}, 自定义: {custom_count})")
    print()
    if dims:
        for d in dims:
            dim_name = d["dimension"] or "未分类"
            print(f"  {dim_name}: {d['cnt']} 条")
    else:
        print("  (暂无引例)")

    # Top 20 标签
    top_tags = db.execute(
        """SELECT t.name, COUNT(ft.fragment_id) AS cnt
           FROM style_tags t
           JOIN fragment_tags ft ON ft.tag_id = t.id
           GROUP BY t.id ORDER BY cnt DESC LIMIT 20"""
    ).fetchall()
    if top_tags:
        print(f"\n  Top 标签:")
        for t in top_tags:
            print(f"    {t['name']}  ({t['cnt']})")
    db.close()


def style_list(dimension=None, tag=None, source_only=False):
    """列出引例摘要。"""
    db = _style_get_db()
    _style_init_tables(db)

    query = "SELECT id, fragment, dimension, source_file, created_at FROM style_fragments WHERE 1=1"
    params = []

    if dimension:
        query += " AND dimension = ?"
        params.append(dimension)
    if tag:
        query += " AND id IN (SELECT ft.fragment_id FROM fragment_tags ft JOIN style_tags t ON t.id = ft.tag_id WHERE t.name = ?)"
        params.append(tag)
    if source_only:
        query += " AND source_file IS NOT NULL"

    query += " ORDER BY id"
    rows = db.execute(query, params).fetchall()

    if not rows:
        print("[novel-cli] (无匹配引例)")
    else:
        for r in rows:
            frag_tags = db.execute(
                "SELECT t.name FROM style_tags t JOIN fragment_tags ft ON ft.tag_id = t.id WHERE ft.fragment_id = ?",
                (r["id"],)
            ).fetchall()
            tag_names = [t["name"] for t in frag_tags]
            source = f" [{r['source_file']}]" if r['source_file'] else ""
            preview = r["fragment"][:80].replace("\n", " ")
            print(f"  #{r['id']} [{r['dimension'] or '未分类'}]{source} | {', '.join(tag_names[:5])}")
            print(f"    {preview}...")
    db.close()


# ── 主入口 ────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    try:
        if cmd == "init":
            path = args[0] if args else "."
            cmd_init(path)

        elif cmd == "type":
            if not args:
                print("用法: cli.py type <add|list> [...]")
                sys.exit(1)
            sub = args[0]
            rest = args[1:]
            if sub == "add":
                if len(rest) < 3:
                    print("用法: cli.py type add <entry|dimension> <类型名> <描述>")
                    sys.exit(1)
                type_add(rest[0], rest[1], rest[2])
            elif sub == "list":
                type_list()
            else:
                print(f"[novel-cli] 未知 type 子命令: {sub}")
                sys.exit(1)

        elif cmd == "entry":
            if not args:
                print("用法: cli.py entry <add|get|set|append|del|list|search> [...]")
                sys.exit(1)
            sub = args[0]
            rest = args[1:]

            if sub == "add":
                if len(rest) < 2:
                    print("用法: cli.py entry add <类型> <名称>（类型自由，新类型先 type add 登记）")
                    sys.exit(1)
                entry_add(rest[0], rest[1])

            elif sub == "get":
                if not rest:
                    print("用法: cli.py entry get <名称> [--type TYPE]")
                    sys.exit(1)
                etype = None
                if "--type" in rest:
                    idx = rest.index("--type")
                    etype = rest[idx + 1]
                    rest.pop(idx)
                    rest.pop(idx)
                entry_get(rest[0], etype)

            elif sub == "set":
                if len(rest) < 4:
                    print("用法: cli.py entry set <名称> <维度> <键> <值>")
                    sys.exit(1)
                entry_set(rest[0], rest[1], rest[2], rest[3])

            elif sub == "append":
                if len(rest) < 4:
                    print("用法: cli.py entry append <名称> <维度> <键> <值>（同一键追加一条描述）")
                    sys.exit(1)
                entry_append(rest[0], rest[1], rest[2], rest[3])

            elif sub == "del":
                if len(rest) < 2:
                    print("用法: cli.py entry del <名称> <维度> [--key KEY]")
                    sys.exit(1)
                key = None
                if "--key" in rest:
                    idx = rest.index("--key")
                    key = rest[idx + 1]
                entry_del(rest[0], rest[1], key)

            elif sub == "list":
                etype = None
                dim_filter = None
                it = iter(rest)
                for a in it:
                    if a == "--type":
                        etype = next(it)
                    elif a == "--dim":
                        dim_filter = next(it)
                entry_list(etype, dim_filter)

            elif sub == "search":
                if not rest:
                    print("用法: cli.py entry search <关键词>")
                    sys.exit(1)
                entry_search(rest[0])
            else:
                print(f"[novel-cli] 未知 entry 子命令: {sub}")
                sys.exit(1)

        elif cmd == "plot":
            if not args:
                print("用法: cli.py plot <create|get|list|status> [...]")
                sys.exit(1)
            sub = args[0]
            rest = args[1:]

            if sub == "create":
                if len(rest) < 2:
                    print("用法: cli.py plot create <L1-L4> <标题> [--parent ID] [--volume N] [--sort N]")
                    sys.exit(1)
                level, title = rest[0], rest[1]
                parent = None
                volume = None
                sort = None
                it = iter(rest[2:])
                for a in it:
                    if a == "--parent":
                        parent = int(next(it))
                    elif a == "--volume":
                        volume = int(next(it))
                    elif a == "--sort":
                        sort = int(next(it))
                plot_create(level, title, parent, volume, sort)

            elif sub == "get":
                if not rest:
                    print("用法: cli.py plot get <id>")
                    sys.exit(1)
                plot_get(int(rest[0]))

            elif sub == "list":
                level = None
                parent = None
                it = iter(rest)
                for a in it:
                    if a == "--level":
                        level = next(it)
                    elif a == "--parent":
                        parent = int(next(it))
                plot_list(level, parent)

            elif sub == "status":
                if len(rest) < 2:
                    print("用法: cli.py plot status <id> <draft|confirmed|revised>（affected/published 不可手动设置）")
                    sys.exit(1)
                plot_status(int(rest[0]), rest[1])

            elif sub == "ref":
                if not rest:
                    print("用法: cli.py plot ref <add|list|del> [...]")
                    sys.exit(1)
                rsub = rest[0]
                rrest = rest[1:]
                if rsub == "add":
                    if len(rrest) < 2:
                        print("用法: cli.py plot ref add <L4_id> <L3_id>（添加次引用，被引用事件须已确认）")
                        sys.exit(1)
                    plot_ref_add(int(rrest[0]), int(rrest[1]))
                elif rsub == "list":
                    if not rrest:
                        print("用法: cli.py plot ref list <node_id>")
                        sys.exit(1)
                    plot_ref_list(int(rrest[0]))
                elif rsub == "del":
                    if len(rrest) < 2:
                        print("用法: cli.py plot ref del <L4_id> <L3_id>（删除次引用）")
                        sys.exit(1)
                    plot_ref_del(int(rrest[0]), int(rrest[1]))
                else:
                    print(f"[novel-cli] 未知 plot ref 子命令: {rsub}")
                    sys.exit(1)
            else:
                print(f"[novel-cli] 未知 plot 子命令: {sub}")
                sys.exit(1)

        elif cmd == "publish":
            if not args:
                print("用法: cli.py publish <plot_node_id>")
                sys.exit(1)
            publish(int(args[0]))

        elif cmd == "fact":
            if not args:
                print("用法: cli.py fact <add|list> [...]")
                sys.exit(1)
            sub = args[0]
            rest = args[1:]
            if sub == "add":
                if not rest:
                    print("用法: cli.py fact add <事实文本> [--category completed|revealed|state_changed] [--source 节点id]")
                    sys.exit(1)
                text = rest[0]
                category = "completed"
                source_node_id = None
                it = iter(rest[1:])
                for a in it:
                    if a == "--category":
                        category = next(it)
                    elif a == "--source":
                        source_node_id = int(next(it))
                fact_add(text, category, source_node_id)
            elif sub == "list":
                category = None
                source_node_id = None
                it = iter(rest)
                for a in it:
                    if a == "--category":
                        category = next(it)
                    elif a == "--source":
                        source_node_id = int(next(it))
                fact_list(category, source_node_id)
            else:
                print(f"[novel-cli] 未知 fact 子命令: {sub}")
                sys.exit(1)

        elif cmd == "context":
            if not args:
                print("用法: cli.py context <node_id>（组装写作上下文：引用/事实/风格规则/前后章）")
                sys.exit(1)
            context(int(args[0]))

        elif cmd == "translation":
            if not args:
                print("用法: cli.py translation <add|set|list|del|apply> [...]")
                sys.exit(1)
            sub = args[0]
            rest = args[1:]
            if sub == "add":
                if len(rest) < 2:
                    print("用法: cli.py translation add <key> <display> [--note 说明]（写作时用 {{key}}，发布时替换为 display）")
                    sys.exit(1)
                key, display = rest[0], rest[1]
                note = None
                if "--note" in rest:
                    idx = rest.index("--note")
                    note = rest[idx + 1]
                translation_add(key, display, note)
            elif sub == "set":
                if len(rest) < 2:
                    print("用法: cli.py translation set <key> <display>（★ 改名：改后 translation apply 更新已发布正文）")
                    sys.exit(1)
                translation_set(rest[0], rest[1])
            elif sub == "list":
                translation_list()
            elif sub == "del":
                if not rest:
                    print("用法: cli.py translation del <key>")
                    sys.exit(1)
                translation_del(rest[0])
            elif sub == "apply":
                node_id = None
                if rest and rest[0] != "--id":
                    node_id = int(rest[0])
                elif "--id" in rest:
                    idx = rest.index("--id")
                    node_id = int(rest[idx + 1])
                translation_apply(node_id)
            else:
                print(f"[novel-cli] 未知 translation 子命令: {sub}")
                sys.exit(1)

        elif cmd == "log":
            if not args:
                print("用法: cli.py log <append|show|rotate> [...]")
                sys.exit(1)
            sub = args[0]
            rest = args[1:]

            if sub == "append":
                if len(rest) < 4:
                    print("用法: cli.py log append <层级> <操作> <目标> <备注>")
                    sys.exit(1)
                log_append(rest[0], rest[1], rest[2], rest[3])

            elif sub == "show":
                tail = 0
                date_str = None
                today = False
                it = iter(rest)
                for a in it:
                    if a == "--tail":
                        tail = int(next(it))
                    elif a == "--date":
                        date_str = next(it)
                    elif a == "--today":
                        today = True
                log_show(tail=tail, date_str=date_str, today=today)

            elif sub == "rotate":
                force = "--force" in rest
                _rotate_log(force=force)
                print(f"[novel-cli] 日志已轮转 ({_today_str()})")
            else:
                print(f"[novel-cli] 未知 log 子命令: {sub}")
                sys.exit(1)

        elif cmd == "validate":
            cmd_validate()

        elif cmd == "export":
            what = args[0] if args else "all"
            cmd_export(what)

        elif cmd == "style":
            if not args:
                print("用法: cli.py style <init|add|rule|import|search|expand|tags|get|overview|list> [...]")
                sys.exit(1)
            sub = args[0]
            rest = args[1:]

            if sub == "init":
                style_init()

            elif sub == "rule":
                if not rest:
                    print("用法: cli.py style rule <add|list|del> [...]")
                    sys.exit(1)
                rsub = rest[0]
                rrest = rest[1:]
                if rsub == "add":
                    if len(rrest) < 2:
                        print("用法: cli.py style rule add <keep|weaken> <指令> [--ref 引例id] [--note 说明]")
                        sys.exit(1)
                    action, content = rrest[0], rrest[1]
                    fragment_ref = note = None
                    it = iter(rrest[2:])
                    for a in it:
                        if a == "--ref":
                            fragment_ref = int(next(it))
                        elif a == "--note":
                            note = next(it)
                    style_rule_add(action, content, fragment_ref, note)
                elif rsub == "list":
                    action = None
                    if "--action" in rrest:
                        idx = rrest.index("--action")
                        action = rrest[idx + 1]
                    style_rule_list(action)
                elif rsub == "del":
                    if not rrest:
                        print("用法: cli.py style rule del <规则id>")
                        sys.exit(1)
                    style_rule_del(int(rrest[0]))
                else:
                    print(f"[novel-cli] 未知 style rule 子命令: {rsub}")
                    sys.exit(1)

            elif sub == "add":
                source = dim = tags_str = fragment = position = note = None
                it = iter(rest)
                for a in it:
                    if a == "--source":
                        source = next(it)
                    elif a == "--dimension":
                        dim = next(it)
                    elif a == "--tags":
                        tags_str = next(it)
                    elif a == "--fragment":
                        fragment = next(it)
                    elif a == "--position":
                        position = next(it)
                    elif a == "--note":
                        note = next(it)
                if not all([source, dim, tags_str, fragment]):
                    print("用法: cli.py style add --source <文件> --dimension <2.1-2.8> --tags '标签,...' --fragment '原文' [--position '位置'] [--note '分析']")
                    sys.exit(1)
                style_add(source, dim, tags_str, fragment, position, note)

            elif sub == "import":
                if not rest:
                    print("用法: cli.py style import <json_file> [--db root|路径]（默认工作区 style.db；--db root 导入根目录参考库）")
                    sys.exit(1)
                db_path = None
                if "--db" in rest:
                    idx = rest.index("--db")
                    db_path = rest[idx + 1]
                    rest.pop(idx)
                    rest.pop(idx)
                style_import(rest[0], db_path)

            elif sub == "search":
                tags_str = dim = None
                match_any = False
                limit = 0
                it = iter(rest)
                for a in it:
                    if a == "--tags":
                        tags_str = next(it)
                    elif a == "--any":
                        match_any = True
                    elif a == "--limit":
                        limit = int(next(it))
                    elif a == "--dimension":
                        dim = next(it)
                if not tags_str:
                    print("用法: cli.py style search --tags '标签1,标签2' [--any] [--limit N] [--dimension 2.X]")
                    sys.exit(1)
                style_search(tags_str, match_any, limit, dim)

            elif sub == "expand":
                if not rest:
                    print("用法: cli.py style expand <fragment_id> [--limit N]")
                    sys.exit(1)
                fid = int(rest[0])
                limit = 0
                if "--limit" in rest:
                    idx = rest.index("--limit")
                    limit = int(rest[idx + 1])
                style_expand(fid, limit)

            elif sub == "tags":
                dim = None
                if "--dimension" in rest:
                    idx = rest.index("--dimension")
                    dim = rest[idx + 1]
                style_tags(dim)

            elif sub == "get":
                if not rest:
                    print("用法: cli.py style get <fragment_id>")
                    sys.exit(1)
                style_get(int(rest[0]))

            elif sub == "overview":
                style_overview()

            elif sub == "list":
                dim = tag = None
                source_only = False
                it = iter(rest)
                for a in it:
                    if a == "--dimension":
                        dim = next(it)
                    elif a == "--tag":
                        tag = next(it)
                    elif a == "--source":
                        source_only = True
                style_list(dim, tag, source_only)

            else:
                print(f"[novel-cli] 未知 style 子命令: {sub}")
                sys.exit(1)

        else:
            print(f"[novel-cli] 未知命令: {cmd}")
            sys.exit(1)

    except SystemExit:
        raise
    except Exception as e:
        print(f"[novel-cli] 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

