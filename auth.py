from flask import Blueprint, jsonify, request
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import SessionLocal, User, Role, OTP
import random
from security import hash_password, verify_password, generate_reset_token, verify_reset_token
from send_email import send_mail_with_html_file

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
login_manager = LoginManager()


@login_manager.user_loader
def load_user(user_id):
    db = SessionLocal()
    try:
        return db.get(User, int(user_id))
    finally:
        db.close()

def grant_role(db, user: User, role_name: str):
    role = db.query(Role).filter_by(name=role_name).first()
    if role and role not in user.roles:
        user.roles.append(role)  # ORM handles the association table
        db.commit()

@auth_bp.post("/signup")  # solely for patients now with email verification via otp
def signup():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"ok": False, "error": "Email and password required"}), 400
    db = SessionLocal()
    try:
        if db.query(User).filter_by(email=email).first():
            return jsonify({"ok": False, "error": "Email already registered"}), 409
        u = User(email=email, password_hash=hash_password(password))
        db.add(u)
        db.commit()
        db.refresh(u)  
        u.roles = []     
        grant_role(db, u, "patient")

        otp_code = f"{random.randint(1000, 9999)}"
        otp = OTP(user_id=u.id, otp_code=otp_code)
        db.add(otp)
        db.commit()
        db.refresh(otp)

        status, response = send_mail_with_html_file(
        recipient_email=email,
        subject="OTP Verification",
        html_file_name="email_template.html",
        placeholders={
            "message": "Kindly use this OTP Code: " + otp_code}
        )

        return jsonify({"ok": True,  "email": u.email})
    finally:
        db.close()

@auth_bp.post("/login")
def login():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    db = SessionLocal()
    try:
        u = db.query(User).filter_by(email=email).first()
        if not u or not verify_password(u.password_hash, password) or not u.is_active:
            return jsonify({"ok": False, "error": "Invalid credentials"}), 401
        login_user(u, remember=bool(data.get("remember")))
        return jsonify({"ok": True, "user": {"email": u.email, "roles": [r.name for r in u.roles], "reset_password": u.reset_password}})
    finally:
        db.close()

@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    return jsonify({"ok": True})

@auth_bp.get("/me")
def me():
    if not current_user.is_authenticated:
        return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, "user": {"email": current_user.email, "roles": [r.name for r in current_user.roles]}})


@auth_bp.post('/set-password')
@login_required
def set_new_password():
    db = SessionLocal()
    try:
        data = request.get_json(force=True) or request.form
        temp_password = data.get('temp_password', '').strip()
        new_password = data.get('new_password', '').strip()
        confirm_password = data.get('confirm_password', '').strip()

        if not temp_password or not new_password or not confirm_password:
            return jsonify({"ok": False, "error": "All fields are required"}), 400

        if new_password != confirm_password:
            return jsonify({"ok": False, "error": "Passwords do not match"}), 400

        user = db.query(User).filter(User.id == current_user.id).first()
        if not user:
            return jsonify({"ok": False, "error": "User not found"}), 404

        # Verify temp password
        if not verify_password(current_user.password_hash, temp_password):
            return jsonify({"ok": False, "error": "Temporary password is incorrect"}), 401

        # Update password and flags
        user.password_hash = hash_password(new_password)
        user.reset_password = False
        user.email_verified = True
        user.is_active = True

        db.commit()
        logout_user()
        return jsonify({"ok": True, "message": "Password updated successfully"})
    finally:
        db.close()


@auth_bp.post('/password-reset-request')
def password_reset_request():
    db = SessionLocal()
    try:
        data = request.get_json(force=True) or request.form
        email = data.get('email', '').strip()

        if not email:
            return jsonify({"ok": False, "error": "Email is required"}), 400

        user = db.query(User).filter(User.email == email).first()

        if not user:
            return jsonify({
                "ok": True,
                "message": "If this email exists, a reset link will be sent."
            })

        token = generate_reset_token(email)
        base_url = request.host_url.rstrip('/')
        reset_link = f"{base_url}/reset-password?token={token}"

        status, response = send_mail_with_html_file(
        recipient_email=email,
        subject="Reset Password",
        html_file_name="password_link.html",
        placeholders={
            "message": "You have made a request to reset your password. Ignore the request if you did not authorise this action",
            "reset_link": reset_link
        }
        )
        return jsonify({
            "ok": True,
            "message": "Check your email for a link to reset password",
        })
    finally:
        db.close()


@auth_bp.post("/verify-otp")
def verify_otp():
    data = request.get_json(force=True)
    email = data.get("email")
    otp_code = data.get("otp_code")

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(email=email).first()
        if not user:
            return jsonify({"ok": False, "error": "User not found"}), 404

        otp_entry = db.query(OTP).filter_by(user_id=user.id, otp_code=otp_code).first()
        if otp_entry:
            # OTP correct: verify user and delete OTP
            user.email_verified = True
            user.is_active = True

            db.delete(otp_entry)
            db.commit()
            return jsonify({"ok": True, "message": "OTP Verified"})
        else:
            return jsonify({"ok": False, "error": "Invalid OTP"}), 400
    finally:
        db.close()


@auth_bp.post('/confirm-reset-password')
def confirm_reset_password():
    db = SessionLocal()
    try:
        data = request.get_json(force=True) or request.form
        token = data.get('token', '')
        new_password = data.get('new_password', '').strip()
        confirm_password = data.get('confirm_password', '').strip()

        if not token or not new_password or not confirm_password:
            return jsonify({"ok": False, "error": "All fields are required"}), 400

        if new_password != confirm_password:
            return jsonify({"ok": False, "error": "Passwords do not match"}), 400

        email = verify_reset_token(token)
        if not email:
            return jsonify({"ok": False, "error": "Invalid or expired reset link"}), 400

        user = db.query(User).filter(User.email == email).first()
        if not user:
            return jsonify({"ok": False, "error": "User not found"}), 404

        user.password_hash = hash_password(new_password)
        user.is_active = True  
        user.reset_password = False

        db.commit()

        return jsonify({
            "ok": True,
            "message": "Password reset successful"
        })
    finally:
        db.close()
