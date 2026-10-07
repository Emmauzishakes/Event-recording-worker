import os
from dotenv import load_dotenv

load_dotenv()

def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


RECORDING_WORKER_SECRET = required("RECORDING_WORKER_SECRET")

BACKEND_INTERNAL_URL = required("BACKEND_INTERNAL_URL").rstrip("/")

LIVEKIT_URL = required("LIVEKIT_URL")

AWS_S3_REGION = os.getenv("AWS_S3_REGION", "af-south-1")
AWS_S3_BUCKET = required("AWS_S3_BUCKET")

AWS_S3_ENDPOINT = os.getenv("AWS_S3_ENDPOINT", "").strip()

WORKER_HOST = os.getenv("WORKER_HOST", "127.0.0.1")
WORKER_PORT = int(os.getenv("WORKER_PORT", "8001"))

RECORDINGS_DIR = os.getenv(
    "RECORDINGS_DIR",
    "/home/ubuntu/worker/recordings",
)

VIDEO_WIDTH = int(os.getenv("VIDEO_WIDTH", "1280"))
VIDEO_HEIGHT = int(os.getenv("VIDEO_HEIGHT", "720"))
VIDEO_FPS = int(os.getenv("VIDEO_FPS", "30"))

VIDEO_CODEC = os.getenv("VIDEO_CODEC", "libx264")
VIDEO_PRESET = os.getenv("VIDEO_PRESET", "veryfast")
VIDEO_CRF = int(os.getenv("VIDEO_CRF", "23"))

AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "48000"))
AUDIO_CHANNELS = int(os.getenv("AUDIO_CHANNELS", "2"))