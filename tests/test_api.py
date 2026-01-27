"""
API Endpoint Tests
"""
import pytest
import json


class TestHealthEndpoints:
    """Test health check endpoints"""
    
    def test_healthz(self, client):
        """Health check should return ok"""
        response = client.get('/healthz')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert 'status' in data
        assert 'version' in data
        assert 'timestamp' in data
    
    def test_version(self, client):
        """Version endpoint should return build info"""
        response = client.get('/version')
        assert response.status_code == 200
        
        data = json.loads(response.data)
        assert 'version' in data
        assert 'features' in data


class TestAnalyzeEndpoints:
    """Test document analysis endpoints"""
    
    def test_analyze_requires_auth(self, client):
        """Analyze endpoint should require authentication"""
        response = client.post('/analyze_start')
        assert response.status_code == 302  # Redirect to login
    
    def test_analyze_requires_file(self, authenticated_client):
        """Analyze should require a file upload"""
        response = authenticated_client.post('/analyze_start')
        assert response.status_code == 400
        
        data = json.loads(response.data)
        assert data['ok'] == False
        assert 'error' in data
    
    def test_analyze_status_requires_job_id(self, authenticated_client):
        """Status check should require job_id"""
        response = authenticated_client.get('/analyze_status')
        assert response.status_code == 400
        
        data = json.loads(response.data)
        assert data['ok'] == False


class TestTriageEndpoint:
    """Test triage endpoint"""
    
    def test_triage_requires_auth(self, client):
        """Triage endpoint should require authentication"""
        response = client.post('/triage_fax', 
            data=json.dumps({'analysis': {}}),
            content_type='application/json')
        assert response.status_code == 302


class TestLetterEndpoint:
    """Test letter generation endpoint"""
    
    def test_letter_requires_auth(self, client):
        """Letter endpoint should require authentication"""
        response = client.post('/generate_assistant_letter',
            data=json.dumps({'analysis': {}, 'letter_type': 'patient'}),
            content_type='application/json')
        assert response.status_code == 302
    
    def test_letter_with_empty_analysis(self, authenticated_client):
        """Letter generation with empty analysis should still work"""
        response = authenticated_client.post('/generate_assistant_letter',
            data=json.dumps({
                'analysis': {},
                'letter_type': 'patient'
            }),
            content_type='application/json')
        
        # May fail due to missing OpenAI key, but shouldn't crash
        assert response.status_code == 200


class TestReportEndpoint:
    """Test report generation endpoint"""
    
    def test_report_requires_auth(self, client):
        """Report endpoint should require authentication"""
        response = client.post('/generate_report',
            data=json.dumps({'form': {}, 'analysis': {}}),
            content_type='application/json')
        assert response.status_code == 302


class TestExportPDF:
    """Test PDF export endpoint"""
    
    def test_export_requires_auth(self, client):
        """PDF export should require authentication"""
        response = client.post('/export_pdf',
            data=json.dumps({'text': 'Test content'}),
            content_type='application/json')
        assert response.status_code == 302
    
    def test_export_requires_content(self, authenticated_client):
        """PDF export should require content"""
        response = authenticated_client.post('/export_pdf',
            data=json.dumps({'text': ''}),
            content_type='application/json')
        
        assert response.status_code == 400
