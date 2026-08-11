def unsafe_relpath(path: str) -> bool:
    """True when a path is not a clean relative path: absolute, empty
    segments, or .. traversal. The single gate for user- and agent-supplied
    file paths persisted by the control plane."""
    segments = path.split("/")
    return ".." in segments or "" in segments
