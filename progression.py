"""Continuation between terms (certificates) and semesters (diplomas).

A student sits inside a course ladder such as Term 1 -> Term 2 -> Term 3. Moving
up is not automatic: the student asks, an admin reviews the money and the marks,
and then chooses one of the outcomes in config.PROGRESSION_DECISIONS.
"""

from datetime import datetime

from config import ATTENDANCE_REQUIREMENT, PASS_MARK, PROGRESSION_DECISIONS, SHORT_COURSE_PERIODS
from fees import compute_student_fees, get_fee_structures
from school import class_for_student


def _int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


# ------------------------------------------------------------------
# The curriculum ladder
# ------------------------------------------------------------------
def course_ladder(course):
    """['Term 1', 'Term 2', ...], ['Semester 1', ...] or the short-course stages."""
    mode = (course["progression_mode"] if course else None) or "term"
    if mode == "short":
        return list(SHORT_COURSE_PERIODS)
    label = "Semester" if mode == "semester" else "Term"
    total = min(max(_int(course["total_periods"] if course else 3, 3), 1), 12)
    return [f"{label} {i}" for i in range(1, total + 1)]


def is_short_course(course):
    return (course["progression_mode"] if course else None) == "short"


def course_for_name(db, course_name):
    return db.execute(
        "SELECT * FROM courses WHERE name = ?", (course_name,)
    ).fetchone()


def periods_for_course(db, course_name):
    """The ladder a course bills and progresses through, by course name.

    Replaces the old global TERMS list: a short course has stages, a diploma has
    semesters, and an unknown course name falls back to three terms so existing
    records still render.
    """
    return course_ladder(course_for_name(db, course_name))


def period_word(ladder):
    """'Term', 'Semester' or 'Stage' - how this course names its periods."""
    first = (ladder or [""])[0]
    if first.startswith("Semester"):
        return "Semester"
    if first.startswith("Term"):
        return "Term"
    return "Stage"


def ladders_by_course(db):
    """{course_name: ladder} for every course, for pages that label many rows."""
    return {
        row["name"]: course_ladder(row)
        for row in db.execute("SELECT * FROM courses").fetchall()
    }


def current_period(student, ladder):
    stored = student["current_period"]
    return stored if stored in ladder else ladder[0]


def next_period(ladder, period):
    try:
        index = ladder.index(period)
    except ValueError:
        return None
    return ladder[index + 1] if index + 1 < len(ladder) else None


def period_number(ladder, period):
    try:
        return ladder.index(period) + 1
    except ValueError:
        return None


# ------------------------------------------------------------------
# Evidence shown beside each request
# ------------------------------------------------------------------
def term_fees(db, student, period):
    """What this term bills against what was tagged as paid for it."""
    structures = [
        s for s in get_fee_structures(db, student["course"], student["academic_year"] or "")
        if s["term"] == period
    ]
    billed = sum((s["tuition_fee"] or 0) + (s["exam_fee"] or 0) + (s["other_fee"] or 0)
                 for s in structures)

    row = db.execute(
        """SELECT COALESCE(SUM(amount), 0) AS paid FROM fee_payments
           WHERE application_id = ? AND status = 'confirmed' AND term = ?""",
        (student["id"], period),
    ).fetchone()

    return {
        "period": period,
        "billed": billed,
        "paid": row["paid"] or 0,
        "outstanding": max(0.0, billed - (row["paid"] or 0)),
        "priced": bool(structures),
    }


def period_results(db, student, period):
    """Exam percentages for a period. Exams with no term set are included, since
    the teacher portal leaves that field optional, and reported as untagged."""
    cls = class_for_student(db, student)
    if not cls:
        return {"rows": [], "average": None, "ungraded": 0, "untagged": 0, "class": None}

    rows = db.execute(
        """SELECT e.id, e.title, e.max_score, e.term, s.name AS subject_name,
                  r.score, r.grade
           FROM exams e
           LEFT JOIN class_subjects s ON s.id = e.subject_id
           LEFT JOIN exam_results r ON r.exam_id = e.id AND r.student_id = ?
           WHERE e.class_id = ? AND (e.term = ? OR e.term IS NULL OR e.term = '')
           ORDER BY e.created_at DESC""",
        (student["id"], cls["id"], period),
    ).fetchall()

    percentages = []
    for r in rows:
        if r["score"] is None or not r["max_score"]:
            continue
        percentages.append(float(r["score"]) / float(r["max_score"]) * 100)

    return {
        "rows": rows,
        "average": round(sum(percentages) / len(percentages), 1) if percentages else None,
        "ungraded": len([r for r in rows if r["score"] is None]),
        "untagged": len([r for r in rows if r["term"] != period]),
        "class": cls,
    }


def attendance_rate(db, student):
    cls = class_for_student(db, student)
    if not cls:
        return None
    row = db.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status = 'present' THEN 1 ELSE 0 END) AS present
           FROM attendance WHERE class_id = ? AND student_id = ?""",
        (cls["id"], student["id"]),
    ).fetchone()
    if not row or not row["total"]:
        return None
    return round((row["present"] or 0) / row["total"] * 100, 1)


def snapshot(db, student):
    """Everything one review needs, in one dict."""
    course = course_for_name(db, student["course"])
    ladder = course_ladder(course)
    period = current_period(student, ladder)
    upcoming = next_period(ladder, period)

    fees = compute_student_fees(db, student)
    marks = period_results(db, student, period)
    present = attendance_rate(db, student)
    billed = term_fees(db, student, period)

    return {
        "course": course,
        "ladder": ladder,
        "period": period,
        "period_index": period_number(ladder, period),
        "next": upcoming,
        "is_last": upcoming is None,
        "term_fees": billed,
        "balance": fees["balance"],
        "total_due": fees["total_due"],
        "paid": fees["paid"],
        "results": marks,
        "attendance": present,
        "advice": advise(marks["average"], billed["outstanding"], fees["balance"], present),
        "pending": db.execute(
            """SELECT * FROM progression_requests
               WHERE application_id = ? AND status = 'pending'
               ORDER BY id DESC LIMIT 1""",
            (student["id"],),
        ).fetchone(),
    }


def advise(average, term_outstanding, balance, attendance):
    """Advisory only - the admin can still approve. Returns (decision, reasons)."""
    reasons = []
    if average is not None and average < PASS_MARK:
        reasons.append(f"Average {average}% is below the {PASS_MARK:.0f}% pass mark.")
    if attendance is not None and attendance < ATTENDANCE_REQUIREMENT:
        reasons.append(f"Attendance {attendance}% is under the required {ATTENDANCE_REQUIREMENT:.0f}%.")
    if term_outstanding > 0:
        reasons.append(f"KES {term_outstanding:,.0f} still unpaid for this period.")
    elif balance > 0:
        reasons.append(f"Overall balance of KES {balance:,.0f}.")

    if average is not None and average < PASS_MARK:
        return "repeat", reasons
    return "advance", reasons


# ------------------------------------------------------------------
# Requests
# ------------------------------------------------------------------
def request_period(db, student, kind, reason, requested_by):
    """File a continuation or deferral request. Returns (row, error)."""
    course = course_for_name(db, student["course"])
    ladder = course_ladder(course)
    period = current_period(student, ladder)

    existing = db.execute(
        """SELECT * FROM progression_requests
           WHERE application_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1""",
        (student["id"],),
    ).fetchone()
    if existing:
        return existing, "You already have a request waiting for a decision."

    if kind == "advance":
        upcoming = next_period(ladder, period)
        if not upcoming:
            return None, (f"{period} is the last {period_word(ladder).lower()} of this course. "
                          "Ask the office to sign you off as finished.")
    else:
        upcoming = None

    now = datetime.utcnow().isoformat()
    cursor = db.execute(
        """INSERT INTO progression_requests
           (application_id, kind, from_period, to_period, academic_year, reason,
            status, requested_by, requested_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            student["id"], kind, period, upcoming,
            student["academic_year"], (reason or "").strip() or None,
            requested_by, now,
        ),
    )
    row = db.execute(
        "SELECT * FROM progression_requests WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return row, None


def queue(db, status="pending"):
    where = "WHERE p.status = 'pending'" if status == "pending" else "WHERE p.status = 'decided'"
    return db.execute(
        f"""SELECT p.*, a.full_name, a.admission_number, a.course, a.study_mode,
                   a.academic_year, a.student_status, a.current_period
            FROM progression_requests p
            JOIN applications a ON a.id = p.application_id
            {where}
            ORDER BY p.requested_at DESC, p.id DESC"""
    ).fetchall()


def history(db, student_id):
    return db.execute(
        """SELECT * FROM progression_requests
           WHERE application_id = ? ORDER BY requested_at DESC, id DESC""",
        (student_id,),
    ).fetchall()


def pending_count(db):
    row = db.execute(
        "SELECT COUNT(*) AS c FROM progression_requests WHERE status = 'pending'"
    ).fetchone()
    return row["c"] if row else 0


# ------------------------------------------------------------------
# Decisions
# ------------------------------------------------------------------
def apply_decision(db, request_row, decision, actor, form):
    """Record a decision and move the student's state to match it.

    Returns (ok, message). The fee balance is deliberately never a blocker -
    the admin sees it and decides.
    """
    if decision not in PROGRESSION_DECISIONS:
        return False, "Choose a decision first."

    student = db.execute(
        "SELECT * FROM applications WHERE id = ?", (request_row["application_id"],)
    ).fetchone()
    if not student:
        return False, "That student record no longer exists."

    course = course_for_name(db, student["course"])
    ladder = course_ladder(course)
    period = current_period(student, ladder)
    now = datetime.utcnow().isoformat()

    new_course = (form.get("new_course") or "").strip()
    new_mode = (form.get("new_study_mode") or "").strip()
    return_period = (form.get("return_period") or "").strip()
    notes = (form.get("notes") or "").strip()

    target_period = None

    if decision == "advance":
        target_period = request_row["to_period"] or next_period(ladder, period)
        if not target_period:
            return False, (f"{period} is the last {period_word(ladder).lower()}. "
                           "Use 'mark complete' instead.")
        db.execute(
            "UPDATE applications SET current_period = ?, student_status = 'active' WHERE id = ?",
            (target_period, student["id"]),
        )

    elif decision == "repeat":
        db.execute(
            "UPDATE applications SET student_status = 'repeating' WHERE id = ?",
            (student["id"],),
        )

    elif decision == "change_course":
        replacement = course_for_name(db, new_course) if new_course else None
        if not replacement:
            return False, "Pick the course the student is moving to."
        new_ladder = course_ladder(replacement)
        db.execute(
            """UPDATE applications
               SET course = ?, study_mode = ?, current_period = ?, student_status = 'active'
               WHERE id = ?""",
            (
                new_course,
                new_mode or student["study_mode"],
                new_ladder[0],
                student["id"],
            ),
        )
        target_period = new_ladder[0]

    elif decision == "defer":
        if not return_period:
            return False, "Enter when the student is expected back."
        db.execute(
            "UPDATE applications SET student_status = 'deferred' WHERE id = ?",
            (student["id"],),
        )

    elif decision == "complete":
        db.execute(
            "UPDATE applications SET student_status = 'graduated' WHERE id = ?",
            (student["id"],),
        )

    else:  # rejected - leave the student exactly where they are
        pass

    db.execute(
        """UPDATE progression_requests
           SET status = 'decided', decision = ?, decision_notes = ?, to_period = ?,
               new_course = ?, new_study_mode = ?, return_period = ?,
               decided_at = ?, decided_by = ?
           WHERE id = ?""",
        (
            # to_period records where the student actually landed, so a repeat or a
            # decline must not keep the period the request was asking for.
            decision, notes or None, target_period,
            new_course or None, new_mode or None, return_period or None,
            now, actor, request_row["id"],
        ),
    )

    labels = {
        "advance": f"moved to {target_period}",
        "repeat": f"asked to repeat {period}",
        "change_course": f"transferred to {new_course}",
        "defer": f"deferred, back {return_period}",
        "complete": "marked as having finished the course",
        "rejected": "request declined",
    }
    return True, f"{student['full_name']} {labels[decision]}."
