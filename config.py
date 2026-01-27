"""
Maneiro.ai Configuration
Supports AWS Parameter Store for production secrets
"""
import os
from functools import lru_cache

try:
    import boto3
except ImportError:
    boto3 = None


def get_parameter(name: str, default: str = "") -> str:
    """Get parameter from AWS Parameter Store or environment"""
    # Environment variable takes precedence
    env_key = name.upper().replace("-", "_").replace("/", "_")
    if env_key in os.environ:
        return os.environ[env_key]
    
    # Try AWS Parameter Store in production
    if boto3 and os.environ.get("USE_PARAMETER_STORE"):
        try:
            ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-west-2"))
            path = os.environ.get("PARAMETER_STORE_PATH", "/maneiro/prod/")
            response = ssm.get_parameter(Name=f"{path}{name}", WithDecryption=True)
            return response["Parameter"]["Value"]
        except Exception:
            pass
    
    return default


class Config:
    """Base configuration"""
    # Flask
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    
    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///maneiro.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    
    # Fix Render's postgres:// URL
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace("postgres://", "postgresql://", 1)
    
    # Session
    SESSION_TYPE = "filesystem"
    SESSION_PERMANENT = False
    PERMANENT_SESSION_LIFETIME = 86400
    
    # Security
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600
    
    # OpenAI
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1")
    
    # AWS
    AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
    AWS_S3_BUCKET = os.environ.get("AWS_S3_BUCKET", "")
    
    # Stripe
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    
    # Rate Limiting
    RATELIMIT_DEFAULT = "200 per day"
    RATELIMIT_STORAGE_URL = os.environ.get("REDIS_URL", "memory://")
    
    # App Version
    APP_VERSION = os.environ.get("APP_VERSION", "2026.7")
    BUILD_TIME = os.environ.get("BUILD_TIME", "")
    GIT_COMMIT = os.environ.get("GIT_COMMIT", "")
    
    # Feature Flags
    FEATURE_STRICT_SCHEMA = os.environ.get("FEATURE_STRICT_SCHEMA", "1") == "1"
    FEATURE_PROGRESS_STAGES = os.environ.get("FEATURE_PROGRESS_STAGES", "1") == "1"
    FEATURE_MULTI_SPECIALTY = os.environ.get("FEATURE_MULTI_SPECIALTY", "1") == "1"


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    TESTING = False
    
    # Override with Parameter Store in production
    SECRET_KEY = get_parameter("secret-key", Config.SECRET_KEY)
    OPENAI_API_KEY = get_parameter("openai-api-key", Config.OPENAI_API_KEY)
    STRIPE_SECRET_KEY = get_parameter("stripe-secret-key", Config.STRIPE_SECRET_KEY)
    STRIPE_WEBHOOK_SECRET = get_parameter("stripe-webhook-secret", Config.STRIPE_WEBHOOK_SECRET)


class TestingConfig(Config):
    """Testing configuration"""
    DEBUG = True
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


@lru_cache()
def get_config(env: str = None):
    """Get configuration by environment name"""
    env = env or os.environ.get("FLASK_ENV", "development")
    configs = {
        "development": DevelopmentConfig,
        "production": ProductionConfig,
        "testing": TestingConfig,
    }
    return configs.get(env, DevelopmentConfig)
