import { fetchJson } from './api';

export type AiOperationsData = {
    period: { days: number; from: string; to: string };
    overview: { total_runs: number; success: number; failed: number; cancelled: number; success_rate: number; failure_rate: number };
    daily: Array<{ date: string; success: number; failed: number; cancelled: number }>;
    agents: Array<{ agent_id: string | null; agent_name: string; success: number; failed: number; cancelled: number; total: number; success_rate: number }>;
    models: Array<{ model_id: string | null; model_name: string; provider: string; success: number; failed: number; cancelled: number; total: number; success_rate: number }>;
    failures: Array<{ run_id: string; agent_id: string | null; agent_name: string; model_name: string; error_code: string; error_message: string; goal: string; created_at: string }>;
    reports: Array<{ run_id: string; agent_id: string | null; agent_name: string; title: string; report_type: string; created_at: string }>;
};

export const aiOperationsApi = {
    get: (days: 7 | 30 | 90) => fetchJson<AiOperationsData>(`/enterprise/ai-operations?days=${days}`),
};
