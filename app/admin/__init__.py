"""Admin-only surface.

Everything gated to Poysis staff lives here: directory seeding, and whatever
comes next. The point of the package is that "who is privileged" is answered in
exactly one place (`app.admin.auth`) rather than re-derived per endpoint.

Typical use for trying something on your own account before general release:

    from app.admin.auth import is_admin
    if is_admin(user_id):
        ...new behaviour...
"""
from app.admin.auth import is_admin, is_seed_workspace, require_admin

__all__ = ["is_admin", "is_seed_workspace", "require_admin"]
