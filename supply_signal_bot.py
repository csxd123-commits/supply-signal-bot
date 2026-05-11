"""
공급망 시그널 텔레그램 봇 v5
"""

import requests
import schedule
import time
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────
GEMINI_API_KEY   = "AIzaSyCg3ylWOfW3aPM-gCOia5ioX7zCZS9eBfc"
TELEGRAM_TOKEN   = "8796878101:AAHRbfnsrUZKhX0h4ZneFZcmIV4tzbu_NKo"
TELEGRAM_CHAT_ID = "1178221090"
RUN_TIME         = "08:00"
MAX_RESULTS      = 20
DAYS_LIMIT       = 3
# ──────────────────────────────────────────────────────────────────────────────

# 키워드 하나씩 단순하게 — 검색결과 많아짐
GOOGLE_NEWS_QUERIES = [
    "쇼티지",
    "공급부족",
    "병목 공급망",
    "리드타임",
    "납기지연",
    "수급불안",
    "재고부족",
    "생산차질",
    "공급망 위기",
    "증설 공장",
    "shortage",
    "bottleneck",
]

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
)

SEV_EMOJI = {"H": "🔴", "M": "🟡", "L": "🟢"}
SEV_LABEL = {"H": "고위험", "M": "중위험", "L": "저위험"}


def parse_pub_date(date_str: str):
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        return None


def fetch_google_news(query: str) -> list:
    url = (
        "https://news.google.com/rss/search"
        f"?q={requests.utils.quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_LIMIT)
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        for item in root.iter("item"):
            title   = item.findtext("title", "").strip()
            link    = item.findtext("link", "").strip()
            pub_raw = item.findtext("pubDate", "").strip()
            source_el = item.find("source")
            source  = source_el.text if source_el is not None else "Google News"

            pub_dt = parse_pub_date(pub_raw)
            if pub_dt and pub_dt < cutoff:
                continue

            pub_str = pub_dt.astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M") if pub_dt else ""
            items.append({"title": title, "link": link, "source": source, "pub": pub_str, "pub_dt": pub_dt})

        items.sort(key=lambda x: x["pub_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items[:10]
    except Exception as e:
        print(f"  오류 ({query}): {e}")
        return []


def classify_with_gemini(articles: list) -> list:
    if not articles:
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    article_list = "\n".join([
        f"{i+1}. [{a['source']}] {a['title']}"
        for i, a in enumerate(articles)
    ])
    prompt = f"""다음 뉴스 제목들을 분석해서 공급망 이슈(병목/쇼티지/리드타임/증설 등) 관련성을 판단하고 JSON으로만 응답해줘.
공급망과 무관한 기사는 제외.

{article_list}

[{{
  "index": 번호,
  "summary": "2문장 한국어 요약. 어떤 품목/산업이 왜 영향받는지",
  "keyword": "병목|쇼티지|리드타임|증설|공급부족|납기지연|생산차질|재고부족|수급불안 중 1개",
  "severity": "H|M|L",
  "impact": "영향받는 산업/기업 한 줄"
}}]

H=즉각적 공급중단/가격폭등, M=수급불안/상승압력, L=잠재리스크
JSON만 반환. 오늘: {today}"""

    try:
        resp = requests.post(
            GEMINI_URL,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 3000},
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        s, e = text.find("["), text.rfind("]")
        if s < 0 or e < 0:
            return _fallback(articles)
        classified = json.loads(text[s:e+1])
        result = []
        for c in classified:
            idx = c.get("index", 0) - 1
            if 0 <= idx < len(articles):
                a = articles[idx]
                result.append({
                    "title":    a["title"],
                    "summary":  c.get("summary", ""),
                    "keyword":  c.get("keyword", ""),
                    "severity": c.get("severity", "L"),
                    "impact":   c.get("impact", ""),
                    "source":   a["source"],
                    "pub":      a.get("pub", ""),
                    "url":      a["link"],
                })
        return result
    except Exception as ex:
        print(f"  Gemini 오류: {ex}")
        return _fallback(articles)


def _fallback(articles: list) -> list:
    return [{
        "title": a["title"], "summary": "", "keyword": "공급망",
        "severity": "M", "impact": "", "source": a["source"],
        "pub": a.get("pub", ""), "url": a["link"]
    } for a in articles]


def collect_all_news() -> list:
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 수집 시작 ({DAYS_LIMIT}일 이내, {len(GOOGLE_NEWS_QUERIES)}개 키워드)")
    all_articles = []
    seen = set()

    for q in GOOGLE_NEWS_QUERIES:
        items = fetch_google_news(q)
        new_cnt = 0
        for a in items:
            key = re.sub(r"\s+", "", a["title"])[:25]
            if key not in seen:
                seen.add(key)
                all_articles.append(a)
                new_cnt += 1
        print(f"  [{q}] {len(items)}건 수집 / 신규 {new_cnt}건")
        time.sleep(0.5)

    print(f"  총 {len(all_articles)}건 → Gemini 분류 중...")
    classified = classify_with_gemini(all_articles[:50])
    order = {"H": 0, "M": 1, "L": 2}
    classified.sort(key=lambda x: order.get(x.get("severity", "L"), 2))
    print(f"  최종 {len(classified)}건")
    return classified[:MAX_RESULTS]


def escape_md(text: str) -> str:
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def build_message(items: list) -> str:
    today = datetime.now().strftime("%Y년 %m월 %d일")
    h = sum(1 for i in items if i.get("severity") == "H")
    m = sum(1 for i in items if i.get("severity") == "M")
    l = sum(1 for i in items if i.get("severity") == "L")
    lines = [
        "📡 *공급망 시그널 리포트*",
        f"_{escape_md(today)}_ \\({DAYS_LIMIT}일 이내\\)",
        "",
        f"총 *{len(items)}건* \\| 🔴{h} 🟡{m} 🟢{l}",
        escape_md("─" * 24),
    ]
    for item in items:
        sev   = item.get("severity", "L")
        emoji = SEV_EMOJI.get(sev, "⚪")
        label = SEV_LABEL.get(sev, "")
        kw    = escape_md(item.get("keyword", ""))
        title = escape_md(item.get("title", ""))
        summ  = escape_md(item.get("summary", ""))
        imp   = escape_md(item.get("impact", ""))
        src   = escape_md(item.get("source", ""))
        pub   = escape_md(item.get("pub", ""))
        url   = item.get("url", "")
        lines.append(f"\n{emoji} *\\[{label}\\]* `{kw}`")
        lines.append(f"[{title}]({url})" if url else f"*{title}*")
        if summ:
            lines.append(summ)
        if imp:
            lines.append(f"↳ _{imp}_")
        foot = []
        if src: foot.append(src)
        if pub: foot.append(pub)
        if foot:
            lines.append(escape_md(" · ".join(foot)))
    lines += ["", escape_md("─" * 24), "_supply\\_signal\\_bot_"]
    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    ok = True
    for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
        resp = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                  "parse_mode": "MarkdownV2", "disable_web_page_preview": True},
            timeout=15,
        )
        if not resp.ok:
            print(f"  텔레그램 전송 실패: {resp.text}")
            ok = False
    return ok


def run_once():
    items = collect_all_news()
    if not items:
        print("  수집된 뉴스 없음")
        return
    ok = send_telegram(build_message(items))
    print(f"  텔레그램 전송 {'✅ 완료' if ok else '❌ 실패'}")


def run_scheduler():
    print(f"스케줄러 시작 — 매일 {RUN_TIME} 자동 실행 (Ctrl+C 종료)")
    schedule.every().day.at(RUN_TIME).do(run_once)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run_once() if args.once else run_scheduler()
