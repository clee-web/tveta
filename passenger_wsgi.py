"""WSGI entrypoint for cPanel's "Setup Python App" (Apache + Phusion Passenger).

Passenger imports this file and looks for a callable named `application`, so this
stays deliberately thin: it puts the app directory on sys.path and hands over the
Flask app that run.py builds.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Passenger reports an unhelpful "Application starting up failed" for any import
# error, so the real reason (a missing SECRET_KEY, a package that did not install)
# has to be in the error log.
try:
    from run import app as application
    print(f"AMUAA app loaded from {BASE_DIR} (Python {sys.version.split()[0]})",
          file=sys.stderr)
except Exception:
    import traceback
    traceback.print_exc()
    raise
