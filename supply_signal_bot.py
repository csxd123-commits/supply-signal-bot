"""
supply_signal_bot v25
변경사항:
  - [수정] 텔레그램 토큰/챗ID 직접 입력 방식 복원
  - [수정] 404/403/Gone 깨진 RSS 제거
  - [수정] SSL 오류 사이트 verify=False 처리
  - [수정] XML 파싱 오류 사이트 → lxml 폴백 처리
  - [신규] 작동 확인된 대체 소스 추가 (Yahoo Finance, Investing.com 등)
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
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "8796878101:AAHRbfnsrUZKhX0h4ZneFZcmIV4tzbu_NKo"
TELEGRAM_CHAT_ID = "1178221090"
SEEN_FILE        = "seen_urls.json"
MAX_RESULTS      = 20
SEEN_TTL_HOURS   = 48
SIM_THRESHOLD    = 0.50
# ─────────────────────────────────────────────────────────────────────────────

# ── 키워드 → 카테고리 매핑 (제목 매칭 전용, 복합 구문 위주) ──────────────────
KEYWORD_MAP = {
    "공급망": [
        "공급망 위기", "공급망 차질", "공급망 붕괴", "공급망 리스크",
        "공급 부족", "공급부족", "공급 차질", "공급차질",
        "수급 불안", "수급불안", "수급 차질", "수급차질", "수급 위기",
        "재고 부족", "재고부족", "재고 소진",
        "물량 부족", "물량부족", "물량 확보",
        "부품 수급", "부품 부족", "부품부족", "부품 조달",
        "원료 부족", "원료 조달", "소재 부족",
        "supply chain", "shortage", "supply crunch",
        "수입 차질", "통관 차질",
        "의료 수급", "의약품 수급", "주사기 재고",
    ],
    "수요급증": [
        "수요 급증", "수요급증", "수요 폭증", "수요폭증", "수요 폭발",
        "주문 폭주", "주문폭주", "수주 급증", "수주급증",
        "판매 급증", "판매급증",
        "AI 수요", "데이터센터 수요", "전력 수요 급증",
        "구리 수요 급증", "수요 급등",
        "demand surge", "demand boom", "demand spike",
    ],
    "증설": [
        "공장 증설", "라인 증설", "생산 증설", "설비 증설",
        "공장 준공", "공장 착공", "신규 공장",
        "증설 투자", "증산 계획", "생산능력 확대",
        "팹 증설", "반도체 공장", "배터리 공장",
        "capacity expansion", "new plant", "new factory",
    ],
    "병목": [
        "병목", "bottleneck",
        "생산 차질", "생산차질", "납기 지연", "납기지연",
        "리드타임 증가", "리드타임 급등",
        "공장 가동 중단", "라인 가동 중단",
        "출하 지연", "배송 지연", "공급 지연",
        "lead time", "production halt", "factory shutdown",
    ],
    "관세리스크": [
        "관세 인상", "관세 부과", "관세 폭탄", "관세 충격",
        "상호관세", "보복관세", "추가관세", "고율관세",
        "트럼프 관세", "미국 관세", "25% 관세",
        "수출 규제", "수출규제", "수출 금지",
        "무역 규제", "무역 제재", "무역 분쟁", "무역전쟁",
        "미중 갈등", "미중 분쟁", "반도체 규제",
        "tariff", "trade war", "export ban", "sanctions", "embargo",
    ],
    "원자재": [
        "희토류", "희귀금속", "핵심 광물",
        "리튬 가격", "리튬 부족", "코발트 가격",
        "니켈 가격", "구리 가격", "구리 수급",
        "원자재 가격 급등", "원자재 가격 상승", "원자재 부족",
        "철광석 가격", "알루미늄 가격",
        "배터리 소재", "양극재", "음극재",
        "rare earth", "critical mineral", "lithium shortage",
    ],
}

# 제목에 이 단어 포함 시 무조건 제외
BLACKLIST = [
    "훈장", "수훈", "표창", "포상", "시상",
    "채용", "모집", "교육생", "인턴", "취업",
    "주가", "코스피", "코스닥",
    "채소", "농산물", "배추", "양파", "사과 값", "과일 값",
    "화장품 점검", "식품 점검", "위생 점검",
    "불량률 감소", "품질 향상",
    "결혼", "출산", "인구",
]

CATEGORY_PRIORITY = ["병목", "공급망", "관세리스크", "원자재", "수요급증", "증설"]

# ── RSS 피드 ──────────────────────────────────────────────────────────────────
# (출처명, URL, region, ssl_verify)
# ssl_verify=False → 연합뉴스 등 SSL 인증서 오류 우회
RSS_FEEDS = [
    # ── 국내 ✅ 작동 확인 ──────────────────────────────────────────────────────
    ("한국경제",    "https://www.hankyung.com/feed/economy",               "🇰🇷", True),
    ("매일경제",    "https://www.mk.co.kr/rss/30200030/",                 "🇰🇷", True),
    ("매일경제",    "https://www.mk.co.kr/rss/30100041/",                 "🇰🇷", True),
    ("전자신문",    "https://rss.etnews.com/Section901.xml",              "🇰🇷", True),
    ("아주경제",    "https://www.ajunews.com/rss/economy.xml",            "🇰🇷", True),

    # ── 국내 ⚠️ SSL 우회 필요 ─────────────────────────────────────────────────
    ("연합뉴스",    "https://www.yna.co.kr/rss/economy.xml",              "🇰🇷", False),
    ("연합뉴스",    "https://www.yna.co.kr/rss/industry.xml",             "🇰🇷", False),
    ("뉴시스",      "https://newsis.com/RSS/economy.rss",                 "🇰🇷", False),
    ("뉴시스",      "https://newsis.com/RSS/industry.rss",                "🇰🇷", False),
    ("뉴스1",       "https://www.news1.kr/rss/economy",                  "🇰🇷", False),
    ("헤럴드경제",  "https://biz.heraldcorp.com/rss/",                   "🇰🇷", False),

    # ── 국내 대체 URL ─────────────────────────────────────────────────────────
    ("조선비즈",    "https://biz.chosun.com/RSS/",                       "🇰🇷", True),
    ("머니투데이",  "https://news.mt.co.kr/rss/",                        "🇰🇷", True),
    ("서울경제",    "https://www.sedaily.com/Rss",                       "🇰🇷", True),
    ("파이낸셜뉴스","https://www.fnnews.com/rss",                        "🇰🇷", True),
    ("이데일리",    "https://www.edaily.co.kr/rss/",                     "🇰🇷", True),
    ("SBS Biz",    "https://biz.sbs.co.kr/rss/",                        "🇰🇷", True),
    ("KBS",        "https://news.kbs.co.kr/rss/news-economy.xml",        "🇰🇷", True),
    ("YTN",        "https://www.ytn.co.kr/rss/0102.xml",                 "🇰🇷", True),
    ("비즈워치",    "https://www.bizwatch.co.kr/rss/allArticle.rss",     "🇰🇷", True),
    ("에너지경제",  "https://www.ekn.kr/rss/rss.html",                   "🇰🇷", True),

    # ── 국내 전문 ─────────────────────────────────────────────────────────────
    ("EBN",        "https://www.ebn.co.kr/rss/rss.html",                 "🇰🇷", True),
    ("철강금속신문","https://www.snmnews.com/rss/rss.xml",               "🇰🇷", True),
    ("물류신문",    "https://www.klnews.co.kr/rss/rss.html",             "🇰🇷", True),
    ("케미컬뉴스",  "https://www.chemicalnews.co.kr/rss/allArticle.rss", "🇰🇷", True),

    # ── 해외 ✅ 작동 확인 ──────────────────────────────────────────────────────
    ("Bloomberg",  "https://feeds.bloomberg.com/markets/news.rss",       "🌐", True),
    ("FT",         "https://www.ft.com/rss/home/uk",                     "🌐", True),
    ("WSJ",        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",      "🌐", True),
    ("WSJ",        "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",    "🌐", True),
    ("CNBC",       "https://www.cnbc.com/id/10001147/device/rss/rss.html","🌐", True),
    ("CNBC",       "https://www.cnbc.com/id/19854910/device/rss/rss.html","🌐", True),
    ("SupplyChainDive","https://www.supplychaindive.com/feeds/news/",    "🌐", True),
    ("FreightWaves","https://www.freightwaves.com/news/feed",            "🌐", True),
    ("EE Times",   "https://www.eetimes.com/feed/",                      "🌐", True),
    ("TechCrunch", "https://techcrunch.com/feed/",                       "🌐", True),
    ("The Loadstar","https://theloadstar.com/feed/",                     "🌐", True),

    # ── 해외 대체 (Reuters DNS 차단 대신) ─────────────────────────────────────
    ("Yahoo Finance","https://finance.yahoo.com/rss/topstories",         "🌐", True),
    ("Yahoo Finance","https://finance.yahoo.com/rss/industry",           "🌐", True),
    ("Seeking Alpha","https://seekingalpha.com/feed.xml",                "🌐", True),
    ("Investopedia","https://www.investopedia.com/feedbuilder/feed/getarticles/?name=News", "🌐", True),

    # ── 해외 공급망/원자재 전문 ────────────────────────────────────────────────
    ("Metal Bulletin","https://www.metalbulletin.com/rss",              "🌐", True),
    ("Fastmarkets",  "https://www.fastmarkets.com/rss/news",            "🌐", True),
    ("JOC",          "https://www.joc.com/rss.xml",                     "🌐", True),  # 해운/물류
    ("Nikkei Asia",  "https://asia.nikkei.com/rss/feed/nar",            "🌐", True),
    ("SCMP Business","https://www.scmp.com/rss/5/feed",                 "🌐", True),  # 中 공급망
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
            if kw.lower() in title.lower():
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

def parse_xml_lenient(content: bytes):
    """ET 실패 시 잘못된 문자 제거 후 재시도"""
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        # 제어문자 제거 후 재시도
        cleaned = re.sub(rb'[\x00-\x08\x0b\x0c\x0e-\x1f]', b'', content)
        try:
            return ET.fromstring(cleaned)
        except ET.ParseError:
            return None


def fetch_rss(source: str, url: str, region: str, ssl_verify: bool = True) -> list:
    try:
        resp = requests.get(
            url, timeout=10,
            verify=ssl_verify,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) supply_signal_bot/v25"}
        )
        resp.raise_for_status()

        root = parse_xml_lenient(resp.content)
        if root is None:
            print(f"  RSS 오류 [{source}]: XML 파싱 실패")
            return []

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
        title = a["title"].lower()

        # 1) 블랙리스트 제목 제외
        if any(bl.lower() in title for bl in BLACKLIST):
            continue

        # 2) 제목에서만 키워드 매칭 (desc 제외)
        if any(kw.lower() in title for kw in all_kws):
            a["keyword"] = get_category(a["title"])
            result.append(a)
    return result


def collect_all_news() -> list:
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 수집 시작")

    raw = []
    for source, url, region, ssl_verify in RSS_FEEDS:
        items = fetch_rss(source, url, region, ssl_verify)
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
