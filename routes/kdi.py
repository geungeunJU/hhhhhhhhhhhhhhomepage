# routes/kdi.py
import os, re, json, requests, warnings, time, threading
from datetime import datetime
from flask import Blueprint, jsonify, request
from routes.auth import login_required

warnings.filterwarnings('ignore')

kdi_bp = Blueprint('kdi', __name__)

BASE = "https://eiec.kdi.re.kr"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://eiec.kdi.re.kr/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
KEYWORDS = ["자동차", "모빌리티"]

# 영문 경로 캐시 (한글 경로 WinError 방지)
CACHE_DIR = r"C:\cache\ido_portal\kdi"
os.makedirs(CACHE_DIR, exist_ok=True)

_mem_cache = {}
_cache_lock = threading.Lock()

def _get(url, params=None):
    return requests.get(url, params=params, headers=HEADERS, timeout=15, verify=False)

def _load_cache(key, max_age_hours=6):
    # 메모리 우선
    with _cache_lock:
        entry = _mem_cache.get(key)
    if entry:
        cached_at = entry.get('_cached_at', '')
        if cached_at:
            try:
                dt = datetime.strptime(cached_at, "%Y-%m-%d %H:%M")
                if (datetime.now() - dt).total_seconds() / 3600 < max_age_hours:
                    return entry
            except Exception:
                pass

    # 파일 캐시
    path = os.path.join(CACHE_DIR, f"{key}.json")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            cached_at = data.get('_cached_at', '')
            if cached_at:
                dt = datetime.strptime(cached_at, "%Y-%m-%d %H:%M")
                if (datetime.now() - dt).total_seconds() / 3600 < max_age_hours:
                    with _cache_lock:
                        _mem_cache[key] = data
                    return data
        except Exception:
            pass
    return None

def _save_cache(key, data):
    data['_cached_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _cache_lock:
        _mem_cache[key] = data
    try:
        path = os.path.join(CACHE_DIR, f"{key}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[KDI] {key} 캐시 저장 완료")
    except Exception as e:
        print(f"[KDI] 파일 캐시 저장 실패: {e}")


# ── 1. KDI 발행물 파싱 ─────────────────────────────────────

def _fetch_nara(keyword):
    """나라경제 발행물 목록 수집"""
    r = _get(f"{BASE}/publish/naraMainSearch.do", {"searchKey": keyword})
    html = r.text
    items = []

    # naraView.do?fcode=...&cidx=XXXXX 기준 파싱
    pattern = re.compile(
        r'href="([^"]*naraView\.do\?[^"]*cidx=(\d+)[^"]*)"'
        r'[\s\S]{0,600}?'
        r'<p>([\s\S]+?)</p>'
    )
    seen_cidx = set()
    for m in pattern.finditer(html):
        href  = m.group(1)
        cidx  = m.group(2)
        title = re.sub(r'<[^>]+>', '', m.group(3)).strip()
        if cidx in seen_cidx or not title:
            continue
        seen_cidx.add(cidx)

        start = m.start()
        snippet = html[start:start+800]
        # 카테고리: <em class="...">특집</em>
        cat_m    = re.search(r'<em[^>]*>([^<]+)</em>', snippet)
        # 저자: 첫 번째 <span>
        author_m = re.search(r'<span>([^<]+)</span>', snippet)
        # 날짜: YYYY년 MM월호
        date_m   = re.search(r'(\d{4}년 \d{2}월호)', snippet)

        items.append({
            "category": cat_m.group(1).strip() if cat_m else '',
            "title":    title,
            "author":   author_m.group(1).strip() if author_m else '',
            "date":     date_m.group(1) if date_m else '',
            "url":      BASE + "/publish/" + href.lstrip('./'),
        })
    print(f"[KDI nara] '{keyword}' → {len(items)}건")
    return items


def _collect_nara():
    cache = _load_cache("nara")
    if cache:
        return cache

    all_items = []
    seen = set()
    for kw in KEYWORDS:
        items = _fetch_nara(kw)
        for it in items:
            key = it['url']
            if key not in seen:
                seen.add(key)
                it['keyword'] = kw
                all_items.append(it)
        time.sleep(0.5)

    # 날짜 기준 최신순 정렬
    def sort_key(x):
        m = re.search(r'(\d{4})년 (\d{2})월호', x.get('date', ''))
        if m:
            return (int(m.group(1)), int(m.group(2)))
        return (0, 0)
    all_items.sort(key=sort_key, reverse=True)

    result = {"status": "success", "items": all_items, "count": len(all_items)}
    _save_cache("nara", result)
    return result


# ── 2. KDI 경제정책자료 파싱 ──────────────────────────────

def _fetch_material(keyword):
    r = _get(f"{BASE}/policy/materialList.do",
             {"search_txt": keyword, "pg": "1", "pp": "30", "type": "A", "device": "pc"})
    html = r.text
    items = []

    # num= 기준으로 분리해서 각 블록 파싱
    parts = re.split(r'materialView\.do\?num=', html)
    seen_num = set()
    for part in parts[1:]:  # 첫 번째는 헤더
        num_m = re.match(r'(\d+)', part)
        if not num_m:
            continue
        num = num_m.group(1)
        if num in seen_num:
            continue
        seen_num.add(num)

        # 이 블록에서 <p>제목</p> 찾기
        title_m = re.search(r'<p>(.*?)</p>', part[:2000])
        if not title_m:
            continue
        title = title_m.group(1).strip()
        if not title:
            continue

        # 제목 이후에서 기관, 날짜 찾기
        after = part[title_m.end():]
        org_m  = re.search(r'<span>([^<\d][^<]*?)</span>', after[:500])
        date_m = re.search(r'(\d{4}\.\d{2}\.\d{2})', after[:500])

        items.append({
            "title": title,
            "org":   org_m.group(1).strip() if org_m else '',
            "date":  date_m.group(1) if date_m else '',
            "url":   f"{BASE}/policy/materialView.do?num={num}",
            "num":   num,
        })
    print(f"[KDI material] '{keyword}' → {len(items)}건")
    return items


def _collect_material():
    cache = _load_cache("material")
    if cache:
        return cache

    all_items = []
    seen = set()
    for kw in KEYWORDS:
        items = _fetch_material(kw)
        for it in items:
            if it['num'] not in seen:
                seen.add(it['num'])
                it['keyword'] = kw
                all_items.append(it)
        time.sleep(0.5)

    all_items.sort(key=lambda x: x.get('date', ''), reverse=True)
    result = {"status": "success", "items": all_items, "count": len(all_items)}
    _save_cache("material", result)
    return result


# ── 3. KDI 국내연구자료 파싱 ──────────────────────────────

def _fetch_domestic(keyword):
    r = _get(f"{BASE}/policy/domesticList.do",
             {"search_txt": keyword, "pg": "1", "pp": "30", "type": "A"})
    html = r.text
    items = []

    parts = re.split(r'domesticView\.do\?ac=', html)
    seen_ac = set()
    for part in parts[1:]:
        ac_m = re.match(r'(\d+)', part)
        if not ac_m:
            continue
        ac = ac_m.group(1)
        if ac in seen_ac:
            continue
        seen_ac.add(ac)

        title_m = re.search(r'<(?:p|strong)>(.*?)</(?:p|strong)>', part[:2000])
        if not title_m:
            continue
        title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()
        if not title:
            continue

        after  = part[title_m.end():]
        org_m  = re.search(r'<span>([^<\d][^<]*?)</span>', after[:500])
        date_m = re.search(r'(\d{4}\.\d{2}\.\d{2})', after[:500])

        items.append({
            "title": title,
            "org":   org_m.group(1).strip() if org_m else '',
            "date":  date_m.group(1) if date_m else '',
            "url":   f"{BASE}/policy/domesticView.do?ac={ac}",
            "ac":    ac,
        })
    print(f"[KDI domestic] '{keyword}' → {len(items)}건")
    return items


def _collect_domestic():
    cache = _load_cache("domestic")
    if cache:
        return cache

    all_items = []
    seen = set()
    for kw in KEYWORDS:
        items = _fetch_domestic(kw)
        for it in items:
            if it['ac'] not in seen:
                seen.add(it['ac'])
                it['keyword'] = kw
                all_items.append(it)
        time.sleep(0.5)

    all_items.sort(key=lambda x: x.get('date', ''), reverse=True)
    result = {"status": "success", "items": all_items, "count": len(all_items)}
    _save_cache("domestic", result)
    return result


# ── API 엔드포인트 ─────────────────────────────────────────

def _filter_by_keyword(data, keyword):
    """keyword 파라미터로 필터링
    - 항목의 'keyword' 필드가 일치하면 우선 반환
    - 없으면 제목/카테고리에서 검색
    - keyword가 없으면 전체 반환
    """
    if not keyword:
        return data
    kw = keyword.strip()
    items = data.get('items', [])

    # 1순위: keyword 필드 정확히 일치
    by_field = [it for it in items if it.get('keyword', '') == kw]
    if by_field:
        result = dict(data)
        result['items'] = by_field
        return result

    # 2순위: 제목/카테고리에 포함
    kw_lower = kw.lower()
    by_text = [it for it in items
               if kw_lower in it.get('title', '').lower()
               or kw_lower in it.get('category', '').lower()
               or kw_lower in it.get('org', '').lower()]
    result = dict(data)
    result['items'] = by_text
    return result


@kdi_bp.route("/api/kdi/nara")
@login_required
def api_nara():
    try:
        kw = request.args.get('keyword', '')
        return jsonify(_filter_by_keyword(_collect_nara(), kw))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@kdi_bp.route("/api/kdi/material")
@login_required
def api_material():
    try:
        kw = request.args.get('keyword', '')
        return jsonify(_filter_by_keyword(_collect_material(), kw))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@kdi_bp.route("/api/kdi/domestic")
@login_required
def api_domestic():
    try:
        kw = request.args.get('keyword', '')
        return jsonify(_filter_by_keyword(_collect_domestic(), kw))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@kdi_bp.route("/api/kdi/pdf-url")
@login_required
def api_pdf_url():
    """KDI 상세 페이지에서 PDF 다운로드 URL 추출"""
    page_url = request.args.get('page', '')
    if not page_url or 'eiec.kdi.re.kr' not in page_url:
        return jsonify({"pdf_url": None, "error": "잘못된 URL"}), 400
    try:
        r = _get(page_url)
        html = r.text
        # PDF 링크 패턴 탐색
        pdf = None
        patterns = [
            r'href="([^"]+\.pdf[^"]*)"',
            r'href="([^"]+/download[^"]*)"',
            r"'([^']+fileDown[^']+)'",
            r'href="([^"]+fileDown[^"]*)"',
            r'location\.href\s*=\s*["\']([^"\']+)["\']',
        ]
        for pat in patterns:
            m = re.search(pat, html)
            if m:
                url = m.group(1)
                if not url.startswith('http'):
                    url = 'https://eiec.kdi.re.kr' + url
                pdf = url
                break
        return jsonify({"pdf_url": pdf, "page_url": page_url})
    except Exception as e:
        return jsonify({"pdf_url": None, "error": str(e)}), 500


@kdi_bp.route("/api/kdi/proxy")
@login_required
def api_proxy():
    """KDI 페이지를 프록시로 표시 (PDF URL 못 찾을 때 대체)"""
    url = request.args.get('url', '')
    if not url or 'eiec.kdi.re.kr' not in url:
        return "잘못된 URL", 400
    try:
        from flask import Response
        r = _get(url)
        html = r.text
        # base 태그 삽입
        base = '<base href="https://eiec.kdi.re.kr/" target="_blank">'
        html = html.replace('<head>', f'<head>{base}', 1)
        return Response(html, content_type='text/html; charset=utf-8')
    except Exception as e:
        return f"프록시 오류: {e}", 502


@kdi_bp.route("/api/kdi/refresh/<source>", methods=["POST"])
@login_required
def api_refresh(source):
    """캐시 강제 갱신 (admin용)"""
    from flask import session
    if session.get('role') != 'admin':
        return jsonify({"status": "error", "message": "권한 없음"}), 403
    # 메모리 + 파일 캐시 삭제
    with _cache_lock:
        _mem_cache.pop(source, None)
    cache_file = os.path.join(CACHE_DIR, f"{source}.json")
    if os.path.exists(cache_file):
        try:
            os.remove(cache_file)
        except Exception:
            pass
    if source == "nara":
        data = _collect_nara()
    elif source == "material":
        data = _collect_material()
    elif source == "domestic":
        data = _collect_domestic()
    else:
        return jsonify({"status": "error", "message": "알 수 없는 소스"}), 400
    return jsonify({"status": "success", "count": data.get("count", 0)})