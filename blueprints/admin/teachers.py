import os
import re
import shutil
import sqlite3
from datetime import datetime

from flask import (
    render_template, request, redirect, url_for, flash, send_from_directory
)
from werkzeug.security import generate_password_hash

from config import UPLOAD_DIR, STUDY_MODES
from database import get_db
from fees import generate_student_password
from school import generate_teacher_username
from utils import login_required, save_upload
from ._bp import admin_bp


# ------------------------------------------------------------------
# Pages
# ------------------------------------------------------------------
@admin_bp.route("/teachers")
@login_required
def teachers():
    with get_db() as db:
        rows = db.execute("SELECT * FROM teachers ORDER BY full_name COLLATE NOCASE").fetchall()
        teachers_list = []
        for t in rows:
            classes = db.execute(
                """SELECT c.id, c.name FROM classes c
                   JOIN teacher_classes tc ON tc.class_id = c.id
                   WHERE tc.teacher_id = ?
                   ORDER BY c.name COLLATE NOCASE""",
                (t["id"],),
            ).fetchall()
            teachers_list.append({"teacher": t, "classes": classes})
        courses = db.execute(
            "SELECT name FROM courses ORDER BY name COLLATE NOCASE"
        ).fetchall()
        modes = db.execute(
            "SELECT DISTINCT study_mode FROM applications WHERE study_mode IS NOT NULL AND study_mode != ''"
        ).fetchall()

    mode_options = sorted({m["study_mode"] for m in modes} | set(STUDY_MODES))
    return render_template(
        "teachers.html",
        teachers=teachers_list,
        courses=courses,
        mode_options=mode_options,
    )


@admin_bp.route("/teachers/<int:teacher_id>")
@login_required
def teacher_detail(teacher_id):
    with get_db() as db:
        teacher = db.execute(
            "SELECT * FROM teachers WHERE id = ?", (teacher_id,)
        ).fetchone()
        if not teacher:
            flash("Teacher not found.", "danger")
            return redirect(url_for("admin.teachers"))

        documents = db.execute(
            "SELECT * FROM teacher_documents WHERE teacher_id = ? ORDER BY uploaded_at DESC",
            (teacher_id,),
        ).fetchall()

        all_classes = db.execute(
            """SELECT c.*,
                 (SELECT COUNT(*) FROM class_subjects s WHERE s.class_id = c.id) AS subject_count,
                 (SELECT COUNT(*) FROM applications a
                    WHERE a.admission_number IS NOT NULL
                      AND a.course = c.course
                      AND a.academic_year = c.academic_year
                      AND a.study_mode = c.study_mode) AS student_count
               FROM classes c
               ORDER BY c.academic_year DESC, c.course COLLATE NOCASE, c.study_mode""",
        ).fetchall()

        assigned = {
            r["class_id"]
            for r in db.execute(
                "SELECT class_id FROM teacher_classes WHERE teacher_id = ?",
                (teacher_id,),
            ).fetchall()
        }
        subjects = db.execute(
            "SELECT * FROM class_subjects ORDER BY name COLLATE NOCASE"
        ).fetchall()

    subjects_by_class = {}
    for s in subjects:
        subjects_by_class.setdefault(s["class_id"], []).append(s)

    return render_template(
        "teacher_detail.html",
        teacher=teacher,
        documents=documents,
        classes=all_classes,
        assigned=assigned,
        subjects_by_class=subjects_by_class,
    )


# ------------------------------------------------------------------
# Teacher CRUD
# ------------------------------------------------------------------
@admin_bp.route("/teachers/add", methods=["POST"])
@login_required
def add_teacher():
    full_name = request.form.get("full_name", "").strip()
    if not full_name:
        flash("Teacher name is required.", "danger")
        return redirect(url_for("admin.teachers"))

    with get_db() as db:
        username = generate_teacher_username(db, full_name)
        password = generate_student_password()
        cur = db.execute(
            """INSERT INTO teachers
               (full_name, email, phone, national_id, qualification,
                username, password_hash, initial_password, must_change_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                full_name,
                request.form.get("email", "").strip(),
                request.form.get("phone", "").strip(),
                request.form.get("national_id", "").strip(),
                request.form.get("qualification", "").strip(),
                username,
                generate_password_hash(password),
                password,
                datetime.utcnow().isoformat(),
            ),
        )
        teacher_id = cur.lastrowid
        saved = _save_teacher_files(db, teacher_id)

    extra = f" {saved} qualification document(s) uploaded." if saved else ""
    flash(
        f"Teacher “{full_name}” added. Username: {username} · Password: {password}.{extra}",
        "success",
    )
    return redirect(url_for("admin.teacher_detail", teacher_id=teacher_id))


@admin_bp.route("/teachers/<int:teacher_id>/edit", methods=["POST"])
@login_required
def edit_teacher(teacher_id):
    full_name = request.form.get("full_name", "").strip()
    if not full_name:
        flash("Teacher name is required.", "danger")
        return redirect(url_for("admin.teacher_detail", teacher_id=teacher_id))

    with get_db() as db:
        db.execute(
            """UPDATE teachers
               SET full_name = ?, email = ?, phone = ?, national_id = ?, qualification = ?
               WHERE id = ?""",
            (
                full_name,
                request.form.get("email", "").strip(),
                request.form.get("phone", "").strip(),
                request.form.get("national_id", "").strip(),
                request.form.get("qualification", "").strip(),
                teacher_id,
            ),
        )
        saved = _save_teacher_files(db, teacher_id)

    flash("Teacher details updated." + (f" {saved} document(s) added." if saved else ""), "success")
    return redirect(url_for("admin.teacher_detail", teacher_id=teacher_id))


@admin_bp.route("/teachers/<int:teacher_id>/delete", methods=["POST"])
@login_required
def delete_teacher(teacher_id):
    with get_db() as db:
        teacher = db.execute(
            "SELECT * FROM teachers WHERE id = ?", (teacher_id,)
        ).fetchone()
        if teacher:
            db.execute("DELETE FROM teacher_documents WHERE teacher_id = ?", (teacher_id,))
            db.execute("DELETE FROM teacher_classes WHERE teacher_id = ?", (teacher_id,))
            db.execute("DELETE FROM teachers WHERE id = ?", (teacher_id,))
            shutil.rmtree(os.path.join(UPLOAD_DIR, f"teacher_{teacher_id}"), ignore_errors=True)
            flash(f"Teacher “{teacher['full_name']}” removed.", "success")
    return redirect(url_for("admin.teachers"))


@admin_bp.route("/teachers/<int:teacher_id>/reset-password", methods=["POST"])
@login_required
def reset_teacher_password(teacher_id):
    with get_db() as db:
        teacher = db.execute(
            "SELECT * FROM teachers WHERE id = ?", (teacher_id,)
        ).fetchone()
        if not teacher:
            flash("Teacher not found.", "danger")
            return redirect(url_for("admin.teachers"))

        password = generate_student_password()
        db.execute(
            """UPDATE teachers
               SET password_hash = ?, initial_password = ?, must_change_password = 1
               WHERE id = ?""",
            (generate_password_hash(password), password, teacher_id),
        )

    flash(
        f"New password for {teacher['full_name']}: {password}. "
        "They must change it on next login.",
        "success",
    )
    return redirect(url_for("admin.teacher_detail", teacher_id=teacher_id))


@admin_bp.route("/teachers/<int:teacher_id>/classes", methods=["POST"])
@login_required
def save_teacher_classes(teacher_id):
    class_ids = request.form.getlist("class_ids")
    with get_db() as db:
        db.execute("DELETE FROM teacher_classes WHERE teacher_id = ?", (teacher_id,))
        for raw in class_ids:
            try:
                cid = int(raw)
            except (TypeError, ValueError):
                continue
            db.execute(
                "INSERT OR IGNORE INTO teacher_classes (teacher_id, class_id) VALUES (?, ?)",
                (teacher_id, cid),
            )
    flash("Class assignments saved.", "success")
    return redirect(url_for("admin.teacher_detail", teacher_id=teacher_id))


@admin_bp.route("/teachers/<int:teacher_id>/document/<stored_name>")
@login_required
def view_teacher_document(teacher_id, stored_name):
    folder = os.path.join(UPLOAD_DIR, f"teacher_{teacher_id}")
    return send_from_directory(folder, stored_name)


# ------------------------------------------------------------------
# Classes
# ------------------------------------------------------------------
@admin_bp.route("/classes")
@login_required
def classes():
    return render_template("classes.html", **_load_classes())


@admin_bp.route("/classes/add", methods=["POST"])
@login_required
def add_class():
    course = request.form.get("course", "").strip()
    academic_year = request.form.get("academic_year", "").strip()
    study_mode = request.form.get("study_mode", "").strip()

    if not course or not academic_year or not study_mode:
        flash("Course, academic year and study mode are required.", "danger")
        return redirect(url_for("admin.classes"))

    if not re.fullmatch(r"\d{4}", academic_year):
        # A class stored as "26" never matches an application's "2026", so its
        # roster would look empty with nothing obviously wrong.
        flash("Enter the academic year as four digits, e.g. 2026.", "danger")
        return redirect(url_for("admin.classes"))

    name = request.form.get("name", "").strip() or f"{course} {academic_year} {study_mode}"
    try:
        with get_db() as db:
            db.execute(
                """INSERT INTO classes (name, course, academic_year, study_mode, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, course, academic_year, study_mode, datetime.utcnow().isoformat()),
            )
        flash(f"Class “{name}” created.", "success")
    except sqlite3.IntegrityError:
        flash("A class for that course, year and study mode already exists.", "danger")

    return redirect(url_for("admin.classes"))


@admin_bp.route("/classes/<int:class_id>/delete", methods=["POST"])
@login_required
def delete_class(class_id):
    with get_db() as db:
        cls = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        if cls:
            db.execute("DELETE FROM teacher_classes WHERE class_id = ?", (class_id,))
            db.execute("DELETE FROM class_subjects WHERE class_id = ?", (class_id,))
            db.execute("DELETE FROM attendance WHERE class_id = ?", (class_id,))
            db.execute(
                "DELETE FROM exam_results WHERE exam_id IN (SELECT id FROM exams WHERE class_id = ?)",
                (class_id,),
            )
            db.execute("DELETE FROM exams WHERE class_id = ?", (class_id,))
            db.execute("DELETE FROM classes WHERE id = ?", (class_id,))
            flash(f"Class “{cls['name']}” removed.", "success")
    return redirect(url_for("admin.classes"))


@admin_bp.route("/classes/<int:class_id>/subjects/add", methods=["POST"])
@login_required
def add_subject(class_id):
    name = request.form.get("name", "").strip()
    if not name:
        flash("Subject/module name is required.", "danger")
        return redirect(url_for("admin.classes"))

    with get_db() as db:
        exists = db.execute(
            "SELECT 1 FROM class_subjects WHERE class_id = ? AND name = ?",
            (class_id, name),
        ).fetchone()
        if exists:
            flash(f"“{name}” is already in this class.", "warning")
        else:
            db.execute(
                "INSERT INTO class_subjects (class_id, name, created_at) VALUES (?, ?, ?)",
                (class_id, name, datetime.utcnow().isoformat()),
            )
            flash(f"Subject “{name}” added.", "success")

    return redirect(url_for("admin.classes"))


@admin_bp.route("/classes/subjects/<int:subject_id>/delete", methods=["POST"])
@login_required
def delete_subject(subject_id):
    with get_db() as db:
        db.execute("DELETE FROM class_subjects WHERE id = ?", (subject_id,))
    flash("Subject removed.", "success")
    return redirect(url_for("admin.classes"))


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _save_teacher_files(db, teacher_id):
    """Store any uploaded qualification documents for a teacher."""
    files = request.files.getlist("qualification_files")
    category = request.form.get("document_category", "").strip() or "Qualification"
    now = datetime.utcnow().isoformat()
    saved = 0

    for file_storage in files:
        if not file_storage or not file_storage.filename:
            continue
        try:
            meta = save_upload(file_storage, f"teacher_{teacher_id}", category)
        except ValueError as exc:
            flash(str(exc), "warning")
            continue
        if not meta:
            continue
        db.execute(
            """INSERT INTO teacher_documents
               (teacher_id, category, original_name, stored_name, mime_type, size_bytes, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                teacher_id, meta["category"], meta["original"], meta["stored"],
                meta["mime"], meta["size"], now,
            ),
        )
        saved += 1
    return saved


def _load_classes():
    with get_db() as db:
        class_rows = db.execute(
            """SELECT c.*,
                 (SELECT COUNT(*) FROM class_subjects s WHERE s.class_id = c.id) AS subject_count,
                 (SELECT COUNT(*) FROM teacher_classes tc WHERE tc.class_id = c.id) AS teacher_count,
                 (SELECT COUNT(*) FROM applications a
                    WHERE a.admission_number IS NOT NULL
                      AND a.course = c.course
                      AND a.academic_year = c.academic_year
                      AND a.study_mode = c.study_mode) AS student_count
               FROM classes c
               ORDER BY c.academic_year DESC, c.course COLLATE NOCASE, c.study_mode""",
        ).fetchall()
        subjects = db.execute(
            "SELECT * FROM class_subjects ORDER BY name COLLATE NOCASE"
        ).fetchall()
        course_rows = db.execute(
            "SELECT name FROM courses ORDER BY name COLLATE NOCASE"
        ).fetchall()
        mode_rows = db.execute(
            "SELECT DISTINCT study_mode FROM applications WHERE study_mode IS NOT NULL AND study_mode != ''"
        ).fetchall()

    subjects_by_class = {}
    for s in subjects:
        subjects_by_class.setdefault(s["class_id"], []).append(s)

    mode_options = sorted({m["study_mode"] for m in mode_rows} | set(STUDY_MODES))
    return {
        "classes": class_rows,
        "subjects_by_class": subjects_by_class,
        "courses": course_rows,
        "mode_options": mode_options,
        "current_year": datetime.utcnow().year,
    }
