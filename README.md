# SSI Operations Streamlit App — Data-Safe Split

This app is for moving ONLY these modules to Streamlit:
- Inventory
- Sales / Orders
- Dispatch
- Products
- Clients / Vendors
- Reports
- Users / Units / Accounts data if present

These modules stay on the existing Render app:
- Payroll
- Attendance
- Employees

## Data safety rule
The importer stores a full backup first, then imports only non-payroll modules. It does not delete Firebase/Render data.

## Live database recommendation
For Streamlit Cloud, use Supabase/PostgreSQL by adding this secret/environment variable:

```bash
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/postgres
```

If DATABASE_URL is missing, the app uses SQLite for local testing only. Streamlit Cloud local SQLite can reset after redeploy, so it is not recommended for live business data.

## Migration steps
1. Open your current Render app.
2. Download/export full JSON backup from Backup/Firebase module or browser localStorage.
3. Deploy this Streamlit app.
4. Open Migration / Backup page.
5. Upload JSON backup.
6. Keep `Also import payroll/attendance/employees here` unchecked.
7. Verify counts and records.
8. Download a Streamlit JSON backup after import.
9. Only after verification, use Streamlit for Inventory/Sales/Dispatch.

## Run locally
```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```
