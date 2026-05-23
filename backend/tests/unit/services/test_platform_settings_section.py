"""Every section exposes a Pydantic schema and a default value.

Cheap structural tests — every change to the section catalog should
keep this passing without any DB hits. The exact section count grows
over time (sections accumulate as new platform features ship); we
assert a floor instead of an equality so adding a new section doesn't
break CI.
"""

from __future__ import annotations

import pytest

from app.services.platform_settings import (
    DANGEROUS_FIELDS,
    EMAIL_NOTIFY_SECTIONS,
    SECTION_SCHEMAS,
    SECTION_TO_KEY,
    PlatformSettingsSection,
    list_sections,
    section_schema_json,
)


def test_section_count_at_least_baseline():
    assert len(list(PlatformSettingsSection)) >= 19
    assert len(list_sections()) >= 19


@pytest.mark.parametrize("section", list(PlatformSettingsSection))
def test_every_section_has_a_schema(section):
    assert section in SECTION_SCHEMAS, section
    schema = SECTION_SCHEMAS[section]
    instance = schema()
    assert instance.model_dump() is not None


@pytest.mark.parametrize("section", list(PlatformSettingsSection))
def test_every_section_has_a_persistence_key_or_is_composite(section):
    if section == PlatformSettingsSection.AUTH_REGISTRATION:
        # Composite — splits across four legacy keys.
        return
    assert section in SECTION_TO_KEY, section


@pytest.mark.parametrize("section", list(PlatformSettingsSection))
def test_section_schema_json_emits_jsonschema(section):
    payload = section_schema_json(section.value)
    assert "properties" in payload
    assert payload.get("type") == "object"


def test_dangerous_fields_subset_of_section_schema():
    for section, fields in DANGEROUS_FIELDS.items():
        schema = SECTION_SCHEMAS[section]
        model_fields = set(schema.model_fields.keys())
        unknown = fields - model_fields
        assert not unknown, (section, unknown)


def test_email_notify_sections_are_known():
    for section in EMAIL_NOTIFY_SECTIONS:
        assert section in SECTION_SCHEMAS
