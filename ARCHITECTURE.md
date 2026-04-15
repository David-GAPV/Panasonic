# Panasonic IDP — System Architecture

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AWS Cloud — ap-southeast-1                        │
│                          Account: 853878127521                             │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    VPC: idp-panasonic-vpc (10.0.0.0/16)            │   │
│  │                                                                     │   │
│  │  ┌──────────────────────────────────────────────────────────────┐  │   │
│  │  │              Public Subnet (10.0.1.0/24) — AZ: 1a           │  │   │
│  │  │                                                              │  │   │
│  │  │  ┌─────────────────────────────────────────────────────┐    │  │   │
│  │  │  │      Unified Web + OCR EC2 (t3.micro)               │    │  │   │
│  │  │  │      IP: 13.214.12.26                               │    │  │   │
│  │  │  │                                                     │    │  │   │
│  │  │  │  ┌───────────────────────────────────────────────┐  │    │  │   │
│  │  │  │  │ Nginx (443/80) — SSL/TLS termination         │  │    │  │   │
│  │  │  │  │      ↓ reverse proxy                         │  │    │  │   │
│  │  │  │  │ Flask :5000 — Web App + OCR                  │  │    │  │   │
│  │  │  │  │   - Dashboard, Upload, Review UI             │  │    │  │   │
│  │  │  │  │   - Bedrock Claude vision (OCR)              │  │    │  │   │
│  │  │  │  │   - DOCX parsing (python-docx)               │  │    │  │   │
│  │  │  │  │   - PDF to image (pdf2image)                 │  │    │  │   │
│  │  │  │  │ Systemd service: idp-web                     │  │    │  │   │
│  │  │  │  └───────────────────────────────────────────────┘  │    │  │   │
│  │  │  └─────────────────────────────────────────────────────┘    │  │   │
│  │  └──────────────────────────────────────────────────────────────┘  │   │
│  │                                                                     │   │
│  │  ┌──────────────────────────────────────────────────────────────┐  │   │
│  │  │           Private Subnet (10.0.2.0/24) — AZ: 1a/1b         │  │   │
│  │  │                                                              │  │   │
│  │  │  ┌──────────────────────────────────────────────────────┐   │  │   │
│  │  │  │         RDS PostgreSQL (db.t3.micro)                 │   │  │   │
│  │  │  │         idp-panasonic-postgres                       │   │  │   │
│  │  │  │         DB: idpdb | User: idpadmin                   │   │  │   │
│  │  │  │                                                      │   │  │   │
│  │  │  │  Tables: documents, extracted_fields,                │   │  │   │
│  │  │  │          validation_results, audit_log               │   │  │   │
│  │  │  └──────────────────────────────────────────────────────┘   │  │   │
│  │  └──────────────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────────┐   │
│  │ S3 Bucket        │  │ SNS Topic        │  │ AWS Bedrock            │   │
│  │ idp-panasonic-   │  │ idp-panasonic-   │  │ (us-east-1)            │   │
│  │ docs-8538...     │  │ notifications    │  │                        │   │
│  │                  │  │                  │  │ Claude Sonnet 4        │   │
│  │ /uploads/        │  │ Email subscriber:│  │ Vision OCR extraction  │   │
│  │  (original docs) │  │ david@g-asiapac  │  │ 95%+ accuracy          │   │
│  │                  │  │ .com.vn          │  │                        │   │
│  └──────────────────┘  └──────────────────┘  └────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘

                              ▲
                              │ HTTPS (port 443)
                              │ Domain: idp.pngha.io.vn
                              │ SSL: Let's Encrypt (certbot)
                              │
                    ┌─────────┴─────────┐
                    │   Users (Browser)  │
                    │                    │
                    │  admin    ─ Full   │
                    │  reviewer ─ Review │
                    │  uploader ─ Upload │
                    │  david    ─ Full   │
                    └────────────────────┘
```

## Component Details

### 1. Unified Web + OCR Server (EC2 — t3.micro)
- IP: 13.214.12.26
- OS: Amazon Linux 2023
- Nginx reverse proxy (ports 80/443) → Flask (port 5000)
- SSL via Let's Encrypt certbot for `idp.pngha.io.vn`
- Systemd service: `idp-web`
- Python 3.9, Flask, psycopg2, boto3, fpdf2, pdf2image, python-docx

**OCR Extraction:**
- AWS Bedrock Claude Sonnet 4 vision for PDF/image extraction
- Direct DOCX parsing via python-docx (no OCR needed)
- Document-specific prompts for invoice, B/L, CO extraction
- 30+ fields extracted per document type with 95% confidence

### 2. Database (RDS PostgreSQL — db.t3.micro)
- Endpoint: idp-panasonic-postgres.c9220g60mxx2.ap-southeast-1.rds.amazonaws.com
- Database: `idpdb`
- 4 tables:
  - `documents` — doc_id, doc_type, type_confidence, status, shipment_ref, filename, uploaded_by, original_s3_key
  - `extracted_fields` — document_id, field_name, field_value, confidence, corrected_value, reviewed
  - `validation_results` — document_id, check_type (mandatory/date/amount/invoice/cross_verify), check_name, passed, detail
  - `audit_log` — document_id, action, new_value, user_id, logged_at

### 4. Object Storage (S3)
- Bucket: `idp-panasonic-docs-853878127521`
- Stores original uploaded documents under `uploads/` prefix
- Presigned URLs for document preview in browser

### 5. Notifications (SNS)
- Topic: `idp-panasonic-notifications`
- Email subscriber: david@g-asiapac.com.vn
- Triggers: document flagged, approved, rejected, cross-verify mismatch

## Data Flow

```
User uploads file(s) with Shipment Reference
        │
        ▼
┌─ Web App (Flask) ─────────────────────────────────────────────┐
│  1. Save to S3 (uploads/)                                     │
│  2. POST file to OCR API (:8000/extract)                      │
│     ┌─ OCR Server ──────────────────────────────────────┐     │
│     │  a. DOCX → python-docx text extraction            │     │
│     │  b. PDF/Image → Tesseract OCR (eng+vie, 300 DPI)  │     │
│     │  c. classify_document() → type + confidence        │     │
│     │  d. extract_fields() → 20+ fields + confidence     │     │
│     │  Return JSON: {document_type, fields, full_text}   │     │
│     └────────────────────────────────────────────────────┘     │
│  3. Store document + fields in PostgreSQL                      │
│  4. Auto-flag if any field confidence < 70%                    │
│  5. Business rule validation (mandatory fields, formats)       │
│  6. Cross-document verification (within shipment_ref group)    │
│  7. SNS notification if flagged                                │
└────────────────────────────────────────────────────────────────┘
        │
        ▼
Reviewer sees flagged docs in Review Queue
        │
        ▼
┌─ Review Workflow ─────────────────────────────────────────────┐
│  • Side-by-side: original doc (left) + extracted fields (right)│
│  • Edit/correct field values (old→new logged in audit)         │
│  • Approve → status=completed, SAP payload logged              │
│  • Reject → status=rejected, reason logged                     │
│  • SNS notification on approve/reject                          │
└────────────────────────────────────────────────────────────────┘
```

## Role-Based Access Control (RBAC)

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
| SAP Integration | ✅ | ❌ | ❌ |

## Validation Pipeline

1. **Confidence threshold** — Fields < 70% confidence → auto-flag
2. **Mandatory field check** — Per doc type (invoice needs invoice_number/date/total_amount/supplier_name, etc.)
3. **Format validation** — Date format, amount numeric, invoice number pattern
4. **Cross-document verification** — Compare overlapping fields within same shipment_ref group:
   - 11 fields compared: supplier_name, buyer_name, total_amount, container_no, vessel, port_of_loading, port_of_discharge, gross_weight, net_weight, total_packages, currency
   - Smart comparison: container subset matching, unit text normalization
   - Mismatches stored as validation_results (check_type='cross_verify')

## Security

- HTTPS/TLS via Let's Encrypt (certbot auto-renewal)
- Session-based authentication with SHA-256 password hashing
- Role-based route protection via `@role_required` decorator
- S3 presigned URLs (1-hour expiry) for document access
- VPC security groups restrict database access to web/OCR EC2 only
- IAM instance role (no hardcoded credentials)

## Supported Document Types

| Type | Classification Keywords | Mandatory Fields |
|---|---|---|
| Commercial Invoice | commercial invoice, unit price, total cif value | invoice_number, date, total_amount, supplier_name |
| Packing List | packing list, carton no, ctns | packing_list_no, total_packages |
| Bill of Lading | bill of lading, b/l no, booking ref | bl_number, vessel, port_of_loading, port_of_discharge |
| Warehouse Receipt | warehouse receipt, goods received, wh receipt no | wh_receipt_no, date |

## Supported File Formats

PDF, PNG, JPEG, TIFF, DOCX, HEIC, WebP — Max 16 MB per file
