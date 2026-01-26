"""
Configuration management with AWS Parameter Store integration
"""
import os
import boto3
from typing import Optional, Dict, Any
from functools import lru_cache


class Config:
    """Base configuration with AWS Parameter Store support"""
    
    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
    
    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'postgresql://localhost/maneiro')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
    }
    
    # Redis (for sessions and Celery)
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    
    # Session
    SESSION_TYPE = 'redis'
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True
    PERMANENT_SESSION_LIFETIME = 86400  # 24 hours
    
    # Security
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None
    
    # File uploads
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = '/tmp/maneiro_uploads'
    
    # Rate limiting
    RATELIMIT_STORAGE_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/1')
    RATELIMIT_STRATEGY = 'fixed-window'
    
    # AWS
    AWS_REGION = os.environ.get('AWS_REGION', 'us-west-2')
    AWS_S3_BUCKET = os.environ.get('AWS_S3_BUCKET')
    
    # Parameter Store path
    PARAMETER_STORE_PATH = os.environ.get('PARAMETER_STORE_PATH', '/maneiro/prod/')
    
    # Stripe (load from Parameter Store in production)
    STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY')
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')
    STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')
    
    # OpenAI
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
    OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-4.1')
    
    # Monitoring
    SENTRY_DSN = os.environ.get('SENTRY_DSN')
    
    # Celery
    CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/2')
    CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'redis://localhost:6379/3')


class ParameterStoreConfig(Config):
    """Configuration that loads secrets from AWS Parameter Store"""
    
    def __init__(self):
        super().__init__()
        self._load_from_parameter_store()
    
    @lru_cache(maxsize=128)
    def _get_ssm_client(self):
        """Get cached SSM client"""
        return boto3.client('ssm', region_name=self.AWS_REGION)
    
    def _load_from_parameter_store(self):
        """Load secrets from Parameter Store"""
        if not self.PARAMETER_STORE_PATH:
            return
        
        try:
            ssm = self._get_ssm_client()
            
            # Get all parameters under the path
            paginator = ssm.get_paginator('get_parameters_by_path')
            pages = paginator.paginate(
                Path=self.PARAMETER_STORE_PATH,
                Recursive=True,
                WithDecryption=True
            )
            
            for page in pages:
                for param in page['Parameters']:
                    # Extract parameter name (remove path prefix)
                    name = param['Name'].replace(self.PARAMETER_STORE_PATH, '')
                    value = param['Value']
                    
                    # Map parameter names to config attributes
                    param_map = {
                        'secret-key': 'SECRET_KEY',
                        'database-url': 'SQLALCHEMY_DATABASE_URI',
                        'redis-url': 'REDIS_URL',
                        'stripe-secret-key': 'STRIPE_SECRET_KEY',
                        'stripe-publishable-key': 'STRIPE_PUBLISHABLE_KEY',
                        'stripe-webhook-secret': 'STRIPE_WEBHOOK_SECRET',
                        'openai-api-key': 'OPENAI_API_KEY',
                        'sentry-dsn': 'SENTRY_DSN',
                        'aws-s3-bucket': 'AWS_S3_BUCKET',
                    }
                    
                    if name in param_map:
                        setattr(self, param_map[name], value)
        
        except Exception as e:
            print(f"Warning: Could not load from Parameter Store: {e}")
            # Fall back to environment variables


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False


class ProductionConfig(ParameterStoreConfig):
    """Production configuration with Parameter Store"""
    DEBUG = False
    TESTING = False


class TestingConfig(Config):
    """Testing configuration"""
    DEBUG = True
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'postgresql://localhost/maneiro_test'
    WTF_CSRF_ENABLED = False


# Config dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}


def get_config(env: Optional[str] = None) -> Config:
    """Get configuration for environment"""
    env = env or os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])()
