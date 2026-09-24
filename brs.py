import streamlit as st
import pandas as pd
import hashlib
import calendar
import io
import re
import qrcode
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
# HELPER FUNCTIONS
# ==========================================
def format_branch_name(branch_code):
    branch_map = {"CS": "CSE", "CI": "CSE-AIML", "CD": "CSE-DS", "AI": "AIML", "EC": "ECE", "EE": "EEE", "CV": "Civil", "ME": "ME", "AE": "AE"}
    return branch_map.get(str(branch_code).strip().upper(), str(branch_code).strip().upper())

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
    formatted_branch = format_branch_name(student.get('branch_code', ''))

    p_style = getSampleStyleSheet()['Normal']
    p_style.alignment = 1 
    p_style.fontSize = 8
    
    photo_io = get_student_photo(display_id)
    if photo_io:
        photo_io.seek(0)
        p_img = RLImage(photo_io, width=55, height=70)
        p_img.hAlign = 'CENTER'
        p_img.vAlign = 'MIDDLE'
        digital_col = p_img
    else:
        qr = qrcode.make(PHOTO_BOOTH_URL)
        qr_io = io.BytesIO()
        qr.save(qr_io, format="PNG")
        qr_io.seek(0)
        p_img = RLImage(qr_io, width=55, height=55)
        p_img.hAlign = 'CENTER'
        p_img.vAlign = 'MIDDLE'
        # 🟢 PIN ADDED TO PDF
        pin = student.get('photo_pin', 'XXXX')
        digital_col = [p_img, Paragraph(f"Scan to Upload<br/>PIN: <b>{pin}</b>", p_style)]

    physical_col = Paragraph("<br/><br/><br/>Affix Physical<br/>Photo Here", p_style)

    s_data = [
        ["USN / Admin No.", "Student Name", "Branch", "Type", "Digital Photo", "Physical Photo"],
        [display_id, student.get('full_name',''), formatted_branch, "UG", digital_col, physical_col]
    ]
    
    style_cmds = [
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]

    t1 = Table(s_data, colWidths=[85, 145, 55, 40, 100, 100], rowHeights=[20, 90])
    t1.setStyle(TableStyle(style_cmds))
    t1.wrapOn(c, w, h)
    _, t1_h = t1.wrap(w, h)
    t1.drawOn(c, margin, y - t1_h)
    y -= (t1_h + 20)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, f"Semester: {current_sem}")
    y -= 15

    c.drawString(margin, y, "Courses offered")
    y -= 5

    c_data = [["Course code", "Course title", "Credits", "Select"]]
    total_credits = 0.0
    
    courses = sort_courses_by_sequence(courses)
    
    for crs in courses:
        cred = float(crs.get('credits', 0))
        total_credits += cred
        c_data.append([
            crs.get('course_code', ''), 
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
    y -= (decl_h + 30)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, f"Date: {date.today().strftime('%d-%m-%Y')}")
    c.drawRightString(w - margin, y, "Signature of the Student")
    
    c.save()
    return buf.getvalue()


def generate_regular_pdf_bulk(student_course_list, academic_year="2026-27", term="ODD", current_sem=1):
    PHOTO_BOOTH_URL = "https://amceducationphotobhoot.streamlit.app/"
    
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin = 35

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

    for item in student_course_list:
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
        formatted_branch = format_branch_name(student.get('branch_code', ''))

        p_style = getSampleStyleSheet()['Normal']
        p_style.alignment = 1 
        p_style.fontSize = 8
        
        photo_io = get_student_photo(display_id)
        if photo_io:
            photo_io.seek(0)
            p_img = RLImage(photo_io, width=55, height=70)
            p_img.hAlign = 'CENTER'
            p_img.vAlign = 'MIDDLE'
            digital_col = p_img
        else:
            p_img = RLImage(io.BytesIO(cached_qr_bytes), width=55, height=55)
            p_img.hAlign = 'CENTER'
            p_img.vAlign = 'MIDDLE'
            # 🟢 PIN ADDED TO PDF
            pin = student.get('photo_pin', 'XXXX')
            digital_col = [p_img, Paragraph(f"Scan to Upload<br/>PIN: <b>{pin}</b>", p_style)]

        physical_col = Paragraph("<br/><br/><br/>Affix Physical<br/>Photo Here", p_style)

        s_data = [
            ["USN / Admin No.", "Student Name", "Branch", "Type", "Digital Photo", "Physical Photo"],
            [display_id, student.get('full_name',''), formatted_branch, "UG", digital_col, physical_col]
        ]
        
        style_cmds = [
            ('GRID', (0,0), (-1,-1), 0.5, colors.black),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
        ]

        t1 = Table(s_data, colWidths=[85, 145, 55, 40, 100, 100], rowHeights=[20, 90])
        t1.setStyle(TableStyle(style_cmds))
        t1.wrapOn(c, w, h)
        _, t1_h = t1.wrap(w, h)
        t1.drawOn(c, margin, y - t1_h)
        y -= (t1_h + 20)

        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin, y, f"Semester: {current_sem}")
        y -= 15

        c.drawString(margin, y, "Courses offered")
        y -= 5

        c_data = [["Course code", "Course title", "Credits", "Select"]]
        total_credits = 0.0
        
        courses = sort_courses_by_sequence(courses)
        
        for crs in courses:
            cred = float(crs.get('credits', 0))
            total_credits += cred
            c_data.append([
                crs.get('course_code', ''), 
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
        y -= (decl_h + 30)

        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin, y, f"Date: {date.today().strftime('%d-%m-%Y')}")
        c.drawRightString(w - margin, y, "Signature of the Student")
        
        c.showPage()
        
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

    p_style = getSampleStyleSheet()['Normal']
    p_style.alignment = 1 
    p_style.fontSize = 8
    
    photo_io = get_student_photo(display_id)
    if photo_io:
        photo_io.seek(0)
        p_img = RLImage(photo_io, width=55, height=70)
        p_img.hAlign = 'CENTER'
        p_img.vAlign = 'MIDDLE'
        digital_col = p_img
    else:
        qr = qrcode.make(PHOTO_BOOTH_URL)
        qr_io = io.BytesIO()
        qr.save(qr_io, format="PNG")
        qr_io.seek(0)
        p_img = RLImage(qr_io, width=55, height=55)
        p_img.hAlign = 'CENTER'
        p_img.vAlign = 'MIDDLE'
        # 🟢 PIN ADDED TO PDF
        pin = student.get('photo_pin', 'XXXX')
        digital_col = [p_img, Paragraph(f"Scan to Upload<br/>PIN: <b>{pin}</b>", p_style)]

    physical_col = Paragraph("<br/><br/><br/>Affix Physical<br/>Photo Here", p_style)

    s_data = [
        ["USN / Admin No.", "Student Name", "Branch", "Type", "Digital Photo", "Physical Photo"],
        [display_id, student.get('full_name',''), formatted_branch, "UG", digital_col, physical_col]
    ]
    
    style_cmds = [
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]

    t1 = Table(s_data, colWidths=[85, 145, 55, 40, 100, 100], rowHeights=[20, 90])
    t1.setStyle(TableStyle(style_cmds))
    t1.wrapOn(c, w, h)
    _, t1_h = t1.wrap(w, h)
    t1.drawOn(c, margin, y - t1_h)
    y -= (t1_h + 20)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "Semester Details")
    c.drawRightString(w - margin, y, f"Registration Date: {date.today().strftime('%d-%m-%Y')}")
    y -= 20

    c.drawString(margin, y, "Courses Registered")
    y -= 5

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

        c_data.append([
            crs['course_code'], 
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
        payment_mode = st.selectbox("Payment Mode", ["UPI (QR / App)", "Bank Transfer (NEFT / RTGS)", "SBI Direct"])
        utr = st.text_input("Transaction ID / UTR No.")
        college_account = st.text_input("Credited To A/C (Bank details)")
        
    if st.button("💾 Save & Sync Receipt", type="primary", use_container_width=True):
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

    tab_reg, tab_summer, tab_reports = st.tabs(["📝 Regular Course Registration", "☀️ Summer Exam Registration", "📊 Summer Fee Reports"])
    
    # --- REGULAR REGISTRATION ---
    with tab_reg:
        st.info(f"📅 **Active Academic Term:** {active_ay} | **{active_term}** Semester")
        
        entry_mode = st.radio("Registration Mode:", ["👤 Single Student Entry", "🚀 Bulk Branch Auto-Registration"], horizontal=True)
        st.divider()
        
        if entry_mode == "👤 Single Student Entry":
            target_id = st.text_input("Enter Student USN or Admission Number").strip().upper()
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
                        st.download_button("🖨️ Re-Download Application PDF", data=pdf_bytes, file_name=f"Regular_Application_{active_usn_or_admin}.pdf", mime="application/pdf", type="primary")
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
                                
                                st.success(f"**{stu['full_name']}** | Branch: **{branch_code}** ({prog_type}) | Sem: **{current_sem}** | Scheme: **{student_scheme}**")
                                
                                courses_res = supabase.table("master_courses").select("*").execute()
                                all_courses = courses_res.data if courses_res.data else []
                                
                                core_courses = [c for c in all_courses if c.get('semester_id') == current_sem and branch_match(c.get('branch_code', ''), branch_code) and c.get('course_type', 'CORE') == 'CORE' and int(c.get('scheme_batch', 25)) == student_scheme]
                                pe_courses = [c for c in all_courses if c.get('semester_id') == current_sem and branch_match(c.get('branch_code', ''), branch_code) and c.get('course_type') == 'PE' and int(c.get('scheme_batch', 25)) == student_scheme]
                                oe_courses = [c for c in all_courses if c.get('semester_id') == current_sem and not branch_match(c.get('branch_code', ''), branch_code) and c.get('course_type') == 'OE' and int(c.get('scheme_batch', 25)) == student_scheme]
                                
                                selected_codes, total_credits = [], 0.0
                                
                                st.markdown("### 1. Mandatory Core Courses")
                                if not core_courses:
                                    st.warning("No core courses found for this semester.")
                                for core in core_courses:
                                    st.markdown(f"- ✅ **{core['course_code']}** - {core['title']} *(Auto-assigned)*")
                                    selected_codes.append(core['course_code'])
                                    total_credits += float(core.get('credits', 4))
                                    
                                if pe_courses or oe_courses:
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
                                        
                                st.info(f"📊 **Total Credits Selected:** {total_credits}")
                                
                                if st.button("💾 Submit Registration & Generate PDF", type="primary"):
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
                                            st.download_button("🖨️ Download Official Application PDF", data=pdf_bytes, file_name=f"Regular_Application_{active_usn_or_admin}.pdf", mime="application/pdf", type="primary")
                                        except Exception as e:
                                            st.error(f"Database Error: {e}")

        # 🟢 BULK AUTO-REGISTRATION MODE
        elif entry_mode == "🚀 Bulk Branch Auto-Registration":
            st.markdown("### Bulk Class Registration Engine")
            col_b1, col_b2, col_b3 = st.columns(3)
            
            try:
                branches_data = supabase.table("master_branches").select("branch_code").execute().data
                branch_list = [b['branch_code'] for b in branches_data if str(b['branch_code']).upper() != 'COMMON']
            except: branch_list = []
            
            b_branch = col_b1.selectbox("Select Branch", ["-- Select --"] + branch_list)
            b_sem = col_b2.number_input("Target Semester", min_value=1, max_value=10, value=1)
            b_scheme = col_b3.number_input("Scheme Batch (e.g., 25)", value=25)
            
            if b_branch != "-- Select --":
                is_odd_sem = (b_sem % 2 != 0)
                is_active_odd = (active_term.upper() == 'ODD')
                
                if is_odd_sem != is_active_odd:
                    st.error(f"❌ **Term Mismatch:** Cannot bulk register students into Semester {b_sem} during an {active_term} term.")
                else:
                    with st.spinner("Analyzing curriculum and active students..."):
                        stu_res = supabase.table("master_students").select("usn, admission_number, status, full_name, branch_code, photo_pin").eq("branch_code", b_branch).eq("current_sem", str(b_sem)).eq("scheme_batch", str(b_scheme)).execute()
                        valid_stu = [s for s in (stu_res.data or []) if str(s.get('status', '')).strip().upper() == 'ACTIVE']
                        
                        courses_res = supabase.table("master_courses").select("*").eq("semester_id", str(b_sem)).eq("scheme_batch", str(b_scheme)).execute()
                        all_courses = courses_res.data if courses_res.data else []
                        
                        core_courses = [c for c in all_courses if branch_match(c.get('branch_code', ''), b_branch) and c.get('course_type', 'CORE') == 'CORE']
                        pe_courses = [c for c in all_courses if branch_match(c.get('branch_code', ''), b_branch) and c.get('course_type') == 'PE']
                        oe_courses = [c for c in all_courses if not branch_match(c.get('branch_code', ''), b_branch) and c.get('course_type') == 'OE']
                        
                        if not valid_stu:
                            st.warning(f"No ACTIVE students found in {b_branch} Semester {b_sem} (Scheme {b_scheme}).")
                        elif not core_courses:
                            st.warning(f"No Core courses mapped to {b_branch} for Semester {b_sem}. Please update Master Courses.")
                        else:
                            st.info(f"👥 Found **{len(valid_stu)} Active Students** eligible for registration.")
                            
                            active_ids = [s.get('usn') if pd.notna(s.get('usn')) and s.get('usn') != '' else s.get('admission_number') for s in valid_stu]
                            staging_check = supabase.table("course_registration_online").select("usn, course_code").eq("academic_year", active_ay).eq("semester", b_sem).eq("registration_type", "REGULAR").in_("usn", active_ids).execute()
                            registered_data = staging_check.data or []
                            registered_usns = list(set([r['usn'] for r in registered_data]))
                            
                            if registered_usns:
                                st.success(f"✅ {len(registered_usns)} students in this batch already have active registrations.")
                                if st.button("🖨️ Re-Download Master PDF (Already Registered Students)", type="secondary"):
                                    with st.spinner("Reconstructing applications from database..."):
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
                                                
                                        pdf_bytes = generate_regular_pdf_bulk(student_course_payload, academic_year=active_ay, term=active_term, current_sem=b_sem)
                                        st.download_button("📥 Click Here to Save Master PDF", data=pdf_bytes, file_name=f"Bulk_ReDownload_{b_branch}_Sem{b_sem}.pdf", mime="application/pdf", type="primary")
                                st.divider()
                            
                            if not pe_courses and not oe_courses:
                                st.success("🌟 **Pure Core Semester Detected!** There are no electives for this batch. All students take the exact same courses.")
                                st.markdown("**Courses to be registered:**")
                                for c in core_courses:
                                    st.markdown(f"- {c['course_code']} - {c['title']}")
                                
                                if st.button(f"🚀 One-Click Auto-Register All {len(valid_stu)} Students", type="primary"):
                                    with st.spinner("Processing massive dual-write insertion..."):
                                        payload_staging, payload_official, student_course_payload = [], [], []
                                        processed_ids = []
                                        
                                        for s in valid_stu:
                                            active_id = s.get('usn') if pd.notna(s.get('usn')) and s.get('usn') != '' else s.get('admission_number')
                                            processed_ids.append(active_id)
                                            
                                            pdf_courses = []
                                            for c in core_courses:
                                                payload_staging.append({"usn": active_id, "course_code": c['course_code'], "semester": b_sem, "academic_year": active_ay, "semester_type": active_term, "registration_type": "REGULAR", "rule_category": "", "fee_amount": 0, "payment_status": "PAID", "utr_number": ""})
                                                payload_official.append({"usn": active_id, "course_code": c['course_code'], "semester": b_sem, "academic_year": active_ay, "semester_type": active_term, "registration_type": "REGULAR"})
                                                pdf_courses.append({"course_code": c['course_code'], "course_title": c['title'], "credits": float(c.get('credits', 0))})
                                                
                                            student_course_payload.append({'student': s, 'courses': pdf_courses})
                                        
                                        try:
                                            for i in range(0, len(processed_ids), 50):
                                                supabase.table("course_registration_online").delete().eq("academic_year", active_ay).eq("semester_type", active_term).eq("registration_type", "REGULAR").in_("usn", processed_ids[i:i+50]).execute()
                                                supabase.table("course_registrations").delete().eq("academic_year", active_ay).eq("semester_type", active_term).in_("usn", processed_ids[i:i+50]).execute()
                                            
                                            for i in range(0, len(payload_staging), 500):
                                                supabase.table("course_registration_online").insert(payload_staging[i:i+500]).execute()
                                                supabase.table("course_registrations").insert(payload_official[i:i+500]).execute()
                                                
                                            st.success(f"✅ Successfully registered {len(valid_stu)} students for {len(core_courses)} courses each!")
                                            
                                            pdf_bytes = generate_regular_pdf_bulk(student_course_payload, academic_year=active_ay, term=active_term, current_sem=b_sem)
                                            st.download_button("📥 Download Master PDF (All Students)", data=pdf_bytes, file_name=f"Bulk_Applications_{b_branch}_Sem{b_sem}.pdf", mime="application/pdf", type="primary")

                                        except Exception as e:
                                            st.error(f"Bulk Registration Error: {e}")
                            
                            else:
                                st.warning(f"⚠️ **Electives Detected.** This semester has {len(pe_courses)} PE and {len(oe_courses)} OE options. You must upload a CSV mapping each student to their chosen courses.")
                                
                                st.markdown("Please upload a CSV containing only **`usn`** (or admission number) and **`course_code`**. (Include both core and elective codes for each student).")
                                b_csv = st.file_uploader("Upload Department CSV", type="csv")
                                
                                if b_csv and st.button("🚀 Process Bulk CSV Upload", type="primary"):
                                    df = pd.read_csv(b_csv)
                                    df.columns = [c.strip().lower() for c in df.columns]
                                    
                                    if 'usn' not in df.columns or 'course_code' not in df.columns:
                                        st.error("CSV must contain 'usn' and 'course_code' columns.")
                                    else:
                                        with st.spinner("Processing CSV Dual-Write..."):
                                            payload_staging, payload_official = [], []
                                            csv_ids = list(set(df['usn'].dropna().astype(str).str.strip().str.upper()))
                                            
                                            for _, row in df.iterrows():
                                                u = str(row['usn']).strip().upper()
                                                cc = str(row['course_code']).strip().upper()
                                                if u and cc:
                                                    payload_staging.append({"usn": u, "course_code": cc, "semester": b_sem, "academic_year": active_ay, "semester_type": active_term, "registration_type": "REGULAR", "rule_category": "", "fee_amount": 0, "payment_status": "PAID", "utr_number": ""})
                                                    payload_official.append({"usn": u, "course_code": cc, "semester": b_sem, "academic_year": active_ay, "semester_type": active_term, "registration_type": "REGULAR"})
                                            
                                            try:
                                                for i in range(0, len(csv_ids), 50):
                                                    supabase.table("course_registration_online").delete().eq("academic_year", active_ay).eq("semester_type", active_term).eq("registration_type", "REGULAR").in_("usn", csv_ids[i:i+50]).execute()
                                                    supabase.table("course_registrations").delete().eq("academic_year", active_ay).eq("semester_type", active_term).in_("usn", csv_ids[i:i+50]).execute()
                                                
                                                for i in range(0, len(payload_staging), 500):
                                                    supabase.table("course_registration_online").insert(payload_staging[i:i+500]).execute()
                                                    supabase.table("course_registrations").insert(payload_official[i:i+500]).execute()
                                                    
                                                st.success(f"✅ Successfully processed CSV and registered {len(csv_ids)} students!")
                                                
                                                st_res = supabase.table("master_students").select("usn, admission_number, full_name, branch_code, photo_pin").in_("usn", csv_ids).execute()
                                                found_usns = [s['usn'] for s in st_res.data]
                                                missing = [x for x in csv_ids if x not in found_usns]
                                                if missing:
                                                    st_res_adm = supabase.table("master_students").select("usn, admission_number, full_name, branch_code, photo_pin").in_("admission_number", missing).execute()
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
                                                    
                                                pdf_bytes = generate_regular_pdf_bulk(student_course_payload, academic_year=active_ay, term=active_term, current_sem=b_sem)
                                                st.download_button("📥 Download Master PDF (All CSV Students)", data=pdf_bytes, file_name=f"Bulk_Applications_{b_branch}_CSV.pdf", mime="application/pdf", type="primary")

                                            except Exception as e:
                                                st.error(f"Upload Error: {e}")

    # --- SUMMER REGISTRATION ---
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
            selected_sum_cycle_name = st.selectbox("Select Target Exam Cycle (Summer):", options=list(sum_cycle_options.keys()))
            target_sum_cycle = sum_cycle_options[selected_sum_cycle_name]
            target_sum_cycle_id = target_sum_cycle['cycle_id']
            
            target_sum_ay = target_sum_cycle.get('academic_year', active_ay)
            
            st.subheader("☀️ Summer Semester Application")
            summer_usn = st.text_input("Enter USN for Summer Semester Processing").strip().upper()
            
            if summer_usn:
                staging_check = supabase.table("course_registration_online").select("*").eq("usn", summer_usn).eq("cycle_id", target_sum_cycle_id).execute()
                is_staged = staging_check.data and len(staging_check.data) > 0
                
                if is_staged:
                    st.success(f"✅ Student '{summer_usn}' is already registered online and is LIVE in the COE database!")
                    
                    reg_data = staging_check.data
                    current_utr = reg_data[0].get('utr_number', '') or ""
                    total_fee = reg_data[0].get('fee_amount', 0)
                    
                    col_u1, col_u2 = st.columns([2, 1])
                    new_utr = col_u1.text_input("Transaction ID / UTR", value=current_utr)
                    if col_u2.button("Update UTR Record"):
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
                    st.download_button("🖨️ Re-Download Application PDF", data=pdf_bytes, file_name=f"Summer_Application_{summer_usn}.pdf", mime="application/pdf", type="primary")
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
                                            if st.checkbox(f"[{crs['course_code']}] {crs['course_title']} | Grade: {crs['grade']} | Rule: {crs['rule']}", value=True):
                                                selected_summer_courses.append(crs)
                                        
                                        if selected_summer_courses:
                                            total_fee = calculate_summer_fees(selected_summer_courses)
                                            st.info(f"💰 **Calculated Total Fee (Including Base 400):** ₹{total_fee}")
                                            
                                            rule_1_credits = sum([c['credits'] for c in selected_summer_courses if "Rule 1" in c['rule']])
                                            
                                            allow_extra_credit = st.checkbox(
                                                "🚨 **Principal/HOD Override:** Allow 1 additional credit (Max 15)", 
                                                help="Check this box if the student has special written permission to exceed the standard 14-credit limit."
                                            )
                                            max_allowed = 15 if allow_extra_credit else 14
                                            
                                            if rule_1_credits > max_allowed:
                                                st.error(f"❌ **Credit Limit Exceeded!** Selected {rule_1_credits} credits under Rule 1. Maximum allowed is **{max_allowed} Credits**.")
                                            else:
                                                st.markdown("### Payment Details")
                                                target_utr = st.text_input("Transaction ID / UTR (Optional)", help="Leave blank to write manually.")
                                                
                                                if st.button("💾 Submit Registration & Generate PDF", type="primary"):
                                                    payload_staging = [{"cycle_id": target_sum_cycle_id, "usn": summer_usn, "course_code": c['course_code'], "semester": c['semester'], "academic_year": target_sum_ay, "semester_type": "SUMMER", "registration_type": "SUMMER", "rule_category": c['rule'], "fee_amount": total_fee, "payment_status": "PAID", "utr_number": target_utr.strip()} for c in selected_summer_courses]
                                                    payload_official = [{"cycle_id": target_sum_cycle_id, "usn": summer_usn, "course_code": c['course_code'], "semester": c['semester'], "academic_year": target_sum_ay, "semester_type": "SUMMER", "registration_type": "SUMMER"} for c in selected_summer_courses]
                                                    
                                                    try:
                                                        supabase.table("course_registration_online").delete().eq("cycle_id", target_sum_cycle_id).eq("usn", summer_usn).execute()
                                                        supabase.table("course_registrations").delete().eq("cycle_id", target_sum_cycle_id).eq("usn", summer_usn).execute()
                                                        
                                                        supabase.table("course_registration_online").insert(payload_staging).execute()
                                                        supabase.table("course_registrations").insert(payload_official).execute()
                                                        
                                                        st.success(f"✅ Application successfully registered and sent directly to the COE!")
                                                        pdf_bytes = generate_summer_pdf(student, selected_summer_courses, total_fee, target_utr, academic_year=target_sum_ay, exam_type="Summer")
                                                        st.download_button("🖨️ Download Official Application PDF", data=pdf_bytes, file_name=f"Summer_Application_{summer_usn}.pdf", mime="application/pdf", type="primary")
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
            
            if st.button("📥 Generate Department Report", type="primary"):
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
                            type="primary"
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
            
            if st.button("📥 Generate Institutional Report", type="primary"):
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
                            type="primary"
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
