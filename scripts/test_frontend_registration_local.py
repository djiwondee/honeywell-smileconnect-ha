# Change log:
# - 2026-09-21: Initial version. Locks in the Lovelace-resource
#   registration added in 0.4.0 after a live load-order race made the card
#   fail on every page RELOAD (see __init__.py's change log). That logic
#   is fiddly - it has to be idempotent, has to update an existing entry
#   on a version bump instead of adding a second one, and has to cope with
#   hass.data["lovelace"] having been both a dict and a dataclass across
#   Home Assistant versions - and none of it is covered by tests/, which
#   only tests the HA-independent api/ layer.
"""Local regression test for the frontend/Lovelace resource registration.

Needs no gateway, no credentials, no network and no Home Assistant test
harness - it calls the real helpers from __init__.py against stand-in
resource collections. Same niche as scripts/test_scene_guards_local.py
and scripts/test_session_recovery_local.py:

    python3 scripts/test_frontend_registration_local.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.honeywell_smileconnect import (  # noqa: E402
    CARD_FILENAME,
    FRONTEND_URL_BASE,
    _async_register_lovelace_resource,
    _hashed_card_url,
    _lovelace_resources,
)
from homeassistant.components.lovelace.const import DOMAIN as LOVELACE_DOMAIN  # noqa: E402

RESULTS: list[str] = []
URL_V1 = _hashed_card_url("aaaaaaaa")
URL_V2 = _hashed_card_url("bbbbbbbb")
# The pre-0.4.0 shape, which an upgrade has to rewrite rather than leave
# behind as a second, dead entry.
URL_LEGACY = f"{FRONTEND_URL_BASE}/{CARD_FILENAME}?v=0.4.0"


def check(name: str, condition: bool) -> None:
    RESULTS.append(("PASS  " if condition else "FAIL  ") + name)


class StorageCollection:
    """Stand-in for Lovelace's ResourceStorageCollection (storage mode)."""

    def __init__(self, items=None):
        self.items = list(items or [])
        self.created = []
        self.updated = []
        self.info_calls = 0

    async def async_get_info(self):
        self.info_calls += 1
        return {"resources": len(self.items)}

    def async_items(self):
        return self.items

    async def async_create_item(self, data):
        self.created.append(data)
        self.items.append({"id": "new", **data})

    async def async_update_item(self, item_id, data):
        self.updated.append((item_id, data))
        for item in self.items:
            if item["id"] == item_id:
                item.update(data)


class YamlCollection:
    """Stand-in for ResourceYAMLCollection - read-only, no create/update."""

    def __init__(self, items=None):
        self.items = list(items or [])

    async def async_get_info(self):
        return {"resources": len(self.items)}

    def async_items(self):
        return self.items


class FakeHass:
    def __init__(self, data):
        self.data = data


def run(coro):
    return asyncio.run(coro)


def test_creates_when_absent() -> None:
    collection = StorageCollection()
    hass = FakeHass({LOVELACE_DOMAIN: {"resources": collection}})
    ok = run(_async_register_lovelace_resource(hass, URL_V1))
    check(
        "absent: creates exactly one module resource",
        ok
        and collection.created == [{"res_type": "module", "url": URL_V1}]
        and not collection.updated,
    )


def test_is_idempotent() -> None:
    collection = StorageCollection([{"id": "a", "url": URL_V1}])
    hass = FakeHass({LOVELACE_DOMAIN: {"resources": collection}})
    ok = run(_async_register_lovelace_resource(hass, URL_V1))
    check(
        "already present at the same version: does nothing",
        ok and not collection.created and not collection.updated,
    )


def test_updates_on_version_bump() -> None:
    collection = StorageCollection([{"id": "a", "url": URL_V1}])
    hass = FakeHass({LOVELACE_DOMAIN: {"resources": collection}})
    ok = run(_async_register_lovelace_resource(hass, URL_V2))
    check(
        "version bump: UPDATES the entry instead of adding a second one",
        ok
        and collection.updated == [("a", {"url": URL_V2})]
        and not collection.created
        and len(collection.items) == 1,
    )


def test_rewrites_the_legacy_query_string_url() -> None:
    collection = StorageCollection([{"id": "a", "url": URL_LEGACY}])
    hass = FakeHass({LOVELACE_DOMAIN: {"resources": collection}})
    ok = run(_async_register_lovelace_resource(hass, URL_V1))
    check(
        "legacy ?v= URL: rewritten in place, not duplicated",
        ok
        and collection.updated == [("a", {"url": URL_V1})]
        and not collection.created
        and len(collection.items) == 1,
    )


def test_leaves_foreign_resources_alone() -> None:
    other = {"id": "x", "url": "/local/some-other-card.js"}
    collection = StorageCollection([other])
    hass = FakeHass({LOVELACE_DOMAIN: {"resources": collection}})
    run(_async_register_lovelace_resource(hass, URL_V1))
    check(
        "never touches another integration's resources",
        not collection.updated and other in collection.items,
    )


def test_dataclass_shaped_lovelace_data() -> None:
    class LovelaceData:
        def __init__(self, resources):
            self.resources = resources

    collection = StorageCollection()
    hass = FakeHass({LOVELACE_DOMAIN: LovelaceData(collection)})
    ok = run(_async_register_lovelace_resource(hass, URL_V1))
    check("hass.data['lovelace'] as a dataclass is handled", ok and collection.created)


def test_yaml_mode_declines() -> None:
    hass = FakeHass({LOVELACE_DOMAIN: {"resources": YamlCollection()}})
    check(
        "YAML mode: declines (read-only), caller falls back to extra_module_url",
        _lovelace_resources(hass) is None
        and run(_async_register_lovelace_resource(hass, URL_V1)) is False,
    )


def test_missing_lovelace_declines() -> None:
    check(
        "lovelace not set up: declines instead of raising",
        run(_async_register_lovelace_resource(FakeHass({}), URL_V1)) is False,
    )


def main() -> int:
    for test in (
        test_creates_when_absent,
        test_is_idempotent,
        test_updates_on_version_bump,
        test_rewrites_the_legacy_query_string_url,
        test_leaves_foreign_resources_alone,
        test_dataclass_shaped_lovelace_data,
        test_yaml_mode_declines,
        test_missing_lovelace_declines,
    ):
        test()

    print("\n".join(RESULTS))
    failures = [line for line in RESULTS if line.startswith("FAIL")]
    print(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
