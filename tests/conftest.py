"""
Test Configuration and Fixtures
"""
import os
import pytest
from app import create_app, db
from app.models import Organization, User, OrganizationPlan, UserRole


@pytest.fixture(scope='session')
def app():
    """Create application for testing"""
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    os.environ['SECRET_KEY'] = 'test-secret-key'
    os.environ['FLASK_ENV'] = 'development'
    
    app = create_app('development')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture(scope='function')
def client(app):
    """Create test client"""
    return app.test_client()


@pytest.fixture(scope='function')
def session(app):
    """Create database session for testing"""
    with app.app_context():
        yield db.session
        db.session.rollback()


@pytest.fixture(scope='function')
def test_org(app):
    """Create test organization"""
    with app.app_context():
        org = Organization(
            name='Test Clinic',
            slug='test-clinic',
            email='test@clinic.com',
            plan=OrganizationPlan.TRIAL,
            max_monthly_jobs=50
        )
        db.session.add(org)
        db.session.commit()
        yield org
        db.session.delete(org)
        db.session.commit()


@pytest.fixture(scope='function')
def test_user(app, test_org):
    """Create test user"""
    with app.app_context():
        user = User(
            organization_id=test_org.id,
            username='testuser',
            email='test@example.com',
            first_name='Test',
            last_name='User',
            role=UserRole.ADMIN
        )
        user.set_password('testpassword123')
        db.session.add(user)
        db.session.commit()
        yield user
        db.session.delete(user)
        db.session.commit()


@pytest.fixture(scope='function')
def authenticated_client(client, test_user):
    """Create authenticated test client"""
    client.post('/login', data={
        'username': 'testuser',
        'password': 'testpassword123'
    })
    return client
