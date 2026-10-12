import os
import shutil

from flask import (
    render_template, request, redirect, url_for,
    flash, abort
)

from config import UPLOAD_DIR
from database import get_db
from fees import compute_student_fees
from progression import periods_for_course
from utils import login_required
from ._bp import admin_bp


@admin_bp.route("/application/<application_id>")
@login_required
def application_detail(application_id):
    with get_db() as db:
        application = db.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        if not application:
            abort(404)
        documents = db.execute(
            "SELECT * FROM documents WHERE application_id = ? ORDER BY uploaded_at",
            (application_id,),
        ).fetchall()
        fee_info = compute_student_fees(db, application)
        # A short course has stages, a diploma semesters - the payment form can only
        # tag money against periods this student's course actually has.
        terms = periods_for_course(db, application["course"])

    # The list links carry their own filters through, so "back" returns to the
    # page and search the reviewer was actually on.
    keep = {
        k: request.args.get(k)
        for k in ("status", "q", "page", "per_page")
        if request.args.get(k)
    }
    back_link = url_for("admin.applications", **keep) if keep else url_for("admin.applications")

    return render_template(
        "application_detail.html",
        application=application,
        documents=documents,
        fee_info=fee_info,
        terms=terms,
        back_link=back_link,
    )


def _back_or_detail(application_id):
    """Where a POST should land.

    The detail form carries the list URL it came from, including page and filters,
    so reviewing page 3 of pending applicants does not jump back to page 1 after
    every decision. Only a same-site path is accepted.
    """
    target = request.form.get("back", "")
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(url_for("admin.application_detail", application_id=application_id))


@admin_bp.route("/application/<application_id>/status", methods=["POST"])
@login_required
def update_status(application_id):
    new_status = request.form.get("status")
    notes = request.form.get("notes", "").strip()
    action = request.form.get("action", "save")

    # "Save notes" submits no status: the three status buttons are the only
    # controls allowed to change it, so notes can be saved without touching it.
    if not new_status and request.form.get("save_notes"):
        with get_db() as db:
            db.execute(
                "UPDATE applications SET notes = ? WHERE id = ?",
                (notes, application_id),
            )
        flash("Notes saved.", "success")
        return _back_or_detail(application_id)

    if new_status not in ("pending", "verified", "rejected"):
        flash("Invalid status.", "danger")
        return _back_or_detail(application_id)

    with get_db() as db:
        db.execute(
            "UPDATE applications SET status = ?, notes = ? WHERE id = ?",
            (new_status, notes, application_id),
        )

    if action == "verify_and_print" and new_status == "verified":
        flash("Application verified. Print dialog opening…", "success")
        return redirect(url_for(
            "admin.print_application",
            application_id=application_id,
            auto=1,
        ))

    flash(f"Application marked as {new_status}.", "success")
    return _back_or_detail(application_id)


@admin_bp.route("/application/<application_id>/delete", methods=["POST"])
@login_required
def delete_application(application_id):
    with get_db() as db:
        application = db.execute(
            "SELECT full_name FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        if not application:
            abort(404)

        # fee_payments, attendance and exam_results point at the application by
        # plain text id with no foreign key, so they survive the delete unless
        # removed here. Confirmed money is a different matter — that is ledger
        # history, and the office has to void it deliberately, not by accident.
        ledger = db.execute(
            """SELECT COUNT(*) AS c, COALESCE(SUM(amount), 0) AS total FROM fee_payments
               WHERE application_id = ? AND status = 'confirmed'""",
            (application_id,),
        ).fetchone()
        if ledger["c"]:
            flash(
                f"{application['full_name']} has {ledger['c']} confirmed payment(s) "
                f"totalling {ledger['total']:,.0f}. Void or delete them on this page first, "
                "then delete the application.",
                "danger",
            )
            return redirect(url_for("admin.application_detail", application_id=application_id))

        for table, column in (
            ("fee_payments", "application_id"),
            ("attendance", "student_id"),
            ("exam_results", "student_id"),
            ("documents", "application_id"),
        ):
            db.execute(f"DELETE FROM {table} WHERE {column} = ?", (application_id,))
        db.execute("DELETE FROM applications WHERE id = ?", (application_id,))

    folder = os.path.join(UPLOAD_DIR, application_id)
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)

    flash("Application deleted.", "info")
    return redirect(url_for("admin.applications"))


@admin_bp.route("/application/<application_id>/print")
@login_required
def print_application(application_id):
    auto = request.args.get("auto") == "1"
    with get_db() as db:
        application = db.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        if not application:
            abort(404)

    return render_template(
        "print_application.html",
        application=application,
        auto_print=auto,
    )