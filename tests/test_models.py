"""
Database Model Tests
"""
import pytest
from app.models import Organization, User, Job, AuditLog, OrganizationPlan, UserRole, JobStatus


class TestOrganization:
    """Test Organization model"""
    
    def test_create_organization(self, app):
        """Should create organization"""
        from app import db
        
        with app.app_context():
            org = Organization(
                name='Test Org',
                slug='test-org',
                email='test@org.com',
                plan=OrganizationPlan.TRIAL
            )
            db.session.add(org)
            db.session.commit()
            
            assert org.id is not None
            assert org.name == 'Test Org'
            assert org.plan == OrganizationPlan.TRIAL
            assert org.monthly_job_count == 0
            assert org.max_monthly_jobs == 50
            
            db.session.delete(org)
            db.session.commit()
    
    def test_can_create_job(self, test_org, app):
        """Should check job creation limit"""
        with app.app_context():
            from app import db
            org = db.session.merge(test_org)
            
            assert org.can_create_job == True
            
            org.monthly_job_count = 50
            assert org.can_create_job == False


class TestUser:
    """Test User model"""
    
    def test_create_user(self, app, test_org):
        """Should create user"""
        from app import db
        
        with app.app_context():
            org = db.session.merge(test_org)
            
            user = User(
                organization_id=org.id,
                username='newuser',
                email='new@user.com',
                first_name='New',
                last_name='User',
                role=UserRole.DOCTOR
            )
            user.set_password('password123')
            db.session.add(user)
            db.session.commit()
            
            assert user.id is not None
            assert user.check_password('password123') == True
            assert user.check_password('wrongpassword') == False
            
            db.session.delete(user)
            db.session.commit()
    
    def test_password_hashing(self, app, test_org):
        """Password should be hashed"""
        from app import db
        
        with app.app_context():
            org = db.session.merge(test_org)
            
            user = User(
                organization_id=org.id,
                username='hashtest',
                first_name='Hash',
                last_name='Test',
                role=UserRole.STAFF
            )
            user.set_password('mypassword')
            
            assert user.password_hash != 'mypassword'
            assert user.check_password('mypassword') == True


class TestJob:
    """Test Job model"""
    
    def test_create_job(self, app, test_org, test_user):
        """Should create job"""
        from app import db
        
        with app.app_context():
            org = db.session.merge(test_org)
            user = db.session.merge(test_user)
            
            job = Job(
                id='job_test_123',
                organization_id=org.id,
                user_id=user.id,
                status=JobStatus.WAITING,
                input_filename='test.pdf'
            )
            db.session.add(job)
            db.session.commit()
            
            assert job.id == 'job_test_123'
            assert job.status == JobStatus.WAITING
            
            db.session.delete(job)
            db.session.commit()


class TestAuditLog:
    """Test AuditLog model"""
    
    def test_create_audit_log(self, app, test_org, test_user):
        """Should create audit log entry"""
        from app import db
        
        with app.app_context():
            org = db.session.merge(test_org)
            user = db.session.merge(test_user)
            
            log = AuditLog(
                organization_id=org.id,
                user_id=user.id,
                event_type='test_event',
                event_description='Test event description',
                ip_address='127.0.0.1'
            )
            db.session.add(log)
            db.session.commit()
            
            assert log.id is not None
            assert log.event_type == 'test_event'
            assert log.created_at is not None
            
            db.session.delete(log)
            db.session.commit()
