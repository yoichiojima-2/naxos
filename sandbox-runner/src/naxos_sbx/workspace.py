import functools
import logging
from pathlib import Path

from google.cloud import storage

from .config import CLAUDE_CONFIG_DIR, WORKDIR

log = logging.getLogger(__name__)


@functools.cache
def _storage_client() -> storage.Client:
    return storage.Client()


class Workspace:
    """Session workspace persisted to the environment's GCS bucket.

    Layout in the bucket: sessions/{session_id}/ws/** for agent files and
    sessions/{session_id}/transcript/** for the SDK's session files.
    """

    def __init__(self, session_id: str, bucket: str) -> None:
        self.session_id = session_id
        self.bucket_name = bucket
        self.root = WORKDIR / session_id
        self.ws = self.root / "ws"

    @property
    def prefix(self) -> str:
        return f"sessions/{self.session_id}"

    def _bucket(self) -> storage.Bucket:
        return _storage_client().bucket(self.bucket_name)

    def restore(self) -> None:
        self.ws.mkdir(parents=True, exist_ok=True)
        CLAUDE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        for blob in self._bucket().list_blobs(prefix=f"{self.prefix}/"):
            relative = blob.name[len(self.prefix) + 1 :]
            if not relative:
                continue
            area, _, tail = relative.partition("/")
            if not tail:
                continue
            base = {"ws": self.ws, "transcript": CLAUDE_CONFIG_DIR}.get(area)
            if base is None:
                continue
            target = base / tail
            target.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(target)
        log.info("restored workspace for %s", self.session_id)

    def checkpoint(self) -> None:
        self._upload_tree(self.ws, "ws")
        self._upload_tree(CLAUDE_CONFIG_DIR, "transcript")
        log.info("checkpointed workspace for %s", self.session_id)

    def _upload_tree(self, source: Path, area: str) -> None:
        if not source.exists():
            return
        bucket = self._bucket()
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            name = f"{self.prefix}/{area}/{path.relative_to(source)}"
            bucket.blob(name).upload_from_filename(path)
