import secrets
from datetime import datetime, timedelta

from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template,
    request, url_for
)
from flask_login import current_user

from app.decorators import student_required
from app.extensions import db
from app.models import Test, Question, Submission, Response
from app.utils import mail, push
from app.utils.grading import grade_submission

student_bp = Blueprint("student", __name__, template_folder="../../templates/student")


def get_test_or_404(code):
    test = Test.query.filter_by(access_code=code.upper()).first()
    if not test:
        abort(404)
    return test


def _deadline(submission, test):
    if not test.duration_minutes:
        return None
    return submission.started_at + timedelta(minutes=test.duration_minutes)


def _time_expired(submission, test):
    if not test.strict_timer:
        return False
    deadline = _deadline(submission, test)
    return deadline is not None and datetime.utcnow() > deadline


def _my_submission(test):
    """The current student's own submission for this test, of either status."""
    return Submission.query.filter_by(test_id=test.id, student_id=current_user.id).first()


@student_bp.route("/<code>")
@student_required
def entry(code):
    test = get_test_or_404(code)

    if test.status == "draft":
        return render_template("student/unavailable.html", test=test, reason="This test has not been published yet.")

    submission = _my_submission(test)
    if submission:
        if submission.status == "in_progress":
            return redirect(url_for("student.attempt", code=code))
        return redirect(url_for("student.success", code=code, ref=submission.reference_number))

    if test.status == "closed":
        return render_template("student/unavailable.html", test=test, reason="This test is now closed.")

    if not test.visible_to_batch(current_user.batch):
        return render_template("student/unavailable.html", test=test, reason="This test is not available for your batch.")

    return render_template("student/entry.html", test=test)


@student_bp.route("/<code>/start", methods=["POST"])
@student_required
def start(code):
    test = get_test_or_404(code)
    if test.status != "active":
        flash("This test is not currently open.", "error")
        return redirect(url_for("student.entry", code=code))

    if not test.visible_to_batch(current_user.batch):
        flash("This test is not available for your batch.", "error")
        return redirect(url_for("student.entry", code=code))

    if test.one_attempt_only and _my_submission(test):
        flash("You have already started or submitted this test.", "error")
        return redirect(url_for("student.entry", code=code))

    submission = Submission(
        test_id=test.id,
        student_id=current_user.id,
        reference_number=Submission.new_reference_number(),
        dup_guard_token=secrets.token_hex(16),
        student_name=current_user.name,
        roll_number=current_user.roll_number,
        reg_number=current_user.reg_number,
        batch=current_user.batch,
        email=current_user.email,
        mobile=current_user.mobile,
        status="in_progress",
        started_at=datetime.utcnow(),
    )
    db.session.add(submission)
    db.session.commit()

    return redirect(url_for("student.attempt", code=code))


def _current_open_submission(test):
    submission = _my_submission(test)
    if not submission or submission.status != "in_progress":
        return None
    return submission


@student_bp.route("/<code>/attempt")
@student_required
def attempt(code):
    test = get_test_or_404(code)
    submission = _current_open_submission(test)
    if not submission:
        return redirect(url_for("student.entry", code=code))

    if _time_expired(submission, test):
        return redirect(url_for("student.review", code=code))

    questions = list(test.questions)
    answers = {r.question_id: r.selected_answer for r in submission.responses}
    deadline = _deadline(submission, test)

    return render_template(
        "student/attempt.html", test=test, submission=submission,
        questions=questions, answers=answers,
        deadline_iso=(deadline.isoformat() + "Z") if deadline else None,
    )


@student_bp.route("/<code>/answer", methods=["POST"])
@student_required
def save_answer(code):
    test = get_test_or_404(code)
    submission = _current_open_submission(test)
    if not submission:
        return jsonify({"ok": False, "error": "No active session."}), 400

    if _time_expired(submission, test):
        return jsonify({"ok": False, "error": "Time is up. Answers are locked."}), 400

    data = request.get_json(silent=True) or {}
    question_id = data.get("question_id")
    answer = data.get("answer")  # 'A'/'B'/'C'/'D' or None to clear

    question = db.session.get(Question, question_id)
    if not question or question.test_id != test.id:
        return jsonify({"ok": False, "error": "Invalid question."}), 400

    if answer is not None and answer not in ("A", "B", "C", "D"):
        return jsonify({"ok": False, "error": "Invalid answer."}), 400

    if not test.allow_answer_change:
        existing = Response.query.filter_by(submission_id=submission.id, question_id=question.id).first()
        if existing and existing.selected_answer:
            return jsonify({"ok": False, "error": "Answer changes are not allowed for this test."}), 400

    response = Response.query.filter_by(submission_id=submission.id, question_id=question.id).first()
    if not response:
        response = Response(submission_id=submission.id, question_id=question.id)
        db.session.add(response)
    response.selected_answer = answer
    db.session.commit()

    answered_count = Response.query.filter(
        Response.submission_id == submission.id, Response.selected_answer.isnot(None)
    ).count()

    return jsonify({"ok": True, "answered_count": answered_count})


@student_bp.route("/<code>/review")
@student_required
def review(code):
    test = get_test_or_404(code)
    submission = _current_open_submission(test)
    if not submission:
        return redirect(url_for("student.entry", code=code))

    total = test.total_questions
    answered_qids = {r.question_id for r in submission.responses if r.selected_answer}
    answered = len(answered_qids)
    unanswered_numbers = [q.q_number for q in test.questions if q.id not in answered_qids]
    expired = _time_expired(submission, test)

    return render_template(
        "student/review.html", test=test, submission=submission,
        total=total, answered=answered, unanswered_numbers=unanswered_numbers,
        expired=expired,
    )


@student_bp.route("/<code>/submit", methods=["POST"])
@student_required
def submit(code):
    test = get_test_or_404(code)
    submission = _current_open_submission(test)
    if not submission:
        return redirect(url_for("student.entry", code=code))

    if not test.allow_skip and not _time_expired(submission, test):
        answered_qids = {r.question_id for r in submission.responses if r.selected_answer}
        if answered_qids != {q.id for q in test.questions}:
            flash("Please answer all questions before submitting.", "error")
            return redirect(url_for("student.review", code=code))

    submission.status = "submitted"
    submission.submitted_at = datetime.utcnow()
    db.session.commit()

    grade_submission(submission)

    if mail.mail_configured() and test.teacher.email:
        mail.send_email(
            test.teacher.email,
            f"New submission: {test.title} — {current_user.name}",
            (
                f"{current_user.name} ({current_user.roll_number}) just submitted {test.title}.\n\n"
                f"Score: {submission.score} / {submission.max_score} ({submission.percentage}%)\n"
                f"Correct: {submission.correct_count}, Wrong: {submission.wrong_count}, "
                f"Unanswered: {submission.unanswered_count}\n\n"
                f"View full results in your dashboard."
            ),
        )

    push.notify_teacher(
        test.teacher, "New submission",
        f"{current_user.name} — {submission.score}/{submission.max_score} on {test.title}",
        url_for("teacher.student_detail", test_id=test.id, submission_id=submission.id, _external=True),
    )

    return redirect(url_for("student.success", code=code, ref=submission.reference_number))


@student_bp.route("/<code>/success")
@student_required
def success(code):
    test = get_test_or_404(code)
    ref = request.args.get("ref", "")
    submission = Submission.query.filter_by(reference_number=ref, test_id=test.id).first()
    if not submission or submission.status != "submitted" or submission.student_id != current_user.id:
        abort(404)
    return render_template(
        "student/success.html", test=test, submission=submission,
        show_score=test.show_result_immediately,
    )


@student_bp.route("/<code>/mine")
@student_required
def mine(code):
    """Let a student review their own past submission — their answer vs.
    the correct answer — if the teacher has allowed review for this test."""
    test = get_test_or_404(code)
    submission = _my_submission(test)
    if not submission or submission.status != "submitted":
        abort(404)
    if not test.allow_review:
        return render_template("student/unavailable.html", test=test, reason="Answer review is not enabled for this test.")

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
    return render_template("student/mine.html", test=test, submission=submission, rows=rows)
