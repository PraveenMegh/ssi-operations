# SSI Operations Streamlit App v2 — Data-Safe Operations Module

This app is for **operations only**:
- Products
- Clients / Vendors
- Inventory
- Sales Orders
- Dispatch
- Reports
- Backup / Import / Export

These stay in the existing Render payroll app:
- Payroll
- Attendance
- Employees

## Important data safety points

1. Import stores a full backup first.
2. Payroll / Attendance / Employees are skipped by default.
3. Old inventory rows are not deleted.
4. Old inventory can be converted into stock ledger movements.
5. Dispatch deducts stock through stock movement records.
6. Current stock is calculated from the stock ledger, not overwritten manually.

## Recommended live database

For Streamlit Cloud, use Supabase/PostgreSQL and set:

```bash
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/postgres
```

SQLite is okay only for local testing.

## Run locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Migration flow

1. Export JSON backup from old Render/Firebase app.
2. Deploy this app.
3. Open **Migration / Backup**.
4. Upload JSON.
5. Keep payroll checkbox unchecked.
6. Verify Products, Clients, Inventory, Orders.
7. Export a new JSON backup from Streamlit.
8. Then start using operations in Streamlit.
