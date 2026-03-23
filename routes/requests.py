"""
routes/requests.py
AI팀 요청사항 기능 Blueprint (기능 8)
"""

from flask import Blueprint, request, jsonify, session
from routes.auth import login_required
import json
import os
from datetime import datetime

requests_bp = Blueprint('requests', __name__)

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'ai_requests.json')


def _load_data():
    """JSON 파일에서 요청사항 데이터 로드"""
    if not os.path.exists(DATA_FILE):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        _save_data([])
        return []
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_data(data):
    """JSON 파일에 요청사항 데이터 저장"""
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _next_id(data):
    """다음 ID 생성 (기존 최대 ID + 1)"""
    if not data:
        return 1
    return max(item['id'] for item in data) + 1


# ──────────────────────────────────────────
# GET /api/requests  →  요청 목록 반환
# ──────────────────────────────────────────
@requests_bp.route('/api/requests', methods=['GET'])
@login_required
def get_requests():
    data = _load_data()
    result = []
    for item in sorted(data, key=lambda x: x['id'], reverse=True):
        result.append({
            'id':            item['id'],
            'title':         item['title'],
            'author':        item['author'],
            'status':        item['status'],
            'created_at':    item['created_at'],
            'comment_count': len(item.get('comments', []))
        })
    return jsonify(result)


# ──────────────────────────────────────────
# POST /api/requests  →  새 요청 작성
# ──────────────────────────────────────────
@requests_bp.route('/api/requests', methods=['POST'])
@login_required
def create_request():
    body = request.get_json(force=True, silent=True) or {}
    title   = (body.get('title') or '').strip()
    content = (body.get('content') or '').strip()

    if not title or not content:
        return jsonify({'success': False, 'error': '제목과 내용을 입력해 주세요.'}), 400

    data = _load_data()
    new_item = {
        'id':         _next_id(data),
        'title':      title,
        'content':    content,
        'author':     session.get('username', '익명'),
        'status':     '처리중',
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'comments':   []
    }
    data.append(new_item)
    _save_data(data)
    return jsonify({'success': True, 'id': new_item['id']}), 201


# ──────────────────────────────────────────
# GET /api/requests/<id>  →  상세 조회
# ──────────────────────────────────────────
@requests_bp.route('/api/requests/<int:req_id>', methods=['GET'])
@login_required
def get_request(req_id):
    data = _load_data()
    item = next((x for x in data if x['id'] == req_id), None)
    if not item:
        return jsonify({'error': '요청을 찾을 수 없습니다.'}), 404
    return jsonify(item)


# ──────────────────────────────────────────
# POST /api/requests/<id>/comment  →  댓글 추가 (최대 5개)
# ──────────────────────────────────────────
@requests_bp.route('/api/requests/<int:req_id>/comment', methods=['POST'])
@login_required
def add_comment(req_id):
    data = _load_data()
    item = next((x for x in data if x['id'] == req_id), None)
    if not item:
        return jsonify({'success': False, 'error': '요청을 찾을 수 없습니다.'}), 404

    comments = item.get('comments', [])
    if len(comments) >= 5:
        return jsonify({'success': False, 'error': '댓글은 최대 5개까지 작성할 수 있습니다.'}), 400

    body    = request.get_json(force=True, silent=True) or {}
    content = (body.get('content') or '').strip()
    if not content:
        return jsonify({'success': False, 'error': '댓글 내용을 입력해 주세요.'}), 400

    new_comment = {
        'id':         (max((c['id'] for c in comments), default=0) + 1),
        'author':     session.get('username', '익명'),
        'content':    content,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M')
    }
    comments.append(new_comment)
    item['comments'] = comments
    _save_data(data)
    return jsonify({'success': True, 'comment': new_comment}), 201


# ──────────────────────────────────────────
# PATCH /api/requests/<id>/status  →  상태 변경 (admin 전용)
# ──────────────────────────────────────────
@requests_bp.route('/api/requests/<int:req_id>/status', methods=['PATCH'])
@login_required
def update_status(req_id):
    # admin 권한 체크
    if session.get('username') != 'admin':
        return jsonify({'success': False, 'error': '관리자만 상태를 변경할 수 있습니다.'}), 403

    data = _load_data()
    item = next((x for x in data if x['id'] == req_id), None)
    if not item:
        return jsonify({'error': '요청을 찾을 수 없습니다.'}), 404

    body   = request.get_json(force=True, silent=True) or {}
    status = (body.get('status') or '').strip()
    if status not in ('처리중', '완료'):
        return jsonify({'success': False, 'error': '올바른 상태값을 입력해 주세요. (처리중 / 완료)'}), 400

    item['status'] = status
    _save_data(data)
    return jsonify({'success': True, 'status': status})