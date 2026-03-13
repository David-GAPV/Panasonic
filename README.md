# Panasonic Vietnam IDP System

Intelligent Document Processing system for Panasonic Appliances Vietnam Co., Ltd.

## 🚀 Quick Access

**Dashboard**: http://18.142.225.22

**Status**: ✅ Live and operational

## What This System Does

Automates the processing of import/export documents for Panasonic Vietnam:
- **Commercial Invoices** - Financial declarations
- **Packing Lists** - Itemized shipment contents
- **Bills of Lading** - Shipping contracts
- **Warehouse Receipts** - Goods received confirmations

### Key Features
- OCR extraction with Tesseract (English + Vietnamese)
- Cross-document validation
- Automatic discrepancy detection
- Human review workflow for flagged items
- Complete audit trail (7-year retention)

## Current Deployment

| Component | Details |
|-----------|---------|
| **Dashboard** | http://18.142.225.22 |
| **OCR API** | http://13.215.178.213:8000 |
| **Database** | PostgreSQL 16.13 on RDS |
| **Storage** | S3 bucket with 4 sample documents |
| **Region** | ap-southeast-1 (Singapore) |
| **Profile** | pnsn |

## Sample Data Loaded

The system includes 4 realistic documents from a Shenzhen-to-Hanoi shipment:

1. **Commercial Invoice** (INV-2025-PV-04872) - USD 59,935.00, 7 line items
2. **Packing List** (PL-2025-PV-04872) - 312 cartons, 156,200 units
3. **Bill of Lading** (OOLU8823041500) - OOCL vessel, container tracking
4. **Warehouse Receipt** (WR-2025-TL-00612) - 20 units short (intentional discrepancy)

## Getting Started

### View the Dashboard
```bash
# Open in browser
open http://18.142.225.22

# Or check via curl
curl http://18.142.225.22/api/status | jq .
```

### Check OCR Status
```bash
# Health check (ready after ~8 minutes)
curl http://13.215.178.213:8000/health
```

### Access Database
```bash
PGPASSWORD="IDPPanasonic2025!" psql \
  -h idp-panasonic-postgres.c9220g60mxx2.ap-southeast-1.rds.amazonaws.com \
  -U idpadmin -d idpdb
```

## Documentation

- **[QUICK_START.md](QUICK_START.md)** - How to use the dashboard
- **[DEPLOYMENT_SUMMARY.md](DEPLOYMENT_SUMMARY.md)** - Complete infrastructure details
- **[deploy/README.md](deploy/README.md)** - Deployment scripts documentation

## Project Structure

```
.
├── doc_input/                    # 4 sample DOCX documents
│   ├── 01_Commercial_Invoice_INV-2025-PV-04872.docx
│   ├── 02_Packing_List_PL-2025-PV-04872.docx
│   ├── 03_Bill_of_Lading_OOLU8823041500.docx
│   └── 04_Warehouse_Receipt_WR-2025-TL-00612.docx
│
├── deploy/                       # AWS deployment scripts
│   ├── deploy_all.sh            # Master orchestrator
│   ├── 00_iam_role.sh           # IAM role for EC2
│   ├── 01_network.sh            # VPC, security groups
│   ├── 02_s3.sh                 # S3 bucket setup
│   ├── 03_ocr_ec2.sh            # Tesseract OCR server
│   ├── 04_rds.sh                # PostgreSQL database
│   ├── 05_website_ec2.sh        # Flask dashboard
│   ├── web-app-bootstrap.sh     # Website installer
│   ├── teardown.sh              # Cleanup script
│   └── .deploy-state            # Deployment state
│
├── README.md                     # This file
├── QUICK_START.md               # User guide
└── DEPLOYMENT_SUMMARY.md        # Infrastructure details
```

## Architecture

```
┌──────────────┐
│   Browser    │
└──────┬───────┘
       │ HTTP
       ▼
┌──────────────────────────────────────────────────┐
│  Flask Web Dashboard (EC2 t3.small)              │
│  - Document list & stats                         │
│  - Upload interface                              │
│  - System status API                             │
└──────┬───────────────────────┬───────────────────┘
       │                       │
       │ PostgreSQL            │ HTTP
       ▼                       ▼
┌──────────────────┐    ┌──────────────────┐
│  RDS PostgreSQL  │    │  Tesseract OCR   │
│  (db.t3.micro)   │    │  (EC2 t3.medium) │
│                  │    │  - eng + vie     │
│  5 tables:       │    │  - Flask API     │
│  - documents     │    └──────────────────┘
│  - fields        │
│  - validations   │    ┌──────────────────┐
│  - audit_log     │    │   S3 Bucket      │
│  - doc_types     │    │  - Documents     │
└──────────────────┘    │  - Config files  │
                        └──────────────────┘
```

## Key Technologies

- **OCR**: Tesseract 5.x (compiled from source)
- **Backend**: Python 3.9 + Flask
- **Database**: PostgreSQL 16.13
- **Storage**: AWS S3 with versioning
- **Infrastructure**: AWS EC2, RDS, S3, IAM
- **Region**: ap-southeast-1 (Singapore - closest to Vietnam)

## Cost Estimate

| Resource | Type | Monthly Cost |
|----------|------|--------------|
| OCR EC2 | t3.medium | ~$38 |
| Web EC2 | t3.small | ~$19 |
| RDS | db.t3.micro | ~$15 |
| S3 | First 50GB | ~$1 |
| **Total** | | **~$73/month** |

## Management Commands

### Redeploy Everything
```bash
cd deploy
./deploy_all.sh
```

### Destroy All Resources
```bash
cd deploy
./deploy_all.sh --teardown
```

### Stop Instances (save cost)
```bash
AWS_PROFILE=pnsn aws ec2 stop-instances \
  --instance-ids i-0f1f1440abb67375b i-029d0ca192bf5cad8
```

### Start Instances
```bash
AWS_PROFILE=pnsn aws ec2 start-instances \
  --instance-ids i-0f1f1440abb67375b i-029d0ca192bf5cad8
```

## SSH Access

```bash
# Website EC2
ssh -i deploy/idp-panasonic-key.pem ec2-user@18.142.225.22

# OCR EC2
ssh -i deploy/idp-panasonic-key.pem ec2-user@13.215.178.213
```

## Monitoring

### Check Website Logs
```bash
ssh -i deploy/idp-panasonic-key.pem ec2-user@18.142.225.22 \
  'sudo journalctl -u idp-web -f'
```

### Check OCR Bootstrap
```bash
ssh -i deploy/idp-panasonic-key.pem ec2-user@13.215.178.213 \
  'tail -f /var/log/idp-bootstrap.log'
```

### Database Query
```bash
PGPASSWORD="IDPPanasonic2025!" psql \
  -h idp-panasonic-postgres.c9220g60mxx2.ap-southeast-1.rds.amazonaws.com \
  -U idpadmin -d idpdb \
  -c "SELECT doc_id, status, type_confidence FROM documents ORDER BY created_at DESC;"
```

## Security Notes

- All EC2 instances use security groups with minimal port exposure
- RDS is not publicly accessible (EC2 access only)
- S3 bucket has public access blocked
- IAM roles follow least-privilege principle
- Database password should be changed for production use

## Support & Troubleshooting

### Dashboard not loading?
1. Check EC2 instance status
2. Verify security group allows port 80
3. Check service status: `sudo systemctl status idp-web`

### OCR not responding?
1. Wait 8-10 minutes after deployment for Tesseract compilation
2. Check bootstrap log for errors
3. Verify security group allows port 8000

### Database connection issues?
1. Verify RDS is in "available" state
2. Check security group allows port 5432 from EC2
3. Confirm credentials are correct

## License & Credits

Built for Panasonic Appliances Vietnam Co., Ltd.
Team 07 - AWS ap-southeast-1 deployment

---

**Last Updated**: March 13, 2026
**Deployment Status**: ✅ Operational
