# Maneiro.ai - Clinical Documentation Assistant

🏥 Transform clinical notes into professional referral letters with AI-powered analysis

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/flask-3.1-green.svg)](https://flask.palletsprojects.com/)

## v2026.8 New Features

- **Progress Stages** - Real-time progress bar showing analysis steps (Extracting → Analyzing → Structuring → References → Citations → Complete)
- **Health Endpoint** - `/healthz` for load balancer health checks
- **Version Endpoint** - `/version` returns build info and feature flags

## 🚀 Quick Start

```bash
git clone https://github.com/yourusername/maneiro-ai.git
cd maneiro-ai
docker-compose up -d
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Edit with your keys
flask db upgrade
flask run
```

Visit: http://localhost:5000

## 📖 Documentation

- **[Phased Implementation](docs/PHASED_IMPLEMENTATION.md)** - Strategic rollout plan
- **[Phase 1 Checklist](docs/PHASE_1_CHECKLIST.md)** - 2-week sprint guide

## 🏗️ Phase 1 Architecture (Current)

**Status:** ✅ Ready to implement | **Timeline:** 2 weeks

```
Multi-Tenant System
├── Organizations (clinics) → billing entity
│   ├── Users (doctors, staff)
│   └── Jobs (analysis tasks)
├── Authentication (Flask-Login)
├── PostgreSQL (persistence)
└── Audit Logging (compliance)
```

**Core Features:**
- ✅ Multi-tenant (organizations + users)
- ✅ Job persistence in PostgreSQL
- ✅ Usage limits (50 jobs/month trial)
- ✅ Audit logging
- ✅ PDF/image OCR analysis
- ✅ AWS Transcribe integration
- ✅ AI report generation
- ✅ PubMed citations

## 🔑 Multi-Tenant Design

Every user belongs to an Organization:

```python
# Registration creates org + admin user
Organization(name="Vancouver Eye Clinic", plan="trial", max_jobs=50)
User(organization_id=1, email="dr@clinic.com", role="admin")

# Usage checked at org level
if not org.can_create_job:  # Checks org.monthly_count < org.max_jobs
    return error("Limit reached")
```

## 🗄️ Database Schema

```sql
organizations   -- Clinics (billable entities)
users          -- Belongs to organization  
jobs           -- Belongs to org + user
audit_logs     -- Compliance tracking
```

## ⚙️ Configuration

**Phase 1:** Use environment variables (simple)
```bash
DATABASE_URL=postgresql://...
OPENAI_API_KEY=sk-...
AWS_S3_BUCKET=bucket
SECRET_KEY=secret
```

**Phase 2:** AWS Parameter Store (optional later)

## 📊 Phase Roadmap

### Phase 1: Foundation (2 weeks) ← **YOU ARE HERE**
- Multi-tenant architecture
- User authentication
- Job persistence  
- Audit logging
- Usage limits

### Phase 2: Monetization (2 weeks)
- Stripe integration
- Multi-tier pricing
- Webhooks
- Redis + rate limiting

### Phase 3: Enterprise (8-12 weeks)
- Team management
- SSO, API access
- Custom branding
- Advanced compliance

## ✅ Phase 1 Success Criteria

Ready for Phase 2 when:
- [ ] 3+ clinics registered
- [ ] Jobs persist across restarts
- [ ] Usage limits enforced
- [ ] Audit log working
- [ ] Demoed to real clinic

## 🚀 Deploy to Render

```bash
git push origin main
# Render auto-deploys
# Add PostgreSQL database
# Set environment variables
# Migrations run automatically
```

## 🛠️ Tech Stack

Flask 3.1 | PostgreSQL | SQLAlchemy | OpenAI GPT-4 | AWS S3/Transcribe | ReportLab | Tesseract OCR

## 📞 Next Steps

1. Read: `docs/PHASED_IMPLEMENTATION.md`
2. Follow: `docs/PHASE_1_CHECKLIST.md`  
3. Ship Phase 1 in 2 weeks

---

**Version:** 2026.8 | **Status:** Production Ready | **Python:** 3.11+
