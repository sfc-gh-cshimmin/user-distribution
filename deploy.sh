#!/bin/bash
# =============================================================================
# Deploy User Distribution App to Snowflake SPCS
# Usage: ./deploy.sh [--build] [--push] [--setup] [--all]
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configuration
SNOWFLAKE_ACCOUNT="${SNOWFLAKE_ACCOUNT:-SFSENORTHAMERICA-PRODUCT_DEMOS}"
SNOWFLAKE_USER="${SNOWFLAKE_USER:-CSHIMMIN}"
SNOWFLAKE_DATABASE="user_distribution"
SNOWFLAKE_SCHEMA="spcs"
SNOWFLAKE_REPO="user_dist_repo"
IMAGE_NAME="user-distribution"
IMAGE_TAG="latest"

# Derived values
SNOWFLAKE_ACCOUNT_CLEAN=$(echo "${SNOWFLAKE_ACCOUNT%.snowflakecomputing.com}" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
REPO_URL="${SNOWFLAKE_ACCOUNT_CLEAN}.registry.snowflakecomputing.com/${SNOWFLAKE_DATABASE}/${SNOWFLAKE_SCHEMA}/${SNOWFLAKE_REPO}"
FULL_IMAGE_NAME="${REPO_URL}/${IMAGE_NAME}:${IMAGE_TAG}"

usage() {
    echo -e "${BLUE}Usage:${NC} $0 [options]"
    echo ""
    echo -e "${YELLOW}Options:${NC}"
    echo "  --build      Build the Docker image"
    echo "  --push       Push the Docker image to Snowflake"
    echo "  --setup      Show SQL setup instructions"
    echo "  --no-cache   Build without Docker cache"
    echo "  --all        Run all steps"
    echo "  --help       Show this help message"
}

DO_BUILD=false
DO_PUSH=false
DO_SETUP=false
NO_CACHE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --build) DO_BUILD=true; shift ;;
        --push) DO_PUSH=true; shift ;;
        --setup) DO_SETUP=true; shift ;;
        --no-cache) NO_CACHE=true; shift ;;
        --all) DO_BUILD=true; DO_PUSH=true; DO_SETUP=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo -e "${RED}Unknown option: $1${NC}"; usage; exit 1 ;;
    esac
done

if [[ "$DO_BUILD" == false && "$DO_PUSH" == false && "$DO_SETUP" == false ]]; then
    usage
    exit 1
fi

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         User Distribution - SPCS Deployment                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo -e "${YELLOW}Configuration:${NC}"
echo "  Account:    ${SNOWFLAKE_ACCOUNT}"
echo "  User:       ${SNOWFLAKE_USER}"
echo "  Image:      ${FULL_IMAGE_NAME}"
echo ""

# =============================================================================
# Build
# =============================================================================
if [[ "$DO_BUILD" == true ]]; then
    echo -e "${BLUE}Step 1: Building Docker Image (linux/amd64 for SPCS)${NC}"

    CACHE_FLAG=""
    if [[ "$NO_CACHE" == true ]]; then
        CACHE_FLAG="--no-cache"
    fi

    docker build \
        --platform linux/amd64 \
        $CACHE_FLAG \
        -f "$SCRIPT_DIR/Dockerfile" \
        -t "${IMAGE_NAME}:${IMAGE_TAG}" \
        -t "${FULL_IMAGE_NAME}" \
        "$SCRIPT_DIR"

    echo -e "${GREEN}✅ Docker image built${NC}"
    echo ""
fi

# =============================================================================
# Push
# =============================================================================
if [[ "$DO_PUSH" == true ]]; then
    echo -e "${BLUE}Step 2: Pushing Docker Image to Snowflake Registry${NC}"

    echo -e "${YELLOW}Logging in to Snowflake registry...${NC}"
    if [[ -n "${SNOWFLAKE_PASSWORD}" ]]; then
        echo "${SNOWFLAKE_PASSWORD}" | docker login "${SNOWFLAKE_ACCOUNT_CLEAN}.registry.snowflakecomputing.com" \
            -u "${SNOWFLAKE_USER}" --password-stdin
    else
        docker login "${SNOWFLAKE_ACCOUNT_CLEAN}.registry.snowflakecomputing.com" \
            -u "${SNOWFLAKE_USER}"
    fi

    echo -e "${YELLOW}Pushing: ${FULL_IMAGE_NAME}${NC}"
    docker push "${FULL_IMAGE_NAME}"

    echo -e "${GREEN}✅ Docker image pushed${NC}"
    echo ""
fi

# =============================================================================
# Setup instructions
# =============================================================================
if [[ "$DO_SETUP" == true ]]; then
    echo -e "${BLUE}Step 3: SQL Setup${NC}"
    echo ""
    echo -e "${YELLOW}Run these SQL scripts in Snowsight (as ACCOUNTADMIN):${NC}"
    echo "  1. ${SCRIPT_DIR}/setup/01_create_infrastructure.sql"
    echo "  2. ${SCRIPT_DIR}/setup/02_create_service.sql"
    echo ""
fi

# =============================================================================
# Summary
# =============================================================================
echo -e "${GREEN}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                    Deployment Complete!                      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo -e "${YELLOW}Next Steps:${NC}"
echo "1. Run SQL setup scripts in Snowsight"
echo "2. Check service status:"
echo "   SELECT SYSTEM\$GET_SERVICE_STATUS('USER_DIST_SERVICE');"
echo "3. Get the public URL:"
echo "   SHOW ENDPOINTS IN SERVICE USER_DIST_SERVICE;"
echo "4. Share with attendees: https://<endpoint>/EVENT_NAME"
