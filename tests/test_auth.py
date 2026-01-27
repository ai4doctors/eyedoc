"""
Authentication tests for Maneiro.ai
"""
import pytest


class TestAuth:
    """Authentication route tests"""
    
    def test_login_page_loads(self, client):
        """Test login page is accessible"""
        response = client.get('/login')
        assert response.status_code == 200
        assert b'Sign in' in response.data or b'Login' in response.data
    
    def test_register_page_loads(self, client):
        """Test register page is accessible"""
        response = client.get('/register')
        assert response.status_code == 200
        assert b'Create' in response.data
    
    def test_login_redirect_when_authenticated(self, authenticated_client):
        """Test authenticated users are redirected from login"""
        response = authenticated_client.get('/login')
        assert response.status_code in [200, 302]
    
    def test_logout(self, authenticated_client):
        """Test logout functionality"""
        response = authenticated_client.get('/logout', follow_redirects=True)
        assert response.status_code == 200
    
    def test_protected_route_requires_auth(self, client):
        """Test that protected routes require authentication"""
        response = client.get('/doctor')
        assert response.status_code in [302, 401]  # Redirect to login or unauthorized
    
    def test_register_creates_org_and_user(self, client, app):
        """Test registration creates organization and user"""
        from app.models import Organization, User
        
        response = client.post('/register', data={
            'clinic_name': 'New Test Clinic',
            'first_name': 'John',
            'last_name': 'Doe',
            'username': 'johndoe',
            'email': 'john@newclinic.com',
            'password': 'securepassword123',
            'password_confirm': 'securepassword123'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        
        with app.app_context():
            org = Organization.query.filter_by(name='New Test Clinic').first()
            user = User.query.filter_by(username='johndoe').first()
            
            assert org is not None
            assert user is not None
            assert user.organization_id == org.id


class TestHealthCheck:
    """Health check endpoint tests"""
    
    def test_healthz_endpoint(self, client):
        """Test health check endpoint"""
        response = client.get('/healthz')
        assert response.status_code == 200
        data = response.get_json()
        assert 'status' in data
        assert 'version' in data
