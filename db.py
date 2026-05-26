import os, json, sqlite3
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.getenv('SQLITE_PATH', 'ssi_ops.db')
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()

try:
    import psycopg2
    import psycopg2.extras
except Exception:
    psycopg2 = None

MODULES = ['users','products','clients','inventory','orders','units','accounts']
PAYROLL_MODULES = ['employees','attendance','payroll']


def using_postgres():
    return bool(DATABASE_URL) and psycopg2 is not None

@contextmanager
def get_conn():
    if using_postgres():
        conn = psycopg2.connect(DATABASE_URL)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def execute(conn, sql, params=None):
    params = params or []
    if using_postgres():
        sql = sql.replace('?', '%s')
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur


def init_db():
    with get_conn() as conn:
        if using_postgres():
            execute(conn, '''CREATE TABLE IF NOT EXISTS module_records (
                module TEXT NOT NULL,
                record_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(module, record_id)
            )''')
            execute(conn, '''CREATE TABLE IF NOT EXISTS app_backups (
                id SERIAL PRIMARY KEY,
                backup_name TEXT,
                payload JSONB NOT NULL,
                created_at TEXT NOT NULL
            )''')
        else:
            execute(conn, '''CREATE TABLE IF NOT EXISTS module_records (
                module TEXT NOT NULL,
                record_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(module, record_id)
            )''')
            execute(conn, '''CREATE TABLE IF NOT EXISTS app_backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backup_name TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )''')


def _json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def save_backup(name, full_state):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        execute(conn, 'INSERT INTO app_backups (backup_name, payload, created_at) VALUES (?, ?, ?)',
                [name, full_state if using_postgres() else _json_dumps(full_state), now])


def upsert_record(module, record):
    record_id = str(record.get('id') or record.get('orderId') or record.get('productId') or record.get('clientId') or record.get('username') or f'{module}_{datetime.utcnow().timestamp()}')
    now = datetime.utcnow().isoformat()
    payload = record if using_postgres() else _json_dumps(record)
    with get_conn() as conn:
        if using_postgres():
            execute(conn, '''INSERT INTO module_records(module, record_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(module, record_id) DO UPDATE SET payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at''',
                [module, record_id, json.dumps(record), now])
        else:
            execute(conn, '''INSERT OR REPLACE INTO module_records(module, record_id, payload, updated_at)
                VALUES (?, ?, ?, ?)''', [module, record_id, payload, now])


def import_state(full_state, include_payroll=False):
    init_db()
    name = 'full_firebase_backup_' + datetime.now().strftime('%Y%m%d_%H%M%S')
    save_backup(name, full_state)
    modules = MODULES + (PAYROLL_MODULES if include_payroll else [])
    counts = {}
    for module in modules:
        rows = full_state.get(module, [])
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            if isinstance(row, dict):
                upsert_record(module, row)
        counts[module] = len(rows)
    return counts


def list_records(module):
    init_db()
    with get_conn() as conn:
        cur = execute(conn, 'SELECT record_id, payload, updated_at FROM module_records WHERE module=? ORDER BY updated_at DESC', [module])
        rows = cur.fetchall()
    out = []
    for r in rows:
        payload = r[1] if using_postgres() else r['payload']
        if isinstance(payload, str):
            try: payload = json.loads(payload)
            except Exception: payload = {'raw': payload}
        out.append(payload)
    return out


def export_state():
    data = {}
    for m in MODULES:
        data[m] = list_records(m)
    return data


def flatten(rows):
    flat = []
    for row in rows:
        simple = {}
        for k, v in row.items():
            if isinstance(v, (dict, list)):
                simple[k] = json.dumps(v, ensure_ascii=False)
            else:
                simple[k] = v
        flat.append(simple)
    return flat
