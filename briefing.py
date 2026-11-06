"""
[briefing.py]
settings.json에 등록된 RSS 피드에서 뉴스를 수집하여,
키워드(은행, ai, 금융)가 포함된 기사를 우선적으로 최대 5개 선별하고,
.env 파일의 Gemini API 키를 활용하여 5개 기사를 1회 초고속 일괄 AI 요약한 뒤
news.json, briefing.md, index.html로 저장하는 완성형 스크립트입니다.

외부 패키지 설치(pip) 없이 Python 표준 라이브러리만 사용합니다.
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

    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("GEMINI_API_KEY", "")


def parse_pubdate(pubdate_str: str) -> tuple[datetime, str]:
    """
    RSS 날짜 문자열(RFC 2822 형식)을 읽어서:
    1. 정렬을 위한 datetime 객체 (비교 가능하도록 offset-naive로 통일)
    2. 사람이 보기 편한 'YYYY-MM-DD HH:MM' 문자열
    두 가지를 튜플로 반환합니다.
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
    settings.json에 등록된 모든 RSS 피드 주소에 접속하여
    기사 목록(제목, 링크, 소개문, 날짜, 언론사)을 수집합니다.
    (기사 본문 웹페이지는 스크래핑하지 않고 RSS 요약 정보만 수집)
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

                if not link or link in seen_links:
                    continue
                seen_links.add(link)

                dt_obj, formatted_date = parse_pubdate(pub_date_raw)

                all_articles.append({
                    "source": feed_name,
                    "title": title,
                    "link": link,
                    "description": description,
                    "pub_date_raw": pub_date_raw,
                    "dt": dt_obj,
                    "date": formatted_date
                })

        except Exception as e:
            print(f"[{feed_name}] RSS 수집 중 오류: {e}")

    return all_articles


def filter_and_select_top5(articles: list[dict], keywords: list[str]) -> list[dict]:
    """
    1. 제목 또는 소개문에 키워드(은행, ai, 금융)가 들어간 기사를 찾습니다.
    2. 키워드가 걸린 기사를 최신순으로 우선 배치합니다.
    3. 5개가 되지 않으면 키워드가 없는 일반 최신 기사로 채워 최대 5개를 만듭니다.
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

        art_with_kw = dict(art)
        art_with_kw["matched_keywords"] = matched

        if matched:
            keyword_articles.append(art_with_kw)
        else:
            other_articles.append(art_with_kw)

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


def summarize_all_with_gemini(api_key: str, articles: list[dict]):
    """
    선별된 5개 기사를 1회의 Gemini API 호출로 초고속 일괄 요약합니다.
    (네트워크 호출 최소화 및 타임아웃 방지)
    """
    if not api_key or not articles:
        for art in articles:
            art["ai_summary"] = art["description"] or "요약 없음"
        return

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={api_key}"

    items_text = []
    for idx, art in enumerate(articles, start=1):
        desc = art["description"] if art["description"] else "상세 본문 요약 없음"
        items_text.append(f"[{idx}] 제목: {art['title']}\n    소개: {desc}")

    prompt = (
        "당신은 금융/경제 뉴스 브리핑 전문가입니다. 아래 제공된 5개 기사의 핵심 내용을 바쁜 현대인을 위해 "
        "각각 1~2문장의 친절하고 명확한 한국어로 요약해 주세요.\n"
        "반드시 기사 순서대로 요약 문자열만을 담은 JSON 문자열 배열 형식으로만 응답해 주세요.\n"
        "예시 포맷: [\"1번 요약문\", \"2번 요약문\", \"3번 요약문\", \"4번 요약문\", \"5번 요약문\"]\n\n"
        + "\n\n".join(items_text)
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=25) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            raw_text = res_data["candidates"][0]["content"]["parts"][0]["text"]
            summaries = json.loads(raw_text)

            for idx, art in enumerate(articles):
                if idx < len(summaries) and summaries[idx]:
                    art["ai_summary"] = summaries[idx].strip()
                else:
                    art["ai_summary"] = art["description"] or "요약 없음"
            print("   -> 5개 기사 Gemini 일괄 요약 완료!")
    except Exception as e:
        print(f"   ⚠️ 일괄 요약 실패(기본 소개문 사용): {e}")
        for art in articles:
            art["ai_summary"] = art["description"] or "요약 없음"


def save_markdown_report(output_path: Path, keywords: list[str], articles: list[dict]):
    """
    선별된 기사들을 깔끔한 마크다운(.md) 보고서 파일로 저장합니다.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 📰 데일리 뉴스 브리핑 TOP 5 (with Gemini AI)",
        "",
        f"- **생성 일시**: {now_str}",
        f"- **타깃 키워드**: `{', '.join(keywords)}`",
        "",
        "---",
        ""
    ]

    for idx, art in enumerate(articles, start=1):
        if art["matched_keywords"]:
            kw_tags = " ".join([f"`#{kw}`" for kw in art["matched_keywords"]])
        else:
            kw_tags = "`#최신기사`"

        lines.append(f"### {idx}. [{art['title']}]({art['link']})")
        lines.append(f"- **출처**: {art['source']} | **발행일**: {art['date']}")
        lines.append(f"- **키워드**: {kw_tags}")
        ai_sum = art.get("ai_summary", art["description"])
        if ai_sum:
            lines.append(f"- **🤖 Gemini 핵심 요약**: {ai_sum}")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def save_html_dashboard(output_path: Path, keywords: list[str], articles: list[dict]):
    """
    더블 클릭만으로 브라우저에서 볼 수 있는 모던한 반응형 HTML 카드 대시보드를 생성합니다.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cards_html = ""
    for idx, art in enumerate(articles, start=1):
        safe_title = html.escape(art["title"])
        summary_text = art.get("ai_summary") or art.get("description") or "요약 없음"
        safe_desc = html.escape(summary_text)
        safe_link = html.escape(art["link"])
        safe_source = html.escape(art["source"])
        safe_date = html.escape(art["date"])

        tags_html = ""
        if art["matched_keywords"]:
            for kw in art["matched_keywords"]:
                tags_html += f'<span class="badge badge-kw">#{html.escape(kw)}</span>'
        else:
            tags_html = '<span class="badge badge-normal">#최신</span>'

        cards_html += f"""
        <article class="news-card">
          <div class="card-header">
            <span class="source-tag">{safe_source}</span>
            <span class="rank-tag">TOP {idx}</span>
          </div>
          <h2 class="card-title">
            <a href="{safe_link}" target="_blank" rel="noopener noreferrer">{safe_title}</a>
          </h2>
          <div class="ai-box">
            <span class="ai-label">🤖 Gemini 핵심 요약</span>
            <p class="card-desc">{safe_desc}</p>
          </div>
          <div class="card-footer">
            <div class="tags-container">{tags_html}</div>
            <div class="date-container">🕒 {safe_date}</div>
          </div>
        </article>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>데일리 뉴스 브리핑 TOP 5</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --card-border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #38bdf8;
      --badge-kw-bg: rgba(56, 189, 248, 0.15);
      --badge-kw-text: #38bdf8;
      --badge-norm-bg: rgba(148, 163, 184, 0.15);
      --badge-norm-text: #94a3b8;
      --source-bg: #334155;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 2rem 1rem;
    }}
    .container {{
      max-width: 860px;
      margin: 0 auto;
    }}
    header {{
      margin-bottom: 2rem;
      text-align: center;
      border-bottom: 1px solid var(--card-border);
      padding-bottom: 1.5rem;
    }}
    header h1 {{
      font-size: 2rem;
      font-weight: 800;
      color: var(--text);
      margin-bottom: 0.5rem;
    }}
    .meta-bar {{
      font-size: 0.9rem;
      color: var(--text-muted);
      display: flex;
      justify-content: center;
      gap: 1.5rem;
      flex-wrap: wrap;
    }}
    .keyword-pill {{
      background: var(--badge-kw-bg);
      color: var(--badge-kw-text);
      padding: 0.15rem 0.6rem;
      border-radius: 9999px;
      font-weight: 600;
    }}
    .news-list {{
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }}
    .news-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 1.5rem;
      transition: transform 0.2s ease, border-color 0.2s ease;
    }}
    .news-card:hover {{
      transform: translateY(-2px);
      border-color: var(--primary);
    }}
    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.75rem;
    }}
    .source-tag {{
      background: var(--source-bg);
      font-size: 0.75rem;
      font-weight: 700;
      padding: 0.2rem 0.5rem;
      border-radius: 6px;
    }}
    .rank-tag {{
      font-size: 0.75rem;
      font-weight: 800;
      color: var(--primary);
    }}
    .card-title {{
      font-size: 1.2rem;
      font-weight: 700;
      margin-bottom: 0.8rem;
      line-height: 1.4;
    }}
    .card-title a {{
      color: var(--text);
      text-decoration: none;
    }}
    .card-title a:hover {{
      color: var(--primary);
      text-decoration: underline;
    }}
    .ai-box {{
      background: rgba(15, 23, 42, 0.6);
      border-left: 3px solid var(--primary);
      padding: 0.75rem 1rem;
      border-radius: 0 8px 8px 0;
      margin-bottom: 1rem;
    }}
    .ai-label {{
      font-size: 0.75rem;
      font-weight: 700;
      color: var(--primary);
      display: block;
      margin-bottom: 0.25rem;
    }}
    .card-desc {{
      color: #cbd5e1;
      font-size: 0.95rem;
    }}
    .card-footer {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
      font-size: 0.85rem;
      border-top: 1px solid rgba(255, 255, 255, 0.05);
      padding-top: 0.75rem;
    }}
    .tags-container {{
      display: flex;
      gap: 0.4rem;
    }}
    .badge {{
      display: inline-block;
      padding: 0.15rem 0.5rem;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 600;
    }}
    .badge-kw {{
      background: var(--badge-kw-bg);
      color: var(--badge-kw-text);
      border: 1px solid rgba(56, 189, 248, 0.3);
    }}
    .badge-normal {{
      background: var(--badge-norm-bg);
      color: var(--badge-norm-text);
    }}
    .date-container {{
      color: var(--text-muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>📰 데일리 뉴스 브리핑 TOP 5</h1>
      <div class="meta-bar">
        <span>🕒 수집 일시: <strong>{now_str}</strong></span>
        <span>🏷️ 감지 키워드: <span class="keyword-pill">{', '.join(keywords)}</span></span>
      </div>
    </header>
    <main class="news-list">
      {cards_html}
    </main>
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
    md_path = base_dir / "briefing.md"
    html_path = base_dir / "index.html"

    # API 키 로드
    api_key = load_env_api_key(env_path)
    if api_key:
        print("🔑 Gemini API 키 감지 완료 (AI 요약 기능 활성화)")
    else:
        print("ℹ️ Gemini API 키가 없어 기본 소개문으로 진행합니다.")

    if not settings_path.exists():
        print(f"설정 파일({settings_path})이 존재하지 않습니다.")
        return

    with open(settings_path, "r", encoding="utf-8") as f:
        settings = json.load(f)

    keywords = settings.get("keywords", ["은행", "ai", "금융"])

    print("=" * 60)
    print("1. 모든 RSS 피드에서 기사 수집 중...")
    articles = fetch_all_articles(settings)
    print(f"   -> 총 {len(articles)}개의 기사를 수집했습니다.")

    print("\n2. 키워드 우선순위 필터링 (최대 5개 선별)...")
    selected_top5 = filter_and_select_top5(articles, keywords)

    # 3. Gemini AI 일괄 요약 생성
    if api_key:
        print("\n3. 🤖 Gemini AI를 활용한 5개 기사 핵심 요약 생성 중...")
        summarize_all_with_gemini(api_key, selected_top5)
    else:
        for art in selected_top5:
            art["ai_summary"] = art["description"]

    print("\n4. 결과 파일 생성 중...")
    # 4-1. news.json 저장
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(selected_top5, f, indent=2, ensure_ascii=False)
    print(f"   -> [JSON] {json_path.name} 저장 완료")

    # 4-2. briefing.md 저장
    save_markdown_report(md_path, keywords, selected_top5)
    print(f"   -> [Markdown] {md_path.name} 저장 완료")

    # 4-3. index.html 저장
    save_html_dashboard(html_path, keywords, selected_top5)
    print(f"   -> [HTML] {html_path.name} 저장 완료")

    print("\n" + "=" * 60)
    print("🎉 뉴스 브리핑 & AI 요약이 성공적으로 완료되었습니다!")
    print("=" * 60)


if __name__ == "__main__":
    main()
