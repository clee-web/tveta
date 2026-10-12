from datetime import datetime, timedelta

from flask import render_template, request

from database import get_db
from fees import compute_student_fees
from utils import login_required
from ._bp import admin_bp

STATUSES = ("pending", "verified", "rejected")
PAGE_SIZES = (10, 25, 50, 100)


@admin_bp.route("")
@admin_bp.route("/")
@admin_bp.route("/dashboard")
@login_required
def dashboard():
    """Institution overview: headline numbers, workload and money position."""
    stats = _overview_stats()

    with get_db() as db:
        recent = db.execute(
            """SELECT id, full_name, course, study_mode, status, created_at,
                      admission_number
               FROM applications ORDER BY created_at DESC LIMIT 6"""
        ).fetchall()
        pending_payments = _pending_payments(db)

    return render_template(
        "dashboard.html",
        page_title="Dashboard",
        page_subtitle=f"Overview · {datetime.now():%A, %d %B %Y}",
        stats=stats,
        recent=recent,
        pending_payments=pending_payments,
    )


@admin_bp.route("/applications")
@login_required
def applications():
    """The applicant list, with status filter, search and pagination."""
    status_filter = request.args.get("status", "all")
    if status_filter not in STATUSES + ("all",):
        status_filter = "all"
    search = request.args.get("q", "").strip()

    try:
        per_page = int(request.args.get("per_page", 25))
    except ValueError:
        per_page = 25
    if per_page not in PAGE_SIZES:
        per_page = 25

    clauses, params = [], []
    if status_filter in STATUSES:
        clauses.append("status = ?")
        params.append(status_filter)
    if search:
        clauses.append("(full_name LIKE ? OR email LIKE ? OR id LIKE ? OR course LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like, like])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    with get_db() as db:
        total = db.execute(
            f"SELECT COUNT(*) AS c FROM applications{where}", params
        ).fetchone()["c"]
        pages = max(1, -(-total // per_page))

        page = min(max(1, _int_arg("page", 1)), pages)
        rows = db.execute(
            f"SELECT * FROM applications{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, per_page, (page - 1) * per_page),
        ).fetchall()

        counts = {
            "all": db.execute("SELECT COUNT(*) c FROM applications").fetchone()["c"],
            **{
                s: db.execute(
                    "SELECT COUNT(*) c FROM applications WHERE status = ?", (s,)
                ).fetchone()["c"]
                for s in STATUSES
            },
        }
        pending_payments = _pending_payments(db)

    return render_template(
        "applications.html",
        page_title="Applications",
        page_subtitle=f"{total:,} record{'s' if total != 1 else ''} matching your filters",
        nav_active="applications",
        applications=rows,
        counts=counts,
        status_filter=status_filter,
        search=search,
        page=page,
        pages=pages,
        per_page=per_page,
        pending_payments=pending_payments,
    )


def _int_arg(name, default):
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


def _month_label(year_month):
    """'2026-10' -> 'Oct'."""
    try:
        return datetime.strptime(year_month, "%Y-%m").strftime("%b")
    except (TypeError, ValueError):
        return year_month or "—"


def _pending_payments(db):
    return db.execute(
        """SELECT p.id, p.amount, p.category, p.term, p.method, p.reference,
                  p.paid_at, p.created_at,
                  a.id AS application_id, a.full_name, a.admission_number
           FROM fee_payments p
           JOIN applications a ON a.id = p.application_id
           WHERE p.status = 'pending'
           ORDER BY p.created_at DESC"""
    ).fetchall()


def _overview_stats():
    """Aggregate the numbers shown on the dashboard."""
    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0).isoformat()
    cutoff_30 = (datetime.now() - timedelta(days=30)).isoformat()

    with get_db() as db:
        status_counts = {
            r["status"]: r["c"]
            for r in db.execute(
                "SELECT status, COUNT(*) AS c FROM applications GROUP BY status"
            ).fetchall()
        }
        total_apps = sum(status_counts.values())

        def one(sql, params=()):
            row = db.execute(sql, params).fetchone()
            return row["c"] if row and row["c"] else 0

        stats = {
            "total": total_apps,
            "pending": status_counts.get("pending", 0),
            "verified": status_counts.get("verified", 0),
            "rejected": status_counts.get("rejected", 0),
            "admitted": one(
                "SELECT COUNT(*) c FROM applications WHERE admission_number IS NOT NULL"
            ),
            "active_students": one(
                """SELECT COUNT(*) c FROM applications
                   WHERE admission_number IS NOT NULL AND student_status = 'active'"""
            ),
            "new_this_month": one(
                "SELECT COUNT(*) c FROM applications WHERE created_at >= ?", (month_start,)
            ),
            "last_30": one(
                "SELECT COUNT(*) c FROM applications WHERE created_at >= ?", (cutoff_30,)
            ),
            "teachers": one("SELECT COUNT(*) c FROM teachers"),
            "classes": one("SELECT COUNT(*) c FROM classes"),
            "courses": one("SELECT COUNT(*) c FROM courses"),
            "collected": one(
                "SELECT COALESCE(SUM(amount), 0) c FROM fee_payments WHERE status = 'confirmed'"
            ),
            "awaiting": one(
                "SELECT COALESCE(SUM(amount), 0) c FROM fee_payments WHERE status = 'pending'"
            ),
            "pending_payment_count": one(
                "SELECT COUNT(*) c FROM fee_payments WHERE status = 'pending'"
            ),
            "continuations": one(
                "SELECT COUNT(*) c FROM progression_requests WHERE status = 'pending'"
            ),
            "deferred": one(
                "SELECT COUNT(*) c FROM applications WHERE student_status = 'deferred'"
            ),
            "repeating": one(
                "SELECT COUNT(*) c FROM applications WHERE student_status = 'repeating'"
            ),
        }

        # Outstanding balance is derived per student from fee structures, so it
        # reuses the same calculation the fees page shows.
        students = db.execute(
            "SELECT * FROM applications WHERE admission_number IS NOT NULL"
        ).fetchall()
        stats["outstanding"] = sum(
            max(0.0, compute_student_fees(db, s)["balance"]) for s in students
        )

        stats["trend"] = [
            {"label": _month_label(r["m"]), "count": r["c"]}
            for r in db.execute(
                """SELECT substr(created_at, 1, 7) AS m, COUNT(*) AS c
                   FROM applications
                   WHERE created_at >= date('now', '-6 months')
                   GROUP BY m ORDER BY m"""
            ).fetchall()
        ]

        stats["top_courses"] = [
            {"name": r["course"], "count": r["c"]}
            for r in db.execute(
                """SELECT course, COUNT(*) AS c FROM applications
                   GROUP BY course ORDER BY c DESC, course COLLATE NOCASE LIMIT 5"""
            ).fetchall()
        ]

        stats["mode_split"] = [
            {"name": r["study_mode"], "count": r["c"]}
            for r in db.execute(
                """SELECT study_mode, COUNT(*) AS c FROM applications
                   GROUP BY study_mode ORDER BY c DESC"""
            ).fetchall()
        ]

    return stats
