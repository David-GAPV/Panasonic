# Panasonic Vietnam IDP System

Intelligent Document Processing system for Panasonic Appliances Vietnam Co., Ltd.

## Live System

**URL**: https://idp.pngha.io.vn  
**Status**: ✅ Operational (HTTPS, SSL via Let's Encrypt)

## Overview

Automates the processing of import/export trade documents — Commercial Invoices, Packing Lists, Bills of Lading, Certificates of Origin, and Warehouse Receipts. Documents are uploaded, OCR-extracted using AWS Bedrock Claude vision, validated against business rules and master data, cross-verified within shipment groups, and routed through a human review workflow before SAP handoff.

## Features

- OCR extraction via AWS Bedrock Claude Sonnet vision (95%+ accuracy on scanned PDFs)
- Direct DOCX text parsing with regex field extraction
- Score-based document classification (invoice, packing list, B/L, CO, warehouse receipt)
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
│  │  │  ┌─────────────────────────────────────────────────────┐    │  │   │
│  │  │  │         Unified Web + OCR EC2 (t3.micro)            │    │  │   │
│  │  │  │         IP: 13.214.12.26                            │    │  │   │
│  │  │  │                                                     │    │  │   │
│  │  │  │  Nginx (443/80) → Flask (:5000)                    │    │  │   │
│  │  │  │  - Web UI (dashboard, upload, review)              │    │  │   │
│  │  │  │  - OCR via Bedrock Claude vision                   │    │  │   │
│  │  │  │  - DOCX parsing (python-docx)                      │    │  │   │
│  │  │  │  - PDF to image (pdf2image, poppler)               │    │  │   │
│  │  │  └─────────────────────────────────────────────────────┘    │  │   │
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
│  │ S3 Bucket        │  │ SNS Topic        │  │ AWS Bedrock            │   │
│  │ Document storage │  │ Email alerts on  │  │ Claude Sonnet 4        │   │
│  │ /uploads/ prefix │  │ flag/approve/    │  │ Vision OCR extraction  │   │
│  │ Versioning on    │  │ reject/mismatch  │  │ (us-east-1)            │   │
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
4. **Reference data validation** — Supplier name, HS codes, ports checked against master tables
5. **Cross-document verification** — 11 overlapping fields compared within the same shipment_ref group

All validations re-run automatically when a field is edited.

## Data Flow

```
User uploads file(s) with Shipment Reference
        │
        ▼
Web App → Save to S3 → OCR Extraction (local)
        │                    │
        │         ┌──────────┘
        │         ▼
        │    OCR Processing:
        │      DOCX → python-docx text extraction
        │      PDF/Image → Bedrock Claude vision
        │      classify_document() → type + confidence
        │      extract_fields() → 30+ fields per doc type
        │         │
        │    ◄────┘ Extraction result
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
├── app_deploy.py                 # Unified Flask app (web + OCR)
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
│   ├── 00_iam_role.sh → 05_website_ec2.sh
│   └── teardown.sh
├── doc_input/                    # Sample documents (4 test sets)
├── requirement/                  # BRD and architecture documents
├── images/                       # Panasonic branding assets
├── nginx-idp.conf                # Nginx config (SSL + proxy timeouts)
└── deploy-update.sh              # Quick deploy script
```

## Deployment

### Quick Deploy (code updates)

```bash
./deploy-update.sh
```

This uploads the app, templates, and restarts the service.

### Manual Deploy

```bash
# Copy app to server
sudo cp app_deploy.py /opt/idp-web/app.py
sudo systemctl restart idp-web

# Check status
sudo systemctl status idp-web
sudo journalctl -u idp-web -f
```

## Cost Estimate

| Resource | Type | Monthly Cost |
|----------|------|--------------|
| Web EC2 | t3.micro | ~$19 |
| RDS | db.t3.micro | ~$15 |
| S3 | First 50GB | ~$1 |
| Bedrock | Claude Sonnet | ~$5-20 (usage-based) |
| SNS | Email tier | ~$0 |
| **Total** | | **~$40-55/month** |

## Security

- HTTPS/TLS via Let's Encrypt (certbot auto-renewal)
- Session-based authentication with SHA-256 password hashing
- Role-based route protection (`@role_required` decorator)
- S3 presigned URLs with 1-hour expiry for document access
- RDS not publicly accessible (EC2 access only via security groups)
- IAM instance role for S3/SNS, explicit credentials for Bedrock
- AES-256 encryption at rest (S3 + RDS)

## Tech Stack

- Python 3.9, Flask
- Nginx (reverse proxy + SSL termination)
- AWS Bedrock Claude Sonnet 4 (vision OCR)
- PostgreSQL 16.13 (RDS)
- AWS: EC2, RDS, S3, SNS, Bedrock, IAM, VPC
- Let's Encrypt / certbot
- python-docx, pdf2image, fpdf2, boto3, psycopg2

---

Built for Panasonic Appliances Vietnam Co., Ltd. — Team 07  
Region: ap-southeast-1 (Singapore)  
Last Updated: April 15, 2026
