#!/bin/bash
# Update the web application with upload functionality
set -e

WEB_IP="18.142.225.22"
KEY="deploy/idp-panasonic-key.pem"

echo "=== Updating Flask app with upload functionality ==="

ssh -i "$KEY" -o StrictHostKeyChecking=no ec2-user@$WEB_IP 'sudo bash -s' <<'REMOTE_SCRIPT'
# Stop the service
systemctl stop idp-web

# Backup current app
cp /opt/idp-web/app.py /opt/idp-web/app.py.backup

# Install additional dependencies
pip3 install python-docx PyPDF2 Pillow pytesseract

# Create updated Flask app with upload support
cat > /opt/idp-web/app.py <<'PYEOF'
import os, json, requests, tempfile
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS
import psycopg2, psycopg2.extras
import boto3

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

DB_CFG = dict(host=os.getenv("RDS_ENDPOINT"), dbname=os.getenv("DB_NAME"),
              user=os.getenv("DB_USER"), password=os.getenv("DB_PASS"), connect_timeout=5)
OCR_API = f"http://{os.getenv('OCR_IP')}:8000"
S3_BUCKET = os.getenv("S3_BUCKET")
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-1")

s3_client = boto3.client('s3', region_name=AWS_REGION)

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

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "GET":
        return render_template("upload.html", ocr_ip=os.getenv("OCR_IP"))
    
    # Handle POST - file upload
    result = None
    error = None
    
    if 'file' not in request.files:
        error = "No file provided"
    else:
        file = request.files['file']
        if file.filename == '':
            error = "No file selected"
        elif file:
            try:
                # Save to temp file
                with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
                    file.save(tmp.name)
                    tmp_path = tmp.name
                
                # Upload to S3
                s3_key = f"uploads/{file.filename}"
                s3_client.upload_file(tmp_path, S3_BUCKET, s3_key)
                
                # Send to OCR API
                with open(tmp_path, 'rb') as f:
                    resp = requests.post(
                        f"{OCR_API}/extract",
                        files={"file": (file.filename, f, file.content_type or 'application/octet-stream')},
                        data={"lang": "eng+vie"},
                        timeout=60
                    )
                
                os.unlink(tmp_path)
                
                if resp.status_code == 200:
                    result = resp.json()
                    
                    # Save to database
                    conn = get_db()
                    cur = conn.cursor()
                    doc_id = result.get('document_id', file.filename)
                    
                    cur.execute("""
                        INSERT INTO documents (doc_id, doc_type, type_confidence, filename, uploaded_by, status, original_s3_key)
                        VALUES (%s,%s,%s,%s,%s,'extracted',%s)
                        ON CONFLICT (doc_id) DO UPDATE 
                        SET status='extracted', updated_at=NOW()
                        RETURNING id
                    """, (doc_id, result.get("document_type","unknown"),
                          result.get("type_confidence",0), file.filename, "web_user", s3_key))
                    
                    doc_db_id = cur.fetchone()[0]
                    
                    # Save extracted fields
                    for fname, fdata in result.get("fields",{}).items():
                        if isinstance(fdata, dict):
                            val = str(fdata.get("value", ""))
                            conf = fdata.get("confidence", 0)
                        else:
                            val = str(fdata)
                            conf = 0
                        
                        cur.execute("""
                            INSERT INTO extracted_fields (document_id, field_name, field_value, confidence)
                            VALUES (%s,%s,%s,%s)
                        """, (doc_db_id, fname, val, conf))
                    
                    conn.commit()
                    conn.close()
                else:
                    error = f"OCR API error: {resp.status_code} - {resp.text}"
                    
            except requests.exceptions.ConnectionError:
                error = f"OCR API not reachable at {OCR_API}. Tesseract may still be compiling (~8 min after deploy)"
            except Exception as e:
                error = f"Error processing file: {str(e)}"
    
    return render_template("upload.html", result=result, error=error, ocr_ip=os.getenv("OCR_IP"))

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

# Update dashboard template with navigation
cat > /opt/idp-web/templates/dashboard.html <<'HTMLEOF'
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Panasonic IDP</title>
<style>
body{font-family:Arial;background:#f4f6f9;margin:0}
header{background:#003087;color:#fff;padding:12px 24px}
nav{background:#00256e;padding:8px 24px}
nav a{color:#ccd9ff;text-decoration:none;padding:8px 16px;margin-right:8px;display:inline-block;border-radius:4px}
nav a:hover{background:#003087;color:#fff}
.container{max-width:1200px;margin:24px auto;padding:0 20px}
.card{background:#fff;border:1px solid #ddd;border-radius:6px;padding:20px;margin-bottom:20px}
table{width:100%;border-collapse:collapse}
th{background:#003087;color:#fff;padding:8px;text-align:left;font-size:12px}
td{padding:8px;border-bottom:1px solid #eee;font-size:13px}
.badge{padding:2px 10px;border-radius:12px;font-size:11px;font-weight:600}
.badge-completed{background:#e8f5e9;color:#1a5e20}
.badge-flagged{background:#fff3e0;color:#e65100}
.badge-reviewing{background:#e3f2fd;color:#0d47a1}
.badge-extracted{background:#fce4ec;color:#880e4f}
.badge-queued{background:#f3e5f5;color:#6a1b9a}
</style></head><body>
<header><h1>PANASONIC IDP System</h1></header>
<nav>
<a href="/">Dashboard</a>
<a href="/upload">Upload Document</a>
<a href="/api/status" target="_blank">System Status</a>
</nav>
<div class="container">
<div class="card"><h2>Document Stats</h2>
{% for status, count in stats.items() %}<p><strong>{{ status|replace('_',' ')|title }}:</strong> {{ count }}</p>{% endfor %}
</div>
<div class="card"><h2>Documents</h2><table><thead><tr>
<th>ID</th><th>Type</th><th>Filename</th><th>Status</th><th>Confidence</th><th>Issues</th></tr></thead><tbody>
{% for d in docs %}<tr>
<td>{{ d.doc_id }}</td>
<td>{{ d.doc_type|replace('_',' ')|title if d.doc_type else '—' }}</td>
<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">{{ d.filename or '—' }}</td>
<td><span class="badge badge-{{ d.status }}">{{ d.status|replace('_',' ') }}</span></td>
<td>{{ d.type_confidence }}%</td>
<td>{{ d.failed_checks or 0 }}</td>
</tr>{% endfor %}</tbody></table></div>
<div class="card"><p><strong>OCR API:</strong> http://{{ ocr_ip }}:8000/health</p>
<p><strong>System Status:</strong> <a href="/api/status">/api/status</a></p></div>
</div></body></html>
HTMLEOF

# Create upload template
cat > /opt/idp-web/templates/upload.html <<'HTMLEOF'
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Upload Document - Panasonic IDP</title>
<style>
body{font-family:Arial;background:#f4f6f9;margin:0}
header{background:#003087;color:#fff;padding:12px 24px}
nav{background:#00256e;padding:8px 24px}
nav a{color:#ccd9ff;text-decoration:none;padding:8px 16px;margin-right:8px;display:inline-block;border-radius:4px}
nav a:hover{background:#003087;color:#fff}
.container{max-width:800px;margin:24px auto;padding:0 20px}
.card{background:#fff;border:1px solid #ddd;border-radius:6px;padding:20px;margin-bottom:20px}
.btn{display:inline-block;padding:10px 20px;background:#003087;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:14px}
.btn:hover{background:#00256e}
input[type=file]{display:block;width:100%;padding:10px;border:2px dashed #ccc;border-radius:4px;margin:16px 0;cursor:pointer}
.alert-error{background:#ffebee;border-left:4px solid #c62828;padding:12px;margin:16px 0;color:#b71c1c}
.alert-success{background:#e8f5e9;border-left:4px solid #2e7d32;padding:12px;margin:16px 0;color:#1b5e20}
table{width:100%;border-collapse:collapse;margin-top:12px}
th{background:#003087;color:#fff;padding:8px;text-align:left}
td{padding:8px;border-bottom:1px solid #eee}
</style></head><body>
<header><h1>PANASONIC IDP System</h1></header>
<nav>
<a href="/">Dashboard</a>
<a href="/upload" style="background:#003087">Upload Document</a>
<a href="/api/status" target="_blank">System Status</a>
</nav>
<div class="container">
<div class="card">
<h2>Upload Document for OCR Processing</h2>
<p style="color:#666;margin-bottom:16px">
Supported formats: PDF, PNG, JPEG, TIFF, DOCX<br>
Languages: English + Vietnamese<br>
Max file size: 16 MB
</p>
<form method="POST" enctype="multipart/form-data">
<input type="file" name="file" accept=".pdf,.png,.jpg,.jpeg,.tiff,.tif,.docx" required>
<button type="submit" class="btn">Upload & Extract →</button>
</form>
</div>

{% if error %}
<div class="alert-error"><strong>Error:</strong> {{ error }}</div>
{% endif %}

{% if result %}
<div class="alert-success">✓ Document processed successfully!</div>
<div class="card">
<h2>Extraction Results</h2>
<p><strong>Document ID:</strong> {{ result.document_id or result.filename }}</p>
<p><strong>Document Type:</strong> {{ result.document_type|replace('_',' ')|title }} ({{ result.type_confidence }}% confidence)</p>
<p><strong>Pages:</strong> {{ result.pages or 'N/A' }}</p>

<h3 style="margin-top:20px">Extracted Fields</h3>
{% if result.fields %}
<table>
<tr><th>Field</th><th>Value</th><th>Confidence</th></tr>
{% for fname, fdata in result.fields.items() %}
<tr>
<td><strong>{{ fname|replace('_',' ')|title }}</strong></td>
<td>
{% if fdata is mapping %}
{{ fdata.value }}
{% else %}
{{ fdata }}
{% endif %}
</td>
<td>
{% if fdata is mapping %}
{{ fdata.confidence }}%
{% else %}
—
{% endif %}
</td>
</tr>
{% endfor %}
</table>
{% else %}
<p style="color:#999">No fields extracted</p>
{% endif %}

<p style="margin-top:20px">
<a href="/" class="btn">← Back to Dashboard</a>
</p>
</div>
{% endif %}

</div></body></html>
HTMLEOF

# Add AWS_REGION to service environment
sed -i '/Environment="S3_BUCKET=/a Environment="AWS_REGION=ap-southeast-1"' /etc/systemd/system/idp-web.service

# Reload and restart
systemctl daemon-reload
systemctl restart idp-web

echo "=== Web app updated successfully ==="
REMOTE_SCRIPT

echo "✓ Update complete! Check http://$WEB_IP/upload"
