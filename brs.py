import streamlit as st
import pandas as pd
import hashlib
from datetime import date
from supabase import create_client, Client

# ==========================================
# CONFIGURATION & SUPABASE SETUP
# ==========================================
st.set_page_config(page_title="College Finance Portal", layout="wide", page_icon="🔒")

@st.cache_resource
def init_connection():
    url = st.secrets.get("supabase", {}).get("url", "https://YOUR_SUPABASE_URL.supabase.co")
    key = st.secrets.get("supabase", {}).get("key", "YOUR_SUPABASE_KEY")
    return create_client(url, key)

try:
    supabase: Client = init_connection()
except Exception as e:
    st.warning("⚠️ Supabase is not connected yet. Add your URL and Key to Streamlit secrets.")
    st.stop()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ''
    st.session_state.role = ''

# ==========================================
# AUTHENTICATION (LOGIN MODULE)
# ==========================================
def login_screen():
    st.title("🔒 College Finance Portal")
    st.markdown("Please log in to access the system.")
    
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login", use_container_width=True)
            
            if submitted:
                hashed_pwd = hash_password(password)
                try:
                    response = supabase.table("app_users").select("*").eq("username", username).eq("password_hash", hashed_pwd).execute()
                    if len(response.data) > 0:
                        user = response.data[0]
                        st.session_state.logged_in = True
                        st.session_state.username = user['username']
                        st.session_state.role = user['role']
                        st.rerun()
                    else:
                        st.error("❌ Invalid Username or Password")
                except Exception as e:
                    st.error(f"Database error: {e}")

# ==========================================
# VIEW 1: CLERK DATA ENTRY DASHBOARD
# ==========================================
def clerk_dashboard():
    st.title("📝 Desk Entry: Payment Synchronization")
    st.markdown(f"Logged in as: **{st.session_state.username}** (Clerk)")
    
    # --- DATA ENTRY FORM ---
    with st.form("clerk_entry_form"):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.markdown("### Student Details")
            usn = st.text_input("Student USN (e.g., 1AM24CS099)")
            student_name = st.text_input("Student Name")
            branch_options = ["AE", "AIML", "CSE", "CSE-AIML", "CSE-DS", "CV", "ECE", "EEE", "ISE", "M Tech", "MBA", "MCA"]
            branch = st.selectbox("Branch", branch_options)
            
        with col2:
            st.markdown("### Payment Details")
            payment_date = st.date_input("Date of Payment", date.today())
            amount = st.number_input("Amount Paid (₹)", min_value=1.0, step=100.0)
            
            payment_type = st.selectbox("Fee Type", ["Exam Fee", "Tuition Fee", "Fine", "Lab Dues", "Transport", "Other"])
            other_description = ""
            # If 'Other' is selected, show a text input for description (max 20 chars)
            if payment_type == "Other":
                other_description = st.text_input("Specify Other Fee (Max 20 chars)", max_chars=20)
            
        with col3:
            st.markdown("### Transaction Details")
            payment_mode = st.selectbox("Payment Mode", ["UPI (QR / App)", "Bank Transfer (NEFT / RTGS)"])
            utr = st.text_input("Transaction ID / UTR No.")
            
            # Blank text input for manual account entry
            college_account = st.text_input("Credited To A/C (Bank details)")
            
        st.markdown("---")
        submitted = st.form_submit_button("💾 Save & Sync Receipt", type="primary", use_container_width=True)
        
        if submitted:
            # Handle the 'Other' description logic
            final_payment_type = payment_type
            if payment_type == "Other":
                if not other_description.strip():
                    st.error("⚠️ Please specify the description for 'Other' fee type.")
                    return
                final_payment_type = f"Other - {other_description.strip()}"

            # Validate Mandatory Fields
            if not usn or not student_name or not utr or not college_account:
                st.error("⚠️ USN, Name, UTR, and Account details are mandatory fields!")
            # Validate UPI (Exactly 12 numeric digits)
            elif payment_mode == "UPI (QR / App)" and (not utr.isdigit() or len(utr) != 12):
                st.error("❌ UPI UTR must be exactly 12 numeric digits!")
            # Validate NEFT/RTGS (Exactly 22 alphanumeric characters)
            elif payment_mode == "Bank Transfer (NEFT / RTGS)" and (not utr.isalnum() or len(utr) != 22):
                st.error("❌ NEFT/RTGS UTR must be exactly 22 alphanumeric characters!")
            else:
                data = {
                    "payment_date": str(payment_date),
                    "amount": amount,
                    "utr_number": utr.strip().upper(),
                    "payment_type": final_payment_type,
                    "college_account": college_account.strip(),
                    "payment_mode": payment_mode,
                    "usn": usn.strip().upper(),
                    "student_name": student_name.strip().title(),
                    "branch": branch,
                    "entered_by": st.session_state.username
                }
                
                try:
                    supabase.table("cash_receipts").insert(data).execute()
                    st.success(f"✅ Receipt for ₹{amount} (UTR: {utr.strip().upper()}) saved successfully!")
                except Exception as e:
                    if "duplicate key value" in str(e).lower():
                        st.error(f"❌ Duplicate Entry! The UTR {utr.strip().upper()} has already been entered in the system.")
                    else:
                        st.error(f"Database error: {e}")

    # --- DEPARTMENT DOWNLOAD SECTION ---
    st.markdown("---")
    st.markdown("### 📥 Download Department Reports")
    st.markdown("Download a CSV file of all transactions recorded on a specific date for departmental filing.")
    
    col_dl1, col_dl2 = st.columns([1, 2])
    with col_dl1:
        dl_date = st.date_input("Select Date", date.today(), key="dl_date")
    with col_dl2:
        st.markdown("<br>", unsafe_allow_html=True) # Spacer to align button
        if st.button("Fetch Daily Report"):
            try:
                res = supabase.table("cash_receipts").select("*").eq("payment_date", str(dl_date)).execute()
                df = pd.DataFrame(res.data)
                
                if not df.empty:
                    # Clean up the output dataframe for the department
                    df = df[['payment_date', 'usn', 'student_name', 'branch', 'payment_type', 
                             'amount', 'utr_number', 'payment_mode', 'college_account', 'entered_by']]
                    csv = df.to_csv(index=False).encode('utf-8')
                    
                    st.download_button(
                        label=f"⬇️ Download {dl_date} Report (CSV)",
                        data=csv,
                        file_name=f"Dept_Report_{dl_date}.csv",
                        mime="text/csv",
                        type="primary"
                    )
                else:
                    st.info(f"No records found for {dl_date}.")
            except Exception as e:
                st.error(f"Failed to fetch report: {e}")


# ==========================================
# VIEW 2: ADMIN DASHBOARD (SCRUTINY & EXPORT)
# ==========================================
def admin_dashboard():
    st.title("📊 Admin Consolidation & Scrutiny Panel")
    
    tab1, tab2 = st.tabs(["📥 Download Cash Book", "👥 Manage Users"])
    
    with tab1:
        st.markdown("Filter and export clerk entries for manual scrutiny and external consolidation.")
        
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("From Date", date.today().replace(day=1))
        with col2:
            end_date = st.date_input("To Date", date.today())
            
        if st.button("Fetch Data"):
            try:
                res = supabase.table("cash_receipts") \
                    .select("*") \
                    .gte("payment_date", str(start_date)) \
                    .lte("payment_date", str(end_date)) \
                    .execute()
                
                df = pd.DataFrame(res.data)
                
                if not df.empty:
                    df = df[['payment_date', 'usn', 'student_name', 'branch', 'payment_type', 
                             'amount', 'utr_number', 'payment_mode', 'college_account', 'entered_by', 'created_at']]
                    
                    st.dataframe(df, use_container_width=True)
                    
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="⬇️ Download Data as CSV for Scrutiny",
                        data=csv,
                        file_name=f"CashBook_{start_date}_to_{end_date}.csv",
                        mime="text/csv",
                        type="primary"
                    )
                else:
                    st.info("No records found for the selected date range.")
            except Exception as e:
                st.error(f"Failed to fetch data: {e}")
                
    with tab2:
        st.markdown("Create new clerk logins for the admin desks.")
        with st.form("create_user_form"):
            new_user = st.text_input("New Username")
            new_pass = st.text_input("Temporary Password", type="password")
            new_role = st.selectbox("Role", ["clerk", "admin"])
            
            if st.form_submit_button("Create User"):
                if new_user and new_pass:
                    user_data = {
                        "username": new_user.lower(),
                        "password_hash": hash_password(new_pass),
                        "role": new_role
                    }
                    try:
                        supabase.table("app_users").insert(user_data).execute()
                        st.success(f"User '{new_user}' created successfully with {new_role} privileges.")
                    except Exception as e:
                        if "duplicate key" in str(e).lower():
                            st.error("Username already exists.")
                        else:
                            st.error(f"Error: {e}")

# ==========================================
# MAIN ROUTING LOGIC
# ==========================================
def main():
    if st.session_state.logged_in:
        with st.sidebar:
            st.markdown(f"**User:** {st.session_state.username}")
            st.markdown(f"**Role:** {st.session_state.role.capitalize()}")
            if st.button("Logout", use_container_width=True):
                st.session_state.logged_in = False
                st.session_state.username = ''
                st.session_state.role = ''
                st.rerun()
                
    if not st.session_state.logged_in:
        login_screen()
    else:
        if st.session_state.role == 'admin':
            admin_dashboard()
        elif st.session_state.role == 'clerk':
            clerk_dashboard()
        else:
            st.error("Unauthorized role.")

if __name__ == "__main__":
    main()
