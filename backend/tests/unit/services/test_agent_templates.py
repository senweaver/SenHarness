"""Unit tests for the built-in agent template loader (no DB)."""

from __future__ import annotations

from app.agents.templates import catalog
from app.services.agent_templates import loader


class TestSplitFrontmatter:
    def test_no_frontmatter_returns_passthrough(self):
        text = "# hello\n\nworld\n"
        fm, body = loader.split_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_simple_frontmatter(self):
        text = "---\nname: Foo\ndescription: a thing\ncolor: cyan\n---\n\nbody here\n"
        fm, body = loader.split_frontmatter(text)
        assert fm == {"name": "Foo", "description": "a thing", "color": "cyan"}
        assert body == "body here\n"

    def test_quoted_values_are_stripped(self):
        text = '---\nname: "Quoted Name"\ncolor: "#D97706"\n---\n\nbody'
        fm, _ = loader.split_frontmatter(text)
        assert fm["name"] == "Quoted Name"
        assert fm["color"] == "#D97706"

    def test_missing_terminator_returns_empty(self):
        text = "---\nname: Foo\nno terminator here"
        fm, body = loader.split_frontmatter(text)
        assert fm == {}
        assert body == text


class TestVendoredTemplates:
    """Lock down the 211 vendored templates' invariants."""

    def test_211_templates_present(self):
        files = loader.iter_template_files()
        assert len(files) == 211, f"expected 211 vendored templates, got {len(files)}"

    def test_every_template_parses(self):
        bad: list[str] = []
        for f in loader.iter_template_files():
            parsed = loader.parse_template(f)
            if parsed is None:
                bad.append(str(f))
        assert not bad, f"templates failed to parse: {bad[:5]}"

    def test_every_category_exists_in_catalog(self):
        seen: set[str] = set()
        for f in loader.iter_template_files():
            parsed = loader.parse_template(f)
            assert parsed is not None
            seen.add(parsed["category"])
        assert seen <= set(catalog.CATEGORY_BY_SLUG), (
            f"templates reference uncatalogued categories: {seen - set(catalog.CATEGORY_BY_SLUG)}"
        )

    def test_descriptions_within_db_limit(self):
        for f in loader.iter_template_files():
            parsed = loader.parse_template(f)
            assert parsed is not None
            desc = parsed["description"]
            if desc is not None:
                assert len(desc) <= 512


class TestCatalog:
    def test_seventeen_categories(self):
        assert len(catalog.CATEGORIES) == 17

    def test_get_tags_uses_curated_first(self):
        tags = catalog.get_tags("engineering-frontend-developer", "engineering")
        assert "react" in tags  # curated includes more than the slug split

    def test_get_tags_falls_back_to_derive(self):
        tags = catalog.get_tags("design-some-fictional-agent", "design")
        assert tags == ["some", "fictional"]  # "agent" is a stopword

    def test_derive_drops_category_prefix(self):
        tags = catalog.derive_tags("engineering-rapid-prototyper", "engineering")
        assert "engineering" not in tags
        assert "rapid" in tags

    def test_derive_caps_at_max_tags(self):
        tags = catalog.derive_tags("a-b-c-d-e-f-g-h-i-j", "")
        assert len(tags) <= 5


class TestCloneStripsTemplateKeys:
    """Cloning a template should not produce more rows the loader sees."""

    def test_template_keys_are_filtered(self):
        from app.services.agent import _TEMPLATE_OWNED_KEYS

        src_meta = {
            "template": True,
            "template_slug": "engineering-frontend-developer",
            "category": "engineering",
            "tags": ["frontend"],
            "color": "cyan",
            "code_mode": True,
            "approvals": {"strict": False},
        }
        cloned = {k: v for k, v in src_meta.items() if k not in _TEMPLATE_OWNED_KEYS}
        assert "template" not in cloned
        assert "template_slug" not in cloned
        assert "code_mode" in cloned  # user-set keys preserved
        assert "approvals" in cloned
