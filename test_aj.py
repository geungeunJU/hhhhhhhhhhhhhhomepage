import requests, warnings, re
warnings.filterwarnings('ignore')
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://eiec.kdi.re.kr/"}

r = requests.get("https://eiec.kdi.re.kr/policy/materialList.do",
    params={"search_txt": "자동차", "pg": "1", "pp": "10", "type": "A", "device": "pc"},
    headers=HEADERS, timeout=15, verify=False)
html = r.text

parts = re.split(r'materialView\.do\?num=', html)
print(f"분리된 블록 수: {len(parts)}")
seen = set()
count = 0
for part in parts[1:]:
    num_m = re.match(r'(\d+)', part)
    if not num_m: continue
    num = num_m.group(1)
    if num in seen: continue
    seen.add(num)
    title_m = re.search(r'<p>(.*?)</p>', part[:2000])
    if not title_m: continue
    after = part[title_m.end():]
    org_m = re.search(r'<span>([^<\d][^<]*?)</span>', after[:500])
    date_m = re.search(r'(\d{4}\.\d{2}\.\d{2})', after[:500])
    count += 1
    print(f"{count}. [{num}] {title_m.group(1)[:40]} | {org_m.group(1) if org_m else '?'} | {date_m.group(1) if date_m else '?'}")
print(f"\n총 {count}건")