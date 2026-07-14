"""RFE validation webhook (Task 5.1).

A FastAPI app that accepts an RFE submission zip and validates its formatting.
The request body is the raw zip (``Content-Type: application/zip``); the response
reports whether the submission was accepted and a summary of its corpus.

Run with e.g. ``uvicorn rfe_service.app:app``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from rfe_service.ingest import SubmissionError, load_submission

app = FastAPI(title="RFE Validation Webhook", version="1")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/rfe/submit", response_model=None)
async def submit(request: Request) -> JSONResponse | dict[str, object]:
    """Accept a submission zip; validate its formatting and summarize it."""
    body = await request.body()
    try:
        submission = load_submission(body)
    except SubmissionError as exc:
        return JSONResponse(status_code=422, content={"accepted": False, "error": str(exc)})
    return {
        "accepted": True,
        "grammar": submission.grammar,
        "kind": submission.kind,
        "samples": submission.sample_count,
        "submitter": submission.meta["submitter"],
    }
