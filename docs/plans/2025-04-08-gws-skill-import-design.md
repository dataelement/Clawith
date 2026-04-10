# GWS Skill Import System Design

**Date**: 2025-04-08
**Status**: Design Approved
**Approach**: Reuse existing skill import patterns from skills.py

## Overview

Bulk-import Google Workspace CLI skills from GitHub (`googleworkspace/cli` repository) into the Clawith skill registry. The system imports skills at startup (as global builtins), provides manual re-import capability, and optionally seeds skills for new tenants.

## Architecture

### Components

1. **Service Module** (`backend/app/services/gws_skill_seeder.py`)
   - Core import logic
   - Dependency management for gws-shared

2. **Startup Integration** (`backend/app/main.py`)
   - Automatic seeding on server startup
   - Creates global builtin GWS skills

3. **API Endpoint** (`backend/app/api/gws.py`)
   - Manual re-import trigger
   - Admin-only access

4. **Tenant Hook** (`backend/app/api/tenants.py`)
   - Seeds GWS skills for new tenants
   - Optional customization path

## Data Flow

### Startup Import Flow

```
main.py (lifespan)
    │
    ├─> seed_builtin_tools()
    ├─> seed_agent_templates()
    ├─> seed_skills()
    │
    └─> import_gws_skills(tenant_id=None)
            │
            ├─> Fetch GitHub directory: googleworkspace/cli/skills
            ├─> Filter: gws-shared + gws-*
            ├─> Sort: gws-shared first, then alphabetical
            │
            └─> For each skill:
                    ├─> Fetch SKILL.md from GitHub
                    ├─> Parse YAML frontmatter
                    └─> _save_skill_to_db()
                            ├─> Check for conflicts
                            ├─> Create Skill record (is_builtin=True)
                            └─> Create SkillFile record
```

### Manual Re-import Flow

```
POST /api/gws/skills/import
    │
    ├─> Auth: require_role("org_admin")
    └─> import_gws_skills(current_user.tenant_id)
            │
            └─> [Same as startup, but tenant-scoped]
```

### Tenant Creation Flow

```
POST /api/tenants/self-create
    │
    ├─> Create Tenant
    ├─> Create User (org_admin)
    │
    └─> import_gws_skills(str(new_tenant.id))
            │
            └─> [Same as startup, but tenant-scoped]
```

## Implementation Details

### 1. Core Import Function

```python
async def import_gws_skills(tenant_id: str | None = None) -> int:
    """
    Import GWS skills from GitHub into the skill registry.
    
    Args:
        tenant_id: Optional tenant ID for tenant-scoped import.
                   If None, creates global builtin skills (visible to all tenants).
    
    Returns:
        Number of skills imported.
    
    Raises:
        HTTPException: On GitHub API errors (except 404/429 which are handled gracefully)
    """
```

**Steps**:
1. Get GitHub token from TenantSetting (if available)
2. Fetch directory listing from `https://api.github.com/repos/googleworkspace/cli/contents/skills`
3. Filter entries:
   - Include: `gws-shared` (must be first)
   - Include: `gws-*` (core skills only, exclude workflows/personas/recipes)
4. Sort: `gws-shared` first, then alphabetically
5. For each skill:
   - Fetch `SKILL.md` content
   - Parse YAML frontmatter (name, description)
   - Fallback: use folder_name as name if parsing fails
   - Save using `_save_skill_to_db()` pattern:
     - Check for conflicts by folder_name + tenant_id
     - Create Skill record: is_builtin=True, tenant_id (optional)
     - Create SkillFile record: path="SKILL.md", content
6. Return count of imported skills

### 2. GitHub API Integration

**Reuse existing patterns**:
- `_get_github_token(tenant_id)` from skills.py
- Rate limiting handling (429: log warning, continue)
- Error handling (404/403: log error, skip skill)

**New helper**:
```python
async def _fetch_gws_skill_content(
    skill_name: str, 
    token: str = ""
) -> str | None:
    """Fetch SKILL.md content for a specific GWS skill."""
    url = f"https://api.github.com/repos/googleworkspace/cli/contents/skills/{skill_name}/SKILL.md"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content
        elif resp.status_code == 404:
            logger.warning(f"SKILL.md not found for {skill_name}")
            return None
        else:
            logger.error(f"GitHub API error for {skill_name}: {resp.status_code}")
            return None
```

### 3. Skill Filtering

```python
def _filter_gws_skills(entries: list[dict]) -> list[str]:
    """
    Filter and sort GWS skill directory names.
    
    Priority:
    1. gws-shared (dependency for all other GWS skills)
    2. gws-* core skills (alphabetical)
    
    Excluded: gws-workflow-*, persona-*, recipe-*
    """
    skill_names = []
    
    for entry in entries:
        if entry["type"] != "dir":
            continue
        name = entry["name"]
        
        # Include gws-shared
        if name == "gws-shared":
            continue  # Will be added first separately
        
        # Include gws-* core skills (exclude workflows/personas/recipes)
        if (name.startswith("gws-") and 
            not name.startswith("gws-workflow-") and
            not name.startswith("persona-") and
            not name.startswith("recipe-")):
            skill_names.append(name)
    
    # Sort: gws-shared first, then alphabetical
    skill_names.sort()
    if any(e["name"] == "gws-shared" for e in entries):
        skill_names.insert(0, "gws-shared")
    
    return skill_names
```

### 4. Database Save Pattern

**Reuse `_save_skill_to_db()` logic** (adapted inline):

```python
async def _save_gws_skill(
    folder_name: str,
    skill_md_content: str,
    tenant_id: str | None = None,
) -> bool:
    """
    Save a GWS skill to the database.
    
    Returns:
        True if saved successfully, False if skipped (missing SKILL.md or error)
    """
    import uuid
    from app.models.skill import Skill, SkillFile
    from app.api.skills import _parse_skill_md_frontmatter
    from app.database import async_session
    
    # Parse frontmatter
    frontmatter = _parse_skill_md_frontmatter(skill_md_content)
    name = frontmatter.get("name", folder_name)
    description = frontmatter.get("description", "")
    
    async with async_session() as db:
        # Check for conflict
        conflict_q = select(Skill).where(Skill.folder_name == folder_name)
        if tenant_id:
            conflict_q = conflict_q.where(Skill.tenant_id == uuid.UUID(tenant_id))
        else:
            conflict_q = conflict_q.where(Skill.tenant_id.is_(None))
        
        existing = await db.execute(conflict_q)
        if existing.scalar_one_or_none():
            logger.info(f"Skill {folder_name} already exists, skipping")
            return False
        
        # Create Skill
        skill = Skill(
            name=name,
            description=description,
            category="gws",
            icon="",
            folder_name=folder_name,
            is_builtin=True,
            tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
        )
        db.add(skill)
        await db.flush()
        
        # Create SkillFile
        db.add(SkillFile(
            skill_id=skill.id,
            path="SKILL.md",
            content=skill_md_content.replace("\x00", ""),  # Remove null bytes
        ))
        
        await db.commit()
        logger.info(f"Imported GWS skill: {name}")
        return True
```

### 5. Dependency Management

```python
async def ensure_gws_shared_for_agent(agent_id: str, tenant_id: str | None = None):
    """
    Ensure gws-shared skill is installed when any gws-* skill is assigned.
    
    Called during skill assignment (separate task).
    """
    # Implementation will check if agent has any gws-* skills
    # If yes, ensure gws-shared is also in the agent's skills list
    # This will be implemented as part of the skill assignment flow
    pass
```

## Error Handling

| Error Type | Handling |
|------------|----------|
| GitHub 429 (rate limit) | Log warning, return partial count |
| GitHub 404 (not found) | Log warning, skip skill |
| GitHub 403 (forbidden) | Log error, skip skill |
| Database conflict (409) | Log info, skip skill (already exists) |
| Missing SKILL.md | Log warning, skip skill |
| Invalid frontmatter | Use folder_name as name, empty description |

## Testing Strategy

1. **Unit Tests**:
   - `_filter_gws_skills()` filtering logic
   - `_parse_skill_md_frontmatter()` with valid/invalid content
   - Conflict detection

2. **Integration Tests**:
   - Mock GitHub API responses
   - Test import with and without GitHub token
   - Test tenant-scoped vs global import

3. **Manual Testing**:
   - Startup import verification
   - Manual re-import endpoint
   - Tenant creation flow

## Security Considerations

- **GitHub Token**: Stored in TenantSetting, retrieved at runtime
- **API Access**: Manual re-import requires `org_admin` role
- **Rate Limiting**: Graceful handling prevents service disruption
- **Input Validation**: Skill content sanitized (null bytes removed)

## Performance

- **Startup Impact**: Minimal - GitHub API calls are async, non-blocking
- **Database**: Batch inserts would be nice but not required (typically <20 skills)
- **Caching**: Not needed - skills are static, imported once

## Future Enhancements

1. **Incremental Updates**: Check GitHub commit SHA, only update changed skills
2. **Batch Import**: Import all skills in single transaction
3. **Versioning**: Track GWS skill versions from GitHub tags
4. **Selective Import**: Allow tenants to choose which GWS skills to import

## Dependencies

- **Existing**: `httpx`, `sqlalchemy`, `pydantic`, `loguru`
- **New**: None (reuses existing patterns)

## Rollout Plan

1. **Phase 1**: Core import function + startup integration
2. **Phase 2**: Manual re-import endpoint
3. **Phase 3**: Tenant creation hook
4. **Phase 4**: Dependency management (`ensure_gws_shared_for_agent`)

## Success Criteria

- ✅ GWS skills imported successfully at startup
- ✅ Skills visible in skill registry
- ✅ Manual re-import works without errors
- ✅ Tenant creation seeds GWS skills
- ✅ Rate limiting handled gracefully
- ✅ No duplicate skills created
