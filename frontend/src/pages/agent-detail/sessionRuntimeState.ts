export type ToolReconciliation = {
    executionId: string;
    toolCallId: string;
    toolName: string;
    resultSummary?: string | null;
    errorCode?: string | null;
    canReconcile: boolean;
};

export type SessionActiveRun = {
    runId: string;
    threadId: string;
    sessionId: string;
    status: string;
    waitingType?: string | null;
    waitingReason?: string | null;
    correlationId?: string | null;
    modelStepCount: number;
    canResume: boolean;
    canCancel: boolean;
    pendingToolReconciliations: ToolReconciliation[];
};

const record = (value: unknown): Record<string, unknown> | null =>
    value !== null && typeof value === 'object'
        ? value as Record<string, unknown>
        : null;

const requiredText = (value: unknown): string | null => {
    if (typeof value !== 'string') return null;
    const normalized = value.trim();
    return normalized || null;
};

const optionalText = (value: unknown): string | null =>
    value == null ? null : requiredText(value);

export const sessionActiveRunFromResponse = (payload: unknown): SessionActiveRun | null => {
    const body = record(payload);
    const rawValue = body?.active_run;
    if (rawValue == null) return null;
    const raw = record(rawValue);
    if (!raw) return null;

    const runId = requiredText(raw.run_id);
    const threadId = requiredText(raw.thread_id);
    const sessionId = requiredText(raw.session_id);
    const status = requiredText(raw.status);
    if (!runId || !threadId || !sessionId || !status) return null;

    const correlationId = optionalText(raw.correlation_id);
    const waitingType = optionalText(raw.waiting_type);
    const terminal = ['completed', 'failed', 'cancelled'].includes(status);
    const rawStepCount = raw.model_step_count;
    const modelStepCount = (
        typeof rawStepCount === 'number'
        && Number.isInteger(rawStepCount)
        && rawStepCount >= 0
    ) ? rawStepCount : 0;
    const rawReconciliations = raw.pending_tool_reconciliations;
    if (rawReconciliations != null && !Array.isArray(rawReconciliations)) return null;
    const pendingToolReconciliations: ToolReconciliation[] = [];
    for (const value of rawReconciliations || []) {
        const item = record(value);
        if (!item) return null;
        const executionId = requiredText(item.execution_id);
        const toolCallId = requiredText(item.tool_call_id);
        const toolName = requiredText(item.tool_name);
        if (!executionId || !toolCallId || !toolName) return null;
        pendingToolReconciliations.push({
            executionId,
            toolCallId,
            toolName,
            resultSummary: optionalText(item.result_summary),
            errorCode: optionalText(item.error_code),
            canReconcile: item.can_reconcile === true,
        });
    }

    return {
        runId,
        threadId,
        sessionId,
        status,
        waitingType,
        waitingReason: optionalText(raw.waiting_reason),
        correlationId,
        modelStepCount,
        canResume: (
            raw.can_resume === true
            && status === 'waiting_user'
            && waitingType !== null
            && correlationId !== null
        ),
        canCancel: raw.can_cancel === true && !terminal,
        pendingToolReconciliations,
    };
};

export const sessionRuntimeStateResponseIsValid = (
    payload: unknown,
    parsedActiveRun: SessionActiveRun | null,
): boolean => {
    const body = record(payload);
    if (!body || !("active_run" in body)) return false;
    return body.active_run === null || parsedActiveRun !== null;
};

export const failClosedSessionActiveRun = (
    current: SessionActiveRun | null,
): SessionActiveRun | null => current ? {
    ...current,
    canResume: false,
    canCancel: false,
    pendingToolReconciliations: (current.pendingToolReconciliations || []).map((item) => ({
        ...item,
        canReconcile: false,
    })),
} : null;

export const runtimeCompletionNeedsMessageRefresh = (
    previous: SessionActiveRun | null,
    next: SessionActiveRun | null,
): boolean => previous !== null && next === null;

export const terminalAssistantMessageAlreadyPresent = (
    messages: Array<{ id?: string; role?: string; content?: string; _streaming?: boolean }>,
    messageId: unknown,
    content: unknown,
): boolean => {
    if (typeof messageId === 'string' && messageId.trim()) {
        return messages.some((message) => message.id === messageId);
    }
    const lastMessage = messages[messages.length - 1];
    return (
        lastMessage?.role === 'assistant'
        && lastMessage._streaming !== true
        && typeof content === 'string'
        && lastMessage.content === content
    );
};

export const waitingSessionActiveRunHint = ({
    runId,
    sessionId,
    correlationId,
    current,
}: {
    runId: string;
    sessionId: string;
    correlationId: string;
    current: SessionActiveRun | null;
}): SessionActiveRun => ({
    runId,
    threadId: sessionId,
    sessionId,
    status: 'waiting_user',
    waitingType: 'user',
    waitingReason: null,
    correlationId,
    modelStepCount: current?.modelStepCount || 0,
    canResume: false,
    canCancel: false,
    pendingToolReconciliations: current?.pendingToolReconciliations || [],
});
