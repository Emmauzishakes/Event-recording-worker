import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
from livekit import rtc


logger = logging.getLogger(__name__)


@dataclass
class MediaFrame:
    timestamp: float
    data: bytes
    width: int = 0
    height: int = 0
    sample_rate: int = 0
    channels: int = 0


class LiveKitReceiver:
    def __init__(
        self,
        livekit_url: str,
        token: str,
    ):
        self.livekit_url = livekit_url
        self.token = token

        self.room: Optional[rtc.Room] = None

        self.video_queue: asyncio.Queue = asyncio.Queue(
            maxsize=3
        )

        self.audio_queue: asyncio.Queue = asyncio.Queue(
            maxsize=20
        )

        self.video_streams = []
        self.audio_streams = []

        self.running = False

        self.video_task: Optional[asyncio.Task] = None
        self.audio_task: Optional[asyncio.Task] = None

    async def connect(self):
        self.room = rtc.Room()

        @self.room.on("track_subscribed")
        def on_track_subscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ):
            logger.info(
                "Subscribed to track=%s kind=%s participant=%s",
                publication.sid,
                track.kind,
                participant.identity,
            )

            if participant.identity.startswith("recording-worker"):
                return

            if track.kind == rtc.TrackKind.KIND_VIDEO:
                task = asyncio.create_task(
                    self._receive_video(track)
                )

                self.video_streams.append(task)

            elif track.kind == rtc.TrackKind.KIND_AUDIO:
                task = asyncio.create_task(
                    self._receive_audio(track)
                )

                self.audio_streams.append(task)

        @self.room.on("track_unsubscribed")
        def on_track_unsubscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ):
            logger.info(
                "Track unsubscribed: %s from %s",
                publication.sid,
                participant.identity,
            )

        @self.room.on("participant_connected")
        def on_participant_connected(
            participant: rtc.RemoteParticipant,
        ):
            logger.info(
                "Participant connected: %s",
                participant.identity,
            )

        @self.room.on("participant_disconnected")
        def on_participant_disconnected(
            participant: rtc.RemoteParticipant,
        ):
            logger.info(
                "Participant disconnected: %s",
                participant.identity,
            )

        @self.room.on("connection_state_changed")
        def on_connection_state_changed(
            state: rtc.ConnectionState,
        ):
            logger.info(
                "LiveKit connection state: %s",
                state,
            )

        await self.room.connect(
            self.livekit_url,
            self.token,
            options=rtc.RoomOptions(
                auto_subscribe=True,
            ),
        )

        self.running = True

        logger.info(
            "Connected to LiveKit room: %s",
            self.room.name,
        )

        await self._subscribe_existing_tracks()

    async def _subscribe_existing_tracks(self):
        if self.room is None:
            return

        for participant in self.room.remote_participants.values():
            if participant.identity.startswith("recording-worker"):
                continue

            for publication in participant.track_publications.values():
                if publication.kind not in (
                    rtc.TrackKind.KIND_AUDIO,
                    rtc.TrackKind.KIND_VIDEO,
                ):
                    continue

                if publication.subscribed:
                    track = publication.track

                    if track is None:
                        continue

                    if track.kind == rtc.TrackKind.KIND_VIDEO:
                        task = asyncio.create_task(
                            self._receive_video(track)
                        )
                        self.video_streams.append(task)

                    elif track.kind == rtc.TrackKind.KIND_AUDIO:
                        task = asyncio.create_task(
                            self._receive_audio(track)
                        )
                        self.audio_streams.append(task)

    async def _receive_video(
        self,
        track: rtc.RemoteVideoTrack,
    ):
        stream = rtc.VideoStream(
            track,
            capacity=1,
        )

        try:
            async for event in stream:
                if not self.running:
                    break

                frame = event.frame

                # Normalize to I420 because FFmpeg/PyAV
                # can encode it efficiently.
                i420 = frame.convert(
                    rtc.VideoBufferType.I420
                )

                y = bytes(i420.get_plane(0))
                u = bytes(i420.get_plane(1))
                v = bytes(i420.get_plane(2))

                data = y + u + v

                item = MediaFrame(
                    timestamp=event.timestamp_us / 1_000_000
                    if hasattr(event, "timestamp_us")
                    else 0,
                    data=data,
                    width=i420.width,
                    height=i420.height,
                )

                if self.video_queue.full():
                    try:
                        self.video_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass

                await self.video_queue.put(item)

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Video stream failed"
            )

        finally:
            await stream.aclose()

    async def _receive_audio(
        self,
        track: rtc.RemoteAudioTrack,
    ):
        stream = rtc.AudioStream(
            track,
            sample_rate=48000,
            num_channels=2,
        )

        try:
            async for event in stream:
                if not self.running:
                    break

                frame = event.frame

                data = bytes(frame.data)

                item = MediaFrame(
                    timestamp=event.timestamp_us / 1_000_000
                    if hasattr(event, "timestamp_us")
                    else 0,
                    data=data,
                    sample_rate=frame.sample_rate,
                    channels=frame.num_channels,
                )

                if self.audio_queue.full():
                    try:
                        self.audio_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass

                await self.audio_queue.put(item)

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Audio stream failed"
            )

        finally:
            await stream.aclose()

    async def wait_for_video(self, timeout: float = 30):
        return await asyncio.wait_for(
            self.video_queue.get(),
            timeout=timeout,
        )

    async def wait_for_audio(self, timeout: float = 30):
        return await asyncio.wait_for(
            self.audio_queue.get(),
            timeout=timeout,
        )

    async def stop(self):
        self.running = False

        tasks = (
            self.video_streams
            + self.audio_streams
        )

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )

        self.video_streams.clear()
        self.audio_streams.clear()

        if self.room is not None:
            await self.room.disconnect()
            self.room = None

        logger.info(
            "LiveKit receiver stopped"
        )