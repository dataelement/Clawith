export type ErrorSource = 'http' | 'websocket' | 'runtime' | 'chat';

export interface AppErrorContext {
    message: string;
    code: string;
    status?: number;
    traceId?: string;
    runId?: string;
    agentId?: string;
    stage?: string;
    details?: unknown;
    retryable?: boolean;
    source: ErrorSource;
}

export interface ApiErrorContext extends Omit<AppErrorContext, 'source'> {
    detail?: unknown;
}

interface HttpErrorInput {
    status: number;
    statusText?: string;
    bodyText?: string;
    traceId?: string | null;
}

const fieldLabels: Record<string, string> = {
    name: '名称',
    role_description: '角色描述',
    agent_type: '智能体类型',
    primary_model_id: '主模型',
    max_tokens_per_day: '每日 Token 上限',
    max_tokens_per_month: '每月 Token 上限',
};

export class AppError extends Error implements AppErrorContext {
    code: string;
    status?: number;
    traceId?: string;
    runId?: string;
    agentId?: string;
    stage?: string;
    details?: unknown;
    retryable?: boolean;
    source: ErrorSource;

    constructor(context: AppErrorContext) {
        super(context.message);
        this.name = 'AppError';
        this.code = context.code;
        this.status = context.status;
        this.traceId = context.traceId;
        this.runId = context.runId;
        this.agentId = context.agentId;
        this.stage = context.stage;
        this.details = context.details;
        this.retryable = context.retryable;
        this.source = context.source;
    }
}

export class ApiError extends AppError {
    detail?: unknown;

    constructor(context: ApiErrorContext) {
        super({ ...context, source: 'http' });
        this.name = 'ApiError';
        this.detail = context.detail;
    }
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function optionalString(value: unknown): string | undefined {
    return typeof value === 'string' && value.trim() ? value : undefined;
}

function stableStringify(value: unknown): string | undefined {
    try {
        const serialized = JSON.stringify(value);
        return serialized && serialized !== '{}' ? serialized : undefined;
    } catch {
        return undefined;
    }
}

function validationMessage(items: unknown[]): string | undefined {
    const messages = items.map((item) => {
        if (!isRecord(item)) return stableStringify(item);
        const message = optionalString(item.msg) ?? optionalString(item.message) ?? stableStringify(item);
        if (!message) return undefined;
        const loc = Array.isArray(item.loc) ? item.loc : [];
        const field = String(loc[loc.length - 1] ?? '');
        const label = fieldLabels[field] ?? field;
        return label ? `${label}: ${message}` : message;
    }).filter((message): message is string => Boolean(message));
    return messages.length ? messages.join('; ') : undefined;
}

function messageFromValue(value: unknown): string | undefined {
    if (typeof value === 'string') return optionalString(value);
    if (Array.isArray(value)) return validationMessage(value);
    if (!isRecord(value)) return value == null ? undefined : String(value);

    const direct = optionalString(value.message) ?? optionalString(value.detail);
    if (direct) return direct;
    if (isRecord(value.error)) {
        const nested = optionalString(value.error.message) ?? optionalString(value.error.detail);
        if (nested) return nested;
    }
    return stableStringify(value);
}

function parseBody(bodyText: string): unknown {
    const trimmed = bodyText.trim();
    if (!trimmed) return undefined;
    try {
        return JSON.parse(trimmed);
    } catch {
        return trimmed;
    }
}

function firstField(records: Array<Record<string, unknown> | undefined>, ...keys: string[]): unknown {
    for (const record of records) {
        if (!record) continue;
        for (const key of keys) {
            if (key in record && record[key] != null) return record[key];
        }
    }
    return undefined;
}

export function parseHttpError(input: HttpErrorInput): ApiError {
    const parsed = parseBody(input.bodyText ?? '');
    const envelope = isRecord(parsed) ? parsed : undefined;
    const canonical = envelope && isRecord(envelope.error) ? envelope.error : undefined;
    const legacyDetail = envelope?.detail;
    const legacyContext = isRecord(legacyDetail) ? legacyDetail : undefined;
    const contextRecords = [canonical, legacyContext, envelope];
    const fallback = `HTTP ${input.status}${input.statusText ? ` ${input.statusText}` : ''}`;

    // Canonical fields win; legacy detail and response text remain compatibility fallbacks.
    const message = messageFromValue(canonical?.message)
        ?? messageFromValue(legacyDetail)
        ?? messageFromValue(parsed)
        ?? fallback;
    const details = canonical && 'details' in canonical ? canonical.details : legacyDetail;
    const retryable = firstField(contextRecords, 'retryable');

    return new ApiError({
        message,
        code: optionalString(firstField(contextRecords, 'code'))
            ?? (input.status ? `http_${input.status}` : 'http_error'),
        status: input.status,
        traceId: optionalString(firstField(contextRecords, 'trace_id', 'traceId')) ?? optionalString(input.traceId),
        runId: optionalString(firstField(contextRecords, 'run_id', 'runId')),
        agentId: optionalString(firstField(contextRecords, 'agent_id', 'agentId')),
        stage: optionalString(firstField(contextRecords, 'stage')),
        details,
        retryable: typeof retryable === 'boolean' ? retryable : undefined,
        detail: legacyDetail,
    });
}

export async function parseHttpErrorResponse(response: Response): Promise<ApiError> {
    let bodyText = '';
    try {
        bodyText = await response.text();
    } catch {
        // Preserve the HTTP status and trace header even if the body stream cannot be read.
    }
    return parseHttpError({
        status: response.status,
        statusText: response.statusText,
        bodyText,
        traceId: response.headers.get('X-Trace-Id'),
    });
}

export function normalizeUnknownError(
    error: unknown,
    context: Partial<Omit<AppErrorContext, 'message'>> = {},
): AppError {
    if (error instanceof AppError) return error;
    const message = error instanceof Error
        ? error.message
        : messageFromValue(error) ?? 'Unknown error';
    return new AppError({
        message,
        code: context.code ?? 'unknown_error',
        source: context.source ?? 'http',
        status: context.status,
        traceId: context.traceId,
        runId: context.runId,
        agentId: context.agentId,
        stage: context.stage,
        details: context.details,
        retryable: context.retryable,
    });
}
