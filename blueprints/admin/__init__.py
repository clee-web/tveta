from ._bp import admin_bp

# Import route modules so they register their URLs on admin_bp.
# Placed AFTER admin_bp is defined to avoid circular imports.
from . import auth          # noqa: E402,F401
from . import dashboard     # noqa: E402,F401
from . import applications  # noqa: E402,F401
from . import exports       # noqa: E402,F401
from . import documents     # noqa: E402,F401
from . import academics     # noqa: E402,F401
from . import fees          # noqa: E402,F401
from . import teachers      # noqa: E402,F401
from . import students      # noqa: E402,F401
from . import progression   # noqa: E402,F401

__all__ = ["admin_bp"]