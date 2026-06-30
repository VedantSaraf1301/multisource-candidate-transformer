"""
main.py — FastAPI application entry point.

Run with:
    uvicorn backend.api.main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router

app = FastAPI(
    title="Candidate Data Transformer API",
    description="Thin HTTP wrapper around the transformer pipeline.",
    version="1.0.0",
)

# Allow the Next.js dev server to call this API without browser CORS errors.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
