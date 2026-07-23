"""
OMRChecker Backend API
Run alongside Laravel on a different port (default 8080).
Runs OMR in-process (no subprocess) for faster response.
"""
import glob
import json
import os
import re
import resource
import secrets
import shutil
import struct
import sys
import tempfile
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import cast

import cv2  # pyright: ignore[reportMissingImports]
import pandas as pd  # pyright: ignore[reportMissingImports]
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

try:
    _OPENCV_THREADS = max(1, int(os.getenv("OMR_OPENCV_THREADS", "1")))
except ValueError:
    _OPENCV_THREADS = 1
cv2.setNumThreads(_OPENCV_THREADS)

_App = FastAPI

# Project root (parent of api/) – ensure importable when running as uvicorn api.main:app
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TEMPLATES_DIR = PROJECT_ROOT / "templates"

from src.logger import logger
from src.utils.cache import get_positive_int_env, lru_get, lru_put
DEFAULT_TEMPLATE_ID = "50q"

# Max upload size (20 MB) – reject larger to avoid memory/CPU abuse
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Directory for Checked OMR images (same as checked_omr_path prefix)
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# Cache template files (template.json, config.json, omr_marker.jpg) per template_id to reduce disk I/O
TEMPLATE_FILE_CACHE_MAX = get_positive_int_env("OMR_TEMPLATE_FILE_CACHE_MAX", 32)
_template_file_cache: OrderedDict[str, dict[str, bytes]] = OrderedDict()

# Internal API key required on every request (global_auth_middleware).
# Set via OMR_INTERNAL_API_KEY env var.
INTERNAL_API_KEY = os.getenv("OMR_INTERNAL_API_KEY", "").strip()


def _env_flag(name: str, default: bool) -> bool:
    """Parse boolean-like env values (1/true/yes/on)."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _current_rss_mb() -> float | None:
    """Return current RSS in MB when the platform exposes it cheaply."""
    status_path = Path("/proc/self/status")
    if status_path.exists():
        try:
            for line in status_path.read_text().splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return round(int(parts[1]) / 1024, 2)
        except OSError:
            pass
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (OSError, ValueError):
        return None
    # Linux reports KiB; macOS reports bytes. /proc above handles production Linux.
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(rss / divisor, 2)


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        while i < len(data) and data[i] == 0xFF:
            i += 1
        if i >= len(data):
            return None
        marker = data[i]
        i += 1
        if marker in {0x01, *range(0xD0, 0xD8)}:
            continue
        if i + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[i : i + 2], "big")
        if segment_length < 2 or i + segment_length > len(data):
            return None
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            height = int.from_bytes(data[i + 3 : i + 5], "big")
            width = int.from_bytes(data[i + 5 : i + 7], "big")
            return width, height
        i += segment_length
    return None


def _image_dimensions_from_header(data: bytes, ext: str) -> tuple[int, int] | None:
    if ext == ".png" and len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if ext in {".jpg", ".jpeg"}:
        return _jpeg_dimensions(data)
    return None


# Fail fast: refuse to start with auth disabled unless explicitly opted in for local dev.
# (เดิม key ว่าง = ปิด auth เงียบ ๆ ทั้ง API รวม DELETE — อันตรายถ้าลืมตั้งตอน deploy)
if not INTERNAL_API_KEY and not _env_flag("OMR_ALLOW_NO_AUTH", False):
    raise RuntimeError(
        "OMR_INTERNAL_API_KEY is not set. Refusing to start with authentication disabled. "
        "Set OMR_INTERNAL_API_KEY, or set OMR_ALLOW_NO_AUTH=1 explicitly for local development."
    )


_ENTRY_IMPORT_STARTED_AT = perf_counter()
from src.entry import entry_point
ENTRY_IMPORT_MS = round((perf_counter() - _ENTRY_IMPORT_STARTED_AT) * 1000, 2)


# API docs exposure toggle.
# Production recommendation: OMR_ENABLE_DOCS=false to disable /docs, /redoc, /openapi.json.
ENABLE_DOCS = _env_flag("OMR_ENABLE_DOCS", True)

# Pattern for school_id / exam_id: alphanumeric + dash + underscore, 1-64 chars.
# Strict to prevent path traversal (no "..", "/", "\") and keep folder names sane.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _safe_id(value: str | None, name: str, *, required: bool = False) -> str | None:
    """Validate school_id / exam_id. Return cleaned value or None.

    Raises HTTPException(400) if invalid (or empty when required=True).
    """
    if value is None or value == "":
        if required:
            raise HTTPException(status_code=400, detail=f"{name} is required")
        return None
    if not _SAFE_ID_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {name}: must be 1-64 chars, alphanumeric/dash/underscore only",
        )
    return value


def _compact_evaluation_answer_pairs(eval_data: dict) -> dict:
    """For source_type custom: drop (question, answer) pairs whose answer is null or \"\".

    Template may be e.g. 50q while the teacher banks only some questions — Laravel may send null
    or blanks for the rest; OMR grading only considers listed pairs (see evaluate_concatenated_response).
    """
    if (
        not isinstance(eval_data, dict)
        or eval_data.get("source_type") != "custom"
        or eval_data.get("options") is None
        or not isinstance(eval_data["options"], dict)
    ):
        return eval_data
    opts = eval_data["options"]
    qs = opts.get("questions_in_order")
    ans = opts.get("answers_in_order")
    if not isinstance(qs, list) or not isinstance(ans, list):
        return eval_data
    # Support both explicit questions ["q1","q2",..] and compact ranges ["q1..50"].
    # If lengths mismatch, try expanding question ranges before pairing.
    if len(qs) != len(ans):
        try:
            from src.utils.parsing import parse_fields
            expanded_qs = parse_fields("questions_in_order", qs)
        except Exception:
            return eval_data
        if len(expanded_qs) != len(ans):
            return eval_data
        qs = expanded_qs
    kept_q: list = []
    kept_a: list = []
    for q, a in zip(qs, ans):
        if a is None:
            continue
        if isinstance(a, str) and not a.strip():
            continue
        kept_q.append(q)
        kept_a.append(a)
    if len(kept_q) == len(qs):
        return eval_data
    new_opts = {**opts, "questions_in_order": kept_q, "answers_in_order": kept_a}
    return {**eval_data, "options": new_opts}


def _resolve_within_checked_dir(*parts: str) -> Path:
    """Resolve a subpath under CHECKED_OMR_DIR, raising 400 if path escapes the dir."""
    base = CHECKED_OMR_DIR.resolve()
    target = (CHECKED_OMR_DIR.joinpath(*parts)).resolve()
    try:
        target.relative_to(base)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid path") from e
    return target

app = _App(
    title="OMR Checker API",
    description="Upload OMR sheet image, get responses and score as JSON.",
    version="1.0.0",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
    root_path="/api/omr"
)


# หมายเหตุ: ไม่มี CORS middleware — browser ไม่เคยเรียก API นี้ตรง ๆ
# (Laravel เป็น proxy ให้ทุกอย่าง รวมรูป checked ผ่าน /omr/checked-image ฝั่ง Laravel)


# Paths that bypass the global auth middleware. Keep this list small.
# - /health: needed by systemd / load balancer health checks (no secrets exposed)
# - /docs, /redoc, /openapi.json: API documentation (only when ENABLE_DOCS=true)
_AUTH_BYPASS_PATHS: set[str] = {"/health"}
_AUTH_BYPASS_PREFIXES: tuple[str, ...] = (
    ("/docs", "/redoc", "/openapi.json") if ENABLE_DOCS else tuple()
)


@app.middleware("http")
async def global_auth_middleware(request: Request, call_next):
    """Require Authorization: Bearer <OMR_INTERNAL_API_KEY> on every request.

    Behaviors:
    - If OMR_INTERNAL_API_KEY env var is empty (allowed only with OMR_ALLOW_NO_AUTH=1 — dev)
      → middleware is disabled.
    - Whitelisted paths (see _AUTH_BYPASS_*) skip auth.
    - All other paths must include `Authorization: Bearer <key>` matching INTERNAL_API_KEY.
    """
    if not INTERNAL_API_KEY:
        return await call_next(request)

    path = request.url.path
    if path in _AUTH_BYPASS_PATHS or path.startswith(_AUTH_BYPASS_PREFIXES):
        return await call_next(request)

    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing or invalid Authorization header (expected: 'Bearer <key>')"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth[len("Bearer "):]
    if not secrets.compare_digest(token, INTERNAL_API_KEY):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid API key"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
CHECKED_OMR_DIR = OUTPUTS_DIR / "scans" / "CheckedOMRs"
CHECKED_OMR_DIR.mkdir(parents=True, exist_ok=True)

# Media types for common image extensions
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}
CHECKED_OMR_SCHOOL_PREFIX = "school"
UNKNOWN_SCHOOL_ID = "_unknown"
CHECKED_MAX_SIDE = 1600
CHECKED_TARGET_BYTES = 900 * 1024  # target under ~1MB if possible

# When template defines customLabels.Roll, flag implausible reads (bad photo / alignment).
ROLL_VALIDATION_MIN_LEN = 4


def _roll_slot_count(template_json: dict) -> int | None:
    """Return expanded Roll slot count when template defines customLabels.Roll."""
    custom = template_json.get("customLabels")
    if not isinstance(custom, dict) or "Roll" not in custom:
        return None
    roll_keys = custom.get("Roll")
    if not isinstance(roll_keys, list) or not roll_keys:
        return None
    try:
        from src.utils.parsing import parse_fields

        parsed_keys = parse_fields("Custom Label: Roll", roll_keys)
    except Exception as e:
        logger.info(f"Roll validation skipped: cannot parse customLabels.Roll ({e})")
        return None
    return len(parsed_keys)


def _is_valid_roll_for_template(roll: str, template_json: dict) -> bool:
    max_slots = _roll_slot_count(template_json)
    if max_slots is None:
        return False
    return bool(
        roll
        and roll.isdigit()
        and len(roll) == max_slots
    )


def _roll_stem_for_checked_storage(responses: dict, template_json: dict) -> str | None:
    """Use OMR-read Roll for stable filenames only when the Roll read is plausible."""
    roll = str(responses.get("Roll", "")).strip()
    if not _is_valid_roll_for_template(roll, template_json):
        return None
    return roll


def _checked_omr_relative_parts(
    *,
    school_id: str | None,
    exam_id: str,
    month_folder: str,
    roll_stem: str | None,
    request_id: str,
    upload_original_stem: str,
) -> list[str]:
    """Directory + filename under CHECKED_OMR_DIR (all segments, last is filename).

    exam_id is always set (POST /check requires it). When roll_stem is set: .../exam_id/by-roll/{roll}.jpg
    so rescans with same school + exam + roll overwrite. Otherwise .../exam_id/YYYY-MM/uuid_stem.jpg.
    """
    sid = school_id or UNKNOWN_SCHOOL_ID
    base = [CHECKED_OMR_SCHOOL_PREFIX, sid, exam_id]
    stem = (upload_original_stem or "upload").replace("/", "_").replace("\\", "_")[:120]

    if roll_stem:
        return [*base, "by-roll", f"{roll_stem}.jpg"]

    fname = f"{request_id}_{stem}.jpg"
    return [*base, month_folder, fname]


def _persist_checked_image_optimized(checked_src: Path, persistent_dest: Path) -> Path:
    """Save checked OMR as compressed JPEG while keeping it readable."""
    image = cv2.imread(str(checked_src), cv2.IMREAD_COLOR)
    if image is None:
        raise OSError(f"Unable to read checked image: {checked_src}")

    h, w = image.shape[:2]
    max_side = max(h, w)
    if max_side > CHECKED_MAX_SIDE:
        scale = CHECKED_MAX_SIDE / float(max_side)
        image = cv2.resize(
            image,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )

    # Try progressive compression quality steps to keep size small.
    for quality in (75, 68, 60, 52, 45):
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            continue
        data = encoded.tobytes()
        if len(data) <= CHECKED_TARGET_BYTES or quality == 45:
            persistent_dest.write_bytes(data)
            return persistent_dest

    raise OSError("Failed to encode checked image")


def _roll_warning_if_configured(template_json: dict, row: pd.Series) -> dict | None:
    """Return a warning when Roll is configured but the OMR-read value is implausible.

    N = expanded roll slot count (e.g. 5 for roll1..roll5). This is intentionally non-fatal:
    Laravel can save the scan as unbound/pending and let a teacher correct the student code.
    """
    max_slots = _roll_slot_count(template_json)
    if max_slots is None:
        return None
    roll = str(row.get("Roll", "")).strip()
    if (
        roll
        and roll.isdigit()
        and len(roll) == max_slots
    ):
        return None
    code = "incomplete_roll" if roll and roll.isdigit() and len(roll) < max_slots else "invalid_roll"
    return {
        "code": code,
        "severity": "warning",
        "field": "Roll",
        "message": (
            f"Invalid Roll (student ID read from sheet): expected exactly {max_slots} "
            "digits. The sheet was processed, but Laravel should keep it unbound until a teacher corrects the student code. "
            f"รหัสนักเรียน (Roll) ควรเป็นตัวเลข {max_slots} หลัก "
            "ระบบตรวจคะแนนให้แล้ว แต่ควรให้ครูแก้รหัสนักเรียนก่อนผูกคะแนนรายคน"
        ),
        "min_length": ROLL_VALIDATION_MIN_LEN,
        "max_length": max_slots,
        "expected_length": max_slots,
        "detected_length": len(roll),
    }


def _reject_unreliable_roll_if_configured(template_json: dict, row: pd.Series) -> None:
    """Reject Roll reads that suggest the sheet is misaligned, not merely missing/incomplete."""
    max_slots = _roll_slot_count(template_json)
    if max_slots is None:
        return
    roll = str(row.get("Roll", "")).strip()
    if not roll:
        return
    if roll.isdigit() and len(roll) <= max_slots:
        return
    raise HTTPException(
        status_code=400,
        detail=(
            f"Unreliable Roll read: expected at most {max_slots} digits, got {len(roll)} characters. "
            "This usually means the sheet is misaligned, blurred, cropped, or has multiple marks in the student-code area. "
            "The answer read may also be unreliable, so please retake or rescan the sheet. "
            f"อ่านรหัสนักเรียนผิดปกติ: ควรมีไม่เกิน {max_slots} หลัก แต่อ่านได้ {len(roll)} ตัวอักษร "
            "มักเกิดจากภาพเอียง เบลอ ครอปไม่ครบ หรือฝนช่องรหัสซ้อนหลายช่อง จึงไม่ควรบันทึกคะแนนจากภาพนี้"
        ),
    )


def _serve_checked_omr_file(file_path: str):
    """Serve a file from CheckedOMRs dir; raise HTTPException 404 if invalid.
    file_path can be a filename or subpath like '2025-02/xxx.jpg' (month subfolder).
    """
    if ".." in file_path or "\\" in file_path:
        raise HTTPException(status_code=404, detail="Not found")
    # Normalize: no leading slash, allow month subfolder (e.g. 2025-02/name.jpg)
    file_path = file_path.lstrip("/").replace("\\", "/")
    full = (CHECKED_OMR_DIR / file_path).resolve()
    root = CHECKED_OMR_DIR.resolve()
    try:
        full.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    if not full.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    media_type = MEDIA_TYPES.get(full.suffix.lower(), "application/octet-stream")
    return FileResponse(str(full), media_type=media_type)


@app.get("/checked/{file_path:path}")
async def serve_checked(file_path: str):
    """
    โหลดรูปกระดาษที่ตรวจแล้ว (Checked OMR).
    URL: base_url + /checked/ + checked_omr_filename
    ใช้ checked_omr_filename จาก response ของ POST /check (เช่น .../by-roll/12345.jpg หรือ .../YYYY-MM/uuid_stem.jpg)
    """
    return _serve_checked_omr_file(file_path)


@app.get("/outputs/scans/CheckedOMRs/{file_path:path}")
async def serve_checked_omr(file_path: str):
    """Serve Checked OMR (alias). URL = base_url + /outputs/scans/CheckedOMRs/YYYY-MM/xxx.jpg"""
    return _serve_checked_omr_file(file_path)


def get_template_dir(template_id: str) -> Path:
    path = TEMPLATES_DIR / template_id
    if not path.is_dir() or not (path / "template.json").exists():
        raise HTTPException(status_code=400, detail=f"Template '{template_id}' not found")
    return path


def _get_cached_template_files(template_id: str, template_dir: Path | None = None) -> dict[str, bytes]:
    """Load template.json, config.json, omr_marker.jpg into memory; cache per template_id."""
    cached = lru_get(_template_file_cache, template_id)
    if cached is not None:
        return cached
    if template_dir is None:
        template_dir = get_template_dir(template_id)
    out: dict[str, bytes] = {}
    # reference.png = ภาพต้นฉบับสะอาดสำหรับ FeatureBasedAlignment (ถ้า template ใช้)
    for name in ("template.json", "config.json", "omr_marker.jpg", "reference.png"):
        src = template_dir / name
        if src.exists():
            out[name] = src.read_bytes()
    lru_put(_template_file_cache, template_id, out, TEMPLATE_FILE_CACHE_MAX)
    return out


@app.on_event("startup")
def warmup_omr_worker() -> None:
    """Warm per-worker imports and template files before the first real scan."""
    started_at = perf_counter()
    warmed_templates: list[str] = []
    if TEMPLATES_DIR.is_dir():
        for template_dir in sorted(TEMPLATES_DIR.iterdir()):
            if not template_dir.is_dir() or not (template_dir / "template.json").is_file():
                continue
            _get_cached_template_files(template_dir.name, template_dir)
            warmed_templates.append(template_dir.name)

    print(
        "[OMR API warmup] "
        f"entry_import_ms={ENTRY_IMPORT_MS} "
        f"templates={','.join(warmed_templates) or '-'} "
        f"template_cache_count={len(_template_file_cache)} "
        f"total_ms={round((perf_counter() - started_at) * 1000, 2)}",
        flush=True,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    """Return JSON for any uncaught exception (e.g. timeout). Custom logger has no .exception()."""
    logger.log.exception("OMR API 500: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc),
            "type": type(exc).__name__,
            "source": "omr-api",  # ใช้แยกว่า 500 มาจาก OMR API ไม่ใช่ Laravel
        },
        headers={"X-OMR-API-Error": "1"},
    )


@app.get("/")
def root():
    return {
        "service": "OMR Checker API",
        "docs": "/docs",
        "health": "/health",
        "templates": "/templates",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/templates")
def list_templates():
    """List template_id values: subfolders of templates/ that contain template.json."""
    if not TEMPLATES_DIR.is_dir():
        return {"templates": []}
    out: list[dict[str, str]] = []
    for p in sorted(TEMPLATES_DIR.iterdir()):
        if not p.is_dir() or not (p / "template.json").is_file():
            continue
        out.append({"template_id": p.name})
    return {"templates": out, "default": DEFAULT_TEMPLATE_ID}


@app.post("/check")
def check_omr(
    image: UploadFile = File(..., description="OMR sheet image (jpg/png)"),
    template_id: str = DEFAULT_TEMPLATE_ID,
    evaluate: bool = True,
    evaluation: str | None = Form(None, description="Evaluation config as JSON (from Laravel). If provided, overrides template evaluation and enables scoring."),
    school_id: str | None = Form(
        None,
        description="School id (optional). With Roll-capable templates, checked images use school/exam/by-roll/<roll>.jpg and rescans overwrite.",
    ),
    exam_id: str | None = Form(
        None,
        description="Required. Exam identifier from Laravel. Same school+exam+Roll → same checked image path.",
    ),
    require_roll: bool = Form(
        True,
        description=(
            "When false, skip Roll (student ID) validation — for anonymous/grade-only exams "
            "where students don't bubble a student code. Default true (backward compatible)."
        ),
    ),
):
    """
    Upload an OMR sheet image. Returns responses (Roll, q1, q2, ...).
    - evaluation (optional): JSON string of evaluation config from Laravel. If sent, OMR will use it to compute score and return score + evaluation.
    - If evaluate=true and no evaluation JSON: use template's evaluation.json if present.
    - If evaluate=false and no evaluation JSON: raw responses only; Laravel can compute score.
    - exam_id (required): Laravel must send every time; checked OMR paths are under school/<school_id>/<exam_id>/...
    - school_id (optional): defaults to _unknown if omitted.
    - require_roll (optional, default true): false = ข้าม Roll validation สำหรับ exam โหมด anonymous
      (ตรวจคะแนนอย่างเดียว ไม่ผูกนักเรียน) — Roll ที่อ่านได้จะยังอยู่ใน responses แต่ไม่ถูกบังคับรูปแบบ

    Sync `def` (ไม่ใช่ async) โดยตั้งใจ — FastAPI จะรันใน threadpool ทำให้งาน OpenCV
    ที่กิน CPU หนักไม่ block event loop (ไม่งั้น /health และ request อื่นค้างทั้งหมด)
    """
    timing_started_at = perf_counter()
    timing_stage_started_at = timing_started_at
    timings_ms: dict[str, float] = {}
    request_id = str(uuid.uuid4())
    worker_pid = os.getpid()
    rss_before_mb = _current_rss_mb()
    status_code: int | None = None
    input_dimensions: tuple[int, int] | None = None
    work_dir: Path | None = None
    out_dir: Path | None = None
    upload_bytes = 0

    def mark_timing(name: str) -> None:
        nonlocal timing_stage_started_at
        now = perf_counter()
        timings_ms[name] = round((now - timing_stage_started_at) * 1000, 2)
        timing_stage_started_at = now

    if not image.filename:
        raise HTTPException(status_code=400, detail="No filename")
    ext = Path(image.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        raise HTTPException(
            status_code=400,
            detail="File must be .jpg, .jpeg or .png",
        )

    school_id = _safe_id(school_id, "school_id")
    if exam_id is None or (isinstance(exam_id, str) and exam_id.strip() == ""):
        raise HTTPException(
            status_code=400,
            detail=(
                "exam_id is required: cannot process without an exam identifier. "
                "Laravel must send exam_id on every POST /check. "
                "ต้องส่ง exam_id — ไม่ส่งไม่สามารถประมวลผลได้"
            ),
        )
    exam_id = cast(str, _safe_id(exam_id, "exam_id"))

    template_dir = get_template_dir(template_id)
    work_dir = Path(tempfile.gettempdir()) / f"omr_{request_id}"
    out_dir = Path(tempfile.gettempdir()) / f"omr_out_{request_id}"

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        scans_dir = work_dir / "scans"
        scans_dir.mkdir(exist_ok=True)

        # Write template files from cache (avoid second get_template_dir on cache miss)
        cached = _get_cached_template_files(template_id, template_dir)
        for name, data in cached.items():
            (work_dir / name).write_bytes(data)

        # Evaluation: from Laravel JSON (priority) or from template
        evaluation_sent = evaluation is not None and evaluation.strip()
        if evaluation_sent and evaluation is not None:
            try:
                eval_data = json.loads(evaluation.strip())
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid evaluation JSON: {e!s}",
                ) from e
            eval_data = _compact_evaluation_answer_pairs(eval_data)
            opts_raw = eval_data.get("options")
            opts = opts_raw if isinstance(opts_raw, dict) else {}
            # source_type อยู่ top-level ของ eval_data (ไม่ใช่ใน options)
            if eval_data.get("source_type") == "custom":
                ao = opts.get("answers_in_order")
                if isinstance(ao, list) and len(ao) == 0:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "evaluation: answers_in_order became empty after omitting null/blank slots. "
                            "Either provide at least one answer, or omit those question rows entirely."
                        ),
                    )
            q_order = opts.get("questions_in_order")
            if not isinstance(q_order, list):
                raise HTTPException(
                    status_code=400,
                    detail="evaluation.options.questions_in_order must be a list (array). Check answerKeyPayload structure from Laravel.",
                )
            try:
                from src.utils.validations import validate_evaluation_json
                validate_evaluation_json(eval_data, "evaluation (request body)")
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Evaluation config invalid: {e!s}",
                ) from e
            (work_dir / "evaluation.json").write_text(
                json.dumps(eval_data, ensure_ascii=False),
                encoding="utf-8",
            )
        elif evaluate:
            src = template_dir / "evaluation.json"
            if src.exists():
                shutil.copy2(src, work_dir / "evaluation.json")
        has_evaluation = (work_dir / "evaluation.json").exists()
        mark_timing("prepare")

        # Save uploaded image (with size limit to avoid memory/CPU abuse)
        upload_path = scans_dir / f"upload{ext}"
        header_bytes = bytearray()
        total = 0
        with upload_path.open("wb") as out_file:
            while True:
                chunk = image.file.read(1024 * 256)  # 256 KB at a time (sync — we're in a threadpool)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Image too large. Maximum size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
                    )
                if len(header_bytes) < 64 * 1024:
                    remaining = (64 * 1024) - len(header_bytes)
                    header_bytes.extend(chunk[:remaining])
                out_file.write(chunk)
        upload_bytes = total
        input_dimensions = _image_dimensions_from_header(bytes(header_bytes), ext)
        mark_timing("read_upload")

        # Run OMR in-process (no subprocess = much faster, no Python startup per request)
        omr_args = {
            "output_dir": str(out_dir),
            # "debug" ถูกอ่านเฉพาะใน main.py (CLI) — entry_point ไม่ใช้ ใส่ไว้กัน KeyError เฉย ๆ
            "debug": False,
            "setLayout": False,
            "autoAlign": False,
            "skip_config_table": True,  # skip Rich table when called from API (faster, less log noise)
            "single_file": str(upload_path),
            "return_result": True,
        }
        try:
            entry_result = entry_point(Path(work_dir), omr_args)
        except ValueError as e:
            # e.g. empty string / null in answers_in_order from Laravel evaluation JSON
            raise HTTPException(status_code=400, detail=str(e)) from e
        mark_timing("entry_point")

        row = None
        score = None
        responses = None
        if isinstance(entry_result, dict):
            row = entry_result
            file_id = str(row.get("file_id", upload_path.name))
            raw_score = row.get("score")
            if raw_score is not None and str(raw_score).strip() != "":
                try:
                    score = float(raw_score)
                except (TypeError, ValueError):
                    score = None
            raw_responses = row.get("responses")
            if isinstance(raw_responses, dict):
                responses = {
                    str(key): str(value) if value is not None else ""
                    for key, value in raw_responses.items()
                }
        else:
            # Compatibility fallback for older entry points and test doubles.
            results_glob = out_dir / "scans" / "Results" / "Results_*.csv"
            csv_files = sorted(glob.glob(str(results_glob)))
            if csv_files:
                df = pd.read_csv(csv_files[0], dtype=str, keep_default_na=False)
                if not df.empty:
                    row = df.iloc[0]
                    file_id = str(row.get("file_id", upload_path.name))
                    if "score" in df.columns:
                        raw_score = str(row.get("score", "")).strip()
                        if raw_score != "":
                            try:
                                score = float(raw_score)
                            except ValueError:
                                score = None
                    response_cols = [
                        column
                        for column in df.columns
                        if column
                        not in ("file_id", "input_path", "output_path", "score")
                    ]
                    responses = {
                        column: str(row.get(column, ""))
                        for column in response_cols
                    }

        if row is None or responses is None:
            raise HTTPException(
                status_code=400,
                detail="Not a valid OMR sheet: marker(s) not found in one or more corners. All four corner markers must be visible. Please upload a clear OMR answer sheet.",
            )

        template_for_roll = json.loads(cached["template.json"].decode("utf-8"))
        warnings = []
        # โหมด anonymous (require_roll=false): ไม่บังคับ Roll — นักเรียนไม่ฝนรหัส ใบยังตรวจได้ปกติ
        # โหมด student (require_roll=true): ไม่ reject เมื่อ Roll หาย/ไม่ครบ แต่ส่ง warning ให้ Laravel จัดเป็นงานรอแก้รหัส
        if require_roll:
            _reject_unreliable_roll_if_configured(template_for_roll, row)
            roll_warning = _roll_warning_if_configured(template_for_roll, row)
            if roll_warning:
                warnings.append(roll_warning)

        # Optional: evaluation detail CSV (when evaluation was used)
        evaluation_rows = []
        if has_evaluation:
            eval_glob = out_dir / "scans" / "Evaluation" / f"{Path(upload_path.stem)}_evaluation.csv"
            if eval_glob.exists():
                try:
                    edf = pd.read_csv(str(eval_glob))
                    evaluation_rows = edf.to_dict(orient="records")
                except Exception:
                    pass
        mark_timing("parse_results")

        # Copy checked OMR image to persistent folder.
        # When template defines Roll and the read Roll is plausible: use .../by-roll/{roll}.jpg
        # under the same school + exam so rescans overwrite one file (no UUID pile-up).
        # Otherwise: legacy .../<YYYY-MM>/<uuid>_<stem>.jpg under the same folder layout as before.
        checked_omr_path = None
        checked_omr_filename = None
        checked_src = out_dir / "scans" / "CheckedOMRs" / file_id
        month_folder = datetime.now().strftime("%Y-%m")  # e.g. 2026-04

        roll_stem = _roll_stem_for_checked_storage(responses, template_for_roll)
        rel_parts = _checked_omr_relative_parts(
            school_id=school_id,
            exam_id=exam_id,
            month_folder=month_folder,
            roll_stem=roll_stem,
            request_id=request_id,
            upload_original_stem=Path(image.filename or "upload").stem,
        )
        persistent_dest = CHECKED_OMR_DIR.joinpath(*rel_parts)
        if checked_src.exists():
            persistent_dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                _persist_checked_image_optimized(checked_src, persistent_dest)
                checked_omr_path = str(persistent_dest.relative_to(PROJECT_ROOT))
                checked_omr_filename = "/".join(rel_parts)
            except OSError:
                pass
        mark_timing("persist_checked_image")

        payload = {
            "request_id": request_id,
            "file_id": file_id,
            "responses": responses,
        }
        if warnings:
            payload["warnings"] = warnings
        if checked_omr_path:
            payload["checked_omr_path"] = checked_omr_path
        if checked_omr_filename:
            payload["checked_omr_filename"] = checked_omr_filename
        if has_evaluation and score is not None:
            payload["score"] = score
            payload["evaluation"] = evaluation_rows
        elif score is not None:
            payload["score"] = score
        timings_ms["total"] = round((perf_counter() - timing_started_at) * 1000, 2)
        print(
            "[OMR API timing] "
            f"request_id={request_id} "
            f"pid={worker_pid} "
            f"template_id={template_id} "
            f"school_id={school_id} "
            f"exam_id={exam_id} "
            f"upload_bytes={upload_bytes} "
            f"input_width={input_dimensions[0] if input_dimensions else '-'} "
            f"input_height={input_dimensions[1] if input_dimensions else '-'} "
            f"prepare_ms={timings_ms.get('prepare')} "
            f"read_upload_ms={timings_ms.get('read_upload')} "
            f"entry_point_ms={timings_ms.get('entry_point')} "
            f"parse_results_ms={timings_ms.get('parse_results')} "
            f"persist_checked_image_ms={timings_ms.get('persist_checked_image')} "
            f"total_ms={timings_ms.get('total')}",
            flush=True,
        )

        status_code = 200
        return JSONResponse(status_code=200, content=payload)

    except HTTPException as e:
        status_code = e.status_code
        raise
    except Exception:
        status_code = 500
        raise
    finally:
        # Cleanup temp dirs
        for d in (work_dir, out_dir):
            if d is not None and d.exists():
                try:
                    shutil.rmtree(d)
                except OSError:
                    pass
        rss_after_mb = _current_rss_mb()
        rss_delta_mb = (
            round(rss_after_mb - rss_before_mb, 2)
            if rss_after_mb is not None and rss_before_mb is not None
            else None
        )
        print(
            "[OMR API diagnostics] "
            f"request_id={request_id} "
            f"pid={worker_pid} "
            f"status={status_code if status_code is not None else '-'} "
            f"template_id={template_id} "
            f"upload_bytes={upload_bytes} "
            f"input_width={input_dimensions[0] if input_dimensions else '-'} "
            f"input_height={input_dimensions[1] if input_dimensions else '-'} "
            f"rss_before_mb={rss_before_mb if rss_before_mb is not None else '-'} "
            f"rss_after_mb={rss_after_mb if rss_after_mb is not None else '-'} "
            f"rss_delta_mb={rss_delta_mb if rss_delta_mb is not None else '-'} "
            f"total_ms={round((perf_counter() - timing_started_at) * 1000, 2)} "
            f"temp_dirs_removed={not any(d is not None and d.exists() for d in (work_dir, out_dir))}",
            flush=True,
        )


def _count_files(path: Path) -> int:
    """Count files (recursively) under path. Returns 0 if path does not exist."""
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file())


@app.delete("/exam/{school_id}/{exam_id}")
def delete_exam(school_id: str, exam_id: str):
    """
    Delete all Checked OMR files for a school's exam (use when exam is deleted in Laravel).

    Removes: outputs/scans/CheckedOMRs/school/<school_id>/<exam_id>/  (includes by-roll/ and legacy month folders)
    Auth: handled by global_auth_middleware (Authorization: Bearer <OMR_INTERNAL_API_KEY>)
    """
    school_id = _safe_id(school_id, "school_id", required=True) or ""
    exam_id = _safe_id(exam_id, "exam_id", required=True) or ""

    target = _resolve_within_checked_dir(CHECKED_OMR_SCHOOL_PREFIX, school_id, exam_id)
    legacy_target = _resolve_within_checked_dir(school_id, exam_id)
    path_deleted = f"{CHECKED_OMR_SCHOOL_PREFIX}/{school_id}/{exam_id}"
    # Backward compatibility: old layout before /school prefix
    if (not target.exists() or not target.is_dir()) and legacy_target.exists() and legacy_target.is_dir():
        target = legacy_target
        path_deleted = f"{school_id}/{exam_id}"
    if not target.exists() or not target.is_dir():
        return {"deleted": False, "message": "Exam folder not found", "path": path_deleted}

    files_removed = _count_files(target)
    try:
        shutil.rmtree(target)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {e!s}") from e

    logger.info(f"Deleted exam files: {school_id}/{exam_id} ({files_removed} files)")
    return {
        "deleted": True,
        "path": path_deleted,
        "files_removed": files_removed,
    }


@app.delete("/school/{school_id}")
def delete_school(school_id: str):
    """
    Delete ALL Checked OMR files for a school (use with caution — when a school is removed/migrated).

    Removes: outputs/scans/CheckedOMRs/school/<school_id>/  (all exams + months under it)
    Auth: handled by global_auth_middleware (Authorization: Bearer <OMR_INTERNAL_API_KEY>)
    """
    school_id = _safe_id(school_id, "school_id", required=True) or ""

    target = _resolve_within_checked_dir(CHECKED_OMR_SCHOOL_PREFIX, school_id)
    legacy_target = _resolve_within_checked_dir(school_id)
    path_deleted = f"{CHECKED_OMR_SCHOOL_PREFIX}/{school_id}"
    # Backward compatibility: old layout before /school prefix
    if (not target.exists() or not target.is_dir()) and legacy_target.exists() and legacy_target.is_dir():
        target = legacy_target
        path_deleted = school_id
    if not target.exists() or not target.is_dir():
        return {"deleted": False, "message": "School folder not found", "path": path_deleted}

    files_removed = _count_files(target)
    try:
        shutil.rmtree(target)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {e!s}") from e

    logger.info(f"Deleted school files: {school_id} ({files_removed} files)")
    return {
        "deleted": True,
        "path": path_deleted,
        "files_removed": files_removed,
    }
