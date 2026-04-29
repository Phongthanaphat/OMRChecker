"""
OMRChecker Backend API
Run alongside Laravel on a different port (default 8080).
Runs OMR in-process (no subprocess) for faster response.
"""
import glob
import json
import os
import re
import secrets
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import cv2  # pyright: ignore[reportMissingImports]
import pandas as pd  # pyright: ignore[reportMissingImports]
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

_App = FastAPI

# Project root (parent of api/) – ensure importable when running as uvicorn api.main:app
PROJECT_ROOT = Path(__file__).resolve().parent.parent
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TEMPLATES_DIR = PROJECT_ROOT / "templates"

from src.logger import logger
DEFAULT_TEMPLATE_ID = "50q"

# Max upload size (20 MB) – reject larger to avoid memory/CPU abuse
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Directory for Checked OMR images (same as checked_omr_path prefix)
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# Cache template files (template.json, config.json, omr_marker.jpg) per template_id to reduce disk I/O
_template_file_cache: dict[str, dict[str, bytes]] = {}

# Internal API key for sensitive operations (DELETE endpoints).
# Set via OMR_INTERNAL_API_KEY env var. If empty, auth is disabled (dev mode only — DO NOT use in production with public Nginx).
INTERNAL_API_KEY = os.getenv("OMR_INTERNAL_API_KEY", "").strip()

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


def _verify_internal_key(authorization: str | None = Header(None)) -> None:
    """Verify Authorization: Bearer <OMR_INTERNAL_API_KEY> header for sensitive endpoints.

    If OMR_INTERNAL_API_KEY env var is empty → auth is disabled (dev mode).
    Use secrets.compare_digest to avoid timing attacks.
    """
    if not INTERNAL_API_KEY:
        return  # Dev mode — no key configured
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header (expected: 'Bearer <key>')")
    token = authorization[len("Bearer "):]
    if not secrets.compare_digest(token, INTERNAL_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")


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
    docs_url="/docs",
    openapi_url="/openapi.json",
    root_path="/api/omr"
)


# CORS: allow Laravel / browser to GET images (e.g. img src to checked_omr_path)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# Paths that bypass the global auth middleware. Keep this list small.
# - /health: needed by systemd / load balancer health checks (no secrets exposed)
# - /docs, /redoc, /openapi.json: API documentation (consider protecting in production
#   if you don't want public schema discovery — but they don't expose data without auth)
_AUTH_BYPASS_PATHS: set[str] = {"/health"}
_AUTH_BYPASS_PREFIXES: tuple[str, ...] = ("/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def global_auth_middleware(request: Request, call_next):
    """Require Authorization: Bearer <OMR_INTERNAL_API_KEY> on every request.

    Behaviors:
    - If OMR_INTERNAL_API_KEY env var is empty → middleware is disabled (dev mode).
    - CORS preflight (OPTIONS) is always allowed.
    - Whitelisted paths (see _AUTH_BYPASS_*) skip auth.
    - All other paths must include `Authorization: Bearer <key>` matching INTERNAL_API_KEY.
    """
    if not INTERNAL_API_KEY:
        return await call_next(request)

    if request.method == "OPTIONS":
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


def _checked_sub_parts(school_id: str | None, exam_id: str | None, month_folder: str) -> list[str]:
    parts: list[str] = [CHECKED_OMR_SCHOOL_PREFIX, school_id or UNKNOWN_SCHOOL_ID]
    if exam_id:
        parts.append(exam_id)
    parts.append(month_folder)
    return parts


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
    ใช้ checked_omr_filename จาก response ของ POST /check (รูปแบบ YYYY-MM/filename.jpg)
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
    if template_id in _template_file_cache:
        return _template_file_cache[template_id]
    if template_dir is None:
        template_dir = get_template_dir(template_id)
    out: dict[str, bytes] = {}
    for name in ("template.json", "config.json", "omr_marker.jpg"):
        src = template_dir / name
        if src.exists():
            out[name] = src.read_bytes()
    _template_file_cache[template_id] = out
    return out


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
async def check_omr(
    image: UploadFile = File(..., description="OMR sheet image (jpg/png)"),
    template_id: str = DEFAULT_TEMPLATE_ID,
    evaluate: bool = True,
    evaluation: str | None = Form(None, description="Evaluation config as JSON (from Laravel). If provided, overrides template evaluation and enables scoring."),
    school_id: str | None = Form(None, description="School identifier (optional). Used to organize Checked OMR files: CheckedOMRs/school/<school_id>/<exam_id>/<YYYY-MM>/<file>"),
    exam_id: str | None = Form(None, description="Exam identifier (optional). Used together with school_id to group files by exam (enables targeted cleanup via DELETE /exam/{school_id}/{exam_id})."),
):
    """
    Upload an OMR sheet image. Returns responses (Roll, q1, q2, ...).
    - evaluation (optional): JSON string of evaluation config from Laravel. If sent, OMR will use it to compute score and return score + evaluation.
    - If evaluate=true and no evaluation JSON: use template's evaluation.json if present.
    - If evaluate=false and no evaluation JSON: raw responses only; Laravel can compute score.
    - school_id / exam_id (optional): organize Checked OMR files into per-school/per-exam folders for targeted cleanup.
    """
    if not image.filename:
        raise HTTPException(status_code=400, detail="No filename")
    ext = Path(image.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        raise HTTPException(
            status_code=400,
            detail="File must be .jpg, .jpeg or .png",
        )

    # Validate school_id / exam_id (optional, but if provided must be safe)
    school_id = _safe_id(school_id, "school_id")
    exam_id = _safe_id(exam_id, "exam_id")

    template_dir = get_template_dir(template_id)
    request_id = str(uuid.uuid4())
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
            if opts.get("source_type") == "custom":
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

        # Save uploaded image (with size limit to avoid memory/CPU abuse)
        upload_path = scans_dir / f"upload{ext}"
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await image.read(1024 * 256)  # 256 KB at a time
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Image too large. Maximum size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
                )
            chunks.append(chunk)
        content = b"".join(chunks)
        upload_path.write_bytes(content)

        # Run OMR in-process (no subprocess = much faster, no Python startup per request)
        from src.entry import entry_point

        omr_args = {
            "output_dir": str(out_dir),
            "debug": True,
            "setLayout": False,
            "autoAlign": False,
            "skip_config_table": True,  # skip Rich table when called from API (faster, less log noise)
        }
        try:
            entry_point(Path(work_dir), omr_args)
        except ValueError as e:
            # e.g. empty string / null in answers_in_order from Laravel evaluation JSON
            raise HTTPException(status_code=400, detail=str(e)) from e

        # Results CSV มีเมื่อ OMR ผ่าน marker check (เจอ marker ครบทั้ง 4 มุม)
        # ถ้าไม่เจอ marker แม้แต่มุมเดียว → CropOnMarkers return None → ไฟล์ไป ErrorFiles → ไม่มีแถวใน Results
        results_glob = out_dir / "scans" / "Results" / "Results_*.csv"
        csv_files = sorted(glob.glob(str(results_glob)))
        if not csv_files:
            raise HTTPException(
                status_code=400,
                detail="Not a valid OMR sheet: marker(s) not found in one or more corners. All four corner markers must be visible. Please upload a clear OMR answer sheet.",
            )

        # Read as strings to preserve leading zeros (e.g. Roll "01234").
        df = pd.read_csv(csv_files[0], dtype=str, keep_default_na=False)
        if df.empty:
            raise HTTPException(
                status_code=400,
                detail="Not a valid OMR sheet: marker(s) not found in one or more corners. All four corner markers must be visible. Please upload a clear OMR answer sheet.",
            )

        # First row is our upload (only one image)
        row = df.iloc[0]
        file_id = str(row.get("file_id", upload_path.name))
        score = None
        if "score" in df.columns:
            raw_score = str(row.get("score", "")).strip()
            if raw_score != "":
                try:
                    score = float(raw_score)
                except ValueError:
                    score = None

        # Build responses dict from template columns (file_id, input_path, output_path, score, then Roll, q1, ...)
        response_cols = [c for c in df.columns if c not in ("file_id", "input_path", "output_path", "score")]
        responses = {c: str(row.get(c, "")) for c in response_cols}

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

        # Copy checked OMR image to persistent folder.
        # Path layout: CheckedOMRs/school/<school_id>/<exam_id>/<YYYY-MM>/<file>
        #   - exam_id optional
        #   - if school_id missing, store under CheckedOMRs/school/_unknown/<YYYY-MM>/<file>
        checked_omr_path = None
        checked_omr_filename = None
        checked_src = out_dir / "scans" / "CheckedOMRs" / file_id
        month_folder = datetime.now().strftime("%Y-%m")  # e.g. 2026-04

        sub_parts = _checked_sub_parts(school_id, exam_id, month_folder)

        persistent_checked_dir = CHECKED_OMR_DIR.joinpath(*sub_parts)
        if checked_src.exists():
            persistent_checked_dir.mkdir(parents=True, exist_ok=True)
            original_stem = Path(image.filename or "upload").stem
            safe_name = f"{request_id}_{original_stem}.jpg"
            persistent_dest = persistent_checked_dir / safe_name
            try:
                _persist_checked_image_optimized(checked_src, persistent_dest)
                checked_omr_path = str(persistent_dest.relative_to(PROJECT_ROOT))
                # subpath under CheckedOMRs/ — used by /checked/{file_path:path}
                checked_omr_filename = "/".join([*sub_parts, safe_name])
            except OSError:
                pass

        payload = {
            "request_id": request_id,
            "file_id": file_id,
            "responses": responses,
        }
        if checked_omr_path:
            payload["checked_omr_path"] = checked_omr_path
        if checked_omr_filename:
            payload["checked_omr_filename"] = checked_omr_filename
        if has_evaluation and score is not None:
            payload["score"] = score
            payload["evaluation"] = evaluation_rows
        elif score is not None:
            payload["score"] = score

        return JSONResponse(status_code=200, content=payload)

    finally:
        # Cleanup temp dirs
        for d in (work_dir, out_dir):
            if d.exists():
                try:
                    shutil.rmtree(d)
                except OSError:
                    pass


def _count_files(path: Path) -> int:
    """Count files (recursively) under path. Returns 0 if path does not exist."""
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file())


@app.delete("/exam/{school_id}/{exam_id}")
async def delete_exam(school_id: str, exam_id: str):
    """
    Delete all Checked OMR files for a school's exam (use when exam is deleted in Laravel).

    Removes: outputs/scans/CheckedOMRs/school/<school_id>/<exam_id>/  (all months under it)
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

    logger.info("Deleted exam files: %s/%s (%d files)", school_id, exam_id, files_removed)
    return {
        "deleted": True,
        "path": path_deleted,
        "files_removed": files_removed,
    }


@app.delete("/school/{school_id}")
async def delete_school(school_id: str):
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

    logger.info("Deleted school files: %s (%d files)", school_id, files_removed)
    return {
        "deleted": True,
        "path": path_deleted,
        "files_removed": files_removed,
    }
