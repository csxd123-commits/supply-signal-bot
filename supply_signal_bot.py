"""
supply_signal_bot v24
변경사항:
  - [신규] 국내 RSS 대폭 확장: 조선비즈, 헤럴드경제, 서울경제, 파이낸셜뉴스,
           머니투데이, 뉴시스, SBS Biz, KBS, MBC, YTN, 비즈워치, 인베스트조선,
           케미컬뉴스, 철강금속신문, 반도체네트워크
  - [신규] 해외 RSS 대폭 확장: WSJ, FT, CNBC, Nikkei, FierceElectronics,
           SupplyChainDive, FreightWaves, Mining.com, SPGlobal, Argus
  - [유지] 배치 내 상호 중복 제거, seen.json 3중 저장
"""

import os
import re
import json
import time
import requests
import schedule
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

# ── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "여기에_토큰")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "여기에_챗ID")
SEEN_FILE        = "seen.json"
MAX_RESULTS      = 20
SEEN_TTL_HOURS   = 48
SIM_THRESHOLD    = 0.50
# ─────────────────────────────────────────────────────────────────────────────

# ── 키워드 → 카테고리 매핑 ────────────────────────────────────────────────────
KEYWORD_MAP = {
    "공급망": [
        "공급망", "공급 부족", "공급부족", "수급 불안", "수급불안",
        "수급 차질", "수급차질", "재고 부족", "재고부족",
        "재고 확보", "재고확보", "조달", "물량 부족", "물량부족",
        "supply chain", "shortage", "수입 차질", "통관 지연",
        "주사기", "의료 수급", "필수 의료", "원료 부족",
        "소재 부족", "부품 부족", "부품부족", "부품 수급",
    ],
    "수요급증": [
        "수요 급증", "수요급증", "수요 폭증", "수요폭증",
        "주문 폭주", "주문폭주", "수요 증가", "수요증가",
        "AI 수요", "데이터센터 수요", "구리 수요", "demand surge",
        "주문 증가", "판매 급증", "수주 급증", "수주급증",
        "전력 수요", "전력수요", "대기 수요",
    ],
    "증설": [
        "증설", "증산", "신규 공장", "공장 준공", "공장 착공",
        "capacity expansion", "생산능력", "capa", "팹 증설",
        "공장 건설", "라인 증설", "생산라인", "설비투자",
        "신규 라인", "생산 확대", "생산확대",
    ],
    "병목": [
        "병목", "bottleneck", "생산 차질", "생산차질",
        "리드타임", "lead time", "납기 지연", "납기지연",
        "공장 가동 중단", "라인 중단", "공급 지연", "공급지연",
        "출하 지연", "배송 지연",
    ],
    "관세리스크": [
        "관세", "tariff", "통상", "무역 규제", "무역규제",
        "수출 규제", "수출규제", "제재", "embargo",
        "미중 갈등", "지정학", "무역 분쟁", "무역분쟁",
        "상호관세", "보복관세", "추가관세", "관세 폭탄",
        "트럼프 관세", "미국 관세",
    ],
    "원자재": [
        "희토류", "리튬", "코발트", "니켈", "구리 가격",
        "원자재 가격", "원자재가격", "원재료", "소재 가격",
        "철광석", "알루미늄 가격", "반도체 소재",
        "rare earth", "critical mineral", "핵심 광물",
        "배터리 소재", "양극재", "음극재", "전해질",
    ],
}

# 카테고리 우선순위
CATEGORY_PRIORITY = ["병목", "공급망", "관세리스크", "원자재", "수요급증", "증설"]

# ── RSS 피드 ──────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # ── 국내 경제 종합 ──────────────────────────────────────────────────────────
    ("한국경제",    "https://www.hankyung.com/feed/economy",                        "🇰🇷"),
    ("한국경제",    "https://www.hankyung.com/feed/industry",                       "🇰🇷"),
    ("매일경제",    "https://www.mk.co.kr/rss/30200030/",                          "🇰🇷"),
    ("매일경제",    "https://www.mk.co.kr/rss/30100041/",                          "🇰🇷"),
    ("연합뉴스",    "https://www.yonhapnews.co.kr/rss/economy.xml",                 "🇰🇷"),
    ("연합뉴스",    "https://www.yonhapnews.co.kr/rss/industry.xml",                "🇰🇷"),
    ("이데일리",    "https://www.edaily.co.kr/rss/economy.xml",                     "🇰🇷"),
    ("전자신문",    "https://rss.etnews.com/Section901.xml",                       "🇰🇷"),
    ("뉴스1",      "https://www.news1.kr/rss/economy",                             "🇰🇷"),
    ("아주경제",    "https://www.ajunews.com/rss/economy.xml",                      "🇰🇷"),
    ("조선비즈",    "https://biz.chosun.com/site/data/rss/rss.xml",                 "🇰🇷"),
    ("헤럴드경제",  "https://biz.heraldcorp.com/rss/010000000000.xml",              "🇰🇷"),
    ("서울경제",    "https://www.sedaily.com/RSS/rss.xml",                          "🇰🇷"),
    ("파이낸셜뉴스", "https://www.fnnews.com/rss/fn_economy_economy.xml",           "🇰🇷"),
    ("머니투데이",  "https://news.mt.co.kr/mtview/rss/list.html?CATEGORY=A",       "🇰🇷"),
    ("뉴시스",     "https://www.newsis.com/RSS/economy.rss",                        "🇰🇷"),
    ("뉴시스",     "https://www.newsis.com/RSS/industry.rss",                       "🇰🇷"),
    ("SBS Biz",   "https://biz.sbs.co.kr/rss/economics.rss",                      "🇰🇷"),
    ("KBS",       "https://news.kbs.co.kr/rss/news-economy.xml",                   "🇰🇷"),
    ("YTN",       "https://www.ytn.co.kr/rss/0102.xml",                            "🇰🇷"),
    ("비즈워치",   "https://www.bizwatch.co.kr/rss/allArticle.rss",                 "🇰🇷"),
    ("인베스트조선","https://www.investchosun.com/site/data/rss/rss.xml",           "🇰🇷"),
    # ── 국내 산업/소재 전문 ─────────────────────────────────────────────────────
    ("전자부품연구원", "https://www.ebn.co.kr/rss/rss.html",                        "🇰🇷"),  # EBN 전자배터리뉴스
    ("케미컬뉴스",  "https://www.chemicalnews.co.kr/rss/allArticle.rss",            "🇰🇷"),
    ("반도체네트워크","https://www.seminet.co.kr/rss/rss_news.html",                "🇰🇷"),
    ("철강금속신문", "https://www.snmnews.com/rss/rss.xml",                         "🇰🇷"),
    ("에너지경제",  "https://www.ekn.kr/rss/rss.html",                              "🇰🇷"),
    ("물류신문",    "https://www.klnews.co.kr/rss/rss.html",                        "🇰🇷"),
    # ── 해외 종합 ───────────────────────────────────────────────────────────────
    ("Reuters",    "https://feeds.reuters.com/reuters/businessNews",                "🌐"),
    ("Reuters",    "https://feeds.reuters.com/reuters/industryNews",                "🌐"),
    ("Reuters",    "https://feeds.reuters.com/reuters/technologyNews",              "🌐"),
    ("Bloomberg",  "https://feeds.bloomberg.com/markets/news.rss",                 "🌐"),
    ("AP News",    "https://rsshub.app/apnews/topics/business-news",               "🌐"),
    ("FT",         "https://www.ft.com/rss/home/uk",                               "🌐"),
    ("WSJ",        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",                "🌐"),
    ("WSJ",        "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",              "🌐"),
    ("CNBC",       "https://www.cnbc.com/id/10001147/device/rss/rss.html",         "🌐"),  # Economy
    ("CNBC",       "https://www.cnbc.com/id/19854910/device/rss/rss.html",         "🌐"),  # Tech
    # ── 해외 공급망/산업 전문 ───────────────────────────────────────────────────
    ("SupplyChainDive", "https://www.supplychaindive.com/feeds/news/",             "🌐"),
    ("FreightWaves",    "https://www.freightwaves.com/news/feed",                  "🌐"),
    ("Nikkei Asia",     "https://asia.nikkei.com/rss/feed/nar",                    "🌐"),
    ("SPGlobal",        "https://www.spglobal.com/commodityinsights/en/rss-feed/oil-energy", "🌐"),
    ("Mining.com",      "https://www.mining.com/feed/",                            "🌐"),
    ("Argus Media",     "https://www.argusmedia.com/en/rss-feeds",                 "🌐"),
    ("FierceElectronics","https://www.fierceelectronics.com/rss/xml",              "🌐"),
    ("EE Times",        "https://www.eetimes.com/feed/",                           "🌐"),
    ("TechCrunch",      "https://techcrunch.com/feed/",                            "🌐"),
    ("The Loadstar",    "https://theloadstar.com/feed/",                           "🌐"),  # 물류/해운
]


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    return re.sub(r"[\s\W]+", "", title).lower()


def title_fingerprint(title: str) -> str:
    numbers = "".join(re.findall(r"\d+", title))
    words   = re.sub(r"[\s\W]+", "", title)[:10]
    return numbers + words


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def get_category(title: str, desc: str = "") -> str:
    text = (title + " " + desc).lower()
    for cat in CATEGORY_PRIORITY:
        for kw in KEYWORD_MAP.get(cat, []):
            if kw.lower() in text:
                return cat
    return "공급망"


# ── seen.json ─────────────────────────────────────────────────────────────────

def load_seen() -> dict:
    if not os.path.exists(SEEN_FILE):
        return {}
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_seen(seen: dict):
    cutoff  = (datetime.now(timezone.utc) - timedelta(hours=SEEN_TTL_HOURS)).isoformat()
    cleaned = {k: v for k, v in seen.items() if v >= cutoff}
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)


def is_seen_duplicate(item: dict, seen: dict) -> bool:
    url      = item.get("link", "")
    norm_new = normalize_title(item.get("title", ""))
    fp_new   = title_fingerprint(item.get("title", ""))

    if url in seen:
        return True
    if fp_new and fp_new in seen:
        return True

    for key in seen:
        if key.startswith("http"):
            continue
        if re.match(r"^\d", key) and len(key) < 20:
            continue
        if SequenceMatcher(None, norm_new, key).ratio() >= SIM_THRESHOLD:
            return True

    return False


# ── 배치 내 상호 중복 제거 ────────────────────────────────────────────────────

def dedupe_within_batch(items: list) -> list:
    result      = []
    seen_urls   = set()
    seen_fps    = set()
    seen_titles = []

    for item in items:
        url  = item.get("link", "")
        norm = normalize_title(item.get("title", ""))
        fp   = title_fingerprint(item.get("title", ""))

        if url in seen_urls:
            print(f"  [배치중복-URL] {item['title'][:40]}")
            continue
        if fp and fp in seen_fps:
            print(f"  [배치중복-지문] {item['title'][:40]}")
            continue

        is_dup = False
        for prev in seen_titles:
            if SequenceMatcher(None, norm, prev).ratio() >= SIM_THRESHOLD:
                is_dup = True
                print(f"  [배치중복-유사도] {item['title'][:40]}")
                break

        if is_dup:
            continue

        result.append(item)
        seen_urls.add(url)
        if fp:
            seen_fps.add(fp)
        seen_titles.append(norm)

    return result


# ── RSS 수집 ──────────────────────────────────────────────────────────────────

def fetch_rss(source: str, url: str, region: str) -> list:
    try:
        resp = requests.get(
            url, timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (supply_signal_bot/v23)"}
        )
        resp.raise_for_status()
        root  = ET.fromstring(resp.content)
        ns    = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        result = []
        for it in items:
            def _t(tag):
                el = it.find(tag)
                return (el.text or "").strip() if el is not None else ""

            title = _t("title")
            link  = _t("link") or _t("guid")
            desc  = re.sub(r"<[^>]+>", "", _t("description") or _t("summary"))
            pub   = _t("pubDate") or _t("published") or _t("updated")

            pub_display = ""
            for fmt in [
                "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S GMT",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%SZ",
            ]:
                try:
                    dt = datetime.strptime(pub.strip(), fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    kst = dt.astimezone(timezone(timedelta(hours=9)))
                    pub_display = kst.strftime("%m/%d %H:%M")
                    break
                except Exception:
                    pass

            if not title or not link:
                continue

            result.append({
                "title":  title,
                "link":   link,
                "desc":   desc,
                "source": source,
                "pub":    pub_display,
                "region": region,
            })
        return result
    except Exception as e:
        print(f"  RSS 오류 [{source}]: {e}")
        return []


def filter_by_keywords(articles: list) -> list:
    all_kws = [kw for kws in KEYWORD_MAP.values() for kw in kws]
    result  = []
    for a in articles:
        text = (a["title"] + " " + a.get("desc", "")).lower()
        if any(kw.lower() in text for kw in all_kws):
            a["keyword"] = get_category(a["title"], a.get("desc", ""))
            result.append(a)
    return result


def collect_all_news() -> list:
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 수집 시작")

    raw = []
    for source, url, region in RSS_FEEDS:
        items = fetch_rss(source, url, region)
        print(f"  {source}: {len(items)}건")
        raw.extend(items)

    filtered = filter_by_keywords(raw)
    print(f"  키워드 매칭: {len(filtered)}건")

    deduped = dedupe_within_batch(filtered)
    print(f"  배치 중복 제거 후: {len(deduped)}건")

    deduped.sort(key=lambda x: x.get("pub", "") or "", reverse=True)

    result = deduped[:MAX_RESULTS]
    kr = sum(1 for a in result if a["region"] == "🇰🇷")
    gl = len(result) - kr
    print(f"  최종 {len(result)}건 (🇰🇷{kr} 🌐{gl})")
    return result


# ── 메시지 빌드 ───────────────────────────────────────────────────────────────

def build_message(items: list) -> str:
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


def build_no_news_message() -> str:
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


# ── 텔레그램 전송 ─────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    ok  = True
    for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
        resp = requests.post(
            url,
            json={
                "chat_id":                  TELEGRAM_CHAT_ID,
                "text":                     chunk,
                "parse_mode":               "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if not resp.ok:
            print(f"  전송 실패: {resp.text}")
            ok = False
    return ok


# ── 메인 ─────────────────────────────────────────────────────────────────────

def run_once():
    seen = load_seen()

    items = collect_all_news()
    if not items:
        print("  수집된 뉴스 없음")
        return

    new_items = [a for a in items if not is_seen_duplicate(a, seen)]
    print(f"  seen 중복 제거 후: {len(new_items)}건")

    if not new_items:
        print("  새 기사 없음 (전송 생략)")
        return

    ok = send_telegram(build_message(new_items))

    if ok:
        now_str = datetime.now(timezone.utc).isoformat()
        for a in new_items:
            seen[a["link"]]                   = now_str
            seen[normalize_title(a["title"])] = now_str
            fp = title_fingerprint(a["title"])
            if fp:
                seen[fp]                      = now_str
        save_seen(seen)

    print(f"  텔레그램 전송 {'완료' if ok else '실패'} ({len(new_items)}건)")


def run_scheduler():
    for t in ["08:00", "09:30", "13:00", "16:00"]:
        schedule.every().day.at(t).do(run_once)
    print("스케줄러 시작")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run_once() if args.once else run_scheduler()
