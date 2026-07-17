import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER

CHALK = colors.HexColor("#1F3A2E")
GOLD = colors.HexColor("#C9A227")
CREAM = colors.HexColor("#F1ECDD")
MAROON = colors.HexColor("#7A2E2E")
TEAL = colors.HexColor("#3A6B62")

styles = getSampleStyleSheet()
title_style = ParagraphStyle('title', parent=styles['Heading1'], textColor=colors.white, fontSize=16)
sub_style = ParagraphStyle('sub', parent=styles['Normal'], textColor=colors.white, fontSize=9, alignment=TA_RIGHT)
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
