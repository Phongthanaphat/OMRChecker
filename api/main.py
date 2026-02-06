"""
OMRChecker Backend API
Run alongside Laravel on a different port (default 8080).
Runs OMR in-process (no subprocess) for faster response.
"""
import glob
import json
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd  # pyright: ignore[reportMissingImports]
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

try:
    from fastapi_offline import FastAPIOffline
    _App = FastAPIOffline  # /docs ใช้ Swagger UI จาก local (ทำงานได้ตอนไม่มีเน็ต)
except ImportError:
    _App = FastAPI  # fallback: ใช้ FastAPI ปกติ (/docs ต้องมีเน็ตโหลด CDN)

# Project root (parent of api/) – ensure importable when running as uvicorn api.main:app
PROJECT_ROOT = Path(__file__).resolve().parent.parent
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TEMPLATES_DIR = PROJECT_ROOT / "templates"

from src.logger import logger
DEFAULT_TEMPLATE_ID = "default"

# Max upload size (20 MB) – reject larger to avoid memory/CPU abuse
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Directory for Checked OMR images (same as checked_omr_path prefix)
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# Cache template files (template.json, config.json, omr_marker.jpg) per template_id to reduce disk I/O
_template_file_cache: dict[str, dict[str, bytes]] = {}

app = _App(
    title="OMR Checker API",
    description="Upload OMR sheet image, get responses and score as JSON.",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
    root_path="/ai"
)

# CORS: allow Laravel / browser to GET images (e.g. img src to checked_omr_path)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
CHECKED_OMR_DIR = OUTPUTS_DIR / "scans" / "CheckedOMRs"
CHECKED_OMR_DIR.mkdir(parents=True, exist_ok=True)

# Media types for common image extensions
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}


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


def _get_cached_template_files(template_id: str) -> dict[str, bytes]:
    """Load template.json, config.json, omr_marker.jpg into memory; cache per template_id."""
    if template_id in _template_file_cache:
        return _template_file_cache[template_id]
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
    """Return JSON with error message for any uncaught exception (e.g. timeout)."""
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "type": type(exc).__name__},
    )


@app.get("/")
def root():
    return {"service": "OMR Checker API", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/check")
async def check_omr(
    image: UploadFile = File(..., description="OMR sheet image (jpg/png)"),
    template_id: str = DEFAULT_TEMPLATE_ID,
    evaluate: bool = True,
    evaluation: str | None = Form(None, description="Evaluation config as JSON (from Laravel). If provided, overrides template evaluation and enables scoring."),
):
    """
    Upload an OMR sheet image. Returns responses (Roll, q1, q2, ...).
    - evaluation (optional): JSON string of evaluation config from Laravel. If sent, OMR will use it to compute score and return score + evaluation.
    - If evaluate=true and no evaluation JSON: use template's evaluation.json if present.
    - If evaluate=false and no evaluation JSON: raw responses only; Laravel can compute score.
    """
    if not image.filename:
        raise HTTPException(status_code=400, detail="No filename")
    ext = Path(image.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        raise HTTPException(
            status_code=400,
            detail="File must be .jpg, .jpeg or .png",
        )

    template_dir = get_template_dir(template_id)
    request_id = str(uuid.uuid4())
    work_dir = Path(tempfile.gettempdir()) / f"omr_{request_id}"
    out_dir = Path(tempfile.gettempdir()) / f"omr_out_{request_id}"

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        scans_dir = work_dir / "scans"
        scans_dir.mkdir(exist_ok=True)

        # Write template files from cache (ลด disk I/O ต่อ request)
        cached = _get_cached_template_files(template_id)
        for name, data in cached.items():
            (work_dir / name).write_bytes(data)

        # Evaluation: from Laravel JSON (priority) or from template
        evaluation_sent = evaluation is not None and evaluation.strip()
        logger.info(
            "[API] evaluation param: %s (length=%s)"
            % ("sent" if evaluation_sent else "not sent", len(evaluation) if evaluation else 0)
        )
        if evaluation_sent and evaluation is not None:
            try:
                eval_data = json.loads(evaluation.strip())
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid evaluation JSON: {e!s}",
                ) from e
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
            logger.info(
                "[API] evaluation.json written from request (questions_in_order: %s)"
                % (eval_data.get("options", {}).get("questions_in_order", [])[:3],)
            )
        elif evaluate:
            src = template_dir / "evaluation.json"
            if src.exists():
                shutil.copy2(src, work_dir / "evaluation.json")
                logger.info("[API] evaluation.json copied from template")
            else:
                logger.info("[API] evaluate=True but no evaluation.json in template")
        else:
            logger.info("[API] evaluate=False, no evaluation used")

        has_evaluation = (work_dir / "evaluation.json").exists()
        logger.info(
            "[API] has_evaluation=%s (will draw green/red circles: %s)"
            % (has_evaluation, has_evaluation)
        )

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
        }
        entry_point(Path(work_dir), omr_args)

        # Results CSV มีเมื่อ OMR ผ่าน marker check (เจอ marker ครบทั้ง 4 มุม)
        # ถ้าไม่เจอ marker แม้แต่มุมเดียว → CropOnMarkers return None → ไฟล์ไป ErrorFiles → ไม่มีแถวใน Results
        results_glob = out_dir / "scans" / "Results" / "Results_*.csv"
        csv_files = sorted(glob.glob(str(results_glob)))
        if not csv_files:
            raise HTTPException(
                status_code=400,
                detail="Not a valid OMR sheet: marker(s) not found in one or more corners. All four corner markers must be visible. Please upload a clear OMR answer sheet.",
            )

        df = pd.read_csv(csv_files[0])
        if df.empty:
            raise HTTPException(
                status_code=400,
                detail="Not a valid OMR sheet: marker(s) not found in one or more corners. All four corner markers must be visible. Please upload a clear OMR answer sheet.",
            )

        # First row is our upload (only one image)
        row = df.iloc[0]
        file_id = str(row.get("file_id", upload_path.name))
        score = float(row.get("score", 0)) if "score" in df.columns else None

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

        # Copy checked OMR image to persistent folder (แยกโฟลเดอร์ตามเดือน: CheckedOMRs/YYYY-MM/)
        checked_omr_path = None
        checked_omr_filename = None
        checked_src = out_dir / "scans" / "CheckedOMRs" / file_id
        month_folder = datetime.now().strftime("%Y-%m")  # e.g. 2025-02
        persistent_checked_dir = PROJECT_ROOT / "outputs" / "scans" / "CheckedOMRs" / month_folder
        if checked_src.exists():
            persistent_checked_dir.mkdir(parents=True, exist_ok=True)
            safe_name = f"{request_id}_{Path(image.filename or 'upload').name}"
            persistent_dest = persistent_checked_dir / safe_name
            try:
                shutil.copy2(checked_src, persistent_dest)
                checked_omr_path = str(persistent_dest.relative_to(PROJECT_ROOT))
                # สำหรับโหลดรูปใช้ path จาก CheckedOMRs: YYYY-MM/filename
                checked_omr_filename = f"{month_folder}/{safe_name}"
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
