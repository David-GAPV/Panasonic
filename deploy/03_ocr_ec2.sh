#!/usr/bin/env bash
# 03_ocr_ec2.sh — EC2 with Tesseract OCR + Python Flask API
set -euo pipefail
STATE_FILE="$1"; source "$STATE_FILE"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()     { echo -e "${CYAN}  [ocr-ec2]${NC} $*"; }
success() { echo -e "${GREEN}  [OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}  [WARN]${NC} $*"; }

# ── AMI: Amazon Linux 2023 (latest, ap-southeast-1) ──────────────────────
log "Fetching latest Amazon Linux 2023 AMI..."
AMI_ID=$(aws ec2 describe-images \
  --owners amazon \
  --filters \
    "Name=name,Values=al2023-ami-2023*-x86_64" \
    "Name=state,Values=available" \
  --query "sort_by(Images, &CreationDate)[-1].ImageId" \
  --output text)
success "AMI: $AMI_ID"

# ── User-data: installs Tesseract from source + eng+vie lang packs ────────
# Tesseract source: github.com/tesseract-ocr/tesseract
# Vietnamese traineddata from github.com/tesseract-ocr/tessdata_best
read -r -d '' USER_DATA <<'USERDATA' || true
#!/bin/bash
set -e
exec > /var/log/idp-bootstrap.log 2>&1

echo "=== IDP OCR Bootstrap Start: $(date) ==="

# ── System deps ───────────────────────────────────────────────────────────
dnf update -y
dnf install -y \
  git gcc gcc-c++ make autoconf automake libtool \
  libpng-devel libjpeg-devel libtiff-devel zlib-devel \
  leptonica-devel python3 python3-pip \
  poppler-utils ImageMagick awscli

# ── Build Tesseract from source ───────────────────────────────────────────
echo "--- Building Tesseract from GitHub source ---"
cd /opt
git clone --depth 1 https://github.com/tesseract-ocr/tesseract.git
cd tesseract
./autogen.sh
./configure --prefix=/usr/local
make -j"$(nproc)"
make install
ldconfig
tesseract --version

# ── Language data (English + Vietnamese) ─────────────────────────────────
echo "--- Downloading tessdata ---"
TESSDATA_DIR=/usr/local/share/tessdata
mkdir -p "$TESSDATA_DIR"
BASE_URL="https://github.com/tesseract-ocr/tessdata_best/raw/main"
curl -fsSL "$BASE_URL/eng.traineddata" -o "$TESSDATA_DIR/eng.traineddata"
curl -fsSL "$BASE_URL/vie.traineddata" -o "$TESSDATA_DIR/vie.traineddata"
echo "--- Tesseract + language packs ready ---"

# ── Python OCR API (Flask) ─────────────────────────────────────────────────
pip3 install flask flask-cors pytesseract Pillow pdf2image boto3 psycopg2-binary

mkdir -p /opt/idp-ocr-api

cat > /opt/idp-ocr-api/app.py <<'PYEOF'
"""
IDP OCR API — wraps Tesseract for Panasonic Vietnam IDP system
Endpoints:
  POST /extract        — extract text + fields from uploaded image/pdf
  GET  /health         — liveness check
  GET  /languages      — list supported OCR languages
"""
import os, io, json, logging, re
from flask import Flask, request, jsonify
from flask_cors import CORS
import pytesseract
from PIL import Image
from pdf2image import convert_from_bytes

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

pytesseract.pytesseract.tesseract_cmd = '/usr/local/bin/tesseract'
TESSDATA_DIR = '/usr/local/share/tessdata'
SUPPORTED_LANGS = [f.replace('.traineddata','') for f in os.listdir(TESSDATA_DIR) if f.endswith('.traineddata')]

# ── Field extractors using regex heuristics ──────────────────────────────
FIELD_PATTERNS = {
    "invoice_number": r'(?:invoice\s*n[o.]?|inv\.?[:\s])\s*([A-Z0-9\-/]{6,25})',
    "date":           r'(?:date[:\s]+)(\d{1,2}[\s/\-\.]\w+[\s/\-\.]\d{2,4})',
    "total_amount":   r'(?:total(?:\s+cif)?(?:\s+value)?[:\s]+)(?:USD\s*)?([\d,]+\.?\d{0,2})',
    "supplier_name":  r'(?:seller|exporter|from)[:\s]+([A-Za-z][A-Za-z0-9 ,\.&]{4,60})',
    "hs_code":        r'\b(\d{4}\.\d{2}\.\d{2})\b',
    "po_number":      r'(?:p\.?o\.?\s*n[o.]?|purchase order)[:\s]+([A-Z0-9\-/]{6,25})',
    "bl_number":      r'(?:b/?l\s*n[o.]?|bill of lading)[:\s]+([A-Z0-9\-/]{8,25})',
}

def extract_fields(text):
    fields = {}
    text_lower = text.lower()
    for field, pattern in FIELD_PATTERNS.items():
        m = re.search(pattern, text_lower, re.IGNORECASE | re.MULTILINE)
        if m:
            value = m.group(1).strip()
            # confidence heuristic: longer matches + numeric formats score higher
            conf = min(95, 60 + len(value) * 2)
            fields[field] = {"value": value, "confidence": conf}
    # HS codes — can be multiple
    hs_all = list(set(re.findall(r'\b\d{4}\.\d{2}\.\d{2}\b', text)))
    if hs_all:
        fields["hs_codes"] = {"value": hs_all, "confidence": 90}
    return fields

def classify_document(text):
    text_l = text.lower()
    if any(kw in text_l for kw in ['commercial invoice', 'invoice no', 'unit price']):
        return 'invoice', 92
    if any(kw in text_l for kw in ['packing list', 'carton no', 'gross weight', 'net weight']):
        return 'packing_list', 89
    if any(kw in text_l for kw in ['bill of lading', 'b/l no', 'shipper', 'consignee', 'vessel']):
        return 'bill_of_lading', 91
    if any(kw in text_l for kw in ['warehouse receipt', 'goods received', 'nhap kho', 'received note']):
        return 'warehouse_receipt', 88
    return 'unknown', 40

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "tesseract_version": pytesseract.get_tesseract_version(),
        "supported_languages": SUPPORTED_LANGS
    })

@app.route('/languages', methods=['GET'])
def languages():
    return jsonify({"languages": SUPPORTED_LANGS})

@app.route('/extract', methods=['POST'])
def extract():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided. POST multipart/form-data with key 'file'"}), 400

    file   = request.files['file']
    lang   = request.form.get('lang', 'eng+vie')   # default: bilingual
    doc_id = request.form.get('doc_id', 'unknown')

    log.info(f"[{doc_id}] Received: {file.filename}, lang={lang}")

    raw_bytes = file.read()
    filename  = file.filename.lower()
    images    = []

    try:
        if filename.endswith('.pdf'):
            images = convert_from_bytes(raw_bytes, dpi=300)
        else:
            images = [Image.open(io.BytesIO(raw_bytes))]
    except Exception as e:
        return jsonify({"error": f"Could not decode file: {str(e)}"}), 422

    # OCR each page
    pages = []
    full_text = ""
    for i, img in enumerate(images):
        ocr_config = f'--oem 3 --psm 6 -l {lang}'
        page_text  = pytesseract.image_to_string(img, config=ocr_config)
        data       = pytesseract.image_to_data(img, config=ocr_config, output_type=pytesseract.Output.DICT)
        # character-level confidence (ignore -1 sentinels)
        confs = [int(c) for c in data['conf'] if int(c) > 0]
        avg_conf = round(sum(confs)/len(confs), 1) if confs else 0

        full_text += page_text + "\n"
        pages.append({"page": i+1, "text": page_text, "avg_confidence": avg_conf})

    doc_type, type_conf = classify_document(full_text)
    fields = extract_fields(full_text)

    response = {
        "doc_id":         doc_id,
        "filename":       file.filename,
        "pages":          len(pages),
        "language":       lang,
        "document_type":  doc_type,
        "type_confidence": type_conf,
        "fields":         fields,
        "page_details":   pages,
        "full_text":      full_text
    }

    log.info(f"[{doc_id}] Done — type={doc_type}({type_conf}%), fields={list(fields.keys())}")
    return jsonify(response)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
PYEOF

# ── systemd service ───────────────────────────────────────────────────────
cat > /etc/systemd/system/idp-ocr.service <<'SVCEOF'
[Unit]
Description=IDP OCR API (Tesseract + Flask)
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/opt/idp-ocr-api
ExecStart=/usr/bin/python3 /opt/idp-ocr-api/app.py
Restart=on-failure
RestartSec=5
Environment=TESSDATA_PREFIX=/usr/local/share
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable idp-ocr
systemctl start idp-ocr

echo "=== IDP OCR Bootstrap Complete: $(date) ==="
USERDATA

# ── Launch OCR EC2 (t3.medium — Tesseract compilation needs RAM) ──────────
log "Launching OCR EC2 instance (t3.medium)..."
INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type t3.medium \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_OCR" \
  --subnet-id "$SUBNET_1" \
  --associate-public-ip-address \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --user-data "$USER_DATA" \
  --tag-specifications \
    "ResourceType=instance,Tags=[{Key=Name,Value=$PROJECT-ocr},{Key=Project,Value=$PROJECT}]" \
  --query "Instances[0].InstanceId" \
  --output text)
success "Launched: $INSTANCE_ID"

# ── Wait for public IP ────────────────────────────────────────────────────
log "Waiting for public IP assignment..."
for i in $(seq 1 30); do
  OCR_PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --query "Reservations[0].Instances[0].PublicIpAddress" \
    --output text 2>/dev/null || echo "")
  [[ -n "$OCR_PUBLIC_IP" && "$OCR_PUBLIC_IP" != "None" ]] && break
  sleep 5
done

success "OCR EC2 ready — IP: $OCR_PUBLIC_IP"
warn "Bootstrap (Tesseract compile) takes ~8 min. Monitor: ssh -i $KEY_NAME.pem ec2-user@$OCR_PUBLIC_IP 'tail -f /var/log/idp-bootstrap.log'"

cat >> "$STATE_FILE" <<EOF
AMI_ID=$AMI_ID
OCR_INSTANCE_ID=$INSTANCE_ID
OCR_PUBLIC_IP=$OCR_PUBLIC_IP
EOF
