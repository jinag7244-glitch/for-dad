"""아빠의 스마트 낚시 비서 — Streamlit + GitHub 배포용.
실행: pip install -r requirements.txt && streamlit run app.py
배포: GitHub에 app.py, requirements.txt 업로드 → Streamlit Community Cloud 연결.
QR: 앱의 QR 공유에서 배포 주소 입력. ?trip= 식별자를 포함한 같은 여행을 공유합니다.
주의: 공식 안전 판정·실시간 기상·예약·빈자리 조회 서비스가 아닙니다.
SQLite는 서버 로컬 저장소이므로 Cloud 재시작/재배포 시 유실될 수 있습니다.
공유 주소를 아는 사람은 준비물과 포인트를 수정할 수 있습니다. 개인정보 주의.
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import qrcode
import streamlit as st

st.set_page_config(page_title="아빠의 스마트 낚시 비서", page_icon="🎣", layout="centered", initial_sidebar_state="collapsed")
DB_PATH = os.environ.get("FISHING_DB_PATH", "fishing.sqlite3")
KST = timezone(timedelta(hours=9))


@contextmanager
def database():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def execute(sql, args=()):
    with database() as con:
        con.execute(sql, args)


def query(sql, args=()):
    with database() as con:
        return [dict(x) for x in con.execute(sql, args).fetchall()]


def make_share_url(base, trip_id):
    try:
        parts = urlsplit(base.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return None
        _ = parts.port  # Validate malformed ports too.
        if any(c.isspace() for c in parts.netloc):
            return None
        params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "trip"]
        params.append(("trip", trip_id))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))
    except ValueError:
        return None


def normalize_phone(value):
    text = value.strip()
    if not text:
        return ""
    if not re.fullmatch(r"\+?[0-9()\-\s]+", text):
        return None
    number = re.sub(r"[^0-9+]", "", text)
    return number if re.fullmatch(r"\+?[0-9]{8,15}", number) else None


def reference_level(wave, wind, hazard=False):
    # App-specific illustrative thresholds from the user's proposal, NOT official standards.
    if hazard:
        return "stop"
    if wave is None or wind is None:
        return "unknown"
    if wave >= 1.5 or wind >= 8.0:
        return "stop"
    if wave >= 1.0 or wind >= 6.0:
        return "caution"
    return "check"


def flash(text):
    st.session_state["notice"] = text
    st.rerun()


execute("""CREATE TABLE IF NOT EXISTS spots (
 id TEXT PRIMARY KEY, trip TEXT NOT NULL, name TEXT NOT NULL,
 parking TEXT NOT NULL, memo TEXT NOT NULL, phone TEXT NOT NULL,
 checked TEXT NOT NULL)""")
execute("""CREATE TABLE IF NOT EXISTS checks (
 trip TEXT NOT NULL, kind TEXT NOT NULL, item TEXT NOT NULL, done INTEGER NOT NULL,
 PRIMARY KEY(trip, kind, item))""")
raw = st.query_params.get("trip", "")
try:
    TRIP = str(uuid.UUID(raw))
except (ValueError, TypeError, AttributeError):
    TRIP = str(uuid.uuid4())
    st.query_params["trip"] = TRIP

st.markdown("""
<style>
.stApp {background:#f3f7fa;color:#163046;}
html,body,[class*="st-"] {font-family:system-ui,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;}
.block-container {max-width:650px;padding:0.65rem 0.65rem 0.5rem !important;}
header[data-testid="stHeader"] {height:0;background:transparent;}
[data-testid="stToolbar"] {display:none;}
[data-testid="stVerticalBlock"] {gap:0.4rem;}
h1 {font-size:1.35rem !important;padding:0 !important;}
h2,h3 {font-size:1.05rem !important;padding:0.1rem 0 !important;}
p {margin-bottom:0.25rem;}
.stButton button,[data-testid="stLinkButton"] a,[data-testid="stPopover"] button {
 min-height:2.6rem;border-radius:11px;font-size:0.9rem;}
[data-testid="stHorizontalBlock"] {gap:0.5rem;}
[data-testid="stCaptionContainer"] {font-size:0.76rem;}
@media(max-width:600px) {
 [data-testid="stHorizontalBlock"] {flex-wrap:nowrap !important;}
 [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {min-width:0 !important;}
}
</style>
""", unsafe_allow_html=True)

st.title("🎣 아빠의 스마트 낚시 비서")
st.caption("바다는 신중하게 · 민물은 미리 문의 · 준비물은 빠짐없이 챙겨용!")
share_col, guide_col = st.columns(2)
with share_col:
    with st.popover("▦ QR 공유", use_container_width=True):
        base = st.text_input("배포한 앱 주소", placeholder="https://내앱.streamlit.app", key="share_base")
        url = make_share_url(base, TRIP) if base.strip() else None
        if url:
            image = qrcode.make(url)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            st.image(buf.getvalue(), width=190)
            st.download_button("QR 이미지 저장", buf.getvalue(), "fishing-qr.png", "image/png")
            st.link_button("공유 주소 열기", url)
            st.code(url, language=None, wrap_lines=True)
        elif base.strip():
            st.warning("http(s)://로 시작하는 유효한 배포 주소를 입력해 주세요.")
        st.caption("QR은 앱을 여는 링크입니다. 설치·로그인은 필요 없지만 앱 공개 설정에 따라 로그인이 요구될 수 있습니다. 주소를 아는 사람은 이 여행의 포인트·체크리스트를 수정할 수 있습니다.")
with guide_col:
    with st.popover("ⓘ 이용 안내", use_container_width=True):
        st.write("기상은 직접 입력하며, 자리 현황은 전화로 확인한 내용을 직접 기록합니다. 실시간 자동 조회나 예약 대행 기능은 없습니다.")
        st.caption("같은 여행 주소를 북마크하세요. 체크리스트·포인트는 서버 로컬 SQLite에 저장합니다. 재시작·재배포 시 유실될 수 있어 영구 서비스에는 외부 DB가 필요합니다. QR을 공유하면 같은 여행을 보지만 변경 내용은 새로고침해야 합니다.")
        st.caption("화면은 컴팩트하게 구성했습니다. 작은 화면·키보드·확대·팝업 내용에 따라 스크롤이 필요할 수 있습니다. 안전 안내를 숨기는 방식으로 스크롤을 강제 차단하지 않습니다.")
        if st.button("공유 데이터 새로고침", use_container_width=True):
            st.rerun()
if "notice" in st.session_state:
    st.toast(st.session_state.pop("notice"))

sea, fresh, checklist = st.tabs(["🌊 바다", "🎣 민물", "📋 준비물"])
with sea:
    st.caption("직접 확인한 해당 해역·출조 시간대의 값을 입력하세요. 실시간 측정값이 아닙니다.")
    c1, c2 = st.columns(2)
    wave = c1.number_input("예보 파고 (m)", min_value=0.0, max_value=30.0, value=None, step=0.1, placeholder="직접 입력", key="wave")
    wind = c2.number_input("예보 풍속 (m/s)", min_value=0.0, max_value=100.0, value=None, step=0.5, placeholder="직접 입력", key="wind")
    hazard = st.checkbox("특보·낙뢰·너울·통제 등 위험을 확인했어요", key="hazard")
    level = reference_level(wave, wind, hazard)
    if level == "stop":
        st.error("출조 보류·취소를 우선 검토하세요. 다른 내항도 안전하다고 볼 수 없습니다. 공식 정보와 현장 통제를 확인하세요.")
        with st.popover("💡 안전한 대안은?", use_container_width=True):
            st.write("특정 항구로 이동을 권하지 않습니다. 출조를 연기하거나 실내에서 채비를 점검하세요. 민물로 바꾸더라도 강우·홍수·방류·출입 통제를 별도로 확인해야 합니다.")
    elif level == "caution":
        st.warning("주의 구간입니다. 임의로 내항이나 방파제로 이동하지 말고 공식 정보·현장 통제·선장 판단을 먼저 확인하세요.")
    elif level == "check":
        st.info("입력값은 이 앱의 주의 기준보다 낮습니다. 하지만 출조 안전을 뜻하지 않습니다. 너울·조류·낙뢰·현장 상태도 확인하세요.")
    else:
        st.info("파고와 풍속을 모두 입력하면 참고 안내가 나옵니다. 입력 전에는 판단하지 않습니다.")
    with st.popover("판단 기준 · 꼭 읽어 주세요", use_container_width=True):
        st.write("주의: 파고 1.0m 이상 또는 풍속 6.0m/s 이상. 보류 검토: 파고 1.5m 이상 또는 풍속 8.0m/s 이상. 위험 확인 체크가 있으면 입력값과 관계없이 보류를 안내합니다.")
        st.warning("위 숫자는 사용자가 제안한 앱 내부 예시 기준이며 공식 기준이나 안전 보장이 아닙니다. 낚시 방식·지형·파주기·돌풍·조류에 따라 낮은 수치에서도 위험합니다. 특보와 통제에 따르세요.")
    st.link_button("🌦 기상청 날씨누리 확인", "https://www.weather.go.kr/", use_container_width=True)
    with st.popover("🚢 선상 예약 사이트", use_container_width=True):
        st.caption("외부 사이트로 이동합니다. 예약 완료·출항 여부는 해당 업체에서 확인하세요.")
        st.link_button("물반고기반", "https://www.moolban.com/", use_container_width=True)
        st.link_button("선상24", "https://www.sunsang24.com/", use_container_width=True)

with fresh:
    st.caption("포인트·주차·전화번호는 직접 등록합니다. 실시간 빈자리 정보가 아닙니다.")
    with st.popover("＋ 민물 포인트 등록", use_container_width=True):
        with st.form("add_spot", clear_on_submit=True):
            name = st.text_input("포인트 이름", max_chars=60)
            parking = st.text_input("직접 확인한 주차 정보", max_chars=100, placeholder="모르면 미확인으로 남겨 주세요")
            phone = st.text_input("문의 전화번호 (선택)", max_chars=25)
            memo = st.text_area("자리·주차 문의 결과 (선택)", max_chars=300, height=75)
            if st.form_submit_button("등록", use_container_width=True):
                clean = normalize_phone(phone)
                if not name.strip():
                    st.error("실제 포인트 이름을 입력해 주세요.")
                elif clean is None:
                    st.error("전화번호는 숫자 8~15자리와 +, 공백, 괄호, 하이픈으로 입력하세요.")
                else:
                    execute("INSERT INTO spots VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), TRIP, name.strip(), parking.strip() or "미확인", memo.strip() or "미확인", clean, datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")))
                    flash("포인트를 등록했어용!")
    spots = query("SELECT * FROM spots WHERE trip=? ORDER BY checked DESC, id", (TRIP,))
    if not spots:
        st.info("방문하려는 포인트를 등록하고 출발 전에 직접 문의해 보세용!")
    else:
        lookup = {x["id"]: x for x in spots}
        sid = st.selectbox("포인트 선택 · 검색 가능", list(lookup), format_func=lambda x: lookup[x]["name"], key="spot_select")
        spot = lookup[sid]
        st.text("주차: " + spot["parking"])
        st.text("문의 메모: " + spot["memo"])
        st.caption("사용자 마지막 기록: " + spot["checked"] + " · 지금 상태와 다를 수 있습니다.")
        if spot["phone"]:
            # Number is normalized before saving; this is a real dialer link, not a toast.
            st.markdown(f'[📞 문의 전화 걸기 · {spot["phone"]}](tel:{spot["phone"]})')
            st.caption("휴대폰 전화 앱을 엽니다. PC에서는 전화 연결 앱이 필요할 수 있습니다.")
        else:
            st.caption("문의 전화번호가 등록되지 않았습니다.")
        with st.popover("✎ 정보 수정 / 삭제", use_container_width=True):
            with st.form(f"edit_{sid}"):
                new_name = st.text_input("포인트 이름", value=spot["name"], max_chars=60)
                new_parking = st.text_input("주차 정보", value=spot["parking"], max_chars=100)
                new_phone = st.text_input("문의 전화번호", value=spot["phone"], max_chars=25)
                new_memo = st.text_area("직접 확인한 문의 결과", value=spot["memo"], max_chars=300)
                if st.form_submit_button("수정 저장", use_container_width=True):
                    clean = normalize_phone(new_phone)
                    if not new_name.strip() or clean is None:
                        st.error("이름과 전화번호 형식을 확인하세요.")
                    else:
                        execute("UPDATE spots SET name=?,parking=?,phone=?,memo=?,checked=? WHERE id=? AND trip=?", (new_name.strip(), new_parking.strip() or "미확인", clean, new_memo.strip() or "미확인", datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"), sid, TRIP))
                        flash("문의 내용을 저장했어용!")
            confirm = st.checkbox("이 포인트를 삭제할게요", key=f"delete_{sid}")
            if st.button("확인 후 삭제", disabled=not confirm, key=f"delete_btn_{sid}"):
                execute("DELETE FROM spots WHERE id=? AND trip=?", (sid, TRIP))
                flash("삭제했어용.")
    st.caption("현장 출입·낚시 가능 여부와 주차 허용 여부를 직접 확인하세요. 미확인 갓길 주차는 권하지 않습니다.")

SEA_ITEMS = ["신분증·예약 정보", "구명조끼·미끄럼 방지 신발", "낚싯대·릴·줄·채비", "장갑·펜치·바늘빼기", "아이스박스·얼음", "멀미약 (설명서·전문가 안내 확인)", "모자·선글라스·자외선 차단", "휴대폰·보조배터리·비상 연락처"]
FRESH_ITEMS = ["구명조끼·미끄럼 방지 신발", "낚싯대·받침대·뜰채", "의자·파라솔 (강풍 시 사용 중지)", "미끼·채비", "헤드랜턴·찌불·여분 배터리", "기피제 (표시사항 준수)", "물·간식·개인 상비약", "수건·휴지·쓰레기봉투"]


def save_check(trip, kind, item, key):
    execute("INSERT INTO checks(trip,kind,item,done) VALUES(?,?,?,?) ON CONFLICT(trip,kind,item) DO UPDATE SET done=excluded.done", (trip, kind, item, int(st.session_state[key])))


with checklist:
    kind = st.radio("낚시 종류", ["🌊 바다", "🎣 민물"], horizontal=True, label_visibility="collapsed")
    items = SEA_ITEMS if kind == "🌊 바다" else FRESH_ITEMS
    records = {x["item"]: bool(x["done"]) for x in query("SELECT item,done FROM checks WHERE trip=? AND kind=?", (TRIP, kind))}
    done_count = sum(records.get(item, False) for item in items)
    st.progress(done_count / len(items), text=f"준비 완료 {done_count}/{len(items)}개")
    # Two columns of four short items, rather than a long, two-list page.
    left, right = st.columns(2)
    for idx, item in enumerate(items):
        key = f"check_{TRIP}_{kind}_{idx}"
        with (left if idx < 4 else right):
            st.checkbox(item, value=records.get(item, False), key=key, on_change=save_check, args=(TRIP, kind, item, key))
    if done_count == len(items):
        st.success("짐은 모두 체크했어용! 출발 전 공식 기상·현장 통제도 다시 확인하세요.")
    st.caption("체크 결과는 같은 여행 주소에서 다시 확인할 수 있습니다. 약 복용 시점·용량은 제품별로 확인하세요.")

st.caption("🎣 안전이 먼저라용! 이 앱의 안내는 공식 출조 허가나 안전 보장이 아닙니다.")
