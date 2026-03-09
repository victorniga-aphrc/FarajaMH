#docker compose exec -T web python - <<'PY'
from models import SessionLocal, User, Role, user_roles, init_db
from security import hash_password
init_db()
db = SessionLocal()
try:
    email = "admin@gmail.com".strip().lower()
    username = "admin"
    pw = "Admin123!"
    user = db.query(User).filter_by(email=email).first()
    if not user:
        user = User(email=email, username=username, password_hash=hash_password(pw), is_active=True, email_verified=True)
        db.add(user); db.commit(); db.refresh(user)
        print(f"Created user: {email}")
    else:
        user.password_hash = hash_password(pw)
        if not getattr(user, "username", None):
            user.username = username
        if getattr(user, "is_active", True) is False:
            user.is_active = True
        db.commit()
        print(f"Updated password for: {email}")

    def ensure_role(name):
        r = db.query(Role).filter_by(name=name).first()
        if not r:
            r = Role(name=name); db.add(r); db.commit(); db.refresh(r)
        return r

    admin = ensure_role("admin")

    existing = set(db.execute(user_roles.select().where(user_roles.c.user_id == user.id)))
    def attach(role):
        if not any(row.role_id == role.id for row in existing):
            db.execute(user_roles.insert().values(user_id=user.id, role_id=role.id)); db.commit()
            print(f"Granted {role.name} to {email}")

    attach(admin)

    # Show result
    u = db.query(User).filter_by(email=email).first()
    print("User:", u.email, "Active:", u.is_active, "Roles:", [r.name for r in u.roles])
finally:
    db.close()
#PY
