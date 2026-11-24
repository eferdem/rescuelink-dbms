"""
Authentication and role-based access control.

Two layers are enforced:

1. Route level  -- @roles_required('Admin', ...) rejects the request with 403.
2. Query level  -- every donor/coordinator/courier query carries an
   `org_id = %s` or `user_id = %s` predicate taken from the session, so even
   a hand-crafted URL cannot read another partner's rows.
"""

from __future__ import annotations

from functools import wraps

from flask import flash, g, redirect, request, session, url_for

from db import query_one

ROLE_NAMES = {
    1: "Admin",
    2: "Donor Staff",
    3: "Recipient Coordinator",
    4: "Volunteer Courier",
}
ROLE_LABELS = {
    "Admin": "Platform Admin",
    "Donor Staff": "Donor Staff",
    "Recipient Coordinator": "Recipient Coordinator",
    "Volunteer Courier": "Volunteer Courier",
}


def load_logged_in_user() -> None:
    user_id = session.get("user_id")
    g.user = None
    if user_id is None:
        return
    g.user = query_one("""
        SELECT u.user_id, u.email, u.full_name, u.phone, u.org_id, u.is_active,
               r.role_id, r.role_name, o.name AS org_name, o.org_type
        FROM users u
        JOIN roles r ON r.role_id = u.role_id
        LEFT JOIN organizations o ON o.org_id = u.org_id
        WHERE u.user_id = %s
    """, (user_id,))
    if g.user is None or not g.user["is_active"]:
        session.clear()
        g.user = None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def roles_required(*roles: str):
    """Deny by default: the role must be listed explicitly."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for("login", next=request.path))
            if g.user["role_name"] not in roles:
                flash(f"Access denied: this action requires "
                      f"{' or '.join(roles)}.", "error")
                return redirect(url_for("dashboard")), 403
            return view(*args, **kwargs)

        return wrapped

    return decorator


def org_scope() -> int | None:
    """Organisation id the current user may read, or None for platform admin."""
    if g.user is None:
        return None
    if g.user["role_name"] == "Admin":
        return None
    return g.user["org_id"]


def init_app(app) -> None:
    app.before_request(load_logged_in_user)
