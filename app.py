"""아빠의 스마트 낚시 비서 — 단일 페이지 Streamlit 앱.

실행: pip install -r requirements.txt && streamlit run app.py
배포: 두 파일을 GitHub 같은 폴더에 올리고 Streamlit Community Cloud에서 app.py 선택.
저장: 서버 로컬 SQLite. Cloud 재시작/재배포 시 유실될 수 있으므로 JSON 백업 권장.
접속: ?trip=UUID가 붙은 주소를 북마크하면 같은 여행 기록으로 돌아옵니다.
보안: 로그인 기능 없음. 공유 주소를 아는 사람은 기록을 읽고 수정할 수 있습니다.
실시간 기상/출항/빈자리 API가 없으며, 기본 민물 목록은 명확히 표시한 예시입니다.
"""
from __future__ import annotations

import html
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

import streamlit as st


SEA_AREAS = ["서해 충남", "태안 / 안면도", "인천 / 대부도", "남해 거제 / 통영", "동해 강릉 / 속초"]
FRESH_AREAS = ["양촌", "예산", "아산", "공주"]
# 사용자가 요청한 이름 형식의 예시입니다. 실제 장소나 조황을 확인한 목록이 아닙니다.
EXAMPLES = {
    "양촌": ["양촌 저수지", "양촌 소류지"],
    "예산": ["예산 저수지", "예산 수로"],
    "아산": ["아산 저수지", "아산 좌대 낚시터"],
    "공주": ["공주 저수지", "공주 수로"],
}
ALT_CANDIDATES = {
    "서해 충남": ["삼길포항 내항"],
    "태안 / 안면도": ["태안 / 안면도 내항 — 직접 후보를 확인해 주세용"],
    "인천 / 대부도": ["대부도 탄도항 안쪽"],
    "남해 거제 / 통영": ["거제 / 통영 내항 — 직접 후보를 확인해 주세용"],
    "동해 강릉 / 속초": ["강릉 / 속초 내항 — 직접 후보를 확인해 주세용"],
}
PARKING = ["⚪ 미확인입니다용", "🟢 허용 주차장 바로 앞입니다용", "🟡 허용 주차장에서 도보 약 3분입니다용", "🔴 주차가 어렵습니다용"]
SEATS = ["⚪ 미확인입니다용", "🟢 발판이 평탄하고 자리가 여유롭습니다용", "⚠️ 자리가 빨리 찹니다용", "🔴 현재 자리가 없습니다용"]
SEA_ITEMS = ["신분증 · 선상 예약 정보", "구명조끼 · 미끄럼 방지 신발", "낚싯대 · 릴 · 낚싯줄", "펜치 · 바늘빼기 · 장갑", "멀미약 · 개인 상비약", "아이스박스 · 얼음", "모자 · 선글라스 · 자외선 차단", "물 · 휴대폰 · 보조배터리"]
FRESH_ITEMS = ["대낚시 / 루어대 · 받침대", "낚시 의자", "파라솔 · 고정 장치", "미끼 · 떡밥 / 지렁이", "헤드랜턴 · 여분 배터리", "벌레 기피제", "구명조끼 · 미끄럼 방지 신발", "물 · 수건 · 쓰레기봉투"]
KST = timezone(timedelta(hours=9))


def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")


def normalize_phone(raw):
    raw = raw.strip()
    if not raw:
        return ""
    if not re.fullmatch(r"\+?[0-9 ()\-]+", raw):
        raise ValueError("전화번호는 숫자 8~15자리로 적어주세용. 공백·괄호·하이픈도 쓸 수 있어용.")
    value = re.sub(r"[ ()\-]", "", raw)
    if not re.fullmatch(r"\+?[0-9]{8,15}", value):
        raise ValueError("전화번호의 숫자 개수를 확인해 주세용.")
    return value


def share_url(base, trip):
    try:
        p = urlsplit(base.strip())
        _ = p.port
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or any(c.isspace() for c in base):
            return None
        params = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != "trip"]
        params.append(("trip", trip))
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(params), ""))
    except ValueError:
        return None


def sea_level(wave, wind, hazard=False):
    """사용자가 제안한 참고 기준. 실제 출항 허가나 공식 안전 기준이 아님."""
    if hazard:
        return "stop"
    for value in (wave, wind):
        if value is not None and (not math.isfinite(value) or value < 0):
            return "unknown"
    if (wave is not None and wave >= 1.5) or (wind is not None and wind >= 8.0):
        return "stop"
    if wave is None or wind is None:
        return "unknown"
    if wave >= 1.0 or wind >= 6.0:
        return "caution"
    return "low"


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connection() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS fishing_spots_v2 (
              id TEXT PRIMARY KEY, trip TEXT NOT NULL, region TEXT NOT NULL,
              name TEXT NOT NULL, example INTEGER NOT NULL,
              parking INTEGER NOT NULL DEFAULT 0, seats INTEGER NOT NULL DEFAULT 0,
              species TEXT NOT NULL DEFAULT '', catch_count INTEGER NOT NULL DEFAULT 0,
              memo TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
              verified_at TEXT NOT NULL DEFAULT '')""")
            con.execute("CREATE INDEX IF NOT EXISTS fishing_spots_trip ON fishing_spots_v2(trip)")
            con.execute("""CREATE TABLE IF NOT EXISTS fishing_checks_v2 (
              trip TEXT NOT NULL, kind TEXT NOT NULL, item INTEGER NOT NULL,
              done INTEGER NOT NULL, PRIMARY KEY(trip,kind,item))""")
            con.execute("CREATE TABLE IF NOT EXISTS fishing_trips_v2 (trip TEXT PRIMARY KEY)")

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=20)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def seed(self, trip):
        # 별도 여행 테이블로 초기화를 표시해 모든 포인트를 지워도 예시가 재생성되지 않습니다.
        with self.connection() as con:
            result = con.execute("INSERT OR IGNORE INTO fishing_trips_v2 VALUES(?)", (trip,))
            if result.rowcount:
                for area, names in EXAMPLES.items():
                    for name in names:
                        con.execute("INSERT INTO fishing_spots_v2(id,trip,region,name,example) VALUES(?,?,?,?,1)", (str(uuid.uuid4()), trip, area, name))

    def spots(self, trip, region=None):
        with self.connection() as con:
            if region is None:
                rows = con.execute("SELECT * FROM fishing_spots_v2 WHERE trip=? ORDER BY name,id", (trip,))
            else:
                rows = con.execute("SELECT * FROM fishing_spots_v2 WHERE trip=? AND region=? ORDER BY example,name,id", (trip, region))
            return [dict(r) for r in rows]

    def put_spot(self, trip, record):
        with self.connection() as con:
            # 수정 대상을 trip으로 제한해 다른 여행 기록에 영향을 주지 않습니다.
            old = con.execute("SELECT trip FROM fishing_spots_v2 WHERE id=?", (record["id"],)).fetchone()
            if old and old["trip"] != trip:
                raise ValueError("이 여행의 포인트만 수정할 수 있어용.")
            con.execute("""INSERT INTO fishing_spots_v2
             (id,trip,region,name,example,parking,seats,species,catch_count,memo,phone,verified_at)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
             region=excluded.region,name=excluded.name,example=excluded.example,
             parking=excluded.parking,seats=excluded.seats,species=excluded.species,
             catch_count=excluded.catch_count,memo=excluded.memo,phone=excluded.phone,
             verified_at=excluded.verified_at""", (
                record["id"], trip, record["region"], record["name"], int(record["example"]),
                record["parking"], record["seats"], record["species"], record["catch_count"],
                record["memo"], record["phone"], record["verified_at"]))

    def delete_spot(self, trip, sid):
        with self.connection() as con:
            con.execute("DELETE FROM fishing_spots_v2 WHERE id=? AND trip=?", (sid, trip))

    def checks(self, trip, kind):
        with self.connection() as con:
            return {r["item"]: bool(r["done"]) for r in con.execute("SELECT item,done FROM fishing_checks_v2 WHERE trip=? AND kind=?", (trip, kind))}

    def check(self, trip, kind, item, done):
        with self.connection() as con:
            con.execute("INSERT INTO fishing_checks_v2 VALUES(?,?,?,?) ON CONFLICT(trip,kind,item) DO UPDATE SET done=excluded.done", (trip, kind, item, int(done)))

    def backup(self, trip):
        return {"version": 2, "spots": [{k: v for k, v in r.items() if k != "trip"} for r in self.spots(trip)],
                "checks": {g: [self.checks(trip, g).get(i, False) for i in range(8)] for g in ("sea", "fresh")}}

    def restore(self, trip, payload):
        """검증 완료된 백업을 단일 트랜잭션으로 교체하며 포인트 ID는 새로 만듭니다."""
        with self.connection() as con:
            con.execute("DELETE FROM fishing_spots_v2 WHERE trip=?", (trip,))
            con.execute("DELETE FROM fishing_checks_v2 WHERE trip=?", (trip,))
            for r in payload["spots"]:
                con.execute("""INSERT INTO fishing_spots_v2
                (id,trip,region,name,example,parking,seats,species,catch_count,memo,phone,verified_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (str(uuid.uuid4()), trip, r["region"], r["name"], int(r["example"]), r["parking"], r["seats"], r["species"], r["catch_count"], r["memo"], r["phone"], r["verified_at"]))
            for kind, values in payload["checks"].items():
                con.executemany("INSERT INTO fishing_checks_v2 VALUES(?,?,?,?)", [(trip, kind, i, int(v)) for i, v in enumerate(values)])


def validate_backup(payload):
    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise ValueError("이 앱에서 저장한 백업 파일을 골라주세용.")
    checks = payload.get("checks")
    if not isinstance(checks, dict) or set(checks) != {"sea", "fresh"}:
        raise ValueError("준비물 데이터가 올바르지 않아용.")
    for values in checks.values():
        if not isinstance(values, list) or len(values) != 8 or any(type(v) is not bool for v in values):
            raise ValueError("준비물 체크 형식을 확인해 주세용.")
    rows = payload.get("spots")
    if not isinstance(rows, list) or len(rows) > 300:
        raise ValueError("포인트 목록은 300개까지 불러올 수 있어용.")
    clean = []
    for row in rows:
        if not isinstance(row, dict) or row.get("region") not in FRESH_AREAS:
            raise ValueError("포인트 지역을 확인해 주세용.")
        r = dict(row)
        for field, maximum in [("name", 80), ("species", 100), ("memo", 500), ("phone", 30), ("verified_at", 60)]:
            value = r.get(field)
            if not isinstance(value, str) or len(value) > maximum:
                raise ValueError("포인트의 글자 수와 형식을 확인해 주세용.")
        if not r["name"].strip():
            raise ValueError("포인트 이름을 적어주세용.")
        if type(r.get("example")) not in (bool, int) or r["example"] not in (0, 1):
            raise ValueError("예시 표시를 확인해 주세용.")
        for field, top in [("parking", 3), ("seats", 3), ("catch_count", 10000)]:
            if type(r.get(field)) is not int or not 0 <= r[field] <= top:
                raise ValueError("주차·자리·조과 데이터가 올바르지 않아용.")
        r["phone"] = normalize_phone(r["phone"])
        clean.append(r)
    return {"version": 2, "spots": clean, "checks": checks}


def card(title, lines):
    # 입력값을 HTML로 해석하지 않도록 반드시 escape합니다.
    body = "".join(f'<div class="info-line">{html.escape(str(x))}</div>' for x in lines)
    st.markdown(f'<div class="fishing-card"><div class="card-title">{html.escape(title)}</div>{body}</div>', unsafe_allow_html=True)


def notice(text):
    st.session_state["fishing_notice"] = text
    st.rerun()


def update_check(store, trip, kind, i, key):
    store.check(trip, kind, i, st.session_state[key])


def spot_editor(store, trip, region, record=None):
    record = record or {"id": str(uuid.uuid4()), "name": "", "parking": 0, "seats": 0, "species": "", "catch_count": 0, "memo": "", "phone": "", "example": False, "verified_at": ""}
    with st.form("spot_form_" + record["id"], clear_on_submit=record["id"].startswith("new_")):
        name = st.text_input("실제 포인트 이름을 적어주세용", value=record["name"], max_chars=80)
        parking = st.selectbox("주차 상태를 골라주세용", range(len(PARKING)), index=record["parking"], format_func=lambda i: PARKING[i])
        seats = st.selectbox("자리 상태를 골라주세용", range(len(SEATS)), index=record["seats"], format_func=lambda i: SEATS[i])
        species = st.text_input("직접 확인한 어종을 적어주세용", value=record["species"], max_chars=100, placeholder="붕어 · 배스 등입니다용")
        count = st.number_input("최근 한 번 출조에서 직접 기록한 마릿수입니다용", min_value=0, max_value=10000, value=int(record["catch_count"]), step=1)
        phone = st.text_input("문의할 실제 전화번호를 적어주세용", value=record["phone"], max_chars=30)
        memo = st.text_area("출조일·문의 결과·조과 특징을 적어주세용", value=record["memo"], max_chars=500, height=100)
        verified = st.checkbox("오늘 직접 확인한 정보입니다용", value=False)
        st.caption("확인 체크를 해야 확인 시각을 새로 기록해용. 예시를 수정할 때는 실제 장소인지도 확인해 주세용.")
        if st.form_submit_button("이 포인트로 저장해 주세용", use_container_width=True):
            try:
                if not name.strip():
                    raise ValueError("포인트 이름을 적어주세용.")
                if record["example"] and not verified:
                    raise ValueError("예시를 실제 포인트로 바꾸려면 이름과 위치를 확인하고 오늘 확인 체크를 해주세용.")
                phone = normalize_phone(phone)
                item = dict(record, id=str(uuid.uuid4()) if record["id"].startswith("new_") else record["id"], region=region, name=name.strip(), parking=parking, seats=seats, species=species.strip(), catch_count=int(count), memo=memo.strip(), phone=phone, example=False,
                            verified_at=now_kst() if verified else record["verified_at"])
                store.put_spot(trip, item)
                notice("포인트를 저장했어용!")
            except ValueError as exc:
                st.error(str(exc))


def main():
    st.set_page_config(page_title="아빠의 스마트 낚시 비서", page_icon="🎣", layout="centered", initial_sidebar_state="collapsed")
    st.markdown("""
    <style>
    .block-container {max-width:780px;padding-top:4.2rem;padding-bottom:1.2rem;padding-left:0.85rem;padding-right:0.85rem;}
    /* 아이콘 폰트를 깨뜨리는 전역 font-family나 헤더 숨김을 사용하지 않습니다. */
    h1 {font-size:1.8rem !important;line-height:1.4 !important;}
    h2,h3 {font-size:1.4rem !important;line-height:1.4 !important;}
    [data-testid="stMarkdownContainer"] p {font-size:1.14rem;line-height:1.6;}
    [data-testid="stWidgetLabel"] p {font-size:1.12rem !important;font-weight:600;}
    [data-testid="stCaptionContainer"] p {font-size:1rem !important;}
    [data-testid="stCheckbox"] label p {font-size:1.12rem !important;}
    [data-testid="stCheckbox"] input {min-width:24px;min-height:24px;}
    .stButton button,.stDownloadButton button,[data-testid="stLinkButton"] a,[data-testid="stPopover"] button {min-height:52px;border-radius:12px;}
    .stButton button p,.stDownloadButton button p,[data-testid="stLinkButton"] a p {font-size:1.15rem !important;}
    [data-baseweb="tab"] {min-height:54px;padding-left:10px;padding-right:10px;}
    [data-baseweb="tab"] p {font-size:1.06rem !important;font-weight:700;white-space:nowrap;}
    input,textarea,[data-baseweb="select"] {font-size:19px !important;}
    .hero {background:#123d50;color:#fff;border-radius:18px;padding:18px;margin-bottom:12px;}
    .hero-title {font-size:clamp(23px,5.5vw,30px);font-weight:800;line-height:1.4;}
    .hero-sub {font-size:17px;line-height:1.5;margin-top:7px;color:#e0f2f3;}
    .fishing-card {border:1px solid #93b7c0;border-radius:14px;padding:16px;margin:8px 0 14px;background:var(--secondary-background-color);}
    .card-title {font-size:22px;font-weight:750;margin-bottom:8px;overflow-wrap:anywhere;}
    .info-line {font-size:19px;line-height:1.65;white-space:pre-wrap;overflow-wrap:anywhere;}
    </style>
    """, unsafe_allow_html=True)
    st.markdown('<div class="hero"><div class="hero-title">🎣 아빠의 스마트 낚시 비서</div><div class="hero-sub">날씨부터 자리, 준비물까지<br>천천히 확인하고 즐겁게 다녀오세용!</div></div>', unsafe_allow_html=True)
    raw = st.query_params.get("trip", "")
    try:
        trip = str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        trip = str(uuid.uuid4())
        st.query_params["trip"] = trip
    try:
        store = Store(os.environ.get("FISHING_DB_PATH", "fishing.sqlite3"))
        store.seed(trip)
    except sqlite3.Error:
        st.error("저장소를 열지 못했어용. 앱 관리자에게 저장 경로와 권한을 확인해 달라고 해주세용.")
        st.stop()
    if "fishing_notice" in st.session_state:
        st.success(st.session_state.pop("fishing_notice"))

    sea, fresh, check = st.tabs(["🌊 바다 낚시", "🎣 민물 낚시", "📋 준비물 체크"])
    with sea:
        area = st.selectbox("바다 지역을 골라주세용", SEA_AREAS, key="sea_area")
        st.caption("선택한 지역의 출조 시간대 예보를 직접 입력해 주세용. 실시간 조회는 아닙니다용.")
        with st.expander("🌤 예보를 입력하고 확인하세용", expanded=True):
            period = st.text_input("출조일·시간과 예보 출처를 적어주세용", placeholder="예: 11월 5일 오전 6시 · 기상청입니다용", key="period_" + area)
            c1, c2 = st.columns(2)
            wave = c1.number_input("예보 파고 (m)입니다용", min_value=0.0, max_value=30.0, value=None, step=0.1, placeholder="입력해 주세용", key="wave_" + area)
            wind = c2.number_input("예보 풍속 (m/s)입니다용", min_value=0.0, max_value=100.0, value=None, step=0.5, placeholder="입력해 주세용", key="wind_" + area)
            hazard = st.checkbox("특보·너울·낙뢰·현장 통제 등이 있어용", key="hazard_" + area)
            level = sea_level(wave, wind, hazard)
            if level == "stop":
                st.error("🔴 출조 보류·취소를 먼저 검토하세용! 실제 출항 여부는 선장·운항업체에 확인하세용.")
            elif level == "unknown":
                st.info("⚪ 파고와 풍속을 모두 입력해 주세용. 확인 전에는 판단하지 않아용.")
            elif level == "caution":
                st.warning("🟡 주의가 필요해용! 공식 기상과 현장 통제를 확인하고 선장님께 문의하세용.")
            else:
                st.info("🟢 입력값은 주의 기준보다 낮아용. 출항 가능이나 안전을 보장하지는 않으니 선장님께 꼭 확인하세용.")
            st.caption("파고 1.0m·풍속 6m/s부터 주의, 1.5m·8m/s부터 보류 검토하는 앱 자체 참고 기준입니다용. 공식 기준이 아니며 낮은 수치에서도 위험할 수 있어용.")
        st.link_button("🌦 공식 기상을 확인하세용", "https://www.weather.go.kr/", use_container_width=True)
        if level == "stop":
            st.subheader("🛡️ 위험할 때는 이렇게 바꿔보세용")
            st.warning("지금은 출조 연기나 실내 채비 점검을 먼저 권해드려용. 내항·잔교·석축도 악천후에는 위험할 수 있어용.")
            with st.expander("지역별 재검토 후보를 보세용 · 안전 추천은 아닙니다용"):
                for name in ALT_CANDIDATES[area]:
                    st.write("📍 " + name + " — 위치와 이용 가능 여부를 확인해 주세용.")
                st.caption("사용자 제안 형식의 미검증 후보입니다용. 기상이 나아진 뒤 출입·낚시 허용·현장 상태를 확인해야 해용. 현재 안전한 대체 장소라는 뜻은 아닙니다용.")
        with st.expander("🚢 배 예약 사이트로 이동하세용"):
            st.link_button("⚓ 물반고기반에서 확인하세용", "https://www.moolban.com/", use_container_width=True)
            st.link_button("🌊 선상24에서 확인하세용", "https://www.sunsang24.com/", use_container_width=True)
            st.caption("예약은 외부 사이트에서 진행해 주세용. 이 앱이 예약하거나 출항을 확정하지는 않아용.")
            st.caption("마이피싱 공식 주소는 확인하지 못해 기본 버튼에서 제외했어용. 확인된 주소가 있으면 아래에 넣어주세용.")
            custom = st.text_input("추가 예약 사이트 주소를 적어주세용", placeholder="https://로 시작해 주세용")
            if custom:
                link = share_url(custom, trip)
                if link:
                    p = urlsplit(custom.strip())
                    st.link_button("🚤 입력한 예약 사이트로 이동하세용", urlunsplit((p.scheme, p.netloc, p.path, p.query, "")), use_container_width=True)
                else:
                    st.warning("올바른 http(s) 주소를 적어주세용.")

    with fresh:
        region = st.selectbox("민물 지역을 골라주세용", FRESH_AREAS, key="fresh_region")
        rows = store.spots(trip, region)
        real = [r for r in rows if not r["example"] and r["verified_at"] and r["catch_count"] > 0]
        if real:
            top = max(real, key=lambda r: r["catch_count"])
            st.success(f"🐟 직접 확인 기록에서 {top['name']}의 {top['catch_count']}마리가 가장 많아용! 확인 시각은 {top['verified_at']}입니다용. 오늘 조과 보장은 아니니 최근 상황을 다시 물어보세용.")
        else:
            st.caption("대박 조과를 지어내지는 않아용. 실제 조과를 기록하면 지역 안에서 기록이 좋은 포인트를 먼저 알려드려용.")
        if rows:
            by_id = {r["id"]: r for r in rows}
            sid = st.selectbox("저수지·수로를 골라주세용", list(by_id), format_func=lambda x: ("[예시·미검증] " if by_id[x]["example"] else "") + by_id[x]["name"], key="spot_select_" + region)
            r = by_id[sid]
            if r["example"]:
                st.warning("이 이름은 화면 사용을 돕는 예시입니다용. 실제 장소·주차·자리·조과·연락처는 확인하지 않았어용.")
            card("📍 " + r["name"], [
                "🚗 주차: " + PARKING[r["parking"]],
                "🪑 자리: " + SEATS[r["seats"]],
                "🐟 어종: " + (r["species"] or "아직 직접 기록한 정보가 없어용"),
                f"🎣 직접 기록 조과: {r['catch_count']}마리입니다용" if r["catch_count"] else "🎣 직접 기록 조과: 아직 기록이 없어용",
                "📝 특징·문의 기록: " + (r["memo"] or "방문 전에 문의해 주세용"),
                "🕒 사용자 확인 시각: " + (r["verified_at"] or "아직 확인하지 않았어용"),
            ])
            if r["phone"]:
                st.markdown(f"### [📞 낚시방에 전화하세용]({ 'tel:' + r['phone'] })")
                st.caption("휴대폰 전화 앱을 여는 링크입니다용. PC에서는 전화 앱이 필요할 수 있어용.")
            else:
                st.info("문의 번호가 없어용. 아래 정보 수정에서 실제 낚시방 번호를 등록해 주세용.")
            with st.expander("✏️ 포인트 정보를 수정하세용"):
                spot_editor(store, trip, region, r)
                consent = st.checkbox("이 포인트를 삭제하겠어용", key="delete_" + sid)
                if st.button("선택한 포인트를 삭제하세용", disabled=not consent, key="delete_btn_" + sid):
                    store.delete_spot(trip, sid)
                    notice("포인트를 삭제했어용.")
        else:
            st.info("등록된 포인트가 없어용. 실제 방문할 곳을 추가해 주세용.")
        with st.expander("➕ 실제 포인트를 추가하세용"):
            # 새 폼 ID를 고정해 위젯 입력값이 rerun마다 사라지지 않도록 합니다.
            spot_editor(store, trip, region, {"id": "new_" + trip + "_" + region, "name": "", "example": False, "parking": 0, "seats": 0, "species": "", "catch_count": 0, "memo": "", "phone": "", "verified_at": ""})
        st.caption("자리와 주차 정보는 실시간이 아니에용. 방문 전에 낚시 허용·주차 허용·출입 통제를 직접 확인해 주세용.")

    with check:
        kind_label = st.radio("챙길 준비물을 골라주세용", ["🌊 바다 준비물입니다용", "🎣 민물 준비물입니다용"], horizontal=True)
        kind = "sea" if kind_label.startswith("🌊") else "fresh"
        items = SEA_ITEMS if kind == "sea" else FRESH_ITEMS
        values = store.checks(trip, kind)
        count = sum(values.get(i, False) for i in range(len(items)))
        st.progress(count / len(items), text=f"{len(items)}개 중 {count}개 챙기셨어용!")
        for i, item in enumerate(items):
            key = f"fishing_check_{trip}_{kind}_{i}"
            st.session_state.setdefault(key, values.get(i, False))
            st.checkbox(item + " 챙기셨나용?", key=key, on_change=update_check, args=(store, trip, kind, i, key))
        if count == len(items):
            st.success("짐은 모두 챙기셨어용! 출발 전에 기상과 현장 통제도 한 번 더 확인하세용.")
        st.caption("멀미약은 제품 설명서와 전문가 안내대로 복용하세용. 강풍 때는 파라솔을 접고, 기피제는 표시사항대로 사용하세용.")
        with st.expander("🔄 이 목록의 체크를 다시 시작하세용"):
            reset = st.checkbox("현재 종류의 체크만 모두 지우겠어용", key="reset_confirm_" + kind)
            if st.button("빈 체크리스트로 시작하세용", disabled=not reset, key="reset_btn_" + kind):
                for i in range(len(items)):
                    store.check(trip, kind, i, False)
                # 이미 생성된 위젯을 직접 바꾸지 않고 다음 실행 시작 전에 초기화합니다.
                st.session_state["clear_check_keys"] = f"fishing_check_{trip}_{kind}_"
                notice("준비물 체크를 비웠어용.")

    with st.expander("📱 QR 공유 · 기록 백업을 하세용"):
        base = st.text_input("배포한 앱 주소를 붙여넣어 주세용", placeholder="https://내앱.streamlit.app 입니다용")
        if base:
            url = share_url(base, trip)
            if not url:
                st.warning("http:// 또는 https://로 시작하는 주소를 적어주세용.")
            else:
                st.code(url, language=None, wrap_lines=True)
                # QR 라이브러리가 설치되지 않았어도 앱 전체가 중단되지 않습니다.
                try:
                    import qrcode
                except ImportError:
                    st.warning("QR 도구가 아직 설치되지 않았어용. GitHub의 requirements.txt에 qrcode[pil]을 넣고 앱을 재시작해 주세용. 위 주소로는 바로 접속할 수 있어용.")
                else:
                    try:
                        buf = io.BytesIO()
                        qr = qrcode.QRCode(box_size=7, border=4)
                        qr.add_data(url)
                        qr.make(fit=True)
                        qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
                        st.image(buf.getvalue(), width=230)
                        st.download_button("QR 이미지를 저장하세용", buf.getvalue(), "fishing-qr.png", "image/png", use_container_width=True)
                    except Exception:
                        st.warning("QR 생성에 실패했어용. 위 접속 주소를 복사해 사용하세용.")
        st.warning("주소를 아는 사람은 같은 여행 기록을 보고 수정할 수 있어용. 로그인 보호는 없으니 개인정보는 적지 마세용.")
        if st.button("공유 기록을 새로 확인하세용", use_container_width=True):
            st.session_state["clear_check_keys"] = f"fishing_check_{trip}_"
            st.rerun()
        backup = store.backup(trip)
        st.download_button("💾 포인트·준비물을 백업하세용", json.dumps(backup, ensure_ascii=False, indent=2), "fishing-backup.json", "application/json", use_container_width=True)
        upload = st.file_uploader("저장해 둔 백업을 골라주세용", type=["json"])
        confirm = st.checkbox("불러오면 현재 포인트와 체크 기록을 바꾸겠어용")
        if st.button("백업을 불러오세용", disabled=not (upload and confirm), use_container_width=True):
            try:
                if upload.size > 1_000_000:
                    raise ValueError("1MB 이하의 백업을 골라주세용.")
                payload = validate_backup(json.loads(upload.getvalue().decode("utf-8")))
                store.restore(trip, payload)
                st.session_state["clear_check_keys"] = f"fishing_check_{trip}_"
                notice("백업을 불러왔어용!")
            except (ValueError, UnicodeError, sqlite3.Error):
                st.error("백업 형식이나 저장 상태를 확인해 주세용. 이 앱에서 내려받은 JSON 파일이 필요해용.")
        st.caption("기록은 서버의 SQLite에 저장해용. 재시작·재배포 때 사라질 수 있으니 백업을 받아두세용. 같은 여행 주소를 북마크하세용. 기상 입력값은 현재 세션에서만 유지해용.")
    st.caption("🎣 아빠, 안전이 먼저입니다용! 이 앱은 공식 출항 허가나 실시간 현장 정보가 아니에용.")


if __name__ == "__main__":
    prefix = st.session_state.pop("clear_check_keys", None)
    if prefix:
        for key in list(st.session_state):
            if key.startswith(prefix):
                del st.session_state[key]
    main()
