# Maneiro.ai

Clinical documentation assistant for post-visit reasoning and document quality.

## What It Does

Maneiro takes messy clinical inputs (PDFs, audio, images) and produces structured outputs:
- **Diagnosis** with ICD-10 codes and laterality
- **Plan** aligned to diagnoses  
- **References** from PubMed

Optimized for referral letters, consult letters, follow-ups, and specialty communication.

## What It Is NOT

Maneiro is not an ambient AI scribe. We don't compete with Nabla, Suki, Abridge, or DeepScribe.
Those tools focus on live transcription and tight EMR integration. 
Our strength is post-visit reasoning and document quality.

## Quick Start

```bash
# 1. Copy environment
cp .env.example .env
# Add your OPENAI_API_KEY to .env

# 2. Install dependencies  
pip install -r requirements.txt

# 3. Run
python app.py
```

## Deploy on Render

The included `render.yaml` configures a standard Python web service.

## Environment Variables

```bash
# Required
OPENAI_API_KEY=sk-...

# Optional
APP_VERSION=2026.6
FLASK_SECRET_KEY=change-me
OPENAI_MODEL=gpt-4.1
BUILD_TIME=2026-01-27
GIT_COMMIT=abc1234

# Feature flags
FEATURE_STRICT_SCHEMA=1    # Schema validation with repair

# AWS (for audio transcription)
AWS_S3_BUCKET=
AWS_REGION=
```

## API Endpoints

```
POST /analyze_start     - Start document analysis
GET  /analyze_status    - Poll job status (includes stage progress)
POST /generate_report   - Generate letter from analysis
POST /export_pdf        - Export letter as PDF

GET  /healthz           - Health check
GET  /version           - Version and feature info
GET  /stages            - Available job stages
```

## Version 2026.6

**New in this release:**
- Progress stages showing what's happening during analysis (like Claude/ChatGPT)
- Schema validation with automatic repair
- Specialty selection before generation
- Backend-owned prompt logic
- Feature flags for safe iteration
- Enhanced logging and boot diagnostics

## Legal

Maneiro is a clinical documentation aid. It does not provide medical advice.
Clinicians are responsible for verifying accuracy before use.

All rights reserved.
