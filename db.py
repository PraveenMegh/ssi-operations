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


# ---------- Flexible legacy/Firebase/localStorage import helpers ----------
def _maybe_json(value):
    if isinstance(value, str):
        txt = value.strip()
        if (txt.startswith('{') and txt.endswith('}')) or (txt.startswith('[') and txt.endswith(']')):
            try:
                return json.loads(txt)
            except Exception:
                return value
    return value

def _unwrap_state(raw):
    """Accepts Firebase document, app backup, or localStorage dump and returns the most likely app state."""
    raw = _maybe_json(raw)
    if not isinstance(raw, dict):
        return {}

    # Browser console localStorage export often looks like {"ssiData": "{...}", ...}
    for key in ["ssiData", "SSI_DATA", "ssi_inventory_data", "appState", "state", "payload", "data"]:
        if key in raw:
            val = _maybe_json(raw.get(key))
            if isinstance(val, dict):
                return _unwrap_state(val) if val is not raw else val

    # Firestore REST export may put fields under fields.xxx.arrayValue/mapValue. Basic unwrap.
    if "fields" in raw and isinstance(raw["fields"], dict):
        return _firestore_fields_to_plain(raw["fields"])

    return raw

def _firestore_value_to_plain(v):
    if not isinstance(v, dict):
        return v
    if "stringValue" in v: return _maybe_json(v["stringValue"])
    if "integerValue" in v:
        try: return int(v["integerValue"])
        except Exception: return v["integerValue"]
    if "doubleValue" in v:
        try: return float(v["doubleValue"])
        except Exception: return v["doubleValue"]
    if "booleanValue" in v: return bool(v["booleanValue"])
    if "timestampValue" in v: return v["timestampValue"]
    if "arrayValue" in v:
        values = v.get("arrayValue", {}).get("values", [])
        return [_firestore_value_to_plain(x) for x in values]
    if "mapValue" in v:
        return _firestore_fields_to_plain(v.get("mapValue", {}).get("fields", {}))
    return v

def _firestore_fields_to_plain(fields):
    return {k: _firestore_value_to_plain(v) for k, v in fields.items()}

MODULE_ALIASES = {
    "products": ["products", "product", "items", "itemMaster", "productMaster"],
    "clients": ["clients", "customers", "vendors", "parties", "clientMaster", "customerMaster", "vendorMaster"],
    "inventory": ["inventory", "stock", "stocks", "stockItems", "inventoryItems"],
    "orders": ["orders", "salesOrders", "sales_orders", "saleOrders", "orderList"],
    "dispatches": ["dispatches", "dispatch", "dispatchOrders", "dispatchList"],
    "units": ["units", "uom", "unitMaster"],
    "accounts": ["accounts"],
    "users": ["users"],
    "stock_movements": ["stock_movements", "stockMovements", "stockLedger", "movements"],
    "employees": ["employees"],
    "attendance": ["attendance"],
    "payroll": ["payroll", "salary"],
}

def _rows_from_any(value):
    value = _maybe_json(value)
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        # if dict of records, use values; if a single record, return one record
        vals = list(value.values())
        if vals and all(isinstance(x, dict) for x in vals):
            return vals
        if any(k in value for k in ["id", "name", "orderId", "productId", "clientId", "dispatchId"]):
            return [value]
    return []

def _find_rows(state, module):
    if not isinstance(state, dict):
        return []
    for key in MODULE_ALIASES.get(module, [module]):
        if key in state:
            rows = _rows_from_any(state.get(key))
            if rows:
                return rows
    # One more level down for exports like {collections:{products:[...]}}
    for parent in ["collections", "modules", "tables", "payload", "data", "state"]:
        child = state.get(parent)
        child = _maybe_json(child)
        if isinstance(child, dict):
            rows = _find_rows(child, module)
            if rows:
                return rows
    return []

def normalize_record(module, row):
    row = dict(row)
    # Product aliases
    if module == "products":
        row["id"] = row.get("id") or row.get("productId") or row.get("code") or row.get("productCode") or row.get("name") or row.get("productName")
        row["name"] = row.get("name") or row.get("product_name") or row.get("productName") or row.get("item") or row.get("id")
        row["unit"] = row.get("unit") or row.get("uom") or row.get("unitName") or ""
    elif module == "clients":
        row["id"] = row.get("id") or row.get("clientId") or row.get("customerId") or row.get("vendorId") or row.get("name") or row.get("clientName")
        row["name"] = row.get("name") or row.get("client_name") or row.get("clientName") or row.get("customerName") or row.get("vendorName") or row.get("id")
    elif module == "orders":
        row["id"] = row.get("id") or row.get("orderId") or row.get("orderNo") or row.get("salesOrderNo")
        row["orderId"] = row.get("orderId") or row.get("id")
        row["client_name"] = row.get("client_name") or row.get("clientName") or row.get("customerName") or row.get("buyerName")
        row["items"] = row.get("items") or row.get("products") or row.get("lines") or []
    elif module == "dispatches":
        row["id"] = row.get("id") or row.get("dispatchId") or row.get("dispatchNo")
        row["dispatchId"] = row.get("dispatchId") or row.get("id")
        row["items"] = row.get("items") or row.get("products") or row.get("lines") or []
    elif module == "inventory":
        row["id"] = row.get("id") or row.get("product_id") or row.get("productId") or row.get("product") or row.get("item") or row.get("name")
    return row

def import_state(full_state, include_payroll=False):
    init_db()
    original_state = full_state
    full_state = _unwrap_state(full_state)
    name = "full_import_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    save_backup(name, original_state)
    modules = MODULES + (PAYROLL_MODULES if include_payroll else [])
    counts = {}
    for module in modules:
        rows = _find_rows(full_state, module)
        saved = 0
        for row in rows:
            if isinstance(row, dict):
                rec = normalize_record(module, row)
                if get_record_id(module, rec):
                    upsert_record(module, rec)
                    saved += 1
        counts[module] = saved
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


def product_lookup():
    """Return {product_id: product_record}. Used to display product names instead of IDs."""
    lookup = {}
    for p in list_records("products"):
        pid = str(p.get("id") or p.get("productId") or p.get("sku") or "").strip()
        if pid:
            lookup[pid] = p
    return lookup

def product_name_for(product_id, fallback=""):
    pid = str(product_id or "").strip()
    if not pid:
        return fallback or ""
    p = product_lookup().get(pid, {})
    return p.get("name") or p.get("product_name") or p.get("productName") or fallback or pid

def product_unit_for(product_id, fallback=""):
    pid = str(product_id or "").strip()
    p = product_lookup().get(pid, {})
    return p.get("unit") or p.get("uom") or fallback or ""

def enrich_product_display(row):
    """Keep original IDs but add readable product_name/unit for old imported rows."""
    row = dict(row)
    pid = str(row.get("product_id") or row.get("productId") or row.get("id") or "").strip()
    current_name = row.get("product_name") or row.get("productName") or ""
    # If name is blank or equal to the product id, replace with master product name.
    if pid and (not current_name or str(current_name).strip() == pid):
        row["product_name"] = product_name_for(pid, current_name)
    elif current_name and not row.get("product_name"):
        row["product_name"] = current_name
    if pid and not row.get("unit"):
        row["unit"] = product_unit_for(pid, row.get("uom", ""))
    return row

def enrich_order_items(order):
    order = dict(order)
    items = []
    for item in order.get("items", []) or []:
        if isinstance(item, dict):
            items.append(enrich_product_display(item))
    order["items"] = items
    return order

def add_stock_movement(product_id, product_name, qty, movement_type, ref_no="", remarks="", unit=""):
    qty = float(qty or 0)
    if movement_type in ["OUT", "DISPATCH", "SALE"] and qty > 0:
        qty = -qty
    product_name = product_name_for(product_id, product_name)
    unit = product_unit_for(product_id, unit)
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
        m = enrich_product_display(m)
        pid = str(m.get("product_id") or m.get("product") or m.get("product_name") or "").strip()
        name = m.get("product_name") or product_name_for(pid, pid)
        unit = m.get("unit") or product_unit_for(pid, "")
        if not pid:
            continue
        balances.setdefault(pid, {"product_id": pid, "product_name": name, "unit": unit, "stock_qty": 0.0})
        # Refresh display values on every calculation so old imported rows also show names.
        balances[pid]["product_name"] = product_name_for(pid, name)
        balances[pid]["unit"] = product_unit_for(pid, unit)
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
        name = product_name_for(pid, inv.get("product_name") or inv.get("item") or inv.get("product") or inv.get("name") or pid)
        qty = inv.get("qty") or inv.get("quantity") or inv.get("stock") or inv.get("opening_stock") or 0
        unit = product_unit_for(pid, inv.get("unit") or inv.get("uom") or "")
        add_stock_movement(pid, name, qty, "OPENING", "LEGACY_IMPORT", "Converted from imported inventory", unit)
        count += 1
    return count
