export type AgentNameValidationError = 'required' | 'too_short' | 'too_long';

export function validateAgentName(value: string): AgentNameValidationError | null {
    const length = [...value.trim()].length;
    if (length === 0) return 'required';
    if (length < 2) return 'too_short';
    if (length > 100) return 'too_long';
    return null;
}
