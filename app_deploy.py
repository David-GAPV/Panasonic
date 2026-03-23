import os, io, csv, json, re, hashlib, requests, tempfile, logging, functools
from datetime import datetime
from flask import Flask, render_template, render_template_string, request, jsonify, redirect, url_for, Response, send_file, session
from flask_cors import CORS
import psycopg2, psycopg2.extras
import boto3

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.secret_key = os.getenv("SECRET_KEY", "idp-panasonic-secret-2025-change-me")

@app.context_processor
def inject_user_context():
    """Make user/role available in all templates."""
    return dict(user_role=session.get("role", ""), user_name=session.get("name", ""))

@app.context_processor
def inject_user_context():
    return dict(
        user_role=session.get("role", ""),
        user_name=session.get("name", ""),
        username=session.get("username", ""),
        timedelta=__import__('datetime').timedelta,
    )

DB_CFG = dict(host=os.getenv("RDS_ENDPOINT"), dbname=os.getenv("DB_NAME"),
              user=os.getenv("DB_USER"), password=os.getenv("DB_PASS"), connect_timeout=5)
OCR_API = f"http://{os.getenv('OCR_IP')}:8000"
S3_BUCKET = os.getenv("S3_BUCKET")
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-1")
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "arn:aws:sns:ap-southeast-1:853878127521:idp-panasonic-notifications")
s3_client = boto3.client('s3', region_name=AWS_REGION)
sns_client = boto3.client('sns', region_name=AWS_REGION)

# --- Hardcoded users (simple auth) ---
USERS = {
    "admin":    {"password_hash": hashlib.sha256("admin@IDP2025".encode()).hexdigest(), "role": "admin", "name": "Administrator"},
    "reviewer": {"password_hash": hashlib.sha256("review@IDP2025".encode()).hexdigest(), "role": "reviewer", "name": "Reviewer"},
    "uploader": {"password_hash": hashlib.sha256("upload@IDP2025".encode()).hexdigest(), "role": "uploader", "name": "Uploader"},
    "david":    {"password_hash": hashlib.sha256("david@IDP2025".encode()).hexdigest(), "role": "admin", "name": "David"},
}

def get_db():
    return psycopg2.connect(**DB_CFG)

def current_user():
    return session.get("username", None)

def current_role():
    return session.get("role", None)

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def role_required(*allowed_roles):
    """Decorator to restrict route access by role. Usage: @role_required('admin','reviewer')"""
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("username"):
                return redirect(url_for("login"))
            if session.get("role") not in allowed_roles:
                if request.headers.get('Accept', '').find('json') >= 0 or request.is_json:
                    return jsonify({"error": "Access denied. Your role does not have permission for this action."}), 403
                return render_template_string("""<!DOCTYPE html><html><head><title>Access Denied</title>
<style>body{font-family:'Helvetica Neue',Arial,sans-serif;background:#f5f5f5;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}
.box{background:#fff;padding:48px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);text-align:center;max-width:400px}
h1{color:#d32f2f;font-size:48px;margin-bottom:8px}p{color:#666;margin-bottom:24px}
a{color:#2D4285;text-decoration:none;padding:10px 24px;border:1px solid #2D4285;border-radius:4px}a:hover{background:#2D4285;color:#fff}</style>
</head><body><div class="box"><h1>403</h1><p>Your role <strong>({{ session.get('role','unknown') }})</strong> does not have access to this page.</p><a href="/">Back to Dashboard</a></div></body></html>"""), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

def audit_log(cur, document_id, action, detail="", user=None):
    """Write to audit_log table."""
    u = user or current_user() or "system"
    cur.execute("INSERT INTO audit_log (document_id, action, new_value, user_id) VALUES (%s,%s,%s,%s)",
                (document_id, action, detail, u))

def send_notification(subject, message):
    """Send SNS email notification (best-effort)."""
    try:
        sns_client.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject[:100], Message=message)
    except Exception as e:
        log.warning(f"SNS notification failed: {e}")

def cross_verify_document(cur, doc_db_id, doc_type):
    """FR010: Cross-check extracted data against related documents in the same shipment set.
    Documents are grouped by shipment_ref (user-provided at upload time).
    Only compares within the same shipment_ref group."""
    # Check if this document has a shipment_ref
    cur.execute("SELECT shipment_ref FROM documents WHERE id=%s", (doc_db_id,))
    row = cur.fetchone()
    shipment_ref = (row['shipment_ref'] or '').strip() if row else ''
    if not shipment_ref:
        return []  # No shipment reference — skip cross-verify

    # Get current document's extracted fields
    cur.execute("SELECT field_name, COALESCE(corrected_value, field_value) AS val FROM extracted_fields WHERE document_id=%s", (doc_db_id,))
    my_fields = {r['field_name']: r['val'] for r in cur.fetchall()}

    # Find related documents in the same shipment set (not self)
    cur.execute("SELECT id FROM documents WHERE shipment_ref=%s AND id != %s", (shipment_ref, doc_db_id))
    related_ids = {r['id'] for r in cur.fetchall()}

    if not related_ids:
        return []  # No related documents in this shipment yet

    # Fields to compare across documents
    compare_fields = ['supplier_name', 'buyer_name', 'total_amount', 'container_no',
                      'vessel', 'port_of_loading', 'port_of_discharge', 'gross_weight',
                      'net_weight', 'total_packages', 'currency']
    mismatches = []

    for rel_id in related_ids:
        cur.execute("SELECT doc_id, doc_type FROM documents WHERE id=%s", (rel_id,))
        rel_doc = cur.fetchone()
        if not rel_doc:
            continue
        cur.execute("SELECT field_name, COALESCE(corrected_value, field_value) AS val FROM extracted_fields WHERE document_id=%s", (rel_id,))
        rel_fields = {r['field_name']: r['val'] for r in cur.fetchall()}

        for fname in compare_fields:
            my_val = my_fields.get(fname, '').strip()
            rel_val = rel_fields.get(fname, '').strip()
            if not my_val or not rel_val:
                continue
            # Smart comparison for multi-value fields (container_no, hs_codes)
            if fname in ('container_no', 'hs_codes'):
                my_parts = set(p.strip().lower() for p in re.split(r'[/,;]', my_val) if p.strip())
                rel_parts = set(p.strip().lower() for p in re.split(r'[/,;]', rel_val) if p.strip())
                # If one is a subset of the other, it's not a real mismatch
                if my_parts <= rel_parts or rel_parts <= my_parts:
                    continue
            # Smart comparison for total_packages: ignore unit text ("200 cartons" vs "200")
            elif fname == 'total_packages':
                my_num = re.sub(r'[^\d]', '', my_val)
                rel_num = re.sub(r'[^\d]', '', rel_val)
                if my_num == rel_num:
                    continue
            elif my_val.lower() == rel_val.lower():
                continue
            mismatches.append({
                'field': fname,
                'my_val': my_val,
                'rel_doc_id': rel_doc['doc_id'],
                'rel_doc_type': rel_doc['doc_type'],
                'rel_val': rel_val,
            })

    # Store results
    results = []
    for m in mismatches:
        check_name = f"xref_{m['field']}"
        detail = f"Mismatch: '{m['my_val']}' vs '{m['rel_val']}' in {m['rel_doc_type']} ({m['rel_doc_id']})"
        cur.execute("INSERT INTO validation_results (document_id, check_type, check_name, passed, detail) VALUES (%s,%s,%s,%s,%s)",
            (doc_db_id, 'cross_verify', check_name, False, detail))
        results.append((check_name, False, detail))

    if mismatches:
        cur.execute("UPDATE documents SET status='flagged' WHERE id=%s", (doc_db_id,))
        summary = f"Cross-verification: {len(mismatches)} mismatch(es) found against related documents"
        audit_log(cur, doc_db_id, 'cross_verify_failed', summary)
        send_notification(
            f"[IDP] Cross-Verification Mismatch: document #{doc_db_id}",
            f"Cross-document verification found {len(mismatches)} discrepancy(ies).\n\n" +
            "\n".join(f"- {m['field']}: '{m['my_val']}' vs '{m['rel_val']}' ({m['rel_doc_id']})" for m in mismatches) +
            f"\n\nReview at: https://idp.pngha.io.vn/")

    return results


def validate_fields(fields_dict, doc_type):
    """Run business rule validation on extracted fields. Returns list of (check_name, passed, detail)."""
    results = []
    fmap = {}
    for f in fields_dict:
        fmap[f['field_name']] = f['corrected_value'] or f['field_value']

    # Mandatory fields by doc type
    mandatory = {
        'invoice': ['invoice_number', 'date', 'total_amount', 'supplier_name'],
        'packing_list': ['packing_list_no', 'total_packages'],
        'bill_of_lading': ['bl_number', 'vessel', 'port_of_loading', 'port_of_discharge'],
        'warehouse_receipt': ['wh_receipt_no', 'date'],
    }
    required = mandatory.get(doc_type, ['date'])
    for field in required:
        present = bool(fmap.get(field, '').strip())
        results.append((f"mandatory_{field}", present,
                        f"{field} is present" if present else f"{field} is MISSING (required for {doc_type})"))

    # Date format check
    date_val = fmap.get('date', '')
    if date_val:
        date_ok = bool(re.search(r'\d{1,2}[\s/\-\.]\w+[\s/\-\.]\d{2,4}', date_val))
        results.append(("date_format", date_ok,
                        f"Date '{date_val}' format OK" if date_ok else f"Date '{date_val}' has unexpected format"))

    # Amount is numeric
    amt = fmap.get('total_amount', '')
    if amt:
        amt_clean = amt.replace(',', '').replace(' ', '')
        amt_ok = bool(re.match(r'^\d+\.?\d*$', amt_clean))
        results.append(("amount_numeric", amt_ok,
                        f"Amount '{amt}' is numeric" if amt_ok else f"Amount '{amt}' is not a valid number"))

    # Invoice number format
    inv = fmap.get('invoice_number', '')
    if inv:
        inv_ok = bool(re.match(r'^[A-Z0-9][\w\-/]{4,}$', inv))
        results.append(("invoice_number_format", inv_ok,
                        f"Invoice number '{inv}' format OK" if inv_ok else f"Invoice number '{inv}' has unexpected format"))

    return results

def validate_reference_data(cur, fields_dict, doc_type):
    """FR011: Validate extracted fields against master reference tables (suppliers, HS codes, ports)."""
    results = []
    fmap = {f['field_name']: (f['corrected_value'] or f['field_value']) for f in fields_dict}

    # Supplier name check (invoice and packing list)
    if doc_type in ('invoice', 'packing_list'):
        supplier = fmap.get('supplier_name', '').strip()
        if supplier:
            norm = re.sub(r'[^\w\s]', '', supplier.lower()).strip()
            cur.execute("SELECT name FROM ref_suppliers WHERE active=TRUE AND name_normalized ILIKE %s LIMIT 1",
                        (f'%{norm[:30]}%',))
            row = cur.fetchone()
            if row:
                results.append(('ref_supplier', True, f"Supplier '{supplier}' found in approved supplier list"))
            else:
                results.append(('ref_supplier', False, f"Supplier '{supplier}' NOT found in approved supplier list"))

    # Port of loading check (invoice, bill_of_lading)
    if doc_type in ('invoice', 'bill_of_lading'):
        pol = fmap.get('port_of_loading', '').strip()
        if pol:
            norm = re.sub(r'[^\w\s]', '', pol.lower()).strip()
            cur.execute("SELECT port_name FROM ref_ports WHERE active=TRUE AND %s ILIKE CONCAT('%%', port_name_normalized, '%%') LIMIT 1",
                        (norm,))
            row = cur.fetchone()
            if row:
                results.append(('ref_port_loading', True, f"Port of loading '{pol}' found in approved port list"))
            else:
                results.append(('ref_port_loading', False, f"Port of loading '{pol}' NOT found in approved port list"))

    # Port of discharge check (invoice, bill_of_lading)
    if doc_type in ('invoice', 'bill_of_lading'):
        pod = fmap.get('port_of_discharge', '').strip()
        if pod:
            norm = re.sub(r'[^\w\s]', '', pod.lower()).strip()
            cur.execute("SELECT port_name FROM ref_ports WHERE active=TRUE AND %s ILIKE CONCAT('%%', port_name_normalized, '%%') LIMIT 1",
                        (norm,))
            row = cur.fetchone()
            if row:
                results.append(('ref_port_discharge', True, f"Port of discharge '{pod}' found in approved port list"))
            else:
                results.append(('ref_port_discharge', False, f"Port of discharge '{pod}' NOT found in approved port list"))

    # HS code check (invoice)
    if doc_type == 'invoice':
        hs = fmap.get('hs_code', '').strip()
        if hs:
            cur.execute("SELECT hs_code FROM ref_hs_codes WHERE active=TRUE AND hs_code=%s LIMIT 1", (hs,))
            row = cur.fetchone()
            if row:
                results.append(('ref_hs_code', True, f"HS code '{hs}' found in approved HS code list"))
            else:
                results.append(('ref_hs_code', False, f"HS code '{hs}' NOT found in approved HS code list"))

    return results

# ============================================================
# AUTH ROUTES
# ============================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        pw_hash = hashlib.sha256(password.encode()).hexdigest()
        user = USERS.get(username)
        if user and user["password_hash"] == pw_hash:
            session["username"] = username
            session["role"] = user["role"]
            session["name"] = user["name"]
            return redirect(url_for("dashboard"))
        error = "Invalid username or password"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ============================================================
# DASHBOARD
# ============================================================
@app.route("/")
@login_required
def dashboard():
    q = request.args.get('q', '').strip()
    status_filter = request.args.get('status', '').strip()
    doc_type_filter = request.args.get('doc_type', '').strip()
    sort_by = request.args.get('sort', 'newest')
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        where_clauses, params = [], []
        if q:
            where_clauses.append("(d.doc_id ILIKE %s OR d.filename ILIKE %s)")
            params += [f"%{q}%", f"%{q}%"]
        if status_filter:
            where_clauses.append("d.status = %s")
            params.append(status_filter)
        if doc_type_filter:
            where_clauses.append("d.doc_type = %s")
            params.append(doc_type_filter)
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        order_sql = "d.created_at ASC" if sort_by == 'oldest' else "d.created_at DESC"
        cur.execute(f"""SELECT d.*, COUNT(vr.id) FILTER (WHERE NOT vr.passed) AS failed_checks
            FROM documents d LEFT JOIN validation_results vr ON vr.document_id = d.id
            {where_sql} GROUP BY d.id ORDER BY {order_sql} LIMIT 100""", params)
        docs = cur.fetchall()
        cur.execute("SELECT status, COUNT(*) cnt FROM documents GROUP BY status")
        stats = {r['status']: r['cnt'] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT doc_type FROM documents WHERE doc_type IS NOT NULL ORDER BY doc_type")
        doc_types = [r['doc_type'] for r in cur.fetchall()]
        # KPIs
        cur.execute("SELECT COUNT(*) as c FROM documents")
        total_docs = cur.fetchone()['c']
        cur.execute("SELECT COALESCE(AVG(type_confidence),0) as a FROM documents")
        avg_conf = round(float(cur.fetchone()['a']), 1)
        cur.execute("SELECT COUNT(*) as c FROM documents WHERE created_at::date = CURRENT_DATE")
        today_count = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM documents WHERE status='flagged'")
        flagged_count = cur.fetchone()['c']
        conn.close()
    except Exception as e:
        docs, stats, doc_types = [], {"error": str(e)}, []
        total_docs, avg_conf, today_count, flagged_count = 0, 0, 0, 0
    return render_template("dashboard.html", docs=docs, stats=stats, doc_types=doc_types,
                           q=q, status_filter=status_filter, doc_type_filter=doc_type_filter,
                           sort_by=sort_by,
                           total_docs=total_docs, avg_conf=avg_conf, today_count=today_count,
                           flagged_count=flagged_count, user=session.get("name",""), role=session.get("role",""))

# ============================================================
# UPLOAD
# ============================================================
def process_one_file(file, shipment_ref=None):
    """Process a single uploaded file: S3 upload + OCR + DB save + validation + notification."""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name
        s3_key = f"uploads/{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
        s3_client.upload_file(tmp_path, S3_BUCKET, s3_key)
        with open(tmp_path, 'rb') as f:
            resp = requests.post(f"{OCR_API}/extract",
                files={"file": (file.filename, f, file.content_type or 'application/octet-stream')},
                data={"lang": "eng+vie"}, timeout=120)
        os.unlink(tmp_path)
        if resp.status_code == 200:
            result = resp.json()
            conn = get_db()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Always create a unique doc_id so re-uploads of same filename create new records
            base_id = result.get('document_id', file.filename.rsplit('.', 1)[0])
            timestamp_suffix = datetime.now().strftime('%Y%m%d%H%M%S')
            doc_id = f"{base_id}_{timestamp_suffix}"
            cur.execute("""INSERT INTO documents (doc_id, doc_type, type_confidence, filename, uploaded_by, status, original_s3_key, shipment_ref)
                VALUES (%s,%s,%s,%s,%s,'extracted',%s,%s) RETURNING id""",
                (doc_id, result.get("document_type","unknown"), result.get("type_confidence",0),
                 file.filename, current_user() or "web_user", s3_key, shipment_ref or None))
            doc_db_id = cur.fetchone()['id']
            # Audit: upload
            audit_log(cur, doc_db_id, 'uploaded', f"File: {file.filename}, Type: {result.get('document_type','unknown')}")
            cur.execute("DELETE FROM extracted_fields WHERE document_id=%s", (doc_db_id,))
            low_conf_fields = []
            for fname, fdata in result.get("fields",{}).items():
                val = str(fdata.get("value","")) if isinstance(fdata, dict) else str(fdata)
                conf = fdata.get("confidence",0) if isinstance(fdata, dict) else 0
                cur.execute("INSERT INTO extracted_fields (document_id, field_name, field_value, confidence) VALUES (%s,%s,%s,%s)",
                    (doc_db_id, fname, val, conf))
                if conf < 70:
                    low_conf_fields.append(fname)
            # Flag if low confidence
            type_conf = result.get("type_confidence", 0)
            if low_conf_fields or type_conf < 70:
                reason = []
                if type_conf < 70:
                    reason.append(f"Document type confidence {type_conf}% < 70%")
                if low_conf_fields:
                    reason.append(f"Low confidence fields: {', '.join(low_conf_fields)}")
                cur.execute("UPDATE documents SET status='flagged' WHERE id=%s", (doc_db_id,))
                audit_log(cur, doc_db_id, 'auto_flagged', '; '.join(reason))
                # SNS notification for flagged doc
                send_notification(
                    f"[IDP] Document Flagged: {doc_id}",
                    f"Document {doc_id} ({file.filename}) has been flagged for review.\n\nReason: {'; '.join(reason)}\n\nReview at: https://idp.pngha.io.vn/document/{doc_id}")
            # Run business rule validation
            cur.execute("SELECT * FROM extracted_fields WHERE document_id=%s", (doc_db_id,))
            all_fields = cur.fetchall()
            cur.execute("DELETE FROM validation_results WHERE document_id=%s", (doc_db_id,))
            checks = validate_fields(all_fields, result.get("document_type","unknown"))
            for check_name, passed, detail in checks:
                check_type = check_name.split('_')[0] if '_' in check_name else 'business_rule'
                cur.execute("INSERT INTO validation_results (document_id, check_type, check_name, passed, detail) VALUES (%s,%s,%s,%s,%s)",
                    (doc_db_id, check_type, check_name, passed, detail))
            failed_checks = [c for c in checks if not c[1]]
            if failed_checks:
                cur.execute("UPDATE documents SET status='flagged' WHERE id=%s", (doc_db_id,))
                audit_log(cur, doc_db_id, 'validation_failed',
                    f"{len(failed_checks)} check(s) failed: {', '.join(c[0] for c in failed_checks)}")
            # FR011: Reference data validation
            try:
                ref_checks = validate_reference_data(cur, all_fields, result.get("document_type","unknown"))
                for check_name, passed, detail in ref_checks:
                    cur.execute("INSERT INTO validation_results (document_id, check_type, check_name, passed, detail) VALUES (%s,%s,%s,%s,%s)",
                        (doc_db_id, 'reference', check_name, passed, detail))
                ref_failed = [c for c in ref_checks if not c[1]]
                if ref_failed:
                    cur.execute("UPDATE documents SET status='flagged' WHERE id=%s", (doc_db_id,))
                    audit_log(cur, doc_db_id, 'reference_validation_failed',
                        f"{len(ref_failed)} reference check(s) failed: {', '.join(c[0] for c in ref_failed)}")
            except Exception as ref_err:
                log.warning(f"Reference validation error: {ref_err}")
            # FR010: Cross-document verification
            try:
                xref_results = cross_verify_document(cur, doc_db_id, result.get("document_type","unknown"))
                if xref_results:
                    log.info(f"Cross-verify: {len(xref_results)} mismatch(es) for doc {doc_id}")
            except Exception as xref_err:
                log.warning(f"Cross-verify error: {xref_err}")
            conn.commit(); conn.close()
            return result, None
        else:
            return None, f"OCR API error: {resp.status_code} - {resp.text[:200]}"
    except requests.exceptions.ConnectionError:
        return None, "OCR API not reachable. Service may still be starting."
    except Exception as e:
        return None, f"Error: {str(e)}"

@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "GET":
        return render_template("upload.html")
    files = request.files.getlist('files')
    if not files or (len(files) == 1 and files[0].filename == ''):
        return jsonify({"error": "No file selected"}), 400
    shipment_ref = request.form.get('shipment_ref', '').strip() or None
    result, error = process_one_file(files[0], shipment_ref=shipment_ref)
    if error:
        return jsonify({"error": error}), 200
    return jsonify({"result": result})

# ============================================================
# REVIEW & DOCUMENT DETAIL
# ============================================================
@app.route("/review")
@role_required('admin', 'reviewer')
def review_queue():
    sort_by = request.args.get('sort', 'newest')
    order_sql = "d.created_at ASC" if sort_by == 'oldest' else "d.created_at DESC"
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"""SELECT d.*, COUNT(vr.id) FILTER (WHERE NOT vr.passed) AS failed_checks
            FROM documents d LEFT JOIN validation_results vr ON vr.document_id = d.id
            WHERE d.status IN ('flagged','reviewing','extracted')
            GROUP BY d.id ORDER BY {order_sql}""")
        docs = cur.fetchall()
        conn.close()
    except:
        docs = []
    return render_template("review_queue.html", docs=docs, sort_by=sort_by)

@app.route("/document/<doc_id>")
@login_required
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
        # Generate presigned URL for document preview (only for previewable types)
        preview_url = None
        if doc.get('original_s3_key'):
            fname = (doc.get('filename') or '').lower()
            if fname.endswith('.pdf'):
                try:
                    preview_url = s3_client.generate_presigned_url('get_object',
                        Params={'Bucket': S3_BUCKET, 'Key': doc['original_s3_key'],
                                'ResponseContentDisposition': 'inline',
                                'ResponseContentType': 'application/pdf'}, ExpiresIn=3600)
                except:
                    pass
            elif fname.endswith(('.png', '.jpg', '.jpeg', '.tiff', '.tif', '.webp')):
                content_types = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg',
                                 '.tiff':'image/tiff','.tif':'image/tiff','.webp':'image/webp'}
                ext = '.' + fname.rsplit('.', 1)[-1]
                ct = content_types.get(ext, 'image/png')
                try:
                    preview_url = s3_client.generate_presigned_url('get_object',
                        Params={'Bucket': S3_BUCKET, 'Key': doc['original_s3_key'],
                                'ResponseContentDisposition': 'inline',
                                'ResponseContentType': ct}, ExpiresIn=3600)
                except:
                    pass
        conn.close()
    except Exception as e:
        return f"Error: {e}", 500
    return render_template("document_detail.html", doc=doc, fields=fields,
                           validations=validations, audit=audit, preview_url=preview_url)

@app.route("/document/<doc_id>/approve", methods=["POST"])
@role_required('admin', 'reviewer')
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
        audit_log(cur, doc['id'], 'approved_sap_push', payload)
        conn.commit(); conn.close()
        send_notification(f"[IDP] Document Approved: {doc_id}",
            f"Document {doc_id} has been approved and sent to SAP by {current_user()}.\n\nView: https://idp.pngha.io.vn/document/{doc_id}")
        return jsonify({"message": f"Document {doc_id} approved and sent to SAP", "success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/document/<doc_id>/reject", methods=["POST"])
@role_required('admin', 'reviewer')
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
            audit_log(cur, row[0], 'rejected', reason)
        conn.commit(); conn.close()
        send_notification(f"[IDP] Document Rejected: {doc_id}",
            f"Document {doc_id} has been rejected by {current_user()}.\n\nReason: {reason}\n\nView: https://idp.pngha.io.vn/document/{doc_id}")
        return jsonify({"message": f"Document {doc_id} rejected", "success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/document/<doc_id>/update_field", methods=["POST"])
@role_required('admin', 'reviewer')
def update_field(doc_id):
    try:
        data = request.get_json()
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Get old value for audit
        cur.execute("SELECT field_name, field_value, corrected_value FROM extracted_fields WHERE id=%s", (data['field_id'],))
        old = cur.fetchone()
        cur.execute("UPDATE extracted_fields SET corrected_value=%s, reviewed=TRUE WHERE id=%s", (data['value'], data['field_id']))
        new_status = None
        validations_html = None
        if old:
            old_val = old['corrected_value'] or old['field_value']
            cur.execute("SELECT id, doc_type, status, shipment_ref FROM documents WHERE doc_id=%s", (doc_id,))
            doc_row = cur.fetchone()
            if doc_row:
                audit_log(cur, doc_row['id'], 'field_corrected',
                    f"{old['field_name']}: '{old_val}' → '{data['value']}'")
                # Re-run ALL validation after field edit
                doc_db_id = doc_row['id']
                doc_type = doc_row['doc_type'] or 'unknown'
                cur.execute("SELECT * FROM extracted_fields WHERE document_id=%s", (doc_db_id,))
                all_fields = cur.fetchall()
                # Clear all old validation results
                cur.execute("DELETE FROM validation_results WHERE document_id=%s", (doc_db_id,))
                # Mandatory + business rule checks
                checks = validate_fields(all_fields, doc_type)
                for check_name, passed, detail in checks:
                    check_type = check_name.split('_')[0] if '_' in check_name else 'business_rule'
                    cur.execute("INSERT INTO validation_results (document_id, check_type, check_name, passed, detail) VALUES (%s,%s,%s,%s,%s)",
                        (doc_db_id, check_type, check_name, passed, detail))
                # Reference data checks
                ref_checks = validate_reference_data(cur, all_fields, doc_type)
                for check_name, passed, detail in ref_checks:
                    cur.execute("INSERT INTO validation_results (document_id, check_type, check_name, passed, detail) VALUES (%s,%s,%s,%s,%s)",
                        (doc_db_id, 'reference', check_name, passed, detail))
                # Cross-document verification
                xref_results = cross_verify_document(cur, doc_db_id, doc_type)
                # Check total failures and update status
                cur.execute("SELECT COUNT(*) as c FROM validation_results WHERE document_id=%s AND NOT passed", (doc_db_id,))
                total_failures = cur.fetchone()['c']
                if total_failures == 0 and doc_row['status'] == 'flagged':
                    cur.execute("UPDATE documents SET status='extracted' WHERE id=%s", (doc_db_id,))
                    audit_log(cur, doc_db_id, 'auto_unflagged', 'All validation checks now pass after field correction')
                    new_status = 'extracted'
                elif total_failures > 0 and doc_row['status'] not in ('approved', 'rejected', 'completed'):
                    cur.execute("UPDATE documents SET status='flagged' WHERE id=%s", (doc_db_id,))
                    new_status = 'flagged'
                else:
                    new_status = doc_row['status']
                # Fetch updated validations for response
                cur.execute("SELECT check_name, passed, detail FROM validation_results WHERE document_id=%s", (doc_db_id,))
                validations = cur.fetchall()
                # Fetch updated audit trail for response
                cur.execute("SELECT logged_at, user_id, action, new_value FROM audit_log WHERE document_id=%s ORDER BY logged_at DESC", (doc_db_id,))
                audit_entries = cur.fetchall()
        conn.commit(); conn.close()
        resp = {"success": True, "revalidated": True, "new_status": new_status,
                "validations": [dict(r) for r in validations] if 'validations' in dir() else [],
                "audit": [{"time": r['logged_at'].strftime('%d %b %Y %H:%M:%S') if r['logged_at'] else '',
                           "user": r['user_id'] or 'system',
                           "action": (r['action'] or '').replace('_', ' '),
                           "detail": r['new_value'] or ''} for r in audit_entries] if 'audit_entries' in dir() else []}
        return jsonify(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================
# SAP SIMULATION
# ============================================================
@app.route("/sap/simulate")
@role_required('admin')
def sap_simulate():
    sap_logs, erp_entries, wms_entries = [], [], []
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT al.*, d.doc_id, d.doc_type FROM audit_log al
            LEFT JOIN documents d ON d.id = al.document_id
            WHERE al.action='approved_sap_push' ORDER BY al.logged_at DESC""")
        sap_logs = cur.fetchall()
        for log_entry in sap_logs:
            try:
                payload = json.loads(log_entry['new_value']) if log_entry['new_value'] else {}
            except:
                payload = {}
            fields = payload.get('fields', {})
            doc_type = log_entry.get('doc_type') or payload.get('doc_type', '')
            entry = {
                'doc_id': log_entry.get('doc_id', ''),
                'doc_type': doc_type,
                'fields': fields,
                'posted_at': log_entry['logged_at'].strftime('%d %b %Y %H:%M') if log_entry.get('logged_at') else '',
                'sap_doc_no': f"5100{log_entry['id']:06d}",
                'mat_doc_no': f"4900{log_entry['id']:06d}",
            }
            if doc_type in ('invoice', 'packing_list'):
                erp_entries.append(entry)
            if doc_type in ('warehouse_receipt', 'bill_of_lading'):
                wms_entries.append(entry)
            if doc_type not in ('invoice', 'packing_list', 'warehouse_receipt', 'bill_of_lading'):
                erp_entries.append(entry)
        conn.close()
    except:
        pass
    return render_template("sap_simulation.html", sap_logs=sap_logs, erp_entries=erp_entries, wms_entries=wms_entries)

# ============================================================
# API / EXPORT / DOWNLOAD
# ============================================================
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

@app.route("/document/<doc_id>/download")
@login_required
def download_document(doc_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT original_s3_key, filename FROM documents WHERE doc_id=%s", (doc_id,))
        doc = cur.fetchone()
        conn.close()
        if not doc or not doc['original_s3_key']:
            return "Document not found", 404
        buf = io.BytesIO()
        s3_client.download_fileobj(S3_BUCKET, doc['original_s3_key'], buf)
        buf.seek(0)
        return send_file(buf, download_name=doc['filename'], as_attachment=True)
    except Exception as e:
        return f"Download error: {e}", 500

@app.route("/export/csv")
@role_required('admin', 'reviewer')
def export_csv():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT d.doc_id, d.doc_type, d.status, d.type_confidence, d.filename, d.created_at,
            ef.field_name, ef.field_value, ef.confidence AS field_confidence, ef.corrected_value
            FROM documents d LEFT JOIN extracted_fields ef ON ef.document_id = d.id
            ORDER BY d.created_at DESC, ef.field_name""")
        rows = cur.fetchall()
        conn.close()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Doc ID','Doc Type','Status','Type Confidence','Filename','Created At',
                         'Field Name','Field Value','Field Confidence','Corrected Value'])
        for r in rows:
            writer.writerow([r['doc_id'], r['doc_type'], r['status'], r['type_confidence'],
                r['filename'], r['created_at'], r.get('field_name',''), r.get('field_value',''),
                r.get('field_confidence',''), r.get('corrected_value','')])
        output.seek(0)
        return Response(output.getvalue(), mimetype='text/csv',
                        headers={"Content-Disposition": "attachment;filename=idp_export.csv"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/export/json")
@role_required('admin', 'reviewer')
def export_json():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM documents ORDER BY created_at DESC")
        docs = cur.fetchall()
        result = []
        for d in docs:
            cur.execute("SELECT field_name, field_value, confidence, corrected_value FROM extracted_fields WHERE document_id=%s", (d['id'],))
            fields = {f['field_name']: {"value": f['corrected_value'] or f['field_value'], "confidence": f['confidence']} for f in cur.fetchall()}
            result.append({"doc_id": d['doc_id'], "doc_type": d['doc_type'], "status": d['status'],
                "type_confidence": d['type_confidence'], "filename": d['filename'],
                "created_at": str(d['created_at']), "fields": fields})
        conn.close()
        return Response(json.dumps(result, indent=2, ensure_ascii=False), mimetype='application/json',
                        headers={"Content-Disposition": "attachment;filename=idp_export.json"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/analytics")
@role_required('admin', 'reviewer')
def error_analytics():
    """Error analytics dashboard — FR022."""
    error_by_type, error_by_doc_type, error_trend, recent_errors, correction_stats = [], [], [], [], []
    total_docs = total_errors = total_corrections = 0
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Error counts by check type (mandatory, date, amount, invoice, etc.)
        cur.execute("""SELECT check_type, COUNT(*) as cnt, COUNT(*) FILTER (WHERE NOT passed) as failed
            FROM validation_results GROUP BY check_type ORDER BY failed DESC""")
        error_by_type = cur.fetchall()
        # Errors by document type
        cur.execute("""SELECT d.doc_type, COUNT(vr.id) as total_checks,
            COUNT(vr.id) FILTER (WHERE NOT vr.passed) as failed_checks
            FROM validation_results vr JOIN documents d ON d.id = vr.document_id
            GROUP BY d.doc_type ORDER BY failed_checks DESC""")
        error_by_doc_type = cur.fetchall()
        # Error trend by date (last 30 days)
        cur.execute("""SELECT vr.checked_at::date as day, COUNT(*) FILTER (WHERE NOT vr.passed) as errors
            FROM validation_results vr
            WHERE vr.checked_at >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY day ORDER BY day""")
        error_trend = cur.fetchall()
        # Recent failed validations with document info
        cur.execute("""SELECT vr.check_type, vr.check_name, vr.detail, vr.checked_at,
            d.doc_id, d.doc_type
            FROM validation_results vr JOIN documents d ON d.id = vr.document_id
            WHERE NOT vr.passed ORDER BY vr.checked_at DESC LIMIT 30""")
        recent_errors = cur.fetchall()
        # Correction stats (how many fields were manually corrected)
        cur.execute("""SELECT d.doc_type, COUNT(*) as corrections
            FROM extracted_fields ef JOIN documents d ON d.id = ef.document_id
            WHERE ef.corrected_value IS NOT NULL AND ef.corrected_value != ef.field_value
            GROUP BY d.doc_type ORDER BY corrections DESC""")
        correction_stats = cur.fetchall()
        # Totals
        cur.execute("SELECT COUNT(*) as c FROM documents")
        total_docs = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM validation_results WHERE NOT passed")
        total_errors = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM extracted_fields WHERE corrected_value IS NOT NULL AND corrected_value != field_value")
        total_corrections = cur.fetchone()['c']
        conn.close()
    except Exception as e:
        log.error(f"Analytics error: {e}")
    return render_template("analytics.html", error_by_type=error_by_type, error_by_doc_type=error_by_doc_type,
        error_trend=error_trend, recent_errors=recent_errors, correction_stats=correction_stats,
        total_docs=total_docs, total_errors=total_errors, total_corrections=total_corrections,
        user=session.get("name",""))

@app.route("/report")
@role_required('admin', 'reviewer')
def report_page():
    """Report page — FR029."""
    return render_template("report.html", user=session.get("name",""))

@app.route("/report/pdf")
@role_required('admin', 'reviewer')
def report_pdf():
    """Generate PDF report on processing volume, accuracy, errors, cost savings."""
    from fpdf import FPDF
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Processing volume
        cur.execute("SELECT COUNT(*) as c FROM documents")
        total_docs = cur.fetchone()['c']
        cur.execute("SELECT doc_type, COUNT(*) as cnt FROM documents GROUP BY doc_type ORDER BY cnt DESC")
        by_type = cur.fetchall()
        cur.execute("SELECT status, COUNT(*) as cnt FROM documents GROUP BY status ORDER BY cnt DESC")
        by_status = cur.fetchall()
        cur.execute("SELECT created_at::date as day, COUNT(*) as cnt FROM documents GROUP BY day ORDER BY day DESC LIMIT 30")
        daily = cur.fetchall()
        # Accuracy
        cur.execute("SELECT COALESCE(AVG(type_confidence),0) as a FROM documents")
        avg_type_conf = round(float(cur.fetchone()['a']), 1)
        cur.execute("SELECT COALESCE(AVG(confidence),0) as a FROM extracted_fields")
        avg_field_conf = round(float(cur.fetchone()['a']), 1)
        # Errors
        cur.execute("SELECT COUNT(*) as c FROM validation_results WHERE NOT passed")
        total_errors = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM validation_results")
        total_checks = cur.fetchone()['c']
        cur.execute("SELECT check_type, COUNT(*) as cnt FROM validation_results WHERE NOT passed GROUP BY check_type ORDER BY cnt DESC")
        errors_by_type = cur.fetchall()
        # Corrections
        cur.execute("SELECT COUNT(*) as c FROM extracted_fields WHERE corrected_value IS NOT NULL AND corrected_value != field_value")
        total_corrections = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM extracted_fields")
        total_fields = cur.fetchone()['c']
        conn.close()
    except Exception as e:
        return f"Report error: {e}", 500

    # Cost savings estimate: manual processing ~$2/doc, IDP ~$0.02/doc
    manual_cost = total_docs * 2.0
    idp_cost = total_docs * 0.02
    savings = manual_cost - idp_cost
    pass_rate = round((1 - total_errors / total_checks) * 100, 1) if total_checks > 0 else 100

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(45, 66, 133)
    pdf.cell(0, 12, "Panasonic IDP - Processing Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, f"Generated: {datetime.now().strftime('%d %B %Y %H:%M')} by {current_user()}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    def section(title):
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(45, 66, 133)
        pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(45, 66, 133)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(51, 51, 51)

    def kv(label, value):
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(80, 7, label)
        pdf.set_text_color(51, 51, 51)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, str(value), new_x="LMARGIN", new_y="NEXT")

    section("1. Processing Volume")
    kv("Total Documents Processed:", str(total_docs))
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 7, "Documents by Type:", new_x="LMARGIN", new_y="NEXT")
    for r in by_type:
        kv(f"  {r['doc_type'].replace('_',' ').title()}:", str(r['cnt']))
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 7, "Documents by Status:", new_x="LMARGIN", new_y="NEXT")
    for r in by_status:
        kv(f"  {r['status'].replace('_',' ').title()}:", str(r['cnt']))
    pdf.ln(4)

    section("2. Accuracy Metrics")
    kv("Avg Document Type Confidence:", f"{avg_type_conf}%")
    kv("Avg Field Extraction Confidence:", f"{avg_field_conf}%")
    kv("Validation Pass Rate:", f"{pass_rate}%")
    kv("Total Fields Extracted:", str(total_fields))
    pdf.ln(4)

    section("3. Error Analysis")
    kv("Total Validation Checks:", str(total_checks))
    kv("Total Errors Found:", str(total_errors))
    kv("Manual Corrections Made:", str(total_corrections))
    if errors_by_type:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 7, "Errors by Category:", new_x="LMARGIN", new_y="NEXT")
        for r in errors_by_type:
            kv(f"  {r['check_type'].replace('_',' ').title()}:", str(r['cnt']))
    pdf.ln(4)

    section("4. Cost Savings Estimate")
    kv("Manual Processing Cost (est. $2/doc):", f"${manual_cost:,.2f}")
    kv("IDP Processing Cost (est. $0.02/doc):", f"${idp_cost:,.2f}")
    kv("Estimated Savings:", f"${savings:,.2f}")
    kv("Cost Reduction:", f"{round(savings/manual_cost*100,1) if manual_cost > 0 else 0}%")
    pdf.ln(4)

    if daily:
        section("5. Daily Processing Volume (Last 30 Days)")
        # Sort chronologically and draw a line chart
        daily_sorted = sorted(daily, key=lambda r: r['day'])
        n = len(daily_sorted)
        max_cnt = max(r['cnt'] for r in daily_sorted) or 1

        # Chart title
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(51, 51, 51)
        pdf.cell(0, 8, "Documents Processed per Day", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        # Chart dimensions — extra left margin for Y labels
        chart_x = 35
        chart_y = pdf.get_y()
        chart_w = 150
        chart_h = 70

        # Check page space
        if chart_y + chart_h + 25 > 280:
            pdf.add_page()
            chart_y = pdf.get_y()

        # Y-axis scale: round up to nice number
        y_step = max(1, (max_cnt + 4) // 5)
        y_max = y_step * 5
        if y_max < max_cnt:
            y_max = y_step * 6

        # Background
        pdf.set_fill_color(248, 249, 252)
        pdf.rect(chart_x, chart_y, chart_w, chart_h, style='F')

        # Horizontal grid lines + Y-axis labels
        pdf.set_draw_color(210, 210, 210)
        pdf.set_line_width(0.2)
        for i in range(6):
            gy = chart_y + chart_h - (i * chart_h / 5)
            pdf.line(chart_x, gy, chart_x + chart_w, gy)
            label_val = int(y_max * i / 5)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(100, 100, 100)
            pdf.text(chart_x - 10, gy + 1.5, str(label_val))

        # Y-axis line
        pdf.set_draw_color(120, 120, 120)
        pdf.set_line_width(0.4)
        pdf.line(chart_x, chart_y, chart_x, chart_y + chart_h)
        # X-axis line
        pdf.line(chart_x, chart_y + chart_h, chart_x + chart_w, chart_y + chart_h)

        # Y-axis title — written vertically using individual characters top-to-bottom
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(80, 80, 80)
        y_title = "Documents"
        yt_x = chart_x - 22
        yt_start = chart_y + (chart_h - len(y_title) * 5) / 2
        for i, ch in enumerate(y_title):
            pdf.text(yt_x, yt_start + i * 5, ch)

        # Plot data points and connecting lines
        points = []
        for i, r in enumerate(daily_sorted):
            px = chart_x + (i + 0.5) * chart_w / max(n, 1)
            py = chart_y + chart_h - (r['cnt'] / y_max * chart_h) if y_max > 0 else chart_y + chart_h
            points.append((px, py, r['cnt']))

        # Draw line segments
        if len(points) >= 2:
            pdf.set_draw_color(45, 66, 133)
            pdf.set_line_width(0.8)
            for i in range(len(points) - 1):
                pdf.line(points[i][0], points[i][1], points[i+1][0], points[i+1][1])

        # Draw dots + value labels at each point
        for px, py, cnt in points:
            # White border circle
            pdf.set_fill_color(255, 255, 255)
            pdf.ellipse(px - 2, py - 2, 4, 4, style='F')
            # Blue filled circle
            pdf.set_fill_color(45, 66, 133)
            pdf.ellipse(px - 1.3, py - 1.3, 2.6, 2.6, style='F')
            # Value label above dot
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_text_color(45, 66, 133)
            pdf.text(px - 3, py - 4, str(cnt))

        # X-axis date labels
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(100, 100, 100)
        label_step = max(1, n // 10)
        for i, r in enumerate(daily_sorted):
            if i % label_step == 0 or i == n - 1:
                px = chart_x + (i + 0.5) * chart_w / max(n, 1)
                day_str = str(r['day'])[5:]  # MM-DD
                pdf.text(px - 6, chart_y + chart_h + 5, day_str)

        # X-axis title
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(80, 80, 80)
        pdf.text(chart_x + chart_w / 2 - 5, chart_y + chart_h + 11, "Date")
        pdf.set_y(chart_y + chart_h + 14)

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    fname = f"IDP_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return send_file(buf, download_name=fname, mimetype='application/pdf', as_attachment=True)

@app.route("/report/csv_report")
@role_required('admin', 'reviewer')
def report_csv():
    """Generate CSV summary report."""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT d.doc_id, d.doc_type, d.status, d.type_confidence, d.filename,
            d.uploaded_by, d.created_at,
            COUNT(ef.id) as field_count,
            COALESCE(AVG(ef.confidence),0) as avg_field_conf,
            COUNT(ef.id) FILTER (WHERE ef.corrected_value IS NOT NULL AND ef.corrected_value != ef.field_value) as corrections,
            COUNT(vr.id) FILTER (WHERE NOT vr.passed) as failed_checks
            FROM documents d
            LEFT JOIN extracted_fields ef ON ef.document_id = d.id
            LEFT JOIN validation_results vr ON vr.document_id = d.id
            GROUP BY d.id ORDER BY d.created_at DESC""")
        rows = cur.fetchall()
        conn.close()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Doc ID','Doc Type','Status','Type Confidence','Filename','Uploaded By',
                         'Created At','Fields Extracted','Avg Field Confidence','Corrections','Failed Checks'])
        for r in rows:
            writer.writerow([r['doc_id'], r['doc_type'], r['status'], r['type_confidence'],
                r['filename'], r['uploaded_by'], r['created_at'], r['field_count'],
                round(float(r['avg_field_conf']),1), r['corrections'], r['failed_checks']])
        output.seek(0)
        return Response(output.getvalue(), mimetype='text/csv',
                        headers={"Content-Disposition": f"attachment;filename=IDP_Report_{datetime.now().strftime('%Y%m%d')}.csv"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
