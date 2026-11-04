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

def branch_match(course_branches_str, student_branch):
    if not course_branches_str: return False
    allowed = [b.strip().upper() for b in str(course_branches_str).split(',')]
    return (student_branch in allowed) or ('COMMON' in allowed) or ('ALL' in allowed)

def match_specialization(course_spec, student_spec):
    c_spec = str(course_spec).strip().upper()
    return c_spec in ['GENERAL', 'NONE', 'NAN', '', 'NULL'] or c_spec == student_spec

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
            "Courses Registered": course_codes, "Rules Applied": rules,
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
        
        # We need the usn directly from the student record to fallback appropriately
        batch_usns = []
        for item in batch_items:
            # Prefer official USN in bulk rendering; download logic will fallback anyway
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
            
            # Fallback locally if batch_photos missed due to admission_number mapping
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
# SUMMER SEMESTER PDF GENERATOR 
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

    c_data = [["Course Code", "Course Title", "Previous Grade", "Fee (Rs)", "Apply"]]
    
    courses = sort_courses_by_sequence(courses)
    
    rule2_count = 0
    for crs in courses:
        rule = crs.get('rule', '')
        if "Rule 1" in rule: fee_str, prev_grade = "5600", "NE"
        elif "Rule 2" in rule:
            rule2_count += 1
            fee_str, prev_grade = ("2000" if rule2_count == 1 else "1000"), "AB"
        elif "Rule 3" in rule: fee_str, prev_grade = "600", "F"
        else: fee_str, prev_grade = "-", crs.get('grade', '-')

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
        "I have paid the prescribed fee." if exam_type.upper() == "SUMMER" else "I have paid my regular tuition fees."
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

    if exam_type.upper() == "SUMMER":
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
        payment_type = st.selectbox("Fee Type", ["Exam Fee", "Tuition Fee", "Fine", "Revaluation fee", "Convocation fees", "Arrears fees", "Summer Semester Fee", "Other"], key="clerk_type")
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
# VIEW 2: DEPARTMENT PORTAL (DUAL-WRITE ENABLED)
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

    tab_reg, tab_app, tab_summer, tab_reports = st.tabs(["📝 Phase 1: Course Registration", "🎓 Phase 2: Exam Applications", "☀️ Summer Exams", "📊 Reports"])
    
    # ---------------------------------------------------------
    # TAB 1: REGULAR COURSE REGISTRATION
    # ---------------------------------------------------------
    with tab_reg:
        st.info(f"📅 **Active Academic Term:** {active_ay} | **{active_term}** Semester")
        st.write("Register students for classes at the start of the semester.")
        
        entry_mode = st.radio("Registration Mode:", ["🚀 Bulk Branch Auto-Registration", "👤 Single Student Entry"], horizontal=True, key="reg_mode")
        st.divider()
        
        if entry_mode == "🚀 Bulk Branch Auto-Registration":
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
                    with st.spinner("Analyzing curriculum..."):
                        stu_res = supabase.table("master_students").select("usn, admission_number, status, full_name, branch_code, student_category, specialization").eq("branch_code", b_branch).eq("current_sem", str(b_sem)).eq("scheme_batch", str(b_scheme)).execute()
                        valid_stu = [s for s in (stu_res.data or []) if str(s.get('status', '')).strip().upper() == 'ACTIVE']
                        
                        courses_res = supabase.table("master_courses").select("*").eq("semester_id", str(b_sem)).eq("scheme_batch", str(b_scheme)).execute()
                        all_courses = courses_res.data if courses_res.data else []
                        all_core_courses = [c for c in all_courses if branch_match(c.get('branch_code', ''), b_branch) and c.get('course_type', 'CORE') in ['CORE', 'LAB', 'NON CREDIT', 'PROJECT', 'OMR']]
                        pe_courses = [c for c in all_courses if branch_match(c.get('branch_code', ''), b_branch) and c.get('course_type') == 'PE']
                        
                        if not all_core_courses:
                            st.warning(f"No Core courses mapped to {b_branch} for Semester {b_sem}.")
                        elif not valid_stu:
                            st.warning(f"No active students match this criteria.")
                        else:
                            st.success(f"👥 Ready to process **{len(valid_stu)} Students** for {b_branch}.")
                            
                            if not pe_courses:
                                if st.button(f"🚀 Auto-Register {len(valid_stu)} Students", type="primary"):
                                    payload_official = []
                                    processed_ids = []
                                    for s in valid_stu:
                                        active_id = s.get('usn') if pd.notna(s.get('usn')) and s.get('usn') != '' else s.get('admission_number')
                                        stu_spec = str(s.get('specialization', 'GENERAL')).strip().upper()
                                        processed_ids.append(active_id)
                                        
                                        student_assigned_courses = [c for c in all_core_courses if match_specialization(c.get('specialization'), stu_spec)]
                                        for c in student_assigned_courses:
                                            payload_official.append({"usn": active_id, "course_code": c['course_code'], "semester": b_sem, "academic_year": active_ay, "semester_type": active_term, "registration_type": "REGULAR"})
                                            
                                    with st.spinner(f"☁️ Syncing records..."):
                                        try:
                                            for i in range(0, len(processed_ids), 200):
                                                supabase.table("course_registrations").delete().eq("academic_year", active_ay).eq("semester_type", active_term).in_("usn", processed_ids[i:i+200]).execute()
                                            for i in range(0, len(payload_official), 1000):
                                                supabase.table("course_registrations").insert(payload_official[i:i+1000]).execute()
                                            st.success(f"✅ Cloud Sync Complete! Registered {len(valid_stu)} students.")
                                        except Exception as e:
                                            st.error(f"Error: {e}")
                            else:
                                st.warning("Electives detected! You must upload a CSV mapping for registration.")

        elif entry_mode == "👤 Single Student Entry":
            st.info("Single student course registration is managed via CSV bulk upload if electives are present.")

    # ---------------------------------------------------------
    # TAB 2: EXAM APPLICATIONS (Pre-Exam Eligibility & Arrears)
    # ---------------------------------------------------------
    with tab_app:
        try:
            cycles_res = supabase.table("exam_cycles").select("*").eq("is_active", True).eq("is_brs_active", True).execute()
            active_cycles = [c for c in cycles_res.data if str(c.get('exam_type', '')).strip().upper() != 'SUMMER']
        except: active_cycles = []
        
        if not active_cycles:
            st.warning("No Regular Exam Cycles are currently open for applications.")
        else:
            c_opts = {c['cycle_name']: c for c in active_cycles}
            sel_cycle_name = st.selectbox("Select Target Exam Cycle:", list(c_opts.keys()))
            target_cycle = c_opts[sel_cycle_name]
            target_cycle_id = target_cycle['cycle_id']
            
            # 🟢 Determine Cycle Parity (1 for ODD, 0 for EVEN)
            cycle_parity = 1 if active_term.upper() == 'ODD' else 0
            
            st.info(f"The Exam Application establishes financial eligibility and locks the student into the {sel_cycle_name} cycle.")
            
            app_mode = st.radio("Application Mode:", ["🚀 Bulk Initialize Regular Exam Applications", "👤 Process Arrear Student (Custom)"], horizontal=True)
            st.divider()
            
            if app_mode == "🚀 Bulk Initialize Regular Exam Applications":
                st.write("This engine initializes the `exam_applications` row for **ALL** active regular students in the selected branch, saving their Base Exam Fee as Paid. This allows the COE to bulk print their forms so they can manually tick their arrears.")
                
                col_a1, col_a2 = st.columns(2)
                try:
                    branches_data = supabase.table("master_branches").select("branch_code").execute().data
                    branch_list = [b['branch_code'] for b in branches_data if str(b['branch_code']).upper() != 'COMMON']
                except: branch_list = []
                
                a_branch = col_a1.selectbox("Select Branch:", ["-- Select --"] + branch_list, key="app_branch")
                a_sem = col_a2.number_input("Target Semester:", min_value=1, max_value=10, value=1, key="app_sem")
                
                if a_branch != "-- Select --" and st.button("🚀 Bulk Initialize Applications", type="primary"):
                    with st.spinner("Initializing applications for all active students..."):
                        
                        # 1. Fetch Active Students
                        stu_res = supabase.table("master_students").select("usn, status").eq("branch_code", a_branch).eq("current_sem", str(a_sem)).execute()
                        active_usns = [s['usn'] for s in (stu_res.data or []) if str(s.get('status', 'ACTIVE')).upper() == 'ACTIVE']
                        
                        if not active_usns:
                            st.error("No active students found in this branch/semester.")
                        else:
                            # 2. Check who registered for regular courses
                            regs_res = supabase.table("course_registrations").select("usn").eq("academic_year", active_ay).eq("semester_type", active_term).in_("usn", active_usns).execute()
                            registered_usns = list(set([r['usn'] for r in (regs_res.data or [])]))
                            
                            if not registered_usns:
                                st.warning("None of these students have course registrations for the active term yet. Please complete Phase 1 first.")
                            else:
                                try:
                                    fee_res = supabase.table("master_fees").select("amount").eq("fee_type", "Exam").execute()
                                    base_fee = float(fee_res.data[0]['amount']) if fee_res.data else 2000.0
                                except: base_fee = 2000.0
                                
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
                                        supabase.table("exam_applications").delete().eq("cycle_id", target_cycle_id).in_("usn", registered_usns[i:i+200]).execute()
                                    for i in range(0, len(payload), 500):
                                        supabase.table("exam_applications").insert(payload[i:i+500]).execute()
                                        
                                    st.success(f"✅ Success! Base Applications generated for {len(registered_usns)} students. The COE can now bulk print their forms.")
                                except Exception as e:
                                    st.error(f"Error initializing applications: {e}")

            elif app_mode == "👤 Process Arrear Student (Custom)":
                st.write("Use this desk to collect arrear fees. You can search for ACTIVE students, as well as DETAINED students who need to clear their backlogs.")
                app_usn = st.text_input("Enter Student USN:", key="arr_usn").strip().upper()
                
                if app_usn:
                    stu_res = supabase.table("master_students").select("*").eq("usn", app_usn).execute()
                    if not stu_res.data:
                        st.error("Student not found.")
                    else:
                        student = stu_res.data[0]
                        st.success(f"**{student['full_name']}** | Branch: {student['branch_code']} | Sem: {student['current_sem']} | Status: {student['status']}")
                        
                        # Fetch Regular Courses (if they are ACTIVE)
                        regular_courses = []
                        if str(student['status']).upper() == 'ACTIVE':
                            regs_res = supabase.table("course_registrations").select("course_code").eq("academic_year", active_ay).eq("semester_type", active_term).eq("usn", app_usn).execute()
                            regular_courses = [r['course_code'] for r in (regs_res.data or [])]
                        
                        # 🟢 ODD/EVEN Parity Arrear Filtering
                        results_res = supabase.table("student_results").select("course_code, grade, is_pass").eq("usn", app_usn).execute()
                        failed_codes = [r['course_code'] for r in (results_res.data or []) if r.get('is_pass') == False or str(r.get('grade')).strip().upper() in ['F', 'AB', 'MP', 'NP', 'WH']]
                        
                        eligible_arrears = []
                        if failed_codes:
                            crs_res = supabase.table("master_courses").select("course_code, title, semester_id").in_("course_code", list(set(failed_codes))).execute()
                            for c in (crs_res.data or []):
                                c_sem = int(c.get('semester_id', 0))
                                # Only show arrears matching the current cycle's parity
                                if (c_sem % 2) == cycle_parity:
                                    eligible_arrears.append(c)
                        
                        st.markdown("### 1. Mandatory Regular Courses")
                        if not regular_courses:
                            if str(student['status']).upper() == 'DETAINED':
                                st.info("Student is DETAINED. They are only eligible to write arrears.")
                            else:
                                st.warning("This student is not registered for any regular courses this term.")
                        else:
                            for c in regular_courses:
                                st.markdown(f"- ✅ **{c}** *(Locked - Regular Semester)*")
                                
                        st.markdown(f"### 2. Select Eligible {active_term} Arrears")
                        if not eligible_arrears:
                            st.success(f"🎉 This student has no active backlogs for {active_term} semesters.")
                        else:
                            selected_arrear_codes = []
                            for c in eligible_arrears:
                                if st.checkbox(f"Add Arrear: {c['course_code']} - {c['title']} (Sem {c['semester_id']})", key=f"arr_{c['course_code']}"):
                                    selected_arrear_codes.append(c['course_code'])
                                    
                            try:
                                fee_res = supabase.table("master_fees").select("*").execute()
                                fees = {f['fee_type']: float(f['amount']) for f in fee_res.data}
                            except: fees = {'Exam': 2000, 'Arrear': 500}
                                
                            # If detained, they only pay Arrear fees. If active, they pay Base + Arrears.
                            base = fees.get('Exam', 2000) if str(student['status']).upper() == 'ACTIVE' else 0
                            total_fee = base + (len(selected_arrear_codes) * fees.get('Arrear', 500))
                            
                            st.info(f"💰 **Calculated Application Fee:** ₹{total_fee}")
                            
                            utr = st.text_input("Transaction ID / UTR for Arrears:")
                            
                            if st.button("💾 Submit & Lock Arrears", type="primary"):
                                try:
                                    supabase.table("exam_applications").delete().eq("cycle_id", target_cycle_id).eq("usn", app_usn).execute()
                                    
                                    app_payload = {
                                        "app_id": str(hashlib.md5(f"{app_usn}{target_cycle_id}".encode()).hexdigest()[:8]),
                                        "cycle_id": target_cycle_id,
                                        "usn": app_usn,
                                        "total_fee": total_fee,
                                        "payment_status": "PAID",
                                        "utr_number": utr.strip() if utr.strip() else "PAID-MANUAL"
                                    }
                                    supabase.table("exam_applications").insert(app_payload).execute()
                                    
                                    if selected_arrear_codes:
                                        supabase.table("course_registrations").delete().eq("cycle_id", target_cycle_id).eq("registration_type", "ARREAR").eq("usn", app_usn).execute()
                                        
                                        arr_payload = []
                                        for cc in selected_arrear_codes:
                                            arr_payload.append({
                                                "usn": app_usn,
                                                "course_code": cc,
                                                "academic_year": active_ay,
                                                "semester_type": active_term,
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

    # --- DEPARTMENT SUMMER FEE REPORT ---
    with tab_reports:
        st.subheader("📊 Summer Semester Registration & Fee Report")
        st.info("Download a consolidated list of students who have applied for Summer Semester courses through the portal, including their fee amounts and transaction IDs.")
        
        try:
            cycles_res = supabase.table("exam_cycles").select("cycle_id, cycle_name").eq("exam_type", "Summer").execute()
            summer_cycles = cycles_res.data if cycles_res.data else []
        except:
            summer_cycles = []
            
        if not summer_cycles:
            st.warning("No Summer Exam Cycles found in the database.")
        else:
            try:
                branches_data = supabase.table("master_branches").select("branch_code").execute().data
                branch_list = [b['branch_code'] for b in branches_data if str(b['branch_code']).upper() != 'COMMON']
            except: 
                branch_list = []
                
            col_r1, col_r2 = st.columns(2)
            cycle_opts = {c['cycle_name']: c['cycle_id'] for c in summer_cycles}
            sel_cycle_name = col_r1.selectbox("Select Target Exam Cycle:", list(cycle_opts.keys()), key="dept_rep_cycle")
            sel_branch = col_r2.selectbox("Filter by Branch:", ["-- All Branches --"] + branch_list, key="dept_rep_branch")
            
            if st.button("📥 Generate Department Report", type="primary", key="dept_rep_btn"):
                with st.spinner("Compiling fee collection data..."):
                    df_report = generate_summer_fee_report(cycle_opts[sel_cycle_name], branch_code=sel_branch if sel_branch != "-- All Branches --" else None)
                    
                    if df_report is not None and not df_report.empty:
                        st.success(f"✅ Found {len(df_report)} student records.")
                        st.dataframe(df_report, use_container_width=True, hide_index=True)
                        
                        csv_data = df_report.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label="⬇️ Download CSV Report", 
                            data=csv_data, 
                            file_name=f"Summer_Fee_Report_{sel_branch}_{sel_cycle_name}.csv", 
                            mime="text/csv",
                            type="primary",
                            key="dept_rep_dl"
                        )
                    else:
                        st.warning("No online summer registrations found for the selected criteria.")

# ==========================================
# VIEW 3: ADMIN DASHBOARD (SCRUTINY & EXPORT)
# ==========================================
def admin_dashboard():
    st.title("📊 Admin Consolidation Panel")
    tab1, tab2, tab3 = st.tabs(["📥 Download Cash Book", "👥 Manage Users", "☀️ Summer Fee Reports"])
    
    with tab1:
        c1, c2 = st.columns(2)
        start_date = c1.date_input("From Date", date.today().replace(day=1))
        end_date = c2.date_input("To Date", date.today())
        if st.button("Fetch Data", key="admin_fetch"):
            res = supabase.table("cash_receipts").select("*").gte("payment_date", str(start_date)).lte("payment_date", str(end_date)).execute()
            if res.data:
                df = pd.DataFrame(res.data)
                st.dataframe(df, use_container_width=True)
                st.download_button("⬇️ Download CSV", df.to_csv(index=False).encode('utf-8'), f"CashBook_{start_date}_to_{end_date}.csv", "text/csv", key="admin_dl_cb")
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
                
    with tab3:
        st.subheader("Download Institutional Summer Fee Report")
        st.info("Generates a master list of all summer semester registrations across all branches.")
        try:
            cycles_res = supabase.table("exam_cycles").select("cycle_id, cycle_name").eq("exam_type", "Summer").execute()
            summer_cycles = cycles_res.data if cycles_res.data else []
        except:
            summer_cycles = []
            
        if not summer_cycles:
            st.warning("No Summer Exam Cycles found in the database.")
        else:
            cycle_opts = {c['cycle_name']: c['cycle_id'] for c in summer_cycles}
            sel_cycle_name = st.selectbox("Select Summer Cycle:", list(cycle_opts.keys()), key="admin_rep_cycle")
            
            if st.button("📥 Generate Institutional Report", type="primary", key="admin_rep_btn"):
                with st.spinner("Compiling institutional fee collection data..."):
                    df_report = generate_summer_fee_report(cycle_opts[sel_cycle_name])
                    
                    if df_report is not None and not df_report.empty:
                        df_report["Total Fee Payable (Rs)"] = pd.to_numeric(df_report["Total Fee Payable (Rs)"], errors="coerce").fillna(0)
                        total_collected = df_report["Total Fee Payable (Rs)"].sum()
                        
                        c1, c2 = st.columns(2)
                        c1.metric("Total Students Registered", len(df_report))
                        c2.metric("Estimated Fee Collection", f"₹ {total_collected:,.2f}")
                        
                        st.dataframe(df_report, use_container_width=True, hide_index=True)
                        
                        csv_data = df_report.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label="⬇️ Download Full Report (CSV)", 
                            data=csv_data, 
                            file_name=f"Institutional_Summer_Fees_{sel_cycle_name}.csv", 
                            mime="text/csv",
                            type="primary",
                            key="admin_rep_dl"
                        )
                    else:
                        st.warning("No online summer registrations found for this cycle.")

# ==========================================
# MAIN ROUTING LOGIC
# ==========================================
def main():
    if st.session_state.logged_in:
        with st.sidebar:
            st.markdown(f"**User:** {st.session_state.username}")
            st.markdown(f"**Role:** {st.session_state.role.capitalize()}")
            if st.button("Logout", use_container_width=True, key="sidebar_logout"):
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
