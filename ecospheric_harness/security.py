"""Security hardening for subprocess execution and SSRF mitigation.

Provides subprocess environment sanitization, resource limits, output redaction,
and URL validation to prevent internal network access from model-emitted URLs.

Residual risk: ``subprocess.run`` with ``capture_output=True`` buffers all output
in memory before the ``max_output_bytes`` check is applied post-hoc. For true
output-size enforcement, use ``subprocess.Popen`` with pipe reads (future phase).
"""

from __future__ import annotations

import ipaddress
import os
import re
import resource
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Env vars that are safe to pass through to subprocesses.
# These are needed by GDAL/PROJ and basic system operation.
_ALLOWED_ENV_KEYS: frozenset[str] = frozenset({
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "GDAL_DATA",
    "PROJ_LIB",
    "PROJ_DATA",
    "PYTHONPATH",
})


@dataclass
class SubprocessLimits:
    """Resource limits for subprocess execution."""

    wall_clock_timeout: int = 300  # seconds
    max_output_bytes: int = 100 * 1024 * 1024  # 100 MB
    rlimit_as: int | None = None  # address space limit in bytes (RLIMIT_AS)
    rlimit_nproc: int | None = None  # max processes (RLIMIT_NPROC)
    gdal_cachemax: str = "256"  # GDAL_CACHEMAX in MB


@dataclass
class SanitizationResult:
    """Result of sanitizing subprocess output."""

    stdout: str
    stderr: str
    redactions: list[str] = field(default_factory=list)  # names of redacted patterns


class SubprocessHardener:
    """Hardens subprocess execution with resource limits, env stripping,
    and output sanitization."""

    # Patterns to redact from subprocess output.
    # Each tuple is (regex_pattern, replacement_string).
    _REDACT_PATTERNS: list[tuple[str, str]] = [
        # API keys / tokens
        (r'(?:Bearer\s+)[A-Za-z0-9\-_\.]+', 'Bearer [REDACTED]'),
        (
            r"""(?:api[_-]?key["']?\s*[:=]\s*["']?)[A-Za-z0-9\-_]{20,}""",
            'api_key=[REDACTED]',
        ),
        (
            r"""(?:token["']?\s*[:=]\s*["']?)[A-Za-z0-9\-_]{20,}""",
            'token=[REDACTED]',
        ),
        (
            r"""(?:secret["']?\s*[:=]\s*["']?)[A-Za-z0-9\-_]{20,}""",
            'secret=[REDACTED]',
        ),
        # Absolute home paths
        (rf'(?:{re.escape(str(Path.home()))})[/\S]*', '~[REDACTED]'),
    ]

    def __init__(self, limits: SubprocessLimits | None = None) -> None:
        self._limits = limits or SubprocessLimits()

    @property
    def limits(self) -> SubprocessLimits:
        """Return the configured resource limits."""
        return self._limits

    def build_env(self) -> dict[str, str]:
        """Build a minimal environment for subprocess execution.

        Strips API keys, tokens, secrets. Sets GDAL_CACHEMAX.
        Keeps: PATH, HOME (needed by GDAL/PROJ), LANG, LC_ALL, TZ,
               GDAL_DATA, PROJ_LIB, PROJ_DATA, PYTHONPATH.
        """
        env: dict[str, str] = {}
        for key in _ALLOWED_ENV_KEYS:
            value = os.environ.get(key)
            if value is not None:
                env[key] = value
        env["GDAL_CACHEMAX"] = self._limits.gdal_cachemax
        return env

    def sanitize_output(self, stdout: str, stderr: str) -> SanitizationResult:
        """Redact API keys, tokens, secrets, and home paths from output."""
        redactions: list[str] = []
        clean_stdout = stdout
        clean_stderr = stderr

        for pattern, replacement in self._REDACT_PATTERNS:
            if re.search(pattern, clean_stdout):
                redactions.append(pattern)
            clean_stdout = re.sub(pattern, replacement, clean_stdout)

            # Check stderr separately (use original pattern for name tracking)
            if re.search(pattern, stderr):
                if pattern not in redactions:
                    redactions.append(pattern)
            clean_stderr = re.sub(pattern, replacement, clean_stderr)

        return SanitizationResult(
            stdout=clean_stdout,
            stderr=clean_stderr,
            redactions=redactions,
        )

    def preexec_fn(self) -> Any:
        """Return a function suitable for subprocess preexec_fn.

        Sets RLIMIT_AS and RLIMIT_NPROC if configured.
        The returned closure captures only primitive values (no `self` references)
        so it can be pickled by subprocess fork.
        """
        rlimit_as = self._limits.rlimit_as
        rlimit_nproc = self._limits.rlimit_nproc

        def _apply_limits() -> None:
            if rlimit_as is not None:
                resource.setrlimit(resource.RLIMIT_AS, (rlimit_as, rlimit_as))
            if rlimit_nproc is not None:
                resource.setrlimit(resource.RLIMIT_NPROC, (rlimit_nproc, rlimit_nproc))

        return _apply_limits


def _is_internal_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address is internal/metadata/blocked.

    Returns True if the IP should be blocked (SSRF target).
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_reserved
    )


def is_ssrf_target(url: str) -> bool:
    """Check if a URL targets an internal or metadata IP.

    Blocks:
    - 127.0.0.0/8 (loopback)
    - 169.254.0.0/16 (link-local, includes cloud metadata 169.254.169.254)
    - 10.0.0.0/8 (RFC 1918)
    - 172.16.0.0/12 (RFC 1918)
    - 192.168.0.0/16 (RFC 1918)
    - 0.0.0.0/8 (unspecified)
    - ::1 (IPv6 loopback)
    - fe80::/10 (IPv6 link-local)
    - fc00::/7 (IPv6 unique-local)

    Extracts hostname from URL, resolves it, checks IP against blocked ranges.
    Returns True if the URL is an SSRF target (should be blocked).
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False

    # Try to parse as a literal IP address first.
    try:
        ip = ipaddress.ip_address(hostname)
        return _is_internal_ip(ip)
    except ValueError:
        # Not a literal IP — need to resolve via DNS.
        pass

    # Resolve hostname via DNS with a timeout.
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(5.0)
        try:
            addrinfos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            # DNS resolution failed — don't block (might be valid but unreachable).
            return False

        # Check all resolved IPs.
        for addrinfo in addrinfos:
            ip_str = addrinfo[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if _is_internal_ip(ip):
                return True
    finally:
        # Ensure timeout is restored even if an unexpected error occurs.
        socket.setdefaulttimeout(old_timeout)

    return False


def check_ssrf(url: str) -> None:
    """Raise ValueError if URL is an SSRF target."""
    if is_ssrf_target(url):
        raise ValueError(f"URL '{url}' targets a blocked internal/metadata address")
