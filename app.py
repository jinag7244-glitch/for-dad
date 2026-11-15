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
홈은 선택한 장소 카드·사진 한 장과 짧은 바로가기만 표시하며 예보 API를 호출하지 않습니다.
바다·민물 예보의 장소/시간 위젯은 서로 다른 키를 사용합니다.
사진은 확인된 관광 사진 URL 또는 같은 장소의 위키미디어 공개 사진만 사용합니다.
원본을 불러오지 못하면 다른 장소 사진으로 대신하지 않고 확인 불가를 표시합니다.
바다 비교는 지역별 대표 후보 13곳과 현재 장소만 비교하며 안전을 보장하지 않습니다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import html
import base64
from html.parser import HTMLParser
from io import BytesIO
import re
from urllib.parse import quote, urlparse
import math
import os
import sqlite3
import uuid

import requests
import streamlit as st
from PIL import Image, ImageOps, UnidentifiedImageError

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
# 기존 관광 안내 출처를 유지합니다. 사진 출처는 별도 PHOTO_SOURCES에서 확인합니다.
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
SEA_GROUPS = [
    ('기본 준비물', ['신분증·예약 정보', '구명조끼·미끄럼 방지 신발', '낚싯대·릴·채비', '펜치·장갑', '멀미약·개인 상비약', '아이스박스·얼음', '물·간식', '휴대폰·보조배터리']),
    ('채비·미끼', ['원줄·목줄 여분', '예비 바늘', '봉돌·싱커', '스냅·도래', '대상어용 미끼·루어', '에기·지그 등 예비 채비', '가위·라인커터', '뜰채·물고기 집게']),
    ('의류·안전', ['모자', '편광 선글라스', '자외선 차단제', '방수 재킷·우의', '여벌 옷·양말', '미끄럼 방지 장화', '구급함·밴드', '호루라기·안전등']),
    ('보관·관리', ['낚싯대 가방', '채비통·소품 케이스', '미끼통·두레박', '수건·물티슈', '지퍼백·방수팩', '휴대용 의자 · 허용 시', '헤드랜턴·여분 건전지', '쓰레기봉투']),
    ('식사·이동 확인', ['도시락·식기', '보온병', '계절용 핫팩·쿨토시', '차량 열쇠·지갑', '현금·결제수단', '충전 케이블', '예약처·승선 장소 확인', '기상특보·출항 여부 확인']),
]
SEA_ITEMS = [item for _, items in SEA_GROUPS for item in items]
FRESH_GROUPS = [
    ('기본 준비물', ['낚싯대·받침대', '낚시 의자', '파라솔·고정 장치', '미끼·떡밥·지렁이', '헤드랜턴·여분 배터리', '벌레 기피제', '구명조끼·미끄럼 방지 신발', '물·쓰레기봉투']),
    ('채비·미끼', ['찌·케미', '바늘·목줄 여분', '원줄 여분', '봉돌·편납', '도래·스토퍼', '떡밥 그릇·계량컵', '가위·바늘빼기', '뜰채·살림망 · 허용 시']),
    ('의류·안전', ['모자', '편광 선글라스', '자외선 차단제', '우의·방수 재킷', '여벌 옷·양말', '장갑·미끄럼 방지 장화', '구급함·개인 상비약', '호루라기·휴대 안전등']),
    ('자리·보관', ['낚싯대 가방', '채비통·소품 케이스', '미끼통·물통', '방수포·자리 매트', '좌대·발판 · 허용 시', '수건·물티슈', '지퍼백·방수팩', '보냉가방·얼음']),
    ('식사·이동 확인', ['도시락·식기', '보온병', '휴대폰·보조배터리', '충전 케이블', '계절용 핫팩·쿨토시', '차량 열쇠·지갑', '이용요금·결제수단', '허용구역·운영 여부 확인']),
]
FRESH_ITEMS = [item for _, items in FRESH_GROUPS for item in items]


# 장소별 대표 사진: 한국관광공사 관광 데이터의 원본 URL과 확인한 자료.
PHOTO_SOURCES = {
    's8_1': ('https://tong.visitkorea.or.kr/cms/resource/94/3519494_image2_1.jpg', 'https://forestplanet.com/tour/611263'),
    's0_0': ('https://tong.visitkorea.or.kr/cms/resource/61/3343461_image2_1.jpg', 'https://forestplanet.com/tour/599559'),
    's0_1': ('https://tong.visitkorea.or.kr/cms/resource/23/3340823_image2_1.jpg', 'https://forestplanet.com/tour/597743'),
    's0_2': ('https://tong.visitkorea.or.kr/cms/resource/61/3340561_image2_1.jpg', 'https://forestplanet.com/tour/589061'),
    's1_0': ('https://tong.visitkorea.or.kr/cms/resource/47/3531447_image2_1.jpg', 'https://forestplanet.com/tour/592555'),
    's1_2': ('https://tong.visitkorea.or.kr/cms/resource/33/3567533_image2_1.jpg', 'https://forestplanet.com/tour/580341'),
    's2_0': ('https://tong.visitkorea.or.kr/cms/resource/63/3334163_image2_1.JPG', 'https://forestplanet.com/tour/580976'),
    's2_1': ('https://tong.visitkorea.or.kr/cms/resource/03/3560003_image2_1.jpg', 'https://forestplanet.com/tour/602872'),
    's2_2': ('https://tong.visitkorea.or.kr/cms/resource/41/3349941_image2_1.JPG', 'https://forestplanet.com/tour/587281'),
    's3_0': ('https://tong.visitkorea.or.kr/cms/resource/54/4039054_image2_1.jpg', 'https://forestplanet.com/tour/591589'),
    's3_2': ('https://tong.visitkorea.or.kr/cms/resource/63/3542863_image2_1.jpg', 'https://forestplanet.com/tour/573406'),
    's3_3': ('https://tong.visitkorea.or.kr/cms/resource/93/3376493_image2_1.JPG', 'https://forestplanet.com/tour/574701'),
    's4_1': ('https://tong.visitkorea.or.kr/cms/resource/89/3524989_image2_1.jpg', 'https://forestplanet.com/tour/611533'),
    'f1': ('https://tong.visitkorea.or.kr/cms/resource/20/3106920_image2_1.JPG', 'https://forestplanet.com/tour/590413'),
    'f2': ('https://tong.visitkorea.or.kr/cms/resource/78/3048878_image2_1.jpg', 'https://forestplanet.com/tour/595657'),
    'f4': ('https://tong.visitkorea.or.kr/cms/resource/18/2769818_image2_1.jpg', 'https://forestplanet.com/tour/613165'),
    'f5': ('https://tong.visitkorea.or.kr/cms/resource/87/3064587_image2_1.JPG', 'https://forestplanet.com/tour/588942'),
    'f6': ('https://tong.visitkorea.or.kr/cms/resource/33/2764233_image2_1.png', 'https://forestplanet.com/tour/608735'),
    'f7': ('https://tong.visitkorea.or.kr/cms/resource/88/2667488_image2_1.jpg', 'https://forestplanet.com/tour/594602'),
    'f8': ('https://tong.visitkorea.or.kr/cms/resource/37/2775337_image2_1.jpg', 'https://forestplanet.com/tour/597701'),
    'f9': ('https://tong.visitkorea.or.kr/cms/resource/11/2775211_image2_1.jpg', 'https://forestplanet.com/tour/619214'),
    'f10': ('https://tong.visitkorea.or.kr/cms/resource/23/2761323_image2_1.jpg', 'https://forestplanet.com/tour/576743'),
    'f11': ('https://tong.visitkorea.or.kr/cms/resource/15/2763315_image2_1.JPG', 'https://forestplanet.com/tour/604827'),
    'f12': ('https://tong.visitkorea.or.kr/cms/resource/66/2773366_image2_1.jpg', 'https://forestplanet.com/tour/573963'),
    'f13': ('https://tong.visitkorea.or.kr/cms/resource/18/2767818_image2_1.jpg', 'https://forestplanet.com/tour/572176'),
}
# PageImages API에서 확인한 실제 파일 이름. 공개 이용조건/저작자 메타데이터를 별도 조회합니다.
WIKI_PHOTO_FILES = {
    "s0_0": "Port_of_Anheung,_at_Taean.jpg",
    "s1_0": "제9경_삼길포항.jpg",
    "s1_1": "서산_대산항_전경.jpg",
    "s2_0": "대천연안여객선터미널.jpg",
    "s3_2": "Gyeokpo_Port_13-03902.JPG",
    "s5_2": "Gungpyeong_Port.jpg",
    "s6_2": "Nokdong_Coastal_Ferry_Terminal_DEC2019.jpg",
    "s7_2": "Mokpo_Harbour_festival_Samhakdo_in_2015_(7).jpg",
    "s9_1": "Daebyeon_Port_3.jpg",
    "s10_2": "Jumunjin_Harbor_20220501_003.jpg",
    "s11_2": "Korea-Gyeongju-Gampo.port-01.jpg",
    "s12_0": "제주시_도두항_03.jpg",
}
PHOTO_TITLES = {
    "s0_1": "신진항", "s3_0": "비응항", "s3_1": "선유도 (군산시)",
    "s3_2": "격포항", "s3_3": "곰소항", "s4_0": "인천항",
    "s4_1": "진두항", "s4_2": "선재도", "s4_3": "외포항 (강화군)",
    "s6_0": "국동항", "s6_1": "돌산도", "s6_2": "녹동항",
    "s7_1": "서망항", "s7_2": "목포항", "s8_0": "산양읍",
    "s8_1": "지세포항", "s8_2": "장승포항", "s9_0": "다대포항",
    "s9_1": "대변항", "s10_1": "수산항 (양양군)",
    "s10_2": "주문진항", "s10_3": "묵호항", "s11_0": "후포항",
    "s11_1": "구룡포항", "s11_2": "감포항", "s12_0": "도두항",
    "s12_1": "성산항", "f0": "예당저수지", "f3": "탑정저수지",
    "f4": "초평저수지", "f5": "백곡저수지",
}
# 이미 사진이 확인된 대표항을 우선하되, 후보는 사진이 아니라 예보 수치로 순위를 매깁니다.
SEA_REPRESENTATIVES = [
    "s0_0", "s1_0", "s2_0", "s3_0", "s4_1", "s5_2", "s6_2",
    "s7_2", "s8_1", "s9_1", "s10_2", "s11_2", "s12_0",
]


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


def e(value):
    return html.escape(str(value))


def carp_svg():
    # 가벼운 인라인 붕어 그림. 외부 이미지나 별도 파일이 필요 없습니다.
    fish='''<g fill="currentColor"><path d="M45 58 Q5 22 6 52 Q-4 77 45 78Z"/><ellipse cx="90" cy="65" rx="57" ry="35"/><path d="M57 37 Q77 -1 115 34 M64 94 Q85 119 109 96"/><path d="M84 65 Q68 88 104 87" fill="#bfded3"/><circle cx="128" cy="53" r="6" fill="#f8fbf6"/><circle cx="130" cy="53" r="3" fill="#123b35"/><path d="M144 70 Q157 72 157 81" fill="none" stroke="currentColor" stroke-width="3"/></g><g fill="none" stroke="#b5d9cb" stroke-width="2"><path d="M55 50q14 13 0 26 M72 42q16 23 0 45 M90 38q16 26 0 52 M108 40q15 24 0 47"/></g>'''
    return f'''<svg class="carp-art" viewBox="0 0 480 190" role="img" aria-label="붕어 세 마리가 헤엄치는 그림입니다"><g transform="translate(82,24) scale(1.1)" style="color:#337865">{fish}</g><g transform="translate(375,85) scale(-.66,.66)" style="color:#6d9f8b">{fish}</g><g transform="translate(46,123) scale(.42)" style="color:#94baa1">{fish}</g><path d="M20 162 Q110 145 210 165 T460 162 M45 176 Q150 162 270 177 T450 176" stroke="#accfc1" fill="none" stroke-width="2"/><circle cx="279" cy="46" r="5" fill="none" stroke="#96bdb0"/><circle cx="294" cy="25" r="3" fill="none" stroke="#96bdb0"/></svg>'''


def styles():
    st.markdown('''<style>
    /* 모바일 앱 화면: 물빛 배경 + 사진 카드 + 작은 물고기 탭바. */
    [data-testid="stAppViewContainer"]{background:#f5f7f6;}
    .block-container{box-sizing:border-box!important;width:100%!important;max-width:440px!important;padding:3.1rem 16px calc(102px + env(safe-area-inset-bottom))!important;}
    [data-testid="stVerticalBlock"]{min-width:0;}
    [data-testid="stMarkdownContainer"] p,[data-testid="stWidgetLabel"] p{font-size:18px;line-height:1.55;overflow-wrap:anywhere;color:#173c35;}
    [data-testid="stCaptionContainer"] p{font-size:14px!important;line-height:1.5!important;color:#56736a;}
    h1{font-size:28px!important;line-height:1.3!important;color:#173c35;}
    h2,h3{font-size:22px!important;line-height:1.4!important;overflow-wrap:anywhere;color:#173c35;}
    .stButton button,.stDownloadButton button,[data-testid="stLinkButton"] a{min-height:48px;border-radius:14px;max-width:100%;}
    .stButton button p,[data-testid="stLinkButton"] a p{font-size:18px!important;font-weight:650;white-space:normal;}
    button[kind="primary"],button[data-testid="stBaseButton-primary"]{background:#155e53;border-color:#155e53;color:#fff;}
    button[kind="primary"] p,button[data-testid="stBaseButton-primary"] p{color:#fff;}
    [data-baseweb="select"]{min-width:0;width:100%;font-size:18px!important;}
    [data-baseweb="select"]>div{min-height:48px;background:#fff!important;color:#173c35!important;border-color:#d4e2da!important;}
    input{font-size:18px!important;}
    [data-testid="stCheckbox"]{padding:4px 0;}
    [data-testid="stCheckbox"] label{min-height:44px;align-items:center;}
    [data-testid="stCheckbox"] label p{font-size:18px!important;line-height:1.5!important;}
    [data-testid="stExpander"]{background:#fff;border-radius:16px;}
    [data-testid="stExpander"] summary{min-height:48px;}
    [data-testid="stExpander"] summary p{font-size:17px!important;}
    [data-testid="stRadio"] label{min-height:40px;}
    .block-container:has(.st-key-welcome_screen){padding-bottom:max(1.25rem,env(safe-area-inset-bottom))!important;}
    .st-key-welcome_screen{min-height:calc(100svh - 6rem);display:flex;flex-direction:column;justify-content:center;}
    .st-key-welcome_screen [data-testid="stVerticalBlock"]{gap:14px;}
    .welcome{box-sizing:border-box;width:100%;background:#edf5ee;border:1px solid #dce8d9;border-radius:24px;padding:22px 18px;color:#173c35;text-align:center;}
    .welcome h1{font-size:clamp(29px,7.4vw,36px)!important;line-height:1.25!important;letter-spacing:-.035em;margin:12px 0 16px!important;padding:0!important;color:#173c35;}
    .welcome-title-line{display:block;white-space:nowrap;}
    .welcome .welcome-copy{font-size:clamp(16px,4.3vw,18px)!important;color:#456c5b;line-height:1.7!important;margin:0!important;word-break:keep-all;}
    .welcome-copy span{display:block;}
    .welcome-features{display:flex;justify-content:center;align-items:center;flex-wrap:wrap;gap:8px;margin-top:18px;color:#365d4c;font-size:14px;line-height:1.6;}
    .welcome-features span+span::before{content:"·";margin-right:8px;color:#6b8576;}
    .carp-art{display:block;width:100%;max-width:280px;height:auto;margin:0 auto;}
    .st-key-enter button{min-height:56px;}
    .st-key-enter button p{font-size:20px!important;}
    .st-key-back_top button{min-height:44px!important;padding:6px 0!important;border:0!important;background:transparent!important;color:#46685c!important;}
    .st-key-back_top button p{font-size:15px!important;color:#46685c!important;}
    .topline{color:#155e53;font-size:15px;font-weight:700;margin-bottom:4px;}
    .home-head{display:flex;align-items:center;gap:10px;margin:2px 0 8px;}
    .home-mark{display:flex;align-items:center;justify-content:center;width:38px;height:38px;background:#155e53;border-radius:14px;color:#fff;font-size:22px;}
    .home-brand{font-size:16px;font-weight:750;color:#155e53;}
    .home-greeting{font-size:25px;font-weight:800;letter-spacing:-.04em;color:#173c35;line-height:1.4;margin:8px 0 18px;word-break:keep-all;}
    .place-card{background:#fff;border-radius:24px;box-shadow:0 8px 24px rgba(24,67,51,.07);border:1px solid #e2ebe6;overflow:hidden;margin:0 0 8px;}
    .place-card-meta{padding:18px 18px 16px;}
    .place-label-row{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px;}
    .place-label{color:#155e53;background:#e4f3eb;font-size:14px;font-weight:750;padding:5px 10px;border-radius:9px;line-height:1.5;}
    .place-kind{font-size:13px;color:#56736a;font-weight:650;}
    .place-name{font-size:29px!important;font-weight:850!important;color:#173c35!important;line-height:1.3!important;letter-spacing:-.035em;word-break:keep-all;overflow-wrap:anywhere;margin:0!important;padding:0!important;}
    .place-region{font-size:14px;color:#56736a;margin-top:7px;line-height:1.5;overflow-wrap:anywhere;}
    .photo-stage{position:relative;}
    .photo-stage img{width:100%;display:block;aspect-ratio:4/3;max-height:40svh;object-fit:cover;background:#edf4f1;}
    .photo-missing-small{display:flex;align-items:center;gap:12px;min-height:76px;padding:16px 18px;background:#edf4f1;color:#456459;box-sizing:border-box;}
    .photo-missing-small .photo-empty-icon{font-size:28px;line-height:1;}
    .photo-missing-small strong{display:block;font-size:15px;font-weight:650;line-height:1.5;}
    .photo-credit{position:absolute;right:10px;bottom:10px;max-width:calc(100% - 20px);text-align:right;font-size:12px;line-height:1.5;color:#fff;}
    .photo-credit summary{display:inline-flex;align-items:center;justify-content:center;min-width:44px;min-height:36px;padding:0 10px;background:rgba(15,42,34,.87);border-radius:10px;cursor:pointer;list-style:none;font-size:12px;font-weight:600;}
    .photo-credit summary::-webkit-details-marker{display:none;}
    .photo-credit[open]{left:10px;max-width:none;}
    .photo-credit[open] .photo-credit-body{margin-top:4px;padding:10px;background:#173c35;border-radius:10px;color:#fff;font-size:12px;line-height:1.6;}
    .photo-credit a{color:#fff!important;text-decoration:underline;}
    .photo-credit small{display:block;margin-top:4px;font-size:11px;color:#e3f1e8;}
    .home-shortcuts-title{font-size:14px;font-weight:700;color:#56736a;margin:12px 0 2px;}
    .st-key-home_shortcuts [data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important;gap:8px!important;}
    .st-key-home_shortcuts [data-testid="stColumn"]{min-width:0!important;flex:1 1 0!important;width:auto!important;}
    .st-key-home_shortcuts button{width:100%;min-height:78px!important;display:flex!important;flex-direction:column!important;align-items:center;justify-content:center;gap:6px!important;background:#fff!important;border:1px solid #dce8e0!important;border-radius:18px!important;box-shadow:0 3px 10px rgba(24,67,51,.025);padding:10px 3px!important;}
    .st-key-home_shortcuts button::before{font-size:22px;line-height:1;display:block;color:#155e53;}
    .st-key-home_change button::before{content:"⌖";}
    .st-key-home_weather button::before{content:"☀";}
    .st-key-home_bag button::before{content:"✓";}
    .st-key-home_shortcuts button p{font-size:14px!important;color:#173c35!important;font-weight:700!important;white-space:nowrap;}
    .weather-range{font-size:14px;font-weight:650;color:#4d6c60;background:#eaf2ed;display:inline-block;padding:6px 10px;border-radius:10px;margin:0 0 8px;line-height:1.5;}
    .weather-info{font-size:12px;line-height:1.5;color:#56736a;margin:4px 0 10px;}
    .weather-info summary{min-height:36px;display:inline-flex;align-items:center;cursor:pointer;list-style:none;font-size:12px;}
    .weather-info summary::-webkit-details-marker{display:none;}
    .weather-info div{background:#fff;border:1px solid #dce8e0;border-radius:12px;padding:12px;color:#46665b;font-size:13px;line-height:1.7;margin:4px 0;}
    .weather-info a{color:#155e53;text-decoration:underline;}
    .status{border-radius:18px;padding:15px 16px;margin:6px 0 10px;color:#173c35;background:#e4f3eb;overflow-wrap:anywhere;}
    .status.caution{background:#fff2d7;color:#654b12;}
    .status.stop{background:#ffebe5;color:#843e2e;}
    .status.unknown{background:#e8edf0;color:#3b505b;}
    .status .title{font-size:21px;font-weight:800;line-height:1.45;}
    .status .body{font-size:15px;line-height:1.6;margin-top:6px;}
    .numbers{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:8px 0;}
    .number:last-child:nth-child(3){grid-column:1/-1;}
    .number{min-width:0;border:1px solid #e1ebe5;background:#fff;border-radius:16px;padding:12px 8px;text-align:center;overflow-wrap:anywhere;}
    .number .label{font-size:14px;line-height:1.5;color:#56736a;}.number .value{font-size:26px;font-weight:750;line-height:1.6;color:#173c35;}.number .value small{font-size:16px;}
    .sea-suggest-title{font-size:20px;font-weight:800;color:#173c35;line-height:1.5;margin:18px 0 2px;}
    .sea-suggest-note{font-size:13px;color:#56736a;line-height:1.6;margin-bottom:10px;}
    .sea-empty{font-size:15px;padding:14px 16px;color:#4a6359;background:#edf2ee;border-radius:14px;line-height:1.6;margin-top:10px;}
    .st-key-bottom_nav{position:fixed;bottom:calc(8px + env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);width:calc(100% - 16px);max-width:424px;box-sizing:border-box;z-index:990;background:#fff;border:1px solid #e0eae4;border-radius:22px;padding:6px 3px;box-shadow:0 4px 22px rgba(24,67,51,.10);}
    .st-key-bottom_nav [data-testid="stVerticalBlock"]{gap:0!important;}
    .st-key-bottom_nav [data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important;gap:0!important;justify-content:space-between;}
    .st-key-bottom_nav [data-testid="stColumn"]{min-width:44px!important;max-width:76px!important;flex:1 1 0!important;width:auto!important;}
    .st-key-bottom_nav button{display:flex!important;flex-direction:column!important;justify-content:center;align-items:center;gap:5px!important;width:100%;height:54px;min-height:54px!important;padding:4px 0!important;border:0!important;border-radius:15px!important;background:transparent!important;box-shadow:none!important;color:#657a70!important;}
    .st-key-bottom_nav button::before{content:"";display:block;flex-shrink:0;width:21px;height:20px;background:currentColor;mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 28 24'%3E%3Cpath d='M8 9 2 5v14l6-4c3 7 15 8 18-3C23 1 11 2 8 9Zm13-1a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Z'/%3E%3C/svg%3E") center/contain no-repeat;-webkit-mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 28 24'%3E%3Cpath d='M8 9 2 5v14l6-4c3 7 15 8 18-3C23 1 11 2 8 9Zm13-1a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Z'/%3E%3C/svg%3E") center/contain no-repeat;}
    .st-key-bottom_nav button[kind="primary"],.st-key-bottom_nav button[data-testid="stBaseButton-primary"]{color:#155e53!important;background:#edf6f0!important;}
    .st-key-bottom_nav button p{font-size:12px!important;line-height:1.2!important;font-weight:600!important;color:inherit!important;white-space:nowrap;margin:0!important;}
    .st-key-bottom_nav button:focus-visible{outline:2px solid #155e53!important;outline-offset:-2px;}
    .photo-credit summary:focus-visible,.weather-info summary:focus-visible{outline:2px solid #155e53;outline-offset:2px;}
    @media(max-width:350px){.block-container{padding-left:12px!important;padding-right:12px!important;}.place-card-meta{padding:16px 14px 14px;}.place-name{font-size:26px!important;}.home-greeting{font-size:23px;}.status .title{font-size:20px;}}
    @media(max-height:650px){.welcome{padding-top:16px;padding-bottom:18px;}.carp-art{max-width:220px;}.welcome h1{margin:6px 0 12px!important;}.photo-stage img{max-height:34svh;}}
    </style>''', unsafe_allow_html=True)


def selection_key(key):
    return key in ("bag_kind", "bag_filter") or key.startswith(("sea_day", "sea_start", "sea_end", "fresh_day", "fresh_start", "fresh_end"))


def save_selections():
    saved = dict(st.session_state.get("saved_selections", {}))
    for key in list(st.session_state):
        if selection_key(key):
            saved[key] = st.session_state[key]
    st.session_state["saved_selections"] = saved
    return dict(saved)


def frame():
    return {"page": st.session_state.get("page", "welcome")}


def remember_page():
    history = list(st.session_state.get("page_history", []))
    history.append(frame())
    st.session_state["page_history"] = history[-40:]


def navigate(page):
    allowed = {target for _, target in MENU}
    if page not in allowed:
        return
    current = st.session_state.get("page", "welcome")
    if page != current:
        if current in allowed:
            remember_page()
        else:
            save_selections()
            st.session_state["page_history"] = []
        st.session_state["page"] = page


def go_back():
    allowed = {target for _, target in MENU}
    history = [item for item in st.session_state.get("page_history", []) if item.get("page") in allowed]
    st.session_state["page_history"] = history
    st.session_state["page"] = history.pop()["page"] if history else "dashboard"
    # 뒤로가기는 화면만 이동하며 선택한 장소·시간을 되돌리지 않습니다.


def restore_selections():
    # 화면을 바꾸며 위젯 상태가 정리되어도 이전 선택을 복원합니다.
    for key, value in st.session_state.get("saved_selections", {}).items():
        st.session_state.setdefault(key, value)


MENU = [
    ("홈", "dashboard"), ("지역", "places"),
    ("바다", "sea_weather"), ("민물", "fresh_weather"),
    ("예약", "booking"), ("준비물", "checklist"),
]
PAGE_TITLES = {
    "places": "📍 지역 고르기", "sea_weather": "🌊 바다 날씨",
    "fresh_weather": "🐟 민물 날씨", "booking": "🚢 낚싯배 예약",
    "checklist": "📋 준비물",
}


def bottom_navigation():
    current = st.session_state.get("page", "dashboard")
    hints = {"dashboard":"선택한 장소와 사진", "places":"지역 선택", "sea_weather":"바다 파도·바람", "fresh_weather":"민물 날씨", "booking":"낚싯배 예약", "checklist":"준비물 체크"}
    with st.container(key="bottom_nav"):
        columns = st.columns(len(MENU), gap="small")
        for col, (label, page) in zip(columns, MENU):
            col.button(label, key="nav_" + page, on_click=navigate, args=(page,), help=hints[page], type="primary" if current == page else "secondary", use_container_width=True)


def welcome():
    with st.container(key="welcome_screen"):
        st.markdown(f'''<section class="welcome" aria-label="아빠 낚시 도우미 시작 화면">{carp_svg()}<h1><span class="welcome-title-line">아빠 낚시</span><span class="welcome-title-line">도우미</span></h1><p class="welcome-copy"><span>낚시 가실 때, 더 간편하게.</span><span>제가 옆에서 도와드릴게요.</span></p><div class="welcome-features"><span>날씨 확인</span><span>배 예약</span><span>준비물</span></div></section>''',unsafe_allow_html=True)
        st.button("들어가기 →",type="primary",use_container_width=True,on_click=navigate,args=("dashboard",),key="enter")


def verdict(place, data):
    rating = level(data, place["kind"])
    titles = ["🟢 예보 수치가 낮아요", "🟡 주의가 필요해요", "🔴 출조를 미뤄주세요", "⚪ 예보 확인이 필요해요"]
    if rating == 0:
        message = "안전 보장은 아니에요. 현장 상황을 확인해 주세요."
    elif rating == 1:
        message = "바람·비 등 변화에 주의하고 무리하지 마세요."
    elif rating == 2:
        message = "강한 바람·파도 또는 궂은 날씨 예보가 있어요." if place["kind"] == "sea" else "강풍이나 궂은 날씨 예보가 있어요. 물가 접근을 피하세요."
    else:
        message = "예보가 부족해 판단할 수 없어요. 공식 예보를 확인해 주세요."
    css = ["low", "caution", "stop", "unknown"][rating]
    st.markdown(f'<div class="status {css}"><div class="title">{e(titles[rating])}</div><div class="body">{e(message)}</div></div>', unsafe_allow_html=True)
    pairs = [("바람", data.get("wind"), "m/s"), ("돌풍", data.get("gust"), "m/s")]
    if place["kind"] == "sea":
        pairs.insert(0, ("파도", data.get("wave"), "m"))
    boxes = []
    for label, value, unit in pairs:
        number = f'{value:.1f}<small> {unit}</small>' if finite(value) else '확인 불가'
        boxes.append(f'<div class="number"><div class="label">{label} · 최대</div><div class="value">{number}</div></div>')
    st.markdown('<div class="numbers">'+''.join(boxes)+'</div>', unsafe_allow_html=True)
    return rating


def place_picker(kind=None):
    role = kind or "region"
    if kind is None:
        current_kind = selected_place()["kind"]
        kind_labels = {"sea": "🌊 바다", "fresh": "🐟 민물"}
        st.session_state["region_kind"] = kind_labels[current_kind]
        label = st.radio("어디로 가실까요?", list(kind_labels.values()), horizontal=True, key="region_kind", on_change=region_kind_changed)
        kind = "sea" if label.startswith("🌊") else "fresh"
    place = selected_place(kind)
    catalog = SEA if kind == "sea" else FRESH
    regions = list(dict.fromkeys(p["region"] for p in catalog))
    region_key = "pick_" + role + "_region"
    st.session_state[region_key] = place["region"]
    region = st.selectbox("지역", regions, key=region_key, on_change=region_changed, args=(kind, region_key))
    local = {p["id"]: p for p in catalog if p["region"] == region}
    place_key = "pick_" + role + "_place"
    st.session_state[place_key] = place["id"] if place["id"] in local else next(iter(local))
    pid = st.selectbox("낚시할 곳", list(local), format_func=lambda k: display_name(local[k]), key=place_key, on_change=place_changed, args=(place_key,))
    return ALL[pid]


def time_picker(kind):
    # 두 날씨 탭의 날짜/시간을 서로 다른 키에 보관합니다.
    moment = now()
    days = day_choices(moment)
    upcoming = moment.replace(minute=0, second=0, microsecond=0)
    if upcoming < moment:
        upcoming += timedelta(hours=1)
    day_key, start_key, end_key = kind + "_day", kind + "_start", kind + "_end"
    if st.session_state.get(day_key) not in days:
        st.session_state[day_key] = upcoming.date().isoformat()
    day = st.selectbox("가는 날", days, format_func=day_label, key=day_key)
    if not isinstance(st.session_state.get(start_key), int) or st.session_state[start_key] not in range(24):
        st.session_state[start_key] = upcoming.hour if day == upcoming.date().isoformat() else 6
    begin = st.selectbox("시작 시각", list(range(24)), format_func=hour_label, key=start_key)
    end_options = list(range(begin+1,25))
    if st.session_state.get(end_key) not in end_options:
        st.session_state[end_key] = min(24, begin+6)
    end = st.selectbox("종료 시각", end_options, format_func=hour_label, key=end_key)
    save_selections()
    times = slots(day, begin, end, moment)
    if not times:
        st.info("선택한 시간이 지났어요. 더 늦은 시간이나 다른 날짜를 골라주세요.")
    elif datetime.fromisoformat(times[0]).replace(tzinfo=KST).hour != begin:
        st.caption("이미 지난 정시 예보는 제외했어요.")
    return times


def selected_place(kind=None):
    if kind in ("sea", "fresh"):
        pid = st.session_state.get("selected_" + kind + "_id")
        if pid in ALL and ALL[pid]["kind"] == kind:
            return ALL[pid]
        return (SEA if kind == "sea" else FRESH)[0]
    pid = st.session_state.get("active_place_id")
    return ALL.get(pid, SEA[0])


def dashboard(trip):
    # 홈은 장소·사진·이동만 담당합니다. 날씨나 날짜·시간은 조회하지 않습니다.
    place = selected_place()
    st.markdown('<div class="home-head"><span class="home-mark" aria-hidden="true">🐟</span><span class="home-brand">아빠 낚시 도우미</span></div><div class="home-greeting">아빠, 어디로 떠나실까요?</div>', unsafe_allow_html=True)
    show_place_photo(place, label="선택한 장소", home=True)
    st.markdown('<div class="home-shortcuts-title">출발 전, 간편하게</div>', unsafe_allow_html=True)
    with st.container(key="home_shortcuts"):
        change, weather, bag = st.columns(3, gap="small")
        change.button("지역 변경", key="home_change", on_click=navigate, args=("places",), use_container_width=True)
        weather.button("날씨 확인", key="home_weather", on_click=navigate, args=("sea_weather" if place["kind"] == "sea" else "fresh_weather",), use_container_width=True)
        bag.button("준비물", key="home_bag", on_click=navigate, args=("checklist",), use_container_width=True)


def forecast_for(place, times):
    with st.spinner("선택하신 곳의 날씨를 찾아보고 있어요…"):
        return load_forecast(place["id"], times)


def regions_page():
    st.caption("여기서 고른 장소가 홈에 표시돼요.")
    place_picker()


def weather_page(kind):
    place = place_picker(kind)
    with st.expander("가는 날·시간", expanded=True):
        times = time_picker(kind)
    if not times:
        weather_information()
        return
    data = forecast_for(place, times)
    start = datetime.fromisoformat(times[0])
    end = datetime.fromisoformat(times[-1]) + timedelta(hours=1)
    st.markdown(f'<div class="weather-range">{start:%m.%d %H시} – {end:%m.%d %H시}</div>', unsafe_allow_html=True)
    weather_information(data)
    rating = verdict(place, data)
    if kind == "sea":
        sea_suggestion(place, data, times, rating)


def booking_page():
    st.write("예약 사이트에서 지역과 날짜를 골라주세요.")
    st.link_button("⚓ 선상24 예약 열기 ↗", "https://www.sunsang24.com/ship/list", type="primary", use_container_width=True)
    st.caption("새 창으로 열려요. 빈자리와 실제 출항 여부는 예약처에 확인하세요.")


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
    st.session_state.setdefault("bag_kind", "바다 준비물" if selected_place()["kind"] == "sea" else "민물 준비물")
    label = st.radio("준비물 종류", ["바다 준비물", "민물 준비물"], horizontal=True, key="bag_kind")
    kind = "sea" if label.startswith("바다") else "fresh"
    groups = SEA_GROUPS if kind == "sea" else FRESH_GROUPS
    items = SEA_ITEMS if kind == "sea" else FRESH_ITEMS
    storage_kind = "auto3_" + kind
    try:
        with db() as con:
            done = {r[0]: bool(r[1]) for r in con.execute("SELECT item,done FROM fishing_checks_v2 WHERE trip=? AND kind=?", (trip, storage_kind))}
    except sqlite3.Error:
        done = {}
        st.warning("체크 저장을 확인하지 못했어요. 다시 접속하면 사라질 수 있어요.")
    values = [bool(st.session_state.get(f"bag_{trip}_{kind}_{i}", done.get(i, False))) for i in range(len(items))]
    count = sum(values)
    st.progress(count / len(items), text=f"{len(items)}개 중 {count}개 체크했어요")
    st.caption("모두 필수는 아니에요. 낚시 방식에 맞게 챙기세요.")
    view = st.radio("보기", ["모두", "안 챙긴 것"], horizontal=True, key="bag_filter")
    if view == "안 챙긴 것" and count == len(items):
        st.success("목록의 준비물을 모두 체크하셨어요.")
    offset = 0
    for group_index, (title, group_items) in enumerate(groups):
        group_done = sum(values[offset:offset + len(group_items)])
        with st.expander(f"{title} · {group_done}/{len(group_items)}", expanded=group_index == 0):
            visible = 0
            for local_index, item in enumerate(group_items):
                i = offset + local_index
                if view == "안 챙긴 것" and values[i]:
                    continue
                visible += 1
                key = f"bag_{trip}_{kind}_{i}"
                st.session_state.setdefault(key, values[i])
                st.checkbox(item, key=key, on_change=check_changed, args=(trip, storage_kind, i, key))
            if not visible:
                st.caption("이 묶음은 다 챙기셨어요.")
        offset += len(group_items)
    if st.session_state.pop("save_error", False):
        st.warning("저장하지 못했어요. 잠시 후 다시 확인해 주세요.")
    st.caption("약·기피제는 설명대로 사용하고, 강풍에는 파라솔을 접으세요. 현장 허용 장비만 사용하세요.")
    st.caption("기록은 서버 재시작·재배포 시 사라질 수 있어요.")
    save_selections()


def main():
    st.set_page_config(page_title="아빠 낚시 도우미", page_icon="🐟", layout="centered", initial_sidebar_state="collapsed")
    styles()
    raw = st.query_params.get("trip", "")
    try:
        trip = str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        trip = str(uuid.uuid4())
        st.query_params["trip"] = trip
    st.session_state.setdefault("page", "welcome")
    allowed = {target for _, target in MENU}
    if st.session_state["page"] not in allowed | {"welcome"}:
        st.session_state["page"] = "dashboard"
    st.session_state["page_history"] = [item for item in st.session_state.get("page_history", []) if item.get("page") in allowed]
    restore_selections()
    initialise_places()
    page = st.session_state["page"]
    if page == "welcome":
        welcome()
    else:
        if page != "dashboard" or st.session_state["page_history"]:
            st.button("← 뒤로가기", key="back_top", on_click=go_back)
        if page != "dashboard":
            st.markdown('<div class="topline">🐟 아빠 낚시 도우미</div>', unsafe_allow_html=True)
            st.subheader(PAGE_TITLES[page])
        if page == "dashboard":
            dashboard(trip)
        elif page == "places":
            regions_page()
        elif page == "sea_weather":
            weather_page("sea")
        elif page == "fresh_weather":
            weather_page("fresh")
        elif page == "booking":
            booking_page()
        elif page == "checklist":
            checklist(trip)
    save_selections()
    if st.session_state["page"] != "welcome":
        bottom_navigation()


def display_name(place):
    parts = [part for part in place["name"].split(" · ") if part not in ("허용구역 문의", "운영구역 문의")]
    return " · ".join(parts)


def initialise_places():
    """구버전 선택은 최초 1회만 옮기고, 홈에 표시할 장소는 위젯과 따로 보관합니다."""
    if not st.session_state.get("place_state_v4"):
        old = dict(st.session_state.get("saved_selections", {}))
        old.update({k: v for k, v in st.session_state.items() if k.startswith(("region_", "place_")) or k == "kind_choice"})
        for kind, catalog in (("sea", SEA), ("fresh", FRESH)):
            local = [p for p in catalog if p["region"] == old.get("region_" + kind)] or [catalog[0]]
            pid = old.get("place_" + kind + "_" + local[0]["region"])
            st.session_state["selected_" + kind + "_id"] = pid if pid in {p["id"] for p in local} else local[0]["id"]
        legacy_kind = "fresh" if str(old.get("kind_choice", "🌊 바다")).startswith("🐟") else "sea"
        st.session_state["active_place_id"] = st.session_state["selected_" + legacy_kind + "_id"]
        for old_key, new_key in (("trip_day", "_day"), ("start_hour", "_start"), ("end_hour", "_end")):
            if old_key in old:
                st.session_state.setdefault(legacy_kind + new_key, old[old_key])
        st.session_state["place_state_v4"] = True
    for kind, catalog in (("sea", SEA), ("fresh", FRESH)):
        pid = st.session_state.get("selected_" + kind + "_id")
        if pid not in ALL or ALL[pid]["kind"] != kind:
            st.session_state["selected_" + kind + "_id"] = catalog[0]["id"]
    if st.session_state.get("active_place_id") not in ALL:
        st.session_state["active_place_id"] = SEA[0]["id"]


def select_place(pid):
    place = ALL[pid]
    st.session_state["selected_" + place["kind"] + "_id"] = pid
    st.session_state["active_place_id"] = pid


def region_changed(kind, widget_key):
    region = st.session_state[widget_key]
    catalog = SEA if kind == "sea" else FRESH
    select_place(next(p["id"] for p in catalog if p["region"] == region))


def place_changed(widget_key):
    select_place(st.session_state[widget_key])


def region_kind_changed():
    kind = "sea" if st.session_state["region_kind"].startswith("🌊") else "fresh"
    select_place(selected_place(kind)["id"])


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value):
    parser = PlainText()
    parser.feed(str(value or ""))
    return " ".join(" ".join(parser.parts).split())[:500]


def allowed_url(url, hosts):
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in hosts and not parsed.username and not parsed.password


def distance_km(lat1, lon1, lat2, lon2):
    la1, la2 = math.radians(lat1), math.radians(lat2)
    value = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin(math.radians(lon2-lon1)/2)**2
    return 6371 * 2 * math.asin(min(1, math.sqrt(value)))


def photo_bytes(url):
    """허용한 실제 사진 호스트만 읽고, 8MB/2천만 픽셀 제한 후 작은 JPEG로 전달합니다."""
    hosts = {"tong.visitkorea.or.kr", "upload.wikimedia.org", "thumb.wikimedia.org"}
    if not allowed_url(url, hosts):
        return None
    with requests.get(url, timeout=(3, 8), stream=True, headers={"User-Agent": "DadFishingHelper/4.0 (personal non-commercial photo viewer)"}) as response:
        response.raise_for_status()
        if not allowed_url(response.url, hosts):
            return None
        if not response.headers.get("Content-Type", "").lower().startswith("image/"):
            return None
        limit = 8 * 1024 * 1024
        length = response.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > limit:
            return None
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > limit:
                return None
            chunks.append(chunk)
        with Image.open(BytesIO(b"".join(chunks))) as original:
            if original.width * original.height > 20_000_000:
                return None
            photo = ImageOps.exif_transpose(original).convert("RGB")
            photo.thumbnail((1000, 1000))
            out = BytesIO()
            photo.save(out, "JPEG", quality=85, optimize=True)
            return out.getvalue()


def commons_photo(file_name):
    # 지도/로고/깃발 파일은 실제 장소 사진으로 사용하지 않습니다.
    if not re.search(r"\.(jpg|jpeg|png|webp)$", file_name, re.I) or re.search(r"map|locator|logo|flag|coat.of.arms|daedongyeojido|지도|문장|1950년대", file_name, re.I):
        return None
    data = http_json("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "formatversion": 2,
        "prop": "imageinfo", "titles": "File:" + file_name,
        "iiprop": "url|extmetadata|mime", "iiurlwidth": 960,
        "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl",
    })
    pages = data.get("query", {}).get("pages", [])
    for page in pages:
        for info in page.get("imageinfo", [])[:1]:
            metadata = info.get("extmetadata", {})
            license_name = plain_text(metadata.get("LicenseShortName", {}).get("value", ""))
            author = plain_text(metadata.get("Artist", {}).get("value", ""))
            if not re.search(r"CC[ -]?(BY|0)|public domain|\bPDM\b", license_name, re.I):
                continue
            if not author and "BY" in license_name.upper():
                continue
            if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
                continue
            url = info.get("thumburl") or info.get("url")
            image = photo_bytes(url)
            if not image:
                continue
            source_url = info.get("descriptionurl", "")
            if not allowed_url(source_url, {"commons.wikimedia.org"}):
                source_url = "https://commons.wikimedia.org/wiki/File:" + quote(file_name)
            license_url = metadata.get("LicenseUrl", {}).get("value", "")
            if not allowed_url(license_url, {"creativecommons.org", "www.creativecommons.org"}):
                license_url = source_url
            return {"image": image, "author": author or "Wikimedia Commons", "license": license_name,
                    "source": source_url, "license_url": license_url, "provider": "commons"}
    return None


@st.cache_data(ttl=600, max_entries=100, show_spinner=False)
def load_place_photo(pid):
    """한 장소당 한 장. 확인 불가 시 다른 장소·스톡·지도 사진으로 채우지 않습니다."""
    errors = (requests.RequestException, ValueError, TypeError, KeyError, OSError, UnidentifiedImageError, Image.DecompressionBombError)
    if pid in PHOTO_SOURCES:
        url, source_url = PHOTO_SOURCES[pid]
        try:
            image = photo_bytes(url)
            if image:
                return {"image": image, "author": "한국관광공사", "source": source_url, "provider": "kto"}
        except errors:
            pass
    file_name = WIKI_PHOTO_FILES.get(pid)
    if file_name:
        try:
            result = commons_photo(file_name)
            if result:
                return result
        except errors:
            pass
    place = ALL[pid]
    title = PHOTO_TITLES.get(pid, display_name(place).replace(" 주변 해역", ""))
    try:
        data = http_json("https://ko.wikipedia.org/w/api.php", {
            "action": "query", "format": "json", "formatversion": 2,
            "prop": "pageimages|coordinates|pageprops", "titles": title, "redirects": 1,
            "piprop": "name", "pilicense": "free", "ppprop": "disambiguation",
        })
        for page in data.get("query", {}).get("pages", []):
            if page.get("missing") or "disambiguation" in page.get("pageprops", {}) or page.get("ns") != 0:
                continue
            coordinate = (page.get("coordinates") or [None])[0]
            if coordinate and distance_km(place["lat"], place["lon"], coordinate["lat"], coordinate["lon"]) > 15:
                continue
            file_name = page.get("pageimage", "")
            # 좌표가 없는 동명이항·지도는 자동 사진 연결에서 제외합니다.
            if not coordinate and not any(term in file_name.replace("_", " ") for term in (title, display_name(place))):
                continue
            result = commons_photo(file_name)
            if result:
                return result
    except errors:
        pass
    return None


def show_place_photo(place, label="살펴볼 장소", home=False):
    # 한 개의 HTML 카드에 제목·사진·작은 출처 버튼을 넣어 문서형 배치를 피합니다.
    with st.spinner("장소 사진을 불러오고 있어요…"):
        photo = load_place_photo(place["id"])
    kind = "바다 낚시" if place["kind"] == "sea" else "민물 낚시"
    title_tag = "h1" if home else "h3"
    header = f'<div class="place-card-meta"><div class="place-label-row"><span class="place-label">{e(label)}</span><span class="place-kind">{kind}</span></div><{title_tag} class="place-name">{e(display_name(place))}</{title_tag}><div class="place-region">{e(place["region"])}</div></div>'
    if photo is None:
        media = '<div class="photo-missing-small" role="status"><span class="photo-empty-icon" aria-hidden="true">▧</span><strong>사진을 불러오지 못했어요</strong></div>'
    else:
        encoded = base64.b64encode(photo["image"]).decode("ascii")
        source = photo["source"]
        if photo["provider"] == "kto":
            credit = f'사진 제공: 한국관광공사 · <a href="{e(source)}" target="_blank" rel="noopener noreferrer">출처</a>'
        else:
            credit = f'{e(photo["author"])} · <a href="{e(photo["license_url"])}" target="_blank" rel="noopener noreferrer">{e(photo["license"])}</a> · <a href="{e(source)}" target="_blank" rel="noopener noreferrer">원본</a>'
        media = f'<div class="photo-stage"><img src="data:image/jpeg;base64,{encoded}" alt="{e(display_name(place))} 대표 사진" decoding="async"><details class="photo-credit"><summary aria-label="{e(display_name(place))} 사진 출처와 이용조건">ⓘ 사진</summary><div class="photo-credit-body">{credit}<small>실시간 사진 아님 · 화면 비율에 맞게 일부 잘라 표시</small></div></details></div>'
    st.markdown(f'<article class="place-card" aria-label="{e(label)} {e(display_name(place))}">{header}{media}</article>', unsafe_allow_html=True)
    return photo is not None


def today_sea_times(moment=None):
    moment = moment or now()
    return slots(moment.date().isoformat(), 0, 24, moment)


def sea_score(data):
    if not data.get("complete") or any(not finite(data.get(key)) for key in ("wave", "wind", "gust")):
        return math.inf
    # 파도·바람·돌풍 중 각 주의 기준 대비 비율이 가장 큰 값을 우선합니다.
    return max(data["wave"] / 1.0, data["wind"] / 6.0, data["gust"] / 10.0)


@st.cache_data(ttl=900, max_entries=80, show_spinner=False)
def compare_seas(times, selected_pid):
    candidates = list(dict.fromkeys(SEA_REPRESENTATIVES + [selected_pid]))
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(load_forecast, pid, times): pid for pid in candidates}
        for future in as_completed(jobs):
            pid = jobs[future]
            try:
                results[pid] = future.result()
            except (requests.RequestException, ValueError, TypeError, KeyError):
                results[pid] = {"complete": False}
    return {"candidates": candidates, "forecasts": results}


def choose_sea_candidate(comparison, selected_pid, alternative=False):
    selected_region = ALL[selected_pid]["region"]
    found = []
    for pid in comparison["candidates"]:
        candidate = ALL[pid]
        if alternative and (pid == selected_pid or candidate["region"] == selected_region):
            continue
        forecast = comparison["forecasts"].get(pid, {})
        if forecast.get("complete") and level(forecast, "sea") == 0:
            found.append((sea_score(forecast), pid, forecast))
    if not found:
        return None
    _, pid, forecast = min(found, key=lambda value: (value[0], value[1]))
    return ALL[pid], forecast


def sea_suggestion(place, data, times, rating):
    if rating == 3 or not data.get("complete"):
        st.markdown('<div class="sea-empty">예보가 부족해 다른 장소를 추천하지 않았어요.</div>', unsafe_allow_html=True)
        return
    alternative = rating in (1, 2)
    comparison_times = times if alternative else today_sea_times()
    if not comparison_times:
        st.markdown('<div class="sea-empty">오늘 남은 예보가 없어 비교 후보를 고르지 않았어요.</div>', unsafe_allow_html=True)
        return
    with st.spinner("바다 예보를 비교하고 있어요…"):
        comparison = compare_seas(tuple(comparison_times), place["id"])
    option = choose_sea_candidate(comparison, place["id"], alternative=alternative)
    if option is None:
        st.markdown('<div class="sea-empty">비교한 곳 중 낮은 예보 수치를 확인하지 못했어요.</div>', unsafe_allow_html=True)
        return
    candidate, _ = option
    title = "다른 지역을 살펴보세요" if alternative else "오늘의 바다 후보"
    note = "같은 시간대 · 비교한 후보 중 낮은 예보 수치" if alternative else "오늘 남은 시간 · 비교한 후보 중 낮은 예보 수치"
    st.markdown(f'<div class="sea-suggest-title">{title}</div><div class="sea-suggest-note">{note}</div>', unsafe_allow_html=True)
    # 사진 아래의 수치/비교 현황/긴 안내/홈으로 선택 버튼을 모두 삭제합니다.
    show_place_photo(candidate, label="비교 후보")


def weather_information(data=None):
    # 긴 바닥글 대신 필요한 출처·안전 참고를 상단의 작은 정보 펼침에 보관합니다.
    fetched = f'조회 {e(data["fetched"])} · ' if data else ""
    body = f'{fetched}예보 최댓값 · 한국 시간 · 최대 15분 저장<br>날씨: <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Open-Meteo</a> · <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer">CC BY 4.0</a><br>일부 대표항만 비교하며 출항·현장 안전은 보장하지 않아요. <a href="https://www.weather.go.kr/" target="_blank" rel="noopener noreferrer">공식 예보</a>와 현장 상황을 확인하세요.'
    st.markdown(f'<details class="weather-info"><summary>ⓘ 예보 정보</summary><div>{body}</div></details>', unsafe_allow_html=True)


if __name__=="__main__":
    main()
