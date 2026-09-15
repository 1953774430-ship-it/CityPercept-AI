#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import csv
import hashlib
import json
import logging
import mimetypes
import os
import random
import shutil
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zipfile import ZipFile

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: httpx. Install it with `pip install httpx`."
    ) from exc


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_IMAGE_DIR = REPO_ROOT / "四方图_1系统最新"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "runs"
DEFAULT_PROMPT_PATH = REPO_ROOT / "prompt.md"
DEFAULT_SCHEMA_PATH = REPO_ROOT / "schemas" / "annotation_schema.json"
DEFAULT_RESULT_TEMPLATE_PATH = REPO_ROOT / "3.3_03_方向图评分结果模板.xlsx"
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4.1-mini"
IMAGE_DETAIL_CHOICES = {"low", "high", "auto"}
VERBOSITY_CHOICES = {"low", "medium", "high"}
PROMPT_IMAGE_PATH_PLACEHOLDER = "{{IMAGE_PATH}}"
PROMPT_IMAGE_INPUT_PLACEHOLDER = "{{IMAGE_INPUT}}"
PROMPT_IMAGE_INPUT_SENTINEL = "[同一条请求中的下一段图片输入内容就是待标注图片]"
REVIEW_REPORT_FILENAME = "review.md"
CSV_EXPORT_FILENAME = f"{DEFAULT_RESULT_TEMPLATE_PATH.stem}.csv"
XLSX_NAMESPACES = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
DEFAULT_TEMPLATE_HEADERS = [
    "record_id",
    "point_id",
    "direction",
    "image_name",
    "run_id",
    "model_name",
    "prompt_version",
    "temperature",
    "X8_score",
    "X8_evidence",
    "X9_score",
    "X9_evidence",
    "X9_na_reason",
    "X10_score",
    "X10_evidence",
    "M1_score",
    "M1_evidence",
    "M2_score",
    "M2_evidence",
    "M3_score",
    "M3_evidence",
    "M4_score",
    "M4_evidence",
    "M5_score",
    "M5_evidence",
    "M5_na_reason",
    "Y1_score",
    "Y1_evidence",
    "Y2_score",
    "Y2_evidence",
    "Y3_score",
    "Y3_evidence",
    "json_valid",
    "manual_check_flag",
    "remarks",
]
DIMENSION_EXPORT_SPECS = (
    {
        "dimension": "X8 历史立面显现度",
        "code": "X8",
        "name": "历史立面显现度",
        "score_col": "X8_score",
        "evidence_col": "X8_evidence",
        "na_reason_col": None,
    },
    {
        "dimension": "X9 商业界面开放度",
        "code": "X9",
        "name": "商业界面开放度",
        "score_col": "X9_score",
        "evidence_col": "X9_evidence",
        "na_reason_col": "X9_na_reason",
    },
    {
        "dimension": "X10 风貌协调度",
        "code": "X10",
        "name": "风貌协调度",
        "score_col": "X10_score",
        "evidence_col": "X10_evidence",
        "na_reason_col": None,
    },
    {
        "dimension": "M1 空间开敞感",
        "code": "M1",
        "name": "空间开敞感",
        "score_col": "M1_score",
        "evidence_col": "M1_evidence",
        "na_reason_col": None,
    },
    {
        "dimension": "M2 步行友好感",
        "code": "M2",
        "name": "步行友好感",
        "score_col": "M2_score",
        "evidence_col": "M2_evidence",
        "na_reason_col": None,
    },
    {
        "dimension": "M3 景观舒适感",
        "code": "M3",
        "name": "景观舒适感",
        "score_col": "M3_score",
        "evidence_col": "M3_evidence",
        "na_reason_col": None,
    },
    {
        "dimension": "M4 历史风貌感",
        "code": "M4",
        "name": "历史风貌感",
        "score_col": "M4_score",
        "evidence_col": "M4_evidence",
        "na_reason_col": None,
    },
    {
        "dimension": "M5 商业活力感",
        "code": "M5",
        "name": "商业活力感",
        "score_col": "M5_score",
        "evidence_col": "M5_evidence",
        "na_reason_col": "M5_na_reason",
    },
    {
        "dimension": "Y1 综合空间感知评价",
        "code": "Y1",
        "name": "综合空间感知评价",
        "score_col": "Y1_score",
        "evidence_col": "Y1_evidence",
        "na_reason_col": None,
    },
    {
        "dimension": "Y2 停留意愿",
        "code": "Y2",
        "name": "停留意愿",
        "score_col": "Y2_score",
        "evidence_col": "Y2_evidence",
        "na_reason_col": None,
    },
    {
        "dimension": "Y3 情绪愉悦度",
        "code": "Y3",
        "name": "情绪愉悦度",
        "score_col": "Y3_score",
        "evidence_col": "Y3_evidence",
        "na_reason_col": None,
    },
)
EXPECTED_DIMENSIONS = {spec["dimension"] for spec in DIMENSION_EXPORT_SPECS}
ALLOWED_SCORES = {"1", "2", "3", "4", "5", "NA"}


@dataclass(frozen=True)
class RuntimeConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    max_retries: int
    max_elapsed_seconds: float
    initial_backoff_seconds: float
    max_backoff_seconds: float
    workers: int
    image_detail: str
    temperature: float
    max_output_tokens: int
    verbosity: str
    prompt_text: str
    prompt_version: str
    schema: dict[str, Any]
    dry_run: bool
    run_dir: Path
    api_url: str


@dataclass(frozen=True)
class ResolvedSettings:
    env_file: Path
    loaded_env_keys: tuple[str, ...]
    image_dir: Path
    output_root: Path
    prompt_file: Path
    schema_file: Path
    api_key: str | None
    base_url: str
    model: str
    workers: int
    timeout_seconds: float
    max_retries: int
    max_elapsed_seconds: float
    initial_backoff_seconds: float
    max_backoff_seconds: float
    image_detail: str
    temperature: float
    max_output_tokens: int
    verbosity: str


@dataclass(frozen=True)
class ImageTask:
    index: int
    image_path: Path
    image_id: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use the OpenAI Chat Completions API to annotate images in standard OpenAI format.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument(
        "--image",
        type=Path,
        help="Run a single-image test with the given image path.",
    )
    selector.add_argument(
        "--count",
        type=int,
        help="Run a fixed-number test using the first N images from --image-dir.",
    )
    selector.add_argument(
        "--all",
        action="store_true",
        help="Run on all images found under --image-dir.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="Path to the .env file. Missing files are ignored.",
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=None,
        help="Source image directory. Supports LABELER_IMAGE_DIR in .env.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Root folder for run outputs. Supports LABELER_OUTPUT_ROOT in .env.",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        default=None,
        help="Prompt file. Supports LABELER_PROMPT_FILE in .env.",
    )
    parser.add_argument(
        "--schema-file",
        type=Path,
        default=None,
        help="JSON schema file. Supports LABELER_SCHEMA_FILE in .env.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible base URL. Supports OPENAI_BASE_URL in .env.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Vision-capable model. Supports OPENAI_MODEL in .env.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key. Supports OPENAI_API_KEY in .env.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Thread-level concurrency. Supports LABELER_WORKERS in .env.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Per-request timeout. Supports LABELER_TIMEOUT_SECONDS in .env.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Maximum retry count after the first attempt. Supports LABELER_MAX_RETRIES in .env.",
    )
    parser.add_argument(
        "--max-elapsed-seconds",
        type=float,
        default=None,
        help="Maximum total elapsed time per image task. Supports LABELER_MAX_ELAPSED_SECONDS in .env.",
    )
    parser.add_argument(
        "--initial-backoff-seconds",
        type=float,
        default=None,
        help="Initial exponential backoff delay. Supports LABELER_INITIAL_BACKOFF_SECONDS in .env.",
    )
    parser.add_argument(
        "--max-backoff-seconds",
        type=float,
        default=None,
        help="Maximum exponential backoff delay. Supports LABELER_MAX_BACKOFF_SECONDS in .env.",
    )
    parser.add_argument(
        "--image-detail",
        choices=sorted(IMAGE_DETAIL_CHOICES),
        default=None,
        help="Vision detail level. Supports LABELER_IMAGE_DETAIL in .env.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Sampling temperature. Supports LABELER_TEMPERATURE in .env.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="Maximum output tokens. Supports LABELER_MAX_OUTPUT_TOKENS in .env.",
    )
    parser.add_argument(
        "--verbosity",
        choices=sorted(VERBOSITY_CHOICES),
        default=None,
        help="Response text verbosity. Supports LABELER_VERBOSITY in .env.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip real API calls and only generate requests plus directory layout.",
    )
    return parser.parse_args()


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if not normalized.endswith("/v1"):
        normalized = f"{normalized}/v1"
    return normalized


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_exists(path: Path, description: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"{description} not found: {path}")
    return path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_cli_path(path: Path) -> Path:
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def resolve_env_path(raw_value: str, base_dir: Path) -> Path:
    path = Path(raw_value)
    return path if path.is_absolute() else (base_dir / path).resolve()


def parse_env_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
        if raw_value.strip().startswith('"'):
            value = bytes(value, "utf-8").decode("unicode_escape")
        return value
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def load_env_file(env_file: Path) -> dict[str, str]:
    if not env_file.exists():
        return {}

    env_values: dict[str, str] = {}
    for line_number, raw_line in enumerate(env_file.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid .env line {line_number}: {raw_line}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid .env line {line_number}: {raw_line}")
        env_values[key] = parse_env_value(raw_value)
    return env_values


def resolve_scalar_option(
    cli_value: Any,
    env_name: str,
    env_values: dict[str, str],
    default: Any,
    caster: Callable[[str], Any] | None = None,
) -> Any:
    if cli_value is not None:
        return cli_value

    raw_value = os.environ.get(env_name)
    if raw_value is None or raw_value == "":
        raw_value = env_values.get(env_name)
    if raw_value is None or raw_value == "":
        return default
    if caster is None:
        return raw_value
    try:
        return caster(raw_value)
    except ValueError as exc:
        raise ValueError(f"Invalid value for {env_name}: {raw_value}") from exc


def resolve_choice_option(
    cli_value: str | None,
    env_name: str,
    env_values: dict[str, str],
    default: str,
    allowed_values: set[str],
) -> str:
    value = resolve_scalar_option(cli_value, env_name, env_values, default)
    if value not in allowed_values:
        allowed = ", ".join(sorted(allowed_values))
        raise ValueError(f"Invalid value for {env_name}: {value}. Expected one of: {allowed}")
    return value


def resolve_path_option(
    cli_value: Path | None,
    env_name: str,
    env_values: dict[str, str],
    env_base_dir: Path,
    default: Path,
) -> Path:
    if cli_value is not None:
        return resolve_cli_path(cli_value)
    if env_name in os.environ and os.environ[env_name] != "":
        return resolve_env_path(os.environ[env_name], REPO_ROOT)
    if env_name in env_values and env_values[env_name] != "":
        return resolve_env_path(env_values[env_name], env_base_dir)
    return default.resolve()


def resolve_settings(args: argparse.Namespace, env_values: dict[str, str], env_file: Path) -> ResolvedSettings:
    env_base_dir = env_file.parent
    return ResolvedSettings(
        env_file=env_file,
        loaded_env_keys=tuple(sorted(env_values)),
        image_dir=resolve_path_option(args.image_dir, "LABELER_IMAGE_DIR", env_values, env_base_dir, DEFAULT_IMAGE_DIR),
        output_root=resolve_path_option(args.output_root, "LABELER_OUTPUT_ROOT", env_values, env_base_dir, DEFAULT_OUTPUT_ROOT),
        prompt_file=resolve_path_option(args.prompt_file, "LABELER_PROMPT_FILE", env_values, env_base_dir, DEFAULT_PROMPT_PATH),
        schema_file=resolve_path_option(args.schema_file, "LABELER_SCHEMA_FILE", env_values, env_base_dir, DEFAULT_SCHEMA_PATH),
        api_key=resolve_scalar_option(args.api_key, "OPENAI_API_KEY", env_values, None),
        base_url=resolve_scalar_option(args.base_url, "OPENAI_BASE_URL", env_values, DEFAULT_BASE_URL),
        model=resolve_scalar_option(args.model, "OPENAI_MODEL", env_values, DEFAULT_MODEL),
        workers=resolve_scalar_option(args.workers, "LABELER_WORKERS", env_values, 50, int),
        timeout_seconds=resolve_scalar_option(args.timeout_seconds, "LABELER_TIMEOUT_SECONDS", env_values, 60.0, float),
        max_retries=resolve_scalar_option(args.max_retries, "LABELER_MAX_RETRIES", env_values, 20, int),
        max_elapsed_seconds=resolve_scalar_option(args.max_elapsed_seconds, "LABELER_MAX_ELAPSED_SECONDS", env_values, 300.0, float),
        initial_backoff_seconds=resolve_scalar_option(args.initial_backoff_seconds, "LABELER_INITIAL_BACKOFF_SECONDS", env_values, 1.0, float),
        max_backoff_seconds=resolve_scalar_option(args.max_backoff_seconds, "LABELER_MAX_BACKOFF_SECONDS", env_values, 60.0, float),
        image_detail=resolve_choice_option(args.image_detail, "LABELER_IMAGE_DETAIL", env_values, "auto", IMAGE_DETAIL_CHOICES),
        temperature=resolve_scalar_option(args.temperature, "LABELER_TEMPERATURE", env_values, 0.0, float),
        max_output_tokens=resolve_scalar_option(args.max_output_tokens, "LABELER_MAX_OUTPUT_TOKENS", env_values, 10000, int),
        verbosity=resolve_choice_option(args.verbosity, "LABELER_VERBOSITY", env_values, "low", VERBOSITY_CHOICES),
    )


def create_run_dir(output_root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = output_root / f"run_{timestamp}"
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = output_root / f"run_{timestamp}_{suffix:02d}"
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def setup_logging(run_dir: Path) -> logging.Logger:
    logger = logging.getLogger("openai_image_labeler")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def collect_images(image_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(image_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]


def slugify_stem(text: str, limit: int = 80) -> str:
    cleaned = []
    for char in text:
        if char.isalnum() or char in {"_", "-", "."}:
            cleaned.append(char)
        else:
            cleaned.append("_")
    compact = "".join(cleaned).strip("_") or "image"
    return compact[:limit]


def make_image_id(index: int, image_path: Path) -> str:
    digest = hashlib.sha1(str(image_path).encode("utf-8")).hexdigest()[:10]
    return f"{index:05d}_{slugify_stem(image_path.stem)}_{digest}"


def select_tasks(args: argparse.Namespace, image_dir: Path) -> list[ImageTask]:
    if args.image:
        image_path = resolve_cli_path(args.image).resolve()
        ensure_exists(image_path, "image")
        return [ImageTask(index=1, image_path=image_path, image_id=make_image_id(1, image_path))]

    ensure_exists(image_dir, "image directory")
    images = collect_images(image_dir)
    if not images:
        raise FileNotFoundError(f"No supported images found under: {image_dir}")

    if args.all:
        selected = images
    elif args.count is not None:
        if args.count <= 0:
            raise ValueError("--count must be greater than 0")
        selected = images[: args.count]
    else:
        selected = images[:1]

    return [
        ImageTask(index=index, image_path=path, image_id=make_image_id(index, path))
        for index, path in enumerate(selected, start=1)
    ]


def guess_mime_type(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(image_path.name)
    return mime_type or "application/octet-stream"


def encode_image_as_data_url(image_path: Path) -> str:
    image_bytes = image_path.read_bytes()
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{guess_mime_type(image_path)};base64,{encoded}"


def render_prompt(task: ImageTask, prompt_text: str) -> str:
    return (
        prompt_text
        .replace(PROMPT_IMAGE_PATH_PLACEHOLDER, str(task.image_path))
        .replace(PROMPT_IMAGE_INPUT_PLACEHOLDER, PROMPT_IMAGE_INPUT_SENTINEL)
    )


def build_verbosity_instruction(verbosity: str) -> str:
    return (
        "Return only JSON that matches the provided response schema. "
        f"For free-text string fields inside that JSON, keep verbosity {verbosity}."
    )


def build_payload(task: ImageTask, config: RuntimeConfig, rendered_prompt: str) -> dict[str, Any]:
    return {
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": config.max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "image_annotation",
                "strict": True,
                "schema": config.schema,
            },
        },
        "messages": [
            {
                "role": "system",
                "content": build_verbosity_instruction(config.verbosity),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": rendered_prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": encode_image_as_data_url(task.image_path),
                            "detail": config.image_detail,
                        },
                    },
                ],
            }
        ],
    }


def build_request_log(url: str, headers: dict[str, str], body: str) -> str:
    masked_headers = dict(headers)
    if "Authorization" in masked_headers:
        masked_headers["Authorization"] = "Bearer ***"
    header_lines = "\n".join(f"{key}: {value}" for key, value in masked_headers.items())
    return f"POST {url}\n{header_lines}\n\n{body}"


def build_response_log(response: httpx.Response) -> str:
    header_lines = "\n".join(f"{key}: {value}" for key, value in response.headers.items())
    return (
        f"HTTP {response.http_version} {response.status_code}\n"
        f"{header_lines}\n\n"
        f"{response.text}"
    )


_thread_local = threading.local()


def get_client(config: RuntimeConfig) -> httpx.Client:
    client = getattr(_thread_local, "client", None)
    if client is None:
        timeout = httpx.Timeout(
            connect=config.timeout_seconds,
            read=config.timeout_seconds,
            write=config.timeout_seconds,
            pool=config.timeout_seconds,
        )
        client = httpx.Client(timeout=timeout)
        _thread_local.client = client
    return client


def extract_output_text(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices") or []
    if not choices:
        raise RuntimeError("No choices found in chat completion response.")

    first_choice = choices[0]
    finish_reason = first_choice.get("finish_reason")
    if finish_reason in {"length", "content_filter"}:
        raise RuntimeError(f"Chat completion stopped with finish_reason={finish_reason}.")

    message = first_choice.get("message") or {}
    refusal = message.get("refusal")
    if refusal:
        raise RuntimeError(f"Model refusal: {str(refusal).strip()}")

    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
        if text:
            return text

    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    text_parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"text", "output_text"}:
                text_value = item.get("text", "")
                if text_value:
                    text_parts.append(text_value)
            elif item_type == "refusal":
                raise RuntimeError(f"Model refusal: {item.get('refusal', '')}".strip())
        text = "\n".join(part for part in text_parts if part).strip()
        if text:
            return text

    raise RuntimeError("No assistant message content found in chat completion response.")


def compute_backoff_seconds(
    attempt_number: int,
    config: RuntimeConfig,
    deadline_monotonic: float,
) -> float:
    base_delay = min(
        config.initial_backoff_seconds * (2 ** (attempt_number - 1)),
        config.max_backoff_seconds,
    )
    jitter = random.uniform(0, base_delay * 0.1)
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        return 0.0
    return max(0.0, min(base_delay + jitter, remaining))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_first_row_from_xlsx(template_path: Path) -> list[str]:
    with ZipFile(template_path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("a:si", XLSX_NAMESPACES):
                shared_strings.append(
                    "".join(
                        node.text or ""
                        for node in item.iterfind(".//a:t", XLSX_NAMESPACES)
                    )
                )

        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relationship_map = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in relationships_root
        }

        sheets = workbook_root.find("a:sheets", XLSX_NAMESPACES)
        if sheets is None or not list(sheets):
            raise ValueError(f"No worksheet found in template: {template_path}")

        first_sheet = list(sheets)[0]
        relationship_id = first_sheet.attrib[
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        ]
        worksheet_target = relationship_map[relationship_id]
        worksheet_root = ET.fromstring(archive.read(f"xl/{worksheet_target}"))
        sheet_data = worksheet_root.find("a:sheetData", XLSX_NAMESPACES)
        if sheet_data is None or not list(sheet_data):
            raise ValueError(f"No row data found in template: {template_path}")

        first_row = list(sheet_data)[0]
        headers: list[str] = []
        for cell in first_row:
            cell_type = cell.attrib.get("t")
            value_node = cell.find("a:v", XLSX_NAMESPACES)
            inline_node = cell.find("a:is", XLSX_NAMESPACES)
            if cell_type == "s" and value_node is not None and value_node.text is not None:
                headers.append(shared_strings[int(value_node.text)])
            elif inline_node is not None:
                headers.append(
                    "".join(
                        node.text or ""
                        for node in inline_node.iterfind(".//a:t", XLSX_NAMESPACES)
                    )
                )
            elif value_node is not None:
                headers.append(value_node.text or "")
            else:
                headers.append("")
        return headers


def load_template_headers(template_path: Path) -> list[str]:
    if not template_path.exists():
        return list(DEFAULT_TEMPLATE_HEADERS)
    try:
        headers = read_first_row_from_xlsx(template_path)
        return headers or list(DEFAULT_TEMPLATE_HEADERS)
    except Exception:
        return list(DEFAULT_TEMPLATE_HEADERS)


def parse_image_metadata(image_path: Path) -> dict[str, str]:
    parts = image_path.stem.split("_")
    metadata = {
        "source_record_key": "",
        "point_id": "",
        "longitude": "",
        "latitude": "",
        "direction": "",
        "street_name": "",
        "capture_period": "",
        "extra_tag": "",
    }
    if len(parts) >= 8:
        (
            metadata["source_record_key"],
            metadata["point_id"],
            metadata["longitude"],
            metadata["latitude"],
            metadata["direction"],
            metadata["street_name"],
            metadata["capture_period"],
            metadata["extra_tag"],
        ) = parts[:8]
    return metadata


def build_evaluation_lookup(annotation: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(annotation, dict):
        return {}
    evaluations = annotation.get("evaluations")
    if not isinstance(evaluations, list):
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for item in evaluations:
        if not isinstance(item, dict):
            continue
        dimension = item.get("dimension")
        if isinstance(dimension, str) and dimension not in lookup:
            lookup[dimension] = item
    return lookup


def is_annotation_valid(annotation: dict[str, Any] | None) -> bool:
    if not isinstance(annotation, dict):
        return False
    if not isinstance(annotation.get("image_overview"), str):
        return False
    evaluation_lookup = build_evaluation_lookup(annotation)
    if set(evaluation_lookup) != EXPECTED_DIMENSIONS:
        return False
    for spec in DIMENSION_EXPORT_SPECS:
        evaluation = evaluation_lookup.get(spec["dimension"])
        if not isinstance(evaluation, dict):
            return False
        if evaluation.get("score") not in ALLOWED_SCORES:
            return False
        if not isinstance(evaluation.get("visual_evidence"), str):
            return False
        if not isinstance(evaluation.get("reasoning"), str):
            return False

    score_summary = annotation.get("score_summary")
    if not isinstance(score_summary, dict):
        return False
    if not isinstance(score_summary.get("valid_count"), int):
        return False
    if not isinstance(score_summary.get("mean_score"), (int, float)):
        return False
    for key in ["highest", "lowest"]:
        summary_item = score_summary.get(key)
        if not isinstance(summary_item, dict):
            return False
        if summary_item.get("dimension") not in EXPECTED_DIMENSIONS:
            return False
        if summary_item.get("score") not in ALLOWED_SCORES - {"NA"}:
            return False
    na_dimensions = score_summary.get("na_dimensions")
    if not isinstance(na_dimensions, list):
        return False
    return all(dimension in EXPECTED_DIMENSIONS for dimension in na_dimensions)


def format_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def markdown_cell(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return "-"
    return text.replace("|", "\\|").replace("\n", "<br>")


def format_dimension_summary(summary_item: Any) -> str:
    if not isinstance(summary_item, dict):
        return ""
    dimension = normalize_text(summary_item.get("dimension"))
    score = normalize_text(summary_item.get("score"))
    if not dimension:
        return ""
    if not score:
        return dimension
    return f"{dimension} ({score})"


def build_template_row(result: dict[str, Any], config: RuntimeConfig, record_id: int) -> dict[str, str]:
    image_path = Path(result["image_path"])
    metadata = parse_image_metadata(image_path)
    annotation = result.get("annotation")
    evaluation_lookup = build_evaluation_lookup(annotation)
    json_valid = is_annotation_valid(annotation)

    row = {
        "record_id": str(record_id),
        "point_id": metadata["point_id"],
        "direction": metadata["direction"],
        "image_name": image_path.name,
        "run_id": config.run_dir.name,
        "model_name": config.model,
        "prompt_version": config.prompt_version,
        "temperature": format_scalar(config.temperature),
        "json_valid": "1" if json_valid else "0",
        "manual_check_flag": "0",
    }

    remarks: list[str] = []
    if not result.get("success"):
        remarks.append(normalize_text(result.get("error")) or "task_failed")
    elif not json_valid:
        remarks.append("annotation_schema_mismatch")

    score_summary = annotation.get("score_summary") if isinstance(annotation, dict) else None
    if isinstance(score_summary, dict):
        na_dimensions = score_summary.get("na_dimensions")
        if isinstance(na_dimensions, list) and na_dimensions:
            remarks.append("NA: " + ", ".join(str(item) for item in na_dimensions))

    for spec in DIMENSION_EXPORT_SPECS:
        evaluation = evaluation_lookup.get(spec["dimension"], {})
        score = normalize_text(evaluation.get("score"))
        visual_evidence = normalize_text(evaluation.get("visual_evidence"))
        reasoning = normalize_text(evaluation.get("reasoning"))
        row[spec["score_col"]] = score
        row[spec["evidence_col"]] = visual_evidence
        if spec["na_reason_col"] is not None:
            row[spec["na_reason_col"]] = reasoning if score == "NA" else ""

    row["remarks"] = "；".join(item for item in remarks if item)
    return row


def write_template_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def build_review_markdown(
    result: dict[str, Any],
    row: dict[str, str],
    config: RuntimeConfig,
) -> str:
    image_path = Path(result["image_path"])
    metadata = parse_image_metadata(image_path)
    annotation = result.get("annotation")
    evaluation_lookup = build_evaluation_lookup(annotation)
    score_summary = annotation.get("score_summary") if isinstance(annotation, dict) else {}

    lines = [
        "# 审阅说明",
        "",
        "## 基本信息",
        "",
        f"- record_id: {row.get('record_id', '')}",
        f"- image_id: {result.get('image_id', '')}",
        f"- image_name: {row.get('image_name', '')}",
        f"- source_record_key: {metadata['source_record_key']}",
        f"- point_id: {metadata['point_id']}",
        f"- direction: {metadata['direction']}",
        f"- longitude: {metadata['longitude']}",
        f"- latitude: {metadata['latitude']}",
        f"- street_name: {metadata['street_name']}",
        f"- capture_period: {metadata['capture_period']}",
        f"- run_id: {row.get('run_id', '')}",
        f"- model_name: {row.get('model_name', '')}",
        f"- prompt_version: {row.get('prompt_version', '')}",
        f"- temperature: {row.get('temperature', '')}",
        f"- json_valid: {row.get('json_valid', '')}",
        f"- manual_check_flag: {row.get('manual_check_flag', '')}",
        f"- 原图路径: {image_path}",
    ]

    if result.get("success") and isinstance(annotation, dict):
        lines.extend(
            [
                "",
                "## 场景概述",
                "",
                normalize_text(annotation.get("image_overview")) or "无",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## 运行状态",
                "",
                normalize_text(result.get("error")) or "任务失败，未生成可用标注结果。",
            ]
        )

    lines.extend(
        [
            "",
            "## 逐项评分",
            "",
            "| 指标代码 | 指标名称 | 分数 | 视觉证据 | 评分理由 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for spec in DIMENSION_EXPORT_SPECS:
        evaluation = evaluation_lookup.get(spec["dimension"], {})
        lines.append(
            "| {code} | {name} | {score} | {evidence} | {reasoning} |".format(
                code=spec["code"],
                name=spec["name"],
                score=markdown_cell(evaluation.get("score")),
                evidence=markdown_cell(evaluation.get("visual_evidence")),
                reasoning=markdown_cell(evaluation.get("reasoning")),
            )
        )

    if isinstance(score_summary, dict):
        na_dimensions = score_summary.get("na_dimensions")
        if isinstance(na_dimensions, list) and na_dimensions:
            na_summary = ", ".join(str(item) for item in na_dimensions)
        else:
            na_summary = "无"
        lines.extend(
            [
                "",
                "## 汇总",
                "",
                f"- valid_count: {format_scalar(score_summary.get('valid_count'))}",
                f"- mean_score: {format_scalar(score_summary.get('mean_score'))}",
                f"- highest: {format_dimension_summary(score_summary.get('highest')) or '无'}",
                f"- lowest: {format_dimension_summary(score_summary.get('lowest')) or '无'}",
                f"- na_dimensions: {na_summary}",
            ]
        )

    remarks = normalize_text(row.get("remarks"))
    if remarks:
        lines.extend(
            [
                "",
                "## 备注",
                "",
                remarks,
            ]
        )

    lines.extend(
        [
            "",
            "## 文件对照",
            "",
            f"- 图片副本: `{image_path.name}`",
            "- 结构化结果: `annotation.json` / `result.json`",
        ]
    )
    if (config.run_dir / "items" / str(result.get("image_id", "")) / "final_response.json").exists():
        lines.append("- 原始最终响应: `final_response.json`")
    return "\n".join(lines).rstrip() + "\n"


def export_review_materials(
    results: list[dict[str, Any]],
    rows: list[dict[str, str]],
    config: RuntimeConfig,
    logger: logging.Logger,
) -> None:
    for result, row in zip(results, rows):
        item_dir = config.run_dir / "items" / result["image_id"]
        item_dir.mkdir(parents=True, exist_ok=True)

        image_path = Path(result["image_path"])
        copied_image_path = item_dir / image_path.name
        if image_path.exists():
            shutil.copy2(image_path, copied_image_path)
        else:
            logger.warning("Source image not found for review export: %s", image_path)

        result_json_path = item_dir / "result.json"
        if not result_json_path.exists():
            write_json(result_json_path, result)

        annotation = result.get("annotation")
        annotation_json_path = item_dir / "annotation.json"
        if annotation is not None and not annotation_json_path.exists():
            write_json(annotation_json_path, annotation)

        review_path = item_dir / REVIEW_REPORT_FILENAME
        review_path.write_text(
            build_review_markdown(result, row, config),
            encoding="utf-8",
        )


def build_dry_run_annotation(schema: dict[str, Any]) -> dict[str, Any]:
    dimensions = (
        schema.get("properties", {})
        .get("evaluations", {})
        .get("items", {})
        .get("properties", {})
        .get("dimension", {})
        .get("enum", [])
    )
    if not dimensions:
        dimensions = ["dry_run"]

    evaluations = [
        {
            "dimension": dimension,
            "score": "3",
            "visual_evidence": "dry_run：未发起真实图片分析请求。",
            "reasoning": "dry_run 模式仅用于检查请求结构与落盘结果。",
        }
        for dimension in dimensions
    ]
    anchor_dimension = evaluations[0]["dimension"]
    return {
        "image_overview": "dry_run：未发起真实图片分析请求。",
        "evaluations": evaluations,
        "score_summary": {
            "valid_count": len(evaluations),
            "mean_score": 3.0,
            "highest": {"dimension": anchor_dimension, "score": "3"},
            "lowest": {"dimension": anchor_dimension, "score": "3"},
            "na_dimensions": [],
        },
    }


def process_task(task: ImageTask, config: RuntimeConfig, logger: logging.Logger) -> dict[str, Any]:
    task_dir = config.run_dir / "items" / task.image_id
    request_dir = task_dir / "requests"
    response_dir = task_dir / "responses"
    request_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)

    started_at = utc_now()
    task_started_monotonic = time.monotonic()
    deadline_monotonic = task_started_monotonic + config.max_elapsed_seconds
    rendered_prompt = render_prompt(task, config.prompt_text)
    (task_dir / "rendered_prompt.md").write_text(rendered_prompt + "\n", encoding="utf-8")
    payload = build_payload(task, config, rendered_prompt)
    request_body = json.dumps(payload, ensure_ascii=False, indent=2)
    request_headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    attempts: list[dict[str, Any]] = []
    final_error: str | None = None
    response_payload: dict[str, Any] | None = None
    annotation: dict[str, Any] | None = None
    raw_output_text: str | None = None

    for attempt_index in range(config.max_retries + 1):
        attempt_number = attempt_index + 1
        request_log_path = request_dir / f"attempt_{attempt_number:02d}.http.txt"
        request_log_path.write_text(
            build_request_log(config.api_url, request_headers, request_body),
            encoding="utf-8",
        )

        attempt_started_at = utc_now()
        attempt_started_monotonic = time.monotonic()
        response_log_path = response_dir / f"attempt_{attempt_number:02d}.http.txt"
        logger.info(
            "Submitting %s (attempt %s/%s)",
            task.image_id,
            attempt_number,
            config.max_retries + 1,
        )

        try:
            if config.dry_run:
                dry_run_payload = {
                    "status": "dry_run",
                    "message": "API call skipped because --dry-run was enabled.",
                }
                response_log_path.write_text(
                    "HTTP MOCK 200\ncontent-type: application/json\n\n"
                    + json.dumps(dry_run_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                annotation = build_dry_run_annotation(config.schema)
                raw_output_text = json.dumps(annotation, ensure_ascii=False)
                attempts.append(
                    {
                        "attempt_number": attempt_number,
                        "started_at": attempt_started_at,
                        "finished_at": utc_now(),
                        "duration_seconds": round(time.monotonic() - attempt_started_monotonic, 3),
                        "status": "dry_run",
                        "request_log_path": str(request_log_path.relative_to(config.run_dir)),
                        "response_log_path": str(response_log_path.relative_to(config.run_dir)),
                    }
                )
                break

            client = get_client(config)
            response = client.post(config.api_url, headers=request_headers, content=request_body)
            response_log_path.write_text(build_response_log(response), encoding="utf-8")
            response.raise_for_status()
            response_payload = response.json()

            raw_output_text = extract_output_text(response_payload)
            annotation = json.loads(raw_output_text)
            attempts.append(
                {
                    "attempt_number": attempt_number,
                    "started_at": attempt_started_at,
                    "finished_at": utc_now(),
                    "duration_seconds": round(time.monotonic() - attempt_started_monotonic, 3),
                    "status": "success",
                    "response_id": response_payload.get("id"),
                    "request_log_path": str(request_log_path.relative_to(config.run_dir)),
                    "response_log_path": str(response_log_path.relative_to(config.run_dir)),
                    "usage": response_payload.get("usage"),
                }
            )
            logger.info("Completed %s on attempt %s", task.image_id, attempt_number)
            break
        except Exception as exc:  # noqa: BLE001
            final_error = str(exc)
            duration_seconds = round(time.monotonic() - attempt_started_monotonic, 3)
            attempts.append(
                {
                    "attempt_number": attempt_number,
                    "started_at": attempt_started_at,
                    "finished_at": utc_now(),
                    "duration_seconds": duration_seconds,
                    "status": "failed_attempt",
                    "error": final_error,
                    "request_log_path": str(request_log_path.relative_to(config.run_dir)),
                    "response_log_path": (
                        str(response_log_path.relative_to(config.run_dir))
                        if response_log_path.exists()
                        else None
                    ),
                }
            )
            logger.warning(
                "Attempt %s failed for %s: %s",
                attempt_number,
                task.image_id,
                final_error,
            )
            if attempt_index >= config.max_retries or time.monotonic() >= deadline_monotonic:
                logger.error("Giving up on %s after %s attempts", task.image_id, attempt_number)
                break

            sleep_seconds = compute_backoff_seconds(attempt_number, config, deadline_monotonic)
            if sleep_seconds <= 0:
                logger.error("No retry budget left for %s", task.image_id)
                break
            logger.info("Retrying %s in %.2fs", task.image_id, sleep_seconds)
            time.sleep(sleep_seconds)

    finished_at = utc_now()
    success = annotation is not None
    result = {
        "image_id": task.image_id,
        "image_path": str(task.image_path),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(time.monotonic() - task_started_monotonic, 3),
        "success": success,
        "error": None if success else final_error,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "annotation": annotation,
        "raw_output_text": raw_output_text,
        "rendered_prompt_path": str((task_dir / "rendered_prompt.md").relative_to(config.run_dir)),
        "response_id": response_payload.get("id") if response_payload else None,
        "usage": response_payload.get("usage") if response_payload else None,
    }

    if response_payload is not None:
        write_json(task_dir / "final_response.json", response_payload)
    write_json(task_dir / "result.json", result)
    if annotation is not None:
        write_json(task_dir / "annotation.json", annotation)

    return result


def build_run_config(settings: ResolvedSettings, dry_run: bool, run_dir: Path) -> RuntimeConfig:
    ensure_exists(settings.prompt_file, "prompt file")
    ensure_exists(settings.schema_file, "schema file")

    if not dry_run and not settings.api_key:
        raise ValueError("OPENAI_API_KEY or --api-key is required unless --dry-run is used.")

    base_url = normalize_base_url(settings.base_url)
    prompt_text = read_text(settings.prompt_file)
    prompt_hash = hashlib.sha1(prompt_text.encode("utf-8")).hexdigest()[:8]
    return RuntimeConfig(
        api_key=settings.api_key or "DUMMY_KEY_FOR_DRY_RUN",
        base_url=base_url,
        model=settings.model,
        timeout_seconds=settings.timeout_seconds,
        max_retries=settings.max_retries,
        max_elapsed_seconds=settings.max_elapsed_seconds,
        initial_backoff_seconds=settings.initial_backoff_seconds,
        max_backoff_seconds=settings.max_backoff_seconds,
        workers=settings.workers,
        image_detail=settings.image_detail,
        temperature=settings.temperature,
        max_output_tokens=settings.max_output_tokens,
        verbosity=settings.verbosity,
        prompt_text=prompt_text,
        prompt_version=f"{settings.prompt_file.stem}_{prompt_hash}",
        schema=read_json(settings.schema_file),
        dry_run=dry_run,
        run_dir=run_dir,
        api_url=f"{base_url}/chat/completions",
    )


def save_run_metadata(
    run_dir: Path,
    args: argparse.Namespace,
    config: RuntimeConfig,
    tasks: list[ImageTask],
    settings: ResolvedSettings,
) -> None:
    for directory in ["items", "artifacts"]:
        (run_dir / directory).mkdir(parents=True, exist_ok=True)

    prompt_copy = run_dir / "artifacts" / f"prompt_source{settings.prompt_file.suffix or '.txt'}"
    prompt_copy.write_text(config.prompt_text + "\n", encoding="utf-8")
    write_json(run_dir / "artifacts" / "schema.json", config.schema)

    selection = [
        {
            "index": task.index,
            "image_id": task.image_id,
            "image_path": str(task.image_path),
        }
        for task in tasks
    ]
    write_json(run_dir / "selected_images.json", selection)

    runtime_snapshot = asdict(config)
    runtime_snapshot["api_key"] = "***"
    runtime_snapshot["run_dir"] = str(config.run_dir)
    effective_settings = asdict(settings)
    effective_settings["env_file"] = str(settings.env_file)
    effective_settings["image_dir"] = str(settings.image_dir)
    effective_settings["output_root"] = str(settings.output_root)
    effective_settings["prompt_file"] = str(settings.prompt_file)
    effective_settings["schema_file"] = str(settings.schema_file)
    effective_settings["api_key"] = "***" if settings.api_key else None
    effective_settings["loaded_env_keys"] = list(settings.loaded_env_keys)
    config_snapshot = {
        "cli_args": {
            key: (str(value) if isinstance(value, Path) else value)
            for key, value in vars(args).items()
        },
        "effective_settings": effective_settings,
        "runtime_config": runtime_snapshot,
    }
    write_json(run_dir / "run_config.json", config_snapshot)


def main() -> int:
    args = parse_args()
    env_file = resolve_cli_path(args.env_file)

    try:
        env_values = load_env_file(env_file)
        settings = resolve_settings(args, env_values, env_file)
    except Exception as exc:  # noqa: BLE001
        print(f"Configuration error: {exc}")
        return 1

    settings.output_root.mkdir(parents=True, exist_ok=True)
    run_dir = create_run_dir(settings.output_root)
    logger = setup_logging(run_dir)

    try:
        config = build_run_config(settings, args.dry_run, run_dir)
        tasks = select_tasks(args, settings.image_dir)
        save_run_metadata(run_dir, args, config, tasks, settings)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s", exc)
        return 1

    run_started_at = utc_now()
    started_monotonic = time.monotonic()

    if settings.loaded_env_keys:
        logger.info(
            "Loaded %s setting(s) from %s",
            len(settings.loaded_env_keys),
            settings.env_file,
        )
    else:
        logger.info("No .env settings loaded from %s", settings.env_file)
    logger.info("Run directory: %s", run_dir)
    logger.info("Selected %s image(s)", len(tasks))
    logger.info(
        "Concurrency=%s timeout=%.1fs retries=%s max_elapsed=%.1fs dry_run=%s",
        config.workers,
        config.timeout_seconds,
        config.max_retries,
        config.max_elapsed_seconds,
        config.dry_run,
    )

    results: list[dict[str, Any]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.workers) as executor:
        futures = {executor.submit(process_task, task, config, logger): task for task in tasks}
        for future in concurrent.futures.as_completed(futures):
            task = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unhandled task error for %s: %s", task.image_id, exc)
                results.append(
                    {
                        "image_id": task.image_id,
                        "image_path": str(task.image_path),
                        "success": False,
                        "error": str(exc),
                        "attempt_count": 0,
                        "attempts": [],
                        "annotation": None,
                        "raw_output_text": None,
                        "response_id": None,
                        "usage": None,
                    }
                )

    results.sort(key=lambda item: item["image_id"])
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    annotations_jsonl_path = results_dir / "annotations.jsonl"
    with annotations_jsonl_path.open("w", encoding="utf-8") as jsonl_file:
        for item in results:
            jsonl_file.write(json.dumps(item, ensure_ascii=False) + "\n")

    template_headers = load_template_headers(DEFAULT_RESULT_TEMPLATE_PATH)
    template_rows = [
        build_template_row(result, config, record_id)
        for record_id, result in enumerate(results, start=1)
    ]
    template_csv_path = results_dir / CSV_EXPORT_FILENAME
    write_template_csv(template_csv_path, template_headers, template_rows)
    export_review_materials(results, template_rows, config, logger)

    summary = {
        "started_at": run_started_at,
        "completed_at": utc_now(),
        "total_images": len(results),
        "successful_images": sum(1 for item in results if item.get("success")),
        "failed_images": sum(1 for item in results if not item.get("success")),
        "run_dir": str(run_dir),
        "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        "template_csv_path": str(template_csv_path),
        "template_headers_source": (
            str(DEFAULT_RESULT_TEMPLATE_PATH)
            if DEFAULT_RESULT_TEMPLATE_PATH.exists()
            else "built_in_fallback"
        ),
        "review_report_filename": REVIEW_REPORT_FILENAME,
    }
    write_json(results_dir / "summary.json", summary)

    logger.info(
        "Finished: total=%s success=%s failed=%s duration=%.3fs",
        summary["total_images"],
        summary["successful_images"],
        summary["failed_images"],
        summary["duration_seconds"],
    )

    return 0 if summary["failed_images"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
