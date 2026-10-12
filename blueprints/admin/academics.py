import sqlite3
from datetime import datetime

from flask import (
    render_template, request, redirect, url_for, flash, abort
)

from config import PERIOD_MODES, REGISTRATION_FEE, SHORT_COURSE_PERIODS
from database import get_db
from utils import login_required
from ._bp import admin_bp


# ------------------------------------------------------------------
# Page
# ------------------------------------------------------------------
@admin_bp.route("/academics")
@login_required
def academics():
    return render_template("academics.html", **_load_academics())


# ------------------------------------------------------------------
# Departments
# ------------------------------------------------------------------
@admin_bp.route("/departments/add", methods=["POST"])
@login_required
def add_department():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()

    if not name:
        flash("Department name is required.", "danger")
        return redirect(url_for("admin.academics"))

    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO departments (name, description, created_at) VALUES (?, ?, ?)",
                (name, description, datetime.utcnow().isoformat()),
            )
        flash(f"Department “{name}” added.", "success")
    except sqlite3.IntegrityError:
        flash(f"A department named “{name}” already exists.", "danger")

    return redirect(url_for("admin.academics"))


@admin_bp.route("/departments/<int:department_id>/edit", methods=["POST"])
@login_required
def edit_department(department_id):
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()

    if not name:
        flash("Department name is required.", "danger")
        return redirect(url_for("admin.academics"))

    try:
        with get_db() as db:
            db.execute(
                "UPDATE departments SET name = ?, description = ? WHERE id = ?",
                (name, description, department_id),
            )
        flash("Department updated.", "success")
    except sqlite3.IntegrityError:
        flash(f"Another department is already named “{name}”.", "danger")

    return redirect(url_for("admin.academics"))


@admin_bp.route("/departments/<int:department_id>/delete", methods=["POST"])
@login_required
def delete_department(department_id):
    with get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) AS c FROM courses WHERE department_id = ?",
            (department_id,),
        ).fetchone()["c"]

        if count:
            flash(
                "This department still has courses. Move or delete them first.",
                "warning",
            )
            return redirect(url_for("admin.academics"))

        db.execute("DELETE FROM departments WHERE id = ?", (department_id,))

    flash("Department deleted.", "info")
    return redirect(url_for("admin.academics"))


# ------------------------------------------------------------------
# Courses
# ------------------------------------------------------------------
@admin_bp.route("/courses/add", methods=["POST"])
@login_required
def add_course():
    department_id = request.form.get("department_id", "").strip()
    name = request.form.get("name", "").strip()

    if not department_id or not name:
        flash("Course name and department are required.", "danger")
        return redirect(url_for("admin.academics"))

    with get_db() as db:
        exists = db.execute(
            "SELECT 1 FROM departments WHERE id = ?", (department_id,)
        ).fetchone()
        if not exists:
            flash("Please choose a valid department.", "danger")
            return redirect(url_for("admin.academics"))

        _insert_course(db, department_id)

    flash(f"Course “{name}” added.", "success")
    return redirect(url_for("admin.academics"))


@admin_bp.route("/courses/<int:course_id>/edit", methods=["POST"])
@login_required
def edit_course(course_id):
    department_id = request.form.get("department_id", "").strip()
    name = request.form.get("name", "").strip()

    if not department_id or not name:
        flash("Course name and department are required.", "danger")
        return redirect(url_for("admin.academics"))

    with get_db() as db:
        course = db.execute(
            "SELECT name FROM courses WHERE id = ?", (course_id,)
        ).fetchone()
        if not course:
            abort(404)
        clash = db.execute(
            "SELECT 1 FROM courses WHERE name = ? AND id <> ?", (name, course_id)
        ).fetchone()

    if clash:
        flash(
            f"There is already another course named “{name}”. Fee rules and classes are "
            "matched by name, so two courses cannot share one spelling.",
            "danger",
        )
        return redirect(url_for("admin.academics"))

    renamed = course["name"] != name
    try:
        with get_db() as db:
            db.execute(
                """UPDATE courses
                   SET department_id = ?, name = ?, code = ?, qualification = ?,
                       duration = ?, exam_body = ?, progression_mode = ?,
                       total_periods = ?, registration_fee = ?
                   WHERE id = ?""",
                (
                    department_id,
                    name,
                    request.form.get("code", "").strip().upper() or None,
                    request.form.get("qualification", "").strip(),
                    request.form.get("duration", "").strip(),
                    request.form.get("exam_body", "").strip(),
                    _progression_mode(),
                    _total_periods(),
                    _registration_fee(),
                    course_id,
                ),
            )

            # Fee structures, classes and applications all point at a course by
            # its name string, not by id. Without this the rename would silently
            # strand every fee rule and enrolled student on the old spelling.
            moved = 0
            if renamed:
                for table, column in (
                    ("fee_structures", "course_name"),
                    ("classes", "course"),
                    ("applications", "course"),
                ):
                    moved += db.execute(
                        f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                        (name, course["name"]),
                    ).rowcount
    except sqlite3.IntegrityError:
        flash(
            f"Could not rename to “{name}”: a class or fee structure for that name "
            "already exists. Nothing was changed.",
            "danger",
        )
        return redirect(url_for("admin.academics"))

    flash(
        f"Course “{name}” updated."
        + (f" Moved {moved} linked record(s) onto the new name." if moved else ""),
        "success",
    )
    return redirect(url_for("admin.academics"))


@admin_bp.route("/courses/<int:course_id>/delete", methods=["POST"])
@login_required
def delete_course(course_id):
    with get_db() as db:
        course = db.execute(
            "SELECT name FROM courses WHERE id = ?", (course_id,)
        ).fetchone()
        if not course:
            abort(404)

        linked = []
        for label, table, column in (
            ("fee structures", "fee_structures", "course_name"),
            ("classes", "classes", "course"),
            ("applications", "applications", "course"),
        ):
            n = db.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE {column} = ?",
                (course["name"],),
            ).fetchone()["c"]
            if n:
                linked.append(f"{n} {label}")

        if linked:
            flash(
                f"“{course['name']}” is still used by {', '.join(linked)}. "
                "Clear those first, or rename the course instead of deleting it.",
                "warning",
            )
            return redirect(url_for("admin.academics"))

        db.execute("DELETE FROM courses WHERE id = ?", (course_id,))

    flash("Course deleted.", "info")
    return redirect(url_for("admin.academics"))


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _insert_course(db, department_id):
    db.execute(
        """INSERT INTO courses
           (department_id, name, code, qualification, duration, exam_body,
            progression_mode, total_periods, registration_fee, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            department_id,
            request.form.get("name", "").strip(),
            request.form.get("code", "").strip().upper() or None,
            request.form.get("qualification", "").strip(),
            request.form.get("duration", "").strip(),
            request.form.get("exam_body", "").strip(),
            _progression_mode(),
            _total_periods(),
            _registration_fee(),
            datetime.utcnow().isoformat(),
        ),
    )


def _progression_mode():
    mode = request.form.get("progression_mode", "").strip()
    return mode if mode in PERIOD_MODES else "term"


def _total_periods():
    # A short course is fixed at its two stages; the number box is ignored.
    if _progression_mode() == "short":
        return len(SHORT_COURSE_PERIODS)
    try:
        return min(max(int(request.form.get("total_periods", "").strip()), 1), 12)
    except ValueError:
        return 3


def _registration_fee():
    """This course's registration fee, or the fallback if the box is blank."""
    try:
        value = float((request.form.get("registration_fee", "") or "").strip())
    except ValueError:
        return REGISTRATION_FEE
    return max(0.0, value)


def _load_academics():
    with get_db() as db:
        departments = db.execute(
            """SELECT d.*,
                      (SELECT COUNT(*) FROM courses c
                       WHERE c.department_id = d.id) AS course_count
               FROM departments d
               ORDER BY d.name COLLATE NOCASE"""
        ).fetchall()

        courses = db.execute(
            """SELECT c.*, d.name AS department_name
               FROM courses c
               JOIN departments d ON d.id = c.department_id
               ORDER BY d.name COLLATE NOCASE, c.name COLLATE NOCASE"""
        ).fetchall()

    return {
        "departments": departments,
        "courses": courses,
        "default_registration_fee": REGISTRATION_FEE,
        "short_course_periods": SHORT_COURSE_PERIODS,
    }
