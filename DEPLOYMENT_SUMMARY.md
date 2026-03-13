# Panasonic Vietnam IDP System - Deployment Summary

## Deployment Status: ✅ COMPLETE

Deployed on: March 13, 2026
AWS Profile: `pnsn`
AWS Region: `ap-southeast-1` (Singapore)
AWS Account: `853878127521`

**Latest Deployment:**
- Website URL: http://18.142.225.22 ✅ WORKING
- OCR API: http://13.215.178.213:8000 (compiling, ready in ~8 min)
- Database: Connected ✅
- Sample Documents: 4 documents loaded ✅

---

## Infrastructure Deployed

### 1. S3 Bucket
- **Name**: `idp-panasonic-docs-853878127521`
- **Features**: 
  - Versioning enabled
  - AES-256 encryption
  - Lifecycle policy (Glacier after 1 year, delete after 7 years)
- **Contents**: 4 sample documents uploaded
  - Commercial Invoice (INV-2025-PV-04872)
  - Packing List (PL-2025-PV-04872)
  - Bill of Lading (OOLU8823041500)
  - Warehouse Receipt (WR-2025-TL-00612)

### 2. OCR EC2 Instance
- **IP Address**: `13.215.178.213`
- **Instance Type**: t3.medium
- **Instance ID**: i-029d0ca192bf5cad8
- **Purpose**: Tesseract OCR API server
- **API Endpoint**: `http://13.215.178.213:8000`
- **Note**: Tesseract compilation takes ~8 minutes after launch
- **SSH Access**: `ssh -i idp-panasonic-key.pem ec2-user@13.215.178.213`

### 3. RDS PostgreSQL Database
- **Endpoint**: `idp-panasonic-postgres.c9220g60mxx2.ap-southeast-1.rds.amazonaws.com:5432`
- **Instance Type**: db.t3.micro
- **Engine**: PostgreSQL 16.13
- **Database Name**: `idpdb`
- **Username**: `idpadmin`
- **Password**: `IDPPanasonic2025!`
- **Storage**: 20 GB (gp2)
- **Backup**: 7-day retention

### 4. Website EC2 Instance
- **URL**: `http://18.142.225.22` ✅ WORKING
- **Instance Type**: t3.small
- **Instance ID**: i-0f1f1440abb67375b
- **Purpose**: Flask web dashboard
- **SSH Access**: `ssh -i idp-panasonic-key.pem ec2-user@18.142.225.22`
- **Status**: Ready and serving requests

### 5. Network Resources
- **VPC**: vpc-01d00b3b455847504 (default VPC)
- **Subnets**: 
  - subnet-0ce484e1d96b84325
  - subnet-0cfebba718ec1bb60
- **Security Groups**:
  - OCR SG (sg-03b5641b79f9919cc): ports 22, 8000
  - Web SG (sg-0ab880d46fb42c0ff): ports 22, 80
  - RDS SG (sg-03f1432a631d9762a): port 5432
- **Key Pair**: `idp-panasonic-key.pem` (saved locally)

---

## Access Information

### Web Dashboard
```
http://18.142.225.22
```
✅ Dashboard is live and showing 4 sample documents

### OCR API Health Check
```bash
curl http://13.215.178.213:8000/health
```
⏳ Tesseract is compiling (ready in ~8 minutes from deployment)

### System Status API
```bash
curl http://18.142.225.22/api/status
```
✅ Returns database stats and OCR status

### Database Connection
```bash
psql -h idp-panasonic-postgres.c9220g60mxx2.ap-southeast-1.rds.amazonaws.com \
     -U idpadmin -d idpdb
# Password: IDPPanasonic2025!
```

### S3 Bucket Access
```bash
aws s3 ls s3://idp-panasonic-docs-853878127521/ --profile pnsn
```

---

## Monitoring & Logs

### Check OCR Bootstrap Progress
```bash
ssh -i idp-panasonic-key.pem ec2-user@13.215.178.213 \
  'tail -f /var/log/idp-bootstrap.log'
```

### Check Website Bootstrap Progress
```bash
ssh -i idp-panasonic-key.pem ec2-user@18.142.225.22 \
  'tail -f /var/log/idp-website-bootstrap.log'
```

### Check Website Service Status
```bash
ssh -i idp-panasonic-key.pem ec2-user@18.142.225.22 \
  'sudo systemctl status idp-web'
```

---

## Cost Estimate (ap-southeast-1)

| Resource | Type | $/hour | $/month (730 hrs) |
|----------|------|--------|-------------------|
| OCR EC2 | t3.medium | $0.052 | ~$38 |
| Web EC2 | t3.small | $0.026 | ~$19 |
| RDS | db.t3.micro | $0.021 | ~$15 |
| S3 | First 50GB | - | ~$1 |
| **Total** | | | **~$73/month** |

---

## Database Schema

The PostgreSQL database includes 5 tables:

1. **document_types** - Reference table for document classifications
2. **documents** - Main document tracking with status lifecycle
3. **extracted_fields** - OCR field results with confidence scores
4. **validation_results** - Business rule and cross-document validation
5. **audit_log** - Immutable action log (7-year retention compliant)

### Sample Data Included

The database is pre-seeded with 4 sample documents representing a realistic Panasonic-Huarong shipment, including:
- An intentional quantity discrepancy (20 IGBT units short)
- Various document statuses (completed, reviewing, flagged)
- Extracted fields with confidence scores
- Validation results showing cross-document checks

---

## Teardown

To destroy all resources:

```bash
cd deploy
./deploy_all.sh --teardown
```

This will remove:
- Both EC2 instances
- RDS database (no final snapshot)
- S3 bucket and all contents
- Security groups
- RDS subnet group

The local `.pem` key file will be retained.

---

## Next Steps

1. **Wait 8-10 minutes** for Tesseract to finish compiling on the OCR EC2
2. **Access the dashboard** at http://18.141.57.74
3. **Test OCR extraction** by uploading one of the sample documents
4. **Review the sample data** to understand the document workflow
5. **Check system status** via the API endpoint

---

## Files in This Project

### Input Documents (`doc_input/`)
- `01_Commercial_Invoice_INV-2025-PV-04872.docx`
- `02_Packing_List_PL-2025-PV-04872.docx`
- `03_Bill_of_Lading_OOLU8823041500.docx`
- `04_Warehouse_Receipt_WR-2025-TL-00612.docx`

### Deployment Scripts (`deploy/`)
- `deploy_all.sh` - Master orchestrator
- `01_network.sh` - VPC, security groups, key pair
- `02_s3.sh` - S3 bucket configuration
- `03_ocr_ec2.sh` - Tesseract OCR server
- `04_rds.sh` - PostgreSQL database
- `05_website_ec2.sh` - Flask web dashboard
- `web-app-bootstrap.sh` - Website application installer
- `teardown.sh` - Cleanup script
- `README.md` - Deployment documentation
- `.deploy-state` - Deployment state file

---

## Support

For issues or questions:
1. Check the bootstrap logs on each EC2 instance
2. Verify security group rules allow traffic
3. Confirm RDS is in "available" state
4. Check the deployment state file: `deploy/.deploy-state`
