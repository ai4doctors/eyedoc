"""
API routes with authentication and rate limiting
"""
from flask import Blueprint, request, jsonify, send_file
from flask_login import login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename
from .models import db, Job, JobType, JobStatus
from .auth import usage_limit_check, log_audit_event
from .services.pdf_service import extract_text_from_upload, export_pdf_document
from .services.openai_service import analyze_note, generate_report
from .services.aws_service import start_transcription, get_transcription_status
import os
import uuid
from datetime import datetime, timezone

api_bp = Blueprint('api', __name__)

# Rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100 per hour"]
)


def allowed_file(filename):
    """Check if file extension is allowed"""
    ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'webp'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@api_bp.route('/analyze', methods=['POST'])
@login_required
@usage_limit_check
@limiter.limit("20 per hour")
def analyze():
    """Start analysis job"""
    
    # Check if file was uploaded
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'ok': False, 'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'ok': False, 'error': 'Invalid file type'}), 400
    
    try:
        # Create job
        job_id = f"job_{uuid.uuid4().hex}"
        filename = secure_filename(file.filename)
        force_ocr = request.form.get('force_ocr', 'false').lower() == 'true'
        
        job = Job(
            id=job_id,
            user_id=current_user.id,
            job_type=JobType.ANALYSIS,
            status=JobStatus.WAITING,
            input_filename=filename
        )
        db.session.add(job)
        db.session.commit()
        
        # Extract text
        text, used_ocr, needs_ocr, error = extract_text_from_upload(
            file, 
            force_ocr
        )
        
        if error:
            job.status = JobStatus.ERROR
            job.error_message = error
            db.session.commit()
            return jsonify({'ok': False, 'error': error, 'needs_ocr': needs_ocr}), 400
        
        if needs_ocr and not force_ocr:
            job.status = JobStatus.WAITING
            db.session.commit()
            return jsonify({
                'ok': False,
                'needs_ocr': True,
                'error': 'OCR required for this file'
            }), 400
        
        # Start analysis
        job.status = JobStatus.PROCESSING
        db.session.commit()
        
        # Analyze in background (ideally use Celery)
        analysis = analyze_note(text)
        
        if analysis:
            job.status = JobStatus.COMPLETE
            job.analysis_data = analysis
            job.completed_at = datetime.now(timezone.utc)
            
            # Increment user job count
            current_user.increment_job_count()
            
            db.session.commit()
            
            log_audit_event('analysis_completed', f'Completed analysis job {job_id}')
            
            return jsonify({
                'ok': True,
                'job_id': job_id,
                'analysis': analysis
            })
        else:
            job.status = JobStatus.ERROR
            job.error_message = 'Analysis failed'
            db.session.commit()
            return jsonify({'ok': False, 'error': 'Analysis failed'}), 500
            
    except Exception as e:
        if 'job' in locals():
            job.status = JobStatus.ERROR
            job.error_message = str(e)
            db.session.commit()
        return jsonify({'ok': False, 'error': str(e)}), 500


@api_bp.route('/analyze/status/<job_id>', methods=['GET'])
@login_required
@limiter.limit("100 per hour")
def analyze_status(job_id):
    """Get analysis job status"""
    
    job = Job.query.filter_by(id=job_id, user_id=current_user.id).first()
    
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    
    response = {
        'ok': True,
        'job_id': job.id,
        'status': job.status.value,
        'created_at': job.created_at.isoformat(),
        'updated_at': job.updated_at.isoformat()
    }
    
    if job.status == JobStatus.COMPLETE:
        response['analysis'] = job.analysis_data
        response['completed_at'] = job.completed_at.isoformat()
    
    if job.status == JobStatus.ERROR:
        response['error'] = job.error_message
    
    return jsonify(response)


@api_bp.route('/transcribe', methods=['POST'])
@login_required
@usage_limit_check
@limiter.limit("10 per hour")
def transcribe():
    """Start transcription job"""
    
    if 'audio' not in request.files:
        return jsonify({'ok': False, 'error': 'No audio file'}), 400
    
    audio = request.files['audio']
    if audio.filename == '':
        return jsonify({'ok': False, 'error': 'No file selected'}), 400
    
    try:
        # Create job
        job_id = f"transcribe_{uuid.uuid4().hex}"
        language = request.form.get('language', 'auto')
        mode = request.form.get('mode', 'dictation')
        
        job = Job(
            id=job_id,
            user_id=current_user.id,
            job_type=JobType.TRANSCRIPTION,
            status=JobStatus.PROCESSING,
            input_filename=audio.filename
        )
        db.session.add(job)
        db.session.commit()
        
        # Start AWS Transcribe
        aws_job_id, error = start_transcription(audio, language, mode)
        
        if error:
            job.status = JobStatus.ERROR
            job.error_message = error
            db.session.commit()
            return jsonify({'ok': False, 'error': error}), 500
        
        job.aws_transcribe_job_id = aws_job_id
        db.session.commit()
        
        log_audit_event('transcription_started', f'Started transcription {job_id}')
        
        return jsonify({'ok': True, 'job_id': job_id})
        
    except Exception as e:
        if 'job' in locals():
            job.status = JobStatus.ERROR
            job.error_message = str(e)
            db.session.commit()
        return jsonify({'ok': False, 'error': str(e)}), 500


@api_bp.route('/transcribe/status/<job_id>', methods=['GET'])
@login_required
@limiter.limit("100 per hour")
def transcribe_status(job_id):
    """Get transcription job status"""
    
    job = Job.query.filter_by(id=job_id, user_id=current_user.id).first()
    
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    
    # Check AWS Transcribe status
    if job.aws_transcribe_job_id and job.status == JobStatus.PROCESSING:
        transcript, status, error = get_transcription_status(job.aws_transcribe_job_id)
        
        if status == 'completed':
            job.status = JobStatus.COMPLETE
            job.transcript_text = transcript
            job.completed_at = datetime.now(timezone.utc)
            current_user.increment_job_count()
            db.session.commit()
            
        elif status == 'failed':
            job.status = JobStatus.ERROR
            job.error_message = error or 'Transcription failed'
            db.session.commit()
    
    response = {
        'ok': True,
        'job_id': job.id,
        'status': job.status.value
    }
    
    if job.status == JobStatus.COMPLETE:
        response['transcript'] = job.transcript_text
    
    if job.status == JobStatus.ERROR:
        response['error'] = job.error_message
    
    return jsonify(response)


@api_bp.route('/generate-report', methods=['POST'])
@login_required
@limiter.limit("30 per hour")
def generate_report_endpoint():
    """Generate report from analysis"""
    
    data = request.get_json()
    form = data.get('form', {})
    analysis = data.get('analysis', {})
    
    if not analysis:
        return jsonify({'ok': False, 'error': 'No analysis data'}), 400
    
    try:
        letter_plain, letter_html = generate_report(form, analysis)
        
        log_audit_event('report_generated', 'Generated report')
        
        return jsonify({
            'ok': True,
            'letter_plain': letter_plain,
            'letter_html': letter_html
        })
        
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@api_bp.route('/export-pdf', methods=['POST'])
@login_required
@limiter.limit("50 per hour")
def export_pdf_endpoint():
    """Export letter as PDF"""
    
    data = request.get_json()
    text = data.get('text', '')
    provider_name = data.get('provider_name', '')
    
    if not text:
        return jsonify({'error': 'No content'}), 400
    
    try:
        pdf_path, filename = export_pdf_document(
            text=text,
            provider_name=provider_name,
            patient_token=data.get('patient_token', ''),
            recipient_type=data.get('recipient_type', ''),
            letterhead_data_url=data.get('letterhead_data_url', ''),
            signature_data_url=data.get('signature_data_url', '')
        )
        
        log_audit_event('pdf_exported', 'Exported PDF')
        
        return send_file(
            pdf_path,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/jobs', methods=['GET'])
@login_required
@limiter.limit("100 per hour")
def get_jobs():
    """Get user's jobs"""
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    jobs = Job.query.filter_by(user_id=current_user.id)\
        .order_by(Job.created_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    return jsonify({
        'ok': True,
        'jobs': [{
            'id': job.id,
            'type': job.job_type.value,
            'status': job.status.value,
            'filename': job.input_filename,
            'created_at': job.created_at.isoformat(),
            'completed_at': job.completed_at.isoformat() if job.completed_at else None
        } for job in jobs.items],
        'total': jobs.total,
        'pages': jobs.pages,
        'current_page': jobs.page
    })


@api_bp.route('/usage', methods=['GET'])
@login_required
def get_usage():
    """Get user usage statistics"""
    
    return jsonify({
        'ok': True,
        'subscription_tier': current_user.subscription_tier.value,
        'subscription_status': current_user.subscription_status.value,
        'monthly_jobs': current_user.monthly_job_count,
        'total_jobs': current_user.total_jobs,
        'can_create_job': current_user.can_create_job
    })
