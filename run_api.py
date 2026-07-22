#!/usr/bin/env python3
"""
Run OMR Checker API (port 8080, does not conflict with Laravel 80/8000).
Usage: python run_api.py
       python run_api.py --port 8081 --workers 2
"""
import argparse
import os

for _thread_env in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "OMR_OPENCV_THREADS",
):
    os.environ.setdefault(_thread_env, "1")

import uvicorn


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OMR Checker API")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (0.0.0.0 for VPS)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default 8080, no conflict with Laravel)")
    parser.add_argument(
        "--workers",
        type=int,
        default=_positive_int_env("OMR_API_WORKERS", 2),
        help="Number of worker processes (default 2; override with OMR_API_WORKERS or --workers)",
    )
    args = parser.parse_args()
    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        reload=False,
    )
