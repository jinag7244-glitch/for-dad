"""장보기 알림메모
실행: pip install -r requirements.txt && streamlit run app.py
GitHub에 두 파일을 올리고 Streamlit Community Cloud에서 app.py를 배포하세요.
QR 공유: 앱 안의 QR 버튼에서 실제 배포 주소를 입력합니다.
주의: SQLite는 실행 서버의 로컬 저장소입니다. Cloud 재시작/재배포 시 데이터가
사라질 수 있으며, 여러 서버 사이에 동기화되지 않습니다. 영구 서비스는 외부 DB로 교체하세요.
공유 URL을 아는 사람은 해당 목록을 열람/수정할 수 있습니다. 개인정보를 적지 마세요.
"""
from __future__ import annotations

import io
import os
import sqlite3
import uuid
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import qrcode
import streamlit as st

st.set_page_config(page_title="장보기 알림메모", page_icon="🛒", layout="centered", initial_sidebar_state="collapsed")

DB_PATH = Path(os.environ.get("SHOPPING_DB_PATH", "shopping.sqlite3"))
CATEGORIES = ["🥬 야채", "🍎 과일", "🥩 육류", "🐟 수산물", "🥛 유제품·달걀", "🍚 곡류·면", "🧊 냉동·간편식", "🧂 양념·소스", "🧻 생활용품", "📦 기타"]
PRIORITIES = {1: "🔴 1 · 꼭 필요", 2: "🟡 2 · 가격 보고", 3: "🟢 3 · 저렴할 때"}


@contextmanager
def connect():
    """Commit on success, rollback on error, and always release the connection."""
    con = sqlite3.connect(str(DB_PATH), timeout=20)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def initialize():
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id TEXT NOT NULL, category TEXT NOT NULL, name TEXT NOT NULL,
            priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 3),
            ceiling INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','deferred','purchased')),
            unit_price INTEGER NOT NULL DEFAULT 0, quantity TEXT NOT NULL DEFAULT '0',
            amount INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_items_list ON items(list_id)")


def get_items():
    with connect() as con:
        return [dict(row) for row in con.execute(
            "SELECT * FROM items WHERE list_id=? ORDER BY priority, id", (LIST_ID,)
        ).fetchall()]


def execute(sql, args):
    with connect() as con:
        con.execute(sql, args)


def notify(message):
    st.session_state["notice"] = message
    st.rerun()


def money(value):
    return f"{int(value):,}원"


def amount_for(price, quantity):
    return int((Decimal(str(price)) * Decimal(str(quantity))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def share_url(base):
    parts = urlsplit(base.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password:
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "list"]
    query.append(("list", LIST_ID))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


initialize()
raw_id = st.query_params.get("list", "")
try:
    LIST_ID = str(uuid.UUID(raw_id))
except (ValueError, TypeError, AttributeError):
    LIST_ID = str(uuid.uuid4())
    st.query_params["list"] = LIST_ID

st.markdown("""
<style>
html, body, [class*="st-"] {font-family:system-ui,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;}
.stApp {background:#F6F8F5; color:#192B22;}
.block-container {max-width:540px;padding:0.6rem 0.8rem 0.5rem !important;}
header[data-testid="stHeader"] {height:0;background:transparent;}
[data-testid="stToolbar"] {display:none;}
[data-testid="stVerticalBlock"] {gap:0.42rem;}
h1 {font-size:1.35rem !important;padding:0 !important;line-height:1.5 !important;}
p {margin-bottom:0.25rem;}
[data-testid="stMetric"] {background:white;border:1px solid #DFE8DF;border-radius:12px;padding:7px 10px;}
[data-testid="stMetricLabel"] {font-size:0.73rem;}
[data-testid="stMetricValue"] {font-size:1.2rem;}
.stButton button, [data-testid="stPopover"] button {min-height:2.35rem;border-radius:10px;}
[data-testid="stNumberInput"] input {min-height:2.2rem;}
[data-testid="stCaptionContainer"] {font-size:0.75rem;}
[data-testid="stHorizontalBlock"] {gap:0.5rem;}
[data-testid="stRadio"] > div {gap:0.7rem;}
@media(max-width:600px) {
 .block-container {padding-top:0.45rem !important;}
 [data-testid="stHorizontalBlock"] {flex-wrap:nowrap !important;}
 [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {min-width:0 !important;}
}
</style>
""", unsafe_allow_html=True)

st.title("🛒 장보기 알림메모")
rows = get_items()
cart = [x for x in rows if x["status"] == "purchased"]
pending = [x for x in rows if x["status"] == "pending"]
deferred = [x for x in rows if x["status"] == "deferred"]
a, b = st.columns([1.25, 1])
a.metric("현재 카트 총액", money(sum(x["amount"] for x in cart)))
b.metric("남은 메모 / 담은 품목", f"{len(pending) + len(deferred)} / {len(cart)}")

add_col, qr_col, help_col = st.columns([1.5, 1, 1])
with add_col:
    with st.popover("＋ 메모", use_container_width=True):
        st.caption("수량의 단위(개·봉·kg)는 상품명에 써 주세요.")
        with st.form("add_item", clear_on_submit=True):
            category = st.selectbox("카테고리", CATEGORIES)
            name = st.text_input("상품명", placeholder="예: 사과 (개), 소고기 (100g)", max_chars=70)
            priority = st.selectbox("중요도", [1, 2, 3], index=1, format_func=lambda x: PRIORITIES[x])
            ceiling = st.number_input("희망 단가 상한 (원, 0 = 미지정)", min_value=0, max_value=100_000_000, step=100, value=0)
            if st.form_submit_button("메모 저장", type="primary", use_container_width=True):
                if not name.strip():
                    st.error("상품명을 입력해 주세요.")
                else:
                    execute("INSERT INTO items(list_id,category,name,priority,ceiling) VALUES(?,?,?,?,?)",
                            (LIST_ID, category, name.strip(), priority, int(ceiling)))
                    notify("메모를 추가했어요.")
with qr_col:
    with st.popover("▦ QR", use_container_width=True):
        st.caption("배포한 앱 주소를 입력하면 이 장보기 목록을 여는 QR이 만들어집니다.")
        base = st.text_input("실제 앱 주소", placeholder="https://my-app.streamlit.app", key="base_url")
        url = share_url(base) if base.strip() else None
        if url:
            image = qrcode.make(url)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            st.image(buf.getvalue(), width=210)
            st.link_button("이 목록 열기", url)
            st.code(url, language=None, wrap_lines=True)
            st.download_button("QR 이미지 저장", buf.getvalue(), "shopping-qr.png", "image/png")
        elif base.strip():
            st.warning("https://로 시작하는 실제 배포 주소를 입력해 주세요.")
        st.caption("주소를 아는 사람은 수정할 수 있습니다. 공개 게시에 주의하세요.")
with help_col:
    with st.popover("도움말", use_container_width=True):
        st.markdown("**1 꼭 필요** · **2 가격 보고** · **3 저렴할 때**\n\n비싸면 **보류**하세요. 보류된 상품은 메모에 남고 카트 합계에 포함되지 않습니다.\n\n단가는 상품명에 적은 단위 기준입니다. 실제 결제 전 할인·행사 등을 반영해 가격을 수정하세요. 금액은 원 단위 반올림됩니다.")
        st.caption("목록 주소를 북마크하세요. 같은 주소를 열면 같은 목록입니다. 기기 간 변경사항은 ‘새로고침’으로 확인합니다. 기본 로컬 DB는 Cloud 재시작·재배포 시 사라질 수 있습니다. 영구 보관이 필요하면 외부 DB가 필요합니다. 자동 푸시 알림 기능은 없습니다. 작은 화면·확대·키보드 사용 시에는 스크롤이 생길 수 있습니다.")
        if st.button("새로고침", use_container_width=True):
            st.rerun()

if "notice" in st.session_state:
    st.toast(st.session_state.pop("notice"))

mode = st.radio("보기", ["📝 메모", "⏸ 보류", "🛒 카트"], horizontal=True, label_visibility="collapsed")
current = pending if mode == "📝 메모" else deferred if mode == "⏸ 보류" else cart

if not current:
    st.info({"📝 메모": "살 물건을 ＋ 메모에 추가하세요.", "⏸ 보류": "보류한 상품이 없어요.", "🛒 카트": "아직 카트에 담은 상품이 없어요."}[mode])
    st.caption("상품은 검색 선택창으로 압축 표시합니다. 작은 화면·키보드 사용 중에는 스크롤이 생길 수 있어요.")
else:
    by_id = {item["id"]: item for item in current}
    def label(item_id):
        item = by_id[item_id]
        return f'{PRIORITIES[item["priority"]].split(" · ")[0]} | {item["category"].split(" ", 1)[-1]} · {item["name"]}'
    selected = st.selectbox("상품 선택 · 검색 가능", list(by_id), format_func=label, key=f"select_{mode}_{LIST_ID}")
    item = by_id[selected]
    is_cart = item["status"] == "purchased"
    ceiling_text = money(item["ceiling"]) if item["ceiling"] else "미지정"
    st.caption(f'{PRIORITIES[item["priority"]]}  ·  희망 단가 상한 {ceiling_text}')
    # Separate widget keys ensure quantities/prices don't leak between items.
    price_col, quantity_col = st.columns([1.2, 1])
    price = price_col.number_input("구매 단가 (원)", min_value=0, max_value=100_000_000,
                                  value=int(item["unit_price"]) if is_cart else None, step=100,
                                  placeholder="단가 입력 · 무료는 0",
                                  key=f"price_{LIST_ID}_{selected}")
    quantity = quantity_col.number_input("구매 수량", min_value=0.01, max_value=100_000.0,
                                        value=float(item["quantity"]) if is_cart else 1.0,
                                        step=1.0, format="%.2f", key=f"qty_{LIST_ID}_{selected}")
    subtotal = amount_for(price if price is not None else 0, quantity)
    if item["ceiling"] and price is not None and price > item["ceiling"]:
        st.warning(f'상한보다 {money(price - item["ceiling"])} 비싸요. 보류해도 괜찮아요.')
    elif item["priority"] in (2, 3) and not is_cart:
        st.caption("💡 비싸면 사지 않고 보류하세요." if item["priority"] == 2 else "💡 저렴하지 않다면 다음 기회에!")
    else:
        st.caption("💡 단가 × 수량으로 계산하며, 무료 상품은 단가 0원으로 기록할 수 있어요.")
    st.markdown(f"**이 상품 {money(subtotal)}**" + (f' · 저장액 {money(item["amount"])}' if is_cart else ""))
    left, right = st.columns([1.4, 1])
    with left:
        if st.button("금액·수량 수정 저장" if is_cart else "✓ 카트에 담기", type="primary", use_container_width=True, disabled=price is None):
            execute("UPDATE items SET status='purchased',unit_price=?,quantity=?,amount=? WHERE id=? AND list_id=?",
                    (int(price), str(quantity), subtotal, selected, LIST_ID))
            notify("카트 금액을 갱신했어요." if is_cart else "메모에서 카트로 옮겼어요.")
    with right:
        action = "↩ 메모로" if is_cart or item["status"] == "deferred" else "⏸ 보류"
        if st.button(action, use_container_width=True):
            status = "pending" if is_cart or item["status"] == "deferred" else "deferred"
            execute("UPDATE items SET status=?,unit_price=0,quantity='0',amount=0 WHERE id=? AND list_id=?",
                    (status, selected, LIST_ID))
            notify("메모로 되돌렸어요." if status == "pending" else "보류했어요. 메모는 남아 있어요.")
    with st.popover("⋯ 상품 수정 / 삭제", use_container_width=True):
        with st.form(f"edit_{selected}"):
            new_name = st.text_input("상품명 수정", value=item["name"], max_chars=70)
            new_category = st.selectbox("카테고리 수정", CATEGORIES, index=CATEGORIES.index(item["category"]))
            new_priority = st.selectbox("중요도 수정", [1, 2, 3], index=item["priority"]-1, format_func=lambda x: PRIORITIES[x])
            new_ceiling = st.number_input("희망 단가 상한 수정", min_value=0, max_value=100_000_000, value=item["ceiling"], step=100)
            if st.form_submit_button("수정 저장", use_container_width=True):
                if new_name.strip():
                    execute("UPDATE items SET name=?,category=?,priority=?,ceiling=? WHERE id=? AND list_id=?",
                            (new_name.strip(), new_category, new_priority, int(new_ceiling), selected, LIST_ID))
                    notify("상품 정보를 수정했어요.")
                else:
                    st.error("상품명을 입력해 주세요.")
        st.caption("영구 삭제는 구매 기록과 금액도 삭제합니다. 카트에서 빼기만 하려면 ‘메모로’를 사용하세요.")
        confirm = st.checkbox("이 상품을 영구 삭제할게요", key=f"delete_confirm_{selected}")
        if st.button("확인 후 삭제", disabled=not confirm, use_container_width=True):
            execute("DELETE FROM items WHERE id=? AND list_id=?", (selected, LIST_ID))
            notify("상품을 삭제했어요.")

st.caption("🔴 필수  ·  🟡 가격 보고  ·  🟢 저렴할 때  |  총액은 카트에 저장한 금액 기준")
