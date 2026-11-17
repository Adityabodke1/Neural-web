"""Sandbox GitHub-passthrough tests: the coding agent must be able to
init/commit/push to GitHub with the host's `gh auth login`, like Claude Code.

Run: .venv/bin/python -m pytest backend/tests/test_sandbox_github.py -v
"""
from __future__ import annotations

import subprocess
import tempfile
import time

from backend.app.computer import permissions, sandbox
from backend.app.computer.sandbox import _github_support


def test_git_commands_get_identity_and_helper():
    env, binds = _github_support("git init && git commit -m x")
    assert env.get("GIT_CONFIG_COUNT")
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
             for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pairs["user.name"]                    # commit must never fail on identity
    assert "@" in pairs["user.email"]
    # helper only when the host has a gh login
    from backend.app.computer.sandbox import _token_file_path
    if sandbox._ensure_host_token_file():
        assert _token_file_path().split("/")[-1] in pairs["credential.helper"] \
            or "token" in pairs["credential.helper"]


def test_non_git_commands_get_nothing():
    env, binds = _github_support("python3 scraper.py && echo done")
    assert env == {} and binds == []


def test_token_never_in_argv_or_env():
    """The token is a 0600 FILE bound read-only into the sandbox — it must
    never appear in argv (visible in `ps`) nor in the environment."""
    if not sandbox._ensure_host_token_file():
        return  # host has no gh login — skip
    ws = tempfile.mkdtemp(prefix="nw_sbx_")
    permissions.authorize_folder(ws)
    res = sandbox.get_sandbox().prepare("git push origin main", ws, need_network=True)
    blob = " ".join(res.argv) + " " + " ".join(f"{k}={v}" for k, v in res.env.items())
    assert "gho_" not in blob and "ghp_" not in blob and "github_pat_" not in blob


def test_bwrap_setenv_delivers_env_to_child():
    """bwrap --clearenv wipes env; --setenv must carry GIT_CONFIG through."""
    if not getattr(sandbox.get_sandbox(), "bwrap", None):
        return  # no bwrap on this machine — skip
    ws = tempfile.mkdtemp(prefix="nw_sbx_")
    permissions.authorize_folder(ws)
    res = sandbox.get_sandbox().prepare(
        "echo $GIT_CONFIG_COUNT && echo $HOME && gh --version | head -1",
        ws, need_network=True)
    p = subprocess.run(res.argv, capture_output=True, text=True,
                       env=res.env, timeout=30)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip().splitlines()[0].isdigit()      # GIT_CONFIG_COUNT set
    assert ws in p.stdout                                   # HOME = workspace


def test_real_authenticated_push_inside_sandbox():
    """End-to-end: git init + commit + authenticated push to github.com."""
    if not sandbox._ensure_host_token_file():
        return  # host has no gh login — skip
    ws = tempfile.mkdtemp(prefix="nw_push_")
    permissions.authorize_folder(ws)
    repo_name = f"nw-agent-test-{int(time.time()) % 100000}"
    res = sandbox.get_sandbox().prepare(
        f"git init -q -b main && echo test > f.txt && git add . && "
        f"git commit -qm t && gh repo create {repo_name} --public "
        f"--source . --remote origin --push", ws, need_network=True)
    p = subprocess.run(res.argv, capture_output=True, text=True,
                       env=res.env, timeout=90)
    assert p.returncode == 0, p.stderr or p.stdout
    assert repo_name in p.stdout
