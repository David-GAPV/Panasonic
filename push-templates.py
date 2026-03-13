#!/usr/bin/env python3
"""Generate and push all templates to the web server."""
import subprocess, os

HEADER = '''<header>
<img src="/static/images/Panasonic_whitetext.jpg" alt="Panasonic">
<div style="color:#fff;font-size:18px;font-weight:300">IDP System</div>
</header>'''

NAV = '''<nav>
<a href="/" {d}>Dashboard</a>
<a href="/review" {r}>Review Queue</a>
<a href="/upload" {u}>Upload</a>
<a href="/sap/simulate" {s}>SAP Integration</a>
<a href="/api/status" target="_blank">Status</a>
</nav>'''

CSS_BASE = '''*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Helvetica Neue',Arial,sans-serif;background:#f5f5f5;color:#333}
header{background:#0062af;padding:16px 40px;display:flex;align-items:center;justify-content:space-between}
header img{height:32px}
nav{background:#fff;border-bottom:1px solid #e0e0e0;padding:0 40px}
nav a{display:inline-block;padding:16px 24px;color:#333;text-decoration:none;border-bottom:3px solid transparent;transition:all 0.3s}
nav a:hover,nav a.active{color:#0062af;border-bottom-color:#0062af}
.container{max-width:1400px;margin:32px auto;padding:0 40px}
.card{background:#fff;border-radius:4px;box-shadow:0 2px 4px rgba(0,0,0,0.1);margin-bottom:24px}
.card-header{padding:20px 24px;border-bottom:1px solid #e0e0e0}
.card-header h2{font-size:20px;font-weight:400;color:#333}
.card-body{padding:24px}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:12px;background:#f9f9f9;font-weight:500;font-size:13px;color:#666;border-bottom:2px solid #e0e0e0}
td{padding:12px;border-bottom:1px solid #f0f0f0;font-size:14px}
tr:hover td{background:#fafafa}
.badge{display:inline-block;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:500}
.badge-completed{background:#e8f5e9;color:#2e7d32}
.badge-flagged{background:#fff3e0;color:#f57c00}
.badge-reviewing{background:#e3f2fd;color:#1976d2}
.badge-extracted{background:#fce4ec;color:#c2185b}
.badge-auto_approved{background:#c8e6c9;color:#388e3c}
.badge-rejected{background:#ffcdd2;color:#d32f2f}
a{color:#0062af;text-decoration:none}
a:hover{text-decoration:underline}'''

def nav(active):
    return NAV.format(
        d='class="active"' if active=='d' else '',
        r='class="active"' if active=='r' else '',
        u='class="active"' if active=='u' else '',
        s='class="active"' if active=='s' else '',
    )

templates = {}

# DASHBOARD
templates['dashboard.html'] = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Panasonic IDP</title>
<style>{CSS_BASE}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px;margin-bottom:32px}}
.stat-card{{background:#fff;padding:24px;border-radius:4px;box-shadow:0 2px 4px rgba(0,0,0,0.1)}}
.stat-card .num{{font-size:36px;font-weight:300;color:#0062af;margin-bottom:8px}}
.stat-card .label{{font-size:14px;color:#666;text-transform:uppercase;letter-spacing:0.5px}}
</style></head><body>
{HEADER}
{nav('d')}
<div class="container">
<div class="stats">
{{% for status, count in stats.items() %}}
<div class="stat-card"><div class="num">{{{{ count }}}}</div><div class="label">{{{{ status|replace('_',' ') }}}}</div></div>
{{% endfor %}}
</div>
<div class="card"><div class="card-header"><h2>Recent Documents</h2></div><div class="card-body">
<table><thead><tr><th>Document ID</th><th>Type</th><th>Filename</th><th>Status</th><th>Confidence</th><th>Issues</th><th>Action</th></tr></thead><tbody>
{{% for d in docs %}}<tr>
<td><a href="/document/{{{{ d.doc_id }}}}">{{{{ d.doc_id }}}}</a></td>
<td>{{{{ d.doc_type|replace('_',' ')|title if d.doc_type else '' }}}}</td>
<td style="max-width:300px;overflow:hidden;text-overflow:ellipsis">{{{{ d.filename or '' }}}}</td>
<td><span class="badge badge-{{{{ d.status }}}}">{{{{ d.status|replace('_',' ') }}}}</span></td>
<td>{{{{ d.type_confidence }}}}%</td>
<td>{{{{ d.failed_checks or 0 }}}}</td>
<td><a href="/document/{{{{ d.doc_id }}}}">View</a></td>
</tr>{{% endfor %}}</tbody></table>
</div></div></div></body></html>'''

# REVIEW QUEUE
templates['review_queue.html'] = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Review Queue</title>
<style>{CSS_BASE}
.alert{{background:#e3f2fd;border-left:4px solid #1976d2;padding:16px 20px;margin-bottom:24px;color:#0d47a1}}
</style></head><body>
{HEADER}
{nav('r')}
<div class="container">
<div class="alert">Documents requiring human review and approval</div>
<div class="card"><div class="card-header"><h2>Pending Review ({{{{ docs|length }}}})</h2></div><div class="card-body">
{{% if docs %}}
<table><thead><tr><th>Document ID</th><th>Type</th><th>Status</th><th>Confidence</th><th>Issues</th><th>Action</th></tr></thead><tbody>
{{% for d in docs %}}<tr>
<td><a href="/document/{{{{ d.doc_id }}}}">{{{{ d.doc_id }}}}</a></td>
<td>{{{{ d.doc_type|replace('_',' ')|title }}}}</td>
<td><span class="badge badge-{{{{ d.status }}}}">{{{{ d.status|replace('_',' ') }}}}</span></td>
<td>{{{{ d.type_confidence }}}}%</td>
<td>{{{{ d.failed_checks or 0 }}}}</td>
<td><a href="/document/{{{{ d.doc_id }}}}">Review</a></td>
</tr>{{% endfor %}}</tbody></table>
{{% else %}}<p style="text-align:center;color:#999;padding:60px">No documents pending review</p>{{% endif %}}
</div></div></div></body></html>'''

# UPLOAD
templates['upload.html'] = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Upload Document</title>
<style>{CSS_BASE}
.btn{{display:inline-block;padding:12px 32px;background:#0062af;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:14px;font-weight:500;transition:background 0.3s}}
.btn:hover{{background:#004d8c}}
input[type=file]{{display:block;width:100%;padding:16px;border:2px dashed #ccc;border-radius:4px;margin:20px 0;cursor:pointer;background:#fafafa}}
input[type=file]:hover{{border-color:#0062af;background:#f0f7ff}}
.alert-error{{background:#ffebee;border-left:4px solid #d32f2f;padding:16px 20px;margin-bottom:20px;color:#c62828}}
.alert-success{{background:#e8f5e9;border-left:4px solid #388e3c;padding:16px 20px;margin-bottom:20px;color:#2e7d32}}
</style></head><body>
{HEADER}
{nav('u')}
<div class="container" style="max-width:900px">
<div class="card"><div class="card-header"><h2>Upload Document</h2></div><div class="card-body">
<p style="color:#666;margin-bottom:20px">Supported: PDF, PNG, JPEG, TIFF, DOCX | Max: 16 MB</p>
<form method="POST" enctype="multipart/form-data">
<input type="file" name="file" accept=".pdf,.png,.jpg,.jpeg,.tiff,.tif,.docx" required>
<button type="submit" class="btn">Upload & Extract</button>
</form></div></div>
{{% if error %}}<div class="alert-error">{{{{ error }}}}</div>{{% endif %}}
{{% if result %}}
<div class="alert-success">Document processed successfully</div>
<div class="card"><div class="card-header"><h2>Extraction Results</h2></div><div class="card-body">
<p><strong>Document ID:</strong> {{{{ result.document_id or result.filename }}}}</p>
<p><strong>Type:</strong> {{{{ result.document_type|replace('_',' ')|title }}}} ({{{{ result.type_confidence }}}}%)</p>
{{% if result.fields %}}
<table style="margin-top:16px"><tr><th>Field</th><th>Value</th><th>Confidence</th></tr>
{{% for fname, fdata in result.fields.items() %}}<tr>
<td>{{{{ fname|replace('_',' ')|title }}}}</td>
<td>{{% if fdata is mapping %}}{{{{ fdata.value }}}}{{% else %}}{{{{ fdata }}}}{{% endif %}}</td>
<td>{{% if fdata is mapping %}}{{{{ fdata.confidence }}}}%{{% else %}}—{{% endif %}}</td>
</tr>{{% endfor %}}</table>{{% endif %}}
<p style="margin-top:24px"><a href="/" class="btn">Back to Dashboard</a></p>
</div></div>{{% endif %}}
</div></body></html>'''

# SAP SIMULATION
templates['sap_simulation.html'] = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>SAP Integration</title>
<style>{CSS_BASE}
.alert{{background:#e8f5e9;border-left:4px solid #4caf50;padding:16px 20px;margin-bottom:24px;color:#2e7d32}}
pre{{background:#f5f5f5;padding:12px;border-radius:4px;overflow-x:auto;font-size:12px;font-family:monospace}}
</style></head><body>
{HEADER}
{nav('s')}
<div class="container">
<div class="alert">Documents approved and sent to SAP ERP system</div>
<div class="card"><div class="card-header"><h2>SAP Push Log ({{{{ sap_logs|length }}}} records)</h2></div><div class="card-body">
{{% if sap_logs %}}
<table><thead><tr><th>Timestamp</th><th>Document ID</th><th>Type</th><th>Payload</th></tr></thead><tbody>
{{% for log in sap_logs %}}<tr>
<td>{{{{ log.logged_at.strftime('%d %b %H:%M:%S') if log.logged_at else '' }}}}</td>
<td><a href="/document/{{{{ log.doc_id }}}}">{{{{ log.doc_id }}}}</a></td>
<td>{{{{ log.doc_type|replace('_',' ')|title }}}}</td>
<td><pre style="margin:0;padding:6px;font-size:11px">{{{{ log.new_value[:100] }}}}...</pre></td>
</tr>{{% endfor %}}</tbody></table>
{{% else %}}<p style="text-align:center;color:#999;padding:60px">No SAP pushes yet. Approve documents from the Review Queue.</p>{{% endif %}}
</div></div>
<div class="card"><div class="card-header"><h2>Integration Details</h2></div><div class="card-body">
<p style="margin-bottom:12px"><strong>SAP System:</strong> Simulated (Production uses SAP RFC or REST API)</p>
<p style="margin-bottom:12px"><strong>Method:</strong> RESTful API with JSON payload</p>
<p style="margin-bottom:12px"><strong>Retry Policy:</strong> 3 attempts with exponential backoff</p>
<p><strong>Transaction:</strong> Atomic with rollback on failure</p>
</div></div></div></body></html>'''

# DOCUMENT DETAIL
templates['document_detail.html'] = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Document {{{{ doc.doc_id }}}}</title>
<style>{CSS_BASE}
.btn{{display:inline-block;padding:10px 24px;border:none;border-radius:4px;cursor:pointer;font-size:14px;font-weight:500;margin-right:8px;transition:all 0.3s}}
.btn-approve{{background:#4caf50;color:#fff}}.btn-approve:hover{{background:#388e3c}}
.btn-reject{{background:#f44336;color:#fff}}.btn-reject:hover{{background:#d32f2f}}
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}
input[type=text]{{width:100%;padding:8px;border:1px solid #ddd;border-radius:4px;font-size:14px}}
input[type=text]:focus{{outline:none;border-color:#0062af}}
</style>
<script>
function approveDoc(id){{if(!confirm('Approve and send to SAP?'))return;fetch('/document/'+id+'/approve',{{method:'POST'}}).then(r=>r.json()).then(d=>{{alert(d.message);location.reload()}})}}
function rejectDoc(id){{const r=prompt('Rejection reason:');if(!r)return;fetch('/document/'+id+'/reject',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{reason:r}})}}).then(r=>r.json()).then(d=>{{alert(d.message);location.reload()}})}}
function updateField(fid){{const v=document.getElementById('field_'+fid).value;fetch('/document/{{{{doc.doc_id}}}}/update_field',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{field_id:fid,value:v}})}}).then(r=>r.json()).then(d=>{{if(d.success)alert('Field updated')}})}}
</script>
</head><body>
{HEADER}
{nav('')}
<div class="container">
<p style="margin-bottom:20px"><a href="/">Back to Dashboard</a></p>
<div class="card">
<div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
<div><h2 style="display:inline;margin-right:12px">{{{{ doc.doc_id }}}}</h2><span class="badge badge-{{{{ doc.status }}}}">{{{{ doc.status|replace('_',' ') }}}}</span></div>
<div>{{% if doc.status in ['flagged','reviewing','extracted'] %}}
<button class="btn btn-approve" onclick="approveDoc('{{{{ doc.doc_id }}}}')">Approve & Send to SAP</button>
<button class="btn btn-reject" onclick="rejectDoc('{{{{ doc.doc_id }}}}')">Reject</button>
{{% endif %}}</div>
</div>
<div class="card-body">
<table style="width:auto;margin-bottom:20px">
<tr><td style="color:#888;padding-right:30px">Type</td><td><strong>{{{{ doc.doc_type|replace('_',' ')|title }}}}</strong> ({{{{ doc.type_confidence }}}}%)</td></tr>
<tr><td style="color:#888">Filename</td><td>{{{{ doc.filename }}}}</td></tr>
<tr><td style="color:#888">Uploaded by</td><td>{{{{ doc.uploaded_by or '' }}}}</td></tr>
<tr><td style="color:#888">Created</td><td>{{{{ doc.created_at }}}}</td></tr>
</table></div></div>
<div class="two-col">
<div class="card"><div class="card-header"><h2>Extracted Fields</h2></div><div class="card-body">
{{% if fields %}}<table><tr><th>Field</th><th>Value</th><th>Confidence</th><th>Reviewed</th></tr>
{{% for f in fields %}}<tr>
<td><strong>{{{{ f.field_name|replace('_',' ')|title }}}}</strong></td>
<td><input type="text" id="field_{{{{ f.id }}}}" value="{{{{ f.corrected_value or f.field_value }}}}" onblur="updateField({{{{ f.id }}}})"></td>
<td>{{{{ f.confidence }}}}%</td>
<td>{{{{ 'Yes' if f.reviewed else 'No' }}}}</td>
</tr>{{% endfor %}}</table>
{{% else %}}<p style="color:#999">No fields extracted</p>{{% endif %}}
</div></div>
<div class="card"><div class="card-header"><h2>Validation Results</h2></div><div class="card-body">
{{% if validations %}}<table><tr><th>Check</th><th>Result</th><th>Detail</th></tr>
{{% for v in validations %}}<tr>
<td>{{{{ v.check_name }}}}</td>
<td>{{% if v.passed %}}<span style="color:#388e3c;font-weight:500">PASS</span>{{% else %}}<span style="color:#d32f2f;font-weight:500">FAIL</span>{{% endif %}}</td>
<td>{{{{ v.detail or '' }}}}</td>
</tr>{{% endfor %}}</table>
{{% else %}}<p style="color:#999">No validation results</p>{{% endif %}}
</div></div></div>
<div class="card"><div class="card-header"><h2>Audit Trail</h2></div><div class="card-body">
<table><tr><th>Time</th><th>User</th><th>Action</th><th>Detail</th></tr>
{{% for a in audit %}}<tr>
<td>{{{{ a.logged_at.strftime('%d %b %Y %H:%M:%S') if a.logged_at else '' }}}}</td>
<td>{{{{ a.user_id or 'system' }}}}</td>
<td>{{{{ a.action|replace('_',' ') }}}}</td>
<td style="max-width:500px;overflow:hidden;text-overflow:ellipsis">{{{{ a.new_value or '' }}}}</td>
</tr>{{% else %}}<tr><td colspan="4" style="color:#999;text-align:center">No audit entries</td></tr>{{% endfor %}}
</table></div></div>
</div></body></html>'''

# Write to local files and SCP
os.makedirs('templates', exist_ok=True)
for name, content in templates.items():
    path = f'templates/{name}'
    with open(path, 'w') as f:
        f.write(content)
    print(f"  Written: {path} ({len(content)} bytes)")

print("\nAll templates generated.")
