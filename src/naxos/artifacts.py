import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from naxos import mcp
from naxos.gcs import CloudStorage

logger = logging.getLogger(__name__)


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
        url = f"https://storage.cloud.google.com/{self.bucket}/{prefix}/{entry}".rstrip("/")
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
