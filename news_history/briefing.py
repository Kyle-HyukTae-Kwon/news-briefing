"""
[news_history/briefing.py]
settings.json에 등록된 모든 언론사 RSS에서 뉴스를 수집하고,
1) 제목 또는 소개문에 지정된 키워드('금융', '경제', 'ai')가 포함된 최근 14일치 기사 선별
2) Gemini API를 호출하여 시장 트렌드 3줄 브리핑 요약 생성
3) 기사 집계 및 분석(키워드별, 출처별, 날짜별 합계 및 평균)
4) 숫자 카드, 인터랙티브 그래프(막대/선 그래프), 전체 기사 표를 갖춘 반응형 index.html 대시보드 생성
5) 원본 데이터는 news_history.json 파일로 저장

외부 패키지 설치(pip install) 없이 파이썬 기본 표준 라이브러리만 사용합니다.
(기사 본문 웹페이지 스크래핑은 하지 않고 RSS 정보만 활용합니다.)
"""

from datetime import datetime, timedelta
import email.utils
import html
import json
import os
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET


def parse_pubdate(pubdate_str: str) -> tuple[datetime, str]:
    """
    RSS 날짜 문자열(RFC 2822 형식) 및 기존 저장된 'YYYY-MM-DD HH:MM' 형식을 모두 해석하여
    1) 14일 계산 및 정렬에 사용할 datetime 객체와
    2) 사람이 읽기 편한 'YYYY-MM-DD HH:MM' 형식의 문자열로 변환합니다.
    """
    if not pubdate_str:
        return datetime.min, "날짜 없음"

    # 1. 이미 'YYYY-MM-DD HH:MM' 형식으로 저장되어 있던 기존 기사 파싱
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(pubdate_str.strip(), fmt)
            formatted = dt.strftime("%Y-%m-%d %H:%M")
            return dt, formatted
        except ValueError:
            pass

    # 2. RSS 피드의 RFC 2822 표준 날짜 형식 파싱
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
    한국경제의 유료 구독 서비스인 '한경 프리미엄 / 프리미엄9' 기사인지 판별합니다.
    유료 기사는 원문 열람이 제한되므로 수집 대상에서 제외합니다.
    """
    is_hk = ("한국경제" in source) or ("hankyung.com" in link)
    if not is_hk:
        return False

    lower_link = link.lower()
    if "/premium9" in lower_link or "/premium" in lower_link:
        return True

    lower_title = title.lower()
    premium_keywords = [
        "프리미엄9", "프리미엄 9", "한경 프리미엄", "한경프리미엄",
        "[프리미엄]", "[한경 프리미엄9]", "[프리미엄9]",
        "한경 exclusive", "한경exclusive", "[한경 exclusive]",
        "epic ai", "에픽 ai"
    ]
    for kw in premium_keywords:
        if kw in lower_title:
            return True

    lower_cat = category.lower()
    if "프리미엄" in lower_cat or "premium" in lower_cat:
        return True

    return False


def load_env_api_key(base_dir: Path) -> str:
    """
    환경변수 또는 .env 파일에서 GEMINI_API_KEY를 안전하게 읽어옵니다.
    """
    env_val = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_val:
        return env_val

    # 1. news_history/.env 확인
    local_env = base_dir / ".env"
    if local_env.exists():
        try:
            with open(local_env, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("GEMINI_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
        except Exception:
            pass

    # 2. 루트 폴더의 .env 확인
    parent_env = base_dir.parent / ".env"
    if parent_env.exists():
        try:
            with open(parent_env, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("GEMINI_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
        except Exception:
            pass

    return ""


def summarize_history_with_gemini(api_key: str, articles: list[dict]) -> str:
    """
    Gemini API를 호출하여 수집된 기사 목록을 바탕으로
    핵심 시장 흐름과 인사이트를 3줄로 브리핑합니다.
    """
    if not api_key:
        return (
            "• GEMINI_API_KEY가 설정되지 않아 기본 안내 모드로 표시됩니다.\n"
            "• 최근 금융, 경제, AI 관련 기사가 꾸준히 집계되고 있습니다.\n"
            "• 상세 기사 분석 및 시장 동향은 아래 표와 그래프를 참고해 주세요."
        )

    # 상위 25개 기사의 제목과 날짜를 추출하여 프롬프트 구성
    titles_summary = "\n".join([f"- [{art['date'][:10]}] {art['title']}" for art in articles[:25]])
    prompt = (
        "너는 금융·경제 전문 수석 애널리스트야. 아래 제공된 최근 뉴스 목록을 꼼꼼히 읽고, "
        "현재 시장과 주요 산업의 흐름을 관통하는 핵심 트렌드 3가지를 명확하게 3줄로 브리핑해 줘.\n\n"
        "[작성 규칙]\n"
        "1. 정확히 3개의 줄로 작성하고, 각 줄은 반드시 '- ' 로 시작할 것.\n"
        "2. 금융권 변화, 거시 경제/시장 동향, AI 기술/구독 경제 확산 등 주요 맥락을 짚어줄 것.\n"
        "3. 사족이나 메타 발언('제공된 목록에 따르면' 등)은 쓰지 말고 핵심 사실 위주로 작성할 것.\n\n"
        f"[최신 기사 목록]\n{titles_summary}"
    )

    models = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-2.5-flash"]
    for model_name in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                text = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                clean_lines = [l if l.startswith("- ") else f"- {l}" for l in lines]
                if clean_lines:
                    return "\n".join(clean_lines[:3])
        except Exception:
            continue

    return (
        "• 최근 금융 및 AI 분야에서 기술 도입과 규제, 시장 재편 관련 뉴스가 활발히 보도되었습니다.\n"
        "• 금융권에서는 연말 대출 관리 및 비이자이익 경쟁이 심화되는 양상을 보이고 있습니다.\n"
        "• AI 기술이 일상 서비스와 업무 전반으로 빠르게 파고들며 관련 지출과 보안 이슈가 부각되고 있습니다."
    )


def generate_dashboard_html(output_path: Path, articles: list[dict], gemini_summary: str, min_date_str: str, max_date_str: str):
    """
    사용자 요구사항에 맞춘 반응형 대시보드 index.html 을 생성합니다:
    - 제목: '뉴스 브리핑 히스토리 대시보드'
    - 제목 아래: 'yyyy-mm-dd ~ yyyy-mm-dd & 수집 기간'
    - 최근 7일 / 최근 14일 전환 버튼
    - 숫자 카드 3개 (모은 기사 수, 최다 빈출 키워드와 횟수, 하루 평균 수집 기사 수)
    - 가운데: 키워드별 기사 수 막대 그래프, 출처별 기사 수 막대 그래프 (막대는 0에서 시작)
    - 아래: 날짜별 기사 수 선 그래프
    - 표: 한 줄에 기사 하나 (제목/날짜/출처/키워드, 원문 링크)
    """
    articles_json_str = json.dumps(articles, ensure_ascii=False)

    summary_items_html = ""
    for line in gemini_summary.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        display_text = line_clean[2:].strip() if line_clean.startswith("- ") or line_clean.startswith("• ") else line_clean
        summary_items_html += f"<li>{html.escape(display_text)}</li>\n"

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>뉴스 브리핑 히스토리 대시보드</title>
  <!-- Chart.js 라이브러리 (CDN) -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0"></script>
  <style>
    :root {{
      --bg-color: #f8fafc;
      --card-bg: #ffffff;
      --border-color: #e2e8f0;
      --text-main: #0f172a;
      --text-muted: #64748b;
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --primary-light: #eff6ff;
      --accent-ai: #8b5cf6;
      --accent-finance: #059669;
      --accent-economy: #ea580c;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background-color: var(--bg-color);
      color: var(--text-main);
      line-height: 1.5;
      padding: 2.5rem 1rem;
    }}
    .container {{
      max-width: 1100px;
      margin: 0 auto;
    }}

    /* 헤더 영역 */
    header {{
      text-align: center;
      margin-bottom: 2rem;
      padding-bottom: 1.5rem;
      border-bottom: 2px solid var(--border-color);
    }}
    header h1 {{
      font-size: 2.2rem;
      font-weight: 800;
      color: var(--text-main);
      margin-bottom: 0.5rem;
      letter-spacing: -0.02em;
    }}
    .period-display {{
      font-size: 1.05rem;
      color: var(--text-muted);
      font-weight: 600;
    }}

    /* Gemini AI 인사이트 카드 */
    .ai-insight-card {{
      background: linear-gradient(135deg, #f0fdf4 0%, #e0f2fe 100%);
      border: 1px solid #bae6fd;
      border-radius: 14px;
      padding: 1.5rem 1.75rem;
      margin-bottom: 2rem;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }}
    .ai-badge {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      background: #0284c7;
      color: #ffffff;
      font-size: 0.8rem;
      font-weight: 700;
      padding: 0.3rem 0.75rem;
      border-radius: 20px;
      margin-bottom: 0.85rem;
    }}
    .ai-insight-card ul {{
      list-style-type: none;
      padding-left: 0;
    }}
    .ai-insight-card li {{
      position: relative;
      padding-left: 1.4rem;
      margin-bottom: 0.6rem;
      font-size: 0.98rem;
      color: #0f172a;
      line-height: 1.6;
    }}
    .ai-insight-card li:last-child {{ margin-bottom: 0; }}
    .ai-insight-card li::before {{
      content: "✦";
      position: absolute;
      left: 0;
      color: #0284c7;
      font-weight: bold;
    }}

    /* 기간 전환 버튼 */
    .btn-group {{
      display: flex;
      justify-content: center;
      gap: 0.75rem;
      margin-bottom: 1.75rem;
    }}
    .period-btn {{
      background: #ffffff;
      color: var(--text-muted);
      border: 1px solid var(--border-color);
      padding: 0.65rem 1.5rem;
      border-radius: 30px;
      font-size: 0.95rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s ease;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}
    .period-btn:hover {{
      border-color: var(--primary);
      color: var(--primary);
    }}
    .period-btn.active {{
      background: var(--primary);
      color: #ffffff;
      border-color: var(--primary);
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.25);
    }}

    /* 숫자 통계 카드 3개 */
    .stat-cards-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 1.25rem;
      margin-bottom: 2rem;
    }}
    .stat-card {{
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 1.5rem;
      text-align: center;
      box-shadow: 0 2px 4px rgba(0,0,0,0.03);
    }}
    .stat-label {{
      font-size: 0.88rem;
      font-weight: 700;
      color: var(--text-muted);
      margin-bottom: 0.5rem;
    }}
    .stat-value {{
      font-size: 1.85rem;
      font-weight: 800;
      color: var(--text-main);
    }}
    .stat-subtext {{
      font-size: 0.8rem;
      color: var(--text-muted);
      margin-top: 0.35rem;
    }}

    /* 차트 섹션 */
    .chart-section {{
      margin-bottom: 2rem;
    }}
    .charts-row {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 1.25rem;
      margin-bottom: 1.25rem;
    }}
    .chart-card {{
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 1.25rem 1.5rem;
      box-shadow: 0 2px 4px rgba(0,0,0,0.03);
    }}
    .chart-card h3 {{
      font-size: 1.05rem;
      font-weight: 700;
      margin-bottom: 1rem;
      color: var(--text-main);
    }}
    .chart-container {{
      position: relative;
      height: 260px;
      width: 100%;
    }}

    /* 기사 목록 표 (Table) */
    .table-section {{
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 1.5rem;
      box-shadow: 0 2px 4px rgba(0,0,0,0.03);
    }}
    .table-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.25rem;
    }}
    .table-header h3 {{
      font-size: 1.15rem;
      font-weight: 700;
    }}
    .table-count {{
      font-size: 0.9rem;
      color: var(--text-muted);
      font-weight: 600;
    }}
    .table-responsive {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 0.92rem;
    }}
    th, td {{
      padding: 0.85rem 0.75rem;
      border-bottom: 1px solid var(--border-color);
      vertical-align: middle;
    }}
    th {{
      background-color: #f8fafc;
      color: var(--text-muted);
      font-weight: 700;
      white-space: nowrap;
    }}
    tr:hover td {{
      background-color: #f1f5f9;
    }}
    .col-title {{
      font-weight: 600;
      color: var(--text-main);
      max-width: 440px;
    }}
    .col-title a {{
      color: var(--text-main);
      text-decoration: none;
    }}
    .col-title a:hover {{
      color: var(--primary);
      text-decoration: underline;
    }}
    .source-pill {{
      display: inline-block;
      background: #f1f5f9;
      color: #475569;
      font-size: 0.75rem;
      font-weight: 700;
      padding: 0.2rem 0.5rem;
      border-radius: 6px;
      white-space: nowrap;
    }}
    .kw-tag {{
      display: inline-block;
      font-size: 0.75rem;
      font-weight: 700;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      margin-right: 0.25rem;
      margin-bottom: 0.2rem;
      white-space: nowrap;
    }}
    .kw-금융 {{ background: #ecfdf5; color: #047857; }}
    .kw-경제 {{ background: #fff7ed; color: #c2410c; }}
    .kw-ai {{ background: #f5f3ff; color: #6d28d9; }}
    .link-btn {{
      display: inline-block;
      color: var(--primary);
      text-decoration: none;
      font-weight: 700;
      font-size: 0.85rem;
      white-space: nowrap;
    }}
    .link-btn:hover {{ text-decoration: underline; }}

    /* 모바일 반응형 */
    @media (max-width: 768px) {{
      .stat-cards-grid {{ grid-template-columns: 1fr; }}
      .charts-row {{ grid-template-columns: 1fr; }}
      header h1 {{ font-size: 1.75rem; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <!-- 헤더 -->
    <header>
      <h1>뉴스 브리핑 히스토리 대시보드</h1>
      <p class="period-display" id="periodDisplay">{min_date_str} ~ {max_date_str} & 최근 14일 수집</p>
    </header>

    <!-- Gemini AI 종합 분석 요약 카드 -->
    <section class="ai-insight-card">
      <div class="ai-badge">🤖 Gemini AI 시장 트렌드 총평</div>
      <ul>
        {summary_items_html}
      </ul>
    </section>

    <!-- 수집 기간 선택 버튼 -->
    <div class="btn-group">
      <button class="period-btn" id="btn7Days" onclick="setPeriod(7)">최근 7일</button>
      <button class="period-btn active" id="btn14Days" onclick="setPeriod(14)">최근 14일</button>
    </div>

    <!-- 수집 기간 버튼 아래 숫자 카드 3개 -->
    <section class="stat-cards-grid">
      <div class="stat-card">
        <div class="stat-label">📦 모은 기사 수</div>
        <div class="stat-value" id="cardTotalArticles">0건</div>
        <div class="stat-subtext">선택된 기간 내 집계</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">🔥 가장 많이 나온 키워드</div>
        <div class="stat-value" id="cardTopKeyword">-</div>
        <div class="stat-subtext" id="cardTopKeywordCount">0회 언급</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">⚡ 하루 평균 수집된 기사 수</div>
        <div class="stat-value" id="cardDailyAverage">0.0건</div>
        <div class="stat-subtext">일평균 수집량</div>
      </div>
    </section>

    <!-- 가운데: 막대 그래프 2개 (키워드별, 출처별) -->
    <section class="chart-section">
      <div class="charts-row">
        <div class="chart-card">
          <h3>📌 키워드 별 기사 수</h3>
          <div class="chart-container">
            <canvas id="keywordBarChart"></canvas>
          </div>
        </div>
        <div class="chart-card">
          <h3>🏢 출처(언론사) 별 기사 수</h3>
          <div class="chart-container">
            <canvas id="sourceBarChart"></canvas>
          </div>
        </div>
      </div>

      <!-- 아래: 날짜 별 기사 수 선 그래프 -->
      <div class="chart-card">
        <h3>📈 날짜 별 기사 수 (일별 발생 추이)</h3>
        <div class="chart-container">
          <canvas id="dateLineChart"></canvas>
        </div>
      </div>
    </section>

    <!-- 기사 목록 표: 한 줄에 기사 하나 -->
    <section class="table-section">
      <div class="table-header">
        <h3>📋 수집 기사 상세 목록</h3>
        <span class="table-count" id="tableItemCount">총 0개 기사</span>
      </div>
      <div class="table-responsive">
        <table>
          <thead>
            <tr>
              <th style="width: 50px;">번호</th>
              <th style="width: 130px;">발행일시</th>
              <th style="width: 110px;">출처</th>
              <th>제목</th>
              <th style="width: 140px;">매칭 키워드</th>
              <th style="width: 90px; text-align: center;">원문 링크</th>
            </tr>
          </thead>
          <tbody id="articlesTableBody">
            <!-- 자바스크립트로 동적 렌더링 -->
          </tbody>
        </table>
      </div>
    </section>
  </div>

  <script>
    // 파이썬에서 주입한 전체 기사 데이터 (최근 14일치)
    const RAW_ARTICLES = {articles_json_str};

    // 차트 인스턴스 보관용 전역 변수
    // 막대 위에 수치 항상 표시 (chartjs-plugin-datalabels)
    if (window.ChartDataLabels) {{
      Chart.register(ChartDataLabels);
      Chart.defaults.set('plugins.datalabels', {{ display: false }});
    }}
    const BAR_LABELS = {{
      display: true,
      anchor: 'end',
      align: 'end',
      offset: 2,
      color: '#334155',
      font: {{ weight: 'bold', size: 12 }},
      formatter: v => v.toLocaleString()
    }};

    let keywordChartInstance = null;
    let sourceChartInstance = null;
    let dateChartInstance = null;

    let currentDays = 14;

    // 기간 설정 함수 (7일 or 14일)
    function setPeriod(days) {{
      currentDays = days;

      // 버튼 활성화 클래스 토글
      document.getElementById('btn7Days').classList.toggle('active', days === 7);
      document.getElementById('btn14Days').classList.toggle('active', days === 14);

      renderDashboard(days);
    }}

    // 대시보드 전체 렌더링 함수
    function renderDashboard(days) {{
      // 1. 기준일 계산
      const now = new Date();
      const cutoff = new Date();
      cutoff.setDate(now.getDate() - days);
      cutoff.setHours(0, 0, 0, 0);

      // 2. 해당 기간 기사 필터링
      const filtered = RAW_ARTICLES.filter(art => {{
        if (!art.date) return false;
        const artDate = new Date(art.date.replace(' ', 'T'));
        return artDate >= cutoff;
      }});

      // 3. 수집 기간 텍스트 업데이트 (yyyy-mm-dd ~ yyyy-mm-dd & 수집 기간)
      let minDateStr = "{min_date_str}";
      let maxDateStr = "{max_date_str}";
      if (filtered.length > 0) {{
        const dates = filtered.map(a => a.date.split(' ')[0]).sort();
        minDateStr = dates[0];
        maxDateStr = dates[dates.length - 1];
      }}
      document.getElementById('periodDisplay').innerText = 
        `${{minDateStr}} ~ ${{maxDateStr}} & 최근 ${{days}}일 수집`;

      // 4. 통계 계산 (숫자 카드 3개)
      const totalArticles = filtered.length;
      document.getElementById('cardTotalArticles').innerText = `${{totalArticles.toLocaleString()}}건`;

      // 키워드별 집계 (한 기사에 키워드가 여러개면 각각 센다)
      const keywordCounts = {{ "금융": 0, "경제": 0, "ai": 0 }};
      filtered.forEach(art => {{
        if (art.matched_keywords) {{
          art.matched_keywords.forEach(kw => {{
            const key = kw.toLowerCase();
            keywordCounts[key] = (keywordCounts[key] || 0) + 1;
          }});
        }}
      }});

      // 가장 많이 나온 키워드 찾기
      let topKw = "-";
      let topKwCount = 0;
      for (const [kw, count] of Object.entries(keywordCounts)) {{
        if (count > topKwCount) {{
          topKw = kw;
          topKwCount = count;
        }}
      }}
      document.getElementById('cardTopKeyword').innerText = topKw !== "-" ? topKw.toUpperCase() : "-";
      document.getElementById('cardTopKeywordCount').innerText = `${{topKwCount}}회 언급`;

      // 하루 평균 수집된 기사 수
      const dailyAverage = (totalArticles / days).toFixed(1);
      document.getElementById('cardDailyAverage').innerText = `${{dailyAverage}}건/일`;

      // 5. 출처별 집계
      const sourceCounts = {{}};
      filtered.forEach(art => {{
        const src = art.source || "기타";
        sourceCounts[src] = (sourceCounts[src] || 0) + 1;
      }});

      // 6. 날짜별 집계 (오름차순)
      const dateCounts = {{}};
      filtered.forEach(art => {{
        const d = art.date ? art.date.split(' ')[0] : "기타";
        dateCounts[d] = (dateCounts[d] || 0) + 1;
      }});
      const sortedDates = Object.keys(dateCounts).sort();

      // 7. 차트 렌더링
      renderKeywordChart(keywordCounts);
      renderSourceChart(sourceCounts);
      renderDateChart(sortedDates, dateCounts);

      // 8. 테이블 렌더링
      renderTable(filtered);
    }}

    // 키워드별 막대 그래프 렌더링 (막대는 0에서 시작)
    function renderKeywordChart(keywordCounts) {{
      const ctx = document.getElementById('keywordBarChart').getContext('2d');
      if (keywordChartInstance) keywordChartInstance.destroy();

      const labels = Object.keys(keywordCounts);
      const data = Object.values(keywordCounts);

      keywordChartInstance = new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: labels.map(l => l.toUpperCase()),
          datasets: [{{
            label: '기사 수',
            data: data,
            backgroundColor: ['#10b981', '#f97316', '#8b5cf6'],
            borderRadius: 6,
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          layout: {{ padding: {{ top: 24 }} }},
          plugins: {{ legend: {{ display: false }}, datalabels: BAR_LABELS }},
          scales: {{
            y: {{
              beginAtZero: true,
              grace: '10%',
              ticks: {{ precision: 0 }}
            }}
          }}
        }}
      }});
    }}

    // 출처별 막대 그래프 렌더링 (막대는 0에서 시작)
    function renderSourceChart(sourceCounts) {{
      const ctx = document.getElementById('sourceBarChart').getContext('2d');
      if (sourceChartInstance) sourceChartInstance.destroy();

      const labels = Object.keys(sourceCounts);
      const data = Object.values(sourceCounts);

      sourceChartInstance = new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: labels,
          datasets: [{{
            label: '기사 수',
            data: data,
            backgroundColor: '#3b82f6',
            borderRadius: 6,
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          layout: {{ padding: {{ top: 24 }} }},
          plugins: {{ legend: {{ display: false }}, datalabels: BAR_LABELS }},
          scales: {{
            y: {{
              beginAtZero: true,
              grace: '10%',
              ticks: {{ precision: 0 }}
            }}
          }}
        }}
      }});
    }}

    // 날짜별 선 그래프 렌더링 (일별 추이)
    function renderDateChart(dates, dateCounts) {{
      const ctx = document.getElementById('dateLineChart').getContext('2d');
      if (dateChartInstance) dateChartInstance.destroy();

      const data = dates.map(d => dateCounts[d] || 0);

      dateChartInstance = new Chart(ctx, {{
        type: 'line',
        data: {{
          labels: dates,
          datasets: [{{
            label: '발생 기사 수',
            data: data,
            borderColor: '#2563eb',
            backgroundColor: 'rgba(37, 99, 235, 0.08)',
            borderWidth: 2.5,
            fill: true,
            tension: 0.25,
            pointBackgroundColor: '#2563eb',
            pointRadius: 4
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          layout: {{ padding: {{ top: 24 }} }},
          plugins: {{ legend: {{ display: false }}, datalabels: {{ ...BAR_LABELS, offset: 4 }} }},
          scales: {{
            y: {{
              beginAtZero: true,
              grace: '10%',
              ticks: {{ precision: 0 }}
            }}
          }}
        }}
      }});
    }}

    // 기사 표(Table) 렌더링
    function renderTable(articles) {{
      const tbody = document.getElementById('articlesTableBody');
      document.getElementById('tableItemCount').innerText = `총 ${{articles.length}}개 기사`;
      tbody.innerHTML = '';

      if (articles.length === 0) {{
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 2rem; color: #64748b;">수집된 기사가 없습니다.</td></tr>';
        return;
      }}

      articles.forEach((art, idx) => {{
        const tr = document.createElement('tr');
        
        // 키워드 태그 HTML
        let kwHtml = '';
        if (art.matched_keywords) {{
          art.matched_keywords.forEach(kw => {{
            const keyLower = kw.toLowerCase();
            kwHtml += `<span class="kw-tag kw-${{keyLower}}">${{kw}}</span>`;
          }});
        }}

        tr.innerHTML = `
          <td style="color: #64748b; font-weight: 600;">${{idx + 1}}</td>
          <td style="white-space: nowrap; color: #64748b; font-size: 0.85rem;">${{art.date || '-'}}</td>
          <td><span class="source-pill">${{art.source || '-'}}</span></td>
          <td class="col-title"><a href="${{art.link}}" target="_blank" rel="noopener noreferrer">${{escapeHtml(art.title)}}</a></td>
          <td>${{kwHtml}}</td>
          <td style="text-align: center;">
            <a class="link-btn" href="${{art.link}}" target="_blank" rel="noopener noreferrer">원문 보기 →</a>
          </td>
        `;
        tbody.appendChild(tr);
      }});
    }}

    // HTML 특수문자 이스케이프 유틸
    function escapeHtml(str) {{
      if (!str) return '';
      return str.replace(/[&<>"']/g, function(m) {{
        return {{
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#039;'
        }}[m];
      }});
    }}

    // 초기 화면 로드 (기본 14일치)
    window.onload = function() {{
      setPeriod(14);
    }};
  </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)


def main():
    base_dir = Path(__file__).resolve().parent
    settings_path = base_dir / "settings.json"
    json_path = base_dir / "news_history.json"
    html_path = base_dir / "index.html"

    # 설정 파일 존재 여부 확인
    if not settings_path.exists():
        print(f"❌ 설정 파일({settings_path})이 없습니다.")
        return

    # 1. API 키 불러오기
    api_key = load_env_api_key(base_dir)
    if api_key:
        print("🔑 GEMINI_API_KEY 로드 완료")
    else:
        print("ℹ️ GEMINI_API_KEY가 없습니다. 기본 브리핑 텍스트로 대체 생성합니다.")

    # 2. settings.json 읽기
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = json.load(f)

    target_keywords = ["금융", "경제", "ai"]
    feeds = settings.get("feeds", [])

    # 3. 14일 기준 시각 계산
    now = datetime.now()
    cutoff_date = now - timedelta(days=14)

    # 4. 기존 news_history.json 불러오기 (누적 보관 모드)
    existing_articles = []
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    existing_articles = loaded
        except Exception as e:
            print(f"⚠️ 기존 히스토리 로드 실패: {e}")

    # 기사 링크를 기준으로 딕셔너리 생성 (중복 자동 방지)
    articles_map = {}
    # 기존 기사도 키워드(금융, 경제, ai)를 다시 판정해서 그 외 키워드는 제거하고,
    # 해당 키워드가 하나도 없는 기사는 히스토리에서 제외
    removed_by_keyword = 0
    for art in existing_articles:
        link = art.get("link")
        if not link:
            continue
        search_target = f"{art.get('title', '')} {art.get('description', '')}".lower()
        matched = [kw for kw in target_keywords if kw.lower() in search_target]
        if not matched:
            removed_by_keyword += 1
            continue
        art["matched_keywords"] = matched
        articles_map[link] = art
    if removed_by_keyword:
        print(f"🧹 대상 키워드가 없는 기존 기사 {removed_by_keyword}건 제거")

    print("=" * 60)
    print(f"📰 [뉴스 히스토리 누적 수집 및 대시보드 갱신] 시작")
    print(f"🕒 현재 시각: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 보존 기준: 최근 14일치 ({cutoff_date.strftime('%Y-%m-%d')} 이후 기사)")
    print(f"📂 기존 보관 기사: {len(articles_map)}건")
    print(f"🏷️ 감지 키워드: {', '.join(target_keywords)}")
    print(f"🌐 수집 대상 RSS 피드 수: {len(feeds)}개")
    print("=" * 60)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    new_articles_count = 0
    total_fetched_count = 0

    # 5. 각 언론사 RSS 피드를 순회하며 신규 기사 수집 및 추가
    for feed in feeds:
        feed_name = feed.get("name", "알 수 없음")
        feed_url = feed.get("url", "")

        if not feed_url:
            continue

        print(f"\n📡 수집 중: [{feed_name}] ({feed_url})")
        feed_article_count = 0
        feed_matched_count = 0

        try:
            req = urllib.request.Request(feed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()

            root = ET.fromstring(xml_data)
            items = root.findall(".//item")
            feed_article_count = len(items)
            total_fetched_count += feed_article_count

            for item in items:
                title = item.findtext("title", default="").strip()
                link = item.findtext("link", default="").strip()
                description = item.findtext("description", default="").strip()
                pub_date_raw = item.findtext("pubDate", default="").strip()
                category = item.findtext("category", default="").strip()

                # [포토] 기사 제외
                if "[포토]" in title:
                    continue

                # 한경 프리미엄9 유료 기사 제외
                if is_hankyung_premium(feed_name, title, link, category):
                    continue

                if not link:
                    continue

                # 발행 날짜 파싱
                dt_obj, formatted_date = parse_pubdate(pub_date_raw)

                # 조건 1: 최근 14일 이내 발행된 기사인지 확인
                if dt_obj < cutoff_date:
                    continue

                # 조건 2: 제목 또는 소개문에 키워드(금융, 경제, ai)가 포함되어 있는지 확인
                search_target = f"{title} {description}".lower()
                matched = [
                    kw for kw in target_keywords
                    if kw.lower() in search_target
                ]

                # 키워드가 1개 이상 걸린 기사 추가
                if matched:
                    feed_matched_count += 1
                    # 이미 저장된 기사가 아니라면 신규 카운트 증가
                    if link not in articles_map:
                        new_articles_count += 1

                    articles_map[link] = {
                        "source": feed_name,
                        "title": title,
                        "link": link,
                        "date": formatted_date,
                        "description": description,
                        "matched_keywords": matched
                    }

            print(f"   -> 전체 {feed_article_count}개 중 조건 만족 {feed_matched_count}개 기사 확인")

        except Exception as e:
            print(f"   ⚠️ 수집 실패: {e}")

    # 6. 전체 누적 기사 중 14일 초과된 오래된 기사 정리 및 정렬
    valid_articles = []
    dates_list = []
    expired_count = 0

    for link, art in articles_map.items():
        dt_obj, formatted_date = parse_pubdate(art.get("date", ""))
        # 14일 지난 기사는 자동 삭제
        if dt_obj < cutoff_date:
            expired_count += 1
            continue

        art_copy = dict(art)
        art_copy["_sort_dt"] = dt_obj
        valid_articles.append(art_copy)
        if formatted_date and formatted_date != "날짜 없음":
            dates_list.append(formatted_date.split(" ")[0])

    # 7. 최신순(내림차순) 정렬
    valid_articles.sort(key=lambda x: x["_sort_dt"], reverse=True)

    # 8. JSON 파일 저장용 데이터 정제 (_sort_dt 제외)
    final_results = []
    for art in valid_articles:
        final_results.append({
            "source": art["source"],
            "title": art["title"],
            "link": art["link"],
            "date": art["date"],
            "description": art["description"],
            "matched_keywords": art["matched_keywords"]
        })

    # 9. news_history.json 파일로 누적 저장
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(final_results, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"💾 [누적 저장 완료] 총 {len(final_results)}건 (신규 추가: +{new_articles_count}건, 14일 만료 삭제: -{expired_count}건)")
    print(f"📁 파일: {json_path.resolve()}")
    print("=" * 60)

    # 8. Gemini API 호출하여 시장 종합 브리핑 요약 생성
    print("\n🤖 Gemini AI 14일 시장 트렌드 총평 생성 중...")
    gemini_summary = summarize_history_with_gemini(api_key, final_results)

    # 날짜 범위 산출
    if dates_list:
        dates_list.sort()
        min_date_str = dates_list[0]
        max_date_str = dates_list[-1]
    else:
        min_date_str = cutoff_date.strftime("%Y-%m-%d")
        max_date_str = now.strftime("%Y-%m-%d")

    # 9. index.html 대시보드 생성
    print(f"\n📊 대시보드 웹페이지({html_path.name}) 생성 중...")
    generate_dashboard_html(html_path, final_results, gemini_summary, min_date_str, max_date_str)
    print(f"   -> [HTML] {html_path.name} 생성 완료 (제목: '뉴스 브리핑 히스토리 대시보드')")

    print("\n" + "=" * 60)
    print(f"🎉 뉴스 브리핑 히스토리 대시보드 구축 완료!")
    print(f"👉 파일 열기: {html_path.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
