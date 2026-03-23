import requests, re, json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://online.webbook.kr/",
}

r = requests.get(
    "https://online.webbook.kr/books/auto48-03/files/search/search_config.js",
    headers=headers, timeout=15
)
text = r.text
print(f"파일 크기: {len(text)}자")
print(f"파일 끝 100자: {repr(text[-100:])}")
print(f"파일 시작 50자: {repr(text[:50])}")

# var textForPages = [...] 에서 [ 이후 전체 추출
start = text.find('var textForPages =["')
if start == -1:
    start = text.find("var textForPages =[")
print(f"\ntextForPages 시작 위치: {start}")

if start >= 0:
    # [ 위치 찾기
    bracket_start = text.find('[', start)
    # 대괄호 짝 맞추기
    depth = 0
    end = bracket_start
    for i, ch in enumerate(text[bracket_start:], bracket_start):
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                end = i
                break
    
    pages_raw = text[bracket_start:end+1]
    print(f"배열 길이: {len(pages_raw)}자")
    print(f"배열 끝: {repr(pages_raw[-50:])}")
    
    try:
        pages = json.loads(pages_raw)
        print(f"\n총 페이지 수: {len(pages)}")
        
        # 내용 있는 페이지 출력
        for i, pg in enumerate(pages):
            if pg and pg.strip():
                clean = pg.replace('\r\n', '\n').replace('\r', '\n').strip()
                print(f"\n{'='*50}")
                print(f"[{i+1}페이지]")
                print(clean[:400])
    except Exception as e:
        print(f"JSON 파싱 오류: {e}")
        # 수동 파싱 시도
        print("수동 파싱 시도...")
        entries = re.findall(r'"((?:[^"\\]|\\.)*)"', pages_raw)
        print(f"항목 수: {len(entries)}")
        for i, entry in enumerate(entries[:10]):
            decoded = entry.encode().decode('unicode_escape', errors='replace')
            print(f"\n[{i}]: {decoded[:200]}")

print("\n\n완료!")