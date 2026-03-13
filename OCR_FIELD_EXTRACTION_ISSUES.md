# OCR Field Extraction - Issue Summary

## Problem
Uploaded invoice PDF showed `invoice_number: APPLIANCES` instead of `INV-2025-PV-04872`, along with garbled supplier/buyer names and vessel fields.

## Root Causes

### 1. DOCX Header Not Extracted
`Invoice No.: INV-2025-PV-04872` was stored in `word/header1.xml` (document header), not the body. `python-docx` only reads body paragraphs/tables by default.

**Fix:** Added extraction from `doc.sections[*].header` and `doc.sections[*].footer`.

### 2. PDF OCR Multi-Column Layout Merging
Tesseract reads multi-column PDFs left-to-right, merging columns into single lines:
```
PANASONIC Invoice No.:
APPLIANCES VIETNAM CO., LTD. INV-2025-PV-04872
```
The regex `Invoice No.:\n?([A-Z0-9]...)` matched `APPLIANCES` (first word after the label) instead of `INV-2025-PV-04872`.

**Fix:** Prioritized `INV-` pattern search over label-based extraction. `INV-` is unique and unambiguous.

### 3. Supplier/Buyer Names Merged Across Columns
OCR merged 3 table columns (SELLER / BUYER / NOTIFY PARTY) into one line:
```
Shenzhen Huarong Electronic Panasonic Appliances Vietnam Co., Panasonic Appliances Vietnam Co.,
```

**Fix:** Used candidate iteration with `Panasonic` exclusion filter. Found clean supplier name from the declaration/signature section at document bottom (line 67: `Shenzhen Huarong Electronic Components Co.,`). Buyer extracted via direct `Panasonic Appliances Vietnam Co., Ltd.` pattern.

### 4. Vessel Field Grabbed Extra Text
OCR merged `Vessel / Voyage: OOCL Zhoushan V.025E` with `Country of Origin: P.R. China` from adjacent column.

**Fix:** Added stop-word truncation: `(.+?)(?:\s+Country|\n)`.

### 5. DOCX Table Cell Extraction Merged Columns
Original code joined table cells with `|` on one line (`' | '.join(cells)`), merging SELLER/BUYER/NOTIFY cells.

**Fix:** Changed to cell-by-cell extraction — each cell on its own line.

### 6. Old Extracted Fields Not Deleted on Re-upload
Web app `INSERT INTO extracted_fields` on re-upload added new fields alongside old ones (due to `ON CONFLICT DO UPDATE` only updating the `documents` row). Approve route picked up the first (old, wrong) value.

**Fix:** Added `DELETE FROM extracted_fields WHERE document_id=%s` before inserting new fields in `app_deploy.py`.

### 7. Payment Terms Grabbed Adjacent Columns
OCR: `Payment: T/T 30 days after Incoterms 2020: FOB Currency: USD`

**Fix:** Truncation pattern: `(.+?)(?:\s+Incoterms|\s+FOB|\s+Currency|\n)`.

## Files Modified
- `ocr_app_deploy.py` — OCR API with all pattern fixes, docx header extraction, cell-by-cell table extraction
- `app_deploy.py` — Added `DELETE FROM extracted_fields` before re-insert on upload
- `templates/sap_simulation.html` — Updated with new field display sections

## Deployment
- OCR API: `13.215.178.213` → `/opt/idp-ocr-api/app.py` → `sudo systemctl restart idp-ocr`
- Web App: `18.142.225.22` → `/opt/idp-web/app.py` → `sudo systemctl restart idp-web`
- After code deploy, must clean DB and re-upload documents for new extraction to take effect
