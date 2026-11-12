"""아빠 낚시 도우미 · 자동 예보 조회 버전

실행: pip install -r requirements.txt && streamlit run app.py
배포: GitHub 같은 폴더에 app.py / requirements.txt → Streamlit Community Cloud.
API 키 없이 개인·비상업 용도로 사용하는 Open-Meteo 무료 API를 연결했습니다.
상업용 서비스는 Open-Meteo 유료 계약 및 API 주소/키 설정이 별도로 필요합니다.
지역 좌표는 지역 주변 대표점이며 정확한 낚시 발판 위치가 아닙니다.
파도·바람은 모델 예보입니다. 공식 특보/운항/출입/빈자리 자동 조회는 아닙니다.
실제 출항은 선장에게, 민물 이용 가능 구역은 운영자에게 확인해야 합니다.
체크리스트는 SQLite에 보관되며 서버 재시작/재배포 시 유실될 수 있습니다.
이전 버전의 자유 입력 폼을 없앴습니다. 기존 포인트 기록 테이블은 삭제하지 않습니다.
앱 화면의 QR 생성·공유 기능은 제거했습니다. 일반 앱 주소로 접속하세요.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import html
import math
import os
import sqlite3
from urllib.parse import quote
import uuid

import requests
import streamlit as st

KST = timezone(timedelta(hours=9))

# 바다는 낚싯배 출발항/주변 해역 후보입니다. 항만 전체의 낚시 허용을 뜻하지 않습니다.
# 좌표는 주변 대표 좌표로 Open-Meteo가 격자 중심으로 보정하여 응답합니다.
SEA_DATA = {
    "충남 · 태안 / 안면도": [("안흥항", 36.68,126.13),("신진도항",36.67,126.13),("백사장항",36.59,126.31),("영목항",36.40,126.42),("구매항",36.44,126.43)],
    "충남 · 서산 / 당진": [("삼길포항",37.00,126.45),("대산항 주변 해역",37.03,126.37),("장고항",37.04,126.56),("왜목항",37.05,126.53)],
    "충남 · 보령 / 서천": [("대천항",36.33,126.50),("오천항",36.44,126.51),("무창포항",36.25,126.53),("홍원항",36.15,126.50),("마량항",36.12,126.50)],
    "전북 · 군산 / 부안": [("군산 비응항",35.94,126.52),("군산 선유도 주변 해역",35.81,126.41),("부안 격포항",35.63,126.47),("부안 곰소항",35.59,126.61)],
    "인천 · 영흥 / 강화": [("인천 남항 주변 해역",37.44,126.58),("영흥도 진두항",37.25,126.49),("선재도 주변 해역",37.23,126.53),("강화 외포항",37.70,126.38)],
    "경기 · 대부도 / 화성": [("탄도항",37.19,126.64),("전곡항",37.19,126.65),("궁평항",37.12,126.68)],
    "전남 · 여수 / 고흥": [("여수 국동항",34.72,127.72),("여수 돌산 주변 해역",34.62,127.79),("고흥 녹동항",34.53,127.13)],
    "전남 · 완도 / 목포": [("완도항",34.31,126.76),("진도 서망항",34.36,126.13),("목포 주변 해역",34.76,126.32)],
    "경남 · 통영 / 거제": [("통영 산양읍 주변 해역",34.78,128.38),("거제 지세포항",34.83,128.71),("거제 장승포항",34.86,128.73)],
    "부산 · 기장": [("부산 다대포항",35.05,128.98),("기장 대변항",35.22,129.23)],
    "강원 · 동해안": [("속초항",38.21,128.60),("양양 수산항",38.08,128.66),("강릉 주문진항",37.89,128.83),("동해 묵호항",37.55,129.12)],
    "경북 · 동해안": [("울진 후포항",36.68,129.46),("포항 구룡포항",35.99,129.56),("경주 감포항",35.81,129.51)],
    "제주": [("제주시 도두항",33.51,126.47),("제주 성산포항",33.47,126.93),("서귀포항",33.24,126.57)],
}
# 민물은 관광공사/지자체에 소개된 실제 시설/호수입니다. 수시로 바뀌는 운영/자리/요금은 전화 확인.
# source는 모든 카드에서 확인 가능합니다. 연락처를 찾지 못한 곳은 임의로 채우지 않습니다.
FRESH_DATA = [
    ("충남 · 예산", "예당호 · 허용구역 문의",36.64,126.79,"", "붕어·잉어를 소개하는 호수입니다. 이용할 좌대와 허용 구역을 먼저 확인하세요.","시설별로 주차를 확인하세요", "https://www.yesan.go.kr/prog/trrsrt/tour/sub02_01_05/view.do?trrsrtNo=10"),
    ("충남 · 아산", "봉재낚시터 · 봉재저수지",36.8831,127.0321,"041-531-3196","저수지 연안과 좌대 낚시를 소개하는 곳입니다.","관광 안내에 주차 가능으로 소개돼 있어요", "https://english.visitkorea.or.kr/svc/contents/contentsView.do?vcontsId=192716"),
    ("충남 · 아산", "송악저수지 · 운영구역 문의",36.72,127.00,"","호수 주변 날씨를 확인하는 후보입니다. 실제 낚시 구역은 별도 문의하세요.","주차 위치를 현장에 확인하세요", "https://korean.visitkorea.or.kr/detail/ms_detail.do?cotid=87d58d87-380a-4fa7-a811-460229536c58"),
    ("충남 · 논산", "탑정호 · 허용구역 문의",36.19,127.15,"","논산시 관광 안내에 낚시가 소개된 호수입니다. 이용 가능한 구역을 문의하세요.","시설별로 주차를 확인하세요", "https://seongdong.nonsan.go.kr/tour/html/sub02/020412.html"),
    ("충북 · 진천", "초평집 좌대 · 초평호",36.81057,127.51206,"","붕어·잉어 등 다양한 어종을 소개하는 좌대 시설입니다.","관광 안내에 주차 가능으로 소개돼 있어요", "https://data.visitkorea.or.kr/resource/2769747"),
    ("충북 · 진천", "백곡저수지낚시터",36.88,127.42,"","저수지 낚시터 후보입니다. 운영과 이용 구역을 문의하세요.","주차 위치를 현장에 확인하세요", "https://korean.visitkorea.or.kr/detail/ms_detail.do?cotid=0968faea-73b6-4cac-be01-87c2416b0e55"),
    ("경기 · 안성", "장광낚시터",37.09570,127.42917,"031-672-6677","관광 안내에 붕어·송어와 좌대 시설이 소개돼 있어요.","관광 안내에 주차 가능으로 소개돼 있어요", "https://data.visitkorea.or.kr/page/2753998"),
    ("경기 · 안성", "성은낚시터",37.03,127.15,"031-655-6507","붕어·향어와 방갈로 시설을 소개하는 곳입니다.","주차를 운영자에게 확인하세요", "https://english.visitkorea.or.kr/svc/contents/contentsView.do?vcontsId=188784"),
    ("경기 · 용인", "신원낚시터",37.18610,127.22632,"","저수지와 수상 좌대가 소개된 시설입니다.","관광 안내에 주차 가능으로 소개돼 있어요", "https://data.visitkorea.or.kr/page/2739452"),
    ("경기 · 용인", "한터낚시터",37.25971,127.27650,"","관광 안내에 향어·잉어와 방갈로 시설이 소개돼 있어요.","관광 안내에 주차 가능으로 소개돼 있어요", "https://data.visitkorea.or.kr/page/2741007"),
    ("경기 · 시흥", "금이낚시터",37.39915,126.82951,"031-315-9459","붕어·향어·잉어·메기를 소개하는 민물낚시터입니다.","관광 안내에 주차 가능으로 소개돼 있어요", "https://data.visitkorea.or.kr/page/2751249"),
    ("경기 · 시흥", "왕골낚시터",37.36626,126.85760,"031-480-1330","하우스와 노지 낚시 시설이 소개돼 있어요.","관광 안내에 주차 가능으로 소개돼 있어요", "https://data.visitkorea.or.kr/linkedview/2751367"),
    ("경기 · 이천", "고담낚시터",37.23840,127.46167,"031-634-0877","잔교 좌대와 방갈로를 소개하는 저수지 시설입니다.","관광 안내에 주차 가능으로 소개돼 있어요", "https://data.visitkorea.or.kr/linkedview/2773302"),
    ("경기 · 이천", "각평낚시터",37.23925,127.38495,"031-632-8205","붕어·잉어·향어와 여러 좌대 시설을 소개하는 곳입니다.","관광 안내에 주차 가능으로 소개돼 있어요", "https://data.visitkorea.or.kr/resource/2767699"),
    ("경기 · 연천", "전곡낚시터 · 낚시힐링캠프",38.03709,127.03406,"031-835-8833","메기·장어·송어와 방갈로 시설을 소개하는 곳입니다.","관광 안내에 주차 가능으로 소개돼 있어요", "https://data.visitkorea.or.kr/linkedview/2735334"),
]
SEA = [dict(id=f"s{i}_{j}", kind="sea",region=region,name=name,lat=lat,lon=lon,phone="",source="",feature="선상낚시 출발항 또는 주변 해역 후보입니다. 항만 전체의 낚시 허용을 뜻하지는 않아요.",parking="출발항과 주차를 예약 업체에 확인하세요") for i,(region,places) in enumerate(SEA_DATA.items()) for j,(name,lat,lon) in enumerate(places)]
FRESH = [dict(id=f"f{i}",kind="fresh",region=r,name=n,lat=la,lon=lo,phone=p,feature=f,parking=park,source=s) for i,(r,n,la,lo,p,f,park,s) in enumerate(FRESH_DATA)]
ALL = {p["id"]:p for p in SEA+FRESH}
SEA_ITEMS = ["신분증·예약 정보", "구명조끼·미끄럼 방지 신발", "낚싯대·릴·채비", "펜치·장갑", "멀미약·개인 상비약", "아이스박스·얼음", "물·간식", "휴대폰·보조배터리"]
FRESH_ITEMS = ["낚싯대·받침대", "낚시 의자", "파라솔·고정 장치", "미끼·떡밥·지렁이", "헤드랜턴·여분 배터리", "벌레 기피제", "구명조끼·미끄럼 방지 신발", "물·쓰레기봉투"]


def now():
    return datetime.now(KST)


def day_choices(moment=None):
    moment = moment or now()
    return [(moment.date() + timedelta(days=i)).isoformat() for i in range(7)]


def day_label(value):
    day = datetime.fromisoformat(value).date()
    offset = (day - now().date()).days
    prefix = {0: "오늘", 1: "내일", 2: "모레"}.get(offset, "")
    weekday = "월화수목금토일"[day.weekday()]
    return f"{prefix + ' · ' if prefix else ''}{day.month}월 {day.day}일 ({weekday})"


def hour_label(hour):
    if hour == 0:
        return "오전 12시 · 자정"
    if hour == 12:
        return "오후 12시 · 정오"
    if hour == 24:
        return "밤 12시 · 다음 날 자정"
    return f"{'오전' if hour < 12 else '오후'} {hour if hour < 12 else hour-12}시"


def slots(day, start_hour, end_hour, moment=None):
    """같은 날짜 안에서 시간 단위로 선택. 종료 24시는 다음 날 0시입니다."""
    moment = moment or now()
    if day not in day_choices(moment):
        return ()
    if not (isinstance(start_hour, int) and isinstance(end_hour, int) and 0 <= start_hour < end_hour <= 24):
        return ()
    start = datetime.fromisoformat(day).replace(tzinfo=KST)
    hours = [start + timedelta(hours=i) for i in range(start_hour, end_hour)]
    return tuple(t.strftime("%Y-%m-%dT%H:%M") for t in hours if t >= moment)


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value >= 0


def parse_series(payload, field, times):
    hourly = payload.get("hourly",{})
    stamps, values = hourly.get("time",[]), hourly.get(field,[])
    if len(stamps) != len(values):
        return None
    lookup = dict(zip(stamps,values))
    data = [lookup.get(t) for t in times]
    return data if all(finite(v) for v in data) else None


def http_json(url, params):
    response = requests.get(url,params=params,timeout=(4,10),headers={"User-Agent":"DadFishingHelper/3.0 (personal non-commercial app)"})
    response.raise_for_status()
    result = response.json()
    if not isinstance(result,dict) or result.get("error"):
        raise ValueError("Forecast response unavailable")
    return result


@st.cache_data(ttl=900,max_entries=200,show_spinner=False)
def load_forecast(pid, times):
    """네트워크 오류·결측을 좋은 날씨로 바꾸지 않고 unknown으로 남깁니다."""
    p = ALL[pid]
    base = {"latitude":p["lat"],"longitude":p["lon"],"timezone":"Asia/Seoul","forecast_days":7}
    wind=None; marine=None
    with ThreadPoolExecutor(max_workers=2) as pool:
        wf=pool.submit(http_json,"https://api.open-meteo.com/v1/forecast",dict(base,hourly="wind_speed_10m,wind_gusts_10m,weather_code",wind_speed_unit="ms"))
        mf=pool.submit(http_json,"https://marine-api.open-meteo.com/v1/marine",dict(base,hourly="wave_height",cell_selection="sea")) if p["kind"]=="sea" else None
        try:
            wind=wf.result()
        except (requests.RequestException,ValueError,TypeError):
            pass
        if mf:
            try:
                marine=mf.result()
            except (requests.RequestException,ValueError,TypeError):
                pass
    result={"fetched":now().strftime("%m월 %d일 %H:%M"),"times":times,"wind":None,"gust":None,"wave":None,"codes":None,"complete":False}
    if wind:
        for k,field in [("wind","wind_speed_10m"),("gust","wind_gusts_10m"),("codes","weather_code")]:
            values=parse_series(wind,field,times)
            result[k]=values if k=="codes" else (max(values) if values else None)
    if marine:
        values=parse_series(marine,"wave_height",times)
        result["wave"]=max(values) if values else None
    result["complete"]=all(result[k] is not None for k in (("wind","gust","codes","wave") if p["kind"]=="sea" else ("wind","gust","codes")))
    return result


def level(data, kind):
    """0=앱 주의 기준 미만,1=주의,2=보류,3=확인 불가. 공식 안전 판정 아님."""
    wave,wind,gust=data.get("wave"),data.get("wind"),data.get("gust")
    codes=data.get("codes") or []
    if any(c in (95,96,99,65,67,75,82,86) for c in codes):
        return 2
    if (kind=="sea" and wave is not None and wave>=1.5) or (wind is not None and wind>=8) or (gust is not None and gust>=12):
        return 2
    if not data.get("complete"):
        return 3
    if (kind=="sea" and wave>=1) or wind>=6 or gust>=10 or any(c>=51 for c in codes):
        return 1
    return 0


def score(data,kind):
    if not data.get("complete"):
        return math.inf
    return max(data["wind"]/8,data["gust"]/12,(data["wave"]/1.5 if kind=="sea" else 0))


def distance(a,b):
    la1,la2=math.radians(a["lat"]),math.radians(b["lat"])
    x=math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin(math.radians(b["lon"]-a["lon"])/2)**2
    return 6371*2*math.asin(min(1,math.sqrt(x)))


def alternatives(place, current, times):
    # 같은 낚시 유형의 가까운 4개 지역을 대표 후보 1개씩만 비교합니다.
    # 동네 내항이 바깥 해역보다 무조건 안전하다고 가정하지 않습니다.
    candidates=sorted(SEA if place["kind"]=="sea" else FRESH,key=lambda p:distance(place,p))
    chosen=[]; seen={place["region"]}
    for p in candidates:
        if p["region"] not in seen:
            seen.add(p["region"]); chosen.append(p)
        if len(chosen)==4:
            break
    with ThreadPoolExecutor(max_workers=4) as pool:
        forecasts=list(pool.map(lambda p:load_forecast(p["id"],times),chosen))
    valid=[(p,d) for p,d in zip(chosen,forecasts) if level(d,p["kind"])==0 and score(d,p["kind"])<score(current,place["kind"])]
    return sorted(valid,key=lambda x:(score(x[1],x[0]["kind"]),distance(place,x[0])))[:2]


def e(value):
    return html.escape(str(value))


def carp_svg():
    # 가벼운 인라인 붕어 그림. 외부 이미지나 별도 파일이 필요 없습니다.
    fish='''<g fill="currentColor"><path d="M45 58 Q5 22 6 52 Q-4 77 45 78Z"/><ellipse cx="90" cy="65" rx="57" ry="35"/><path d="M57 37 Q77 -1 115 34 M64 94 Q85 119 109 96"/><path d="M84 65 Q68 88 104 87" fill="#bfded3"/><circle cx="128" cy="53" r="6" fill="#f8fbf6"/><circle cx="130" cy="53" r="3" fill="#123b35"/><path d="M144 70 Q157 72 157 81" fill="none" stroke="currentColor" stroke-width="3"/></g><g fill="none" stroke="#b5d9cb" stroke-width="2"><path d="M55 50q14 13 0 26 M72 42q16 23 0 45 M90 38q16 26 0 52 M108 40q15 24 0 47"/></g>'''
    return f'''<svg class="carp-art" viewBox="0 0 480 190" role="img" aria-label="붕어 세 마리가 헤엄치는 그림입니다"><g transform="translate(82,24) scale(1.1)" style="color:#337865">{fish}</g><g transform="translate(375,85) scale(-.66,.66)" style="color:#6d9f8b">{fish}</g><g transform="translate(46,123) scale(.42)" style="color:#94baa1">{fish}</g><path d="M20 162 Q110 145 210 165 T460 162 M45 176 Q150 162 270 177 T450 176" stroke="#accfc1" fill="none" stroke-width="2"/><circle cx="279" cy="46" r="5" fill="none" stroke="#96bdb0"/><circle cx="294" cy="25" r="3" fill="none" stroke="#96bdb0"/></svg>'''


def styles():
    st.markdown('''<style>
    .block-container{max-width:760px;padding:4.1rem .9rem calc(265px + env(safe-area-inset-bottom));}
    /* 아이콘 폰트를 보존하기 위해 전역 font-family를 덮지 않습니다. */
    h1{font-size:1.85rem!important;line-height:1.4!important;} h2,h3{font-size:1.4rem!important;}
    [data-testid="stMarkdownContainer"] p,[data-testid="stWidgetLabel"] p{font-size:1.14rem;line-height:1.6;}
    [data-testid="stCaptionContainer"] p{font-size:.98rem!important;}
    .stButton button,.stDownloadButton button,[data-testid="stLinkButton"] a{min-height:56px;border-radius:15px;}
    .stButton button p,[data-testid="stLinkButton"] a p{font-size:1.15rem!important;font-weight:650;}
    button[kind="primary"]{background:#23624f;border:1px solid #23624f;color:#fff;}
    input,[data-baseweb="select"]{font-size:19px!important;}
    [data-baseweb="tab"]{min-height:54px;padding:8px;}
    [data-baseweb="tab"] p{font-size:1.12rem!important;font-weight:700;}
    .welcome{background:#f2f7ef;border:1px solid #dce8d9;border-radius:26px;padding:24px 22px 20px;color:#183f35;text-align:center;}
    .welcome .eyebrow{font-size:16px;letter-spacing:2px;font-weight:700;color:#4a7563;}
    .welcome h1{font-size:clamp(26px,6vw,34px)!important;margin:12px 0 8px;color:#183f35;}
    .welcome p{font-size:20px!important;color:#456c5b;line-height:1.7;}
    .carp-art{display:block;width:100%;max-height:185px;margin:auto;}
    .topline{display:flex;align-items:center;justify-content:space-between;background:#edf5ed;border-radius:18px;padding:12px 16px;color:#183f35;margin-bottom:14px;}
    .topline strong{font-size:25px;}.topline span{font-size:14px;color:#51715f;}
    .status{border-radius:18px;padding:18px 20px;margin:12px 0;color:#183f35;background:#eaf3eb;border-left:6px solid #52856c;}
    .status.caution{background:#fff5de;color:#634a13;border-left-color:#ce9b37;}
    .status.stop{background:#fff0ec;color:#803f31;border-left-color:#c1644b;}
    .status.unknown{background:#f0f2f4;color:#414d59;border-left-color:#7b8790;}
    .status .title{font-size:25px;font-weight:800;line-height:1.45;}
    .status .body{font-size:19px;line-height:1.6;margin-top:8px;}
    .numbers{display:flex;gap:10px;margin:10px 0 12px;}
    .number{flex:1;border:1px solid #dce5dc;background:var(--secondary-background-color);border-radius:14px;padding:13px 10px;text-align:center;}
    .number .label{font-size:16px;}.number .value{font-size:27px;font-weight:750;line-height:1.6;}
    .candidate{border:1px solid #ccdcd0;border-radius:16px;padding:16px;margin:8px 0;background:var(--secondary-background-color);}
    .candidate strong{font-size:22px;}.candidate p{font-size:18px!important;margin:6px 0;}
    .st-key-bottom_nav{position:fixed;bottom:0;left:50%;transform:translateX(-50%);width:100%;max-width:760px;box-sizing:border-box;z-index:990;background:var(--background-color,#fff);border-top:1px solid #d4e2d8;border-radius:20px 20px 0 0;padding:10px 12px calc(12px + env(safe-area-inset-bottom));box-shadow:0 -5px 22px rgba(24,63,53,.09);}
    .st-key-bottom_nav [data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important;gap:8px;}
    .st-key-bottom_nav [data-testid="stColumn"]{min-width:0!important;flex:1 1 0!important;}
    .st-key-bottom_nav button{min-height:62px;padding:4px 2px;}
    .st-key-bottom_nav .st-key-back_bottom button{min-height:38px;}
    .st-key-bottom_nav button p{font-size:1rem!important;line-height:1.4;}
    .st-key-bottom_nav button p::first-letter{font-size:1.55rem;}
    .topline{flex-wrap:wrap;gap:8px;}
    @media(max-width:380px){.st-key-bottom_nav button p{font-size:.95rem!important;}.topline strong{font-size:22px;}}
    </style>''',unsafe_allow_html=True)


def selection_key(key):
    return key in ("kind_choice", "bag_kind", "trip_day", "start_hour", "end_hour") or key.startswith(("region_", "place_"))


def save_selections():
    saved = dict(st.session_state.get("saved_selections", {}))
    for key in list(st.session_state):
        if selection_key(key):
            saved[key] = st.session_state[key]
    st.session_state["saved_selections"] = saved
    return dict(saved)


def frame():
    return {"page": st.session_state.get("page", "home"), "selections": save_selections()}


def remember_page():
    history = list(st.session_state.get("page_history", []))
    history.append(frame())
    st.session_state["page_history"] = history[-40:]


def navigate(page):
    if page != st.session_state.get("page", "home"):
        remember_page()
        st.session_state["page"] = page


def go_back():
    history = list(st.session_state.get("page_history", []))
    if not history:
        st.session_state["page"] = "home"
        return
    previous = history.pop()
    st.session_state["page_history"] = history
    st.session_state["page"] = previous["page"]
    saved = previous["selections"]
    st.session_state["saved_selections"] = dict(saved)
    for key in list(st.session_state):
        if selection_key(key) and key not in saved:
            del st.session_state[key]
    for key, value in saved.items():
        st.session_state[key] = value


def restore_selections():
    # 화면을 바꾸며 위젯 상태가 정리되어도 이전 선택을 복원합니다.
    for key, value in st.session_state.get("saved_selections", {}).items():
        st.session_state.setdefault(key, value)


MENU = [
    ("🐟 지역선택", "places"), ("🐠 바다날씨", "sea_weather"),
    ("🐡 민물날씨", "fresh_weather"), ("🐟 다른추천", "compare"),
    ("🐠 주차·지도", "detail"), ("🐡 배예약", "booking"),
    ("🐟 준비물", "checklist"), ("🐠 처음", "home"),
]
PAGE_TITLES = {
    "places": "📍 지역과 장소를 고르세요", "sea_weather": "🌊 바다 날씨",
    "fresh_weather": "🐟 민물 날씨", "compare": "🛡 다른 장소 추천",
    "detail": "🚗 주차·자리·지도·전화", "booking": "🚢 낚싯배 예약",
    "checklist": "📋 준비물 체크리스트",
}


def bottom_navigation():
    current = st.session_state.get("page", "home")
    with st.container(key="bottom_nav"):
        st.button("← 뒤로가기", key="back_bottom", on_click=go_back,
                  disabled=not st.session_state.get("page_history"), use_container_width=True)
        for row in (MENU[:4], MENU[4:]):
            columns = st.columns(4)
            for col, (label, page) in zip(columns, row):
                col.button(label, key="nav_" + page, on_click=navigate, args=(page,),
                           type="primary" if current == page else "secondary", use_container_width=True)


def welcome():
    st.markdown(f'''<div class="welcome"><div class="eyebrow">아빠의 즐거운 낚시를 위해</div>{carp_svg()}<h1>아빠 낚시<br>도우미 앱이에요 🎣</h1><p>아빠, 낚시 가실 때<br>더 간편하고 빠르게<br>제가 도와드릴게요.</p></div>''',unsafe_allow_html=True)
    st.write("")
    st.button("들어가세요 →",type="primary",use_container_width=True,on_click=navigate,args=("places",),key="enter")
    st.caption("🌊 바다 날씨 · 🐟 민물 낚시 · 📋 준비물 체크")
    st.caption("날씨는 참고용 예보예요. 실제 출항과 현장 통제는 출발 전에 확인하세요.")


def verdict(place,data):
    rating=level(data,place["kind"])
    titles=["🟢 예보 수치는 주의 기준보다 낮아요", "🟡 이 시간대에는 주의가 필요해요", "🔴 이 시간대 출조는 미루는 게 좋겠어요", "⚪ 날씨를 확인하지 못했어요"]
    if place["kind"] == "sea":
        caution = "파도·바람 또는 비·눈 예보에 주의가 필요해요. 무리하지 마시고 현장에 문의하세요."
        stop = "강한 파도·바람 또는 강한 비·눈·뇌우 예보 때문에 출조 보류를 권해드려요. 실제 출항 여부는 선장님께 확인하세요."
        low = "낮은 예보 수치가 안전이나 출항을 보장하지는 않아요. 출발 전에 현장 상황을 다시 확인하세요."
    else:
        caution = "바람·돌풍 또는 비·눈 예보에 주의가 필요해요. 물가에서는 특히 조심하세요."
        stop = "강한 바람·돌풍 또는 강한 비·눈·뇌우 예보 때문에 출조 보류를 권해드려요. 수위와 현장 통제도 확인하세요."
        low = "낮은 예보 수치가 물가의 안전을 보장하지는 않아요. 수위와 현장 상황을 다시 확인하세요."
    messages=[low,caution,stop,"예보가 일부 누락되어 판단할 수 없어요. 날씨가 좋다고 생각하지 마시고 공식 예보를 확인하세요."]
    cls=["low","caution","stop","unknown"][rating]
    st.markdown(f'<div class="status {cls}"><div class="title">{e(titles[rating])}</div><div class="body">{e(messages[rating])}</div></div>',unsafe_allow_html=True)
    if place["kind"]=="fresh":
        st.caption("민물은 바다 파도 대신 바람·돌풍·비·눈·뇌우 예보를 확인해요.")
    pairs=[("바람",data.get("wind"),"m/s"),("돌풍",data.get("gust"),"m/s")]
    if place["kind"]=="sea":
        pairs=[("파도",data.get("wave"),"m")]+pairs[:1]
    boxes="".join(f'<div class="number"><div class="label">{label} · 예보 시간대 최댓값</div><div class="value">{value:.1f}<small> {unit}</small></div></div>' if value is not None else f'<div class="number"><div class="label">{label}</div><div class="value">확인하지 못했어요</div></div>' for label,value,unit in pairs)
    st.markdown(f'<div class="numbers">{boxes}</div>',unsafe_allow_html=True)
    a,b=data["times"][0],data["times"][-1]
    start=datetime.fromisoformat(a); end=datetime.fromisoformat(b)+timedelta(hours=1)
    st.caption(f"{start:%m월 %d일 %H시} ~ {end:%m월 %d일 %H시} 예보의 최댓값이에요 · 한국 시간 기준")
    st.caption(f"마지막 조회: {data['fetched']} · 예보는 최대 15분간 저장해 사용해요.")
    return rating


def recommend(place,data,times):
    with st.spinner("다른 지역의 같은 시간대 예보도 비교하고 있어요…"):
        options=alternatives(place,data,times)
    st.subheader("🐟 대신 살펴볼 곳이에요")
    if not options:
        st.info("비교한 다른 지역에서도 충분히 낮은 예보 수치를 확인하지 못했어요. 오늘은 쉬시거나 날짜를 바꾸시는 게 좋겠어요.")
        return
    for p,d in options:
        numbers=(f"파도 {d['wave']:.1f}m · " if p["kind"]=="sea" else "")+f"바람 {d['wind']:.1f}m/s · 돌풍 {d['gust']:.1f}m/s"
        st.markdown(f'<div class="candidate"><strong>📍 {e(p["name"])}</strong><p>{e(p["region"])}</p><p>{e(numbers)}</p><p>선택한 곳보다 예보 수치가 낮아 비교 후보로 추천드려요.</p></div>',unsafe_allow_html=True)
        st.button("이 후보도 자세히 보세요 →",key="rec_"+p["id"],use_container_width=True,on_click=choose_place,args=(p["id"],))
    st.caption("같은 낚시 종류의 가까운 다른 지역 4곳만 비교했어요. 전국 최적지나 안전 보장이 아니에요. 거리·운영·특보·현장 통제는 별도로 확인하세요.")


def choose_place(pid):
    remember_page()
    p=ALL[pid]
    st.session_state["page"]="sea_weather" if p["kind"]=="sea" else "fresh_weather"
    st.session_state["kind_choice"]="🌊 바다" if p["kind"]=="sea" else "🐟 민물"
    st.session_state["region_"+p["kind"]]=p["region"]
    st.session_state["place_"+p["kind"]+"_"+p["region"]]=pid
    save_selections()


def detail(place):
    with st.container():
        st.write(place["feature"])
        st.write("🚗 "+place["parking"])
        st.write("🪑 지금 빈자리는 자동으로 알 수 없어요. 방문 전에 운영자에게 확인하세요.")
        if place["phone"]:
            st.markdown(f'### [📞 운영자에게 전화하세요](tel:{place["phone"]})')
            st.caption("공개 관광 안내에 나온 연락처예요. 번호와 운영 여부가 바뀔 수 있어요.")
        search=quote(place["name"].split(" · ")[0]+" "+place["region"])
        st.link_button("🗺 지도에서 장소를 보세요","https://map.naver.com/p/search/"+search,use_container_width=True)
        if place["source"]:
            st.link_button("📖 원래 관광 안내를 보세요",place["source"],use_container_width=True)
        st.caption("주차·어종·시설은 공개 관광 안내를 요약했어요. 현재 조과·빈자리·허용 구역을 보장하지 않아요. 항만·호수 전체에서 낚시하셔도 된다는 뜻은 아니에요.")


def place_picker(kind=None):
    if kind is None:
        kind_label = st.radio("어디로 낚시 가실까요?", ["🌊 바다", "🐟 민물"], horizontal=True, key="kind_choice")
        kind = "sea" if kind_label.startswith("🌊") else "fresh"
    else:
        st.session_state["kind_choice"] = "🌊 바다" if kind == "sea" else "🐟 민물"
    catalog = SEA if kind == "sea" else FRESH
    regions = list(dict.fromkeys(p["region"] for p in catalog))
    c1, c2 = st.columns(2)
    region = c1.selectbox("지역을 고르세요", regions, key="region_" + kind)
    local = {p["id"]: p for p in catalog if p["region"] == region}
    pid = c2.selectbox("장소를 고르세요", list(local), format_func=lambda k: local[k]["name"], key="place_" + kind + "_" + region)
    save_selections()
    return local[pid]


def time_picker():
    moment = now()
    days = day_choices(moment)
    upcoming = moment.replace(minute=0, second=0, microsecond=0)
    if upcoming < moment:
        upcoming += timedelta(hours=1)
    if st.session_state.get("trip_day") not in days:
        st.session_state["trip_day"] = upcoming.date().isoformat()
    day = st.selectbox("가는 날을 고르세요", days, format_func=day_label, key="trip_day")
    if not isinstance(st.session_state.get("start_hour"), int) or st.session_state["start_hour"] not in range(24):
        st.session_state["start_hour"] = upcoming.hour if day == upcoming.date().isoformat() else 6
    c1, c2 = st.columns(2)
    begin = c1.selectbox("시작 시각", list(range(24)), format_func=hour_label, key="start_hour")
    end_options = list(range(begin + 1, 25))
    if st.session_state.get("end_hour") not in end_options:
        st.session_state["end_hour"] = min(24, begin + 6)
    end = c2.selectbox("종료 시각", end_options, format_func=hour_label, key="end_hour")
    st.caption("한 시간 단위로 골라주세요. 오전 12시는 자정, 오후 12시는 낮 12시예요. 같은 날짜 안에서 고르며 밤 12시에 끝낼 수도 있어요.")
    st.caption("날짜는 오늘부터 7일간 고를 수 있어요. 먼 날짜의 예보는 출발 전에 다시 확인하세요.")
    save_selections()
    times = slots(day, begin, end, moment)
    if not times:
        st.info("이미 지난 시간이거나 정시 예보가 남아 있지 않아요. 더 늦은 시각이나 내일 이후 날짜를 골라주세요.")
    elif datetime.fromisoformat(times[0]).replace(tzinfo=KST).hour != begin:
        st.caption("오늘 이미 지난 정시 예보는 제외하고 남은 시각만 보여드려요.")
    return times


def forecast_for(place, times):
    with st.spinner("선택하신 곳의 날씨를 찾아보고 있어요…"):
        return load_forecast(place["id"], times)


def weather_footer():
    if st.button("🔄 예보를 다시 불러오세요", key="refresh_weather", use_container_width=True):
        load_forecast.clear()
        st.rerun()
    st.link_button("기상청 공식 예보·특보 확인", "https://www.weather.go.kr/", use_container_width=True)
    st.caption("날씨 데이터: [Open-Meteo](https://open-meteo.com/) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)")


def regions_page():
    st.caption("지역과 장소만 골라두세요. 날씨·추천·주차·예약은 아래의 각 물고기 메뉴에서 따로 볼 수 있어요.")
    place = place_picker()
    st.success("선택한 장소: " + place["name"])
    page = "sea_weather" if place["kind"] == "sea" else "fresh_weather"
    st.button("선택한 곳의 날씨 보기 →", key="open_weather", on_click=navigate, args=(page,), use_container_width=True, type="primary")
    st.button("주차·지도·전화 보기 →", key="open_detail", on_click=navigate, args=("detail",), use_container_width=True)


def weather_page(kind):
    place = place_picker(kind)
    times = time_picker()
    if not times:
        return
    data = forecast_for(place, times)
    rating = verdict(place, data)
    if rating in (1, 2):
        st.button("다른 장소를 추천받을게요 →", key="open_compare", on_click=navigate, args=("compare",), use_container_width=True)
    weather_footer()


def compare_page():
    st.caption("선택한 장소와 다른 지역의 같은 시간대 예보를 비교해요. 안전이나 실제 출항을 보장하지는 않아요.")
    place = place_picker()
    times = time_picker()
    if not times:
        return
    data = forecast_for(place, times)
    rating = verdict(place, data)
    if not data["complete"]:
        st.info("예보가 일부 없어 다른 장소와 정확히 비교할 수 없어요. 나중에 다시 확인해 주세요.")
    elif rating in (1, 2):
        recommend(place, data, times)
    else:
        st.info("선택한 곳의 예보 수치가 앱의 주의 기준보다 낮아 따로 대체 장소를 추천하지 않았어요. 현장 상황은 출발 전에 확인하세요.")
    weather_footer()


def place_details_page():
    place = place_picker()
    st.markdown("### " + place["name"])
    detail(place)


def booking_page():
    st.caption("여기는 낚싯배 예약 사이트로 연결하는 메뉴예요. 예약과 출항 여부는 해당 사이트나 선장님께 확인하세요.")
    place = place_picker("sea")
    st.info(place["name"] + "의 배편을 예약 사이트에서 검색해 주세요. 현재 빈자리나 운항 여부를 자동으로 조회하지는 않아요.")
    st.link_button("🚢 물반고기반에서 배 예약", "https://www.moolban.com/", use_container_width=True)
    st.link_button("⚓ 선상24에서 배 예약", "https://www.sunsang24.com/", use_container_width=True)
    st.button("예약 전 바다 날씨 확인 →", key="booking_weather", on_click=navigate, args=("sea_weather",), use_container_width=True)



@contextmanager
def db():
    con=sqlite3.connect(os.environ.get("FISHING_DB_PATH","fishing.sqlite3"),timeout=15)
    try:
        with con:
            con.execute("CREATE TABLE IF NOT EXISTS fishing_checks_v2(trip TEXT NOT NULL,kind TEXT NOT NULL,item INTEGER NOT NULL,done INTEGER NOT NULL,PRIMARY KEY(trip,kind,item))")
            yield con
    finally:
        con.close()


def check_changed(trip,kind,index,key):
    try:
        with db() as con:
            con.execute("INSERT INTO fishing_checks_v2 VALUES(?,?,?,?) ON CONFLICT(trip,kind,item) DO UPDATE SET done=excluded.done",(trip,kind,index,int(st.session_state[key])))
    except sqlite3.Error:
        st.session_state["save_error"]=True


def checklist(trip):
    st.subheader("챙긴 물건만 눌러주세요 🎒")
    label=st.radio("준비물 종류를 고르세요",["바다 준비물","민물 준비물"],horizontal=True,key="bag_kind")
    kind="sea" if label.startswith("바다") else "fresh"
    items=SEA_ITEMS if kind=="sea" else FRESH_ITEMS
    # 새 버전 아이템 순서가 달라 기존 체크와 섞이지 않게 저장 kind를 분리합니다.
    storage_kind="auto3_"+kind
    try:
        with db() as con:
            done={r[0]:bool(r[1]) for r in con.execute("SELECT item,done FROM fishing_checks_v2 WHERE trip=? AND kind=?",(trip,storage_kind))}
    except sqlite3.Error:
        done={}
        st.warning("서버에 체크를 저장하지 못했어요. 화면 체크는 가능하지만 다시 접속하면 사라질 수 있어요.")
    count=sum(bool(st.session_state.get(f"bag_{trip}_{kind}_{i}",done.get(i,False))) for i in range(len(items)))
    st.progress(count/len(items),text=f"{len(items)}개 중 {count}개 챙기셨어요!")
    for i,item in enumerate(items):
        key=f"bag_{trip}_{kind}_{i}"
        st.session_state.setdefault(key,done.get(i,False))
        st.checkbox(item+" 챙기셨나요?",key=key,on_change=check_changed,args=(trip,storage_kind,i,key))
    if st.session_state.pop("save_error",False):
        st.warning("체크를 저장하지 못했어요. 잠시 후 다시 확인해 주세요.")
    st.caption("멀미약과 벌레 기피제는 제품 설명대로 사용하세요. 강풍에는 파라솔을 펴지 마세요. 체크는 서버에 저장돼요. 재시작·재배포 시 기록이 사라질 수 있어요.")


def main():
    st.set_page_config(page_title="아빠 낚시 도우미",page_icon="🐟",layout="centered",initial_sidebar_state="collapsed")
    styles()
    raw=st.query_params.get("trip","")
    try:
        trip=str(uuid.UUID(raw))
    except (ValueError,TypeError,AttributeError):
        trip=str(uuid.uuid4());st.query_params["trip"]=trip
    st.session_state.setdefault("page", "home")
    restore_selections()
    if st.session_state["page"] == "home":
        welcome()
    else:
        st.button("← 뒤로가기", key="back_top", on_click=go_back, use_container_width=True)
        st.markdown('<div class="topline"><strong>🐟 아빠 낚시 도우미</strong><span>오늘도 안전하게 다녀오세요</span></div>', unsafe_allow_html=True)
        page = st.session_state["page"]
        st.subheader(PAGE_TITLES.get(page, "낚시 도우미"))
        if page == "places":
            regions_page()
        elif page == "sea_weather":
            weather_page("sea")
        elif page == "fresh_weather":
            weather_page("fresh")
        elif page == "compare":
            compare_page()
        elif page == "detail":
            place_details_page()
        elif page == "booking":
            booking_page()
        elif page == "checklist":
            checklist(trip)
    save_selections()
    bottom_navigation()


if __name__=="__main__":
    main()
