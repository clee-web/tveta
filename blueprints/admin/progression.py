from flask import flash, redirect, render_template, request, session, url_for

from config import STUDY_MODES
from database import get_db
from progression import (
    apply_decision, queue as requests_queue, request_period, snapshot,
)
from utils import login_required

from ._bp import admin_bp


@admin_bp.route("/progression")
@login_required
def progression():
    tab = request.args.get("tab", "pending")
    if tab not in ("pending", "decided"):
        tab = "pending"

    with get_db() as db:
        cards = []
        for row in requests_queue(db, tab):
            student = db.execute(
                "SELECT * FROM applications WHERE id = ?", (row["application_id"],)
            ).fetchone()
            if student:
                cards.append({
                    "request": row,
                    "student": student,
                    "evidence": snapshot(db, student),
                })

        counts = {
            "pending": len([c for c in cards if c["request"]["status"] == "pending"]),
            "decided": db.execute(
                "SELECT COUNT(*) AS c FROM progression_requests WHERE status = 'decided'"
            ).fetchone()["c"],
            "deferred": _count_students(db, "deferred"),
            "repeating": _count_students(db, "repeating"),
        }
        courses = db.execute("SELECT name FROM courses ORDER BY name").fetchall()

    return render_template(
        "progression.html",
        tab=tab,
        cards=cards,
        counts=counts,
        courses=courses,
        study_modes=STUDY_MODES,
    )


@admin_bp.route("/progression/file", methods=["POST"])
@login_required
def file_progression():
    """Raise a request on behalf of a student who never logs in."""
    admission = request.form.get("admission_number", "").strip()
    kind = request.form.get("kind", "").strip()
    if kind not in ("advance", "defer", "complete"):
        kind = "advance"

    with get_db() as db:
        student = db.execute(
            "SELECT * FROM applications WHERE admission_number = ?", (admission,)
        ).fetchone()
        if not student:
            flash(f"No admitted student found with reference “{admission}”.", "danger")
            return redirect(url_for("admin.progression"))

        _, error = request_period(db, student, kind, request.form.get("reason"), "admin")
        if error:
            flash(error, "warning")
            return redirect(url_for("admin.progression"))

    flash(f"Request filed for {student['full_name']}.", "success")
    return redirect(url_for("admin.progression"))


@admin_bp.route("/progression/<int:request_id>/decide", methods=["POST"])
@login_required
def decide_progression(request_id):
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM progression_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if not row:
            flash("That request has already been handled.", "warning")
            return redirect(url_for("admin.progression"))

        ok, message = apply_decision(
            db, row,
            request.form.get("decision", "").strip(),
            session.get("admin_username", "admin"),
            request.form,
        )

    flash(message, "success" if ok else "danger")
    return redirect(url_for("admin.progression", tab="decided" if ok else "pending"))


def _count_students(db, status):
    return db.execute(
        "SELECT COUNT(*) AS c FROM applications WHERE student_status = ?", (status,)
    ).fetchone()["c"]
