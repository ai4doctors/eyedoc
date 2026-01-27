# Maneiro.ai

**AI-Powered Clinical Documentation Assistant**

Transform clinical notes into professional referral letters, patient communications, and insurance documentation with AI-powered analysis.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/flask-3.1-green.svg)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Features

- **Document Analysis** - Extract structured data from clinical notes, PDFs, and images
- **ICD-10 Coding** - Automatic diagnosis coding across 14+ medical specialties
- **Letter Generation** - Create professional referral letters, patient letters, and insurance documentation
- **Voice Recording** - Record and transcribe clinical encounters with AWS Transcribe
- **Evidence Citations** - Automatic PubMed references and clinical guideline citations
- **Multi-tenant** - Organization-based access control with role management
- **PDF Export** - Professional letterhead and signature support

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 15+ (or Docker)
- OpenAI API key
- AWS account (optional, for S3/Transcribe)

### Local Development

```bash
# Clone repository
git clone https://github.com/yourusername/maneiro-ai.git
cd maneiro-ai

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start PostgreSQL (with Docker)
docker-compose up -d postgres

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Initialize database
python init_db.py

# Run development server
flask run
```

Visit http://localhost:5000

### First User Setup

```bash
flask shell
>>> from app import db
>>> from app.models import Organization, User, OrganizationPlan, UserRole
>>> org = Organization(name="My Clinic", slug="my-clinic", email="admin@clinic.com", plan=OrganizationPlan.TRIAL)
>>> db.session.add(org)
>>> db.session.flush()
>>> user = User(organization_id=org.id, username="admin", email="admin@clinic.com", first_name="Admin", last_name="User", role=UserRole.ADMIN)
>>> user.set_password("your-password")
>>> db.session.add(user)
>>> db.session.commit()
```

## Project Structure

```
maneiro-ai/
├── app/
│   ├── __init__.py      # Flask app factory
│   ├── api.py           # API endpoints
│   ├── auth.py          # Authentication routes
│   ├── models.py        # Database models
│   ├── templates/       # Jinja2 templates
│   │   ├── index.html
│   │   ├── assistant.html
│   │   └── auth/
│   └── static/
│       ├── css/
│       ├── js/
│       └── img/
├── docs/                # Documentation
├── migrations/          # Database migrations
├── tests/              # Test suite
├── config.py           # Configuration classes
├── wsgi.py             # WSGI entry point
├── Dockerfile          # Container definition
├── docker-compose.yml  # Local services
├── render.yaml         # Render deployment
└── requirements.txt    # Python dependencies
```

## Deployment

### Render (Recommended)

1. Fork this repository
2. Create new Web Service on Render
3. Connect your GitHub repo
4. Add PostgreSQL database
5. Set environment variables
6. Deploy

See [render.yaml](render.yaml) for infrastructure-as-code configuration.

### AWS App Runner

```bash
# Build and push to ECR
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin YOUR_ACCOUNT.dkr.ecr.us-east-2.amazonaws.com
docker build -t maneiro .
docker tag maneiro:latest YOUR_ACCOUNT.dkr.ecr.us-east-2.amazonaws.com/maneiroapp:latest
docker push YOUR_ACCOUNT.dkr.ecr.us-east-2.amazonaws.com/maneiroapp:latest

# Deploy via App Runner console or CLI
```

### Docker

```bash
docker build -t maneiro .
docker run -p 5000:5000 --env-file .env maneiro
```

## Configuration

| Variable | Description | Required |
|----------|-------------|----------|
| `SECRET_KEY` | Flask secret key | Yes |
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `OPENAI_API_KEY` | OpenAI API key | Yes |
| `OPENAI_MODEL` | Model name (default: gpt-4.1) | No |
| `AWS_S3_BUCKET` | S3 bucket for file storage | No |
| `AWS_REGION` | AWS region | No |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/analyze_start` | POST | Upload and analyze document |
| `/analyze_status` | GET | Check analysis job status |
| `/generate_report` | POST | Generate referral letter |
| `/export_pdf` | POST | Export letter as PDF |
| `/triage_fax` | POST | Triage incoming fax |
| `/transcribe_start` | POST | Start audio transcription |
| `/healthz` | GET | Health check |

## Documentation

- [Quick Start Guide](docs/QUICKSTART.md)
- [Phased Implementation](docs/PHASED_IMPLEMENTATION.md)
- [Phase 1 Checklist](docs/PHASE_1_CHECKLIST.md)

## License

MIT License - see [LICENSE](LICENSE) for details.

## Support

For issues and feature requests, please use GitHub Issues.

---

**Version:** 2026.8 | **Status:** Production Ready
