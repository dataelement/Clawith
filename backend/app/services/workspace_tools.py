"""Workspace deployment tools for the Software Engineer agent."""

import re
import uuid
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session
from app.models.workspace import WorkspaceBugReport, WorkspaceProject

logger = logging.getLogger(__name__)

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$")
RESERVED_SLUGS = {"_index", "api", "static", "health"}

settings = get_settings()


def validate_slug(slug: str) -> str | None:
    """Validate a workspace slug. Returns error message or None if valid."""
    if not slug:
        return "Slug cannot be empty."
    if slug in RESERVED_SLUGS:
        return f"Slug '{slug}' is reserved. Choose a different name."
    if not SLUG_PATTERN.match(slug):
        return (
            f"Invalid slug '{slug}'. Must be 2-50 characters, "
            "lowercase letters, numbers, and hyphens only. "
            "Cannot start or end with a hyphen."
        )
    return None


async def check_slug_available(slug: str) -> str | None:
    """Check if slug is available in DB. Returns error message or None."""
    async with async_session() as db:
        existing = await db.execute(
            select(WorkspaceProject).where(WorkspaceProject.slug == slug)
        )
        if existing.scalar_one_or_none():
            return f"Slug '{slug}' is already in use."
    return None


import asyncio
import html as html_mod
import json
import shutil

from sqlalchemy.exc import IntegrityError

from app.services.workspace_index import regenerate_index


def _get_docker_client():
    """Get a Docker client connected via the mounted socket. Lazy import to avoid import-time failure."""
    import docker
    return docker.DockerClient(base_url="unix:///var/run/docker.sock")


async def tool_request_build(
    agent_id: uuid.UUID, arguments: dict
) -> str:
    """Create a build request for the SE agent."""
    slug = arguments.get("slug", "").strip()
    name = arguments.get("name", "").strip()
    description = arguments.get("description", "").strip()

    if not name or not description:
        return "Error: 'name' and 'description' are required."

    slug_error = validate_slug(slug)
    if slug_error:
        return f"Error: {slug_error}"

    avail_error = await check_slug_available(slug)
    if avail_error:
        return f"Error: {avail_error}"

    try:
        async with async_session() as db:
            project = WorkspaceProject(
                slug=slug,
                name=name,
                description=description,
                requested_by=agent_id,
                status="requested",
            )
            db.add(project)
            await db.commit()
    except IntegrityError:
        return f"Error: Slug '{slug}' is already in use."

    return (
        f"Build request created!\n"
        f"- Slug: {slug}\n"
        f"- Name: {name}\n"
        f"- Description: {description}\n"
        f"The Software Engineer agent will pick this up."
    )


async def tool_request_build_human(
    arguments: dict,
) -> str:
    """Create a build request from a human (no agent_id)."""
    slug = arguments.get("slug", "").strip()
    name = arguments.get("name", "").strip()
    description = arguments.get("description", "").strip()
    requester = arguments.get("requester", "").strip() or "Frank"

    if not name or not description:
        return "Error: 'name' and 'description' are required."

    slug_error = validate_slug(slug)
    if slug_error:
        return f"Error: {slug_error}"

    avail_error = await check_slug_available(slug)
    if avail_error:
        return f"Error: {avail_error}"

    try:
        async with async_session() as db:
            project = WorkspaceProject(
                slug=slug,
                name=name,
                description=description,
                requested_by_human=requester,
                status="requested",
            )
            db.add(project)
            await db.commit()
    except IntegrityError:
        return f"Error: Slug '{slug}' is already in use."

    return f"Build request '{name}' created for slug '{slug}'."


async def tool_list_build_requests() -> str:
    """List pending build requests."""
    async with async_session() as db:
        result = await db.execute(
            select(WorkspaceProject)
            .where(WorkspaceProject.status == "requested")
            .order_by(WorkspaceProject.created_at.asc())
        )
        projects = result.scalars().all()

    if not projects:
        return "No pending build requests."

    lines = ["Pending build requests:\n"]
    for p in projects:
        requester = p.requested_by_human or f"Agent {p.requested_by}"
        lines.append(
            f"- [{p.slug}] {p.name}\n"
            f"  Requested by: {requester}\n"
            f"  Description: {p.description}\n"
        )
    return "\n".join(lines)


async def tool_deploy_static(
    agent_id: uuid.UUID, ws: Path, arguments: dict
) -> str:
    """Deploy static files from agent workspace to /srv/workspace/{slug}/."""
    slug = arguments.get("slug", "").strip()
    source_dir = arguments.get("source_dir", "").strip()

    if not slug or not source_dir:
        return "Error: 'slug' and 'source_dir' are required."

    slug_error = validate_slug(slug)
    if slug_error:
        return f"Error: {slug_error}"

    # Resolve source path within agent workspace
    source_path = (ws / source_dir).resolve()
    if not str(source_path).startswith(str(ws.resolve())):
        return "Error: source_dir must be within your workspace."
    if not source_path.is_dir():
        return f"Error: Directory '{source_dir}' not found in your workspace."
    if not (source_path / "index.html").exists():
        return f"Error: No index.html found in '{source_dir}'. Static sites must have an index.html."

    # Check/create project record
    async with async_session() as db:
        result = await db.execute(
            select(WorkspaceProject).where(WorkspaceProject.slug == slug)
        )
        project = result.scalar_one_or_none()

        if project and project.deploy_type == "container" and project.status == "deployed":
            return f"Error: Slug '{slug}' is used by a running container deployment. Undeploy it first."

        if not project:
            # Auto-create project record for direct deploys
            project = WorkspaceProject(
                slug=slug,
                name=slug.replace("-", " ").title(),
                description="Deployed via deploy_static",
                built_by=agent_id,
                deploy_type="static",
                status="building",
            )
            db.add(project)
            await db.flush()
        else:
            project.deploy_type = "static"
            project.built_by = agent_id
            project.status = "building"

        await db.commit()
        project_id = project.id

    # Copy files to workspace static dir
    dest_path = Path(settings.WORKSPACE_STATIC_DIR) / slug
    try:
        if dest_path.exists():
            shutil.rmtree(dest_path)
        shutil.copytree(source_path, dest_path)
    except Exception as e:
        async with async_session() as db:
            result = await db.execute(
                select(WorkspaceProject).where(WorkspaceProject.id == project_id)
            )
            project = result.scalar_one()
            project.status = "failed"
            await db.commit()
        return f"Error copying files: {e}"

    # Update status
    async with async_session() as db:
        result = await db.execute(
            select(WorkspaceProject).where(WorkspaceProject.id == project_id)
        )
        project = result.scalar_one()
        project.status = "deployed"
        await db.commit()

    # Regenerate index (non-blocking)
    try:
        await regenerate_index()
    except Exception:
        logger.exception("Index regeneration failed after deploy_static")

    return (
        f"Static site deployed successfully!\n"
        f"- URL: /workspace/{slug}/\n"
        f"- Files copied from: {source_dir}\n"
        f"The site is now live."
    )


async def tool_request_container_deploy(
    agent_id: uuid.UUID, ws: Path, arguments: dict
) -> str:
    """Submit a container deployment for approval."""
    slug = arguments.get("slug", "").strip()
    dockerfile_path = arguments.get("dockerfile_path", "").strip()
    port = arguments.get("port")
    name = arguments.get("name", "").strip()
    description = arguments.get("description", "").strip()
    resource_suggestion = arguments.get("resource_limits_suggestion", {})

    if not all([slug, dockerfile_path, port, name, description]):
        return "Error: slug, dockerfile_path, port, name, and description are all required."

    slug_error = validate_slug(slug)
    if slug_error:
        return f"Error: {slug_error}"

    # Verify Dockerfile exists
    dockerfile_full = (ws / dockerfile_path).resolve()
    if not str(dockerfile_full).startswith(str(ws.resolve())):
        return "Error: dockerfile_path must be within your workspace."
    if not dockerfile_full.exists():
        return f"Error: Dockerfile not found at '{dockerfile_path}'."

    # Check/create project record
    async with async_session() as db:
        result = await db.execute(
            select(WorkspaceProject).where(WorkspaceProject.slug == slug)
        )
        project = result.scalar_one_or_none()

        if project and project.status == "deployed":
            return f"Error: Slug '{slug}' is already deployed. Undeploy it first."
        if project and project.status == "awaiting_approval":
            return f"Error: Slug '{slug}' already has a pending approval request."

        if not project:
            project = WorkspaceProject(
                slug=slug,
                name=name,
                description=description,
                built_by=agent_id,
                deploy_type="container",
                status="awaiting_approval",
                container_port=port,
                resource_limits=resource_suggestion if resource_suggestion else None,
            )
            db.add(project)
        else:
            project.name = name
            project.description = description
            project.built_by = agent_id
            project.deploy_type = "container"
            project.status = "awaiting_approval"
            project.container_port = port
            project.resource_limits = resource_suggestion if resource_suggestion else None

        await db.commit()

    return (
        f"Container deployment request submitted for approval.\n"
        f"- Slug: {slug}\n"
        f"- Name: {name}\n"
        f"- Dockerfile: {dockerfile_path}\n"
        f"- Port: {port}\n"
        f"- Suggested limits: {json.dumps(resource_suggestion) if resource_suggestion else 'none'}\n"
        f"Frank will review and approve this deployment."
    )


async def tool_list_workspace_projects() -> str:
    """List all workspace projects."""
    async with async_session() as db:
        result = await db.execute(
            select(WorkspaceProject).order_by(WorkspaceProject.created_at.desc())
        )
        projects = result.scalars().all()

    if not projects:
        return "No workspace projects."

    lines = ["Workspace projects:\n"]
    for p in projects:
        url = f"/workspace/{p.slug}/" if p.status == "deployed" else "(not live)"
        lines.append(
            f"- [{p.slug}] {p.name} — {p.status} ({p.deploy_type})\n"
            f"  URL: {url}\n"
        )
    return "\n".join(lines)


async def tool_undeploy_project(arguments: dict) -> str:
    """Remove a deployed workspace project."""
    slug = arguments.get("slug", "").strip()
    if not slug:
        return "Error: 'slug' is required."

    async with async_session() as db:
        result = await db.execute(
            select(WorkspaceProject).where(WorkspaceProject.slug == slug)
        )
        project = result.scalar_one_or_none()
        if not project:
            return f"Error: Project '{slug}' not found."

        if project.deploy_type == "static":
            # Delete static files
            dest = Path(settings.WORKSPACE_STATIC_DIR) / slug
            if dest.exists():
                shutil.rmtree(dest)

        elif project.deploy_type == "container" and project.container_id:
            # Stop and remove container
            try:
                client = _get_docker_client()
                try:
                    container = client.containers.get(project.container_id)
                    container.stop(timeout=10)
                    container.remove()
                except Exception:
                    pass  # container already gone or not found
                client.close()
            except Exception as e:
                logger.exception("Failed to remove container for %s", slug)
                return f"Warning: Container removal failed ({e}), but project will be marked as undeployed."

            # Remove nginx conf
            conf_path = Path(settings.WORKSPACE_CONF_DIR) / f"{slug}.conf"
            if conf_path.exists():
                conf_path.unlink()

            # Reload gateway nginx
            await _reload_gateway()

        project.status = "undeployed"
        await db.commit()

    # Regenerate index
    try:
        await regenerate_index()
    except Exception:
        logger.exception("Index regeneration failed after undeploy")

    return f"Project '{slug}' has been undeployed."


async def tool_get_bug_reports(arguments: dict) -> str:
    """List bug reports, optionally filtered by status."""
    from sqlalchemy.orm import selectinload

    status_filter = arguments.get("status_filter", "open").strip()

    async with async_session() as db:
        query = (
            select(WorkspaceBugReport)
            .options(selectinload(WorkspaceBugReport.project))
            .join(WorkspaceProject)
        )
        if status_filter != "all":
            query = query.where(WorkspaceBugReport.status == status_filter)
        query = query.order_by(WorkspaceBugReport.created_at.desc())
        result = await db.execute(query)
        reports = result.scalars().all()

        if not reports:
            return f"No bug reports with status '{status_filter}'."

        lines = [f"Bug reports ({status_filter}):\n"]
        for r in reports:
            proj_slug = r.project.slug if r.project else "unknown"
            lines.append(
                f"- [{r.id}] Project: {proj_slug}\n"
                f"  Source: {r.source} | Status: {r.status}\n"
                f"  Description: {r.description[:200]}\n"
                f"  Created: {r.created_at}\n"
            )
    return "\n".join(lines)


async def tool_resolve_bug(arguments: dict) -> str:
    """Mark a bug report as fixed."""
    bug_id = arguments.get("bug_report_id", "").strip()
    if not bug_id:
        return "Error: 'bug_report_id' is required."

    try:
        bug_uuid = uuid.UUID(bug_id)
    except ValueError:
        return f"Error: Invalid UUID '{bug_id}'."

    async with async_session() as db:
        report = await db.get(WorkspaceBugReport, bug_uuid)
        if not report:
            return f"Error: Bug report '{bug_id}' not found."
        report.status = "fixed"
        await db.commit()

    return f"Bug report {bug_id} marked as fixed."


async def tool_report_workspace_bug(
    agent_id: uuid.UUID, arguments: dict
) -> str:
    """Report a bug on a workspace project (agent-initiated)."""
    slug = arguments.get("slug", "").strip()
    description = arguments.get("description", "").strip()

    if not slug or not description:
        return "Error: 'slug' and 'description' are required."

    async with async_session() as db:
        result = await db.execute(
            select(WorkspaceProject).where(
                WorkspaceProject.slug == slug,
                WorkspaceProject.status == "deployed",
            )
        )
        project = result.scalar_one_or_none()
        if not project:
            return f"Error: No deployed project found with slug '{slug}'."

        report = WorkspaceBugReport(
            project_id=project.id,
            source="user_report",
            description=description[:2000],
        )
        db.add(report)
        await db.commit()

    return f"Bug report created for project '{slug}'. The Software Engineer agent will investigate."


async def approve_container_deploy(slug: str, resource_limits: dict | None = None) -> dict:
    """Approve and execute a container deployment. Returns status dict."""
    async with async_session() as db:
        result = await db.execute(
            select(WorkspaceProject).where(WorkspaceProject.slug == slug)
        )
        project = result.scalar_one_or_none()
        if not project:
            return {"ok": False, "error": f"Project '{slug}' not found."}
        if project.status != "awaiting_approval":
            return {"ok": False, "error": f"Project '{slug}' is not awaiting approval (status: {project.status})."}

        # Override resource limits if provided
        if resource_limits:
            project.resource_limits = resource_limits

        project.status = "building"
        agent_id = project.built_by
        port = project.container_port
        limits = project.resource_limits or {}
        await db.commit()

    # Find the Dockerfile in the agent's workspace
    agent_ws = Path(settings.AGENT_DATA_DIR) / str(agent_id)
    # Search for Dockerfile in workspace subdirectories
    dockerfile_candidates = list(agent_ws.glob(f"workspace/{slug}/**/Dockerfile")) + \
                           list(agent_ws.glob(f"workspace/**/Dockerfile"))
    if not dockerfile_candidates:
        # Also check for any Dockerfile in the workspace
        dockerfile_candidates = list(agent_ws.glob("workspace/**/Dockerfile"))

    if not dockerfile_candidates:
        async with async_session() as db:
            proj = await db.get(WorkspaceProject, (await db.execute(
                select(WorkspaceProject.id).where(WorkspaceProject.slug == slug)
            )).scalar_one())
            proj.status = "failed"
            await db.commit()
        return {"ok": False, "error": "No Dockerfile found in agent workspace."}

    dockerfile_path = dockerfile_candidates[0]
    build_context = dockerfile_path.parent

    # Build and start container
    try:
        client = _get_docker_client()
        container_name = f"ws-{slug}"
        image_tag = f"ws-{slug}:latest"

        # Build the image
        logger.info("Building Docker image '%s' from %s", image_tag, build_context)
        image, build_logs = client.images.build(
            path=str(build_context),
            tag=image_tag,
            rm=True,
        )
        for log_line in build_logs:
            if "stream" in log_line:
                logger.debug("Build: %s", log_line["stream"].strip())

        # Stop/remove existing container if any
        try:
            old = client.containers.get(container_name)
            old.stop(timeout=10)
            old.remove()
        except Exception:
            pass

        # Prepare container kwargs
        container_kwargs = {
            "image": image_tag,
            "name": container_name,
            "hostname": container_name,
            "detach": True,
            "restart_policy": {"Name": "unless-stopped"},
            "network": "workspace",
        }

        # Apply resource limits
        if limits.get("memory"):
            container_kwargs["mem_limit"] = limits["memory"]
        if limits.get("cpus"):
            container_kwargs["nano_cpus"] = int(float(limits["cpus"]) * 1e9)

        # Start the container
        logger.info("Starting container '%s'", container_name)
        container = client.containers.run(**container_kwargs)

        client.close()
    except Exception as e:
        logger.exception("Failed to build/start container for '%s'", slug)
        async with async_session() as db:
            result = await db.execute(
                select(WorkspaceProject).where(WorkspaceProject.slug == slug)
            )
            proj = result.scalar_one()
            proj.status = "failed"
            await db.commit()
        return {"ok": False, "error": f"Docker build/start failed: {e}"}

    # Write nginx conf snippet
    conf_content = f"""location /workspace/{slug}/ {{
    proxy_pass http://{container_name}:{port}/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}}
"""
    conf_path = Path(settings.WORKSPACE_CONF_DIR) / f"{slug}.conf"
    conf_path.write_text(conf_content, encoding="utf-8")
    logger.info("Wrote nginx conf for '%s'", slug)

    # Reload gateway
    await _reload_gateway()

    # Update project record
    async with async_session() as db:
        result = await db.execute(
            select(WorkspaceProject).where(WorkspaceProject.slug == slug)
        )
        proj = result.scalar_one()
        proj.status = "deployed"
        proj.container_id = container.id
        proj.container_image = image_tag
        await db.commit()

    # Regenerate index
    try:
        from app.services.workspace_index import regenerate_index
        await regenerate_index()
    except Exception:
        logger.exception("Index regeneration failed after container deploy")

    return {
        "ok": True,
        "slug": slug,
        "container_name": container_name,
        "url": f"/workspace/{slug}/",
    }


async def reject_container_deploy(slug: str) -> dict:
    """Reject a container deployment request."""
    async with async_session() as db:
        result = await db.execute(
            select(WorkspaceProject).where(WorkspaceProject.slug == slug)
        )
        project = result.scalar_one_or_none()
        if not project:
            return {"ok": False, "error": f"Project '{slug}' not found."}
        if project.status != "awaiting_approval":
            return {"ok": False, "error": f"Project '{slug}' is not awaiting approval (status: {project.status})."}
        project.status = "rejected"
        await db.commit()
    return {"ok": True, "slug": slug, "status": "rejected"}


async def _reload_gateway() -> None:
    """Reload the workspace gateway nginx config."""
    try:
        client = _get_docker_client()
        container = client.containers.get(settings.WORKSPACE_GATEWAY_CONTAINER)
        container.exec_run("nginx -s reload")
        client.close()
        logger.info("Workspace gateway nginx reloaded")
    except Exception:
        logger.exception("Failed to reload workspace gateway nginx")
