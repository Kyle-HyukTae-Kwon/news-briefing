import json
import urllib.request
import xml.etree.ElementTree as ET

# 1. settings.json 파일에서 RSS 주소 목록 읽어오기
with open("settings.json", "r", encoding="utf-8") as f:
    settings = json.load(f)

# 최종 결과를 담을 딕셔너리 (언론사 이름을 키로 사용)
latest_articles = {}

# 웹 요청 시 언론사 서버에서 접근을 차단하지 않도록 일반 웹 브라우저인 것처럼 헤더(User-Agent) 설정
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# 2. settings.json 에 등록된 각 언론사 피드 순회
for feed in settings.get("feeds", []):
    feed_name = feed["name"]
    feed_url = feed["url"]
    
    # 웹 요청 객체 생성
    req = urllib.request.Request(feed_url, headers=headers)
    
    # 3. URL 접속 및 XML 데이터 가져오기
    with urllib.request.urlopen(req) as response:
        xml_data = response.read()
    
    # 4. XML 데이터 해석 (파싱)
    root = ET.fromstring(xml_data)
    
    # RSS 2.0 규격에서 기사는 <item> 태그 안에 들어있음
    # 최신 기사 3개만 가져오기 위해 슬라이싱 [:3] 사용
    items = root.findall(".//item")[:3]
    
    feed_results = []
    for item in items:
        # 각 태그에서 텍스트(문자열) 추출
        title = item.findtext("title", default="").strip()
        link = item.findtext("link", default="").strip()
        date = item.findtext("pubDate", default="").strip()
        
        # 제목, 링크, 날짜만 딕셔너리로 저장
        feed_results.append({
            "제목": title,
            "링크": link,
            "날짜": date
        })
    
    latest_articles[feed_name] = feed_results

# 5. 수집한 결과를 보기 좋은 JSON 문자열로 변환하여 화면에 출력
# indent=2: 들여쓰기 2칸으로 예쁘게 정렬
# ensure_ascii=False: 한글이 깨지지 않고 한글 그대로 출력되도록 설정
json_output = json.dumps(latest_articles, indent=2, ensure_ascii=False)
print(json_output)

