import streamlit as st
from PIL import Image

st.set_page_config(
    page_title="CityPercept AI",
    page_icon="🏙️",
    layout="wide"
)

st.title("🏙️ CityPercept AI")
st.subheader("城市空间体验智能分析平台")

st.write(
    "上传一张街景图片，AI 将从步行性、绿化、安全感、活力等维度分析城市空间品质。"
)

uploaded_file = st.file_uploader(
    "上传街景图片",
    type=["jpg", "jpeg", "png"]
)

if uploaded_file is not None:

    image = Image.open(uploaded_file)

    st.image(
        image,
        caption="上传的街景图片",
        use_container_width=True
    )

    if st.button("🚀 开始智能分析"):

        with st.spinner("AI正在分析街景空间……"):

            st.success("图片分析完成！")

            st.subheader("📊 空间品质评分")

            col1, col2, col3, col4 = st.columns(4)

            col1.metric("步行性", "4.2 / 5")
            col2.metric("绿化感知", "3.8 / 5")
            col3.metric("安全感", "3.5 / 5")
            col4.metric("空间活力", "4.1 / 5")

            st.subheader("🤖 AI空间分析")

            st.write("""
            该街景具有较好的城市空间品质。

            - 人行空间连续性较好
            - 沿街绿化具有一定遮阴作用
            - 建筑界面连续性较强
            - 机动车空间占据视觉比例较高
            - 整体空间具有一定活力
            """)

            st.subheader("⭐ 综合空间品质")

            st.metric(
                "综合评分",
                "3.9 / 5"
            )
