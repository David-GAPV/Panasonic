# Panasonic Vietnam IDP — AWS Deployment Scripts

## Architecture Deployed

```
Internet
   │
   ├── EC2 Website  (t3.small)   ── http://<IP>         Flask IDP Dashboard
   ├── EC2 OCR      (t3.medium)  ── http://<IP>:8000    Tesseract OCR API
   ├── RDS PostgreSQL (db.t3.micro)                       IDP schema + data
   └── S3 Bucket                  ── idp-panasonic-docs-<account>
```

**Region:** ap-southeast-1 (Singapore)

---

## Prerequisites

```bash
# 1. AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install

# 2. jq
sudo apt install jq   # Ubuntu/Debian
brew install jq       # macOS

# 3. AWS credentials
aws configure
# Enter: Access Key ID, Secret Access Key, Region: ap-southeast-1, Output: json
```

---

## Deploy

```bash
chmod +x deploy_all.sh *.sh

# Deploy using the 'pnsn' profile (default)
./deploy_all.sh

# Or specify a different profile
AWS_PROFILE=myprofile ./deploy_all.sh

# Or specify both profile and region
AWS_PROFILE=pnsn AWS_REGION=us-east-1 ./deploy_all.sh
```

Full deployment takes **~10 minutes**:
- Network/SGs:  ~30 sec
- S3 bucket:    ~30 sec
- OCR EC2:      ~2 min launch + **~8 min** Tesseract compile (background)
- RDS:          ~5-7 min provisioning
- Website EC2:  ~2 min launch + ~1 min Flask startup

---

## What Gets Created

| Resource | Type | Purpose |
|---|---|---|
| `idp-panasonic-docs-<account>` | S3 Bucket | Document storage — versioned, AES-256, Glacier after 1yr |
| `idp-panasonic-ocr` | EC2 t3.medium | Tesseract built from github.com/tesseract-ocr/tesseract |
| `idp-panasonic-postgres` | RDS db.t3.micro | PostgreSQL 16, IDP schema, seed data |
| `idp-panasonic-web` | EC2 t3.small | Flask dashboard at http://\<IP\> |
| `idp-panasonic-key` | Key Pair | SSH access (saved as `idp-panasonic-key.pem`) |
| 3 Security Groups | SG | OCR (22,8000), Web (22,80), RDS (5432) |

---

## After Deployment

```bash
# Dashboard
open http://<WEB_PUBLIC_IP>

# Check OCR API health (ready after ~8 min)
curl http://<OCR_PUBLIC_IP>:8000/health

# Test OCR extraction with one of the sample documents
curl -X POST http://<OCR_PUBLIC_IP>:8000/extract \
  -F "file=@/path/to/Commercial_Invoice.docx" \
  -F "lang=eng+vie"

# SSH into OCR box and watch Tesseract build
ssh -i idp-panasonic-key.pem ec2-user@<OCR_PUBLIC_IP>
tail -f /var/log/idp-bootstrap.log

# System status JSON
curl http://<WEB_PUBLIC_IP>/api/status
```

---

## S3 Folder Structure

```
s3://idp-panasonic-docs-<account>/
  uploads/
    invoices/
    packing-lists/
    bills-of-lading/
    warehouse-receipts/
  processed/
  rejected/
  audit-logs/
  config/
    idp_schema.sql
```

---

## Database Schema (idpdb)

| Table | Purpose |
|---|---|
| `documents` | One row per uploaded document — status lifecycle |
| `extracted_fields` | OCR field results with confidence scores |
| `validation_results` | Business rule and cross-doc check results |
| `audit_log` | Immutable action log (7yr retention compliant) |
| `document_types` | Reference: invoice, packing_list, bill_of_lading, warehouse_receipt |

Seed data includes all 4 sample documents from the Panasonic-Huarong shipment,
including the IGBT shortfall discrepancy on the Warehouse Receipt.

---

## Teardown

```bash
./deploy_all.sh --teardown
```

Destroys: both EC2s, RDS (no final snapshot), S3 bucket + contents, security groups.
Local `.pem` key file is retained.

---

## Cost Estimate (ap-southeast-1)

| Resource | $/hr | $/month |
|---|---|---|
| EC2 t3.medium (OCR) | $0.052 | ~$38 |
| EC2 t3.small (Web) | $0.026 | ~$19 |
| RDS db.t3.micro | $0.021 | ~$15 |
| S3 (first 50GB) | — | ~$1 |
| **Total** | | **~$73/month** |

Terminate EC2s when not needed to save cost.
