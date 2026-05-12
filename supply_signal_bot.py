"""
supply_signal_bot v22
변경사항:
  - [신규] dedupe_within_batch(): 배치 내 상호 중복 제거
  - [강화] title_fingerprint(): 숫자+핵심어 지문 기반 중복 판별 추가
  - [조정] 제목 유사도 임계값 60% -> 50%
  - [유지] seen.json: URL / 정규화제목 / 지문 3중 저장
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
SEEN_TTL_HOURS   = 48          # 48시간 지난 seen 항목 자동 삭제
SIM_THRESHOLD    = 0.50        # 제목 유사도 임계값
# ─────────────────────────────────────────────────────────────────────────────

# ── 키워드 → 카테고리 매핑 ────────────────────────────────────────────────────
KEYWORD_MAP = {
    "공급망":  ["공급망", "공급 부족", "공급부족", "수급 불안", "수급불안",
               "supply chain", "shortage", "조달", "재고 부족", "재고부족",
               "주사기", "의료 수급", "필수 의료"],
    "수요급증": ["수요 급증", "수요급증", "수요 폭증", "주문 폭주", "수요 증가",
               "AI 수요", "demand surge", "주문 증가", "구리 수요"],
    "증설":    ["증설", "증산", "신규 공장", "공장 준공", "capacity expansion",
               "생산능력", "capa", "팹 증설"],
    "병목":    ["병목", "bottleneck", "생산 차질", "생산차질", "리드타임",
               "lead time", "납기 지연", "납기지연"],
    "공급망":  ["중동전쟁", "전쟁 리스크", "지정학", "관세", "수출 규제",
               "수출규제", "embargo"],
}

# 카테고리 → 태그 (중복 방지용 순서 우선순위)
CATEGORY_PRIORITY = ["수요급증", "증설", "병목", "공급망"]

# ── RSS 피드 ──────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # (출처명, URL, region)
    ("한국경제",  "https://www.hankyung.com/feed/economy",             "🇰🇷"),
    ("한국경제",  "https://www.hankyung.com/feed/industry",            "🇰🇷"),
    ("매일경제",  "https://www.mk.co.kr/rss/30200030/",               "🇰🇷"),
    ("매일경제",  "https://www.mk.co.kr/rss/30100041/",               "🇰🇷"),
    ("연합뉴스",  "https://www.yonhapnewstv.co.kr/rss/economy.xml",   "🇰🇷"),
    ("이데일리",  "https://www.edaily.co.kr/rss/economy.xml",         "🇰🇷"),
    ("전자신문",  "https://rss.etnews.com/Section901.xml",            "🇰🇷"),
    ("뉴스1",    "https://www.news1.kr/rss/economy",                  "🇰🇷"),
    ("아주경제",  "https://www.ajunews.com/rss/economy.xml",          "🇰🇷"),
    ("Reuters",  "https://feeds.reuters.com/reuters/businessNews",    "🌐"),
    ("Reuters",  "https://feeds.reuters.com/reuters/industryNews",    "🌐"),
    ("Bloomberg","https://feeds.bloomberg.com/markets/news.rss",      "🌐"),
]


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    """공백·특수문자 제거 + 소문자화 → 유사도 비교용"""
    return re.sub(r"[\s\W]+", "", title).lower()


def title_fingerprint(title: str) -> str:
    """
    숫자(4593) + 핵심명사 앞 10자 → 짧지만 강력한 지문
    ex) '주사기 4593만개 재고 확보' → '4593주사기재고확보'
    """
    numbers = "".join(re.findall(r"\d+", title))
    words   = re.sub(r"[\s\W]+", "", title)[:10]
    return numbers + words


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def get_category(title: str, desc: str = "") -> str:
    """제목+설명에서 카테고리 판별. 매칭 없으면 '공급망' 기본"""
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
    # TTL 초과 항목 정리
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=SEEN_TTL_HOURS)).isoformat()
    cleaned = {k: v for k, v in seen.items() if v >= cutoff}
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)


def is_seen_duplicate(item: dict, seen: dict) -> bool:
    """
    seen(dict) 기준 3중 중복 체크:
      1) URL 완전 일치
      2) 정규화 제목 유사도 >= SIM_THRESHOLD
      3) 지문(숫자+핵심어) 완전 일치
    """
    url       = item.get("link", "")
    norm_new  = normalize_title(item.get("title", ""))
    fp_new    = title_fingerprint(item.get("title", ""))

    if url in seen:
        return True
    if fp_new and fp_new in seen:
        return True

    # 유사도 체크 (seen에 저장된 정규화 제목들과 비교)
    for key in seen:
        # URL처럼 생긴 key는 건너뜀
        if key.startswith("http"):
            continue
        # 지문 key는 길이 짧고 숫자로 시작 → 건너뜀 (이미 위에서 체크)
        if re.match(r"^\d", key) and len(key) < 20:
            continue
        ratio = SequenceMatcher(None, norm_new, key).ratio()
        if ratio >= SIM_THRESHOLD:
            return True

    return False


# ── 배치 내 상호 중복 제거 ────────────────────────────────────────────────────

def dedupe_within_batch(items: list) -> list:
    """
    같은 배치 안에서 중복 제거 (URL + 유사도 + 지문)
    newsis.com 기사와 v.daum.net 재인덱싱 기사 같은 케이스 처리
    """
    result      = []
    seen_urls   = set()
    seen_fps    = set()
    seen_titles = []   # 정규화 제목 목록

    for item in items:
        url      = item.get("link", "")
        norm     = normalize_title(item.get("title", ""))
        fp       = title_fingerprint(item.get("title", ""))

        # 1) URL 중복
        if url in seen_urls:
            print(f"  [배치중복-URL] {item['title'][:40]}")
            continue

        # 2) 지문 중복
        if fp and fp in seen_fps:
            print(f"  [배치중복-지문] {item['title'][:40]}")
            continue

        # 3) 제목 유사도 중복
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
            headers={"User-Agent": "Mozilla/5.0 (supply_signal_bot)"}
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
            desc  = _t("description") or _t("summary")
            pub   = _t("pubDate") or _t("published") or _t("updated")

            # 발행시간 파싱 → KST 표시용
            pub_display = ""
            for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%dT%H:%M:%SZ"]:
                try:
                    dt = datetime.strptime(pub.strip(), fmt)
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
    """키워드 포함 기사만 추출 + 카테고리 태그 부착"""
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

    # 1차: 키워드 필터
    filtered = filter_by_keywords(raw)
    print(f"  키워드 매칭: {len(filtered)}건")

    # 2차: 배치 내 상호 중복 제거 ← 핵심 신규 로직
    deduped = dedupe_within_batch(filtered)
    print(f"  배치 중복 제거 후: {len(deduped)}건")

    # 최신순 정렬 (pub 없으면 뒤로)
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


# ── 메인 로직 ─────────────────────────────────────────────────────────────────

def run_once():
    seen = load_seen()

    items = collect_all_news()
    if not items:
        print("  수집된 뉴스 없음")
        return

    # 3차: seen.json 기준 세션 간 중복 제거
    new_items = [a for a in items if not is_seen_duplicate(a, seen)]
    print(f"  seen 중복 제거 후: {len(new_items)}건")

    if not new_items:
        print("  새 기사 없음 (전송 생략)")
        return

    ok = send_telegram(build_message(new_items))

    if ok:
        now_str = datetime.now(timezone.utc).isoformat()
        for a in new_items:
            seen[a["link"]]                          = now_str  # URL
            seen[normalize_title(a["title"])]        = now_str  # 정규화 제목
            fp = title_fingerprint(a["title"])
            if fp:
                seen[fp]                             = now_str  # 지문
        save_seen(seen)

    print(f"  텔레그램 전송 {'완료' if ok else '실패'} ({len(new_items)}건)")


def run_scheduler():
    # 원하는 시간대 추가 가능
    for t in ["08:00", "09:30", "13:00", "16:00"]:
        schedule.every().day.at(t).do(run_once)
    print("스케줄러 시작")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="1회 실행 후 종료")
    args = parser.parse_args()
    run_once() if args.once else run_scheduler()
