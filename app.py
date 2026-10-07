import logging

from fastapi import FastAPI

from routes.health import router as health_router
from routes.recording import router as recording_router


logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
)


app = FastAPI(
    title="Events Recording Worker",
    version="1.0.0",
)


app.include_router(
    health_router
)

app.include_router(
    recording_router
)


@app.get("/")
async def root():
    return {
        "service": "events-recording-worker",
        "status": "running",
    }