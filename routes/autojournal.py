# routes/autojournal.py
# 오토저널 수집 및 표시 모듈
import os, sys, re, json, requests, threading, time
from datetime import datetime
from flask import Blueprint, jsonify, request, session
from routes.auth import login_required

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

autojournal_bp = Blueprint('autojournal', __name__)

# ── 캐시 설정 ──────────────────────────────────────────────
# 영문 경로 사용 (한글 경로 WinError 방지)
CACHE_DIR = r"C:\cache\ido_portal\autojournal"
os.makedirs(CACHE_DIR, exist_ok=True)

_mem_cache = {}        # 메모리 캐시 (서버 재시작 전까지 유지)
_cache_lock = threading.Lock()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://online.webbook.kr/",
}
BASE_URL = "https://online.webbook.kr/books"


def _cache_get(key):
    """메모리 캐시 우선, 없으면 파일 캐시 확인"""
    with _cache_lock:
        entry = _mem_cache.get(key)
    if entry:
        return entry

    # 파일 캐시 확인
    path = os.path.join(CACHE_DIR, f"{key}.json")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 메모리에도 올려두기
            with _cache_lock:
                _mem_cache[key] = data
            return data
        except Exception:
            pass
    return None


def _cache_set(key, data):
    """메모리 + 파일 동시 저장"""
    data['_cached_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _cache_lock:
        _mem_cache[key] = data
    try:
        path = os.path.join(CACHE_DIR, f"{key}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[오토저널] 파일 캐시 저장 실패: {e}")


def _cache_valid(data, max_days=30):
    """캐시 유효성 확인 (오토저널은 30일)"""
    cached_at = data.get('_cached_at', '')
    if not cached_at:
        return False
    try:
        dt = datetime.strptime(cached_at, "%Y-%m-%d %H:%M")
        return (datetime.now() - dt).days < max_days
    except Exception:
        return False



# ── 호 목록 자동 탐색 ────────────────────────────────────

BOOKCASE_URL = "https://online.webbook.kr/bookcase/hsqxe"


def _scrape_issues():
    """bookcase HTML의 bookData JSON에서 실제 호 목록 파싱"""
    try:
        r = requests.get(BOOKCASE_URL, headers=HEADERS, timeout=15)
        html = r.text

        # bookData: [...] 배열 추출
        m = re.search(r'bookData:\s*(\[.*?\])\s*[,}]', html, re.DOTALL)
        if not m:
            print("[오토저널] bookData 패턴 미발견")
            return []

        books = json.loads(m.group(1))
        print(f"[오토저널] bookData 파싱: {len(books)}개")

        issues = []
        for b in books:
            title = b.get('title', '')      # "오토저널 2025.03"
            blink = b.get('bLink', '')      # "oagn"
            wb_url = f"https://online.webbook.kr/books/{blink}/"

            # 제목에서 연도/월 추출
            tm = re.search(r'(\d{4})[.\-](\d{1,2})', title)
            if tm:
                year, month = int(tm.group(1)), int(tm.group(2))
                label = f"{year}년 {month}월호"
            else:
                year, month = 0, 0
                label = title

            if blink:
                issues.append({
                    "id":    blink,
                    "year":  year,
                    "month": month,
                    "label": label,
                    "title": title,
                    "url":   wb_url,
                })

        issues.sort(key=lambda x: (x['year'], x['month']), reverse=True)
        print(f"[오토저널] bookcase 크롤링 완료: {len(issues)}개")
        return issues

    except Exception as e:
        print(f"[오토저널] bookcase 크롤링 실패: {e}")
        return []


def _known_issues():
    """최근 36개월(3년) 호 목록 - bookcase 크롤링 실패 시 폴백용"""
    now = datetime.now()
    BASE_YEAR = 2026
    BASE_VOL  = 48
    issues = []
    for delta in range(36):
        total_months = now.year * 12 + (now.month - 1) - delta
        year  = total_months // 12
        month = total_months % 12 + 1
        vol   = BASE_VOL - (BASE_YEAR - year)
        issue_id = f"auto{vol:02d}-{month:02d}"
        issues.append({
            "id":    issue_id,
            "year":  year,
            "month": month,
            "vol":   vol,
            "label": f"{year}년 {month}월호 (제{vol}권 {month}호)",
            "url":   f"https://online.webbook.kr/books/{issue_id}/",
        })
    return issues


def _discover_issues():
    """bookcase 크롤링 → 실패 시 _known_issues() 폴백"""
    # 메모리 캐시 확인
    with _cache_lock:
        entry = _mem_cache.get('issues')
    if entry and isinstance(entry, dict) and entry.get('issues'):
        return entry['issues']

    # bookcase 페이지 크롤링 시도
    issues = _scrape_issues()

    # 크롤링 실패하면 폴백
    if not issues:
        print("[오토저널] bookcase 크롤링 실패, 폴백 사용")
        issues = _known_issues()

    with _cache_lock:
        _mem_cache['issues'] = {'issues': issues}
    return issues


# ── 특정 호 텍스트 수집 ──────────────────────────────────

def _fetch_issue_text(issue_id):
    """search_config.js에서 페이지별 텍스트를 수집합니다."""
    cached = _cache_get(f'issue_{issue_id}')
    if cached and _cache_valid(cached, max_days=30):
        return cached

    url = f"{BASE_URL}/{issue_id}/files/search/search_config.js"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        text = r.text
    except Exception as e:
        print(f"[오토저널] {issue_id} 수집 실패: {e}")
        return None

    # textForPages 배열 파싱
    start = text.find('var textForPages =[')
    if start < 0:
        return None

    bracket_start = text.find('[', start)
    depth, end = 0, bracket_start
    for i, ch in enumerate(text[bracket_start:], bracket_start):
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                end = i
                break

    pages_raw = text[bracket_start:end+1]

    # JSON 파싱 시도, 실패 시 수동 파싱
    pages = []
    try:
        pages = json.loads(pages_raw)
    except Exception:
        entries = re.findall(r'"((?:[^"\\]|\\.)*)"', pages_raw)
        for entry in entries:
            try:
                decoded = entry.encode('utf-8').decode('unicode_escape', errors='replace')
                pages.append(decoded)
            except Exception:
                pages.append(entry)

    # 페이지 텍스트 정리 + 목차 추출
    clean_pages = []
    toc = []
    for i, pg in enumerate(pages):
        if not pg or not pg.strip():
            clean_pages.append("")
            continue
        clean = pg.replace('\r\n', '\n').replace('\r', '\n').strip()
        clean_pages.append(clean)
        first_line = clean.split('\n')[0].strip()
        if first_line and len(first_line) > 5 and len(first_line) < 80:
            toc.append({"page": i + 1, "title": first_line})

    result = {
        "issue_id": issue_id,
        "total_pages": len(clean_pages),
        "pages": clean_pages,
        "toc": toc,
        "_collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    _cache_set(f'issue_{issue_id}', result)
    print(f"[오토저널] {issue_id} 수집 완료: {len(clean_pages)}페이지")
    return result


# ── API 엔드포인트 ────────────────────────────────────────

@autojournal_bp.route("/api/autojournal/issues")
@login_required
def get_issues():
    issues = _discover_issues()
    return jsonify({"status": "success", "issues": issues})


@autojournal_bp.route("/api/autojournal/refresh", methods=["POST"])
@login_required
def refresh_issues():
    """호 목록 캐시 초기화 후 36개 재반환"""
    with _cache_lock:
        _mem_cache.pop('issues', None)
    issues = _discover_issues()
    return jsonify({"status": "success", "issues": issues, "count": len(issues)})


@autojournal_bp.route("/api/autojournal/issue/<issue_id>")
@login_required
def get_issue(issue_id):
    # issue_id 검증 (보안)
    if not re.match(r'^auto\d{2}-\d{2}$', issue_id):
        return jsonify({"status": "error", "message": "잘못된 호 ID"}), 400
    data = _fetch_issue_text(issue_id)
    if not data:
        return jsonify({"status": "error", "message": "수집 실패"}), 404
    # 페이지 텍스트는 용량이 크므로 목차만 반환
    return jsonify({
        "status": "success",
        "issue_id": issue_id,
        "total_pages": data["total_pages"],
        "toc": data["toc"],
        "collected_at": data.get("_collected_at", ""),
    })


@autojournal_bp.route("/api/autojournal/page/<issue_id>/<int:page_num>")
@login_required
def get_page(issue_id, page_num):
    if not re.match(r'^auto\d{2}-\d{2}$', issue_id):
        return jsonify({"status": "error", "message": "잘못된 호 ID"}), 400
    data = _fetch_issue_text(issue_id)
    if not data:
        return jsonify({"status": "error", "message": "수집 실패"}), 404
    pages = data.get("pages", [])
    if page_num < 1 or page_num > len(pages):
        return jsonify({"status": "error", "message": "페이지 범위 초과"}), 400
    return jsonify({
        "status":    "success",
        "issue_id":  issue_id,
        "page_num":  page_num,
        "total":     len(pages),
        "text":      pages[page_num - 1],
        "image_url": f"https://online.webbook.kr/books/{issue_id}/files/large/",
    })


@autojournal_bp.route("/api/autojournal/proxy/<issue_id>")
@autojournal_bp.route("/api/autojournal/proxy/<issue_id>/<path:subpath>")
def proxy_issue(issue_id, subpath=""):
    """오토저널 사이트를 Flask가 프록시하여 iframe에서 표시"""
    from flask import session, Response
    if 'username' not in session:
        return "Unauthorized", 401

    if not re.match(r'^[a-zA-Z0-9_-]{2,20}$', issue_id):
        return "잘못된 호 ID", 400

    if subpath:
        target_url = f"{BASE_URL}/{issue_id}/{subpath}"
    else:
        target_url = f"{BASE_URL}/{issue_id}/"

    if request.query_string:
        target_url += "?" + request.query_string.decode()

    try:
        resp = requests.get(target_url, headers=HEADERS, timeout=15)
        content_type = resp.headers.get("Content-Type", "text/html")

        if "text/html" in content_type:
            html = resp.text
            base_tag = f'<base href="https://online.webbook.kr/books/{issue_id}/">'
            html = html.replace("<head>", f"<head>{base_tag}", 1)
            if "<head>" not in html:
                html = base_tag + html
            return Response(html, content_type="text/html; charset=utf-8")

        return Response(resp.content, content_type=content_type)
    except Exception as e:
        return f"프록시 오류: {e}", 502


@autojournal_bp.route("/api/autojournal/search/<issue_id>")
@login_required
def search_issue(issue_id):
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"status": "error", "message": "검색어가 없습니다."}), 400
    if not re.match(r'^auto\d{2}-\d{2}$', issue_id):
        return jsonify({"status": "error", "message": "잘못된 호 ID"}), 400

    data = _fetch_issue_text(issue_id)
    if not data:
        return jsonify({"status": "error", "message": "수집 실패"}), 404

    results = []
    for i, pg in enumerate(data.get("pages", [])):
        if query.lower() in pg.lower():
            # 검색어 주변 텍스트 추출
            idx = pg.lower().find(query.lower())
            snippet = pg[max(0, idx-50):idx+150].replace('\n', ' ')
            results.append({"page": i + 1, "snippet": snippet})

    return jsonify({
        "status":  "success",
        "query":   query,
        "results": results,
        "count":   len(results),
    })