"""Question Bank pages: export everything, import a whole bank in one file."""
from flask import (
    abort, current_app, flash, jsonify, redirect, render_template, request,
    send_file, url_for,
)
from flask_login import current_user

from app.decorators import teacher_required
from app.utils import bank_io

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def register(bp):
    @bp.route("/bank")
    @teacher_required
    def bank_home():
        return render_template("teacher/bank.html", errors=None, warnings=None)

    @bp.route("/bank/template")
    @teacher_required
    def bank_template():
        return send_file(bank_io.build_bank_template(), as_attachment=True,
                         download_name="question_bank_template.xlsx", mimetype=XLSX)

    @bp.route("/bank/export")
    @teacher_required
    def bank_export():
        return send_file(bank_io.export_bank(current_user.id), as_attachment=True,
                         download_name="question_bank_backup.xlsx", mimetype=XLSX)

    @bp.route("/bank/import", methods=["POST"])
    @teacher_required
    def bank_import():
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Please choose a file.", "error")
            return redirect(url_for("teacher.bank_home"))
        if not file.filename.lower().endswith(".xlsx"):
            flash("Please upload an Excel .xlsx file.", "error")
            return redirect(url_for("teacher.bank_home"))

        rows, errors, warnings = bank_io.parse_bank(file.stream)
        if errors:
            return render_template("teacher/bank.html", errors=errors, warnings=warnings)

        plan = bank_io.build_plan(current_user.id, rows)
        summary, per_test, samples = bank_io.plan_summary(plan)
        token = bank_io.stash_upload(current_user.id, rows)
        return render_template(
            "teacher/bank_preview.html", token=token, summary=summary, per_test=per_test,
            samples=samples, warnings=warnings + plan["warnings"], row_count=len(rows),
        )

    @bp.route("/bank/import/apply", methods=["POST"])
    @teacher_required
    def bank_apply():
        token = request.form.get("token", "")
        if request.form.get("backup_ok") != "on":
            flash("Please tick the box to confirm you have downloaded a backup.", "error")
            return redirect(url_for("teacher.bank_home"))
        started = bank_io.start_job(
            current_app._get_current_object(), token, current_user.id,
            delete_missing=request.form.get("delete_missing") == "on",
        )
        if not started:
            flash("That upload has expired. Please upload the file again.", "error")
            return redirect(url_for("teacher.bank_home"))
        return redirect(url_for("teacher.bank_job", token=token))

    @bp.route("/bank/job/<token>")
    @teacher_required
    def bank_job(token):
        if not bank_io.get_job(token, current_user.id):
            abort(404)
        return render_template("teacher/bank_job.html", token=token)

    @bp.route("/bank/job/<token>/status")
    @teacher_required
    def bank_job_status(token):
        job = bank_io.get_job(token, current_user.id)
        if not job:
            abort(404)
        return jsonify({
            "state": job["state"], "done": job["done"], "total": job["total"],
            "stats": job["stats"], "errors": job["errors"], "changes": len(job["log"]),
        })

    @bp.route("/bank/job/<token>/changes.xlsx")
    @teacher_required
    def bank_job_changes(token):
        job = bank_io.get_job(token, current_user.id)
        if not job:
            abort(404)
        return send_file(bank_io.export_change_log(job["log"]), as_attachment=True,
                         download_name="question_bank_changes.xlsx", mimetype=XLSX)
