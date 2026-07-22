export interface RuntimeModelOption {
    id: string;
    enabled?: boolean;
    supports_tool_calling?: boolean | null;
}

export function isRuntimeReadyModel(model: RuntimeModelOption): boolean;
export function runtimeReadyModels<T extends RuntimeModelOption>(models: T[]): T[];
export function resolveEffectiveChatModelId(options: {
    models: RuntimeModelOption[];
    overrideModelId?: string | null;
    agentPrimaryModelId?: string | null;
    tenantDefaultModelId?: string | null;
}): string | null;
