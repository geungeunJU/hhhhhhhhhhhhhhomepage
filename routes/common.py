import os
import time
from datetime import datetime
from flask import Blueprint, jsonify, session, render_template
from routes.auth import login_required

common_bp = Blueprint('common', __name__)

NOTICES_DIR = os.path.join(os.path.dirname(__file__), '..', 'static', 'notices')

# ──────────────────────────────────────────
# 접속자 추적 데이터
# ──────────────────────────────────────────

# 현재 접속자: { username: 마지막_접속_시각 }
# 5분(300초) 이내에 활동한 사용자를 '현재 접속자'로 간주
_active_visitors = {}
ACTIVE_TIMEOUT = 300  # 5분

# 일일 누적 접속자: { 'date': 'YYYY-MM-DD', 'users': set(username, ...) }
_daily_visitors = {
    'date': datetime.now().strftime('%Y-%m-%d'),
    'users': set()
}


def _record_visitor(username):
    """
    접속자 기록 함수
    - 30초 이내 같은 사용자 재호출 무시 (중복 방지)
    - 현재 접속자 목록에 username과 현재 시각 저장
    - 일일 누적 접속자 set에 username 추가
    - 날짜가 바뀌면 일일 누적 자동 초기화
    """
    global _daily_visitors

    now = time.time()
    today = datetime.now().strftime('%Y-%m-%d')

    # 30초 이내 같은 사용자 재호출 무시
    if username in _active_visitors:
        if now - _active_visitors[username] < 30:
            return

    # 현재 접속자 갱신 (마지막 활동 시각 업데이트)
    _active_visitors[username] = now

    # 날짜가 바뀌었으면 일일 누적 초기화
    if _daily_visitors['date'] != today:
        _daily_visitors = {
            'date': today,
            'users': set()
        }

    # 오늘 접속자 목록에 추가 (set이라 중복 자동 제거)
    _daily_visitors['users'].add(username)


def _count_active_visitors():
    """
    현재 접속자 수 반환
    - ACTIVE_TIMEOUT(5분) 이내에 활동한 사용자만 카운트
    - 오래된 기록은 자동 삭제
    """
    now = time.time()

    # 5분 이상 된 사용자 제거
    expired = [u for u, t in _active_visitors.items() if now - t >= ACTIVE_TIMEOUT]
    for u in expired:
        del _active_visitors[u]

    return len(_active_visitors)


def _count_daily_visitors():
    """
    일일 누적 접속자 수 반환
    - 오늘 날짜의 고유 사용자 수
    """
    today = datetime.now().strftime('%Y-%m-%d')

    # 날짜가 바뀌었으면 0 반환
    if _daily_visitors['date'] != today:
        return 0

    return len(_daily_visitors['users'])


# ──────────────────────────────────────────
# 메인 페이지
# ──────────────────────────────────────────
@common_bp.route('/main')
@login_required
def main():
    username = session.get('username', '')
    role = session.get('role', 'user')
    _record_visitor(username)
    return render_template('main.html', username=username, role=role)


# ──────────────────────────────────────────
# 공지사항 API
# 파일명 형식: YYYYMMDD_제목.txt
# 응답: [{ date, title, filename }, ...]  최신순
# ──────────────────────────────────────────
@common_bp.route('/api/notices')
@login_required
def get_notices():
    notices = []
    if not os.path.isdir(NOTICES_DIR):
        return jsonify(notices)

    for fname in sorted(os.listdir(NOTICES_DIR), reverse=True):
        if not fname.endswith('.txt'):
            continue
        base = fname[:-4]
        parts = base.split('_', 1)
        if len(parts) == 2:
            date_str, title = parts
            if len(date_str) == 8 and date_str.isdigit():
                display_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            else:
                display_date = date_str
            notices.append({
                'filename': fname,
                'title': title,
                'date': display_date,
            })

    return jsonify(notices)


@common_bp.route('/api/notices/<filename>')
@login_required
def get_notice_content(filename):
    """공지사항 파일 내용 반환"""
    safe_name = os.path.basename(filename)
    file_path = os.path.join(NOTICES_DIR, safe_name)

    if not os.path.isfile(file_path):
        return jsonify({'error': '파일을 찾을 수 없습니다.'}), 404

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    return jsonify({'filename': safe_name, 'content': content})


# ──────────────────────────────────────────
# 접속자 카운트 API
# 프론트엔드에서 30초마다 폴링
# 응답: { active: 현재접속자수, daily: 일일누적접속자수, date: 오늘날짜 }
# ──────────────────────────────────────────
@common_bp.route('/api/visitors')
@login_required
def get_visitors():
    # 조회만 (기록 없음)
    active = _count_active_visitors()
    daily = _count_daily_visitors()
    today = datetime.now().strftime('%Y-%m-%d')

    return jsonify({
        'active': active,   # 현재 접속자 수 (5분 이내)
        'daily': daily,     # 일일 누적 접속자 수
        'date': today       # 오늘 날짜
    })