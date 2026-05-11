"""
공급망 시그널 텔레그램 봇 v11
"""

import requests
import schedule
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "8796878101:AAHRbfnsrUZKhX0h4ZneFZcmIV4tzbu_NKo"
TELEGRAM_CHAT_ID = "1178221090"
MAX_RESULTS      = 10
DAYS_LIMIT       = 3
# ──────────────────────────────────────────────────────────────────────────────

EXCLUDE_KEYWORDS = [
    "부동산", "아파트", "전세금", "월세", "집값", "양도세", "청약", "분양", "임대차",
    "공모주", "전세버스", "선거", "대선", "총선", "의원", "국회",
    "노사갈등", "노조", "파업", "임금협상", "단체교섭",
    "소통 창구", "對국민", "국민 소통",
]

HIGH_KEYWORDS = ["공급 중단", "생산 중단", "전면 중단", "폭등", "위기 심화", "급감", "대란"]
MID_KEYWORDS  = ["쇼티지", "공급부족", "공급 부족", "수급불안", "납기지연", "생산차질",
                 "재고부족", "병목", "리드타임", "shortage", "bottleneck", "수급 불안", "급등"]
LOW_KEYWORDS  = ["증설", "공급망", "수급", "조달", "재고", "물량"]

GOOGLE_NEWS_QUERIES = [
    "쇼티지", "공급부족", "병목 공급망", "리드타임",
    "납기지연", "수급불안", "재고부족", "생산차질",
    "공급망 위기", "증설 공장", "shortage", "bottleneck",
]

SEV_EMOJI = {"H": "🔴", "M": "🟡", "L": "🟢"}
SEV_LABEL = {"H": "고위험", "M": "중위험", "L": "저위험"}


def parse_pub_date(date_str):
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        return None


def is_excluded(title):
    for kw in EXCLUDE_KEYWORDS:
        if kw in title:
            return True
    return False


def classify_severity(title):
    for kw in HIGH_KEYWORDS:
        if kw in title:
            return "H"
    for kw in MID_KEYWORDS:
        if kw in title:
            return "M"
    return "L"


def extract_keyword(title):
    kw_map = {
        "쇼티지": "쇼티지", "shortage": "쇼티지",
        "공급부족": "공급부족", "공급 부족": "공급부족",
        "병목": "병목", "bottleneck": "병목",
        "리드타임": "리드타임",
        "납기지연": "납기지연", "납기 지연": "납기지연",
        "생산차질": "생산차질", "생산 차질": "생산차질",
        "재고부족": "재고부족", "재고 부족": "재고부족",
        "수급불안": "수급불안", "수급 불안": "수급불안",
        "증설": "증설",
    }
    for k, v in kw_map.items():
        if k in title:
            return v
    return "공급망"


def extract_entities(title):
    """5글자 이상 연속 한글/영문 단어 추출 — 회사명·제품명 해당"""
    # 한글 5글자 이상
    korean = set(re.findall(r'[가-힣]{5,}', title))
    # 영문 5글자 이상 (대소문자 무관)
    english = set(w.lower() for w in re.findall(r'[A-Za-z]{5,}', title))
    return korean | english


def is_duplicate(new_title, seen_titles):
    """
    두 가지 기준 중 하나라도 해당하면 중복:
    1. 핵심 단어 45% 이상 겹침
    2. 5글자 이상 고유명사가 1개 이상 겹침
    """
    new_words = set(w for w in re.sub(r'[^\w]', ' ', new_title).split() if len(w) >= 2)
    new_entities = extract_entities(new_title)

    for old_title in seen_titles:
        old_words = set(w for w in re.sub(r'[^\w]', ' ', old_title).split() if len(w) >= 2)
        old_entities = extract_entities(old_title)

        # 고유명사 겹침 체크
        if new_entities & old_entities:
            return True

        # 단어 비율 체크
        if new_words and old_words:
            overlap = len(new_words & old_words) / min(len(new_words), len(old_words))
            if overlap >= 0.45:
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
            items.append({
                "title": title, "link": link, "source": source,
                "pub": pub_str, "pub_dt": pub_dt,
                "severity": classify_severity(title),
                "keyword": extract_keyword(title),
            })
        items.sort(key=lambda x: x["pub_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items[:15]
    except Exception as e:
        print(f"  오류 ({query}): {e}")
        return []


def collect_all_news():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 수집 시작")
    all_articles = []
    seen_titles = []

    for q in GOOGLE_NEWS_QUERIES:
        items = fetch_google_news(q)
        new_cnt = 0
        for a in items:
            if is_duplicate(a["title"], seen_titles):
                continue
            seen_titles.append(a["title"])
            all_articles.append(a)
            new_cnt += 1
        print(f"  [{q}] 신규 {new_cnt}건")
        time.sleep(0.5)

    # 최신순 정렬
    all_articles.sort(key=lambda x: x.get("pub_dt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    result = all_articles[:MAX_RESULTS]
    print(f"  최종 {len(result)}건")
    return result


def html_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message(items):
    today = datetime.now().strftime("%Y년 %m월 %d일")
    h = sum(1 for i in items if i.get("severity") == "H")
    m = sum(1 for i in items if i.get("severity") == "M")
    l = sum(1 for i in items if i.get("severity") == "L")

    lines = [
        "📡 <b>공급망 시그널 리포트</b>",
        f"{today} ({DAYS_LIMIT}일 이내) · 총 {len(items)}건",
        "─" * 22,
    ]
    for item in items:
        kw    = html_escape(item.get("keyword", ""))
        title = html_escape(item.get("title", ""))
        src   = html_escape(item.get("source", ""))
        pub   = item.get("pub", "")
        url   = item.get("link", "")

        lines.append(f"\n🔹 #{kw}")
        if url:
            lines.append(f'<a href="{url}">{title}</a>')
        else:
            lines.append(f"<b>{title}</b>")
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
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        )
        if not resp.ok:
            print(f"  전송 실패: {resp.text}")
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
    schedule.every().day.at("08:00").do(run_once)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run_once() if args.once else run_scheduler()
