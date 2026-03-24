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
    "date":              r'(?:date(?:\s+of\s+issue)?|date\s+received|invoice\s+date|ngay(?:\s+phat\s+hanh)?|ngay\s+nhap\s+kho)[:\s]*\n?\s*(\d{1,2}[\s/\-\.]\w+[\s/\-\.]\d{2,4})',
    "po_number":         r'(?:PO\s*(?:No\.?|Ref)?|Purchase\s+Order|So\s+don\s+dat\s+hang)[:\s]*\n?\s*(PO[\-][A-Z0-9\-/]{6,30})',
    "bl_number":         r'(?:B/L\s*No\.?\s*|So\s+van\s+don)[:\s]*\n?\s*([A-Z0-9]{8,30})',
    "total_amount":      r'(?:TOTAL\s+CIF\s+VALUE|TOTAL\s+CHARGES|total\s+amount|Tong\s+gia\s+tri\s+CIF)[:\s|]*\n?(?:USD\s*)?([\d,]+\.?\d{0,2})',
    "vessel":            r'(?:vessel\s*/?\s*voyage|Tau\s*/?\s*Chuyen)[:\s]*\n?\s*(.+?)(?:\s+Country|\s+Xuat\s+xu|\s*$)',
    "port_of_loading":   r'(?:port\s+of\s+loading|Cang\s+xuat)[:\s]*\n?\s*([A-Za-z][A-Za-z0-9 ,\.]{4,60})',
    "port_of_discharge": r'(?:port\s+of\s+(?:discharge|arrival)|arrival\s+port|Cang\s+nhap)[:\s]*\n?\s*([A-Za-z][A-Za-z0-9 ,\.]{4,60})',
    "gross_weight":      r'(?:gross\s*w(?:t|eight)?|Trong\s+luong\s+ca\s+bi)[:\s]*\n?\s*([\d,]+\.?\d*\s*kg)',
    "net_weight":        r'(?:n\.?w\.?|net\s*w(?:t|eight)?|Trong\s+luong\s+tinh)[:\s]*\n?\s*([\d,]+\.?\d*\s*kg)',
    "measurement":       r'(?:measurement|cbm|cem)[:\s]*\n?\s*([\d,]+\.?\d*\s*(?:CBM|CEM|cbm))',
    "incoterms":         r'(?:Incoterms)[:\s]*\n?\s*([A-Z]{3}\s+[\w ]+?)(?:\n|$)',
    "currency":          r'(?:Currency|Dong\s+tien)[:\s]*\n?\s*([A-Z]{3})\s*$',
    "payment_terms":     r'(?:Payment|Dieu\s+khoan\s+giao\s+hang)[:\s]*\n?\s*(.+?)(?:\s+Incoterms|\s+FOB|\s+Currency|\n|$)',
    "booking_ref":       r'(?:booking\s*ref)[:\s]*\n?\s*([A-Z0-9\-]{8,30})',
    "freight":           r'(?:ocean\s+freight)[:\s]*\n?\s*(?:USD\s*)?([\d,]+\.?\d{0,2})',
    "etd":               r'(?:ETD)[:\s]*\n?\s*(\d{1,2}\s+\w+\s+\d{4})',
    "eta":               r'(?:ETA)[:\s]*\n?\s*(\d{1,2}\s+\w+\s+\d{4})',
    "packing_list_no":   r'(?:packing\s+list\s*n[o.]?\.?|So\s+phieu\s+dong\s+goi)[:\s]*\n?\s*([A-Z0-9\-/]{6,30})',
    "seal_no":           r'(?:seal\s*n[o.]?\.?|So\s+seal)[:\s]*\n?\s*([A-Z0-9\-]+(?:\s*/\s*[A-Z0-9\-]+)?)',
    "customs_clearance": r'(?:customs\s*clearance|Thong\s+quan)[:\s]*\n?\s*(\d{1,2}[\s/\-\.]\w+[\s/\-\.]\d{2,4})',
    "invoice_value":     r'(?:invoice\s+value)[:\s]*\n?\s*(?:USD\s*)?([\d,]+\.?\d{0,2})',
    "lc_number":         r'(?:L/C\s*No\.?)[:\s]*\n?\s*([A-Z0-9\-]{8,30})',
    "insurance_amount":  r'(?:Insurance(?:\s+Amount)?)[:\s]*\n?\s*(?:USD\s*)?([\d,]+\.?\d{0,2})',
    "country_of_origin": r'(?:Country\s+of\s+Origin|Xuat\s+xu\s+hang\s+hoa)[:\s]*\n?\s*([A-Za-z][A-Za-z ]{1,30})',
}

def extract_fields(text, filename=''):
    fields = {}
    for field, pattern in FIELD_PATTERNS.items():
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            value = m.group(1).strip()
            conf = min(95, 60 + len(value) * 2)
            fields[field] = {"value": value, "confidence": conf}

    # Packing list number fallback: standalone PL-XXXX pattern (OCR may lose the label in multi-column layouts)
    if "packing_list_no" not in fields:
        pl_fb = re.search(r'\b(PL[\-]\d{4}[\-][A-Z0-9\-]+)\b', text)
        if pl_fb:
            fields["packing_list_no"] = {"value": pl_fb.group(1).strip(), "confidence": 85}

    # B/L number fallback: standalone B/L-like pattern (OCR may misread digits/letters)
    if "bl_number" not in fields:
        # Try common carrier prefixes with flexible digit matching
        bl_fb = re.search(r'\b((?:MAEU|OOLU|HDMU|COSU|MSKU|CMAU|TCLU|MAEUS?)\d{7,12})\b', text)
        if bl_fb:
            fields["bl_number"] = {"value": bl_fb.group(1).strip(), "confidence": 80}
        else:
            # Generic: 4+ uppercase letters followed by 7+ digits
            bl_fb2 = re.search(r'\b([A-Z]{4,5}\d{7,12})\b', text)
            if bl_fb2:
                fields["bl_number"] = {"value": bl_fb2.group(1).strip(), "confidence": 75}

    # Date fallback: standalone date near top of document (OCR may lose "Date" label)
    if "date" not in fields:
        date_standalone = re.search(r'^(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\s*$', text, re.IGNORECASE | re.MULTILINE)
        if date_standalone:
            fields["date"] = {"value": date_standalone.group(1).strip(), "confidence": 80}

    # Date fallback: table-style "Date of Issue\n18 March 2025" or "Date Received\n21 March 2025"
    if "date" not in fields:
        date_fb = re.search(r'(?:Date\s+(?:of\s+Issue|Received|Issued))\s*\n\s*(\d{1,2}[\s/\-\.]\w+[\s/\-\.]\d{2,4})', text, re.IGNORECASE | re.MULTILINE)
        if date_fb:
            fields["date"] = {"value": date_fb.group(1).strip(), "confidence": 85}

    # Port of discharge fallback: "Arrival Port" label
    if "port_of_discharge" not in fields:
        pod_fb = re.search(r'(?:arrival\s+port|destination\s+port|port\s+of\s+destination)[:\s]+([A-Za-z][A-Za-z0-9 ,\.]{4,60})', text, re.IGNORECASE)
        if pod_fb:
            fields["port_of_discharge"] = {"value": pod_fb.group(1).strip(), "confidence": 85}

    # PO number fallback: "PO No." label variant in B/L or standalone PO-XXXX
    if "po_number" not in fields:
        po_fb = re.search(r'(?:PO\s*No\.?)[:\s]*\n?\s*(PO[\-][A-Z0-9\-/]{6,30})', text, re.IGNORECASE | re.MULTILINE)
        if po_fb:
            fields["po_number"] = {"value": po_fb.group(1).strip(), "confidence": 85}
        else:
            po_fb2 = re.search(r'\b(PO-\d{4}-[A-Z0-9\-]+)\b', text)
            if po_fb2:
                fields["po_number"] = {"value": po_fb2.group(1).strip(), "confidence": 80}

    # Currency fallback: extract from "TOTAL CIF VALUE: USD" or standalone "USD" near amounts
    if "currency" not in fields:
        cur_fb = re.search(r'(?:TOTAL\s+CIF\s+VALUE|Amount)[:\s]*\n?\s*(USD|EUR|JPY|VND)', text, re.IGNORECASE)
        if cur_fb:
            fields["currency"] = {"value": cur_fb.group(1).upper().strip(), "confidence": 80}
        else:
            # Look for currency code near price columns
            cur_fb2 = re.search(r'(?:Unit\s+Price|Amount)\s*\(?\s*(USD|EUR|JPY|VND)\s*\)?', text, re.IGNORECASE)
            if cur_fb2:
                fields["currency"] = {"value": cur_fb2.group(1).upper().strip(), "confidence": 75}

    # Incoterms fallback: extract from "CIF" in "TOTAL CIF VALUE" or standalone trade terms
    if "incoterms" not in fields:
        ict_fb = re.search(r'\b(CIF|FOB|CFR|EXW|DDP|DAP)\s+([\w ]{3,30}?)(?:\s+VALUE|\s+TOTAL|\n|$)', text, re.IGNORECASE)
        if ict_fb:
            fields["incoterms"] = {"value": f"{ict_fb.group(1).upper()} {ict_fb.group(2).strip()}", "confidence": 75}
        else:
            # Just the term itself
            ict_fb2 = re.search(r'\b(CIF|FOB|CFR|EXW|DDP|DAP)\b', text)
            if ict_fb2:
                fields["incoterms"] = {"value": ict_fb2.group(1).upper(), "confidence": 70}

    # Measurement/CBM fallback: look for digits + CBM/CEM pattern anywhere in text
    if "measurement" not in fields:
        meas_fb = re.search(r'([\d,]+\.?\d*)\s*(?:CBM|CEM|cbm)\b', text, re.IGNORECASE)
        if meas_fb:
            fields["measurement"] = {"value": f"{meas_fb.group(1).strip()} CBM", "confidence": 80}

    # Container number fallback: handle OCR artifacts (MAEUT 738001 → MAEU7788001)
    if "container_no" not in fields:
        # Try with "Container No" label context (handles OCR spacing/substitution)
        cnt_fb = re.search(r'(?:Container\s*No\.?)[:\s]*\n?\s*([A-Z]{3,5}[A-Z0-9T]?\s*\d[\s]?\d{5,7})', text, re.IGNORECASE)
        if cnt_fb:
            val = re.sub(r'\s+', '', cnt_fb.group(1).strip())
            fields["container_no"] = {"value": val, "confidence": 75}
        else:
            # Standalone carrier prefix pattern
            cnt_fb2 = re.search(r'\b((?:MAEU|OOLU|HDMU|COSU|MSKU|CMAU|TCLU)\d{7})\b', text)
            if cnt_fb2:
                fields["container_no"] = {"value": cnt_fb2.group(1).strip(), "confidence": 80}

    # Gross weight fallback: standalone in table cell or after "Gross Weight" header
    if "gross_weight" not in fields:
        gw_fb = re.search(r'Gross\s+W(?:t|eight|i)\s*[:\s]*\n?\s*([\d,]+\.?\d*\s*kg)', text, re.IGNORECASE | re.MULTILINE)
        if gw_fb:
            fields["gross_weight"] = {"value": gw_fb.group(1).strip(), "confidence": 80}
        else:
            # Look for weight values (digits + kg) in cargo/particulars section
            nw_val = fields.get("net_weight", {}).get("value", "")
            gw_candidates = re.findall(r'([\d,]+\.?\d*\s*kg)', text, re.IGNORECASE)
            # Pick the largest weight value as gross weight (if not already net weight)
            best_gw = None
            best_val = 0
            for cand in gw_candidates:
                if cand.strip() == nw_val.strip():
                    continue
                num_str = re.sub(r'[^\d.]', '', cand.replace(',', ''))
                try:
                    num = float(num_str)
                    if num > best_val:
                        best_val = num
                        best_gw = cand.strip()
                except:
                    pass
            if best_gw:
                fields["gross_weight"] = {"value": best_gw, "confidence": 75}

    # Invoice number: prioritize INV- pattern (most reliable), then Invoice No/Ref label
    # Handle OCR artifacts: "INV-2025 V-3300" (space in VN), "INV-2025-VN-3300"
    inv_m = re.search(r'(INV[\-]\d{4}[\-\s][A-Z]{1,4}[\-\s][\w\-]+)', text)
    if inv_m:
        val = re.sub(r'\s+', '-', inv_m.group(1).strip())  # normalize spaces to hyphens
        fields["invoice_number"] = {"value": val, "confidence": 92}
    else:
        inv_m1b = re.search(r'(INV[\-][\w\-]+)', text)
        if inv_m1b:
            fields["invoice_number"] = {"value": inv_m1b.group(1).strip(), "confidence": 88}
        else:
            inv_m2 = re.search(r'(?:Invoice\s*(?:No\.?|Ref\.?)|So\s+hoa\s+don)[:\s]*\n?\s*([A-Z0-9][\w\-/]{5,30})', text, re.IGNORECASE | re.MULTILINE)
            if inv_m2:
                fields["invoice_number"] = {"value": inv_m2.group(1).strip(), "confidence": 85}
            elif filename:
                inv_m3 = re.search(r'(INV[\-][\w\-]+)', filename)
                if inv_m3:
                    fields["invoice_number"] = {"value": inv_m3.group(1).strip(), "confidence": 75}

    # WH receipt number — handle "WH Receipt No.", "So phieu nhap kho", table-cell format, standalone WR- pattern
    wr_m = re.search(r'(?:WH\s*Receipt\s*No\.?|So\s+phieu\s+nhap\s+kho|WR[\-])[:\s]*\n?\s*(WR[\-][A-Z0-9\-]+)', text, re.IGNORECASE | re.MULTILINE)
    if wr_m:
        fields["wh_receipt_no"] = {"value": wr_m.group(1).strip(), "confidence": 90}
    else:
        wr_m2 = re.search(r'(WR-\d{4}-[A-Z]{2,4}-\d{4,6})', text)
        if wr_m2:
            fields["wh_receipt_no"] = {"value": wr_m2.group(1).strip(), "confidence": 85}

    # Supplier: find company name with city prefix + Co., Ltd. (handle OCR artifacts: Co.. Ltd, Co. Ltd, Co., Ltd)
    sup_pat = r'^((?:Shenzhen|Shanghai|Beijing|Guangzhou|Dongguan|Foshan|Ningbo|Xiamen|Suzhou|Hangzhou|Hanoi|Ha\s*Noi|Ho\s*Chi\s*Minh|Vietnam)\s+\w[\w\s,\.]+?Co\.[\.,]?\s*Ltd\.?)'
    sup_candidates = re.finditer(sup_pat, text, re.IGNORECASE | re.MULTILINE)
    for sup_clean in sup_candidates:
        val = sup_clean.group(1).strip()
        if 'Panasonic' not in val:
            # Normalize all variants: "Co.. Ltd", "Co. Ltd", "Co.,Ltd" → "Co., Ltd."
            val = re.sub(r'Co\.[\.,]*\s*Ltd', 'Co., Ltd', val)
            fields["supplier_name"] = {"value": val, "confidence": 92}
            break
    # Fallback: Seller/Shipper/Supplier/Exporter label followed by company name on next line
    if "supplier_name" not in fields:
        sup_label = re.search(r'(?:Seller|Shipper|Supplier|Exporter|Nguoi\s+xuat\s+khau|Ben\s+ban|Nha\s+cung\s+cap)[^:\n]*[:\s]*\n\s*(.+?Co\.[\.,]?\s*Ltd\.?)', text, re.IGNORECASE | re.MULTILINE)
        if sup_label:
            val = sup_label.group(1).strip()
            if 'Panasonic' not in val:
                val = re.sub(r'Co\.[\.,]*\s*Ltd', 'Co., Ltd', val)
                fields["supplier_name"] = {"value": val, "confidence": 85}

    # Buyer: find "Panasonic ... Co., Ltd." — normalize missing comma
    buy_clean = re.search(r'(Panasonic\s+Appliances\s+Vietnam\s+Co\.,?\s*Ltd\.)', text, re.IGNORECASE)
    if buy_clean:
        val = buy_clean.group(1).strip()
        # Normalize "Co. Ltd." → "Co., Ltd."
        val = re.sub(r'Co\.\s+Ltd', 'Co., Ltd', val)
        fields["buyer_name"] = {"value": val, "confidence": 92}
    else:
        buy_m = re.search(r'(Panasonic[\w\s]+Co\.,?\s*Ltd\.?)', text, re.IGNORECASE)
        if buy_m:
            val = buy_m.group(1).strip()
            val = re.sub(r'Co\.\s+Ltd', 'Co., Ltd', val)
            fields["buyer_name"] = {"value": val, "confidence": 80}

    # Tax code: Tax Code (MST): 0101248141
    tax_m = re.search(r'(?:Tax\s*Code)[^:]*[:\s]+(\d{10,13})', text, re.IGNORECASE)
    if tax_m:
        fields["tax_code"] = {"value": tax_m.group(1).strip(), "confidence": 90}

    # HS codes - collect all unique
    hs_all = list(set(re.findall(r'\b\d{4}\.\d{2}\.\d{2}\b', text)))
    if hs_all:
        fields["hs_codes"] = {"value": ", ".join(sorted(hs_all)), "confidence": 90}

    # Total packages: labeled or standalone "NNN cartons/CTNS/thung"
    tp = re.search(r'(?:total\s+packages|no\.?\s+of\s+packages|Tong\s+so\s+kien)[:\s]+(\d[\d,]*)', text, re.IGNORECASE)
    if tp:
        fields["total_packages"] = {"value": tp.group(1).strip(), "confidence": 85}
    else:
        tp2 = re.search(r'(\d[\d,]*)\s+(?:cartons|ctns|packages|thung)\b', text, re.IGNORECASE)
        if tp2:
            fields["total_packages"] = {"value": tp2.group(1).strip(), "confidence": 75}

    # Container numbers - standard ISO 6346: 4 letters + 7 digits
    containers = list(dict.fromkeys(re.findall(r'\b([A-Z]{4}\d{7})\b', text)))
    if containers:
        fields["container_no"] = {"value": " / ".join(containers[:4]), "confidence": 90}

    # Expected/Received qty from TOTALS area in warehouse receipt
    totals_m = re.search(r'TOTALS?.*?([\d,]{4,})\s+([\d,]{4,})', text)
    if totals_m:
        fields["expected_qty"] = {"value": totals_m.group(1).strip(), "confidence": 85}
        fields["received_qty"] = {"value": totals_m.group(2).strip(), "confidence": 85}
    else:
        totals_m2 = re.search(r'TOTALS\n(?:TOTALS\n)*(\d[\d,]+)\n(\d[\d,]+)', text)
        if totals_m2:
            fields["expected_qty"] = {"value": totals_m2.group(1).strip(), "confidence": 85}
            fields["received_qty"] = {"value": totals_m2.group(2).strip(), "confidence": 85}

    return fields

def classify_document(text):
    t = text.lower()
    # Score-based classification to avoid order-dependent misclassification
    scores = {'invoice': 0, 'packing_list': 0, 'bill_of_lading': 0, 'warehouse_receipt': 0}
    # Warehouse receipt (check first - most specific keywords, higher weight)
    for k in ['warehouse receipt', 'goods received', 'nhap kho', 'phieu nhap kho', 'wh receipt no', 'date received', 'goods received note', 'receiving party', 'inspection', 'ngay nhap kho', 'ben nhan hang', 'kiem tra chat luong']:
        if k in t: scores['warehouse_receipt'] += 25
    # Bill of lading
    for k in ['bill of lading', 'b/l no', 'booking ref', 'place of issue', 'sea waybill', 'ocean bill', 'van don duong bien', 'so van don', 'ngay phat hanh', 'ben thong bao']:
        if k in t: scores['bill_of_lading'] += 20
    # Packing list
    for k in ['packing list', 'carton no', 'packing list no', 'carton marking', 'shipping marks', 'phieu dong goi', 'so phieu dong goi', 'chi tiet dong goi', 'ky hieu van chuyen']:
        if k in t: scores['packing_list'] += 20
    # 'ctns' and 'thung' are weak — appear in B/L and WR too, lower weight
    if 'ctns' in t: scores['packing_list'] += 10
    # Invoice
    for k in ['commercial invoice', 'unit price', 'amount in words', 'subtotal', 'total cif value', 'proforma invoice', 'hoa don thuong mai', 'so hoa don', 'tong gia tri cif', 'chi tiet hang hoa', 'don gia']:
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
            # Use PSM 3 (auto segmentation) for better multi-column/table layout detection
            cfg3 = f'--oem 3 --psm 3 -l {lang}'
            txt3 = pytesseract.image_to_string(img, config=cfg3)
            # Also try PSM 6 (single block) and merge unique lines for completeness
            cfg6 = f'--oem 3 --psm 6 -l {lang}'
            txt6 = pytesseract.image_to_string(img, config=cfg6)
            # Merge: use PSM 3 as base, append unique non-empty lines from PSM 6
            lines3 = set(l.strip() for l in txt3.splitlines() if l.strip())
            extra = [l for l in txt6.splitlines() if l.strip() and l.strip() not in lines3]
            txt = txt3
            if extra:
                txt += '\n' + '\n'.join(extra)
            data = pytesseract.image_to_data(img, config=cfg3, output_type=pytesseract.Output.DICT)
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
