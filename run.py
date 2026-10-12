import os
import sys

from flask import Flask, render_template, session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

from config import (
    BASE_DIR,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USERNAME,
    DEBUG,
    IS_PRODUCTION,
    UPLOAD_DIR,
)
from database import init_db, get_db
from extensions import mail
from progression import period_word

from blueprints.public import bp as public_bp
from blueprints.admin import admin_bp
from blueprints.student import bp as student_bp
from blueprints.teacher import bp as teacher_bp


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "static"),
    )
    app.config.from_object("config.Config")

    # cPanel terminates HTTPS at Apache and hands us plain HTTP, so without this
    # every redirect and url_for() would rebuild the address as http://.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    mail.init_app(app)

    init_db()
    _warn_default_admin_password()

    # Courses name their periods differently (terms, semesters, stages), so a
    # template label has to follow the ladder it was given.
    app.jinja_env.globals["period_word"] = period_word

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(teacher_bp)

    @app.context_processor
    def inject_admin_badges():
        """Sidebar/topbar counters, shared by every admin view."""
        if not session.get("admin_logged_in"):
            return {}
        with get_db() as db:
            payments = db.execute(
                "SELECT COUNT(*) AS c FROM fee_payments WHERE status = 'pending'"
            ).fetchone()
            continuations = db.execute(
                "SELECT COUNT(*) AS c FROM progression_requests WHERE status = 'pending'"
            ).fetchone()
        return {
            "pending_payment_count": payments["c"] if payments else 0,
            "progression_pending_count": continuations["c"] if continuations else 0,
        }

    # Friendly root for /admin → /admin/dashboard
    _register_error_pages(app)
    return app


def _warn_default_admin_password():
    """The boot process seeds admin/admin123, and hosts often never change it.

    Only the error log is visible to whoever deployed the app, so that is where
    the warning goes. It checks the hash rather than trusting the environment,
    because an admin who changed the password leaves the old variable behind.
    """
    if not IS_PRODUCTION:
        return
    with get_db() as db:
        accounts = db.execute(
            "SELECT username, password_hash FROM admins"
        ).fetchall()
    weak = [
        account["username"] for account in accounts
        if check_password_hash(account["password_hash"], DEFAULT_ADMIN_PASSWORD)
    ]
    if weak:
        print(
            "WARNING: admin login(s) still accept the default ADMIN_PASSWORD: "
            + ", ".join(weak)
            + " - change it from the admin panel before going live.",
            file=sys.stderr,
        )


COPY = {
    400: ("That request could not be understood.",
          "Check the form and try again. Nothing was saved."),
    403: ("You are not allowed to see this page.",
          "If you think that is wrong, ask the office to check which classes you are assigned to."),
    404: ("There is nothing at that address.",
          "The record may have been deleted, or the link may be out of date."),
    405: ("That action is not allowed on this page.",
          "Use the buttons on the page instead of the address bar."),
    500: ("The server could not finish that.",
          "Nothing was lost — try again, and tell the office if it keeps happening."),
}


def _register_error_pages(app):
    """Without these, a bad link or a server fault shows a raw Werkzeug traceback.

    The handler reports its own status code and never leaks the exception text,
    so a real failure stays diagnosable in the server log rather than on screen.
    """
    for code, (heading, message) in COPY.items():
        def handler(_exc, heading=heading, message=message, code=code):
            return render_template(
                "error.html", code=code, heading=heading, message=message
            ), code

        app.register_error_handler(code, handler)

    def generic(exc):
        code = exc.code if isinstance(exc, HTTPException) else 500
        heading, message = COPY.get(code, (f"Something went wrong ({code}).",
                                           "Try again, and tell the office if it keeps happening."))
        return render_template(
            "error.html", code=code, heading=heading, message=message
        ), code

    app.register_error_handler(Exception, generic)


app = create_app()


if __name__ == "__main__":
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print("  AMUAA Admin — Student Application System")
    print("=" * 60)
    print(f"  Public site    ->  http://{host}:{port}/")
    print(f"  Admin login    ->  http://{host}:{port}/admin/login")
    print(f"  Student portal ->  http://{host}:{port}/student/login")
    print(f"  Teacher portal ->  http://{host}:{port}/teacher/login")
    if DEBUG:
        # The seeded credentials only exist on a developer machine; on a host the
        # operator supplies ADMIN_PASSWORD through the environment.
        print(f"  Username: {DEFAULT_ADMIN_USERNAME}")
        print(f"  Password: {DEFAULT_ADMIN_PASSWORD}")
    print("=" * 60)
    app.run(host=host, port=port, debug=DEBUG)