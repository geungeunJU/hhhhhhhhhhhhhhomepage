# -*- coding: utf-8 -*-
# routes/news.py
# ============================================================
#  뉴스 자동화 모듈 (기능 1/2/3 + 크롤링 내장)
#
#  [기능 1] 키워드 뉴스 검색   -> GET /api/news/keyword
#  [기능 2] 신문사 1면 뉴스    -> GET /api/news/front
#  [기능 3] 자동차 관련 뉴스   -> GET /api/news/car
#  [기능 4] 원문 크롤링        -> GET /api/news/article?url=...
#  [스케줄] 매일 오전 7시 자동 수집 -> 파일 저장
# ============================================================

import os
import sys
import re
import json
import threading
import time
import requests
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, session
from routes.auth import login_required

# config.py 불러오기 (routes 폴더의 상위 폴더에 위치)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config

news_bp = Blueprint('news', __name__)

# ────────────────────────────────────────────────────────────
#  [메모리 캐시] 파일 저장 없이 메모리에만 보관 (1시간 유효)
#  서버 재시작 -> 초기화 -> 항상 최신 데이터 수집
# ────────────────────────────────────────────────────────────
CACHE_TTL = 3600  # 1시간 (초)

_mem_cache = {
    "keyword": {"data": None, "at": None},
    "front":   {"data": None, "at": None},
    "car":     {"data": None, "at": None},
}
_mem_lock = threading.Lock()


def _save_cache(feature: str, data: dict):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    data['_collected_at'] = ts
    with _mem_lock:
        _mem_cache[feature]["data"] = data
        _mem_cache[feature]["at"]   = datetime.now()
    print(f"[메모리 캐시] {feature} 저장 완료 ({ts})")


def _load_cache(feature: str) -> dict | None:
    with _mem_lock:
        entry = _mem_cache.get(feature)
        if not entry or entry["data"] is None or entry["at"] is None:
            return None
        age = (datetime.now() - entry["at"]).total_seconds()
        if age > CACHE_TTL:
            print(f"[메모리 캐시] {feature} 만료 ({int(age//60)}분 경과) -> 재수집")
            return None
        return entry["data"]


def _cache_collected_at(feature: str):
    with _mem_lock:
        entry = _mem_cache.get(feature)
        if entry and entry["data"]:
            return entry["data"].get("_collected_at")
    return None


def _load_filter_settings() -> dict:
    return {
        "keyword_filter": list(config.SEARCH_KEYWORDS),
        "car_filter":     list(config.CAR_KEYWORDS),
    }


def _save_filter_settings(settings: dict):
    pass  # 메모리 캐시 방식에서 필터 설정 저장 불필요

# ────────────────────────────────────────────────────────────
#  [내장 크롤러] 원문 페이지 전문 / 사진 / 표 / 요약 수집
# ────────────────────────────────────────────────────────────

def _crawl_article(url: str) -> dict:
    
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"full_text": "", "summary": "", "images": [], "tables": []}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://www.naver.com/",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return {"full_text": "", "summary": "", "images": [], "tables": []}

    # ── 1) 본문 텍스트 추출 (셀렉터 우선순위 순)
    BODY_SELECTORS = [
        # 네이버 뉴스
        "#dic_area", "#articleBodyContents",
        # 일반 언론사
        "#article-view-content-div",   # newsworks, 뉴스워크스 등
        "#article_content",            # 다수 언론사
        "#articleBody", "#article-body",
        ".article-view-content",       # 공통 클래스
        ".article_txt", ".article-txt",
        ".article_body", ".article-body",
        ".news_end", ".view_con",
        "#content-body", ".content-body",
        "#newsct_article",             # 중앙일보
        ".article__body",              # 한겨레 등
        "article",                     # HTML5 시맨틱
        ".post-content", ".entry-content",
    ]

    body_el = None
    for sel in BODY_SELECTORS:
        body_el = soup.select_one(sel)
        if body_el:
            break

    # fallback: 가장 긴 <p> 묶음을 본문으로 사용
    if not body_el:
        # <p> 태그들 중 텍스트가 많은 부모 컨테이너를 찾음
        best_parent = None
        best_len = 0
        for p in soup.find_all("p"):
            parent = p.parent
            if parent and parent.name not in ["html", "body", "head"]:
                text_len = len(parent.get_text(strip=True))
                if text_len > best_len:
                    best_len = text_len
                    best_parent = parent
        if best_parent and best_len > 100:
            body_el = best_parent

    full_text = ""
    if body_el:
        for tag in body_el.select("script, style, .ad, .related, button, .reporter, nav"):
            tag.decompose()
        full_text = body_el.get_text(separator="\n", strip=True)
        # 너무 짧으면 본문 아닌 것으로 판단
        if len(full_text) < 50:
            full_text = ""

    # ── 2) 2줄 요약 생성
    summary = _make_summary(full_text)

    # ── 3) 이미지 수집
    images = []
    search_area = body_el if body_el else soup
    for img in search_area.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
        if not src.startswith("http"):
            continue
        # 광고/아이콘 등 제외
        if any(kw in src.lower() for kw in ["logo", "icon", "banner", "ad_", "/ads/", "spacer"]):
            continue
        width = _parse_width(img)
        if width and width < config.MIN_IMAGE_WIDTH:
            continue
        parent = img.parent
        cap_el = (
            parent.find("figcaption")
            or parent.find("em")
            or parent.find("span", class_=re.compile(r"cap", re.I))
        )
        caption = cap_el.get_text(strip=True) if cap_el else ""
        images.append({"url": src, "caption": caption})
        if len(images) >= config.MAX_IMAGES_PER_ARTICLE:
            break

    # ── 4) 표 수집
    tables = []
    if body_el:
        for tbl in body_el.find_all("table"):
            tables.append(str(tbl))

    return {
        "full_text": full_text,
        "summary":   summary,
        "images":    images,
        "tables":    tables,
    }


def _make_summary(text: str) -> str:
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?。])\s+", text)
    result = []
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 20:
            continue
        if any(kw in sent for kw in ["기자", "ⓒ", "무단 전재", "저작권", "©"]):
            continue
        result.append(sent)
        if len(result) >= 2:
            break
    return " ".join(result)


def _parse_width(img_tag) -> int | None:
    w = img_tag.get("width")
    if w:
        try:
            return int(str(w).replace("px", "").strip())
        except ValueError:
            pass
    m = re.search(r"width\s*:\s*(\d+)", img_tag.get("style", ""))
    if m:
        return int(m.group(1))
    return None


# ────────────────────────────────────────────────────────────
#  [수집 함수] 실제 데이터 수집 (스케줄러 + 수동 버튼 공유)
# ────────────────────────────────────────────────────────────

def _collect_keyword(date: str) -> dict:
    
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    relevance_filter = _load_filter_settings()["keyword_filter"]
    articles = []
    for keyword in config.SEARCH_KEYWORDS:
        items = _naver_search(keyword, yesterday)
        time.sleep(0.5)   # API 요청 간 딜레이
        collected = 0
        for item in items:
            title = _strip_html(item.get("title", ""))
            if any(ex in title for ex in config.EXCLUDE_KEYWORDS):
                continue
            if not any(rel in title for rel in relevance_filter):
                continue
            link     = item.get("originallink") or item.get("link", "")
            pub_date = _parse_date(item.get("pubDate", ""))
            articles.append({
                "keyword":      keyword,
                "source":       _extract_source(link),
                "date":         pub_date,
                "title":        title,
                "summary":      "",   # 클릭 시 로드
                "full_text":    "",   # 클릭 시 로드
                "images":       [],
                "tables":       [],
                "original_url": link,
            })
            collected += 1
        print(f"[기능1] {keyword}: {len(items)}건 중 관련 {collected}건 수집")
    return {"status": "success", "date": date, "feature": "keyword", "articles": articles}


def _crawl_newsstand_main(oid: str) -> list:
    # 각 신문사 RSS 피드에서 최신 기사 목록을 가져옵니다.
    # 404/막힌 RSS는 빈 문자열로 두면 바로 네이버 API 폴백 사용
    RSS_MAP = {
        "023": "",  # 조선일보 RSS 404
        "366": "",  # 조선Biz RSS 404
        "009": "https://www.mk.co.kr/rss/30000001/",
        "015": "https://www.hankyung.com/feed/all-news",
        "214": "",  # MBC RSS XML 오류
        "056": "",  # KBS RSS 404
        "052": "",  # YTN RSS 404
        "025": "",  # 중앙일보 RSS 406
    }

    rss_url = RSS_MAP.get(oid)
    if not rss_url:
        return []

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)",
        "Accept": "application/rss+xml, application/xml, text/xml",
    }

    try:
        import xml.etree.ElementTree as ET
        resp = requests.get(rss_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"  -> RSS {rss_url} 상태코드: {resp.status_code}")
            return []

        # XML 파싱
        root = ET.fromstring(resp.content)
        # RSS 2.0: channel > item
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items_xml = root.findall(".//item")

        items = []
        for item in items_xml:
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            # CDATA 처리: link가 비어있으면 guid 사용
            if not link:
                link = item.findtext("guid", "").strip()
            title = _strip_html(title)
            if not title or not link or len(title) < 5:
                continue
            items.append({"title": title, "url": link})
            if len(items) >= 5:
                break

        if items:
            print(f"  -> RSS {rss_url} 에서 {len(items)}건 수집")
        return items

    except Exception as e:
        print(f"  -> RSS {rss_url} 실패: {e}")
        return []


def _collect_front(date: str) -> dict:
    
    articles = []
    for paper in config.FRONT_PAGE_PAPERS:
        print(f"[기능2] {paper['name']} 수집 중...")
        items = _crawl_newsstand_main(paper["oid"])
        if not items:
            print(f"  -> 크롤링 실패, API 검색으로 대체")
            items = _search_by_press(paper["name"], max_items=3)
        print(f"[기능2] {paper['name']}: {len(items)}건 수집 완료")
        for item in items:
            articles.append({
                "source":       paper["name"],
                "date":         date,
                "title":        item["title"],
                "summary":      "",   # 클릭 시 로드
                "full_text":    "",   # 클릭 시 로드
                "images":       [],
                "tables":       [],
                "original_url": item["url"],
            })
        time.sleep(1)   # 신문사 간 딜레이 (1초)
    return {"status": "success", "date": date, "feature": "front", "articles": articles}


def _collect_car(date: str) -> dict:
    
    CAR_FILTER_KEYWORDS = ["전기차", "하이브리드", "수소전기차", "자율주행", "배터리"]
    date_nodash = date.replace("-", "")
    relevance_filter = _load_filter_settings()["car_filter"]
    articles = []
    for keyword in CAR_FILTER_KEYWORDS:
        items = _naver_search(keyword, date_nodash)
        time.sleep(0.5)   # API 요청 간 딜레이
        collected = 0
        for item in items:
            title = _strip_html(item.get("title", ""))
            if any(ex in title for ex in config.EXCLUDE_KEYWORDS):
                continue
            if not any(rel in title for rel in relevance_filter):
                continue
            link     = item.get("originallink") or item.get("link", "")
            pub_date = _parse_date(item.get("pubDate", ""))
            articles.append({
                "keyword":      keyword,
                "source":       _extract_source(link),
                "date":         pub_date,
                "title":        title,
                "summary":      "",   # 클릭 시 로드
                "full_text":    "",   # 클릭 시 로드
                "images":       [],
                "tables":       [],
                "original_url": link,
            })
            collected += 1
        print(f"[기능3] {keyword}: {len(items)}건 중 관련 {collected}건 수집")
    return {"status": "success", "date": date, "feature": "car", "articles": articles}


_collecting = False  # 수집 중복 실행 방지 플래그

def _collect_all():
    global _collecting
    if _collecting:
        print("[뉴스 수집] 이미 수집 중입니다. 중복 실행 무시.")
        return
    _collecting = True
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[뉴스 수집] 시작: {today}")
    try:
        data = _collect_keyword(today)
        _save_cache("keyword", data)
        print(f"[뉴스 수집] 기능1 완료: {len(data['articles'])}건")
    except Exception as e:
        print(f"[뉴스 수집] 기능1 오류: {e}")
    try:
        data = _collect_front(today)
        _save_cache("front", data)
        print(f"[뉴스 수집] 기능2 완료: {len(data['articles'])}건")
    except Exception as e:
        print(f"[뉴스 수집] 기능2 오류: {e}")
    try:
        data = _collect_car(today)
        _save_cache("car", data)
        print(f"[뉴스 수집] 기능3 완료: {len(data['articles'])}건")
    except Exception as e:
        print(f"[뉴스 수집] 기능3 오류: {e}")
    print(f"[뉴스 수집] 전체 완료")
    _collecting = False


# ────────────────────────────────────────────────────────────
#  [스케줄러] 매일 오전 7시 자동 수집
# ────────────────────────────────────────────────────────────

def _scheduler():
    
    print("[스케줄러] 뉴스 자동 수집 스케줄러 시작 (매일 오전 7시)")
    while True:
        now = datetime.now()
        # 다음 오전 7시 계산
        target = now.replace(hour=7, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        print(f"[스케줄러] 다음 수집까지 {int(wait_seconds//3600)}시간 {int((wait_seconds%3600)//60)}분 대기")
        time.sleep(wait_seconds)
        _collect_all()



# 서버 시작 시 스케줄러 백그라운드 실행
_scheduler_thread = threading.Thread(target=_scheduler, daemon=True)
_scheduler_thread.start()

# 서버 시작 시 즉시 수집 (메모리 캐시가 비어있으므로 항상 실행)
print('[뉴스] 서버 시작 -> 즉시 수집 시작')
threading.Thread(target=_collect_all, daemon=True).start()



# ────────────────────────────────────────────────────────────
#  [API] 기능 1/2/3 - 캐시 우선, 없으면 실시간 수집
# ────────────────────────────────────────────────────────────

@news_bp.route("/api/news/keyword", methods=["GET"])
@login_required
def keyword_news():
    cached = _load_cache("keyword")
    if cached:
        return jsonify(cached)
    # 캐시 만료 -> 백그라운드 재수집 후 빈 응답 (프론트가 status 폴링으로 감지)
    if not _collecting:
        threading.Thread(target=_collect_all, daemon=True).start()
    return jsonify({"status": "collecting", "articles": []})


@news_bp.route("/api/news/front", methods=["GET"])
@login_required
def front_page_news():
    cached = _load_cache("front")
    if cached:
        return jsonify(cached)
    if not _collecting:
        threading.Thread(target=_collect_all, daemon=True).start()
    return jsonify({"status": "collecting", "articles": []})


@news_bp.route("/api/news/car", methods=["GET"])
@login_required
def car_news():
    cached = _load_cache("car")
    if cached:
        return jsonify(cached)
    if not _collecting:
        threading.Thread(target=_collect_all, daemon=True).start()
    return jsonify({"status": "collecting", "articles": []})


# ────────────────────────────────────────────────────────────
#  [API] 단일 키워드 즉시 검색 (UI 입력창용)
# ────────────────────────────────────────────────────────────

@news_bp.route("/api/news/search", methods=["GET"])
@login_required
def search_news():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"status": "error", "message": "검색어를 입력해주세요.", "articles": []})

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    today     = datetime.now().strftime("%Y-%m-%d")

    try:
        items = _naver_search(q, yesterday)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "articles": []})

    articles = []
    for item in items:
        title = _strip_html(item.get("title", ""))
        if any(ex in title for ex in config.EXCLUDE_KEYWORDS):
            continue
        link     = item.get("originallink") or item.get("link", "")
        pub_date = _parse_date(item.get("pubDate", ""))
        articles.append({
            "keyword":      q,
            "source":       _extract_source(link),
            "date":         pub_date,
            "title":        title,
            "summary":      "",
            "full_text":    "",
            "images":       [],
            "tables":       [],
            "original_url": link,
        })

    print(f"[검색] {q}: {len(articles)}건 수집")
    return jsonify({
        "status":   "success",
        "date":     today,
        "query":    q,
        "articles": articles,
    })


# ────────────────────────────────────────────────────────────
#  [API] 필터 설정 조회/저장 (admin 전용)
# ────────────────────────────────────────────────────────────

@news_bp.route("/api/news/filter-settings", methods=["GET"])
@login_required
def get_filter_settings():
    
    if session.get('role') != 'admin':
        return jsonify({"status": "error", "message": "권한이 없습니다."}), 403
    return jsonify(_load_filter_settings())


@news_bp.route("/api/news/filter-settings", methods=["POST"])
@login_required
def save_filter_settings():
    
    if session.get('role') != 'admin':
        return jsonify({"status": "error", "message": "권한이 없습니다."}), 403
    data = request.get_json()
    # 빈 문자열 제거 + 앞뒤 공백 제거
    keyword_filter = [w.strip() for w in data.get("keyword_filter", []) if w.strip()]
    car_filter     = [w.strip() for w in data.get("car_filter", []) if w.strip()]
    settings = {"keyword_filter": keyword_filter, "car_filter": car_filter}
    _save_filter_settings(settings)
    return jsonify({"status": "success", "message": "저장되었습니다.", "settings": settings})


# ────────────────────────────────────────────────────────────
#  [API] 수집 상태 (마지막 수집 시간)
# ────────────────────────────────────────────────────────────

@news_bp.route("/api/news/status", methods=["GET"])
@login_required
def news_status():
    today = datetime.now().strftime("%Y-%m-%d")
    collected_at = None
    is_today = False
    for feature in ["keyword", "front", "car"]:
        ts = _cache_collected_at(feature)
        if ts:
            if collected_at is None or ts > collected_at:
                collected_at = ts
                is_today = ts.startswith(today)
    return jsonify({
        "collecting":   _collecting,
        "collected_at": collected_at,
        "is_today":     is_today,
    })


# ────────────────────────────────────────────────────────────
#  [API] 수동 수집 버튼 (로그인 사용자 전체)
# ────────────────────────────────────────────────────────────

@news_bp.route("/api/news/fetch", methods=["POST"])
@login_required
def fetch_news():
    
    if _collecting:
        return jsonify({"status": "busy", "message": "이미 수집 중입니다. 잠시 후 다시 시도하세요."})

    # 백그라운드에서 수집 (응답은 즉시 반환)
    t = threading.Thread(target=_collect_all, daemon=True)
    t.start()
    return jsonify({"status": "success", "message": "뉴스 수집을 시작했습니다. 잠시 후 새로고침하세요."})


# ────────────────────────────────────────────────────────────
#  [API] 원문 기사 크롤링 (모달용)
#  GET /api/news/article?url=https://...
# ────────────────────────────────────────────────────────────

@news_bp.route("/api/news/article", methods=["GET"])
@login_required
def get_article():
    
    from urllib.parse import unquote
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"status": "error", "message": "url 파라미터가 필요합니다."}), 400

    # 이중 인코딩 방지: %25 -> % 같은 경우 한 번 더 디코딩
    url = unquote(url)

    crawled = _crawl_article(url)

    # 본문이 비어있으면 오류 대신 메시지 포함해서 반환
    if not crawled["full_text"]:
        crawled["summary"] = "이 사이트는 본문 자동 수집이 지원되지 않습니다. 원문 사이트에서 직접 확인해주세요."

    return jsonify({
        "status":    "success",
        "url":       url,
        "full_text": crawled["full_text"],
        "summary":   crawled["summary"],
        "images":    crawled["images"],
        "tables":    crawled["tables"],
    })


# ────────────────────────────────────────────────────────────
#  [네이버 뉴스스탠드 크롤러]
# ────────────────────────────────────────────────────────────

def _crawl_newsstand(oid: str, max_items: int = 5) -> list:
    
    # oid -> 신문사 검색 키워드 매핑
    OID_TO_QUERY = {
        "023": "site:chosun.com",
        "366": "site:biz.chosun.com",
        "009": "site:mk.co.kr",
        "015": "site:hankyung.com",
        "214": "site:imnews.imbc.com",
        "056": "site:news.kbs.co.kr",
        "052": "site:ytn.co.kr",
        "025": "site:joongang.co.kr",
    }
    # oid -> 신문사명 (검색 쿼리용)
    OID_TO_NAME = {
        "023": "조선일보",
        "366": "조선비즈",
        "009": "매일경제",
        "015": "한국경제",
        "214": "MBC 뉴스",
        "056": "KBS 뉴스",
        "052": "YTN",
        "025": "중앙일보",
    }

    name = OID_TO_NAME.get(oid, "")
    if not name:
        return []

    # 네이버 검색 API로 해당 신문사 최신 뉴스 검색
    headers = {
        "X-Naver-Client-Id":     config.NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
    }
    params = {
        "query":   name,          # 신문사명으로 검색
        "display": max_items,
        "sort":    "date",        # 최신순
    }
    try:
        resp = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers=headers, params=params, timeout=10
        )
        resp.raise_for_status()
        raw_items = resp.json().get("items", [])

        items = []
        for item in raw_items:
            title = _strip_html(item.get("title", "")).strip()
            link  = item.get("originallink") or item.get("link", "")
            if not title or not link:
                continue
            items.append({"title": title, "url": link})
            if len(items) >= max_items:
                break
        return items
    except Exception as e:
        print(f"[_crawl_newsstand] {name} 오류: {e}")
        return []


# ────────────────────────────────────────────────────────────
#  [공통 유틸]
# ────────────────────────────────────────────────────────────

def _search_by_press(press_name: str, max_items: int = 5) -> list:
    
    headers = {
        "X-Naver-Client-Id":     config.NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
    }
    params = {
        "query":   press_name,
        "display": max_items,
        "sort":    "date",
    }
    try:
        resp = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers=headers, params=params, timeout=10
        )
        resp.raise_for_status()
        raw_items = resp.json().get("items", [])
        result = []
        for item in raw_items:
            title = _strip_html(item.get("title", "")).strip()
            link  = item.get("originallink") or item.get("link", "")
            if not title or not link:
                continue
            result.append({"title": title, "url": link})
            if len(result) >= max_items:
                break
        return result
    except Exception as e:
        print(f"[_search_by_press] {press_name} 오류: {e}")
        return []


def _naver_search(keyword: str, date: str) -> list:
    headers = {
        "X-Naver-Client-Id":     config.NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
    }
    params = {"query": keyword, "display": 10, "sort": "date"}
    try:
        resp = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers=headers, params=params, timeout=10
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        filtered = [i for i in items
                    if date[4:6] in i.get("pubDate", "")
                    and date[6:] in i.get("pubDate", "")]
        return filtered if filtered else items
    except Exception:
        return []


def _strip_html(text: str) -> str:
    # HTML 태그 제거 및 엔티티 디코딩
    # 태그 제거
    text = re.sub(r"<[^>]+>", "", text)
    # HTML 엔티티 디코딩 (&quot; -> "  &amp; -> &  &lt; -> <  등)
    import html
    text = html.unescape(text)
    return text.strip()


def _parse_date(pub_date: str) -> str:
    try:
        dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _extract_source(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    if not m:
        return "알 수 없음"
    domain = m.group(1)
    mapping = {
        "chosun.com":     "조선일보",
        "mk.co.kr":       "매일경제",
        "hankyung.com":   "한국경제",
        "joongang.co.kr": "중앙일보",
        "mbc.co.kr":      "MBC",
        "kbs.co.kr":      "KBS",
        "ytn.co.kr":      "YTN",
    }
    for key, val in mapping.items():
        if key in domain:
            return val
    return domain