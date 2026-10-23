import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import av
import httpx
import numpy as np

from config import (
    BACKEND_INTERNAL_URL,
    LIVEKIT_URL,
    RECORDINGS_DIR,
    RECORDING_WORKER_SECRET,
    VIDEO_CODEC,
    VIDEO_CRF,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_PRESET,
    VIDEO_WIDTH,
    AWS_S3_BUCKET,
)

from livekit_service import LiveKitReceiver
from media_validation import validate_mp4
from s3_service import upload_file


logger = logging.getLogger(__name__)


@dataclass
class RecordingJob:
    job_id: str
    room: str
    s3_prefix: str

    stop_event: asyncio.Event

    task: Optional[asyncio.Task] = None

    status: str = "starting"

    local_path: Optional[str] = None
    s3_key: Optional[str] = None

    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class RecordingManager:
    def __init__(self):
        self.jobs: dict[str, RecordingJob] = {}

    async def start(
        self,
        room: str,
        job_id: str,
        s3_prefix: str,
    ) -> RecordingJob:

        if job_id in self.jobs:
            raise RuntimeError(
                f"Recording job already exists: {job_id}"
            )

        active = [
            job
            for job in self.jobs.values()
            if job.status in (
                "starting",
                "recording",
                "finalizing",
            )
        ]

        if active:
            raise RuntimeError(
                "Another recording is already active"
            )

        job = RecordingJob(
            job_id=job_id,
            room=room,
            s3_prefix=s3_prefix,
            stop_event=asyncio.Event(),
        )

        self.jobs[job_id] = job

        job.task = asyncio.create_task(
            self._run(job)
        )

        return job

    async def stop(
        self,
        job_id: str,
    ):
        job = self.jobs.get(job_id)

        if job is None:
            raise RuntimeError(
                f"Recording job not found: {job_id}"
            )

        if job.status not in (
            "starting",
            "recording",
        ):
            return job

        logger.info(
            "Stopping recording job: %s",
            job_id,
        )

        job.status = "finalizing"
        job.stop_event.set()

        return job

    async def _run(
        self,
        job: RecordingJob,
    ):
        receiver: Optional[LiveKitReceiver] = None
        container: Optional[av.container.OutputContainer] = None

        try:
            Path(RECORDINGS_DIR).mkdir(parents=True, exist_ok=True)

            job.local_path = os.path.join(RECORDINGS_DIR, f"{job.job_id}.mp4")
            partial_path = os.path.join(RECORDINGS_DIR, f"{job.job_id}.partial.mp4")
            token = await self._request_recording_token(room=job.room, job_id=job.job_id)
            receiver = LiveKitReceiver(livekit_url=LIVEKIT_URL, token=token)

            await receiver.connect()
            logger.info("Waiting for broadcaster media...")

            first_video = await receiver.wait_for_video(timeout=60)
            first_audio = None

            try:
                first_audio = await receiver.wait_for_audio(
                    timeout=5
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "No audio received yet; continuing"
                )

            container = av.open(partial_path, mode="w", format="mp4")

            video_stream = container.add_stream(VIDEO_CODEC, rate=VIDEO_FPS)

            video_stream.width = VIDEO_WIDTH
            video_stream.height = VIDEO_HEIGHT
            video_stream.pix_fmt = "yuv420p"

            video_stream.options = {
                "preset": VIDEO_PRESET,
                "crf": str(VIDEO_CRF),
            }

            audio_stream = container.add_stream("aac", rate=48000)
            audio_stream.layout = "stereo"

            job.status = "recording"
            job.started_at = time.time()

            await self._callback_started(job)

            video_task = asyncio.create_task(
                self._write_video(job, receiver, container, video_stream, first_video)
            )
            audio_task = asyncio.create_task(
                self._write_audio(job, receiver, container, audio_stream, first_audio)
            )

            await asyncio.gather(video_task, audio_task)

            for packet in video_stream.encode(None):
                container.mux(packet)
            for packet in audio_stream.encode(None):
                container.mux(packet)

            container.close()
            container = None

            os.replace(partial_path, job.local_path)
            validation = validate_mp4(job.local_path)

            job.status = "finalizing"
            job.completed_at = time.time()

            s3_key = f"{job.s3_prefix.rstrip('/')}/{job.job_id}.mp4"
            job.s3_key = s3_key
            file_size = upload_file(job.local_path, s3_key)
            duration = job.completed_at - job.started_at if job.started_at else 0

            await self._callback_complete(
                job=job,
                file_size=file_size,
                duration=duration,
                has_video=validation["has_video"],
                has_audio=validation["has_audio"],
            )

            logger.info(
                "Recording completed: job=%s s3=%s",
                job.job_id,
                s3_key,
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            logger.exception(
                "Recording failed: %s",
                job.job_id,
            )

            job.status = "failed"
            job.completed_at = time.time()

            await self._callback_failed(
                job,
                str(exc),
            )

        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    logger.exception(
                        "Failed closing media container"
                    )

            if receiver is not None:
                try:
                    await receiver.stop()
                except Exception:
                    logger.exception(
                        "Failed stopping LiveKit receiver"
                    )

    async def _write_video(
        self,
        job: RecordingJob,
        receiver: LiveKitReceiver,
        container,
        stream,
        first_frame,
    ):
        frame = first_frame

        while not job.stop_event.is_set():
            try:
                await self._encode_video_frame(
                    container,
                    stream,
                    frame,
                )

                frame = await receiver.wait_for_video(
                    timeout=2
                )

            except asyncio.TimeoutError:
                continue

        logger.info(
            "Video recording loop stopped: %s",
            job.job_id,
        )

    async def _write_audio(
        self,
        job: RecordingJob,
        receiver: LiveKitReceiver,
        container,
        stream,
        first_frame,
    ):
        frame = first_frame

        while not job.stop_event.is_set():
            try:
                if frame is None:
                    frame = await receiver.wait_for_audio(timeout=2)

                self._encode_audio_frame(container, stream, frame)
                frame = await receiver.wait_for_audio(timeout=2)

            except asyncio.TimeoutError:
                continue

        logger.info("Audio recording loop stopped: %s", job.job_id)

    async def _encode_video_frame(
        self,
        container,
        stream,
        media_frame,
    ):
        data = np.frombuffer(
            media_frame.data,
            dtype=np.uint8,
        )

        expected = (
            media_frame.width
            * media_frame.height
            * 3
            // 2
        )

        if data.size != expected:
            logger.warning(
                "Unexpected I420 frame size: "
                "%s expected=%s",
                data.size,
                expected,
            )

            return

        frame = av.VideoFrame(
            media_frame.width,
            media_frame.height,
            "yuv420p",
        )

        y_size = (
            media_frame.width
            * media_frame.height
        )

        uv_size = y_size // 4

        frame.planes[0].update(
            data[
                0:y_size
            ].tobytes()
        )

        frame.planes[1].update(
            data[
                y_size:y_size + uv_size
            ].tobytes()
        )

        frame.planes[2].update(
            data[
                y_size + uv_size:
            ].tobytes()
        )

        frame = frame.reformat(
            width=VIDEO_WIDTH,
            height=VIDEO_HEIGHT,
            format="yuv420p",
        )

        for packet in stream.encode(frame):
            container.mux(packet)

    def _encode_audio_frame(
        self,
        container,
        stream,
        media_frame,
    ):
        data = np.frombuffer(
            media_frame.data,
            dtype=np.int16,
        )

        if data.size == 0:
            return

        channels = media_frame.channels or 2
        sample_rate = media_frame.sample_rate or 48000

        samples_per_channel = (
            data.size // channels
        )

        frame = av.AudioFrame.from_ndarray(
            data.reshape(
                1,
                -1,
            ),
            format="s16",
            layout="stereo"
            if channels == 2
            else "mono",
        )

        frame.sample_rate = sample_rate

        frame = frame.resample(
            format="fltp",
            layout="stereo",
            rate=48000,
        )

        for packet in stream.encode(frame):
            container.mux(packet)

    async def _request_recording_token(
        self,
        room: str,
        job_id: str,
    ) -> str:
        url = (
            f"{BACKEND_INTERNAL_URL}"
            f"/api/internal/recordings/token"
        )

        async with httpx.AsyncClient(
            timeout=15
        ) as client:

            response = await client.post(
                url,
                json={
                    "room": room,
                    "job_id": job_id,
                },
                headers={
                    "X-Worker-Secret":
                        RECORDING_WORKER_SECRET,
                },
            )

            response.raise_for_status()

            payload = response.json()

        token = payload.get("token")

        if not token:
            raise RuntimeError(
                "Django did not return a recording token"
            )

        return token

    async def _callback_started(
        self,
        job: RecordingJob,
    ):
        url = (
            f"{BACKEND_INTERNAL_URL}"
            f"/api/internal/recordings/"
            f"{job.job_id}/started"
        )

        await self._callback(
            url,
            {
                "job_id": job.job_id,
                "room": job.room,
            },
        )

    async def _callback_complete(
        self,
        job: RecordingJob,
        file_size: int,
        duration: float,
        has_video: bool,
        has_audio: bool,
    ):
        url = f"{BACKEND_INTERNAL_URL}/api/internal/recordings/{job.job_id}/complete"

        await self._callback(
            url,
            {
                "job_id": job.job_id,
                "room": job.room,
                "s3_key": job.s3_key,
                "file_size": file_size,
                "duration": round(duration, 3),
                "has_video": has_video,
                "has_audio": has_audio,
            },
        )

    async def _callback_failed(
        self,
        job: RecordingJob,
        error: str,
    ):
        url = (
            f"{BACKEND_INTERNAL_URL}"
            f"/api/internal/recordings/"
            f"{job.job_id}/failed"
        )

        try:
            await self._callback(
                url,
                {
                    "job_id": job.job_id,
                    "room": job.room,
                    "error": error[:4000],
                },
            )
        except Exception:
            logger.exception(
                "Failed sending failure callback"
            )

    async def _callback(
        self,
        url: str,
        payload: dict,
    ):
        async with httpx.AsyncClient(
            timeout=15
        ) as client:

            response = await client.post(
                url,
                json=payload,
                headers={
                    "X-Worker-Secret":
                        RECORDING_WORKER_SECRET,
                },
            )

            response.raise_for_status()

    def get_status(
        self,
        job_id: str,
    ) -> Optional[dict]:

        job = self.jobs.get(job_id)

        if job is None:
            return None

        return {
            "job_id": job.job_id,
            "room": job.room,
            "status": job.status,
            "s3_key": job.s3_key,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
        }


recording_manager = RecordingManager()