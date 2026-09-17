from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from app.decorators import student_required
from app.models import Student, Test

student_auth_bp = Blueprint("student_auth", __name__, url_prefix="/student")


@student_auth_bp.route("/help")
def help_page():
    # No @student_required: also useful to a student stuck on the login
    # page, or before they've received their credentials.
    is_student = current_user.is_authenticated and isinstance(current_user, Student)
    return render_template("student/help.html", is_student=is_student)


@student_auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated and isinstance(current_user, Student):
        return redirect(url_for("student_auth.portal"))

    if request.method == "POST":
        roll = request.form.get("roll_number", "").strip()
        password = request.form.get("password", "")

        students = Student.query.filter_by(roll_number=roll).all()
        student = None
        for s in students:
            if s.check_password(password):
                student = s
                break

        if not student:
            flash("Incorrect roll number or password.", "error")
            return render_template("student/login.html")

        login_user(student, remember=True)
        next_url = request.args.get("next")
        return redirect(next_url or url_for("student_auth.portal"))

    return render_template("student/login.html")


@student_auth_bp.route("/logout")
@student_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("student_auth.login"))


@student_auth_bp.route("/portal")
@student_required
def portal():
    # Institute-wide: every subject-teacher's tests appear here, narrowed to
    # whichever ones are open to this student's batch (or unrestricted).
    all_tests = Test.query.filter(
        Test.status.in_(["active", "closed"])
    ).order_by(Test.created_at.desc()).all()
    visible_tests = [t for t in all_tests if t.visible_to_batch(current_user.batch)]

    my_submissions = {
        s.test_id: s for s in current_user.submissions.filter_by(status="submitted")
    }

    progress, chapter_progress = _build_progress(visible_tests, my_submissions)

    # With 200-300 tests uploaded at once, students study different chapters
    # at different times -- let them narrow the list down (by chapter, and/or
    # a free-text search) to just what they're working on right now. These
    # filters only affect the list below; the progress summary above always
    # reflects everything visible, not the current filter.
    available_chapters = sorted({t.chapter for t in visible_tests if t.chapter})
    chapter = request.args.get("chapter", "")
    q = request.args.get("q", "").strip()

    tests = visible_tests
    if chapter:
        tests = [t for t in tests if t.chapter == chapter]
    if q:
        needle = q.lower()
        tests = [
            t for t in tests
            if needle in (t.title or "").lower()
            or needle in (t.chapter or "").lower()
            or needle in (t.subject or "").lower()
        ]

    return render_template(
        "student/portal.html", tests=tests, my_submissions=my_submissions,
        available_chapters=available_chapters, chapter=chapter, q=q,
        progress=progress, chapter_progress=chapter_progress,
    )


def _build_progress(visible_tests, my_submissions):
    """Overall + chapter-wise attempt progress for the student's dashboard.
    Average score only counts tests configured to reveal the score
    immediately -- otherwise it would leak a number the teacher intended
    to withhold."""
    chapters = {}
    for t in visible_tests:
        if not t.chapter:
            continue
        entry = chapters.setdefault(t.chapter, {"total": 0, "attempted": 0, "score_sum": 0.0, "score_count": 0})
        entry["total"] += 1
        sub = my_submissions.get(t.id)
        if sub:
            entry["attempted"] += 1
            if t.show_result_immediately:
                entry["score_sum"] += sub.percentage
                entry["score_count"] += 1

    chapter_progress = []
    for name in sorted(chapters):
        e = chapters[name]
        chapter_progress.append({
            "chapter": name, "total": e["total"], "attempted": e["attempted"],
            "avg_percentage": round(e["score_sum"] / e["score_count"], 1) if e["score_count"] else None,
        })

    progress = {
        "tests_attempted": sum(1 for t in visible_tests if t.id in my_submissions),
        "tests_total": len(visible_tests),
        "chapters_attempted": sum(1 for e in chapters.values() if e["attempted"] > 0),
        "chapters_total": len(chapters),
    }
    return progress, chapter_progress
