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
    };
};

export const failClosedSessionActiveRun = (
    current: SessionActiveRun | null,
): SessionActiveRun | null => current ? {
    ...current,
    canResume: false,
    canCancel: false,
} : null;

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
});
