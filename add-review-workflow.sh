#!/bin/bash
# Add human review/approval workflow and SAP simulation
set -e

WEB_IP="13.214.12.26"
KEY="deploy/idp-panasonic-key.pem"

echo "=== Adding Review Workflow & SAP Integration ==="

ssh -i "$KEY" -o StrictHostKeyChecking=no ec2-user@$WEB_IP 'sudo bash -s' <<'REMOTE_SCRIPT'
# Stop the service
systemctl stop idp-web

# Create enhanced Flask app with review workflow
cat > /opt/idp-web/app.py <<'PYEOF'
import os, json, requests, tempfile
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_cors import CORS
import psycopg2, psycopg2.extras
import boto3

app = Flask(__name__)
CORS(app)
app.secret_key = 'panasonic-idp-secret-2026'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

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
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
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
    return render_template("dashboard.html", docs=docs, stats=stats)

@app.route("/document/<doc_id>")
def document_detail(doc_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM documents WHERE doc_id=%s", (doc_id,))
        doc = cur.fetchone()
        if not doc:
            return "Document not found", 404
        
        cur.execute("SELECT * FROM extracted_fields WHERE document_id=%s ORDER BY field_name", (doc["id"],))
        fields = cur.fetchall()
        cur.execute("SELECT * FROM validation_results WHERE document_id=%s", (doc["id"],))
        validations = cur.fetchall()
        cur.execute("SELECT * FROM audit_log WHERE document_id=%s ORDER BY logged_at DESC LIMIT 20", (doc["id"],))
        audit = cur.fetchall()
        conn.close()
        
        # Get S3 presigned URL if available
        s3_url = None
        if doc.get('original_s3_key'):
            try:
                s3_url = s3_client.generate_presigned_url('get_object',
                    Params={'Bucket': S3_BUCKET, 'Key': doc['original_s3_key']},
                    ExpiresIn=3600)
            except:
                pass
        
        return render_template("document_detail.html", doc=doc, fields=fields,
                             validations=validations, audit=audit, s3_url=s3_url)
    except Exception as e:
        return f"<pre>Error: {e}</pre>", 500

@app.route("/review")
def review_queue():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT d.*, COUNT(vr.id) FILTER (WHERE NOT vr.passed) AS failed_checks
            FROM documents d
            LEFT JOIN validation_results vr ON vr.document_id = d.id
            WHERE d.status IN ('flagged', 'reviewing', 'extracted')
            GROUP BY d.id ORDER BY d.created_at ASC
        """)
        docs = cur.fetchall()
        conn.close()
    except Exception as e:
        docs = []
    return render_template("review_queue.html", docs=docs)

@app.route("/document/<doc_id>/approve", methods=["POST"])
def approve_document(doc_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Update document status
        cur.execute("""
            UPDATE documents SET status='auto_approved', updated_at=NOW()
            WHERE doc_id=%s RETURNING id
        """, (doc_id,))
        doc_db_id = cur.fetchone()[0]
        
        # Log approval
        cur.execute("""
            INSERT INTO audit_log (document_id, user_id, action, new_value)
            VALUES (%s, %s, %s, %s)
        """, (doc_db_id, 'reviewer_web', 'approved', 'Status → auto_approved'))
        
        # Simulate SAP integration
        cur.execute("SELECT * FROM documents WHERE id=%s", (doc_db_id,))
        doc = cur.fetchone()
        
        sap_payload = {
            "document_id": doc_id,
            "document_type": doc[3] if len(doc) > 3 else "unknown",
            "status": "approved",
            "timestamp": datetime.now().isoformat()
        }
        
        # Log SAP push
        cur.execute("""
            INSERT INTO audit_log (document_id, user_id, action, new_value)
            VALUES (%s, %s, %s, %s)
        """, (doc_db_id, 'system', 'sap_push', f'SAP payload: {json.dumps(sap_payload)}'))
        
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "message": "Document approved and sent to SAP"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/document/<doc_id>/reject", methods=["POST"])
def reject_document(doc_id):
    try:
        reason = request.json.get('reason', 'No reason provided')
        conn = get_db()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE documents SET status='rejected', updated_at=NOW()
            WHERE doc_id=%s RETURNING id
        """, (doc_id,))
        doc_db_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO audit_log (document_id, user_id, action, new_value)
            VALUES (%s, %s, %s, %s)
        """, (doc_db_id, 'reviewer_web', 'rejected', f'Reason: {reason}'))
        
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "message": "Document rejected"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/document/<doc_id>/update_field", methods=["POST"])
def update_field(doc_id):
    try:
        field_id = request.json.get('field_id')
        new_value = request.json.get('value')
        
        conn = get_db()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE extracted_fields 
            SET corrected_value=%s, reviewed=TRUE 
            WHERE id=%s
            RETURNING document_id, field_name, field_value
        """, (new_value, field_id))
        
        result = cur.fetchone()
        if result:
            doc_db_id, field_name, old_value = result
            cur.execute("""
                INSERT INTO audit_log (document_id, user_id, action, old_value, new_value)
                VALUES (%s, %s, %s, %s, %s)
            """, (doc_db_id, 'reviewer_web', f'field_correction_{field_name}', old_value, new_value))
        
        conn.commit()
        conn.close()
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/sap/simulate")
def sap_simulation():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT al.*, d.doc_id, d.doc_type
            FROM audit_log al
            JOIN documents d ON d.id = al.document_id
            WHERE al.action = 'sap_push'
            ORDER BY al.logged_at DESC
            LIMIT 50
        """)
        sap_logs = cur.fetchall()
        conn.close()
    except Exception as e:
        sap_logs = []
    return render_template("sap_simulation.html", sap_logs=sap_logs)

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "GET":
        return render_template("upload.html")
    
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
                with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
                    file.save(tmp.name)
                    tmp_path = tmp.name
                
                s3_key = f"uploads/{file.filename}"
                s3_client.upload_file(tmp_path, S3_BUCKET, s3_key)
                
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
                    error = f"OCR API error: {resp.status_code}"
                    
            except requests.exceptions.ConnectionError:
                error = f"OCR API not reachable. Tesseract may still be compiling (~8 min)"
            except Exception as e:
                error = f"Error: {str(e)}"
    
    return render_template("upload.html", result=result, error=error)

@app.route("/api/status")
def api_status():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
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

echo "=== Flask app updated with review workflow ==="

# Restart service
systemctl restart idp-web
sleep 3

if systemctl is-active --quiet idp-web; then
    echo "✓ Service restarted successfully"
else
    echo "✗ Service failed to start, checking logs..."
    journalctl -u idp-web -n 20
fi

REMOTE_SCRIPT

echo ""
echo "✓ Review workflow added!"
echo "  - Review Queue: http://$WEB_IP/review"
echo "  - SAP Simulation: http://$WEB_IP/sap/simulate"
echo "  - Document Details: http://$WEB_IP/document/<doc_id>"
