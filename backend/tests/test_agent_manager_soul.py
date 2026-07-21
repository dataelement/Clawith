def test_agent_soul_template_never_receives_role_description_metadata():
    from app.services.agent_manager import _render_soul_template

    rendered = _render_soul_template(
        """# Soul — {{agent_name}}

## Identity
- Name: {{agent_name}}
- Role: {{role_description}}
- Creator: {{creator_name}}
- Created: {{created_at}}
""",
        agent_name="Evidence Agent",
        creator_name="Ray",
        created_at="2026-07-16",
    )

    assert "Evidence Agent" in rendered
    assert "Ray" in rendered
    assert "2026-07-16" in rendered
    assert "role_description" not in rendered
    assert "- Role:" not in rendered


def test_demo_seed_does_not_copy_product_role_metadata_into_soul():
    from pathlib import Path

    seed_source = (Path(__file__).parents[1] / "seed.py").read_text(encoding="utf-8")

    copied_role_pattern = (
        'soul_path.write_text(f"# {agent.name}\\n\\n{agent.role_description}'
    )
    assert copied_role_pattern not in seed_source
    assert "_Describe your identity, responsibilities, and boundaries._" in seed_source


def test_agent_template_soul_uses_the_selected_agent_name_placeholder():
    from app.services.agent_manager import _render_soul_template

    rendered = _render_soul_template(
        "# Soul — {name}\n\n## Identity\nTemplate-owned identity",
        agent_name="Risk Partner",
        creator_name="Ray",
        created_at="2026-07-16",
    )

    assert rendered.startswith("# Soul — Risk Partner")
    assert "{name}" not in rendered
    assert "Template-owned identity" in rendered
