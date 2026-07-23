import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER

CHALK = colors.HexColor("#163A5C")
GOLD = colors.HexColor("#C9A227")
CREAM = colors.HexColor("#F0F4F9")
MAROON = colors.HexColor("#1F5C99")
TEAL = colors.HexColor("#1F7A8C")

styles = getSampleStyleSheet()
title_style = ParagraphStyle('title', parent=styles['Heading1'], textColor=colors.white, fontSize=16)
sub_style = ParagraphStyle('sub', parent=styles['Normal'], textColor=colors.white, fontSize=9, alignment=TA_RIGHT)
sub_style_dark = ParagraphStyle('subd', parent=styles['Normal'], textColor=colors.black, fontSize=8.5, alignment=TA_RIGHT)
h3 = ParagraphStyle('h3', parent=styles['Heading3'], textColor=CHALK)
normal = styles['Normal']
center = ParagraphStyle('center', parent=styles['Normal'], alignment=TA_CENTER)


def _header_table(school_line, title_line, meta_lines, width):
    data = [[Paragraph(f"<font color='#C9A227' size=8>{school_line.upper()}</font><br/><font color='white' size=14><b>{title_line}</b></font>", normal),
            Paragraph("<br/>".join(meta_lines), sub_style)]]
    t = Table(data, colWidths=[width * 0.6, width * 0.4])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), CHALK),
        ('TOPPADDING', (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
        ('LEFTPADDING', (0, 0), (0, 0), 14),
        ('RIGHTPADDING', (-1, 0), (-1, 0), 14),
        ('LINEBELOW', (0, 0), (-1, -1), 3, GOLD),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return t


def build_report_card(student, exam, subjects, scores, summ, position, class_size, term_label, academic_year, parent_copy=False, school_name='Nala Secondary School'):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm)
    width = doc.width
    elements = []
    title = ("Parent Report — " if parent_copy else "") + f"{exam} Report Card"
    meta = [f"Student: <b>{student['name']}</b>", f"Class: Form {student['form']} — {student['stream']}",
           f"{term_label}, {academic_year}"]
    elements.append(_header_table(school_name, title, meta, width))
    elements.append(Spacer(1, 6))

    data = [["#", "Subject", "Score", "Grade", "Subj. Pos.", "Remark"]]
    from app import grade_for, subject_ranking
    import db as dbmod
    conn = dbmod.get_db()
    for i, s in enumerate(subjects):
        score = scores[i]
        g = grade_for(score)
        rank_list = subject_ranking(conn, student['form'], exam, s['id'])
        rank = next(r['position'] for r in rank_list if r['id'] == student['id'])
        data.append([str(i + 1), s['name'], str(score), g['letter'], str(rank), g['remark']])
    conn.close()
    tbl = Table(data, colWidths=[16, width * 0.34, 45, 40, 55, width * 0.28])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), CREAM),
        ('TEXTCOLOR', (0, 0), (-1, 0), TEAL),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ('ALIGN', (2, 0), (4, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 14))

    stats = [["Average Score", "GPA (lower better)", "Division", "Class Position"],
            [f"{summ['avg']:.1f}%", f"{summ['gpa']:.2f}", summ['division'], f"{position} of {class_size}"]]
    stbl = Table(stats, colWidths=[width / 4.0] * 4)
    stbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), CREAM),
        ('FONTSIZE', (0, 0), (-1, 0), 8), ('TEXTCOLOR', (0, 0), (-1, 0), TEAL),
        ('FONTSIZE', (0, 1), (-1, 1), 13), ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, 1), (-1, 1), CHALK),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8), ('BOX', (0, 0), (-1, -1), 0.5, colors.white),
    ]))
    elements.append(stbl)
    doc.build(elements)
    buf.seek(0)
    return buf


def build_full_report_card(student, subjects, term_label, academic_year, school_name, school_code,
                           school_region, school_district, remarks, settings, position, class_size):
    """Builds the government-style continuous-assessment report card:
    TEST / MIDTERM / EXAM columns combined into a FINAL SCORE per subject,
    with performance analysis, attendance/behaviour, grading standards, and signatures.

    TEST maps to the 'Test 1' exam type, MIDTERM to 'Midterm', EXAM to 'Terminal/Annual'.
    FINAL SCORE is the average of whichever of those three have a score recorded
    (a blank/0 component is treated as not-yet-assessed and excluded from the average).
    """
    from app import grade_for, division_for, student_summary
    import db as dbmod
    conn = dbmod.get_db()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=12 * mm, bottomMargin=12 * mm,
                            leftMargin=14 * mm, rightMargin=14 * mm)
    width = doc.width
    elements = []

    gov_style = ParagraphStyle('gov', parent=styles['Normal'], alignment=TA_CENTER, fontSize=10, leading=13)
    gov_bold = ParagraphStyle('govb', parent=gov_style, fontName='Helvetica-Bold', fontSize=12)

    header_lines = [
        Paragraph("<b>PRESIDENT'S OFFICE</b>", gov_bold),
        Paragraph("REGIONAL ADMINISTRATION AND LOCAL GOVERNMENT", gov_style),
    ]
    if school_district:
        header_lines.append(Paragraph(f"{school_district.upper()} DISTRICT COUNCIL", gov_style))
    header_lines.append(Paragraph(f"<b>{school_name.upper()}</b>", gov_bold))
    header_lines.append(Paragraph(
        f"STUDENT'S REPORT FOR FORM {student['form']} — {term_label.upper()}, {academic_year}", gov_style))

    header_tbl = Table([[l] for l in header_lines], colWidths=[width])
    header_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FBF0D9')),
        ('BOX', (0, 0), (-1, -1), 1, GOLD),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    elements.append(header_tbl)
    elements.append(Spacer(1, 6))

    contact_left = f"Phone: {settings.get('phone','')}<br/>Email: {settings.get('email','')}"
    contact_right = f"P.O. Box {settings.get('pobox','')}<br/>{school_region}"
    contact_tbl = Table([[Paragraph(contact_left, normal), Paragraph(contact_right, sub_style_dark)]],
                        colWidths=[width * 0.6, width * 0.4])
    contact_tbl.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 8.5)]))
    elements.append(contact_tbl)
    elements.append(Spacer(1, 4))

    student_line = Table([[Paragraph(f"<b>STUDENT NAME:</b> {student['name']}", normal),
                          Paragraph(f"<b>STREAM:</b> {student['stream']}", normal)]],
                         colWidths=[width * 0.7, width * 0.3])
    student_line.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 10)]))
    elements.append(student_line)
    elements.append(Spacer(1, 8))

    # ---- Subject table: TEST / MIDTERM / EXAM -> FINAL ----
    data = [["S/N", "SUBJECT", "TEST\n(100%)", "MIDTERM\n(100%)", "EXAM\n(100%)", "FINAL", "GRADE", "COMMENT"]]
    total = 0.0
    grade_points = []
    for i, s in enumerate(subjects):
        row = conn.execute("""SELECT exam_type, score FROM results
                             WHERE student_id=? AND subject_id=?""", (student['id'], s['id'])).fetchall()
        by_exam = {r['exam_type']: r['score'] for r in row}
        test = by_exam.get('Test 1', 0)
        midterm = by_exam.get('Midterm', 0)
        exam = by_exam.get('Terminal/Annual', 0)
        components = [v for v in (test, midterm, exam) if v and v > 0]
        final = sum(components) / len(components) if components else 0
        g = grade_for(final)
        total += final
        grade_points.append(g['points'])
        data.append([str(i + 1), s['name'],
                    str(test) if test else "", str(midterm) if midterm else "", str(exam) if exam else "",
                    f"{final:.1f}", g['letter'], g['remark']])
    conn.close()

    tbl = Table(data, colWidths=[20, width * 0.26, 48, 55, 45, 42, 35, width * 0.20], repeatRows=1)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), CHALK), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 8), ('FONTSIZE', (0, 0), (-1, 0), 7.5),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('ALIGN', (1, 1), (1, -1), 'LEFT'), ('ALIGN', (7, 1), (7, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 10))

    avg = total / len(subjects) if subjects else 0
    avg_grade = grade_for(avg)
    point_sum = sum(sorted(grade_points)[:7]) if grade_points else 0
    division = division_for(point_sum)
    passed = "PASSED" if division != "Division 0" else "FAILED"

    analysis = f"""<b>A: PERFORMANCE ANALYSIS</b><br/>
    The total of all subject final scores is <b>{total:.1f}</b>. Her/His average score is <b>{avg:.1f}</b>,
    which is a grade <b>{avg_grade['letter']}</b>.<br/>
    Position: her/his class position is <b>{position}</b> among <b>{class_size}</b> in the class.<br/>
    Division: <b>{division}</b>, points <b>{point_sum}</b>. Considering this performance, she/he has <b>{passed}</b>."""
    elements.append(Paragraph(analysis, normal))
    elements.append(Spacer(1, 10))

    behaviour_html = f"""<b>B: ATTENDANCE AND BEHAVIOUR ANALYSIS</b><br/>
    Behaviour: {remarks.get('behaviour') or '.' * 40}<br/>
    Attendance: {remarks.get('attendance') or '.' * 40}<br/><br/>
    Name of the class teacher: {remarks.get('class_teacher_name') or '.' * 30}
    &nbsp;&nbsp;&nbsp; Signature: {'.' * 20}<br/>
    Class teacher's comment: {remarks.get('class_teacher_comment') or '.' * 50}"""
    elements.append(Paragraph(behaviour_html, normal))
    elements.append(Spacer(1, 10))

    # ---- Grading & division standards, side by side ----
    grade_data = [["Score", "Grade", "Comment"],
                 ["75-100", "A", "Excellent"], ["65-74", "B", "Very Good"], ["45-64", "C", "Good"],
                 ["30-44", "D", "Satisfactory"], ["0-29", "F", "Unsatisfactory"]]
    div_data = [["Points", "Division"], ["7-17", "I"], ["18-21", "II"], ["22-25", "III"],
               ["26-33", "IV"], ["34-35", "FAIL"]]
    gt = Table(grade_data, colWidths=[width * 0.15, width * 0.10, width * 0.20])
    dt = Table(div_data, colWidths=[width * 0.15, width * 0.15])
    for t in (gt, dt):
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), CREAM), ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")), ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
    standards_row = Table([["C: TESTING STANDARDS", "", ""]], colWidths=[width])
    standards_row.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), CREAM), ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold')]))
    elements.append(standards_row)
    combo = Table([[gt, dt]], colWidths=[width * 0.5, width * 0.5])
    elements.append(combo)
    elements.append(Spacer(1, 10))

    items = settings.get('items_to_bring', '')
    items_html = "<br/>".join(f"&nbsp;&nbsp;{i+1}. {line}" for i, line in enumerate(items.split('\n')) if line.strip())
    instructions = f"""<b>D: IMPORTANT INSTRUCTIONS</b><br/>
    The school closed at <b>{settings.get('term_close_date','________')}</b>,
    school will open at <b>{settings.get('term_open_date','________')}</b>.<br/>
    When she/he returns to school, she/he should come with:<br/>{items_html}"""
    elements.append(Paragraph(instructions, normal))
    elements.append(Spacer(1, 12))

    signoff = f"""Name of the Head of School: <b>{settings.get('head_teacher_name') or '.' * 30}</b>
    &nbsp;&nbsp;&nbsp; Signature: {'.' * 20}<br/>
    Comment of the Head of School: {remarks.get('head_teacher_comment') or '.' * 60}"""
    elements.append(Paragraph(signoff, normal))

    doc.build(elements)
    buf.seek(0)
    return buf


def build_results_sheet(form, exam, subjects, rows, term_label, academic_year, school_name='Nala Secondary School'):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=14 * mm, bottomMargin=14 * mm,
                            leftMargin=12 * mm, rightMargin=12 * mm)
    width = doc.width
    elements = [_header_table(school_name, f"Form {form} — {exam} Results Sheet",
                              [f"{term_label}, {academic_year}", "Pass mark: 30 (NECTA standard)"], width),
               Spacer(1, 8)]

    header = ["Pos.", "Student"] + [s['abbr'] for s in subjects] + ["Total", "Avg", "GPA", "Division"]
    data = [header]
    for r in rows:
        row = [str(r['position']), r['name']] + [str(sc) for sc in r['scores']] + \
              [str(r['total']), f"{r['avg']:.1f}", f"{r['gpa']:.2f}", r['division']]
        data.append(row)
    ncols = len(header)
    col_widths = [26, 90] + [(width - 26 - 90 - 140) / len(subjects)] * len(subjects) + [35, 35, 35, 65]
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), CHALK), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 7), ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'), ('ALIGN', (2, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    elements.append(tbl)
    doc.build(elements)
    buf.seek(0)
    return buf


def build_necta_report(form, exam, ctx, academic_year):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=14 * mm, bottomMargin=14 * mm,
                            leftMargin=12 * mm, rightMargin=12 * mm)
    width = doc.width
    elements = []
    elements.append(Paragraph("NATIONAL EXAMINATIONS COUNCIL OF TANZANIA", center))
    elements.append(Paragraph(f"FORM {ctx['roman_form_word']} TERM RESULTS — {exam.upper()} {academic_year}", center))
    elements.append(Paragraph(f"{ctx['school_code']} - {ctx['school_name_official']}", ParagraphStyle('b', parent=center, fontName='Helvetica-Bold')))
    elements.append(Spacer(1, 10))

    div_data = [["SEX", "I", "II", "III", "IV", "0"]]
    for sex in ['M', 'F']:
        c = ctx['sex_counts'][sex]
        div_data.append([sex, c['I'], c['II'], c['III'], c['IV'], c['0']])
    dc = ctx['div_counts']
    div_data.append(["T", dc['I'], dc['II'], dc['III'], dc['IV'], dc['0']])
    dtbl = Table(div_data, colWidths=[60] * 6)
    dtbl.setStyle(_grid_style())
    elements.append(dtbl)
    elements.append(Spacer(1, 8))

    cand_data = [["CNO", "SEX", "AGGT", "DIV", "SUBJECTS"]]
    for r in ctx['cand_rows']:
        cand_data.append([r['cno'], r['sex'], str(r['agg']), r['div'], Paragraph(r['subj_str'], ParagraphStyle('sm', fontSize=6))])
    ctbl = Table(cand_data, colWidths=[70, 35, 40, 35, width - 180], repeatRows=1)
    ctbl.setStyle(_grid_style())
    elements.append(ctbl)
    elements.append(Spacer(1, 8))

    elements.append(Paragraph(f"Region: {ctx['school_region']} | Total Passed: {ctx['total_passed']} | "
                              f"Centre GPA: {ctx['centre_gpa']:.4f} ({ctx['competency_level_centre']})", normal))
    elements.append(Spacer(1, 8))

    subj_data = [["Code", "Subject", "Reg", "Sat", "Pass", "GPA", "Competency"]]
    for s in ctx['subj_perf']:
        subj_data.append([s['code'], s['name'], str(s['reg']), str(s['sat']), str(s['pass']),
                          f"{s['gpa']:.4f}", ctx['competency_level'](s['gpa'])])
    stbl = Table(subj_data, colWidths=[40, 150, 35, 35, 35, 50, width - 345], repeatRows=1)
    stbl.setStyle(_grid_style())
    elements.append(stbl)
    doc.build(elements)
    buf.seek(0)
    return buf


def _grid_style():
    return TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ])


def build_receipt(student, payment, due, paid_total, school_name='Nala Secondary School'):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm)
    width = doc.width
    receipt_no = f"RCT-{payment['id']:06d}"
    elements = [Paragraph(school_name, ParagraphStyle('t', parent=styles['Heading1'], textColor=CHALK)),
               Paragraph("Official Payment Receipt", normal), Spacer(1, 10)]
    data = [["Receipt No", receipt_no], ["Date", payment['date']], ["Student", student['name']],
           ["Student ID", student['id']], ["Class", f"Form {student['form']} — {student['stream']}"],
           ["Category", payment['category']], ["Amount Paid", f"TZS {payment['amount']:,}"],
           ["Note", payment['note'] or "—"], ["Term Fee Due", f"TZS {due:,}"],
           ["Total Paid to Date", f"TZS {paid_total:,}"], ["Remaining Balance", f"TZS {max(due-paid_total,0):,}"]]
    tbl = Table(data, colWidths=[width * 0.4, width * 0.6])
    tbl.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 10), ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('FONTNAME', (1, 6), (1, 6), 'Helvetica-Bold'), ('FONTNAME', (1, 10), (1, 10), 'Helvetica-Bold'),
    ]))
    elements.append(tbl)
    doc.build(elements)
    buf.seek(0)
    return buf


def build_reminder(student, due, paid, term_label, academic_year, school_name='Nala Secondary School'):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm)
    width = doc.width
    balance = max(due - paid, 0)
    salutation = f"Dear {student['parent_name']}," if student['parent_name'] else "Dear Parent/Guardian,"
    elements = [Paragraph(school_name, ParagraphStyle('t', parent=styles['Heading1'], textColor=CHALK)),
               Paragraph("Fee Balance Reminder", normal), Spacer(1, 14),
               Paragraph(salutation, normal), Spacer(1, 8),
               Paragraph(f"This letter is to remind you of an outstanding school fee balance for "
                        f"<b>{student['name']}</b> ({student['id']}), Form {student['form']} — {student['stream']}, "
                        f"for {term_label}, {academic_year}.", normal),
               Spacer(1, 12)]
    data = [["Total Term Fee", f"TZS {due:,}"], ["Amount Paid", f"TZS {paid:,}"],
           ["Outstanding Balance", f"TZS {balance:,}"]]
    tbl = Table(data, colWidths=[width * 0.5, width * 0.5])
    tbl.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 10),
                             ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                             ('FONTNAME', (1, 2), (1, 2), 'Helvetica-Bold'),
                             ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
    elements.append(tbl)
    elements.append(Spacer(1, 14))
    elements.append(Paragraph("Kindly settle this balance at your earliest convenience. Please contact the "
                              "school office if you have any questions.", normal))
    doc.build(elements)
    buf.seek(0)
    return buf
