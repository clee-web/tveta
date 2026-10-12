from flask import (
    Blueprint, current_app, request, jsonify,
    send_from_directory, abort, Response
)
from datetime import datetime
import html
import os
import uuid

from config import BASE_DIR
from database import get_db
from utils import save_upload, notify_admin_new_application

bp = Blueprint("public", __name__)

# Field names are for the form; applicants are shown these words instead.
FIELD_LABELS = {
    "firstName": "your first name",
    "surname": "your surname",
    "email": "your email address",
    "phone": "your phone number",
    "nationality": "your nationality",
    "dateOfBirth": "your date of birth",
    "course": "the course you are applying for",
    "studyMode": "how you intend to study (full-time or part-time)",
    "nextOfKinName": "the name of your next of kin",
    "nextOfKinRelationship": "how your next of kin is related to you",
}


@bp.route("/")
def index():
    """Serve public landing page with the live course list."""
    with get_db() as db:
        courses = db.execute(
            "SELECT name FROM courses ORDER BY name COLLATE NOCASE"
        ).fetchall()

    options = "".join(
        f'<option>{html.escape(row["name"])}</option>' for row in courses
    )

    with open(os.path.join(BASE_DIR, "index.html"), "r", encoding="utf-8") as fh:
        page = fh.read()

    page = page.replace("<!--COURSE_OPTIONS-->", options)
    return Response(page, mimetype="text/html")


@bp.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(os.path.join(BASE_DIR, "assets"), filename)


@bp.route("/api/applications", methods=["POST"])
def submit_application():
    try:
        form = request.form
        files = request.files

        if form.get("website"):
            abort(400, "Bot detected")

        required = [
            "firstName", "surname", "email", "phone", "nationality",
            "dateOfBirth", "course", "studyMode",
            "nextOfKinName", "nextOfKinRelationship",
        ]
        for field in required:
            if not form.get(field, "").strip():
                # Applicants see this text. "Missing field: nextOfKinRelationship"
                # tells them nothing about what to type in the box.
                return jsonify(
                    ok=False,
                    message=f"Please fill in {FIELD_LABELS.get(field, 'this field')}.",
                ), 400

        if not form.get("consent"):
            return jsonify(ok=False, message="Consent is required."), 400

        if "passportPhoto" not in files or not files["passportPhoto"].filename:
            return jsonify(ok=False, message="A passport-size photo is required."), 400

        if "identityDocument" not in files or not files["identityDocument"].filename:
            return jsonify(ok=False, message="National ID/passport is required."), 400

        academic_files = [f for f in files.getlist("academicDocuments") if f and f.filename]
        if not academic_files:
            return jsonify(ok=False, message="At least one academic document is required."), 400

        first_name = form.get("firstName", "").strip()
        middle_name = form.get("middleName", "").strip()
        surname = form.get("surname", "").strip()
        full_name = " ".join(p for p in (first_name, middle_name, surname) if p)
        if not full_name:
            return jsonify(ok=False, message="Student name is required."), 400

        application_id = uuid.uuid4().hex

        with get_db() as db:
            db.execute(
                """INSERT INTO applications
                   (id, full_name, surname, middle_name, first_name, email, phone,
                    nationality, date_of_birth, medical_condition, course, study_mode,
                    start_date, end_date, next_of_kin_name, next_of_kin_relationship,
                    sponsor_name, sponsor_organisation, sponsor_contact_person,
                    previous_education, status, academic_year, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'pending', ?, ?)""",
                (
                    application_id,
                    full_name,
                    surname,
                    middle_name,
                    first_name,
                    form["email"].strip(),
                    form["phone"].strip(),
                    form.get("nationality", "").strip(),
                    form.get("dateOfBirth", "").strip(),
                    form.get("medicalCondition", "").strip(),
                    form["course"].strip(),
                    form["studyMode"].strip(),
                    form.get("startDate", "").strip(),
                    form.get("endDate", "").strip(),
                    form.get("nextOfKinName", "").strip(),
                    form.get("nextOfKinRelationship", "").strip(),
                    form.get("sponsorName", "").strip(),
                    form.get("sponsorOrganisation", "").strip(),
                    form.get("sponsorContactPerson", "").strip(),
                    form.get("previousEducation", "").strip(),
                    datetime.utcnow().strftime("%Y"),
                    datetime.utcnow().isoformat(),
                ),
            )

            # Save passport photo
            info = save_upload(files["passportPhoto"], application_id, "passport")
            if info:
                _insert_document(db, application_id, info)

            # Save ID document
            info = save_upload(files["identityDocument"], application_id, "id")
            if info:
                _insert_document(db, application_id, info)

            # Save academic documents
            for f in academic_files:
                info = save_upload(f, application_id, "academic")
                if info:
                    _insert_document(db, application_id, info)

            # Save CV if present
            if "cv" in files and files["cv"].filename:
                info = save_upload(files["cv"], application_id, "cv")
                if info:
                    _insert_document(db, application_id, info)

        # Send email notification
        with get_db() as db:
            app_row = db.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
            doc_count = db.execute(
                "SELECT COUNT(*) c FROM documents WHERE application_id = ?",
                (application_id,)
            ).fetchone()["c"]

        try:
            notify_admin_new_application(
                current_app._get_current_object(),
                app_row,
                doc_count,
                current_app.config["ADMIN_NOTIFY_EMAILS"],
            )
        except Exception as e:
            current_app.logger.warning(f"Admin notification failed: {e}")

        return jsonify(ok=True, id=application_id, message="Application received.")

    except ValueError as e:
        return jsonify(ok=False, message=str(e)), 400
    except Exception:
        current_app.logger.exception("Application submission failed")
        return jsonify(ok=False, message="Server error while saving application."), 500


def _insert_document(db, application_id, info):
    db.execute(
        """INSERT INTO documents
           (application_id, category, original_name, stored_name,
            mime_type, size_bytes, uploaded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            application_id, info["category"],
            info["original"], info["stored"],
            info["mime"], info["size"],
            datetime.utcnow().isoformat(),
        ),
    )