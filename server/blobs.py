"""Where the photos themselves are kept: Cloudflare R2 (or any S3-style storage).

When the R2_* settings are present, every preview is written to the bucket and
the server's own disk is only a working copy that can be emptied at any time.
Without them, photos stay on the server's disk as before.

    R2_ENDPOINT  R2_BUCKET  R2_ACCESS_KEY_ID  R2_SECRET_ACCESS_KEY
"""
from __future__ import annotations

import os
from pathlib import Path


class NoBlobs:
    """Storage switched off: everything stays on the server's disk."""
    on = False

    def put(self, key: str, path: Path) -> None: ...
    def fetch(self, key: str, path: Path) -> bool: return False
    def delete_prefix(self, prefix: str) -> int: return 0


class R2Blobs:
    on = True

    def __init__(self, endpoint: str, bucket: str, key_id: str, secret: str):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.s3 = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=key_id, aws_secret_access_key=secret,
                               region_name="auto", config=Config(signature_version="s3v4", retries={"max_attempts": 4}))

    def put(self, key: str, path: Path) -> None:
        self.s3.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": "image/jpeg"})

    def fetch(self, key: str, path: Path) -> bool:
        """Bring one object down to path. False when the bucket does not have it."""
        from botocore.exceptions import ClientError
        part = path.with_suffix(f".{os.getpid()}.fetch")
        try:
            self.s3.download_file(self.bucket, key, str(part))
        except ClientError as e:
            part.unlink(missing_ok=True)
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        part.replace(path)
        return True

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        pages = self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix)
        for page in pages:
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
                n += len(keys)
        return n


def from_environment():
    e = os.environ
    names = ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    if all(e.get(n) for n in names):
        return R2Blobs(*(e[n] for n in names))
    return NoBlobs()
