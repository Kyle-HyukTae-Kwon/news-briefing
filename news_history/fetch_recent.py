"""
[fetch_recent.py]
news_history 폴더의 settings.json에 등록된 RSS 주소에서
최신 기사 3개를 가져와 '제목', '링크', '날짜'만 JSON 형식으로 화면에 보여주는 예제 코드입니다.

외부 패키지 설치(pip install) 없이, 파이썬 기본 내장 라이브러리만 사용합니다.
"""

from datetime import datetime
import email.utils
import json
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET


def parse_date(date_str: str) -> tuple[datetime, str]:
    """
    RSS에서 제공하는 날짜 문자열(예: 'Mon, 09 Nov 2026 20:00:00 +0900')을
    1) 최신순 정렬을 위한 datetime 객체와
    2) 사람이 읽기 편한 'YYYY-MM-DD HH:MM' 형식의 문자열로 변환합니다.
    """
    if not date_str:
        return datetime.min, "날짜 정보 없음"
    try:
        # email.utils.parsedate_to_datetime 은 파이썬 기본 내장 함수로,
        # RSS 표준 날짜 형식(RFC 2822)을 별도 라이브러리 설치 없이 바로 해석해 줍니다.
        dt = email.utils.parsedate_to_datetime(date_str)
        
        # 시간대(timezone) 정보가 있다면 시스템 로컬 시간대로 맞추어 변환합니다.
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
            
        # strftime을 사용해 연-월-일 시:분 형태로 깔끔하게 만듭니다.
        formatted = dt.strftime("%Y-%m-%d %H:%M")
        return dt, formatted
    except Exception:
        # 혹시라도 날짜 형식이 달라서 파싱에 실패하면 최소 날짜와 원본 문자열을 반환합니다.
        return datetime.min, date_str


def get_top3_news():
    # 1. settings.json 파일의 위치 파악하기
    # Path(__file__).resolve().parent 는 현재 이 스크립트 파일이 위치한 폴더(news_history)를 가리킵니다.
    current_dir = Path(__file__).resolve().parent
    settings_path = current_dir / "settings.json"

    # 설정 파일이 없는 경우를 대비한 안전 장치
    if not settings_path.exists():
        print(f"❌ 설정 파일({settings_path})을 찾을 수 없습니다.")
        return

    # 2. settings.json 파일 열어서 내용 읽어오기
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = json.load(f)

    # settings.json 에서 "feeds" 목록 가져오기
    feeds = settings.get("feeds", [])
    all_articles = []

    # 3. 웹서버 요청 시 보낼 헤더(Header) 설정
    # 일부 언론사 사이트는 파이썬 기본 프로그램의 접근을 차단하므로,
    # 웹 브라우저에서 접근하는 것처럼 User-Agent 정보를 함께 전달합니다.
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    # 4. 등록된 모든 RSS 주소를 하나씩 돌면서 기사 수집하기
    for feed in feeds:
        feed_name = feed.get("name", "알 수 없음")
        feed_url = feed.get("url", "")

        if not feed_url:
            continue

        try:
            # urllib.request.Request 로 주소와 헤더를 묶어 요청 준비
            req = urllib.request.Request(feed_url, headers=headers)
            
            # urllib.request.urlopen 으로 웹사이트의 RSS XML 데이터를 다운로드
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()

            # xml.etree.ElementTree 로 XML 구조를 파이썬에서 다루기 쉽게 파싱
            root = ET.fromstring(xml_data)
            
            # RSS 피드에서 개별 기사는 항상 <item> 태그 안에 들어있습니다.
            items = root.findall(".//item")

            for item in items:
                # findtext 로 각 태그 안의 텍스트 값을 안전하게 추출합니다.
                title = item.findtext("title", default="").strip()
                link = item.findtext("link", default="").strip()
                pub_date_raw = item.findtext("pubDate", default="").strip()
                category = item.findtext("category", default="").strip()

                # 한국경제 프리미엄9 유료 기사 제외
                if ("한국경제" in feed_name or "hankyung.com" in link):
                    lower_t = title.lower()
                    if "/premium" in link.lower() or "프리미엄" in lower_t or "exclusive" in lower_t or "epic ai" in lower_t or "프리미엄" in category:
                        continue

                # 날짜 변환 함수를 호출하여 정렬용 날짜 객체와 표시용 문자열을 얻습니다.
                dt_obj, formatted_date = parse_date(pub_date_raw)

                # 제목과 링크가 온전히 있는 기사만 리스트에 추가합니다.
                if title and link:
                    all_articles.append({
                        "title": title,
                        "link": link,
                        "date": formatted_date,
                        "_sort_dt": dt_obj  # 정렬할 때만 임시로 쓸 날짜 객체
                    })

        except Exception as e:
            # 특정 언론사 접속이 일시적으로 실패해도 멈추지 않고 다음 언론사를 계속 진행합니다.
            continue

    # 5. 수집된 모든 기사를 날짜 기준 최신순(내림차순, reverse=True)으로 정렬합니다.
    all_articles.sort(key=lambda x: x["_sort_dt"], reverse=True)

    # 6. 상위 3개(최신 글 3개)만 뽑아서 요구된 3가지 항목(title, link, date)만 남깁니다.
    top3_articles = []
    for art in all_articles[:3]:
        top3_articles.append({
            "title": art["title"],
            "link": art["link"],
            "date": art["date"]
        })

    # 7. 파이썬 딕셔너리 리스트를 예쁜 JSON 문자열로 변환하여 화면에 출력합니다.
    # - indent=2: 2칸씩 보기 좋게 들여쓰기
    # - ensure_ascii=False: 한글이 유니코드(\\uXXXX)로 깨지지 않고 한글 그대로 표시
    json_result = json.dumps(top3_articles, indent=2, ensure_ascii=False)
    print(json_result)


# 이 파이썬 파일을 직접 실행했을 때 main 처럼 get_top3_news() 를 실행하라는 의미입니다.
if __name__ == "__main__":
    get_top3_news()

