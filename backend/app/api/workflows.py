"""Workflow API routes."""

import csv
import io
import json
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _can_access_workflow(wf: "Workflow | None", user: User) -> bool:
    """Check user can access workflow: owner + same tenant, or platform_admin."""
    if not wf:
        return False
    if user.role == "platform_admin":
        return True
    return wf.created_by == user.id and wf.tenant_id == user.tenant_id


class WorkflowChat(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


class WorkflowCreate(BaseModel):
    instruction: str = Field(min_length=2, max_length=5000)




class WorkflowUpdate(BaseModel):
    title: str | None = None

class WorkflowStepOut(BaseModel):
    id: uuid.UUID
    step_order: int
    title: str
    agent_name: str | None = None
    status: str
    deliverable_type: str
    deliverable_data: dict | None = None
    raw_output: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    model_config = {"from_attributes": True}


class WorkflowOut(BaseModel):
    id: uuid.UUID
    title: str
    user_instruction: str
    status: str
    summary: str | None = None
    next_steps: str | None = None
    steps: list[WorkflowStepOut] = []
    created_at: str
    completed_at: str | None = None

    model_config = {"from_attributes": True}


class WorkflowListItem(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    created_at: str
    completed_at: str | None = None

    model_config = {"from_attributes": True}


@router.post("/", status_code=201)
async def create_workflow(
    body: WorkflowCreate,
    current_user: User = Depends(get_current_user),
):
    from app.services.workflow_orchestrator import create_and_run_workflow
    wf_id = await create_and_run_workflow(
        instruction=body.instruction,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )
    return {"id": str(wf_id), "message": "Workflow started"}


@router.get("/")
async def list_workflows(
    page: int = 1,
    size: int = 20,
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_user),
):
    from app.services.workflow_orchestrator import list_workflows as _list
    effective_tenant = uuid.UUID(tenant_id) if tenant_id and current_user.role == "platform_admin" else current_user.tenant_id
    workflows, total = await _list(effective_tenant, current_user.id, page, size)
    return {
        "items": [
            {
                "id": str(w.id), "title": w.title, "status": w.status,
                "created_at": w.created_at.isoformat() if w.created_at else None,
                "completed_at": w.completed_at.isoformat() if w.completed_at else None,
            }
            for w in workflows
        ],
        "total": total, "page": page, "page_size": size,
    }


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    from app.services.workflow_orchestrator import get_workflow_detail
    wf = await get_workflow_detail(workflow_id)
    if not _can_access_workflow(wf, current_user):
        raise HTTPException(404, "Workflow not found")

    steps = sorted(wf.steps, key=lambda s: s.step_order)
    return {
        "id": str(wf.id), "title": wf.title, "user_instruction": wf.user_instruction,
        "status": wf.status, "summary": wf.summary, "next_steps": wf.next_steps,
        "created_at": wf.created_at.isoformat() if wf.created_at else None,
        "completed_at": wf.completed_at.isoformat() if wf.completed_at else None,
        "steps": [
            {
                "id": str(s.id), "step_order": s.step_order, "title": s.title,
                "agent_name": s.agent_name, "status": s.status,
                "deliverable_type": s.deliverable_type,
                "deliverable_data": s.deliverable_data,
                "raw_output": s.raw_output,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            }
            for s in steps
        ],
    }


@router.post("/{workflow_id}/steps/{step_id}/export")
async def export_step_csv(
    workflow_id: uuid.UUID,
    step_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    from app.services.workflow_orchestrator import get_workflow_detail
    wf = await get_workflow_detail(workflow_id)
    if not _can_access_workflow(wf, current_user):
        raise HTTPException(404, "Workflow not found")

    step = next((s for s in wf.steps if s.id == step_id), None)
    if not step or not step.raw_output:
        raise HTTPException(404, "Step not found or no output")

    # Parse markdown table to CSV
    lines = [l.strip() for l in step.raw_output.split("\n") if l.strip().startswith("|")]
    if len(lines) < 2:
        raise HTTPException(400, "No table found in output")

    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.match(r"^[-:]+$", c) for c in cells):
            continue
        rows.append(cells)

    output = io.StringIO()
    writer = csv.writer(output)
    for row in rows:
        writer.writerow(row)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=step-{step_id}.csv"},
    )




@router.put("/{workflow_id}")
async def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowUpdate,
    current_user: User = Depends(get_current_user),
):
    from app.database import async_session
    from app.models.workflow import Workflow
    from sqlalchemy import select

    async with async_session() as db:
        result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        wf = result.scalar_one_or_none()
        if not _can_access_workflow(wf, current_user):
            raise HTTPException(404, "Workflow not found")
        if body.title is not None:
            wf.title = body.title
        await db.commit()
    return {"message": "Workflow updated"}


@router.post("/{workflow_id}/retry")
async def retry_workflow(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """Retry all failed steps in a workflow."""
    import asyncio
    from app.database import async_session
    from app.models.workflow import Workflow, WorkflowStep
    from app.services.workflow_orchestrator import _execute_step, _summarize_workflow
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with async_session() as db:
        result = await db.execute(
            select(Workflow).where(Workflow.id == workflow_id).options(selectinload(Workflow.steps))
        )
        wf = result.scalar_one_or_none()
        if not _can_access_workflow(wf, current_user):
            raise HTTPException(404, "Workflow not found")

        failed_steps = [s for s in wf.steps if s.status == "failed"]
        if not failed_steps:
            return {"message": "No failed steps to retry", "retried": 0}

        # Reset failed steps
        for s in failed_steps:
            s.status = "pending"
            s.raw_output = None
            s.deliverable_data = None
        wf.status = "running"
        await db.commit()

    async def _retry_bg():
        from datetime import datetime, timezone
        async with async_session() as db:
            result = await db.execute(
                select(Workflow).where(Workflow.id == workflow_id).options(selectinload(Workflow.steps))
            )
            wf = result.scalar_one()
            steps = sorted(wf.steps, key=lambda s: s.step_order)

        prev_outputs = []
        for step in steps:
            if step.status == "done" and step.raw_output:
                prev_outputs.append(f"[{step.title}]\n{step.raw_output[:2000]}")
                continue
            if step.status != "pending":
                continue

            async with async_session() as db:
                result = await db.execute(select(WorkflowStep).where(WorkflowStep.id == step.id))
                s = result.scalar_one()
                s.status = "running"
                s.started_at = datetime.now(timezone.utc)
                await db.commit()

            try:
                output = await _execute_step(step, prev_outputs, wf.user_instruction)
                prev_outputs.append(f"[{step.title}]\n{output[:2000]}")
                async with async_session() as db:
                    result = await db.execute(select(WorkflowStep).where(WorkflowStep.id == step.id))
                    s = result.scalar_one()
                    s.status = "done"
                    s.raw_output = output
                    s.deliverable_data = {"content": output}
                    s.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                await asyncio.sleep(3)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Retry step {step.id} failed: {e}")
                async with async_session() as db:
                    result = await db.execute(select(WorkflowStep).where(WorkflowStep.id == step.id))
                    s = result.scalar_one()
                    s.status = "failed"
                    s.raw_output = f"Error: {str(e)[:500]}"
                    s.completed_at = datetime.now(timezone.utc)
                    await db.commit()

        await _summarize_workflow(workflow_id)

    asyncio.create_task(_retry_bg(), name=f"workflow-retry-{workflow_id}")
    return {"message": f"Retrying {len(failed_steps)} failed steps", "retried": len(failed_steps)}


@router.delete("/{workflow_id}")
async def delete_workflow(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    from app.database import async_session
    from app.models.workflow import Workflow, WorkflowStep
    from sqlalchemy import select, delete as sql_delete

    async with async_session() as db:
        result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        wf = result.scalar_one_or_none()
        if not _can_access_workflow(wf, current_user):
            raise HTTPException(404, "Workflow not found")
        await db.execute(sql_delete(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id))
        await db.execute(sql_delete(Workflow).where(Workflow.id == workflow_id))
        await db.commit()

    return {"message": "Workflow deleted"}



@router.post("/{workflow_id}/steps/{step_id}/import-to-crm")
async def import_step_to_crm(
    workflow_id: uuid.UUID,
    step_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """Parse a workflow step's table output and import contacts into CRM."""
    import re as _re
    from app.services.workflow_orchestrator import get_workflow_detail
    from app.models.crm import CRMContact

    wf = await get_workflow_detail(workflow_id)
    if not _can_access_workflow(wf, current_user):
        raise HTTPException(404, "Workflow not found")

    step = next((s for s in wf.steps if s.id == step_id), None)
    if not step or not step.raw_output:
        raise HTTPException(404, "Step not found or no output")

    raw = step.raw_output

    # Parse markdown table: find rows with enough columns that contain real company data
    table_lines = [l.strip() for l in raw.split("\n") if l.strip().startswith("|")]

    contacts = []
    for line in table_lines:
        cells = [c.strip().replace("**", "").strip() for c in line.strip("|").split("|")]

        # Skip separator rows
        if all(_re.match(r"^[-:]+$", c) for c in cells):
            continue
        # Skip header rows
        if any(kw in line.lower() for kw in ["公司名称", "company", "国家", "country", "优先级", "priority"]):
            continue
        # Skip sub-category headers (e.g. | **太阳能EPC** |)
        non_empty = [c for c in cells if c and c not in ("-", "")]
        if len(non_empty) <= 2:
            continue
        # Must have at least 5 columns to be a real data row
        if len(cells) < 5:
            continue
        # First cell might be a number, skip it to get company name
        idx = 0
        if cells[0].isdigit() or cells[0] in ("", "#"):
            idx = 1
        if idx >= len(cells):
            continue

        company = cells[idx].strip()
        # Skip if company name looks like a category or number
        if not company or len(company) < 3 or company.isdigit():
            continue
        if any(kw in company for kw in ["太阳能", "电信运营商", "矿业公司", "微型电网", "进口商", "分销商", "排名", "公司"]):
            continue

        # Extract fields by position after company
        country = cells[idx+1].strip() if idx+1 < len(cells) else ""
        contact_person = cells[idx+2].strip() if idx+2 < len(cells) else ""
        title = cells[idx+3].strip() if idx+3 < len(cells) else ""
        email = cells[idx+4].strip() if idx+4 < len(cells) else ""
        phone = cells[idx+5].strip() if idx+5 < len(cells) else ""

        # Clean empty/placeholder values
        for val_name in ["country", "contact_person", "title", "email", "phone"]:
            v = locals()[val_name]
            if v in ("-", "", "?", "N/A", "Management", "Sales", "Procurement", "Operations", "BD Team", "Sales Team", "Energy Team", "Sustainability"):
                locals()[val_name] = ""

        # Re-read cleaned values
        clean_email = email if email not in ("-", "", "?", "N/A") else ""
        clean_phone = phone if phone not in ("-", "", "?", "N/A") else ""
        clean_country = country if country not in ("-", "", "?") else ""
        clean_contact = contact_person if contact_person not in ("-", "", "?", "Management", "Sales", "Procurement", "Operations", "BD Team", "Sales Team", "Energy Team", "Sustainability", "CEO Office") else ""

        contacts.append({
            "company": company,
            "name": clean_contact or company,
            "country": clean_country,
            "email": clean_email if "@" in (clean_email or "") else None,
            "phone": clean_phone if clean_phone and len(clean_phone) > 5 else None,
            "title": title if title not in ("-", "", "?") else None,
        })

    if not contacts:
        raise HTTPException(400, "No valid contact data found in step output")

    from app.database import async_session
    from sqlalchemy import select
    imported = 0
    skipped = 0

    async with async_session() as db:
        for c in contacts:
            # Check duplicate by company name
            existing = await db.execute(
                select(CRMContact).where(
                    CRMContact.tenant_id == current_user.tenant_id,
                    CRMContact.company == c["company"],
                )
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            contact = CRMContact(
                tenant_id=current_user.tenant_id,
                name=c["name"],
                company=c["company"],
                email=c["email"],
                phone=c["phone"],
                country=c["country"],
                industry=step.title,
                source="workflow",
                tags=["auto-import", wf.title[:50]],
            )
            db.add(contact)
            imported += 1

        if imported > 0:
            await db.commit()

    return {"imported": imported, "skipped": skipped, "total": len(contacts)}



@router.post("/{workflow_id}/steps/{step_id}/export-pdf")
async def export_step_pdf(
    workflow_id: uuid.UUID,
    step_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """Export a workflow step's markdown output as a styled PDF."""
    from app.services.workflow_orchestrator import get_workflow_detail
    import subprocess, tempfile, os

    wf = await get_workflow_detail(workflow_id)
    if not _can_access_workflow(wf, current_user):
        raise HTTPException(404, "Workflow not found")

    step = next((s for s in wf.steps if s.id == step_id), None)
    if not step or not step.raw_output:
        raise HTTPException(404, "Step not found or no output")

    md_content = step.raw_output
    title = step.title or "Report"

    # Build HTML from markdown
    try:
        import markdown
        html_body = markdown.markdown(
            md_content,
            extensions=["tables", "fenced_code", "nl2br"],
        )
    except ImportError:
        # Fallback: basic conversion
        html_body = md_content.replace("\n", "<br>")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: 'Helvetica Neue', Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif; 
         max-width: 800px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; line-height: 1.7; font-size: 14px; }}
  h1 {{ color: #111; border-bottom: 2px solid #3b82f6; padding-bottom: 8px; font-size: 22px; }}
  h2 {{ color: #333; border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; font-size: 18px; margin-top: 24px; }}
  h3 {{ color: #555; font-size: 15px; margin-top: 20px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 12px; }}
  th {{ background: #f1f5f9; padding: 8px 10px; border: 1px solid #d1d5db; text-align: left; font-weight: 600; }}
  td {{ padding: 6px 10px; border: 1px solid #e5e7eb; }}
  tr:nth-child(even) {{ background: #f8fafc; }}
  code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 3px; font-size: 12px; }}
  pre {{ background: #f1f5f9; padding: 12px; border-radius: 6px; overflow-x: auto; }}
  .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid #e5e7eb; }}
  .header-title {{ font-size: 10px; color: #888; }}
  strong {{ color: #1a1a1a; }}
</style></head><body>
<div class="header">
  <div class="header-title">PulseAgent Workflow Report</div>
  <div class="header-title">{title}</div>
</div>
<h1>{title}</h1>
{html_body}
<div style="margin-top: 40px; padding-top: 12px; border-top: 1px solid #e5e7eb; font-size: 10px; color: #999;">
  Generated by PulseAgent AI Workflow Engine
</div>
</body></html>"""

    # Try weasyprint first, then wkhtmltopdf, then return HTML
    pdf_bytes = None

    # Method 1: weasyprint
    try:
        from weasyprint import HTML as WeasyHTML
        pdf_bytes = WeasyHTML(string=html).write_pdf()
    except ImportError:
        pass

    # Method 2: wkhtmltopdf
    if not pdf_bytes:
        try:
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
                f.write(html)
                html_path = f.name
            pdf_path = html_path.replace(".html", ".pdf")
            result = subprocess.run(
                ["wkhtmltopdf", "--quiet", "--encoding", "utf-8", "--page-size", "A4", html_path, pdf_path],
                capture_output=True, timeout=30,
            )
            if result.returncode == 0 and os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()
            os.unlink(html_path)
            if os.path.exists(pdf_path):
                os.unlink(pdf_path)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Method 3: Return HTML as downloadable file
    if not pdf_bytes:
        return StreamingResponse(
            iter([html.encode("utf-8")]),
            media_type="text/html",
            headers={"Content-Disposition": f"attachment; filename={title}.html"},
        )

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={title}.pdf"},
    )


@router.post("/{workflow_id}/chat")
async def workflow_chat(
    workflow_id: uuid.UUID,
    body: WorkflowChat,
    current_user: User = Depends(get_current_user),
):
    """Chat with AI in the context of a workflow — can dispatch agent actions."""
    from app.services.workflow_orchestrator import get_workflow_detail, call_llm_simple

    wf = await get_workflow_detail(workflow_id)
    if not _can_access_workflow(wf, current_user):
        raise HTTPException(404, "Workflow not found")

    # Build context from workflow state
    steps_ctx = ""
    for s in sorted(wf.steps, key=lambda x: x.step_order):
        status_icon = "✅" if s.status == "done" else "❌" if s.status == "failed" else "🔄" if s.status == "running" else "⏳"
        output_preview = (s.raw_output or "")[:600]
        steps_ctx += f"\n### {status_icon} 步骤{s.step_order + 1}: {s.title} ({s.agent_name})\n状态: {s.status}\n产出摘要: {output_preview}\n"

    system = f"""你是 PulseAgent 工作流助手。用户正在查看一个外贸工作流的执行结果，你需要基于工作流上下文回答问题、提供建议、或帮助用户采取下一步行动。

## 工作流信息
- 标题: {wf.title}
- 用户指令: {wf.user_instruction}
- 状态: {wf.status}
{f'- 汇总: {wf.summary}' if wf.summary else ''}
{f'- 建议下一步: {wf.next_steps}' if wf.next_steps else ''}

## 各步骤执行结果
{steps_ctx}

## 你的职责
1. 回答用户关于工作流结果的任何问题
2. 基于产出数据提供深入分析和建议
3. 如果用户要求采取行动（发邮件、发社媒、重试失败步骤等），告知具体操作方式
4. 用中文回答，简洁专业"""

    try:
        reply = await call_llm_simple(system, body.message)
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(500, f"AI 回复失败: {str(e)[:200]}")
