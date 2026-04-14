            # Build context and call LLM with failover support
            from app.services.agent_context import build_agent_context
            from app.services.llm import call_agent_llm_with_tools

            static_prompt, dynamic_prompt = await build_agent_context(agent_id, agent.name, agent.role_description or "")
            system_prompt = f"{static_prompt}\n\n{dynamic_prompt}"

            user_prompt = f"[自动调度任务] {instruction}"

            # Call LLM with unified failover support
            reply = await call_agent_llm_with_tools(
                db=db,
                agent_id=agent_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_rounds=50,
            )
