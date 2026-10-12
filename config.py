import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.environ.get("DB_PATH") or os.path.join(BASE_DIR, "database.db")

ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_FILE_BYTES = 8 * 1024 * 1024            # 8 MB per file
MAX_REQUEST_BYTES = 40 * 1024 * 1024        # 40 MB total per request

# Fees / admissions
# Each course carries its own registration fee (some are 1000, others 2000 or
# 3000). This value is only the fallback for a course row that has none.
REGISTRATION_FEE = 1000.0
ADMISSION_PREFIX = "AMUAA"
TERMS = ["Term 1", "Term 2", "Term 3"]

# Classes / teacher portal
STUDY_MODES = ["Online / E-learning", "Evening", "Afternoon", "Weekend", "Part-time"]

# Students
STUDENT_STATUSES = [
    "active", "repeating", "deferred", "suspended", "graduated", "withdrawn",
]

# Continuation / progression between terms (certificate courses),
# semesters (diploma courses) or the two fixed stages of a short course.
PERIOD_MODES = ["term", "semester", "short"]

# A 4-month course has no first/second/third term. The student does the course
# work, goes for attachment, and then finishes - so the ladder is these two
# stages instead of numbered periods.
SHORT_COURSE_PERIODS = ["Course work", "Attachment"]
PROGRESSION_DECISIONS = [
    "advance", "repeat", "change_course", "defer", "complete", "rejected",
]

# What the admin sees next to each decision button.
PASS_MARK = 50.0
ATTENDANCE_REQUIREMENT = 75.0

SCHOOL_MOTTO = "Through learning, we avoid poverty."

# Printed on page 2 of the admission letter.
PAYMENT_INSTRUCTIONS = [
    "Fees are payable at the beginning of each term and must be cleared before the "
    "end-of-term examinations.",
    "Payments are made at the admissions office or through the institute's official "
    "bank account / M-Pesa paybill.",
    "Always obtain an official receipt for every payment and keep it safe - it is "
    "required for clearance.",
    "A student with an outstanding balance may be barred from examinations and from "
    "collection of certificates or transcripts.",
]

# Printed on page 3 of the admission letter. Each entry is (section title, [rules]).
SCHOOL_RULES = [
    ("1. Attendance and Punctuality", [
        "Every student must attend all scheduled lessons, practicals and examinations.",
        "A minimum of 75% attendance is required to sit for end-of-term examinations.",
        "Students must arrive on time; persistent lateness is a disciplinary matter.",
        "Absence must be reported to the class teacher in advance, with evidence where possible.",
    ]),
    ("2. Fees and Financial Obligations", [
        "Fees must be paid by the dates announced at the beginning of each term.",
        "No student may sit for examinations with an outstanding fee balance unless a written payment plan has been approved.",
        "Certificates and transcripts are released only after full settlement of fees.",
    ]),
    ("3. Conduct and Discipline", [
        "Respect for fellow students, trainers and all institute staff is mandatory.",
        "Bullying, harassment, fighting, theft, vandalism and the use of drugs or alcohol are strictly prohibited.",
        "Any damage to institute property will be repaired or paid for by the responsible student.",
        "Mobile phones must be on silent and used only outside lessons unless permitted by the trainer.",
    ]),
    ("4. Academic Integrity", [
        "Cheating, impersonation or any form of examination malpractice leads to cancellation of results.",
        "Assignments and projects must be the student's own work; plagiarism is an offence.",
        "Students must keep their examination cards and present them when required.",
    ]),
    ("5. Dress Code and Identity", [
        "Students must dress decently and observe the institute's dress code at all times.",
        "The student identity card must be carried within the institute and produced on demand.",
        "The identity card is not transferable and must be reported immediately if lost.",
    ]),
    ("6. General", [
        "All students must familiarise themselves with and obey all notices issued by the administration.",
        "The administration reserves the right to amend these rules and regulations when necessary.",
        "Ignorance of these rules is not an excuse for any breach.",
    ]),
]

DEFAULT_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


def _flag(name, default):
    """Read an on/off environment variable the way hosts set them (1/true/yes)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Debug is strictly opt-in. Flask's debug mode exposes the interactive Werkzeug
# debugger, which runs arbitrary code, so a host that forgets to set a flag must
# never end up with it on. Start locally with FLASK_DEBUG=1 to get the reloader.
DEBUG = _flag("FLASK_DEBUG", False)
IS_PRODUCTION = not DEBUG

if DEBUG:
    import secrets
    _SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
else:
    _SECRET_KEY = os.environ.get("SECRET_KEY")
    if not _SECRET_KEY:
        # Booting anyway would leave each Passenger worker signing with its own
        # throwaway key, so logins would fail at random instead of once, clearly.
        raise RuntimeError(
            "SECRET_KEY is required to run outside debug mode.\n"
            "  Just working on the app locally? Turn debug on instead:\n"
            "      cmd>          set FLASK_DEBUG=1 && py run.py\n"
            "      PowerShell>   $env:FLASK_DEBUG=1; py run.py\n"
            "  Deploying to a host? Generate a key and add it in cPanel -> Setup Python\n"
            "  App -> Add Environment Variable (see HOSTING.md):\n"
            '      python -c "import secrets; print(secrets.token_hex(32))"\n'
            "  Keep it private, and never change it once the site is live, or every\n"
            "  session is invalidated."
        )


class Config:
    SECRET_KEY = _SECRET_KEY
    DEBUG = DEBUG
    MAX_CONTENT_LENGTH = MAX_REQUEST_BYTES

    # Cookies carry the admin, student and teacher sessions.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # cPanel serves the site over HTTPS through Apache; the flag is opt-out rather
    # than opt-in so a forgotten setting cannot leave sessions in the clear.
    SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", IS_PRODUCTION)

    # Mail
    MAIL_SERVER   = os.environ.get("MAIL_SERVER",   "smtp.gmail.com")
    MAIL_PORT     = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS  = _flag("MAIL_USE_TLS", True)
    MAIL_USE_SSL  = _flag("MAIL_USE_SSL", False)
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "youraddress@gmail.com")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "your-app-password")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER",
                                         os.environ.get("MAIL_USERNAME", "youraddress@gmail.com"))

    ADMIN_NOTIFY_EMAILS = [
        address.strip()
        for address in os.environ.get(
            "ADMIN_NOTIFY_EMAILS",
            "admin@amuaaips.co.ke,admissions@amuaaips.co.ke"
        ).split(",")
        if address.strip()
    ]