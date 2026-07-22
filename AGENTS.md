# OMRChecker Project Instructions

## Project overview

OMRChecker is a Python FastAPI service used by the SMHub Laravel application to check OMR answer sheets.

Production environment:

* Ubuntu 22.04
* Project path: `/var/www/OMRChecker`
* Python virtual environment: `/var/www/OMRChecker/venv`
* API listens on `127.0.0.1:8080`
* Laravel calls the API through `POST /check`
* The service is managed by `omr-checker-api.service`
* Current startup command uses `run_api.py`
* Image processing uses OpenCV and NumPy
* Laravel is a separate project at `/var/www/smhub_web`

## Important constraints

* Do not change the existing `/check` API request or response contract.
* Do not modify the Laravel project unless explicitly requested.
* Existing OMR detection and scoring results must remain compatible.
* Prioritize correctness first, then memory usage, then processing speed.
* Do not introduce external paid services.
* Avoid unnecessary architectural rewrites.
* Make focused, reviewable changes.

## Performance context

The production server has approximately:

* 10 GB RAM
* 2 GB swap
* MySQL uses approximately 1.6 GB RAM
* OMRChecker previously ran with 4 workers and reached approximately 3.1 GB RAM after two weeks
* Individual OMR workers grew to different sizes, from approximately 300 MB to 1.2 GB
* Typical processing time is approximately 1.5–2 seconds per sheet
* CPU is usually mostly idle

The production service should normally use 2 workers unless benchmarks demonstrate that more workers are necessary and memory-safe.

## Optimization rules

When changing image-processing code:

* Avoid retaining request images in global variables.
* Do not cache original, thresholded, warped, annotated, or intermediate images.
* Template/configuration caching must have a bounded maximum size.
* Release temporary image references as soon as practical.
* Temporary directories and files must be deleted in a `finally` block.
* Resize oversized input images before expensive processing when this does not reduce detection accuracy.
* Avoid processing or scanning an entire directory when only one request image is required.
* Avoid unnecessary NumPy array copies.
* Do not use unbounded lists, dictionaries, caches, or logging handlers.
* Limit OpenCV, OpenBLAS, MKL, OMP, and NumExpr thread usage where appropriate.
* Prefer worker recycling over hiding a confirmed memory leak.
* Do not add `gc.collect()` as the only solution to memory growth.

## Investigation requirements

Before modifying code:

1. Inspect `run_api.py` and the FastAPI `/check` endpoint.
2. Trace the complete image-processing path.
3. Identify all global state, caches, temporary directories, image copies, and directory scans.
4. Determine whether memory growth is a real retained-reference leak or native-library memory pooling.
5. Check whether request cleanup occurs on both success and failure.

Add lightweight per-request diagnostics when useful:

* Worker PID
* RSS before processing
* RSS after processing
* Memory delta
* Processing duration
* Input image dimensions

Do not log sensitive student data or full image contents.

## Validation requirements

After making changes:

* Run existing automated tests.
* Add focused tests where practical.
* Test successful and failed OMR requests.
* Confirm temporary files are removed.
* Confirm the API response remains compatible.
* Compare output accuracy before and after optimization.
* Benchmark processing time and memory usage.
* Report files changed, root cause found, tests run, and remaining risks.

Do not claim that a memory leak is fixed unless measurements support the claim.
