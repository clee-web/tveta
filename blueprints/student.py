from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash, send_file
)
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db
from fees import compute_student_fees
from progression import history, periods_for_course, request_period, snapshot
from school import (
    class_for_student, qr_data_uri, qr_png_bytes, student_qr_payload,
)
from utils import student_login_required

bp = Blueprint("student", __name__, url_prefix="/student")


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("student_id"):
        return redirect(url_for("student.dashboard"))

    if request.method == "POST":
        admission_number = request.form.get("admission_number", "").strip()
        password = request.form.get("password", "")

        with get_db() as db:
            student = db.execute(
                "SELECT * FROM applications WHERE admission_number = ?",
                (admission_number,),
            ).fetchone()

        if (
            student
            and student["student_password_hash"]
            and check_password_hash(student["student_password_hash"], password)
        ):
            session.clear()
            session["student_id"] = student["id"]
            session["student_name"] = student["full_name"]
            if student["must_change_password"]:
                flash("Welcome! Please set a new password to continue.", "info")
                return redirect(url_for("student.set_password"))
            flash(f"Welcome, {student['full_name']}.", "success")
            return redirect(url_for("student.dashboard"))

        flash("Invalid admission number or password.", "danger")

    return render_template("student_login.html")


@bp.route("/set-password", methods=["GET", "POST"])
def set_password():
    """First-login step: replace the password printed on the admission letter."""
    if not session.get("student_id"):
        flash("Please log in to your student portal.", "warning")
        return redirect(url_for("student.login"))

    with get_db() as db:
        student = db.execute(
            "SELECT * FROM applications WHERE id = ?", (session["student_id"],)
        ).fetchone()

        if not student:
            session.clear()
            return redirect(url_for("student.login"))

        if not student["must_change_password"]:
            return redirect(url_for("student.dashboard"))

        if request.method == "POST":
            new = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")

            if len(new) < 8:
                flash("The new password must be at least 8 characters.", "danger")
            elif new != confirm:
                flash("The new passwords do not match.", "danger")
            else:
                db.execute(
                    """UPDATE applications
                       SET student_password_hash = ?, student_initial_password = NULL,
                           must_change_password = 0
                       WHERE id = ?""",
                    (generate_password_hash(new), student["id"]),
                )
                # They are already who they say they are, and they just proved it by
                # supplying the office-issued password. Bouncing them back to the
                # login screen only invites a weak "make it the same again" choice.
                flash("Password changed. You are signed in with your new password.", "success")
                return redirect(url_for("student.dashboard"))

    return render_template("student_set_password.html", student=student)


@bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("student.login"))


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------
@bp.route("/")
@student_login_required
def dashboard():
    with get_db() as db:
        student = db.execute(
            "SELECT * FROM applications WHERE id = ?", (session["student_id"],)
        ).fetchone()
        if not student:
            session.clear()
            return redirect(url_for("student.login"))
        info = compute_student_fees(db, student)
        # Declaring a payment only makes sense against a period this course has:
        # a short course has stages, a diploma semesters.
        terms = periods_for_course(db, student["course"])

        cls = class_for_student(db, student)
        attendance = None
        if cls:
            marks = db.execute(
                """SELECT attend_date, status FROM attendance
                   WHERE student_id = ? AND class_id = ?
                   ORDER BY attend_date DESC LIMIT 60""",
                (student["id"], cls["id"]),
            ).fetchall()
            present = sum(1 for m in marks if m["status"] == "present")
            attendance = {
                "marks": marks,
                "present": present,
                "absent": len(marks) - present,
                "total": len(marks),
                "rate": round(present / len(marks) * 100) if marks else None,
            }

    return render_template(
        "student_dashboard.html",
        student=student,
        info=info,
        cls=cls,
        attendance=attendance,
        terms=terms,
        current_year=datetime.utcnow().year,
    )


@bp.route("/payment", methods=["POST"])
@student_login_required
def declare_payment():
    amount = _amount("amount")
    if amount is None:
        flash("That amount is not a number we can read. Enter it as digits, e.g. 4500.", "danger")
        return redirect(url_for("student.dashboard"))
    if amount <= 0:
        flash("Enter the amount you have paid.", "danger")
        return redirect(url_for("student.dashboard"))

    with get_db() as db:
        student = db.execute(
            "SELECT * FROM applications WHERE id = ?", (session["student_id"],)
        ).fetchone()
        if not student:
            session.clear()
            return redirect(url_for("student.login"))

        now = datetime.utcnow().isoformat()
        db.execute(
            """INSERT INTO fee_payments
               (application_id, amount, category, term, academic_year, method,
                reference, status, recorded_by, paid_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 'student', ?, ?)""",
            (
                student["id"], amount,
                request.form.get("category", "Tuition").strip() or "Tuition",
                request.form.get("term", "").strip() or None,
                student["academic_year"],
                request.form.get("method", "").strip() or None,
                request.form.get("reference", "").strip() or None,
                now, now,
            ),
        )

    flash("Payment notification submitted. The accounts office will confirm it.", "success")
    return redirect(url_for("student.dashboard"))


@bp.route("/password", methods=["POST"])
@student_login_required
def change_password():
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    with get_db() as db:
        student = db.execute(
            "SELECT * FROM applications WHERE id = ?", (session["student_id"],)
        ).fetchone()

        if not student or not student["student_password_hash"] or not check_password_hash(
            student["student_password_hash"], current
        ):
            flash("Your current password is incorrect.", "danger")
            return redirect(url_for("student.dashboard"))

        if len(new) < 8:
            flash("The new password must be at least 8 characters.", "danger")
            return redirect(url_for("student.dashboard"))

        if new != confirm:
            flash("The new passwords do not match.", "danger")
            return redirect(url_for("student.dashboard"))

        db.execute(
            """UPDATE applications
               SET student_password_hash = ?, student_initial_password = NULL,
                   must_change_password = 0
               WHERE id = ?""",
            (generate_password_hash(new), student["id"]),
        )

    flash("Password updated successfully.", "success")
    return redirect(url_for("student.dashboard"))


@bp.route("/exams")
@student_login_required
def exams():
    with get_db() as db:
        student = db.execute(
            "SELECT * FROM applications WHERE id = ?", (session["student_id"],)
        ).fetchone()
        if not student:
            session.clear()
            return redirect(url_for("student.login"))

        cls = class_for_student(db, student)
        rows = []
        if cls:
            rows = db.execute(
                """SELECT e.*, s.name AS subject_name, r.score, r.grade
                   FROM exams e
                   LEFT JOIN class_subjects s ON s.id = e.subject_id
                   LEFT JOIN exam_results r ON r.exam_id = e.id AND r.student_id = ?
                   WHERE e.class_id = ?
                     AND (e.academic_year IS NULL OR e.academic_year = ?)
                   ORDER BY e.created_at DESC""",
                (student["id"], cls["id"], cls["academic_year"]),
            ).fetchall()

    return render_template("student_exams.html", student=student, cls=cls, exams=rows)


@bp.route("/continuation")
@student_login_required
def continuation():
    with get_db() as db:
        student = _student(db)
        if not student:
            session.clear()
            return redirect(url_for("student.login"))
        evidence = snapshot(db, student)
        past = history(db, student["id"])

    return render_template(
        "student_continuation.html", student=student, evidence=evidence, past=past
    )


@bp.route("/continuation/request", methods=["POST"])
@student_login_required
def request_continuation():
    kind = request.form.get("kind", "").strip()
    if kind not in ("advance", "defer", "complete"):
        kind = "advance"

    with get_db() as db:
        student = _student(db)
        if not student:
            session.clear()
            return redirect(url_for("student.login"))

        _, error = request_period(db, student, kind, request.form.get("reason"), "student")

    if error:
        flash(error, "warning")
    elif kind == "defer":
        flash("Your request to pause studies has been sent to the office.", "success")
    elif kind == "complete":
        flash("The office has been told you finished your last term.", "success")
    else:
        flash("Your continuation request has been sent for approval.", "success")
    return redirect(url_for("student.continuation"))


@bp.route("/qr")
@student_login_required
def qr():
    with get_db() as db:
        student = db.execute(
            "SELECT * FROM applications WHERE id = ?", (session["student_id"],)
        ).fetchone()
        if not student:
            session.clear()
            return redirect(url_for("student.login"))

    payload = student_qr_payload(student)
    return render_template("student_qr.html", student=student, qr=qr_data_uri(payload))


@bp.route("/qr.png")
@student_login_required
def qr_png():
    with get_db() as db:
        student = db.execute(
            "SELECT * FROM applications WHERE id = ?", (session["student_id"],)
        ).fetchone()
        if not student:
            session.clear()
            return redirect(url_for("student.login"))

    code = student["admission_number"] or student["id"]
    filename = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in code)
    return send_file(
        qr_png_bytes(student_qr_payload(student)),
        as_attachment=True,
        download_name=f"{filename}.png",
        mimetype="image/png",
    )


def _student(db):
    return db.execute(
        "SELECT * FROM applications WHERE id = ?", (session["student_id"],)
    ).fetchone()


def _amount(field):
    raw = request.form.get(field, "").strip().replace(",", "")
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        # None, not 0.0: a mistyped amount must be refused with a clear message,
        # not silently booked as a free payment.
        return None
