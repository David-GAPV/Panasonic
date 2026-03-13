#!/usr/bin/env bash
# teardown.sh — destroy all IDP AWS resources
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_FILE="$SCRIPT_DIR/.deploy-state"
source "$STATE_FILE"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()     { echo -e "${CYAN}  [teardown]${NC} $*"; }
success() { echo -e "${GREEN}  [OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}  [SKIP]${NC} $*"; }

# EC2 instances
for id_var in OCR_INSTANCE_ID WEB_INSTANCE_ID; do
  id="${!id_var:-}"
  [[ -z "$id" ]] && continue
  log "Terminating EC2 $id..."
  aws ec2 terminate-instances --instance-ids "$id" >/dev/null 2>&1 && success "$id terminated" || warn "$id not found"
done

# RDS — remove deletion protection first
if [[ -n "${RDS_IDENTIFIER:-}" ]]; then
  log "Removing RDS deletion protection..."
  aws rds modify-db-instance --db-instance-identifier "$RDS_IDENTIFIER" \
    --no-deletion-protection --apply-immediately >/dev/null 2>&1 || true
  sleep 5
  log "Deleting RDS $RDS_IDENTIFIER..."
  aws rds delete-db-instance --db-instance-identifier "$RDS_IDENTIFIER" \
    --skip-final-snapshot >/dev/null 2>&1 && success "RDS delete initiated" || warn "RDS not found"
fi

# S3 bucket — empty first
if [[ -n "${S3_BUCKET:-}" ]]; then
  log "Emptying S3 bucket $S3_BUCKET..."
  aws s3 rm "s3://$S3_BUCKET" --recursive >/dev/null 2>&1 || true
  log "Deleting bucket..."
  aws s3api delete-bucket --bucket "$S3_BUCKET" >/dev/null 2>&1 && success "Bucket deleted" || warn "Bucket not found"
fi

# Security groups (after instances terminate)
log "Waiting 15s for instances to terminate before deleting SGs..."
sleep 15
for sg_var in SG_OCR SG_WEB SG_RDS; do
  sg="${!sg_var:-}"
  [[ -z "$sg" ]] && continue
  aws ec2 delete-security-group --group-id "$sg" 2>/dev/null && success "SG $sg deleted" || warn "SG $sg busy/not found"
done

# RDS subnet group
aws rds delete-db-subnet-group \
  --db-subnet-group-name "${PROJECT:-idp-panasonic}-rds-subnet-grp" 2>/dev/null && success "RDS subnet group deleted" || true

# Key pair from AWS (local .pem preserved)
if [[ -n "${KEY_NAME:-}" ]]; then
  aws ec2 delete-key-pair --key-name "$KEY_NAME" 2>/dev/null && success "Key pair $KEY_NAME deleted from AWS" || true
fi

success "Teardown complete. Local .pem file retained."
