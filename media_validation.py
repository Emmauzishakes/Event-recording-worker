import json
import os
import subprocess
from pathlib import Path


class MediaValidationError(Exception):
    pass


def validate_mp4(
    file_path: str,
    *,
    require_audio: bool = False,
    min_duration_seconds: float = 1.0,
    min_size_bytes: int = 1024,
) -> dict:
    path = Path(file_path)

    if not path.is_file():
        raise MediaValidationError("Recording file does not exist.")

    file_size = path.stat().st_size

    if file_size < min_size_bytes:
        raise MediaValidationError(
            f"Recording is too small ({file_size} bytes)."
        )

    command = [
        "ffprobe",
        "-v", "error",
        "-count_frames",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(path),
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        metadata = json.loads(result.stdout)
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as exc:
        raise MediaValidationError("FFprobe could not read the recording.") from exc

    streams = metadata.get("streams", [])
    video_streams = [
        stream for stream in streams
        if stream.get("codec_type") == "video"
    ]
    audio_streams = [
        stream for stream in streams
        if stream.get("codec_type") == "audio"
    ]

    if not video_streams:
        raise MediaValidationError("Recording contains no video stream.")

    format_info = metadata.get("format", {})
    try:
        duration = float(format_info.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0

    if duration < min_duration_seconds:
        raise MediaValidationError(
            f"Recording duration is too short: {duration} seconds."
        )

    video_frames = video_streams[0].get("nb_read_frames")
    if video_frames is not None and int(video_frames) <= 0:
        raise MediaValidationError(
            "Video stream contains no readable frames."
        )

    if require_audio and not audio_streams:
        raise MediaValidationError("Recording contains no audio stream.")

    return {
        "file_size": file_size,
        "duration": duration,
        "has_video": True,
        "has_audio": bool(audio_streams),
        "video_codec": video_streams[0].get("codec_name"),
        "audio_codec": (
            audio_streams[0].get("codec_name")
            if audio_streams else None
        ),
        "video_frames": video_frames,
    }