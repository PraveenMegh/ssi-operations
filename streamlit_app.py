import json
from datetime import datetime, date
import pandas as pd
import streamlit as st

from db import (
    init_db, import_state, list_records, export_state, upsert_record, delete_record,
    MODULES, PAYROLL_MODULES, flatten, using_postgres, add_stock_movement,
    stock_balance_by_product, normalize_legacy_inventory, list_backups, make_id,
    enrich_product_display, enrich_order_items, product_name_for, product_unit_for
)

st.set_page_config(page_title="SSI Operations", layout="wide")
init_db()

st.title("SSI Operations — Inventory / Sales / Dispatch")
st.caption("Payroll, Attendance and Employees remain on existing Render app. This app manages operations only.")

with st.sidebar:
    st.header("Modules")
    page = st.radio(
        "Open",
        ["Migration / Backup", "Dashboard", "Products", "Clients / Vendors", "Inventory", "Sales Orders", "Dispatch", "Reports", "Export Data"],
    )
    st.divider()
    st.write("Storage:", "PostgreSQL/Supabase" if using_postgres() else "SQLite local/testing")
    if not using_postgres():
        st.warning("For live Streamlit Cloud, connect DATABASE_URL/Supabase. Local SQLite can reset on redeploy.")

def safe_df(rows):
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(flatten(rows))

def display_rows(module, rows):
    cleaned = []
    for r in rows:
        if module in ["inventory", "stock_movements"]:
            cleaned.append(enrich_product_display(r))
        elif module in ["orders", "dispatches"]:
            cleaned.append(enrich_order_items(r))
        else:
            cleaned.append(r)
    return cleaned

def show_table(module, title):
    st.subheader(title)
    rows = display_rows(module, list_records(module))
    df = safe_df(rows)
    if df.empty:
        st.info("No data available yet.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button(f"Download {title} CSV", df.to_csv(index=False).encode("utf-8"), f"{module}.csv", "text/csv")
    return rows

def product_options():
    rows = list_records("products")
    opts = {}
    for p in rows:
        pid = str(p.get("id") or p.get("productId") or p.get("name") or "").strip()
        name = p.get("name") or p.get("product_name") or pid
        if pid:
            opts[f"{name} ({pid})"] = p
    return opts

def client_options():
    rows = list_records("clients")
    opts = {}
    for c in rows:
        cid = str(c.get("id") or c.get("clientId") or c.get("name") or "").strip()
        name = c.get("name") or c.get("client_name") or cid
        if cid:
            opts[f"{name} ({cid})"] = c
    return opts

if page == "Migration / Backup":
    st.subheader("Import existing Render/Firebase data safely")
    st.write("Upload old app JSON/localStorage/Firebase backup. Importer now detects nested ssiData, payload/data/state, and common module names automatically. Payroll/attendance/employees are skipped unless checkbox is selected.")
    uploaded = st.file_uploader("Upload Firebase/localStorage JSON backup", type=["json"])
    include_payroll = st.checkbox("Also import payroll/attendance/employees here", value=False, help="Keep unchecked as per current plan.")
    if uploaded and st.button("Import safely"):
        try:
            full_state = json.loads(uploaded.read().decode("utf-8"))
            if "payload" in full_state and isinstance(full_state["payload"], dict):
                full_state = full_state["payload"]
            counts = import_state(full_state, include_payroll=include_payroll)
            converted = normalize_legacy_inventory()
            st.success("Import completed. Original backup was stored before import.")
            st.json(counts)
            if sum(counts.values()) == 0:
                st.warning("Import read the file, but no operations records were detected. Please upload the original SSI backup JSON, not the JS code ZIP. If needed, share the backup JSON and I will map its structure.")
            if converted:
                st.info(f"Converted {converted} old inventory rows into stock ledger movements.")
            skipped = {m: len(full_state.get(m, [])) for m in PAYROLL_MODULES if isinstance(full_state.get(m, []), list)}
            if skipped and not include_payroll:
                st.info(f"Payroll modules skipped from operations import: {skipped}")
        except Exception as e:
            st.error(f"Import failed: {e}")

    st.markdown("### Existing import backups")
    b = pd.DataFrame(list_backups())
    if not b.empty:
        st.dataframe(b, use_container_width=True, hide_index=True)

elif page == "Dashboard":
    st.subheader("Operations Dashboard")
    balances = stock_balance_by_product()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Products", len(list_records("products")))
    c2.metric("Clients/Vendors", len(list_records("clients")))
    c3.metric("Orders", len(list_records("orders")))
    c4.metric("Dispatches", len(list_records("dispatches")))
    st.divider()
    stock_df = pd.DataFrame(list(balances.values()))
    st.subheader("Current Stock")
    if stock_df.empty:
        st.info("No stock movement yet. Add opening stock or import old inventory.")
    else:
        st.dataframe(stock_df, use_container_width=True, hide_index=True)

elif page == "Products":
    rows = show_table("products", "Products")
    with st.expander("Add / update product", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            pid = st.text_input("Product ID / Code")
            name = st.text_input("Product Name")
        with col2:
            unit = st.text_input("Unit", value="KG")
            rate = st.number_input("Default Rate", min_value=0.0, step=1.0)
        with col3:
            gst = st.number_input("GST %", min_value=0.0, step=1.0)
            active = st.checkbox("Active", value=True)
        if st.button("Save Product"):
            if not (pid or name):
                st.error("Product ID or Name is required.")
            else:
                rec = {"id": pid or name, "name": name or pid, "unit": unit, "rate": rate, "gst": gst, "active": active}
                upsert_record("products", rec)
                st.success("Product saved.")
                st.rerun()

elif page == "Clients / Vendors":
    show_table("clients", "Clients / Vendors")
    with st.expander("Add / update client/vendor", expanded=True):
        cid = st.text_input("Client/Vendor ID")
        name = st.text_input("Name")
        gstin = st.text_input("GSTIN")
        address = st.text_area("Address")
        contact = st.text_input("Contact / Mobile")
        ctype = st.selectbox("Type", ["Client", "Vendor", "Both"])
        if st.button("Save Client/Vendor"):
            if not (cid or name):
                st.error("ID or Name is required.")
            else:
                upsert_record("clients", {"id": cid or name, "name": name or cid, "gstin": gstin, "address": address, "contact": contact, "type": ctype, "active": True})
                st.success("Client/Vendor saved.")
                st.rerun()

elif page == "Inventory":
    st.subheader("Inventory / Stock Ledger")
    balances = stock_balance_by_product()
    stock_df = pd.DataFrame(list(balances.values()))
    if not stock_df.empty:
        st.dataframe(stock_df, use_container_width=True, hide_index=True)
    else:
        st.info("No stock balance yet.")

    with st.expander("Add stock movement", expanded=True):
        opts = product_options()
        selected = st.selectbox("Product", list(opts.keys()) if opts else ["Manual product"])
        if opts:
            p = opts[selected]
            pid = str(p.get("id"))
            pname = p.get("name") or pid
            unit = p.get("unit", "")
        else:
            pid = st.text_input("Product ID")
            pname = st.text_input("Product Name")
            unit = st.text_input("Unit", value="KG")

        col1, col2, col3 = st.columns(3)
        with col1:
            movement_type = st.selectbox("Movement Type", ["OPENING", "PURCHASE", "ADJUSTMENT_IN", "ADJUSTMENT_OUT"])
        with col2:
            qty = st.number_input("Quantity", min_value=0.0, step=1.0)
        with col3:
            ref_no = st.text_input("Reference No")
        remarks = st.text_area("Remarks")
        if st.button("Save Stock Movement"):
            if not pid or qty <= 0:
                st.error("Product and positive quantity are required.")
            else:
                mt = "OUT" if movement_type == "ADJUSTMENT_OUT" else movement_type
                add_stock_movement(pid, pname, qty, mt, ref_no, remarks, unit)
                st.success("Stock movement saved.")
                st.rerun()

    show_table("stock_movements", "Stock Movement History")

elif page == "Sales Orders":
    st.subheader("Sales Orders")
    show_table("orders", "Existing Sales Orders")
    with st.expander("Create sales order", expanded=True):
        clients = client_options()
        products = product_options()
        order_no = st.text_input("Order No", value=make_id("SO"))
        order_date = st.date_input("Order Date", value=date.today())
        client_label = st.selectbox("Client", list(clients.keys()) if clients else ["Manual client"])
        client = clients.get(client_label, {"id": "", "name": client_label})
        st.write("Add up to 10 product lines")
        lines = []
        for i in range(1, 11):
            with st.container():
                c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                with c1:
                    prod_label = st.selectbox(f"Product {i}", [""] + list(products.keys()), key=f"oprod_{i}")
                with c2:
                    qty = st.number_input(f"Qty {i}", min_value=0.0, step=1.0, key=f"oqty_{i}")
                with c3:
                    rate_default = float(products.get(prod_label, {}).get("rate", 0) or 0) if prod_label else 0.0
                    rate = st.number_input(f"Rate {i}", min_value=0.0, step=1.0, value=rate_default, key=f"orate_{i}")
                with c4:
                    st.write("Amount")
                    st.write(round(qty * rate, 2))
                if prod_label and qty > 0:
                    prod = products[prod_label]
                    lines.append({"product_id": prod.get("id"), "product_name": prod.get("name"), "unit": prod.get("unit", ""), "qty": qty, "rate": rate, "amount": qty * rate})
        total = sum(x["amount"] for x in lines)
        st.metric("Order Total", round(total, 2))
        if st.button("Save Order"):
            if not lines:
                st.error("Add at least one product line.")
            else:
                rec = {
                    "id": order_no,
                    "orderId": order_no,
                    "order_date": str(order_date),
                    "client_id": client.get("id"),
                    "client_name": client.get("name"),
                    "status": "Pending",
                    "items": lines,
                    "total_amount": total,
                    "createdAt": datetime.now().isoformat(),
                    "version": 1,
                }
                upsert_record("orders", rec)
                st.success("Order saved.")
                st.rerun()

elif page == "Dispatch":
    st.subheader("Dispatch")
    orders = [enrich_order_items(o) for o in list_records("orders") if o.get("status") != "Closed"]
    balances = stock_balance_by_product()
    if not orders:
        st.info("No pending order found.")
    else:
        order_map = {f"{o.get('orderId') or o.get('id')} - {o.get('client_name','')}": o for o in orders}
        selected_order = st.selectbox("Select Order", list(order_map.keys()))
        order = order_map[selected_order]
        st.write("Order Items")
        st.dataframe(pd.DataFrame([enrich_product_display(x) for x in order.get("items", [])]), use_container_width=True, hide_index=True)

        dispatch_lines = []
        st.write("Enter dispatch quantity")
        for idx, item in enumerate([enrich_product_display(x) for x in order.get("items", [])]):
            pid = str(item.get("product_id"))
            available = float(balances.get(pid, {}).get("stock_qty", 0) or 0)
            ordered = float(item.get("qty", 0) or 0)
            c1, c2, c3, c4 = st.columns(4)
            c1.write(item.get("product_name"))
            c2.write(f"Ordered: {ordered}")
            c3.write(f"Available: {available}")
            dq = c4.number_input("Dispatch Qty", min_value=0.0, max_value=max(available, 0.0), step=1.0, key=f"dq_{idx}")
            if dq > 0:
                dispatch_lines.append({**item, "dispatch_qty": dq, "available_before": available})

        vehicle = st.text_input("Vehicle / Transport")
        remarks = st.text_area("Dispatch Remarks")
        if st.button("Save Dispatch"):
            if not dispatch_lines:
                st.error("Enter dispatch quantity.")
            else:
                did = make_id("DISP")
                for line in dispatch_lines:
                    add_stock_movement(line.get("product_id"), line.get("product_name"), line.get("dispatch_qty"), "DISPATCH", did, f"Dispatch against {order.get('orderId') or order.get('id')}", line.get("unit",""))
                dispatch = {
                    "id": did,
                    "dispatchId": did,
                    "orderId": order.get("orderId") or order.get("id"),
                    "client_name": order.get("client_name"),
                    "dispatch_date": datetime.now().date().isoformat(),
                    "items": dispatch_lines,
                    "vehicle": vehicle,
                    "remarks": remarks,
                    "createdAt": datetime.now().isoformat(),
                }
                upsert_record("dispatches", dispatch)
                # Basic status update. Full partial remaining tracking can be added next.
                order["status"] = "Dispatched"
                order["lastDispatchId"] = did
                upsert_record("orders", order)
                st.success("Dispatch saved and stock deducted.")
                st.rerun()
    show_table("dispatches", "Dispatch History")

elif page == "Reports":
    st.subheader("Reports")
    balances = pd.DataFrame(list(stock_balance_by_product().values()))
    orders = pd.DataFrame(flatten(display_rows("orders", list_records("orders"))))
    dispatches = pd.DataFrame(flatten(display_rows("dispatches", list_records("dispatches"))))
    movements = pd.DataFrame(flatten(display_rows("stock_movements", list_records("stock_movements"))))

    tab1, tab2, tab3, tab4 = st.tabs(["Current Stock", "Orders", "Dispatches", "Stock Ledger"])
    with tab1:
        if balances.empty: st.info("No stock.")
        else:
            st.dataframe(balances, use_container_width=True, hide_index=True)
            st.download_button("Download Current Stock Excel CSV", balances.to_csv(index=False).encode("utf-8"), "current_stock.csv", "text/csv")
    with tab2:
        if orders.empty: st.info("No orders.")
        else: st.dataframe(orders, use_container_width=True, hide_index=True)
    with tab3:
        if dispatches.empty: st.info("No dispatches.")
        else: st.dataframe(dispatches, use_container_width=True, hide_index=True)
    with tab4:
        if movements.empty: st.info("No stock movements.")
        else: st.dataframe(movements, use_container_width=True, hide_index=True)

elif page == "Export Data":
    st.subheader("Export Streamlit Operations Data")
    data = export_state()
    st.download_button("Download Full JSON Backup", json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"), "ssi_streamlit_operations_backup.json", "application/json")
    for m in MODULES:
        rows = list_records(m)
        if rows:
            df = pd.DataFrame(flatten(rows))
            st.download_button(f"Download {m}.csv", df.to_csv(index=False).encode("utf-8"), f"{m}.csv", "text/csv")
