import os
import json
import time
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "salemi_bot.db")
JSON_BACKUP_FILE = os.path.join(BASE_DIR, "salemi_targets_backup.json")


def get_conn():
    return sqlite3.connect(DB_FILE)


def db_execute(query, params=(), fetchone=False, fetchall=False):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(query, params)

    result = None
    if fetchone:
        result = cur.fetchone()
    elif fetchall:
        result = cur.fetchall()

    conn.commit()
    conn.close()
    return result


def table_exists(table_name: str) -> bool:
    row = db_execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
        fetchone=True
    )
    return row is not None


def get_table_columns(table_name: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table_name})")
    rows = cur.fetchall()
    conn.close()
    return [row[1] for row in rows]


def ensure_column(table_name: str, column_name: str, column_sql: str, log_func=print):
    cols = get_table_columns(table_name)
    if column_name not in cols:
        log_func(f"[LOG] Adding column {column_name} to {table_name}")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
        conn.commit()
        conn.close()


def init_db(log_func=print):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            target_name TEXT NOT NULL,
            reply_text TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_owner_input (
            owner_id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            target_name TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            type TEXT,
            first_seen_at INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS message_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER,
            display_name TEXT,
            username TEXT,
            text TEXT,
            created_at INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()

    ensure_column("targets", "reply_type", "TEXT NOT NULL DEFAULT 'text'", log_func)
    ensure_column("targets", "reply_file_id", "TEXT", log_func)
    ensure_column("targets", "created_at", "INTEGER NOT NULL DEFAULT 0", log_func)
    ensure_column("targets", "updated_at", "INTEGER NOT NULL DEFAULT 0", log_func)
    ensure_column("pending_owner_input", "created_at", "INTEGER NOT NULL DEFAULT 0", log_func)

    now_ts = int(time.time())

    db_execute("""
        UPDATE targets
        SET created_at = ?
        WHERE created_at IS NULL OR created_at = 0
    """, (now_ts,))

    db_execute("""
        UPDATE targets
        SET updated_at = ?
        WHERE updated_at IS NULL OR updated_at = 0
    """, (now_ts,))

    db_execute("""
        UPDATE targets
        SET reply_type = 'text'
        WHERE reply_type IS NULL OR reply_type = ''
    """)

    export_targets_to_json()


def export_targets_to_json():
    if not table_exists("targets"):
        return

    cols = set(get_table_columns("targets"))
    if not {"chat_id", "target_name"}.issubset(cols):
        return

    select_parts = [
        "chat_id",
        "target_name",
        "reply_type" if "reply_type" in cols else "'text' AS reply_type",
        "reply_text" if "reply_text" in cols else "NULL AS reply_text",
        "reply_file_id" if "reply_file_id" in cols else "NULL AS reply_file_id",
        "created_at" if "created_at" in cols else "0 AS created_at",
        "updated_at" if "updated_at" in cols else "0 AS updated_at",
    ]

    rows = db_execute(f"""
        SELECT {", ".join(select_parts)}
        FROM targets
        ORDER BY chat_id, target_name
    """, fetchall=True) or []

    data = []
    for row in rows:
        data.append({
            "chat_id": row[0],
            "target_name": row[1],
            "reply_type": row[2],
            "reply_text": row[3],
            "reply_file_id": row[4],
            "created_at": row[5],
            "updated_at": row[6],
        })

    with open(JSON_BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_or_keep_target(chat_id: int, target_name: str):
    now_ts = int(time.time())

    exists = db_execute("""
        SELECT id FROM targets
        WHERE chat_id = ? AND target_name = ?
    """, (chat_id, target_name), fetchone=True)

    if exists:
        db_execute("""
            UPDATE targets
            SET updated_at = ?
            WHERE chat_id = ? AND target_name = ?
        """, (now_ts, chat_id, target_name))
    else:
        db_execute("""
            INSERT INTO targets (
                chat_id, target_name, reply_type, reply_text, reply_file_id, created_at, updated_at
            )
            VALUES (?, ?, 'text', NULL, NULL, ?, ?)
        """, (chat_id, target_name, now_ts, now_ts))

    export_targets_to_json()


def set_target_reply_text(chat_id: int, target_name: str, reply_text: str):
    now_ts = int(time.time())
    db_execute("""
        UPDATE targets
        SET reply_type = 'text',
            reply_text = ?,
            reply_file_id = NULL,
            updated_at = ?
        WHERE chat_id = ? AND target_name = ?
    """, (reply_text, now_ts, chat_id, target_name))
    export_targets_to_json()


def set_target_reply_gif_file(chat_id: int, target_name: str, file_id: str):
    now_ts = int(time.time())
    db_execute("""
        UPDATE targets
        SET reply_type = 'gif',
            reply_text = NULL,
            reply_file_id = ?,
            updated_at = ?
        WHERE chat_id = ? AND target_name = ?
    """, (file_id, now_ts, chat_id, target_name))
    export_targets_to_json()


def get_targets(chat_id: int):
    return db_execute("""
        SELECT target_name, reply_type, reply_text, reply_file_id
        FROM targets
        WHERE chat_id = ?
        ORDER BY target_name
    """, (chat_id,), fetchall=True) or []


def delete_target(chat_id: int, target_name: str):
    exists = db_execute("""
        SELECT 1 FROM targets
        WHERE chat_id = ? AND target_name = ?
    """, (chat_id, target_name), fetchone=True)

    if not exists:
        return False

    db_execute("""
        DELETE FROM targets
        WHERE chat_id = ? AND target_name = ?
    """, (chat_id, target_name))

    export_targets_to_json()
    return True


def delete_all_targets(chat_id: int):
    row = db_execute("""
        SELECT COUNT(*)
        FROM targets
        WHERE chat_id = ?
    """, (chat_id,), fetchone=True)

    count = row[0] if row else 0

    db_execute("""
        DELETE FROM targets
        WHERE chat_id = ?
    """, (chat_id,))

    export_targets_to_json()
    return count


def set_pending_owner_input(owner_id: int, chat_id: int, target_name: str):
    db_execute("""
        INSERT OR REPLACE INTO pending_owner_input (owner_id, chat_id, target_name, created_at)
        VALUES (?, ?, ?, ?)
    """, (owner_id, chat_id, target_name, int(time.time())))


def get_pending_owner_input(owner_id: int):
    return db_execute("""
        SELECT chat_id, target_name
        FROM pending_owner_input
        WHERE owner_id = ?
    """, (owner_id,), fetchone=True)


def clear_pending_owner_input(owner_id: int):
    db_execute("""
        DELETE FROM pending_owner_input
        WHERE owner_id = ?
    """, (owner_id,))


def upsert_group(chat_id: int, title: str, chat_type: str):
    now_ts = int(time.time())
    exists = db_execute("""
        SELECT chat_id FROM groups WHERE chat_id = ?
    """, (chat_id,), fetchone=True)

    if exists:
        db_execute("""
            UPDATE groups
            SET title = ?, type = ?, last_seen_at = ?
            WHERE chat_id = ?
        """, (title, chat_type, now_ts, chat_id))
    else:
        db_execute("""
            INSERT INTO groups (chat_id, title, type, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
        """, (chat_id, title, chat_type, now_ts, now_ts))


def get_all_groups():
    return db_execute("""
        SELECT chat_id, title, type
        FROM groups
        ORDER BY last_seen_at DESC
    """, fetchall=True) or []


def add_message_log(chat_id: int, user_id, display_name: str, username: str, text: str, created_at: int):
    db_execute("""
        INSERT INTO message_logs (chat_id, user_id, display_name, username, text, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (chat_id, user_id, display_name, username, text, created_at))


def get_24h_group_stats(chat_id: int, now_ts: int | None = None):
    if now_ts is None:
        now_ts = int(time.time())
    since_ts = now_ts - 24 * 60 * 60

    return db_execute("""
        SELECT
            COALESCE(display_name, username, 'کاربر') AS name,
            COUNT(*) AS msg_count
        FROM message_logs
        WHERE chat_id = ? AND created_at >= ?
        GROUP BY user_id, display_name, username
        ORDER BY msg_count DESC, name ASC
    """, (chat_id, since_ts), fetchall=True) or []


def get_24h_group_stats_with_ids(chat_id: int, now_ts: int | None = None):
    if now_ts is None:
        now_ts = int(time.time())
    since_ts = now_ts - 24 * 60 * 60

    return db_execute("""
        SELECT
            user_id,
            COALESCE(display_name, username, 'کاربر') AS name,
            COUNT(*) AS msg_count
        FROM message_logs
        WHERE chat_id = ? AND created_at >= ? AND user_id IS NOT NULL
        GROUP BY user_id, display_name, username
        ORDER BY msg_count DESC, name ASC
    """, (chat_id, since_ts), fetchall=True) or []