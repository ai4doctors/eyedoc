# Maneiro.ai - Clinical Documentation Assistant

🏥 Transform clinical notes into professional referral letters with AI-powered analysis

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/flask-3.1-green.svg)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## ✨ Features

- **AI-Powered Analysis**: Extract diagnoses, plans, and clinical summaries from uploaded notes
- **Smart Reference Selection**: Age-appropriate PubMed citations (filters pediatric papers for adult patients)
- **Professional Letter Generation**: Create referral letters, patient letters, and insurance letters
- **Multi-Tenant Architecture**: Support for multiple clinics with usage tracking
- **Audio Transcription**: Record and transcribe exam notes via AWS Transcribe
- **OCR Support**: Extract text from scanned PDFs and images
- **PDF Export**: Professional letterhead-enabled PDF generation

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 15+ (or Docker)
- OpenAI API key
- AWS account (for S3 + Transcribe)

### Local Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/maneiro-ai.git
cd maneiro-ai

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start PostgreSQL with Docker
docker-compose up -d postgres

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Initialize database
python init_db.py

# Run the application
flask run
```

Visit: http://localhost:5000

## 📁 Project Structure

```
maneiro-ai/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── api.py               # API endpoints (analyze, generate, export)
│   ├── auth.py              # Authentication routes
│   ├── models.py            # Database models
│   ├── templates/
│   │   ├── index.html       # Doctor view (main app)
│   │   ├── assistant.html   # Staff view (triage & letters)
│   │   └── auth/            # Login, register, etc.
│   └── static/
│       ├── css/app.css
│       ├── js/app.js
│       └── img/
├── config.py                # Environment configurations
├── wsgi.py                  # Production entry point
├── init_db.py               # Database initialization
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── render.yaml              # Render.com deployment
├── docs/                    # Implementation guides
└── tests/                   # Test suite
```

## 🔑 Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `SECRET_KEY` | Flask secret key | Yes |
| `OPENAI_API_KEY` | OpenAI API key | Yes |
| `AWS_S3_BUCKET` | S3 bucket for uploads | For audio |
| `AWS_REGION` | AWS region | For audio |

## 🏗️ Architecture

### Multi-Tenant Design

Every user belongs to an Organization (clinic):

```python
Organization(name="Vancouver Eye Clinic", plan="trial", max_jobs=50)
User(organization_id=1, email="dr@clinic.com", role="admin")
```

### User Roles

- **Admin**: Full access + team management
- **Doctor**: Clinical analysis + letter generation
- **Staff**: Triage + patient/insurance letters

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/analyze_start` | POST | Start document analysis |
| `/analyze_status` | GET | Poll analysis status |
| `/generate_report` | POST | Generate referral letter |
| `/export_pdf` | POST | Export letter as PDF |
| `/triage_fax` | POST | Triage incoming documents |
| `/transcribe_start` | POST | Start audio transcription |

## 📊 Database Schema

```sql
organizations   -- Clinics (billable entities)
users           -- Belongs to organization  
jobs            -- Analysis tasks (belongs to org + user)
audit_logs      -- Compliance tracking
```

## 🚢 Deployment

### Render.com (Recommended)

1. Connect your GitHub repository
2. Add PostgreSQL database
3. Set environment variables
4. Deploy!

```yaml
# render.yaml is pre-configured
databases:
  - name: maneiro-db
    plan: starter

services:
  - type: web
    name: maneiro-web
    env: docker
```

### Docker

```bash
docker build -t maneiro-ai .
docker run -p 10000:10000 --env-file .env maneiro-ai
```

## 🧪 Testing

```bash
# Run all tests
pytest

# With coverage
pytest --cov=app tests/

# Specific test file
pytest tests/test_api.py -v
```

## 📖 Documentation

- [Phased Implementation Guide](docs/PHASED_IMPLEMENTATION.md)
- [Phase 1 Checklist](docs/PHASE_1_CHECKLIST.md)
- [Quick Start Guide](docs/QUICKSTART.md)

## 🔒 Security

- CSRF protection on all forms
- Bcrypt password hashing
- Session-based authentication
- Audit logging for compliance
- Rate limiting (Phase 2)

## 📝 License

MIT License - see [LICENSE](LICENSE) for details.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

---

**Version:** 2026.8 | **Status:** Production Ready | **Python:** 3.11+
