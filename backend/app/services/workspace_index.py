"""Generates the workspace index page at /workspace/."""

import html
import logging
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.database import async_session
from app.models.workspace import WorkspaceProject

logger = logging.getLogger(__name__)

settings = get_settings()

STATUS_COLORS = {
    "deployed": "#22c55e",
    "building": "#f59e0b",
    "awaiting_approval": "#3b82f6",
    "failed": "#ef4444",
    "stopped": "#6b7280",
}


async def regenerate_index() -> None:
    """Regenerate the workspace index HTML. Non-blocking — logs errors but does not raise."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(WorkspaceProject)
                .where(WorkspaceProject.status == "deployed")
                .order_by(WorkspaceProject.created_at.desc())
            )
            projects = result.scalars().all()

        index_dir = Path(settings.WORKSPACE_STATIC_DIR) / "_index"
        index_dir.mkdir(parents=True, exist_ok=True)

        project_cards = ""
        for p in projects:
            badge_color = STATUS_COLORS.get(p.status, "#6b7280")
            project_cards += f"""
            <div class="card">
                <div class="card-header">
                    <a href="/workspace/{p.slug}/" class="card-title">{html.escape(p.name)}</a>
                    <span class="badge" style="background:{badge_color}">{p.status}</span>
                </div>
                <p class="card-desc">{html.escape(p.description or "No description")}</p>
                <div class="card-footer">
                    <span class="card-type">{p.deploy_type}</span>
                    <button class="report-btn" onclick="reportIssue('{p.slug}')">Report Issue</button>
                </div>
            </div>"""

        empty_msg = '<p class="empty">No projects deployed yet.</p>' if not projects else ""

        page_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Workspace — NLearn Consultant Company</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 900px; margin: 0 auto; padding: 2rem 1rem; color: #1a1a2e; background: #fafafa; }}
        h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; }}
        .subtitle {{ color: #666; margin-bottom: 2rem; }}
        .empty {{ color: #666; font-style: italic; margin-top: 2rem; }}
        .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 1.25rem; margin-bottom: 1rem; }}
        .card-header {{ display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; }}
        .card-title {{ font-size: 1.1rem; font-weight: 600; color: #1a1a2e; text-decoration: none; }}
        .card-title:hover {{ text-decoration: underline; }}
        .badge {{ font-size: 0.75rem; padding: 0.15rem 0.5rem; border-radius: 9999px; color: #fff; font-weight: 500; }}
        .card-desc {{ color: #555; font-size: 0.9rem; margin-bottom: 0.75rem; }}
        .card-footer {{ display: flex; justify-content: space-between; align-items: center; }}
        .card-type {{ font-size: 0.8rem; color: #888; text-transform: uppercase; }}
        .report-btn {{ background: none; border: 1px solid #d1d5db; border-radius: 4px; padding: 0.25rem 0.75rem; font-size: 0.8rem; cursor: pointer; color: #666; }}
        .report-btn:hover {{ background: #f3f4f6; }}
        .modal-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 100; }}
        .modal {{ position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%); background: #fff; border-radius: 8px; padding: 1.5rem; width: 90%; max-width: 480px; z-index: 101; }}
        .modal h2 {{ font-size: 1.1rem; margin-bottom: 1rem; }}
        .modal textarea {{ width: 100%; min-height: 100px; padding: 0.5rem; border: 1px solid #d1d5db; border-radius: 4px; font-family: inherit; resize: vertical; }}
        .modal-actions {{ display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 1rem; }}
        .modal-actions button {{ padding: 0.4rem 1rem; border-radius: 4px; border: 1px solid #d1d5db; cursor: pointer; }}
        .modal-actions .submit {{ background: #1a1a2e; color: #fff; border-color: #1a1a2e; }}
        .ohnohoney {{ position: absolute; left: -9999px; }}
        .msg {{ padding: 0.75rem; border-radius: 4px; margin-bottom: 1rem; display: none; }}
        .msg-ok {{ background: #dcfce7; color: #166534; }}
        .msg-err {{ background: #fee2e2; color: #991b1b; }}
    </style>
</head>
<body>
    <h1>NLearn Consultant Company — Workspace</h1>
    <p class="subtitle">Projects built by our AI team</p>
    <div id="msg" class="msg"></div>
    {empty_msg}
    {project_cards}

    <div id="modal-overlay" class="modal-overlay" onclick="closeModal()"></div>
    <div id="modal" class="modal" style="display:none">
        <h2>Report an Issue</h2>
        <form id="report-form" onsubmit="submitReport(event)">
            <input type="hidden" id="report-slug" value="">
            <textarea id="report-desc" placeholder="Describe the issue..." required></textarea>
            <input type="text" name="website" class="ohnohoney" tabindex="-1" autocomplete="off">
            <div class="modal-actions">
                <button type="button" onclick="closeModal()">Cancel</button>
                <button type="submit" class="submit">Submit</button>
            </div>
        </form>
    </div>

    <script>
        function reportIssue(slug) {{
            document.getElementById('report-slug').value = slug;
            document.getElementById('report-desc').value = '';
            document.getElementById('modal').style.display = 'block';
            document.getElementById('modal-overlay').style.display = 'block';
        }}
        function closeModal() {{
            document.getElementById('modal').style.display = 'none';
            document.getElementById('modal-overlay').style.display = 'none';
        }}
        async function submitReport(e) {{
            e.preventDefault();
            const slug = document.getElementById('report-slug').value;
            const desc = document.getElementById('report-desc').value;
            const honeypot = document.querySelector('.ohnohoney').value;
            closeModal();
            const msg = document.getElementById('msg');
            try {{
                const res = await fetch('/api/workspace/projects/' + slug + '/report-bug', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{description: desc, website: honeypot}})
                }});
                if (res.ok) {{
                    msg.textContent = 'Thank you! Your report has been submitted.';
                    msg.className = 'msg msg-ok';
                }} else {{
                    const data = await res.json();
                    msg.textContent = data.detail || 'Failed to submit report.';
                    msg.className = 'msg msg-err';
                }}
            }} catch {{
                msg.textContent = 'Network error. Please try again.';
                msg.className = 'msg msg-err';
            }}
            msg.style.display = 'block';
            setTimeout(() => {{ msg.style.display = 'none'; }}, 5000);
        }}
    </script>
</body>
</html>"""

        (index_dir / "index.html").write_text(page_html, encoding="utf-8")
        logger.info("Workspace index page regenerated with %d projects", len(projects))

    except Exception:
        logger.exception("Failed to regenerate workspace index page")


    # Note: uses stdlib html.escape() imported at top of file
