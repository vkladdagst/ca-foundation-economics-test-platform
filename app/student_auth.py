from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from app.decorators import student_required
from app.models import Student, Test

student_auth_bp = Blueprint("student_auth", __name__, url_prefix="/student")


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
    tests = [t for t in all_tests if t.visible_to_batch(current_user.batch)]

    my_submissions = {
        s.test_id: s for s in current_user.submissions.filter_by(status="submitted")
    }

    return render_template("student/portal.html", tests=tests, my_submissions=my_submissions)
