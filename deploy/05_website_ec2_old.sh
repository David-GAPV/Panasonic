#!/usr/bin/env bash
# 05_website_ec2.sh — EC2 hosting the IDP sample web dashboard
set -euo pipefail
STATE_FILE="$1"; source "$STATE_FILE"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()     { echo -e "${CYAN}  [web-ec2]${NC} $*"; }
success() { echo -e "${GREEN}  [OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}  [WARN]${NC} $*"; }

# ── Fetch AMI if not in state ─────────────────────────────────────────────
if [[ -z "${AMI_ID:-}" ]]; then
  log "Fetching latest Amazon Linux 2023 AMI..."
  AMI_ID=$(aws ec2 describe-images \
    --owners amazon \
    --filters \
      "Name=name,Values=al2023-ami-2023.*-x86_64" \
      "Name=state,Values=available" \
    --query "sort_by(Images, &CreationDate)[-1].ImageId" \
    --output text)
  success "AMI: $AMI_ID"
fi

# ── User-data: installs Python Flask web app + applies DB schema ──────────
# Placeholders @@VAR@@ are replaced below before passing to AWS
USER_DATA_TEMPLATE=$(cat <<'USERDATA'
#!/bin/bash
set -e
exec > /var/log/idp-website-bootstrap.log 2>&1
echo "=== IDP Website Bootstrap Start: $(date) ==="

dnf update -y
dnf install -y python3 python3-pip postgresql15 awscli

pip3 install flask flask-cors psycopg2-binary boto3 gunicorn requests

# ── Pull schema from S3 and apply ─────────────────────────────────────────
echo "--- Applying DB schema ---"
sleep 10   # give RDS a moment if it was just created
aws s3 cp s3://@@S3_BUCKET@@/config/idp_schema.sql /tmp/idp_schema.sql \
  --region @@AWS_REGION@@ 2>/dev/null || true

PGPASSWORD="@@DB_PASS@@" psql \
  -h @@RDS_ENDPOINT@@ -U @@DB_USER@@ -d @@DB_NAME@@ \
  -f /tmp/idp_schema.sql 2>&1 | tail -5 || echo "Schema apply failed (may already exist)"

# ── Web application ────────────────────────────────────────────────────────
mkdir -p /opt/idp-web/templates

# ── Flask app ─────────────────────────────────────────────────────────────
cat > /opt/idp-web/app.py <<'PYEOF'
import os, json, requests
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_cors import CORS
import psycopg2, psycopg2.extras

app = Flask(__name__)
CORS(app)

DB_CFG = dict(host="@@RDS_ENDPOINT@@", dbname="@@DB_NAME@@",
              user="@@DB_USER@@",   password="@@DB_PASS@@", connect_timeout=5)
OCR_API = "http://@@OCR_PUBLIC_IP@@:8000"
S3_BUCKET = "@@S3_BUCKET@@"

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
    return render_template("dashboard.html", docs=docs, stats=stats, ocr_ip="@@OCR_PUBLIC_IP@@")

@app.route("/document/<doc_id>")
def document_detail(doc_id):
    try:
        conn = get_db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM documents WHERE doc_id=%s", (doc_id,))
        doc = cur.fetchone()
        cur.execute("SELECT * FROM extracted_fields WHERE document_id=%s ORDER BY field_name", (doc["id"],))
        fields = cur.fetchall()
        cur.execute("SELECT * FROM validation_results WHERE document_id=%s", (doc["id"],))
        validations = cur.fetchall()
        cur.execute("SELECT * FROM audit_log WHERE document_id=%s ORDER BY logged_at DESC", (doc["id"],))
        audit = cur.fetchall()
        conn.close()
    except Exception as e:
        return f"<pre>Error: {e}</pre>", 500
    return render_template("document.html", doc=doc, fields=fields,
                           validations=validations, audit=audit)

@app.route("/upload", methods=["GET","POST"])
def upload():
    result = None
    if request.method == "POST":
        file = request.files.get("file")
        if file:
            try:
                resp = requests.post(
                    f"{OCR_API}/extract",
                    files={"file": (file.filename, file.read(), file.content_type)},
                    data={"lang": "eng+vie", "doc_id": file.filename},
                    timeout=60
                )
                result = resp.json()
                # Persist to DB
                conn = get_db()
                cur  = conn.cursor()
                cur.execute("""
                    INSERT INTO documents (doc_id, doc_type, type_confidence, filename, uploaded_by, status)
                    VALUES (%s,%s,%s,%s,%s,'extracted')
                    ON CONFLICT (doc_id) DO UPDATE SET status='extracted', updated_at=NOW()
                    RETURNING id
                """, (file.filename, result.get("document_type","unknown"),
                      result.get("type_confidence",0), file.filename, "web_user"))
                doc_db_id = cur.fetchone()[0]
                for fname, fdata in result.get("fields",{}).items():
                    val = fdata["value"] if isinstance(fdata["value"],str) else str(fdata["value"])
                    cur.execute("""
                        INSERT INTO extracted_fields (document_id, field_name, field_value, confidence)
                        VALUES (%s,%s,%s,%s)
                    """, (doc_db_id, fname, val, fdata.get("confidence",0)))
                conn.commit(); conn.close()
            except requests.exceptions.ConnectionError:
                result = {"error": f"OCR API not reachable at {OCR_API} — Tesseract may still be compiling (~8 min after deploy)"}
    return render_template("upload.html", result=result)

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

# ── HTML templates ─────────────────────────────────────────────────────────
cat > /opt/idp-web/templates/base.html <<'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Panasonic Vietnam — IDP System</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: Arial, sans-serif; font-size: 14px; background: #f4f6f9; color: #333; }
    header { background: #003087; color: #fff; padding: 12px 24px; display: flex; align-items: center; gap: 16px; }
    header h1 { font-size: 18px; font-weight: 700; letter-spacing: .5px; }
    header span { font-size: 12px; opacity: .8; }
    nav { background: #00256e; display: flex; gap: 2px; padding: 0 24px; }
    nav a { color: #ccd9ff; text-decoration: none; padding: 9px 16px; font-size: 13px; display: block; }
    nav a:hover, nav a.active { background: #003087; color: #fff; }
    .container { max-width: 1200px; margin: 24px auto; padding: 0 20px; }
    .card { background: #fff; border: 1px solid #dde1ea; border-radius: 6px; padding: 20px; margin-bottom: 20px; }
    .card h2 { font-size: 15px; font-weight: 700; color: #003087; margin-bottom: 14px; border-bottom: 2px solid #e8eef8; padding-bottom: 8px; }
    .badge { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }
    .badge-completed  { background: #e8f5e9; color: #1a5e20; }
    .badge-flagged    { background: #fff3e0; color: #e65100; }
    .badge-reviewing  { background: #e3f2fd; color: #0d47a1; }
    .badge-queued     { background: #f3e5f5; color: #6a1b9a; }
    .badge-extracted  { background: #fce4ec; color: #880e4f; }
    table { width: 100%; border-collapse: collapse; }
    th { background: #003087; color: #fff; padding: 8px 12px; text-align: left; font-size: 12px; }
    td { padding: 8px 12px; border-bottom: 1px solid #eef0f5; font-size: 13px; }
    tr:hover td { background: #f8f9ff; }
    .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .stat { background: #fff; border: 1px solid #dde1ea; border-radius: 6px; padding: 14px 16px; text-align: center; }
    .stat .num { font-size: 28px; font-weight: 700; color: #003087; }
    .stat .lbl { font-size: 11px; color: #888; margin-top: 2px; text-transform: uppercase; }
    .btn { display: inline-block; padding: 7px 18px; background: #003087; color: #fff; text-decoration: none;
           border-radius: 4px; font-size: 13px; border: none; cursor: pointer; }
    .btn:hover { background: #00256e; }
    .alert-warn { background: #fff3e0; border-left: 4px solid #e65100; padding: 10px 14px; border-radius: 4px; color: #bf360c; font-size: 13px; }
    .conf-high { color: #1a5e20; font-weight: 600; }
    .conf-med  { color: #e65100; font-weight: 600; }
    .conf-low  { color: #b71c1c; font-weight: 600; }
    a { color: #003087; }
    footer { text-align: center; padding: 20px; color: #aaa; font-size: 12px; }
  </style>
</head>
<body>
<header>
  <div>
    <h1>PANASONIC — Intelligent Document Processing</h1>
    <span>Panasonic Appliances Vietnam Co., Ltd. — Thang Long Industrial Park, Hanoi</span>
  </div>
</header>
<nav>
  <a href="/" class="{{ 'active' if request.path == '/' else '' }}">Dashboard</a>
  <a href="/upload" class="{{ 'active' if request.path == '/upload' else '' }}">Upload Document</a>
  <a href="/api/status" target="_blank">System Status (JSON)</a>
</nav>
<div class="container">
  {% block content %}{% endblock %}
</div>
<footer>IDP System — Panasonic Vietnam &nbsp;|&nbsp; Team 07 &nbsp;|&nbsp; AWS ap-southeast-1</footer>
</body>
</html>
HTMLEOF

cat > /opt/idp-web/templates/dashboard.html <<'HTMLEOF'
{% extends "base.html" %}
{% block content %}
<div class="stat-grid">
  {% for status, count in stats.items() %}
  <div class="stat">
    <div class="num">{{ count }}</div>
    <div class="lbl">{{ status.replace('_',' ') }}</div>
  </div>
  {% endfor %}
</div>

<div class="card">
  <h2>Document Processing Queue</h2>
  <table>
    <thead>
      <tr>
        <th>Document ID</th><th>Type</th><th>Filename</th>
        <th>Status</th><th>Confidence</th><th>Issues</th>
        <th>Uploaded By</th><th>Created</th><th></th>
      </tr>
    </thead>
    <tbody>
    {% for d in docs %}
    <tr>
      <td><a href="/document/{{ d.doc_id }}">{{ d.doc_id }}</a></td>
      <td>{{ d.doc_type|replace('_',' ')|title if d.doc_type else '—' }}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ d.filename }}</td>
      <td><span class="badge badge-{{ d.status }}">{{ d.status.replace('_',' ') }}</span></td>
      <td>
        {% if d.type_confidence %}
          {% if d.type_confidence >= 85 %}<span class="conf-high">{{ d.type_confidence }}%</span>
          {% elif d.type_confidence >= 70 %}<span class="conf-med">{{ d.type_confidence }}%</span>
          {% else %}<span class="conf-low">{{ d.type_confidence }}%</span>
          {% endif %}
        {% else %}—{% endif %}
      </td>
      <td>
        {% if d.failed_checks and d.failed_checks > 0 %}
          <span class="badge badge-flagged">{{ d.failed_checks }} failed</span>
        {% else %}<span class="conf-high">✓ clean</span>{% endif %}
      </td>
      <td>{{ d.uploaded_by or '—' }}</td>
      <td>{{ d.created_at.strftime('%d %b %H:%M') if d.created_at else '—' }}</td>
      <td><a href="/document/{{ d.doc_id }}" class="btn" style="padding:4px 10px;font-size:11px">View</a></td>
    </tr>
    {% else %}
    <tr><td colspan="9" style="text-align:center;padding:30px;color:#aaa">No documents yet. <a href="/upload">Upload one →</a></td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<div class="card" style="font-size:12px;color:#666">
  <h2>Infrastructure Endpoints</h2>
  <table>
    <tr><th>Service</th><th>Endpoint</th><th>Note</th></tr>
    <tr><td>OCR API</td><td>http://{{ ocr_ip }}:8000/health</td><td>Tesseract + Flask — may take ~8 min after first deploy</td></tr>
    <tr><td>OCR Extract</td><td>POST http://{{ ocr_ip }}:8000/extract</td><td>multipart/form-data, field: file</td></tr>
    <tr><td>System JSON</td><td><a href="/api/status">/api/status</a></td><td>DB + OCR health check</td></tr>
  </table>
</div>
{% endblock %}
HTMLEOF

cat > /opt/idp-web/templates/document.html <<'HTMLEOF'
{% extends "base.html" %}
{% block content %}
<p style="margin-bottom:12px"><a href="/">← Back to Dashboard</a></p>
<div class="card">
  <h2>{{ doc.doc_id }} &nbsp; <span class="badge badge-{{ doc.status }}">{{ doc.status.replace('_',' ') }}</span></h2>
  <table style="width:auto">
    <tr><td style="color:#888;padding:4px 16px 4px 0">Type</td><td><b>{{ doc.doc_type|replace('_',' ')|title }}</b> ({{ doc.type_confidence }}% confidence)</td></tr>
    <tr><td style="color:#888">Filename</td><td>{{ doc.filename }}</td></tr>
    <tr><td style="color:#888">Uploaded by</td><td>{{ doc.uploaded_by or '—' }}</td></tr>
    <tr><td style="color:#888">Created</td><td>{{ doc.created_at }}</td></tr>
  </table>
</div>

<div class="card">
  <h2>Extracted Fields</h2>
  {% if fields %}
  <table>
    <tr><th>Field</th><th>Extracted Value</th><th>Confidence</th><th>Reviewed</th><th>Correction</th></tr>
    {% for f in fields %}
    <tr>
      <td><b>{{ f.field_name.replace('_',' ') }}</b></td>
      <td>{{ f.field_value }}</td>
      <td>{% if f.confidence >= 85 %}<span class="conf-high">{{ f.confidence }}%</span>
          {% elif f.confidence >= 70 %}<span class="conf-med">{{ f.confidence }}%</span>
          {% else %}<span class="conf-low">{{ f.confidence }}%</span>{% endif %}</td>
      <td>{{ '✓' if f.reviewed else '—' }}</td>
      <td>{{ f.corrected_value or '—' }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<p style="color:#aaa">No fields extracted yet.</p>{% endif %}
</div>

<div class="card">
  <h2>Validation Results</h2>
  {% if validations %}
  <table>
    <tr><th>Check Type</th><th>Check Name</th><th>Result</th><th>Detail</th></tr>
    {% for v in validations %}
    <tr>
      <td>{{ v.check_type.replace('_',' ') }}</td>
      <td>{{ v.check_name }}</td>
      <td>{% if v.passed %}<span class="conf-high">✓ PASS</span>{% else %}<span class="conf-low">✗ FAIL</span>{% endif %}</td>
      <td>{{ v.detail or '—' }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<p style="color:#aaa">No validation results yet.</p>{% endif %}
</div>

<div class="card">
  <h2>Audit Trail</h2>
  <table>
    <tr><th>Time</th><th>User</th><th>Action</th><th>Detail</th></tr>
    {% for a in audit %}
    <tr>
      <td>{{ a.logged_at.strftime('%d %b %Y %H:%M:%S') if a.logged_at else '—' }}</td>
      <td>{{ a.user_id or 'system' }}</td>
      <td>{{ a.action.replace('_',' ') }}</td>
      <td>{{ a.new_value or '—' }}</td>
    </tr>
    {% else %}<tr><td colspan="4" style="color:#aaa;text-align:center">No audit entries.</td></tr>
    {% endfor %}
  </table>
</div>
{% endblock %}
HTMLEOF

cat > /opt/idp-web/templates/upload.html <<'HTMLEOF'
{% extends "base.html" %}
{% block content %}
<div class="card" style="max-width:600px">
  <h2>Upload Document for OCR Processing</h2>
  <form method="POST" enctype="multipart/form-data">
    <p style="margin-bottom:12px;color:#555">
      Supported: PNG, JPEG, PDF, TIFF &nbsp;|&nbsp; Languages: English + Vietnamese
    </p>
    <input type="file" name="file" accept=".png,.jpg,.jpeg,.pdf,.tiff,.docx" required
           style="display:block;margin-bottom:14px;padding:8px;border:1px solid #ccc;border-radius:4px;width:100%">
    <button type="submit" class="btn">Extract &amp; Process →</button>
  </form>
</div>

{% if result %}
  {% if result.get('error') %}
  <div class="alert-warn">{{ result.error }}</div>
  {% else %}
  <div class="card">
    <h2>Extraction Result — {{ result.filename }}</h2>
    <table style="width:auto;margin-bottom:14px">
      <tr><td style="color:#888;padding-right:20px">Document Type</td>
          <td><b>{{ result.document_type|replace('_',' ')|title }}</b> ({{ result.type_confidence }}%)</td></tr>
      <tr><td style="color:#888">Pages</td><td>{{ result.pages }}</td></tr>
      <tr><td style="color:#888">Language</td><td>{{ result.language }}</td></tr>
    </table>
    <h2>Extracted Fields</h2>
    <table>
      <tr><th>Field</th><th>Value</th><th>Confidence</th></tr>
      {% for fname, fdata in result.fields.items() %}
      <tr>
        <td>{{ fname.replace('_',' ') }}</td>
        <td>{{ fdata.value if fdata.value is string else fdata.value|join(', ') }}</td>
        <td>{% if fdata.confidence >= 85 %}<span class="conf-high">{{ fdata.confidence }}%</span>
            {% elif fdata.confidence >= 70 %}<span class="conf-med">{{ fdata.confidence }}%</span>
            {% else %}<span class="conf-low">{{ fdata.confidence }}%</span>{% endif %}</td>
      </tr>
      {% else %}
      <tr><td colspan="3" style="color:#aaa">No fields extracted — try a clearer image</td></tr>
      {% endfor %}
    </table>
    <p style="margin-top:14px"><a href="/" class="btn">← Back to Dashboard</a></p>
  </div>
  {% endif %}
{% endif %}
{% endblock %}
HTMLEOF

# ── Replace placeholders ──────────────────────────────────────────────────
for f in /opt/idp-web/app.py /opt/idp-web/templates/*.html; do
  sed -i \
    -e "s|@@RDS_ENDPOINT@@|@@RDS_ENDPOINT@@|g" \
    -e "s|@@DB_NAME@@|@@DB_NAME@@|g" \
    -e "s|@@DB_USER@@|@@DB_USER@@|g" \
    -e "s|@@DB_PASS@@|@@DB_PASS@@|g" \
    -e "s|@@OCR_PUBLIC_IP@@|@@OCR_PUBLIC_IP@@|g" \
    -e "s|@@S3_BUCKET@@|@@S3_BUCKET@@|g" \
    -e "s|@@AWS_REGION@@|@@AWS_REGION@@|g" \
    "$f" 2>/dev/null || true
done

# ── systemd ────────────────────────────────────────────────────────────────
cat > /etc/systemd/system/idp-web.service <<'SVCEOF'
[Unit]
Description=IDP Panasonic Vietnam Web Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/idp-web
ExecStart=/usr/bin/python3 /opt/idp-web/app.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable idp-web
systemctl start idp-web

echo "=== IDP Website Bootstrap Complete: $(date) ==="
USERDATA
)

# ── Substitute real values into user-data ─────────────────────────────────
USER_DATA="${USER_DATA_TEMPLATE}"
USER_DATA="${USER_DATA//@@S3_BUCKET@@/$S3_BUCKET}"
USER_DATA="${USER_DATA//@@AWS_REGION@@/$AWS_REGION}"
USER_DATA="${USER_DATA//@@RDS_ENDPOINT@@/$RDS_ENDPOINT}"
USER_DATA="${USER_DATA//@@DB_NAME@@/$DB_NAME}"
USER_DATA="${USER_DATA//@@DB_USER@@/$DB_USER}"
USER_DATA="${USER_DATA//@@DB_PASS@@/$DB_PASS}"
USER_DATA="${USER_DATA//@@OCR_PUBLIC_IP@@/$OCR_PUBLIC_IP}"

# ── Launch Website EC2 (t3.small is plenty for Flask) ─────────────────────
log "Launching Website EC2 instance (t3.small)..."
WEB_INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type t3.small \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_WEB" \
  --subnet-id "$SUBNET_1" \
  --associate-public-ip-address \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":10,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --user-data "$USER_DATA" \
  --tag-specifications \
    "ResourceType=instance,Tags=[{Key=Name,Value=$PROJECT-web},{Key=Project,Value=$PROJECT}]" \
  --query "Instances[0].InstanceId" \
  --output text)
success "Launched: $WEB_INSTANCE_ID"

log "Waiting for public IP..."
for i in $(seq 1 30); do
  WEB_PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids "$WEB_INSTANCE_ID" \
    --query "Reservations[0].Instances[0].PublicIpAddress" \
    --output text 2>/dev/null || echo "")
  [[ -n "$WEB_PUBLIC_IP" && "$WEB_PUBLIC_IP" != "None" ]] && break
  sleep 5
done

success "Website EC2 ready — http://$WEB_PUBLIC_IP  (ready in ~2 min)"
warn "SSH: ssh -i $KEY_NAME.pem ec2-user@$WEB_PUBLIC_IP"

cat >> "$STATE_FILE" <<EOF
WEB_INSTANCE_ID=$WEB_INSTANCE_ID
WEB_PUBLIC_IP=$WEB_PUBLIC_IP
EOF
