"""Vault service — store / read / rotate secrets with envelope encryption."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.vault import VaultItem, VaultItemKind
from app.db.repository import AsyncRepository
from app.security.crypto import Sealed, open_sealed, seal_str
from app.security.keyring import get_keyring


class VaultKeyNotFoundError(LookupError):
    """Raised when a vault template references a non-existent key.

    Stable ``code`` so callers can map to i18n / 4xx codes without
    string-matching the message.
    """

    def __init__(self, key: str) -> None:
        super().__init__(f"vault key not found: {key!r}")
        self.code = "vault.key_not_found"
        self.key = key


# ``${vault://<scope>/<key>}`` — only ``workspace`` scope is supported in M0.6.
# The full match group is the scope, group 2 is the key. Cross-workspace
# references are rejected at resolve time so a leaked template can't escape
# the caller's tenant.
_VAULT_TEMPLATE_RE = re.compile(
    r"\$\{vault://(?P<scope>[a-z_][a-z0-9_]*)/(?P<key>[A-Za-z0-9_\-./]+)\}"
)


async def create_secret(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    owner_identity_id: uuid.UUID | None,
    name: str,
    plaintext: str,
    kind: VaultItemKind = VaultItemKind.API_KEY,
    metadata: dict[str, Any] | None = None,
    required_approval: bool = False,
) -> VaultItem:
    kr = get_keyring()
    sealed = seal_str(plaintext, keyring=kr)
    repo: AsyncRepository[VaultItem] = AsyncRepository(session, VaultItem)
    item = await repo.create(
        workspace_id=workspace_id,
        owner_identity_id=owner_identity_id,
        name=name,
        kind=kind,
        ciphertext=sealed.ciphertext,
        wrapped_dek=sealed.wrapped_dek,
        kek_version=sealed.kek_version,
        metadata_json=metadata or {},
        required_approval=required_approval,
    )
    return item


async def reveal_secret(item: VaultItem) -> str:
    kr = get_keyring()
    sealed = Sealed(
        ciphertext=item.ciphertext,
        wrapped_dek=item.wrapped_dek,
        kek_version=item.kek_version,
    )
    data = open_sealed(sealed, keyring=kr)
    return data.decode()


async def replace_secret(session: AsyncSession, *, item: VaultItem, plaintext: str) -> VaultItem:
    kr = get_keyring()
    sealed = seal_str(plaintext, keyring=kr)
    repo: AsyncRepository[VaultItem] = AsyncRepository(session, VaultItem)
    return await repo.update(
        item,
        ciphertext=sealed.ciphertext,
        wrapped_dek=sealed.wrapped_dek,
        kek_version=sealed.kek_version,
    )


async def _lookup_workspace_secret(
    session: AsyncSession, *, workspace_id: uuid.UUID, name: str
) -> VaultItem | None:
    stmt = (
        select(VaultItem)
        .where(VaultItem.workspace_id == workspace_id)
        .where(VaultItem.name == name)
        .where(VaultItem.deleted_at.is_(None))
        .order_by(VaultItem.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def reveal_workspace_secret(
    session: AsyncSession, *, workspace_id: uuid.UUID, name: str
) -> str:
    """Resolve a workspace-scoped vault entry to its plaintext value.

    Cross-workspace reads are impossible by construction — the lookup
    is keyed on ``(workspace_id, name)`` and missing rows raise
    :class:`VaultKeyNotFoundError` rather than returning an empty
    string, matching the substitution helper's fail-loud contract.
    """
    item = await _lookup_workspace_secret(session, workspace_id=workspace_id, name=name)
    if item is None:
        raise VaultKeyNotFoundError(name)
    return await reveal_secret(item)


async def resolve_vault_template(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    template: str,
) -> str:
    """Substitute every ``${vault://workspace/<key>}`` occurrence in ``template``.

    * Only ``workspace`` scope is permitted in M0.6 — anything else (e.g.
      ``${vault://platform/...}``) raises :class:`ValueError` so a leaked
      template cannot escape the calling workspace.
    * Lookup is by ``VaultItem.name`` filtered on ``workspace_id``; missing
      keys raise :class:`VaultKeyNotFoundError` rather than substituting an
      empty string (silent substitution would mask credential rotation
      mistakes).
    * Multiple templates per string supported; the function resolves each
      match individually so a single missing key fails the whole resolution.
    * Strings with no template markers pass through unchanged at zero cost.
    """
    if template is None or "${vault://" not in template:
        return template or ""

    cache: dict[tuple[str, str], str] = {}

    async def _resolve_one(scope: str, key: str) -> str:
        if scope != "workspace":
            raise ValueError(
                f"vault template scope {scope!r} is not allowed; "
                f"only 'workspace' is supported (got key={key!r})"
            )
        cached = cache.get((scope, key))
        if cached is not None:
            return cached
        item = await _lookup_workspace_secret(session, workspace_id=workspace_id, name=key)
        if item is None:
            raise VaultKeyNotFoundError(key)
        plaintext = await reveal_secret(item)
        cache[(scope, key)] = plaintext
        return plaintext

    # `re.sub` doesn't accept async substitutions; we walk matches manually.
    out: list[str] = []
    cursor = 0
    for match in _VAULT_TEMPLATE_RE.finditer(template):
        out.append(template[cursor : match.start()])
        out.append(await _resolve_one(match.group("scope"), match.group("key")))
        cursor = match.end()
    out.append(template[cursor:])
    return "".join(out)
