#!/usr/bin/env python3
# scripts/create_admin.py
"""
Create (or upsert) an admin user inside the same database the app uses.

Usage:
    python scripts/create_admin.py admin@gmail.com 'Admin123!' admin

Notes:
- This script *requires* the same Argon2-based hasher the app uses.
- It will fail fast if Argon2 (security.hash_password) is not importable.
- It ensures both 'admin' and 'clinician' roles exist and assigns them.
"""

import os
import sys

# Allow "from models import ..." when running from project root
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from models import SessionLocal, User, Role, user_roles, init_db  # type: ignore
from security import hash_password  # MUST match app’s verifier (Argon2)


def ensure_role(db, name: str) -> Role:
    """Ensure a role exists; create if missing."""
    role = db.query(Role).filter_by(name=name).first()
    if not role:
        role = Role(name=name)
        db.add(role)
        db.commit()
        db.refresh(role)
        print(f"✅ Created role: {name}")
    return role


def main(email: str, password: str, username: str | None = None) -> None:
    """
    Create or update an admin user with the given email and password.
    Always uses Argon2 (via security.hash_password).
    """
    # Ensure tables & seed basics if your models.init_db() does that
    init_db()

    db = SessionLocal()
    try:
        email = email.strip().lower()
        username = (username or email.split("@")[0]).strip()

        admin_role = ensure_role(db, "admin")
        clinician_role = ensure_role(db, "clinician")

        user = db.query(User).filter_by(email=email).first()
        if not user:
            user = User(
                email=email,
                username=username,
                password_hash=hash_password(password),
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"✅ Created user: {email}")
        else:
            # Update the password to the provided one (helps when re-running)
            user.password_hash = hash_password(password)
            if not getattr(user, "username", None):
                user.username = username
            if hasattr(user, "is_active") and user.is_active is False:
                user.is_active = True
            db.commit()
            print(f"🔁 Updated password for user: {email}")

        # Attach roles if missing
        def has_role(u, r):
            return any(ur.id == r.id for ur in u.roles)

        if not has_role(user, admin_role):
            db.execute(user_roles.insert().values(user_id=user.id, role_id=admin_role.id))
            db.commit()
            print(f"✅ Granted admin role to {email}")

        if not has_role(user, clinician_role):
            db.execute(user_roles.insert().values(user_id=user.id, role_id=clinician_role.id))
            db.commit()
            print(f"✅ Granted clinician role to {email}")

        print("🎉 Admin setup complete.")

    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("Usage: python scripts/create_admin.py admin@gmail.com 'Admin123!' [username]", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None)
