import os, io, json, logging, re, traceback, base64
from flask import Flask, request, jsonify
from flask_cors import CORS
import pytesseract
from PIL import Image
from pdf2image import convert_from_bytes

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

try:
    import docx as python_docx
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    import boto3
    BEDROCK_CLIENT = boto3.client("bedrock-runtime", region_name="us-east-1")
    HAS_BEDROCK = True
except Exception:
    BEDROCK_CLIENT = None
    HAS_BEDROCK = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

pytesseract.pytesseract.tesseract_cmd = '/usr/local/bin/tesseract'
TESSDATA_DIR = os.getenv('TESSDATA_PREFIX', '/usr/local/share/tessdata')
try:
    SUPPORTED_LANGS = [f.replace('.traineddata','') for f in os.listdir(TESSDATA_DIR) if f.endswith('.traineddata')]
except:
    SUPPORTED_LANGS = ['eng', 'vie']

CLAUDE_MODEL_ID = "us.anthropic.claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Claude Vision extraction prompts per document type
# ---------------------------------------------------------------------------

PROMPT_INVOICE = """Extract ALL fields from this Invoice & Packing List document. Return a JSON object.
Extract every field you can see. Use null if a field is not found. Do NOT include page numbers.

Required keys:
- invoice_number: invoice number (e.g. MSV250374473)
- date: invoice date
- time: time if shown
- supplier_name: shipper/seller company name and address
- buyer_name: consignee/buyer company name (short, e.g. "PANASONIC VIETNAM CO., LTD.")
- buyer_address: buyer full address
- accountee: accountee name and address if different from buyer
- consignee: consignee name and address if different from buyer
- shipping_ref: shipping reference number
- bl_number: bill of lading number
- order_number: order number
- vessel: vessel name and voyage number
- port_of_loading: port of loading (city name, e.g. "SHANGHAI")
- port_of_discharge: port of discharge (city name, e.g. "HO CHI MINH CITY")
- destination: destination country
- etd: estimated time of departure
- eta: estimated time of arrival
- payment_terms: payment terms
- currency: currency code (e.g. USD)
- container_no: all container numbers comma separated
- seal_no: all seal numbers comma separated
- line_items: array of line items, each with {case_no, description, qty, unit_price, amount, net_weight, gross_weight, measurement}
- total_amount: GRAND TOTAL amount (the final total for all containers, NOT per-container subtotal)
- total_packages: total number of packages/cartons (just the number)
- total_net_weight: total net weight with unit
- total_gross_weight: total gross weight with unit
- total_measurement: total measurement in CBM
- country_of_origin: country of origin (e.g. CHINA)
- remarks: any remarks text
- amount_in_words: total amount in words if present

Return ONLY valid JSON, no markdown fences, no explanation."""

PROMPT_BL = """Extract ALL fields from this Bill of Lading / Sea Waybill document image.
This is a standard shipping form with numbered fields (1) through (33). Extract every field visible.
Return a JSON object. Use null if a field is not found.

Required keys:
- bl_number: field (5) Document No. (the main B/L number, e.g. EGLV142551220956)
- local_operational_no: field (5) any secondary number shown (e.g. 2N5500918566)
- shipper_name: field (2) Shipper/Exporter full text
- consignee_name: field (3) Consignee full name and address
- notify_party: field (4) Notify Party full name and address
- also_notify_party: field (9) Also Notify Party
- export_references: field (6) Export References
- forwarding_agent_ref: field (7) Forwarding Agent References
- point_of_origin: field (8) Point and Country of Origin
- email_contacts: any email addresses visible (comma separated)
- pre_carriage_by: field (12) Pre-carriage by
- place_of_receipt: field (13) Place of Receipt / Date
- vessel: field (14) Ocean Vessel / Voyage (e.g. "EVER CONFORM 0311-062S")
- port_of_loading: field (15) Port of Loading (city name)
- port_of_discharge: field (16) Port of Discharge (city name)
- place_of_delivery: field (17) Place of Delivery
- onward_inland_routing: field (10) Onward Inland Routing / Export Instructions
- container_no: field (18) all container numbers comma separated (format XXXX1234567)
- seal_no: field (18) all seal numbers comma separated
- marks_and_numbers: field (18) Marks & Numbers
- total_packages: field (19) Number of Packages with type (e.g. "192 CARTONS")
- description_of_goods: field (20) Description of Goods
- gross_weight: field (21) Gross Weight with unit
- measurement: field (21) Measurement in CBM
- total_containers_in_words: field (22) Total Number of Containers or Packages in words
- freight_charges: field (24) Freight & Charges description
- freight_prepaid: field (24) Prepaid amount if any
- freight_collect: field (24) Collect amount if any
- revenue_tons: field (24) Revenue Tons
- freight_terms: PREPAID or COLLECT
- exchange_rate: fields (31)(32) Exchange Rate
- prepaid_at: field (29) Prepaid at
- collect_at: field (30) Collect at / Destination
- date_of_issue: field (28) Place and Date of Issue
- place_of_issue: field (28) Place of issue (city)
- service_type: field (26) Service Type/Mode (e.g. "FCL/FCL O/O")
- laden_on_board: field (33) Laden on Board date
- bl_type: SEA WAYBILL or BILL OF LADING
- number_of_original_bls: field (27) Number of Original B/Ls

Return ONLY valid JSON, no markdown fences, no explanation."""

PROMPT_CO = """Extract ALL fields from this Certificate of Origin (Form E) document image.
This is an ACFTA Form E with numbered fields. Extract every field visible.
Return a JSON object. Use null if a field is not found.

Required keys:
- co_reference_no: Reference No. shown at top right (e.g. F256091307610001)
- exporter_name: field 1 - Exporter business name
- exporter_address: field 1 - Exporter full address including country
- consignee_name: field 2 - Consignee name (short company name)
- consignee_address: field 2 - Consignee full address
- departure_date: field 3 - Departure date
- vessel: field 3 - Vessel name and voyage
- port_of_loading: field 3 - From port/city and country
- port_of_discharge: field 3 - Port of Discharge
- route_description: field 3 - Full route text (FROM ... TO ... BY SEA)
- official_use: field 4 - any official use text or verification URL
- item_number: field 5 - Item number
- marks_and_numbers: field 6 - Marks and numbers on packages
- description_of_goods: field 7 - Full description of goods including HS code
- hs_codes: HS code numbers found (comma separated, e.g. "8450.20")
- origin_criteria: field 8 - Origin criteria percentage or code
- quantity_and_value: field 9 - Gross weight or quantity and FOB value
- total_packages: number of sets/packages (just the number)
- total_amount: FOB value amount (just the number, e.g. 77596.80)
- currency: currency code (e.g. USD)
- invoice_number: field 10 - Invoice number
- invoice_date: field 10 - Invoice date
- country_of_origin: country where goods were produced
- issued_in_country: country where certificate was issued
- third_party_operator: Third party operator name and address if present
- exporter_declaration_place: field 11 - place and date of exporter declaration
- certification_text: field 12 - certification authority text (brief)
- barcode: field 12 or 13 - any barcode number visible (long numeric string near stamps/signatures, e.g. "252909001589710" or "2523000365246")
- issued_retroactively: field 13 - Yes/No
- movement_certificate: field 13 - Yes/No
- exhibition: field 13 - Yes/No
- third_party_invoicing: field 13 - Yes/No
- certification_place_date: field 12 - Place and date of certification (e.g. "Hangzhou, China MAR. 25, 2025")

Return ONLY valid JSON, no markdown fences, no explanation."""


def extract_fields_claude(raw_bytes, prompt, filename=''):
    """Extract fields from a PDF/image using Claude Sonnet 4.6 vision."""
    if not HAS_BEDROCK:
        return None, "Bedrock not available"
    try:
        # Convert PDF page 1 to image
        if filename.lower().endswith('.pdf'):
            images = convert_from_bytes(raw_bytes, dpi=200, first_page=1, last_page=1)
            if not images:
                return None, "Could not convert PDF to images"
            img = images[0]
        else:
            img = Image.open(io.BytesIO(raw_bytes))

        # Convert to JPEG for Claude
        if img.mode in ('RGBA', 'LA', 'PA'):
            bg = Image.new('RGB', img.size, (255,255,255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": prompt}
            ]}]
        })

        resp = BEDROCK_CLIENT.invoke_model(
            modelId=CLAUDE_MODEL_ID, body=body, contentType="application/json"
        )
        result = json.loads(resp["body"].read())
        claude_text = result["content"][0]["text"]

        # Strip markdown fences if present
        claude_text = re.sub(r'^```json\s*', '', claude_text.strip())
        claude_text = re.sub(r'\s*```$', '', claude_text.strip())
        data = json.loads(claude_text)

        # Convert to our field format: {field_name: {value, confidence}}
        fields = {}
        for key, val in data.items():
            if val is None or str(val).strip() == '' or str(val).lower() == 'null':
                continue
            # Skip arrays (line_items) — flatten them as a summary
            if isinstance(val, list):
                if key == 'line_items' and val:
                    # Store count as a field
                    fields["line_item_count"] = {"value": str(len(val)), "confidence": 95}
                    # Store first item description as sample
                    first = val[0] if val else {}
                    desc = first.get('description', '')
                    if desc:
                        fields["description_of_goods"] = {"value": str(desc)[:100], "confidence": 90}
                continue
            if isinstance(val, bool):
                val = "Yes" if val else "No"
            fields[key] = {"value": str(val).strip(), "confidence": 95}

        return fields, None
    except json.JSONDecodeError as e:
        log.error(f"Claude JSON parse error: {e}")
        return None, f"Claude response parse error: {e}"
    except Exception as e:
        log.error(f"Claude extraction error: {traceback.format_exc()}")
        return None, f"Claude error: {e}"


def extract_fields_claude_multipage(raw_bytes, prompt, filename='', max_pages=2):
    """Extract fields from a multi-page PDF using Claude vision (sends multiple page images)."""
    if not HAS_BEDROCK:
        return None, "Bedrock not available"
    try:
        images = convert_from_bytes(raw_bytes, dpi=200, first_page=1, last_page=max_pages)
        if not images:
            return None, "Could not convert PDF to images"

        content = []
        for img in images:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            img_b64 = base64.b64encode(buf.getvalue()).decode()
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}})

        content.append({"type": "text", "text": prompt})

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": content}]
        })

        resp = BEDROCK_CLIENT.invoke_model(
            modelId=CLAUDE_MODEL_ID, body=body, contentType="application/json"
        )
        result = json.loads(resp["body"].read())
        claude_text = result["content"][0]["text"]
        claude_text = re.sub(r'^```json\s*', '', claude_text.strip())
        claude_text = re.sub(r'\s*```$', '', claude_text.strip())
        data = json.loads(claude_text)

        fields = {}
        for key, val in data.items():
            if val is None or str(val).strip() == '' or str(val).lower() == 'null':
                continue
            if isinstance(val, list):
                if key == 'line_items' and val:
                    fields["line_item_count"] = {"value": str(len(val)), "confidence": 95}
                    first = val[0] if val else {}
                    desc = first.get('description', '')
                    if desc:
                        fields["description_of_goods"] = {"value": str(desc)[:100], "confidence": 90}
                continue
            if isinstance(val, bool):
                val = "Yes" if val else "No"
            fields[key] = {"value": str(val).strip(), "confidence": 95}

        return fields, None
    except json.JSONDecodeError as e:
        log.error(f"Claude JSON parse error: {e}")
        return None, f"Claude response parse error: {e}"
    except Exception as e:
        log.error(f"Claude extraction error: {traceback.format_exc()}")
        return None, f"Claude error: {e}"


def classify_from_filename(filename):
    """Quick classification from filename hints."""
    fn = filename.lower()
    if any(x in fn for x in ['bl_', 'bl-', 'bill_of_lading', 'sea_waybill', 'b_l_', 'bol_']):
        return 'bill_of_lading'
    if any(x in fn for x in ['co_', 'co-', 'certificate', 'form_e', 'form e']):
        return 'certificate_of_origin'
    if any(x in fn for x in ['inv_', 'inv-', 'invoice']):
        return 'invoice'
    if any(x in fn for x in ['pl_', 'packing']):
        return 'packing_list'
    if any(x in fn for x in ['wr_', 'warehouse', 'receipt']):
        return 'warehouse_receipt'
    return None


def classify_document(text):
    """Score-based document classification from OCR text."""
    t = text.lower()
    scores = {
        'invoice': 0, 'packing_list': 0, 'bill_of_lading': 0,
        'warehouse_receipt': 0, 'certificate_of_origin': 0,
    }
    for k in ['certificate of origin', 'form e', 'preferential tariff', 'acfta',
              'asean-china free trade', 'products consigned from', 'origin criteria', 'certifying authority']:
        if k in t: scores['certificate_of_origin'] += 25
    for k in ['bill of lading', 'sea waybill', 'b/l no', 'place of issue',
              'ocean bill', 'document no', 'pre-carriage', 'onward inland', 'notify party']:
        if k in t: scores['bill_of_lading'] += 20
    for k in ['warehouse receipt', 'goods received', 'wh receipt no', 'date received',
              'receiving party', 'inspection']:
        if k in t: scores['warehouse_receipt'] += 25
    for k in ['packing list', 'carton no', 'packing list no', 'carton marking', 'shipping marks']:
        if k in t: scores['packing_list'] += 20
    for k in ['commercial invoice', 'invoice & packing list', 'invoice no', 'unit price',
              'total amount', 'end of invoice', 'proforma invoice', 'total cif value']:
        if k in t: scores['invoice'] += 20
    best = max(scores, key=scores.get)
    conf = min(95, scores[best] + 40)
    return (best, conf) if scores[best] > 0 else ('unknown', 40)


def to_rgb(img):
    if img.mode in ('RGBA', 'LA', 'PA'):
        bg = Image.new('RGB', img.size, (255,255,255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert('RGB') if img.mode != 'RGB' else img


def _extract_from_container(container):
    text = ''
    for p in container.paragraphs:
        if p.text.strip(): text += p.text + '\n'
    for t in container.tables:
        for row in t.rows:
            for cell in row.cells:
                ct = cell.text.strip()
                if ct: text += ct + '\n'
    return text


def extract_text_from_docx(raw_bytes):
    if not HAS_DOCX: return None
    try:
        doc = python_docx.Document(io.BytesIO(raw_bytes))
        text = ''
        for section in doc.sections:
            try: text += _extract_from_container(section.header)
            except: pass
            try: text += _extract_from_container(section.footer)
            except: pass
        text += _extract_from_container(doc)
        return text
    except Exception as e:
        log.error(f"DOCX parse error: {e}")
        return None


def extract_fields_regex(text, filename=''):
    """Fallback regex extraction for DOCX or when Claude is unavailable."""
    fields = {}
    # Invoice number
    m = re.search(r'\b(MSV\d{8,12})\b', text) or re.search(r'\b(INV[\-]\d{4}[\-\s][A-Z0-9\-]+)\b', text)
    if m: fields["invoice_number"] = {"value": re.sub(r'\s+', '-', m.group(1).strip()), "confidence": 90}
    # Date
    m = re.search(r'(?:Date)[:\s]*\n?\s*(\d{1,2}[\./\-]\d{1,2}[\./\-]\d{4})', text, re.I) or \
        re.search(r'(?:Date)[:\s]*\n?\s*(\d{1,2}\s+\w+\s+\d{4})', text, re.I)
    if m: fields["date"] = {"value": m.group(1).strip(), "confidence": 85}
    # Buyer
    m = re.search(r'(PANASONIC\s+VIETNAM\s+CO\.\s*,?\s*LTD\.?)', text, re.I)
    if m: fields["buyer_name"] = {"value": m.group(1).strip(), "confidence": 90}
    # B/L number
    m = re.search(r'\b([A-Z]{4}\d{10,15})\b', text)
    if m: fields["bl_number"] = {"value": m.group(1).strip(), "confidence": 85}
    # Container numbers
    containers = list(dict.fromkeys(re.findall(r'\b([A-Z]{4}\d{7})\b', text)))
    if containers: fields["container_no"] = {"value": ", ".join(containers[:6]), "confidence": 88}
    # Vessel
    m = re.search(r'(?:Vessel\s*/?\s*Voyage)[:\s]*\n?\s*(.+?)(?:\s*$)', text, re.I|re.M)
    if m: fields["vessel"] = {"value": m.group(1).strip(), "confidence": 85}
    # Ports
    m = re.search(r'(?:Port\s+of\s+Loading)[:\s]*\n?\s*([A-Za-z][\w\s,\.]{3,50})', text, re.I)
    if m: fields["port_of_loading"] = {"value": m.group(1).strip(), "confidence": 88}
    m = re.search(r'(?:Port\s+of\s+Discharge|Arrival\s+Port)[:\s]*\n?\s*([A-Za-z][\w\s,\.]{3,50})', text, re.I)
    if m: fields["port_of_discharge"] = {"value": m.group(1).strip(), "confidence": 88}
    # HS codes
    hs = list(set(re.findall(r'\b(\d{4}\.\d{2}(?:\.\d{2})?)\b', text)))
    if hs: fields["hs_codes"] = {"value": ", ".join(sorted(hs)), "confidence": 90}
    # Total packages
    m = re.search(r'(\d[\d,]*)\s+(?:cartons?|ctns?|packages?|sets?)\b', text, re.I)
    if m: fields["total_packages"] = {"value": m.group(1).strip(), "confidence": 80}
    return fields


def process_single_file(file_obj, lang):
    """Process a single uploaded file: classify + extract fields."""
    raw = file_obj.read()
    fname = file_obj.filename
    fname_lower = fname.lower()

    # ---- DOCX: direct text extraction (no Claude needed) ----
    if fname_lower.endswith('.docx') or fname_lower.endswith('.doc'):
        text = extract_text_from_docx(raw)
        if text is None:
            return None, "Could not parse DOCX file."
        doc_type, type_conf = classify_document(text)
        fields = extract_fields_regex(text, fname)
        return {
            "document_id": fname.rsplit('.', 1)[0],
            "filename": fname,
            "pages": 1, "language": lang,
            "document_type": doc_type, "type_confidence": type_conf,
            "fields": fields,
            "page_details": [{"page": 1, "text": text, "avg_confidence": 99}],
            "full_text": text
        }, None

    # ---- PDF / Image: use Claude Bedrock for extraction ----
    if HAS_BEDROCK:
        # Classify from filename first
        doc_type = classify_from_filename(fname)

        if doc_type is None:
            # Quick Tesseract on page 1 for classification only
            try:
                imgs = convert_from_bytes(raw, dpi=150, first_page=1, last_page=1)
                if imgs:
                    img = to_rgb(imgs[0])
                    txt = pytesseract.image_to_string(img, config=f'--oem 3 --psm 3 -l {lang}')
                    doc_type, _ = classify_document(txt)
                else:
                    doc_type = 'unknown'
            except:
                doc_type = 'unknown'

        # Select prompt and extraction method based on doc type
        if doc_type == 'bill_of_lading':
            prompt = PROMPT_BL
            fields, err = extract_fields_claude(raw, prompt, fname)
        elif doc_type == 'certificate_of_origin':
            prompt = PROMPT_CO
            # CO: only page 1 (Original), page 2 is Triplicate with same fields
            fields, err = extract_fields_claude(raw, prompt, fname)
        elif doc_type == 'invoice':
            prompt = PROMPT_INVOICE
            # Invoice may have 2 pages — send both
            fields, err = extract_fields_claude_multipage(raw, prompt, fname, max_pages=2)
        else:
            # Default: try invoice prompt
            prompt = PROMPT_INVOICE
            fields, err = extract_fields_claude(raw, prompt, fname)

        if fields is not None:
            # Get page count for metadata
            try:
                from pdf2image import pdfinfo_from_bytes
                info = pdfinfo_from_bytes(raw)
                num_pages = info.get('Pages', 1)
            except:
                num_pages = 1

            return {
                "document_id": fname.rsplit('.', 1)[0],
                "filename": fname,
                "pages": num_pages, "language": lang,
                "document_type": doc_type,
                "type_confidence": 95,
                "fields": fields,
                "page_details": [{"page": 1, "text": "(extracted by Claude vision)", "avg_confidence": 95}],
                "full_text": "(extracted by Claude vision)"
            }, None
        else:
            log.warning(f"Claude failed ({err}), falling back to Tesseract")

    # ---- Fallback: Tesseract OCR ----
    images = []
    try:
        if fname_lower.endswith('.pdf'):
            images = [to_rgb(p) for p in convert_from_bytes(raw, dpi=200, first_page=1, last_page=2)]
        else:
            images = [to_rgb(Image.open(io.BytesIO(raw)))]
    except Exception as e:
        return None, f"Could not decode: {e}"

    full_text = ""
    pages = []
    for i, img in enumerate(images):
        try:
            txt = pytesseract.image_to_string(img, config=f'--oem 3 --psm 3 -l {lang}')
            data = pytesseract.image_to_data(img, config=f'--oem 3 --psm 3 -l {lang}', output_type=pytesseract.Output.DICT)
            confs = [int(c) for c in data['conf'] if int(c) > 0]
            avg = round(sum(confs)/len(confs), 1) if confs else 0
        except:
            txt = ""; avg = 0
        full_text += txt + "\n"
        pages.append({"page": i+1, "text": txt, "avg_confidence": avg})

    doc_type, type_conf = classify_document(full_text)
    fields = extract_fields_regex(full_text, fname)

    return {
        "document_id": fname.rsplit('.', 1)[0],
        "filename": fname,
        "pages": len(pages), "language": lang,
        "document_type": doc_type, "type_confidence": type_conf,
        "fields": fields,
        "page_details": pages,
        "full_text": full_text
    }, None


@app.route('/health')
def health():
    ver = "unknown"
    try: ver = str(pytesseract.get_tesseract_version())
    except: pass
    return jsonify({
        "status": "ok", "tesseract_version": ver,
        "languages": SUPPORTED_LANGS, "docx_support": HAS_DOCX,
        "bedrock_support": HAS_BEDROCK, "claude_model": CLAUDE_MODEL_ID
    })


@app.route('/extract', methods=['POST'])
def extract():
    lang = request.form.get('lang', 'eng+vie')
    files = request.files.getlist('file')
    if not files or (len(files) == 1 and files[0].filename == ''):
        return jsonify({"error": "No file provided"}), 400

    if len(files) == 1:
        result, err = process_single_file(files[0], lang)
        if err:
            return jsonify({"error": err}), 422
        log.info(f"Processed: {files[0].filename} -> {result['document_type']}")
        return jsonify(result)

    results = []
    for f in files:
        log.info(f"Batch: {f.filename}")
        result, err = process_single_file(f, lang)
        results.append(result if not err else {"filename": f.filename, "error": err})
    return jsonify({"batch": True, "count": len(results), "results": results})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
