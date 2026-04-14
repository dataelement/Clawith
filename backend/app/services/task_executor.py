    # Step 3: Build full agent context (same as chat dialog)
    from app.services.agent_context import build_agent_context
    static_prompt, dynamic_prompt = await build_agent_context(agent_id, agent_name, agent.role_description or "")

    # Add task-execution-specific instructions
    task_addendum = """

## Task Execution Mode

You are now in TASK EXECUTION MODE (not a conversation). A task has been assigned to you.
- Focus on completing the task as thoroughly as possible.
- Break down complex tasks into steps and execute each step.
- Use your tools actively to gather information, send messages, read/write files, etc.
- Provide a detailed execution report at the end.
- If the task involves contacting someone, use `send_feishu_message` to reach them.
- If the task requires data or information, use your tools to fetch it.
- Do NOT ask the user follow-up questions — take initiative and complete the task autonomously.
"""
    dynamic_prompt += task_addendum
    system_prompt = f"{static_prompt}\n\n{dynamic_prompt}"

    # Build user prompt
    if task_type == 'supervision':
        user_prompt = f"[督办任务] {task_title}"
        if task_description:
            user_prompt += f"\n任务描述: {task_description}"
        if supervision_target:
            user_prompt += f"\n督办对象: {supervision_target}"
        user_prompt += "\n\n请执行此督办任务：联系督办对象，了解进展，并汇报结果。"
    else:
        user_prompt = f"[任务执行] {task_title}"
        if task_description:
            user_prompt += f"\n任务描述: {task_description}"
        user_prompt += "\n\n请认真完成此任务，给出详细的执行结果。"

    # Step 4: Call LLM with unified failover support
    from app.services.llm import call_agent_llm_with_tools
