"""Private resume storage on AWS S3.

* Object keys are generated server-side (random), so user-supplied filenames never reach S3 paths.
* Objects are written with server-side encryption; the bucket itself must block all public access.
* Downloads use short-lived pre-signed URLs that force `Content-Disposition: attachment`, so a
  crafted file can't be rendered inline as a page on the bucket's origin.
"""
import asyncio
import secrets

from ..config import get_settings

PRESIGN_SECONDS = 300
_client = None

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _s3():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config
        s = get_settings()
        kwargs = {}
        if s.aws_access_key_id and s.aws_secret_access_key:
            kwargs = {"aws_access_key_id": s.aws_access_key_id, "aws_secret_access_key": s.aws_secret_access_key}
        _client = boto3.client("s3", region_name=s.aws_region,
                               config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=30,
                                             retries={"max_attempts": 3, "mode": "standard"}),
                               **kwargs)
    return _client


def new_object_key(applicant_id: int, file_type: str) -> str:
    return f"resumes/applicant_{applicant_id}/{secrets.token_hex(16)}.{file_type}"


async def put_resume(key: str, data: bytes, file_type: str) -> None:
    s = get_settings()
    await asyncio.to_thread(_s3().put_object, Bucket=s.s3_bucket_name, Key=key, Body=data,
                            ContentType=CONTENT_TYPES[file_type], ServerSideEncryption="AES256")


async def get_resume(key: str) -> bytes:
    s = get_settings()

    def _get():
        obj = _s3().get_object(Bucket=s.s3_bucket_name, Key=key)
        if obj.get("ContentLength", 0) > s.max_resume_bytes:
            raise ValueError("stored object exceeds size limit")
        return obj["Body"].read()

    return await asyncio.to_thread(_get)


async def delete_resume(key: str) -> None:
    s = get_settings()
    await asyncio.to_thread(_s3().delete_object, Bucket=s.s3_bucket_name, Key=key)


def presigned_download(key: str, display_name: str, file_type: str) -> str:
    s = get_settings()
    return _s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": s.s3_bucket_name, "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{display_name}"',
                "ResponseContentType": CONTENT_TYPES[file_type]},
        ExpiresIn=PRESIGN_SECONDS,
    )
