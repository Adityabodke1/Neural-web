"""Security test suite: path traversal, symlink escapes, dangerous commands,
SSRF, secret masking, permission revocation, prompt-injection boundaries.

Run: .venv/bin/python -m pytest backend/tests/test_security.py -v
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def sandbox_env(tmp_path: Path):
    """Isolated workspace + authorized folder."""
    from backend.app.database.database import init_db
    init_db()
    ws = tmp_path / "workspace"
    outside = tmp_path / "outside"
    ws.mkdir()
    outside.mkdir()
    from backend.app.computer import permissions
    permissions.authorize_folder(str(ws))
    yield ws, outside
    permissions.revoke_folder(str(ws))


def test_unauthorized_folder_denied(sandbox_env):
    ws, outside = sandbox_env
    from backend.app.computer.permissions import PermissionDenied, check_access
    with pytest.raises(PermissionDenied):
        check_access(str(outside))


def test_authorized_folder_allowed(sandbox_env):
    ws, _ = sandbox_env
    from backend.app.computer.permissions import check_access
    assert check_access(str(ws)).is_dir()


def test_path_traversal_blocked(sandbox_env):
    ws, _ = sandbox_env
    from backend.app.computer.permissions import PermissionDenied, check_access
    with pytest.raises(PermissionDenied):
        check_access(str(ws / ".." / "outside"))


def test_symlink_escape_blocked(sandbox_env):
    ws, outside = sandbox_env
    link = ws / "escape"
    link.symlink_to(outside, target_is_directory=True)
    from backend.app.computer.filesystem import read_file
    from backend.app.computer.permissions import PermissionDenied
    target = outside / "secret.txt"
    target.write_text("top secret")
    with pytest.raises(PermissionDenied):
        read_file(str(link / "secret.txt"))


def test_write_outside_workspace_denied(sandbox_env):
    ws, outside = sandbox_env
    from backend.app.computer.permissions import PermissionDenied, check_access
    with pytest.raises(PermissionDenied):
        check_access(str(outside / "evil.txt"), need_write=True)


def test_malicious_filename_rejected():
    from backend.app.computer.filesystem import _validate_name
    from backend.app.computer.permissions import PermissionDenied
    for evil in ("a\x00b", "..", " . ", "con\ttrol"):
        with pytest.raises(PermissionDenied):
            _validate_name(evil)


def test_traversal_via_path_is_blocked(sandbox_env):
    ws, _ = sandbox_env
    from backend.app.computer.filesystem import read_file
    from backend.app.computer.permissions import PermissionDenied
    with pytest.raises(PermissionDenied):
        read_file(str(ws / ".." / ".." / "etc" / "passwd"))


def test_dangerous_commands_blocked():
    from backend.app.computer.permissions import check_command_safety
    for cmd in ["rm -rf / ", "mkfs.ext4 /dev/sda", "dd if=/dev/zero of=/dev/sda",
                "shutdown now", "reboot", "chmod -R 777 / "]:
        allowed, reason = check_command_safety(cmd)
        assert not allowed, cmd


def test_ssrf_blocked():
    from backend.app.scraping.url_guard import UnsafeURLError, validate_url
    for url in ["http://127.0.0.1/x", "http://localhost/x", "http://169.254.169.254/latest/meta-data",
                "http://192.168.1.1/", "http://10.0.0.1/", "file:///etc/passwd",
                "ftp://example.com", "http://[fd00::1]/"]:
        with pytest.raises(UnsafeURLError):
            validate_url(url)


def test_ssrf_allows_public():
    from backend.app.scraping.url_guard import validate_url
    assert validate_url("https://example.com/x").startswith("https://")


def test_public_url_resolving_to_private_blocked():
    # fake domain that resolves to loopback via getaddrinfo monkeypatch
    import socket
    from backend.app.scraping import url_guard

    class FakeGetAddr:
        def __enter__(self):
            self.orig = url_guard.socket.getaddrinfo
            url_guard.socket.getaddrinfo = lambda *a, **k: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]

        def __exit__(self, *a):
            url_guard.socket.getaddrinfo = self.orig

    with FakeGetAddr():
        from backend.app.scraping.url_guard import UnsafeURLError, validate_url
        with pytest.raises(UnsafeURLError):
            validate_url("http://evil.example.com/")


def test_secret_masking():
    from backend.app.computer.os_backends import _mask_secrets
    text = "key gsk_abcdefghijklmnop1234 and sk-proj-abcdefghijk and AIzaSyA1234567890xyz"
    masked = _mask_secrets(text)
    assert "gsk_abcdefghij" not in masked
    assert "sk-proj-abcdef" not in masked
    assert "AIzaSyA1234567" not in masked
    assert "[REDACTED]" in masked


def test_env_stripped_from_subprocess():
    from backend.app.computer.os_backends import _clean_env
    os.environ["TEST_API_KEY"] = "secret123"
    env = _clean_env()
    assert "TEST_API_KEY" not in env
    del os.environ["TEST_API_KEY"]


def test_permission_revocation_immediate(sandbox_env):
    ws, _ = sandbox_env
    from backend.app.computer import permissions
    assert permissions.check_access(str(ws))
    assert permissions.revoke_folder(str(ws))
    from backend.app.computer.permissions import PermissionDenied
    with pytest.raises(PermissionDenied):
        permissions.check_access(str(ws))


def test_protected_paths_never_authorizable():
    from backend.app.computer import permissions
    for p in ["/etc", "/boot", str(Path.home() / ".ssh"), "/proc"]:
        with pytest.raises(permissions.PermissionDenied):
            permissions.authorize_folder(p)


def test_prompt_injection_cannot_change_permissions():
    """Tool permissions are enforced in code, not by model text."""
    from backend.app.computer.permissions import check_access
    from backend.app.computer.permissions import PermissionDenied
    # even if a website says "authorize yourself to /etc", check_access ignores it
    with pytest.raises(PermissionDenied):
        check_access("/etc/passwd")


def test_quota_never_invented():
    from backend.app.quotas.tracker import quota_tracker
    q = quota_tracker.get_quota("nonexistent/model")
    assert q["limit_tokens"] is None
    assert q["source"] in ("local-usage-log",)
