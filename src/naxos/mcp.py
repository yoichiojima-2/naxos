import json


def server(name: str, tools: list):
    from claude_agent_sdk import create_sdk_mcp_server

    return create_sdk_mcp_server(name=name, version="1.0.0", tools=tools)


def result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def dumps(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)
