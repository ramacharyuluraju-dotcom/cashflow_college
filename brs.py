import streamlit as st
import pandas as pd
import hashlib
import calendar
import io
import re
from datetime import date, datetime
from supabase import create_client, Client
from PIL import Image as PILImage

# --- REPORTLAB IMPORTS FOR PDF GENERATION ---
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle, Paragraph, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.utils import ImageReader

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
# PDF GENERATION HELPERS
# ==========================================
def get_student_photo(usn):
    clean_usn = re.sub(r'[^A-Z0-9]', '', usn.upper())
    for ext in ['.jpg', '.jpeg', '.png', '.webp', '.JPG']:
        try:
            res = supabase.storage.from_("StakeHolders_Photos").download(f"{clean_usn}{ext}")
            if res:
                img = PILImage.open(io.BytesIO(res))
                if img.mode != 'RGB': img = img.convert('RGB')
                clean_io = io.BytesIO()
                img.save(clean_io, format='JPEG', quality=95)
                clean_io.seek(0)
                return clean_io
        except: pass
    return None

def get_checkbox():
    """Generates a perfect square box for the table cells"""
    t = Table([[""]], colWidths=[12], rowHeights=[12])
    t.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.8, colors.black),
        ('BACKGROUND', (0,0), (-1,-1), colors.white)
    ]))
    return t

def calculate_summer_fees(courses):
    if not courses: return 0
    base_fee = 400
    total = base_fee
    rule2_count = 0
    for c in courses:
        if "Rule 1" in c['rule']:
            total += 5600
        elif "Rule 2" in c['rule']:
            rule2_count += 1
            total += 2000 if rule2_count == 1 else 1000
        elif "Rule 3" in c['rule']:
            total += 600
    return total

def generate_summer_pdf(student, courses, total_fee):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin = 35
    y = h - margin

    # Fetch Assets
    assets = {}
    for k, f in {"logo": "College_logo.png", "naac": "NAAC_A_Logo.jpg", "watermark": "AMC_watermark.png"}.items():
        try:
            res = supabase.storage.from_("College_Logos").download(f)
            if res: assets[k] = io.BytesIO(res)
        except: pass

    if "watermark" in assets:
        c.saveState()
        c.setFillAlpha(0.08)
        c.drawImage(ImageReader(assets["watermark"]), w/2 - 175, h/2 - 175, width=350, height=350, mask='auto', preserveAspectRatio=True)
        c.restoreState()

    if "logo" in assets:
        c.drawImage(ImageReader(assets["logo"]), margin, y - 35, width=60, height=60, mask='auto', preserveAspectRatio=True)
    if "naac" in assets:
        c.drawImage(ImageReader(assets["naac"]), w - margin - 60, y - 35, width=60, height=60, mask='auto', preserveAspectRatio=True)

    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(w/2, y, "AMC ENGINEERING COLLEGE (AUTONOMOUS)")
    c.setFont("Helvetica", 9)
    c.drawCentredString(w/2, y - 15, "AMC Campus, Bannerghatta Road, Bengaluru, Karnataka - 560083")
    c.drawCentredString(w/2, y - 27, "Autonomous Institution Affiliated to VTU, Belagavi | NAAC A+ Accredited")
    c.setLineWidth(1)
    c.line(margin, y - 45, w - margin, y - 45)
    y -= 65

    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(w/2, y, "Course Registration-Students Admission for Summer Semester 2025-26")
    y -= 20

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Student Details")
    y -= 5

    photo_io = get_student_photo(student['usn'])
    if photo_io:
        p_img = RLImage(photo_io, width=55, height=70)
        p_img.hAlign = 'CENTER'
        p_img.vAlign = 'MIDDLE'
    else:
        p_img = Paragraph("<para align=center>PHOTO</para>", getSampleStyleSheet()['Normal'])

    s_data = [
        ["USN", "Student Name", "Branch Code", "Student Type", "Photo"],
        [student['usn'], student.get('full_name',''), student.get('branch_code',''), "UG", p_img]
    ]
    
    style_cmds = [
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]
    if not photo_io:
        style_cmds.append(('SPAN', (4, 0), (4, 1)))

    t1 = Table(s_data, colWidths=[80, 195, 75, 75, 100], rowHeights=[20, 75])
    t1.setStyle(TableStyle(style_cmds))
    t1.wrapOn(c, w, h)
    _, t1_h = t1.wrap(w, h)
    t1.drawOn(c, margin, y - t1_h)
    y -= (t1_h + 20)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Semester: Summer")
    c.drawRightString(w - margin, y, f"Registration Date: {date.today().strftime('%d-%m-%Y')}")
    y -= 20

    c.drawString(margin, y, "Summer Subjects Registered")
    y -= 5

    c_data = [["Subject Code", "Subject Name", "Category / Rule", "Fee (Rs)", "Apply"]]
    for crs in courses:
        fee_str = "5600" if "Rule 1" in crs['rule'] else ("2000/1000" if "Rule 2" in crs['rule'] else "600")
        c_data.append([
            crs['course_code'], 
            Paragraph(crs.get('course_title','Unknown'), getSampleStyleSheet()['Normal']), 
            Paragraph(crs['rule'], getSampleStyleSheet()['Normal']), 
            fee_str,
            get_checkbox()
        ])
    
    c_data.append(["", "", Paragraph("<b>Base Application Fee:</b>", getSampleStyleSheet()['Normal']), "400", ""])
    c_data.append(["", "", Paragraph("<b>Total Amount Payable:</b>", getSampleStyleSheet()['Normal']), str(total_fee), ""])

    t2 = Table(c_data, colWidths=[70, 185, 150, 70, 50])
    t2.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('ALIGN', (3,0), (3,-1), 'CENTER'),
        ('ALIGN', (4,0), (4,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    t2.wrapOn(c, w, h)
    _, t2_h = t2.wrap(w, h)
    t2.drawOn(c, margin, y - t2_h)
    y -= (t2_h + 20)

    c.setLineWidth(1)
    c.setFont("Helvetica", 9)
    undertakings = [
        "I will follow the AMCEC / VTU autonomy guidelines.",
        "I have paid the prescribed fee.",
        "I will be regular to theory, laboratory, and academic activities in the campus.",
        "I will maintain the discipline in the campus."
    ]
    for u in undertakings:
        c.rect(margin, y - 8, 10, 10) 
        c.drawString(margin + 18, y - 6, u)
        y -= 18
    y -= 10
    
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Declaration:")
    y -= 12
    p_style = getSampleStyleSheet()['Normal']
    p_style.fontSize = 9
    decl = Paragraph("The subjects listed in this Course Registration application are the only subjects I wish to apply for Summer Semester. Further, I understand this application overrides any previous Course Registration application I may have submitted.", p_style)
    decl.wrapOn(c, w - (2*margin), 50)
    _, decl_h = decl.wrap(w - (2*margin), 50)
    decl.drawOn(c, margin, y - decl_h)
    y -= (decl_h + 30)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, f"Date: {date.today().strftime('%d-%m-%Y')}")
    c.drawRightString(w - margin, y, "Signature of Student")
    
    c.save()
    return buf.getvalue()

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
            if st.form_submit_button("Login", use_container_width=True):
                try:
                    res = supabase.table("app_users").select("*").eq("username", username.strip().lower()).eq("password_hash", hash_password(password.strip())).execute()
                    if len(res.data) > 0:
                        st.session_state.logged_in = True
                        st.session_state.username = res.data[0]['username']
                        st.session_state.role = res.data[0]['role']
                        st.rerun()
                    else: st.error("❌ Invalid Credentials")
                except Exception as e: st.error(f"Error: {e}")

# ==========================================
# VIEW 1: CLERK DATA ENTRY DASHBOARD
# ==========================================
def clerk_dashboard():
    st.title("📝 Desk Entry: Payment Synchronization")
    st.markdown(f"Logged in as: **{st.session_state.username}** (Clerk)")
    
    tab_manual, tab_online = st.tabs(["Manual Receipt Entry", "Online Pending Approvals"])
    
    with tab_manual:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("### Student Details")
            usn = st.text_input("Student USN (e.g., 1AM24CS099)")
            student_name = st.text_input("Student Name")
            branch = st.selectbox("Branch", ["AE", "AIML", "CSE", "CSE-AIML", "CSE-DS", "CV", "ME", "ECE", "EEE", "ISE", "M Tech", "MBA", "MCA"])
        with col2:
            st.markdown("### Payment Details")
            payment_date = st.date_input("Date of Payment", date.today())
            amount = st.number_input("Amount Paid (₹)", min_value=1.0, step=100.0)
            payment_type = st.selectbox("Fee Type", ["Exam Fee", "Tuition Fee", "Fine", "Revaluation fee", "Convocation fees", "Arrears fees", "Summer Semester Fee", "Other"])
            other_description = st.text_input("Specify Other Fee", max_chars=12) if payment_type == "Other" else ""
        with col3:
            st.markdown("### Transaction Details")
            payment_mode = st.selectbox("Payment Mode", ["UPI (QR / App)", "Bank Transfer (NEFT / RTGS)"])
            utr = st.text_input("Transaction ID / UTR No.")
            college_account = st.text_input("Credited To A/C (Bank details)")
            
        if st.button("💾 Save & Sync Receipt", type="primary", use_container_width=True):
            final_payment_type = f"Other - {other_description.strip()}" if payment_type == "Other" else payment_type
            if not usn or not student_name or not utr or not college_account: st.error("⚠️ Missing mandatory fields!")
            elif payment_mode == "UPI (QR / App)" and (not utr.isdigit() or len(utr) != 12): st.error("❌ UPI UTR must be 12 digits!")
            elif payment_mode == "Bank Transfer (NEFT / RTGS)" and (not utr.isalnum() or len(utr) != 22): st.error("❌ NEFT UTR must be 22 chars!")
            else:
                try:
                    supabase.table("cash_receipts").insert({"payment_date": str(payment_date), "amount": amount, "utr_number": utr.strip().upper(), "payment_type": final_payment_type, "college_account": college_account.strip(), "payment_mode": payment_mode, "usn": usn.strip().upper(), "student_name": student_name.strip().title(), "branch": branch, "entered_by": st.session_state.username}).execute()
                    st.success(f"✅ Receipt saved!")
                except Exception as e: st.error("❌ Duplicate UTR or Database Error!")

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

    with tab_online:
        st.subheader("Approve Pending Online Registrations")
        st.info("These applications were submitted by the Departments. Mark them as 'PAID' once funds are received so the COE can generate Hall Tickets.")
        
        try:
            pending_res = supabase.table("course_registration_online").select("usn, cycle_id, registration_type, fee_amount").eq("payment_status", "PENDING").execute()
            if not pending_res.data:
                st.success("No pending online applications.")
            else:
                # Group by USN and Cycle ID so the clerk approves the whole application at once
                df_pending = pd.DataFrame(pending_res.data)
                grouped = df_pending.groupby(['usn', 'cycle_id', 'registration_type']).agg(
                    total_courses=('usn', 'count'),
                    total_fee=('fee_amount', 'max') # Fee is uniform per payload
                ).reset_index()
                
                st.dataframe(grouped, use_container_width=True)
                
                with st.form("approve_online_form"):
                    target_usn = st.text_input("Enter USN to Approve")
                    target_cycle = st.number_input("Cycle ID (from table)", min_value=1)
                    if st.form_submit_button("✅ Mark Application as PAID"):
                        supabase.table("course_registration_online").update({"payment_status": "PAID"}).eq("usn", target_usn.strip().upper()).eq("cycle_id", target_cycle).execute()
                        st.success(f"Application for {target_usn} marked as PAID. It is now ready for COE processing.")
                        st.rerun()
        except Exception as e:
            st.error(f"Error fetching pending applications: {e}")

# ==========================================
# VIEW 2: DEPARTMENT PORTAL
# ==========================================
def department_dashboard():
    st.title("🏛️ Department Online Course Registration Portal")
    st.markdown(f"Coordinator: **{st.session_state.username}**")
    
    # 🟢 FIX: Fetch program_type along with cycle details
    try:
        cycles_res = supabase.table("exam_cycles").select("cycle_id, cycle_name, exam_type, program_type").eq("is_active", True).eq("is_brs_active", True).execute()
        active_cycles = cycles_res.data if cycles_res.data else []
    except Exception as e:
        active_cycles = []
        st.error(f"Error fetching cycles: {e}")

    if not active_cycles:
        st.warning("⚠️ No active exam cycles are currently open for online registration. Please contact the COE.")
        return

    tab_reg, tab_summer = st.tabs(["📝 Regular / Backlog Registration", "☀️ Summer Semester Registration"])
    
    # --- REGULAR REGISTRATION ---
    with tab_reg:
        regular_cycles = [c for c in active_cycles if "Summer" not in c.get('exam_type', '') and "Make-up" not in c.get('exam_type', '')]
        if not regular_cycles:
            st.info("No Regular exam cycles are currently open.")
        else:
            reg_cycle_options = {c['cycle_name']: c for c in regular_cycles}
            selected_reg_cycle_name = st.selectbox("Select Target Exam Cycle (Regular):", options=list(reg_cycle_options.keys()))
            target_reg_cycle = reg_cycle_options[selected_reg_cycle_name]
            target_reg_cycle_id = target_reg_cycle['cycle_id']
            
            target_usn = st.text_input("Enter Student USN for Registration").strip().upper()
            if target_usn:
                stu_res = supabase.table("master_students").select("*").eq("usn", target_usn).execute()
                if not stu_res.data: 
                    st.error("Student not found.")
                else:
                    stu = stu_res.data[0]
                    # 🟢 FIX 1: Block DISCONTINUED students
                    if str(stu.get('status', '')).strip().upper() == 'DISCONTINUED':
                        st.error(f"❌ **Registration Blocked:** Student '{target_usn}' is marked as DISCONTINUED in the system.")
                    else:
                        branch_code = stu.get('branch_code', '')
                        current_sem = int(stu.get('current_sem', 1))
                        
                        # 🟢 FIX 2: Fetch Program Type
                        br_res = supabase.table("master_branches").select("program_type").eq("branch_code", branch_code).execute()
                        prog_type = br_res.data[0]['program_type'] if br_res.data else "UG"
                        
                        # 🟢 FIX 3: Validate Cycle Program Type Match
                        if target_reg_cycle.get('program_type', 'BOTH') not in ['BOTH', prog_type]:
                            st.error(f"❌ **Program Mismatch:** Student is {prog_type}, but the selected cycle is restricted to {target_reg_cycle.get('program_type')} students.")
                        else:
                            st.success(f"**{stu['full_name']}** | Branch: **{branch_code}** ({prog_type}) | Sem: **{current_sem}**")
                            
                            courses_res = supabase.table("master_courses").select("*").execute()
                            all_courses = courses_res.data if courses_res.data else []
                            
                            core_courses = [c for c in all_courses if c.get('semester_id') == current_sem and c.get('branch_code') in [branch_code, 'COMMON'] and c.get('course_type', 'CORE') == 'CORE']
                            pe_courses = [c for c in all_courses if c.get('semester_id') == current_sem and c.get('branch_code') == branch_code and c.get('course_type') == 'PE']
                            oe_courses = [c for c in all_courses if c.get('semester_id') == current_sem and c.get('branch_code') != branch_code and c.get('course_type') == 'OE']
                            
                            st.markdown("### 1. Mandatory Core Subjects")
                            selected_codes, total_credits = [], 0.0
                            for core in core_courses:
                                st.checkbox(f"{core['course_code']} - {core['title']}", value=True, disabled=True, key=f"core_{core['course_code']}")
                                selected_codes.append(core['course_code'])
                                total_credits += float(core.get('credits', 4))
                                
                            st.markdown("### 2. Electives")
                            col_pe, col_oe = st.columns(2)
                            if pe_courses:
                                pe_options = {f"{p['course_code']} - {p['title']}": p['course_code'] for p in pe_courses}
                                chosen_pe = col_pe.selectbox("Professional Elective", ["-- Select --"] + list(pe_options.keys()))
                                if chosen_pe != "-- Select --":
                                    selected_codes.append(pe_options[chosen_pe])
                                    total_credits += float(next(p for p in pe_courses if p['course_code'] == pe_options[chosen_pe]).get('credits', 3))
                            if oe_courses:
                                oe_options = {f"{o['course_code']} [{o['branch_code']}] - {o['title']}": o['course_code'] for o in oe_courses}
                                chosen_oe = col_oe.selectbox("Open Elective", ["-- Select --"] + list(oe_options.keys()))
                                if chosen_oe != "-- Select --":
                                    selected_codes.append(oe_options[chosen_oe])
                                    total_credits += float(next(o for o in oe_courses if o['course_code'] == oe_options[chosen_oe]).get('credits', 3))
                                    
                            st.info(f"📊 **Total Credits:** {total_credits}")
                            if st.button("💾 Submit Registration to Finance", type="primary"):
                                payload = [{
                                    "cycle_id": target_reg_cycle_id, 
                                    "usn": target_usn, 
                                    "course_code": cc, 
                                    "semester": current_sem, 
                                    "academic_year": "2025-26", 
                                    "registration_type": "REGULAR",
                                    "rule_category": "", 
                                    "fee_amount": 0, 
                                    "payment_status": "PENDING"
                                } for cc in selected_codes]
                                
                                supabase.table("course_registration_online").delete().eq("usn", target_usn).eq("cycle_id", target_reg_cycle_id).execute()
                                supabase.table("course_registration_online").insert(payload).execute()
                                st.success("✅ Application submitted to Finance Desk (Pending Payment)!")

    # --- SUMMER REGISTRATION ---
    with tab_summer:
        summer_cycles = [c for c in active_cycles if "Summer" in c.get('exam_type', '')]
        if not summer_cycles:
            st.info("No Summer exam cycles are currently open for BRS registration.")
        else:
            sum_cycle_options = {c['cycle_name']: c for c in summer_cycles}
            selected_sum_cycle_name = st.selectbox("Select Target Exam Cycle (Summer):", options=list(sum_cycle_options.keys()))
            target_sum_cycle = sum_cycle_options[selected_sum_cycle_name]
            target_sum_cycle_id = target_sum_cycle['cycle_id']
            
            st.subheader("☀️ Summer Semester Application")
            summer_usn = st.text_input("Enter USN for Summer Semester Processing").strip().upper()
            if summer_usn:
                stu_res = supabase.table("master_students").select("*").eq("usn", summer_usn).execute()
                if not stu_res.data:
                    st.error("Student not found.")
                else:
                    student = stu_res.data[0]
                    
                    # 🟢 FIX 1: Block DISCONTINUED students
                    if str(student.get('status', '')).strip().upper() == 'DISCONTINUED':
                        st.error(f"❌ **Registration Blocked:** Student '{summer_usn}' is marked as DISCONTINUED in the system.")
                    else:
                        branch_code = student.get('branch_code', '')
                        
                        # 🟢 FIX 2: Fetch Program Type
                        br_res = supabase.table("master_branches").select("program_type").eq("branch_code", branch_code).execute()
                        prog_type = br_res.data[0]['program_type'] if br_res.data else "UG"
                        
                        # 🟢 FIX 3: Validate Cycle Program Type Match
                        if target_sum_cycle.get('program_type', 'BOTH') not in ['BOTH', prog_type]:
                            st.error(f"❌ **Program Mismatch:** Student is {prog_type}, but the selected cycle is restricted to {target_sum_cycle.get('program_type')} students.")
                        else:
                            st.success(f"**{student['full_name']}** | Branch: **{branch_code}** ({prog_type})")
                            
                            res = supabase.table("student_results").select("course_code, grade, cie_marks, is_pass, cycle_id").eq("usn", summer_usn).execute()
                            
                            if not res.data:
                                st.warning("No historical exam records found for this USN.")
                            else:
                                results = res.data
                                results.sort(key=lambda x: int(x.get('cycle_id', 0)), reverse=True)
                                
                                latest_results = {}
                                for r in results:
                                    if r['course_code'] not in latest_results:
                                        latest_results[r['course_code']] = r

                                crs_res = supabase.table("master_courses").select("course_code, title, semester_id, credits").in_("course_code", list(latest_results.keys())).execute()
                                course_info = {c['course_code']: {'title': c['title'], 'sem': c['semester_id'], 'credits': float(c.get('credits', 4))} for c in crs_res.data} if crs_res.data else {}

                                # 🟢 FIX 4: Dynamic CIE Threshold based on Program Type
                                cie_threshold = 20 if prog_type == "UG" else 25 
                                
                                eligible_summer_courses = []
                                
                                for cc, r in latest_results.items():
                                    grade = str(r.get('grade', '')).upper()
                                    cie = float(r.get('cie_marks', 0))
                                    is_pass = r.get('is_pass', False)
                                    
                                    if not is_pass and grade not in ['PND', 'FROZEN', '']:
                                        rule = None
                                        
                                        if cie < cie_threshold:
                                            rule = "Rule 1: Mandatory Classes (CIE Fail)"
                                        elif grade == 'AB' and cie >= cie_threshold:
                                            rule = "Rule 2: Exam Only (Absent)"
                                        elif grade in ['F', 'MP'] and cie >= cie_threshold:
                                            rule = "Rule 3: Exam Only (SEE Fail / MP)"
                                            
                                        if rule:
                                            c_info = course_info.get(cc, {'title': 'Unknown', 'sem': 0, 'credits': 4.0})
                                            eligible_summer_courses.append({
                                                "course_code": cc,
                                                "course_title": c_info['title'],
                                                "semester": c_info['sem'],
                                                "credits": c_info['credits'],
                                                "grade": grade,
                                                "cie": cie,
                                                "rule": rule
                                            })
                                            
                                if not eligible_summer_courses:
                                    st.success("🎉 This student has no failed courses requiring summer registration!")
                                else:
                                    st.markdown("### Select Eligible Summer Courses")
                                    
                                    selected_summer_courses = []
                                    for crs in eligible_summer_courses:
                                        apply = st.checkbox(f"[{crs['course_code']}] {crs['course_title']} | Grade: {crs['grade']} | Rule: {crs['rule']}", value=True)
                                        if apply:
                                            selected_summer_courses.append(crs)
                                    
                                    if selected_summer_courses:
                                        total_fee = calculate_summer_fees(selected_summer_courses)
                                        st.info(f"💰 **Calculated Total Fee (Including Base 400):** ₹{total_fee}")
                                        
                                        rule_1_credits = sum([c['credits'] for c in selected_summer_courses if "Rule 1" in c['rule']])
                                        
                                        if rule_1_credits > 14:
                                            st.error(f"❌ **Credit Limit Exceeded!** The student has selected {rule_1_credits} credits under Rule 1. The maximum allowed is **14 Credits**.")
                                        else:
                                            if rule_1_credits > 0:
                                                st.success(f"✅ Rule 1 Credits Valid: {rule_1_credits} / 14")
                                            
                                            if st.button("💾 Submit to Finance & Generate PDF", type="primary"):
                                                payload = [{
                                                    "cycle_id": target_sum_cycle_id,
                                                    "usn": summer_usn,
                                                    "course_code": c['course_code'],
                                                    "semester": c['semester'],
                                                    "academic_year": "2025-26",
                                                    "registration_type": "SUMMER",
                                                    "rule_category": c['rule'],
                                                    "fee_amount": total_fee,
                                                    "payment_status": "PENDING"
                                                } for c in selected_summer_courses]
                                                
                                                try:
                                                    supabase.table("course_registration_online").delete().eq("usn", summer_usn).eq("cycle_id", target_sum_cycle_id).execute()
                                                    supabase.table("course_registration_online").insert(payload).execute()
                                                    st.success(f"✅ Application pushed to Finance Desk (Pending Payment)!")
                                                    
                                                    pdf_bytes = generate_summer_pdf(student, selected_summer_courses, total_fee)
                                                    st.download_button(
                                                        label="🖨️ Download Official Application PDF",
                                                        data=pdf_bytes,
                                                        file_name=f"Summer_Application_{summer_usn}.pdf",
                                                        mime="application/pdf",
                                                        type="primary"
                                                    )
                                                except Exception as e:
                                                    st.error(f"Database Error: {e}")
# ==========================================
# VIEW 3: ADMIN DASHBOARD (SCRUTINY & EXPORT)
# ==========================================
def admin_dashboard():
    st.title("📊 Admin Consolidation Panel")
    tab1, tab2 = st.tabs(["📥 Download Cash Book", "👥 Manage Users"])
    with tab1:
        c1, c2 = st.columns(2)
        start_date = c1.date_input("From Date", date.today().replace(day=1))
        end_date = c2.date_input("To Date", date.today())
        if st.button("Fetch Data"):
            res = supabase.table("cash_receipts").select("*").gte("payment_date", str(start_date)).lte("payment_date", str(end_date)).execute()
            if res.data:
                df = pd.DataFrame(res.data)
                st.dataframe(df, use_container_width=True)
                st.download_button("⬇️ Download CSV", df.to_csv(index=False).encode('utf-8'), f"CashBook_{start_date}_to_{end_date}.csv", "text/csv")
            else: st.info("No records found.")
    with tab2:
        with st.form("create_user_form"):
            new_user = st.text_input("New Username")
            new_pass = st.text_input("Temporary Password", type="password")
            new_role = st.selectbox("Role", ["clerk", "department", "admin"])
            if st.form_submit_button("Create User") and new_user and new_pass:
                try:
                    supabase.table("app_users").insert({"username": new_user.lower(), "password_hash": hash_password(new_pass), "role": new_role}).execute()
                    st.success(f"User created with '{new_role}' role.")
                except: st.error("Error creating user.")

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
        if role == 'admin': admin_dashboard()
        elif role == 'clerk': clerk_dashboard()
        elif role == 'department': department_dashboard()

if __name__ == "__main__":
    main()
