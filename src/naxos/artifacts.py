import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from naxos import mcp
from naxos.gcs import CloudStorage

logger = logging.getLogger(__name__)


def share_url(bucket: str, path: str) -> str:
    """Serve through the IAP-protected UI when its URL is known; the direct
    GCS link needs per-user bucket IAM and 403s for everyone else."""
    ui = os.environ.get("UI_URL", "").rstrip("/")
    if ui:
        return f"{ui}/artifacts/{path}"
    return f"https://storage.cloud.google.com/{bucket}/{path}"


def browse(cs: CloudStorage, bucket: str) -> list[dict]:
    """Group the store's objects back into the artifacts publish() wrote:
    one entry per <role>/<date>-<title>/ prefix, newest first."""
    groups: dict[tuple[str, str], list[str]] = {}
    for name in cs.list_all(bucket):
        role, _, rest = name.partition("/")
        folder, _, file = rest.partition("/")
        if not file:
            continue
        groups.setdefault((role, folder), []).append(file)
    artifacts = []
    for (role, folder), files in groups.items():
        entry = "index.html" if "index.html" in files else files[0] if len(files) == 1 else ""
        artifacts.append(
            {
                "role": role,
                "date": folder[:10],
                "title": folder[11:],
                "url": f"/artifacts/{role}/{folder}/{entry}".rstrip("/"),
                "files": len(files),
            }
        )
    artifacts.sort(key=lambda a: (a["title"], a["role"]))
    artifacts.sort(key=lambda a: a["date"], reverse=True)
    return artifacts


@dataclass
class Artifacts:
    role: str
    bucket: str
    ws: Path
    cs: CloudStorage

    def publish(self, path: str, title: str) -> str:
        source = (self.ws / path).resolve()
        if not source.is_relative_to(self.ws.resolve()):
            raise ValueError(f"path must stay inside the workspace: {path}")
        if not source.exists():
            raise FileNotFoundError(f"{path} not found in workspace")
        prefix = f"{self.role}/{datetime.now(UTC).date().isoformat()}-{title}"
        if source.is_file():
            self.cs.upload_file(self.bucket, f"{prefix}/{source.name}", source)
            entry = source.name
        else:
            for file in source.rglob("*"):
                if file.is_file():
                    self.cs.upload_file(self.bucket, f"{prefix}/{file.relative_to(source)}", file)
            entry = "index.html" if (source / "index.html").exists() else ""
        url = share_url(self.bucket, f"{prefix}/{entry}".rstrip("/"))
        logger.info(f"published artifact: {url}")
        return url

    def mcp(self):
        return mcp.server("artifacts", self.tools())

    def tools(self) -> list:
        from claude_agent_sdk import tool

        @tool(
            "publish_artifact",
            "Publish a file or directory from the workspace to the shared team "
            "artifact store and return an authenticated URL that teammates open "
            "in a browser (HTML renders directly, relative assets work). Use a "
            "short kebab-case title. Published artifacts are immutable - they "
            "cannot be overwritten or deleted, so publish once when the "
            "deliverable is final.",
            {"path": str, "title": str},
        )
        async def publish_artifact(args):
            try:
                return mcp.result(self.publish(args["path"], args["title"]))
            except Exception as e:
                return mcp.result(f"Failed: {e}")

        return [publish_artifact]
