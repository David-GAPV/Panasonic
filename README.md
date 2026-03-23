# Panasonic Vietnam IDP System

Intelligent Document Processing system for Panasonic Appliances Vietnam Co., Ltd.

## Live System

**URL**: https://idp.pngha.io.vn  
**Status**: ✅ Operational (HTTPS, SSL via Let's Encrypt)

## Overview

Automates the processing of import/export trade documents — Commercial Invoices, Packing Lists, Bills of Lading, and Warehouse Receipts. Documents are uploaded, OCR-extracted, validated against business rules and master data, cross-verified within shipment groups, and routed through a human review workflow before SAP handoff.

## Features

- OCR extraction via Tesseract 5.5.2 (English + Vietnamese) with direct DOCX parsing
- Score-based document classification (invoice, packing list, B/L, warehouse receipt)
- 20+ regex field extraction patterns with confidence scoring
- Confidence threshold auto-flagging (< 70%)
- Mandatory field validation per document type
- Reference data validation against master tables (suppliers, HS codes, ports)
- Cross-document verification within user-defined shipment groups (11 overlapping fields)
- Auto-revalidation on field edit (mandatory + reference + cross-doc checks re-run live)
- Human review workflow with side-by-side original document and extracted fields
- Inline field editing with audit trail (old → new value logged, no page reload)
- Approve / Reject actions with SNS email notifications
- SAP MIRO/MIGO simulation with structured JSON payloads
- Role-Based Access Control (admin, reviewer, uploader)
- Analytics dashboard with error breakdown charts
- PDF and CSV report generation
- CSV/JSON export of document data
- Search and filter across all documents
- Upload time sorting (newest/oldest first)
- Status-aware action buttons on dashboard
- Full audit trail (7-year retention compliant)
- Supported formats: PDF, PNG, JPEG, TIFF, DOCX, HEIC, WebP (max 16 MB)

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AWS Cloud — ap-southeast-1                        │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    VPC: idp-panasonic-vpc (10.0.0.0/16)            │   │
│  │                                                                     │   │
│  │  ┌──────────────────────────────────────────────────────────────┐  │   │
│  │  │              Public Subnet (10.0.1.0/24)                     │  │   │
│  │  │                                                              │  │   │
│  │  │  ┌─────────────────────┐    ┌─────────────────────────┐     │  │   │
│  │  │  │  Web EC2 (t3.micro) │    │  OCR EC2 (t3.medium)    │     │  │   │
│  │  │  │                     │    │                         │     │  │   │
│  │  │  │  Nginx (443/80)     │    │  Flask API (:8000)      │     │  │   │
│  │  │  │   ↓ reverse proxy   │    │  Tesseract 5.5.2        │     │  │   │
│  │  │  │  Flask (:5000)      │───→│  python-docx            │     │  │   │
│  │  │  │  (idp-web service)  │    │  pillow-heif, pdf2image │     │  │   │
│  │  │  └─────────────────────┘    └─────────────────────────┘     │  │   │
│  │  └──────────────────────────────────────────────────────────────┘  │   │
│  │                                                                     │   │
│  │  ┌──────────────────────────────────────────────────────────────┐  │   │
│  │  │           Private Subnet (10.0.2.0/24)                      │  │   │
│  │  │                                                              │  │   │
│  │  │  ┌──────────────────────────────────────────────────────┐   │  │   │
│  │  │  │         RDS PostgreSQL 16.13 (db.t3.micro)           │   │  │   │
│  │  │  │         Database: idpdb                              │   │  │   │
│  │  │  │         8 tables (documents, extracted_fields,       │   │  │   │
│  │  │  │         validation_results, audit_log, doc_types,    │   │  │   │
│  │  │  │         ref_suppliers, ref_hs_codes, ref_ports)      │   │  │   │
│  │  │  └──────────────────────────────────────────────────────┘   │  │   │
│  │  └──────────────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────────┐   │
│  │ S3 Bucket        │  │ SNS Topic        │  │ IAM Role               │   │
│  │ Document storage │  │ Email alerts on  │  │ idp-panasonic-ec2-role │   │
│  │ /uploads/ prefix │  │ flag/approve/    │  │ S3 + SNS + RDS access  │   │
│  │ Versioning on    │  │ reject/mismatch  │  │                        │   │
│  └──────────────────┘  └──────────────────┘  └────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                              ▲
                              │ HTTPS (443)
                              │ Domain: idp.pngha.io.vn
                              │ SSL: Let's Encrypt (certbot)
                              │
                    ┌─────────┴─────────┐
                    │   Users (Browser)  │
                    │                    │
                    │  Roles:            │
                    │  admin / reviewer  │
                    │  / uploader        │
                    └────────────────────┘
```

## Role-Based Access Control

| Feature | admin | reviewer | uploader |
|---|:---:|:---:|:---:|
| Dashboard (KPIs, doc list) | ✅ | ✅ | ✅ |
| Upload documents | ✅ | ✅ | ✅ |
| View document detail | ✅ | ✅ | ✅ (read-only) |
| Download original | ✅ | ✅ | ✅ |
| Review queue | ✅ | ✅ | ❌ |
| Approve / Reject | ✅ | ✅ | ❌ |
| Edit extracted fields | ✅ | ✅ | ❌ |
| Analytics | ✅ | ✅ | ❌ |
| Reports (PDF/CSV) | ✅ | ✅ | ❌ |
| Export (CSV/JSON) | ✅ | ✅ | ❌ |
| SAP Simulation | ✅ | ❌ | ❌ |

## Validation Pipeline

1. **Confidence threshold** — Fields below 70% confidence auto-flag the document
2. **Mandatory field check** — Per document type (e.g. invoice requires invoice_number, date, total_amount, supplier_name)
3. **Format validation** — Date formats, numeric amounts, invoice number patterns
4. **Reference data validation** — Supplier name, HS codes, ports checked against master tables (ref_suppliers, ref_hs_codes, ref_ports)
5. **Cross-document verification** — 11 overlapping fields compared within the same shipment_ref group (supplier_name, buyer_name, total_amount, container_no, vessel, ports, weights, packages, currency)

All validations re-run automatically when a field is edited.

## Data Flow

```
User uploads file(s) with Shipment Reference
        │
        ▼
Web App → Save to S3 → POST to OCR API
        │                    │
        │         ┌──────────┘
        │         ▼
        │    OCR Server:
        │      DOCX → python-docx extraction
        │      PDF/Image → Tesseract (eng+vie, 300 DPI)
        │      classify_document() → type + confidence
        │      extract_fields() → 20+ fields
        │         │
        │    ◄────┘ JSON response
        ▼
Store in PostgreSQL → Run validation pipeline
        │
        ├─ All pass → status: extracted
        └─ Any fail → status: flagged → SNS notification
                │
                ▼
        Review Queue → Reviewer edits fields
                │       (auto-revalidation on each edit)
                ▼
        Approve → SAP payload logged → SNS notification
        Reject  → Reason logged → SNS notification
```

## Project Structure

```
.
├── app_deploy.py                 # Main Flask web application
├── ocr_app_deploy.py             # OCR/extraction Flask API
├── templates/                    # HTML templates
│   ├── login.html
│   ├── dashboard.html
│   ├── upload.html
│   ├── review_queue.html
│   ├── document_detail.html
│   ├── sap_simulation.html
│   ├── analytics.html
│   └── report.html
├── deploy/                       # AWS deployment scripts
│   ├── deploy_all.sh
│   ├── 00_iam_role.sh
│   ├── 01_network.sh
│   ├── 02_s3.sh
│   ├── 03_ocr_ec2.sh
│   ├── 04_rds.sh
│   ├── 05_website_ec2.sh
│   ├── web-app-bootstrap.sh
│   └── teardown.sh
├── doc_input/                    # Sample DOCX documents (3 test sets)
├── requirement/                  # BRD and architecture documents
├── images/                       # Panasonic branding assets
├── nginx-idp.conf                # Nginx reverse proxy config
├── create_test_docs.py           # Test document generator
├── create_architecture_doc.py    # Architecture DOCX generator
└── generate_checklist_excel.py   # Requirements checklist generator
```

## Deployment

Infrastructure is deployed via bash scripts in `deploy/` using AWS CLI with profile `pnsn`.

```bash
# Deploy everything
cd deploy && ./deploy_all.sh

# Teardown all resources
cd deploy && ./deploy_all.sh --teardown
```

### Deploy pattern (updates)

Web application:
```bash
scp -i deploy/idp-panasonic-key.pem app_deploy.py ec2-user@<WEB_IP>:/tmp/app.py
ssh -i deploy/idp-panasonic-key.pem ec2-user@<WEB_IP> \
  'sudo cp /tmp/app.py /opt/idp-web/app.py && sudo systemctl restart idp-web'
```

Templates:
```bash
scp -i deploy/idp-panasonic-key.pem templates/*.html ec2-user@<WEB_IP>:/tmp/
ssh -i deploy/idp-panasonic-key.pem ec2-user@<WEB_IP> \
  'sudo cp /tmp/*.html /opt/idp-web/templates/ && sudo systemctl restart idp-web'
```

OCR service:
```bash
scp -i deploy/idp-panasonic-key.pem ocr_app_deploy.py ec2-user@<OCR_IP>:/tmp/app.py
ssh -i deploy/idp-panasonic-key.pem ec2-user@<OCR_IP> \
  'sudo cp /tmp/app.py /opt/idp-ocr-api/app.py && sudo systemctl restart idp-ocr'
```

## Cost Estimate

| Resource | Type | Monthly Cost |
|----------|------|--------------|
| OCR EC2 | t3.medium | ~$38 |
| Web EC2 | t3.micro | ~$19 |
| RDS | db.t3.micro | ~$15 |
| S3 | First 50GB | ~$1 |
| SNS | Email tier | ~$0 |
| **Total** | | **~$73/month** |

## Security

- HTTPS/TLS via Let's Encrypt (certbot auto-renewal)
- Session-based authentication with SHA-256 password hashing
- Role-based route protection (`@role_required` decorator)
- S3 presigned URLs with 1-hour expiry for document access
- RDS not publicly accessible (EC2 access only via security groups)
- S3 public access blocked
- IAM instance roles (no hardcoded AWS credentials)
- AES-256 encryption at rest (S3 + RDS)

## Sample Data

The system includes 12 sample DOCX documents across 3 shipment sets:

- **Original set** (4 docs) — Shenzhen-to-Hanoi shipment with intentional warehouse receipt discrepancy
- **Test Set A** (4 docs) — Consistent data across all documents (should pass cross-verification)
- **Test Set B** (4 docs) — Deliberate mismatches for testing cross-document verification

## Documentation

- `ARCHITECTURE.md` — Detailed system architecture with diagrams
- `REQUIREMENTS_CHECKLIST.md` — BRD requirements coverage
- `REQUIREMENTS_CHECKLIST.xlsx` — Requirements tracking spreadsheet
- `requirement/` — Original BRD and architecture description documents
- `deploy/README.md` — Deployment scripts documentation

## Tech Stack

- Python 3.9, Flask, Gunicorn
- Nginx (reverse proxy + SSL termination)
- Tesseract 5.5.2 (compiled from source, LSTM engine)
- PostgreSQL 16.13 (RDS)
- AWS: EC2, RDS, S3, SNS, IAM, VPC
- Let's Encrypt / certbot
- python-docx, pillow-heif, pdf2image, fpdf2, boto3, psycopg2

---

Built for Panasonic Appliances Vietnam Co., Ltd. — Team 07  
Region: ap-southeast-1 (Singapore)  
Last Updated: March 23, 2026
