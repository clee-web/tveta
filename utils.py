import os
import uuid
from functools import wraps

from flask import session, redirect, url_for, flash
from werkzeug.utils import secure_filename
from threading import Thread

from config import UPLOAD_DIR, ALLOWED_EXT, MAX_FILE_BYTES
from extensions import mail
from flask_mail import Message


# ------------------------------------------------------------------
# Auth decorator
# ------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            flash("Please log in to access the admin area.", "warning")
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return wrapper


def student_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("student_id"):
            flash("Please log in to your student portal.", "warning")
            return redirect(url_for("student.login"))

        # First login: force the student to replace the password printed on their letter.
        from database import get_db
        with get_db() as db:
            student = db.execute(
                "SELECT must_change_password FROM applications WHERE id = ?",
                (session["student_id"],),
            ).fetchone()

        if student and student["must_change_password"]:
            flash("Please set a new password before continuing.", "warning")
            return redirect(url_for("student.set_password"))

        return f(*args, **kwargs)
    return wrapper


def teacher_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("teacher_id"):
            flash("Please log in to the teacher portal.", "warning")
            return redirect(url_for("teacher.login"))

        # First login: force the teacher to replace their generated password.
        from database import get_db
        with get_db() as db:
            teacher = db.execute(
                "SELECT must_change_password FROM teachers WHERE id = ?",
                (session["teacher_id"],),
            ).fetchone()

        if teacher and teacher["must_change_password"]:
            flash("Please set a new password before continuing.", "warning")
            return redirect(url_for("teacher.set_password"))

        return f(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------------
# File upload
# ------------------------------------------------------------------
def save_upload(file_storage, application_id, category):
    """Save one uploaded file safely. Returns dict with metadata or None."""
    if not file_storage or not file_storage.filename:
        return None

    original = secure_filename(file_storage.filename)
    ext = os.path.splitext(original)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"File type not allowed: {original}")

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_FILE_BYTES:
        raise ValueError(f"File too large: {original}")

    app_dir = os.path.join(UPLOAD_DIR, application_id)
    os.makedirs(app_dir, exist_ok=True)

    stored = f"{category}_{uuid.uuid4().hex}{ext}"
    file_storage.save(os.path.join(app_dir, stored))

    return {
        "original": original,
        "stored": stored,
        "mime": file_storage.mimetype,
        "size": size,
        "category": category,
    }


# ------------------------------------------------------------------
# Email helpers
# ------------------------------------------------------------------
def _send_async_email(app, msg):
    with app.app_context():
        try:
            mail.send(msg)
        except Exception as e:
            app.logger.error(f"Email send failed: {e}")


def notify_admin_new_application(app, application, document_count, admin_emails):
    """Send HTML notification to admins. Runs in a background thread."""
    subject = f"[AMUAA] New application: {application['full_name']} — {application['course']}"

    body = f"""
    <h2 style="color:#762b3a;margin:0 0 12px">New Student Application</h2>
    <p>A new application has been submitted through the AMUAA website.</p>
    <table style="border-collapse:collapse;font-family:sans-serif;font-size:14px">
      <tr><td style="padding:6px 12px;background:#f6f1e7"><b>Reference</b></td>
          <td style="padding:6px 12px">{application['id'][:8].upper()}</td></tr>
      <tr><td style="padding:6px 12px;background:#f6f1e7"><b>Full name</b></td>
          <td style="padding:6px 12px">{application['full_name']}</td></tr>
      <tr><td style="padding:6px 12px;background:#f6f1e7"><b>Email</b></td>
          <td style="padding:6px 12px">{application['email']}</td></tr>
      <tr><td style="padding:6px 12px;background:#f6f1e7"><b>Phone</b></td>
          <td style="padding:6px 12px">{application['phone']}</td></tr>
      <tr><td style="padding:6px 12px;background:#f6f1e7"><b>Course</b></td>
          <td style="padding:6px 12px">{application['course']}</td></tr>
      <tr><td style="padding:6px 12px;background:#f6f1e7"><b>Study mode</b></td>
          <td style="padding:6px 12px">{application['study_mode']}</td></tr>
      <tr><td style="padding:6px 12px;background:#f6f1e7"><b>Documents</b></td>
          <td style="padding:6px 12px">{document_count} file(s) attached</td></tr>
    </table>
    <p style="font-size:12px;color:#777;margin-top:24px">
      Automated message from the AMUAA online application system.
    </p>
    """

    msg = Message(subject=subject, recipients=admin_emails, html=body)
    Thread(target=_send_async_email, args=(app, msg), daemon=True).start()