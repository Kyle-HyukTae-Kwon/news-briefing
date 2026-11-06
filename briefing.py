"""
[briefing.py]
settings.json에 등록된 RSS 피드에서 뉴스를 수집하고,
Gemini API를 호출하여 각 기사마다 '세 줄 요약'을 생성한 뒤
index.html 및 news.json 결과물을 생성하는 스크립트입니다.

외부 패키지 설치 없이 Python 기본 내장 모듈만 사용합니다.
"""

from datetime import datetime
import email.utils
import html
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


def load_env_api_key(env_path: Path) -> str:
    """
    외부 라이브러리(python-dotenv) 없이 파이썬 기본 기능으로
    .env 파일에서 GEMINI_API_KEY를 안전하게 읽어옵니다.
    """
    if not env_path.exists():
        return os.environ.get("GEMINI_API_KEY", "")

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return os.environ.get("GEMINI_API_KEY", "")


def parse_pubdate(pubdate_str: str) -> tuple[datetime, str]:
    """
    RSS 날짜 문자열(RFC 2822)을 정렬용 datetime과 사람이 읽기 편한 문자열로 변환합니다.
    """
    if not pubdate_str:
        return datetime.min, ""
    try:
        dt = email.utils.parsedate_to_datetime(pubdate_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        formatted = dt.strftime("%Y-%m-%d %H:%M")
        return dt, formatted
    except Exception:
        return datetime.min, pubdate_str


def fetch_all_articles(settings: dict) -> list[dict]:
    """
    settings.json의 모든 RSS 피드에서 기사 목록을 수집합니다.
    (기사 본문 웹페이지는 스크래핑하지 않고 RSS 피드 정보만 사용)
    """
    feeds = settings.get("feeds", [])
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    all_articles = []
    seen_links = set()

    for feed in feeds:
        feed_name = feed.get("name", "알 수 없음")
        feed_url = feed.get("url")

        if not feed_url:
            continue

        try:
            req = urllib.request.Request(feed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()

            root = ET.fromstring(xml_data)
            items = root.findall(".//item")

            for item in items:
                title = item.findtext("title", default="").strip()
                link = item.findtext("link", default="").strip()
                description = item.findtext("description", default="").strip()
                pub_date_raw = item.findtext("pubDate", default="").strip()

                # 기사 제목 중에 [포토] 들어간 기사는 제외
                if "[포토]" in title:
                    continue

                if not link or link in seen_links:
                    continue
                seen_links.add(link)

                dt_obj, formatted_date = parse_pubdate(pub_date_raw)

                all_articles.append({
                    "source": feed_name,
                    "title": title,
                    "link": link,
                    "description": description,
                    "dt": dt_obj,
                    "date": formatted_date
                })

        except Exception as e:
            print(f"[{feed_name}] RSS 수집 오류: {e}")

    return all_articles


def filter_and_select_top5(articles: list[dict], keywords: list[str]) -> list[dict]:
    """
    제목이나 소개문에 키워드(은행, ai, 금융)가 포함된 기사를 우선 선별하고,
    부족하면 일반 최신 기사로 채워 최대 5개를 선택합니다.
    """
    keyword_articles = []
    other_articles = []
    lower_keywords = [k.lower() for k in keywords]

    for art in articles:
        search_target = f"{art['title']} {art['description']}".lower()
        matched = [
            orig_kw for orig_kw, low_kw in zip(keywords, lower_keywords)
            if low_kw in search_target
        ]
        art_copy = dict(art)
        art_copy["matched_keywords"] = matched

        if matched:
            keyword_articles.append(art_copy)
        else:
            other_articles.append(art_copy)

    keyword_articles.sort(key=lambda x: x["dt"], reverse=True)
    other_articles.sort(key=lambda x: x["dt"], reverse=True)

    selected = (keyword_articles + other_articles)[:5]

    clean_results = []
    for art in selected:
        clean_results.append({
            "source": art["source"],
            "title": art["title"],
            "link": art["link"],
            "date": art["date"],
            "description": art["description"],
            "matched_keywords": art["matched_keywords"]
        })

    return clean_results


def summarize_with_gemini(api_key: str, title: str, description: str) -> str:
    """
    지정된 요약 프롬프트 규칙에 따라 Gemini로 세 줄 요약을 생성합니다.
    - '자세한 내용은 원문 확인 필요' 문구는 포함하지 않습니다.
    - API 키가 없거나 실패하더라도 안전한 대체 텍스트를 반환합니다.
    """
    # 1. API 키가 없는 경우
    if not api_key:
        fallback = [
            f"- {title}",
            f"- {description[:60]}..." if description else "- 기사 내용 요약 준비 중입니다.",
            "- 금융·경제 관련 주요 뉴스입니다."
        ]
        return "\n".join(fallback)

    # 2. Gemini 요청 프롬프트 구성
    clean_desc = description.strip() if description else "소개문 내용 없음"
    prompt = (
        "아래 기사 정보만 보고 한국어로 핵심 내용을 세 줄 요약해 줘.\n"
        "- 각 줄은 한 문장이어야 하며, 줄 앞에 반드시 \"- \" 를 붙인다\n"
        "- 기사 정보에 없는 내용, 숫자, 이름은 절대 추측하거나 지어내지 않는다\n"
        "- '자세한 내용은 원문 확인 필요' 같은 메타 문구는 절대 쓰지 말고, 기사 내용의 핵심만을 작성한다\n\n"
        f"[기사 정보]\n"
        f"제목: {title}\n"
        f"소개문: {clean_desc}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={api_key}"

    payload = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ]
    }

    # 3. HTTP 요청 및 응답 처리
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            summary_text = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()

            # '원문 확인 필요' 문구가 혹시라도 포함되어 있으면 제거
            filtered_lines = [
                line for line in summary_text.split("\n")
                if "원문 확인 필요" not in line and line.strip()
            ]
            return "\n".join(filtered_lines)
    except Exception as e:
        print(f"      ⚠️ 요약 실패 ({e}): 기본 내용으로 대체합니다.")
        fallback = [
            f"- {title}",
            f"- {clean_desc[:60]}..." if clean_desc != "소개문 내용 없음" else "- 기사 상세 내용입니다.",
            "- 금융 및 경제 분야 소식입니다."
        ]
        return "\n".join(fallback)


def generate_index_html(output_path: Path, articles: list[dict], collected_time_str: str):
    """
    사용자 요구사항에 맞춘 index.html 생성:
    - 제목: '금융·경제 아침 브리핑'
    - 맨 위에: 수집 시각
    - 기사마다: 제목, 세 줄 요약, 언론사 이름, 원문 링크
    - 맨 아래에: 'AI 요약은 틀릴 수 있음' 표시
    - 키가 없거나 실패해도 정상 표시
    """
    cards_html = ""
    for idx, art in enumerate(articles, start=1):
        safe_title = html.escape(art["title"])
        safe_source = html.escape(art["source"])
        safe_link = html.escape(art["link"])
        safe_date = html.escape(art["date"])

        # 세 줄 요약 줄바꿈 분리 및 HTML 리스트 처리
        raw_summary = art.get("three_line_summary", "")
        summary_lines = [line.strip() for line in raw_summary.split("\n") if line.strip()]

        summary_items_html = ""
        for line in summary_lines:
            # "- " 접두어가 있다면 제거 후 깔끔한 li 태그로 표시
            display_line = line[2:].strip() if line.startswith("- ") else line
            summary_items_html += f"<li>{html.escape(display_line)}</li>\n"

        cards_html += f"""
        <article class="news-card">
          <div class="card-meta">
            <span class="source-badge">{safe_source}</span>
            <span class="date-text">{safe_date}</span>
          </div>

          <h2 class="article-title">
            <a href="{safe_link}" target="_blank" rel="noopener noreferrer">{safe_title}</a>
          </h2>

          <div class="summary-container">
            <div class="summary-label">📋 세 줄 요약</div>
            <ul class="summary-list">
              {summary_items_html}
            </ul>
          </div>

          <div class="card-footer">
            <a class="origin-btn" href="{safe_link}" target="_blank" rel="noopener noreferrer">
              원문 기사 보기 →
            </a>
          </div>
        </article>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>금융·경제 아침 브리핑</title>
  <style>
    :root {{
      --bg-color: #f8fafc;
      --card-bg: #ffffff;
      --border-color: #e2e8f0;
      --text-main: #0f172a;
      --text-muted: #64748b;
      --primary-color: #0284c7;
      --primary-light: #e0f2fe;
      --badge-bg: #f1f5f9;
      --warning-bg: #fffbeb;
      --warning-text: #b45309;
      --warning-border: #fde68a;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background-color: var(--bg-color);
      color: var(--text-main);
      line-height: 1.6;
      padding: 2rem 1rem;
    }}
    .container {{
      max-width: 760px;
      margin: 0 auto;
    }}
    header {{
      text-align: center;
      margin-bottom: 2rem;
      padding-bottom: 1.25rem;
      border-bottom: 2px solid var(--border-color);
    }}
    header h1 {{
      font-size: 2rem;
      font-weight: 800;
      color: var(--text-main);
      margin-bottom: 0.5rem;
      letter-spacing: -0.02em;
    }}
    .collected-time {{
      font-size: 0.95rem;
      color: var(--text-muted);
      font-weight: 500;
    }}
    .news-feed {{
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
    }}
    .news-card {{
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 1.5rem;
      box-shadow: 0 2px 4px rgba(0, 0, 0, 0.04);
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }}
    .news-card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 8px 16px rgba(0, 0, 0, 0.08);
    }}
    .card-meta {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.75rem;
    }}
    .source-badge {{
      background: var(--primary-light);
      color: var(--primary-color);
      font-size: 0.8rem;
      font-weight: 700;
      padding: 0.2rem 0.6rem;
      border-radius: 6px;
    }}
    .date-text {{
      font-size: 0.85rem;
      color: var(--text-muted);
    }}
    .article-title {{
      font-size: 1.25rem;
      font-weight: 700;
      line-height: 1.4;
      margin-bottom: 1rem;
    }}
    .article-title a {{
      color: var(--text-main);
      text-decoration: none;
    }}
    .article-title a:hover {{
      color: var(--primary-color);
      text-decoration: underline;
    }}
    .summary-container {{
      background: #f8fafc;
      border: 1px solid #edf2f7;
      border-left: 4px solid var(--primary-color);
      border-radius: 6px;
      padding: 1rem 1.2rem;
      margin-bottom: 1rem;
    }}
    .summary-label {{
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--primary-color);
      margin-bottom: 0.5rem;
    }}
    .summary-list {{
      list-style-type: none;
      padding-left: 0;
    }}
    .summary-list li {{
      position: relative;
      padding-left: 1.2rem;
      margin-bottom: 0.4rem;
      font-size: 0.95rem;
      color: #334155;
      line-height: 1.5;
    }}
    .summary-list li:last-child {{
      margin-bottom: 0;
    }}
    .summary-list li::before {{
      content: "-";
      position: absolute;
      left: 0;
      color: var(--primary-color);
      font-weight: bold;
    }}
    .card-footer {{
      display: flex;
      justify-content: flex-end;
      border-top: 1px solid var(--border-color);
      padding-top: 0.75rem;
    }}
    .origin-btn {{
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--primary-color);
      text-decoration: none;
      padding: 0.25rem 0.5rem;
      border-radius: 4px;
      transition: background-color 0.15s ease;
    }}
    .origin-btn:hover {{
      background-color: var(--primary-light);
      text-decoration: underline;
    }}
    footer.disclaimer-box {{
      margin-top: 3rem;
      text-align: center;
      padding: 1rem;
      background: var(--warning-bg);
      border: 1px solid var(--warning-border);
      color: var(--warning-text);
      font-size: 0.9rem;
      font-weight: 600;
      border-radius: 8px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>금융·경제 아침 브리핑</h1>
      <p class="collected-time">수집 시각: {collected_time_str}</p>
    </header>

    <main class="news-feed">
      {cards_html}
    </main>

    <footer class="disclaimer-box">
      ⚠️ AI 요약은 틀릴 수 있음
    </footer>
  </div>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)


def main():
    base_dir = Path(__file__).resolve().parent
    settings_path = base_dir / "settings.json"
    env_path = base_dir / ".env"
    json_path = base_dir / "news.json"
    html_path = base_dir / "index.html"

    collected_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 60)
    print(f"🌅 [금융·경제 아침 브리핑] 수집 시작 ({collected_time_str})")
    print("=" * 60)

    # 1. API 키 불러오기
    api_key = load_env_api_key(env_path)
    if api_key:
        print("🔑 GEMINI_API_KEY 로드 완료")
    else:
        print("ℹ️ GEMINI_API_KEY 가 없습니다. 기본 요약 텍스트로 대체 생성합니다.")

    # 2. settings.json 읽기
    if not settings_path.exists():
        print(f"❌ 설정 파일({settings_path})이 없습니다.")
        return

    with open(settings_path, "r", encoding="utf-8") as f:
        settings = json.load(f)

    keywords = settings.get("keywords", ["은행", "ai", "금융"])

    # 3. RSS 피드 수집 및 선별
    print("\n1. RSS 피드에서 뉴스 수집 중...")
    articles = fetch_all_articles(settings)
    print(f"   -> 전체 {len(articles)}개 기사 수집 완료")

    print("\n2. 키워드 우선순위 최대 5개 기사 선별...")
    selected_top5 = filter_and_select_top5(articles, keywords)
    print(f"   -> {len(selected_top5)}개 기사 선별 완료")

    # 4. 각 기사별 세 줄 요약 생성
    print("\n3. 기사별 Gemini 세 줄 요약 생성 중...")
    for idx, art in enumerate(selected_top5, start=1):
        print(f"   [{idx}/{len(selected_top5)}] 요약 생성 중: {art['title'][:30]}...")
        three_lines = summarize_with_gemini(api_key, art["title"], art["description"])
        art["three_line_summary"] = three_lines

    # 5. 결과 파일 생성 (index.html 및 news.json)
    print("\n4. 결과 파일 생성 중...")

    # index.html 생성
    generate_index_html(html_path, selected_top5, collected_time_str)
    print(f"   -> [HTML] {html_path.name} 생성 완료 (제목: '금융·경제 아침 브리핑')")

    # news.json 생성
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(selected_top5, f, indent=2, ensure_ascii=False)
    print(f"   -> [JSON] {json_path.name} 저장 완료")

    print("\n" + "=" * 60)
    print("🎉 금융·경제 아침 브리핑 생성이 성공적으로 완료되었습니다!")
    print(f"👉 파일 열기: {html_path.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
