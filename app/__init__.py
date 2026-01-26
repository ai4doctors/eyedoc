from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_session import Session

from .utils.config import get_config
from .models import db
from .auth import init_auth, auth_bp
from .api import api_bp, limiter
from .stripe_webhook import webhook_bp as stripe_webhook_bp

migrate = Migrate()
sess = Session()

def create_app(env: str | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates")
    cfg = get_config(env)
    app.config.from_object(cfg)

    db.init_app(app)
    migrate.init_app(app, db)
    sess.init_app(app)

    init_auth(app)
    limiter.init_app(app)

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(stripe_webhook_bp, url_prefix="/webhooks")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index():
        return {"name": "Maneiro.ai", "status": "running"}

    return app
