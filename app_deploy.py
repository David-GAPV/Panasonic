import os, json, requests, tempfile
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS
import psycopg2, psycopg2.extras
import boto3

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)
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
        cur.execute("""SELECT d.*, COUNT(vr.id) FILTER (WHERE NOT vr.passed) AS failed_checks
            FROM documents d LEFT JOIN validation_results vr ON vr.document_id = d.id
            GROUP BY d.id ORDER BY d.created_at DESC LIMIT 50""")
        docs = cur.fetchall()
        cur.execute("SELECT status, COUNT(*) cnt FROM documents GROUP BY status")
        stats = {r['status']: r['cnt'] for r in cur.fetchall()}
        conn.close()
    except Exception as e:
        docs, stats = [], {"error": str(e)}
    return render_template("dashboard.html", docs=docs, stats=stats)

def process_one_file(file):
    """Process a single uploaded file: S3 upload + OCR + DB save. Returns (result_dict, error_string)."""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name
        s3_key = f"uploads/{file.filename}"
        s3_client.upload_file(tmp_path, S3_BUCKET, s3_key)
        with open(tmp_path, 'rb') as f:
            resp = requests.post(f"{OCR_API}/extract",
                files={"file": (file.filename, f, file.content_type or 'application/octet-stream')},
                data={"lang": "eng+vie"}, timeout=120)
        os.unlink(tmp_path)
        if resp.status_code == 200:
            result = resp.json()
            conn = get_db()
            cur = conn.cursor()
            doc_id = result.get('document_id', file.filename)
            cur.execute("""INSERT INTO documents (doc_id, doc_type, type_confidence, filename, uploaded_by, status, original_s3_key)
                VALUES (%s,%s,%s,%s,%s,'extracted',%s) ON CONFLICT (doc_id) DO UPDATE SET status='extracted', updated_at=NOW() RETURNING id""",
                (doc_id, result.get("document_type","unknown"), result.get("type_confidence",0), file.filename, "web_user", s3_key))
            doc_db_id = cur.fetchone()[0]
            cur.execute("DELETE FROM extracted_fields WHERE document_id=%s", (doc_db_id,))
            for fname, fdata in result.get("fields",{}).items():
                val = str(fdata.get("value","")) if isinstance(fdata, dict) else str(fdata)
                conf = fdata.get("confidence",0) if isinstance(fdata, dict) else 0
                cur.execute("INSERT INTO extracted_fields (document_id, field_name, field_value, confidence) VALUES (%s,%s,%s,%s)",
                    (doc_db_id, fname, val, conf))
            conn.commit(); conn.close()
            return result, None
        else:
            return None, f"OCR API error: {resp.status_code} - {resp.text[:200]}"
    except requests.exceptions.ConnectionError:
        return None, "OCR API not reachable. Service may still be starting."
    except Exception as e:
        return None, f"Error: {str(e)}"

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "GET":
        return render_template("upload.html")

    files = request.files.getlist('files')
    if not files or (len(files) == 1 and files[0].filename == ''):
        return jsonify({"error": "No file selected"}), 400

    # Single file upload (JS sends one at a time for progress tracking)
    result, error = process_one_file(files[0])
    if error:
        return jsonify({"error": error}), 200
    return jsonify({"result": result})

@app.route("/review")
def review_queue():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT d.*, COUNT(vr.id) FILTER (WHERE NOT vr.passed) AS failed_checks
            FROM documents d LEFT JOIN validation_results vr ON vr.document_id = d.id
            WHERE d.status IN ('flagged','reviewing','extracted')
            GROUP BY d.id ORDER BY d.created_at DESC""")
        docs = cur.fetchall()
        conn.close()
    except:
        docs = []
    return render_template("review_queue.html", docs=docs)

@app.route("/document/<doc_id>")
def document_detail(doc_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM documents WHERE doc_id=%s", (doc_id,))
        doc = cur.fetchone()
        if not doc: return "Not found", 404
        cur.execute("SELECT * FROM extracted_fields WHERE document_id=%s ORDER BY field_name", (doc['id'],))
        fields = cur.fetchall()
        cur.execute("SELECT * FROM validation_results WHERE document_id=%s", (doc['id'],))
        validations = cur.fetchall()
        cur.execute("SELECT * FROM audit_log WHERE document_id=%s ORDER BY logged_at DESC", (doc['id'],))
        audit = cur.fetchall()
        conn.close()
    except Exception as e:
        return f"Error: {e}", 500
    return render_template("document_detail.html", doc=doc, fields=fields, validations=validations, audit=audit)

@app.route("/document/<doc_id>/approve", methods=["POST"])
def approve_document(doc_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM documents WHERE doc_id=%s", (doc_id,))
        doc = cur.fetchone()
        if not doc: return jsonify({"error": "Not found"}), 404
        cur.execute("UPDATE documents SET status='completed', updated_at=NOW() WHERE doc_id=%s", (doc_id,))
        cur.execute("SELECT * FROM extracted_fields WHERE document_id=%s", (doc['id'],))
        fields = {f['field_name']: f['corrected_value'] or f['field_value'] for f in cur.fetchall()}
        payload = json.dumps({"doc_id": doc_id, "doc_type": doc['doc_type'], "fields": fields})
        cur.execute("INSERT INTO audit_log (document_id, action, new_value) VALUES (%s, 'approved_sap_push', %s)", (doc['id'], payload))
        conn.commit(); conn.close()
        return jsonify({"message": f"Document {doc_id} approved and sent to SAP", "success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/document/<doc_id>/reject", methods=["POST"])
def reject_document(doc_id):
    try:
        data = request.get_json() or {}
        reason = data.get("reason", "No reason")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM documents WHERE doc_id=%s", (doc_id,))
        row = cur.fetchone()
        cur.execute("UPDATE documents SET status='rejected', updated_at=NOW() WHERE doc_id=%s", (doc_id,))
        if row:
            cur.execute("INSERT INTO audit_log (document_id, action, new_value) VALUES (%s, 'rejected', %s)", (row[0], reason))
        conn.commit(); conn.close()
        return jsonify({"message": f"Document {doc_id} rejected", "success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/document/<doc_id>/update_field", methods=["POST"])
def update_field(doc_id):
    try:
        data = request.get_json()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE extracted_fields SET corrected_value=%s, reviewed=TRUE WHERE id=%s", (data['value'], data['field_id']))
        conn.commit(); conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/sap/simulate")
def sap_simulate():
    sap_logs, erp_entries, wms_entries = [], [], []
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # SAP push log
        cur.execute("""SELECT al.*, d.doc_id, d.doc_type FROM audit_log al
            LEFT JOIN documents d ON d.id = al.document_id
            WHERE al.action='approved_sap_push' ORDER BY al.logged_at DESC""")
        sap_logs = cur.fetchall()
        # Build ERP and WMS entries from approved docs
        for log in sap_logs:
            try:
                payload = json.loads(log['new_value']) if log['new_value'] else {}
            except:
                payload = {}
            fields = payload.get('fields', {})
            doc_type = log.get('doc_type') or payload.get('doc_type', '')
            entry = {
                'doc_id': log.get('doc_id', ''),
                'doc_type': doc_type,
                'fields': fields,
                'posted_at': log['logged_at'].strftime('%d %b %Y %H:%M') if log.get('logged_at') else '',
                'sap_doc_no': f"5100{log['id']:06d}",
                'mat_doc_no': f"4900{log['id']:06d}",
            }
            if doc_type in ('invoice', 'packing_list'):
                erp_entries.append(entry)
            if doc_type in ('warehouse_receipt', 'bill_of_lading'):
                wms_entries.append(entry)
            # If unknown type, add to both
            if doc_type not in ('invoice', 'packing_list', 'warehouse_receipt', 'bill_of_lading'):
                erp_entries.append(entry)
        conn.close()
    except:
        pass
    return render_template("sap_simulation.html", sap_logs=sap_logs, erp_entries=erp_entries, wms_entries=wms_entries)

@app.route("/api/status")
def api_status():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT status, COUNT(*) cnt FROM documents GROUP BY status")
        stats = {r['status']: r['cnt'] for r in cur.fetchall()}
        conn.close()
        db_ok = True
    except:
        stats, db_ok = {}, False
    return jsonify({"db": db_ok, "document_stats": stats})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
