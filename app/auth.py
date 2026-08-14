from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from app.decorators import teacher_required
from app.extensions import db
from app.models import Teacher

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated and isinstance(current_user, Teacher):
        return redirect(url_for("teacher.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        teacher = Teacher.query.filter_by(email=email).first()

        if teacher and teacher.check_password(password):
            login_user(teacher, remember=True)
            next_url = request.args.get("next")
            return redirect(next_url or url_for("teacher.dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@teacher_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    """Bootstrap the very first teacher account on a fresh deployment —
    no shell/console access needed. Self-disables (404s) once any teacher
    account exists, so it can't be used to create rogue accounts later."""
    if Teacher.query.count() > 0:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        errors = []
        if not name:
            errors.append("Name is required.")
        if not email:
            errors.append("Email is required.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("auth/setup.html")

        teacher = Teacher(name=name, email=email)
        teacher.set_password(password)
        db.session.add(teacher)
        db.session.commit()

        login_user(teacher, remember=True)
        flash("Account created. Welcome!", "success")
        return redirect(url_for("teacher.dashboard"))

    return render_template("auth/setup.html")
