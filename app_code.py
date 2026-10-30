import streamlit as st

# 1. 페이지 설정 (모바일 컴팩트 화면 구성)
st.set_page_config(
    page_title="장보기 알림 메모",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# 커스텀 CSS: 모바일 여백 최소화 및 컴포넌트 컴팩트화
st.markdown(
    """
    <style>
        .block-container {
            padding-top: 1rem;
            padding-bottom: 1rem;
            padding-left: 0.5rem;
            padding-right: 0.5rem;
        }
        div[data-testid="stVerticalBlock"] > div {
            gap: 0.3rem;
        }
        h3 {
            margin-bottom: 0.2rem;
            padding-top: 0rem;
            font-size: 1.1rem;
        }
        .stButton>button {
            width: 100%;
            padding: 2px 4px;
            font-size: 0.8rem;
        }
    </style>
""",
    unsafe_allow_html=True,
)

# 세션 상태 초기화
if "todo_list" not in st.session_state:
    st.session_state.todo_list = []
if "cart_list" not in st.session_state:
    st.session_state.cart_list = []
if "item_id_counter" not in st.session_state:
    st.session_state.item_id_counter = 0

# 카테고리 정의
CATEGORIES = [
    "🥦 야채/채소",
    "🍎 과일",
    "🥩 정육/계란",
    "🐟 수산",
    "🍞 가공/베이커리",
    "🧃 음료/주류",
    "🧴 생활용품",
    "기타",
]

# 상단 헤더 & 카트 총액 표시
total_cart_amount = sum(item["total"] for item in st.session_state.cart_list)

col_title, col_total = st.columns([1.2, 1])
with col_title:
    st.markdown("### 🛒 장보기 메모")
with col_total:
    st.markdown(
        f"<h3 style='text-align: right; color: #1E88E5;'>합계: {total_cart_amount:,}원</h3>",
        unsafe_allow_html=True,
    )

st.divider()

# 메인 레이아웃: 좌(살 물건 목록) / 우(카트 담긴 목록 + 등록)
col_left, col_right = st.columns(2)

# ==========================================
# 좌측: 살 물건 리스트 (중요도/비싼경우 건너뛰기 고려)
# ==========================================
with col_left:
    st.subheader("📝 살 물건")

    if not st.session_state.todo_list:
        st.caption("목록이 비어있습니다. 우측에서 추가해주세요.")
    else:
        sorted_todos = sorted(
            st.session_state.todo_list, key=lambda x: x["priority"]
        )

        for item in sorted_todos:
            p_badge = {1: "🔴1", 2: "🟡2", 3: "🟢3"}.get(
                item["priority"], ""
            )

            with st.container():
                c1, c2, c3 = st.columns([2.5, 1, 1])

                with c1:
                    st.write(
                        f"**{p_badge} {item['name']}**  \n<small>{item['category']}</small>",
                        unsafe_allow_html=True,
                    )

                # 담기 (가격/수량 입력 팝오버)
                with c2:
                    with st.popover("담기"):
                        st.caption(f"🛒 '{item['name']}' 카트 담기")
                        price = st.number_input(
                            "단가(원)",
                            min_value=0,
                            step=100,
                            key=f"p_{item['id']}",
                        )
                        qty = st.number_input(
                            "수량",
                            min_value=1,
                            value=1,
                            step=1,
                            key=f"q_{item['id']}",
                        )

                        if st.button("확인", key=f"confirm_{item['id']}"):
                            st.session_state.cart_list.append(
                                {
                                    "name": item["name"],
                                    "category": item["category"],
                                    "price": price,
                                    "qty": qty,
                                    "total": price * qty,
                                }
                            )
                            st.session_state.todo_list = [
                                t
                                for t in st.session_state.todo_list
                                if t["id"] != item["id"]
                            ]
                            st.rerun()

                # 비싸서 패스 / 삭제 버튼
                with c3:
                    if st.button("패스", key=f"del_{item['id']}"):
                        st.session_state.todo_list = [
                            t
                            for t in st.session_state.todo_list
                            if t["id"] != item["id"]
                        ]
                        st.rerun()

# ==========================================
# 우측: 품목 추가 & 카트에 담긴 물건 현황
# ==========================================
with col_right:
    # 신규 품목 추가
    with st.popover("➕ 새 품목 추가", use_container_width=True):
        new_cat = st.selectbox("카테고리", CATEGORIES)
        new_name = st.text_input("상품명", placeholder="예: 시금치 1단")
        new_prio = st.radio(
            "중요도",
            [1, 2, 3],
            format_func=lambda x: f"{x}순위 ({'필수' if x==1 else '보통' if x==2 else '여유'})",
            horizontal=True,
        )

        if st.button("메모에 등록", type="primary"):
            if new_name.strip():
                st.session_state.item_id_counter += 1
                st.session_state.todo_list.append(
                    {
                        "id": st.session_state.item_id_counter,
                        "category": new_cat,
                        "name": new_name.strip(),
                        "priority": new_prio,
                    }
                )
                st.rerun()

    st.subheader("🛒 담은 바구니")

    if not st.session_state.cart_list:
        st.caption("아직 담은 물건이 없습니다.")
    else:
        for idx, cart_item in enumerate(st.session_state.cart_list):
            cc1, cc2 = st.columns([3, 1])
            with cc1:
                st.write(
                    f"**{cart_item['name']}** ({cart_item['qty']}개)  \n"
                    f"<small>{cart_item['price']:,}원 × {cart_item['qty']} = **{cart_item['total']:,}원**</small>",
                    unsafe_allow_html=True,
                )
            with cc2:
                if st.button("취소", key=f"cart_del_{idx}"):
                    st.session_state.cart_list.pop(idx)
                    st.rerun()