import os
from flask import Blueprint, jsonify, current_app
from routes.auth import login_required

aircon_bp = Blueprint('aircon', __name__)

COMPANY_MAP = {
    'doowon': '두원',
    'hanon':  '한온시스템',
    'wia':    '현대위아',
    'denso':  '덴소',
    'valeo':  '발레오',
    'mahle':  '말레',  
}


def _aircon_folder(company: str) -> str:
    return os.path.join(current_app.static_folder, 'aircon', company)


@aircon_bp.route('/api/aircon/<company>', methods=['GET'])
@login_required
def get_aircon_files(company):
    company_lower = company.lower()
    if company_lower not in COMPANY_MAP:
        return jsonify({'error': '알 수 없는 업체입니다.'}), 404

    folder = _aircon_folder(company_lower)
    if not os.path.isdir(folder):
        files = []
    else:
        files = sorted([
            f for f in os.listdir(folder)
            if f.lower().endswith('.html')
        ])

    return jsonify({
        'company':    company_lower,
        'company_kr': COMPANY_MAP[company_lower],
        'files':      files,
    })


@aircon_bp.route('/api/aircon/<company>/<path:filename>', methods=['GET'])
@login_required
def get_aircon_content(company, filename):
    company_lower = company.lower()
    if company_lower not in COMPANY_MAP:
        return jsonify({'error': '알 수 없는 업체입니다.'}), 404

    filepath = os.path.join(_aircon_folder(company_lower), filename)
    if not os.path.isfile(filepath):
        return jsonify({'error': '파일을 찾을 수 없습니다.'}), 404

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    return jsonify({
        'filename': filename,
        'content':  content,
    })