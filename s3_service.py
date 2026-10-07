import logging
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from config import AWS_S3_BUCKET, AWS_S3_ENDPOINT, AWS_S3_REGION

logger = logging.getLogger(__name__)
_client = None

def get_s3_client():
    global _client
    if _client is not None:
        return _client

    config = Config(
        signature_version="s3v4",
        retries={
            "max_attempts": 5,
            "mode": "standard",
        },
        connect_timeout=10,
        read_timeout=120,
    )

    kwargs = {"region_name": AWS_S3_REGION, "config": config}
    if AWS_S3_ENDPOINT:
        kwargs["endpoint_url"] = AWS_S3_ENDPOINT
    _client = boto3.client("s3", **kwargs)
    return _client

def upload_file(local_path: str, s3_key: str, content_type: str = "video/mp4") -> int:
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(local_path)
    client = get_s3_client()
    extra_args = {"ContentType": content_type}
    logger.info("Uploading recording to s3://%s/%s", AWS_S3_BUCKET, s3_key)
    client.upload_file(str(path), AWS_S3_BUCKET, s3_key, ExtraArgs=extra_args)
    size = path.stat().st_size
    logger.info("S3 upload complete: %s bytes", size)
    return size

def object_exists(s3_key: str) -> bool:
    client = get_s3_client()
    try:
        client.head_object(Bucket=AWS_S3_BUCKET, Key=s3_key)
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise

def get_object_size(s3_key: str) -> int:
    client = get_s3_client()
    response = client.head_object(Bucket=AWS_S3_BUCKET, Key=s3_key)
    return int(response["ContentLength"])

def delete_object(s3_key: str) -> None:
    client = get_s3_client()
    client.delete_object(Bucket=AWS_S3_BUCKET, Key=s3_key)


def presign_get(s3_key: str, expires_in: int = 3600) -> str:
    client = get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": AWS_S3_BUCKET,
            "Key": s3_key,
        },
        ExpiresIn=expires_in,
    )