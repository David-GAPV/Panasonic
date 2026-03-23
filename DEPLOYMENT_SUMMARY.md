# Deployment Summary

> For current system details, see [README.md](README.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## Infrastructure

| Component | Service | Details |
|-----------|---------|---------|
| Web Server | EC2 t3.micro | Nginx + Flask, systemd `idp-web` |
| OCR Server | EC2 t3.medium | Tesseract 5.5.2 + Flask, systemd `idp-ocr` |
| Database | RDS db.t3.micro | PostgreSQL 16.13, 8 tables |
| Storage | S3 | Versioning, AES-256, lifecycle policies |
| Notifications | SNS | Email alerts on flag/approve/reject |
| Network | VPC | Public + private subnets, security groups |
| SSL | Let's Encrypt | certbot, domain: idp.pngha.io.vn |

## Deployment Scripts

All in `deploy/` directory, using AWS CLI with profile `pnsn` in `ap-southeast-1`:

```
deploy_all.sh       — Master orchestrator (also supports --teardown)
00_iam_role.sh      — IAM role for EC2
01_network.sh       — VPC, subnets, security groups
02_s3.sh            — S3 bucket setup
03_ocr_ec2.sh       — OCR EC2 instance
04_rds.sh           — PostgreSQL RDS
05_website_ec2.sh   — Web EC2 instance
web-app-bootstrap.sh — Website application installer
teardown.sh         — Resource cleanup
```

## Cost

~$73/month total (OCR EC2 $38 + Web EC2 $19 + RDS $15 + S3 $1)
