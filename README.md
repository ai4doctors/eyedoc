# Maneiro.ai v2026.7

Clinical Documentation Assistant - Transform clinical notes into professional referral letters with AI-powered analysis.

## Features

### Core
- **Multi-tenant Architecture** - Organizations with role-based access (Owner, Admin, Doctor, Assistant)
- **User Authentication** - Secure login with bcrypt password hashing
- **Usage Tracking** - Per-organization job limits by subscription tier
- **Audit Logging** - Compliance-ready activity tracking

### Analysis (v2026.6+)
- **9-Stage Progress Tracking** - Real-time progress updates during analysis
- **Schema Validation** - Strict output validation with auto-repair
- **Multi-Specialty Support** - Ophthalmology, Primary Care, Cardiology, Dermatology
- **Quality Scoring** - Automatic referral quality assessment
- **ICD-10 Extraction** - Automatic diagnosis code extraction
- **Clinical Warnings** - Red flag identification

### Letter Generation
- **7-Stage Progress Tracking** - Real-time letter generation progress
- **Multiple Templates** - Standard, Urgent, Co-management, Second Opinion, Patient Education, Insurance
- **PDF Export** - Professional PDF output with letterhead/signature

## Directory Structure

```
maneiro-v2026.7/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── models.py            # Database models (User, Org, Job, AuditLog)
│   ├── auth.py              # Authentication routes
│   ├── api.py               # API endpoints with progress stages
│   ├── static/
│   │   ├── css/app.css
│   │   ├── js/app.js
│   │   └── images/
│   └── templates/
│       ├── index.html
│       └── auth/
├── config.py                # Configuration with AWS Parameter Store
├── wsgi.py                  # Production entry point
├── requirements.txt
├── Dockerfile
├── render.yaml
└── README.md
```

## Quick Start

### Local Development

```bash
# Clone and enter directory
git clone https://github.com/yourusername/maneiro-ai.git
cd maneiro-ai

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export FLASK_ENV=development
export SECRET_KEY=dev-secret-key
export OPENAI_API_KEY=sk-your-key

# Run
flask run
```

Visit http://localhost:5000

### Deploy to Render

1. Push to GitHub
2. Create new Web Service on Render
3. Connect repository
4. Add environment variables:
   - `OPENAI_API_KEY`
   - `SECRET_KEY` (auto-generated)
5. Deploy

## API Endpoints

### Analysis
- `POST /analyze_start` - Start analysis (accepts `specialty` param)
- `GET /analyze_status?job_id=xxx` - Get status with progress

### Letter
- `POST /letter_start` - Start letter generation (accepts `template` param)
- `GET /letter_status?job_id=xxx` - Get status with progress

### Configuration
- `GET /specialties` - List available specialties
- `GET /templates` - List available letter templates
- `GET /stages` - Get stage definitions
- `GET /healthz` - Health check
- `GET /version` - Version and feature flags

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FLASK_ENV` | Environment (development/production) | development |
| `SECRET_KEY` | Flask secret key | required |
| `DATABASE_URL` | PostgreSQL connection string | sqlite:///maneiro.db |
| `OPENAI_API_KEY` | OpenAI API key | required |
| `OPENAI_MODEL` | Model to use | gpt-4.1 |
| `APP_VERSION` | Version string | 2026.7 |
| `FEATURE_STRICT_SCHEMA` | Enable schema validation | 1 |
| `FEATURE_PROGRESS_STAGES` | Enable progress tracking | 1 |
| `FEATURE_MULTI_SPECIALTY` | Enable specialty selection | 1 |

## Subscription Tiers

| Tier | Jobs/Month | Team Members |
|------|------------|--------------|
| Free | 5 | 1 |
| Basic | 50 | 3 |
| Professional | 500 | 10 |
| Enterprise | Unlimited | Unlimited |

## Version History

- **v2026.7** - Full multi-tenant architecture with all v2026.6 improvements
- **v2026.6** - Progress stages, schema validation, specialty handling
- **v2026.5** - Quality scoring, ICD-10 extraction, clinical warnings
- **v2026.1** - Initial multi-tenant architecture

## License

Copyright © 2026 Maneiro.ai. All rights reserved.
