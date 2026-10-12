"""Fee calculation and admission-number helpers shared by admin + portal."""

import json
import secrets
from datetime import datetime

from config import ADMISSION_PREFIX, REGISTRATION_FEE, SHORT_COURSE_PERIODS
from database import _course_code


# A short course bills in stages, and 'Attachment' sorts before 'Course work'
# alphabetically, so the fee breakdown is ordered by ladder position instead.
_SHORT_RANK = {name: index for index, name in enumerate(SHORT_COURSE_PERIODS)}
_LAST_RANK = len(SHORT_COURSE_PERIODS)


# Ambiguous characters (0/O, 1/I/L) are left out so the printed password is easy to read.
_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_student_password(length=6):
    """Create a short, readable first-login password."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def to_float(value):
    try:
        return max(0.0, float(value))
    except (ValueError, TypeError):
        return 0.0


def parse_other_items(raw):
    """Turn the stored JSON into a list of {name, amount} dicts."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []

    items = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        amount = to_float(entry.get("amount"))
        if name or amount:
            items.append({"name": name or "Other", "amount": amount})
    return items


def collect_other_items(form):
    """Read the repeated 'other fee' rows out of a submitted form."""
    names = form.getlist("other_item_name")
    amounts = form.getlist("other_item_amount")
    items = []
    for index, name in enumerate(names):
        name = (name or "").strip()
        amount = to_float(amounts[index] if index < len(amounts) else 0)
        if name or amount > 0:
            items.append({"name": name or "Other", "amount": amount})
    return items


def other_items_total(items):
    return sum(item["amount"] for item in items)


def get_fee_structures(db, course_name, academic_year):
    return db.execute(
        """SELECT * FROM fee_structures
           WHERE course_name = ? AND academic_year = ?
           ORDER BY term""",
        (course_name, academic_year),
    ).fetchall()


def registration_fee_for(db, course_name):
    """The registration fee a course charges.

    Each course carries its own number (1000, 2000, 3000...). config.REGISTRATION_FEE
    is only the fallback for a course that has no row, so an older application whose
    course was deleted still gets a sane figure.
    """
    if course_name:
        row = db.execute(
            "SELECT registration_fee FROM courses WHERE name = ?", (course_name,)
        ).fetchone()
        if row is not None and row["registration_fee"] is not None:
            return to_float(row["registration_fee"])
    return to_float(REGISTRATION_FEE)


def compute_student_fees(db, application):
    """Return the full fee picture for one student (application row)."""
    course = application["course"]
    year = application["academic_year"] or ""

    raw_structures = get_fee_structures(db, course, year)
    structures = []
    for row in raw_structures:
        data = dict(row)
        data["other_items"] = parse_other_items(data.get("other_items"))
        structures.append(data)
    structures.sort(key=lambda s: _SHORT_RANK.get(s["term"], _LAST_RANK))

    tuition = sum((r["tuition_fee"] or 0) for r in structures)
    exam = sum((r["exam_fee"] or 0) for r in structures)
    other = sum((r["other_fee"] or 0) for r in structures)
    fees_total = tuition + exam + other

    payments = db.execute(
        """SELECT * FROM fee_payments
           WHERE application_id = ?
           ORDER BY (status = 'pending') DESC, paid_at DESC, id DESC""",
        (application["id"],),
    ).fetchall()

    paid = sum((p["amount"] or 0) for p in payments if p["status"] == "confirmed")
    pending = sum((p["amount"] or 0) for p in payments if p["status"] == "pending")

    registration = registration_fee_for(db, course)
    total_due = registration + fees_total
    balance = total_due - paid

    return {
        "academic_year": year,
        "course": course,
        "structures": structures,
        "tuition": tuition,
        "exam": exam,
        "other": other,
        "fees_total": fees_total,
        "registration": registration,
        "total_due": total_due,
        "paid": paid,
        "pending": pending,
        "balance": balance,
        "payments": payments,
    }


def generate_admission_number(db, application):
    """Build a unique number like AMUAA/ICT/2026/001."""
    year = application["academic_year"] or datetime.utcnow().strftime("%Y")

    row = db.execute(
        "SELECT code FROM courses WHERE name = ?", (application["course"],)
    ).fetchone()
    code = (row["code"] if row and row["code"] else None) or _course_code(
        application["course"]
    )

    prefix = f"{ADMISSION_PREFIX}/{code}/{year}/"
    # The serial comes from the highest number still in use, not from a row count:
    # counting drops when an application is deleted and would reissue a number that
    # was already printed on an admission letter.
    rows = db.execute(
        "SELECT admission_number FROM applications WHERE admission_number LIKE ?",
        (prefix + "%",),
    ).fetchall()
    highest = 0
    for row in rows:
        tail = row["admission_number"][len(prefix):]
        if tail.isdigit():
            highest = max(highest, int(tail))

    return f"{prefix}{highest + 1:03d}"
