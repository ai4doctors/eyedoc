"""
Authentication Tests
"""
import pytest


class TestRegistration:
    """Test user registration"""
    
    def test_register_page_loads(self, client):
        """Registration page should load"""
        response = client.get('/register')
        assert response.status_code == 200
        assert b'Create your account' in response.data
    
    def test_register_creates_org_and_user(self, client, app):
        """Registration should create organization and user"""
        from app.models import Organization, User
        
        response = client.post('/register', data={
            'clinic_name': 'New Clinic',
            'first_name': 'John',
            'last_name': 'Doe',
            'username': 'johndoe',
            'email': 'john@newclinic.com',
            'password': 'securepassword123',
            'password_confirm': 'securepassword123'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        
        with app.app_context():
            org = Organization.query.filter_by(slug='new-clinic').first()
            assert org is not None
            assert org.name == 'New Clinic'
            
            user = User.query.filter_by(username='johndoe').first()
            assert user is not None
            assert user.organization_id == org.id
    
    def test_register_password_mismatch(self, client):
        """Registration should fail with mismatched passwords"""
        response = client.post('/register', data={
            'clinic_name': 'Test Clinic',
            'first_name': 'John',
            'last_name': 'Doe',
            'username': 'johndoe2',
            'email': 'john2@test.com',
            'password': 'password123',
            'password_confirm': 'differentpassword'
        })
        
        assert b'Passwords do not match' in response.data


class TestLogin:
    """Test user login"""
    
    def test_login_page_loads(self, client):
        """Login page should load"""
        response = client.get('/login')
        assert response.status_code == 200
        assert b'Welcome back' in response.data
    
    def test_login_success(self, client, test_user):
        """Login should succeed with valid credentials"""
        response = client.post('/login', data={
            'username': 'testuser',
            'password': 'testpassword123'
        }, follow_redirects=True)
        
        assert response.status_code == 200
    
    def test_login_invalid_password(self, client, test_user):
        """Login should fail with invalid password"""
        response = client.post('/login', data={
            'username': 'testuser',
            'password': 'wrongpassword'
        })
        
        assert b'Invalid username or password' in response.data
    
    def test_login_nonexistent_user(self, client):
        """Login should fail for nonexistent user"""
        response = client.post('/login', data={
            'username': 'nonexistent',
            'password': 'password123'
        })
        
        assert b'Invalid username or password' in response.data


class TestLogout:
    """Test user logout"""
    
    def test_logout(self, authenticated_client):
        """Logout should end session"""
        response = authenticated_client.get('/logout', follow_redirects=True)
        
        assert response.status_code == 200
        assert b'You have been logged out' in response.data


class TestProtectedRoutes:
    """Test route protection"""
    
    def test_doctor_requires_login(self, client):
        """Doctor view should require authentication"""
        response = client.get('/doctor')
        assert response.status_code == 302  # Redirect to login
    
    def test_assistant_requires_login(self, client):
        """Assistant view should require authentication"""
        response = client.get('/assistant')
        assert response.status_code == 302  # Redirect to login
    
    def test_account_requires_login(self, client):
        """Account page should require authentication"""
        response = client.get('/account')
        assert response.status_code == 302  # Redirect to login
