import os
import io
import csv
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, session,
                    flash, send_file, abort, g)
from werkzeug.security import generate_password_hash, check_password_hash

import db

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(hours=12)

db.init_db()

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 60


# ---------------- Grading logic ----------------
def grade_for(score):
    if score >= 75: return {"letter": "A", "points": 1, "remark": "Excellent"}
    if score >= 65: return {"letter": "B", "points": 2, "remark": "Very Good"}
    if score >= 45: return {"letter": "C", "points": 3, "remark": "Good"}
    if score >= 30: return {"letter": "D", "points": 4, "remark": "Satisfactory (Pass)"}
    return {"letter": "F", "points": 5, "remark": "Fail"}


def division_for(point_sum):
    if point_sum <= 17: return "Division I"
    if point_sum <= 21: return "Division II"
    if point_sum <= 25: return "Division III"
    if point_sum <= 33: return "Division IV"
    return "Division 0"


def competency_level(gpa):
    if gpa <= 1.6: return "Grade A (Excellent)"
    if gpa <= 2.6: return "Grade B (Very Good)"
    if gpa <= 3.6: return "Grade C (Good)"
    if gpa <= 4.6: return "Grade D (Satisfactory)"
    return "Grade F (Fail)"


def student_scores(conn, student_id, exam_type, subject_ids):
    rows = conn.execute(
        "SELECT subject_id, score FROM results WHERE student_id=? AND exam_type=?",
        (student_id, exam_type)).fetchall()
    by_subj = {r["subject_id"]: r["score"] for r in rows}
    return [by_subj.get(sid, 0) for sid in subject_ids]


def student_summary(scores):
    total = sum(scores)
    avg = total / len(scores) if scores else 0
    pts = [grade_for(s)["points"] for s in scores]
    best_seven = sorted(pts)[:7]
    point_sum = sum(best_seven)
    gpa = sum(pts) / len(pts) if pts else 0
    return {"total": total, "avg": avg, "point_sum": point_sum,
            "division": division_for(point_sum), "gpa": gpa}


def class_sheet(conn, form, exam_type, subjects):
    subject_ids = [s["id"] for s in subjects]
    students = conn.execute("SELECT * FROM students WHERE form=? ORDER BY name", (form,)).fetchall()
    rows = []
    for st in students:
        scores = student_scores(conn, st["id"], exam_type, subject_ids)
        summ = student_summary(scores)
        rows.append({"id": st["id"], "name": st["name"], "stream": st["stream"],
                     "scores": scores, **summ})
    rows.sort(key=lambda r: -r["avg"])
    for i, r in enumerate(rows):
        r["position"] = i + 1
    return rows


def subject_ranking(conn, form, exam_type, subject_id):
    students = conn.execute("SELECT * FROM students WHERE form=? ORDER BY name", (form,)).fetchall()
    rows = []
    for st in students:
        row = conn.execute("SELECT score FROM results WHERE student_id=? AND exam_type=? AND subject_id=?",
                           (st["id"], exam_type, subject_id)).fetchone()
        rows.append({"id": st["id"], "name": st["name"], "score": row["score"] if row else 0})
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows):
        r["position"] = i + 1
    return rows


def pass_rate(scores):
    if not scores: return 0
    return len([s for s in scores if s >= 30]) / len(scores) * 100


# ---------------- Fees ----------------
def fee_due_for(conn, student):
    row = conn.execute("SELECT amount FROM fee_custom_due WHERE student_id=?", (student["id"],)).fetchone()
    if row and row["amount"] is not None:
        return row["amount"]
    fs = conn.execute("SELECT amount FROM fee_structure WHERE form=?", (student["form"],)).fetchone()
    return fs["amount"] if fs else 0


def fee_paid_for(conn, student_id):
    row = conn.execute("SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE student_id=?",
                       (student_id,)).fetchone()
    return row["total"] or 0


def fmt_tzs(n):
    return "TZS " + format(int(n or 0), ",")


app.jinja_env.filters['tzs'] = fmt_tzs


# ---------------- Auth helpers ----------------
def current_user():
    if 'role' not in session:
        return None
    return {"role": session['role'], "id": session['id'], "name": session.get('name')}


def login_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user or (roles and user['role'] not in roles):
                return redirect(url_for('login'))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


@app.context_processor
def inject_globals():
    conn = db.get_db()
    term_label = db.get_setting(conn, 'term_label', 'Term II')
    academic_year = db.get_setting(conn, 'academic_year', '2026')
    school_name = db.get_setting(conn, 'school_name', 'Nala Secondary School')
    school_code = db.get_setting(conn, 'school_code', 'S9081')
    school_region = db.get_setting(conn, 'school_region', '')
    school_district = db.get_setting(conn, 'school_district', '')
    pending_count = 0
    user = current_user()
    if user and user['role'] == 'admin':
        row = conn.execute("""SELECT
            (SELECT COUNT(*) FROM students WHERE status='pending') +
            (SELECT COUNT(*) FROM teachers WHERE status='pending') AS c""").fetchone()
        pending_count = row['c']
    conn.close()
    return dict(current_user=user, term_label=term_label, academic_year=academic_year,
                school_name=school_name, school_code=school_code, school_region=school_region,
                school_district=school_district, pending_count=pending_count)


# ---------------- Login / Logout ----------------
def lockout_key(role, uid):
    return f"{role}:{uid}"


@app.route('/', methods=['GET'])
def index():
    if current_user():
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    role = request.values.get('role', 'admin')
    if role not in ('student', 'teacher', 'admin'):
        role = 'admin'
    error = None

    if request.method == 'POST':
        role = request.form.get('role', 'admin')
        uid = request.form.get('id', '').strip()
        pwd = request.form.get('password', '')
        conn = db.get_db()
        key = lockout_key(role, uid)
        att = conn.execute("SELECT * FROM login_attempts WHERE key=?", (key,)).fetchone()
        if att and att['locked_until']:
            locked_until = datetime.fromisoformat(att['locked_until'])
            if datetime.utcnow() < locked_until:
                remaining = int((locked_until - datetime.utcnow()).total_seconds())
                error = f"Too many failed attempts. Try again in {remaining}s."
                conn.close()
                return render_template('login.html', role=role, error=error)

        table = {'student': 'students', 'teacher': 'teachers', 'admin': 'admins'}[role]
        record = conn.execute(f"SELECT * FROM {table} WHERE id=?", (uid,)).fetchone()

        if record and role in ('student', 'teacher') and record['status'] == 'pending':
            conn.close()
            return render_template('login.html', role=role,
                error="Your account is registered but still awaiting admin approval. Check back soon.")

        if record and check_password_hash(record['password_hash'], pwd):
            conn.execute("DELETE FROM login_attempts WHERE key=?", (key,))
            conn.commit()
            session.permanent = True
            session['role'] = role
            session['id'] = uid
            session['name'] = record['name']
            db.log(conn, f"{role}:{uid}", "login", f"{role} {uid} signed in")
            conn.close()
            return redirect(url_for('dashboard'))
        else:
            count = (att['count'] if att else 0) + 1
            locked_until = None
            if count >= MAX_LOGIN_ATTEMPTS:
                locked_until = (datetime.utcnow() + timedelta(seconds=LOCKOUT_SECONDS)).isoformat()
                count = 0
                error = "Too many failed attempts. Locked for 60 seconds."
            else:
                error = "Incorrect ID or password. Please try again."
            conn.execute(
                "INSERT INTO login_attempts (key,count,locked_until) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET count=excluded.count, locked_until=excluded.locked_until",
                (key, count, locked_until))
            conn.commit()
            conn.close()

    return render_template('login.html', role=role, error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    role = request.values.get('role', 'admin')
    if role not in ('student', 'teacher', 'admin'):
        role = 'admin'
    error = None
    success = None

    if request.method == 'POST':
        role = request.form.get('role', 'admin')
        uid = request.form.get('id', '').strip()
        code = request.form.get('recovery_code', '').strip()
        new_pw = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')
        table = {'student': 'students', 'teacher': 'teachers', 'admin': 'admins'}[role]

        conn = db.get_db()
        record = conn.execute(f"SELECT * FROM {table} WHERE id=?", (uid,)).fetchone()

        if not record or not record['recovery_code_hash'] or not check_password_hash(record['recovery_code_hash'], code):
            error = "That ID and recovery code don't match our records."
        elif len(new_pw) < 4:
            error = "New password must be at least 4 characters."
        elif new_pw != confirm_pw:
            error = "New password and confirmation don't match."
        else:
            conn.execute(f"UPDATE {table} SET password_hash=? WHERE id=?",
                         (generate_password_hash(new_pw), uid))
            conn.execute("DELETE FROM login_attempts WHERE key=?", (lockout_key(role, uid),))
            conn.commit()
            db.log(conn, f"{role}:{uid}", "password_recovery", f"{role} {uid} reset their own password via recovery code")
            success = "Password reset. You can sign in now with your new password."
        conn.close()

    return render_template('forgot_password.html', role=role, error=error, success=success)


@app.route('/register', methods=['GET', 'POST'])
def self_register():
    role = request.values.get('role', 'student')
    if role not in ('student', 'teacher'):
        role = 'student'
    error = None
    recovery_code_shown = None
    conn = db.get_db()
    forms = db.get_forms(conn)
    subjects = db.get_subjects(conn)

    if request.method == 'POST':
        role = request.form.get('role', 'student')
        uid = request.form.get('id', '').strip()
        pwd = request.form.get('password', '')
        confirm_pw = request.form.get('confirm_password', '')
        name = request.form.get('name', '').strip()

        exists = (conn.execute("SELECT 1 FROM admins WHERE id=?", (uid,)).fetchone()
                  or conn.execute("SELECT 1 FROM teachers WHERE id=?", (uid,)).fetchone()
                  or conn.execute("SELECT 1 FROM students WHERE id=?", (uid,)).fetchone())

        if not uid or not pwd or not name:
            error = "Please fill in your ID, name, and password."
        elif len(pwd) < 4:
            error = "Password must be at least 4 characters."
        elif pwd != confirm_pw:
            error = "Password and confirmation don't match."
        elif exists:
            error = f'"{uid}" is already registered. Choose a different ID, or use Forgot Password if it\'s yours.'
        else:
            recovery_code = '-'.join(secrets.token_hex(2).upper() for _ in range(2))
            pw_hash = generate_password_hash(pwd)
            rc_hash = generate_password_hash(recovery_code)

            if role == 'student':
                form = request.form.get('form', '').strip().upper()
                stream = request.form.get('stream', '').strip() or 'A'
                sex = 'F' if request.form.get('sex') == 'F' else 'M'
                if form not in [f['name'] for f in forms]:
                    error = "Please choose a valid class."
                else:
                    conn.execute("""INSERT INTO students (id,name,password_hash,form,stream,sex,
                                    recovery_code_hash,status) VALUES (?,?,?,?,?,?,?, 'pending')""",
                                 (uid, name, pw_hash, form, stream, sex, rc_hash))
                    conn.commit()
                    db.log(conn, 'system', 'self_register', f"Student {name} ({uid}) self-registered, awaiting approval")
            else:
                subject_ids = request.form.getlist('subjects')
                if not subject_ids:
                    error = "Please select at least one subject you teach."
                else:
                    conn.execute("""INSERT INTO teachers (id,name,password_hash,recovery_code_hash,status)
                                    VALUES (?,?,?,?, 'pending')""", (uid, name, pw_hash, rc_hash))
                    for s in subject_ids:
                        conn.execute("INSERT INTO teacher_subjects (teacher_id, subject_id) VALUES (?,?)", (uid, int(s)))
                    conn.commit()
                    db.log(conn, 'system', 'self_register', f"Teacher {name} ({uid}) self-registered, awaiting approval")

            if not error:
                recovery_code_shown = recovery_code

    conn.close()
    return render_template('register.html', role=role, error=error, forms=forms, subjects=subjects,
                            recovery_code_shown=recovery_code_shown)


@app.route('/dashboard')
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    if user['role'] == 'student':
        return redirect(url_for('student_results'))
    if user['role'] == 'teacher':
        return redirect(url_for('teacher_enter'))
    if user['role'] == 'admin':
        return redirect(url_for('admin_overview'))
    return redirect(url_for('login'))


# ==================== STUDENT ====================
@app.route('/student/results')
@login_required('student')
def student_results():
    conn = db.get_db()
    sid = session['id']
    student = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    subjects = db.get_subjects(conn)
    exam = request.args.get('exam', db.EXAM_TYPES[-1])
    scores = student_scores(conn, sid, exam, [s['id'] for s in subjects])
    summ = student_summary(scores)
    sheet = class_sheet(conn, student['form'], exam, subjects)
    my_row = next(r for r in sheet if r['id'] == sid)
    subj_rows = []
    for i, s in enumerate(subjects):
        score = scores[i]
        g = grade_for(score)
        rank = next(r['position'] for r in subject_ranking(conn, student['form'], exam, s['id']) if r['id'] == sid)
        subj_rows.append({"name": s['name'], "score": score, "grade": g, "rank": rank})
    conn.close()
    return render_template('student_results.html', student=student, exam=exam,
                           exam_types=db.EXAM_TYPES, subj_rows=subj_rows, summ=summ,
                           class_size=len(sheet), position=my_row['position'])


@app.route('/student/progress')
@login_required('student')
def student_progress():
    conn = db.get_db()
    sid = session['id']
    student = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    subjects = db.get_subjects(conn)
    subject_idx = request.args.get('subject', 'overall')
    labels = db.EXAM_TYPES
    if subject_idx == 'overall':
        data = []
        for exam in db.EXAM_TYPES:
            scores = student_scores(conn, sid, exam, [s['id'] for s in subjects])
            data.append(round(student_summary(scores)['avg'], 1))
        series_label = "Overall Average %"
    else:
        sid_int = int(subject_idx)
        data = []
        for exam in db.EXAM_TYPES:
            row = conn.execute("SELECT score FROM results WHERE student_id=? AND exam_type=? AND subject_id=?",
                               (sid, exam, sid_int)).fetchone()
            data.append(row['score'] if row else 0)
        subj = next(s for s in subjects if s['id'] == sid_int)
        series_label = subj['name'] + " Score"
    conn.close()
    return render_template('student_progress.html', subjects=subjects, subject_idx=subject_idx,
                           labels=labels, data=data, series_label=series_label)


@app.route('/student/fees')
@login_required('student')
def student_fees():
    conn = db.get_db()
    sid = session['id']
    student = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    due = fee_due_for(conn, student)
    paid = fee_paid_for(conn, sid)
    payments = conn.execute("SELECT * FROM payments WHERE student_id=? ORDER BY id DESC", (sid,)).fetchall()
    conn.close()
    return render_template('student_fees.html', due=due, paid=paid, balance=max(due - paid, 0),
                           payments=payments)


@app.route('/student/report.pdf')
@login_required('student')
def student_report_pdf():
    conn = db.get_db()
    sid = session['id']
    exam = request.args.get('exam', db.EXAM_TYPES[-1])
    student = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    subjects = db.get_subjects(conn)
    scores = student_scores(conn, sid, exam, [s['id'] for s in subjects])
    summ = student_summary(scores)
    sheet = class_sheet(conn, student['form'], exam, subjects)
    my_row = next(r for r in sheet if r['id'] == sid)
    term_label = db.get_setting(conn, 'term_label', 'Term II')
    academic_year = db.get_setting(conn, 'academic_year', '2026')
    school_name = db.get_setting(conn, 'school_name', 'Nala Secondary School')
    conn.close()
    from pdfgen import build_report_card
    buf = build_report_card(student, exam, subjects, scores, summ, my_row['position'], len(sheet),
                            term_label, academic_year, school_name=school_name)
    fname = f"{student['name'].replace(' ','_')}_{exam.replace('/','-')}.pdf"
    return send_file(buf, download_name=fname, as_attachment=True, mimetype='application/pdf')


def candidate_number(index, school_code):
    return f"{school_code}/{index+1:04d}"


def roman_form_word(form):
    return {"I": "ONE", "II": "TWO", "III": "THREE", "IV": "FOUR"}.get(form, form)


def necta_context(conn, form, exam):
    school_code = db.get_setting(conn, 'school_code', 'S9081')
    school_name_official = db.get_setting(conn, 'school_name', 'Nala Secondary School').upper()
    school_region = db.get_setting(conn, 'school_region', '')
    school_district = db.get_setting(conn, 'school_district', '')
    subjects = db.get_subjects(conn)
    students = conn.execute("SELECT * FROM students WHERE form=? ORDER BY name", (form,)).fetchall() if form else []
    div_counts = {"I": 0, "II": 0, "III": 0, "IV": 0, "0": 0}
    sex_counts = {"M": {"I": 0, "II": 0, "III": 0, "IV": 0, "0": 0}, "F": {"I": 0, "II": 0, "III": 0, "IV": 0, "0": 0}}
    cand_rows = []
    all_points = []
    for i, st in enumerate(students):
        scores = student_scores(conn, st['id'], exam, [s['id'] for s in subjects])
        summ = student_summary(scores)
        div_key = summ['division'].replace('Division ', '')
        div_counts[div_key] = div_counts.get(div_key, 0) + 1
        if st['sex'] in sex_counts:
            sex_counts[st['sex']][div_key] = sex_counts[st['sex']].get(div_key, 0) + 1
        subj_str = " ".join(f"{s['abbr']}-'{grade_for(scores[idx])['letter']}'" for idx, s in enumerate(subjects))
        cand_rows.append({"cno": candidate_number(i, school_code), "sex": st['sex'], "agg": summ['point_sum'],
                          "div": div_key, "subj_str": subj_str, "name": st['name']})
        all_points.extend(grade_for(s)['points'] for s in scores)

    total_passed = len([r for r in cand_rows if r['div'] != "0"])
    centre_gpa = sum(all_points) / len(all_points) if all_points else 0

    subj_perf = []
    for s in subjects:
        scores = [student_scores(conn, st['id'], exam, [s['id']])[0] for st in students]
        pass_count = len([sc for sc in scores if sc >= 30])
        gpa_val = sum(grade_for(sc)['points'] for sc in scores) / len(scores) if scores else 0
        subj_perf.append({"code": s['code'], "name": s['name'], "reg": len(scores), "sat": len(scores),
                          "pass": pass_count, "gpa": gpa_val})

    return dict(cand_rows=cand_rows, div_counts=div_counts, sex_counts=sex_counts,
               total_passed=total_passed, centre_gpa=centre_gpa, subj_perf=subj_perf,
               roster_count=len(students), school_code=school_code,
               school_name_official=school_name_official, school_region=school_region,
               school_district=school_district,
               roman_form_word=roman_form_word(form) if form else '',
               competency_level=competency_level, competency_level_centre=competency_level(centre_gpa))


# ==================== TEACHER ====================
def teacher_subjects(conn, tid):
    return conn.execute("""
        SELECT s.* FROM subjects s
        JOIN teacher_subjects ts ON ts.subject_id = s.id
        WHERE ts.teacher_id = ? ORDER BY s.sort_order, s.id
    """, (tid,)).fetchall()


def resolve_teacher_state(conn, tid):
    forms = db.get_forms(conn)
    subs = teacher_subjects(conn, tid)
    form = request.args.get('form') or (forms[0]['name'] if forms else None)
    exam = request.args.get('exam', db.EXAM_TYPES[-1])
    subject_id = request.args.get('subject', type=int)
    if not subs:
        subject_id = None
    elif subject_id is None or subject_id not in [s['id'] for s in subs]:
        subject_id = subs[0]['id']
    return form, exam, subject_id, forms, subs


@app.route('/teacher/enter', methods=['GET', 'POST'])
@login_required('teacher')
def teacher_enter():
    conn = db.get_db()
    tid = session['id']
    form, exam, subject_id, forms, subs = resolve_teacher_state(conn, tid)

    if request.method == 'POST':
        form = request.form.get('form')
        exam = request.form.get('exam')
        subject_id = int(request.form.get('subject'))
        students = conn.execute("SELECT id FROM students WHERE form=?", (form,)).fetchall()
        for st in students:
            val = request.form.get(f"score_{st['id']}")
            if val is not None and val.strip() != '':
                try:
                    score = max(0, min(100, int(val)))
                    conn.execute("""
                        INSERT INTO results (student_id, exam_type, subject_id, score) VALUES (?,?,?,?)
                        ON CONFLICT(student_id, exam_type, subject_id) DO UPDATE SET score=excluded.score
                    """, (st['id'], exam, subject_id, score))
                except ValueError:
                    pass
        conn.commit()
        subj_name = next((s['name'] for s in subs if s['id'] == subject_id), '')
        db.log(conn, f"teacher:{tid}", "score_entry", f"Saved {subj_name} scores for Form {form}, {exam}")
        flash("Results saved.", "success")

    students = conn.execute("SELECT * FROM students WHERE form=? ORDER BY name", (form,)).fetchall() if form else []
    rows = []
    for st in students:
        row = conn.execute("SELECT score FROM results WHERE student_id=? AND exam_type=? AND subject_id=?",
                           (st['id'], exam, subject_id)).fetchone()
        rows.append({"id": st['id'], "name": st['name'], "stream": st['stream'],
                     "score": row['score'] if row else 0})
    conn.close()
    return render_template('teacher_enter.html', forms=forms, subs=subs, form=form, exam=exam,
                           subject_id=subject_id, exam_types=db.EXAM_TYPES, rows=rows)


@app.route('/teacher/analysis')
@login_required('teacher')
def teacher_analysis():
    conn = db.get_db()
    tid = session['id']
    form, exam, subject_id, forms, subs = resolve_teacher_state(conn, tid)
    scores = []
    if form and subject_id:
        students = conn.execute("SELECT id FROM students WHERE form=?", (form,)).fetchall()
        for st in students:
            row = conn.execute("SELECT score FROM results WHERE student_id=? AND exam_type=? AND subject_id=?",
                               (st['id'], exam, subject_id)).fetchone()
            scores.append(row['score'] if row else 0)
    avg = sum(scores) / len(scores) if scores else 0
    dist = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    for s in scores:
        dist[grade_for(s)['letter']] += 1
    conn.close()
    subj_name = next((s['name'] for s in subs if s['id'] == subject_id), '')
    return render_template('teacher_analysis.html', forms=forms, subs=subs, form=form, exam=exam,
                           subject_id=subject_id, exam_types=db.EXAM_TYPES, avg=avg,
                           pass_rate=pass_rate(scores), count=len(scores), dist=dist, subj_name=subj_name)


def render_results_sheet(conn, form, exam, forms, back_endpoint, extra_ctx=None):
    subjects = db.get_subjects(conn)
    rows = class_sheet(conn, form, exam, subjects) if form else []
    subject_id = request.args.get('rank_subject', type=int) or (subjects[0]['id'] if subjects else None)
    rank_rows = subject_ranking(conn, form, exam, subject_id) if form and subject_id else []
    ctx = dict(forms=forms, form=form, exam=exam, exam_types=db.EXAM_TYPES, subjects=subjects,
              rows=rows, subject_id=subject_id, rank_rows=rank_rows, back_endpoint=back_endpoint)
    if extra_ctx: ctx.update(extra_ctx)
    return render_template('results_sheet.html', **ctx)


@app.route('/teacher/sheet')
@login_required('teacher')
def teacher_sheet():
    conn = db.get_db()
    forms = db.get_forms(conn)
    form = request.args.get('form') or (forms[0]['name'] if forms else None)
    exam = request.args.get('exam', db.EXAM_TYPES[-1])
    html = render_results_sheet(conn, form, exam, forms, 'teacher_sheet')
    conn.close()
    return html


@app.route('/teacher/necta')
@login_required('teacher')
def teacher_necta():
    conn = db.get_db()
    forms = db.get_forms(conn)
    form = request.args.get('form') or (forms[0]['name'] if forms else None)
    exam = request.args.get('exam', db.EXAM_TYPES[-1])
    ctx = necta_context(conn, form, exam)
    conn.close()
    return render_template('necta_format.html', forms=forms, form=form, exam=exam,
                           exam_types=db.EXAM_TYPES, back_endpoint='teacher_necta', **ctx)


@app.route('/teacher/parents')
@login_required('teacher')
def teacher_parents():
    conn = db.get_db()
    forms = db.get_forms(conn)
    form = request.args.get('form') or (forms[0]['name'] if forms else None)
    exam = request.args.get('exam', db.EXAM_TYPES[-1])
    students = conn.execute("SELECT * FROM students WHERE form=? ORDER BY name", (form,)).fetchall() if form else []
    conn.close()
    return render_template('parent_reports.html', forms=forms, form=form, exam=exam,
                           exam_types=db.EXAM_TYPES, students=students, back_endpoint='teacher_parents')


@app.route('/parent-report/<sid>.pdf')
@login_required('teacher', 'admin')
def parent_report_pdf(sid):
    conn = db.get_db()
    exam = request.args.get('exam', db.EXAM_TYPES[-1])
    student = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if not student: abort(404)
    subjects = db.get_subjects(conn)
    scores = student_scores(conn, sid, exam, [s['id'] for s in subjects])
    summ = student_summary(scores)
    sheet = class_sheet(conn, student['form'], exam, subjects)
    my_row = next(r for r in sheet if r['id'] == sid)
    term_label = db.get_setting(conn, 'term_label', 'Term II')
    academic_year = db.get_setting(conn, 'academic_year', '2026')
    school_name = db.get_setting(conn, 'school_name', 'Nala Secondary School')
    conn.close()
    from pdfgen import build_report_card
    buf = build_report_card(student, exam, subjects, scores, summ, my_row['position'], len(sheet),
                            term_label, academic_year, parent_copy=True, school_name=school_name)
    fname = f"ParentReport_{student['name'].replace(' ','_')}_{exam.replace('/','-')}.pdf"
    return send_file(buf, download_name=fname, as_attachment=True, mimetype='application/pdf')


# ==================== ADMIN ====================
def admin_state():
    forms = None
    form = request.args.get('form', 'All')
    exam = request.args.get('exam', db.EXAM_TYPES[-1])
    return form, exam


@app.route('/admin/overview')
@login_required('admin')
def admin_overview():
    conn = db.get_db()
    form, exam = admin_state()
    forms = db.get_forms(conn)
    subjects = db.get_subjects(conn)
    students = conn.execute("SELECT * FROM students" + (" WHERE form=?" if form != 'All' else ''),
                            (form,) if form != 'All' else ()).fetchall()
    summaries = [student_summary(student_scores(conn, s['id'], exam, [su['id'] for su in subjects])) for s in students]
    all_avg = sum(s['avg'] for s in summaries) / len(summaries) if summaries else 0
    div_counts = {"Division I": 0, "Division II": 0, "Division III": 0, "Division IV": 0, "Division 0": 0}
    for s in summaries:
        div_counts[s['division']] += 1
    subj_averages = []
    for su in subjects:
        vals = [student_scores(conn, s['id'], exam, [su['id']])[0] for s in students]
        avg = sum(vals) / len(vals) if vals else 0
        subj_averages.append({"name": su['name'], "avg": avg})
    subj_averages.sort(key=lambda x: -x['avg'])
    top_subject = subj_averages[0] if subj_averages else None

    total_students = conn.execute("SELECT COUNT(*) c FROM students").fetchone()['c']
    all_students = conn.execute("SELECT * FROM students").fetchall()
    total_due = sum(fee_due_for(conn, s) for s in all_students)
    total_paid = sum(fee_paid_for(conn, s['id']) for s in all_students)
    is_default_admin = session['id'] == 'ADM-001'
    conn.close()
    return render_template('admin_overview.html', forms=forms, form=form, exam=exam,
                           exam_types=db.EXAM_TYPES, roster_count=len(students), total_students=total_students,
                           teacher_count=len(conn_teachers()), avg=all_avg, div_counts=div_counts,
                           top_subject=top_subject, total_due=total_due, total_paid=total_paid,
                           is_default_admin=is_default_admin)


def conn_teachers():
    conn = db.get_db()
    rows = conn.execute("SELECT * FROM teachers").fetchall()
    conn.close()
    return rows


@app.route('/admin/necta-analysis')
@login_required('admin')
def admin_necta_analysis():
    conn = db.get_db()
    form, exam = admin_state()
    forms = db.get_forms(conn)
    subjects = db.get_subjects(conn)
    students = conn.execute("SELECT * FROM students" + (" WHERE form=?" if form != 'All' else ''),
                            (form,) if form != 'All' else ()).fetchall()
    subj_rows = []
    for su in subjects:
        vals = [student_scores(conn, s['id'], exam, [su['id']])[0] for s in students]
        avg = sum(vals) / len(vals) if vals else 0
        subj_rows.append({"name": su['name'], "avg": avg, "pass_rate": pass_rate(vals)})
    conn.close()
    attention = sorted([r for r in subj_rows if r['avg'] < 55], key=lambda r: r['avg'])
    return render_template('admin_necta_analysis.html', forms=forms, form=form, exam=exam,
                           exam_types=db.EXAM_TYPES, subj_rows=subj_rows, attention=attention)


@app.route('/admin/sheet')
@login_required('admin')
def admin_sheet():
    conn = db.get_db()
    forms = db.get_forms(conn)
    form = request.args.get('form') or (forms[0]['name'] if forms else None)
    exam = request.args.get('exam', db.EXAM_TYPES[-1])
    html = render_results_sheet(conn, form, exam, forms, 'admin_sheet', extra_ctx={'is_admin': True})
    conn.close()
    return html


@app.route('/admin/necta')
@login_required('admin')
def admin_necta():
    conn = db.get_db()
    forms = db.get_forms(conn)
    form = request.args.get('form') or (forms[0]['name'] if forms else None)
    exam = request.args.get('exam', db.EXAM_TYPES[-1])
    ctx = necta_context(conn, form, exam)
    conn.close()
    return render_template('necta_format.html', forms=forms, form=form, exam=exam,
                           exam_types=db.EXAM_TYPES, back_endpoint='admin_necta', is_admin=True, **ctx)


@app.route('/necta-report/<form>/<path:exam>.pdf')
@login_required('teacher', 'admin')
def necta_report_pdf(form, exam):
    conn = db.get_db()
    ctx = necta_context(conn, form, exam)
    term_label = db.get_setting(conn, 'term_label', 'Term II')
    academic_year = db.get_setting(conn, 'academic_year', '2026')
    conn.close()
    from pdfgen import build_necta_report
    buf = build_necta_report(form, exam, ctx, academic_year)
    fname = f"NECTA_Format_Form{form}_{exam.replace('/','-')}.pdf"
    return send_file(buf, download_name=fname, as_attachment=True, mimetype='application/pdf')


@app.route('/results-sheet/<form>/<path:exam>.pdf')
@login_required('teacher', 'admin')
def results_sheet_pdf(form, exam):
    conn = db.get_db()
    subjects = db.get_subjects(conn)
    rows = class_sheet(conn, form, exam, subjects)
    term_label = db.get_setting(conn, 'term_label', 'Term II')
    academic_year = db.get_setting(conn, 'academic_year', '2026')
    school_name = db.get_setting(conn, 'school_name', 'Nala Secondary School')
    conn.close()
    from pdfgen import build_results_sheet
    buf = build_results_sheet(form, exam, subjects, rows, term_label, academic_year, school_name=school_name)
    fname = f"Form{form}_{exam.replace('/','-')}_ResultsSheet.pdf"
    return send_file(buf, download_name=fname, as_attachment=True, mimetype='application/pdf')


# ---- Students (search, edit, delete) ----
@app.route('/admin/students')
@login_required('admin')
def admin_students():
    conn = db.get_db()
    form, exam = admin_state()
    q = request.args.get('q', '').strip()
    forms = db.get_forms(conn)
    subjects = db.get_subjects(conn)
    sql = "SELECT * FROM students WHERE 1=1"
    params = []
    if form != 'All':
        sql += " AND form=?"; params.append(form)
    if q:
        sql += " AND (name LIKE ? OR id LIKE ?)"; params.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY name"
    students = conn.execute(sql, params).fetchall()
    rows = []
    for s in students:
        scores = student_scores(conn, s['id'], exam, [su['id'] for su in subjects])
        summ = student_summary(scores)
        rows.append({"student": s, "avg": summ['avg'], "division": summ['division']})
    conn.close()
    return render_template('admin_students.html', forms=forms, form=form, exam=exam, exam_types=db.EXAM_TYPES,
                           rows=rows, q=q)


@app.route('/admin/students/<sid>/edit', methods=['GET', 'POST'])
@login_required('admin')
def admin_student_edit(sid):
    conn = db.get_db()
    student = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if not student: abort(404)
    forms = db.get_forms(conn)
    if request.method == 'POST':
        conn.execute("""UPDATE students SET name=?, sex=?, form=?, stream=?,
                        parent_name=?, parent_phone=?, parent_email=? WHERE id=?""",
                    (request.form['name'].strip(), request.form['sex'], request.form['form'],
                     request.form['stream'].strip(), request.form.get('parent_name', '').strip(),
                     request.form.get('parent_phone', '').strip(), request.form.get('parent_email', '').strip(),
                     sid))
        conn.commit()
        db.log(conn, f"admin:{session['id']}", "student_edit", f"Updated profile for {request.form['name']} ({sid})")
        conn.close()
        flash("Student updated.", "success")
        return redirect(url_for('admin_students'))
    conn.close()
    return render_template('admin_student_edit.html', student=student, forms=forms)


@app.route('/admin/students/<sid>/reset-password', methods=['POST'])
@login_required('admin')
def admin_student_reset_pw(sid):
    conn = db.get_db()
    newpw = request.form.get('password', '').strip()
    if newpw:
        conn.execute("UPDATE students SET password_hash=? WHERE id=?", (generate_password_hash(newpw), sid))
        conn.commit()
        db.log(conn, f"admin:{session['id']}", "password_reset", f"Reset password for student {sid}")
        flash("Password updated.", "success")
    conn.close()
    return redirect(url_for('admin_students'))


@app.route('/admin/students/<sid>/delete', methods=['POST'])
@login_required('admin')
def admin_student_delete(sid):
    conn = db.get_db()
    student = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if student:
        conn.execute("DELETE FROM students WHERE id=?", (sid,))
        conn.execute("DELETE FROM results WHERE student_id=?", (sid,))
        conn.execute("DELETE FROM payments WHERE student_id=?", (sid,))
        conn.execute("DELETE FROM fee_custom_due WHERE student_id=?", (sid,))
        conn.commit()
        db.log(conn, f"admin:{session['id']}", "student_delete", f"Deleted student {student['name']} ({sid})")
        flash("Student deleted.", "success")
    conn.close()
    return redirect(url_for('admin_students'))


# ---- Register students (CSV upload / paste) ----
def parse_student_rows(text, forms_list):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    valid, errors = [], []
    seen = set()
    for idx, line in enumerate(lines):
        delim = '\t' if '\t' in line else ','
        fields = [f.strip() for f in line.split(delim)]
        if idx == 0 and fields[0].lower() in ('id', 'studentid', 'student id'):
            continue
        while len(fields) < 9: fields.append('')
        sid, pwd, name, form_raw, stream, sex_raw, pname, pphone, pemail = fields[:9]
        line_no = idx + 1
        if not sid or not pwd or not name:
            errors.append((line_no, f'Missing ID, password, or name ("{line}")')); continue
        form = form_raw.upper()
        if form not in forms_list:
            errors.append((line_no, f'"{sid}" — invalid Form "{form_raw}" (expected {"/".join(forms_list)})')); continue
        if sid in seen:
            errors.append((line_no, f'"{sid}" — duplicate in this batch')); continue
        seen.add(sid)
        sex = 'F' if sex_raw.upper() == 'F' else 'M'
        valid.append({"id": sid, "password": pwd, "name": name, "form": form, "stream": stream or 'A',
                      "sex": sex, "parent_name": pname, "parent_phone": pphone, "parent_email": pemail})
    return valid, errors


@app.route('/admin/register', methods=['GET', 'POST'])
@login_required('admin')
def admin_register():
    conn = db.get_db()
    forms = db.get_forms(conn)
    forms_list = [f['name'] for f in forms]
    preview, errors = None, None
    action = request.form.get('action') if request.method == 'POST' else None

    if request.method == 'POST':
        text = request.form.get('rows_text', '')
        if 'file' in request.files and request.files['file'].filename:
            text = request.files['file'].read().decode('utf-8', errors='ignore')

        valid, errs = parse_student_rows(text, forms_list)
        existing_ids = {r['id'] for r in conn.execute("SELECT id FROM students").fetchall()}
        existing_ids |= {r['id'] for r in conn.execute("SELECT id FROM teachers").fetchall()}
        existing_ids |= {r['id'] for r in conn.execute("SELECT id FROM admins").fetchall()}
        final_valid = []
        for v in valid:
            if v['id'] in existing_ids:
                errs.append((0, f'"{v["id"]}" already exists as a user'))
            else:
                final_valid.append(v)

        if action == 'confirm':
            added = 0
            for v in final_valid:
                conn.execute("""INSERT INTO students (id,name,password_hash,form,stream,sex,parent_name,parent_phone,parent_email)
                              VALUES (?,?,?,?,?,?,?,?,?)""",
                            (v['id'], v['name'], generate_password_hash(v['password']), v['form'], v['stream'],
                             v['sex'], v['parent_name'], v['parent_phone'], v['parent_email']))
                added += 1
            conn.commit()
            db.log(conn, f"admin:{session['id']}", "bulk_register", f"Registered {added} student(s)")
            conn.close()
            flash(f"{added} student(s) registered successfully.", "success")
            return redirect(url_for('admin_register'))
        else:
            preview, errors = final_valid, errs
            session['_pending_text'] = text

    conn.close()
    return render_template('admin_register.html', forms_list=forms_list, preview=preview, errors=errors,
                           rows_text=request.form.get('rows_text', ''))


@app.route('/admin/register/template.csv')
@login_required('admin')
def admin_register_template():
    csv_text = "ID,Password,FullName,Form,Stream,Sex,ParentName,ParentPhone,ParentEmail\n" \
               "MW-3001,pass123,Juma Ally,I,Alpha,M,Mama Juma,0712345678,\n" \
               "MW-3002,pass456,Zawadi Kombe,II,Beta,F,Baba Zawadi,0765432109,zawadi.parent@example.com\n"
    buf = io.BytesIO(csv_text.encode())
    return send_file(buf, download_name='student_registration_template.csv', as_attachment=True, mimetype='text/csv')


# ---- Pending self-registration approvals ----
@app.route('/admin/pending')
@login_required('admin')
def admin_pending():
    conn = db.get_db()
    pending_students = conn.execute("SELECT * FROM students WHERE status='pending' ORDER BY id").fetchall()
    pending_teachers = conn.execute("SELECT * FROM teachers WHERE status='pending' ORDER BY id").fetchall()
    teacher_subjects = {}
    for t in pending_teachers:
        rows = conn.execute("""SELECT s.name FROM teacher_subjects ts JOIN subjects s ON s.id=ts.subject_id
                              WHERE ts.teacher_id=?""", (t['id'],)).fetchall()
        teacher_subjects[t['id']] = [r['name'] for r in rows]
    conn.close()
    return render_template('admin_pending.html', pending_students=pending_students,
                            pending_teachers=pending_teachers, teacher_subjects=teacher_subjects)


@app.route('/admin/pending/<kind>/<sid>/approve', methods=['POST'])
@login_required('admin')
def admin_pending_approve(kind, sid):
    table = 'students' if kind == 'student' else 'teachers'
    conn = db.get_db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (sid,)).fetchone()
    if row:
        conn.execute(f"UPDATE {table} SET status='active' WHERE id=?", (sid,))
        conn.commit()
        db.log(conn, f"admin:{session['id']}", "approve_registration", f"Approved {kind} {row['name']} ({sid})")
        flash(f"{row['name']} approved and can now log in.", "success")
    conn.close()
    return redirect(url_for('admin_pending'))


@app.route('/admin/pending/<kind>/<sid>/reject', methods=['POST'])
@login_required('admin')
def admin_pending_reject(kind, sid):
    table = 'students' if kind == 'student' else 'teachers'
    conn = db.get_db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (sid,)).fetchone()
    if row:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (sid,))
        if kind == 'teacher':
            conn.execute("DELETE FROM teacher_subjects WHERE teacher_id=?", (sid,))
        conn.commit()
        db.log(conn, f"admin:{session['id']}", "reject_registration", f"Rejected {kind} {row['name']} ({sid})")
        flash(f"Registration for {row['name']} rejected and removed.", "success")
    conn.close()
    return redirect(url_for('admin_pending'))


# ---- Manage staff ----
@app.route('/admin/staff')
@login_required('admin')
def admin_staff():
    conn = db.get_db()
    subjects = db.get_subjects(conn)
    teachers = conn.execute("SELECT * FROM teachers ORDER BY name").fetchall()
    teacher_rows = []
    for t in teachers:
        subs = teacher_subjects(conn, t['id'])
        perf = []
        for s in subs:
            vals = [student_scores(conn, st['id'], db.EXAM_TYPES[-1], [s['id']])[0]
                    for st in conn.execute("SELECT id FROM students").fetchall()]
            avg = sum(vals) / len(vals) if vals else None
            perf.append({"name": s['name'], "avg": avg})
        teacher_rows.append({"teacher": t, "subjects": subs, "perf": perf})
    admins = conn.execute("SELECT * FROM admins ORDER BY name").fetchall()
    conn.close()
    return render_template('admin_staff.html', subjects=subjects, teacher_rows=teacher_rows, admins=admins)


@app.route('/admin/staff/teacher/add', methods=['POST'])
@login_required('admin')
def admin_add_teacher():
    conn = db.get_db()
    tid = request.form.get('id', '').strip()
    pwd = request.form.get('password', '').strip()
    name = request.form.get('name', '').strip()
    subject_ids = request.form.getlist('subjects')
    existing = conn.execute("SELECT id FROM teachers WHERE id=? UNION SELECT id FROM admins WHERE id=? UNION SELECT id FROM students WHERE id=?",
                            (tid, tid, tid)).fetchone()
    if not tid or not pwd or not name:
        flash("Please fill in staff ID, password and name.", "error")
    elif existing:
        flash(f'"{tid}" is already in use — choose a different ID.', "error")
    elif not subject_ids:
        flash("Please select at least one subject for this teacher.", "error")
    else:
        conn.execute("INSERT INTO teachers (id,name,password_hash) VALUES (?,?,?)",
                    (tid, name, generate_password_hash(pwd)))
        for s in subject_ids:
            conn.execute("INSERT INTO teacher_subjects (teacher_id, subject_id) VALUES (?,?)", (tid, int(s)))
        conn.commit()
        db.log(conn, f"admin:{session['id']}", "teacher_add", f"Added teacher {name} ({tid})")
        flash(f"{name} added as a teacher.", "success")
    conn.close()
    return redirect(url_for('admin_staff'))


@app.route('/admin/staff/admin/add', methods=['POST'])
@login_required('admin')
def admin_add_admin():
    conn = db.get_db()
    aid = request.form.get('id', '').strip()
    pwd = request.form.get('password', '').strip()
    name = request.form.get('name', '').strip()
    existing = conn.execute("SELECT id FROM teachers WHERE id=? UNION SELECT id FROM admins WHERE id=? UNION SELECT id FROM students WHERE id=?",
                            (aid, aid, aid)).fetchone()
    if not aid or not pwd or not name:
        flash("Please fill in admin ID, password and name.", "error")
    elif existing:
        flash(f'"{aid}" is already in use — choose a different ID.', "error")
    else:
        conn.execute("INSERT INTO admins (id,name,password_hash) VALUES (?,?,?)", (aid, name, generate_password_hash(pwd)))
        conn.commit()
        db.log(conn, f"admin:{session['id']}", "admin_add", f"Added admin {name} ({aid})")
        flash(f"{name} added as an admin.", "success")
    conn.close()
    return redirect(url_for('admin_staff'))


@app.route('/admin/staff/<kind>/<sid>/reset-password', methods=['POST'])
@login_required('admin')
def admin_staff_reset_pw(kind, sid):
    if kind not in ('teacher', 'admin'): abort(404)
    conn = db.get_db()
    table = 'teachers' if kind == 'teacher' else 'admins'
    newpw = request.form.get('password', '').strip()
    if newpw:
        conn.execute(f"UPDATE {table} SET password_hash=? WHERE id=?", (generate_password_hash(newpw), sid))
        conn.commit()
        db.log(conn, f"admin:{session['id']}", "password_reset", f"Reset password for {kind} {sid}")
        flash("Password updated.", "success")
    conn.close()
    return redirect(url_for('admin_staff'))


@app.route('/admin/staff/<kind>/<sid>/delete', methods=['POST'])
@login_required('admin')
def admin_staff_delete(kind, sid):
    if kind not in ('teacher', 'admin'): abort(404)
    conn = db.get_db()
    table = 'teachers' if kind == 'teacher' else 'admins'
    if kind == 'admin' and conn.execute("SELECT COUNT(*) c FROM admins").fetchone()['c'] <= 1:
        flash("You can't remove the last remaining admin account.", "error")
        conn.close()
        return redirect(url_for('admin_staff'))
    row = conn.execute(f"SELECT name FROM {table} WHERE id=?", (sid,)).fetchone()
    if row:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (sid,))
        if kind == 'teacher':
            conn.execute("DELETE FROM teacher_subjects WHERE teacher_id=?", (sid,))
        conn.commit()
        db.log(conn, f"admin:{session['id']}", "staff_remove", f"Removed {kind} {row['name']} ({sid})")
        flash("Removed.", "success")
    conn.close()
    return redirect(url_for('admin_staff'))


# ---- Curriculum ----
@app.route('/admin/curriculum')
@login_required('admin')
def admin_curriculum():
    conn = db.get_db()
    term_label = db.get_setting(conn, 'term_label', 'Term II')
    academic_year = db.get_setting(conn, 'academic_year', '2026')
    school_name = db.get_setting(conn, 'school_name', 'Nala Secondary School')
    school_code = db.get_setting(conn, 'school_code', 'S9081')
    school_region = db.get_setting(conn, 'school_region', '')
    school_district = db.get_setting(conn, 'school_district', '')
    forms = db.get_forms(conn)
    subjects = db.get_subjects(conn)
    conn.close()
    return render_template('admin_curriculum.html', term_label=term_label, academic_year=academic_year,
                           school_name_setting=school_name, school_code_setting=school_code,
                           school_region_setting=school_region, school_district_setting=school_district,
                           forms=forms, subjects=subjects)


@app.route('/admin/curriculum/term', methods=['POST'])
@login_required('admin')
def admin_curriculum_term():
    conn = db.get_db()
    db.set_setting(conn, 'term_label', request.form.get('term_label', 'Term II').strip())
    db.set_setting(conn, 'academic_year', request.form.get('academic_year', '2026').strip())
    db.log(conn, f"admin:{session['id']}", "curriculum_edit", "Updated term label / academic year")
    conn.close()
    flash("Term settings saved.", "success")
    return redirect(url_for('admin_curriculum'))


@app.route('/admin/curriculum/school-info', methods=['POST'])
@login_required('admin')
def admin_curriculum_school_info():
    conn = db.get_db()
    name = request.form.get('school_name', '').strip()
    code = request.form.get('school_code', '').strip()
    region = request.form.get('school_region', '').strip()
    district = request.form.get('school_district', '').strip()
    if name:
        db.set_setting(conn, 'school_name', name)
    if code:
        db.set_setting(conn, 'school_code', code)
    db.set_setting(conn, 'school_region', region)
    db.set_setting(conn, 'school_district', district)
    db.log(conn, f"admin:{session['id']}", "school_info_edit",
           f"Updated school identity: {name or '(unchanged)'} / {code or '(unchanged)'} / {region} / {district}")
    conn.close()
    flash("School information saved.", "success")
    return redirect(url_for('admin_curriculum'))


@app.route('/admin/curriculum/form/add', methods=['POST'])
@login_required('admin')
def admin_curriculum_add_form():
    conn = db.get_db()
    name = request.form.get('name', '').strip().upper()
    if name:
        existing = conn.execute("SELECT name FROM forms WHERE name=?", (name,)).fetchone()
        if existing:
            flash("That class level already exists.", "error")
        else:
            max_order = conn.execute("SELECT COALESCE(MAX(sort_order),-1) m FROM forms").fetchone()['m']
            conn.execute("INSERT INTO forms (name, sort_order) VALUES (?,?)", (name, max_order + 1))
            conn.execute("INSERT OR IGNORE INTO fee_structure (form, amount) VALUES (?,0)", (name,))
            conn.commit()
            db.log(conn, f"admin:{session['id']}", "curriculum_edit", f"Added class level Form {name}")
            flash(f"Form {name} added.", "success")
    conn.close()
    return redirect(url_for('admin_curriculum'))


@app.route('/admin/curriculum/form/<name>/delete', methods=['POST'])
@login_required('admin')
def admin_curriculum_delete_form(name):
    conn = db.get_db()
    in_use = conn.execute("SELECT COUNT(*) c FROM students WHERE form=?", (name,)).fetchone()['c']
    if in_use:
        flash(f"Can't remove Form {name} — there are students currently enrolled in it.", "error")
    else:
        conn.execute("DELETE FROM forms WHERE name=?", (name,))
        conn.commit()
        db.log(conn, f"admin:{session['id']}", "curriculum_edit", f"Removed class level Form {name}")
        flash(f"Form {name} removed.", "success")
    conn.close()
    return redirect(url_for('admin_curriculum'))


@app.route('/admin/curriculum/subject/add', methods=['POST'])
@login_required('admin')
def admin_curriculum_add_subject():
    conn = db.get_db()
    name = request.form.get('name', '').strip()
    if name:
        existing = conn.execute("SELECT id FROM subjects WHERE name=?", (name,)).fetchone()
        if existing:
            flash("That subject already exists.", "error")
        else:
            abbr = ''.join(w[0] for w in name.split() if w)[:6].upper() or name[:4].upper()
            max_order = conn.execute("SELECT COALESCE(MAX(sort_order),-1) m FROM subjects").fetchone()['m']
            code = str(100 + max_order + 1)
            conn.execute("INSERT INTO subjects (name,abbr,code,sort_order) VALUES (?,?,?,?)",
                        (name, abbr, code, max_order + 1))
            conn.commit()
            db.log(conn, f"admin:{session['id']}", "curriculum_edit", f'Added subject "{name}"')
            flash(f'Subject "{name}" added.', "success")
    conn.close()
    return redirect(url_for('admin_curriculum'))


@app.route('/admin/curriculum/subject/<int:subject_id>/delete', methods=['POST'])
@login_required('admin')
def admin_curriculum_delete_subject(subject_id):
    conn = db.get_db()
    row = conn.execute("SELECT name FROM subjects WHERE id=?", (subject_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM subjects WHERE id=?", (subject_id,))
        conn.execute("DELETE FROM results WHERE subject_id=?", (subject_id,))
        conn.execute("DELETE FROM teacher_subjects WHERE subject_id=?", (subject_id,))
        conn.commit()
        db.log(conn, f"admin:{session['id']}", "curriculum_edit", f'Removed subject "{row["name"]}"')
        flash(f'Subject "{row["name"]}" removed, including all recorded scores for it.', "success")
    conn.close()
    return redirect(url_for('admin_curriculum'))


# ---- Fees ----
@app.route('/admin/fees')
@login_required('admin')
def admin_fees():
    conn = db.get_db()
    form, _ = admin_state()
    forms = db.get_forms(conn)
    fee_structure = {r['form']: r['amount'] for r in conn.execute("SELECT * FROM fee_structure").fetchall()}
    students = conn.execute("SELECT * FROM students" + (" WHERE form=?" if form != 'All' else ''),
                            (form,) if form != 'All' else ()).fetchall()
    rows = []
    total_due = total_paid = 0
    for s in students:
        due = fee_due_for(conn, s)
        paid = fee_paid_for(conn, s['id'])
        total_due += due; total_paid += paid
        rows.append({"student": s, "due": due, "paid": paid, "balance": max(due - paid, 0)})
    conn.close()
    return render_template('admin_fees.html', forms=forms, form=form, fee_structure=fee_structure,
                           rows=rows, total_due=total_due, total_paid=total_paid)


@app.route('/admin/fees/structure', methods=['POST'])
@login_required('admin')
def admin_fees_structure():
    conn = db.get_db()
    for f in db.get_forms(conn):
        val = request.form.get(f"fee_{f['name']}", '0')
        try: amount = int(val)
        except ValueError: amount = 0
        conn.execute("UPDATE fee_structure SET amount=? WHERE form=?", (amount, f['name']))
    conn.commit()
    db.log(conn, f"admin:{session['id']}", "fee_structure_edit", "Updated term fee structure")
    conn.close()
    flash("Fee structure saved.", "success")
    return redirect(url_for('admin_fees'))


@app.route('/admin/fees/payment/<sid>', methods=['POST'])
@login_required('admin')
def admin_record_payment(sid):
    conn = db.get_db()
    try:
        amount = int(request.form.get('amount', '0'))
    except ValueError:
        amount = 0
    category = request.form.get('category', 'General').strip() or 'General'
    note = request.form.get('note', '').strip()
    if amount <= 0:
        flash("Please enter a valid positive amount.", "error")
        conn.close()
        return redirect(url_for('admin_fees'))
    date_str = datetime.utcnow().strftime('%d %b %Y')
    cur = conn.execute("INSERT INTO payments (student_id,date,amount,category,note) VALUES (?,?,?,?,?)",
                       (sid, date_str, amount, category, note))
    conn.commit()
    student = conn.execute("SELECT name FROM students WHERE id=?", (sid,)).fetchone()
    db.log(conn, f"admin:{session['id']}", "payment_recorded",
          f"Recorded {fmt_tzs(amount)} ({category}) for {student['name']} ({sid})")
    payment_id = cur.lastrowid
    conn.close()
    flash("Payment recorded.", "success")
    return redirect(url_for('admin_fees') + f"#receipt-{payment_id}")


@app.route('/receipt/<int:payment_id>.pdf')
@login_required('admin')
def receipt_pdf(payment_id):
    conn = db.get_db()
    payment = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not payment: abort(404)
    student = conn.execute("SELECT * FROM students WHERE id=?", (payment['student_id'],)).fetchone()
    due = fee_due_for(conn, student)
    paid_total = fee_paid_for(conn, student['id'])
    school_name = db.get_setting(conn, 'school_name', 'Nala Secondary School')
    conn.close()
    from pdfgen import build_receipt
    buf = build_receipt(student, payment, due, paid_total, school_name=school_name)
    fname = f"Receipt_{student['name'].replace(' ','_')}_{payment_id}.pdf"
    return send_file(buf, download_name=fname, as_attachment=True, mimetype='application/pdf')


@app.route('/admin/fees/reminder/<sid>.pdf')
@login_required('admin')
def reminder_pdf(sid):
    conn = db.get_db()
    student = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if not student: abort(404)
    due = fee_due_for(conn, student)
    paid = fee_paid_for(conn, sid)
    term_label = db.get_setting(conn, 'term_label', 'Term II')
    academic_year = db.get_setting(conn, 'academic_year', '2026')
    db.log(conn, f"admin:{session['id']}", "reminder_generated", f"Generated fee reminder for {student['name']} ({sid})")
    school_name = db.get_setting(conn, 'school_name', 'Nala Secondary School')
    conn.close()
    from pdfgen import build_reminder
    buf = build_reminder(student, due, paid, term_label, academic_year, school_name=school_name)
    fname = f"FeeReminder_{student['name'].replace(' ','_')}.pdf"
    return send_file(buf, download_name=fname, as_attachment=True, mimetype='application/pdf')


@app.route('/admin/fees/custom-due/<sid>', methods=['POST'])
@login_required('admin')
def admin_custom_due(sid):
    conn = db.get_db()
    val = request.form.get('amount', '').strip()
    if val == '':
        conn.execute("DELETE FROM fee_custom_due WHERE student_id=?", (sid,))
    else:
        try:
            amount = int(val)
            conn.execute("""INSERT INTO fee_custom_due (student_id, amount) VALUES (?,?)
                          ON CONFLICT(student_id) DO UPDATE SET amount=excluded.amount""", (sid, amount))
        except ValueError:
            flash("Please enter a valid number.", "error")
    conn.commit()
    db.log(conn, f"admin:{session['id']}", "fee_custom_due", f"Set custom due for student {sid}")
    conn.close()
    flash("Custom fee due updated.", "success")
    return redirect(url_for('admin_fees'))


# ---- Announcements (shared) ----
def announcements_view(can_post, author_label, can_delete, template):
    conn = db.get_db()
    if request.method == 'POST' and can_post:
        title = request.form.get('title', '').strip()
        body = request.form.get('body', '').strip()
        if title and body:
            date_str = datetime.utcnow().strftime('%d %b %Y')
            conn.execute("INSERT INTO announcements (title,body,author,date) VALUES (?,?,?,?)",
                        (title, body, author_label, date_str))
            conn.commit()
            db.log(conn, f"{session['role']}:{session['id']}", "announcement_post", f'Posted "{title}"')
    items = conn.execute("SELECT * FROM announcements ORDER BY id DESC").fetchall()
    conn.close()
    return render_template(template, items=items, can_post=can_post, can_delete=can_delete)


@app.route('/student/announcements')
@login_required('student')
def student_announcements():
    return announcements_view(False, None, False, 'announcements.html')


@app.route('/teacher/announcements', methods=['GET', 'POST'])
@login_required('teacher')
def teacher_announcements():
    conn = db.get_db()
    name = conn.execute("SELECT name FROM teachers WHERE id=?", (session['id'],)).fetchone()['name']
    conn.close()
    return announcements_view(True, f"{name} (Teacher)", False, 'announcements.html')


@app.route('/admin/announcements', methods=['GET', 'POST'])
@login_required('admin')
def admin_announcements():
    conn = db.get_db()
    name = conn.execute("SELECT name FROM admins WHERE id=?", (session['id'],)).fetchone()['name']
    conn.close()
    return announcements_view(True, f"{name} (Admin)", True, 'announcements.html')


@app.route('/admin/announcements/<int:aid>/delete', methods=['POST'])
@login_required('admin')
def admin_announcement_delete(aid):
    conn = db.get_db()
    row = conn.execute("SELECT title FROM announcements WHERE id=?", (aid,)).fetchone()
    conn.execute("DELETE FROM announcements WHERE id=?", (aid,))
    conn.commit()
    if row:
        db.log(conn, f"admin:{session['id']}", "announcement_delete", f'Deleted "{row["title"]}"')
    conn.close()
    return redirect(url_for('admin_announcements'))


# ---- Settings: backup, activity log, danger zone ----
@app.route('/admin/settings')
@login_required('admin')
def admin_settings():
    conn = db.get_db()
    logs = conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT 50").fetchall()
    conn.close()
    db_size = os.path.getsize(db.DB_PATH) if os.path.exists(db.DB_PATH) else 0
    return render_template('admin_settings.html', logs=logs, db_size=db_size)


@app.route('/admin/settings/regenerate-recovery-code', methods=['POST'])
@login_required('admin')
def admin_regenerate_recovery_code():
    conn = db.get_db()
    recovery_code = '-'.join(secrets.token_hex(2).upper() for _ in range(2))
    conn.execute("UPDATE admins SET recovery_code_hash=? WHERE id=?",
                 (generate_password_hash(recovery_code), session['id']))
    conn.commit()
    db.log(conn, f"admin:{session['id']}", "recovery_code_regenerated", "Generated a new personal recovery code")
    conn.close()
    flash(f"Your new recovery code is: {recovery_code} — write it down now, it will not be shown again.", "success")
    return redirect(url_for('admin_settings'))


@app.route('/admin/settings/backup.db')
@login_required('admin')
def admin_backup_db():
    db.get_db().close()  # ensure not mid-write
    return send_file(db.DB_PATH, download_name=f"nala_sms_backup_{datetime.utcnow().strftime('%Y-%m-%d')}.db",
                     as_attachment=True, mimetype='application/x-sqlite3')


@app.route('/admin/settings/restore', methods=['POST'])
@login_required('admin')
def admin_restore_db():
    f = request.files.get('backup_file')
    if not f or not f.filename.endswith('.db'):
        flash("Please upload a valid .db backup file.", "error")
        return redirect(url_for('admin_settings'))
    f.save(db.DB_PATH)
    flash("Backup restored. Please log in again.", "success")
    session.clear()
    return redirect(url_for('login'))


@app.route('/admin/settings/reset', methods=['POST'])
@login_required('admin')
def admin_reset_all():
    if request.form.get('confirm') != 'ERASE':
        flash('Type ERASE exactly to confirm — nothing was deleted.', "error")
        return redirect(url_for('admin_settings'))
    conn = db.get_db()
    for table in ['students', 'teachers', 'teacher_subjects', 'admins', 'results', 'announcements',
                 'fee_structure', 'fee_custom_due', 'payments', 'activity_log', 'settings',
                 'login_attempts', 'subjects', 'forms']:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()
    session.clear()
    flash("All school data has been erased.", "success")
    return redirect(url_for('login'))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_DEBUG', '0') == '1')
