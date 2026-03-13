import os, io, json, logging, re, traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
import pytesseract
from PIL import Image
from pdf2image import convert_from_bytes

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    logging.info("pillow-heif registered")
except ImportError:
    logging.warning("pillow-heif not available")

try:
    import docx as python_docx
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

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

FIELD_PATTERNS = {
    "date":              r'(?:date[:\s]+)(\d{1,2}[\s/\-\.]\w+[\s/\-\.]\d{2,4})',
    "po_number":         r'(?:PO\s*(?:No\.?|Ref)?[:\s]+)(PO[\-][A-Z0-9\-/]{6,30})',
    "bl_number":         r'(?:B/L\s*No\.?\s*[:\s]+)([A-Z0-9]{8,30})',
    "total_amount":      r'(?:TOTAL\s+CIF\s+VALUE|TOTAL\s+CHARGES|total\s+amount)[:\s|]*\n?(?:USD\s*)?([\d,]+\.?\d{0,2})',
    "vessel":            r'(?:vessel\s*/?\s*voyage)[:\s]+(.+?)(?:\s+Country|\s+$|\n)',
    "port_of_loading":   r'(?:port\s+of\s+loading)[:\s]+([A-Za-z][A-Za-z0-9 ,\.]{4,60})',
    "port_of_discharge": r'(?:port\s+of\s+(?:discharge|arrival))[:\s]+([A-Za-z][A-Za-z0-9 ,\.]{4,60})',
    "gross_weight":      r'(?:gross\s*w(?:t|eight)?)[:\s]+([\d,]+\.?\d*\s*kg)',
    "net_weight":        r'(?:n\.?w\.?|net\s*w(?:t|eight)?)[:\s]+([\d,]+\.?\d*\s*kg)',
    "measurement":       r'(?:measurement|cbm)[:\s]+([\d,]+\.?\d*\s*CBM)',
    "incoterms":         r'(?:Incoterms)[:\s]+([A-Z]{3}\s+\w+)',
    "currency":          r'(?:Currency)[:\s]+([A-Z]{3})',
    "payment_terms":     r'(?:Payment)[:\s]+(.+?)(?:\s+Incoterms|\s+FOB|\s+Currency|\n|$)',
    "booking_ref":       r'(?:booking\s*ref)[:\s]+([A-Z0-9\-]{8,30})',
    "freight":           r'(?:ocean\s+freight)[:\s]+(?:USD\s*)?([\d,]+\.?\d{0,2})',
    "etd":               r'(?:ETD)[:\s]+(\d{1,2}\s+\w+\s+\d{4})',
    "eta":               r'(?:ETA)[:\s]+(\d{1,2}\s+\w+\s+\d{4})',
    "packing_list_no":   r'(?:packing\s+list\s*n[o.]?)[:\s]+([A-Z0-9\-/]{6,30})',
    "seal_no":           r'(?:seal\s*n[o.]?)[:\s]+([A-Z0-9\-]+(?:\s*/\s*[A-Z0-9\-]+)?)',
    "customs_clearance": r'(?:customs\s*clearance)[:\s]+(\d{1,2}\s+\w+\s+\d{4})',
    "invoice_value":     r'(?:invoice\s+value)[:\s]+(?:USD\s*)?([\d,]+\.?\d{0,2})',
}

def extract_fields(text, filename=''):
    fields = {}
    for field, pattern in FIELD_PATTERNS.items():
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            value = m.group(1).strip()
            conf = min(95, 60 + len(value) * 2)
            fields[field] = {"value": value, "confidence": conf}

    # Invoice number: prioritize INV- pattern (most reliable), then Invoice No/Ref label
    inv_m = re.search(r'(INV[\-][\w\-]+)', text)
    if inv_m:
        fields["invoice_number"] = {"value": inv_m.group(1).strip(), "confidence": 92}
    else:
        inv_m2 = re.search(r'(?:Invoice\s*(?:No\.?|Ref))[:\s]*\n?\s*([A-Z0-9][\w\-/]{5,30})', text, re.IGNORECASE | re.MULTILINE)
        if inv_m2:
            fields["invoice_number"] = {"value": inv_m2.group(1).strip(), "confidence": 85}
        elif filename:
            inv_m3 = re.search(r'(INV[\-][\w\-]+)', filename)
            if inv_m3:
                fields["invoice_number"] = {"value": inv_m3.group(1).strip(), "confidence": 75}

    # WH receipt number
    wr_m = re.search(r'(?:WH\s*Receipt\s*No\.?|WR[\-])[:\s]*(WR[\-][A-Z0-9\-]+)', text, re.IGNORECASE)
    if wr_m:
        fields["wh_receipt_no"] = {"value": wr_m.group(1).strip(), "confidence": 90}
    else:
        wr_m2 = re.search(r'(WR-\d{4}-[A-Z]{2}-\d{4,6})', text)
        if wr_m2:
            fields["wh_receipt_no"] = {"value": wr_m2.group(1).strip(), "confidence": 85}

    # Supplier: find clean company name on standalone line (from declaration/signature section)
    # Exclude lines containing "Panasonic" (those are merged OCR columns)
    sup_candidates = re.finditer(r'^((?:Shenzhen|Shanghai|Beijing|Guangzhou|Dongguan|Foshan)\s+\w[\w\s,\.]+?Co\.,?)$', text, re.IGNORECASE | re.MULTILINE)
    for sup_clean in sup_candidates:
        val = sup_clean.group(1).strip()
        if 'Panasonic' in val:
            continue  # skip merged OCR lines
        pos = sup_clean.end()
        next_bit = text[pos:pos+20].strip()
        if next_bit.startswith('Ltd'):
            val = val + ' Ltd.'
        fields["supplier_name"] = {"value": val, "confidence": 92}
        break

    # Buyer: find "Panasonic ... Co., Ltd."
    buy_clean = re.search(r'(Panasonic\s+Appliances\s+Vietnam\s+Co\.,?\s*Ltd\.)', text, re.IGNORECASE)
    if buy_clean:
        fields["buyer_name"] = {"value": buy_clean.group(1).strip(), "confidence": 92}
    else:
        buy_m = re.search(r'(Panasonic[\w\s]+Co\.)', text, re.IGNORECASE)
        if buy_m:
            fields["buyer_name"] = {"value": buy_m.group(1).strip(), "confidence": 80}

    # Tax code: Tax Code (MST): 0101248141
    tax_m = re.search(r'(?:Tax\s*Code)[^:]*[:\s]+(\d{10,13})', text, re.IGNORECASE)
    if tax_m:
        fields["tax_code"] = {"value": tax_m.group(1).strip(), "confidence": 90}

    # HS codes - collect all unique
    hs_all = list(set(re.findall(r'\b\d{4}\.\d{2}\.\d{2}\b', text)))
    if hs_all:
        fields["hs_codes"] = {"value": ", ".join(sorted(hs_all)), "confidence": 90}

    # Total packages
    tp = re.search(r'(?:total\s+packages)[:\s]+(\d[\d,]*)', text, re.IGNORECASE)
    if tp:
        fields["total_packages"] = {"value": tp.group(1).strip(), "confidence": 85}
    else:
        tp2 = re.search(r'(\d+)\s+(?:cartons|ctns)', text, re.IGNORECASE)
        if tp2:
            fields["total_packages"] = {"value": tp2.group(1).strip() + " cartons", "confidence": 75}

    # Container numbers - collect unique
    containers = list(dict.fromkeys(re.findall(r'([A-Z]{4}\d{7}[\-\d]*)', text)))
    if containers:
        fields["container_no"] = {"value": " / ".join(containers[:4]), "confidence": 90}

    # Expected/Received qty from TOTALS area in warehouse receipt
    # Cell-by-cell extraction puts each value on its own line, so look for the pattern across lines
    totals_m = re.search(r'TOTALS?.*?([\d,]{4,})\s+([\d,]{4,})', text)
    if totals_m:
        fields["expected_qty"] = {"value": totals_m.group(1).strip(), "confidence": 85}
        fields["received_qty"] = {"value": totals_m.group(2).strip(), "confidence": 85}
    else:
        # Cell-by-cell: TOTALS\n...\n156,200\n156,180
        totals_m2 = re.search(r'TOTALS\n(?:TOTALS\n)*(\d[\d,]+)\n(\d[\d,]+)', text)
        if totals_m2:
            fields["expected_qty"] = {"value": totals_m2.group(1).strip(), "confidence": 85}
            fields["received_qty"] = {"value": totals_m2.group(2).strip(), "confidence": 85}

    return fields

def classify_document(text):
    t = text.lower()
    # Score-based classification to avoid order-dependent misclassification
    scores = {'invoice': 0, 'packing_list': 0, 'bill_of_lading': 0, 'warehouse_receipt': 0}
    # Warehouse receipt (check first - most specific keywords)
    for k in ['warehouse receipt', 'goods received', 'nhap kho', 'wh receipt no', 'date received']:
        if k in t: scores['warehouse_receipt'] += 20
    # Bill of lading
    for k in ['bill of lading', 'b/l no', 'booking ref', 'place of issue', 'sea waybill']:
        if k in t: scores['bill_of_lading'] += 20
    # Packing list
    for k in ['packing list', 'carton no', 'packing list no', 'ctns', 'carton marking']:
        if k in t: scores['packing_list'] += 20
    # Invoice
    for k in ['commercial invoice', 'unit price', 'amount in words', 'subtotal', 'total cif value']:
        if k in t: scores['invoice'] += 20
    best = max(scores, key=scores.get)
    conf = min(95, scores[best] + 40)
    if scores[best] == 0:
        return 'unknown', 40
    return best, conf

def to_rgb(img):
    if img.mode in ('RGBA', 'LA', 'PA'):
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    if img.mode != 'RGB':
        return img.convert('RGB')
    return img

def _extract_from_container(container):
    """Extract text from paragraphs and tables in a docx container (body/header/footer)."""
    text = ''
    for p in container.paragraphs:
        if p.text.strip():
            text += p.text + '\n'
    for t in container.tables:
        for row in t.rows:
            for cell in row.cells:
                ct = cell.text.strip()
                if ct:
                    text += ct + '\n'
    return text

def extract_text_from_docx(raw_bytes):
    """Extract text from a .docx file including headers and footers."""
    if not HAS_DOCX:
        return None
    try:
        doc = python_docx.Document(io.BytesIO(raw_bytes))
        text = ''
        # Extract from headers and footers first (often contain doc number, date)
        for section in doc.sections:
            try:
                text += _extract_from_container(section.header)
            except:
                pass
            try:
                text += _extract_from_container(section.footer)
            except:
                pass
        # Extract from body
        text += _extract_from_container(doc)
        return text
    except Exception as e:
        log.error(f"DOCX parse error: {e}")
        return None

def process_single_file(file_obj, lang):
    raw = file_obj.read()
    fname = file_obj.filename.lower()

    # Handle DOCX files - extract text directly (no OCR needed)
    if fname.endswith('.docx') or fname.endswith('.doc'):
        text = extract_text_from_docx(raw)
        if text is None:
            return None, "Could not parse DOCX file. python-docx may not be installed."
        doc_type, type_conf = classify_document(text)
        fields = extract_fields(text, file_obj.filename)
        return {
            "document_id": file_obj.filename.rsplit('.', 1)[0],
            "filename": file_obj.filename,
            "pages": 1,
            "language": lang,
            "document_type": doc_type,
            "type_confidence": type_conf,
            "fields": fields,
            "page_details": [{"page": 1, "text": text, "avg_confidence": 99}],
            "full_text": text
        }, None

    # Handle image/PDF files via OCR
    images = []
    try:
        if fname.endswith('.pdf'):
            images = [to_rgb(p) for p in convert_from_bytes(raw, dpi=300)]
        else:
            img = Image.open(io.BytesIO(raw))
            images = [to_rgb(img)]
    except Exception as e:
        log.error(f"Decode error for {file_obj.filename}: {traceback.format_exc()}")
        return None, f"Could not decode: {e}"

    full_text = ""
    pages = []
    for i, img in enumerate(images):
        try:
            cfg = f'--oem 3 --psm 6 -l {lang}'
            txt = pytesseract.image_to_string(img, config=cfg)
            data = pytesseract.image_to_data(img, config=cfg, output_type=pytesseract.Output.DICT)
            confs = [int(c) for c in data['conf'] if int(c) > 0]
            avg = round(sum(confs)/len(confs), 1) if confs else 0
        except Exception as e:
            log.error(f"OCR error page {i+1} of {file_obj.filename}: {traceback.format_exc()}")
            txt = ""
            avg = 0
        full_text += txt + "\n"
        pages.append({"page": i+1, "text": txt, "avg_confidence": avg})

    doc_type, type_conf = classify_document(full_text)
    fields = extract_fields(full_text, file_obj.filename)
    return {
        "document_id": file_obj.filename.rsplit('.', 1)[0],
        "filename": file_obj.filename,
        "pages": len(pages),
        "language": lang,
        "document_type": doc_type,
        "type_confidence": type_conf,
        "fields": fields,
        "page_details": pages,
        "full_text": full_text
    }, None

@app.route('/health')
def health():
    ver = "unknown"
    try:
        ver = str(pytesseract.get_tesseract_version())
    except:
        pass
    return jsonify({"status": "ok", "tesseract_version": ver, "languages": SUPPORTED_LANGS, "docx_support": HAS_DOCX})

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
        if err:
            results.append({"filename": f.filename, "error": err})
        else:
            results.append(result)
    return jsonify({"batch": True, "count": len(results), "results": results})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
