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


async def delete_prefix(bucket: str, prefix: str) -> None:
    def _delete_prefix() -> None:
        storage_client = client()
        blobs = list(storage_client.bucket(bucket).list_blobs(prefix=prefix))
        for start in range(0, len(blobs), 100):
            with storage_client.batch(raise_exception=False):
                for blob in blobs[start : start + 100]:
                    blob.delete()

    await asyncio.to_thread(_delete_prefix)


async def delete(bucket: str, path: str) -> None:
    def _delete() -> None:
        blob = client().bucket(bucket).blob(path)
        if blob.exists():
            blob.delete()

    await asyncio.to_thread(_delete)
