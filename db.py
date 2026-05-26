import os, json, sqlite3
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.getenv("SQLITE_PATH", "ssi_ops.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

try:
    import psycopg2
except Exception:
    psycopg2 = None

MODULES = ["users", "products", "clients", "inventory", "orders", "dispatches", "units", "accounts", "stock_movements"]
PAYROLL_MODULES = ["employees", "attendance", "payroll"]

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
        sql = sql.replace("?", "%s")
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur

def init_db():
    with get_conn() as conn:
        if using_postgres():
            execute(conn, """
                CREATE TABLE IF NOT EXISTS module_records (
                    module TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(module, record_id)
                )
            """)
            execute(conn, """
                CREATE TABLE IF NOT EXISTS app_backups (
                    id SERIAL PRIMARY KEY,
                    backup_name TEXT,
                    payload JSONB NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
        else:
            execute(conn, """
                CREATE TABLE IF NOT EXISTS module_records (
                    module TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(module, record_id)
                )
            """)
            execute(conn, """
                CREATE TABLE IF NOT EXISTS app_backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    backup_name TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

def _json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)

def _json_loads(payload):
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except Exception:
            return {"raw": payload}
    return payload

def make_id(prefix):
    return f"{prefix}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"

def get_record_id(module, record):
    for key in ["id", "orderId", "dispatchId", "productId", "clientId", "username", "movementId"]:
        if record.get(key):
            return str(record.get(key))
    return make_id(module)

def save_backup(name, full_state):
    now = datetime.utcnow().isoformat()
    payload = full_state if using_postgres() else _json_dumps(full_state)
    with get_conn() as conn:
        execute(conn, "INSERT INTO app_backups (backup_name, payload, created_at) VALUES (?, ?, ?)", [name, payload, now])

def upsert_record(module, record):
    init_db()
    record = dict(record)
    record_id = get_record_id(module, record)
    record["id"] = record.get("id") or record_id
    record["updatedAt"] = datetime.utcnow().isoformat()
    payload = record if using_postgres() else _json_dumps(record)
    with get_conn() as conn:
        if using_postgres():
            execute(conn, """
                INSERT INTO module_records(module, record_id, payload, updated_at)
                VALUES (?, ?, ?::jsonb, ?)
                ON CONFLICT(module, record_id)
                DO UPDATE SET payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at
            """, [module, record_id, json.dumps(record), record["updatedAt"]])
        else:
            execute(conn, """
                INSERT OR REPLACE INTO module_records(module, record_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
            """, [module, record_id, payload, record["updatedAt"]])
    return record

def delete_record(module, record_id):
    init_db()
    with get_conn() as conn:
        execute(conn, "DELETE FROM module_records WHERE module=? AND record_id=?", [module, str(record_id)])

def list_records(module):
    init_db()
    with get_conn() as conn:
        cur = execute(conn, "SELECT record_id, payload, updated_at FROM module_records WHERE module=? ORDER BY updated_at DESC", [module])
        rows = cur.fetchall()
    out = []
    for r in rows:
        payload = r[1] if using_postgres() else r["payload"]
        out.append(_json_loads(payload))
    return out

def get_record(module, record_id):
    init_db()
    with get_conn() as conn:
        cur = execute(conn, "SELECT payload FROM module_records WHERE module=? AND record_id=?", [module, str(record_id)])
        row = cur.fetchone()
    if not row:
        return None
    return _json_loads(row[0] if using_postgres() else row["payload"])

def import_state(full_state, include_payroll=False):
    init_db()
    name = "full_import_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    save_backup(name, full_state)
    modules = MODULES + (PAYROLL_MODULES if include_payroll else [])
    counts = {}
    for module in modules:
        rows = full_state.get(module, [])
        if isinstance(rows, dict):
            rows = list(rows.values())
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            if isinstance(row, dict):
                upsert_record(module, row)
        counts[module] = len(rows)
    return counts

def export_state(include_backups=False):
    data = {m: list_records(m) for m in MODULES}
    if include_backups:
        data["_backups"] = list_backups()
    return data

def list_backups():
    init_db()
    with get_conn() as conn:
        cur = execute(conn, "SELECT backup_name, payload, created_at FROM app_backups ORDER BY created_at DESC")
        rows = cur.fetchall()
    out = []
    for r in rows:
        name = r[0] if using_postgres() else r["backup_name"]
        created = r[2] if using_postgres() else r["created_at"]
        out.append({"backup_name": name, "created_at": created})
    return out

def flatten(rows):
    flat = []
    for row in rows:
        simple = {}
        for k, v in row.items():
            simple[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
        flat.append(simple)
    return flat

def add_stock_movement(product_id, product_name, qty, movement_type, ref_no="", remarks="", unit=""):
    qty = float(qty or 0)
    if movement_type in ["OUT", "DISPATCH", "SALE"] and qty > 0:
        qty = -qty
    record = {
        "id": make_id("mov"),
        "movementId": make_id("mov"),
        "date": datetime.now().date().isoformat(),
        "product_id": product_id,
        "product_name": product_name,
        "unit": unit,
        "qty": qty,
        "movement_type": movement_type,
        "ref_no": ref_no,
        "remarks": remarks,
        "createdAt": datetime.utcnow().isoformat(),
    }
    return upsert_record("stock_movements", record)

def stock_balance_by_product():
    balances = {}
    for m in list_records("stock_movements"):
        pid = str(m.get("product_id") or m.get("product") or m.get("product_name") or "").strip()
        name = m.get("product_name") or pid
        if not pid:
            continue
        balances.setdefault(pid, {"product_id": pid, "product_name": name, "unit": m.get("unit", ""), "stock_qty": 0.0})
        try:
            balances[pid]["stock_qty"] += float(m.get("qty", 0) or 0)
        except Exception:
            pass
    return balances

def normalize_legacy_inventory():
    """Creates stock movement rows from old inventory rows once. Does not delete old inventory."""
    existing = list_records("stock_movements")
    if existing:
        return 0
    count = 0
    for inv in list_records("inventory"):
        pid = str(inv.get("product_id") or inv.get("productId") or inv.get("item") or inv.get("product") or inv.get("name") or "").strip()
        if not pid:
            continue
        name = inv.get("product_name") or inv.get("item") or inv.get("product") or inv.get("name") or pid
        qty = inv.get("qty") or inv.get("quantity") or inv.get("stock") or inv.get("opening_stock") or 0
        unit = inv.get("unit") or inv.get("uom") or ""
        add_stock_movement(pid, name, qty, "OPENING", "LEGACY_IMPORT", "Converted from imported inventory", unit)
        count += 1
    return count
