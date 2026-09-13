"""Pure-Python SSH transport for dulwich, backed by paramiko.

Adapted from dulwich's own reference vendor (``contrib/paramiko_vendor.py``,
Apache-2.0 OR GPL-2.0-or-later, Copyright (C) 2013 Aaron O'Mullan) - that
module is not shipped inside the installed ``dulwich`` package (it lives in
the project's top-level ``contrib/`` directory, which isn't part of the pip
distribution), so a trimmed-down copy lives here instead.

This is the reason the plugin depends on `dulwich` + `paramiko` at all: both
are pure-Python, so git access never needs a `git` or `ssh` binary to be
present in the plugin sandbox.

Difference from the upstream vendor: host key checking defaults to
trust-on-first-use (``AutoAddPolicy``) instead of strict verification,
because the plugin sandbox has no persistent `~/.ssh/known_hosts` a user
could pre-populate. This is a deliberate security trade-off for a
read-only deploy-key use case; see PRIVACY.md.
"""
from __future__ import annotations

import io
import re
from typing import BinaryIO, cast

import paramiko

_PEM_MARKER_RE = re.compile(
    r"-----BEGIN ([A-Z0-9 ]+ PRIVATE KEY)-----\s*(.*?)\s*-----END \1-----",
    re.DOTALL,
)


def _repair_flattened_pem(text: str) -> str:
    """Reconstruct a PEM/OpenSSH private key whose newlines were collapsed
    into spaces.

    Observed in practice: Dify's `secret-input` credential field renders as
    a single-line input, so pasting a multi-line private key into it turns
    every newline into a space, producing one long line like
    ``-----BEGIN OPENSSH PRIVATE KEY----- <base64...> -----END ... -----``.
    paramiko's parser requires real line breaks, so this puts them back.
    """
    match = _PEM_MARKER_RE.search(text)
    if not match:
        return text
    key_type, body = match.groups()
    compact_body = re.sub(r"\s+", "", body)
    if not compact_body:
        return text
    wrapped = "\n".join(compact_body[i : i + 70] for i in range(0, len(compact_body), 70))
    return f"-----BEGIN {key_type}-----\n{wrapped}\n-----END {key_type}-----\n"


class _ParamikoWrapper:
    def __init__(self, client: paramiko.SSHClient, channel: paramiko.Channel) -> None:
        self.client = client
        self.channel = channel
        self.channel.setblocking(True)

    @property
    def stderr(self) -> BinaryIO:
        return cast(BinaryIO, self.channel.makefile_stderr("rb"))

    def can_read(self) -> bool:
        return self.channel.recv_ready()

    def write(self, data: bytes) -> None:
        self.channel.sendall(data)

    def read(self, n: int | None = None) -> bytes:
        data = self.channel.recv(n or 4096)
        if not data:
            return b""
        if n and len(data) < n:
            return data + self.read(n - len(data))
        return data

    def close(self) -> None:
        # Closing only the channel leaves the underlying SSHClient/transport
        # (and its TCP connection) open indefinitely - dulwich only ever
        # calls close() on this wrapper, never touches `client` directly.
        self.channel.close()
        self.client.close()


class ParamikoSSHVendor:
    """Minimal dulwich SSHVendor implementation backed by paramiko."""

    def __init__(self, pkey: paramiko.PKey | None = None) -> None:
        self._pkey = pkey

    def run_command(
        self,
        host: str,
        command: bytes,
        username: str | None = None,
        port: int | None = None,
        **kwargs: object,
    ) -> _ParamikoWrapper:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host,
            port=port or 22,
            username=username or "git",
            pkey=self._pkey,
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )
        transport = client.get_transport()
        if transport is None:
            raise RuntimeError("SSH transport could not be established")
        channel = transport.open_session(timeout=15)
        try:
            channel.set_environment_variable(name="GIT_PROTOCOL", value="version=2")
        except Exception:
            # Not all servers accept env forwarding; git protocol v1 still works.
            pass
        channel.exec_command(command.decode("utf-8"))
        return _ParamikoWrapper(client, channel)


def _key_classes() -> tuple[type[paramiko.PKey], ...]:
    # PKey.from_private_key() is only usable on a concrete subclass (per its
    # own docstring), so the algorithm must be detected by trying each one.
    # DSSKey (DSA) was removed in newer paramiko releases, hence getattr.
    classes = [paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey]
    dss_key = getattr(paramiko, "DSSKey", None)
    if dss_key is not None:
        classes.append(dss_key)
    return tuple(classes)


def load_private_key(pem_text: str, passphrase: str | None) -> paramiko.PKey:
    """Parse a PEM/OpenSSH/PKCS8 private key, trying each supported algorithm.

    paramiko's parser is strict about surrounding whitespace/blank lines
    around the BEGIN/END markers - pasting a key into a form field commonly
    picks up a stray leading/trailing newline or space, which otherwise
    makes every key type fail to parse with a confusing error. Normalizing
    line endings and trimming outer whitespace here fixes that without
    touching the key material itself. If the newlines themselves were lost
    (e.g. a single-line credential field), the PEM structure is rebuilt too.
    """
    normalized = pem_text.strip().replace("\r\n", "\n").replace("\r", "\n")
    if normalized.count("\n") < 2:
        normalized = _repair_flattened_pem(normalized)
    last_error: Exception | None = None
    for key_cls in _key_classes():
        try:
            return key_cls.from_private_key(io.StringIO(normalized), password=passphrase or None)
        except paramiko.SSHException as e:
            last_error = e
            continue
    raise ValueError(f"Could not parse SSH private key with any supported algorithm: {last_error}")


def build_ssh_vendor(credentials: dict) -> ParamikoSSHVendor:
    auth_mode = credentials.get("auth_mode", "none")
    if auth_mode != "ssh_key":
        return ParamikoSSHVendor(pkey=None)
    pem = credentials.get("ssh_private_key")
    if not pem:
        raise ValueError("auth_mode is 'ssh_key' but no SSH private key is configured on the provider.")
    passphrase = credentials.get("ssh_key_passphrase") or None
    return ParamikoSSHVendor(pkey=load_private_key(pem, passphrase))
