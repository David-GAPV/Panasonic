# Panasonic Vietnam IDP - Quick Start Guide

## 🎉 Your Dashboard is Live!

### Access the Dashboard
Open your browser and go to:
```
http://18.142.225.22
```

## What You'll See

### 1. Document Statistics
The dashboard shows real-time stats for all documents:
- **Completed**: 2 documents (fully processed)
- **Reviewing**: 1 document (awaiting human review)
- **Flagged**: 1 document (has validation issues)

### 2. Document Processing Queue
A table showing all 4 sample documents:

| Document ID | Type | Status | Issues |
|-------------|------|--------|--------|
| INV-2025-PV-04872 | Commercial Invoice | Completed | ✓ clean |
| PL-2025-PV-04872 | Packing List | Completed | ✓ clean |
| OOLU8823041500 | Bill of Lading | Reviewing | Unable to verify |
| WR-2025-TL-00612 | Warehouse Receipt | Flagged | Qty shortfall: 20 IGBT units |

### 3. System Status
At the bottom of the dashboard, you'll see:
- OCR API endpoint: `http://13.215.178.213:8000/health`
- System Status JSON: `/api/status`

## Understanding the Sample Data

The 4 documents represent a realistic shipment from Shenzhen Huarong to Panasonic Vietnam:

### Commercial Invoice (INV-2025-PV-04872)
- **Value**: USD 59,935.00
- **Items**: Capacitors, IGBT modules, PCB assemblies, etc.
- **Status**: Completed - all validations passed

### Packing List (PL-2025-PV-04872)
- **Total Cartons**: 312
- **Total Items**: 156,200 units
- **Status**: Completed - matches invoice

### Bill of Lading (OOLU8823041500)
- **Vessel**: OOCL Zhoushan V.025E
- **Container**: OOLU8823041500
- **Status**: Reviewing - awaiting warehouse receipt

### Warehouse Receipt (WR-2025-TL-00612)
- **Received**: 156,180 units
- **Expected**: 156,200 units
- **Status**: Flagged - 20 IGBT units missing (carton 068 damaged)

## Testing the System

### 1. Check System Status
```bash
curl http://18.142.225.22/api/status | jq .
```

Expected output:
```json
{
  "db": true,
  "document_stats": {
    "completed": 2,
    "flagged": 1,
    "reviewing": 1
  },
  "ocr_api": false
}
```

Note: `ocr_api` will show `false` until Tesseract finishes compiling (~8 minutes)

### 2. Check OCR API (after 8 minutes)
```bash
curl http://13.215.178.213:8000/health
```

Expected output:
```json
{
  "status": "healthy",
  "tesseract_version": "5.x.x",
  "languages": ["eng", "vie"]
}
```

### 3. Monitor OCR Compilation Progress
```bash
ssh -i deploy/idp-panasonic-key.pem ec2-user@13.215.178.213 \
  'tail -f /var/log/idp-bootstrap.log'
```

Look for: "Tesseract compilation complete"

## What Makes This System Realistic?

### 1. Intentional Discrepancy
The warehouse receipt shows 20 missing IGBT units - this is deliberate to demonstrate:
- Cross-document validation (FR-010 in the BRD)
- Exception flagging (FR-013)
- Human review workflow

### 2. Real-World Data
- Actual Panasonic Vietnam address (Thang Long Industrial Park)
- Real HS codes for electronics (8532.22.00, 8541.21.00, etc.)
- Realistic component types for air-conditioner manufacturing
- Proper ACFTA Form E reference for China-ASEAN trade

### 3. Complete Document Lifecycle
Each document shows a different stage:
- **Completed**: Auto-approved, no issues
- **Reviewing**: Waiting for cross-document verification
- **Flagged**: Has validation failures, needs human intervention

## Next Steps

### Upload Your Own Document
1. Click "Upload Document" in the navigation
2. Select a document (PNG, JPEG, PDF, DOCX)
3. Wait for OCR extraction
4. View extracted fields and confidence scores

Note: OCR upload will only work after Tesseract finishes compiling

### Explore Document Details
Click on any document ID in the table to see:
- All extracted fields with confidence scores
- Validation results (pass/fail)
- Complete audit trail

### Check Database Directly
```bash
PGPASSWORD="IDPPanasonic2025!" psql \
  -h idp-panasonic-postgres.c9220g60mxx2.ap-southeast-1.rds.amazonaws.com \
  -U idpadmin -d idpdb \
  -c "SELECT doc_id, doc_type, status FROM documents;"
```

## Architecture Overview

```
┌─────────────────┐
│   Your Browser  │
│  18.142.225.22  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────────┐
│  Flask Website  │─────▶│  PostgreSQL RDS  │
│   (t3.small)    │      │   (db.t3.micro)  │
└────────┬────────┘      └──────────────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────────┐
│  Tesseract OCR  │      │    S3 Bucket     │
│   (t3.medium)   │      │  (documents +    │
│  13.215.178.213 │      │   config files)  │
└─────────────────┘      └──────────────────┘
```

## Troubleshooting

### Dashboard not loading?
```bash
# Check if website service is running
ssh -i deploy/idp-panasonic-key.pem ec2-user@18.142.225.22 \
  'sudo systemctl status idp-web'
```

### Database connection error?
```bash
# Check RDS status
AWS_PROFILE=pnsn aws rds describe-db-instances \
  --db-instance-identifier idp-panasonic-postgres \
  --query 'DBInstances[0].DBInstanceStatus'
```

### OCR not working?
Wait 8-10 minutes after deployment, then check:
```bash
curl http://13.215.178.213:8000/health
```

## Cost Management

Current monthly cost: ~$73/month

To stop instances when not in use:
```bash
# Stop EC2 instances (saves ~$57/month)
AWS_PROFILE=pnsn aws ec2 stop-instances \
  --instance-ids i-0f1f1440abb67375b i-029d0ca192bf5cad8

# Start them again when needed
AWS_PROFILE=pnsn aws ec2 start-instances \
  --instance-ids i-0f1f1440abb67375b i-029d0ca192bf5cad8
```

## Complete Teardown

When you're done testing:
```bash
cd deploy
./deploy_all.sh --teardown
```

This removes all resources and stops all charges.

---

**Questions?** Check the full documentation in `DEPLOYMENT_SUMMARY.md`
