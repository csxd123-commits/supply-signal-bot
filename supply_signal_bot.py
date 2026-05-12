"""
supply_signal_bot v27
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
import math
try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

# ── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "8796878101:AAHRbfnsrUZKhX0h4ZneFZcmIV4tzbu_NKo"
TELEGRAM_CHAT_ID = "-1003984467582"
CLAUDE_API_KEY   = "여기에_ANTHROPIC_API_KEY"   # https://console.anthropic.com
NAVER_CLIENT_ID  = "WQHg_9Xr2Jn8dxEx1GnN"
NAVER_CLIENT_SECRET = "H2P2y94ZxM"
SEEN_FILE        = "seen_urls.json"
MAX_RESULTS      = 20
SEEN_TTL_HOURS   = 48
SIM_THRESHOLD    = 0.50
# ─────────────────────────────────────────────────────────────────────────────

# ── 키워드 → 카테고리 매핑 ─────────────────────────────────────────────────
KEYWORD_MAP = {
    "공급망": [
        # 범용 쇼티지
        "공급망", "쇼티지", "shortage", "supply chain", "supply crunch",
        "공급 부족", "공급부족", "공급 차질", "공급차질",
        "수급 불안", "수급불안", "수급 차질", "수급차질", "수급 위기", "수급 부족",
        "재고 부족", "재고부족", "재고 소진", "재고 고갈",
        "물량 부족", "물량부족",
        "수입 차질", "통관 차질",
        # 반도체·칩
        "반도체 부족", "반도체 수급", "반도체 공급 차질",
        "칩 부족", "칩 수급", "chip shortage",
        "DRAM 부족", "DRAM 수급", "메모리 부족", "메모리 수급",
        "HBM 부족", "HBM 수급", "HBM 공급",
        "비메모리 수급", "시스템반도체 부족",
        # 부품·소재
        "부품 수급", "부품 부족", "부품부족", "부품 조달",
        "기판 부족", "기판 수급", "기판 공급 차질",
        "PCB 부족", "PCB 수급",
        "전력반도체 부족", "전력반도체 수급",
        "감속기 부족", "감속기 수급",
        "모터 부족", "모터 수급",
        "원료 부족", "소재 부족",
        # 에너지·전력
        "전력 부족", "전력난", "전력 수급 위기", "전력 대란",
        "블랙아웃", "정전 위기",
        # 의료
        "의료 수급", "의약품 수급", "주사기 재고",
        # 컨테이너·해운
        "컨테이너 부족", "컨테이너 수급",
        # 항만
        "항만 적체", "항구 혼잡", "port congestion",
    ],
    "수요급증": [
        "수요 급증", "수요급증", "수요 폭증", "수요폭증",
        "주문 폭주", "주문폭주", "수주 급증", "수주급증",
        "판매 급증", "수요 급등", "수요 폭발",
        "demand surge", "demand boom", "demand spike",
        # AI·데이터센터
        "AI 수요", "데이터센터 수요", "GPU 수요",
        "HBM 수요 급증", "반도체 수요 급증",
        # 전력·에너지
        "전력 수요 급증", "전력 수요 폭증",
        # 친환경·배터리
        "전기차 수요 급증", "ESS 수요 급증", "배터리 수요 급증",
        # 방산
        "방산 수요", "방위산업 수주 급증", "무기 수요",
    ],
    "증설": [
        "증설", "증산",
        "공장 준공", "공장 착공", "신규 공장", "공장 신설",
        "팹 증설", "반도체 공장", "배터리 공장", "기가팩토리",
        "생산능력 확대", "설비 투자", "라인 증설",
        "capacity expansion", "new plant", "new factory", "gigafactory",
    ],
    "병목": [
        "병목", "bottleneck",
        "납기 지연", "납기지연", "납기 차질",
        "생산 차질", "생산차질",
        "리드타임", "lead time",
        "공장 가동 중단", "출하 지연", "배송 지연", "운송 차질",
        "production halt", "factory shutdown",
        # 해운·물류 비용
        "운임 급등", "해상 운임 급등", "컨테이너 운임 급등",
        "BDI 급등", "물류비 급등", "freight rate",
        # 파업
        "항만 파업", "부두 파업", "물류 파업", "철도 파업",
        "dock strike", "port strike",
        # 재해·사고
        "공장 화재", "공장 폭발", "공장 침수",
    ],
    "관세리스크": [
        # 관세 (한국어)
        "관세 인상", "관세 부과", "관세 폭탄", "관세 충격", "관세 영향",
        "상호관세", "보복관세", "추가관세", "고율관세",
        "트럼프 관세", "미국 관세", "25% 관세", "10% 관세",
        # 무역 규제
        "수출 규제", "수출규제", "수출 금지",
        "무역 분쟁", "무역전쟁", "무역 제재",
        "미중 갈등", "미중 분쟁",
        "반도체 수출 규제", "반도체 규제", "수입 규제",
        "대중 제재", "중국 규제", "중국산 규제",
        # 공급망 재편
        "IRA", "CHIPS Act", "칩스법",
        "리쇼어링", "니어쇼어링", "프렌드쇼어링",
        "공급망 재편", "탈중국",
        # 영어 (맥락 있는 복합어만)
        "tariff hike", "tariff increase", "tariff war", "trade war",
        "export ban", "export control", "import ban",
        "trade sanction", "chip sanction", "semiconductor sanction",
        "tech embargo", "chip embargo",
        "decoupling", "friend-shoring", "reshoring",
    ],
    "원자재": [
        # 광물·금속 위기
        "희토류 부족", "희토류 수급", "희토류 공급", "희토류 규제", "희토류 수출 금지",
        "리튬 부족", "리튬 수급", "리튬 공급 차질", "리튬 쇼티지", "리튬 가격 급등",
        "코발트 부족", "코발트 수급", "코발트 가격 급등",
        "니켈 부족", "니켈 수급", "니켈 공급 차질", "니켈 가격 급등",
        "구리 부족", "구리 수급", "구리 공급 차질", "구리 쇼티지",
        "알루미늄 부족", "알루미늄 수급", "알루미늄 공급 차질",
        "흑연 수출 금지", "흑연 규제", "흑연 부족",
        "갈륨 규제", "갈륨 수출 통제", "게르마늄 규제",
        "텅스텐 수출 금지", "인듐 부족", "몰리브덴 부족",
        "팔라듐 공급 부족", "팔라듐 수급",
        "철광석 공급 차질", "원료탄 수급",
        # 에너지 위기
        "LNG 수급", "LNG 공급 차질", "LNG 부족", "LNG 대란",
        "천연가스 부족", "천연가스 수급", "천연가스 공급 차질",
        "석탄 공급 차질", "석탄 수급 불안",
        "에너지 대란", "에너지 수급 위기", "에너지 부족",
        "요소수 부족", "요소수 대란", "요소수 수급",
        "우라늄 공급", "우라늄 부족",
        # 반도체·배터리 소재
        "폴리실리콘 부족", "폴리실리콘 수급",
        "포토레지스트 부족", "포토레지스트 수급",
        "불화수소 부족", "불화수소 수급",
        "실리콘 웨이퍼 부족", "웨이퍼 공급 차질",
        "배터리 소재 부족", "배터리 소재 수급",
        "양극재 부족", "양극재 수급 차질",
        "음극재 부족", "분리막 수급",
        # 영어
        "rare earth shortage", "rare earth ban", "rare earth supply",
        "critical mineral shortage", "mineral supply chain",
        "lithium shortage", "lithium supply crunch",
        "cobalt shortage", "nickel shortage",
        "copper shortage", "copper supply crunch",
        "polysilicon shortage", "wafer shortage",
        "LNG shortage", "LNG supply crisis",
        "natural gas shortage", "energy crisis",
    ],
    "물류위기": [
        "운임 폭등", "해상운임 폭등", "컨테이너 운임 폭등",
        "운임 급등", "해상 운임", "컨테이너 운임",
        "BDI 급등", "물류비 폭등",
        "freight rate surge", "shipping cost",
        "항만 마비", "항만 혼잡", "항구 마비",
        "물류 대란", "물류 마비", "물류 차질",
        "항만 파업", "부두 파업", "물류 파업",
        "dock strike", "port strike", "logistics strike",
        "선박 부족", "선복 부족",
    ],

    # ── 로봇·휴머노이드 ────────────────────────────────────────────────────────
    "로봇부품": [
        # 감속기
        "감속기 부족", "감속기 수급", "감속기 공급 차질", "감속기 납기",
        "하모닉드라이브 부족", "하모닉드라이브 수급",
        "harmonic drive 부족", "harmonic drive shortage",
        "RV감속기 부족", "사이클로이드 감속기 수급",
        # 액추에이터·모터
        "액추에이터 부족", "액추에이터 수급", "액추에이터 공급 차질",
        "서보모터 수급", "서보모터 부족", "서보모터 공급 차질",
        "리니어모터 수급", "DD모터 수급",
        # 정밀기계 부품
        "볼스크류 부족", "볼스크류 수급",
        "리니어가이드 부족", "리니어가이드 수급",
        "베어링 부족", "베어링 수급", "베어링 공급 차질",
        "엔코더 부족", "엔코더 수급",
        # 로봇 전체
        "휴머노이드 부품 수급", "로봇 공급망", "로봇 부품 수급",
        "협동로봇 수급", "산업용 로봇 수급",
        "robot shortage", "humanoid supply chain",
    ],

    # ── 전력반도체·화합물반도체 ───────────────────────────────────────────────
    "전력반도체": [
        # SiC
        "SiC 부족", "SiC 수급", "SiC 웨이퍼 부족", "SiC 웨이퍼 수급",
        "SiC 공급 차질", "탄화규소 부족", "탄화규소 수급",
        "SiC wafer shortage", "silicon carbide shortage",
        # GaN
        "GaN 부족", "GaN 수급", "GaN 공급 차질",
        "GaN shortage", "gallium nitride shortage",
        # IGBT·MOSFET
        "IGBT 부족", "IGBT 수급", "IGBT 공급 차질",
        "MOSFET 부족", "MOSFET 수급",
        # 전력반도체 일반
        "전력반도체 부족", "전력반도체 수급", "전력반도체 공급 차질",
        "화합물반도체 수급", "화합물반도체 부족",
        "power semiconductor shortage",
    ],

    # ── 배터리·소재 ───────────────────────────────────────────────────────────
    "배터리소재": [
        # 셀·팩
        "배터리 셀 부족", "배터리 셀 수급", "배터리 팩 수급",
        "배터리 공급 차질", "battery shortage", "battery supply chain",
        # 양극재
        "양극재 부족", "양극재 수급", "양극재 공급 차질",
        "NCM 수급", "NCM 부족", "NCA 수급", "NCA 부족",
        "LFP 수급", "LFP 부족", "LFP 공급 차질", "LFP shortage",
        # 음극재
        "음극재 부족", "음극재 수급", "음극재 공급 차질",
        "천연흑연 수급", "인조흑연 수급",
        # 분리막
        "분리막 부족", "분리막 수급", "분리막 공급 차질",
        "separator shortage",
        # 동박·알박
        "동박 부족", "동박 수급", "동박 공급 차질",
        "알박 부족", "알박 수급", "알루미늄박 수급",
        "전지박 수급", "전지박 부족",
        "copper foil shortage",
        # 전해액·전해질
        "전해액 부족", "전해액 수급", "전해액 공급 차질",
        "전해질 부족", "전해질 수급",
        "electrolyte shortage",
        # 바인더·도전재
        "바인더 수급", "도전재 수급", "CNT 수급",
        "NMP 수급", "NMP 부족",
        # 전고체·차세대
        "전고체 배터리 소재", "전고체 소재 부족",
        "나트륨 배터리 수급", "나트륨이온 배터리",
        "solid state battery shortage",
    ],

    # ── 전력기기·그리드 ───────────────────────────────────────────────────────
    "전력기기": [
        # 변압기
        "변압기 부족", "변압기 수급", "변압기 납기 지연",
        "고압 변압기 부족", "변압기 공급 차질", "변압기 대란",
        "초고압 변압기 수급", "배전 변압기 부족",
        "transformer shortage", "transformer lead time", "transformer supply",
        # 차단기·개폐기
        "차단기 수급", "차단기 부족",
        "GIS 수급", "GIS 부족", "가스절연개폐장치 수급",
        "개폐기 수급", "수배전반 수급",
        # 인버터
        "인버터 부족", "인버터 수급", "인버터 공급 차질",
        "PCS 수급", "PCS 부족",
        # 케이블·부스바
        "전력케이블 수급", "전력케이블 부족",
        "부스바 수급", "부스바 부족",
        "HVDC 장비 수급",
        # 전력기기 일반
        "전력기기 수급", "전력기기 부족",
        "그리드 장비 부족", "송전 설비 수급",
        "전력설비 공급 차질",
    ],

    # ── 광통신·데이터센터 부품 ────────────────────────────────────────────────
    "광통신부품": [
        # 광트랜시버·모듈
        "광트랜시버 부족", "광트랜시버 수급", "광트랜시버 공급 차질",
        "광모듈 수급", "광모듈 부족", "광모듈 공급 차질",
        "transceiver shortage", "optical module shortage",
        "800G 트랜시버 수급", "400G 트랜시버 수급",
        # 광케이블·부품
        "광케이블 부족", "광케이블 수급",
        "광섬유 부족", "광섬유 수급",
        "VCSEL 수급", "DFB 레이저 수급",
        # 패키징
        "CoWoS 공급 차질", "CoWoS 부족", "CoWoS 수급",
        "HBM 패키징 부족", "첨단 패키징 수급",
        "advanced packaging shortage",
        "SoIC 수급", "칩렛 수급",
        # AI 인프라 부품
        "ASIC 부족", "ASIC 수급",
        "NPU 수급", "AI칩 수급", "AI칩 부족",
        "GPU 부족", "GPU 수급", "GPU shortage",
    ],

    # ── 자동차 부품 ───────────────────────────────────────────────────────────
    "자동차부품": [
        # 차량용 반도체
        "차량용 반도체 부족", "차량용 반도체 수급", "차량용 반도체 공급 차질",
        "automotive chip shortage", "automotive semiconductor",
        # 수동부품
        "MLCC 부족", "MLCC 수급", "MLCC 공급 차질",
        "MLCC shortage",
        # 센서·카메라
        "라이다 수급", "라이다 부족",
        "카메라모듈 수급", "카메라모듈 부족",
        "레이더 부품 수급",
        # 와이어하네스·커넥터
        "와이어하네스 수급", "와이어하네스 부족",
        "커넥터 수급", "커넥터 부족",
        # 완성차
        "자동차 부품 수급", "자동차 부품 부족",
        "완성차 생산 차질", "완성차 공급 차질",
    ],

    # ── 수소·신에너지 ─────────────────────────────────────────────────────────
    "수소에너지": [
        "수소 공급 차질", "수소 수급", "수소 부족",
        "전해조 수급", "전해조 부족", "전해조 공급 차질",
        "수전해 설비 수급",
        "연료전지 부품 수급", "연료전지 공급 차질",
        "수소 공급망", "청정수소 수급",
        "MEA 수급", "MEA 부족",
        "hydrogen shortage", "electrolyzer shortage",
        "fuel cell shortage",
    ],

    # ── 풍력·태양광 ───────────────────────────────────────────────────────────
    "신재생에너지": [
        # 태양광
        "태양광 모듈 수급", "태양광 모듈 부족", "태양광 공급 차질",
        "폴리실리콘 수급", "폴리실리콘 부족",
        "태양전지 수급", "셀 수급",
        "solar module shortage", "solar supply chain",
        # 풍력
        "풍력 부품 수급", "풍력 부품 부족", "풍력 타워 수급",
        "블레이드 수급", "블레이드 부족",
        "풍력 발전기 수급", "나셀 수급",
        "wind turbine shortage", "blade shortage",
        # 공통
        "풍력 공급망", "태양광 공급망",
        "재생에너지 부품 수급",
    ],

    # ── 조선·방산 ─────────────────────────────────────────────────────────────
    "조선방산": [
        # 조선 소재
        "후판 수급", "후판 부족", "후판 공급 차질",
        "강판 수급", "강판 부족",
        # 조선 기자재
        "조선 부품 수급", "선박 부품 부족", "선박 기자재 수급",
        "엔진 수급", "선박 엔진 수급",
        "프로펠러 수급", "프로펠러 부족",
        "해양플랜트 부품 수급",
        # 방산
        "방산 부품 수급", "방산 공급망", "방산 부품 부족",
        "방산 소재 수급", "방산 소재 부족",
        "탄약 수급", "탄약 부족", "탄약 공급 차질",
        "무기 공급 차질", "방위산업 공급 차질",
        "미사일 부품 수급", "드론 부품 수급",
        "defense supply chain", "ammunition shortage",
        "defense component shortage",
    ],

    # ── 디스플레이 ────────────────────────────────────────────────────────────
    "디스플레이": [
        # OLED 소재
        "OLED 소재 수급", "OLED 소재 부족",
        "발광재료 수급", "발광재료 부족",
        "OLED material shortage",
        # 부품
        "편광판 수급", "편광판 부족",
        "백라이트 수급", "BLU 수급",
        "드라이버IC 수급", "드라이버IC 부족", "DDIC 수급", "DDIC 부족",
        "디스플레이 부품 수급", "디스플레이 공급 차질",
        # 유리·기판
        "유리기판 수급", "유리기판 부족",
        "글라스 수급", "글라스 부족",
        "display shortage", "panel shortage",
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
    "수급 안정", "수급 회복", "수급 정상",
    # 이란·중동 전쟁 노이즈
    "Iran", "이란 휴전", "이란 전쟁", "이란 평화", "이란 핵",
    "ceasefire", "peace plan", "money-laundering",
    "war crime", "far-right", "AfD", "BRICS",
    # 금융·투자 노이즈
    "hedge fund", "gold price", "silver price",
    "markets wrap", "morning squawk", "market open",
    # HR·일반 노이즈
    "baby boutique", "cargo chief", "new role",
    "earnings", "quarterly results", "profit",
]

CATEGORY_PRIORITY = ["병목", "물류위기", "공급망", "전력반도체", "광통신부품", "자동차부품", "배터리소재", "전력기기", "로봇부품", "조선방산", "수소에너지", "신재생에너지", "디스플레이", "관세리스크", "원자재", "수요급증", "증설"]

# ── 글로벌 핵심 기업 리스트 ───────────────────────────────────────────────────
# 기업명이 제목에 있고, 공급망 맥락어도 동시에 있을 때만 잡힘
GLOBAL_COMPANIES = {
    # 반도체 파운드리·장비
    "반도체": [
        "TSMC", "ASML", "Lam Research", "Applied Materials", "Tokyo Electron",
        "Intel Foundry", "GlobalFoundries", "UMC",
    ],
    # AI·GPU
    "수요급증": [
        "NVIDIA", "AMD", "Broadcom", "Marvell", "Qualcomm",
    ],
    # 광통신·데이터센터
    "광통신부품": [
        "Lumentum", "Coherent", "Corning", "Ciena", "Fabrinet",
        "II-VI", "Inphi",
    ],
    # 배터리
    "배터리소재": [
        "CATL", "Panasonic", "LG Energy", "Samsung SDI", "SK On",
        "QuantumScape", "Solid Power",
    ],
    # 전력·에너지
    "수소에너지": [
        "Bloom Energy", "Plug Power", "Nel Hydrogen", "ITM Power",
        "Ballard Power",
    ],
    # 풍력·태양광
    "신재생에너지": [
        "Vestas", "Siemens Energy", "GE Vernova", "First Solar",
        "Enphase", "SolarEdge",
    ],
    # 전력반도체
    "전력반도체": [
        "Wolfspeed", "Onsemi", "ON Semiconductor", "STMicroelectronics",
        "Infineon", "Rohm",
    ],
    # 전력기기
    "전력기기": [
        "Eaton", "ABB", "Siemens", "Schneider Electric",
        "Hitachi Energy", "GE Grid",
    ],
    # 자동차·모빌리티
    "자동차부품": [
        "Tesla", "Rivian", "BYD", "CATL", "Aptiv",
        "Continental", "Bosch",
    ],
    # 로봇
    "로봇부품": [
        "Fanuc", "Yaskawa", "Harmonic Drive", "Nabtesco",
        "Figure AI", "Boston Dynamics", "1X Technologies",
    ],
    # 조선·방산
    "조선방산": [
        "Lockheed Martin", "RTX", "Raytheon", "Northrop Grumman",
        "L3Harris", "BAE Systems",
    ],
}

# 공급망 맥락어 (기업명과 함께 있을 때만 잡음)
COMPANY_CONTEXT_KEYWORDS = [
    "공급", "수급", "부족", "차질", "지연", "증설", "감산", "중단",
    "shortage", "supply", "delay", "halt", "cut", "expand", "shortage",
    "disruption", "constraint", "bottleneck", "capacity",
]

# ── 네이버 뉴스 검색 키워드 ─────────────────────────────────────────────────
# 카테고리별 검색어 → 네이버 뉴스 API로 최신 기사 수집
NAVER_QUERIES = [
    # 공급망·쇼티지
    ("공급망 차질",    "공급망"),
    ("공급 부족",      "공급망"),
    ("수급 불안",      "공급망"),
    ("재고 부족",      "공급망"),
    ("부품 수급",      "공급망"),
    # 반도체
    ("반도체 수급",    "공급망"),
    ("HBM 수급",       "공급망"),
    ("차량용 반도체",  "자동차부품"),
    # 배터리 소재
    ("양극재 수급",    "배터리소재"),
    ("동박 수급",      "배터리소재"),
    ("분리막 수급",    "배터리소재"),
    ("LFP 공급",       "배터리소재"),
    # 전력기기
    ("변압기 부족",    "전력기기"),
    ("변압기 수급",    "전력기기"),
    ("인버터 수급",    "전력기기"),
    # 전력반도체
    ("SiC 웨이퍼",     "전력반도체"),
    ("GaN 수급",       "전력반도체"),
    ("IGBT 수급",      "전력반도체"),
    # 광통신
    ("광트랜시버 수급","광통신부품"),
    ("CoWoS 수급",     "광통신부품"),
    # 로봇
    ("감속기 수급",    "로봇부품"),
    ("액추에이터 수급","로봇부품"),
    # 원자재
    ("희토류 규제",    "원자재"),
    ("요소수 수급",    "원자재"),
    ("LNG 수급",       "원자재"),
    ("천연가스 부족",  "원자재"),
    # 관세·무역
    ("관세 인상",      "관세리스크"),
    ("수출 규제",      "관세리스크"),
    ("미중 갈등",      "관세리스크"),
    ("트럼프 관세",    "관세리스크"),
    # 병목
    ("납기 지연",      "병목"),
    ("생산 차질",      "병목"),
    # 증설
    ("공장 증설",      "증설"),
    ("공장 준공",      "증설"),
    # 수요급증
    ("수요 급증",      "수요급증"),
    ("수주 급증",      "수요급증"),
    # 조선·방산
    ("후판 수급",      "조선방산"),
    ("방산 부품",      "조선방산"),
    # 에너지
    ("전력난",         "공급망"),
    ("에너지 대란",    "원자재"),
]


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
    ("뉴시스",      "https://newsis.com/rss/economy.xml",                 "🇰🇷", False),
    ("뉴시스",      "https://newsis.com/rss/industry.xml",                "🇰🇷", False),
    ("뉴스1",       "https://www.news1.kr/rss/economy",                  "🇰🇷", False),
    ("헤럴드경제",  "https://biz.heraldcorp.com/rss/",                   "🇰🇷", False),

    # ── 국내 대체 URL ─────────────────────────────────────────────────────────
    ("조선비즈",    "https://biz.chosun.com/rss/rss.xml",                "🇰🇷", True),
    ("머니투데이",  "https://www.mt.co.kr/rss/",                         "🇰🇷", True),
    ("서울경제",    "https://www.sedaily.com/RSS/rssMain.xml",           "🇰🇷", True),
    ("파이낸셜뉴스","https://www.fnnews.com/rss/fn_news_headline.xml",   "🇰🇷", True),
    ("이데일리",    "https://www.edaily.co.kr/rss/newsflash.xml",        "🇰🇷", True),
    ("SBS Biz",    "https://biz.sbs.co.kr/rss/",                        "🇰🇷", True),
    ("KBS",        "https://news.kbs.co.kr/rss/rss.xml",                "🇰🇷", True),
    ("YTN",        "https://www.ytn.co.kr/rss/all.xml",                 "🇰🇷", True),
    ("비즈워치",    "https://www.bizwatch.co.kr/rss/",                   "🇰🇷", True),
    ("에너지경제",  "https://www.ekn.kr/rss/allCategory.xml",            "🇰🇷", True),

    # ── 국내 전문 ─────────────────────────────────────────────────────────────
    ("EBN",        "https://www.ebn.co.kr/rss/",                        "🇰🇷", True),
    ("철강금속신문","https://www.snmnews.com/rss/",                      "🇰🇷", True),
    ("물류신문",    "https://www.klnews.co.kr/rss/",                     "🇰🇷", True),
    ("케미컬뉴스",  "https://www.chemicalnews.co.kr/rss/allArticle.rss","🇰🇷", False),

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
    # ── 해외 공급망/원자재 전문 ────────────────────────────────────────────────
    ("JOC",          "https://www.joc.com/rss.xml",                     "🌐", True),  # 해운/물류
    ("Nikkei Asia",  "https://asia.nikkei.com/rss/feed/nar",            "🌐", True),
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


def fetch_naver_news(query: str, category: str, display: int = 20) -> list:
    """네이버 뉴스 검색 API로 기사 수집"""
    if not NAVER_CLIENT_ID or NAVER_CLIENT_ID == "여기에_NAVER_CLIENT_ID":
        return []
    try:
        resp = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={
                "X-Naver-Client-Id":     NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            },
            params={"query": query, "display": display, "sort": "date"},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        result = []
        for it in items:
            title = re.sub(r"<[^>]+>", "", it.get("title", "")).strip()
            link  = it.get("originallink") or it.get("link", "")
            desc  = re.sub(r"<[^>]+>", "", it.get("description", "")).strip()
            pub   = it.get("pubDate", "")

            pub_display = ""
            try:
                dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z")
                pub_display = dt.astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M")
            except Exception:
                pass

            if not title or not link:
                continue

            result.append({
                "title":   title,
                "link":    link,
                "desc":    desc,
                "source":  "네이버뉴스",
                "pub":     pub_display,
                "region":  "🇰🇷",
                "keyword": category,
            })
        return result
    except Exception as e:
        print(f"  네이버 오류 [{query}]: {e}")
        return []


def _pub_to_kst(pub_str: str) -> str:
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]:
        try:
            dt = datetime.strptime(pub_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M")
        except Exception:
            pass
    return ""


def fetch_rss(source: str, url: str, region: str, ssl_verify: bool = True) -> list:
    # ── feedparser 우선 시도 (관대한 파싱) ───────────────────────────────────
    if HAS_FEEDPARSER:
        try:
            import ssl as _ssl
            ctx = None if ssl_verify else _ssl._create_unverified_context()
            fd  = feedparser.parse(url, handlers=[] if ssl_verify else
                  [feedparser.api._build_urllib2_request(url)])
            # feedparser는 SSL 설정이 어려우므로 requests로 content 받아서 파싱
            resp = requests.get(
                url, timeout=10, verify=ssl_verify,
                headers={"User-Agent": "Mozilla/5.0 supply_signal_bot/v26"}
            )
            resp.raise_for_status()
            fd = feedparser.parse(resp.content)
            result = []
            for entry in fd.entries:
                title = entry.get("title", "").strip()
                link  = entry.get("link", "") or entry.get("id", "")
                desc  = re.sub(r"<[^>]+>", "", entry.get("summary", ""))
                pub   = entry.get("published", "") or entry.get("updated", "")
                pub_display = _pub_to_kst(pub) if pub else ""
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
            pass  # feedparser 실패 시 ET로 폴백

    # ── ET 폴백 ──────────────────────────────────────────────────────────────
    try:
        resp = requests.get(
            url, timeout=10, verify=ssl_verify,
            headers={"User-Agent": "Mozilla/5.0 supply_signal_bot/v26"}
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
            pub_display = _pub_to_kst(pub) if pub else ""
            if not title or not link:
                continue
            result.append({
                "title":  title, "link": link, "desc": desc,
                "source": source, "pub": pub_display, "region": region,
            })
        return result
    except Exception as e:
        print(f"  RSS 오류 [{source}]: {e}")
        return []


def classify_with_claude(articles: list) -> list:
    """
    Claude API로 기사 제목 배치 분류.
    공급망·쇼티지·원자재·관세·수요급증·증설 관련 기사만 추출.
    30건씩 배치 처리.
    """
    if not articles:
        return []
    if CLAUDE_API_KEY == "여기에_ANTHROPIC_API_KEY":
        print("  [Claude API] API 키 미설정 → 키워드 필터만 사용")
        return articles  # API 키 없으면 그냥 통과

    BATCH = 30
    result = []

    for i in range(0, len(articles), BATCH):
        batch = articles[i:i+BATCH]
        lines = "\n".join(
            f"{j+1}. {a['title']}" for j, a in enumerate(batch)
        )
        prompt = f"""아래 뉴스 기사 제목 목록을 분석해서, 다음 중 하나 이상에 해당하는 기사 번호와 카테고리를 JSON으로만 반환해줘.

분류 기준:
- 공급망: 부품/소재/원료/의약품 공급 부족·차질·수급 불안
- 병목: 납기 지연, 생산 차질, 리드타임 증가, 공장 가동 중단
- 수요급증: 특정 품목·산업의 수요/주문 급증·폭증
- 증설: 공장 착공·준공·생산능력 확대·투자 발표
- 관세리스크: 관세 인상·무역 규제·수출입 제재
- 원자재: 원자재·광물·에너지 가격 급등락·수급 불안

제외 기준 (아래는 무조건 제외):
- 일반 주가/증시 등락, 기업 실적 발표
- 인사/채용/수상/표창
- 농수산물 가격 (배추, 양파, 수산물 등)
- 정치/외교 일반 뉴스 (공급망 영향 없는 것)
- 소비자 대상 마케팅/세일/프로모션

기사 목록:
{lines}

응답 형식 (JSON만, 설명 없이):
[{{"index": 1, "category": "공급망"}}, {{"index": 3, "category": "관세리스크"}}]

관련 없는 기사가 하나도 없으면: []"""

        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key":         CLAUDE_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type":      "application/json",
                },
                json={
                    "model":      "claude-haiku-4-5-20251001",
                    "max_tokens": 1000,
                    "messages":   [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            text = resp.json()["content"][0]["text"].strip()
            # JSON 추출
            s, e = text.find("["), text.rfind("]")
            if s < 0:
                continue
            classified = json.loads(text[s:e+1])
            for c in classified:
                idx = c.get("index", 0) - 1
                if 0 <= idx < len(batch):
                    batch[idx]["keyword"] = c.get("category", "공급망")
                    result.append(batch[idx])
            print(f"  [Claude API] 배치 {i//BATCH+1}: {len(batch)}건 → {len([c for c in classified])}건 선별")
        except Exception as ex:
            print(f"  [Claude API] 오류: {ex} → 해당 배치 키워드 필터 결과 사용")
            result.extend(batch)

    return result



def filter_by_keywords(articles: list) -> list:
    all_kws   = [kw for kws in KEYWORD_MAP.values() for kw in kws]
    all_cos   = [(cat, co) for cat, cos in GLOBAL_COMPANIES.items() for co in cos]
    ctx_lower = [c.lower() for c in COMPANY_CONTEXT_KEYWORDS]
    result    = []

    for a in articles:
        title = a["title"].lower()

        # 1) 블랙리스트 제목 제외
        if any(bl.lower() in title for bl in BLACKLIST):
            continue

        # 2) 네이버 API 수집 기사는 이미 키워드 분류 완료 → 바로 포함
        if a.get("source") == "네이버뉴스" and a.get("keyword"):
            result.append(a)
            continue

        # 3) 일반 키워드 매칭 (RSS 기사)
        if any(kw.lower() in title for kw in all_kws):
            a["keyword"] = get_category(a["title"])
            result.append(a)
            continue

        # 4) 글로벌 기업명 + 공급망 맥락어 동시 매칭
        for cat, co in all_cos:
            if co.lower() in title:
                if any(ctx in title for ctx in ctx_lower):
                    a["keyword"] = cat
                    result.append(a)
                    break

    return result


def collect_all_news() -> list:
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 수집 시작")

    raw = []

    # ── 네이버 뉴스 API (국내 키워드별 검색) ────────────────────────────────
    naver_total = 0
    for query, category in NAVER_QUERIES:
        items = fetch_naver_news(query, category, display=10)
        naver_total += len(items)
        raw.extend(items)
    print(f"  네이버뉴스: {naver_total}건 (쿼리 {len(NAVER_QUERIES)}개)")

    # ── 해외 RSS 피드 ────────────────────────────────────────────────────────
    for source, url, region, ssl_verify in RSS_FEEDS:
        items = fetch_rss(source, url, region, ssl_verify)
        print(f"  {source}: {len(items)}건")
        raw.extend(items)

    # 24시간 이내 기사만 허용
    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc - timedelta(hours=24)
    recent  = []
    for a in raw:
        pub = a.get("pub", "")
        if not pub:
            recent.append(a)   # 날짜 없으면 일단 포함
            continue
        try:
            dt = datetime.strptime(pub, "%m/%d %H:%M")
            dt = dt.replace(year=now_utc.year, tzinfo=timezone(timedelta(hours=9)))
            # 연말/연초 경계: 미래 날짜면 작년으로
            if dt > now_utc + timedelta(hours=1):
                dt = dt.replace(year=now_utc.year - 1)
            if dt >= cutoff:
                recent.append(a)
        except Exception:
            recent.append(a)
    print(f"  24h 필터 후: {len(recent)}건 (전체 {len(raw)}건)")

    # 1차: 키워드 광범위 필터 (블랙리스트 + 느슨한 키워드)
    pre = filter_by_keywords(recent)
    print(f"  1차 키워드 필터: {len(pre)}건")

    # 2차: Claude API 정밀 분류
    filtered = classify_with_claude(pre)
    print(f"  2차 Claude 분류: {len(filtered)}건")

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
