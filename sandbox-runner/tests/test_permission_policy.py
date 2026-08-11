from naxos_shared.events import PermissionMode, PermissionPolicy


def _policy(*rules: tuple[str, str]) -> PermissionPolicy:
    return PermissionPolicy.model_validate(
        {"default": "always_ask", "rules": [{"tool": t, "mode": m} for t, m in rules]}
    )


def test_exact_match_and_default():
    policy = _policy(("Bash", "always_allow"))
    assert policy.mode_for("Bash") is PermissionMode.ALWAYS_ALLOW
    assert policy.mode_for("Read") is PermissionMode.ALWAYS_ASK


def test_star_matches_everything():
    policy = _policy(("*", "always_allow"))
    assert policy.mode_for("anything") is PermissionMode.ALWAYS_ALLOW


def test_glob_matches_tool_families():
    policy = _policy(("mcp__artifacts__*", "always_allow"))
    assert policy.mode_for("mcp__artifacts__artifact_create") is PermissionMode.ALWAYS_ALLOW
    assert policy.mode_for("mcp__github__create_issue") is PermissionMode.ALWAYS_ASK


def test_first_matching_rule_wins():
    policy = _policy(("mcp__artifacts__*", "always_ask"), ("*", "always_allow"))
    assert policy.mode_for("mcp__artifacts__artifact_share") is PermissionMode.ALWAYS_ASK
    assert policy.mode_for("Bash") is PermissionMode.ALWAYS_ALLOW
