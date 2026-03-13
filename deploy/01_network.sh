#!/usr/bin/env bash
# 01_network.sh — VPC, Security Groups, Key Pair
set -euo pipefail
STATE_FILE="$1"; source "$STATE_FILE"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; NC='\033[0m'
log()     { echo -e "${CYAN}  [network]${NC} $*"; }
success() { echo -e "${GREEN}  [OK]${NC} $*"; }

PROJECT="idp-panasonic"
KEY_NAME="$PROJECT-key"

# ── Key Pair ──────────────────────────────────────────────────────────────
if [[ ! -f "$KEY_NAME.pem" ]]; then
  log "Creating EC2 key pair: $KEY_NAME"
  aws ec2 create-key-pair \
    --key-name "$KEY_NAME" \
    --query 'KeyMaterial' \
    --output text > "$KEY_NAME.pem"
  chmod 400 "$KEY_NAME.pem"
  success "Key pair saved: $KEY_NAME.pem"
else
  # Key file exists — ensure it's also registered in AWS (import if needed)
  if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
    log "Importing existing public key for $KEY_NAME"
    PUBLIC_KEY=$(ssh-keygen -y -f "$KEY_NAME.pem" 2>/dev/null || true)
    if [[ -n "$PUBLIC_KEY" ]]; then
      aws ec2 import-key-pair \
        --key-name "$KEY_NAME" \
        --public-key-material "$(echo "$PUBLIC_KEY" | base64)" >/dev/null
    fi
  fi
  success "Key pair already exists: $KEY_NAME.pem"
fi

# ── VPC — use default VPC to keep things simple ───────────────────────────
log "Fetching default VPC..."
VPC_ID=$(aws ec2 describe-vpcs \
  --filters "Name=isDefault,Values=true" \
  --query "Vpcs[0].VpcId" --output text)

[[ "$VPC_ID" == "None" || -z "$VPC_ID" ]] && {
  log "No default VPC — creating one..."
  aws ec2 create-default-vpc >/dev/null
  VPC_ID=$(aws ec2 describe-vpcs \
    --filters "Name=isDefault,Values=true" \
    --query "Vpcs[0].VpcId" --output text)
}
success "VPC: $VPC_ID"

# ── Subnets ───────────────────────────────────────────────────────────────
SUBNET_IDS=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=defaultForAz,Values=true" \
  --query "Subnets[*].SubnetId" --output text | tr '\t' ',')
SUBNET_1=$(echo "$SUBNET_IDS" | cut -d',' -f1)
SUBNET_2=$(echo "$SUBNET_IDS" | cut -d',' -f2)
success "Subnets: $SUBNET_1, $SUBNET_2"

# ── Security Groups ───────────────────────────────────────────────────────
create_sg() {
  local name="$1" desc="$2"
  local existing
  existing=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$name" "Name=vpc-id,Values=$VPC_ID" \
    --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || echo "None")
  if [[ "$existing" == "None" || -z "$existing" ]]; then
    aws ec2 create-security-group \
      --group-name "$name" --description "$desc" --vpc-id "$VPC_ID" \
      --query "GroupId" --output text
  else
    echo "$existing"
  fi
}

add_ingress() {
  local sg="$1" proto="$2" port="$3" cidr="$4"
  aws ec2 authorize-security-group-ingress \
    --group-id "$sg" --protocol "$proto" --port "$port" --cidr "$cidr" \
    2>/dev/null || true   # ignore "already exists" errors
}

# OCR EC2 security group: SSH + port 8000 (OCR API)
SG_OCR=$(create_sg "$PROJECT-ocr-sg" "IDP OCR EC2 - SSH and OCR API")
add_ingress "$SG_OCR" tcp 22   "0.0.0.0/0"
add_ingress "$SG_OCR" tcp 8000 "0.0.0.0/0"
success "OCR security group: $SG_OCR"

# Website EC2 security group: SSH + HTTP 80
SG_WEB=$(create_sg "$PROJECT-web-sg" "IDP Website EC2 - SSH and HTTP")
add_ingress "$SG_WEB" tcp 22 "0.0.0.0/0"
add_ingress "$SG_WEB" tcp 80 "0.0.0.0/0"
success "Website security group: $SG_WEB"

# RDS security group: PostgreSQL 5432 from EC2s only
SG_RDS=$(create_sg "$PROJECT-rds-sg" "IDP RDS PostgreSQL - from EC2s")
add_ingress "$SG_RDS" tcp 5432 "0.0.0.0/0"   # tighten to SG_OCR/SG_WEB CIDR in prod
success "RDS security group: $SG_RDS"

# ── RDS Subnet Group (needs 2 AZs) ────────────────────────────────────────
log "Creating RDS subnet group..."
aws rds create-db-subnet-group \
  --db-subnet-group-name "$PROJECT-rds-subnet-grp" \
  --db-subnet-group-description "IDP RDS subnet group" \
  --subnet-ids "$SUBNET_1" "$SUBNET_2" \
  >/dev/null 2>&1 || true   # ignore if already exists
success "RDS subnet group ready"

# ── Persist to state ──────────────────────────────────────────────────────
cat >> "$STATE_FILE" <<EOF
KEY_NAME=$KEY_NAME
VPC_ID=$VPC_ID
SUBNET_1=$SUBNET_1
SUBNET_2=$SUBNET_2
SG_OCR=$SG_OCR
SG_WEB=$SG_WEB
SG_RDS=$SG_RDS
PROJECT=$PROJECT
EOF

success "Network setup complete"
