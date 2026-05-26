import json
from datetime import datetime
import pandas as pd
import streamlit as st
from db import init_db, import_state, list_records, export_state, upsert_record, MODULES, PAYROLL_MODULES, flatten, using_postgres

st.set_page_config(page_title='SSI Operations', layout='wide')
init_db()

st.title('SSI Operations — Inventory / Sales / Dispatch')
st.caption('Payroll, Attendance and Employees remain on existing Render app. This Streamlit app imports and manages non-payroll modules only.')

with st.sidebar:
    st.header('Modules')
    page = st.radio('Open', ['Migration / Backup','Dashboard','Products','Clients','Inventory','Orders','Dispatch','Reports','Export Data'])
    st.divider()
    st.write('Storage:', 'PostgreSQL/Supabase' if using_postgres() else 'SQLite local/testing')
    if not using_postgres():
        st.warning('For live Streamlit Cloud, connect DATABASE_URL/Supabase. Local SQLite can reset on redeploy.')


def show_table(module, title):
    st.subheader(title)
    rows = list_records(module)
    if not rows:
        st.info('No data imported yet.')
        return []
    df = pd.DataFrame(flatten(rows))
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button(f'Download {title} CSV', df.to_csv(index=False).encode('utf-8'), f'{module}.csv', 'text/csv')
    return rows

if page == 'Migration / Backup':
    st.subheader('Import existing Render/Firebase data safely')
    st.write('Upload the full JSON backup from your current app. Payroll/attendance/employees will be preserved in backup but NOT imported into this Streamlit operations app unless you tick the checkbox.')
    uploaded = st.file_uploader('Upload Firebase/localStorage JSON backup', type=['json'])
    include_payroll = st.checkbox('Also import payroll/attendance/employees here', value=False, help='Keep unchecked as per current plan.')
    if uploaded and st.button('Import safely'):
        try:
            full_state = json.loads(uploaded.read().decode('utf-8'))
            if 'payload' in full_state and isinstance(full_state['payload'], dict):
                full_state = full_state['payload']
            counts = import_state(full_state, include_payroll=include_payroll)
            st.success('Import completed. Original backup was also stored before import.')
            st.json(counts)
            skipped = {m: len(full_state.get(m, [])) for m in PAYROLL_MODULES if isinstance(full_state.get(m, []), list)}
            if skipped and not include_payroll:
                st.info(f'Payroll modules kept out of Streamlit import: {skipped}')
        except Exception as e:
            st.error(f'Import failed: {e}')
    st.markdown('### Safe migration rule')
    st.write('Do not delete Firebase/Render data until this Streamlit app has been tested and exported successfully.')

elif page == 'Dashboard':
    cols = st.columns(len(MODULES))
    for c, m in zip(cols, MODULES):
        c.metric(m.title(), len(list_records(m)))

elif page == 'Products':
    show_table('products', 'Products')
    with st.expander('Add / update product'):
        pid = st.text_input('Product ID')
        name = st.text_input('Product Name')
        unit = st.text_input('Unit', value='KG')
        rate = st.number_input('Rate', min_value=0.0, step=1.0)
        active = st.checkbox('Active', value=True)
        if st.button('Save Product') and (pid or name):
            upsert_record('products', {'id': pid or name, 'name': name, 'unit': unit, 'rate': rate, 'active': active, 'updatedAt': datetime.now().isoformat()})
            st.success('Product saved')
            st.rerun()

elif page == 'Clients':
    show_table('clients', 'Clients / Vendors')
    with st.expander('Add / update client/vendor'):
        cid = st.text_input('Client ID')
        name = st.text_input('Name')
        gstin = st.text_input('GSTIN')
        address = st.text_area('Address')
        if st.button('Save Client') and (cid or name):
            upsert_record('clients', {'id': cid or name, 'name': name, 'gstin': gstin, 'address': address, 'active': True, 'updatedAt': datetime.now().isoformat()})
            st.success('Client saved')
            st.rerun()

elif page == 'Inventory':
    show_table('inventory', 'Inventory')
    with st.expander('Add stock entry'):
        item = st.text_input('Item / Product')
        qty = st.number_input('Quantity', step=1.0)
        location = st.text_input('Location / Unit')
        if st.button('Save Stock Entry') and item:
            upsert_record('inventory', {'id': f'inv_{datetime.now().timestamp()}', 'item': item, 'qty': qty, 'location': location, 'createdAt': datetime.now().isoformat()})
            st.success('Inventory entry saved')
            st.rerun()

elif page == 'Orders':
    show_table('orders', 'Sales Orders')

elif page == 'Dispatch':
    orders = show_table('orders', 'Dispatch View - Orders')
    st.info('Dispatch uses imported orders. Existing Render payroll data is not touched.')

elif page == 'Reports':
    st.subheader('Reports')
    for module in ['products','clients','inventory','orders']:
        rows = list_records(module)
        st.metric(module.title(), len(rows))
    inv = pd.DataFrame(flatten(list_records('inventory')))
    if not inv.empty:
        st.write('Inventory data')
        st.dataframe(inv, use_container_width=True, hide_index=True)

elif page == 'Export Data':
    st.subheader('Export Streamlit Operations Data')
    data = export_state()
    st.download_button('Download JSON Backup', json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8'), 'ssi_streamlit_operations_backup.json', 'application/json')
    for m in MODULES:
        rows = list_records(m)
        if rows:
            df = pd.DataFrame(flatten(rows))
            st.download_button(f'Download {m}.csv', df.to_csv(index=False).encode('utf-8'), f'{m}.csv', 'text/csv')
