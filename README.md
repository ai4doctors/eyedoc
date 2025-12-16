AI4Doctors

Deploy on Render
1) Push this repo to GitHub
2) Create a new Render Web Service from the repo
3) Render will use render.yaml automatically

Environment variables
APP_VERSION: optional string shown in the footer
OPENAI_API_KEY: optional. If set, the app can be extended to use OpenAI for higher quality DDx, plan, and letters.

Notes
If your PDFs are scanned images, text extraction will be limited. OCR is not included in this build.
