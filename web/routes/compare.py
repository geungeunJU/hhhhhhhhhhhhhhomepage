"""
routes/compare.py
기능4: SR 비교분석 Blueprint
"""

import os
import io
from flask import Blueprint, request, jsonify
from routes.auth import login_required

compare_bp = Blueprint('compare', __name__)


def extract_text_from_file(file) -> str:
    """업로드된 파일에서 텍스트 추출 (.txt / .docx / .pdf)"""
    filename = file.filename.lower()

    if filename.endswith('.txt'):
        return file.read().decode('utf-8', errors='ignore')

    elif filename.endswith('.docx'):
        try:
            import docx
            doc = docx.Document(io.BytesIO(file.read()))
            return '\n'.join(p.text for p in doc.paragraphs)
        except ImportError:
            return "[오류] python-docx 패키지가 설치되지 않았습니다."

    elif filename.endswith('.pdf'):
        try:
            import pdfplumber
            text_parts = []
            with pdfplumber.open(io.BytesIO(file.read())) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        text_parts.append(t)
            return '\n'.join(text_parts)
        except ImportError:
            return "[오류] pdfplumber 패키지가 설치되지 않았습니다."

    return "[오류] 지원하지 않는 파일 형식입니다."


def compute_similarity(text1: str, text2: str) -> float:
    """두 텍스트의 단어 기반 유사도(Jaccard) 반환 (0~100)"""
    if not text1 or not text2:
        return 0.0
    set1 = set(text1.split())
    set2 = set(text2.split())
    if not set1 and not set2:
        return 100.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return round(intersection / union * 100, 1) if union else 0.0


@compare_bp.route('/api/compare', methods=['POST'])
@login_required
def compare_texts():
    """
    두 문서를 받아 diff 결과와 유사도를 반환.
    - JSON body: { text1, text2 }
    - FormData:  file1, file2 (파일 업로드)
    반환: { diff_html, similarity, text1, text2 }
    """
    text1 = ''
    text2 = ''

    # ── FormData (파일 업로드) ──────────────────────────────
    if request.files:
        file1 = request.files.get('file1')
        file2 = request.files.get('file2')
        if file1:
            text1 = extract_text_from_file(file1)
        if file2:
            text2 = extract_text_from_file(file2)

    # ── JSON body ─────────────────────────────────────────
    else:
        data = request.get_json(silent=True) or {}
        text1 = data.get('text1', '')
        text2 = data.get('text2', '')

    if not text1 and not text2:
        return jsonify({'error': '비교할 텍스트가 없습니다.'}), 400

    similarity = compute_similarity(text1, text2)

    # diff_html은 프론트엔드(diff-match-patch)가 생성하므로
    # 서버는 원본 텍스트와 유사도만 반환
    return jsonify({
        'text1': text1,
        'text2': text2,
        'similarity': similarity,
        'diff_html': ''   # 클라이언트 사이드 diff 사용
    })