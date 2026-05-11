"""
공급망 시그널 텔레그램 봇 v7
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
MAX_RESULTS      = 15
DAYS_LIMIT       = 3
# ──────────────────────────────────────────────────────────────────────────────

# 이 단어가 제목에 있으면 제외 (부동산·IPO 등 완전 무관)
EXCLUDE_KEYWORDS = [
    "부동산", "아파트", "전세금", "월세", "집값", "양도세", "청약", "분양", "임대차",
    "공모주", "IPO", "상장", "전세버스", "택시업", "버스업",
    "선거", "대선", "총선", "정치", "의원", "국회",
]

GOOGLE_NEWS_QUERIES = [
    "쇼티지", "공급부족", "병목 공급망", "리드타임",
    "납기지연", "수급불안", "재고부족", "생산차질",
    "공급망 위기", "증설 공장", "shortage", "bottleneck",
]

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
)

SEV_EMOJI = {"H": "🔴", "M": "🟡", "L": "🟢"}
SEV_LABEL = {"H": "고위험", "M": "중위험", "L": "저위험"}


def parse_pub_date(date_str):
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        return None


def is_excluded(title):
    """무관 기사 필터링"""
    for kw in EXCLUDE_KEYWORDS:
        if kw in title:
            return True
    return False


def fetch_google_news(query):
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
            pub_dt  = parse_pub_date(pub_raw)
            if pub_dt and pub_dt < cutoff:
                continue
            if is_excluded(title):
                continue
            kst = timezone(timedelta(hours=9))
            pub_str = pub_dt.astimezone(kst).strftime("%m/%d %H:%M") if pub_dt else ""
            items.append({"title": title, "link": link, "source": source, "pub": pub_str, "pub_dt": pub_dt})
        items.sort(key=lambda x: x["pub_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items[:10]
    except Exception as e:
        print(f"  오류 ({query}): {e}")
        return []


def similar(a, b):
    """제목 유사도 체크 — 핵심 단어 60% 이상 겹치면 중복"""
    wa = set(re.sub(r"[^\w]", " ", a).split())
    wb = set(re.sub(r"[^\w]", " ", b).split())
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / min(len(wa), len(wb))
    return overlap > 0.6


def classify_with_gemini(articles):
    if not articles:
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    article_list = "\n".join([
        f"{i+1}. [{a['source']}] {a['title']}"
        for i, a in enumerate(articles)
    ])
    prompt = f"""다음 뉴스 제목들의 공급망/산업 이슈 심각도를 분류해줘. JSON 배열로만 응답.

{article_list}

[{{"index": 번호, "summary": "2문장 요약", "keyword": "병목|쇼티지|리드타임|증설|공급부족|납기지연|생산차질|재고부족|수급불안 중 1개", "severity": "H|M|L", "impact": "영향 산업 한 줄"}}]

H=즉각적공급중단/가격폭등, M=수급불안/상승압력, L=잠재리스크
공급망·산업·원자재와 무관한 기사는 제외. JSON만. 오늘:{today}"""

    try:
        resp = requests.post(
            GEMINI_URL,
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.1, "maxOutputTokens": 3000}},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = (data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", ""))
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


def _fallback(articles):
    return [{"title": a["title"], "summary": "", "keyword": "공급망",
             "severity": "M", "impact": "", "source": a["source"],
             "pub": a.get("pub", ""), "url": a["link"]} for a in articles]


def collect_all_news():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 수집 시작")
    all_articles = []
    seen_titles = []

    for q in GOOGLE_NEWS_QUERIES:
        items = fetch_google_news(q)
        new_cnt = 0
        for a in items:
            # 유사 제목 중복 제거
            if any(similar(a["title"], t) for t in seen_titles):
                continue
            seen_titles.append(a["title"])
            all_articles.append(a)
            new_cnt += 1
        print(f"  [{q}] {len(items)}건 / 신규 {new_cnt}건")
        time.sleep(0.5)

    print(f"  총 {len(all_articles)}건 → Gemini 분류 중...")
    classified = classify_with_gemini(all_articles[:50])
    order = {"H": 0, "M": 1, "L": 2}
    classified.sort(key=lambda x: order.get(x.get("severity", "L"), 2))
    print(f"  최종 {len(classified)}건")
    return classified[:MAX_RESULTS]


def html_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message(items):
    """HTML 모드 — 제목 클릭하면 기사로 이동"""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    h = sum(1 for i in items if i.get("severity") == "H")
    m = sum(1 for i in items if i.get("severity") == "M")
    l = sum(1 for i in items if i.get("severity") == "L")

    lines = [
        "📡 <b>공급망 시그널 리포트</b>",
        f"{today} ({DAYS_LIMIT}일 이내)",
        f"총 {len(items)}건 | 🔴{h} 🟡{m} 🟢{l}",
        "─" * 22,
    ]
    for item in items:
        sev   = item.get("severity", "L")
        emoji = SEV_EMOJI.get(sev, "⚪")
        label = SEV_LABEL.get(sev, "")
        kw    = html_escape(item.get("keyword", ""))
        title = html_escape(item.get("title", ""))
        summ  = html_escape(item.get("summary", ""))
        imp   = html_escape(item.get("impact", ""))
        src   = html_escape(item.get("source", ""))
        pub   = item.get("pub", "")
        url   = item.get("url", "")

        lines.append(f"\n{emoji} <b>[{label}]</b> #{kw}")
        # 제목을 클릭 가능한 링크로
        if url:
            lines.append(f'<a href="{url}">{title}</a>')
        else:
            lines.append(f"<b>{title}</b>")
        if summ:
            lines.append(summ)
        if imp:
            lines.append(f"↳ {imp}")
        foot = []
        if src: foot.append(src)
        if pub: foot.append(pub)
        if foot:
            lines.append(" · ".join(foot))

    lines += ["", "─" * 22, "supply_signal_bot"]
    return "\n".join(lines)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    ok = True
    for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
        resp = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                  "parse_mode": "HTML",
                  "disable_web_page_preview": True},
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
    print(f"  텔레그램 전송 {'완료' if ok else '실패'}")


def run_scheduler():
    print(f"스케줄러 시작 — 매일 {RUN_TIME} 자동 실행")
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
