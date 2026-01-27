"""
Pytest fixtures for Maneiro.ai tests
"""
import pytest
from app import create_app, db
from app.models import Organization, User, OrganizationPlan, UserRole


@pytest.fixture
def app():
    """Create application for testing"""
    app = create_app('development')
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['WTF_CSRF_ENABLED'] = False
    
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    """Create test client"""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Create test CLI runner"""
    return app.test_cli_runner()


@pytest.fixture
def test_org(app):
    """Create a test organization"""
    with app.app_context():
        org = Organization(
            name='Test Clinic',
            slug='test-clinic',
            email='test@test.com',
            plan=OrganizationPlan.TRIAL,
            max_monthly_jobs=50
        )
        db.session.add(org)
        db.session.commit()
        return org.id


@pytest.fixture
def test_user(app, test_org):
    """Create a test user"""
    with app.app_context():
        user = User(
            organization_id=test_org,
            username='testuser',
            email='test@test.com',
            first_name='Test',
            last_name='User',
            role=UserRole.ADMIN
        )
        user.set_password('testpassword')
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def authenticated_client(client, test_user, app):
    """Create authenticated test client"""
    with app.app_context():
        user = User.query.get(test_user)
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
    return client
