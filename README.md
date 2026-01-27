# Maneiro.ai - Clinical Documentation Assistant

🏥 Transform clinical notes into professional referral letters with AI-powered analysis

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/flask-3.1-green.svg)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Features

- **Document Analysis** - Upload PDFs, images, or audio recordings for AI-powered clinical analysis
- **Smart Extraction** - Automatic extraction of diagnoses, treatment plans, and clinical findings
- **Letter Generation** - Generate professional referral letters, patient letters, and insurance documentation
- **Evidence Citations** - Automatic PubMed references and clinical guideline citations
- **Multi-Language** - Support for English, Spanish, Portuguese, and French
- **OCR Support** - Extract text from scanned documents and handwritten notes
- **Audio Transcription** - AWS Transcribe integration for voice recordings
- **Assistant Mode** - Triage incoming faxes and generate patient communications

## Quick Start

```bash
# Clone the repository
git clone https://github.com/yourusername/maneiro-ai.git
cd maneiro-ai

# Start database with Docker
docker-compose up -d postgres

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Initialize database
python init_db.py

# Run the application
flask run
```

Visit: http://localhost:5000

## Configuration

Create a `.env` file with the following variables:

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/maneiro

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

# AWS (for audio transcription and file storage)
AWS_S3_BUCKET=your-bucket-name
AWS_REGION=us-west-2
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

# Application
SECRET_KEY=your-secret-key
FLASK_ENV=development
```

## Project Structure

```
maneiro-ai/
├── app/
│   ├── __init__.py          # Application factory
│   ├── api.py               # API endpoints
│   ├── auth.py              # Authentication routes
│   ├── models.py            # Database models
│   ├── templates/           # Jinja2 templates
│   │   ├── index.html       # Doctor view
│   │   ├── assistant.html   # Assistant/staff view
│   │   └── auth/            # Auth templates
│   └── static/
│       ├── css/app.css      # Styles
│       ├── js/app.js        # Frontend logic
│       └── img/             # Images
├── docs/                    # Documentation
├── tests/                   # Test suite
├── config.py               # Configuration classes
├── wsgi.py                 # WSGI entry point
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container build
├── docker-compose.yml      # Local development
└── render.yaml             # Render deployment
```

## Architecture

### Multi-Tenant Design

```
Organization (Clinic)
├── Users (Doctors, Staff)
├── Jobs (Analysis tasks)
└── Audit Logs (Compliance)
```

### User Roles

- **Admin** - Full access, team management
- **Doctor** - Clinical analysis and letter generation
- **Staff** - Triage and patient communications

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/analyze_start` | POST | Start document analysis |
| `/analyze_status` | GET | Check analysis progress |
| `/generate_report` | POST | Generate referral letter |
| `/triage_fax` | POST | Triage incoming communication |
| `/generate_assistant_letter` | POST | Generate patient/insurance letter |
| `/export_pdf` | POST | Export letter as PDF |
| `/transcribe_start` | POST | Start audio transcription |

## Deployment

### Render (Recommended)

1. Connect your GitHub repository to Render
2. Add PostgreSQL database
3. Set environment variables
4. Deploy automatically on push

### Docker

```bash
docker build -t maneiro-ai .
docker run -p 5000:5000 --env-file .env maneiro-ai
```

## Development

### Running Tests

```bash
pytest
pytest --cov=app tests/  # With coverage
```

### Database Migrations

```bash
flask db migrate -m "Description"
flask db upgrade
```

## Tech Stack

- **Backend**: Flask 3.1, SQLAlchemy, Flask-Login
- **Database**: PostgreSQL
- **AI**: OpenAI GPT-4
- **OCR**: Tesseract, PyMuPDF
- **PDF**: ReportLab, PyPDF2
- **Cloud**: AWS S3, AWS Transcribe
- **Deployment**: Render, Docker

## Documentation

- [Quick Start Guide](docs/QUICKSTART.md)
- [Phased Implementation](docs/PHASED_IMPLEMENTATION.md)
- [Phase 1 Checklist](docs/PHASE_1_CHECKLIST.md)

## License

MIT License - see [LICENSE](LICENSE) file

## Support

For support, email support@maneiro.ai

---

**Version:** 2026.8 | **Status:** Production Ready
