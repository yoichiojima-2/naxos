import asyncio
import functools

from google.cloud import storage


@functools.cache
def client() -> storage.Client:
    return storage.Client()


async def download(bucket: str, path: str) -> bytes | None:
    def _download() -> bytes | None:
        blob = client().bucket(bucket).blob(path)
        if not blob.exists():
            return None
        return blob.download_as_bytes()

    return await asyncio.to_thread(_download)


async def delete(bucket: str, path: str) -> None:
    def _delete() -> None:
        blob = client().bucket(bucket).blob(path)
        if blob.exists():
            blob.delete()

    await asyncio.to_thread(_delete)
