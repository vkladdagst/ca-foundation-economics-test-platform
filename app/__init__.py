import os

from flask import Flask, render_template

from config import Config
from app.extensions import db, login_manager, csrf


def create_app(config_class=Config):
    app = Flask(
        __name__, instance_relative_config=True,
        template_folder="../templates", static_folder="../static",
    )
    app.config.from_object(config_class)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app.models import Teacher, Student

    @login_manager.user_loader
    def load_user(user_id):
        try:
            kind, raw_id = user_id.split("-", 1)
        except ValueError:
            return None
        if kind == "teacher":
            return db.session.get(Teacher, int(raw_id))
        if kind == "student":
            return db.session.get(Student, int(raw_id))
        return None

    from app.auth import auth_bp
    from app.student_auth import student_auth_bp
    from app.teacher.routes import teacher_bp
    from app.student.routes import student_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(student_auth_bp)
    app.register_blueprint(teacher_bp, url_prefix="/teacher")
    app.register_blueprint(student_bp, url_prefix="/test")

    @app.route("/")
    def index():
        from flask_login import current_user
        if current_user.is_authenticated:
            from flask import redirect, url_for
            return redirect(url_for("teacher.dashboard"))
        return render_template("index.html")

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.template_filter("dt")
    def format_datetime(value, fmt="%d %b %Y, %I:%M %p"):
        if not value:
            return "—"
        return value.strftime(fmt)

    @app.template_filter("d")
    def format_date(value, fmt="%d %b %Y"):
        if not value:
            return "—"
        return value.strftime(fmt)

    with app.app_context():
        db.create_all()

    return app
