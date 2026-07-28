import logging
import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from google.cloud import storage
from google.cloud.storage import Client

from src import mcp

logger = logging.getLogger(__name__)


@dataclass
class CloudStorage:
    """Cloud Storage wrapper designed to be handed to an agent as tools.

    Same philosophy as src/bq.py: guardrails live in code, not in the
    prompt. Reads into the model's context are size-capped and listings
    are count-capped so a large bucket or object cannot blow up a turn.
    The agent-facing tools are read-only; write access is expected to be
    blocked by IAM (objectViewer), not by this class.
    """

    project: str | None = os.environ.get("GCLOUD_PROJECT_ID")
    max_read_bytes: int = 1024**2  # 1 MB per object read
    max_list_results: int = 200

    @cached_property
    def client(self) -> Client:
        # storage.Client(project=None) means "no project" rather than
        # "infer from ADC", so only pass it when explicitly set
        if self.project:
            return storage.Client(project=self.project)
        return storage.Client()

    def list_buckets(self) -> list[str]:
        return [b.name for b in self.client.list_buckets()]

    def list_objects(self, bucket: str, prefix: str = "") -> list[str]:
        blobs = self.client.list_blobs(bucket, prefix=prefix, max_results=self.max_list_results)
        return [b.name for b in blobs]

    def get_object_info(self, bucket: str, path: str) -> dict:
        blob = self.client.bucket(bucket).get_blob(path)
        if blob is None:
            raise FileNotFoundError(f"gs://{bucket}/{path} not found")
        return {
            "uri": f"gs://{bucket}/{path}",
            "size_bytes": blob.size,
            "content_type": blob.content_type,
            "updated": blob.updated.isoformat(),
            "storage_class": blob.storage_class,
        }

    def exists(self, bucket: str, path: str) -> bool:
        return self.client.bucket(bucket).blob(path).exists()

    def read_text(self, bucket: str, path: str) -> str:
        logger.info(f"read: gs://{bucket}/{path}")
        blob = self.client.bucket(bucket).get_blob(path)
        if blob is None:
            raise FileNotFoundError(f"gs://{bucket}/{path} not found")
        data = blob.download_as_bytes(start=0, end=self.max_read_bytes - 1)
        text = data.decode("utf-8", errors="replace")
        if blob.size > self.max_read_bytes:
            text += f"\n...[truncated: showing {self.max_read_bytes} of {blob.size} bytes]"
        return text

    def write_text(self, bucket: str, path: str, data: str) -> str:
        """Programmatic use only - deliberately not exposed in tools()."""
        self.client.bucket(bucket).blob(path).upload_from_string(data)
        return f"gs://{bucket}/{path}"

    def upload_file(self, bucket: str, path: str, source: Path | str) -> str:
        """Programmatic use only - deliberately not exposed in tools()."""
        self.client.bucket(bucket).blob(path).upload_from_filename(str(source))
        return f"gs://{bucket}/{path}"

    def download_file(self, bucket: str, path: str, dest: Path | str) -> None:
        """Programmatic use only - deliberately not exposed in tools()."""
        blob = self.client.bucket(bucket).get_blob(path)
        if blob is None:
            raise FileNotFoundError(f"gs://{bucket}/{path} not found")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(dest)

    def download_prefix(self, bucket: str, prefix: str, dest: Path | str) -> int:
        """Programmatic use only - deliberately not exposed in tools()."""
        dest = Path(dest)
        count = 0
        for blob in self.client.list_blobs(bucket, prefix=prefix):
            relative = blob.name.removeprefix(prefix)
            if not relative:
                continue
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(target)
            count += 1
        return count

    def mcp(self):
        return mcp.server("gcs", self.tools())

    def tools(self) -> list:
        """Read-only agent-facing tools for the Claude Agent SDK.

        Errors come back as tool results instead of raising so the model
        can read the message and correct itself on the next turn.
        """
        from claude_agent_sdk import tool

        @tool(
            "list_gcs_objects",
            "List object paths in a Cloud Storage bucket. Listings are "
            "capped, so narrow down with a prefix when a bucket holds many "
            "objects, e.g. prefix='logs/2026-07-'. Bucket name goes without "
            "the gs:// scheme.",
            {
                "type": "object",
                "properties": {"bucket": {"type": "string"}, "prefix": {"type": "string"}},
                "required": ["bucket"],
            },
        )
        async def list_gcs_objects(args):
            try:
                return mcp.result(mcp.dumps(self.list_objects(args["bucket"], args.get("prefix", ""))))
            except Exception as e:
                return mcp.result(f"Failed: {e}")

        @tool(
            "get_gcs_object_info",
            "Get a Cloud Storage object's size, content type, and last "
            "update. Call this before reading an unfamiliar object: the "
            "size tells you whether content will come back truncated, the "
            "content type whether it is text at all.",
            {"bucket": str, "path": str},
        )
        async def get_gcs_object_info(args):
            try:
                return mcp.result(mcp.dumps(self.get_object_info(args["bucket"], args["path"])))
            except Exception as e:
                return mcp.result(f"Failed: {e}")

        @tool(
            "read_gcs_object",
            "Read a text object from Cloud Storage. Reads are size-capped "
            "and marked as truncated when the object is larger than the "
            "cap. Binary content comes back garbled - check the content "
            "type with get_gcs_object_info if unsure.",
            {"bucket": str, "path": str},
        )
        async def read_gcs_object(args):
            try:
                return mcp.result(self.read_text(args["bucket"], args["path"]))
            except Exception as e:
                return mcp.result(f"Failed: {e}")

        return [list_gcs_objects, get_gcs_object_info, read_gcs_object]
