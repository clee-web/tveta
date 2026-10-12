import json
import sqlite3
from datetime import datetime

from flask import (
    render_template, request, redirect, url_for,
    flash, abort, session
)
from werkzeug.security import generate_password_hash

from config import (
    TERMS, PAYMENT_INSTRUCTIONS, SCHOOL_RULES, SCHOOL_MOTTO,
)
from database import get_db
from fees import (
    collect_other_items, compute_student_fees, generate_admission_number,
    generate_student_password, other_items_total, parse_other_items,
    registration_fee_for,
)
from progression import course_for_name, course_ladder, periods_for_course
from utils import login_required
from ._bp import admin_bp


# ------------------------------------------------------------------
# Fee structures
# ------------------------------------------------------------------
@admin_bp.route("/fees")
@login_required
def fees():
    with get_db() as db:
        raw_structures = db.execute(
            """SELECT * FROM fee_structures
               ORDER BY academic_year DESC, course_name COLLATE NOCASE, term"""
        ).fetchall()

        structures = []
        for row in raw_structures:
            data = dict(row)
            data["other_items"] = parse_other_items(data.get("other_items"))
            structures.append(data)

        courses = db.execute(
            "SELECT * FROM courses ORDER BY name COLLATE NOCASE"
        ).fetchall()

        student_rows = db.execute(
            """SELECT * FROM applications
               WHERE admission_number IS NOT NULL
               ORDER BY admission_number"""
        ).fetchall()

        students = [
            {"student": s, "info": compute_student_fees(db, s)}
            for s in student_rows
        ]

        # Both maps key on the course name, which is how fee_structures relate to
        # courses. The add-form uses them to offer only the periods a course really
        # has and to show the registration fee that course charges.
        fallback_fee = registration_fee_for(db, None)
        periods_by_course = {c["name"]: course_ladder(c) for c in courses}
        registration_by_course = {
            c["name"]: (c["registration_fee"]
                        if c["registration_fee"] is not None else fallback_fee)
            for c in courses
        }

    return render_template(
        "fees.html",
        structures=structures,
        courses=courses,
        students=students,
        terms=TERMS,
        periods_by_course=periods_by_course,
        registration_by_course=registration_by_course,
        current_year=datetime.utcnow().year,
    )


@admin_bp.route("/fees/add", methods=["POST"])
@login_required
def add_fee():
    course_name = request.form.get("course_name", "").strip()
    year = request.form.get("academic_year", "").strip()
    term = request.form.get("term", "").strip()

    if not course_name or not year or not term:
        flash("Course, academic year and term are required.", "danger")
        return redirect(url_for("admin.fees"))

    try:
        with get_db() as db:
            course = course_for_name(db, course_name)
            if course:
                ladder = course_ladder(course)
                if term not in ladder:
                    flash(
                        f"{course_name} does not run a {term}. It has "
                        f"{', '.join(ladder)}.", "danger")
                    return redirect(url_for("admin.fees"))

            items = collect_other_items(request.form)
            db.execute(
                """INSERT INTO fee_structures
                   (course_name, academic_year, term,
                    tuition_fee, exam_fee, other_fee, other_items, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    course_name, year, term,
                    _amount("tuition_fee") or 0.0, _amount("exam_fee") or 0.0,
                    other_items_total(items),
                    json.dumps(items) if items else None,
                    datetime.utcnow().isoformat(),
                ),
            )
        flash(f"Fee structure saved for {course_name} — {year} {term}.", "success")
    except sqlite3.IntegrityError:
        flash("A fee structure for that course, year and term already exists.", "danger")

    return redirect(url_for("admin.fees"))


@admin_bp.route("/fees/<int:fee_id>/edit", methods=["POST"])
@login_required
def edit_fee(fee_id):
    with get_db() as db:
        items = collect_other_items(request.form)
        db.execute(
            """UPDATE fee_structures
               SET tuition_fee = ?, exam_fee = ?, other_fee = ?, other_items = ?
               WHERE id = ?""",
            (_amount("tuition_fee") or 0.0, _amount("exam_fee") or 0.0,
             other_items_total(items),
             json.dumps(items) if items else None, fee_id),
        )
    flash("Fee structure updated.", "success")
    return redirect(url_for("admin.fees"))


@admin_bp.route("/fees/<int:fee_id>/delete", methods=["POST"])
@login_required
def delete_fee(fee_id):
    with get_db() as db:
        db.execute("DELETE FROM fee_structures WHERE id = ?", (fee_id,))
    flash("Fee structure deleted.", "info")
    return redirect(url_for("admin.fees"))


# ------------------------------------------------------------------
# Student fees & admission
# ------------------------------------------------------------------
@admin_bp.route("/student/<application_id>/registration-paid", methods=["POST"])
@login_required
def mark_registration_paid(application_id):
    with get_db() as db:
        application = _get_application(db, application_id)

        if application["registration_paid"]:
            flash("Registration fee is already marked as paid.", "info")
            return redirect(url_for("admin.application_detail", application_id=application_id))

        now = datetime.utcnow().isoformat()
        fee = registration_fee_for(db, application["course"])
        db.execute(
            """INSERT INTO fee_payments
               (application_id, amount, category, academic_year, method,
                status, recorded_by, paid_at, created_at)
               VALUES (?, ?, 'Registration', ?, 'cash', 'confirmed', ?, ?, ?)""",
            (application_id, fee, application["academic_year"],
             session.get("admin_username", "admin"), now, now),
        )
        db.execute(
            "UPDATE applications SET registration_paid = 1, registration_paid_at = ? WHERE id = ?",
            (now, application_id),
        )

    flash(f"Registration fee of KES {fee:,.0f} recorded.", "success")
    return redirect(url_for("admin.application_detail", application_id=application_id))


@admin_bp.route("/student/<application_id>/payment", methods=["POST"])
@login_required
def record_payment(application_id):
    amount = _amount("amount")
    if amount is None:
        flash("That amount is not a number we can read. Enter it as digits, e.g. 4500.", "danger")
        return redirect(url_for("admin.application_detail", application_id=application_id))
    if amount <= 0:
        flash("Enter a payment amount greater than zero.", "danger")
        return redirect(url_for("admin.application_detail", application_id=application_id))

    with get_db() as db:
        application = _get_application(db, application_id)
        _insert_payment(
            db,
            application_id,
            amount,
            request.form.get("category", "Tuition").strip() or "Tuition",
            application["academic_year"],
            request.form.get("term", "").strip() or None,
            request.form.get("method", "").strip() or None,
            request.form.get("reference", "").strip() or None,
            status="confirmed",
            recorded_by=session.get("admin_username", "admin"),
        )

    flash("Payment recorded.", "success")
    return redirect(url_for("admin.application_detail", application_id=application_id))


@admin_bp.route("/student/<application_id>/payment/<int:payment_id>/confirm", methods=["POST"])
@login_required
def confirm_payment(application_id, payment_id):
    void = request.form.get("action") == "void"
    with get_db() as db:
        _get_application(db, application_id)
        payment = db.execute(
            "SELECT id, amount, status FROM fee_payments WHERE id = ? AND application_id = ?",
            (payment_id, application_id),
        ).fetchone()
        if not payment:
            flash("That payment entry no longer exists.", "danger")
            return redirect(url_for("admin.application_detail", application_id=application_id))

        if void:
            db.execute("UPDATE fee_payments SET status = 'pending' WHERE id = ?", (payment_id,))
            message = "Confirmation reversed. The payment is pending again."
            category = "warning"
        elif payment["status"] == "confirmed":
            # Re-confirming used to claim success a second time; say what actually
            # happened instead of implying a change was made.
            message = "This payment was already confirmed."
            category = "info"
        else:
            db.execute(
                "UPDATE fee_payments SET status = 'confirmed', recorded_by = ? WHERE id = ?",
                (session.get("admin_username", "admin"), payment_id),
            )
            message = f"Payment of {payment['amount']:,.0f} confirmed."
            category = "success"

    # The alert bell confirms payments in place, so it asks to return where it came from.
    fallback = url_for("admin.application_detail", application_id=application_id)
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        fallback = target
    flash(message, category)
    return redirect(fallback)


@admin_bp.route("/student/<application_id>/payment/<int:payment_id>/delete", methods=["POST"])
@login_required
def delete_payment(application_id, payment_id):
    with get_db() as db:
        _get_application(db, application_id)
        db.execute(
            "DELETE FROM fee_payments WHERE id = ? AND application_id = ?",
            (payment_id, application_id),
        )
    flash("Payment entry removed.", "info")
    return redirect(url_for("admin.application_detail", application_id=application_id))


@admin_bp.route("/student/<application_id>/admit", methods=["POST"])
@login_required
def issue_admission(application_id):
    with get_db() as db:
        application = _get_application(db, application_id)

        if not application["registration_paid"]:
            flash("Registration fee must be paid before issuing an admission letter.", "warning")
            return redirect(url_for("admin.application_detail", application_id=application_id))

        if application["admission_number"]:
            flash("An admission letter has already been issued.", "info")
            return redirect(url_for("admin.admission_letter", application_id=application_id))

        number = generate_admission_number(db, application)
        password = generate_student_password()
        db.execute(
            """UPDATE applications
               SET admission_number = ?, admitted_at = ?, student_password_hash = ?,
                   student_initial_password = ?, must_change_password = 1,
                   student_status = 'active'
               WHERE id = ?""",
            (number, datetime.utcnow().isoformat(),
             generate_password_hash(password), password, application_id),
        )

    flash(
        f"Admission letter issued. Admission number: {number}. "
        f"First-login password: {password} (also shown on the admission letter).",
        "success",
    )
    return redirect(url_for("admin.admission_letter", application_id=application_id))


@admin_bp.route("/student/<application_id>/reset-password", methods=["POST"])
@login_required
def reset_student_password(application_id):
    with get_db() as db:
        application = _get_application(db, application_id)

        if not application["admission_number"]:
            flash("Issue the admission letter before resetting a password.", "warning")
            return redirect(url_for("admin.application_detail", application_id=application_id))

        password = generate_student_password()
        db.execute(
            """UPDATE applications
               SET student_password_hash = ?, student_initial_password = ?,
                   must_change_password = 1
               WHERE id = ?""",
            (generate_password_hash(password), password, application_id),
        )

    flash(
        f"New portal password for {application['full_name']}: {password}. "
        f"It is also shown on the admission letter.",
        "success",
    )
    return redirect(url_for("admin.application_detail", application_id=application_id))


@admin_bp.route("/student/<application_id>/registration-slip")
@login_required
def registration_slip(application_id):
    with get_db() as db:
        application = _get_application(db, application_id)
        fee = registration_fee_for(db, application["course"])
    return render_template(
        "registration_slip.html",
        application=application,
        registration_fee=fee,
    )


@admin_bp.route("/application/<application_id>/admission-letter")
@admin_bp.route("/student/<application_id>/admission-letter")
@login_required
def admission_letter(application_id):
    with get_db() as db:
        application = _get_application(db, application_id)
        if not application["admission_number"]:
            flash("Issue the admission letter first.", "warning")
            return redirect(url_for("admin.application_detail", application_id=application_id))
        info = compute_student_fees(db, application)
        terms = periods_for_course(db, application["course"])

    return render_template(
        "admission_letter.html",
        application=application,
        info=info,
        terms=terms,
        payment_instructions=PAYMENT_INSTRUCTIONS,
        school_rules=SCHOOL_RULES,
        school_motto=SCHOOL_MOTTO,
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
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


def _get_application(db, application_id):
    application = db.execute(
        "SELECT * FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    if not application:
        abort(404)
    return application


def _insert_payment(db, application_id, amount, category, year, term,
                    method, reference, status, recorded_by):
    now = datetime.utcnow().isoformat()
    db.execute(
        """INSERT INTO fee_payments
           (application_id, amount, category, term, academic_year, method,
            reference, status, recorded_by, paid_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (application_id, amount, category, term, year, method,
         reference, status, recorded_by, now, now),
    )
