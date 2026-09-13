from collections.abc import Mapping
from typing import Any

from dify_plugin.interfaces.datasource import DatasourceProvider

from utils.ssh_vendor import load_private_key


class GitDatasourceProvider(DatasourceProvider):
    """Validates provider-level credentials for the Git datasource.

    The repository URL is a per-datasource-node parameter, not a
    credential, so at this point we don't know which repo will be
    accessed yet - the only thing we can validate here is that the
    supplied SSH key (if any) actually parses.
    """

    def _validate_credentials(self, credentials: Mapping[str, Any]) -> None:
        auth_mode = credentials.get("auth_mode", "none")
        if auth_mode not in ("none", "ssh_key"):
            raise ValueError(f"Unsupported auth_mode: {auth_mode}")

        if auth_mode == "ssh_key":
            private_key = credentials.get("ssh_private_key")
            if not private_key:
                raise ValueError("SSH private key is required when auth_mode is 'ssh_key'.")
            passphrase = credentials.get("ssh_key_passphrase") or None
            try:
                load_private_key(private_key, passphrase)
            except Exception as e:
                raise ValueError(f"Invalid SSH private key or passphrase: {e}") from e
