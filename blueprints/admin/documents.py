import os

from flask import send_from_directory

from config import UPLOAD_DIR
from utils import login_required
from ._bp import admin_bp


@admin_bp.route("/document/<application_id>/<stored_name>")
@login_required
def view_document(application_id, stored_name):
    folder = os.path.join(UPLOAD_DIR, application_id)
    return send_from_directory(folder, stored_name)