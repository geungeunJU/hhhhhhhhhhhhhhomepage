import os
import re
import pdfplumber
from flask import Blueprint, jsonify, request, current_app, send_file, abort

patents_bp = Blueprint('patents', __name__)

COMPANY_MAP = {
    'hanon': '한온시스템',
    'wia': '현대위아',
    'valeo': '발레오',
    'denso': '덴소',
    'doowon': '두원공조',
    'hyundai': '현대자동차',
}

PRODUCT_KEYWORDS = {
    'system': 'system',
    'compressor': 'compressor',
    'valve': 'valve',
    'software': 'software',
}

PRODUCT_LABEL_MAP = {
    'system': '시스템',
    'compressor': '압축기',
    'valve': '밸브',
    'software': '소프트웨어',
    'etc': '기타',
}


def classify_file(filename: str):
    fname_lower = filename.lower()
    company_key = 'etc'
    for key in COMPANY_MAP:
        if key in fname_lower:
            company_key = key
            break
    product_key = 'etc'
    for key in PRODUCT_KEYWORDS:
        if key in fname_lower:
            product_key = key
            break
    return company_key, product_key


def extract_year(filename: str):
    """파일명에서 년도 추출 (예: ..._2025.pdf → 2025)"""
    match = re.search(r'_(\d{4})(?:\.pdf)?$', filename, re.IGNORECASE)
    return match.group(1) if match else '-'


def extract_invention_title(filepath: str):
    """PDF에서 발명의 명칭 추출"""
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages[:2]:
                text = page.extract_text()
                if not text:
                    continue
                # "(54) 제목" 또는 "발명의 명칭 제목" 패턴 모두 시도
                for pattern in [
                    r'\(54\)\s*(.+?)(?:\n|$)',
                    r'발명의\s*명칭\s*(.+?)(?:\n|$)',
                ]:
                    match = re.search(pattern, text)
                    if match:
                        title = match.group(1).strip()
                        # "발명의 명칭", "고안의 명칭" 등 앞부분 제거
                        title = re.sub(r'^(발명의\s*명칭|고안의\s*명칭)\s*', '', title).strip()
                        if title:
                            return title
    except Exception:
        pass
    return None


@patents_bp.route('/api/patents/meta')
def patents_meta():
    companies = [{'key': 'all', 'label': '전체'}]
    for key, label in COMPANY_MAP.items():
        companies.append({'key': key, 'label': label})
    products = [{'key': 'all', 'label': '전체'}]
    for key, label in PRODUCT_LABEL_MAP.items():
        products.append({'key': key, 'label': label})
    return jsonify({'companies': companies, 'products': products})


@patents_bp.route('/api/patents')
def patents_list():
    company_filter = request.args.get('company', 'all')
    product_filter = request.args.get('product', 'all')

    base_dir     = os.path.join(current_app.static_folder, 'patent')
    pdf_done_dir = os.path.join(base_dir, 'pdf_done')
    html_dir     = os.path.join(base_dir, 'html')

    if not os.path.exists(pdf_done_dir):
        return jsonify([])

    results = []
    for filename in os.listdir(pdf_done_dir):
        if not filename.lower().endswith('.pdf'):
            continue

        company_key, product_key = classify_file(filename)

        if company_filter != 'all' and company_key != company_filter:
            continue
        if product_filter != 'all' and product_key != product_filter:
            continue

        # HTML 매칭
        pdf_basename = os.path.splitext(filename)[0]
        html_filename = None
        has_html = False
        if os.path.exists(html_dir):
            for html_file in os.listdir(html_dir):
                if html_file.lower().endswith('.html') and html_file.startswith(pdf_basename.split('_')[0]):
                    html_filename = html_file
                    has_html = True
                    break

        # 년도 추출
        year = extract_year(filename)

        # 발명 제목 추출
        filepath = os.path.join(pdf_done_dir, filename)
        invention_title = extract_invention_title(filepath)
        title = invention_title if invention_title else pdf_basename

        results.append({
            'filename':   filename,
            'title':      title,
            'company':    company_key,
            'company_kr': COMPANY_MAP.get(company_key, '기타'),
            'product':    product_key,
            'product_kr': PRODUCT_LABEL_MAP.get(product_key, '기타'),
            'year':       year,
            'pdf_url':    f'/api/patents/view/pdf_done/{filename}',
            'html_url':   f'/api/patents/view/html/{html_filename}' if has_html and html_filename else None,
            'has_html':   has_html,
        })

    results.sort(key=lambda x: x['filename'])
    return jsonify(results)


@patents_bp.route('/api/patents/view/<path:filename>')
def patents_view(filename):
    filepath = os.path.join(current_app.static_folder, 'patent', filename)
    if not os.path.isfile(filepath):
        abort(404)
    if filename.lower().endswith('.html'):
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    return send_file(filepath, mimetype='application/pdf')