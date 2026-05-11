"""
공급망 시그널 텔레그램 봇 v19
- 한/영 통합 수집, 최신순 정렬
- 외신 쿼리 en-US 적용
- KST 시간 표시
- 고유명사 기반 중복 제거
- 24시간 이내 이전 기사 중복 제거 (제목 기준)
"""

import requests
import schedule
import time
import re
import xml.etree.ElementTree as ET
import json
import os
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "8796878101:AAHRbfnsrUZKhX0h4ZneFZcmIV4tzbu_NKo"
TELEGRAM_CHAT_ID = "-1003984467582"
MAX_RESULTS      = 12
DAYS_LIMIT       = 3
SEEN_FILE        = "seen_urls.json"
# ──────────────────────────────────────────────────────────────────────────────

EXCLUDE_KEYWORDS = [
    # 부동산
    "부동산", "아파트", "전세금", "월세", "집값", "양도세", "청약", "분양", "임대차",
    # 금융/공모
    "공모주", "IPO", "상장",
    # 정치
    "전세버스", "선거", "대선", "총선", "의원", "국회", "정당", "여당", "야당",
    # 노사
    "노사갈등", "노조", "파업", "임금협상", "단체교섭",
    # 기타 무관
    "소통 창구", "對국민", "국민 소통",
    # 식음료·생활용품 (콘서트·티켓 등)
    "콘서트", "공연", "티켓", "연예", "아이돌", "배우", "드라마", "영화",
    "버터", "빵", "과자", "음식", "식당", "카페", "커피", "라면",
    "기차표", "항공권", "숙박", "호텔", "여행",
    "두꾼", "떡", "쭈꾸미", "삼겹살", "치킨",
]

# 한국어 쿼리
KO_QUERIES = [
    ("쇼티지",              "ko", "KR", "KR:ko"),
    ("공급부족",             "ko", "KR", "KR:ko"),
    ("병목 공급망",          "ko", "KR", "KR:ko"),
    ("리드타임",             "ko", "KR", "KR:ko"),
    ("납기지연",             "ko", "KR", "KR:ko"),
    ("수급불안",             "ko", "KR", "KR:ko"),
    ("재고부족",             "ko", "KR", "KR:ko"),
    ("생산차질",             "ko", "KR", "KR:ko"),
    ("공급망 위기",          "ko", "KR", "KR:ko"),
    ("증설 공장",            "ko", "KR", "KR:ko"),
    ("광풍 반도체",          "ko", "KR", "KR:ko"),
    ("광풍 배터리",          "ko", "KR", "KR:ko"),
    ("광풍 원자재",          "ko", "KR", "KR:ko"),
    ("품귀 원자재",          "ko", "KR", "KR:ko"),
    ("품귀 반도체",          "ko", "KR", "KR:ko"),
    ("수요 폭발 공급",       "ko", "KR", "KR:ko"),
    ("수요 급증 생산",       "ko", "KR", "KR:ko"),
    ("물량 부족 산업",       "ko", "KR", "KR:ko"),
]

# 영어 쿼리 (미국/대만/중국 외신)
EN_QUERIES = [
    ("semiconductor shortage",          "en-US", "US", "US:en"),
    ("chip shortage supply",            "en-US", "US", "US:en"),
    ("supply chain bottleneck",         "en-US", "US", "US:en"),
    ("raw material shortage",           "en-US", "US", "US:en"),
    ("TSMC supply constraint",          "en-US", "US", "US:en"),
    ("Taiwan chip supply",              "en-US", "US", "US:en"),
    ("China supply chain shortage",     "en-US", "US", "US:en"),
    ("lead time manufacturing",         "en-US", "US", "US:en"),
    ("capacity expansion factory",      "en-US", "US", "US:en"),
    ("supply disruption industry",      "en-US", "US", "US:en"),
]

ALL_QUERIES = KO_QUERIES + EN_QUERIES


def load_seen_titles():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            data = json.load(f)
        # 24시간 이내 것만 유지
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        return {k: v for k, v in data.items()
                if datetime.fromisoformat(v) > cutoff}
    return {}


def save_seen_titles(seen: dict):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f)


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


def extract_keyword(title):
    kw_map = {
        "쇼티지": "쇼티지", "shortage": "shortage",
        "공급부족": "공급부족", "공급 부족": "공급부족",
        "병목": "병목", "bottleneck": "bottleneck",
        "리드타임": "리드타임", "lead time": "lead time",
        "납기지연": "납기지연",
        "생산차질": "생산차질", "disruption": "disruption",
        "재고부족": "재고부족", "inventory": "inventory",
        "수급불안": "수급불안",
        "증설": "증설", "capacity": "capacity",
        "chip": "chip", "semiconductor": "반도체",
        "TSMC": "TSMC", "constraint": "공급제한",
        "광풍": "광풍", "품귀": "품귀",
        "수요 폭발": "수요폭발", "수요 급증": "수요급증",
        "물량 부족": "물량부족",
    }
    for k, v in kw_map.items():
        if k.lower() in title.lower():
            return v
    return "공급망"


# 일반 공급망 용어 — 고유명사 판단에서 제외
COMMON_WORDS = {
    "공급망", "해상공급망", "공급부족", "공급위기", "생산차질", "공급망위기",
    "수급불안", "재고부족", "납기지연", "병목현상", "쇼티지", "리드타임",
    "원자재", "반도체", "semiconductor", "shortage", "supply", "chain",
    "bottleneck", "capacity", "disruption", "inventory", "manufacturing",
}


def is_duplicate(new_title, seen_titles):
    def get_proper_nouns(title):
        korean  = set(re.findall(r'[가-힣]{4,}', title)) - COMMON_WORDS
        english = set(w.lower() for w in re.findall(r'[A-Za-z]{5,}', title)) - COMMON_WORDS
        return korean | english

    new_nouns = get_proper_nouns(new_title)
    new_words = set(w for w in re.sub(r'[^\w]', ' ', new_title).split() if len(w) >= 2)

    for old_title in seen_titles:
        old_nouns = get_proper_nouns(old_title)
        if new_nouns and old_nouns and (new_nouns & old_nouns):
            return True
        old_words = set(w for w in re.sub(r'[^\w]', ' ', old_title).split() if len(w) >= 2)
        if new_words and old_words:
            if len(new_words & old_words) / min(len(new_words), len(old_words)) >= 0.60:
                return True
    return False


def fetch_news(query, hl, gl, ceid):
    url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_LIMIT)
    region = "🇰🇷" if gl == "KR" else "🌐"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        for item in root.iter("item"):
            title     = item.findtext("title", "").strip()
            link      = item.findtext("link", "").strip()
            pub_raw   = item.findtext("pubDate", "").strip()
            source_el = item.find("source")
            source    = source_el.text if source_el is not None else ""
            pub_dt    = parse_pub_date(pub_raw)
            if pub_dt and pub_dt < cutoff:
                continue
            if is_excluded(title):
                continue
            kst     = timezone(timedelta(hours=9))
            pub_str = pub_dt.astimezone(kst).strftime("%m/%d %H:%M") if pub_dt else ""
            items.append({
                "title": title, "link": link, "source": source,
                "pub": pub_str, "pub_dt": pub_dt,
                "keyword": extract_keyword(title),
                "region": region,
            })
        items.sort(key=lambda x: x["pub_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items[:8]
    except Exception as e:
        print(f"  오류 ({query[:20]}): {e}")
        return []


def collect_all_news():
    kst_now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9)))
    print(f"\n[{kst_now.strftime('%Y-%m-%d %H:%M')} KST] 수집 시작")

    all_articles = []
    seen_titles  = []

    for q, hl, gl, ceid in ALL_QUERIES:
        for a in fetch_news(q, hl, gl, ceid):
            if is_duplicate(a["title"], seen_titles):
                continue
            seen_titles.append(a["title"])
            all_articles.append(a)
        time.sleep(0.3)

    all_articles.sort(key=lambda x: x.get("pub_dt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    result = all_articles[:MAX_RESULTS]

    kr = sum(1 for a in result if a["region"] == "🇰🇷")
    gl_cnt = len(result) - kr
    print(f"  최종 {len(result)}건 (🇰🇷{kr} 🌐{gl_cnt})")
    return result


def html_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message(items):
    kst_now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9)))
    today   = kst_now.strftime("%Y년 %m월 %d일 %H:%M")
    kr      = sum(1 for i in items if i.get("region") == "🇰🇷")
    gl      = len(items) - kr

    lines = [
        "📡 <b>공급망 시그널 리포트</b>",
        f"{today} KST · 🇰🇷{kr} 🌐{gl}",
        "─" * 22,
    ]
    for item in items:
        kw     = html_escape(item.get("keyword", ""))
        title  = html_escape(item.get("title", ""))
        src    = html_escape(item.get("source", ""))
        pub    = item.get("pub", "")
        url    = item.get("link", "")
        region = item.get("region", "")

        lines.append(f"\n{region} <b>#{kw}</b>")
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


def build_no_news_message():
    kst_now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9)))
    today   = kst_now.strftime("%Y년 %m월 %d일 %H:%M")
    return (
        "📡 <b>공급망 시그널 리포트</b>\n"
        f"{today} KST\n"
        "─" * 22 + "\n\n"
        "🔕 새 기사 없음\n"
        "이전 대비 신규 기사가 없습니다.\n\n"
        "─" * 22 + "\n"
        "supply_signal_bot"
    )


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    ok  = True
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
    seen_titles = load_seen_titles()  # 이전 24시간 제목 불러오기

    items = collect_all_news()

    if not items:
        print("  수집된 뉴스 없음")
        send_telegram(build_no_news_message())
        return

    # 이전에 보낸 기사 제목 기준으로 제외
    new_items = [a for a in items if a["title"] not in seen_titles]

    if not new_items:
        print("  새 기사 없음 (전부 중복)")
        send_telegram(build_no_news_message())
        return

    ok = send_telegram(build_message(new_items))

    if ok:
        now_str = datetime.now(timezone.utc).isoformat()
        for a in new_items:
            seen_titles[a["title"]] = now_str
        save_seen_titles(seen_titles)

    print(f"  텔레그램 전송 {'완료' if ok else '실패'} ({len(new_items)}건)")


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
