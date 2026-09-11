import json
from datetime import datetime, date

from flask import (
    Blueprint, abort, flash, redirect, render_template, request,
    send_file, url_for, current_app
)
from flask_login import current_user
from werkzeug.utils import secure_filename

from app.decorators import teacher_required
from app.extensions import db
from app.models import Test, Question, Submission, Response, Student, PushSubscription, Teacher
from app.utils import excel_io, mail, push
from app.utils.grading import test_statistics, question_statistics, compute_ranks, recalculate_test

teacher_bp = Blueprint("teacher", __name__, template_folder="../../templates/teacher")


def get_owned_test(test_id):
    test = Test.query.get_or_404(test_id)
    if test.teacher_id != current_user.id:
        abort(403)
    return test


def get_owned_submission(test, submission_id):
    submission = Submission.query.get_or_404(submission_id)
    if submission.test_id != test.id:
        abort(404)
    return submission


# ---------------------------------------------------------------- dashboard

@teacher_bp.route("/dashboard")
@teacher_required
def dashboard():
    tests = current_user.tests.order_by(Test.created_at.desc())
    total_tests = tests.count()
    total_students = Student.query.count()  # shared institute-wide roster
    start_of_month = date.today().replace(day=1)
    tests_this_month = tests.filter(Test.created_at >= start_of_month).count()
    total_submissions = (
        db.session.query(Submission)
        .join(Test)
        .filter(Test.teacher_id == current_user.id, Submission.status == "submitted")
        .count()
    )
    recent_tests = tests.limit(8).all()
    return render_template(
        "teacher/dashboard.html",
        total_tests=total_tests, total_students=total_students,
        tests_this_month=tests_this_month, total_submissions=total_submissions,
        recent_tests=recent_tests,
    )


# --------------------------------------------------------------------- tests

@teacher_bp.route("/tests")
@teacher_required
def tests_list():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")

    query = current_user.tests
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Test.title.ilike(like), Test.test_number.ilike(like)))
    if status:
        query = query.filter(Test.status == status)

    tests = query.order_by(Test.created_at.desc()).all()
    return render_template("teacher/tests_list.html", tests=tests, q=q, status=status)


def _apply_test_form(test, form):
    test.title = form.get("title", "").strip()
    test.subject = form.get("subject", "CA Foundation Economics").strip()
    test.test_number = form.get("test_number", "").strip()
    test.description = form.get("description", "").strip()

    date_raw = form.get("test_date", "")
    test.test_date = datetime.strptime(date_raw, "%Y-%m-%d").date() if date_raw else None

    duration_raw = form.get("duration_minutes", "")
    test.duration_minutes = int(duration_raw) if duration_raw else None

    test.marks_per_question_default = float(form.get("marks_per_question_default") or 1.0)
    test.negative_marks_default = float(form.get("negative_marks_default") or 0.0)

    test.questions_visible = form.get("questions_visible") == "on"
    test.allow_answer_change = form.get("allow_answer_change") == "on"
    test.allow_skip = form.get("allow_skip") == "on"
    test.show_result_immediately = form.get("show_result_immediately") == "on"
    test.one_attempt_only = form.get("one_attempt_only") == "on"
    test.strict_timer = form.get("strict_timer") == "on"
    test.allow_review = form.get("allow_review") == "on"
    test.target_batches = form.getlist("target_batches")


def _distinct_batches():
    rows = db.session.query(Student.batch).filter(Student.batch.isnot(None), Student.batch != "").distinct()
    return sorted({r[0] for r in rows})


@teacher_bp.route("/tests/new", methods=["GET", "POST"])
@teacher_required
def test_new():
    if request.method == "POST":
        test = Test(teacher_id=current_user.id, status="draft")
        _apply_test_form(test, request.form)
        if not test.title:
            flash("Test title is required.", "error")
            return render_template("teacher/test_form.html", test=None, available_batches=_distinct_batches())
        db.session.add(test)
        db.session.commit()
        flash("Test created. Now add your questions and answer key.", "success")
        return redirect(url_for("teacher.questions", test_id=test.id))

    return render_template("teacher/test_form.html", test=None, available_batches=_distinct_batches())


@teacher_bp.route("/tests/<int:test_id>/edit", methods=["GET", "POST"])
@teacher_required
def test_edit(test_id):
    test = get_owned_test(test_id)
    if request.method == "POST":
        _apply_test_form(test, request.form)
        db.session.commit()
        flash("Test updated.", "success")
        return redirect(url_for("teacher.tests_list"))
    return render_template("teacher/test_form.html", test=test, available_batches=_distinct_batches())


@teacher_bp.route("/tests/<int:test_id>/duplicate", methods=["POST"])
@teacher_required
def test_duplicate(test_id):
    original = get_owned_test(test_id)
    copy = Test(
        teacher_id=current_user.id,
        title=f"{original.title} (Copy)",
        subject=original.subject,
        test_number=original.test_number,
        description=original.description,
        duration_minutes=original.duration_minutes,
        marks_per_question_default=original.marks_per_question_default,
        negative_marks_default=original.negative_marks_default,
        questions_visible=original.questions_visible,
        allow_answer_change=original.allow_answer_change,
        allow_skip=original.allow_skip,
        show_result_immediately=original.show_result_immediately,
        one_attempt_only=original.one_attempt_only,
        strict_timer=original.strict_timer,
        allow_review=original.allow_review,
        target_batches_json=original.target_batches_json,
        status="draft",
    )
    db.session.add(copy)
    db.session.flush()

    for q in original.questions:
        db.session.add(Question(
            test_id=copy.id, order_index=q.order_index, q_number=q.q_number,
            text=q.text, option_a=q.option_a, option_b=q.option_b,
            option_c=q.option_c, option_d=q.option_d,
            correct_answer=q.correct_answer, marks=q.marks,
            negative_marks=q.negative_marks, explanation=q.explanation,
        ))
    db.session.commit()
    flash(f'Duplicated as "{copy.title}". Edit it and publish when ready.', "success")
    return redirect(url_for("teacher.test_edit", test_id=copy.id))


@teacher_bp.route("/tests/<int:test_id>/delete", methods=["POST"])
@teacher_required
def test_delete(test_id):
    test = get_owned_test(test_id)
    db.session.delete(test)
    db.session.commit()
    flash("Test deleted.", "success")
    return redirect(url_for("teacher.tests_list"))


def _students_for_test(test):
    """The shared roster, narrowed to the test's target batches (all, if unrestricted)."""
    query = Student.query
    targets = test.target_batches
    if targets:
        query = query.filter(Student.batch.in_(targets))
    return query.all()


@teacher_bp.route("/tests/<int:test_id>/publish", methods=["POST"])
@teacher_required
def test_publish(test_id):
    test = get_owned_test(test_id)
    if test.total_questions == 0:
        flash("Add at least one question before publishing.", "error")
        return redirect(url_for("teacher.questions", test_id=test.id))
    test.status = "active"
    db.session.commit()

    link = url_for("student.entry", code=test.access_code, _external=True)
    audience = _students_for_test(test)
    notes = []

    if mail.mail_configured():
        recipients = [(s.email, s.name) for s in audience]

        def body(name):
            return (
                f"Hi {name},\n\n"
                f"A new test has been published: {test.title} ({test.subject})\n"
                f"{test.total_questions} questions, {test.max_marks} marks"
                f"{' (negative marking applies)' if test.negative_marks_default else ''}.\n\n"
                f"Log in and start here: {link}\n\n"
                f"— {current_user.name}"
            )

        sent, _ = mail.send_bulk(recipients, f"New test published: {test.title}", body)
        notes.append(f"emailed {sent}")

    pushed = push.notify_specific_students(
        audience, "New test published",
        f"{test.subject}: {test.title} — {test.total_questions} questions", link,
    )
    if pushed:
        notes.append(f"push to {pushed}")

    suffix = f" Notified students ({', '.join(notes)})." if notes else " Share the link/code with students."
    flash("Test published." + suffix, "success")
    return redirect(url_for("teacher.tests_list"))


@teacher_bp.route("/tests/<int:test_id>/unpublish", methods=["POST"])
@teacher_required
def test_unpublish(test_id):
    test = get_owned_test(test_id)
    test.status = "draft"
    db.session.commit()
    flash("Test unpublished.", "success")
    return redirect(url_for("teacher.tests_list"))


@teacher_bp.route("/tests/<int:test_id>/close", methods=["POST"])
@teacher_required
def test_close(test_id):
    test = get_owned_test(test_id)
    test.status = "closed"
    db.session.commit()
    flash("Test closed to new submissions.", "success")
    return redirect(url_for("teacher.tests_list"))


@teacher_bp.route("/tests/<int:test_id>/upload-paper", methods=["POST"])
@teacher_required
def upload_paper(test_id):
    test = get_owned_test(test_id)
    file = request.files.get("paper")
    if not file or file.filename == "":
        flash("No file selected.", "error")
        return redirect(url_for("teacher.questions", test_id=test.id))

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in current_app.config["ALLOWED_PAPER_EXTENSIONS"]:
        flash("Only PDF, PNG, and JPG files are allowed.", "error")
        return redirect(url_for("teacher.questions", test_id=test.id))

    filename = secure_filename(f"test_{test.id}_{int(datetime.utcnow().timestamp())}.{ext}")
    filepath = f"{current_app.config['UPLOAD_FOLDER']}/{filename}"
    file.save(filepath)

    test.question_paper_path = filename
    test.question_paper_original_name = file.filename
    db.session.commit()
    flash("Question paper uploaded.", "success")
    return redirect(url_for("teacher.questions", test_id=test.id))


@teacher_bp.route("/tests/<int:test_id>/paper")
@teacher_required
def view_paper(test_id):
    test = get_owned_test(test_id)
    if not test.question_paper_path:
        abort(404)
    return send_file(f"{current_app.config['UPLOAD_FOLDER']}/{test.question_paper_path}")


# ---------------------------------------------------------------- questions

@teacher_bp.route("/tests/<int:test_id>/questions")
@teacher_required
def questions(test_id):
    test = get_owned_test(test_id)
    return render_template("teacher/questions.html", test=test, questions=list(test.questions))


@teacher_bp.route("/tests/<int:test_id>/questions/new", methods=["GET", "POST"])
@teacher_required
def question_new(test_id):
    test = get_owned_test(test_id)
    if request.method == "POST":
        q = Question(
            test_id=test.id,
            order_index=test.next_question_number(),
            q_number=int(request.form.get("q_number") or test.next_question_number()),
            text=request.form.get("text", "").strip(),
            option_a=request.form.get("option_a", "").strip(),
            option_b=request.form.get("option_b", "").strip(),
            option_c=request.form.get("option_c", "").strip(),
            option_d=request.form.get("option_d", "").strip(),
            correct_answer=request.form.get("correct_answer", "A").strip().upper(),
            marks=float(request.form.get("marks") or test.marks_per_question_default),
            negative_marks=float(request.form.get("negative_marks") or test.negative_marks_default),
            explanation=request.form.get("explanation", "").strip(),
        )
        db.session.add(q)
        db.session.commit()
        flash(f"Question {q.q_number} added.", "success")
        if request.form.get("action") == "save_and_new":
            return redirect(url_for("teacher.question_new", test_id=test.id))
        return redirect(url_for("teacher.questions", test_id=test.id))

    next_num = test.next_question_number()
    return render_template(
        "teacher/question_form.html", test=test, question=None,
        next_num=next_num,
    )


@teacher_bp.route("/tests/<int:test_id>/questions/<int:qid>/edit", methods=["GET", "POST"])
@teacher_required
def question_edit(test_id, qid):
    test = get_owned_test(test_id)
    q = Question.query.get_or_404(qid)
    if q.test_id != test.id:
        abort(404)

    if request.method == "POST":
        q.q_number = int(request.form.get("q_number") or q.q_number)
        q.text = request.form.get("text", "").strip()
        q.option_a = request.form.get("option_a", "").strip()
        q.option_b = request.form.get("option_b", "").strip()
        q.option_c = request.form.get("option_c", "").strip()
        q.option_d = request.form.get("option_d", "").strip()
        q.correct_answer = request.form.get("correct_answer", "A").strip().upper()
        q.marks = float(request.form.get("marks") or test.marks_per_question_default)
        q.negative_marks = float(request.form.get("negative_marks") or test.negative_marks_default)
        q.explanation = request.form.get("explanation", "").strip()
        db.session.commit()
        flash(f"Question {q.q_number} updated.", "success")
        return redirect(url_for("teacher.questions", test_id=test.id))

    return render_template("teacher/question_form.html", test=test, question=q, next_num=q.q_number)


@teacher_bp.route("/tests/<int:test_id>/questions/<int:qid>/delete", methods=["POST"])
@teacher_required
def question_delete(test_id, qid):
    test = get_owned_test(test_id)
    q = Question.query.get_or_404(qid)
    if q.test_id != test.id:
        abort(404)
    db.session.delete(q)
    db.session.commit()
    flash("Question deleted.", "success")
    return redirect(url_for("teacher.questions", test_id=test.id))


@teacher_bp.route("/tests/<int:test_id>/questions/<int:qid>/move/<direction>", methods=["POST"])
@teacher_required
def question_move(test_id, qid, direction):
    test = get_owned_test(test_id)
    q = Question.query.get_or_404(qid)
    if q.test_id != test.id:
        abort(404)

    ordered = list(test.questions)
    idx = next((i for i, item in enumerate(ordered) if item.id == q.id), None)
    if idx is None:
        abort(404)

    swap_idx = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap_idx < len(ordered):
        other = ordered[swap_idx]
        q.order_index, other.order_index = other.order_index, q.order_index
        db.session.commit()

    return redirect(url_for("teacher.questions", test_id=test.id))


@teacher_bp.route("/tests/<int:test_id>/questions/import/template")
@teacher_required
def question_import_template(test_id):
    get_owned_test(test_id)
    buf = excel_io.build_question_import_template()
    return send_file(
        buf, as_attachment=True, download_name="question_import_template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@teacher_bp.route("/tests/<int:test_id>/questions/import", methods=["GET", "POST"])
@teacher_required
def question_import(test_id):
    test = get_owned_test(test_id)
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Please choose a file to import.", "error")
            return redirect(url_for("teacher.question_import", test_id=test.id))

        ext = file.filename.rsplit(".", 1)[-1].lower()
        if ext not in current_app.config["ALLOWED_IMPORT_EXTENSIONS"]:
            flash("Only .xlsx, .xls, or .csv files are supported.", "error")
            return redirect(url_for("teacher.question_import", test_id=test.id))

        rows, errors = excel_io.parse_question_import(
            file.stream, test.marks_per_question_default, test.negative_marks_default
        )

        if errors:
            return render_template(
                "teacher/question_import.html", test=test, errors=errors, rows=None
            )

        replace = request.form.get("replace") == "on"
        if replace:
            Question.query.filter_by(test_id=test.id).delete()

        start_order = test.next_question_number() if not replace else 1
        for i, row in enumerate(rows):
            db.session.add(Question(
                test_id=test.id, order_index=start_order + i, **row,
            ))
        db.session.commit()
        flash(f"Imported {len(rows)} questions successfully.", "success")
        return redirect(url_for("teacher.questions", test_id=test.id))

    return render_template("teacher/question_import.html", test=test, errors=None, rows=None)


@teacher_bp.route("/tests/<int:test_id>/questions/export")
@teacher_required
def question_export(test_id):
    test = get_owned_test(test_id)
    buf = excel_io.export_questions(test)
    return send_file(
        buf, as_attachment=True,
        download_name=f"{test.title.replace(' ', '_')}_questions.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@teacher_bp.route("/tests/<int:test_id>/answerkey/export")
@teacher_required
def answerkey_export(test_id):
    test = get_owned_test(test_id)
    buf = excel_io.export_answer_key(test)
    return send_file(
        buf, as_attachment=True,
        download_name=f"{test.title.replace(' ', '_')}_answer_key.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ------------------------------------------------------------------ results

@teacher_bp.route("/tests/<int:test_id>/results")
@teacher_required
def results(test_id):
    test = get_owned_test(test_id)
    search = request.args.get("q", "").strip()
    sort = request.args.get("sort", "rank")

    query = test.submissions.filter_by(status="submitted")
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(
            Submission.student_name.ilike(like), Submission.roll_number.ilike(like),
        ))
    submissions = query.all()
    ranked = compute_ranks(submissions)

    if sort == "name":
        ranked.sort(key=lambda t: (t[1].student_name or "").lower())
    elif sort == "roll":
        ranked.sort(key=lambda t: t[1].roll_number or "")
    elif sort == "percentage":
        ranked.sort(key=lambda t: t[1].percentage, reverse=True)
    # default 'rank' already sorted by score desc from compute_ranks

    stats = test_statistics(test)
    return render_template(
        "teacher/results.html", test=test, ranked=ranked, stats=stats,
        search=search, sort=sort,
    )


@teacher_bp.route("/tests/<int:test_id>/results/export")
@teacher_required
def results_export(test_id):
    test = get_owned_test(test_id)
    submissions = list(test.submissions.filter_by(status="submitted"))
    ranked = compute_ranks(submissions)
    buf = excel_io.export_results(test, ranked)
    return send_file(
        buf, as_attachment=True,
        download_name=f"{test.title.replace(' ', '_')}_results.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@teacher_bp.route("/tests/<int:test_id>/results/export-detailed")
@teacher_required
def results_export_detailed(test_id):
    test = get_owned_test(test_id)
    submissions = list(test.submissions.filter_by(status="submitted"))
    buf = excel_io.export_detailed_responses(test, submissions)
    return send_file(
        buf, as_attachment=True,
        download_name=f"{test.title.replace(' ', '_')}_detailed_responses.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@teacher_bp.route("/tests/<int:test_id>/results/<int:submission_id>")
@teacher_required
def student_detail(test_id, submission_id):
    test = get_owned_test(test_id)
    submission = get_owned_submission(test, submission_id)
    responses = {r.question_id: r for r in submission.responses}
    rows = []
    for q in test.questions:
        r = responses.get(q.id)
        rows.append({
            "question": q,
            "selected": r.selected_answer if r else None,
            "is_correct": r.is_correct if r else None,
            "marks_awarded": r.marks_awarded if r else 0,
        })
    return render_template("teacher/student_detail.html", test=test, submission=submission, rows=rows)


@teacher_bp.route("/tests/<int:test_id>/recalculate", methods=["POST"])
@teacher_required
def recalculate(test_id):
    test = get_owned_test(test_id)
    count = recalculate_test(test)
    flash(f"Recalculated {count} submission(s) against the current answer key.", "success")
    return redirect(url_for("teacher.results", test_id=test.id))


@teacher_bp.route("/tests/<int:test_id>/analytics")
@teacher_required
def analytics(test_id):
    test = get_owned_test(test_id)
    stats = question_statistics(test)
    return render_template("teacher/analytics.html", test=test, stats=stats)


@teacher_bp.route("/tests/<int:test_id>/remind", methods=["POST"])
@teacher_required
def remind_non_attempters(test_id):
    test = get_owned_test(test_id)

    if not mail.mail_configured() and not push.push_configured():
        flash("Neither email nor push notifications are configured yet — see Settings.", "error")
        return redirect(url_for("teacher.results", test_id=test.id))

    attempted_ids = {s.student_id for s in test.submissions.filter_by(status="submitted") if s.student_id}
    non_attempters = [s for s in _students_for_test(test) if s.id not in attempted_ids]

    link = url_for("student.entry", code=test.access_code, _external=True)
    notes = []

    if mail.mail_configured():
        def body(name):
            return (
                f"Hi {name},\n\n"
                f"This is a reminder that you haven't yet attempted: {test.title}\n\n"
                f"Log in and start here: {link}\n\n"
                f"— {current_user.name}"
            )

        recipients = [(s.email, s.name) for s in non_attempters]
        sent, skipped = mail.send_bulk(recipients, f"Reminder: {test.title}", body)
        notes.append(f"emailed {sent} (of {len(non_attempters)}; {skipped} had no email / failed)")

    pushed = push.notify_specific_students(
        non_attempters, "Test reminder", f"You haven't attempted: {test.title}", link,
    )
    if pushed:
        notes.append(f"push to {pushed}")

    flash("Reminder sent — " + "; ".join(notes) + ".", "success")
    return redirect(url_for("teacher.results", test_id=test.id))


# ------------------------------------------------------------- student roster
#
# The roster is shared institute-wide: every teacher (subject) sees and can
# manage the same students, so one student login works across every
# subject-teacher's tests. `teacher_id` on Student only records who added
# them; it is not an access boundary here.

@teacher_bp.route("/students")
@teacher_required
def roster():
    q = request.args.get("q", "").strip()
    query = Student.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Student.name.ilike(like), Student.roll_number.ilike(like)))
    students = query.order_by(Student.roll_number).all()
    return render_template("teacher/roster.html", students=students, q=q)


@teacher_bp.route("/students/new", methods=["GET", "POST"])
@teacher_required
def student_new():
    if request.method == "POST":
        roll = request.form.get("roll_number", "").strip()
        name = request.form.get("name", "").strip()
        batch = request.form.get("batch", "").strip()
        reg = request.form.get("reg_number", "").strip()
        email = request.form.get("email", "").strip()
        mobile = request.form.get("mobile", "").strip()

        if not roll or not name:
            flash("Roll number and name are required.", "error")
            return render_template("teacher/student_form.html", student=None)

        existing = Student.query.filter_by(roll_number=roll).first()
        if existing:
            flash(f"A student with roll number '{roll}' already exists ({existing.name}) — they can already log in and see every subject's tests. No need to add them again.", "error")
            return render_template("teacher/student_form.html", student=None)

        password = Student.generate_pin()
        student = Student(
            teacher_id=current_user.id, roll_number=roll, name=name,
            batch=batch, reg_number=reg, email=email, mobile=mobile,
        )
        student.set_password(password)
        db.session.add(student)
        db.session.commit()
        return render_template(
            "teacher/roster_credentials.html",
            entries=[{"roll_number": roll, "name": name, "batch": batch, "password": password}],
        )

    return render_template("teacher/student_form.html", student=None)


@teacher_bp.route("/students/<int:sid>/edit", methods=["GET", "POST"])
@teacher_required
def student_edit(sid):
    student = Student.query.get_or_404(sid)
    if request.method == "POST":
        student.name = request.form.get("name", "").strip()
        student.batch = request.form.get("batch", "").strip()
        student.reg_number = request.form.get("reg_number", "").strip()
        student.email = request.form.get("email", "").strip()
        student.mobile = request.form.get("mobile", "").strip()
        db.session.commit()
        flash("Student updated.", "success")
        return redirect(url_for("teacher.roster"))
    return render_template("teacher/student_form.html", student=student)


@teacher_bp.route("/students/<int:sid>/reset-password", methods=["POST"])
@teacher_required
def student_reset_password(sid):
    student = Student.query.get_or_404(sid)
    password = Student.generate_pin()
    student.set_password(password)
    db.session.commit()
    return render_template(
        "teacher/roster_credentials.html",
        entries=[{"roll_number": student.roll_number, "name": student.name, "batch": student.batch, "password": password}],
    )


@teacher_bp.route("/students/<int:sid>/delete", methods=["POST"])
@teacher_required
def student_delete(sid):
    student = Student.query.get_or_404(sid)
    db.session.delete(student)
    db.session.commit()
    flash("Student removed from roster (for every subject).", "success")
    return redirect(url_for("teacher.roster"))


@teacher_bp.route("/students/import/template")
@teacher_required
def student_import_template():
    buf = excel_io.build_roster_import_template()
    return send_file(
        buf, as_attachment=True, download_name="student_roster_template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@teacher_bp.route("/students/import", methods=["GET", "POST"])
@teacher_required
def student_import():
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Please choose a file to import.", "error")
            return redirect(url_for("teacher.student_import"))

        ext = file.filename.rsplit(".", 1)[-1].lower()
        if ext not in current_app.config["ALLOWED_IMPORT_EXTENSIONS"]:
            flash("Only .xlsx, .xls, or .csv files are supported.", "error")
            return redirect(url_for("teacher.student_import"))

        rows, errors = excel_io.parse_roster_import(file.stream)
        if errors:
            return render_template("teacher/student_import.html", errors=errors)

        created = []
        skipped = []
        for row in rows:
            existing = Student.query.filter_by(roll_number=row["roll_number"]).first()
            if existing:
                skipped.append(row["roll_number"])
                continue
            password = Student.generate_pin()
            student = Student(teacher_id=current_user.id, **row)
            student.set_password(password)
            db.session.add(student)
            created.append({**row, "password": password})
        db.session.commit()

        if skipped:
            flash(f"Skipped {len(skipped)} roll number(s) already in the shared roster: {', '.join(skipped)}", "info")
        if not created:
            flash("No new students were added.", "error")
            return redirect(url_for("teacher.roster"))

        return render_template("teacher/roster_credentials.html", entries=created)

    return render_template("teacher/student_import.html", errors=None)


@teacher_bp.route("/students/credentials/export", methods=["POST"])
@teacher_required
def roster_credentials_export():
    payload = json.loads(request.form.get("payload", "[]"))
    buf = excel_io.export_roster_credentials(payload)
    return send_file(
        buf, as_attachment=True, download_name="student_logins.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# --------------------------------------------------------------- colleagues
#
# Other subject-teachers (e.g. Maths alongside Economics). Each teacher only
# ever sees and manages their own tests/dashboard; they share the student
# roster above so one student login works across every subject.

@teacher_bp.route("/colleagues")
@teacher_required
def colleagues():
    teachers = Teacher.query.order_by(Teacher.name).all()
    return render_template("teacher/colleagues.html", teachers=teachers)


@teacher_bp.route("/colleagues/new", methods=["GET", "POST"])
@teacher_required
def colleague_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()

        if not name or not email:
            flash("Name and email are required.", "error")
            return render_template("teacher/colleague_form.html")

        if Teacher.query.filter_by(email=email).first():
            flash(f"A teacher account with email '{email}' already exists.", "error")
            return render_template("teacher/colleague_form.html")

        password = Teacher.generate_temp_password()
        teacher = Teacher(name=name, email=email)
        teacher.set_password(password)
        db.session.add(teacher)
        db.session.commit()
        return render_template("teacher/colleague_credentials.html", teacher=teacher, password=password)

    return render_template("teacher/colleague_form.html")


@teacher_bp.route("/colleagues/<int:tid>/reset-password", methods=["POST"])
@teacher_required
def colleague_reset_password(tid):
    teacher = Teacher.query.get_or_404(tid)
    password = Teacher.generate_temp_password()
    teacher.set_password(password)
    db.session.commit()
    return render_template("teacher/colleague_credentials.html", teacher=teacher, password=password)


# ------------------------------------------------------------------ settings

@teacher_bp.route("/settings")
@teacher_required
def settings():
    device_count = PushSubscription.query.filter_by(
        owner_type="teacher", owner_id=current_user.id
    ).count()
    return render_template(
        "teacher/settings.html",
        email_configured=mail.mail_configured(),
        push_configured=push.push_configured(),
        push_device_count=device_count,
    )


@teacher_bp.route("/settings/test-email", methods=["POST"])
@teacher_required
def settings_test_email():
    if not mail.mail_configured():
        flash("Email is not configured. Set the SMTP_* environment variables first.", "error")
        return redirect(url_for("teacher.settings"))
    if not current_user.email:
        flash("Your account has no email address on file.", "error")
        return redirect(url_for("teacher.settings"))

    ok = mail.send_email(
        current_user.email,
        "Test email — CA Foundation Economics Test Platform",
        "This is a test email. If you received it, email notifications are working correctly.",
    )
    if ok:
        flash(f"Test email sent to {current_user.email}. Check your inbox (and spam folder).", "success")
    else:
        flash("Could not send the test email — check the SMTP settings and the server logs.", "error")
    return redirect(url_for("teacher.settings"))


@teacher_bp.route("/settings/test-push", methods=["POST"])
@teacher_required
def settings_test_push():
    if not push.push_configured():
        flash("Push is not configured. Set the VAPID_* environment variables first.", "error")
        return redirect(url_for("teacher.settings"))

    sent = push.notify_teacher(
        current_user, "Test push notification",
        "If you see this, push notifications are working correctly.",
        url_for("teacher.dashboard", _external=True),
    )
    if sent:
        flash(f"Test push sent to {sent} device(s). Check for a notification now.", "success")
    else:
        flash("No push reached a device — enable notifications on this device first (Dashboard or Settings), then try again.", "error")
    return redirect(url_for("teacher.settings"))
