"""
[briefing.py]
settings.json에 등록된 RSS 피드에서 뉴스를 수집하고,
선별된 기사의 실제 웹페이지 원문(본문)을 스크래핑한 뒤,
Gemini API를 통해 원문 기반의 충실한 '세 줄 요약'을 생성하여
index.html 및 news.json 결과물을 생성하는 스크립트입니다.

외부 패키지 설치 없이 Python 기본 내장 모듈만 사용합니다.
"""

from datetime import datetime
import email.utils
import html
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


def load_env_api_key(env_path: Path) -> str:
    """
    1) 시스템 환경변수(GitHub Actions Secrets 등)를 우선 확인하고,
    2) 없으면 .env 파일에서 GEMINI_API_KEY를 안전하게 읽어옵니다.
    외부 라이브러리(python-dotenv) 없이 파이썬 기본 기능만 사용합니다.
    """
    # 1. 환경변수 확인
    env_val = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_val:
        return env_val

    # 2. .env 파일 확인
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GEMINI_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
        except Exception:
            pass
    return ""


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


def is_hankyung_premium(source: str = "", title: str = "", link: str = "", category: str = "") -> bool:
    """
    한국경제 기사 중 유료 구독 서비스인 '한경 프리미엄 / 프리미엄9' 기사인지 판별합니다.
    유료 기사는 로그인 및 구독 권한이 필요해 원문 확인(스크래핑)이 불가능하므로 수집에서 제외합니다.
    (일반 한국경제 기본 기사는 정상 수집됩니다.)
    """
    is_hk = ("한국경제" in source) or ("hankyung.com" in link)
    if not is_hk:
        return False

    # 1. URL 경로 확인 (/premium9, /premium 등)
    lower_link = link.lower()
    if "/premium9" in lower_link or "/premium" in lower_link:
        return True

    # 2. 제목 내 프리미엄 관련 키워드 확인
    lower_title = title.lower()
    premium_title_keywords = [
        "프리미엄9", "프리미엄 9", "한경 프리미엄", "한경프리미엄",
        "[프리미엄]", "[한경 프리미엄9]", "[프리미엄9]",
        "한경 exclusive", "한경exclusive", "[한경 exclusive]",
        "epic ai", "에픽 ai"
    ]
    for kw in premium_title_keywords:
        if kw in lower_title:
            return True

    # 3. RSS 카테고리 태그 확인
    lower_cat = category.lower()
    if "프리미엄" in lower_cat or "premium" in lower_cat:
        return True

    return False


def fetch_all_articles(settings: dict) -> list[dict]:
    """
    settings.json의 모든 RSS 피드에서 기사 목록을 수집합니다.
    - [포토] 기사는 제외합니다.
    - 한국경제 유료 기사(한경 프리미엄9)는 원문 확인 불가로 제외합니다.
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
                category = item.findtext("category", default="").strip()

                # 기사 제목 중에 [포토] 들어간 기사는 제외
                if "[포토]" in title:
                    continue

                # 한국경제 프리미엄9 유료 기사 제외 (원문 확인 불가)
                if is_hankyung_premium(feed_name, title, link, category):
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


def fetch_article_content(url: str, fallback_desc: str = "", timeout: int = 10) -> str:
    """
    기사 링크(URL)에 직접 접속하여 웹페이지 본문 텍스트(원문)를 추출합니다.
    외부 라이브러리(BeautifulSoup 등) 설치 없이 파이썬 기본 표준 라이브러리(urllib, re, html)만 사용합니다.
    본문 추출 실패 시 RSS 소개문(fallback_desc)을 반환합니다.
    """
    if not url or not url.startswith("http"):
        return fallback_desc

    # 한국경제 프리미엄9 유료 기사는 스크래핑 건너뛰기
    if is_hankyung_premium("", "", url):
        return fallback_desc

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("content-type", "")
            raw_data = resp.read()

        # 인코딩 확인
        encoding = "utf-8"
        if "charset=" in content_type.lower():
            encoding = content_type.lower().split("charset=")[-1].split(";")[0].strip()

        try:
            html_text = raw_data.decode(encoding)
        except Exception:
            try:
                html_text = raw_data.decode("cp949")
            except Exception:
                html_text = raw_data.decode("utf-8", errors="ignore")

        # 1. 불필요한 태그 및 주석 제거
        cleaned = re.sub(r"<(script|style|head|nav|footer|header|iframe|aside)[^>]*>.*?</\1>", " ", html_text, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)

        # 2. 언론사별 주요 본문 영역 추출 (중첩 태그 안전 대응)
        body_html = ""
        # 한국경제 (hankyung.com)
        if "hankyung.com" in url:
            m = re.search(r'<div[^>]+id=["\']articletxt["\'][^>]*>', cleaned, re.IGNORECASE)
            if m:
                start_pos = m.end()
                end_match = re.search(r'(<div[^>]+class=["\'][^"\']*(?:article-copyright|article-byline|article-extra|article-recirculation)|</article>)', cleaned[start_pos:], re.IGNORECASE)
                body_html = cleaned[start_pos:start_pos + end_match.start()] if end_match else cleaned[start_pos:start_pos + 4000]

        # 매일경제 (mk.co.kr)
        elif "mk.co.kr" in url:
            m = re.search(r'<div[^>]+class=["\'][^"\']*news_cnt_detail_wrap[^"\']*["\'][^>]*>', cleaned, re.IGNORECASE)
            if not m:
                m = re.search(r'<div[^>]+id=["\']article_body["\'][^>]*>', cleaned, re.IGNORECASE)
            if m:
                start_pos = m.end()
                end_match = re.search(r'(<div[^>]+class=["\'][^"\']*(?:art_copyright|byline_wrap|news_btn_bottom|related_news)|</article>)', cleaned[start_pos:], re.IGNORECASE)
                body_html = cleaned[start_pos:start_pos + end_match.start()] if end_match else cleaned[start_pos:start_pos + 4000]

        # 3. 범용 본문 컨테이너 검색
        if not body_html:
            m = re.search(r'<article[^>]*>(.*?)</article>', cleaned, flags=re.DOTALL | re.IGNORECASE)
            if m:
                body_html = m.group(1)
            else:
                p_list = re.findall(r'<p[^>]*>(.*?)</p>', cleaned, flags=re.DOTALL | re.IGNORECASE)
                body_html = " ".join(p_list)

        # 4. 줄바꿈 보존 및 태그 제거
        body_html = re.sub(r'<(?:br|/p|/div|/li)[^>]*>', '\n', body_html, flags=re.IGNORECASE)
        raw_text = re.sub(r'<[^>]+>', ' ', body_html)
        raw_text = html.unescape(raw_text)

        # 5. 이메일, 기자 이름, 저작권 문구만 부분 정제 (전체 줄이 삭제되지 않도록)
        raw_text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '', raw_text)
        raw_text = re.sub(r'[가-힣]{2,4}\s*기자(?:\s*=[^,\n]*)?', '', raw_text)
        raw_text = re.sub(r'(?:ⓒ|저작권자|무단전재|재배포|무단 전재).*', '', raw_text)

        lines = []
        for line in raw_text.splitlines():
            line = re.sub(r'\s+', ' ', line).strip()
            if len(line) >= 15:
                lines.append(line)

        article_content = "\n".join(lines).strip()

        # 만약 본문이 50자 미만으로 부실하면 fallback_desc 활용
        if len(article_content) < 50:
            return fallback_desc if fallback_desc else article_content

        # 본문이 너무 길면 상위 2500자 (충분한 정보 + 초고속 API 처리)
        return article_content[:2500]

    except Exception as e:
        print(f"      ⚠️ 본문 스크래핑 실패 ({url}): {e}")
        return fallback_desc


def create_text_fallback_summary(title: str, content: str) -> str:
    """
    API 키가 없거나 호출 실패 시 기사 본문(원문)에서 실제 문장을 추출하여
    '금융·경제 관련 주요 뉴스입니다' 같은 무의미한 상투어 없이 3줄을 구성합니다.
    """
    if not content:
        return f"- {title}"

    # 문장 단위로 분리 (온점, 느낌표, 물음표 기준)
    sentences = re.split(r'(?<=[.?!])\s+', content)
    meaningful = [
        s.strip() for s in sentences
        if len(s.strip()) >= 20 and not re.search(r'(@|기자|ⓒ|무단전재)', s)
    ]

    result = []
    # 첫째 줄은 제목을 기반으로 정제
    result.append(f"- {title}")

    # 둘째, 셋째 줄은 본문에서 추출한 실제 기사 문장으로 채움
    for s in meaningful:
        if s[:20] in title or title[:20] in s:
            continue
        display_s = s[:100] + "..." if len(s) > 100 else s
        result.append(f"- {display_s}")
        if len(result) >= 3:
            break

    # 만약 문장이 1개뿐이었다면 남은 문장으로 채움
    if len(result) < 3 and content:
        for s in sentences:
            s_clean = s.strip()
            if s_clean and f"- {s_clean}" not in result:
                result.append(f"- {s_clean[:100]}")
                if len(result) >= 3:
                    break

    return "\n".join(result[:3])


def summarize_with_gemini(api_key: str, title: str, content: str) -> str:
    """
    기사 원문(본문 텍스트)을 기반으로 Gemini를 호출하여 핵심 세 줄 요약을 생성합니다.
    - 상투적인 문구('금융·경제 관련 주요 뉴스입니다', '원문 확인 필요' 등)를 절대 배제하고
    - 기사의 실제 원문에 담긴 핵심 사실, 수치, 배경, 영향을 3문장으로 요약합니다.
    """
    clean_content = content.strip() if content else ""

    # 1. API 키가 없거나 비어있는 경우 -> 기사 원문 기반 실제 문장 3줄 fallback 생성
    if not api_key:
        return create_text_fallback_summary(title, clean_content)

    # 2. Gemini 프롬프트 구성 (원문 기반 핵심 3줄 요약)
    prompt = (
        "너는 전문 금융·경제 뉴스 에디터야. 아래 제공된 [기사 원문]을 꼼꼼히 읽고 원문의 핵심 내용을 바탕으로 한국어로 충실한 3줄 요약을 작성해 줘.\n\n"
        "[작성 규칙]\n"
        "1. 정확히 3개의 줄로 작성하고, 각 줄은 반드시 '- ' 로 시작할 것.\n"
        "2. 3줄은 각각 다음 핵심 관점을 다룰 것:\n"
        "   - 첫째 줄: 핵심 사건이나 발표 내용 (무엇이 일어났는가?)\n"
        "   - 둘째 줄: 구체적인 배경, 원인 또는 핵심 수치/사실 (왜, 구체적으로 어떤 내용인가?)\n"
        "   - 셋째 줄: 시장·업계에 미칠 영향이나 향후 전망 (앞으로 어떻게 되는가?)\n"
        "3. 절대로 '금융·경제 관련 주요 뉴스입니다', '자세한 내용은 원문 확인 필요', '기사 내용에 따르면' 같은 무의미한 상투어나 메타 설명, 빈말을 쓰지 말 것.\n"
        "4. '제공해주신 기사 원문이...', '요약해 드립니다', '작성하였습니다' 등 AI 자신이나 지시사항에 대해 언급하는 사족이나 메타 발언을 절대 하지 말고, 3줄 모두 순수 기사 핵심 사실만 작성할 것.\n"
        "5. 오직 [기사 원문]에 실제로 존재하는 구체적인 사실, 수치, 내용만을 기반으로 3줄을 알차게 꽉 채울 것.\n\n"
        f"[기사 제목]\n{title}\n\n"
        f"[기사 원문]\n{clean_content if clean_content else title}"
    )

    models = [
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-2.5-flash",
        "gemini-3.1-flash-lite"
    ]

    for model_name in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "temperature": 0.2
            }
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
                summary_text = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()

                lines = [line.strip() for line in summary_text.splitlines() if line.strip()]
                # '- ' 로 시작하지 않는 줄이 있으면 붙여주기
                formatted_lines = [l if l.startswith("- ") else f"- {l}" for l in lines]

                # 상투어 및 메타 사족 필터링
                meta_patterns = ["원문 확인 필요", "주요 뉴스입니다", "주요 소식입니다", "제공해주신", "원문이 제목", "요약해 드립니다", "작성하였습니다"]
                clean_lines = [
                    l for l in formatted_lines
                    if not any(pat in l for pat in meta_patterns)
                ]

                if len(clean_lines) >= 3:
                    return "\n".join(clean_lines[:3])
                elif len(clean_lines) >= 1:
                    return "\n".join(clean_lines)
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                continue
            print(f"      ⚠️ Gemini 호출 에러 ({model_name}, HTTP {e.code}): {e}")
        except Exception as e:
            print(f"      ⚠️ 요약 실패 ({model_name}): {e}")

    # 모든 모델 호출 실패 시 원문 기반 자연스러운 fallback 생성
    return create_text_fallback_summary(title, clean_content)


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

    # 4. 각 기사별 원문 수집 및 세 줄 요약 생성
    print("\n3. 기사별 원문 수집 및 Gemini 세 줄 요약 생성 중...")
    for idx, art in enumerate(selected_top5, start=1):
        print(f"   [{idx}/{len(selected_top5)}] 원문 수집 중: {art['title'][:30]}...")
        content = fetch_article_content(art["link"], fallback_desc=art["description"])
        art["content"] = content

        print(f"   [{idx}/{len(selected_top5)}] 3줄 요약 생성 중...")
        three_lines = summarize_with_gemini(api_key, art["title"], content)
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
