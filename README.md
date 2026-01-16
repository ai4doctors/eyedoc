# AI4Health

Stable synchronous build.

Features

1. Step 1 upload PDF or TXT and extract text inline
2. Step 2 aligned fields using CSS grid
3. Premium UI styling with Source Sans 3
4. Generated letter rendered as rich HTML only
5. Email button uses mailto with subject and body prefilled

Local run

1. Create a virtual environment
2. pip install -r requirements.txt
3. python app.py

Deploy

Procfile uses gunicorn app:app
