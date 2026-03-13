#!/usr/bin/env bash
# 04_rds.sh — RDS PostgreSQL (db.t3.micro) with IDP schema
set -euo pipefail
STATE_FILE="$1"; source "$STATE_FILE"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()     { echo -e "${CYAN}  [rds]${NC} $*"; }
success() { echo -e "${GREEN}  [OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}  [WARN]${NC} $*"; }

DB_IDENTIFIER="${PROJECT}-postgres"
DB_NAME="idpdb"
DB_USER="idpadmin"
DB_PASS="IDPPanasonic2025!"   # change before production use
DB_CLASS="db.t3.micro"
DB_ENGINE="postgres"
DB_ENGINE_VERSION="16.13"
DB_STORAGE=20   # GB

# ── Check if already exists ───────────────────────────────────────────────
EXISTING=$(aws rds describe-db-instances \
  --db-instance-identifier "$DB_IDENTIFIER" \
  --query "DBInstances[0].DBInstanceStatus" \
  --output text 2>/dev/null || echo "notfound")

if [[ "$EXISTING" != "notfound" && "$EXISTING" != "None" ]]; then
  warn "RDS instance $DB_IDENTIFIER already exists (status: $EXISTING)"
else
  log "Creating RDS PostgreSQL instance: $DB_IDENTIFIER ($DB_CLASS)..."
  aws rds create-db-instance \
    --db-instance-identifier  "$DB_IDENTIFIER" \
    --db-instance-class       "$DB_CLASS" \
    --engine                  "$DB_ENGINE" \
    --engine-version          "$DB_ENGINE_VERSION" \
    --master-username         "$DB_USER" \
    --master-user-password    "$DB_PASS" \
    --db-name                 "$DB_NAME" \
    --allocated-storage       "$DB_STORAGE" \
    --storage-type            gp2 \
    --no-multi-az \
    --no-publicly-accessible \
    --db-subnet-group-name    "${PROJECT}-rds-subnet-grp" \
    --vpc-security-group-ids  "$SG_RDS" \
    --backup-retention-period 7 \
    --preferred-backup-window "17:00-18:00" \
    --preferred-maintenance-window "Sun:18:00-Sun:19:00" \
    --deletion-protection \
    --tags "Key=Project,Value=$PROJECT" "Key=Name,Value=$DB_IDENTIFIER" \
    >/dev/null
  success "RDS create initiated — waiting for available state (~5 min)..."
fi

# ── Poll until available ──────────────────────────────────────────────────
log "Polling RDS status..."
for i in $(seq 1 60); do
  STATUS=$(aws rds describe-db-instances \
    --db-instance-identifier "$DB_IDENTIFIER" \
    --query "DBInstances[0].DBInstanceStatus" --output text)
  echo -ne "\r  [rds] Status: $STATUS (${i}/60)   "
  [[ "$STATUS" == "available" ]] && break
  sleep 15
done
echo ""

RDS_ENDPOINT=$(aws rds describe-db-instances \
  --db-instance-identifier "$DB_IDENTIFIER" \
  --query "DBInstances[0].Endpoint.Address" --output text)
success "RDS available — endpoint: $RDS_ENDPOINT"

# ── SQL schema (applied by website EC2 which has psql available) ──────────
# We write the schema to S3 so the website EC2 can pull and apply it
log "Uploading IDP schema to S3..."
cat > /tmp/idp_schema.sql <<SQLEOF
-- IDP Panasonic Vietnam — PostgreSQL Schema
-- Applied automatically by website EC2 bootstrap

CREATE TABLE IF NOT EXISTS document_types (
  id          SERIAL PRIMARY KEY,
  code        VARCHAR(30) UNIQUE NOT NULL,
  name        VARCHAR(100) NOT NULL
);
INSERT INTO document_types (code, name) VALUES
  ('invoice',           'Commercial Invoice'),
  ('packing_list',      'Packing List'),
  ('bill_of_lading',    'Bill of Lading'),
  ('warehouse_receipt', 'Warehouse Receipt')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS documents (
  id              SERIAL PRIMARY KEY,
  doc_id          VARCHAR(50) UNIQUE NOT NULL,
  original_s3_key TEXT,
  doc_type        VARCHAR(30) REFERENCES document_types(code),
  type_confidence INTEGER,
  filename        VARCHAR(255),
  uploaded_by     VARCHAR(100),
  status          VARCHAR(30) DEFAULT 'queued'
                  CHECK (status IN ('queued','extracting','extracted',
                                    'validating','flagged','reviewing',
                                    'reviewed','auto_approved','completed','rejected')),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS extracted_fields (
  id          SERIAL PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
  field_name  VARCHAR(100) NOT NULL,
  field_value TEXT,
  confidence  INTEGER CHECK (confidence BETWEEN 0 AND 100),
  reviewed    BOOLEAN DEFAULT FALSE,
  corrected_value TEXT,
  extracted_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS validation_results (
  id          SERIAL PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
  check_type  VARCHAR(50) NOT NULL,
  check_name  VARCHAR(100) NOT NULL,
  passed      BOOLEAN NOT NULL,
  detail      TEXT,
  checked_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_log (
  id          BIGSERIAL PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  user_id     VARCHAR(100),
  action      VARCHAR(100) NOT NULL,
  old_value   TEXT,
  new_value   TEXT,
  ip_address  INET,
  logged_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_docs_status   ON documents(status);
CREATE INDEX IF NOT EXISTS idx_docs_type     ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_fields_docid  ON extracted_fields(document_id);
CREATE INDEX IF NOT EXISTS idx_audit_docid   ON audit_log(document_id);
CREATE INDEX IF NOT EXISTS idx_audit_time    ON audit_log(logged_at DESC);

-- Seed sample documents to show in the UI
INSERT INTO documents (doc_id, doc_type, type_confidence, filename, uploaded_by, status)
VALUES
  ('INV-2025-PV-04872', 'invoice',           92, 'Commercial_Invoice_INV-2025-PV-04872.docx', 'officer_hoa',   'completed'),
  ('PL-2025-PV-04872',  'packing_list',      89, 'Packing_List_PL-2025-PV-04872.docx',       'officer_hoa',   'completed'),
  ('OOLU8823041500',    'bill_of_lading',    91, 'Bill_of_Lading_OOLU8823041500.docx',        'officer_minh',  'reviewing'),
  ('WR-2025-TL-00612',  'warehouse_receipt', 88, 'Warehouse_Receipt_WR-2025-TL-00612.docx',  'officer_thanh', 'flagged')
ON CONFLICT DO NOTHING;

INSERT INTO extracted_fields (document_id, field_name, field_value, confidence) VALUES
  (1, 'invoice_number',  'INV-2025-PV-04872',           97),
  (1, 'supplier_name',   'Shenzhen Huarong Electronic',  84),
  (1, 'total_amount',    '59935.00',                     95),
  (1, 'date',            '14 February 2025',             91),
  (2, 'po_number',       'PO-2025-VN-38810',             93),
  (2, 'total_cartons',   '312',                          96),
  (3, 'bl_number',       'OOLU8823041500',               98),
  (3, 'vessel',          'OOCL Zhoushan V.025E',         88),
  (4, 'received_qty',    '156180',                       72),
  (4, 'expected_qty',    '156200',                       72)
ON CONFLICT DO NOTHING;

INSERT INTO validation_results (document_id, check_type, check_name, passed, detail) VALUES
  (1, 'business_rule',   'Mandatory fields present',  TRUE,  NULL),
  (1, 'business_rule',   'HS codes format valid',     TRUE,  NULL),
  (1, 'cross_document',  'Invoice vs PO amount match',TRUE,  NULL),
  (3, 'cross_document',  'B/L vs Invoice qty match',  FALSE, 'Unable to verify — awaiting warehouse receipt'),
  (4, 'cross_document',  'Receipt vs Packing List',   FALSE, 'Qty shortfall: 20 IGBT units. Carton 068 damaged.')
ON CONFLICT DO NOTHING;

INSERT INTO audit_log (document_id, user_id, action, new_value) VALUES
  (1, 'officer_hoa',    'upload',        'Status → queued'),
  (1, 'system',         'ocr_complete',  'Status → extracted, 10 fields'),
  (1, 'system',         'auto_approved', 'Status → completed'),
  (4, 'system',         'flag_raised',   'Discrepancy: WR qty 156180 vs PL 156200'),
  (4, 'officer_thanh',  'review_opened', 'Assigned to reviewing queue')
;
SQLEOF

aws s3 cp /tmp/idp_schema.sql "s3://$S3_BUCKET/config/idp_schema.sql" --sse AES256 >/dev/null
success "Schema uploaded to s3://$S3_BUCKET/config/idp_schema.sql"

cat >> "$STATE_FILE" <<EOF
RDS_IDENTIFIER=$DB_IDENTIFIER
RDS_ENDPOINT=$RDS_ENDPOINT
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASS=$DB_PASS
EOF
