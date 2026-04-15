#!/bin/bash
# Add enhanced templates for review workflow
set -e

WEB_IP="13.214.12.26"
KEY="deploy/idp-panasonic-key.pem"

echo "=== Adding Enhanced Templates ==="

ssh -i "$KEY" -o StrictHostKeyChecking=no ec2-user@$WEB_IP 'sudo bash -s' <<'REMOTE_SCRIPT'

# Enhanced dashboard (hide OCR endpoint)
cat > /opt/idp-web/templates/dashboard.html <<'HTMLEOF'
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Panasonic IDP</title>
<style>
body{font-family:Arial;background:#f4f6f9;margin:0}
header{background:#003087;color:#fff;padding:12px 24px}
nav{background:#00256e;padding:8px 24px}
nav a{color:#ccd9ff;text-decoration:none;padding:8px 16px;margin-right:8px;display:inline-block;border-radius:4px}
nav a:hover,nav a.active{background:#003087;color:#fff}
.container{max-width:1200px;margin:24px auto;padding:0 20px}
.card{background:#fff;border:1px solid #ddd;border-radius:6px;padding:20px;margin-bottom:20px}
h2{color:#003087;margin-bottom:16px;font-size:18px}
table{width:100%;border-collapse:collapse}
th{background:#003087;color:#fff;padding:8px;text-align:left;font-size:12px}
td{padding:8px;border-bottom:1px solid #eee;font-size:13px}
tr:hover td{background:#f8f9ff}
.badge{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600}
.badge-completed{background:#e8f5e9;color:#1a5e20}
.badge-flagged{background:#fff3e0;color:#e65100}
.badge-reviewing{background:#e3f2fd;color:#0d47a1}
.badge-extracted{background:#fce4ec;color:#880e4f}
.badge-queued{background:#f3e5f5;color:#6a1b9a}
.badge-auto_approved{background:#c8e6c9;color:#2e7d32}
.badge-rejected{background:#ffcdd2;color:#c62828}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px}
.stat{background:#fff;border:1px solid #ddd;border-radius:6px;padding:14px;text-align:center}
.stat .num{font-size:28px;font-weight:700;color:#003087}
.stat .lbl{font-size:11px;color:#888;margin-top:4px;text-transform:uppercase}
a{color:#003087;text-decoration:none}
a:hover{text-decoration:underline}
</style></head><body>
<header><h1>PANASONIC IDP System</h1><p style="font-size:12px;opacity:0.8;margin-top:4px">Intelligent Document Processing - Panasonic Vietnam</p></header>
<nav>
<a href="/" class="active">Dashboard</a>
<a href="/review">Review Queue</a>
<a href="/upload">Upload Document</a>
<a href="/sap/simulate">SAP Integration</a>
<a href="/api/status" target="_blank">System Status</a>
</nav>
<div class="container">
<div class="stat-grid">
{% for status, count in stats.items() %}
<div class="stat">
<div class="num">{{ count }}</div>
<div class="lbl">{{ status|replace('_',' ') }}</div>
</div>
{% endfor %}
</div>

<div class="card">
<h2>Recent Documents</h2>
<table><thead><tr>
<th>Document ID</th><th>Type</th><th>Filename</th><th>Status</th><th>Confidence</th><th>Issues</th><th>Action</th>
</tr></thead><tbody>
{% for d in docs %}<tr>
<td><a href="/document/{{ d.doc_id }}">{{ d.doc_id }}</a></td>
<td>{{ d.doc_type|replace('_',' ')|title if d.doc_type else '—' }}</td>
<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">{{ d.filename or '—' }}</td>
<td><span class="badge badge-{{ d.status }}">{{ d.status|replace('_',' ') }}</span></td>
<td>{{ d.type_confidence }}%</td>
<td>{{ d.failed_checks or 0 }}</td>
<td><a href="/document/{{ d.doc_id }}" style="font-size:12px">View →</a></td>
</tr>{% endfor %}</tbody></table>
</div>
</div></body></html>
HTMLEOF

# Review queue template
cat > /opt/idp-web/templates/review_queue.html <<'HTMLEOF'
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Review Queue - Panasonic IDP</title>
<style>
body{font-family:Arial;background:#f4f6f9;margin:0}
header{background:#003087;color:#fff;padding:12px 24px}
nav{background:#00256e;padding:8px 24px}
nav a{color:#ccd9ff;text-decoration:none;padding:8px 16px;margin-right:8px;display:inline-block;border-radius:4px}
nav a:hover,nav a.active{background:#003087;color:#fff}
.container{max-width:1200px;margin:24px auto;padding:0 20px}
.card{background:#fff;border:1px solid #ddd;border-radius:6px;padding:20px;margin-bottom:20px}
h2{color:#003087;margin-bottom:16px}
table{width:100%;border-collapse:collapse}
th{background:#003087;color:#fff;padding:8px;text-align:left;font-size:12px}
td{padding:8px;border-bottom:1px solid #eee;font-size:13px}
tr:hover td{background:#f8f9ff}
.badge{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600}
.badge-flagged{background:#fff3e0;color:#e65100}
.badge-reviewing{background:#e3f2fd;color:#0d47a1}
.badge-extracted{background:#fce4ec;color:#880e4f}
.alert-info{background:#e3f2fd;border-left:4px solid:#1976d2;padding:12px;margin-bottom:20px;color:#0d47a1}
a{color:#003087;text-decoration:none}
a:hover{text-decoration:underline}
</style></head><body>
<header><h1>PANASONIC IDP System</h1></header>
<nav>
<a href="/">Dashboard</a>
<a href="/review" class="active">Review Queue</a>
<a href="/upload">Upload Document</a>
<a href="/sap/simulate">SAP Integration</a>
<a href="/api/status" target="_blank">System Status</a>
</nav>
<div class="container">
<div class="alert-info">
<strong>Review Queue:</strong> Documents requiring human review and approval before SAP integration
</div>

<div class="card">
<h2>Documents Pending Review ({{ docs|length }})</h2>
{% if docs %}
<table><thead><tr>
<th>Document ID</th><th>Type</th><th>Status</th><th>Confidence</th><th>Issues</th><th>Action</th>
</tr></thead><tbody>
{% for d in docs %}<tr>
<td><a href="/document/{{ d.doc_id }}">{{ d.doc_id }}</a></td>
<td>{{ d.doc_type|replace('_',' ')|title }}</td>
<td><span class="badge badge-{{ d.status }}">{{ d.status|replace('_',' ') }}</span></td>
<td>{{ d.type_confidence }}%</td>
<td>{{ d.failed_checks or 0 }} failed checks</td>
<td><a href="/document/{{ d.doc_id }}" style="font-size:12px;font-weight:600">Review →</a></td>
</tr>{% endfor %}</tbody></table>
{% else %}
<p style="text-align:center;color:#999;padding:40px">No documents pending review</p>
{% endif %}
</div>
</div></body></html>
HTMLEOF

# Document detail with review interface
cat > /opt/idp-web/templates/document_detail.html <<'HTMLEOF'
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Document {{ doc.doc_id }} - Panasonic IDP</title>
<style>
body{font-family:Arial;background:#f4f6f9;margin:0}
header{background:#003087;color:#fff;padding:12px 24px}
nav{background:#00256e;padding:8px 24px}
nav a{color:#ccd9ff;text-decoration:none;padding:8px 16px;margin-right:8px;display:inline-block;border-radius:4px}
nav a:hover{background:#003087;color:#fff}
.container{max-width:1400px;margin:24px auto;padding:0 20px}
.card{background:#fff;border:1px solid #ddd;border-radius:6px;padding:20px;margin-bottom:20px}
h2{color:#003087;margin-bottom:16px;font-size:18px}
table{width:100%;border-collapse:collapse}
th{background:#003087;color:#fff;padding:8px;text-align:left;font-size:12px}
td{padding:8px;border-bottom:1px solid #eee;font-size:13px}
.badge{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600}
.badge-flagged{background:#fff3e0;color:#e65100}
.badge-reviewing{background:#e3f2fd;color:#0d47a1}
.badge-extracted{background:#fce4ec;color:#880e4f}
.badge-completed{background:#e8f5e9;color:#1a5e20}
.badge-auto_approved{background:#c8e6c9;color:#2e7d32}
.badge-rejected{background:#ffcdd2;color:#c62828}
.btn{display:inline-block;padding:10px 20px;border:none;border-radius:4px;cursor:pointer;font-size:14px;margin-right:8px}
.btn-approve{background:#4caf50;color:#fff}
.btn-reject{background:#f44336;color:#fff}
.btn:hover{opacity:0.9}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.conf-high{color:#1a5e20;font-weight:600}
.conf-med{color:#e65100;font-weight:600}
.conf-low{color:#b71c1c;font-weight:600}
input[type=text]{width:100%;padding:6px;border:1px solid #ddd;border-radius:4px}
</style>
<script>
function approveDoc(docId) {
  if (!confirm('Approve this document and send to SAP?')) return;
  fetch('/document/' + docId + '/approve', {method: 'POST'})
    .then(r => r.json())
    .then(d => {
      alert(d.message);
      location.reload();
    });
}
function rejectDoc(docId) {
  const reason = prompt('Rejection reason:');
  if (!reason) return;
  fetch('/document/' + docId + '/reject', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({reason: reason})
  }).then(r => r.json()).then(d => {
    alert(d.message);
    location.reload();
  });
}
function updateField(fieldId) {
  const val = document.getElementById('field_' + fieldId).value;
  fetch('/document/{{ doc.doc_id }}/update_field', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({field_id: fieldId, value: val})
  }).then(r => r.json()).then(d => {
    if (d.success) alert('Field updated');
  });
}
</script>
</head><body>
<header><h1>PANASONIC IDP System</h1></header>
<nav>
<a href="/">Dashboard</a>
<a href="/review">Review Queue</a>
<a href="/upload">Upload Document</a>
<a href="/sap/simulate">SAP Integration</a>
</nav>
<div class="container">
<p style="margin-bottom:12px"><a href="/">← Back to Dashboard</a></p>

<div class="card">
<h2>{{ doc.doc_id }} <span class="badge badge-{{ doc.status }}">{{ doc.status|replace('_',' ') }}</span></h2>
<table style="width:auto;margin-bottom:16px">
<tr><td style="color:#888;padding-right:20px">Type</td><td><b>{{ doc.doc_type|replace('_',' ')|title }}</b> ({{ doc.type_confidence }}% confidence)</td></tr>
<tr><td style="color:#888">Filename</td><td>{{ doc.filename }}</td></tr>
<tr><td style="color:#888">Uploaded by</td><td>{{ doc.uploaded_by or '—' }}</td></tr>
<tr><td style="color:#888">Created</td><td>{{ doc.created_at }}</td></tr>
</table>

{% if doc.status in ['flagged', 'reviewing', 'extracted'] %}
<button class="btn btn-approve" onclick="approveDoc('{{ doc.doc_id }}')">✓ Approve & Send to SAP</button>
<button class="btn btn-reject" onclick="rejectDoc('{{ doc.doc_id }}')">✗ Reject</button>
{% endif %}
</div>

<div class="two-col">
<div class="card">
<h2>Extracted Fields</h2>
{% if fields %}
<table>
<tr><th>Field</th><th>Value</th><th>Confidence</th><th>Corrected</th></tr>
{% for f in fields %}
<tr>
<td><b>{{ f.field_name|replace('_',' ')|title }}</b></td>
<td>
<input type="text" id="field_{{ f.id }}" value="{{ f.corrected_value or f.field_value }}" 
       onblur="updateField({{ f.id }})">
</td>
<td>
{% if f.confidence >= 85 %}<span class="conf-high">{{ f.confidence }}%</span>
{% elif f.confidence >= 70 %}<span class="conf-med">{{ f.confidence }}%</span>
{% else %}<span class="conf-low">{{ f.confidence }}%</span>{% endif %}
</td>
<td>{{ '✓' if f.reviewed else '—' }}</td>
</tr>
{% endfor %}
</table>
{% else %}<p style="color:#999">No fields extracted</p>{% endif %}
</div>

<div class="card">
<h2>Validation Results</h2>
{% if validations %}
<table>
<tr><th>Check</th><th>Result</th><th>Detail</th></tr>
{% for v in validations %}
<tr>
<td>{{ v.check_name }}</td>
<td>{% if v.passed %}<span class="conf-high">✓ PASS</span>{% else %}<span class="conf-low">✗ FAIL</span>{% endif %}</td>
<td>{{ v.detail or '—' }}</td>
</tr>
{% endfor %}
</table>
{% else %}<p style="color:#999">No validation results</p>{% endif %}
</div>
</div>

<div class="card">
<h2>Audit Trail</h2>
<table>
<tr><th>Time</th><th>User</th><th>Action</th><th>Detail</th></tr>
{% for a in audit %}
<tr>
<td>{{ a.logged_at.strftime('%d %b %Y %H:%M:%S') if a.logged_at else '—' }}</td>
<td>{{ a.user_id or 'system' }}</td>
<td>{{ a.action|replace('_',' ') }}</td>
<td style="max-width:400px;overflow:hidden;text-overflow:ellipsis">{{ a.new_value or '—' }}</td>
</tr>
{% else %}<tr><td colspan="4" style="color:#999;text-align:center">No audit entries</td></tr>
{% endfor %}
</table>
</div>

</div></body></html>
HTMLEOF

# SAP simulation page
cat > /opt/idp-web/templates/sap_simulation.html <<'HTMLEOF'
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>SAP Integration - Panasonic IDP</title>
<style>
body{font-family:Arial;background:#f4f6f9;margin:0}
header{background:#003087;color:#fff;padding:12px 24px}
nav{background:#00256e;padding:8px 24px}
nav a{color:#ccd9ff;text-decoration:none;padding:8px 16px;margin-right:8px;display:inline-block;border-radius:4px}
nav a:hover,nav a.active{background:#003087;color:#fff}
.container{max-width:1200px;margin:24px auto;padding:0 20px}
.card{background:#fff;border:1px solid #ddd;border-radius:6px;padding:20px;margin-bottom:20px}
h2{color:#003087;margin-bottom:16px}
table{width:100%;border-collapse:collapse}
th{background:#003087;color:#fff;padding:8px;text-align:left;font-size:12px}
td{padding:8px;border-bottom:1px solid #eee;font-size:13px}
tr:hover td{background:#f8f9ff}
.alert-info{background:#e8f5e9;border-left:4px solid:#4caf50;padding:12px;margin-bottom:20px;color:#2e7d32}
pre{background:#f5f5f5;padding:12px;border-radius:4px;overflow-x:auto;font-size:12px}
</style></head><body>
<header><h1>PANASONIC IDP System</h1></header>
<nav>
<a href="/">Dashboard</a>
<a href="/review">Review Queue</a>
<a href="/upload">Upload Document</a>
<a href="/sap/simulate" class="active">SAP Integration</a>
<a href="/api/status" target="_blank">System Status</a>
</nav>
<div class="container">
<div class="alert-info">
<strong>SAP Integration Simulation:</strong> This page shows documents that have been approved and sent to SAP ERP system
</div>

<div class="card">
<h2>SAP Push Log ({{ sap_logs|length }} records)</h2>
{% if sap_logs %}
<table><thead><tr>
<th>Timestamp</th><th>Document ID</th><th>Type</th><th>Payload</th>
</tr></thead><tbody>
{% for log in sap_logs %}<tr>
<td>{{ log.logged_at.strftime('%d %b %H:%M:%S') if log.logged_at else '—' }}</td>
<td><a href="/document/{{ log.doc_id }}">{{ log.doc_id }}</a></td>
<td>{{ log.doc_type|replace('_',' ')|title }}</td>
<td><pre style="margin:0;padding:4px;font-size:11px">{{ log.new_value[:100] }}...</pre></td>
</tr>{% endfor %}</tbody></table>
{% else %}
<p style="text-align:center;color:#999;padding:40px">No SAP pushes yet. Approve documents from the review queue.</p>
{% endif %}
</div>

<div class="card">
<h2>Integration Details</h2>
<p><strong>SAP System:</strong> Simulated (Production would use SAP RFC or REST API)</p>
<p><strong>Integration Method:</strong> RESTful API with JSON payload</p>
<p><strong>Retry Policy:</strong> 3 attempts with exponential backoff</p>
<p><strong>Transaction Management:</strong> Atomic with rollback on failure</p>
</div>
</div></body></html>
HTMLEOF

# Restart service
systemctl restart idp-web
sleep 2

echo "✓ Templates updated"

REMOTE_SCRIPT

echo ""
echo "✓ All templates added successfully!"
echo ""
echo "New Features:"
echo "  ✓ Review Queue with human approval workflow"
echo "  ✓ Document detail page with side-by-side view"
echo "  ✓ Field correction interface"
echo "  ✓ SAP integration simulation"
echo "  ✓ OCR API endpoint hidden from UI"
echo ""
echo "Access:"
echo "  Dashboard:  http://$WEB_IP/"
echo "  Review:     http://$WEB_IP/review"
echo "  SAP:        http://$WEB_IP/sap/simulate"
