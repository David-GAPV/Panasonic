#!/bin/bash
# IDP Web Application Bootstrap - Downloaded and executed by EC2 user-data
set -e

RDS_ENDPOINT="$1"
DB_NAME="$2"
DB_USER="$3"
DB_PASS="$4"
OCR_IP="$5"
S3_BUCKET="$6"
AWS_REGION="$7"

echo "=== Installing packages ==="
dnf install -y python3 python3-pip postgresql15
pip3 install flask flask-cors psycopg2-binary boto3 gunicorn requests

echo "=== Applying DB schema ==="
sleep 10
aws s3 cp "s3://$S3_BUCKET/config/idp_schema.sql" /tmp/idp_schema.sql --region "$AWS_REGION" 2>/dev/null || true
PGPASSWORD="$DB_PASS" psql -h "$RDS_ENDPOINT" -U "$DB_USER" -d "$DB_NAME" -f /tmp/idp_schema.sql 2>&1 | tail -5 || echo "Schema may already exist"

mkdir -p /opt/idp-web/templates

echo "=== Creating Flask app ==="
cat > /opt/idp-web/app.py <<'PYEOF'
import os, json, requests
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import psycopg2, psycopg2.extras

app = Flask(__name__)
CORS(app)

DB_CFG = dict(host=os.getenv("RDS_ENDPOINT"), dbname=os.getenv("DB_NAME"),
              user=os.getenv("DB_USER"), password=os.getenv("DB_PASS"), connect_timeout=5)
OCR_API = f"http://{os.getenv('OCR_IP')}:8000"
S3_BUCKET = os.getenv("S3_BUCKET")

def get_db():
    return psycopg2.connect(**DB_CFG)

@app.route("/")
def dashboard():
    try:
        conn = get_db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT d.*, COUNT(vr.id) FILTER (WHERE NOT vr.passed) AS failed_checks
            FROM documents d
            LEFT JOIN validation_results vr ON vr.document_id = d.id
            GROUP BY d.id ORDER BY d.created_at DESC LIMIT 50
        """)
        docs = cur.fetchall()
        cur.execute("SELECT status, COUNT(*) cnt FROM documents GROUP BY status")
        stats = {r['status']: r['cnt'] for r in cur.fetchall()}
        conn.close()
    except Exception as e:
        docs, stats = [], {"error": str(e)}
    return render_template("dashboard.html", docs=docs, stats=stats, ocr_ip=os.getenv("OCR_IP"))

@app.route("/api/status")
def api_status():
    try:
        conn = get_db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT status, COUNT(*) cnt FROM documents GROUP BY status")
        stats = {r['status']: r['cnt'] for r in cur.fetchall()}
        conn.close()
        db_ok = True
    except Exception as e:
        stats, db_ok = {}, False
    try:
        ocr_resp = requests.get(f"{OCR_API}/health", timeout=3)
        ocr_ok = ocr_resp.status_code == 200
    except:
        ocr_ok = False
    return jsonify({"db": db_ok, "ocr_api": ocr_ok, "document_stats": stats})

if __name__=="__main__":
    app.run(host="0.0.0.0", port=80, debug=False)
PYEOF

# Create minimal HTML template
cat > /opt/idp-web/templates/dashboard.html <<'HTMLEOF'
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Panasonic IDP</title>
<style>
body{font-family:Arial;background:#f4f6f9;margin:0}
header{background:#003087;color:#fff;padding:12px 24px}
.container{max-width:1200px;margin:24px auto;padding:0 20px}
.card{background:#fff;border:1px solid #ddd;border-radius:6px;padding:20px;margin-bottom:20px}
table{width:100%;border-collapse:collapse}
th{background:#003087;color:#fff;padding:8px;text-align:left}
td{padding:8px;border-bottom:1px solid #eee}
.badge{padding:2px 10px;border-radius:12px;font-size:11px}
.badge-completed{background:#e8f5e9;color:#1a5e20}
.badge-flagged{background:#fff3e0;color:#e65100}
</style></head><body>
<header><h1>PANASONIC IDP System</h1></header>
<div class="container">
<div class="card"><h2>Document Stats</h2>
{% for status, count in stats.items() %}<p>{{ status }}: {{ count }}</p>{% endfor %}
</div>
<div class="card"><h2>Documents</h2><table><thead><tr>
<th>ID</th><th>Type</th><th>Status</th><th>Confidence</th><th>Issues</th></tr></thead><tbody>
{% for d in docs %}<tr>
<td>{{ d.doc_id }}</td>
<td>{{ d.doc_type }}</td>
<td><span class="badge badge-{{ d.status }}">{{ d.status }}</span></td>
<td>{{ d.type_confidence }}%</td>
<td>{{ d.failed_checks or 0 }}</td>
</tr>{% endfor %}</tbody></table></div>
<div class="card"><p>OCR API: http://{{ ocr_ip }}:8000/health</p>
<p>System Status: <a href="/api/status">/api/status</a></p></div>
</div></body></html>
HTMLEOF

# Create systemd service
cat > /etc/systemd/system/idp-web.service <<SVCEOF
[Unit]
Description=IDP Web Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/idp-web
Environment="RDS_ENDPOINT=$RDS_ENDPOINT"
Environment="DB_NAME=$DB_NAME"
Environment="DB_USER=$DB_USER"
Environment="DB_PASS=$DB_PASS"
Environment="OCR_IP=$OCR_IP"
Environment="S3_BUCKET=$S3_BUCKET"
ExecStart=/usr/bin/python3 /opt/idp-web/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable idp-web
systemctl start idp-web

echo "=== IDP Website Bootstrap Complete ==="
