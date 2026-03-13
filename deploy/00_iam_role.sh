#!/usr/bin/env bash
# 00_iam_role.sh — IAM role for EC2 instances to access S3
set -euo pipefail
STATE_FILE="$1"; source "$STATE_FILE"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; NC='\033[0m'
log()     { echo -e "${CYAN}  [iam]${NC} $*"; }
success() { echo -e "${GREEN}  [OK]${NC} $*"; }

ROLE_NAME="${PROJECT}-ec2-role"
INSTANCE_PROFILE_NAME="${PROJECT}-ec2-profile"

# ── Create IAM role ───────────────────────────────────────────────────────
log "Creating IAM role: $ROLE_NAME"
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  success "Role already exists: $ROLE_NAME"
else
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ec2.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
  success "Role created: $ROLE_NAME"
fi

# ── Attach S3 read policy ─────────────────────────────────────────────────
log "Attaching S3 read policy..."
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "${PROJECT}-s3-access" \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Effect\": \"Allow\",
      \"Action\": [\"s3:GetObject\", \"s3:ListBucket\"],
      \"Resource\": [
        \"arn:aws:s3:::${S3_BUCKET}\",
        \"arn:aws:s3:::${S3_BUCKET}/*\"
      ]
    }]
  }" 2>/dev/null || true
success "S3 policy attached"

# ── Create instance profile ───────────────────────────────────────────────
log "Creating instance profile: $INSTANCE_PROFILE_NAME"
if aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null 2>&1; then
  success "Instance profile already exists"
else
  aws iam create-instance-profile \
    --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null
  sleep 2
  aws iam add-role-to-instance-profile \
    --instance-profile-name "$INSTANCE_PROFILE_NAME" \
    --role-name "$ROLE_NAME" >/dev/null
  success "Instance profile created and role attached"
fi

# Wait for IAM propagation
sleep 5

cat >> "$STATE_FILE" <<EOF
IAM_ROLE_NAME=$ROLE_NAME
IAM_INSTANCE_PROFILE=$INSTANCE_PROFILE_NAME
EOF

success "IAM role setup complete"
