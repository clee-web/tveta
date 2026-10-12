from flask import (
    render_template, send_file, abort, request, redirect, url_for, flash,
)

from config import STUDY_MODES, STUDENT_STATUSES
from database import get_db
from fees import compute_student_fees
from school import qr_data_uri, qr_png_bytes, student_qr_payload, class_for_student
from utils import login_required
from ._bp import admin_bp


# ------------------------------------------------------------------
# Student management (admitted students)
# ------------------------------------------------------------------
@admin_bp.route("/students")
@login_required
def students_list():
    status = request.args.get("status", "").strip()
    course = request.args.get("course", "").strip()
    year = request.args.get("year", "").strip()
    q = request.args.get("q", "").strip()

    query = "SELECT * FROM applications WHERE admission_number IS NOT NULL"
    params = []
    if status in STUDENT_STATUSES:
        query += " AND student_status = ?"
        params.append(status)
    if course:
        query += " AND course = ?"
        params.append(course)
    if year:
        query += " AND academic_year = ?"
        params.append(year)
    if q:
        query += (" AND (full_name LIKE ? OR admission_number LIKE ? "
                  "OR email LIKE ? OR phone LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like, like])
    query += " ORDER BY admission_number"

    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        students = [{"student": s, "info": compute_student_fees(db, s)} for s in rows]
        courses = [r["course"] for r in db.execute(
            """SELECT DISTINCT course FROM applications
               WHERE admission_number IS NOT NULL ORDER BY course COLLATE NOCASE"""
        ).fetchall()]
        years = [r["academic_year"] for r in db.execute(
            """SELECT DISTINCT academic_year FROM applications
               WHERE admission_number IS NOT NULL AND academic_year IS NOT NULL
               ORDER BY academic_year DESC"""
        ).fetchall()]

    return render_template(
        "students.html",
        students=students,
        courses=courses,
        years=years,
        statuses=STUDENT_STATUSES,
        filters={"status": status, "course": course, "year": year, "q": q},
    )


@admin_bp.route("/students/<application_id>")
@login_required
def student_profile(application_id):
    with get_db() as db:
        student = _get_application(db, application_id)
        if not student["admission_number"]:
            flash("This applicant has not been admitted yet.", "warning")
            return redirect(url_for("admin.application_detail", application_id=application_id))
        info = compute_student_fees(db, student)
        documents = db.execute(
            "SELECT * FROM documents WHERE application_id = ? ORDER BY uploaded_at",
            (application_id,),
        ).fetchall()
        cls = class_for_student(db, student)
        courses = [r["name"] for r in db.execute(
            "SELECT name FROM courses ORDER BY name COLLATE NOCASE"
        ).fetchall()]

    return render_template(
        "student_profile.html",
        student=student,
        info=info,
        documents=documents,
        cls=cls,
        courses=courses,
        study_modes=STUDY_MODES,
        statuses=STUDENT_STATUSES,
    )


@admin_bp.route("/students/<application_id>/edit", methods=["POST"])
@login_required
def edit_student(application_id):
    form = request.form
    with get_db() as db:
        student = _get_application(db, application_id)
        if not student["admission_number"]:
            flash("This applicant has not been admitted yet.", "warning")
            return redirect(url_for("admin.application_detail", application_id=application_id))

        first = form.get("first_name", "").strip()
        middle = form.get("middle_name", "").strip()
        surname = form.get("surname", "").strip()
        full_name = " ".join(p for p in (first, middle, surname) if p) or student["full_name"]

        db.execute(
            """UPDATE applications
               SET full_name = ?, surname = ?, middle_name = ?, first_name = ?,
                   email = ?, phone = ?, nationality = ?, date_of_birth = ?,
                   medical_condition = ?, course = ?, study_mode = ?, academic_year = ?,
                   start_date = ?, end_date = ?,
                   next_of_kin_name = ?, next_of_kin_relationship = ?,
                   sponsor_name = ?, sponsor_organisation = ?, sponsor_contact_person = ?
               WHERE id = ?""",
            (
                full_name, surname, middle, first,
                form.get("email", "").strip(), form.get("phone", "").strip(),
                form.get("nationality", "").strip(), form.get("date_of_birth", "").strip(),
                form.get("medical_condition", "").strip(),
                form.get("course", "").strip() or student["course"],
                form.get("study_mode", "").strip() or student["study_mode"],
                form.get("academic_year", "").strip() or student["academic_year"],
                form.get("start_date", "").strip(), form.get("end_date", "").strip(),
                form.get("next_of_kin_name", "").strip(),
                form.get("next_of_kin_relationship", "").strip(),
                form.get("sponsor_name", "").strip(),
                form.get("sponsor_organisation", "").strip(),
                form.get("sponsor_contact_person", "").strip(),
                application_id,
            ),
        )

    flash("Student details updated.", "success")
    return redirect(url_for("admin.student_profile", application_id=application_id))


@admin_bp.route("/students/<application_id>/status", methods=["POST"])
@login_required
def set_student_status(application_id):
    new_status = request.form.get("student_status", "").strip()
    if new_status not in STUDENT_STATUSES:
        flash("Invalid student status.", "danger")
        return redirect(url_for("admin.student_profile", application_id=application_id))

    with get_db() as db:
        student = _get_application(db, application_id)
        if not student["admission_number"]:
            flash("This applicant has not been admitted yet.", "warning")
            return redirect(url_for("admin.application_detail", application_id=application_id))
        db.execute(
            "UPDATE applications SET student_status = ? WHERE id = ?",
            (new_status, application_id),
        )

    flash(f"Student status changed to {new_status}.", "success")
    return redirect(url_for("admin.student_profile", application_id=application_id))


@admin_bp.route("/students/<application_id>/qr-card")
@login_required
def student_qr_card(application_id):
    with get_db() as db:
        student = _get_application(db, application_id)
    payload = student_qr_payload(student)
    return render_template("qr_card.html", student=student, qr=qr_data_uri(payload))


@admin_bp.route("/students/<application_id>/qr.png")
@login_required
def student_qr_png(application_id):
    with get_db() as db:
        student = _get_application(db, application_id)
    return send_file(
        qr_png_bytes(student_qr_payload(student)),
        as_attachment=True,
        download_name=f"{_safe_name(student)}.png",
        mimetype="image/png",
    )


@admin_bp.route("/classes/<int:class_id>/qr-cards")
@login_required
def class_qr_cards(class_id):
    with get_db() as db:
        cls = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        if not cls:
            abort(404)
        students = db.execute(
            """SELECT * FROM applications
               WHERE admission_number IS NOT NULL
                 AND course = ? AND academic_year = ? AND study_mode = ?
               ORDER BY full_name COLLATE NOCASE""",
            (cls["course"], cls["academic_year"], cls["study_mode"]),
        ).fetchall()

    cards = [
        {"student": s, "qr": qr_data_uri(student_qr_payload(s))}
        for s in students
    ]
    return render_template("qr_cards.html", cls=cls, cards=cards)


def _get_application(db, application_id):
    application = db.execute(
        "SELECT * FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    if not application:
        abort(404)
    return application


def _safe_name(student):
    code = student["admission_number"] or student["id"]
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in code)
