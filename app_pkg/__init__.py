# app_pkg/__init__.py
from __future__ import annotations
import logging, os
os.environ["TRANSFORMERS_NO_TORCHVISION"] = "1"
from flask import Flask, send_from_directory, session
from flask_wtf import CSRFProtect
from flask_sock import Sock
from .extensions import mail
from dotenv import load_dotenv
from flask_login import current_user

from config import Config
from auth import auth_bp, login_manager
from admin import admin_bp
from models import init_db, create_conversation
from .core.faiss_core import initialize_faiss

csrf = CSRFProtect()
sock = Sock()
logger = logging.getLogger(__name__)

def create_app() -> Flask:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    base_dir = os.path.dirname(os.path.abspath(__file__))        # /app/app_pkg
    project_root = os.path.abspath(os.path.join(base_dir, "..")) # /app
    # project_root = os.path.abspath(os.path.dirname(__file__) + "/..")

    app = Flask(
        __name__,
        template_folder=os.path.join(project_root, "templates"),
        static_folder=os.path.join(project_root, "static"),
    )

    app.config.from_object(Config)

    app.config["FAISS_INDEX_PATH"] = os.getenv(
        "FAISS_INDEX_PATH",
        app.config.get("FAISS_INDEX_PATH",
                       os.path.join(project_root, "data", "faiss", "mental_health_cases.index")),
    )
    app.config["FAISS_METADATA_PATH"] = os.getenv(
        "FAISS_METADATA_PATH",
        app.config.get("FAISS_METADATA_PATH",
                       os.path.join(project_root, "data", "faiss", "mental_health_cases_metadata.pkl")),
    )
    app.config.setdefault(
        "QUESTIONS_JSON_PATH",
        os.environ.get(
            "QUESTIONS_JSON_PATH",
            os.path.join(project_root, "data", "faiss", "questions.json"),
        ),
    )

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = not app.debug
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    os.environ["CREWAI_TELEMETRY_DISABLED"] = "1"

    # Extensions
    login_manager.init_app(app)
    csrf.init_app(app)
    sock.init_app(app)
    mail.init_app(app)

    # Blueprints
    from .routes.misc import misc_bp, register_error_handlers
    from .routes.faiss_routes import faiss_bp
    from .routes.stt import stt_bp, register_ws_routes

    # Agents blueprint (optional: depends on crewai stack)
    agents_bp = None
    try:
        from .routes.agents import agents_bp as _agents_bp  # type: ignore
        agents_bp = _agents_bp
    except Exception as e:
        logger.warning("Agents blueprint disabled (crewai stack not available): %s", e)

    # Exempt JSON auth API from CSRF to avoid 400s on POST
    try:
        csrf.exempt(auth_bp)
    except Exception:
        logger.exception("Failed to exempt auth blueprint from CSRF")

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(misc_bp)
    if agents_bp is not None:
        app.register_blueprint(agents_bp)
    app.register_blueprint(faiss_bp)
    app.register_blueprint(stt_bp)
    register_ws_routes(sock)

   # aliases (optional)
    v = app.view_functions 

    try:
        app.add_url_rule('/', endpoint='index', view_func=v['misc_bp.index'])
    except KeyError:
        logger.error("Alias failed: misc_bp.index not found")

    try:
        app.add_url_rule('/admin', endpoint='admin_page', view_func=v['admin.summary'])
        logger.info("Alias created: admin_page -> admin.summary")
    except KeyError:
        pass

    try:
        app.add_url_rule('/health', endpoint='health_check', view_func=v['misc_bp.health_check'])
    except KeyError:
        pass

    try:
        app.add_url_rule('/clinicians', endpoint='clinicians', view_func=v['misc_bp.clinicians'])
    except KeyError:
        logger.error("Alias failed: misc_bp.clinicians not found")

    try:
        app.add_url_rule('/clinician_dashboard', endpoint='clinician_dashboard', view_func=v['misc_bp.clinician_dashboard'])
        logger.info("Alias created: clinician_dashboard -> admin.summary")
    except KeyError:
        logger.warning("Alias note: clinician.summary not found — check clinician_bp definitions")

    # for clinicians when they're first added
    try:
        app.add_url_rule('/new-password', endpoint='new-password', view_func=v['misc_bp.new-password'])
    except KeyError:
        logger.error("Alias failed: misc_bp.new-password not found")

    try:
        app.add_url_rule('/otp-verification', endpoint='otp-verification', view_func=v['misc_bp.otp-verification'])
    except KeyError:
        logger.error("Alias failed: misc_bp.otp-verification not found")

    try:
        app.add_url_rule('/reset-email', endpoint='reset-email', view_func=v['misc_bp.reset-email'])
    except KeyError:
        logger.error("Alias failed: misc_bp.reset-email not found")

    try:
        app.add_url_rule('/reset-password', endpoint='reset-password', view_func=v['misc_bp.reset-password'])
    except KeyError:
        logger.error("Alias failed: misc_bp.reset-password not found")

    favicon_path = os.path.join(app.static_folder or "", "favicon.ico")
    if os.path.exists(favicon_path):
        app.add_url_rule("/favicon.ico", "favicon",
                         lambda: send_from_directory(app.static_folder, "favicon.ico"))

    @app.before_request
    def ensure_conversation():
        try:
            # keep both keys in sync for backward compatibility
            if 'id' in session and 'conversation_id' not in session:
                session['conversation_id'] = session['id']
            if 'conversation_id' in session and 'id' not in session:
                session['id'] = session['conversation_id']

            if current_user.is_authenticated and not session.get('conversation_id'):
                cid = create_conversation(owner_user_id=current_user.id)
                session['conversation_id'] = cid
                session['id'] = cid
                session['conv'] = []
        except Exception:
            pass

    @app.after_request
    def apply_security_headers(response):
        # Permissions-Policy is the modern header; avoid duplicate Feature-Policy (deprecated)
        response.headers["Permissions-Policy"] = "microphone=(self)"
        return response

    try:
        init_db()
    except Exception:
        logger.exception("DB init failed")

    if not initialize_faiss(app):
        logger.error("Failed to initialize FAISS.")

    register_error_handlers(app)
    logger.info("Flask app created.")
    return app
