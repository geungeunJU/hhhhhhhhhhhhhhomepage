# routes/compare.py
from flask import Blueprint, request, jsonify
from routes.auth import login_required

compare_bp = Blueprint('compare', __name__)

@compare_bp.route('/api/compare', methods=['POST'])
@login_required
def compare_text():
    """
    SR 비교분석 API
    클라이언트에서 보내준 text1, text2를 받아 처리하거나
    파일 업로드 시 서버에서 파싱 후 결과 반환
    """
    data = request.json
    text1 = data.get('text1', '')
    text2 = data.get('text2', '')
    
    # 실제 diff는 클라이언트사이드(js)에서 diff-match-patch 라이브러리를 사용하기로 했으므로,
    # 여기서는 데이터 유효성 검증 및 간단한 처리 로직만 수행
    # 필요 시 서버측에서 파일 파싱(pdf/docx to text) 로직 추가 가능
    
    return jsonify({
        'status': 'success',
        'text1': text1,
        'text2': text2
    })