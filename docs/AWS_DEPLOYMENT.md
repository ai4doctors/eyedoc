# Maneiro.ai AWS Deployment Guide

## Your Current Setup
- **App Runner Service**: `tjkedirmgv.us-east-2.awsapprunner.com` ✅ Working
- **Custom Domain**: `app.maneiro.ai` ❌ Not working (needs DNS fix)
- **Region**: `us-east-2` (Ohio)

---

## QUICK FIX: Custom Domain (app.maneiro.ai)

### Step 1: Check App Runner Custom Domain Status

1. Go to **AWS Console** → **App Runner** → **Your Service**
2. Click **Custom domains** tab
3. Check the status of `app.maneiro.ai`

**If domain is not added:**
- Click **Link domain**
- Enter `app.maneiro.ai`
- Copy the certificate validation records

### Step 2: Configure Route 53

Go to **Route 53** → **Hosted zones** → **maneiro.ai**

**Add these records:**

**Record 1 - Main CNAME:**
```
Record name: app
Record type: CNAME
Value: tjkedirmgv.us-east-2.awsapprunner.com
TTL: 300
```

**Record 2 - Certificate Validation (if App Runner shows one):**
```
Record name: _xxxx.app.maneiro.ai  (copy from App Runner)
Record type: CNAME
Value: _yyyy.acm-validations.aws  (copy from App Runner)
TTL: 300
```

### Step 3: Wait and Verify

```bash
# Check DNS (wait 5-10 minutes for propagation)
dig app.maneiro.ai CNAME

# Test the domain
curl -I https://app.maneiro.ai/healthz
```

---

## Deploy Updated Code to AWS

### Option 1: Deploy via ECR (Docker Push)

This is what you were doing with VS Code + Docker.

```bash
# 1. Set your account ID
export AWS_ACCOUNT_ID="YOUR_12_DIGIT_ACCOUNT_ID"
export AWS_REGION="us-east-2"

# 2. Login to ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

# 3. Build image
docker build -t maneiro .

# 4. Tag for ECR
docker tag maneiro:latest \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/maneiro:latest

# 5. Push to ECR
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/maneiro:latest

# App Runner will auto-deploy if configured for automatic deployment
```

### Option 2: Deploy via GitHub Connection

If you prefer GitHub-based deployment:

1. **App Runner** → **Your Service** → **Source and deployment**
2. Change to **Source code repository**
3. Connect to GitHub and select your repo
4. App Runner will build and deploy on each push

---

## Environment Variables

Set these in **App Runner** → **Your Service** → **Configuration**:

| Variable | Value | Notes |
|----------|-------|-------|
| `FLASK_SECRET_KEY` | `your-secure-random-64-char-string` | Generate with `openssl rand -hex 32` |
| `OPENAI_API_KEY` | `sk-...` | Your OpenAI key |
| `AWS_REGION` | `us-east-2` | Your region |
| `AWS_S3_BUCKET` | `your-bucket-name` | For audio uploads |
| `DATABASE_URL` | `postgresql://...` or `sqlite:///maneiro.db` | See database section |
| `APP_VERSION` | `2026.5` | Current version |
| `CLINIC_NAME` | `Integra Eyecare Centre` | Optional |
| `CLINIC_SHORT` | `Integra` | For PDF filenames |

---

## Database Setup

### Option A: SQLite (Simple, for testing)

Just set:
```
DATABASE_URL=sqlite:///maneiro.db
```

**Note:** Data is lost when container restarts. Good for testing only.

### Option B: RDS PostgreSQL (Production)

1. **Create RDS instance** (if not already done):
   - Engine: PostgreSQL 15
   - Instance: db.t3.micro (free tier eligible)
   - Storage: 20 GB
   - Make note of endpoint, username, password

2. **Set DATABASE_URL:**
```
DATABASE_URL=postgresql://USERNAME:PASSWORD@YOUR-RDS-ENDPOINT.rds.amazonaws.com:5432/maneiro
```

3. **Initialize database** - set temporarily:
```
RESET_DB=1
```

4. **After first deploy, remove RESET_DB**

---

## IAM Permissions

Your App Runner service needs an **Instance Role** with these permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject"
            ],
            "Resource": "arn:aws:s3:::YOUR-BUCKET/*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "transcribe:StartTranscriptionJob",
                "transcribe:GetTranscriptionJob"
            ],
            "Resource": "*"
        }
    ]
}
```

Set in: **App Runner** → **Your Service** → **Configuration** → **Security** → **Instance role**

---

## Dockerfile for AWS

Update your `Dockerfile`:

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-por \
    poppler-utils libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# App Runner uses PORT env var (default 8080)
CMD ["sh", "-c", "gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8080} --timeout 180 --workers 2"]
```

**Important:** App Runner defaults to port **8080**, not 10000. Make sure your service is configured to use port 8080.

---

## Deploy Script

Create `deploy-aws.sh` in your project root:

```bash
#!/bin/bash
set -e

# Configuration
AWS_ACCOUNT_ID="YOUR_ACCOUNT_ID"  # Replace with your 12-digit account ID
AWS_REGION="us-east-2"
ECR_REPO="maneiro"

echo "🔐 Logging into ECR..."
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

echo "🏗️  Building Docker image..."
docker build -t $ECR_REPO .

echo "🏷️  Tagging image..."
docker tag $ECR_REPO:latest \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:latest

echo "📤 Pushing to ECR..."
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:latest

echo ""
echo "✅ Image pushed! App Runner will auto-deploy."
echo "🔗 Check status: https://us-east-2.console.aws.amazon.com/apprunner"
echo "🌐 Default URL: https://tjkedirmgv.us-east-2.awsapprunner.com"
echo "🌐 Custom URL: https://app.maneiro.ai"
```

Make executable:
```bash
chmod +x deploy-aws.sh
```

---

## Verification Checklist

After deployment:

- [ ] Health check works: `curl https://tjkedirmgv.us-east-2.awsapprunner.com/healthz`
- [ ] Login page loads
- [ ] Can register/login
- [ ] File upload works
- [ ] Analysis completes
- [ ] Letter generation works
- [ ] PDF export works
- [ ] Custom domain resolves (after DNS setup)

---

## Troubleshooting

### Custom Domain Not Working

1. **Check App Runner**: Custom domains → Status should be "Active"
2. **Check Route 53**: Ensure CNAME record exists for `app`
3. **Check certificate**: ACM → Certificates → Should be "Issued"
4. **Test DNS**: `dig app.maneiro.ai` should show CNAME to App Runner

### 502/503 Errors

Check CloudWatch logs:
```bash
aws logs tail /aws/apprunner/maneiro/application --follow --region us-east-2
```

Common causes:
- Missing environment variables
- Database connection failed
- Port mismatch (should be 8080)
- Container crashed on startup

### Transcription Not Working

1. Check IAM role has `transcribe:*` permissions
2. Check S3 bucket exists and is accessible
3. Check `AWS_REGION` and `AWS_S3_BUCKET` are set

---

## Next Steps

1. **Fix custom domain** (add CNAME in Route 53)
2. **Push updated code** (run deploy script)
3. **Set environment variables** (especially `OPENAI_API_KEY`)
4. **Test the application**

Need me to help with any specific step?
