# Events Recording Worker

Custom recording service for the Events Ticketing System.

The worker joins a LiveKit room as a subscribe-only participant, receives the broadcaster's audio/video tracks, encodes them into MP4 using FFmpeg/PyAV, uploads the completed recording to Amazon S3, and reports the recording status back to Django.

This service does not use LiveKit Egress.

## Architecture

```text
Browser Broadcaster
        |
        v
     LiveKit
        |
        | WebRTC
        v
Recording Worker
        |
        +--> AudioStream
        |
        +--> VideoStream
        |
        v
   FFmpeg / PyAV
        |
        v
    recording.mp4
        |
        v
       S3
        |
        v
      Django
```

## Requirements

* Ubuntu 22.04+
* Python 3.10+
* FFmpeg
* Python virtual environment
* LiveKit server
* Amazon S3
* EC2 IAM role with S3 permissions

## Installation

```bash
sudo apt update
sudo apt install -y ffmpeg python3-venv
```

Create the virtual environment:

```bash
cd /home/ubuntu/worker

python3 -m venv .venv

source .venv/bin/activate

pip install --upgrade pip

pip install -r requirements.txt
```

Verify FFmpeg:

```bash
ffmpeg -version
```

Verify the worker dependencies:

```bash
python -c "import fastapi, boto3, av, livekit; print('dependencies OK')"
```

## Configuration

Create the environment file:

```bash
cp .env.example .env
```

Edit:

```bash
nano .env
```

The worker requires:

```env
RECORDING_WORKER_SECRET=
BACKEND_INTERNAL_URL=
LIVEKIT_URL=
AWS_S3_REGION=
AWS_S3_BUCKET=
```

AWS access keys are intentionally not configured.

The worker uses the EC2 instance IAM role through the normal AWS credential provider chain.

## Running the worker

```bash
source .venv/bin/activate

uvicorn app:app \
  --host 127.0.0.1 \
  --port 8001
```

Health check:

```bash
curl http://127.0.0.1:8001/health
```

Expected:

```json
{
  "status": "ok",
  "service": "events-recording-worker"
}
```

## Recording lifecycle

Django requests:

```http
POST /recording/start
```

with:

```json
{
  "room": "event-slug",
  "job_id": "recording-job-id",
  "s3_prefix": "recordings/event-slug"
}
```

The worker:

1. Validates the shared worker secret.
2. Requests a short-lived LiveKit recording token from Django.
3. Joins the LiveKit room.
4. Subscribes to the broadcaster's media.
5. Encodes audio/video into MP4.
6. Stops when Django requests `/recording/stop`.
7. Uploads the MP4 to S3.
8. Notifies Django that the recording completed.

## Security

The worker uses a dedicated LiveKit identity for each recording.

The worker does not use the broadcaster identity.

The recording token should grant:

```text
room_join=true
can_publish=false
can_subscribe=true
```

The LiveKit API secret should remain on the Django backend.

AWS credentials are not stored in the worker environment.

The EC2 instance profile supplies temporary AWS credentials automatically.

## S3

Recordings use:

```text
recordings/<event-slug>/<job-id>.mp4
```

The S3 bucket should remain private.

Playback should be provided through Django-generated presigned URLs rather than public S3 objects.

## Production process

Run the worker through systemd rather than a terminal session:

```text
systemd
   |
   v
uvicorn
   |
   v
FastAPI worker
```

The worker should normally listen only on:

```text
127.0.0.1:8001
```

If Django is hosted on another network or service, expose the worker through a controlled HTTPS endpoint or private network rather than directly exposing port 8001 to the public internet.
