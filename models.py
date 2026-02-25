# models.py
import os
import uuid as _uuid
from datetime import datetime
from security import hash_password
from sqlalchemy import (
    create_engine, Column, String, Text, DateTime, ForeignKey, Integer,
    Boolean, Table, UniqueConstraint, JSON, Index, text, func
)
from sqlalchemy.orm import (
    sessionmaker, declarative_base, relationship, scoped_session
)
import dotenv
dotenv.load_dotenv()

# DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:percy@localhost:5432/mhsdb")
DB_URL = os.getenv("DATABASE_URL")
#in this case, host = localhost, username = postgres, port = 5432, password = percy, db name = mhsdb

engine = create_engine(DB_URL, echo=False, future=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))
Base = declarative_base()

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id"), primary_key=True),
    UniqueConstraint("user_id", "role_id", name="uq_user_role"),
)

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    owner_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    messages = relationship("Message", back_populates="conversation",
                            cascade="all, delete-orphan",
                            order_by="Message.created_at.asc()")
    screenings = relationship("ScreeningEvent", back_populates="conversation",
                              cascade="all, delete-orphan",
                              order_by="ScreeningEvent.created_at.desc()")
    def __repr__(self) -> str:
        return f"<Conversation id={self.id} owner_user_id={self.owner_user_id}>"

class Message(Base):
    __tablename__ = "messages"
    id = Column(String, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), index=True, nullable=False)
    role = Column(String, index=True, nullable=False)
    type = Column(String, default="message", nullable=False)
    message = Column(Text, nullable=True)
    timestamp = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    conversation = relationship("Conversation", back_populates="messages")

    # NEW (optional) FAISS fields
    faiss_question_id = Column(String(128), index=True, nullable=True)
    faiss_category = Column(String(32), index=True, nullable=True)
    faiss_is_answer = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_messages_conv_faisscat", "conversation_id", "faiss_category"),
    )

    def __repr__(self) -> str:
        return (f"<Message id={self.id} conv={self.conversation_id} role={self.role} "
                f"type={self.type} faiss_q={self.faiss_question_id} "
                f"faiss_cat={self.faiss_category} is_ans={self.faiss_is_answer}>")

class ScreeningEvent(Base):
    __tablename__ = "screening_events"
    id = Column(String, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), index=True, nullable=True)
    overall_flag = Column(String, index=True)
    results_json = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    conversation = relationship("Conversation", back_populates="screenings")
    def __repr__(self) -> str:
        return f"<ScreeningEvent id={self.id} conv={self.conversation_id} flag={self.overall_flag}>"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=True)
    username = Column(String(255), unique=True, nullable=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    email_verified = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    roles = relationship("Role", secondary=user_roles, back_populates="users", lazy="joined")
    # optional institution for clinician users
    institution_id = Column(Integer, ForeignKey("institutions.id"), nullable=True)
    institution = relationship("Institution", back_populates="clinicians")
    reset_password = Column(Boolean, default=False, nullable=False)

    @property
    def is_authenticated(self): return True
    @property
    def is_anonymous(self): return False
    def get_id(self): return str(self.id)
    def has_role(self, name: str) -> bool: return any(r.name == name for r in self.roles)
    @property
    def display_name(self) -> str:
        if self.username:
            return self.username
        if self.name:
            return self.name
        email_local = (self.email or "").split("@")[0].strip()
        return email_local or self.email
    def __repr__(self) -> str: return f"<User id={self.id} email={self.email}>"


class OTP(Base):
    __tablename__ = "otps"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    otp_code = Column(String(4), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Institution(Base):
    __tablename__ = "institutions"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    clinicians = relationship("User", back_populates="institution")  # list of clinicians


class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String(32), unique=True, nullable=False)
    users = relationship("User", secondary=user_roles, back_populates="roles")
    def __repr__(self) -> str: return f"<Role id={self.id} name={self.name}>"

class ConversationOwner(Base):
    __tablename__ = "conversation_owners"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), index=True, nullable=False, unique=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    def __repr__(self) -> str:
        return f"<ConversationOwner conv={self.conversation_id} owner={self.owner_user_id}>"


def _seed_roles_admin():
    db = SessionLocal()
    try:
        # print("Starting DB initialization...")
        # Seed roles first
        role_names = ["admin", "patient", "clinician"]
        existing_roles = {r.name: r for r in db.query(Role).all()}
        # print(f"Existing roles in DB: {list(existing_roles.keys())}")
        for name in role_names:
            if name not in existing_roles:
                role = Role(name=name)
                db.add(role)
                existing_roles[name] = role
                print(f"Added role: {name}")
        db.commit()
        # print("Roles committed.")

        # Seed admin user
        admin_email = "test@admin.com"
        admin_user = db.query(User).filter_by(email=admin_email).first()
        if not admin_user:
            admin_user = User(
                email=admin_email,
                password_hash=hash_password("adminpassword"),
                is_active=True,
                email_verified=True,
            )
            db.add(admin_user)
            db.commit()  # commit so admin_user.id exists
# '            print(f"Created admin user: {admin_email}")
# '        else:
#             print(f"Admin user already exists: {admin_email}")

        # Assign admin role to the user
        admin_role = existing_roles["admin"]
        exists = db.execute(
            user_roles.select().where(
                (user_roles.c.user_id == admin_user.id) &
                (user_roles.c.role_id == admin_role.id)
            )
        ).first()
        if not exists:
            db.execute(
                user_roles.insert().values(
                    user_id=admin_user.id,
                    role_id=admin_role.id
                )
            )
            db.commit()
        #     print(f"Assigned 'admin' role to {admin_email}")
        # else:
        #     print(f"Admin role already assigned to {admin_email}")

        # Seed README admin (admin@gmail.com / Admin123!) so docs and login match
        readme_admin_email = "admin@gmail.com"
        readme_admin = db.query(User).filter_by(email=readme_admin_email).first()
        if not readme_admin:
            readme_admin = User(
                email=readme_admin_email,
                password_hash=hash_password("Admin123!"),
                is_active=True,
                email_verified=True,
            )
            db.add(readme_admin)
            db.commit()
            db.refresh(readme_admin)
        admin_role = existing_roles["admin"]
        clinician_role = existing_roles.get("clinician")
        for r in (admin_role, clinician_role):
            if r is None:
                continue
            exists = db.execute(
                user_roles.select().where(
                    (user_roles.c.user_id == readme_admin.id) & (user_roles.c.role_id == r.id)
                )
            ).first()
            if not exists:
                db.execute(user_roles.insert().values(user_id=readme_admin.id, role_id=r.id))
        db.commit()

        # Seed README doctor (doctor1@gmail.com / Doctor1234)
        doctor_email = "doctor1@gmail.com"
        doctor_user = db.query(User).filter_by(email=doctor_email).first()
        if not doctor_user:
            doctor_user = User(
                email=doctor_email,
                password_hash=hash_password("Doctor1234"),
                is_active=True,
                email_verified=True,
            )
            db.add(doctor_user)
            db.commit()
            db.refresh(doctor_user)
        if clinician_role:
            exists = db.execute(
                user_roles.select().where(
                    (user_roles.c.user_id == doctor_user.id) & (user_roles.c.role_id == clinician_role.id)
                )
            ).first()
            if not exists:
                db.execute(user_roles.insert().values(user_id=doctor_user.id, role_id=clinician_role.id))
                db.commit()

    finally:
        db.close()

def init_db():
    """Just for seeding - Alembic handles all the postgres db migrations now"""
    _seed_roles_admin()


def create_conversation(owner_user_id: int | None = None) -> str:
    db = SessionLocal()
    try:
        cid = str(_uuid.uuid4())
        db.add(Conversation(id=cid, owner_user_id=owner_user_id))
        db.commit()
        return cid
    finally:
        db.close()

def log_message(
    conversation_id: str,
    role: str,
    message: str | None,
    timestamp: str | None,
    type_: str = "message",
    *,
    faiss_question_id: str | None = None,
    faiss_category: str | None = None,
    faiss_is_answer: bool = False,
) -> str:
    db = SessionLocal()
    try:
        mid = str(_uuid.uuid4())
        db.add(Message(
            id=mid,
            conversation_id=conversation_id,
            role=role,
            type=type_,
            message=message,
            timestamp=timestamp,
            faiss_question_id=faiss_question_id,
            faiss_category=faiss_category,
            faiss_is_answer=faiss_is_answer,
        ))
        db.commit()
        return mid
    finally:
        db.close()

def list_conversations():
    db = SessionLocal()
    try:
        return db.query(Conversation).order_by(Conversation.created_at.desc()).all()
    finally:
        db.close()


def list_conversations_for_user(user_id: int):
    db = SessionLocal()
    try:
        rows = (
            db.query(
                Conversation.id,
                Conversation.created_at,
                func.count(Message.id).label("message_count"),
            )
            .outerjoin(Message, Message.conversation_id == Conversation.id)
            .filter(Conversation.owner_user_id == user_id)
            .group_by(Conversation.id, Conversation.created_at)
            .order_by(Conversation.created_at.desc())
            .all()
        )

        out = []
        for cid, created_at, msg_count in rows:
            latest = (
                db.query(Message)
                .filter(Message.conversation_id == cid, Message.message.isnot(None))
                .order_by(Message.created_at.desc())
                .first()
            )
            preview = (latest.message or "").strip() if latest else ""
            if len(preview) > 160:
                preview = preview[:160] + "..."
            out.append(
                {
                    "id": cid,
                    "created_at": created_at.isoformat() if created_at else None,
                    "message_count": int(msg_count or 0),
                    "preview": preview,
                }
            )
        return out
    finally:
        db.close()


def get_conversation_if_owned_by(conversation_id: str, user_id: int):
    db = SessionLocal()
    try:
        return (
            db.query(Conversation)
            .filter(
                Conversation.id == conversation_id,
                Conversation.owner_user_id == user_id,
            )
            .first()
        )
    finally:
        db.close()


def delete_conversation_if_owned_by(conversation_id: str, user_id: int) -> bool:
    db = SessionLocal()
    try:
        convo = (
            db.query(Conversation)
            .filter(
                Conversation.id == conversation_id,
                Conversation.owner_user_id == user_id,
            )
            .first()
        )
        if not convo:
            return False
        db.delete(convo)
        db.commit()
        return True
    finally:
        db.close()


def delete_conversation_by_id(conversation_id: str) -> bool:
    db = SessionLocal()
    try:
        convo = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not convo:
            return False
        db.delete(convo)
        db.commit()
        return True
    finally:
        db.close()

def get_conversation_messages(conversation_id: str):
    db = SessionLocal()
    try:
        return (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .all()
        )
    finally:
        db.close()
