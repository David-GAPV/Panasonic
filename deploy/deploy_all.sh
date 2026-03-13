#!/usr/bin/env bash
# =============================================================================
# Panasonic Vietnam IDP — AWS Infrastructure Deployment
# Deploys: S3 bucket, OCR EC2 (Tesseract), RDS PostgreSQL, Website EC2
# Region:  ap-southeast-1 (Singapore — closest to Vietnam)
# Usage:   ./deploy_all.sh [--teardown]
# =============================================================================
set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:-pnsn}"
export AWS_REGION="${AWS_REGION:-ap-southeast-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="$SCRIPT_DIR/.deploy-state"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()     { echo -e "${CYAN}[$(date '+%H:%M:%S')]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Preflight ─────────────────────────────────────────────────────────────
check_prerequisites() {
  log "Checking prerequisites..."
  command -v aws  >/dev/null 2>&1 || error "aws CLI not found. Install: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
  command -v jq   >/dev/null 2>&1 || error "jq not found. Install: sudo apt install jq / brew install jq"
  aws sts get-caller-identity >/dev/null 2>&1 || error "AWS credentials not configured for profile '$AWS_PROFILE'. Run: aws configure --profile $AWS_PROFILE"

  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
  USER_ARN=$(aws sts get-caller-identity --query Arn --output text)
  success "Authenticated as $USER_ARN (account $ACCOUNT_ID) in $AWS_REGION using profile '$AWS_PROFILE'"
}

# ── Teardown ──────────────────────────────────────────────────────────────
teardown() {
  warn "Starting TEARDOWN — all IDP resources will be destroyed."
  [[ -f "$STATE_FILE" ]] || error "No state file found at $STATE_FILE. Nothing to tear down."
  source "$STATE_FILE"

  read -p "$(echo -e "${RED}Confirm destroy ALL IDP resources? (yes/no): ${NC}")" CONFIRM
  [[ "$CONFIRM" == "yes" ]] || { log "Aborted."; exit 0; }

  bash "$SCRIPT_DIR/teardown.sh"
  rm -f "$STATE_FILE"
  success "Teardown complete."
  exit 0
}

[[ "${1:-}" == "--teardown" ]] && teardown

# ── Deploy ────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Panasonic Vietnam IDP — AWS Deployment${NC}"
echo -e "${BOLD}  Profile: $AWS_PROFILE${NC}"
echo -e "${BOLD}  Region: $AWS_REGION${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}\n"

check_prerequisites

# Initialise state file
cat > "$STATE_FILE" <<EOF
# IDP Deploy State — $(date)
AWS_PROFILE=$AWS_PROFILE
AWS_REGION=$AWS_REGION
ACCOUNT_ID=$ACCOUNT_ID
EOF

# Run each module in order
for script in \
  "01_network.sh" \
  "02_s3.sh" \
  "00_iam_role.sh" \
  "03_ocr_ec2.sh" \
  "04_rds.sh" \
  "05_website_ec2.sh"; do
  echo ""
  log "Running $script ..."
  bash "$SCRIPT_DIR/$script" "$STATE_FILE"
done

# ── Final summary ─────────────────────────────────────────────────────────
source "$STATE_FILE"
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  DEPLOYMENT COMPLETE${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "  ${CYAN}S3 Bucket:${NC}      s3://$S3_BUCKET"
echo -e "  ${CYAN}OCR EC2 IP:${NC}     $OCR_PUBLIC_IP  (SSH: ssh -i $KEY_NAME.pem ec2-user@$OCR_PUBLIC_IP)"
echo -e "  ${CYAN}RDS Endpoint:${NC}   $RDS_ENDPOINT:5432  (db: idpdb)"
echo -e "  ${CYAN}Website:${NC}        http://$WEB_PUBLIC_IP"
echo -e "  ${CYAN}Key Pair:${NC}       $KEY_NAME.pem  (keep safe — not recoverable)"
echo -e "  ${CYAN}State file:${NC}     $STATE_FILE"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}\n"
echo -e "  To destroy all resources: ${YELLOW}./deploy_all.sh --teardown${NC}\n"
