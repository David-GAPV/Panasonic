#!/bin/bash
set -e
WEB_IP="18.142.225.22"
KEY="deploy/idp-panasonic-key.pem"
SSH_CMD="ssh -i $KEY -o StrictHostKeyChecking=no ec2-user@$WEB_IP"
SCP_CMD="scp -i $KEY -o StrictHostKeyChecking=no"

echo "=== Step 1: Upload logos ==="
$SCP_CMD images/Panasonic_whitetext.jpg ec2-user@$WEB_IP:/tmp/
$SCP_CMD images/Panasonic_blue_text.webp ec2-user@$WEB_IP:/tmp/

echo "=== Step 2: Upload templates ==="
for f in templates/*.html; do
    $SCP_CMD "$f" "ec2-user@$WEB_IP:/tmp/$(basename $f)"
done

echo "=== Step 3: Install on server ==="
$SSH_CMD 'sudo bash -s' <<'EOF3'
mkdir -p /opt/idp-web/static/images
cp /tmp/Panasonic_whitetext.jpg /opt/idp-web/static/images/
cp /tmp/Panasonic_blue_text.webp /opt/idp-web/static/images/
chmod -R 755 /opt/idp-web/static
for t in dashboard upload review_queue sap_simulation document_detail; do
  cp /tmp/${t}.html /opt/idp-web/templates/
done
ls -la /opt/idp-web/static/images/ /opt/idp-web/templates/
EOF3

echo "=== Step 4: Upload Flask app ==="
$SCP_CMD app_deploy.py ec2-user@$WEB_IP:/tmp/app.py
$SCP_CMD nginx-idp.conf ec2-user@$WEB_IP:/tmp/idp.conf

$SSH_CMD 'sudo bash -s' <<'EOF4'
cp /tmp/app.py /opt/idp-web/app.py
echo "Flask app installed"
EOF4

echo "=== Step 5: Update systemd service ==="
$SSH_CMD "sudo bash -s" <<EOFSVC
cat > /etc/systemd/system/idp-web.service <<SVCEOF
[Unit]
Description=IDP Web Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/idp-web
Environment="RDS_ENDPOINT=idp-panasonic-postgres.c9220g60mxx2.ap-southeast-1.rds.amazonaws.com"
Environment="DB_NAME=idpdb"
Environment="DB_USER=idpadmin"
Environment="DB_PASS=IDPPanasonic2025!"
Environment="OCR_IP=13.215.178.213"
Environment="S3_BUCKET=idp-panasonic-docs-853878127521"
Environment="AWS_REGION=ap-southeast-1"
ExecStart=/usr/bin/python3 /opt/idp-web/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl restart idp-web
echo "Flask service restarted on port 5000"
EOFSVC

echo "=== Step 6: Setup Nginx + SSL ==="
$SSH_CMD 'sudo bash -s' <<'EOF6'
dnf install -y nginx 2>/dev/null || yum install -y nginx 2>/dev/null
rm -f /etc/nginx/conf.d/default.conf 2>/dev/null
cp /tmp/idp.conf /etc/nginx/conf.d/idp.conf
nginx -t && systemctl enable nginx && systemctl restart nginx
echo "Nginx running"

# Install certbot
pip3 install certbot certbot-nginx 2>/dev/null || dnf install -y certbot python3-certbot-nginx 2>/dev/null || true
certbot --nginx -d idp.pngha.io.vn --non-interactive --agree-tos -m haithe123123@gmail.com --redirect 2>&1 || echo "Certbot: DNS may not point here yet"
echo "=== Done ==="
EOF6

echo ""
echo "=== Deployment complete ==="
echo "HTTP:  http://18.142.225.22"
echo "HTTPS: https://idp.pngha.io.vn"
