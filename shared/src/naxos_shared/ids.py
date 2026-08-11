import hashlib
import json
import secrets

PREFIXES = {
    "agent": "agent",
    "environment": "env",
    "session": "sess",
    "deployment": "depl",
    "deployment_run": "drun",
    "vault": "vlt",
    "credential": "cred",
    "memory_store": "memstore",
    "memory": "mem",
    "artifact": "art",
    "skill": "skill",
    "skill_file": "skf",
    "confirmation": "conf",
    "run": "run",
}


def new_id(kind: str) -> str:
    return f"{PREFIXES[kind]}_{secrets.token_hex(12)}"


def call_hash(tool_name: str, tool_input: dict) -> str:
    """Identity of a tool call across sandbox restarts.

    tool_use_id is regenerated when the SDK replays a pending call after resume,
    so approval decisions are keyed on the call's content instead.
    """
    canonical = json.dumps(
        tool_input, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(f"{tool_name}\n{canonical}".encode()).hexdigest()
