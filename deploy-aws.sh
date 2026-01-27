#!/bin/bash
set -e

# ============================================
# Maneiro.ai AWS Deployment Script
# ============================================

# Configuration - EDIT THESE VALUES
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-YOUR_12_DIGIT_ACCOUNT_ID}"
AWS_REGION="${AWS_REGION:-us-east-2}"
ECR_REPO="${ECR_REPO:-maneiro}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=======================================${NC}"
echo -e "${YELLOW}  Maneiro.ai AWS Deployment${NC}"
echo -e "${YELLOW}=======================================${NC}"
echo ""

# Check if AWS_ACCOUNT_ID is set
if [[ "$AWS_ACCOUNT_ID" == "YOUR_12_DIGIT_ACCOUNT_ID" ]]; then
    echo -e "${RED}Error: Please set AWS_ACCOUNT_ID${NC}"
    echo "Either:"
    echo "  1. Edit this script and replace YOUR_12_DIGIT_ACCOUNT_ID"
    echo "  2. Or run: AWS_ACCOUNT_ID=123456789012 ./deploy-aws.sh"
    exit 1
fi

ECR_URI="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

echo -e "${GREEN}Configuration:${NC}"
echo "  AWS Account: $AWS_ACCOUNT_ID"
echo "  Region: $AWS_REGION"
echo "  ECR Repo: $ECR_REPO"
echo "  Image Tag: $IMAGE_TAG"
echo ""

# Step 1: Login to ECR
echo -e "${YELLOW}🔐 Logging into ECR...${NC}"
aws ecr get-login-password --region $AWS_REGION | \
    docker login --username AWS --password-stdin $ECR_URI
echo -e "${GREEN}✓ Logged in${NC}"
echo ""

# Step 2: Build Docker image
echo -e "${YELLOW}🏗️  Building Docker image...${NC}"
docker build -t $ECR_REPO:$IMAGE_TAG .
echo -e "${GREEN}✓ Image built${NC}"
echo ""

# Step 3: Tag for ECR
echo -e "${YELLOW}🏷️  Tagging image for ECR...${NC}"
docker tag $ECR_REPO:$IMAGE_TAG $ECR_URI/$ECR_REPO:$IMAGE_TAG
echo -e "${GREEN}✓ Image tagged${NC}"
echo ""

# Step 4: Push to ECR
echo -e "${YELLOW}📤 Pushing to ECR...${NC}"
docker push $ECR_URI/$ECR_REPO:$IMAGE_TAG
echo -e "${GREEN}✓ Image pushed${NC}"
echo ""

# Done
echo -e "${GREEN}=======================================${NC}"
echo -e "${GREEN}  ✅ Deployment Complete!${NC}"
echo -e "${GREEN}=======================================${NC}"
echo ""
echo "If App Runner auto-deploy is enabled, your service will update automatically."
echo ""
echo "URLs:"
echo "  🔗 Default: https://tjkedirmgv.us-east-2.awsapprunner.com"
echo "  🌐 Custom:  https://app.maneiro.ai"
echo ""
echo "Monitor:"
echo "  📊 Console: https://$AWS_REGION.console.aws.amazon.com/apprunner"
echo "  📋 Logs:    aws logs tail /aws/apprunner/maneiro/application --follow"
