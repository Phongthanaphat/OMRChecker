#!/usr/bin/env python3
"""
Run OMR Checker API (port 8080, does not conflict with Laravel 80/8000).
Usage: python run_api.py
       python run_api.py --port 8081 --workers 4
"""
import argparse
import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OMR Checker API")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (0.0.0.0 for VPS)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default 8080, no conflict with Laravel)")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (default 1; use 4-8 for production)",
    )
    args = parser.parse_args()
    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        reload=False,
    )
