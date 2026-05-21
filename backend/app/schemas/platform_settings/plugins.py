"""Plugin loader gate (M3.5 / M3.9).

The plugin loader honours every field here:

* ``allow_user_plugins`` is the master switch (default-deny per
  design principle 7). When ``False`` the loader never reads the
  ``STORAGE_LOCAL_PATH/plugins/`` directory and writes one
  ``plugin.disabled_by_platform_setting`` audit row per startup so
  operators see why nothing loaded.
* ``allow_unapproved_plugins`` is the dev-mode escape: with this
  flag on the loader skips the ed25519 signature check and the
  registry approval gate. Production deployments must keep it off.
* ``signing_root_pubkey`` is the platform-wide ed25519 verify key
  (base64 of the 32 raw bytes). Plugins ship a ``plugin.yaml.sig``
  file containing the base64 signature of the folder ``sha256``;
  the loader verifies that signature with this key. Set this once
  per deployment; the matching private key never lives on the
  server.
* ``auto_reload_on_admin_approve`` controls whether approving a
  registry row from the admin console immediately re-runs the
  loader (default ``True``). Operators that prefer a cold restart
  flip it to ``False`` so the load happens at the next deploy.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PluginsSettings(BaseModel):
    allow_user_plugins: bool = False
    allow_unapproved_plugins: bool = False
    signing_root_pubkey: str | None = Field(default=None, max_length=4096)
    auto_reload_on_admin_approve: bool = True
