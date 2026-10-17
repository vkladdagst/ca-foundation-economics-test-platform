import os

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

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
    from app.push_routes import push_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(student_auth_bp)
    app.register_blueprint(teacher_bp, url_prefix="/teacher")
    app.register_blueprint(student_bp, url_prefix="/test")
    app.register_blueprint(push_bp)

    @app.context_processor
    def inject_push_config():
        return {"vapid_public_key": app.config.get("VAPID_PUBLIC_KEY", "")}

    @app.route("/")
    def index():
        from flask_login import current_user
        from flask import redirect, url_for
        if current_user.is_authenticated:
            if isinstance(current_user, Student):
                return redirect(url_for("student_auth.portal"))
            return redirect(url_for("teacher.dashboard"))
        return render_template("index.html")

    # ---- Progressive Web App (installable "Add to Home Screen") ----

    @app.route("/manifest.webmanifest")
    def manifest():
        resp = send_from_directory(app.static_folder, "manifest.webmanifest")
        resp.headers["Content-Type"] = "application/manifest+json"
        return resp

    @app.route("/sw.js")
    def service_worker():
        with open(os.path.join(app.static_folder, "sw.js"), "r", encoding="utf-8") as f:
            body = f.read()
        resp = Response(body, mimetype="application/javascript")
        # Served from root so the worker can control the whole origin.
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/offline")
    def offline():
        return render_template("offline.html")

    @app.route("/healthz")
    def healthz():
        """Cheap by default: answers without touching the database. Any uptime
        monitor that calls this every few minutes would otherwise wake the
        free-tier Neon database each time and burn its monthly compute budget
        on nothing. Add ?db=1 for the real check (an actual SELECT 1) -- the
        GitHub workflow does, a few times a day; if the database is
        unreachable it returns 503, the workflow run fails and GitHub emails
        the repo owner."""
        # Render sets RENDER_GIT_COMMIT to the commit that is actually running.
        commit = os.environ.get("RENDER_GIT_COMMIT", "")[:7]
        if request.args.get("db") != "1":
            return jsonify({"status": "ok", "database": "not checked", "commit": commit}), 200
        try:
            db.session.execute(db.text("SELECT 1"))
            return jsonify({"status": "ok", "database": "connected", "commit": commit}), 200
        except Exception as exc:
            return jsonify({"status": "error", "database": "unreachable", "detail": str(exc)[:200]}), 503

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

    from app.migrations import run_light_migrations
    run_light_migrations(app)

    return app
