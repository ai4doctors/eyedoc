# Quick Start Guide

Get Maneiro running in 10 minutes.

## Prerequisites

- Python 3.11+
- PostgreSQL 15+ (or Docker)
- OpenAI API key
- AWS account (for S3 + Transcribe)

## Local Setup

### 1. Clone and Install

```bash
git clone https://github.com/yourusername/maneiro-ai.git
cd maneiro-ai
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Start Database

```bash
docker-compose up -d postgres
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your keys:
# - DATABASE_URL (already set for Docker)
# - OPENAI_API_KEY
# - AWS_S3_BUCKET
# - AWS_REGION
```

### 4. Initialize Database

```bash
flask db upgrade
```

### 5. Create First Organization

```bash
flask shell
>>> from app import db
>>> from app.models import Organization, User, OrganizationPlan, UserRole
>>> org = Organization(name="Test Clinic", slug="test-clinic", email="test@test.com", plan=OrganizationPlan.TRIAL)
>>> db.session.add(org)
>>> db.session.flush()
>>> user = User(organization_id=org.id, email="admin@test.com", first_name="Admin", last_name="User", role=UserRole.ADMIN)
>>> user.set_password("password123")
>>> db.session.add(user)
>>> db.session.commit()
>>> exit()
```

### 6. Run Application

```bash
flask run
```

Visit: http://localhost:5000

Login with: `admin@test.com` / `password123`

## Next Steps

1. Upload a clinical note PDF
2. Review analysis results
3. Generate a referral letter
4. Export as PDF

## Troubleshooting

**Database connection fails:**
```bash
docker-compose ps  # Check postgres is running
docker-compose logs postgres  # Check logs
```

**OCR not working:**
```bash
# Install Tesseract
brew install tesseract  # macOS
apt-get install tesseract-ocr  # Ubuntu
```

**AWS Transcribe fails:**
- Check AWS credentials are set
- Verify S3 bucket exists and has correct permissions

## See Also

- [PHASED_IMPLEMENTATION.md](PHASED_IMPLEMENTATION.md) - Full architecture
- [PHASE_1_CHECKLIST.md](PHASE_1_CHECKLIST.md) - Implementation guide
