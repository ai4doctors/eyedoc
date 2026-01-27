"""
Pytest fixtures for Maneiro.ai tests
"""
import pytest
from app import create_app, db
from app.models import Organization, User, OrganizationPlan, UserRole


@pytest.fixture(scope='session')
def app():
    """Create application for testing."""
    app = create_app('development')
    app.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'WTF_CSRF_ENABLED': False,
    })
    
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Create CLI runner."""
    return app.test_cli_runner()


@pytest.fixture
def org(app):
    """Create test organization."""
    with app.app_context():
        organization = Organization(
            name='Test Clinic',
            slug='test-clinic',
            email='test@clinic.com',
            plan=OrganizationPlan.TRIAL,
            max_monthly_jobs=50
        )
        db.session.add(organization)
        db.session.commit()
        yield organization
        db.session.delete(organization)
        db.session.commit()


@pytest.fixture
def user(app, org):
    """Create test user."""
    with app.app_context():
        test_user = User(
            organization_id=org.id,
            username='testuser',
            email='test@example.com',
            first_name='Test',
            last_name='User',
            role=UserRole.ADMIN
        )
        test_user.set_password('testpassword')
        db.session.add(test_user)
        db.session.commit()
        yield test_user
        db.session.delete(test_user)
        db.session.commit()


@pytest.fixture
def auth_client(client, user):
    """Create authenticated test client."""
    client.post('/login', data={
        'username': 'testuser',
        'password': 'testpassword'
    })
    return client
