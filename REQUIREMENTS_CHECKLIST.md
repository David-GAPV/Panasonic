# BRD Section 3 — Requirements Checklist

**Project:** Panasonic IDP (Intelligent Document Processing)
**Website:** https://idp.pngha.io.vn/
**Evaluated:** 16 March 2026

---

## Section 3.1 — Functional Requirements (FR001–FR030)

| Req ID | Category | Description | Done? | Explanation |
|--------|----------|-------------|-------|-------------|
| FR001 | Document Upload | System shall support document upload via web interface from desktop and mobile devices | ✅ | Upload page at `/upload` supports file selection and upload. Responsive CSS works on mobile browsers. Accepts PNG, JPEG, PDF, TIFF, DOCX, HEIC, WebP. Max 16 MB. |
| FR002 | Document Upload | System shall support batch upload of multiple documents simultaneously | ✅ | Upload page supports `multiple` file input. Files uploaded sequentially with per-file progress bars and overall progress. No explicit 50-file limit enforced but functionally works. |
| FR003 | Document Upload | System shall provide real-time image quality feedback during upload | ❌ | No DPI check or image quality scoring is implemented during upload. OCR confidence is shown after extraction, but no pre-upload quality feedback (e.g., "image below 300 DPI") exists. |
| FR004 | Document Classification | System shall automatically classify document types (invoice, packing list, bill of lading, warehouse receipt) | ✅ | Score-based classifier in `ocr_app_deploy.py` → `classify_document()` identifies all 4 types using keyword scoring. Confidence score returned. |
| FR005 | OCR Extraction | System shall extract text data from uploaded document images using OCR technology | ✅ | Tesseract 5.5.2 with `--oem 3 --psm 6` at 300 DPI. DOCX files parsed directly via python-docx (no OCR needed). Processing time depends on document size. |
| FR006 | OCR Extraction | System shall support multilingual OCR for Vietnamese and English text | ✅ | Default lang parameter is `eng+vie`. Tesseract trained data for both languages installed. Vietnamese diacritical marks handled. |
| FR007 | Field Recognition | System shall identify and extract key fields (invoice number, date, amount, supplier name, line items, etc.) | ✅ | `extract_fields()` extracts 20+ field types: invoice_number, date, po_number, bl_number, total_amount, supplier_name, buyer_name, tax_code, hs_codes, vessel, ports, weights, containers, etc. Line items not individually extracted. |
| FR008 | Confidence Scoring | System shall provide confidence scores (0-100%) for each extracted field | ✅ | Every field returned with `confidence` value (0–100). Formula: `min(95, 60 + len(value)*2)`. OCR page-level avg confidence also computed via `image_to_data`. |
| FR009 | Data Validation | System shall validate extracted data against business rules (format, mandatory fields, data ranges) | ⚠️ Partial | `validation_results` table exists in DB and is displayed in document detail page. However, no automated business rule validation logic is implemented in `app_deploy.py` — the table is empty unless manually populated. |
| FR010 | Cross-Verification | System shall cross-check extracted data against related documents in the system | ❌ | No cross-document verification implemented. No logic to compare invoice vs. PO, invoice vs. packing list, or detect discrepancies between related documents. |
| FR011 | Reference Data Validation | System shall validate extracted data against master data (supplier database, product catalog, HS codes) | ❌ | No master data tables exist. No supplier database, product catalog, or HS code reference validation. Extracted data is stored as-is. |
| FR012 | External API Integration | System shall integrate with government customs APIs for real-time validation | ❌ | No external customs API integration. This would require Vietnam customs portal API access which is not implemented. |
| FR013 | Exception Handling | System shall flag documents with low confidence scores (<70%) or validation failures for manual review | ⚠️ Partial | Documents go to review queue with status `extracted`/`flagged`. However, no automatic flagging logic based on confidence threshold (<70%) exists — all extracted documents appear in review queue regardless of confidence. |
| FR014 | Review Queue | System shall provide a prioritized queue interface for manual review of flagged documents | ✅ | `/review` page shows documents with status `flagged`, `reviewing`, `extracted`. Displays doc type, confidence, issue count. Sorted by creation date (not priority/urgency). No filtering UI. |
| FR015 | Review Interface | System shall display original document image side-by-side with extracted data for reviewer validation | ⚠️ Partial | Document detail page shows extracted fields in editable table with confidence scores and validation results. However, original document image is NOT displayed side-by-side — only the S3 key is stored, no image viewer/preview is rendered. |
| FR016 | Manual Correction | System shall allow authorized users to correct extraction errors and override validation flags | ✅ | Document detail page has editable input fields for each extracted value. `update_field` endpoint saves `corrected_value` and marks `reviewed=TRUE`. Changes tracked via audit log on approval. |
| FR017 | Approval Workflow | System shall support one-click approval for high-confidence extractions meeting all validation criteria | ✅ | "Approve & Send to SAP" button on document detail page. Single click triggers `/document/<id>/approve` which updates status to `completed` and logs SAP push payload to audit_log. |
| FR018 | ERP Integration | System shall automatically push validated data to SAP ERP system via API | ⚠️ Partial | SAP integration is simulated. Approved documents generate a JSON payload stored in `audit_log` and displayed in SAP ERP (MIRO) tab. No actual SAP API connection — this is a simulation/demo. |
| FR019 | WMS Integration | System shall automatically push validated warehouse data to WMS via API | ⚠️ Partial | WMS integration is simulated. Approved warehouse receipts and B/L documents appear in WMS (MIGO) tab with field mapping. No actual WMS API connection — simulation only. |
| FR020 | Transaction Management | System shall implement atomic transactions with rollback capability | ⚠️ Partial | PostgreSQL transactions used (`conn.commit()`). However, no explicit rollback on partial failure — if OCR succeeds but DB insert fails, the S3 upload is not rolled back. No distributed transaction management. |
| FR021 | Audit Trail | System shall log all document processing activities | ✅ | `audit_log` table records actions with `document_id`, `action`, `new_value`, `logged_at`, `user_id`. Approval and rejection actions logged. Displayed in document detail page with timestamp, user, action, detail columns. |
| FR022 | Error Analytics | System shall track and categorize error types for continuous improvement analysis | ❌ | No error analytics dashboard. No error type classification or trend analysis. Errors are logged to server console but not aggregated or categorized in the UI. |
| FR023 | User Dashboard | System shall provide role-based dashboards showing relevant metrics and tasks | ⚠️ Partial | Dashboard at `/` shows document status counts (stat cards) and recent documents table. Not role-based — all users see the same view. No personalized tasks or KPIs. |
| FR024 | Search Functionality | System shall allow users to search processed documents by multiple criteria | ❌ | No search functionality. Dashboard shows last 50 documents. No filtering by date range, document type, supplier, or status. |
| FR025 | Document Retrieval | System shall allow users to retrieve and view original documents and extracted data | ⚠️ Partial | Document detail page shows extracted fields and audit trail. Original document stored in S3 but no download link or document viewer in the UI. |
| FR026 | Notification System | System shall send notifications to users for documents requiring review | ❌ | No notification system. No email or in-app notifications. Users must manually check the review queue. |
| FR027 | User Management | System shall support role-based access control with different permission levels | ❌ | No authentication or user management. No login page, no user roles, no RBAC. All pages accessible without authentication. `uploaded_by` is hardcoded as `web_user`. |
| FR028 | ML Model Training | System shall capture human corrections and feed them into model retraining pipeline | ❌ | Human corrections are saved (`corrected_value` in `extracted_fields`), but no ML retraining pipeline exists. Corrections are stored but not used for model improvement. |
| FR029 | Reporting | System shall generate standard reports on processing volume, accuracy rates, error types, and cost savings | ❌ | No reporting module. No PDF/Excel report generation. Dashboard shows basic status counts only. |
| FR030 | Data Export | System shall allow authorized users to export extracted data in standard formats (CSV, Excel, JSON) | ⚠️ Partial | `/api/status` returns JSON status. SAP push log shows JSON payloads. However, no dedicated export feature for CSV/Excel download of extracted data. |

---

### Functional Requirements Summary

| Status | Count | Percentage |
|--------|-------|------------|
| ✅ Done | 11 | 37% |
| ⚠️ Partial | 10 | 33% |
| ❌ Not Done | 9 | 30% |

---

## Section 3.2 — Non-Functional Requirements (NFR001–NFR040)

| Req ID | Category | Description | Target | Done? | Explanation |
|--------|----------|-------------|--------|-------|-------------|
| NFR001 | Performance | OCR extraction within 10 seconds per document | ≤10s | ⚠️ Partial | DOCX files process in <2s (direct text extraction). PDF OCR at 300 DPI takes 5–30s depending on page count. Single-page PDFs likely within 10s; multi-page may exceed. |
| NFR002 | Performance | Support 500 concurrent users | 500+ | ❌ | Flask development server (`debug=False`) with no WSGI server (gunicorn/uwsgi). Single-threaded. Would not handle 500 concurrent users. Nginx reverse proxy helps but backend is the bottleneck. |
| NFR003 | Performance | UI response time under 2 seconds | ≤2s | ✅ | Static HTML templates with minimal JS. Dashboard queries are simple SQL. Page loads are fast for normal usage. |
| NFR004 | Performance | Handle 15,000 documents per day | 15,000/day | ❌ | Single EC2 instance for OCR, single EC2 for web. No queue system. Sequential processing. Would not sustain 15K docs/day throughput. |
| NFR005 | Scalability | Horizontal scaling to accommodate 3x volume growth | 3x capacity | ❌ | Single EC2 instances, no auto-scaling groups, no load balancer. Architecture is single-server, not horizontally scalable. |
| NFR006 | Scalability | Auto-scale compute resources based on queue depth | Auto-scaling | ❌ | No auto-scaling groups configured. No SQS queue. Fixed EC2 instances. |
| NFR007 | Availability | 99.5% uptime during business hours | ≥99.5% | ⚠️ Partial | Systemd services with `Restart=on-failure` provide basic process recovery. RDS managed by AWS has built-in availability. However, single EC2 instances with no redundancy — if instance fails, service is down until manual intervention. |
| NFR008 | Availability | Automated failover for critical components | <5 min failover | ❌ | No failover configuration. Single AZ deployment. No standby instances, no Route53 health checks, no ALB. |
| NFR009 | Reliability | Data integrity with zero data loss during processing failures | 0% data loss | ⚠️ Partial | S3 upload happens before OCR processing, so raw documents are preserved. PostgreSQL provides ACID transactions. However, no explicit error recovery — if process crashes mid-extraction, document may be in inconsistent state. |
| NFR010 | Reliability | Transaction rollback for failed data insertions | 100% rollback | ⚠️ Partial | PostgreSQL transactions with `conn.commit()`. If DB insert fails, Python exception prevents commit. But no explicit `try/except/rollback` pattern in all code paths. S3 uploads not rolled back on DB failure. |
| NFR011 | Security | Encrypt all data in transit using TLS 1.3+ | TLS 1.3+ | ✅ | HTTPS via Let's Encrypt (certbot) on Nginx. SSL certificate active for `idp.pngha.io.vn`. TLS 1.2/1.3 supported by default Nginx config. |
| NFR012 | Security | Encrypt all data at rest using AES-256 | AES-256 | ⚠️ Partial | RDS PostgreSQL has AWS default encryption (AES-256) if enabled at creation. S3 bucket uses SSE-S3 by default. EC2 EBS volumes may not have encryption enabled explicitly. |
| NFR013 | Security | Multi-factor authentication for all user access | 100% MFA | ❌ | No authentication system at all. No login, no MFA. Application is open access. |
| NFR014 | Security | Role-based access control (RBAC) | RBAC enforced | ❌ | No RBAC. No user roles. No permission system. All endpoints accessible by anyone. |
| NFR015 | Security | Comprehensive audit logs for all user actions | 100% logging | ⚠️ Partial | `audit_log` table captures approval and rejection actions. Upload actions not explicitly logged to audit_log (only document creation). Field edits not logged individually. |
| NFR016 | Security | Automated security scanning and vulnerability detection | Weekly scans | ❌ | No security scanning tools configured. No vulnerability detection. No automated security testing. |
| NFR017 | Compliance | Audit trails meeting regulatory requirements for 7+ years | 7 year retention | ⚠️ Partial | Audit logs stored in PostgreSQL with no retention policy or auto-deletion. Data persists as long as RDS exists. No formal 7-year retention policy configured. |
| NFR018 | Compliance | Immutable audit logs preventing tampering | Immutable logging | ❌ | Audit logs in regular PostgreSQL table — can be updated or deleted by anyone with DB access. Not write-once/append-only. |
| NFR019 | Compliance | Support customs and tax documentation requirements for Vietnam | Full compliance | ⚠️ Partial | System extracts tax code (MST), HS codes, customs-related fields. Vietnamese text supported. However, no formal compliance review done against Vietnamese customs regulations. |
| NFR020 | Usability | Intuitive UI requiring minimal training (< 2 hours) | ≤2 hour training | ✅ | Clean, simple UI with clear navigation. Upload → Review → Approve workflow is straightforward. Minimal training needed for basic operations. |
| NFR021 | Usability | Mobile-responsive interface | Full mobile support | ⚠️ Partial | CSS uses flexible layouts and `max-width` containers. Basic responsiveness works. However, no explicit mobile breakpoints, no `@media` queries, tables may overflow on small screens. |
| NFR022 | Usability | Vietnamese and English language interfaces | 2 languages | ❌ | UI is English-only. No language switcher. No Vietnamese translation of UI labels, buttons, or messages. (OCR supports Vietnamese text extraction, but UI itself is English.) |
| NFR023 | Usability | Context-sensitive help and tooltips | Help available | ❌ | No tooltips, no help text, no user guide within the application. |
| NFR024 | Maintainability | Modular architecture allowing component updates without downtime | Zero-downtime updates | ⚠️ Partial | Separate EC2 for OCR and Web allows independent updates. However, `systemctl restart` causes brief downtime. No rolling deployment, no blue-green, no load balancer. |
| NFR025 | Maintainability | Test coverage of at least 80% for critical functions | ≥80% coverage | ❌ | No test suite. No unit tests, no integration tests. Zero test coverage. |
| NFR026 | Maintainability | Comprehensive technical documentation | Complete docs | ⚠️ Partial | `README.md`, `DEPLOYMENT_SUMMARY.md`, `QUICK_START.md`, `OCR_FIELD_EXTRACTION_ISSUES.md` exist. Architecture doc in `requirement/` folder. However, no API documentation, no code-level documentation. |
| NFR027 | Interoperability | RESTful APIs with JSON data format | REST API available | ✅ | Flask REST endpoints: `/upload` (POST), `/document/<id>/approve` (POST), `/document/<id>/reject` (POST), `/document/<id>/update_field` (POST), `/api/status` (GET), OCR `/extract` (POST), `/health` (GET). All return JSON. |
| NFR028 | Interoperability | Integrate with SAP ERP using standard SAP APIs or RFC | SAP integration | ⚠️ Partial | SAP integration is simulated — approved documents generate JSON payloads displayed in MIRO format. No actual SAP RFC/BAPI/IDoc connection. Demo/simulation only. |
| NFR029 | Interoperability | Integrate with WMS using standard API protocols | WMS integration | ⚠️ Partial | WMS integration is simulated — approved warehouse receipts/B/L displayed in MIGO format. No actual WMS API connection. Demo/simulation only. |
| NFR030 | Disaster Recovery | Automated daily backups with 24-hour RPO | RPO ≤24h | ⚠️ Partial | RDS has automated backups enabled by default (7-day retention). S3 data is durable. However, EC2 instances have no automated backup/AMI snapshots. Application code only exists on EC2 + local repo. |
| NFR031 | Disaster Recovery | Recovery time objective of 4 hours | RTO ≤4h | ⚠️ Partial | RDS can be restored from snapshot. EC2 can be recreated from deploy scripts. However, no documented DR procedure, no tested recovery plan. Manual intervention required. |
| NFR032 | Monitoring | Real-time monitoring dashboard for system health | Real-time monitoring | ⚠️ Partial | `/api/status` endpoint returns DB connectivity and document stats. No dedicated monitoring dashboard (CloudWatch, Grafana, etc.). No system health metrics (CPU, memory, disk). |
| NFR033 | Monitoring | Automated alerts for anomalies and errors | Automated alerting | ❌ | No alerting configured. No CloudWatch alarms, no SNS notifications, no PagerDuty/Slack integration. |
| NFR034 | Accuracy | OCR extraction ≥85% character-level accuracy | ≥85% | ✅ | Tesseract 5.5.2 with OEM 3 (LSTM) at 300 DPI. DOCX files get 99% accuracy (direct text extraction). PDF OCR accuracy depends on document quality but generally meets 85%+ for clean scanned documents. |
| NFR035 | Accuracy | Field extraction ≥90% field-level accuracy | ≥90% | ⚠️ Partial | Key fields (invoice number, date, PO, amounts) extract well from DOCX. PDF OCR has challenges with multi-column layouts causing field merging. Extensive regex patterns cover 20+ fields. Accuracy varies by document quality. |
| NFR036 | Accuracy | Detect ≥95% of data mismatches during validation | ≥95% detection | ❌ | No automated mismatch detection. No cross-document validation. Validation_results table exists but no validation logic populates it. |
| NFR037 | Capacity | Support 150GB annual data growth | 150GB+/year | ✅ | S3 for document storage (virtually unlimited). RDS can be scaled. Current setup supports the capacity requirement. |
| NFR038 | Capacity | Database supports 2.5M+ document records annually | 2.5M+ records/year | ⚠️ Partial | PostgreSQL on RDS can handle millions of records. However, current `db.r5.large` or similar instance not verified. No indexing optimization or partitioning for high-volume queries. |
| NFR039 | Localization | Properly handle Vietnamese diacritical marks | Full Vietnamese support | ✅ | Tesseract `vie` language pack installed. PostgreSQL UTF-8 encoding. Python 3 native Unicode. Vietnamese characters preserved in extraction and storage. |
| NFR040 | Cost Efficiency | Infrastructure costs under $0.01 per document | ≤$0.01/doc | ⚠️ Partial | Current infrastructure: 2 EC2 instances + RDS + S3. Monthly cost ~$150-200. At 10,000 docs/month = $0.015-0.02/doc. At higher volumes, cost per doc decreases. Meets target at scale but not at current low volume. |

---

### Non-Functional Requirements Summary

| Status | Count | Percentage |
|--------|-------|------------|
| ✅ Done | 7 | 17.5% |
| ⚠️ Partial | 19 | 47.5% |
| ❌ Not Done | 14 | 35% |

---

## Overall Summary

| Category | Total | ✅ Done | ⚠️ Partial | ❌ Not Done |
|----------|-------|---------|------------|-------------|
| Functional (FR001–FR030) | 30 | 11 (37%) | 10 (33%) | 9 (30%) |
| Non-Functional (NFR001–NFR040) | 40 | 7 (17.5%) | 19 (47.5%) | 14 (35%) |
| **Total** | **70** | **18 (26%)** | **29 (41%)** | **23 (33%)** |

### Key Gaps (High Priority Not Done)
- **FR027** — No authentication/RBAC
- **FR010** — No cross-document verification
- **FR003** — No image quality feedback
- **NFR002** — Cannot handle 500 concurrent users (single Flask dev server)
- **NFR005/006** — No horizontal scaling or auto-scaling
- **NFR008** — No failover
- **NFR013/014** — No authentication, no MFA, no RBAC
- **NFR018** — Audit logs not immutable
- **NFR036** — No automated mismatch detection
