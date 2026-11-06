"""
[뉴스 브리핑 자동화 프로그램]
- 기능: settings.json 에 등록된 RSS 피드에서 뉴스를 수집하고,
        설정한 키워드(은행, ai, 금융 등)가 포함된 기사를 선별하여
        화면 출력 및 마크다운(.md) 보고서 파일로 저장합니다.
- 특징: 외부 라이브러리 설치 없이 Python 기본 기능(표준 라이브러리)만 사용합니다.
"""

from datetime import datetime
import email.utils
import json
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET


def format_rss_date(raw_date_str: str) -> str:
    """
    RSS 날짜 형식(예: 'Fri, 06 Nov 2026 19:58:51 +09:00')을
    사람이 읽기 쉬운 한국식 날짜 형식('2026-11-06 19:58')으로 변환합니다.
    """
    if not raw_date_str:
        return ""
    try:
        # email.utils.parsedate_to_datetime 은 파이썬 기본 제공 기능으로
        # 인터넷 표준(RFC 2822) 날짜 문자열을 datetime 객체로 손쉽게 변환해 줍니다.
        dt = email.utils.parsedate_to_datetime(raw_date_str)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        # 혹시라도 날짜 형식이 다를 경우 원본 문자열을 그대로 반환
        return raw_date_str


def fetch_and_filter_news(settings_path: Path):
    """
    settings.json 을 읽어서 각 피드의 기사를 가져오고,
    키워드에 해당하는 기사를 선별합니다.
    """
    # 1. 설정 파일 읽기
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = json.load(f)

    keywords = settings.get("keywords", [])
    feeds = settings.get("feeds", [])

    # 브라우저인 것처럼 위장하여 언론사 웹서버의 차단(403 Forbidden)을 방지
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    # 대소문자 구분 없이 검색하기 위해 키워드를 소문자로 준비
    lower_keywords = [k.lower() for k in keywords]

    briefing_results = {}
    total_matched_count = 0

    print("=" * 60)
    print("🚀 뉴스 브리핑 수집을 시작합니다.")
    print(f"📌 감지 키워드: {', '.join(keywords)}")
    print("=" * 60)

    # 2. 각 언론사 RSS 순회
    for feed in feeds:
        feed_name = feed["name"]
        feed_url = feed["url"]
        print(f"\n📡 [{feed_name}] 최신 뉴스 수집 중...")

        try:
            # 웹 주소로 요청하여 XML 다운로드
            req = urllib.request.Request(feed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()

            # XML 데이터 분석
            root = ET.fromstring(xml_data)
            items = root.findall(".//item")

            matched_articles = []

            for item in items:
                title = item.findtext("title", default="").strip()
                link = item.findtext("link", default="").strip()
                raw_date = item.findtext("pubDate", default="").strip()
                desc = item.findtext("description", default="").strip()

                # 날짜를 읽기 쉽게 변환
                clean_date = format_rss_date(raw_date)

                # 제목과 본문 요약에서 키워드가 포함되어 있는지 검사
                search_target = f"{title} {desc}".lower()
                found_keywords = [
                    kw for kw, l_kw in zip(keywords, lower_keywords)
                    if l_kw in search_target
                ]

                # 키워드가 하나라도 발견되면 결과 목록에 추가
                if found_keywords:
                    matched_articles.append({
                        "제목": title,
                        "링크": link,
                        "날짜": clean_date,
                        "매칭키워드": found_keywords,
                        "요약": desc[:120] + ("..." if len(desc) > 120 else "")
                    })

            briefing_results[feed_name] = matched_articles
            total_matched_count += len(matched_articles)
            print(f"   -> 전체 {len(items)}건 중 {len(matched_articles)}건 매칭됨")

        except Exception as e:
            print(f"   ❌ [{feed_name}] 수집 중 오류 발생: {e}")
            briefing_results[feed_name] = []

    # 3. 콘솔에 JSON 형태로 먼저 출력
    print("\n" + "=" * 60)
    print("📋 [1] 키워드 매칭 기사 JSON 출력")
    print("=" * 60)
    print(json.dumps(briefing_results, indent=2, ensure_ascii=False))

    # 4. 마크다운(.md) 보고서 파일 생성
    today_str = datetime.now().strftime("%Y%m%d")
    report_file = settings_path.parent / f"briefing_{today_str}.md"
    generate_markdown_report(report_file, keywords, briefing_results, total_matched_count)

    print("\n" + "=" * 60)
    print(f"🎉 브리핑 보고서 생성이 완료되었습니다!")
    print(f"📄 저장 경로: {report_file.name}")
    print("=" * 60)


def generate_markdown_report(output_path: Path, keywords: list[str], results: dict, total_count: int):
    """
    수집된 뉴스 결과를 보기 좋은 마크다운 문서로 만들어 저장합니다.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# 📰 데일리 뉴스 브리핑 리포트",
        f"",
        f"- **작성 일시**: {now_str}",
        f"- **모니터링 키워드**: `{', '.join(keywords)}`",
        f"- **총 매칭 기사**: **{total_count}건**",
        f"",
        f"---",
        f""
    ]

    for feed_name, articles in results.items():
        lines.append(f"## 🏛️ {feed_name} ({len(articles)}건)")
        lines.append("")
        if not articles:
            lines.append("> 매칭된 기사가 없습니다.")
            lines.append("")
            continue

        for idx, art in enumerate(articles, start=1):
            kw_tags = " ".join([f"`#{kw}`" for kw in art["매칭키워드"]])
            lines.append(f"### {idx}. [{art['제목']}]({art['링크']})")
            lines.append(f"- **발행일**: {art['날짜']} | **키워드**: {kw_tags}")
            if art["요약"]:
                lines.append(f"- **내용 요약**: {art['요약']}")
            lines.append("")

    # UTF-8 인코딩으로 마크다운 파일 저장
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    current_dir = Path(__file__).resolve().parent
    settings_file = current_dir / "settings.json"
    fetch_and_filter_news(settings_file)

