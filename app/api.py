"""
API Blueprint (Phase 1)

Endpoints:
- POST /api/analyze: Start analysis job
- GET /api/analyze/status/<job_id>: Check status
- POST /api/generate-report: Generate letter
- POST /api/export-pdf: Export as PDF
"""
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models import Job, JobStatus

api_bp = Blueprint('api', __name__)

@api_bp.route('/analyze', methods=['POST'])
@login_required
def analyze():
    # Check org usage limit
    if not current_user.organization.can_create_job:
        return jsonify({
            'error': 'Monthly limit reached',
            'used': current_user.organization.monthly_job_count,
            'max': current_user.organization.max_monthly_jobs
        }), 429
    
    # TODO: Implement analysis
    # See original app.py for full implementation
    return jsonify({'job_id': 'job_123', 'status': 'processing'})

@api_bp.route('/analyze/status/<job_id>')
@login_required
def analyze_status(job_id):
    job = Job.query.filter_by(
        id=job_id,
        organization_id=current_user.organization_id
    ).first_or_404()
    
    return jsonify({
        'status': job.status.value,
        'data': job.analysis_data
    })

# See docs/PHASE_1_CHECKLIST.md for full implementation
