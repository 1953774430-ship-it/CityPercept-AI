from __future__ import annotations

import hmac
import json
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parent
LABELER_SCRIPT = (
    PROJECT_ROOT / "llm标注结果" / "scripts" / "openai_image_labeler.py"
)
LABELER_ENV = PROJECT_ROOT / "llm标注结果" / ".env"

METRIC_DIMENSIONS = (
    "M2 步行友好感",
    "M3 景观舒适感",
    "M4 历史风貌感",
    "M5 商业活力感",
)

SECRET_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "LABELER_IMAGE_DETAIL",
    "LABELER_TEMPERATURE",
    "LABELER_MAX_OUTPUT_TOKENS",
    "LABELER_VERBOSITY",
)


def apply_page_style() -> None:
    st.markdown(
        """
        <style>
        :root {
            --cp-ink: #17212b;
            --cp-muted: #5f6b7a;
            --cp-accent: #0f766e;
            --cp-line: #d9e1e8;
            --cp-surface: #ffffff;
        }

        html, body, [data-testid="stAppViewContainer"] {
            font-family: "Microsoft YaHei", "PingFang SC",
                "Noto Sans SC", sans-serif;
        }

        [data-testid="stAppViewContainer"] {
            color: var(--cp-ink);
            background: #f6f8fa;
        }

        [data-testid="stHeader"] {
            background: transparent;
        }

        .block-container {
            max-width: 1180px;
            padding-top: 2.4rem;
            padding-bottom: 3.5rem;
        }

        h1, h2, h3 {
            color: var(--cp-ink);
        }

        h1 {
            font-size: 2.6rem !important;
            font-weight: 800 !important;
            letter-spacing: 0 !important;
            line-height: 1.18 !important;
            margin-bottom: 0.25rem !important;
        }

        h1::after {
            display: block;
            width: 58px;
            height: 4px;
            margin-top: 0.7rem;
            content: "";
            background: var(--cp-accent);
            border-radius: 2px;
        }

        h2 {
            font-size: 1.55rem !important;
            font-weight: 750 !important;
            letter-spacing: 0 !important;
            margin-top: 1.8rem !important;
        }

        h3 {
            font-size: 1.15rem !important;
            font-weight: 700 !important;
            letter-spacing: 0 !important;
        }

        [data-testid="stSubheader"] {
            color: var(--cp-muted);
            font-weight: 500;
        }

        [data-testid="stMetric"] {
            min-height: 112px;
            padding: 1rem 1.1rem;
            background: var(--cp-surface);
            border: 1px solid var(--cp-line);
            border-radius: 8px;
        }

        [data-testid="stMetricLabel"] {
            color: var(--cp-muted);
            font-size: 0.92rem !important;
            font-weight: 600 !important;
        }

        [data-testid="stMetricValue"] {
            color: var(--cp-accent);
            font-size: 1.85rem !important;
            font-weight: 800 !important;
        }

        [data-testid="stMetricDelta"] {
            font-size: 0.82rem !important;
        }

        [data-testid="stFileUploaderDropzone"] {
            padding: 1.2rem;
            background: var(--cp-surface);
            border: 1px dashed #9db0bd;
            border-radius: 8px;
        }

        [data-testid="stFileUploaderDropzone"]:hover {
            border-color: var(--cp-accent);
            background: #f2faf8;
        }

        .stButton > button,
        .stDownloadButton > button {
            min-height: 2.7rem;
            border-radius: 7px !important;
            font-weight: 700 !important;
        }

        .stButton > button[kind="primary"] {
            color: #ffffff;
            background: var(--cp-accent);
            border-color: var(--cp-accent);
            box-shadow: none;
        }

        .stButton > button[kind="primary"]:hover {
            background: #0b5f59;
            border-color: #0b5f59;
        }

        .stDownloadButton > button {
            color: var(--cp-accent);
            background: var(--cp-surface);
            border-color: #9db0bd;
        }

        .stDownloadButton > button:hover {
            color: #0b5f59;
            border-color: var(--cp-accent);
        }

        [data-testid="stDataFrame"] {
            overflow: hidden;
            background: var(--cp-surface);
            border: 1px solid var(--cp-line);
            border-radius: 8px;
        }

        [data-testid="stAlert"] {
            border-radius: 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def require_password() -> None:
    try:
        expected_password = str(st.secrets.get("APP_PASSWORD", "")).strip()
    except Exception:
        expected_password = ""

    expected_password = (
        expected_password
        or os.environ.get("CITYPERCEPT_PASSWORD", "").strip()
    )
    if not expected_password:
        return

    if st.session_state.get("citypercept_authenticated", False):
        return

    st.title("CityPercept AI")
    st.subheader("城市空间体验智能分析平台")
    st.caption("请输入访问密码后继续")
    password = st.text_input(
        "访问密码",
        type="password",
        placeholder="请输入访问密码",
    )
    if st.button("进入系统", type="primary"):
        if hmac.compare_digest(password, expected_password):
            st.session_state["citypercept_authenticated"] = True
            st.rerun()
        st.error("密码错误，请重新输入。")
    st.stop()


def find_annotation(output_root: Path) -> dict[str, Any]:
    annotations = sorted(
        output_root.rglob("annotation.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not annotations:
        raise RuntimeError("评分脚本已运行，但没有找到 annotation.json。")
    return json.loads(annotations[0].read_text(encoding="utf-8"))


def run_ai_analysis(
    image_bytes: bytes,
    filename: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not LABELER_SCRIPT.exists():
        raise FileNotFoundError(f"找不到评分脚本：{LABELER_SCRIPT}")

    with tempfile.TemporaryDirectory(prefix="citypercept_") as temporary_dir:
        temp_root = Path(temporary_dir)
        image_path = temp_root / (Path(filename).name or "upload.jpg")
        output_root = temp_root / "runs"
        image_path.write_bytes(image_bytes)

        command = [
            sys.executable,
            str(LABELER_SCRIPT),
            "--image",
            str(image_path),
            "--output-root",
            str(output_root),
            "--env-file",
            str(LABELER_ENV),
            "--workers",
            "1",
        ]
        if dry_run:
            command.append("--dry-run")

        process_env = os.environ.copy()
        try:
            for key in SECRET_ENV_KEYS:
                value = st.secrets.get(key, "")
                if str(value).strip():
                    process_env[key] = str(value)
        except Exception:
            pass

        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=process_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=420,
            check=False,
        )

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            if "401 Unauthorized" in detail:
                raise RuntimeError(
                    "模型接口认证失败（401）。请更新 "
                    "llm标注结果/.env 中的 OPENAI_API_KEY，"
                    "并确认 OPENAI_BASE_URL 与密钥来源一致。"
                )
            if "403 Forbidden" in detail:
                raise RuntimeError(
                    "模型接口拒绝访问（403）。请检查 API Key 权限、"
                    "模型名称和接口地址。"
                )
            raise RuntimeError(detail or "AI 评分脚本执行失败。")

        return find_annotation(output_root)


def build_evaluation_lookup(
    annotation: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        item.get("dimension", ""): item
        for item in annotation.get("evaluations", [])
        if isinstance(item, dict)
    }


def format_score(evaluation: dict[str, Any] | None) -> str:
    if not evaluation:
        return "暂无"
    score = str(evaluation.get("score", "")).strip()
    return "NA" if score == "NA" else f"{score} / 5"


def api_key_is_configured() -> bool:
    try:
        if str(st.secrets.get("OPENAI_API_KEY", "")).strip():
            return True
    except Exception:
        pass

    if os.environ.get("OPENAI_API_KEY", "").strip():
        return True

    if not LABELER_ENV.exists():
        return False

    for raw_line in LABELER_ENV.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "OPENAI_API_KEY":
            continue
        value = value.strip().strip("\"'")
        return bool(value and value != "replace_with_your_api_key")
    return False


def render_analysis(
    annotation: dict[str, Any],
    filename: str,
    is_demo: bool = False,
) -> None:
    evaluations = build_evaluation_lookup(annotation)

    if is_demo:
        st.warning("当前展示为演示数据，未调用真实模型。")
    else:
        st.success("图片分析完成")
    st.subheader("空间品质评分")

    columns = st.columns(4)
    for column, dimension in zip(columns, METRIC_DIMENSIONS):
        label = dimension.split(maxsplit=1)[1]
        column.metric(label, format_score(evaluations.get(dimension)))

    st.subheader("AI 空间分析")
    st.write(annotation.get("image_overview", "暂无整体判断。"))

    detail_rows = []
    for item in annotation.get("evaluations", []):
        detail_rows.append(
            {
                "评价维度": item.get("dimension", ""),
                "评分": item.get("score", ""),
                "画面依据": item.get("visual_evidence", ""),
                "判断理由": item.get("reasoning", ""),
            }
        )
    st.dataframe(detail_rows, use_container_width=True, hide_index=True)

    summary = annotation.get("score_summary", {})
    overall = evaluations.get("Y1 综合空间感知评价")
    summary_columns = st.columns(3)
    summary_columns[0].metric("综合评分", format_score(overall))
    summary_columns[1].metric(
        "有效指标",
        str(summary.get("valid_count", len(detail_rows))),
    )
    summary_columns[2].metric(
        "平均分",
        str(summary.get("mean_score", "暂无")),
    )

    output_name = f"{Path(filename).stem}_analysis.json"
    st.download_button(
        "下载结构化评分结果",
        data=json.dumps(annotation, ensure_ascii=False, indent=2),
        file_name=output_name,
        mime="application/json",
    )


st.set_page_config(
    page_title="CityPercept AI",
    page_icon="🏙️",
    layout="wide",
)

apply_page_style()
require_password()

st.title("CityPercept AI")
st.subheader("城市空间体验智能分析平台")
st.write(
    "上传一张街景图片，AI 将从步行友好、景观舒适、历史风貌、"
    "商业活力等维度分析城市空间品质。"
)

uploaded_file = st.file_uploader(
    "上传街景图片",
    type=["jpg", "jpeg", "png"],
)

if uploaded_file is not None:
    api_key_ready = api_key_is_configured()
    image = Image.open(uploaded_file)
    image_bytes = uploaded_file.getvalue()
    image_key = hashlib.sha256(image_bytes).hexdigest()

    st.image(
        image,
        caption="上传的街景图片",
        use_container_width=True,
    )

    if not api_key_ready:
        st.info("当前未配置模型密钥，可先预览平台结果页。")

    action_label = "开始智能分析" if api_key_ready else "预览演示结果"
    if st.button(action_label, type="primary"):
        with st.spinner("AI 正在分析街景空间……"):
            try:
                annotation = run_ai_analysis(
                    image_bytes,
                    uploaded_file.name,
                    dry_run=not api_key_ready,
                )
            except Exception as exc:
                st.error(f"分析失败：{exc}")
                st.stop()

        st.session_state["citypercept_result"] = {
            "image_key": image_key,
            "filename": uploaded_file.name,
            "annotation": annotation,
            "is_demo": not api_key_ready,
        }

    saved_result = st.session_state.get("citypercept_result")
    if saved_result and saved_result.get("image_key") == image_key:
        render_analysis(
            saved_result["annotation"],
            saved_result["filename"],
            saved_result.get("is_demo", False),
        )
