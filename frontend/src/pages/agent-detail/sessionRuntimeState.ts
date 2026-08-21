export type ToolResolutionStatus =
    | 'checking'
    | 'saved'
    | 'not_saved'
    | 'partial'
    | 'conflicted'
    | 'unavailable';

export type ToolReconciliation = {
    executionId: string;
    toolCallId: string;
    toolName: string;
    resultSummary?: string | null;
    errorCode?: string | null;
    canReconcile: boolean;
    resolutionStatus?: ToolResolutionStatus;
    savedCount?: number;
    pendingCount?: number;
    conflictedCount?: number;
    workspaceResolution?: boolean;
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

export const activeRunForSession = (
    activeRun: SessionActiveRun | null,
    sessionId: unknown,
): SessionActiveRun | null => {
    if (!activeRun || sessionId == null) return null;
    return activeRun.sessionId === String(sessionId) ? activeRun : null;
};

type SessionToolMessage = {
    role: string;
    toolName?: string;
    toolCallId?: string;
    toolArgs?: unknown;
    toolStatus?: string;
};

const toolTargetKey = (args: unknown): string => {
    let parsed = args;
    if (typeof args === 'string') {
        try {
            parsed = JSON.parse(args);
        } catch {
            return '';
        }
    }
    if (parsed === null || typeof parsed !== 'object') return '';
    const record = parsed as Record<string, unknown>;
    const value = record.path
        || record.file_path
        || record.output_path
        || record.target_path
        || record.filename
        || record.url
        || record.query
        || record.name
        || '';
    return typeof value === 'string' ? value.trim() : '';
};

export const mergeSessionToolMessage = <T extends SessionToolMessage>(
    messages: T[],
    incoming: T,
): T[] => {
    const incomingTarget = toolTargetKey(incoming.toolArgs);
    if (incoming.toolCallId) {
        const exactIndex = messages.findIndex(
            (message) => message.role === 'tool_call' && message.toolCallId === incoming.toolCallId,
        );
        if (exactIndex >= 0) {
            const existing = messages[exactIndex];
            if (existing.toolStatus === 'done' && incoming.toolStatus === 'running') return messages;
            return [
                ...messages.slice(0, exactIndex),
                { ...existing, ...incoming },
                ...messages.slice(exactIndex + 1),
            ];
        }
    }
    const sameRunningTool = (message: T) => (
        message.role === 'tool_call'
        && message.toolName === incoming.toolName
        && message.toolStatus === 'running'
        && (
            (!!incomingTarget && toolTargetKey(message.toolArgs) === incomingTarget)
            || (!incoming.toolCallId && !incomingTarget)
        )
    );
    const reverseIndex = [...messages].reverse().findIndex(sameRunningTool);
    if (reverseIndex < 0) return [...messages, incoming];
    const index = messages.length - 1 - reverseIndex;
    return [
        ...messages.slice(0, index),
        { ...messages[index], ...incoming },
        ...messages.slice(index + 1),
    ];
};

export const mergeSessionToolMessages = <T extends SessionToolMessage>(
    messages: T[],
    incoming: T[],
): T[] => incoming.reduce(mergeSessionToolMessage, messages);

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

const TOOL_RESOLUTION_STATUSES = new Set<ToolResolutionStatus>([
    'checking',
    'saved',
    'not_saved',
    'partial',
    'conflicted',
    'unavailable',
]);

const optionalCount = (value: unknown): number | undefined => (
    typeof value === 'number' && Number.isInteger(value) && value >= 0
        ? value
        : undefined
);

const firstDefined = (raw: Record<string, unknown>, ...keys: string[]): unknown => {
    for (const key of keys) {
        if (raw[key] !== undefined) return raw[key];
    }
    return undefined;
};

export const toolReconciliationNeedsUserAction = (
    reconciliation: ToolReconciliation,
): boolean => reconciliation.canReconcile;

export const toolReconciliationsByCallId = (
    reconciliations: ToolReconciliation[],
): Map<string, ToolReconciliation> => new Map(
    reconciliations.map((reconciliation) => [reconciliation.toolCallId, reconciliation]),
);

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
        const rawResolutionStatus = firstDefined(item, 'resolution_status', 'resolutionStatus');
        if (
            rawResolutionStatus !== undefined
            && (typeof rawResolutionStatus !== 'string'
                || !TOOL_RESOLUTION_STATUSES.has(rawResolutionStatus as ToolResolutionStatus))
        ) return null;
        pendingToolReconciliations.push({
            executionId,
            toolCallId,
            toolName,
            resultSummary: optionalText(item.result_summary),
            errorCode: optionalText(item.error_code),
            canReconcile: item.can_reconcile === true,
            ...(rawResolutionStatus !== undefined
                ? { resolutionStatus: rawResolutionStatus as ToolResolutionStatus }
                : {}),
            ...(optionalCount(firstDefined(item, 'saved_count', 'savedCount')) !== undefined
                ? { savedCount: optionalCount(firstDefined(item, 'saved_count', 'savedCount')) }
                : {}),
            ...(optionalCount(firstDefined(item, 'pending_count', 'pendingCount')) !== undefined
                ? { pendingCount: optionalCount(firstDefined(item, 'pending_count', 'pendingCount')) }
                : {}),
            ...(optionalCount(firstDefined(item, 'conflicted_count', 'conflictedCount')) !== undefined
                ? { conflictedCount: optionalCount(firstDefined(item, 'conflicted_count', 'conflictedCount')) }
                : {}),
            ...(typeof firstDefined(item, 'workspace_resolution', 'workspaceResolution') === 'boolean'
                ? { workspaceResolution: firstDefined(item, 'workspace_resolution', 'workspaceResolution') as boolean }
                : {}),
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

export const runtimeTerminalPacketNeedsMessageRefresh = (
    runtimeStatus: unknown,
): boolean => typeof runtimeStatus === 'string'
    && ['completed', 'failed', 'cancelled'].includes(runtimeStatus);

type TerminalAssistantMessageLike = {
    id?: string;
    role?: string;
    content?: string;
    _streaming?: boolean;
    runtimeError?: unknown;
};

const terminalAssistantMessageIndex = (
    messages: TerminalAssistantMessageLike[],
    messageId: unknown,
    content: unknown,
): number => {
    if (typeof messageId === 'string' && messageId.trim()) {
        return messages.findIndex((message) => message.id === messageId);
    }
    const lastMessage = messages[messages.length - 1];
    return (
        lastMessage?.role === 'assistant'
        && lastMessage._streaming !== true
        && typeof content === 'string'
        && lastMessage.content === content
    ) ? messages.length - 1 : -1;
};

export const terminalAssistantMessageAlreadyPresent = (
    messages: TerminalAssistantMessageLike[],
    messageId: unknown,
    content: unknown,
): boolean => terminalAssistantMessageIndex(messages, messageId, content) >= 0;

export const mergeTerminalAssistantMessage = <T extends TerminalAssistantMessageLike>(
    messages: T[],
    terminalMessage: T,
): T[] => {
    const index = terminalAssistantMessageIndex(
        messages,
        terminalMessage.id,
        terminalMessage.content,
    );
    if (index < 0) return [...messages, terminalMessage];
    if (terminalMessage.runtimeError === undefined) return messages;
    return messages.map((message, position) => position === index
        ? { ...message, runtimeError: terminalMessage.runtimeError }
        : message);
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
