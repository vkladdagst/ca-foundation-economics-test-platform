import logging
import secrets
import threading
from datetime import datetime, timedelta

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, url_for
)
from flask_login import current_user

from app.decorators import student_required
from app.extensions import db
from app.models import Test, Question, Submission, Response, Teacher
from app.utils import explain, mail, push
from app.utils.grading import grade_submission

logger = logging.getLogger(__name__)

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


def _my_attempts(test):
    """The current student's attempts at this test, oldest first, split into
    (the one still in progress or None, the finished ones)."""
    subs = (
        Submission.query.filter_by(test_id=test.id, student_id=current_user.id)
        .order_by(Submission.started_at, Submission.id).all()
    )
    open_sub = next((x for x in reversed(subs) if x.status == "in_progress"), None)
    return open_sub, [x for x in subs if x.status == "submitted"]


@student_bp.route("/<code>")
@student_required
def entry(code):
    test = get_test_or_404(code)

    if test.status == "draft":
        return render_template("student/unavailable.html", test=test, reason="This test has not been published yet.")

    open_sub, done = _my_attempts(test)
    if open_sub:
        return redirect(url_for("student.attempt", code=code))
    if done:
        if test.one_attempt_only:
            return redirect(url_for("student.success", code=code, ref=done[-1].reference_number))
        if test.status != "active" or not test.visible_to_batch(current_user.batch):
            return redirect(url_for("student.progress", code=code))
        return render_template("student/entry.html", test=test, attempts=done)

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

    open_sub, done = _my_attempts(test)
    if open_sub:
        return redirect(url_for("student.attempt", code=code))
    if test.one_attempt_only and done:
        flash("You have already submitted this test.", "error")
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
    return _my_attempts(test)[0]


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

    earlier_attempts = Submission.query.filter(
        Submission.test_id == test.id, Submission.student_id == current_user.id,
        Submission.status == "submitted", Submission.id != submission.id,
    ).count()
    submission.status = "submitted"
    submission.submitted_at = datetime.utcnow()
    db.session.commit()

    grade_submission(submission)

    if earlier_attempts:
        # A retake: the teacher was already told about this student's first
        # attempt, and 50 students retaking tests would otherwise flood their
        # inbox and phone.
        return redirect(url_for("student.success", code=code, ref=submission.reference_number))

    # Teacher alerts (email + push) are network calls to third parties that
    # can take seconds each. They run in the background so the student gets
    # their result immediately -- otherwise a whole class submitting at the
    # same moment ties up every worker thread waiting on those calls.
    threading.Thread(
        target=_notify_teacher_of_submission,
        args=(
            current_app._get_current_object(), test.teacher_id, test.teacher.email,
            f"New submission: {test.title} — {current_user.name}",
            (
                f"{current_user.name} ({current_user.roll_number}) just submitted {test.title}.\n\n"
                f"Score: {submission.score} / {submission.max_score} ({submission.percentage}%)\n"
                f"Correct: {submission.correct_count}, Wrong: {submission.wrong_count}, "
                f"Unanswered: {submission.unanswered_count}\n\n"
                f"View full results in your dashboard."
            ),
            f"{current_user.name} — {submission.score}/{submission.max_score} on {test.title}",
            url_for("teacher.student_detail", test_id=test.id, submission_id=submission.id, _external=True),
        ),
        daemon=True,
    ).start()

    return redirect(url_for("student.success", code=code, ref=submission.reference_number))


def _notify_teacher_of_submission(app, teacher_id, teacher_email, subject, body, push_body, push_url):
    with app.app_context():
        try:
            if mail.mail_configured() and teacher_email:
                mail.send_email(teacher_email, subject, body)
            teacher = db.session.get(Teacher, teacher_id)
            if teacher:
                push.notify_teacher(teacher, "New submission", push_body, push_url)
        except Exception:
            logger.exception("Background teacher notification failed")
        finally:
            db.session.remove()


@student_bp.route("/<code>/success")
@student_required
def success(code):
    test = get_test_or_404(code)
    ref = request.args.get("ref", "")
    submission = Submission.query.filter_by(reference_number=ref, test_id=test.id).first()
    if not submission or submission.status != "submitted" or submission.student_id != current_user.id:
        abort(404)
    _, done = _my_attempts(test)
    position = next((i for i, x in enumerate(done) if x.id == submission.id), 0)
    previous = done[position - 1] if position > 0 else None
    return render_template(
        "student/success.html", test=test, submission=submission,
        show_score=test.show_result_immediately,
        attempt_no=position + 1, total_attempts=len(done), previous=previous,
        can_retry=(not test.one_attempt_only and test.status == "active"
                   and test.visible_to_batch(current_user.batch)),
    )


@student_bp.route("/<code>/mine")
@student_required
def mine(code):
    """Let a student review one of their own past attempts — their answer vs.
    the correct answer — if the teacher has allowed review for this test.
    Shows the latest attempt unless ?ref= names another one."""
    test = get_test_or_404(code)
    _, done = _my_attempts(test)
    if not done:
        abort(404)
    if not test.allow_review:
        return render_template("student/unavailable.html", test=test, reason="Answer review is not enabled for this test.")

    ref = request.args.get("ref", "")
    submission = next((x for x in done if x.reference_number == ref), done[-1])

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
    return render_template(
        "student/mine.html", test=test, submission=submission, rows=rows,
        ai_enabled=explain.ai_configured(), attempts=done,
        attempt_no=done.index(submission) + 1,
    )


def _result_of(response):
    """'correct' | 'wrong' | 'skipped' for one stored answer."""
    if response is None or response.selected_answer is None:
        return "skipped"
    return "correct" if response.is_correct else "wrong"


@student_bp.route("/<code>/progress")
@student_required
def progress(code):
    """Compare this student's attempts at one test, oldest to newest."""
    test = get_test_or_404(code)
    _, done = _my_attempts(test)
    if not done:
        return redirect(url_for("student.entry", code=code))

    show_scores = bool(test.show_result_immediately)
    best = max(done, key=lambda x: (x.percentage, x.id)) if show_scores else None
    attempts = []
    for i, sub in enumerate(done):
        delta = None
        if i > 0 and show_scores:
            delta = round(sub.percentage - done[i - 1].percentage, 1)
        attempts.append({"no": i + 1, "sub": sub, "delta": delta, "is_best": sub is best and len(done) > 1})

    grid = summary = None
    if show_scores and test.allow_review and len(done) > 1:
        rows = Response.query.filter(Response.submission_id.in_([x.id for x in done])).all()
        by = {(r.submission_id, r.question_id): r for r in rows}
        grid = []
        for q in test.questions:
            grid.append({"q": q, "cells": [_result_of(by.get((x.id, q.id))) for x in done]})
        before, after = (g["cells"][-2] for g in grid), (g["cells"][-1] for g in grid)
        summary = {"improved": [], "slipped": [], "still_missing": [], "held": []}
        for g, b, a in zip(grid, before, after):
            if b != "correct" and a == "correct":
                summary["improved"].append(g["q"].q_number)
            elif b == "correct" and a != "correct":
                summary["slipped"].append(g["q"].q_number)
            elif b != "correct" and a != "correct":
                summary["still_missing"].append(g["q"].q_number)
            else:
                summary["held"].append(g["q"].q_number)

    return render_template(
        "student/progress.html", test=test, attempts=attempts, show_scores=show_scores,
        grid=grid, summary=summary,
        can_retry=(not test.one_attempt_only and test.status == "active"
                   and test.visible_to_batch(current_user.batch)),
    )


@student_bp.route("/<code>/explain/<int:qid>", methods=["POST"])
@student_required
def explain_question(code, qid):
    """Explain why the correct option is right and the others are wrong.
    Only available after the student has submitted (never during an attempt)
    and only where the teacher allows answer review."""
    test = get_test_or_404(code)
    _, done = _my_attempts(test)
    if not done or not test.allow_review:
        return jsonify({"ok": False, "error": "Explanations aren't available for this test."}), 403

    question = db.session.get(Question, qid)
    if not question or question.test_id != test.id:
        return jsonify({"ok": False, "error": "Invalid question."}), 404

    text, error = explain.explanation_for(question, current_user.id)
    if error:
        return jsonify({"ok": False, "error": error})
    return jsonify({"ok": True, "explanation": text})
