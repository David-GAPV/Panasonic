#!/usr/bin/env bash
# 05_website_ec2.sh — EC2 hosting the IDP sample web dashboard
set -euo pipefail
STATE_FILE="$1"; source "$STATE_FILE"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()     { echo -e "${CYAN}  [web-ec2]${NC} $*"; }
success() { echo -e "${GREEN}  [OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}  [WARN]${NC} $*"; }

# ── Upload bootstrap script to S3 ─────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log "Uploading web bootstrap script to S3..."
aws s3 cp "$SCRIPT_DIR/web-app-bootstrap.sh" "s3://$S3_BUCKET/config/web-app-bootstrap.sh" --sse AES256
success "Bootstrap script uploaded"

# ── Fetch AMI if not in state ─────────────────────────────────────────────
if [[ -z "${AMI_ID:-}" ]]; then
  log "Fetching latest Amazon Linux 2023 AMI..."
  AMI_ID=$(aws ec2 describe-images \
    --owners amazon \
    --filters \
      "Name=name,Values=al2023-ami-2023.*-x86_64" \
      "Name=state,Values=available" \
    --query "sort_by(Images, &CreationDate)[-1].ImageId" \
    --output text)
  success "AMI: $AMI_ID"
fi

# ── Compact user-data: downloads and runs bootstrap script ────────────────
USER_DATA=$(cat <<USERDATA
#!/bin/bash
exec > /var/log/idp-website-bootstrap.log 2>&1
dnf update -y
dnf install -y awscli
aws s3 cp s3://$S3_BUCKET/config/web-app-bootstrap.sh /tmp/bootstrap.sh --region $AWS_REGION
chmod +x /tmp/bootstrap.sh
/tmp/bootstrap.sh "$RDS_ENDPOINT" "$DB_NAME" "$DB_USER" "$DB_PASS" "$OCR_PUBLIC_IP" "$S3_BUCKET" "$AWS_REGION"
USERDATA
)

# ── Launch Website EC2 (t3.small is plenty for Flask) ─────────────────────
log "Launching Website EC2 instance (t3.small)..."
WEB_INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type t3.small \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_WEB" \
  --subnet-id "$SUBNET_1" \
  --iam-instance-profile "Name=$IAM_INSTANCE_PROFILE" \
  --associate-public-ip-address \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":10,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --user-data "$USER_DATA" \
  --tag-specifications \
    "ResourceType=instance,Tags=[{Key=Name,Value=$PROJECT-web},{Key=Project,Value=$PROJECT}]" \
  --query "Instances[0].InstanceId" \
  --output text)
success "Launched: $WEB_INSTANCE_ID"

log "Waiting for public IP..."
for i in $(seq 1 30); do
  WEB_PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids "$WEB_INSTANCE_ID" \
    --query "Reservations[0].Instances[0].PublicIpAddress" \
    --output text 2>/dev/null || echo "")
  [[ -n "$WEB_PUBLIC_IP" && "$WEB_PUBLIC_IP" != "None" ]] && break
  sleep 5
done

success "Website EC2 ready — http://$WEB_PUBLIC_IP  (ready in ~2 min)"
warn "SSH: ssh -i $KEY_NAME.pem ec2-user@$WEB_PUBLIC_IP"

cat >> "$STATE_FILE" <<EOF
WEB_INSTANCE_ID=$WEB_INSTANCE_ID
WEB_PUBLIC_IP=$WEB_PUBLIC_IP
EOF
