# AI4Doctors

A lightweight Flask app for clinicians:
- Upload one encounter PDF
- Generates structured clinical summary, ranked differential, next steps, plan
- Pulls PubMed references via NCBI E-utilities
- Generates clean, copyable rich text letters without re-uploading

## Deploy on Render
1. Push this repo to GitHub
2. Create a Render Web Service from the repo
3. Ensure the start command is `gunicorn app:app`
4. Optional: set `OPENAI_API_KEY` to enable higher-quality drafting

## Local run
```bash
pip install -r requirements.txt
python app.py
```

## Environment variables
- OPENAI_API_KEY (optional)
- OPENAI_MODEL (optional, default gpt-4o-mini)
- NCBI_API_KEY (optional)
- APP_VERSION, BUILD_TIME
