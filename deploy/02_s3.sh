#!/usr/bin/env bash
# 02_s3.sh — S3 bucket for IDP document storage
set -euo pipefail
STATE_FILE="$1"; source "$STATE_FILE"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; NC='\033[0m'
log()     { echo -e "${CYAN}  [s3]${NC} $*"; }
success() { echo -e "${GREEN}  [OK]${NC} $*"; }

# Bucket name must be globally unique — use account ID suffix
S3_BUCKET="idp-panasonic-docs-${ACCOUNT_ID}"

# ── Create bucket ─────────────────────────────────────────────────────────
log "Creating S3 bucket: $S3_BUCKET"
if aws s3api head-bucket --bucket "$S3_BUCKET" 2>/dev/null; then
  success "Bucket already exists: $S3_BUCKET"
else
  # ap-southeast-1 requires LocationConstraint (us-east-1 is the only region that omits it)
  aws s3api create-bucket \
    --bucket "$S3_BUCKET" \
    --region "$AWS_REGION" \
    --create-bucket-configuration LocationConstraint="$AWS_REGION"
  success "Bucket created: $S3_BUCKET"
fi

# ── Block all public access ────────────────────────────────────────────────
log "Blocking public access..."
aws s3api put-public-access-block \
  --bucket "$S3_BUCKET" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# ── Enable versioning (documents must be recoverable) ─────────────────────
log "Enabling versioning..."
aws s3api put-bucket-versioning \
  --bucket "$S3_BUCKET" \
  --versioning-configuration Status=Enabled

# ── Server-side encryption (AES-256) ──────────────────────────────────────
log "Enabling AES-256 encryption..."
aws s3api put-bucket-encryption \
  --bucket "$S3_BUCKET" \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      },
      "BucketKeyEnabled": true
    }]
  }'

# ── Lifecycle rule: move to Glacier after 1 year, delete after 7 years ────
log "Setting lifecycle policy (archive after 1yr, delete after 7yr)..."
aws s3api put-bucket-lifecycle-configuration \
  --bucket "$S3_BUCKET" \
  --lifecycle-configuration '{
    "Rules": [{
      "ID": "idp-archive-policy",
      "Status": "Enabled",
      "Filter": { "Prefix": "" },
      "Transitions": [{
        "Days": 365,
        "StorageClass": "GLACIER"
      }],
      "Expiration": { "Days": 2555 }
    }]
  }'

# ── Create logical folder structure (empty objects as prefixes) ───────────
log "Creating folder structure..."
for prefix in "uploads/invoices/" "uploads/packing-lists/" "uploads/bills-of-lading/" \
              "uploads/warehouse-receipts/" "processed/" "rejected/" "audit-logs/"; do
  aws s3api put-object --bucket "$S3_BUCKET" --key "$prefix" --content-length 0 >/dev/null
done

# ── Upload sample documents if they exist ─────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ls "$SCRIPT_DIR"/doc_input/*.docx >/dev/null 2>&1; then
  for f in "$SCRIPT_DIR"/doc_input/*.docx; do
    [[ -f "$f" ]] && aws s3 cp "$f" "s3://$S3_BUCKET/uploads/" --sse AES256 && log "Uploaded sample: $(basename "$f")"
  done
fi

success "S3 bucket fully configured: s3://$S3_BUCKET"

# ── Persist ───────────────────────────────────────────────────────────────
cat >> "$STATE_FILE" <<EOF
S3_BUCKET=$S3_BUCKET
EOF
