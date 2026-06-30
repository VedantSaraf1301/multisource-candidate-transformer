from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router

app = FastAPI(
    title="Candidate Data Transformer API",
    description="Thin HTTP wrapper around the transformer pipeline.",
    version="1.0.0",
)

# Allows the Next.js dev server on :3000 to call this API without CORS errors.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
