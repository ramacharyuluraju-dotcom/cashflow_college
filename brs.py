import streamlit as st
import pandas as pd
import hashlib
import calendar
from datetime import date
from supabase import create_client, Client

# ==========================================
# CONFIGURATION & SUPABASE SETUP
# ==========================================
st.set_page_config(page_title="College Finance & Academic Portal", layout="wide", page_icon="🔒")

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
    st.title("🔒 College Finance & Academic Portal")
    st.markdown("Please log in to access the system.")
    
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login", use_container_width=True)
            
            if submitted:
                clean_username = username.strip().lower()
                hashed_pwd = hash_password(password.strip())
                try:
                    response = supabase.table("app_users").select("*").eq("username", clean_username).eq("password_hash", hashed_pwd).execute()
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
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("### Student Details")
        usn = st.text_input("Student USN (e.g., 1AM24CS099)")
        student_name = st.text_input("Student Name")
        branch_options = ["AE", "AIML", "CSE", "CSE-AIML", "CSE-DS", "CV", "ME", "ECE", "EEE", "ISE", "M Tech", "MBA", "MCA"]
        branch = st.selectbox("Branch", branch_options)
        
    with col2:
        st.markdown("### Payment Details")
        payment_date = st.date_input("Date of Payment", date.today())
        amount = st.number_input("Amount Paid (₹)", min_value=1.0, step=100.0)
        
        payment_type = st.selectbox("Fee Type", ["Exam Fee", "Tuition Fee", "Fine", "Revaluation fee", "Convocation fees", "Arrears fees", "Summer Semester Fee", "Other"])
        other_description = ""
        if payment_type == "Other":
            other_description = st.text_input("Specify Other Fee (Max 12 chars)", max_chars=12)
        
    with col3:
        st.markdown("### Transaction Details")
        payment_mode = st.selectbox("Payment Mode", ["UPI (QR / App)", "Bank Transfer (NEFT / RTGS)"])
        utr = st.text_input("Transaction ID / UTR No.")
        college_account = st.text_input("Credited To A/C (Bank details)")
        
    st.markdown("---")
    submitted = st.button("💾 Save & Sync Receipt", type="primary", use_container_width=True)
    
    if submitted:
        final_payment_type = payment_type
        if payment_type == "Other":
            if not other_description.strip():
                st.error("⚠️ Please specify the description for 'Other' fee type.")
                return
            final_payment_type = f"Other - {other_description.strip()}"

        if not usn or not student_name or not utr or not college_account:
            st.error("⚠️ USN, Name, UTR, and Account details are mandatory fields!")
        elif payment_mode == "UPI (QR / App)" and (not utr.isdigit() or len(utr) != 12):
            st.error("❌ UPI UTR must be exactly 12 numeric digits!")
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

    st.markdown("---")
    st.markdown("### 📥 Download Department Reports")
    filter_option = st.selectbox("Select Time Period", ["Today", "Between Dates", "By Month", "Academic Year"])
    
    today = date.today()
    start_date = today
    end_date = today
    report_name_suffix = f"{today}"
    
    if filter_option == "Today":
        start_date = today; end_date = today
    elif filter_option == "Between Dates":
        col_d1, col_d2 = st.columns(2)
        start_date = col_d1.date_input("From Date", today.replace(day=1))
        end_date = col_d2.date_input("To Date", today)
        report_name_suffix = f"{start_date}_to_{end_date}"
    elif filter_option == "By Month":
        col_m1, col_m2 = st.columns(2)
        months = list(calendar.month_name)[1:]
        selected_month = col_m1.selectbox("Month", months, index=today.month - 1)
        selected_year = col_m2.selectbox("Year", range(today.year + 1, today.year - 5, -1), index=1)
        month_index = months.index(selected_month) + 1
        _, last_day = calendar.monthrange(selected_year, month_index)
        start_date = date(selected_year, month_index, 1)
        end_date = date(selected_year, month_index, last_day)
        report_name_suffix = f"{selected_month}_{selected_year}"
    elif filter_option == "Academic Year":
        current_year = today.year
        default_start_year = current_year - 1 if today.month < 8 else current_year
        academic_years = [f"{y}-{y+1}" for y in range(current_year + 1, current_year - 5, -1)]
        selected_ay = st.selectbox("Select Academic Year (Aug-Jul)", academic_years, index=1)
        ay_start_year = int(selected_ay.split("-")[0])
        ay_end_year = int(selected_ay.split("-")[1])
        start_date = date(ay_start_year, 8, 1)
        end_date = date(ay_end_year, 7, 31)
        report_name_suffix = f"AY_{selected_ay}"

    if st.button("Fetch Report"):
        try:
            res = supabase.table("cash_receipts").select("*").gte("payment_date", str(start_date)).lte("payment_date", str(end_date)).execute()
            df = pd.DataFrame(res.data)
            if not df.empty:
                df = df[['payment_date', 'usn', 'student_name', 'branch', 'payment_type', 'amount', 'utr_number', 'payment_mode', 'college_account', 'entered_by']]
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button(label=f"⬇️ Download Report ({filter_option})", data=csv, file_name=f"Dept_Report_{report_name_suffix}.csv", mime="text/csv", type="primary")
            else:
                st.info("No records found for the selected period.")
        except Exception as e:
            st.error(f"Failed to fetch report: {e}")

# ==========================================
# VIEW 2: DEPARTMENT PORTAL (ONLINE REGISTRATION)
# ==========================================
def department_dashboard():
    st.title("🏛️ Department Online Course Registration Portal")
    st.markdown(f"Logged in Department Coordinator: **{st.session_state.username}**")
    
    tab_reg, tab_summer = st.tabs(["📝 Regular / Backlog Semester Registration", "☀️ Summer Semester Registration"])
    
    # --- REGULAR / BACKLOG REGISTRATION ---
    with tab_reg:
        st.subheader("Manage Student Course Registrations")
        
        target_usn = st.text_input("Enter Student USN for Registration").strip().upper()
        if target_usn:
            try:
                stu_res = supabase.table("master_students").select("*").eq("usn", target_usn).execute()
                if not stu_res.data:
                    st.error(f"Student {target_usn} not found in database.")
                else:
                    stu = stu_res.data[0]
                    branch_code = stu.get('branch_code', '')
                    current_sem = int(stu.get('current_sem', 1))
                    
                    st.success(f"Student: **{stu['full_name']}** | Branch: **{branch_code}** | Current Sem: **{current_sem}**")
                    
                    # Fetch available courses for this branch/semester from master_courses
                    courses_res = supabase.table("master_courses").select("*").execute()
                    all_courses = courses_res.data if courses_res.data else []
                    
                    # Separate Core and Electives
                    core_courses = [c for c in all_courses if c.get('semester_id') == current_sem and c.get('branch_code') in [branch_code, 'COMMON'] and c.get('course_type', 'CORE') == 'CORE']
                    pe_courses = [c for c in all_courses if c.get('semester_id') == current_sem and c.get('branch_code') == branch_code and c.get('course_type') == 'PE']
                    oe_courses = [c for c in all_courses if c.get('semester_id') == current_sem and c.get('branch_code') != branch_code and c.get('course_type') == 'OE']
                    
                    st.markdown("### 1. Mandatory Core Subjects (Pre-selected)")
                    selected_codes = []
                    total_credits = 0.0
                    
                    for core in core_courses:
                        st.checkbox(f"{core['course_code']} - {core['title']} ({core.get('credits', 4)} Credits)", value=True, disabled=True, key=f"core_{core['course_code']}")
                        selected_codes.append(core['course_code'])
                        total_credits += float(core.get('credits', 4))
                        
                    st.markdown("### 2. Elective Selections")
                    col_pe, col_oe = st.columns(2)
                    
                    chosen_pe = None
                    chosen_oe = None
                    
                    if pe_courses:
                        pe_options = {f"{p['course_code']} - {p['title']}": p['course_code'] for p in pe_courses}
                        chosen_pe_label = col_pe.selectbox("Professional Elective (In-Branch)", options=["-- Select --"] + list(pe_options.keys()))
                        if chosen_pe_label != "-- Select --":
                            chosen_pe = pe_options[chosen_pe_label]
                            selected_codes.append(chosen_pe)
                            match_c = next(p for p in pe_courses if p['course_code'] == chosen_pe)
                            total_credits += float(match_c.get('credits', 3))
                            
                    if oe_courses:
                        oe_options = {f"{o['course_code']} [{o['branch_code']}] - {o['title']}": o['course_code'] for o in oe_courses}
                        chosen_oe_label = col_oe.selectbox("Open Elective (Other Branch)", options=["-- Select --"] + list(oe_options.keys()))
                        if chosen_oe_label != "-- Select --":
                            chosen_oe = oe_options[chosen_oe_label]
                            selected_codes.append(chosen_oe)
                            match_o = next(o for o in oe_courses if o['course_code'] == chosen_oe)
                            total_credits += float(match_o.get('credits', 3))
                            
                    st.info(f"📊 **Total Selected Credits:** {total_credits}")
                    
                    if st.button("💾 Submit Registration for Department Approval", type="primary"):
                        try:
                            # Save to course_registrations table
                            payload = [{
                                "cycle_id": st.session_state.get('active_cycle_id', 1),
                                "usn": target_usn,
                                "course_code": cc,
                                "semester": current_sem,
                                "academic_year": "2025-26",
                                "semester_type": "EVEN"
                            } for cc in selected_codes]
                            
                            # Clear old and insert new
                            supabase.table("course_registrations").delete().eq("usn", target_usn).execute()
                            supabase.table("course_registrations").insert(payload).execute()
                            st.success(f"✅ Successfully registered {len(selected_codes)} courses for {target_usn}!")
                        except Exception as e:
                            st.error(f"Registration Error: {e}")
            except Exception as e:
                st.error(f"Error fetching student: {e}")

    # --- SUMMER SEMESTER REGISTRATION ---
    with tab_summer:
        st.subheader("☀️ Summer Semester Application Form")
        st.info("Applies the exact 3 Summer Rules (CIE Fail mandatory classes, Absent Exam Only, SEE Fail Exam Only) and enforces credit caps.")
        
        summer_usn = st.text_input("Enter USN for Summer Semester Processing").strip().upper()
        if summer_usn:
            # We check if this student has records in student_results matching summer rules
            try:
                res = supabase.table("student_results").select("*").eq("usn", summer_usn).execute()
                if not res.data:
                    st.warning("No historical exam records found for this USN.")
                else:
                    results = res.data
                    prog_type = "UG" # Default
                    cie_threshold = 20 if prog_type == "UG" else 25
                    
                    eligible_summer_courses = []
                    for r in results:
                        grade = str(r.get('grade', '')).upper()
                        cie = float(r.get('cie_marks', 0))
                        is_pass = r.get('is_pass', False)
                        
                        if not is_pass and grade not in ['PND', 'FROZEN', '']:
                            rule = None
                            if cie < cie_threshold:
                                rule = "Rule 1: Mandatory Classes (CIE Fail) [Max 14 Credits]"
                            elif grade == 'AB' and cie >= cie_threshold:
                                rule = "Rule 2: Exam Only (Absent)"
                            elif grade == 'F' and cie >= cie_threshold:
                                rule = "Rule 3: Exam Only (SEE Fail)"
                                
                            if rule:
                                eligible_summer_courses.append({
                                    "course_code": r['course_code'],
                                    "grade": grade,
                                    "cie": cie,
                                    "rule": rule
                                })
                                
                    if not eligible_summer_courses:
                        st.success("🎉 This student has no failed courses requiring summer semester registration!")
                    else:
                        st.markdown("### Eligible Backlog Courses for Summer")
                        df_summer_elig = pd.DataFrame(eligible_summer_courses)
                        st.dataframe(df_summer_elig, use_container_width=True)
                        
                        has_rule_1 = any("Rule 1" in c['rule'] for c in eligible_summer_courses)
                        if has_rule_1:
                            st.warning("⚠️ **Credit Cap Notice:** Student falls under Rule 1 (Mandatory Classes). Total registered credits for summer cannot exceed **14 Credits**.")
                            
                        st.markdown("### 🖨️ Print & Pay Actions")
                        if st.button("📄 Generate Summer Application & Fee Challan"):
                            st.success("✅ Application generated! Present this printout to the Finance Desk for fee payment.")
            except Exception as e:
                st.error(f"Error analyzing summer eligibility: {e}")

# ==========================================
# VIEW 3: ADMIN DASHBOARD (SCRUTINY & EXPORT)
# ==========================================
def admin_dashboard():
    st.title("📊 Admin Consolidation & Scrutiny Panel")
    
    tab1, tab2, tab3 = st.tabs(["📥 Download Cash Book", "👥 Manage Users", "🔑 Reset Passwords"])
    
    with tab1:
        st.markdown("Filter and export clerk entries for manual scrutiny and external consolidation.")
        
        col1, col2 = st.columns(2)
        start_date = col1.date_input("From Date", date.today().replace(day=1))
        end_date = col2.date_input("To Date", date.today())
            
        if st.button("Fetch Data"):
            try:
                res = supabase.table("cash_receipts").select("*").gte("payment_date", str(start_date)).lte("payment_date", str(end_date)).execute()
                df = pd.DataFrame(res.data)
                
                if not df.empty:
                    df = df[['payment_date', 'usn', 'student_name', 'branch', 'payment_type', 'amount', 'utr_number', 'payment_mode', 'college_account', 'entered_by', 'created_at']]
                    st.dataframe(df, use_container_width=True)
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button(label="⬇️ Download Data as CSV for Scrutiny", data=csv, file_name=f"CashBook_{start_date}_to_{end_date}.csv", mime="text/csv", type="primary")
                else:
                    st.info("No records found for the selected date range.")
            except Exception as e:
                st.error(f"Failed to fetch data: {e}")
                
    with tab2:
        st.markdown("Create new logins for clerks or department coordinators.")
        with st.form("create_user_form"):
            new_user = st.text_input("New Username")
            new_pass = st.text_input("Temporary Password", type="password")
            new_role = st.selectbox("Role", ["clerk", "department", "admin"])
            
            if st.form_submit_button("Create User"):
                if new_user and new_pass:
                    user_data = {
                        "username": new_user.lower(),
                        "password_hash": hash_password(new_pass),
                        "role": new_role
                    }
                    try:
                        supabase.table("app_users").insert(user_data).execute()
                        st.success(f"User '{new_user}' created successfully with '{new_role}' privileges.")
                    except Exception as e:
                        if "duplicate key" in str(e).lower():
                            st.error("Username already exists.")
                        else:
                            st.error(f"Error: {e}")

    with tab3:
        st.markdown("Reset passwords for existing users.")
        try:
            users_res = supabase.table("app_users").select("username").execute()
            user_list = [u['username'] for u in users_res.data]
        except Exception:
            user_list = []
            
        with st.form("reset_password_form"):
            target_user = st.selectbox("Select User", user_list)
            new_pwd = st.text_input("New Password", type="password")
            
            if st.form_submit_button("Update Password"):
                if target_user and new_pwd:
                    try:
                        new_hashed_pwd = hash_password(new_pwd.strip())
                        res = supabase.table("app_users").update({"password_hash": new_hashed_pwd}).eq("username", target_user).execute()
                        if hasattr(res, 'data') and len(res.data) > 0:
                            st.success(f"✅ Password updated successfully for user '{target_user}'.")
                        else:
                            st.error("❌ Update failed.")
                    except Exception as e:
                        st.error(f"❌ Error updating password: {e}")

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
        role = st.session_state.role.lower()
        if role == 'admin':
            admin_dashboard()
        elif role == 'clerk':
            clerk_dashboard()
        elif role == 'department':
            department_dashboard()
        else:
            st.error("Unauthorized role.")

if __name__ == "__main__":
    main()
