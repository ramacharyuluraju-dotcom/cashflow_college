import streamlit as st
import pandas as pd
import hashlib
import calendar
import io
import os
import re
import qrcode
import concurrent.futures
from datetime import date, datetime
from collections import defaultdict
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
# HELPER FUNCTIONS & CONCURRENCY
# ==========================================
def format_branch_name(branch_code):
    branch_map = {"CS": "CSE", "CI": "CSE-AIML", "CD": "CSE-DS", "AI": "AIML", "EC": "ECE", "EE": "EEE", "CV": "Civil", "ME": "ME", "AE": "AE"}
    return branch_map.get(str(branch_code).strip().upper(), str(branch_code).strip().upper())

def fetch_complete_bucket_map(bucket_name):
    file_map = {}
    limit = 1000; offset = 0
    while True:
        try:
            files = supabase.storage.from_(bucket_name).list("", options={"limit": limit, "offset": offset})
            if not files: break
            for f in files:
                fname = f.get('name', '')
                if not fname or fname == '.emptyFolderPlaceholder': continue
                basename = os.path.basename(fname)
                key = re.sub(r'[^A-Z0-9]', '', os.path.splitext(basename)[0].upper())
                file_map[key] = fname
            if len(files) < limit: break
            offset += limit
        except: break
    return file_map

def download_photo_worker(args):
    usn, file_map = args
    clean_usn = re.sub(r'[^A-Z0-9]', '', usn.upper())
    
    if clean_usn in file_map:
        try:
            res = supabase.storage.from_("StakeHolders_Photos").download(file_map[clean_usn])
            if res:
                img = PILImage.open(io.BytesIO(res))
                if img.mode != 'RGB': img = img.convert('RGB')
                clean_io = io.BytesIO()
                img.save(clean_io, format='JPEG', quality=95)
                clean_io.seek(0)
                return usn, clean_io
        except: pass
    return usn, None

# 🟢 FALLBACK TO ADMISSION NUMBER IF USN MIGRATED
def get_student_photo(usn, admission_number=None):
    ids_to_check = [str(usn).upper()]
    if admission_number and pd.notna(admission_number):
        ids_to_check.append(str(admission_number).upper())
        
    for current_id in ids_to_check:
        if not current_id or current_id == 'NAN': continue
        clean_id = re.sub(r'[^A-Z0-9]', '', current_id)
        
        for ext in ['.jpg', '.jpeg', '.png', '.webp', '.JPG']:
            try:
                res = supabase.storage.from_("StakeHolders_Photos").download(f"{clean_id}{ext}")
                if res:
                    img = PILImage.open(io.BytesIO(res))
                    if img.mode != 'RGB': img = img.convert('RGB')
                    clean_io = io.BytesIO()
                    img.save(clean_io, format='JPEG', quality=95)
                    clean_io.seek(0)
                    return clean_io
            except: pass
    return None

def sort_courses_by_sequence(course_list):
    def extract_seq(course):
        match = re.search(r'\d{3}', str(course.get('course_code', '')))
        return int(match.group()) if match else 999
    return sorted(course_list, key=extract_seq)

def calculate_summer_fees(courses):
    if not courses: return 0
    base_fee = 400
    total = base_fee
    rule2_count = 0
    for c in courses:
        if "Rule 1" in c.get('rule', ''): total += 5600
        elif "Rule 2" in c.get('rule', ''):
            rule2_count += 1
            total += 2000 if rule2_count == 1 else 1000
        elif "Rule 3" in c.get('rule', ''): total += 600
    return total

def calculate_ode_fees(courses):
    """Calculates ODE fees: Rs 6000 for Attempt 1, Rs 3000 for Attempt 2+, + Rs 100 App Fee + Rs 600 Grade Card Fee."""
    if not courses:
        return 0
    app_fee = 100
    grade_card_fee = 600
    subject_total = 0
    for c in courses:
        rule_str = str(c.get('rule', '')).upper()
        if "ATTEMPT 2" in rule_str or "SUBSEQUENT" in rule_str or c.get('attempt_num', 1) > 1:
            subject_total += 3000
        else:
            subject_total += 6000
    return subject_total + app_fee + grade_card_fee

def branch_match(course_branches_str, student_branch):
    if not course_branches_str: return False
    allowed = [b.strip().upper() for b in str(course_branches_str).split(',')]
    return (student_branch in allowed) or ('COMMON' in allowed) or ('ALL' in allowed)

def match_specialization(course_spec, student_spec):
    c_spec = str(course_spec).strip().upper()
    return c_spec in ['GENERAL', 'NONE', 'NAN', '', 'NULL'] or c_spec == student_spec

def detect_cycle_term(cycle_dict, fallback_term="ODD"):
    """Detects ODD or EVEN parity from the exam cycle metadata or name."""
    sem_type = str(cycle_dict.get('semester_type', '') or '').strip().upper()
    if sem_type in ['ODD', 'EVEN']:
        return sem_type
    c_name = str(cycle_dict.get('cycle_name', '') or '').upper()
    if re.search(r'\bEVEN\b', c_name):
        return 'EVEN'
    if re.search(r'\bODD\b', c_name):
        return 'ODD'
    return fallback_term.upper()

def generate_summer_fee_report(cycle_id, branch_code=None):
    regs_res = supabase.table("course_registration_online").select("*").eq("cycle_id", cycle_id).execute()
    if not regs_res.data: return None
        
    usns = list(set([r['usn'] for r in regs_res.data]))
    student_map = {}
    
    for i in range(0, len(usns), 100):
        chunk = usns[i:i+100]
        st_res = supabase.table("master_students").select("usn, full_name, branch_code").in_("usn", chunk).execute()
        if st_res.data:
            for s in st_res.data: student_map[s['usn']] = s
                
    report_data = []
    grouped = defaultdict(list)
    for r in regs_res.data: grouped[r['usn']].append(r)
        
    for usn, courses in grouped.items():
        stu = student_map.get(usn, {})
        stu_branch = stu.get('branch_code', 'Unknown')
        
        if branch_code and stu_branch != branch_code: continue 
            
        course_codes = ", ".join([c['course_code'] for c in courses])
        rules = ", ".join([c.get('rule_category', '') for c in courses])
        total_fee = courses[0].get('fee_amount', 0)
        utr = courses[0].get('utr_number', '')
        
        report_data.append({
            "USN": usn, "Name": stu.get('full_name', 'Unknown'), "Branch": stu_branch,
            "Courses Registered": course_codes, "Rules / Attempts Applied": rules,
            "Total Fee Payable (Rs)": total_fee, "UTR / Transaction ID": utr
        })
        
    return pd.DataFrame(report_data)

# ==========================================
# REGULAR SEMESTER PDF GENERATORS
# ==========================================
def generate_regular_pdf(student, courses, academic_year="2026-27", term="ODD", current_sem=1):
    PHOTO_BOOTH_URL = "https://amceducationphotobhoot.streamlit.app/"
    
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin = 35
    y = h - margin

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

    if "logo" in assets: c.drawImage(ImageReader(assets["logo"]), margin, y - 35, width=60, height=60, mask='auto', preserveAspectRatio=True)
    if "naac" in assets: c.drawImage(ImageReader(assets["naac"]), w - margin - 60, y - 35, width=60, height=60, mask='auto', preserveAspectRatio=True)

    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(w/2, y, "AMC ENGINEERING COLLEGE (AUTONOMOUS)")
    c.setFont("Helvetica", 9)
    c.drawCentredString(w/2, y - 15, "AMC Campus, Bannerghatta Road, Bengaluru, Karnataka - 560083")
    c.drawCentredString(w/2, y - 27, "Autonomous Institution Affiliated to VTU, Belagavi | NAAC A+ Accredited")
    c.setLineWidth(1)
    c.line(margin, y - 45, w - margin, y - 45)
    y -= 65

    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(w/2, y, f"Course Registration - {term.upper()} Semester {academic_year}")
    y -= 20

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Student Details")
    y -= 5

    display_id = student.get('admission_number') if pd.isna(student.get('usn')) or student.get('usn') == '' else student.get('usn')
    
    # 🟢 DYNAMIC BRANCH + SPECIALIZATION FORMATTING
    formatted_branch = format_branch_name(student.get('branch_code', ''))
    stu_spec = str(student.get('specialization', 'GENERAL')).strip().upper()
    if stu_spec not in ['GENERAL', 'NONE', 'NAN', '']:
        formatted_branch += f" ({stu_spec})"
    
    if str(display_id).startswith("TMP-"):
        table_display_id = str(display_id).split("-")[-1]
    else:
        table_display_id = display_id

    p_style = getSampleStyleSheet()['Normal']
    p_style.alignment = 1 
    p_style.fontSize = 8
    
    photo_io = get_student_photo(display_id, student.get('admission_number'))
    has_photo = bool(photo_io)
    
    if has_photo:
        photo_io.seek(0)
        p_img = RLImage(photo_io, width=60, height=80)
        p_img.hAlign = 'CENTER'
        p_img.vAlign = 'MIDDLE'
        digital_col = p_img
        
        s_data = [
            ["USN / Admin No.", "Student Name", "Branch", "Type", "Sem", "Student Photo"],
            [table_display_id, student.get('full_name',''), formatted_branch, "UG", str(current_sem), digital_col]
        ]
        t1_colWidths = [85, 155, 55, 40, 40, 150]
        
    else:
        qr = qrcode.make(PHOTO_BOOTH_URL)
        qr_io = io.BytesIO()
        qr.save(qr_io, format="PNG")
        qr_io.seek(0)
        p_img = RLImage(qr_io, width=50, height=50)
        p_img.hAlign = 'CENTER'
        p_img.vAlign = 'MIDDLE'
        
        pin = student.get('photo_pin', 'XXXX')
        login_text = f"Scan to Upload<br/>User Name: <b>{display_id}</b><br/>PIN: <b>{pin}</b>"
        digital_col = [p_img, Paragraph(login_text, p_style)]
        physical_col = Paragraph("<br/><br/><br/>Affix Physical<br/>Photo Here", p_style)

        s_data = [
            ["USN / Admin No.", "Student Name", "Branch", "Type", "Sem", "Digital Photo", "Physical Photo"],
            [table_display_id, student.get('full_name',''), formatted_branch, "UG", str(current_sem), digital_col, physical_col]
        ]
        t1_colWidths = [80, 135, 50, 40, 30, 95, 95]
    
    style_cmds = [
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]

    t1 = Table(s_data, colWidths=t1_colWidths, rowHeights=[20, 115])
    t1.setStyle(TableStyle(style_cmds))
    t1.wrapOn(c, w, h)
    _, t1_h = t1.wrap(w, h)
    t1.drawOn(c, margin, y - t1_h)
    y -= (t1_h + 20)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Courses offered")
    y -= 5

    p_style_code = getSampleStyleSheet()['Normal']
    p_style_code.fontSize = 9
    p_style_code.leading = 11

    c_data = [["Course code", "Course title", "Credits", "Select"]]
    total_credits = 0.0
    
    courses = sort_courses_by_sequence(courses)
    
    for crs in courses:
        cred = float(crs.get('credits', 0))
        total_credits += cred
        
        raw_code = str(crs.get('course_code', ''))
        safe_code = raw_code.replace('/', '/ ')
        
        c_data.append([
            Paragraph(safe_code, p_style_code), 
            Paragraph(crs.get('course_title','Unknown'), getSampleStyleSheet()['Normal']), 
            str(int(cred) if cred.is_integer() else cred), "Yes" 
        ])
        
    c_data.append(["", Paragraph("<b>Total Credits</b>", getSampleStyleSheet()['Normal']), str(int(total_credits) if total_credits.is_integer() else total_credits), ""])

    t2 = Table(c_data, colWidths=[110, 305, 55, 55])
    t2.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('ALIGN', (2,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    t2.wrapOn(c, w, h)
    _, t2_h = t2.wrap(w, h)
    t2.drawOn(c, margin, y - t2_h)
    y -= (t2_h + 20)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "STUDENT UNDERTAKING:")
    y -= 15
    
    c.setLineWidth(1)
    c.setFont("Helvetica", 9)
    undertakings = [
        "I will strictly follow the AMCEC/VTU autonomy guidelines.",
        "I have paid the full tuition fees and examination fees for the current semester.",
        "I am aware that I must maintain a minimum of 85% attendance to appear for SEE.",
        "I have verified that my selected credits align with the academic regulations."
    ]
    for u in undertakings:
        c.rect(margin, y - 8, 10, 10) 
        c.drawString(margin + 18, y - 6, u)
        y -= 18
    y -= 10
    
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "DECLARATION:")
    y -= 12
    p_style = getSampleStyleSheet()['Normal']
    p_style.fontSize = 9
    decl = Paragraph("I hereby declare that the information provided is true to the best of my knowledge. I have carefully selected the courses listed above and I request to be registered for the same in the current semester.", p_style)
    decl.wrapOn(c, w - (2*margin), 50)
    _, decl_h = decl.wrap(w - (2*margin), 50)
    decl.drawOn(c, margin, y - decl_h)
    
    y -= (decl_h + 60) 

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, f"Date: {date.today().strftime('%d-%m-%Y')}")
    c.drawRightString(w - margin, y, "_________________________")
    c.drawRightString(w - margin, y - 15, "Signature of the Student")
    
    c.save()
    return buf.getvalue()


# 🟢 BULK PDF GENERATOR
def generate_regular_pdf_bulk(student_course_list, academic_year="2026-27", term="ODD", current_sem=1, progress_bar=None, status_text=None):
    PHOTO_BOOTH_URL = "https://amceducationphotobhoot.streamlit.app/"
    
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin = 35

    if status_text: status_text.info("🖨️ Initializing PDF engine & downloading assets...")

    raw_assets = {}
    for k, f in {"logo": "College_logo.png", "naac": "NAAC_A_Logo.jpg", "watermark": "AMC_watermark.png"}.items():
        try:
            res = supabase.storage.from_("College_Logos").download(f)
            if res: raw_assets[k] = res
        except: pass

    qr = qrcode.make(PHOTO_BOOTH_URL)
    cached_qr_io = io.BytesIO()
    qr.save(cached_qr_io, format="PNG")
    cached_qr_bytes = cached_qr_io.getvalue()

    if status_text: status_text.info("🔍 Scanning Student Photo database...")
    photo_file_map = fetch_complete_bucket_map("StakeHolders_Photos")
    
    BATCH_SIZE = 50 
    total = len(student_course_list)

    for i in range(0, total, BATCH_SIZE):
        batch_items = student_course_list[i : i + BATCH_SIZE]
        
        batch_usns = []
        for item in batch_items:
            u = item['student'].get('usn')
            if pd.isna(u) or u == '': u = item['student'].get('admission_number')
            batch_usns.append(u)
        
        if status_text:
            status_text.info(f"🖨️ Rendering PDFs: Processing Batch {i//BATCH_SIZE + 1} ({min(i+BATCH_SIZE, total)}/{total} students)...")

        batch_photos = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(download_photo_worker, (u, photo_file_map)): u for u in batch_usns}
            for future in concurrent.futures.as_completed(futures):
                u, p_stream = future.result()
                if p_stream: batch_photos[u] = p_stream

        for item in batch_items:
            student = item['student']
            courses = item['courses']
            y = h - margin

            if "watermark" in raw_assets:
                c.saveState()
                c.setFillAlpha(0.08)
                c.drawImage(ImageReader(io.BytesIO(raw_assets["watermark"])), w/2 - 175, h/2 - 175, width=350, height=350, mask='auto', preserveAspectRatio=True)
                c.restoreState()

            if "logo" in raw_assets: c.drawImage(ImageReader(io.BytesIO(raw_assets["logo"])), margin, y - 35, width=60, height=60, mask='auto', preserveAspectRatio=True)
            if "naac" in raw_assets: c.drawImage(ImageReader(io.BytesIO(raw_assets["naac"])), w - margin - 60, y - 35, width=60, height=60, mask='auto', preserveAspectRatio=True)

            c.setFont("Helvetica-Bold", 15)
            c.drawCentredString(w/2, y, "AMC ENGINEERING COLLEGE (AUTONOMOUS)")
            c.setFont("Helvetica", 9)
            c.drawCentredString(w/2, y - 15, "AMC Campus, Bannerghatta Road, Bengaluru, Karnataka - 560083")
            c.drawCentredString(w/2, y - 27, "Autonomous Institution Affiliated to VTU, Belagavi | NAAC A+ Accredited")
            c.setLineWidth(1)
            c.line(margin, y - 45, w - margin, y - 45)
            y -= 65

            c.setFont("Helvetica-Bold", 11)
            c.drawCentredString(w/2, y, f"Course Registration - {term.upper()} Semester {academic_year}")
            y -= 20

            c.setFont("Helvetica-Bold", 10)
            c.drawString(margin, y, "Student Details")
            y -= 5

            display_id = student.get('admission_number') if pd.isna(student.get('usn')) or student.get('usn') == '' else student.get('usn')
            
            # 🟢 DYNAMIC BRANCH + SPECIALIZATION FORMATTING
            formatted_branch = format_branch_name(student.get('branch_code', ''))
            stu_spec = str(student.get('specialization', 'GENERAL')).strip().upper()
            if stu_spec not in ['GENERAL', 'NONE', 'NAN', '']:
                formatted_branch += f" ({stu_spec})"

            if str(display_id).startswith("TMP-"):
                table_display_id = str(display_id).split("-")[-1]
            else:
                table_display_id = display_id

            p_style = getSampleStyleSheet()['Normal']
            p_style.alignment = 1 
            p_style.fontSize = 8
            
            photo_io = batch_photos.get(display_id)
            if not photo_io:
                photo_io = get_student_photo(display_id, student.get('admission_number'))
                
            has_photo = bool(photo_io)
            
            if has_photo:
                photo_io.seek(0)
                p_img = RLImage(photo_io, width=60, height=80)
                p_img.hAlign = 'CENTER'
                p_img.vAlign = 'MIDDLE'
                digital_col = p_img
                
                s_data = [
                    ["USN / Admin No.", "Student Name", "Branch", "Type", "Sem", "Student Photo"],
                    [table_display_id, student.get('full_name',''), formatted_branch, "UG", str(current_sem), digital_col]
                ]
                t1_colWidths = [85, 155, 55, 40, 40, 150]
                
            else:
                p_img = RLImage(io.BytesIO(cached_qr_bytes), width=50, height=50)
                p_img.hAlign = 'CENTER'
                p_img.vAlign = 'MIDDLE'
                
                pin = student.get('photo_pin', 'XXXX')
                login_text = f"Scan to Upload<br/>User Name: <b>{display_id}</b><br/>PIN: <b>{pin}</b>"
                digital_col = [p_img, Paragraph(login_text, p_style)]
                physical_col = Paragraph("<br/><br/><br/>Affix Physical<br/>Photo Here", p_style)

                s_data = [
                    ["USN / Admin No.", "Student Name", "Branch", "Type", "Sem", "Digital Photo", "Physical Photo"],
                    [table_display_id, student.get('full_name',''), formatted_branch, "UG", str(current_sem), digital_col, physical_col]
                ]
                t1_colWidths = [80, 135, 50, 40, 30, 95, 95]

            style_cmds = [
                ('GRID', (0,0), (-1,-1), 0.5, colors.black),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
            ]

            t1 = Table(s_data, colWidths=t1_colWidths, rowHeights=[20, 115])
            t1.setStyle(TableStyle(style_cmds))
            t1.wrapOn(c, w, h)
            _, t1_h = t1.wrap(w, h)
            t1.drawOn(c, margin, y - t1_h)
            y -= (t1_h + 20)

            c.setFont("Helvetica-Bold", 10)
            c.drawString(margin, y, "Courses offered")
            y -= 5

            p_style_code = getSampleStyleSheet()['Normal']
            p_style_code.fontSize = 9
            p_style_code.leading = 11

            c_data = [["Course code", "Course title", "Credits", "Select"]]
            total_credits = 0.0
            
            courses = sort_courses_by_sequence(courses)
            
            for crs in courses:
                cred = float(crs.get('credits', 0))
                total_credits += cred
                
                raw_code = str(crs.get('course_code', ''))
                safe_code = raw_code.replace('/', '/ ')
                
                c_data.append([
                    Paragraph(safe_code, p_style_code), 
                    Paragraph(crs.get('course_title','Unknown'), getSampleStyleSheet()['Normal']), 
                    str(int(cred) if cred.is_integer() else cred), "Yes" 
                ])
                
            c_data.append(["", Paragraph("<b>Total Credits</b>", getSampleStyleSheet()['Normal']), str(int(total_credits) if total_credits.is_integer() else total_credits), ""])

            t2 = Table(c_data, colWidths=[110, 305, 55, 55])
            t2.setStyle(TableStyle([
                ('GRID', (0,0), (-1,-1), 0.5, colors.black),
                ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('ALIGN', (0,0), (0,-1), 'CENTER'),
                ('ALIGN', (2,0), (-1,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ]))
            t2.wrapOn(c, w, h)
            _, t2_h = t2.wrap(w, h)
            t2.drawOn(c, margin, y - t2_h)
            y -= (t2_h + 20)

            c.setFont("Helvetica-Bold", 10)
            c.drawString(margin, y, "STUDENT UNDERTAKING:")
            y -= 15
            
            c.setLineWidth(1)
            c.setFont("Helvetica", 9)
            undertakings = [
                "I will strictly follow the AMCEC/VTU autonomy guidelines.",
                "I have paid the full tuition fees and examination fees for the current semester.",
                "I am aware that I must maintain a minimum of 85% attendance to appear for SEE.",
                "I have verified that my selected credits align with the academic regulations."
            ]
            for u in undertakings:
                c.rect(margin, y - 8, 10, 10) 
                c.drawString(margin + 18, y - 6, u)
                y -= 18
            y -= 10
            
            c.setFont("Helvetica-Bold", 10)
            c.drawString(margin, y, "DECLARATION:")
            y -= 12
            p_style = getSampleStyleSheet()['Normal']
            p_style.fontSize = 9
            decl = Paragraph("I hereby declare that the information provided is true to the best of my knowledge. I have carefully selected the courses listed above and I request to be registered for the same in the current semester.", p_style)
            decl.wrapOn(c, w - (2*margin), 50)
            _, decl_h = decl.wrap(w - (2*margin), 50)
            decl.drawOn(c, margin, y - decl_h)
            
            y -= (decl_h + 60) 

            c.setFont("Helvetica-Bold", 10)
            c.drawString(margin, y, f"Date: {date.today().strftime('%d-%m-%Y')}")
            c.drawRightString(w - margin, y, "_________________________")
            c.drawRightString(w - margin, y - 15, "Signature of the Student")
            
            c.showPage()
            
        if progress_bar:
            progress_bar.progress(min((i + BATCH_SIZE) / total, 1.0))

        for stream in batch_photos.values(): stream.close()
        batch_photos.clear()
        
    c.save()
    return buf.getvalue()

# ==========================================
# SUMMER & ODE PDF GENERATOR 
# ==========================================
def generate_summer_pdf(student, courses, total_fee, utr_string="", academic_year="2026-27", exam_type="Regular"):
    PHOTO_BOOTH_URL = "https://amceducationphotobhoot.streamlit.app/"
    
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin = 35
    y = h - margin

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

    if "logo" in assets: c.drawImage(ImageReader(assets["logo"]), margin, y - 35, width=60, height=60, mask='auto', preserveAspectRatio=True)
    if "naac" in assets: c.drawImage(ImageReader(assets["naac"]), w - margin - 60, y - 35, width=60, height=60, mask='auto', preserveAspectRatio=True)

    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(w/2, y, "AMC ENGINEERING COLLEGE (AUTONOMOUS)")
    c.setFont("Helvetica", 9)
    c.drawCentredString(w/2, y - 15, "AMC Campus, Bannerghatta Road, Bengaluru, Karnataka - 560083")
    c.drawCentredString(w/2, y - 27, "Autonomous Institution Affiliated to VTU, Belagavi | NAAC A+ Accredited")
    c.setLineWidth(1)
    c.line(margin, y - 45, w - margin, y - 45)
    y -= 65

    c.setFont("Helvetica-Bold", 11)
    if exam_type.upper() == "ODE":
        c.drawCentredString(w/2, y, f"On-Demand Examination (ODE) Application Form - {academic_year}")
    else:
        c.drawCentredString(w/2, y, f"Course Registration/ Exam application form- {exam_type} Semester {academic_year}")
    y -= 20

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Student Details")
    y -= 5

    display_id = student.get('admission_number') if pd.isna(student.get('usn')) or student.get('usn') == '' else student.get('usn')
    
    formatted_branch = format_branch_name(student.get('branch_code', ''))
    stu_spec = str(student.get('specialization', 'GENERAL')).strip().upper()
    if stu_spec not in ['GENERAL', 'NONE', 'NAN', '']:
        formatted_branch += f" ({stu_spec})"

    current_sem = student.get('current_sem', '-')

    if str(display_id).startswith("TMP-"):
        table_display_id = str(display_id).split("-")[-1]
    else:
        table_display_id = display_id

    p_style = getSampleStyleSheet()['Normal']
    p_style.alignment = 1 
    p_style.fontSize = 8
    
    photo_io = get_student_photo(display_id, student.get('admission_number'))
    has_photo = bool(photo_io)
    
    if has_photo:
        photo_io.seek(0)
        p_img = RLImage(photo_io, width=60, height=80)
        p_img.hAlign = 'CENTER'
        p_img.vAlign = 'MIDDLE'
        digital_col = p_img
        
        s_data = [
            ["USN / Admin No.", "Student Name", "Branch", "Type", "Sem", "Student Photo"],
            [table_display_id, student.get('full_name',''), formatted_branch, "UG", str(current_sem), digital_col]
        ]
        t1_colWidths = [85, 155, 55, 40, 40, 150]
        
    else:
        qr = qrcode.make(PHOTO_BOOTH_URL)
        qr_io = io.BytesIO()
        qr.save(qr_io, format="PNG")
        qr_io.seek(0)
        p_img = RLImage(qr_io, width=50, height=50)
        p_img.hAlign = 'CENTER'
        p_img.vAlign = 'MIDDLE'
        
        pin = student.get('photo_pin', 'XXXX')
        login_text = f"Scan to Upload<br/>User Name: <b>{display_id}</b><br/>PIN: <b>{pin}</b>"
        digital_col = [p_img, Paragraph(login_text, p_style)]
        physical_col = Paragraph("<br/><br/><br/>Affix Physical<br/>Photo Here", p_style)

        s_data = [
            ["USN / Admin No.", "Student Name", "Branch", "Type", "Sem", "Digital Photo", "Physical Photo"],
            [table_display_id, student.get('full_name',''), formatted_branch, "UG", str(current_sem), digital_col, physical_col]
        ]
        t1_colWidths = [80, 135, 50, 40, 30, 95, 95]

    style_cmds = [
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]

    t1 = Table(s_data, colWidths=t1_colWidths, rowHeights=[20, 115])
    t1.setStyle(TableStyle(style_cmds))
    t1.wrapOn(c, w, h)
    _, t1_h = t1.wrap(w, h)
    t1.drawOn(c, margin, y - t1_h)
    y -= (t1_h + 20)

    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(w - margin, y, f"Registration Date: {date.today().strftime('%d-%m-%Y')}")
    y -= 15

    c.drawString(margin, y, "Courses Registered")
    y -= 5

    p_style_code = getSampleStyleSheet()['Normal']
    p_style_code.fontSize = 9
    p_style_code.leading = 11

    third_col_header = "ODE Attempt" if exam_type.upper() == "ODE" else "Previous Grade"
    c_data = [["Course Code", "Course Title", third_col_header, "Fee (Rs)", "Apply"]]
    
    courses = sort_courses_by_sequence(courses)
    
    rule2_count = 0
    for crs in courses:
        rule = str(crs.get('rule', ''))
        if exam_type.upper() == "ODE":
            if "ATTEMPT 2" in rule.upper() or "SUBSEQUENT" in rule.upper() or crs.get('attempt_num', 1) > 1:
                fee_str, prev_grade = "3000", "Attempt 2+"
            else:
                fee_str, prev_grade = "6000", "Attempt 1"
        elif "Rule 1" in rule:
            fee_str, prev_grade = "5600", "NE"
        elif "Rule 2" in rule:
            rule2_count += 1
            fee_str, prev_grade = ("2000" if rule2_count == 1 else "1000"), "AB"
        elif "Rule 3" in rule:
            fee_str, prev_grade = "600", "F"
        else:
            fee_str, prev_grade = "-", crs.get('grade', '-')

        raw_code = str(crs.get('course_code', ''))
        safe_code = raw_code.replace('/', '/ ')

        c_data.append([
            Paragraph(safe_code, p_style_code), 
            Paragraph(crs.get('course_title','Unknown'), getSampleStyleSheet()['Normal']), 
            prev_grade, fee_str, "Applied" 
        ])
    
    if exam_type.upper() == "SUMMER":
        c_data.append(["", Paragraph("<b>Base Application Fee:</b>", getSampleStyleSheet()['Normal']), "", "400", ""])
        c_data.append(["", Paragraph("<b>Total Amount Payable:</b>", getSampleStyleSheet()['Normal']), "", str(total_fee) if total_fee > 0 else "-", ""])
    elif exam_type.upper() == "ODE":
        c_data.append(["", Paragraph("<b>Application Fee:</b>", getSampleStyleSheet()['Normal']), "", "100", ""])
        c_data.append(["", Paragraph("<b>Grade Card Fee:</b>", getSampleStyleSheet()['Normal']), "", "600", ""])
        c_data.append(["", Paragraph("<b>Total Amount Payable:</b>", getSampleStyleSheet()['Normal']), "", str(total_fee) if total_fee > 0 else "-", ""])

    t2 = Table(c_data, colWidths=[110, 205, 90, 60, 60])
    t2.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('ALIGN', (2,0), (4,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    t2.wrapOn(c, w, h)
    _, t2_h = t2.wrap(w, h)
    t2.drawOn(c, margin, y - t2_h)
    y -= (t2_h + 20)

    c.setLineWidth(1)
    c.setFont("Helvetica", 9)
    undertakings = [
        "I will strictly follow the AMCEC / VTU autonomy guidelines.",
        "I have paid the prescribed fee." if exam_type.upper() in ["SUMMER", "ODE"] else "I have paid my regular tuition fees."
    ]
    for u in undertakings:
        c.rect(margin, y - 8, 10, 10) 
        c.drawString(margin + 18, y - 6, u)
        y -= 18
    y -= 10
    
    if exam_type.upper() == "SUMMER":
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margin, y, "Note - Summer Semester Rules:")
        y -= 12
        c.setFont("Helvetica", 8)
        c.drawString(margin, y, "Rule 1: Mandatory Classes (CIE Fail) | Rule 2: Exam Only (Absent) | Rule 3: Exam Only (SEE Fail / MP)")
        y -= 18
    elif exam_type.upper() == "ODE":
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margin, y, "Note - On-Demand Examination (ODE) Fee Structure:")
        y -= 12
        c.setFont("Helvetica", 8)
        c.drawString(margin, y, "Attempt 1: Rs 6,000 / subject | Subsequent Attempts (Attempt 2+): Rs 3,000 / subject | Application Fee: Rs 100 | Grade Card Fee: Rs 600")
        y -= 18

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Declaration:")
    y -= 12
    p_style = getSampleStyleSheet()['Normal']
    p_style.fontSize = 9
    decl = Paragraph("The courses listed in this application are the only courses I wish to apply for this Examination. Further, I understand this application overrides any previous application I may have submitted.", p_style)
    decl.wrapOn(c, w - (2*margin), 50)
    _, decl_h = decl.wrap(w - (2*margin), 50)
    decl.drawOn(c, margin, y - decl_h)
    y -= (decl_h + 20)

    if exam_type.upper() in ["SUMMER", "ODE"]:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin, y, "Transaction ID / UTR:")
        c.setFont("Helvetica", 10)
        if utr_string and utr_string.strip(): c.drawString(margin + 120, y, utr_string.strip())
        else: c.drawString(margin + 120, y, "__________________________________________________")
        y -= 30

    # 2 lines of space for signature
    y -= 40 

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, f"Date: {date.today().strftime('%d-%m-%Y')}")
    c.drawRightString(w - margin, y, "_________________________")
    c.drawRightString(w - margin, y - 15, "Signature of Student")
    
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
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("### Student Details")
        usn = st.text_input("Student USN (e.g., 1AM24CS099)", key="clerk_usn")
        student_name = st.text_input("Student Name", key="clerk_name")
        branch = st.selectbox("Branch", ["AE", "AIML", "CSE", "CSE-AIML", "CSE-DS", "CV", "ME", "ECE", "EEE", "ISE", "M Tech", "MBA", "MCA"], key="clerk_branch")
    with col2:
        st.markdown("### Payment Details")
        payment_date = st.date_input("Date of Payment", date.today(), key="clerk_date")
        amount = st.number_input("Amount Paid (₹)", min_value=1.0, step=100.0, key="clerk_amount")
        payment_type = st.selectbox("Fee Type", ["Exam Fee", "Tuition Fee", "Fine", "Revaluation fee", "Convocation fees", "Arrears fees", "Summer Semester Fee", "On-Demand Exam (ODE) Fee", "Other"], key="clerk_type")
        other_description = st.text_input("Specify Other Fee", max_chars=12, key="clerk_other") if payment_type == "Other" else ""
    with col3:
        st.markdown("### Transaction Details")
        payment_mode = st.selectbox("Payment Mode", ["UPI (QR / App)", "Bank Transfer (NEFT / RTGS)", "SBI Direct"], key="clerk_mode")
        utr = st.text_input("Transaction ID / UTR No.", key="clerk_utr")
        college_account = st.text_input("Credited To A/C (Bank details)", key="clerk_acc")
        
    if st.button("💾 Save & Sync Receipt", type="primary", use_container_width=True, key="clerk_save_btn"):
        final_payment_type = f"Other - {other_description.strip()}" if payment_type == "Other" else payment_type
        clean_utr = utr.strip().upper()
        
        if not usn or not student_name or not clean_utr or not college_account: 
            st.error("⚠️ Missing mandatory fields!")
        elif payment_mode == "UPI (QR / App)" and (not clean_utr.isdigit() or len(clean_utr) != 12): 
            st.error("❌ UPI UTR must be 12 digits!")
        elif payment_mode == "Bank Transfer (NEFT / RTGS)" and (not clean_utr.isalnum() or len(clean_utr) != 22): 
            st.error("❌ NEFT UTR must be 22 characters!")
        elif payment_mode == "SBI Direct" and not clean_utr.isalnum():
            st.error("❌ SBI Direct Transaction ID must be alphanumeric!")
        else:
            try:
                supabase.table("cash_receipts").insert({
                    "payment_date": str(payment_date), 
                    "amount": amount, 
                    "utr_number": clean_utr, 
                    "payment_type": final_payment_type, 
                    "college_account": college_account.strip(), 
                    "payment_mode": payment_mode, 
                    "usn": usn.strip().upper(), 
                    "student_name": student_name.strip().title(), 
                    "branch": branch, 
                    "entered_by": st.session_state.username
                }).execute()
                st.success(f"✅ Receipt saved successfully for {usn.strip().upper()} via {payment_mode}!")
            except Exception as e: 
                st.error(f"❌ Duplicate UTR or Database Error: {e}")

# ==========================================
# VIEW 2: DEPARTMENT PORTAL (INTEGRATED)
# ==========================================
def department_dashboard():
    st.title("🏛️ Department Online Course Registration Portal")
    st.markdown(f"Coordinator: **{st.session_state.username}**")
    
    try:
        gs_res = supabase.table("global_settings").select("*").execute()
        global_settings = {r['setting_key']: r['setting_value'] for r in gs_res.data}
    except:
        global_settings = {}
        
    active_ay = global_settings.get('active_academic_year', '2026-27')
    active_term = global_settings.get('active_term', 'ODD')

    tab_reg, tab_app, tab_summer, tab_ode, tab_reports = st.tabs([
        "📝 Phase 1: Course Registration",
        "🎓 Phase 2: Exam Applications",
        "☀️ Summer Exams",
        "⚡ On-Demand (ODE)",
        "📊 Reports"
    ])
    
    # ---------------------------------------------------------
    # TAB 1: REGULAR COURSE REGISTRATION (FULL FEATURES + ONLINE)
    # ---------------------------------------------------------
    with tab_reg:
        st.info(f"📅 **Active Academic Term:** {active_ay} | **{active_term}** Semester")
        st.write("Register students for classes at the start of the semester.")
        
        entry_mode = st.radio(
            "Registration Mode:",
            ["👤 Single Student Entry", "🚀 Bulk Branch Auto-Registration"],
            horizontal=True,
            key="reg_mode"
        )
        st.divider()
        
        # ---------- SINGLE STUDENT ENTRY ----------
        if entry_mode == "👤 Single Student Entry":
            target_id = st.text_input("Enter Student USN or Admission Number", key="single_target_id").strip().upper()
            if target_id:
                stu_res = supabase.table("master_students").select("*").eq("usn", target_id).execute()
                if not stu_res.data:
                    stu_res = supabase.table("master_students").select("*").eq("admission_number", target_id).execute()
                
                if not stu_res.data: 
                    st.error("Student not found in database.")
                else:
                    stu = stu_res.data[0]
                    active_usn_or_admin = stu.get('usn') if pd.notna(stu.get('usn')) and stu.get('usn') != '' else stu.get('admission_number')
                    
                    current_sem = int(stu.get('current_sem', 1))
                    student_scheme = int(stu.get('scheme_batch', 25))
                    stu_spec = str(stu.get('specialization', 'GENERAL')).strip().upper()
                    
                    cat_val = str(stu.get('student_category', '')).strip().upper()
                    student_is_lateral = (cat_val == 'LATERAL')
                    
                    staging_check = supabase.table("course_registration_online").select("*").eq("usn", active_usn_or_admin).eq("academic_year", active_ay).eq("semester", current_sem).eq("registration_type", "REGULAR").execute()
                    is_staged = staging_check.data and len(staging_check.data) > 0
                    
                    if is_staged:
                        st.success(f"✅ Student '{active_usn_or_admin}' is already registered online and is LIVE in the COE database!")
                        reg_data = staging_check.data
                        course_codes = [r['course_code'] for r in reg_data]
                        crs_res = supabase.table("master_courses").select("course_code, title, credits").in_("course_code", course_codes).execute()
                        
                        c_info = {c['course_code']: c for c in (crs_res.data or [])}
                        
                        reconstructed_courses = [{
                            'course_code': r['course_code'], 
                            'course_title': c_info.get(r['course_code'], {}).get('title', 'Unknown'),
                            'credits': float(c_info.get(r['course_code'], {}).get('credits', 0.0))
                        } for r in reg_data]
                        
                        pdf_bytes = generate_regular_pdf(stu, reconstructed_courses, academic_year=active_ay, term=active_term, current_sem=current_sem)
                        st.download_button("🖨️ Re-Download Application PDF", data=pdf_bytes, file_name=f"Regular_Application_{active_usn_or_admin}.pdf", mime="application/pdf", type="primary", key="single_redl_btn")
                    else:
                        if str(stu.get('status', '')).strip().upper() == 'DISCONTINUED':
                            st.error(f"❌ **Registration Blocked:** Student '{active_usn_or_admin}' is marked as DISCONTINUED.")
                        else:
                            is_odd_sem = (current_sem % 2 != 0)
                            is_active_odd = (active_term.upper() == 'ODD')
                            
                            if is_odd_sem != is_active_odd:
                                st.error(f"❌ **Term Mismatch Block:** The Active Term is **{active_term}**, but this student is currently mapped to **Semester {current_sem}**. They must be officially promoted in the database before they can register.")
                            else:
                                branch_code = stu.get('branch_code', '')
                                br_res = supabase.table("master_branches").select("program_type").eq("branch_code", branch_code).execute()
                                prog_type = br_res.data[0]['program_type'] if br_res.data else "UG"
                                
                                lat_badge = " | 🎓 **Lateral Entry**" if student_is_lateral else ""
                                spec_badge = f" | 🎯 **Track:** {stu_spec}" if stu_spec not in ['GENERAL', 'NONE', 'NAN', ''] else ""
                                
                                st.success(f"**{stu['full_name']}** | Branch: **{branch_code}** ({prog_type}) | Sem: **{current_sem}** | Scheme: **{student_scheme}**{spec_badge}{lat_badge}")
                                
                                courses_res = supabase.table("master_courses").select("*").execute()
                                all_courses = courses_res.data if courses_res.data else []
                                
                                core_courses = [c for c in all_courses if c.get('semester_id') == current_sem and branch_match(c.get('branch_code', ''), branch_code) and c.get('course_type', 'CORE') in ['CORE', 'LAB', 'NON CREDIT', 'PROJECT', 'OMR', 'ONLINE'] and int(c.get('scheme_batch', 25)) == student_scheme and match_specialization(c.get('specialization'), stu_spec)]
                                lateral_courses = [c for c in all_courses if c.get('semester_id') == current_sem and branch_match(c.get('branch_code', ''), branch_code) and c.get('course_type', 'CORE') == 'LATERAL' and int(c.get('scheme_batch', 25)) == student_scheme and match_specialization(c.get('specialization'), stu_spec)]
                                
                                pe_courses = [c for c in all_courses if c.get('semester_id') == current_sem and branch_match(c.get('branch_code', ''), branch_code) and c.get('course_type') == 'PE' and int(c.get('scheme_batch', 25)) == student_scheme and match_specialization(c.get('specialization'), stu_spec)]
                                oe_courses = [c for c in all_courses if c.get('semester_id') == current_sem and not branch_match(c.get('branch_code', ''), branch_code) and c.get('course_type') == 'OE' and int(c.get('scheme_batch', 25)) == student_scheme and match_specialization(c.get('specialization'), stu_spec)]
                                
                                selected_codes, total_credits = [], 0.0
                                
                                st.markdown("### 1. Mandatory Core, Lab, Project, OMR, Online & Non-Credit Courses")
                                if not core_courses:
                                    st.warning("No core courses found for this semester.")
                                for core in core_courses:
                                    st.markdown(f"- ✅ **{core['course_code']}** - {core['title']} *(Auto-assigned {core.get('course_type', 'CORE')})*")
                                    selected_codes.append(core['course_code'])
                                    total_credits += float(core.get('credits', 4))
                                    
                                if student_is_lateral and lateral_courses:
                                    st.markdown("### 🎓 Additional Lateral Entry Subjects")
                                    for lat in lateral_courses:
                                        st.markdown(f"- ✅ **{lat['course_code']}** - {lat['title']} *(Auto-assigned LATERAL)*")
                                        selected_codes.append(lat['course_code'])
                                        total_credits += float(lat.get('credits', 0))
                                    
                                if pe_courses or oe_courses:
                                    st.markdown("### 2. Electives")
                                    col_pe, col_oe = st.columns(2)
                                    if pe_courses:
                                        pe_options = {f"{p['course_code']} - {p['title']}": p['course_code'] for p in pe_courses}
                                        chosen_pe = col_pe.selectbox("Professional Elective", ["-- Select --"] + list(pe_options.keys()), key="single_pe")
                                        if chosen_pe != "-- Select --":
                                            selected_codes.append(pe_options[chosen_pe])
                                            total_credits += float(next(p for p in pe_courses if p['course_code'] == pe_options[chosen_pe]).get('credits', 3))
                                    if oe_courses:
                                        oe_options = {f"{o['course_code']} [{o['branch_code']}] - {o['title']}": o['course_code'] for o in oe_courses}
                                        chosen_oe = col_oe.selectbox("Open Elective", ["-- Select --"] + list(oe_options.keys()), key="single_oe")
                                        if chosen_oe != "-- Select --":
                                            selected_codes.append(oe_options[chosen_oe])
                                            total_credits += float(next(o for o in oe_courses if o['course_code'] == oe_options[chosen_oe]).get('credits', 3))
                                        
                                st.info(f"📊 **Total Credits Selected:** {total_credits}")
                                
                                if st.button("💾 Submit Registration & Generate PDF", type="primary", key="single_reg_btn"):
                                    if not selected_codes:
                                        st.error("❌ **Invalid Submission:** No courses were selected. Cannot submit an empty registration.")
                                    else:
                                        payload_staging = [{"usn": active_usn_or_admin, "course_code": cc, "semester": current_sem, "academic_year": active_ay, "semester_type": active_term, "registration_type": "REGULAR", "rule_category": "", "fee_amount": 0, "payment_status": "PAID", "utr_number": ""} for cc in selected_codes]
                                        payload_official = [{"usn": active_usn_or_admin, "course_code": cc, "semester": current_sem, "academic_year": active_ay, "semester_type": active_term, "registration_type": "REGULAR"} for cc in selected_codes]
                                        
                                        try:
                                            supabase.table("course_registration_online").delete().eq("academic_year", active_ay).eq("semester_type", active_term).eq("registration_type", "REGULAR").eq("usn", active_usn_or_admin).execute()
                                            supabase.table("course_registrations").delete().eq("academic_year", active_ay).eq("semester_type", active_term).eq("usn", active_usn_or_admin).execute()
                                            
                                            supabase.table("course_registration_online").insert(payload_staging).execute()
                                            supabase.table("course_registrations").insert(payload_official).execute()
                                            
                                            st.success("✅ Application successfully registered and sent directly to the COE!")
                                            
                                            pdf_courses = [{
                                                "course_code": cc, 
                                                "course_title": next((c['title'] for c in all_courses if c['course_code'] == cc), "Unknown"),
                                                "credits": next((float(c['credits']) for c in all_courses if c['course_code'] == cc), 0.0)
                                            } for cc in selected_codes]
                                            
                                            pdf_bytes = generate_regular_pdf(stu, pdf_courses, academic_year=active_ay, term=active_term, current_sem=current_sem)
                                            st.download_button("🖨️ Download Official Application PDF", data=pdf_bytes, file_name=f"Regular_Application_{active_usn_or_admin}.pdf", mime="application/pdf", type="primary", key="single_dl_new")
                                        except Exception as e:
                                            st.error(f"Database Error: {e}")

        # ---------- BULK BRANCH AUTO-REGISTRATION ----------
        elif entry_mode == "🚀 Bulk Branch Auto-Registration":
            st.markdown("### Bulk Class Registration Engine")
            col_b1, col_b2, col_b3 = st.columns(3)
            
            try:
                branches_data = supabase.table("master_branches").select("branch_code").execute().data
                branch_list = [b['branch_code'] for b in branches_data if str(b['branch_code']).upper() != 'COMMON']
            except: branch_list = []
            
            b_branch = col_b1.selectbox("Select Branch", ["-- Select --"] + branch_list, key="bulk_branch")
            b_sem = col_b2.number_input("Target Semester", min_value=1, max_value=10, value=1, key="bulk_sem")
            b_scheme = col_b3.number_input("Scheme Batch (e.g., 25)", value=25, key="bulk_scheme")
            
            if b_branch != "-- Select --":
                is_odd_sem = (b_sem % 2 != 0)
                is_active_odd = (active_term.upper() == 'ODD')
                
                if is_odd_sem != is_active_odd:
                    st.error(f"❌ **Term Mismatch:** Cannot bulk register students into Semester {b_sem} during an {active_term} term.")
                else:
                    with st.spinner("Analyzing curriculum and active students..."):
                        
                        stu_res = supabase.table("master_students").select("usn, admission_number, status, full_name, branch_code, photo_pin, section, student_category, specialization").eq("branch_code", b_branch).eq("current_sem", str(b_sem)).eq("scheme_batch", str(b_scheme)).execute()
                        all_branch_stu = [s for s in (stu_res.data or []) if str(s.get('status', '')).strip().upper() == 'ACTIVE']
                        
                        available_sections = sorted(list(set([str(s.get('section', 'Unassigned')).strip().upper() for s in all_branch_stu if s.get('section')])))
                        available_specs = sorted(list(set([str(s.get('specialization', 'GENERAL')).strip().upper() for s in all_branch_stu])))
                        
                    if not all_branch_stu:
                        st.warning(f"No ACTIVE students found in {b_branch} Semester {b_sem} (Scheme {b_scheme}).")
                    else:
                        st.divider()
                        st.markdown("#### 🎯 Batch Deployment & Filtering")
                        
                        col_f1, col_f2 = st.columns(2)
                        b_section = col_f1.selectbox("Filter Target Section", ["-- Process Entire Branch --"] + available_sections, key="bulk_sec_filter")
                        
                        if len(available_specs) > 1 or (len(available_specs) == 1 and available_specs[0] != 'GENERAL'):
                            b_spec = col_f2.selectbox("Filter Target Specialization Track", ["-- Process All Tracks --"] + available_specs, key="bulk_spec_filter")
                        else:
                            b_spec = "-- Process All Tracks --"
                            col_f2.info("Standard Curriculum (No Specialized Tracks Detected)")
                        
                        valid_stu = all_branch_stu
                        pdf_suffix = "ALL"
                        
                        if b_section != "-- Process Entire Branch --":
                            valid_stu = [s for s in valid_stu if str(s.get('section', '')).strip().upper() == b_section]
                            pdf_suffix = f"Sec_{b_section}"
                            
                        if b_spec != "-- Process All Tracks --":
                            valid_stu = [s for s in valid_stu if str(s.get('specialization', 'GENERAL')).strip().upper() == b_spec]
                            pdf_suffix += f"_{b_spec}"
                            
                        valid_stu = sorted(valid_stu, key=lambda x: x.get('full_name', ''))
                        
                        courses_res = supabase.table("master_courses").select("*").eq("semester_id", str(b_sem)).eq("scheme_batch", str(b_scheme)).execute()
                        all_courses = courses_res.data if courses_res.data else []
                        
                        all_core_courses = [c for c in all_courses if branch_match(c.get('branch_code', ''), b_branch) and c.get('course_type', 'CORE') in ['CORE', 'LAB', 'NON CREDIT', 'PROJECT', 'OMR', 'ONLINE']]
                        all_lateral_courses = [c for c in all_courses if branch_match(c.get('branch_code', ''), b_branch) and c.get('course_type', 'CORE') == 'LATERAL']
                        
                        pe_courses = [c for c in all_courses if branch_match(c.get('branch_code', ''), b_branch) and c.get('course_type') == 'PE']
                        oe_courses = [c for c in all_courses if not branch_match(c.get('branch_code', ''), b_branch) and c.get('course_type') == 'OE']
                        
                        if not all_core_courses:
                            st.warning(f"No Core courses mapped to {b_branch} for Semester {b_sem}. Please update Master Courses.")
                        elif not valid_stu:
                            st.warning(f"No students match the selected filters.")
                        else:
                            st.success(f"👥 Ready to process **{len(valid_stu)} Students** for {b_branch}.")
                            
                            active_ids = [s.get('usn') if pd.notna(s.get('usn')) and s.get('usn') != '' else s.get('admission_number') for s in valid_stu]
                            
                            registered_data = []
                            for i in range(0, len(active_ids), 50):
                                chunk_ids = active_ids[i:i+50]
                                staging_check = supabase.table("course_registration_online").select("usn, course_code").eq("academic_year", active_ay).eq("semester", b_sem).eq("registration_type", "REGULAR").in_("usn", chunk_ids).execute()
                                if staging_check.data:
                                    registered_data.extend(staging_check.data)
                                    
                            registered_usns = list(set([r['usn'] for r in registered_data]))
                            
                            if registered_usns:
                                st.success(f"✅ {len(registered_usns)} students in this selection already have active registrations.")
                                if st.button(f"🖨️ Re-Download Master PDF Batch", type="secondary", key="bulk_redl_btn"):
                                    grouped_courses = defaultdict(list)
                                    for r in registered_data:
                                        grouped_courses[r['usn']].append(r['course_code'])
                                        
                                    student_course_payload = []
                                    for s in valid_stu:
                                        s_id = s.get('usn') if pd.notna(s.get('usn')) and s.get('usn') != '' else s.get('admission_number')
                                        if s_id in grouped_courses:
                                            c_codes = grouped_courses[s_id]
                                            pdf_courses = [{
                                                "course_code": cc, 
                                                "course_title": next((c['title'] for c in all_courses if c['course_code'] == cc), "Unknown"),
                                                "credits": next((float(c.get('credits', 0)) for c in all_courses if c['course_code'] == cc), 0.0)
                                            } for cc in c_codes]
                                            student_course_payload.append({'student': s, 'courses': pdf_courses})
                                            
                                    status_text = st.empty()
                                    progress_bar = st.progress(0)
                                    
                                    pdf_bytes = generate_regular_pdf_bulk(student_course_payload, academic_year=active_ay, term=active_term, current_sem=b_sem, progress_bar=progress_bar, status_text=status_text)
                                    
                                    status_text.success("✅ Master PDF Reconstructed Successfully!")
                                    st.download_button("📥 Save Master PDF", data=pdf_bytes, file_name=f"Bulk_ReDownload_{b_branch}_{pdf_suffix}.pdf", mime="application/pdf", type="primary", key="bulk_redl_save")
                                st.divider()
                            
                            if not pe_courses and not oe_courses:
                                st.success("🌟 **Pure Core Semester Detected!** There are no electives for this batch. The system will automatically handle Lateral subjects for applicable students.")
                                
                                if st.button(f"🚀 One-Click Auto-Register {len(valid_stu)} Students", type="primary", key="bulk_autoreg_btn"):
                                    payload_staging, payload_official, student_course_payload = [], [], []
                                    processed_ids = []
                                    
                                    for s in valid_stu:
                                        active_id = s.get('usn') if pd.notna(s.get('usn')) and s.get('usn') != '' else s.get('admission_number')
                                        
                                        cat_val = str(s.get('student_category', '')).strip().upper()
                                        is_lateral = (cat_val == 'LATERAL')
                                        
                                        stu_spec = str(s.get('specialization', 'GENERAL')).strip().upper()
                                        processed_ids.append(active_id)
                                        
                                        student_assigned_courses = [c for c in all_core_courses if match_specialization(c.get('specialization'), stu_spec)]
                                        
                                        if is_lateral:
                                            student_assigned_courses.extend([c for c in all_lateral_courses if match_specialization(c.get('specialization'), stu_spec)])
                                        
                                        pdf_courses = []
                                        for c in student_assigned_courses:
                                            payload_staging.append({"usn": active_id, "course_code": c['course_code'], "semester": b_sem, "academic_year": active_ay, "semester_type": active_term, "registration_type": "REGULAR", "rule_category": "", "fee_amount": 0, "payment_status": "PAID", "utr_number": ""})
                                            payload_official.append({"usn": active_id, "course_code": c['course_code'], "semester": b_sem, "academic_year": active_ay, "semester_type": active_term, "registration_type": "REGULAR"})
                                            pdf_courses.append({"course_code": c['course_code'], "course_title": c['title'], "credits": float(c.get('credits', 0))})
                                            
                                        student_course_payload.append({'student': s, 'courses': pdf_courses})
                                    
                                    with st.spinner(f"☁️ Syncing {len(payload_staging) * 2} records to the COE Cloud Database..."):
                                        try:
                                            for i in range(0, len(processed_ids), 200):
                                                supabase.table("course_registration_online").delete().eq("academic_year", active_ay).eq("semester_type", active_term).eq("registration_type", "REGULAR").in_("usn", processed_ids[i:i+200]).execute()
                                                supabase.table("course_registrations").delete().eq("academic_year", active_ay).eq("semester_type", active_term).in_("usn", processed_ids[i:i+200]).execute()
                                            
                                            for i in range(0, len(payload_staging), 1000):
                                                supabase.table("course_registration_online").insert(payload_staging[i:i+1000]).execute()
                                                supabase.table("course_registrations").insert(payload_official[i:i+1000]).execute()
                                                
                                            st.success(f"✅ Cloud Sync Complete! Registered {len(valid_stu)} students.")
                                        except Exception as e:
                                            st.error(f"Bulk Registration Error: {e}")
                                            st.stop() 

                                    status_text = st.empty()
                                    progress_bar = st.progress(0)
                                    
                                    pdf_bytes = generate_regular_pdf_bulk(student_course_payload, academic_year=active_ay, term=active_term, current_sem=b_sem, progress_bar=progress_bar, status_text=status_text)
                                    
                                    status_text.success("✅ Master PDF Generated Successfully!")
                                    st.download_button("📥 Download Section PDF", data=pdf_bytes, file_name=f"Applications_{b_branch}_{pdf_suffix}.pdf", mime="application/pdf", type="primary", key="bulk_autoreg_dl")
                            
                            else:
                                st.warning(f"⚠️ **Electives Detected.** This semester has {len(pe_courses)} PE and {len(oe_courses)} OE options. You must upload a CSV mapping each student to their chosen courses.")
                                
                                st.markdown("Please upload a CSV containing only **`usn`** (or admission number) and **`course_code`**. (Include both core and elective codes for each student).")
                                b_csv = st.file_uploader("Upload Department CSV", type="csv", key="bulk_csv_upload")
                                
                                if b_csv and st.button("🚀 Process Bulk CSV Upload", type="primary", key="bulk_csv_btn"):
                                    df = pd.read_csv(b_csv)
                                    df.columns = [c.strip().lower() for c in df.columns]
                                    
                                    if 'usn' not in df.columns or 'course_code' not in df.columns:
                                        st.error("CSV must contain 'usn' and 'course_code' columns.")
                                    else:
                                        payload_staging, payload_official = [], []
                                        csv_ids = list(set(df['usn'].dropna().astype(str).str.strip().str.upper()))
                                        
                                        for _, row in df.iterrows():
                                            u = str(row['usn']).strip().upper()
                                            cc = str(row['course_code']).strip().upper()
                                            if u and cc:
                                                payload_staging.append({"usn": u, "course_code": cc, "semester": b_sem, "academic_year": active_ay, "semester_type": active_term, "registration_type": "REGULAR", "rule_category": "", "fee_amount": 0, "payment_status": "PAID", "utr_number": ""})
                                                payload_official.append({"usn": u, "course_code": cc, "semester": b_sem, "academic_year": active_ay, "semester_type": active_term, "registration_type": "REGULAR"})
                                        
                                        with st.spinner(f"☁️ Syncing {len(payload_staging) * 2} rows from CSV to Cloud Database..."):
                                            try:
                                                for i in range(0, len(csv_ids), 200):
                                                    supabase.table("course_registration_online").delete().eq("academic_year", active_ay).eq("semester_type", active_term).eq("registration_type", "REGULAR").in_("usn", csv_ids[i:i+200]).execute()
                                                    supabase.table("course_registrations").delete().eq("academic_year", active_ay).eq("semester_type", active_term).in_("usn", csv_ids[i:i+200]).execute()
                                                
                                                for i in range(0, len(payload_staging), 1000):
                                                    supabase.table("course_registration_online").insert(payload_staging[i:i+1000]).execute()
                                                    supabase.table("course_registrations").insert(payload_official[i:i+1000]).execute()
                                                    
                                                st.success(f"✅ Successfully processed CSV and registered {len(csv_ids)} students!")
                                            except Exception as e:
                                                st.error(f"Upload Error: {e}")
                                                st.stop()

                                        with st.spinner("🗂️ Gathering student database records..."):
                                            try:
                                                st_res = supabase.table("master_students").select("usn, admission_number, full_name, branch_code, photo_pin, student_category, specialization").in_("usn", csv_ids).execute()
                                                found_usns = [s['usn'] for s in st_res.data]
                                                missing = [x for x in csv_ids if x not in found_usns]
                                                if missing:
                                                    st_res_adm = supabase.table("master_students").select("usn, admission_number, full_name, branch_code, photo_pin, student_category, specialization").in_("admission_number", missing).execute()
                                                    st_res.data.extend(st_res_adm.data)
                                                    
                                                stu_dict = {}
                                                for s in st_res.data:
                                                    if s.get('usn'): stu_dict[s['usn']] = s
                                                    if s.get('admission_number'): stu_dict[s['admission_number']] = s
                                                    
                                                grouped_courses = defaultdict(list)
                                                for p in payload_staging:
                                                    grouped_courses[p['usn']].append(p['course_code'])
                                                    
                                                student_course_payload = []
                                                for uid, c_codes in grouped_courses.items():
                                                    s = stu_dict.get(uid)
                                                    if not s: continue
                                                    
                                                    pdf_courses = [{
                                                        "course_code": cc, 
                                                        "course_title": next((c['title'] for c in all_courses if c['course_code'] == cc), "Unknown"),
                                                        "credits": next((float(c['credits']) for c in all_courses if c['course_code'] == cc), 0.0)
                                                    } for cc in c_codes]
                                                    
                                                    student_course_payload.append({'student': s, 'courses': pdf_courses})
                                            except Exception as e:
                                                st.error(f"Data mapping error: {e}")
                                                st.stop()
                                        
                                        status_text = st.empty()
                                        progress_bar = st.progress(0)
                                        
                                        pdf_bytes = generate_regular_pdf_bulk(student_course_payload, academic_year=active_ay, term=active_term, current_sem=b_sem, progress_bar=progress_bar, status_text=status_text)
                                        
                                        status_text.success("✅ Master PDF Generated Successfully!")
                                        st.download_button("📥 Download Master PDF (All CSV Students)", data=pdf_bytes, file_name=f"Bulk_Applications_{b_branch}_{pdf_suffix}_CSV.pdf", mime="application/pdf", type="primary", key="bulk_csv_dl")

    # ---------------------------------------------------------
    # TAB 2: EXAM APPLICATIONS (REGULAR & ARREARS)
    # ---------------------------------------------------------
    with tab_app:
        try:
            cycles_res = supabase.table("exam_cycles").select("*").execute()
            all_cycles_data = cycles_res.data or []
            active_cycles = [
                c for c in all_cycles_data
                if (bool(c.get('is_active')) or bool(c.get('is_brs_active')))
                and str(c.get('exam_type', '') or '').strip().upper() not in ['SUMMER', 'ODE']
                and 'SUMMER' not in str(c.get('cycle_name', '') or '').upper()
                and 'ODE' not in str(c.get('cycle_name', '') or '').upper()
                and 'ON-DEMAND' not in str(c.get('cycle_name', '') or '').upper()
            ]
        except Exception as e:
            st.error(f"Error loading exam cycles: {e}")
            active_cycles = []
        
        if not active_cycles:
            st.warning("No Regular Exam Cycles are currently open for applications.")
        else:
            c_opts = {c['cycle_name']: c for c in active_cycles}
            sel_cycle_name = st.selectbox("Select Target Exam Cycle:", list(c_opts.keys()), key="exam_cycle_sel")
            target_cycle = c_opts[sel_cycle_name]
            target_cycle_id = target_cycle['cycle_id']
            
            cycle_term = detect_cycle_term(target_cycle, fallback_term=active_term)
            cycle_parity = 1 if cycle_term == 'ODD' else 0
            
            st.info(f"The Exam Application establishes financial eligibility and locks the student into the **{sel_cycle_name}** cycle (**{cycle_term}** Parity).")
            
            app_mode = st.radio(
                "Application Mode:",
                ["🚀 Bulk Initialize Regular Exam Applications", "👤 Process Arrear Student (Custom)"],
                horizontal=True,
                key="app_mode"
            )
            st.divider()
            
            if app_mode == "🚀 Bulk Initialize Regular Exam Applications":
                st.write("This engine initializes the `exam_applications` row for **ALL** active regular students in the selected branch, saving their Base Exam Fee as Paid, and links their registered courses to this Exam Cycle so the COE can bulk print their forms.")
                
                col_a1, col_a2 = st.columns(2)
                try:
                    branches_data = supabase.table("master_branches").select("branch_code").execute().data
                    branch_list = [b['branch_code'] for b in branches_data if str(b['branch_code']).upper() != 'COMMON']
                except:
                    branch_list = []
                
                a_branch = col_a1.selectbox("Select Branch:", ["-- Select --"] + branch_list, key="app_branch")
                a_sem = col_a2.number_input("Target Exam Semester:", min_value=1, max_value=10, value=1, key="app_sem")
                
                if a_branch != "-- Select --" and st.button("🚀 Bulk Initialize Applications", type="primary", key="bulk_init_app_btn"):
                    with st.spinner("Initializing applications for all active students..."):
                        stu_res = supabase.table("master_students").select("usn, admission_number, status, current_sem").eq("branch_code", a_branch).execute()
                        branch_active_students = [
                            s for s in (stu_res.data or [])
                            if str(s.get('status', 'ACTIVE')).strip().upper() == 'ACTIVE'
                        ]
                        
                        branch_ids = [
                            (s.get('usn') if pd.notna(s.get('usn')) and str(s.get('usn')).strip() != '' else s.get('admission_number'))
                            for s in branch_active_students
                        ]
                        branch_ids = [u for u in branch_ids if u]
                        
                        if not branch_ids:
                            st.error("No active students found in this branch.")
                        else:
                            reg_rows = []
                            for i in range(0, len(branch_ids), 200):
                                chunk = branch_ids[i:i+200]
                                r_res = supabase.table("course_registrations").select("reg_id, usn, semester, cycle_id, registration_type").in_("usn", chunk).execute()
                                if r_res.data:
                                    reg_rows.extend(r_res.data)
                                    
                            matching_regs = [
                                r for r in reg_rows
                                if (str(r.get('semester', '')) == str(a_sem) or r.get('cycle_id') == target_cycle_id)
                                and str(r.get('registration_type', 'REGULAR')).upper() == 'REGULAR'
                            ]
                            registered_usns = sorted(list(set([r['usn'] for r in matching_regs if r.get('usn')])))
                            
                            if not registered_usns:
                                st.warning(f"No course registrations found for {a_branch} Semester {a_sem}. Please complete Phase 1 Course Registration first.")
                            else:
                                try:
                                    fee_res = supabase.table("master_fees").select("amount").eq("fee_type", "Exam").execute()
                                    base_fee = float(fee_res.data[0]['amount']) if fee_res.data else 2000.0
                                except:
                                    base_fee = 2000.0
                                
                                payload = []
                                for u in registered_usns:
                                    payload.append({
                                        "app_id": str(hashlib.md5(f"{u}{target_cycle_id}".encode()).hexdigest()[:8]),
                                        "cycle_id": target_cycle_id,
                                        "usn": u,
                                        "total_fee": base_fee,
                                        "payment_status": "PAID",
                                        "utr_number": "BULK-REGULAR-PAID"
                                    })
                                
                                try:
                                    for i in range(0, len(registered_usns), 200):
                                        chunk_u = registered_usns[i:i+200]
                                        supabase.table("course_registrations").update({"cycle_id": target_cycle_id}).eq("semester", a_sem).in_("usn", chunk_u).execute()
                                    
                                    for i in range(0, len(registered_usns), 200):
                                        supabase.table("exam_applications").delete().eq("cycle_id", target_cycle_id).in_("usn", registered_usns[i:i+200]).execute()
                                    for i in range(0, len(payload), 500):
                                        supabase.table("exam_applications").insert(payload[i:i+500]).execute()
                                        
                                    st.success(f"✅ Success! Base Applications generated and linked to **{sel_cycle_name}** for **{len(registered_usns)}** students. The COE can now bulk print their forms.")
                                except Exception as e:
                                    st.error(f"Error initializing applications: {e}")

            elif app_mode == "👤 Process Arrear Student (Custom)":
                st.write("Use this desk to collect arrear fees. You can search for ACTIVE students, as well as DETAINED students who need to clear their backlogs.")
                app_usn = st.text_input("Enter Student USN:", key="arr_usn").strip().upper()
                
                if app_usn:
                    stu_res = supabase.table("master_students").select("*").eq("usn", app_usn).execute()
                    if not stu_res.data:
                        stu_res = supabase.table("master_students").select("*").eq("admission_number", app_usn).execute()
                        
                    if not stu_res.data:
                        st.error("Student not found.")
                    else:
                        student = stu_res.data[0]
                        stu_id = student.get('usn') or student.get('admission_number')
                        st.success(f"**{student['full_name']}** | Branch: {student['branch_code']} | Sem: {student['current_sem']} | Status: {student['status']}")
                        
                        regular_courses = []
                        if str(student['status']).upper() == 'ACTIVE':
                            regs_res = supabase.table("course_registrations").select("course_code, cycle_id, academic_year, semester_type, registration_type").eq("usn", stu_id).execute()
                            for r in (regs_res.data or []):
                                if str(r.get('registration_type', 'REGULAR')).upper() == 'REGULAR':
                                    if r.get('cycle_id') == target_cycle_id or (r.get('academic_year') == active_ay and str(r.get('semester_type', '')).upper() == cycle_term):
                                        regular_courses.append(r['course_code'])
                            regular_courses = sorted(list(set(regular_courses)))
                        
                        results_res = supabase.table("student_results").select("course_code, grade, is_pass, cycle_id").eq("usn", stu_id).execute()
                        raw_results = results_res.data or []
                        raw_results.sort(key=lambda x: int(x.get('cycle_id') or 0), reverse=True)
                        
                        latest_res = {}
                        for r in raw_results:
                            cc = r.get('course_code')
                            if cc and cc not in latest_res:
                                latest_res[cc] = r
                                
                        failed_codes = [
                            cc for cc, r in latest_res.items()
                            if r.get('is_pass') == False or str(r.get('grade', '')).strip().upper() in ['F', 'AB', 'MP', 'NP', 'WH', 'NE']
                        ]
                        
                        eligible_arrears = []
                        if failed_codes:
                            crs_res = supabase.table("master_courses").select("course_code, title, semester_id").in_("course_code", list(set(failed_codes))).execute()
                            for c in (crs_res.data or []):
                                try:
                                    c_sem = int(c.get('semester_id', 0))
                                    if (c_sem % 2) == cycle_parity:
                                        eligible_arrears.append(c)
                                except:
                                    pass
                        
                        st.markdown("### 1. Mandatory Regular Courses")
                        if not regular_courses:
                            if str(student['status']).upper() == 'DETAINED':
                                st.info("Student is DETAINED. They are only eligible to write arrears.")
                            else:
                                st.warning("No regular courses linked to this cycle/term for this student.")
                        else:
                            for c in regular_courses:
                                st.markdown(f"- ✅ **{c}** *(Locked - Regular Semester)*")
                                
                        st.markdown(f"### 2. Select Eligible {cycle_term} Semester Arrears")
                        if not eligible_arrears:
                            st.success(f"🎉 This student has no active backlogs for {cycle_term} semesters.")
                        else:
                            selected_arrear_codes = []
                            selected_arrear_objs = []
                            for c in eligible_arrears:
                                if st.checkbox(f"Add Arrear: {c['course_code']} - {c['title']} (Sem {c['semester_id']})", key=f"arr_{c['course_code']}"):
                                    selected_arrear_codes.append(c['course_code'])
                                    selected_arrear_objs.append(c)
                                    
                            try:
                                fee_res = supabase.table("master_fees").select("*").execute()
                                fees = {f['fee_type']: float(f['amount']) for f in (fee_res.data or [])}
                            except:
                                fees = {'Exam': 2000, 'Arrear': 500}
                                
                            base = fees.get('Exam', 2000) if (str(student['status']).upper() == 'ACTIVE' and len(regular_courses) > 0) else 0
                            total_fee = base + (len(selected_arrear_codes) * fees.get('Arrear', 500))
                            
                            st.info(f"💰 **Calculated Application Fee:** ₹{total_fee}")
                            
                            utr = st.text_input("Transaction ID / UTR for Arrears:", key="arr_utr")
                            
                            if st.button("💾 Submit & Lock Arrears", type="primary", key="arr_submit_btn"):
                                try:
                                    supabase.table("exam_applications").delete().eq("cycle_id", target_cycle_id).eq("usn", stu_id).execute()
                                    
                                    app_payload = {
                                        "app_id": str(hashlib.md5(f"{stu_id}{target_cycle_id}".encode()).hexdigest()[:8]),
                                        "cycle_id": target_cycle_id,
                                        "usn": stu_id,
                                        "total_fee": total_fee,
                                        "payment_status": "PAID",
                                        "utr_number": utr.strip() if utr.strip() else "PAID-MANUAL"
                                    }
                                    supabase.table("exam_applications").insert(app_payload).execute()
                                    
                                    supabase.table("course_registrations").delete().eq("cycle_id", target_cycle_id).eq("registration_type", "ARREAR").eq("usn", stu_id).execute()
                                    
                                    if selected_arrear_objs:
                                        arr_payload = []
                                        for c_obj in selected_arrear_objs:
                                            arr_payload.append({
                                                "usn": stu_id,
                                                "course_code": c_obj['course_code'],
                                                "semester": int(c_obj.get('semester_id', 1)),
                                                "academic_year": target_cycle.get('academic_year', active_ay),
                                                "semester_type": cycle_term,
                                                "cycle_id": target_cycle_id,
                                                "registration_type": "ARREAR"
                                            })
                                        supabase.table("course_registrations").insert(arr_payload).execute()
                                        
                                    st.success("✅ Application Submitted! Arrears are officially locked to the cycle.")
                                except Exception as e:
                                    st.error(f"Error: {e}")

    # ---------------------------------------------------------
    # TAB 3: SUMMER EXAMS
    # ---------------------------------------------------------
    with tab_summer:
        try:
            cycles_res = supabase.table("exam_cycles").select("cycle_id, cycle_name, exam_type, program_type, academic_year").eq("is_active", True).eq("is_brs_active", True).eq("exam_type", "Summer").execute()
            summer_cycles = cycles_res.data if cycles_res.data else []
        except:
            summer_cycles = []

        if not summer_cycles:
            st.info("No Summer exam cycles are currently open for BRS registration.")
        else:
            sum_cycle_options = {c['cycle_name']: c for c in summer_cycles}
            selected_sum_cycle_name = st.selectbox("Select Target Exam Cycle (Summer):", options=list(sum_cycle_options.keys()), key="sum_cycle_sel")
            target_sum_cycle = sum_cycle_options[selected_sum_cycle_name]
            target_sum_cycle_id = target_sum_cycle['cycle_id']
            
            target_sum_ay = target_sum_cycle.get('academic_year', active_ay)
            
            st.subheader("☀️ Summer Semester Application")
            summer_usn = st.text_input("Enter USN for Summer Semester Processing", key="sum_usn").strip().upper()
            
            if summer_usn:
                staging_check = supabase.table("course_registration_online").select("*").eq("usn", summer_usn).eq("cycle_id", target_sum_cycle_id).execute()
                is_staged = staging_check.data and len(staging_check.data) > 0
                
                if is_staged:
                    st.success(f"✅ Student '{summer_usn}' is already registered online and is LIVE in the COE database!")
                    
                    reg_data = staging_check.data
                    current_utr = reg_data[0].get('utr_number', '') or ""
                    total_fee = reg_data[0].get('fee_amount', 0)
                    
                    col_u1, col_u2 = st.columns([2, 1])
                    new_utr = col_u1.text_input("Transaction ID / UTR", value=current_utr, key="sum_utr_upd")
                    if col_u2.button("Update UTR Record", key="sum_utr_btn"):
                        supabase.table("course_registration_online").update({"utr_number": new_utr.strip()}).eq("usn", summer_usn).eq("cycle_id", target_sum_cycle_id).execute()
                        st.success("✅ UTR successfully updated in the database!")
                        st.rerun()

                    course_codes = [r['course_code'] for r in reg_data]
                    crs_res = supabase.table("master_courses").select("course_code, title").in_("course_code", course_codes).execute()
                    c_titles = {c['course_code']: c['title'] for c in (crs_res.data or [])}
                    
                    reconstructed_courses = []
                    for r in reg_data:
                        reconstructed_courses.append({
                            'course_code': r['course_code'],
                            'course_title': c_titles.get(r['course_code'], 'Unknown'),
                            'rule': r.get('rule_category', '')
                        })
                        
                    stu_res = supabase.table("master_students").select("*").eq("usn", summer_usn).execute()
                    student = stu_res.data[0] if stu_res.data else {'usn': summer_usn}
                    
                    pdf_bytes = generate_summer_pdf(student, reconstructed_courses, total_fee, current_utr, academic_year=target_sum_ay, exam_type="Summer")
                    st.download_button("🖨️ Re-Download Application PDF", data=pdf_bytes, file_name=f"Summer_Application_{summer_usn}.pdf", mime="application/pdf", type="primary", key="sum_redl_btn")
                else:
                    stu_res = supabase.table("master_students").select("*").eq("usn", summer_usn).execute()
                    if not stu_res.data:
                        st.error("Student not found.")
                    else:
                        student = stu_res.data[0]
                        student_scheme = int(student.get('scheme_batch', 25))
                        
                        if str(student.get('status', '')).strip().upper() == 'DISCONTINUED':
                            st.error(f"❌ **Registration Blocked:** Student '{summer_usn}' is marked as DISCONTINUED.")
                        else:
                            branch_code = student.get('branch_code', '')
                            br_res = supabase.table("master_branches").select("program_type").eq("branch_code", branch_code).execute()
                            prog_type = br_res.data[0]['program_type'] if br_res.data else "UG"
                            
                            if target_sum_cycle.get('program_type', 'BOTH') not in ['BOTH', prog_type]:
                                st.error(f"❌ **Program Mismatch:** Student is {prog_type}, but cycle restricted to {target_sum_cycle.get('program_type')}.")
                            else:
                                st.success(f"**{student['full_name']}** | Branch: **{branch_code}** ({prog_type}) | Scheme: **{student_scheme}**")
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

                                    cie_threshold = 20 if prog_type == "UG" else 25 
                                    eligible_summer_courses = []
                                    
                                    for cc, r in latest_results.items():
                                        grade = str(r.get('grade', '')).upper()
                                        cie = float(r.get('cie_marks', 0))
                                        is_pass = r.get('is_pass', False)
                                        
                                        if not is_pass and grade not in ['PND', 'PENDING', 'FROZEN', '']:
                                            rule = None
                                            if cie < cie_threshold:
                                                rule = "Rule 1: Mandatory Classes (CIE Fail)"
                                            elif grade == 'AB' and cie >= cie_threshold:
                                                rule = "Rule 2: Exam Only (Absent)"
                                            elif grade in ['F', 'MP'] and cie >= cie_threshold:
                                                rule = "Rule 3: Exam Only (SEE Fail / MP)"
                                                
                                            if rule:
                                                c_info = course_info.get(cc, {'title': 'Unknown', 'sem': 0, 'credits': 4.0})
                                                eligible_summer_courses.append({"course_code": cc, "course_title": c_info['title'], "semester": c_info['sem'], "credits": c_info['credits'], "grade": grade, "cie": cie, "rule": rule})
                                                
                                    if not eligible_summer_courses:
                                        st.success("🎉 This student has no failed courses requiring summer registration!")
                                    else:
                                        st.markdown("### Select Eligible Summer Courses")
                                        selected_summer_courses = []
                                        for crs in eligible_summer_courses:
                                            if st.checkbox(f"[{crs['course_code']}] {crs['course_title']} | Grade: {crs['grade']} | Rule: {crs['rule']}", value=True, key=f"sum_chk_{crs['course_code']}"):
                                                selected_summer_courses.append(crs)
                                        
                                        if selected_summer_courses:
                                            total_fee = calculate_summer_fees(selected_summer_courses)
                                            st.info(f"💰 **Calculated Total Fee (Including Base 400):** ₹{total_fee}")
                                            
                                            rule_1_credits = sum([c['credits'] for c in selected_summer_courses if "Rule 1" in c['rule']])
                                            
                                            allow_extra_credit = st.checkbox(
                                                "🚨 **Principal/HOD Override:** Allow 1 additional credit (Max 15)", 
                                                help="Check this box if the student has special written permission to exceed the standard 14-credit limit.",
                                                key="sum_extra_cred"
                                            )
                                            max_allowed = 15 if allow_extra_credit else 14
                                            
                                            if rule_1_credits > max_allowed:
                                                st.error(f"❌ **Credit Limit Exceeded!** Selected {rule_1_credits} credits under Rule 1. Maximum allowed is **{max_allowed} Credits**.")
                                            else:
                                                st.markdown("### Payment Details")
                                                target_utr = st.text_input("Transaction ID / UTR (Optional)", help="Leave blank to write manually.", key="sum_target_utr")
                                                
                                                if st.button("💾 Submit Registration & Generate PDF", type="primary", key="sum_reg_btn"):
                                                    payload_staging = [{"cycle_id": target_sum_cycle_id, "usn": summer_usn, "course_code": c['course_code'], "semester": c['semester'], "academic_year": target_sum_ay, "semester_type": "SUMMER", "registration_type": "SUMMER", "rule_category": c['rule'], "fee_amount": total_fee, "payment_status": "PAID", "utr_number": target_utr.strip()} for c in selected_summer_courses]
                                                    payload_official = [{"cycle_id": target_sum_cycle_id, "usn": summer_usn, "course_code": c['course_code'], "semester": c['semester'], "academic_year": target_sum_ay, "semester_type": "SUMMER", "registration_type": "SUMMER"} for c in selected_summer_courses]
                                                    
                                                    try:
                                                        supabase.table("course_registration_online").delete().eq("cycle_id", target_sum_cycle_id).eq("usn", summer_usn).execute()
                                                        supabase.table("course_registrations").delete().eq("cycle_id", target_sum_cycle_id).eq("usn", summer_usn).execute()
                                                        
                                                        supabase.table("course_registration_online").insert(payload_staging).execute()
                                                        supabase.table("course_registrations").insert(payload_official).execute()
                                                        
                                                        st.success(f"✅ Application successfully registered and sent directly to the COE!")
                                                        pdf_bytes = generate_summer_pdf(student, selected_summer_courses, total_fee, target_utr, academic_year=target_sum_ay, exam_type="Summer")
                                                        st.download_button("🖨️ Download Official Application PDF", data=pdf_bytes, file_name=f"Summer_Application_{summer_usn}.pdf", mime="application/pdf", type="primary", key="sum_pdf_dl")
                                                    except Exception as e:
                                                        st.error(f"Database Error: {e}")

    # ---------------------------------------------------------
    # TAB 4: ON-DEMAND EXAMINATIONS (ODE)
    # ---------------------------------------------------------
    with tab_ode:
        try:
            all_c_res = supabase.table("exam_cycles").select("cycle_id, cycle_name, exam_type, program_type, academic_year, is_active, is_brs_active").execute()
            all_c_data = all_c_res.data or []
            ode_cycles = [
                c for c in all_c_data
                if (bool(c.get('is_active')) or bool(c.get('is_brs_active')))
                and (
                    str(c.get('exam_type', '')).strip().upper() == 'ODE'
                    or 'ODE' in str(c.get('cycle_name', '')).upper()
                    or 'ON-DEMAND' in str(c.get('cycle_name', '')).upper()
                )
            ]
            all_ode_cycle_ids = [
                c['cycle_id'] for c in all_c_data
                if str(c.get('exam_type', '')).strip().upper() == 'ODE'
                or 'ODE' in str(c.get('cycle_name', '')).upper()
                or 'ON-DEMAND' in str(c.get('cycle_name', '')).upper()
            ]
        except Exception as e:
            st.error(f"Error loading ODE cycles: {e}")
            ode_cycles, all_ode_cycle_ids = [], []

        if not ode_cycles:
            st.info("No On-Demand Examination (ODE) cycles are currently open for BRS registration.")
        else:
            ode_cycle_options = {c['cycle_name']: c for c in ode_cycles}
            selected_ode_cycle_name = st.selectbox("Select Target ODE Cycle:", options=list(ode_cycle_options.keys()), key="ode_cycle_sel")
            target_ode_cycle = ode_cycle_options[selected_ode_cycle_name]
            target_ode_cycle_id = target_ode_cycle['cycle_id']
            target_ode_ay = target_ode_cycle.get('academic_year', active_ay)

            st.subheader("⚡ On-Demand Examination (ODE) Application")
            st.caption("Fee Rules: **₹6,000 / subject** (Attempt 1) | **₹3,000 / subject** (Subsequent
