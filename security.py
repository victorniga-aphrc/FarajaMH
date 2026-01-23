from argon2 import PasswordHasher
from argon2.low_level import Type
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import current_app

# Keep these params aligned with what the app used in production
ph = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=2,
    hash_len=32,
    type=Type.ID,
)

def hash_password(pw: str) -> str:
    return ph.hash(pw)

def verify_password(hash_: str, pw: str) -> bool:
    try:
        return ph.verify(hash_, pw)
    except Exception:
        return False


def get_serializer():
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt="password-reset"
    )

def generate_reset_token(email: str) -> str:
    s = get_serializer()
    return s.dumps(email)

def verify_reset_token(token: str, max_age=3600) -> str | None:
    s = get_serializer()
    try:
        email = s.loads(token, max_age=max_age)
        return email
    except (SignatureExpired, BadSignature):
        return None
