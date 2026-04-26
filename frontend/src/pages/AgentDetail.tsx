import React, { useState, useEffect, useMemo, useRef, useCallback, Component, ErrorInfo } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import ConfirmModal from '../components/ConfirmModal';
import type { FileBrowserApi } from '../components/FileBrowser';
import FileBrowser from '../components/FileBrowser';
import ChannelConfig from '../components/ChannelConfig';
import MarkdownRenderer from '../components/MarkdownRenderer';
import PromptModal from '../components/PromptModal';
import OpenClawSettings from './OpenClawSettings';
import type { LivePreviewState } from '../components/AgentBayLivePanel';
import AgentSidePanel, { SidePanelTab } from '../components/AgentSidePanel';
import type { WorkspaceActivity, WorkspaceLiveDraft } from '../components/WorkspaceOperationPanel';
import AgentCredentials from '../components/AgentCredentials';
import { activityApi, agentApi, channelApi, enterpriseApi, fileApi, scheduleApi, skillApi, taskApi, triggerApi, uploadFileWithProgress } from '../services/api';
import { useAppStore } from '../stores';
import { useAuthStore } from '../stores';
import { copyToClipboard } from '../utils/clipboard';
import { formatFileSize } from '../utils/formatFileSize';
import { IconPaperclip, IconSend } from '@tabler/icons-react';
import { useDropZone } from '../hooks/useDropZone';

const TABS = ['status', 'aware', 'mind', 'tools', 'skills', 'relationships', 'workspace', 'chat', 'activityLog', 'approvals', 'settings'] as const;

const WORKSPACE_TOOLS = new Set([
    'write_file',
    'edit_file',
    'delete_file',
    'convert_markdown_to_docx',
    'convert_csv_to_xlsx',
    'convert_markdown_to_pdf',
    'convert_html_to_pdf',
    'convert_html_to_pptx',
]);

function workspaceActionForTool(tool: string): WorkspaceLiveDraft['action'] {
    if (tool === 'edit_file') return 'edit';
    if (tool === 'delete_file') return 'delete';
    if (tool.startsWith('convert_')) return 'convert';
    return 'write';
}

function decodeJsonStringFragment(value: string): string {
    try {
        return JSON.parse(`"${value.replace(/"/g, '\\"')}"`);
    } catch {
        return value.replace(/\\n/g, '\n').replace(/\\"/g, '"').replace(/\\\\/g, '\\');
    }
}

function readPartialJsonString(raw: string, key: string): string | undefined {
    const marker = `"${key}"`;
    const markerIdx = raw.indexOf(marker);
    if (markerIdx < 0) return undefined;
    const colonIdx = raw.indexOf(':', markerIdx + marker.length);
    if (colonIdx < 0) return undefined;
    const firstQuote = raw.indexOf('"', colonIdx + 1);
    if (firstQuote < 0) return undefined;
    let escaped = false;
    let value = '';
    for (let i = firstQuote + 1; i < raw.length; i += 1) {
        const ch = raw[i];
        if (escaped) {
            value += `\\${ch}`;
            escaped = false;
            continue;
        }
        if (ch === '\\') {
            escaped = true;
            continue;
        }
        if (ch === '"') break;
        value += ch;
    }
    return decodeJsonStringFragment(value);
}

function parseWorkspaceDraftArgs(tool: string, raw: string): Pick<WorkspaceLiveDraft, 'path' | 'content'> {
    let parsed: any = null;
    try {
        parsed = JSON.parse(raw || '{}');
    } catch {
        parsed = null;
    }
    const getString = (key: string) => {
        const parsedValue = parsed?.[key];
        if (typeof parsedValue === 'string') return parsedValue;
        return readPartialJsonString(raw || '', key);
    };
    const sourcePath = getString('source_path');
    const path = getString('path') || getString('target_path') || sourcePath;
    let content = getString('content');
    if (tool === 'edit_file') content = getString('new_string') || content;
    return { path, content };
}

function workspaceFileName(path: string): string {
    return path.replace(/^workspace\//, '') || path;
}

// Format large token numbers with K/M suffixes
const formatTokens = (n: number) => {
    if (!n) return '0';
    if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
    return String(n);
};

const getCategoryLabels = (t: any): Record<string, string> => ({
    file: t('agent.toolCategories.file'),
    task: t('agent.toolCategories.task'),
    communication: t('agent.toolCategories.communication'),
    search: t('agent.toolCategories.search'),
    aware: t('agent.toolCategories.aware', 'Aware & Triggers'),
    social: t('agent.toolCategories.social', 'Social'),
    code: t('agent.toolCategories.code', 'Code & Execution'),
    discovery: t('agent.toolCategories.discovery', 'Discovery'),
    email: t('agent.toolCategories.email', 'Email'),
    feishu: t('agent.toolCategories.feishu', 'Feishu / Lark'),
    custom: t('agent.toolCategories.custom'),
    general: t('agent.toolCategories.general'),
    agentbay: t('agent.toolCategories.agentbay', 'AgentBay'),
});

function ToolsManager({ agentId, canManage = false }: { agentId: string; canManage?: boolean }) {
    const { t } = useTranslation();
    const [tools, setTools] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [configTool, setConfigTool] = useState<any | null>(null);
    const [configData, setConfigData] = useState<Record<string, any>>({});
    const [configJson, setConfigJson] = useState('');
    const [configSaving, setConfigSaving] = useState(false);
    const [toolTab, setToolTab] = useState<'company' | 'installed'>('company');
    const [deletingToolId, setDeletingToolId] = useState<string | null>(null);
    const [configCategory, setConfigCategory] = useState<string | null>(null);
    const [focusedField, setFocusedField] = useState<string | null>(null);
    // Global (company-level) config for the currently open modal — used to show
    // lock hints and prevent agent from overriding company-set fields.
    const [configGlobalData, setConfigGlobalData] = useState<Record<string, any>>({});

    const CATEGORY_CONFIG_SCHEMAS: Record<string, any> = {
        agentbay: {
            title: 'AgentBay Settings',
            fields: [
                { key: 'api_key', label: 'API Key (from AgentBay)', type: 'password', placeholder: 'Enter your AgentBay API key' },
                { key: 'os_type', label: 'Cloud Computer OS', type: 'select', default: 'windows', options: [{ value: 'linux', label: 'Linux' }, { value: 'windows', label: 'Windows' }] },
            ]
        },
        atlassian: {
            title: 'Atlassian Connectivity Settings',
            fields: [
                { key: 'api_key', label: 'API Key (Atlassian API Token)', type: 'password', placeholder: 'Enter your Atlassian API key' },
                { key: 'cloud_id', label: 'Cloud ID (Optional)', type: 'text', placeholder: 'e.g. bcc01-abc-123' }
            ]
        }
    };

    const loadTools = async () => {
        try {
            const token = localStorage.getItem('token');
            const res = await fetch(`/api/tools/agents/${agentId}/with-config`, {
                headers: { Authorization: `Bearer ${token}` },
            });
            if (res.ok) setTools(await res.json());
            else {
                // Fallback to old endpoint
                const res2 = await fetch(`/api/tools/agents/${agentId}`, { headers: { Authorization: `Bearer ${token}` } });
                if (res2.ok) setTools(await res2.json());
            }
        } catch (e) { console.error(e); }
        setLoading(false);
    };

    useEffect(() => { loadTools(); }, [agentId]);

    const toggleTool = async (toolId: string, enabled: boolean) => {
        setTools(prev => prev.map(t => t.id === toolId ? { ...t, enabled } : t));
        try {
            const token = localStorage.getItem('token');
            await fetch(`/api/tools/agents/${agentId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify([{ tool_id: toolId, enabled }]),
            });
        } catch (e) { console.error(e); }
    };

    // Sensitive field keys that should not be pre-filled from masked global config.
    // Hardcoded fallback set + dynamic extraction from config_schema password-type fields.
    const SENSITIVE_KEYS_BASE = new Set(['api_key', 'private_key', 'auth_code', 'password', 'secret']);

    const getSensitiveKeys = (schema: any): Set<string> => {
        const keys = new Set(SENSITIVE_KEYS_BASE);
        if (schema?.fields) {
            for (const field of schema.fields) {
                if (field.type === 'password') keys.add(field.key);
            }
        }
        return keys;
    };

    const openConfig = (tool: any) => {
        setConfigTool(tool);
        // Build merged config: start with global defaults, overlay agent overrides.
        // For sensitive fields, only use agent_config values (global ones are masked
        // like "****xxxx" and should not pre-fill the input).
        const sensitiveKeys = getSensitiveKeys(tool.config_schema);
        const globalCfg = tool.global_config || {};
        const agentCfg = tool.agent_config || {};
        const merged: Record<string, any> = {};
        for (const [k, v] of Object.entries(globalCfg)) {
            if (!sensitiveKeys.has(k)) merged[k] = v;
        }
        Object.assign(merged, agentCfg);
        setConfigData(merged);
        setConfigJson(JSON.stringify(agentCfg, null, 2));
        setFocusedField(null);
    };

    const openCategoryConfig = async (category: string) => {
        setConfigCategory(category);
        setConfigData({});
        setConfigGlobalData({});
        setConfigSaving(true);
        setFocusedField(null);
        try {
            const token = localStorage.getItem('token');
            const res = await fetch(`/api/tools/agents/${agentId}/category-config/${category}`, {
                headers: { Authorization: `Bearer ${token}` },
            });
            if (res.ok) {
                const data = await res.json();
                // global_config: company-level (masked sensitive fields like ****xxxx)
                // agent_config: agent-level overrides only
                const globalCfg = data.global_config || {};
                const agentCfg = data.agent_config || {};
                setConfigGlobalData(globalCfg);
                // Pre-fill only agent-level values; company fields show as hints
                const catSchema = CATEGORY_CONFIG_SCHEMAS[category];
                const sensitiveKeys = getSensitiveKeys(catSchema);
                const merged: Record<string, any> = {};
                for (const [k, v] of Object.entries(globalCfg)) {
                    // Non-sensitive global fields (e.g. os_type) pre-fill; sensitive ones don't
                    if (!sensitiveKeys.has(k)) merged[k] = v;
                }
                Object.assign(merged, agentCfg);
                setConfigData(merged);
            }
        } catch (e) { console.error(e); }
        setConfigSaving(false);
    };

    const saveConfig = async () => {
        if (!configTool && !configCategory) return;
        setConfigSaving(true);
        try {
            const token = localStorage.getItem('token');

            if (configCategory) {
                const raw = configData;
                // Strip empty sensitive fields so untouched password inputs
                // don't send empty values that would clear an inherited company key
                const catSchema = CATEGORY_CONFIG_SCHEMAS[configCategory!];
                const sensitiveKeys = getSensitiveKeys(catSchema);
                const payload: Record<string, any> = {};
                for (const [k, v] of Object.entries(raw)) {
                    if (sensitiveKeys.has(k) && (v === '' || v === undefined || v === null)) continue;
                    payload[k] = v;
                }
                await fetch(`/api/tools/agents/${agentId}/category-config/${configCategory}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                    body: JSON.stringify({ config: payload }),
                });
                setConfigCategory(null);
            } else {
                const hasSchema = configTool.config_schema?.fields?.length > 0;
                const raw = hasSchema ? configData : JSON.parse(configJson || '{}');
                // Strip empty sensitive fields only — agent CAN override company values
                const sensitiveKeys = getSensitiveKeys(configTool.config_schema);
                const payload: Record<string, any> = {};
                for (const [k, v] of Object.entries(raw)) {
                    if (sensitiveKeys.has(k) && (v === '' || v === undefined || v === null)) continue;
                    payload[k] = v;
                }
                await fetch(`/api/tools/agents/${agentId}/tool-config/${configTool.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                    body: JSON.stringify({ config: payload }),
                });
                setConfigTool(null);
            }
            loadTools();
        } catch (e) { alert('Save failed: ' + e); }
        setConfigSaving(false);
    };

    if (loading) return <div style={{ color: 'var(--text-tertiary)', padding: '20px' }}>{t('common.loading')}</div>;

    // Company tools = platform presets (builtin) + company admin-added tools (admin)
    const companyTools = tools.filter(t => t.source === 'builtin' || t.source === 'admin');
    const agentInstalledTools = tools.filter(t => t.source === 'agent');

    const groupByCategory = (toolList: any[]) =>
        toolList.reduce((acc: Record<string, any[]>, t) => {
            const cat = t.category || 'general';
            (acc[cat] = acc[cat] || []).push(t);
            return acc;
        }, {});

    const renderToolGroup = (groupedTools: Record<string, any[]>) =>
        Object.entries(groupedTools).map(([category, catTools]) => (
            <div key={category}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 14px', marginBottom: '8px' }}>
                    <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                        {getCategoryLabels(t)[category] || category}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {CATEGORY_CONFIG_SCHEMAS[category] && canManage && (
                            <button
                                onClick={() => openCategoryConfig(category)}
                                style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}
                                title={`Configure ${category}`}
                            >⚙️ Config</button>
                        )}
                        {canManage && (
                            <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }} title={`Enable/Disable all ${getCategoryLabels(t)[category] || category} tools`}>
                                <input type="checkbox"
                                    checked={(catTools as any[]).every(t => t.enabled)}
                                    onChange={async (e) => {
                                        const targetEnabled = e.target.checked;
                                        // Optimistic fast update
                                        const catToolIds = new Set((catTools as any[]).map(t => t.id));
                                        setTools(prev => prev.map(t => catToolIds.has(t.id) ? { ...t, enabled: targetEnabled } : t));
                                        try {
                                            const token = localStorage.getItem('token');
                                            const payload = Array.from(catToolIds).map(id => ({ tool_id: id, enabled: targetEnabled }));
                                            await fetch(`/api/tools/agents/${agentId}`, {
                                                method: 'PUT',
                                                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                                                body: JSON.stringify(payload),
                                            });
                                        } catch (err: any) {
                                            console.error('Bulk update failed', err);
                                            loadTools();
                                        }
                                    }}
                                    style={{ opacity: 0, width: 0, height: 0 }} />
                                <span style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, borderRadius: '22px', background: (catTools as any[]).every(t => t.enabled) ? 'var(--accent-primary)' : 'var(--bg-tertiary)', transition: '0.3s', boxShadow: 'inset 0 1px 3px rgba(0,0,0,0.1)' }}>
                                    <span style={{ position: 'absolute', left: (catTools as any[]).every(t => t.enabled) ? '20px' : '2px', top: '2px', width: '18px', height: '18px', borderRadius: '50%', background: '#fff', transition: '0.3s', boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }} />
                                </span>
                            </label>
                        )}
                    </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {(catTools as any[]).map((tool: any) => {
                        const hasConfig = tool.config_schema?.fields?.length > 0 || tool.type === 'mcp';
                        const hasAgentOverride = tool.agent_config && Object.keys(tool.agent_config).length > 0;
                        const isGlobalCategoryConfig = category === 'agentbay' && tool.name === 'agentbay_browser_navigate';
                        return (
                            <div key={tool.id} className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flex: 1, minWidth: 0 }}>
                                    <span style={{ fontSize: '18px' }}>{tool.icon}</span>
                                    <div style={{ minWidth: 0 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                            <span style={{ fontWeight: 500, fontSize: '13px' }}>{tool.display_name}</span>
                                            {tool.type === 'mcp' && (
                                                <span style={{ fontSize: '10px', background: 'var(--primary)', color: '#fff', borderRadius: '4px', padding: '1px 5px' }}>MCP</span>
                                            )}
                                            {tool.type === 'builtin' && (
                                                <span style={{ fontSize: '10px', background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', borderRadius: '4px', padding: '1px 5px' }}>Built-in</span>
                                            )}
                                            {hasAgentOverride && (
                                                <span style={{ fontSize: '10px', background: 'rgba(99,102,241,0.15)', color: 'var(--accent-color)', borderRadius: '4px', padding: '1px 5px' }}>{t('enterprise.tools.configured', 'Configured')}</span>
                                            )}
                                        </div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {tool.description}
                                            {tool.mcp_server_name && <span> · {tool.mcp_server_name}</span>}
                                        </div>
                                    </div>
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                                    {canManage && hasConfig && !isGlobalCategoryConfig && (
                                        <button
                                            onClick={() => openConfig(tool)}
                                            style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)' }}
                                            title="Configure per-agent settings"
                                        >⚙️ Config</button>
                                    )}
                                    {canManage && tool.source === 'agent' && tool.agent_tool_id && (
                                        <button
                                            onClick={async () => {
                                                if (!confirm(t('agent.tools.confirmDelete', `Remove "${tool.display_name}" from this agent?`))) return;
                                                setDeletingToolId(tool.id);
                                                try {
                                                    const token = localStorage.getItem('token');
                                                    const res = await fetch(`/api/tools/agent-tool/${tool.agent_tool_id}`, {
                                                        method: 'DELETE',
                                                        headers: { Authorization: `Bearer ${token}` },
                                                    });
                                                    if (res.ok) await loadTools();
                                                    else alert('Delete failed');
                                                } catch (e) { alert('Delete failed: ' + e); }
                                                setDeletingToolId(null);
                                            }}
                                            disabled={deletingToolId === tool.id}
                                            style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-tertiary)', opacity: deletingToolId === tool.id ? 0.5 : 1 }}
                                            title={t('agent.tools.removeTool', 'Remove from agent')}
                                        >{deletingToolId === tool.id ? '...' : '✕'}</button>
                                    )}
                                    {canManage ? (
                                        <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }}>
                                            <input
                                                type="checkbox"
                                                checked={tool.enabled}
                                                onChange={e => toggleTool(tool.id, e.target.checked)}
                                                style={{ opacity: 0, width: 0, height: 0 }}
                                            />
                                            <span style={{
                                                position: 'absolute', inset: 0,
                                                background: tool.enabled ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                                                borderRadius: '11px', transition: 'background 0.2s',
                                            }}>
                                                <span style={{
                                                    position: 'absolute', left: tool.enabled ? '20px' : '2px', top: '2px',
                                                    width: '18px', height: '18px', background: '#fff',
                                                    borderRadius: '50%', transition: 'left 0.2s',
                                                }} />
                                            </span>
                                        </label>
                                    ) : (
                                        <span style={{ fontSize: '11px', color: tool.enabled ? 'var(--accent-primary)' : 'var(--text-tertiary)', fontWeight: 500 }}>
                                            {tool.enabled ? t('common.enabled', 'On') : t('common.disabled', 'Off')}
                                        </span>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>
        ));

    const activeTools = toolTab === 'company' ? companyTools : agentInstalledTools;

    return (
        <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div style={{ display: 'flex', gap: '4px', padding: '4px', background: 'var(--bg-secondary)', borderRadius: '8px', marginBottom: '12px' }}>
                    <button
                        onClick={() => setToolTab('company')}
                        style={{
                            flex: 1, padding: '7px 12px', border: 'none', borderRadius: '6px', cursor: 'pointer',
                            fontSize: '12px', fontWeight: 600, transition: 'all 0.2s',
                            background: toolTab === 'company' ? 'var(--bg-primary)' : 'transparent',
                            color: toolTab === 'company' ? 'var(--text-primary)' : 'var(--text-tertiary)',
                            boxShadow: toolTab === 'company' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                        }}
                    >
                        {t('agent.tools.companyTools', 'Company Tools')} ({companyTools.length})
                    </button>
                    <button
                        onClick={() => setToolTab('installed')}
                        style={{
                            flex: 1, padding: '7px 12px', border: 'none', borderRadius: '6px', cursor: 'pointer',
                            fontSize: '12px', fontWeight: 600, transition: 'all 0.2s',
                            background: toolTab === 'installed' ? 'var(--bg-primary)' : 'transparent',
                            color: toolTab === 'installed' ? 'var(--text-primary)' : 'var(--text-tertiary)',
                            boxShadow: toolTab === 'installed' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                        }}
                    >
                        {t('agent.tools.agentInstalled', 'Agent Self-Installed Tools')} ({agentInstalledTools.length})
                    </button>
                </div>

                {/* Tool List */}
                {activeTools.length > 0 ? (
                    renderToolGroup(groupByCategory(activeTools))
                ) : (
                    <div className="card" style={{ textAlign: 'center', padding: '30px', color: 'var(--text-tertiary)' }}>
                        {toolTab === 'installed' ? t('agent.tools.noInstalled', 'No agent-installed tools yet') : t('agent.tools.noCompany', 'No company-configured tools')}
                    </div>
                )}
            </div>
            {tools.length === 0 && (
                <div className="card" style={{ textAlign: 'center', padding: '30px', color: 'var(--text-tertiary)' }}>
                    {t('common.noData')}
                </div>
            )}

            {/* Tool Config Modal */}
            {(configTool || configCategory) && (() => {
                const target = configTool || CATEGORY_CONFIG_SCHEMAS[configCategory!];
                const fields = configTool ? (configTool.config_schema?.fields || []) : (target.fields || []);
                const title = configTool ? configTool.display_name : target.title;
                const isCat = !!configCategory;
                return (
                    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.55)', zIndex: 2000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                        onClick={() => { setConfigTool(null); setConfigCategory(null); }}>
                        <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', width: '480px', maxWidth: '95vw', maxHeight: '80vh', overflow: 'auto', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                                <div>
                                    <h3 style={{ margin: 0 }}>⚙️ {title}</h3>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{isCat ? 'Shared category configuration (affects all tools in this category)' : 'Per-agent configuration (overrides global defaults)'}</div>
                                </div>
                                <button onClick={() => { setConfigTool(null); setConfigCategory(null); }} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)' }}>✕</button>
                            </div>

                            {fields.length > 0 ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                    {fields
                                        .filter((field: any) => {
                                            // Handle depends_on: hide fields unless dependency is met
                                            if (!field.depends_on) return true;
                                            return Object.entries(field.depends_on).every(([depKey, depVals]: [string, any]) =>
                                                (depVals as string[]).includes(configData[depKey] ?? '')
                                            );
                                        })
                                        .map((field: any) => {
                                            // Get user role from store directly in the map function
                                            const userFromStore = useAuthStore.getState().user;
                                            const currentUserRole = userFromStore?.role;
                                            const isReadOnly = field.read_only_for_roles?.includes(currentUserRole);
                                            return (
                                                <div key={field.key}>
                                                    <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '4px' }}>
                                                        {field.label}
                                                        {isReadOnly && <span style={{ fontWeight: 400, color: 'var(--text-tertiary)', marginLeft: '4px' }}>(Admin only)</span>}
                                                        {/* Show company-configured value as a hint in the label */}
                                                        {(() => {
                                                            const globalVal = configTool?.global_config?.[field.key] ?? configGlobalData?.[field.key];
                                                            if (!globalVal) return null;
                                                            return (
                                                                <span style={{ fontWeight: 400, color: 'var(--accent-primary)', marginLeft: '4px', fontSize: '11px' }}>
                                                                    (company: {String(globalVal).slice(0, 20)}{String(globalVal).length > 20 ? '\u2026' : ''})
                                                                </span>
                                                            );
                                                        })()}
                                                    </label>
                                                    {field.type === 'checkbox' ? (
                                                        <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: isReadOnly ? 'not-allowed' : 'pointer' }}>
                                                            <input
                                                                type="checkbox"
                                                                checked={configData[field.key] ?? field.default ?? false}
                                                                disabled={isReadOnly}
                                                                onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.checked }))}
                                                                style={{ opacity: 0, width: 0, height: 0 }}
                                                            />
                                                            <span style={{
                                                                position: 'absolute', inset: 0,
                                                                background: (configData[field.key] ?? field.default) ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                                                                borderRadius: '11px', transition: 'background 0.2s', opacity: isReadOnly ? 0.6 : 1,
                                                            }}>
                                                                <span style={{
                                                                    position: 'absolute', left: (configData[field.key] ?? field.default) ? '20px' : '2px', top: '2px',
                                                                    width: '18px', height: '18px', background: '#fff',
                                                                    borderRadius: '50%', transition: 'left 0.2s',
                                                                }} />
                                                            </span>
                                                        </label>
                                                    ) : field.type === 'password' ? (
                                                        <>
                                                        {(() => {
                                                            const globalVal = configTool?.global_config?.[field.key] ?? configGlobalData?.[field.key];
                                                            const isUsingGlobal = globalVal && !configData[field.key];
                                                            
                                                            if (isUsingGlobal && focusedField !== field.key) {
                                                                return (
                                                                    <div 
                                                                        className="form-input" 
                                                                        style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'text', background: 'var(--bg-tertiary)', borderColor: 'var(--border)', overflow: 'hidden' }}
                                                                        onClick={() => setFocusedField(field.key)}
                                                                    >
                                                                        <span style={{ flex: 1, color: 'var(--text-tertiary)', fontSize: '13px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t('agent.tools.usingCompanyKey', 'Using company key ({{val}})', { val: globalVal })}</span>
                                                                        <span style={{ fontSize: '12px', color: 'var(--accent-primary)', flexShrink: 0, cursor: 'pointer' }}>{t('common.edit', 'Edit')}</span>
                                                                    </div>
                                                                );
                                                            }

                                                            return (
                                                                <input type="password" autoComplete="new-password" className="form-input"
                                                                    autoFocus={focusedField === field.key}
                                                                    value={configData[field.key] ?? ''}
                                                                    placeholder={globalVal ? t('agent.tools.usingCompanyKey', 'Using company key ({{val}})', { val: globalVal }) : (field.placeholder || t('admin.leaveBlankDefault', 'Leave blank to use global default'))}
                                                                    onBlur={(e) => {
                                                                        if (!e.target.value) setFocusedField(null);
                                                                    }}
                                                                    onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.value }))} />
                                                            );
                                                        })()}
                                                        {/* Per-provider help text for auth_code */}
                                                        {field.key === 'auth_code' && (() => {
                                                            const providerField = configTool?.config_schema?.fields?.find((f: any) => f.key === 'email_provider');
                                                            const selectedProvider = configData['email_provider'] || providerField?.default || '';
                                                            const providerOption = providerField?.options?.find((o: any) => o.value === selectedProvider);
                                                            if (!providerOption?.help_text) return null;
                                                            return (
                                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px', lineHeight: '1.5' }}>
                                                                    {providerOption.help_text}
                                                                    {providerOption.help_url && (
                                                                        <> &middot; <a href={providerOption.help_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-primary)', textDecoration: 'none' }}>Setup guide</a></>
                                                                    )}
                                                                </div>
                                                            );
                                                        })()}

                                                        </>
                                                    ) : field.type === 'select' ? (
                                                        <select className="form-input" value={configData[field.key] ?? field.default ?? ''}
                                                            onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.value }))}>
                                                            {(field.options || []).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                                                        </select>
                                                    ) : field.type === 'number' ? (
                                                        <input type="number" className="form-input" value={configData[field.key] ?? field.default ?? ''} placeholder={field.placeholder || ''} min={field.min} max={field.max} onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.value ? Number(e.target.value) : '' }))} />
                                                    ) : (
                                                        <>
                                                        {(() => {
                                                            const globalVal = configTool?.global_config?.[field.key] ?? configGlobalData?.[field.key];
                                                            const isUsingGlobal = globalVal && !configData[field.key];
                                                            
                                                            if (isUsingGlobal && focusedField !== field.key) {
                                                                return (
                                                                    <div 
                                                                        className="form-input" 
                                                                        style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'text', background: 'var(--bg-tertiary)', borderColor: 'var(--border)', overflow: 'hidden' }}
                                                                        onClick={() => setFocusedField(field.key)}
                                                                    >
                                                                        <span style={{ flex: 1, color: 'var(--text-tertiary)', fontSize: '13px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t('agent.tools.usingCompanyConfig', 'Using company config ({{val}})', { val: globalVal })}</span>
                                                                        <span style={{ fontSize: '12px', color: 'var(--accent-primary)', flexShrink: 0, cursor: 'pointer' }}>{t('common.edit', 'Edit')}</span>
                                                                    </div>
                                                                );
                                                            }

                                                            return (
                                                                <input type="text" className="form-input"
                                                                    autoFocus={focusedField === field.key}
                                                                    value={configData[field.key] ?? ''}
                                                                    placeholder={globalVal ? t('agent.tools.usingCompanyConfig', 'Using company config ({{val}})', { val: globalVal }) : (field.placeholder || t('admin.leaveBlankDefault', 'Leave blank to use global default'))}
                                                                    onBlur={(e) => {
                                                                        if (!e.target.value) setFocusedField(null);
                                                                    }}
                                                                    onChange={e => setConfigData(p => ({ ...p, [field.key]: e.target.value }))} />
                                                            );
                                                        })()}
                                                        </>
                                                    )}
                                                </div>
                                            );
                                        })}
                                    {/* Email tool: test connection button + help text */}
                                    {configTool?.category === 'email' && (
                                        <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                            <button
                                                className="btn btn-secondary"
                                                style={{ alignSelf: 'flex-start' }}
                                                onClick={async () => {
                                                    const btn = document.getElementById('email-test-btn');
                                                    const status = document.getElementById('email-test-status');
                                                    if (btn) btn.textContent = 'Testing...';
                                                    if (btn) (btn as HTMLButtonElement).disabled = true;
                                                    try {
                                                        const token = localStorage.getItem('token');
                                                        const res = await fetch('/api/tools/test-email', {
                                                            method: 'POST',
                                                            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                                                            body: JSON.stringify({ config: configData }),
                                                        });
                                                        const data = await res.json();
                                                        if (status) {
                                                            status.textContent = data.ok
                                                                ? `${data.imap}\n${data.smtp}`
                                                                : `${data.imap || ''}\n${data.smtp || ''}\n${data.error || ''}`;
                                                            status.style.color = data.ok ? 'var(--success)' : 'var(--error)';
                                                        }
                                                    } catch (e: any) {
                                                        if (status) { status.textContent = `Error: ${e.message}`; status.style.color = 'var(--error)'; }
                                                    } finally {
                                                        if (btn) { btn.textContent = 'Test Connection'; (btn as HTMLButtonElement).disabled = false; }
                                                    }
                                                }}
                                                id="email-test-btn"
                                            >Test Connection</button>
                                            <div id="email-test-status" style={{ fontSize: '11px', whiteSpace: 'pre-line', minHeight: '16px' }}></div>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div>
                                    <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '4px' }}>Config JSON (Agent Override)</label>
                                    <textarea
                                        className="form-input"
                                        value={configJson}
                                        onChange={e => setConfigJson(e.target.value)}
                                        style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', minHeight: '120px', resize: 'vertical' }}
                                        placeholder='{}'
                                    />
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                        Global default: <code style={{ fontSize: '10px' }}>{JSON.stringify(configTool?.global_config || {}).slice(0, 80)}</code>
                                    </div>
                                </div>
                            )}

                            <div style={{ display: 'flex', gap: '8px', marginTop: '16px', justifyContent: 'flex-end' }}>
                                {configTool && configTool.agent_config && Object.keys(configTool.agent_config || {}).length > 0 && (
                                    <button className="btn btn-ghost" style={{ color: 'var(--error)', marginRight: 'auto' }} onClick={async () => {
                                        const token = localStorage.getItem('token');
                                        await fetch(`/api/tools/agents/${agentId}/tool-config/${configTool.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ config: {} }) });
                                        setConfigTool(null); loadTools();
                                    }}>Reset to Global</button>
                                )}
                                {isCat && (
                                    <button
                                        className="btn btn-secondary"
                                        style={{ marginRight: 'auto' }}
                                        onClick={async () => {
                                            const btn = document.getElementById('cat-test-btn');
                                            if (btn) btn.textContent = 'Testing...';
                                            try {
                                                const token = localStorage.getItem('token');
                                                const res = await fetch(`/api/tools/agents/${agentId}/category-config/${configCategory}/test`, {
                                                    method: 'POST',
                                                    headers: { Authorization: `Bearer ${token}` }
                                                });
                                                const data = await res.json();
                                                alert(data.message || (data.ok ? '✅ Test successful' : '❌ Test failed: ' + data.error));
                                            } catch (e: any) { alert('Test failed: ' + e.message); }
                                            finally { if (btn) btn.textContent = 'Test Connection'; }
                                        }}
                                        id="cat-test-btn"
                                    >Test Connection</button>
                                )}
                                <button className="btn btn-secondary" onClick={() => { setConfigTool(null); setConfigCategory(null); }}>Cancel</button>
                                <button className="btn btn-primary" onClick={saveConfig} disabled={configSaving}>{configSaving ? t('common.saving', 'Saving…') : t('common.save', 'Save')}</button>
                            </div>
                        </div>
                    </div>
                );
            })()}
        </>
    );
}

/** Convert rich schedule JSON to cron expression */
function schedToCron(sched: { freq: string; interval: number; time: string; weekdays?: number[] }): string {
    const [h, m] = (sched.time || '09:00').split(':').map(Number);
    if (sched.freq === 'weekly') {
        const days = (sched.weekdays || [1, 2, 3, 4, 5]).join(',');
        return sched.interval > 1 ? `${m} ${h} * * ${days}` : `${m} ${h} * * ${days}`;
    }
    // daily
    if (sched.interval === 1) return `${m} ${h} * * *`;
    return `${m} ${h} */${sched.interval} * *`;
}

const getRelationOptions = (t: any) => [
    { value: 'supervisor', label: t('agent.detail.supervisor') },
    { value: 'subordinate', label: t('agent.detail.subordinate') },
    { value: 'collaborator', label: t('agent.detail.collaborator') },
    { value: 'peer', label: t('agent.detail.peer') },
    { value: 'mentor', label: t('agent.detail.mentor') },
    { value: 'stakeholder', label: t('agent.detail.stakeholder') },
    { value: 'other', label: t('agent.detail.other') },
];

const getAgentRelationOptions = getRelationOptions;

/** Tiny copy button shown on hover at the bottom of message bubbles */
function CopyMessageButton({ text }: { text: string }) {
    const [copied, setCopied] = React.useState(false);
    const handleCopy = (e: React.MouseEvent) => {
        e.stopPropagation();
        const copySuccess = () => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        };
        
        if (navigator.clipboard && window.isSecureContext) {
            copyToClipboard(text).then(copySuccess).catch(err => console.error('Clipboard API failed', err));
        } else {
            // Fallback for non-HTTPS dev environments
            const textArea = document.createElement("textarea");
            textArea.value = text;
            textArea.style.position = "fixed";  // Avoid scrolling to bottom
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {
                if (document.execCommand('copy')) {
                    copySuccess();
                }
            } catch (err) {
                console.error('Fallback copy failed', err);
            }
            document.body.removeChild(textArea);
        }
    };
    return (
        <button
            onClick={handleCopy}
            title="Copy"
            style={{
                background: 'none', border: 'none', cursor: 'pointer', padding: '2px',
                color: copied ? 'var(--accent-text)' : 'var(--text-tertiary)',
                opacity: copied ? 1 : 0.5, transition: 'opacity .15s, color .15s',
                display: 'inline-flex', alignItems: 'center', verticalAlign: 'middle',
                marginLeft: '6px', flexShrink: 0,
            }}
            onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
            onMouseLeave={e => (e.currentTarget.style.opacity = copied ? '1' : '0.5')}
        >
            {copied ? (
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
            ) : (
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>
            )}
        </button>
    );
}

function fetchAuth<T>(url: string, options?: RequestInit): Promise<T> {
    const token = localStorage.getItem('token');
    return fetch(`/api${url}`, {
        ...options,
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }).then(async (r) => {
        if (!r.ok) {
            const bodyText = await r.text();
            let detail: unknown;
            try {
                detail = bodyText ? JSON.parse(bodyText)?.detail : undefined;
            } catch {
                detail = bodyText;
            }
            const message = typeof detail === 'string'
                ? detail
                : bodyText?.trim() || `HTTP ${r.status}`;
            const error: any = new Error(message);
            error.status = r.status;
            error.detail = detail;
            throw error;
        }
        return r.json();
    });
}

// ── Pulse LED keyframe (shared with Chat.tsx, guarded by ID) ──────────────
const _PULSE_STYLE_ID = 'cw-tool-pulse-style';
if (typeof document !== 'undefined' && !document.getElementById(_PULSE_STYLE_ID)) {
    const _s = document.createElement('style');
    _s.id = _PULSE_STYLE_ID;
    _s.textContent = `
        @keyframes cw-pulse-led {
            0%, 100% { opacity: 1; transform: scale(1); box-shadow: 0 0 0 0 rgba(99,102,241,0.6); }
            50%       { opacity: 0.55; transform: scale(1.5); box-shadow: 0 0 0 4px rgba(99,102,241,0); }
        }
        .cw-running-led { animation: cw-pulse-led 1.4s ease-in-out infinite; }
    `;
    document.head.appendChild(_s);
}


/**
 * AnalysisCard — unified controlled collapsible card for all agent-internal processing.
 *
 * Covers three scenarios:
 *   - Thinking only (no tools): agent reasoned before answering directly
 *   - Tools only: agent called tools without visible thinking
 *   - Thinking + Tools: interleaved thinking and tool calls (most common)
 *
 * CONTROLLED component (expanded + onToggle from parent) to survive WS re-renders.
 */
type AnalysisItem =
    | { type: 'thinking'; content: string }
    | { type: 'tool'; name: string; args: any; status: 'running' | 'done'; result?: string };

function AnalysisCard({
    items, t, expanded, onToggle, isGroupRunning,
}: {
    items: AnalysisItem[];
    t: (k: string) => string;
    expanded: boolean;
    onToggle: () => void;
    /** True when parent isWaiting/isStreaming AND this is the last active group */
    isGroupRunning: boolean;
}) {
    const toolItems = items.filter(i => i.type === 'tool') as Extract<AnalysisItem, { type: 'tool' }>[];
    const thinkingItems = items.filter(i => i.type === 'thinking') as Extract<AnalysisItem, { type: 'thinking' }>[];
    const hasTools = toolItems.length > 0;
    const toolCount = toolItems.length;

    // Check if any tool is still running
    const hasRunningTool = toolItems.some(tc => tc.status === 'running');
    const isRunning = hasRunningTool || isGroupRunning;

    // Last running tool name (displayed in header while active)
    const runningTool = [...toolItems].reverse().find(tc => tc.status === 'running') ?? null;

    // For collapsed thinking-only state: show a one-line preview of thinking content
    const allThinkingText = thinkingItems.map(it => it.content).join(' ').trim();
    const thinkingSummary = allThinkingText.length > 55
        ? allThinkingText.slice(0, 55) + '…'
        : allThinkingText;

    return (
        <div style={{ paddingLeft: '36px', marginBottom: '6px' }}>
            <div style={{
                borderRadius: '8px',
                background: 'rgba(99,102,241,0.06)',
                border: `1px solid ${isRunning ? 'rgba(99,102,241,0.32)' : 'rgba(99,102,241,0.18)'}`,
                fontSize: '12px',
                overflow: 'hidden',
                transition: 'border-color 0.3s ease',
            }}>
                {/* ── Header toggle ── */}
                <button
                    onClick={onToggle}
                    style={{
                        background: 'none', border: 'none', cursor: 'pointer',
                        width: '100%', display: 'flex', alignItems: 'center', gap: '6px',
                        padding: '7px 10px',
                        color: 'var(--accent-text, #818cf8)',
                    }}
                >
                    {/* Status indicator: pulse when running, static green when done */}
                    {isRunning ? (
                        <span className="cw-running-led" style={{
                            display: 'inline-block', width: '6px', height: '6px',
                            borderRadius: '50%', background: '#818cf8', flexShrink: 0,
                        }} />
                    ) : (
                        <span style={{
                            display: 'inline-block', width: '6px', height: '6px',
                            borderRadius: '50%', background: '#22c55e', flexShrink: 0, opacity: 0.85,
                        }} />
                    )}

                    {/* Title + contextual subtitle */}
                    <span style={{ flex: 1, textAlign: 'left', display: 'flex', alignItems: 'center', gap: '5px', minWidth: 0 }}>
                        <span style={{ fontWeight: 500, flexShrink: 0 }}>{t('agent.chat.analysing')}</span>
                        <span style={{ color: 'rgba(99,102,241,0.35)', flexShrink: 0 }}>·</span>
                        {isRunning ? (
                            // Running: show current tool name, or 'Thinking...' if no active tool
                            <span style={{
                                fontFamily: 'var(--font-mono)', fontSize: '11px', color: '#a5b4fc',
                                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                            }}>
                                {runningTool ? runningTool.name : t('agent.chat.thinking')}
                            </span>
                        ) : !hasTools && thinkingSummary ? (
                            // Done + thinking only: single-line preview
                            <span style={{
                                fontSize: '11px', color: 'var(--text-tertiary)',
                                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                            }}>{thinkingSummary}</span>
                        ) : null}
                    </span>

                    {/* Tool count badge (hidden when no tools) */}
                    {toolCount > 0 && (
                        <span style={{
                            background: 'rgba(99,102,241,0.18)', color: '#818cf8',
                            borderRadius: '10px', padding: '1px 7px',
                            fontSize: '10px', fontWeight: 600, flexShrink: 0,
                        }}>{toolCount}</span>
                    )}

                    {/* Expand chevron */}
                    <span style={{
                        fontSize: '10px', color: 'var(--text-tertiary)',
                        transition: 'transform 0.2s', display: 'inline-block',
                        transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
                        flexShrink: 0,
                    }}>▶</span>
                </button>

                {/* ── Collapsed: tool name pills (only when tools present) ── */}
                {!expanded && hasTools && (
                    <div style={{ padding: '0 10px 7px 10px', display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                        {toolItems.map((tc, i) => {
                            const running = tc.status === 'running';
                            return (
                                <span key={i} style={{
                                    background: running ? 'rgba(99,102,241,0.14)' : 'rgba(99,102,241,0.08)',
                                    border: `1px solid ${running ? 'rgba(99,102,241,0.28)' : 'rgba(99,102,241,0.14)'}`,
                                    borderRadius: '4px', padding: '1px 6px',
                                    fontSize: '10px', color: running ? '#818cf8' : '#a5b4fc',
                                    fontFamily: 'var(--font-mono)',
                                    display: 'inline-flex', alignItems: 'center', gap: '4px',
                                }}>
                                    {running && (
                                        <span className="cw-running-led" style={{
                                            display: 'inline-block', width: '4px', height: '4px',
                                            borderRadius: '50%', background: '#818cf8', flexShrink: 0,
                                        }} />
                                    )}
                                    {tc.name}
                                </span>
                            );
                        })}
                    </div>
                )}

                {/* ── Expanded: all items in chronological order ── */}
                {expanded && (
                    <div style={{ borderTop: '1px solid rgba(99,102,241,0.15)' }}>
                        {items.map((item, idx) => {
                            if (item.type === 'thinking') {
                                // Thinking block: plain italic text, scrollable
                                return (
                                    <div key={idx} style={{
                                        padding: '8px 12px',
                                        borderBottom: idx < items.length - 1 ? '1px solid rgba(99,102,241,0.08)' : 'none',
                                        fontSize: '11px', lineHeight: '1.7',
                                        color: 'var(--text-tertiary)',
                                        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                        maxHeight: '200px', overflowY: 'auto',
                                        fontStyle: 'italic',
                                        background: 'rgba(147,130,220,0.04)',
                                    }}>
                                        {item.content}
                                    </div>
                                );
                            }

                            // Tool row: native <details> for per-item collapse
                            const tc = item;
                            const running = tc.status === 'running';
                            const argsStr = tc.args && Object.keys(tc.args).length > 0
                                ? JSON.stringify(tc.args, null, 2) : '';
                            const hasDetail = !!(argsStr || tc.result);
                            return (
                                <details
                                    key={idx}
                                    open={running}
                                    style={{
                                        borderBottom: idx < items.length - 1 ? '1px solid rgba(99,102,241,0.10)' : 'none',
                                    }}
                                >
                                    <summary style={{
                                        padding: '7px 10px',
                                        display: 'flex', alignItems: 'center', gap: '5px',
                                        cursor: hasDetail ? 'pointer' : 'default',
                                        listStyle: 'none', userSelect: 'none',
                                    }}>
                                        <span
                                            className={running ? 'cw-running-led' : undefined}
                                            style={{
                                                display: 'inline-block', width: '5px', height: '5px',
                                                borderRadius: '50%',
                                                background: running ? '#f59e0b' : '#22c55e',
                                                flexShrink: 0,
                                            }}
                                        />
                                        <span style={{
                                            fontFamily: 'var(--font-mono)', fontSize: '11px',
                                            color: '#818cf8', fontWeight: 600, flex: 1,
                                        }}>{tc.name}</span>
                                        {running && (
                                            <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                                                {t('common.loading')}
                                            </span>
                                        )}
                                        {hasDetail && <span style={{ fontSize: '9px', color: 'var(--text-tertiary)' }}>▶</span>}
                                    </summary>
                                    {hasDetail && (
                                        <div style={{ padding: '0 10px 8px 20px' }}>
                                            {argsStr && (
                                                <div style={{
                                                    fontFamily: 'var(--font-mono)', fontSize: '10px',
                                                    color: 'var(--text-tertiary)', whiteSpace: 'pre-wrap',
                                                    wordBreak: 'break-all', maxHeight: '80px', overflowY: 'auto',
                                                    background: 'rgba(0,0,0,0.12)', borderRadius: '4px',
                                                    padding: '4px 6px', marginBottom: tc.result ? '4px' : 0,
                                                }}>{argsStr}</div>
                                            )}
                                            {tc.result && (
                                                <div style={{
                                                    fontSize: '10px', color: 'var(--text-secondary)',
                                                    whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                                                    maxHeight: '120px', overflowY: 'auto',
                                                    borderTop: argsStr ? '1px solid rgba(99,102,241,0.10)' : 'none',
                                                    paddingTop: argsStr ? '4px' : 0,
                                                }}>
                                                    {tc.result.length > 500 ? tc.result.slice(0, 500) + '…' : tc.result}
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </details>
                            );
                        })}
                    </div>
                )}
            </div>
        </div>
    );
}








function RelationshipEditor({ agentId, readOnly = false }: { agentId: string; readOnly?: boolean }) {
    const { t, i18n } = useTranslation();
    const isChinese = i18n.language?.startsWith('zh');
    const humanSearchRef = useRef<HTMLDivElement>(null);
    const agentSearchRef = useRef<HTMLDivElement>(null);
    const getHumanMemberSourceLabel = useCallback((member: any) => {
        if (member?.provider_name) return member.provider_name;
        return isChinese ? '平台用户' : 'Platform User';
    }, [isChinese]);

    const renderHumanMemberSourceBadge = useCallback((member: any) => {
        const isPlatformUser = !member?.provider_name;
        return (
            <span
                style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    padding: '1px 6px',
                    borderRadius: '999px',
                    fontSize: '10px',
                    fontWeight: 600,
                    marginRight: '6px',
                    background: isPlatformUser ? 'rgba(99,102,241,0.10)' : 'rgba(16,185,129,0.10)',
                    color: isPlatformUser ? 'rgb(79,70,229)' : 'rgb(16,185,129)',
                    border: isPlatformUser ? '1px solid rgba(99,102,241,0.18)' : '1px solid rgba(16,185,129,0.18)',
                }}
            >
                {getHumanMemberSourceLabel(member)}
            </span>
        );
    }, [getHumanMemberSourceLabel]);

    const [search, setSearch] = useState('');
    const [showHumanForm, setShowHumanForm] = useState(false);
    const [searchResults, setSearchResults] = useState<any[]>([]);
    const [showMemberDropdown, setShowMemberDropdown] = useState(false);
    const [selectedMembers, setSelectedMembers] = useState<any[]>([]);
    const [relation, setRelation] = useState('collaborator');
    const [description, setDescription] = useState('');
    const [agentSearch, setAgentSearch] = useState('');
    const [showAgentForm, setShowAgentForm] = useState(false);
    const [agentSearchResults, setAgentSearchResults] = useState<any[]>([]);
    const [showAgentDropdown, setShowAgentDropdown] = useState(false);
    const [selectedAgents, setSelectedAgents] = useState<any[]>([]);
    const [agentRelation, setAgentRelation] = useState('collaborator');
    const [agentDescription, setAgentDescription] = useState('');
    const [editingId, setEditingId] = useState<string | null>(null);
    const [editRelation, setEditRelation] = useState('');
    const [editDescription, setEditDescription] = useState('');
    const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
    const [editAgentRelation, setEditAgentRelation] = useState('');
    const [editAgentDescription, setEditAgentDescription] = useState('');
    const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set());

    const { data: relationships = [], refetch } = useQuery({
        queryKey: ['relationships', agentId],
        queryFn: () => fetchAuth<any[]>(`/agents/${agentId}/relationships/`),
    });
    const { data: agentRelationships = [], refetch: refetchAgentRels } = useQuery({
        queryKey: ['agent-relationships', agentId],
        queryFn: () => fetchAuth<any[]>(`/agents/${agentId}/relationships/agents`),
    });

    const relatedMemberIds = useMemo(() => new Set(relationships.map((r: any) => r.member_id)), [relationships]);
    const relatedAgentIds = useMemo(() => new Set(agentRelationships.map((r: any) => r.target_agent_id)), [agentRelationships]);
    const selectedMemberIds = useMemo(() => new Set(selectedMembers.map((m: any) => m.id)), [selectedMembers]);
    const selectedAgentIds = useMemo(() => new Set(selectedAgents.map((a: any) => a.id)), [selectedAgents]);

    const visibleMemberResults = useMemo(
        () => searchResults.filter((m: any) => !relatedMemberIds.has(m.id)),
        [searchResults, relatedMemberIds],
    );
    const visibleAgentResults = useMemo(
        () => agentSearchResults.filter((a: any) => !relatedAgentIds.has(a.id)),
        [agentSearchResults, relatedAgentIds],
    );

    const loadOrgMembers = async (keyword = '') => {
        const query = keyword.trim() ? `?search=${encodeURIComponent(keyword.trim())}` : '';
        const results = await fetchAuth<any[]>(`/enterprise/org/members${query}`);
        setSearchResults(results);
    };

    const loadAgentCandidates = async (keyword = '') => {
        const query = keyword.trim() ? `?search=${encodeURIComponent(keyword.trim())}` : '';
        const results = await fetchAuth<any[]>(`/agents/${agentId}/relationships/agent-candidates${query}`);
        setAgentSearchResults(results);
    };

    useEffect(() => {
        if (!search || search.length < 1) { setSearchResults([]); return; }
        const timer = setTimeout(() => {
            loadOrgMembers(search);
        }, 300);
        return () => clearTimeout(timer);
    }, [search]);

    useEffect(() => {
        if (!agentSearch || agentSearch.length < 1) { setAgentSearchResults([]); return; }
        const timer = setTimeout(() => {
            loadAgentCandidates(agentSearch);
        }, 300);
        return () => clearTimeout(timer);
    }, [agentId, agentSearch]);

    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            const target = e.target as Node;
            if (showMemberDropdown && humanSearchRef.current && !humanSearchRef.current.contains(target)) {
                setShowMemberDropdown(false);
            }
            if (showAgentDropdown && agentSearchRef.current && !agentSearchRef.current.contains(target)) {
                setShowAgentDropdown(false);
            }
        };
        if (showMemberDropdown || showAgentDropdown) {
            document.addEventListener('mousedown', handleClickOutside);
        }
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [showMemberDropdown, showAgentDropdown]);

    const resetHumanDraft = () => {
        setShowHumanForm(false);
        setSearch('');
        setSearchResults([]);
        setShowMemberDropdown(false);
        setSelectedMembers([]);
        setRelation('collaborator');
        setDescription('');
    };

    const resetAgentDraft = () => {
        setShowAgentForm(false);
        setAgentSearch('');
        setAgentSearchResults([]);
        setShowAgentDropdown(false);
        setSelectedAgents([]);
        setAgentRelation('collaborator');
        setAgentDescription('');
    };

    const toggleMemberSelection = (member: any) => {
        setSelectedMembers(prev =>
            prev.some((item: any) => item.id === member.id)
                ? prev.filter((item: any) => item.id !== member.id)
                : [...prev, member]
        );
    };

    const toggleAgentSelection = (agent: any) => {
        setSelectedAgents(prev =>
            prev.some((item: any) => item.id === agent.id)
                ? prev.filter((item: any) => item.id !== agent.id)
                : [...prev, agent]
        );
    };

    const addRelationship = async () => {
        if (!selectedMembers.length) return;
        const existing = new Map(
            relationships.map((r: any) => [r.member_id, { member_id: r.member_id, relation: r.relation, description: r.description }])
        );
        selectedMembers.forEach((member: any) => {
            existing.set(member.id, { member_id: member.id, relation, description });
        });
        await fetchAuth(`/agents/${agentId}/relationships/`, { method: 'PUT', body: JSON.stringify({ relationships: Array.from(existing.values()) }) });
        resetHumanDraft();
        refetch();
    };

    const removeRelationship = async (relId: string) => {
        setDeletingIds(prev => new Set(prev).add(relId));
        try {
            await fetchAuth(`/agents/${agentId}/relationships/${relId}`, { method: 'DELETE' });
            refetch();
        } catch {
            setDeletingIds(prev => { const s = new Set(prev); s.delete(relId); return s; });
            refetch();
        } finally {
            setDeletingIds(prev => { const s = new Set(prev); s.delete(relId); return s; });
        }
    };

    const startEditRelationship = (r: any) => {
        setEditingId(r.id);
        setEditRelation(r.relation || 'collaborator');
        setEditDescription(r.description || '');
    };

    const saveEditRelationship = async (targetId: string) => {
        const updated = relationships.map((r: any) => ({
            member_id: r.member_id,
            relation: r.id === targetId ? editRelation : r.relation,
            description: r.id === targetId ? editDescription : r.description,
        }));
        await fetchAuth(`/agents/${agentId}/relationships/`, { method: 'PUT', body: JSON.stringify({ relationships: updated }) });
        setEditingId(null);
        refetch();
    };

    const addAgentRelationship = async () => {
        if (!selectedAgents.length) return;
        const existing = new Map(
            agentRelationships.map((r: any) => [r.target_agent_id, { target_agent_id: r.target_agent_id, relation: r.relation, description: r.description }])
        );
        selectedAgents.forEach((agent: any) => {
            existing.set(agent.id, { target_agent_id: agent.id, relation: agentRelation, description: agentDescription });
        });
        await fetchAuth(`/agents/${agentId}/relationships/agents`, { method: 'PUT', body: JSON.stringify({ relationships: Array.from(existing.values()) }) });
        resetAgentDraft();
        refetchAgentRels();
    };

    const removeAgentRelationship = async (relId: string) => {
        setDeletingIds(prev => new Set(prev).add(relId));
        try {
            await fetchAuth(`/agents/${agentId}/relationships/agents/${relId}`, { method: 'DELETE' });
            refetchAgentRels();
        } catch {
            setDeletingIds(prev => { const s = new Set(prev); s.delete(relId); return s; });
            refetchAgentRels();
        } finally {
            setDeletingIds(prev => { const s = new Set(prev); s.delete(relId); return s; });
        }
    };

    const startEditAgentRelationship = (r: any) => {
        setEditingAgentId(r.id);
        setEditAgentRelation(r.relation || 'collaborator');
        setEditAgentDescription(r.description || '');
    };

    const saveEditAgentRelationship = async (targetId: string) => {
        const updated = agentRelationships.map((r: any) => ({
            target_agent_id: r.target_agent_id,
            relation: r.id === targetId ? editAgentRelation : r.relation,
            description: r.id === targetId ? editAgentDescription : r.description,
        }));
        await fetchAuth(`/agents/${agentId}/relationships/agents`, { method: 'PUT', body: JSON.stringify({ relationships: updated }) });
        setEditingAgentId(null);
        refetchAgentRels();
    };

    return (
        <div>
            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '12px' }}>{t('agent.detail.humanRelationships')}</h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>{t('agent.detail.humanRelationships')}</p>
                {relationships.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '16px' }}>
                        {relationships.map((r: any) => (
                            <div key={r.id} style={{
                                borderRadius: '8px', border: '1px solid var(--border-subtle)',
                                overflow: 'hidden',
                                opacity: deletingIds.has(r.id) ? 0.4 : 1,
                                transition: 'opacity 0.2s ease',
                                pointerEvents: deletingIds.has(r.id) ? 'none' : 'auto',
                            }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px' }}>
                                    <div style={{ width: '36px', height: '36px', borderRadius: '50%', background: 'rgba(224,238,238,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', fontWeight: 600, flexShrink: 0 }}>{r.member?.name?.[0] || '?'}</div>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ fontWeight: 600, fontSize: '13px' }}>{r.member?.name || '?'} <span className="badge" style={{ fontSize: '10px', marginLeft: '4px' }}>{r.relation_label}</span></div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            {renderHumanMemberSourceBadge(r.member)}
                                            {r.member?.department_path || ''} · {r.member?.email || ''}
                                        </div>
                                        {r.description && editingId !== r.id && <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>{r.description}</div>}
                                    </div>
                                    {!readOnly && editingId !== r.id && (
                                        <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                                            <button className="btn btn-ghost" style={{ fontSize: '12px' }} onClick={() => startEditRelationship(r)}>{t('common.edit', 'Edit')}</button>
                                            <button
                                                className="btn btn-ghost"
                                                style={{ color: deletingIds.has(r.id) ? 'var(--text-tertiary)' : 'var(--error)', fontSize: '12px' }}
                                                disabled={deletingIds.has(r.id)}
                                                onClick={() => removeRelationship(r.id)}
                                            >
                                                {deletingIds.has(r.id) ? t('common.deleting', 'Deleting...') : t('common.delete')}
                                            </button>
                                        </div>
                                    )}
                                </div>
                                {editingId === r.id && (
                                    <div style={{ padding: '0 10px 10px', borderTop: '1px solid var(--border-subtle)', background: 'var(--bg-elevated)' }}>
                                        <div style={{ display: 'flex', gap: '8px', marginTop: '8px', marginBottom: '8px' }}>
                                            <select className="input" value={editRelation} onChange={e => setEditRelation(e.target.value)} style={{ width: '140px', fontSize: '12px' }}>
                                                {getRelationOptions(t).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                                            </select>
                                        </div>
                                        <textarea className="input" value={editDescription} onChange={e => setEditDescription(e.target.value)} rows={2} style={{ fontSize: '12px', resize: 'vertical', marginBottom: '8px', width: '100%' }} placeholder={t('agent.detail.descriptionPlaceholder', 'Description...')} />
                                        <div style={{ display: 'flex', gap: '8px' }}>
                                            <button className="btn btn-primary" style={{ fontSize: '12px' }} onClick={() => saveEditRelationship(r.id)}>{t('common.save', 'Save')}</button>
                                            <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setEditingId(null)}>{t('common.cancel')}</button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                )}
                {!readOnly && !showHumanForm && (
                    <button className="btn btn-secondary" type="button" onClick={() => setShowHumanForm(true)}>
                        {t('agent.detail.addRelationship', 'Add Relationship')}
                    </button>
                )}
                {!readOnly && showHumanForm && (
                    <div
                        style={{ border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '12px', background: 'var(--bg-elevated)' }}
                        onMouseDownCapture={(e) => {
                            const target = e.target as Node;
                            if (humanSearchRef.current && !humanSearchRef.current.contains(target)) {
                                setShowMemberDropdown(false);
                            }
                        }}
                    >
                        <div ref={humanSearchRef} style={{ position: 'relative', marginBottom: '8px' }}>
                            <input
                                className="input"
                                placeholder={t('agent.detail.searchMembers')}
                                value={search}
                                onChange={e => {
                                    setSearch(e.target.value);
                                    setShowMemberDropdown(true);
                                }}
                                onFocus={() => {
                                    setShowMemberDropdown(true);
                                    if (!search.trim() && searchResults.length === 0) {
                                        loadOrgMembers();
                                    }
                                }}
                                style={{ fontSize: '13px' }}
                            />
                            {showMemberDropdown && visibleMemberResults.length > 0 && (
                                <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: '6px', marginTop: '4px', maxHeight: '200px', overflowY: 'auto', zIndex: 10, boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}>
                                    {visibleMemberResults.map((m: any) => {
                                        const checked = selectedMemberIds.has(m.id);
                                        return (
                                            <div key={m.id} style={{ padding: '8px 12px', cursor: 'pointer', fontSize: '13px', borderBottom: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'flex-start', gap: '8px' }}
                                                onClick={() => toggleMemberSelection(m)}
                                                onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-elevated)')}
                                                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                                <input type="checkbox" checked={checked} readOnly style={{ marginTop: '2px' }} />
                                                <div style={{ minWidth: 0, flex: 1 }}>
                                                    <div style={{ fontWeight: 500 }}>{m.name}</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                        {renderHumanMemberSourceBadge(m)}
                                                        {m.department_path} · {m.email}
                                                    </div>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                        {showMemberDropdown && search && visibleMemberResults.length === 0 && (
                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>
                                {t('agent.detail.noSearchResults', 'No available results')}
                            </div>
                        )}
                        {selectedMembers.length > 0 && (
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', marginBottom: '10px' }}>
                                {selectedMembers.map((member: any) => (
                                    <div
                                        key={member.id}
                                        style={{
                                            display: 'inline-flex',
                                            alignItems: 'center',
                                            gap: '8px',
                                            border: '1px solid var(--border-subtle)',
                                            borderRadius: '10px',
                                            padding: '8px 10px',
                                            background: 'var(--bg-primary)',
                                            fontSize: '12px',
                                            lineHeight: 1.2,
                                        }}
                                    >
                                        <div style={{ width: '24px', height: '24px', borderRadius: '50%', background: 'var(--bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '11px', flexShrink: 0 }}>
                                            {member.name?.[0] || '?'}
                                        </div>
                                        <div style={{ minWidth: 0 }}>
                                            <div style={{ fontWeight: 600 }}>{member.name}</div>
                                            <div style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>{member.department_path || member.email || ''}</div>
                                        </div>
                                        <button className="btn btn-ghost" type="button" style={{ fontSize: '12px', padding: 0, minWidth: 'auto', marginLeft: '2px' }} onClick={() => toggleMemberSelection(member)}>×</button>
                                    </div>
                                ))}
                            </div>
                        )}
                        <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
                            <select className="input" value={relation} onChange={e => setRelation(e.target.value)} style={{ width: '160px', fontSize: '12px' }}>
                                {getRelationOptions(t).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                            </select>
                        </div>
                        <textarea className="input" placeholder="" value={description} onChange={e => setDescription(e.target.value)} rows={2} style={{ fontSize: '12px', resize: 'vertical', marginBottom: '8px' }} />
                        <div style={{ display: 'flex', gap: '8px' }}>
                            <button className="btn btn-primary" style={{ fontSize: '12px' }} onClick={addRelationship} disabled={selectedMembers.length === 0}>
                                {t('common.confirm')} {selectedMembers.length > 0 ? `(${selectedMembers.length})` : ''}
                            </button>
                            <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={resetHumanDraft}>
                                {t('common.cancel')}
                            </button>
                        </div>
                    </div>
                )}
            </div>
            <div className="card" style={{ marginBottom: '12px' }}>
                <h4 style={{ marginBottom: '12px' }}>{t('agent.detail.agentRelationships')}</h4>
                <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>{t('agent.detail.agentRelationships')}</p>
                {agentRelationships.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '16px' }}>
                        {agentRelationships.map((r: any) => (
                            <div key={r.id} style={{
                                borderRadius: '8px', border: '1px solid rgba(16,185,129,0.3)',
                                background: 'rgba(16,185,129,0.05)', overflow: 'hidden',
                                opacity: deletingIds.has(r.id) ? 0.4 : 1,
                                transition: 'opacity 0.2s ease',
                                pointerEvents: deletingIds.has(r.id) ? 'none' : 'auto',
                            }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px' }}>
                                    <div style={{ width: '36px', height: '36px', borderRadius: '50%', background: 'rgba(16,185,129,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', flexShrink: 0 }}>A</div>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ fontWeight: 600, fontSize: '13px' }}>{r.target_agent?.name || '?'} <span className="badge" style={{ fontSize: '10px', marginLeft: '4px', background: 'rgba(16,185,129,0.15)', color: 'rgb(16,185,129)' }}>{r.relation_label}</span></div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{r.target_agent?.role_description || 'Agent'}</div>
                                        {r.description && editingAgentId !== r.id && <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>{r.description}</div>}
                                    </div>
                                    {!readOnly && editingAgentId !== r.id && (
                                        <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                                            <button className="btn btn-ghost" style={{ fontSize: '12px' }} onClick={() => startEditAgentRelationship(r)}>{t('common.edit', 'Edit')}</button>
                                            <button
                                                className="btn btn-ghost"
                                                style={{ color: deletingIds.has(r.id) ? 'var(--text-tertiary)' : 'var(--error)', fontSize: '12px' }}
                                                disabled={deletingIds.has(r.id)}
                                                onClick={() => removeAgentRelationship(r.id)}
                                            >
                                                {deletingIds.has(r.id) ? t('common.deleting', 'Deleting...') : t('common.delete')}
                                            </button>
                                        </div>
                                    )}
                                </div>
                                {editingAgentId === r.id && (
                                    <div style={{ padding: '0 10px 10px', borderTop: '1px solid rgba(16,185,129,0.2)', background: 'var(--bg-elevated)' }}>
                                        <div style={{ display: 'flex', gap: '8px', marginTop: '8px', marginBottom: '8px' }}>
                                            <select className="input" value={editAgentRelation} onChange={e => setEditAgentRelation(e.target.value)} style={{ width: '140px', fontSize: '12px' }}>
                                                {getAgentRelationOptions(t).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                                            </select>
                                        </div>
                                        <textarea className="input" value={editAgentDescription} onChange={e => setEditAgentDescription(e.target.value)} rows={2} style={{ fontSize: '12px', resize: 'vertical', marginBottom: '8px', width: '100%' }} placeholder={t('agent.detail.descriptionPlaceholder', 'Description...')} />
                                        <div style={{ display: 'flex', gap: '8px' }}>
                                            <button className="btn btn-primary" style={{ fontSize: '12px' }} onClick={() => saveEditAgentRelationship(r.id)}>{t('common.save', 'Save')}</button>
                                            <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={() => setEditingAgentId(null)}>{t('common.cancel')}</button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                )}
                {!readOnly && !showAgentForm && (
                    <button className="btn btn-secondary" type="button" onClick={() => setShowAgentForm(true)}>
                        {t('agent.detail.addRelationship', 'Add Relationship')}
                    </button>
                )}
                {!readOnly && showAgentForm && (
                    <div
                        style={{ border: '1px solid rgba(16,185,129,0.3)', borderRadius: '8px', padding: '12px', background: 'var(--bg-elevated)' }}
                        onMouseDownCapture={(e) => {
                            const target = e.target as Node;
                            if (agentSearchRef.current && !agentSearchRef.current.contains(target)) {
                                setShowAgentDropdown(false);
                            }
                        }}
                    >
                        <div ref={agentSearchRef} style={{ position: 'relative', marginBottom: '8px' }}>
                            <input
                                className="input"
                                placeholder={t('agent.detail.searchAgents', '搜索可见数字员工...')}
                                value={agentSearch}
                                onChange={e => {
                                    setAgentSearch(e.target.value);
                                    setShowAgentDropdown(true);
                                }}
                                onFocus={() => {
                                    setShowAgentDropdown(true);
                                    if (!agentSearch.trim() && agentSearchResults.length === 0) {
                                        loadAgentCandidates();
                                    }
                                }}
                                style={{ fontSize: '13px' }}
                            />
                            {showAgentDropdown && visibleAgentResults.length > 0 && (
                                <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: '6px', marginTop: '4px', maxHeight: '200px', overflowY: 'auto', zIndex: 10, boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}>
                                    {visibleAgentResults.map((agent: any) => {
                                        const checked = selectedAgentIds.has(agent.id);
                                        return (
                                            <div key={agent.id} style={{ padding: '8px 12px', cursor: 'pointer', fontSize: '13px', borderBottom: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'flex-start', gap: '8px' }}
                                                onClick={() => toggleAgentSelection(agent)}
                                                onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-elevated)')}
                                                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                                <input type="checkbox" checked={checked} readOnly style={{ marginTop: '2px' }} />
                                                <div style={{ minWidth: 0, flex: 1 }}>
                                                    <div style={{ fontWeight: 500 }}>{agent.name}</div>
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{agent.role_description || 'Agent'}</div>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                        {showAgentDropdown && agentSearch && visibleAgentResults.length === 0 && (
                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>
                                {t('agent.detail.noSearchResults', 'No available results')}
                            </div>
                        )}
                        {selectedAgents.length > 0 && (
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', marginBottom: '10px' }}>
                                {selectedAgents.map((agent: any) => (
                                    <div
                                        key={agent.id}
                                        style={{
                                            display: 'inline-flex',
                                            alignItems: 'center',
                                            gap: '8px',
                                            border: '1px solid rgba(16,185,129,0.24)',
                                            borderRadius: '10px',
                                            padding: '8px 10px',
                                            background: 'var(--bg-primary)',
                                            fontSize: '12px',
                                            lineHeight: 1.2,
                                        }}
                                    >
                                        <div style={{ width: '24px', height: '24px', borderRadius: '50%', background: 'rgba(16,185,129,0.12)', color: 'rgb(16,185,129)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '11px', flexShrink: 0 }}>
                                            {agent.name?.[0] || 'A'}
                                        </div>
                                        <div style={{ minWidth: 0 }}>
                                            <div style={{ fontWeight: 600 }}>{agent.name}</div>
                                            <div style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>{agent.role_description || 'Agent'}</div>
                                        </div>
                                        <button className="btn btn-ghost" type="button" style={{ fontSize: '12px', padding: 0, minWidth: 'auto', marginLeft: '2px' }} onClick={() => toggleAgentSelection(agent)}>×</button>
                                    </div>
                                ))}
                            </div>
                        )}
                        <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
                            <select className="input" value={agentRelation} onChange={e => setAgentRelation(e.target.value)} style={{ width: '160px', flexShrink: 0, fontSize: '12px' }}>
                                {getAgentRelationOptions(t).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
                            </select>
                        </div>
                        <textarea className="input" placeholder="" value={agentDescription} onChange={e => setAgentDescription(e.target.value)} rows={2} style={{ fontSize: '12px', resize: 'vertical', marginBottom: '8px' }} />
                        <div style={{ display: 'flex', gap: '8px' }}>
                            <button className="btn btn-primary" style={{ fontSize: '12px' }} onClick={addAgentRelationship} disabled={selectedAgents.length === 0}>
                                {t('common.confirm')} {selectedAgents.length > 0 ? `(${selectedAgents.length})` : ''}
                            </button>
                            <button className="btn btn-secondary" style={{ fontSize: '12px' }} onClick={resetAgentDraft}>
                                {t('common.cancel')}
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

function AgentDetailInner() {
    const { t, i18n } = useTranslation();
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const location = useLocation();
    const validTabs = ['status', 'aware', 'mind', 'tools', 'skills', 'relationships', 'workspace', 'chat', 'activityLog', 'approvals', 'settings'];
    const hashTab = location.hash?.replace('#', '');
    const [activeTab, setActiveTabRaw] = useState<string>(hashTab && validTabs.includes(hashTab) ? hashTab : 'status');

    // Sync URL hash when tab changes
    const setActiveTab = (tab: string) => {
        setActiveTabRaw(tab);
        window.history.replaceState(null, '', `#${tab}`);
    };

    const { data: agent, isLoading } = useQuery({
        queryKey: ['agent', id],
        queryFn: () => agentApi.get(id!),
        enabled: !!id,
    });

    // ── Aware tab data: triggers ──
    const { data: awareTriggers = [], refetch: refetchTriggers } = useQuery({
        queryKey: ['triggers', id],
        queryFn: () => triggerApi.list(id!),
        enabled: !!id && activeTab === 'aware',
        refetchInterval: activeTab === 'aware' ? 5000 : false,
    });

    // ── Aware tab data: focus.md ──
    const { data: focusFile } = useQuery({
        queryKey: ['file', id, 'focus.md'],
        queryFn: () => fileApi.read(id!, 'focus.md').catch(() => null),
        enabled: !!id && activeTab === 'aware',
    });

    // ── Aware tab data: task_history.md ──
    const { data: taskHistoryFile } = useQuery({
        queryKey: ['file', id, 'task_history.md'],
        queryFn: () => fileApi.read(id!, 'task_history.md').catch(() => null),
        enabled: !!id && activeTab === 'aware',
    });

    // ── Aware tab data: reflection sessions (trigger monologues) ──
    const { data: reflectionSessions = [] } = useQuery({
        queryKey: ['reflection-sessions', id],
        queryFn: async () => {
            const tkn = localStorage.getItem('token');
            const res = await fetch(`/api/agents/${id}/sessions?scope=all`, { headers: { Authorization: `Bearer ${tkn}` } });
            if (!res.ok) return [];
            const all = await res.json();
            return all.filter((s: any) => s.source_channel === 'trigger');
        },
        enabled: !!id && activeTab === 'aware',
        refetchInterval: activeTab === 'aware' ? 10000 : false,
    });

    // ── Aware tab state ──
    const [expandedFocus, setExpandedFocus] = useState<string | null>(null);
    const [expandedReflection, setExpandedReflection] = useState<string | null>(null);
    const [reflectionMessages, setReflectionMessages] = useState<Record<string, any[]>>({});
    const [showAllFocus, setShowAllFocus] = useState(false);
    const [showCompletedFocus, setShowCompletedFocus] = useState(false);
    const [showAllTriggers, setShowAllTriggers] = useState(false);
    const [showAllReflections, setShowAllReflections] = useState(false);
    const [reflectionPage, setReflectionPage] = useState(0);
    const REFLECTIONS_PAGE_SIZE = 10;
    const SECTION_PAGE_SIZE = 5;

    const { data: soulContent } = useQuery({
        queryKey: ['file', id, 'soul.md'],
        queryFn: () => fileApi.read(id!, 'soul.md'),
        enabled: !!id && activeTab === 'mind',
    });

    const { data: memoryFiles = [] } = useQuery({
        queryKey: ['files', id, 'memory'],
        queryFn: () => fileApi.list(id!, 'memory'),
        enabled: !!id && activeTab === 'mind',
    });
    const [expandedMemory, setExpandedMemory] = useState<string | null>(null);
    const { data: memoryFileContent } = useQuery({
        queryKey: ['file', id, expandedMemory],
        queryFn: () => fileApi.read(id!, expandedMemory!),
        enabled: !!id && !!expandedMemory,
    });

    const { data: skillFiles = [] } = useQuery({
        queryKey: ['files', id, 'skills'],
        queryFn: () => fileApi.list(id!, 'skills'),
        enabled: !!id && activeTab === 'skills',
    });

    const [workspacePath, setWorkspacePath] = useState('workspace');
    const { data: workspaceFiles = [] } = useQuery({
        queryKey: ['files', id, workspacePath],
        queryFn: () => fileApi.list(id!, workspacePath),
        enabled: !!id && activeTab === 'workspace',
    });

    const { data: activityLogs = [] } = useQuery({
        queryKey: ['activity', id],
        queryFn: () => activityApi.list(id!, 100),
        enabled: !!id && (activeTab === 'activityLog' || activeTab === 'status'),
        refetchInterval: activeTab === 'activityLog' ? 10000 : false,
    });

    // Chat history
    // ── Session state (replaces old conversations query) ──────────────────
    const [sessions, setSessions] = useState<any[]>([]);
    const [allSessions, setAllSessions] = useState<any[]>([]);
    const [activeSession, setActiveSession] = useState<any | null>(null);
    const [chatScope, setChatScope] = useState<'mine' | 'all'>('mine');
    const [allUserFilter, setAllUserFilter] = useState<string>('');  // filter by username in All Users
    const [historyMsgs, setHistoryMsgs] = useState<any[]>([]);
    const [sessionsLoading, setSessionsLoading] = useState(false);
    const [allSessionsLoading, setAllSessionsLoading] = useState(false);
    const [agentExpired, setAgentExpired] = useState(false);
    // Websocket chat state (for 'me' conversation)
    const token = useAuthStore((s) => s.token);
    const currentUser = useAuthStore((s) => s.user);
    const isAdmin = currentUser?.role === 'platform_admin' || currentUser?.role === 'org_admin';
    const isAgentOwner =
        currentUser?.id != null &&
        (agent as any)?.creator_id != null &&
        String((agent as any).creator_id) === String(currentUser.id);
    /** Chat sidebar: who may list all sessions & read others' threads (matches backend scope=all). */
    const canViewAllAgentChatSessions =
        currentUser?.role === 'platform_admin' ||
        currentUser?.role === 'org_admin' ||
        currentUser?.role === 'agent_admin' ||
        isAgentOwner;
    type SessionRuntimeKey = string;
    const wsMapRef = useRef<Record<SessionRuntimeKey, WebSocket>>({});
    const reconnectTimerRef = useRef<Record<SessionRuntimeKey, ReturnType<typeof setTimeout> | null>>({});
    const reconnectDisabledRef = useRef<Record<SessionRuntimeKey, boolean>>({});
    const sessionUiStateRef = useRef<Record<SessionRuntimeKey, { isWaiting: boolean; isStreaming: boolean }>>({});
    const activeSessionIdRef = useRef<string | null>(null);
    const currentAgentIdRef = useRef<string | undefined>(id);
    const sessionMsgAbortRef = useRef<AbortController | null>(null);
    const sessionLoadSeqRef = useRef(0);

    const buildSessionRuntimeKey = (agentId: string, sessionId: string) => `${agentId}:${sessionId}`;

    const clearReconnectTimer = (key: SessionRuntimeKey) => {
        const timer = reconnectTimerRef.current[key];
        if (timer) {
            clearTimeout(timer);
            reconnectTimerRef.current[key] = null;
        }
    };

    const closeSessionSocket = (key: SessionRuntimeKey, disableReconnect = true) => {
        if (disableReconnect) reconnectDisabledRef.current[key] = true;
        clearReconnectTimer(key);
        const ws = wsMapRef.current[key];
        if (ws && ws.readyState !== WebSocket.CLOSED) ws.close();
        delete wsMapRef.current[key];
        delete sessionUiStateRef.current[key];
    };

    const setSessionUiState = (key: SessionRuntimeKey, next: Partial<{ isWaiting: boolean; isStreaming: boolean }>) => {
        const prev = sessionUiStateRef.current[key] || { isWaiting: false, isStreaming: false };
        sessionUiStateRef.current[key] = { ...prev, ...next };
    };

    /** Normalize IDs — API/JSON may use number vs string; loose equality was breaking "own session" detection. */
    const sessionUserIdStr = (s: any) => (s?.user_id == null ? '' : String(s.user_id));
    const viewerUserIdStr = () => (currentUser?.id == null ? '' : String(currentUser.id));

    /** Ensure session shape from POST/list so P2P "mine" is never mistaken for read-only or agent thread. */
    const normalizeChatSession = (sess: any) => {
        if (!sess || typeof sess !== 'object') return sess;
        const vu = viewerUserIdStr();
        const rawUid =
            sess.user_id != null && String(sess.user_id).trim() !== '' ? String(sess.user_id) : vu;
        return {
            ...sess,
            id: String(sess.id),
            agent_id: sess.agent_id != null ? String(sess.agent_id) : sess.agent_id,
            user_id: rawUid,
            unread_count: Number(sess.unread_count || 0),
            is_primary: Boolean(sess.is_primary),
            source_channel:
                typeof sess.source_channel === 'string' && sess.source_channel.trim()
                    ? sess.source_channel
                    : 'web',
            participant_type:
                typeof sess.participant_type === 'string' && sess.participant_type.trim()
                    ? sess.participant_type
                    : 'user',
            is_group: Boolean(sess.is_group),
        };
    };

    const clearUnreadForSession = (sessionId?: string | null) => {
        if (!sessionId) return;
        const sid = String(sessionId);
        setSessions(prev => prev.map((item: any) => String(item.id) === sid ? { ...item, unread_count: 0 } : item));
        setAllSessions(prev => prev.map((item: any) => String(item.id) === sid ? { ...item, unread_count: 0 } : item));
        setActiveSession((prev: any) => prev && String(prev.id) === sid ? { ...prev, unread_count: 0 } : prev);
    };

    const isWritableSession = (sess: any, scopeOverride: 'mine' | 'all' = chatScope) => {
        if (!sess) return false;
        const sc = String(sess.source_channel || 'web').toLowerCase();
        const pt = String(sess.participant_type || 'user').toLowerCase();
        if (sc === 'agent' || pt === 'agent') return false;
        if (sess.is_group) return false;
        if (canViewAllAgentChatSessions && scopeOverride === 'all') return false;
        const su = sessionUserIdStr(sess);
        const vu = viewerUserIdStr();
        if (su && vu && su !== vu) return false;
        return true;
    };

    const isViewingOtherUsersSessions = canViewAllAgentChatSessions && chatScope === 'all';

    /** Sessions in scope=all that are not the current viewer's own P2P rows (for admin「其他用户」tab).
     *  Agent-to-agent sessions (source_channel === 'agent') store the creator's user_id, so we must
     *  exempt them from the user_id check — otherwise they'd always be hidden. */
    const otherUsersSessions = useMemo(() => {
        const vu = viewerUserIdStr();
        return allSessions.filter((s: any) => {
            // Always show agent-to-agent sessions in the "Other users" tab
            if (String(s.source_channel || '').toLowerCase() === 'agent') return true;
            const su = sessionUserIdStr(s);
            if (vu && su === vu) return false;
            return true;
        });
    }, [allSessions, currentUser?.id]);

    const otherUserPickerOptions = useMemo(() => {
        const m = new Map<string, string>();
        for (const s of otherUsersSessions) {
            const uid = sessionUserIdStr(s);
            if (!uid) continue;
            if (!m.has(uid)) m.set(uid, String(s.username || s.user_id || uid));
        }
        return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]));
    }, [otherUsersSessions]);

    const othersListForPicker = useMemo(() => {
        if (!allUserFilter) return otherUsersSessions;
        return otherUsersSessions.filter((s: any) => sessionUserIdStr(s) === allUserFilter);
    }, [otherUsersSessions, allUserFilter]);

    useEffect(() => {
        if (!canViewAllAgentChatSessions && chatScope === 'all') setChatScope('mine');
    }, [canViewAllAgentChatSessions, chatScope]);

    const clearChatSelection = () => {
        activeSessionIdRef.current = null;
        setActiveSession(null);
        setChatMessages([]);
        setHistoryMsgs([]);
        setWsConnected(false);
        setIsStreaming(false);
        setIsWaiting(false);
    };

    const onAdminTabMine = () => {
        setChatScope('mine');
        setAllUserFilter('');
        if (activeSession && sessionUserIdStr(activeSession) !== viewerUserIdStr()) clearChatSelection();
    };

    const onAdminTabOthers = () => {
        setChatScope('all');
        setAllUserFilter('');
        fetchAllSessions();
        if (activeSession && sessionUserIdStr(activeSession) === viewerUserIdStr()) clearChatSelection();
    };
    const syncActiveSocketState = (sess: any | null = activeSession, agentId: string | undefined = id) => {
        if (!sess || !agentId) {
            wsRef.current = null;
            setWsConnected(false);
            return;
        }
        const key = buildSessionRuntimeKey(agentId, sess.id);
        const ws = wsMapRef.current[key];
        wsRef.current = ws ?? null;
        setWsConnected(!!ws && ws.readyState === WebSocket.OPEN);
    };

    const fetchMySessions = async (silent = false, agentId: string | undefined = id) => {
        if (!agentId) return [];
        if (!silent && currentAgentIdRef.current === agentId) setSessionsLoading(true);
        try {
            const tkn = localStorage.getItem('token');
            const res = await fetch(`/api/agents/${agentId}/sessions?scope=mine`, { headers: { Authorization: `Bearer ${tkn}` } });
            if (res.ok) {
                const data = (await res.json()).map((row: any) => normalizeChatSession(row));
                if (currentAgentIdRef.current === agentId) setSessions(data);
                if (!silent && currentAgentIdRef.current === agentId) setSessionsLoading(false);
                return data;
            }
        } catch { }
        if (!silent && currentAgentIdRef.current === agentId) setSessionsLoading(false);
        return [];
    };

    const fetchAllSessions = async () => {
        if (!id || !canViewAllAgentChatSessions) return;
        setAllSessionsLoading(true);
        try {
            const tkn = localStorage.getItem('token');
            const res = await fetch(`/api/agents/${id}/sessions?scope=all`, { headers: { Authorization: `Bearer ${tkn}` } });
            if (!currentAgentIdRef.current || currentAgentIdRef.current !== id) return;
            if (res.ok) {
                const all = (await res.json())
                    .filter((s: any) => String(s.source_channel || 'direct').toLowerCase() !== 'trigger')
                    .map((row: any) => normalizeChatSession(row));
                setAllSessions(all);
            } else {
                setAllSessions([]);
                if (res.status === 403) {
                    console.warn('[chat] scope=all sessions forbidden (need org/platform/agent admin)');
                }
            }
        } catch {
            if (currentAgentIdRef.current === id) setAllSessions([]);
        } finally {
            setAllSessionsLoading(false);
        }
    };

    const selectSession = async (rawSess: any, scopeOverride: 'mine' | 'all' = chatScope) => {
        const sess = normalizeChatSession(rawSess);
        const targetAgentId = id;
        if (!targetAgentId) return;
        const runtimeKey = buildSessionRuntimeKey(targetAgentId, String(sess.id));
        const runtimeState = sessionUiStateRef.current[runtimeKey] || { isWaiting: false, isStreaming: false };
        activeSessionIdRef.current = sess.id;
        setChatMessages([]);
        setHistoryMsgs([]);
        setIsStreaming(runtimeState.isStreaming);
        setIsWaiting(runtimeState.isWaiting);
        setActiveSession(sess);
        setAgentExpired(false);
        syncActiveSocketState(sess, targetAgentId);

        // Abort any pending message load and increment sequence
        sessionMsgAbortRef.current?.abort();
        const controller = new AbortController();
        sessionMsgAbortRef.current = controller;
        const loadSeq = ++sessionLoadSeqRef.current;
        try {
            const tkn = localStorage.getItem('token');
            const res = await fetch(`/api/agents/${targetAgentId}/sessions/${sess.id}/messages`, {
                headers: { Authorization: `Bearer ${tkn}` },
                signal: controller.signal,
            });
            if (!res.ok) return;
            const msgs = await res.json();
            if (controller.signal.aborted || loadSeq !== sessionLoadSeqRef.current) return;
            if (currentAgentIdRef.current !== targetAgentId) return;
            if (activeSessionIdRef.current !== sess.id) return;
            const preParsed = msgs.map((m: any) => parseChatMsg({
                role: m.role, content: m.content || '',
                ...(m.toolName && { toolName: m.toolName, toolArgs: m.toolArgs, toolStatus: m.toolStatus, toolResult: m.toolResult }),
                ...(m.thinking && { thinking: m.thinking }),
                ...(m.created_at && { timestamp: m.created_at }),
                ...(m.id && { id: m.id }),
            }));

            if (isWritableSession(sess, scopeOverride)) {
                setChatMessages(preParsed);
            } else {
                setHistoryMsgs(preParsed);
            }
            // The backend marks the session as read when the current user opens it. Mirror that
            // immediately in local state so unread badges clear without waiting for the next poll.
            clearUnreadForSession(String(sess.id));
            queryClient.invalidateQueries({ queryKey: ['agents'] });
        } catch (err: any) {
            if (err?.name === 'AbortError') return;
            console.error('Failed to load session messages:', err);
        }
    };

    const createNewSession = async () => {
        if (!id) return;
        try {
            const tkn = localStorage.getItem('token');
            const res = await fetch(`/api/agents/${id}/sessions`, {
                method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tkn}` },
                body: JSON.stringify({}),
            });
            if (res.ok) {
                const newSess = normalizeChatSession(await res.json());
                setChatScope('mine');
                setSessions((prev) => [newSess, ...prev]);
                setIsStreaming(false);
                setIsWaiting(false);
                await selectSession(newSess, 'mine');
            } else {
                const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
                console.error('Failed to create session:', err);
                alert(`Failed to create session: ${err.detail || res.status}`);
            }
        } catch (err: any) {
            console.error('Failed to create session:', err);
            alert(`Failed to create session: ${err.message || err}`);
        }
    };

    const deleteSession = async (sessionId: string) => {
        if (!confirm(t('chat.deleteConfirm', 'Delete this session and all its messages? This cannot be undone.'))) return;
        const tkn = localStorage.getItem('token');
        try {
            await fetch(`/api/agents/${id}/sessions/${sessionId}`, { method: 'DELETE', headers: { Authorization: `Bearer ${tkn}` } });
            if (id) closeSessionSocket(buildSessionRuntimeKey(id, sessionId), true);
            // If deleted the active session, clear it
            if (activeSession?.id === sessionId) {
                activeSessionIdRef.current = null;
                setActiveSession(null);
                setChatMessages([]);
                setHistoryMsgs([]);
                setWsConnected(false);
                setIsStreaming(false);
                setIsWaiting(false);
            }
            await fetchMySessions(false, id);
            if (canViewAllAgentChatSessions) await fetchAllSessions();
        } catch (e: any) {
            alert(e.message || 'Delete failed');
        }
    };

    // Expiry editor modal state
    const [showExpiryModal, setShowExpiryModal] = useState(false);
    const [expiryValue, setExpiryValue] = useState('');       // datetime-local string or ''
    const [expirySaving, setExpirySaving] = useState(false);

    const openExpiryModal = () => {
        const cur = (agent as any)?.expires_at;
        // Convert ISO to datetime-local format (YYYY-MM-DDTHH:MM)
        setExpiryValue(cur ? new Date(cur).toISOString().slice(0, 16) : '');
        setShowExpiryModal(true);
    };

    const addHours = (h: number) => {
        const base = (agent as any)?.expires_at ? new Date((agent as any).expires_at) : new Date();
        const next = new Date(base.getTime() + h * 3600_000);
        setExpiryValue(next.toISOString().slice(0, 16));
    };

    const saveExpiry = async (permanent = false) => {
        setExpirySaving(true);
        try {
            const token = localStorage.getItem('token');
            const body = permanent ? { expires_at: null } : { expires_at: expiryValue ? new Date(expiryValue).toISOString() : null };
            await fetch(`/api/agents/${id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify(body),
            });
            queryClient.invalidateQueries({ queryKey: ['agent', id] });
            setShowExpiryModal(false);
        } catch (e) { alert('Failed: ' + e); }
        setExpirySaving(false);
    };
    interface ChatMsg { role: 'user' | 'assistant' | 'tool_call'; content: string; fileName?: string; toolName?: string; toolArgs?: any; toolStatus?: 'running' | 'done'; toolResult?: string; thinking?: string; imageUrl?: string; timestamp?: string; }
    const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);
    // Transient info banner (e.g. fallback model switch notification)
    const [chatInfoMsg, setChatInfoMsg] = useState<string | null>(null);
    const chatInfoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    // Stable expanded-state map for tool groups — keyed by groupStartIndex.
    // Stored in a ref so it survives parent re-renders without causing extra renders.
    const toolGroupExpandedRef = useRef<Map<number, boolean>>(new Map());
    const [toolGroupExpandedVersion, setToolGroupExpandedVersion] = useState(0);
    const toggleToolGroup = (key: number) => {
        const m = toolGroupExpandedRef.current;
        m.set(key, !m.get(key));
        setToolGroupExpandedVersion(v => v + 1); // trigger re-render
    };
    const [liveState, setLiveState] = useState<LivePreviewState>({});
    const [livePanelVisible, setLivePanelVisible] = useState(false);
    const [sidePanelTab, setSidePanelTab] = useState<SidePanelTab>('workspace');
    const [workspaceActivePath, setWorkspaceActivePath] = useState<string | null>(null);
    const [workspaceLockedPath, setWorkspaceLockedPath] = useState<string | null>(null);
    const [workspaceActivities, setWorkspaceActivities] = useState<WorkspaceActivity[]>([]);
    const [workspaceLiveDraft, setWorkspaceLiveDraft] = useState<WorkspaceLiveDraft | null>(null);
    const workspaceEditingRef = useRef(false);
    const workspaceLockedPathRef = useRef<string | null>(null);
    const [wsSessionId, setWsSessionId] = useState<string>('');
    const [sessionListCollapsed, setSessionListCollapsed] = useState(false);
    const [chatInput, setChatInput] = useState('');
    const [wsConnected, setWsConnected] = useState(false);
    const [isWaiting, setIsWaiting] = useState(false);
    const [isStreaming, setIsStreaming] = useState(false);
    const [chatUploadDrafts, setChatUploadDrafts] = useState<{ id: string; name: string; percent: number; previewUrl?: string; sizeBytes: number }[]>([]);
    const chatUploadAbortRef = useRef<Map<string, () => void>>(new Map());
    type AttachedFileRef = { name: string; text: string; path?: string; imageUrl?: string; source?: 'upload' | 'workspace_auto' };
    type PendingChatMessage = {
        runtimeKey: SessionRuntimeKey;
        contentForLLM: string;
        userMsg: string;
        fileName: string;
        imageUrl?: string;
    };
    const [attachedFiles, setAttachedFiles] = useState<AttachedFileRef[]>([]);
    const dismissedWorkspaceRefPath = useRef<string | null>(null);
    const pendingChatSendRef = useRef<PendingChatMessage | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const chatEndRef = useRef<HTMLDivElement>(null);
    const chatContainerRef = useRef<HTMLDivElement>(null);
    const chatInputRef = useRef<HTMLTextAreaElement>(null);
    const chatInputAreaRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const workspacePreviewLocked = !!workspaceLockedPath;
    useEffect(() => {
        workspaceLockedPathRef.current = workspaceLockedPath;
    }, [workspaceLockedPath]);
    const allowWorkspaceAutoSwitch = useCallback((path?: string | null) => {
        if (!path) return false;
        if (workspaceEditingRef.current) return false;
        if (!workspaceLockedPathRef.current) return true;
        return workspaceLockedPathRef.current === path;
    }, []);
    const allowLivePanelAutoFocus = useCallback(() => {
        return !workspaceEditingRef.current && !workspaceLockedPathRef.current;
    }, []);
    const handleWorkspaceSelectPath = useCallback((path: string) => {
        setWorkspaceActivePath(path);
        if (workspaceLockedPath) setWorkspaceLockedPath(path);
    }, [workspaceLockedPath]);
    const handleWorkspaceToggleLock = useCallback(() => {
        setWorkspaceLockedPath((current) => current ? null : workspaceActivePath);
    }, [workspaceActivePath]);
    const handleWorkspaceEditingChange = useCallback((editing: boolean) => {
        workspaceEditingRef.current = editing;
    }, []);

    // Settings form local state
    const [settingsForm, setSettingsForm] = useState({
        primary_model_id: '',
        fallback_model_id: '',
        context_window_size: 100,
        max_tool_rounds: 50,
        max_tokens_per_day: '' as string | number,
        max_tokens_per_month: '' as string | number,
        max_triggers: 20,
        min_poll_interval_min: 5,
        webhook_rate_limit: 5,
    });
    const [settingsSaving, setSettingsSaving] = useState(false);
    const [settingsSaved, setSettingsSaved] = useState(false);
    const [settingsError, setSettingsError] = useState('');
    const settingsInitRef = useRef(false);

    // Sync settings form from server data on load
    useEffect(() => {
        if (agent && !settingsInitRef.current) {
            setSettingsForm({
                primary_model_id: agent.primary_model_id || '',
                fallback_model_id: agent.fallback_model_id || '',
                context_window_size: agent.context_window_size ?? 100,
                max_tool_rounds: (agent as any).max_tool_rounds ?? 50,
                max_tokens_per_day: agent.max_tokens_per_day || '',
                max_tokens_per_month: agent.max_tokens_per_month || '',
                max_triggers: (agent as any).max_triggers ?? 20,
                min_poll_interval_min: (agent as any).min_poll_interval_min ?? 5,
                webhook_rate_limit: (agent as any).webhook_rate_limit ?? 5,
            });
            settingsInitRef.current = true;
        }
    }, [agent]);

    // Welcome message editor state (must be at top level -- not inside IIFE)
    const [wmDraft, setWmDraft] = useState('');
    const [wmSaved, setWmSaved] = useState(false);
    useEffect(() => { setWmDraft((agent as any)?.welcome_message || ''); }, [(agent as any)?.welcome_message]);

    // Reset cached state when switching to a different agent
    const prevIdRef = useRef(id);
    useEffect(() => {
        if (id && id !== prevIdRef.current) {
            prevIdRef.current = id;
            settingsInitRef.current = false;
            setSettingsSaved(false);
            setSettingsError('');
            setWmDraft('');
            setWmSaved(false);
            // Invalidate all queries for the old agent to force fresh data
            queryClient.invalidateQueries({ queryKey: ['agent', id] });
            // Re-apply hash so refresh preserves the current tab
            window.history.replaceState(null, '', `#${activeTab}`);
        }
    }, [id]);

    // Load chat history + connect websocket when chat tab is active
    const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'];
    const parseChatMsg = (msg: ChatMsg): ChatMsg => {
        if (msg.role !== 'user') return msg;
        let parsed = { ...msg };
        // Standard web chat format: [file:name.pdf]\ncontent
        const newFmt = msg.content.match(/^\[file:([^\]]+)\]\n?/);
        if (newFmt) { parsed = { ...msg, fileName: newFmt[1], content: msg.content.slice(newFmt[0].length).trim() }; }
        // Feishu/Slack channel format: [文件已上传: workspace/uploads/name]
        const chanFmt = !newFmt && msg.content.match(/^\[\u6587\u4ef6\u5df2\u4e0a\u4f20: (?:workspace\/uploads\/)?([^\]\n]+)\]/);
        if (chanFmt) {
            const raw = chanFmt[1]; const fileName = raw.split('/').pop() || raw;
            parsed = { ...msg, fileName, content: msg.content.slice(chanFmt[0].length).trim() };
        }
        // Old format: [File: name.pdf]\nFile location:...\nQuestion: user_msg
        const oldFmt = !newFmt && !chanFmt && msg.content.match(/^\[File: ([^\]]+)\]/);
        if (oldFmt) {
            const fileName = oldFmt[1];
            const qMatch = msg.content.match(/\nQuestion: ([\s\S]+)$/);
            parsed = { ...msg, fileName, content: qMatch ? qMatch[1].trim() : '' };
        }
        // If file is an image and no imageUrl yet, build download URL for preview
        if (parsed.fileName && !parsed.imageUrl && id) {
            const ext = parsed.fileName.split('.').pop()?.toLowerCase() || '';
            if (IMAGE_EXTS.includes(ext)) {
                parsed.imageUrl = `/api/agents/${id}/files/download?path=workspace/uploads/${encodeURIComponent(parsed.fileName)}&token=${token}`;
            }
        }
        return parsed;
    };


    useEffect(() => {
        currentAgentIdRef.current = id;
    }, [id]);

    // Reset visible state whenever the viewed agent changes.
    // Existing background sockets keep running and will be cleaned up on unmount.
    useEffect(() => {
        sessionMsgAbortRef.current?.abort();
        activeSessionIdRef.current = null;
        setActiveSession(null);
        setChatMessages([]);
        setHistoryMsgs([]);
        setIsStreaming(false);
        setIsWaiting(false);
        setWsConnected(false);
        wsRef.current = null;
        setWorkspaceLockedPath(null);
        setWorkspaceActivePath(null);
        setWorkspaceActivities([]);
        setWorkspaceLiveDraft(null);
        setLiveState({});
        setSidePanelTab('workspace');
        setChatScope('mine');
        setAllUserFilter('');
        setSessions([]);
        setAllSessions([]);
        setAgentExpired(false);
        settingsInitRef.current = false;
    }, [id]);

    // Switching login account or token must not leave another user's sessions/messages in memory.
    useEffect(() => {
        setSessions([]);
        setAllSessions([]);
        setAllUserFilter('');
        setChatScope('mine');
        sessionMsgAbortRef.current?.abort();
        activeSessionIdRef.current = null;
        setActiveSession(null);
        setChatMessages([]);
        setHistoryMsgs([]);
        setWsConnected(false);
        setIsStreaming(false);
        setIsWaiting(false);
        setSessionsLoading(false);
        setAllSessionsLoading(false);
        Object.keys(reconnectDisabledRef.current).forEach((k) => {
            reconnectDisabledRef.current[k] = true;
        });
        Object.keys(wsMapRef.current).forEach((k) => {
            const ws = wsMapRef.current[k];
            if (ws && ws.readyState !== WebSocket.CLOSED) ws.close();
        });
        wsMapRef.current = {};
        wsRef.current = null;
    }, [currentUser?.id, token]);

    useEffect(() => {
        if (!id || !token || activeTab !== 'chat') return;
        fetchMySessions(false, id).then((data: any) => {
            if (currentAgentIdRef.current !== id) return;
            setSessionsLoading(false);
            if (data && data.length > 0) selectSession(data[0], 'mine');
        });
    }, [id, token, activeTab, currentUser?.id]);

    const ensureSessionSocket = (sess: any, agentId: string, authToken: string) => {
        const sessionId = String(sess.id);
        const key = buildSessionRuntimeKey(agentId, sessionId);
        const existing = wsMapRef.current[key];
        if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) return;
        reconnectDisabledRef.current[key] = false;
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const sessionParam = `&session_id=${sessionId}`;

        const scheduleReconnect = () => {
            if (reconnectDisabledRef.current[key]) return;
            clearReconnectTimer(key);
            reconnectTimerRef.current[key] = setTimeout(() => {
                reconnectTimerRef.current[key] = null;
                if (!reconnectDisabledRef.current[key]) ensureSessionSocket(sess, agentId, authToken);
            }, 2000);
        };

        const ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat/${agentId}?token=${authToken}${sessionParam}`);
        wsMapRef.current[key] = ws;
        ws.onopen = () => {
            if (reconnectDisabledRef.current[key]) {
                ws.close();
                return;
            }
            if (currentAgentIdRef.current === agentId && activeSessionIdRef.current === sessionId) {
                wsRef.current = ws;
                setWsConnected(true);
            }
            if (pendingChatSendRef.current?.runtimeKey === key) {
                const pending = pendingChatSendRef.current;
                pendingChatSendRef.current = null;
                setChatInfoMsg(null);
                dispatchChatMessage(ws, key, pending);
            }
        };
        ws.onclose = (e) => {
            if (wsMapRef.current[key] === ws) delete wsMapRef.current[key];
            setSessionUiState(key, { isWaiting: false, isStreaming: false });
            const isActiveRuntime = currentAgentIdRef.current === agentId && activeSessionIdRef.current === sessionId;
            if (isActiveRuntime) {
                wsRef.current = null;
                setWsConnected(false);
                setIsWaiting(false);
                setIsStreaming(false);
            }
            if (e.code === 4003 || e.code === 4002) {
                reconnectDisabledRef.current[key] = true;
                clearReconnectTimer(key);
                if (isActiveRuntime && e.code === 4003) setAgentExpired(true);
                return;
            }
            scheduleReconnect();
        };
        ws.onerror = (error) => {
            const isActiveRuntime = currentAgentIdRef.current === agentId && activeSessionIdRef.current === sessionId;
            if (isActiveRuntime) setWsConnected(false);
            console.warn(`WebSocket error for session ${sessionId}:`, error);
            // Error automatically triggers onclose with abnormal code, which handles reconnect
        };
        ws.onmessage = (e) => {
            const d = JSON.parse(e.data);
            const isActiveRuntime = currentAgentIdRef.current === agentId && activeSessionIdRef.current === sessionId;
            if (['thinking', 'chunk', 'tool_call', 'done', 'error', 'quota_exceeded'].includes(d.type)) {
                const nextStreaming = ['thinking', 'chunk', 'tool_call'].includes(d.type);
                const endStreaming = ['done', 'error', 'quota_exceeded'].includes(d.type);
                setSessionUiState(key, {
                    isWaiting: false,
                    isStreaming: endStreaming ? false : nextStreaming,
                });
            }
            if (!isActiveRuntime) {
                if (['done', 'error', 'quota_exceeded', 'trigger_notification'].includes(d.type)) {
                    fetchMySessions(true, agentId);
                    queryClient.invalidateQueries({ queryKey: ['agents'] });
                }
                if (['done', 'error', 'quota_exceeded'].includes(d.type)) {
                    closeSessionSocket(key, true);
                }
                return;
            }

            if (['thinking', 'chunk', 'tool_call', 'done', 'error', 'quota_exceeded'].includes(d.type)) {
                setIsWaiting(false);
                if (['thinking', 'chunk', 'tool_call'].includes(d.type)) setIsStreaming(true);
                if (['done', 'error', 'quota_exceeded'].includes(d.type)) setIsStreaming(false);
            }

            // Capture session_id from the 'connected' message for Take Control
            if (d.type === 'connected' && d.session_id) {
                if (isActiveRuntime) setWsSessionId(d.session_id);
                return;
            }

            if (d.type === 'thinking') {
                setChatMessages(prev => {
                    const last = prev[prev.length - 1];
                    if (last && last.role === 'assistant' && (last as any)._streaming) {
                        return [...prev.slice(0, -1), { ...last, thinking: (last.thinking || '') + d.content } as any];
                    }
                    return [...prev, { role: 'assistant', content: '', thinking: d.content, _streaming: true } as any];
                });
            } else if (d.type === 'workspace_draft') {
                if (WORKSPACE_TOOLS.has(d.name)) {
                    const parsedDraft = parseWorkspaceDraftArgs(d.name, d.arguments || '');
                    const draft: WorkspaceLiveDraft = {
                        id: d.id || `${d.name}-${d.index || 0}`,
                        tool: d.name,
                        action: workspaceActionForTool(d.name),
                        status: 'drafting',
                        ...parsedDraft,
                    };
                    setWorkspaceLiveDraft(draft);
                    if (allowWorkspaceAutoSwitch(draft.path)) {
                        setWorkspaceActivePath(draft.path!);
                    }
                    if (allowLivePanelAutoFocus()) {
                        setSidePanelTab('workspace');
                        setLivePanelVisible(true);
                        setSessionListCollapsed(true);
                        useAppStore.setState({ sidebarCollapsed: true });
                    }
                }
            } else if (d.type === 'tool_call') {
                if (WORKSPACE_TOOLS.has(d.name)) {
                    if (d.status === 'running') {
                        const rawArgs = typeof d.args === 'string' ? d.args : JSON.stringify(d.args || {});
                        const parsedDraft = parseWorkspaceDraftArgs(d.name, rawArgs);
                        const draft: WorkspaceLiveDraft = {
                            id: d.id || `${d.name}-running`,
                            tool: d.name,
                            action: workspaceActionForTool(d.name),
                            status: 'running',
                            ...parsedDraft,
                        };
                        setWorkspaceLiveDraft(draft);
                        if (allowWorkspaceAutoSwitch(draft.path)) {
                            setWorkspaceActivePath(draft.path!);
                        }
                        if (allowLivePanelAutoFocus()) {
                            setSidePanelTab('workspace');
                            setLivePanelVisible(true);
                            setSessionListCollapsed(true);
                            useAppStore.setState({ sidebarCollapsed: true });
                        }
                    } else if (d.status === 'done') {
                        setWorkspaceLiveDraft(null);
                    }
                }
                if (d.live_preview) {
                    const lp = d.live_preview;
                    setLiveState(prev => {
                        const next = { ...prev };
                        if ((lp.env === 'desktop' || lp.env === 'browser') && lp.screenshot_url) {
                            if (lp.env === 'desktop') next.desktop = { screenshotUrl: lp.screenshot_url };
                            else next.browser = { screenshotUrl: lp.screenshot_url };
                            if (allowLivePanelAutoFocus()) setSidePanelTab(lp.env === 'desktop' ? 'desktop' : 'browser');
                        } else if (lp.env === 'code' && lp.output) {
                            const existing = prev.code?.output || '';
                            next.code = { output: existing + (existing ? '\n---\n' : '') + lp.output };
                            if (allowLivePanelAutoFocus()) setSidePanelTab('code');
                        }
                        return next;
                    });
                    if (allowLivePanelAutoFocus()) {
                        setLivePanelVisible(true);
                        setSessionListCollapsed(true);
                        useAppStore.setState({ sidebarCollapsed: true });
                    }
                }
                    if (d.workspace_activity) {
                        const activity = d.workspace_activity as WorkspaceActivity;
                        setWorkspaceLiveDraft(null);
                        setWorkspaceActivities(prev => [activity, ...prev.filter(item => item.path !== activity.path)].slice(0, 20));
                        if (activity.action === 'delete' && activity.ok !== false && !activity.pendingApproval) {
                            handleWorkspacePathDeleted(activity.path);
                        }
                        if (activity.action !== 'delete' && activity.ok !== false && allowWorkspaceAutoSwitch(activity.path)) {
                            setWorkspaceActivePath(activity.path);
                        }
                    if (allowLivePanelAutoFocus()) {
                        setSidePanelTab('workspace');
                        setLivePanelVisible(true);
                        setSessionListCollapsed(true);
                        useAppStore.setState({ sidebarCollapsed: true });
                    }
                    queryClient.invalidateQueries({ queryKey: ['files', id, workspacePath] });
                }
                setChatMessages(prev => {
                    const toolMsg: ChatMsg = { role: 'tool_call', content: '', toolName: d.name, toolArgs: d.args, toolStatus: d.status, toolResult: d.result };
                    if (d.status === 'done') {
                        const lastIdx = prev.length - 1;
                        const last = prev[lastIdx];
                        if (last && last.role === 'tool_call' && last.toolName === d.name && last.toolStatus === 'running') return [...prev.slice(0, lastIdx), toolMsg];
                    }
                    return [...prev, toolMsg];
                });
                if (d.status === 'done') {
                    const currentSessionId = activeSessionIdRef.current ? String(activeSessionIdRef.current) : '';
                    if (currentSessionId) clearUnreadForSession(currentSessionId);
                    queryClient.invalidateQueries({ queryKey: ['agents'] });
                }
            } else if (d.type === 'chunk') {
                setChatMessages(prev => {
                    const last = prev[prev.length - 1];
                    if (last && last.role === 'assistant' && (last as any)._streaming) return [...prev.slice(0, -1), { ...last, content: last.content + d.content } as any];
                    return [...prev, { role: 'assistant', content: d.content, _streaming: true } as any];
                });
            } else if (d.type === 'done') {
                setChatMessages(prev => {
                    const last = prev[prev.length - 1];
                    const thinking = (last && last.role === 'assistant' && (last as any)._streaming) ? last.thinking : undefined;
                    if (last && last.role === 'assistant' && (last as any)._streaming) return [...prev.slice(0, -1), parseChatMsg({ role: 'assistant', content: d.content, thinking, timestamp: new Date().toISOString() })];
                    return [...prev, parseChatMsg({ role: d.role, content: d.content, timestamp: new Date().toISOString() })];
                });
                const currentSessionId = activeSessionIdRef.current ? String(activeSessionIdRef.current) : '';
                if (currentSessionId) clearUnreadForSession(currentSessionId);
                fetchMySessions(true, agentId);
                queryClient.invalidateQueries({ queryKey: ['agents'] });
            } else if (d.type === 'error' || d.type === 'quota_exceeded') {
                const msg = d.content || d.detail || d.message || 'Request denied';
                setChatMessages(prev => {
                    const last = prev[prev.length - 1];
                    if (last && last.role === 'assistant' && last.content === `⚠️ ${msg}`) return prev;
                    return [...prev, parseChatMsg({ role: 'assistant', content: `⚠️ ${msg}` })];
                });
                if (msg.includes('expired') || msg.includes('Setup failed') || msg.includes('no LLM model') || msg.includes('No model')) {
                    reconnectDisabledRef.current[key] = true;
                    if (msg.includes('expired')) setAgentExpired(true);
                }
            } else if (d.type === 'trigger_notification') {
                const targetSessionId = d.session_id ? String(d.session_id) : '';
                const currentSessionId = activeSessionIdRef.current ? String(activeSessionIdRef.current) : '';
                if (targetSessionId && currentSessionId === targetSessionId) {
                    setChatMessages(prev => [...prev, parseChatMsg({ role: 'assistant', content: d.content })]);
                    clearUnreadForSession(targetSessionId);
                }
                fetchMySessions(true, agentId);
                queryClient.invalidateQueries({ queryKey: ['agents'] });
            } else if (d.type === 'info') {
                // Subtle transient banner for system events (e.g. fallback model switch)
                setChatInfoMsg(d.content || '');
                if (chatInfoTimerRef.current) clearTimeout(chatInfoTimerRef.current);
                chatInfoTimerRef.current = setTimeout(() => setChatInfoMsg(null), 6000);
            } else {
                setChatMessages(prev => [...prev, parseChatMsg({ role: d.role, content: d.content })]);
            }
        };
    };

    const dispatchChatMessage = (socket: WebSocket, runtimeKey: SessionRuntimeKey, payload: PendingChatMessage) => {
        setIsWaiting(true);
        setIsStreaming(false);
        setSessionUiState(runtimeKey, { isWaiting: true, isStreaming: false });
        setChatMessages(prev => [...prev, parseChatMsg({
            role: 'user',
            content: payload.userMsg,
            fileName: payload.fileName,
            imageUrl: payload.imageUrl,
            timestamp: new Date().toISOString()
        })]);
        socket.send(JSON.stringify({
            content: payload.contentForLLM,
            display_content: payload.userMsg,
            file_name: payload.fileName
        }));
    };

    useEffect(() => {
        if (!id || !token || activeTab !== 'chat') return;
        if (!activeSession) {
            syncActiveSocketState(null, id);
            return;
        }
        activeSessionIdRef.current = String(activeSession.id);
        if (!isWritableSession(activeSession)) {
            syncActiveSocketState(activeSession, id);
            return;
        }
        ensureSessionSocket(activeSession, id, token);
        syncActiveSocketState(activeSession, id);
    }, [id, token, activeTab, activeSession?.id, chatScope, canViewAllAgentChatSessions]);

    const handleWorkspacePathDeleted = useCallback((path: string) => {
        let removedName = '';
        setAttachedFiles((prev) => prev.filter((file) => {
            const shouldRemove = file.source === 'workspace_auto' && file.path === path;
            if (shouldRemove) removedName = file.name;
            return !shouldRemove;
        }));
        setWorkspaceLockedPath((current) => current === path ? null : current);
        dismissedWorkspaceRefPath.current = path;
        if (removedName) {
            setChatInfoMsg(`Removed attachment: ${removedName} (file was deleted).`);
            if (chatInfoTimerRef.current) clearTimeout(chatInfoTimerRef.current);
            chatInfoTimerRef.current = setTimeout(() => {
                setChatInfoMsg(null);
                chatInfoTimerRef.current = null;
            }, 4000);
        }
    }, []);

    useEffect(() => {
        const shouldAutoReference = livePanelVisible && sidePanelTab === 'workspace' && !!workspaceActivePath;
        if (!shouldAutoReference) {
            dismissedWorkspaceRefPath.current = null;
            setAttachedFiles((prev) => prev.filter((file) => file.source !== 'workspace_auto'));
            return;
        }
        const path = workspaceActivePath!;
        if (dismissedWorkspaceRefPath.current === path) return;
        setAttachedFiles((prev) => {
            const withoutAuto = prev.filter((file) => file.source !== 'workspace_auto');
            return [
                ...withoutAuto,
                { name: workspaceFileName(path), text: '', path, source: 'workspace_auto' },
            ];
        });
    }, [livePanelVisible, sidePanelTab, workspaceActivePath]);

    useEffect(() => {
        return () => {
            sessionMsgAbortRef.current?.abort();
            Object.keys(reconnectDisabledRef.current).forEach((key) => { reconnectDisabledRef.current[key] = true; });
            Object.keys(reconnectTimerRef.current).forEach((key) => clearReconnectTimer(key));
            Object.values(wsMapRef.current).forEach((ws) => {
                if (ws.readyState !== WebSocket.CLOSED) ws.close();
            });
            wsMapRef.current = {};
            wsRef.current = null;
        };
    }, []);

    // Smart scroll: only auto-scroll if user is at the bottom
    const isNearBottom = useRef(true);
    const isFirstLoad = useRef(true);
    const [showScrollBtn, setShowScrollBtn] = useState(false);
    const [chatScrollBtnBottom, setChatScrollBtnBottom] = useState(96);
    // Read-only history scroll-to-bottom
    const historyContainerRef = useRef<HTMLDivElement>(null);
    const [showHistoryScrollBtn, setShowHistoryScrollBtn] = useState(false);
    const handleHistoryScroll = () => {
        const el = historyContainerRef.current;
        if (!el) return;
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        setShowHistoryScrollBtn(distFromBottom > 200);
    };
    const scrollHistoryToBottom = () => {
        const el = historyContainerRef.current;
        if (el) el.scrollTop = el.scrollHeight;
        setShowHistoryScrollBtn(false);
    };
    // Auto-show button when history messages overflow the container
    useEffect(() => {
        const el = historyContainerRef.current;
        if (!el) return;
        // Use a small timeout to let the DOM render the messages first
        const timer = setTimeout(() => {
            const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
            setShowHistoryScrollBtn(distFromBottom > 200);
        }, 100);
        return () => clearTimeout(timer);
    }, [historyMsgs, activeSession?.id]);
    // Memoized component for each chat message to avoid re-renders while typing
    const ChatMessageItem = React.useMemo(() => React.memo(({
        msg, i, isLeft, t, senderLabel, avatarText, forceSenderLabel = false,
    }: {
        msg: any;
        i: number;
        isLeft: boolean;
        t: any;
        senderLabel?: string;
        avatarText?: string;
        forceSenderLabel?: boolean;
    }) => {
        const fe = msg.fileName?.split('.').pop()?.toLowerCase() ?? '';
        const fi = fe === 'pdf' ? '📄' : (fe === 'csv' || fe === 'xlsx' || fe === 'xls') ? '📊' : (fe === 'docx' || fe === 'doc') ? '📝' : '📎';
        const isImage = msg.imageUrl && ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'].includes(fe);
        const resolvedSenderLabel = msg.sender_name || senderLabel;
        const resolvedAvatarText = avatarText || (resolvedSenderLabel ? resolvedSenderLabel[0] : (isLeft ? 'A' : 'U'));
        const showSenderLabel = !!resolvedSenderLabel && (forceSenderLabel || !!msg.sender_name);

        // Parse [image_data:data:image/...;base64,...] markers from user message content.
        // The backend persists these markers in the DB to preserve multimodal context
        // across turns. They must ALWAYS be stripped from displayContent so users never
        // see raw base64 strings in the chat bubble.
        // Guard: only collect extracted images for thumbnail rendering when msg.imageUrl
        // is NOT already set — otherwise the image is already shown via the isImage path
        // and rendering again from the marker would display it twice.
        const IMAGE_DATA_RE = /\[image_data:(data:image\/[^;]+;base64,[^\]]+)\]/g;
        const inlineImages: string[] = [];
        let displayContent = msg.content || '';
        if (displayContent.includes('[image_data:')) {
            displayContent = displayContent.replace(IMAGE_DATA_RE, (_: string, dataUrl: string) => {
                // Only collect for thumbnail rendering if not already shown via imageUrl
                if (!msg.imageUrl) inlineImages.push(dataUrl);
                return ''; // always strip the marker from displayed text
            }).trim();
        }

        const timestampHtml = msg.timestamp ? (() => {
            const d = new Date(msg.timestamp);
            const now = new Date();
            const diffMs = now.getTime() - d.getTime();
            const isToday = d.toDateString() === now.toDateString();
            let timeStr = '';
            if (isToday) timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            else if (diffMs < 7 * 86400000) timeStr = d.toLocaleDateString([], { weekday: 'short' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            else timeStr = d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            return (
                <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '4px', opacity: 0.6, display: 'flex', alignItems: 'center', justifyContent: isLeft ? 'flex-start' : 'flex-end' }}>
                    {timeStr}
                    {msg.content && <CopyMessageButton text={msg.content} />}
                </div>
            );
        })() : null;

        return (
            <div key={i} style={{ display: 'flex', flexDirection: isLeft ? 'row' : 'row-reverse', gap: '8px', marginBottom: '8px' }}>
                <div style={{ width: '28px', height: '28px', borderRadius: '50%', background: isLeft ? 'var(--bg-elevated)' : 'rgba(16,185,129,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', flexShrink: 0, color: 'var(--text-secondary)', fontWeight: 600 }}>{resolvedAvatarText}</div>
                <div style={{ maxWidth: '75%', padding: '8px 12px', borderRadius: '12px', background: isLeft ? 'var(--bg-secondary)' : 'rgba(16,185,129,0.1)', fontSize: '13px', lineHeight: '1.5', wordBreak: 'break-word' }}>
                    {showSenderLabel && <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '2px', fontWeight: 600 }}>{resolvedSenderLabel}</div>}
                    {isImage ? (
                        <div style={{ marginBottom: '4px' }}>
                            <img src={msg.imageUrl} alt={msg.fileName} style={{ maxWidth: '200px', maxHeight: '150px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }} loading="lazy" />
                        </div>
                    ) : (msg.fileName && (
                        <div style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', background: isLeft ? 'rgba(0,0,0,0.05)' : 'rgba(0,0,0,0.08)', borderRadius: '6px', padding: '4px 8px', marginBottom: msg.content ? '4px' : '0', fontSize: '11px', border: '1px solid var(--border-subtle)', color: 'var(--text-secondary)' }}>
                            <span>{fi}</span>
                            <span style={{ fontWeight: 500, color: 'var(--text-primary)', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{msg.fileName}</span>
                        </div>
                    ))}
                    {/* Render images extracted from [image_data:] markers (multimodal context) */}
                    {inlineImages.length > 0 && (
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: displayContent ? '6px' : '0' }}>
                            {inlineImages.map((url, idx) => (
                                <img
                                    key={idx}
                                    src={url}
                                    alt="attached image"
                                    style={{ maxWidth: '200px', maxHeight: '150px', borderRadius: '8px', border: '1px solid var(--border-subtle)', objectFit: 'cover' }}
                                    loading="lazy"
                                />
                            ))}
                        </div>
                    )}
                    {msg.thinking && (
                        <details style={{ marginBottom: '8px', fontSize: '12px', background: 'rgba(147, 130, 220, 0.08)', borderRadius: '6px', border: '1px solid rgba(147, 130, 220, 0.15)' }}>
                            <summary style={{ padding: '6px 10px', cursor: 'pointer', color: 'rgba(147, 130, 220, 0.9)', fontWeight: 500, userSelect: 'none', display: 'flex', alignItems: 'center', gap: '4px' }}>Thinking</summary>
                            <div style={{ padding: '4px 10px 8px', fontSize: '12px', lineHeight: '1.6', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '300px', overflow: 'auto' }}>{msg.thinking}</div>
                        </details>
                    )}
                    {msg.role === 'assistant' ? (
                        (msg as any)._streaming && !msg.content ? (
                            <div className="thinking-indicator">
                                <div className="thinking-dots"><span /><span /><span /></div>
                                <span style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('agent.chat.thinking', 'Thinking...')}</span>
                            </div>
                        ) : <MarkdownRenderer content={displayContent} />
                    ) : <div style={{ whiteSpace: 'pre-wrap' }}>{displayContent}</div>}
                    {timestampHtml}
                </div>
            </div>
        );
    }), [t]);

    const handleChatScroll = () => {
        const el = chatContainerRef.current;
        if (!el) return;
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        isNearBottom.current = distFromBottom < 5;
        setShowScrollBtn(distFromBottom > 200);
    };
    const scrollToBottom = () => {
        chatEndRef.current?.scrollIntoView({ behavior: 'instant' as ScrollBehavior });
        setShowScrollBtn(false);
    };
    useEffect(() => {
        if (!chatEndRef.current) return;
        if (isFirstLoad.current && chatMessages.length > 0) {
            // First load: instant jump to bottom, no animation
            chatEndRef.current.scrollIntoView({ behavior: 'instant' as ScrollBehavior });
            isFirstLoad.current = false;
            return;
        }
        if (isNearBottom.current) {
            chatEndRef.current.scrollIntoView({ behavior: 'instant' as ScrollBehavior });
        }
    }, [chatMessages]);

    useEffect(() => {
        const gapAboveComposer = 14;
        const updateScrollButtonOffset = () => {
            const composerAreaHeight = chatInputAreaRef.current?.offsetHeight ?? 82;
            setChatScrollBtnBottom(composerAreaHeight + gapAboveComposer);
        };

        updateScrollButtonOffset();
        if (typeof ResizeObserver === 'undefined' || !chatInputAreaRef.current) return;

        const observer = new ResizeObserver(() => updateScrollButtonOffset());
        observer.observe(chatInputAreaRef.current);
        return () => observer.disconnect();
    }, [activeSession?.id, activeTab, chatUploadDrafts.length, attachedFiles.length]);

    const sendChatMsg = () => {
        if (!id || !activeSession?.id) return;
        const activeRuntimeKey = buildSessionRuntimeKey(id, String(activeSession.id));
        const activeSocket = wsMapRef.current[activeRuntimeKey];
        if (!chatInput.trim() && attachedFiles.length === 0) return;

        let userMsg = chatInput.trim();
        let contentForLLM = userMsg;
        let displayFiles = '';

        if (attachedFiles.length > 0) {
            let filesPrompt = '';
            let filesDisplay = '';

            attachedFiles.forEach(file => {
                filesDisplay += `[📎 ${file.name}] `;
                if (file.imageUrl && supportsVision) {
                    filesPrompt += `[image_data:${file.imageUrl}]\n`;
                } else if (file.imageUrl) {
                    filesPrompt += `[图片文件已上传: ${file.name}，保存在 ${file.path || ''}]\n`;
                } else {
                    const wsPath = file.path || '';
                    const codePath = wsPath.replace(/^workspace\//, '');
                    const fileLoc = wsPath ? `\nFile location: ${wsPath} (for read_file/read_document tools)\nIn execute_code, use relative path: "${codePath}" (working directory is workspace/)\n` : '';
                    if (file.source === 'workspace_auto') {
                        filesPrompt += `[Workspace reference: ${file.name}]${fileLoc}\nUse read_file or read_document if you need the file contents.\n\n`;
                    } else {
                        filesPrompt += `[File: ${file.name}]${fileLoc}\n${file.text}\n\n`;
                    }
                }
            });

            if (supportsVision && attachedFiles.some(f => f.imageUrl)) {
                contentForLLM = userMsg ? `${filesPrompt}\n${userMsg}` : `${filesPrompt}\n请分析这些文件`;
            } else {
                contentForLLM = userMsg ? `${filesPrompt}\nQuestion: ${userMsg}` : `Please analyze these files:\n\n${filesPrompt}`;
            }

            displayFiles = filesDisplay.trim();
            userMsg = userMsg ? `${displayFiles}\n${userMsg}` : displayFiles;
        }

        const payload: PendingChatMessage = {
            runtimeKey: activeRuntimeKey,
            contentForLLM,
            userMsg,
            fileName: attachedFiles.map(f => f.name).join(', '),
            imageUrl: attachedFiles.length === 1 ? attachedFiles[0].imageUrl : undefined,
        };

        setChatInput('');
        // Reset textarea height after clearing content
        if (chatInputRef.current) {
            chatInputRef.current.style.height = 'auto';
        }
        dismissedWorkspaceRefPath.current = null;
        setAttachedFiles((prev) => prev.filter((file) => file.source === 'workspace_auto'));

        if (!activeSocket || activeSocket.readyState !== WebSocket.OPEN) {
            pendingChatSendRef.current = payload;
            if (token) ensureSessionSocket(activeSession, id, token);
            setChatInfoMsg('Connection is reconnecting. Your message will be sent automatically.');
            if (chatInfoTimerRef.current) clearTimeout(chatInfoTimerRef.current);
            chatInfoTimerRef.current = setTimeout(() => setChatInfoMsg(null), 4000);
            return;
        }

        dispatchChatMessage(activeSocket, activeRuntimeKey, payload);
    };

    const handleChatFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files || []);
        if (!files.length) return;
        const allowedFiles = files.slice(0, 10 - attachedFiles.length);
        if (!allowedFiles.length) {
            alert('Limit of 10 attached files reached.');
            return;
        }

        const baseTime = Date.now();
        const newDrafts = allowedFiles.map((file, i) => ({
            id: `up-${baseTime}-${i}-${file.name}`,
            name: file.name,
            percent: 0,
            previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined,
            sizeBytes: file.size,
        }));
        setChatUploadDrafts((prev) => [...prev, ...newDrafts]);

        const runOne = async (file: File, draft: (typeof newDrafts)[0]) => {
            const { promise, abort } = uploadFileWithProgress(
                `/chat/upload`,
                file,
                (pct) => {
                    setChatUploadDrafts((prev) =>
                        prev.map((d) => (d.id === draft.id ? { ...d, percent: pct >= 101 ? 100 : pct } : d)),
                    );
                },
                id ? { agent_id: id } : undefined,
            );
            chatUploadAbortRef.current.set(draft.id, abort);
            try {
                const data = await promise;
                if (draft.previewUrl) URL.revokeObjectURL(draft.previewUrl);
                setChatUploadDrafts((prev) => prev.filter((d) => d.id !== draft.id));
                chatUploadAbortRef.current.delete(draft.id);
                setAttachedFiles((prev) =>
                    [...prev, {
                        name: data.filename,
                        text: data.extracted_text,
                        path: data.workspace_path,
                        imageUrl: data.image_data_url || undefined,
                    }].slice(0, 10),
                );
            } catch (err: any) {
                if (draft.previewUrl) URL.revokeObjectURL(draft.previewUrl);
                setChatUploadDrafts((prev) => prev.filter((d) => d.id !== draft.id));
                chatUploadAbortRef.current.delete(draft.id);
                if (err?.message !== 'Upload cancelled') alert(t('agent.upload.failed'));
            }
        };

        await Promise.all(allowedFiles.map((file, i) => runOne(file, newDrafts[i])));
        if (fileInputRef.current) fileInputRef.current.value = '';
    };

    // Clipboard paste handler — auto-upload pasted images
    const handlePaste = async (e: React.ClipboardEvent) => {
        const items = e.clipboardData?.items;
        if (!items) return;

        const filesToUpload: File[] = [];
        for (let i = 0; i < items.length; i++) {
            if (items[i].type.startsWith('image/')) {
                const blob = items[i].getAsFile();
                if (blob) {
                    const ext = blob.type.split('/')[1] || 'png';
                    const fileName = `paste-${Date.now()}-${i}.${ext}`;
                    filesToUpload.push(new File([blob], fileName, { type: blob.type }));
                }
            }
        }

        if (!filesToUpload.length) return;
        e.preventDefault();
        const allowedFiles = filesToUpload.slice(0, 10 - attachedFiles.length);
        if (!allowedFiles.length) {
            alert('Limit of 10 attached files reached.');
            return;
        }

        const baseTime = Date.now();
        const newDrafts = allowedFiles.map((file, i) => ({
            id: `paste-${baseTime}-${i}-${file.name}`,
            name: file.name,
            percent: 0,
            previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined,
            sizeBytes: file.size,
        }));
        setChatUploadDrafts((prev) => [...prev, ...newDrafts]);

        const runOne = async (file: File, draft: (typeof newDrafts)[0]) => {
            const { promise, abort } = uploadFileWithProgress(
                `/chat/upload`,
                file,
                (pct) => {
                    setChatUploadDrafts((prev) =>
                        prev.map((d) => (d.id === draft.id ? { ...d, percent: pct >= 101 ? 100 : pct } : d)),
                    );
                },
                id ? { agent_id: id } : undefined,
            );
            chatUploadAbortRef.current.set(draft.id, abort);
            try {
                const data = await promise;
                if (draft.previewUrl) URL.revokeObjectURL(draft.previewUrl);
                setChatUploadDrafts((prev) => prev.filter((d) => d.id !== draft.id));
                chatUploadAbortRef.current.delete(draft.id);
                setAttachedFiles((prev) =>
                    [...prev, {
                        name: data.filename,
                        text: data.extracted_text,
                        path: data.workspace_path,
                        imageUrl: data.image_data_url || undefined,
                    }].slice(0, 10),
                );
            } catch (err: any) {
                if (draft.previewUrl) URL.revokeObjectURL(draft.previewUrl);
                setChatUploadDrafts((prev) => prev.filter((d) => d.id !== draft.id));
                chatUploadAbortRef.current.delete(draft.id);
                if (err?.message !== 'Upload cancelled') alert(t('agent.upload.failed'));
            }
        };

        await Promise.all(allowedFiles.map((file, i) => runOne(file, newDrafts[i])));
    };

    // ── Drag-and-drop chat file upload ──
    const handleDroppedChatFiles = useCallback(async (files: File[]) => {
        if (!wsConnected || chatUploadDrafts.length > 0 || isWaiting || isStreaming || attachedFiles.length >= 10) return;
        const availableSlots = Math.max(0, 10 - attachedFiles.length);
        const filesToProcess = files.slice(0, availableSlots);

        for (const file of filesToProcess) {
            const draftId = Math.random().toString(36).slice(2, 9);
            const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined;
            setChatUploadDrafts(prev => [...prev, { id: draftId, name: file.name, percent: 0, previewUrl, sizeBytes: file.size }]);

            try {
                const { promise } = uploadFileWithProgress(
                    '/chat/upload',
                    file,
                    (pct) => {
                        setChatUploadDrafts(prev => prev.map(d => d.id === draftId ? { ...d, percent: pct >= 101 ? 100 : pct } : d));
                    },
                    id ? { agent_id: id } : undefined,
                );
                const data = await promise;
                setAttachedFiles(prev => [...prev, { name: data.filename, text: data.extracted_text, path: data.workspace_path, imageUrl: data.image_data_url || undefined }]);
            } catch (err: any) {
                if (err?.message !== 'Upload cancelled') {
                    alert(err?.message || t('agent.upload.failed'));
                }
            } finally {
                if (previewUrl) URL.revokeObjectURL(previewUrl);
                setChatUploadDrafts(prev => prev.filter(d => d.id !== draftId));
            }
        }
    }, [id, wsConnected, chatUploadDrafts.length, isWaiting, isStreaming, attachedFiles.length, isWritableSession, t]);

    const { isDragging: isChatDragging, dropZoneProps: chatDropProps } = useDropZone({
        onDrop: handleDroppedChatFiles,
        disabled: !wsConnected || chatUploadDrafts.length > 0 || isWaiting || isStreaming || attachedFiles.length >= 10 || !activeSession || !isWritableSession(activeSession),
    });

    // Expandable activity log
    const [expandedLogId, setExpandedLogId] = useState<string | null>(null);
    const [logFilter, setLogFilter] = useState<string>('user'); // 'user' | 'backend' | 'heartbeat' | 'schedule' | 'messages'

    // Import skill from presets
    const [showImportSkillModal, setShowImportSkillModal] = useState(false);
    const [importingSkillId, setImportingSkillId] = useState<string | null>(null);
    const { data: globalSkillsForImport } = useQuery({
        queryKey: ['global-skills-for-import'],
        queryFn: () => skillApi.list(),
        enabled: showImportSkillModal,
    });
    // Agent-level import from ClawHub / URL
    const [showAgentClawhub, setShowAgentClawhub] = useState(false);
    const [agentClawhubQuery, setAgentClawhubQuery] = useState('');
    const [agentClawhubResults, setAgentClawhubResults] = useState<any[]>([]);
    const [agentClawhubSearching, setAgentClawhubSearching] = useState(false);
    const [agentClawhubInstalling, setAgentClawhubInstalling] = useState<string | null>(null);
    const [showAgentUrlImport, setShowAgentUrlImport] = useState(false);
    const [agentUrlInput, setAgentUrlInput] = useState('');
    const [agentUrlImporting, setAgentUrlImporting] = useState(false);

    const { data: schedules = [] } = useQuery({
        queryKey: ['schedules', id],
        queryFn: () => scheduleApi.list(id!),
        enabled: !!id && activeTab === 'tasks',
    });

    // Schedule form state
    const [showScheduleForm, setShowScheduleForm] = useState(false);
    const schedDefaults = { freq: 'daily', interval: 1, time: '09:00', weekdays: [1, 2, 3, 4, 5] };
    const [schedForm, setSchedForm] = useState({ name: '', instruction: '', schedule: JSON.stringify(schedDefaults), due_date: '' });

    const createScheduleMut = useMutation({
        mutationFn: () => {
            let sched: any;
            try { sched = JSON.parse(schedForm.schedule); } catch { sched = schedDefaults; }
            return scheduleApi.create(id!, { name: schedForm.name, instruction: schedForm.instruction, cron_expr: schedToCron(sched) });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['schedules', id] });
            setShowScheduleForm(false);
            setSchedForm({ name: '', instruction: '', schedule: JSON.stringify(schedDefaults), due_date: '' });
        },
        onError: (err: any) => {
            const msg = err?.detail || err?.message || String(err);
            alert(`Failed to create schedule: ${msg}`);
        },
    });

    const toggleScheduleMut = useMutation({
        mutationFn: ({ sid, enabled }: { sid: string; enabled: boolean }) =>
            scheduleApi.update(id!, sid, { is_enabled: enabled }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['schedules', id] }),
    });

    const deleteScheduleMut = useMutation({
        mutationFn: (sid: string) => scheduleApi.delete(id!, sid),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['schedules', id] }),
    });

    const triggerScheduleMut = useMutation({
        mutationFn: async (sid: string) => {
            const res = await scheduleApi.trigger(id!, sid);
            return res;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['schedules', id] });
            showToast('✅ Schedule triggered — executing in background', 'success');
        },
        onError: (err: any) => {
            const msg = err?.response?.data?.detail || err?.message || 'Failed to trigger schedule';
            showToast(msg, 'error');
        },
    });


    const { data: metrics } = useQuery({
        queryKey: ['metrics', id],
        queryFn: () => agentApi.metrics(id!).catch(() => null),
        enabled: !!id && activeTab === 'status',
        retry: false,
    });

    const { data: channelConfig } = useQuery({
        queryKey: ['channel', id],
        queryFn: () => channelApi.get(id!),
        enabled: !!id && activeTab === 'settings',
    });

    const { data: webhookData } = useQuery({
        queryKey: ['webhook-url', id],
        queryFn: () => channelApi.webhookUrl(id!),
        enabled: !!id && activeTab === 'settings',
    });

    const { data: llmModels = [] } = useQuery({
        queryKey: ['llm-models'],
        queryFn: () => enterpriseApi.llmModels(),
        enabled: activeTab === 'settings' || activeTab === 'status' || activeTab === 'chat',
    });

    const supportsVision = !!agent?.primary_model_id && llmModels.some(
        (m: any) => m.id === agent.primary_model_id && m.supports_vision
    );

    const { data: permData } = useQuery({
        queryKey: ['agent-permissions', id],
        queryFn: () => fetchAuth<any>(`/agents/${id}/permissions`),
        enabled: !!id && activeTab === 'settings',
    });

    // ─── Soul editor ─────────────────────────────────────
    const [soulEditing, setSoulEditing] = useState(false);
    const [soulDraft, setSoulDraft] = useState('');

    const saveSoul = useMutation({
        mutationFn: () => fileApi.write(id!, 'soul.md', soulDraft),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['file', id, 'soul.md'] });
            setSoulEditing(false);
        },
    });


    const CopyBtn = ({ url }: { url: string }) => (
        <button title="Copy" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', marginLeft: '6px', padding: '1px 4px', cursor: 'pointer', borderRadius: '3px', border: '1px solid var(--border-color)', background: 'var(--bg-primary)', color: 'var(--text-secondary)', verticalAlign: 'middle', lineHeight: 1 }}
            onClick={() => copyToClipboard(url).then(() => { })}>
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <rect x="4" y="4" width="9" height="11" rx="1.5" /><path d="M3 11H2a1 1 0 01-1-1V2a1 1 0 011-1h8a1 1 0 011 1v1" />
            </svg>
        </button>
    );

    // ─── File viewer ─────────────────────────────────────
    const [viewingFile, setViewingFile] = useState<string | null>(null);
    const [fileEditing, setFileEditing] = useState(false);
    const [fileDraft, setFileDraft] = useState('');
    const [promptModal, setPromptModal] = useState<{ title: string; placeholder: string; action: string } | null>(null);
    const [deleteConfirm, setDeleteConfirm] = useState<{ path: string; name: string; isDir: boolean } | null>(null);
    const [uploadToast, setUploadToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [editingRole, setEditingRole] = useState(false);
    const [roleInput, setRoleInput] = useState('');
    const [editingName, setEditingName] = useState(false);
    const [nameInput, setNameInput] = useState('');
    const showToast = (message: string, type: 'success' | 'error' = 'success') => {
        setUploadToast({ message, type });
        setTimeout(() => setUploadToast(null), 3000);
    };
    const { data: fileContent } = useQuery({
        queryKey: ['file-content', id, viewingFile],
        queryFn: () => fileApi.read(id!, viewingFile!),
        enabled: !!viewingFile,
    });

    // ─── Task creation & detail ───────────────────────────────────
    const [showTaskForm, setShowTaskForm] = useState(false);
    const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
    const [taskForm, setTaskForm] = useState({ title: '', description: '', priority: 'medium', type: 'todo' as 'todo' | 'supervision', supervision_target_name: '', remind_schedule: '', due_date: '' });
    const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
    const { data: taskLogs = [] } = useQuery({
        queryKey: ['task-logs', id, selectedTaskId],
        queryFn: () => taskApi.getLogs(id!, selectedTaskId!),
        enabled: !!id && !!selectedTaskId,
        refetchInterval: selectedTaskId ? 3000 : false,
    });

    // Schedule execution history (selectedTaskId format: 'sched-{uuid}')
    const expandedScheduleId = selectedTaskId?.startsWith('sched-') ? selectedTaskId.slice(6) : null;
    const { data: scheduleHistoryData } = useQuery({
        queryKey: ['schedule-history', id, expandedScheduleId],
        queryFn: () => scheduleApi.history(id!, expandedScheduleId!),
        enabled: !!id && !!expandedScheduleId,
    });
    const createTask = useMutation({
        mutationFn: (data: any) => {
            const cleaned = { ...data };
            if (!cleaned.due_date) delete cleaned.due_date;
            return taskApi.create(id!, cleaned);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['tasks', id] });
            setShowTaskForm(false);
            setTaskForm({ title: '', description: '', priority: 'medium', type: 'todo', supervision_target_name: '', remind_schedule: '', due_date: '' });
        },
    });

    if (isLoading || !agent) {
        return <div style={{ padding: '40px', color: 'var(--text-tertiary)' }}>{t('common.loading')}</div>;
    }

    // Compute display status (including OpenClaw disconnected detection)
    const computeStatusKey = () => {
        if (agent.status === 'error') return 'error';
        if (agent.status === 'creating') return 'creating';
        if (agent.status === 'stopped') return 'stopped';
        if ((agent as any).agent_type === 'openclaw' && agent.status === 'running' && (agent as any).openclaw_last_seen) {
            const elapsed = Date.now() - new Date((agent as any).openclaw_last_seen).getTime();
            if (elapsed > 60 * 60 * 1000) return 'disconnected';
        }
        return agent.status === 'running' ? 'running' : 'idle';
    };
    const statusKey = computeStatusKey();
    const canManage = (agent as any).access_level === 'manage' || isAdmin;

    return (
        <>
            <div>
                {/* Header */}
                <div className="page-header">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                        <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'var(--accent-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '24px' }}>{(Array.from(agent.name || 'A')[0] as string || 'A').toUpperCase()}</div>
                        <div style={{ flex: 1, minWidth: 0, overflow: 'hidden' }}>
                            {canManage && editingName ? (
                                <input
                                    className="page-title"
                                    autoFocus
                                    value={nameInput}
                                    onChange={e => setNameInput(e.target.value)}
                                    onBlur={async () => {
                                        setEditingName(false);
                                        if (nameInput.trim() && nameInput !== agent.name) {
                                            await agentApi.update(id!, { name: nameInput.trim() } as any);
                                            queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                        } else {
                                            setNameInput(agent.name);
                                        }
                                    }}
                                    onKeyDown={async e => {
                                        if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                                        if (e.key === 'Escape') { setEditingName(false); setNameInput(agent.name); }
                                    }}
                                    style={{
                                        background: 'var(--bg-elevated)', border: '1px solid var(--accent-primary)',
                                        borderRadius: '6px', color: 'var(--text-primary)',
                                        padding: '4px 10px', minWidth: '320px', width: 'auto', outline: 'none',
                                        marginBottom: '0', display: 'block',
                                    }}
                                />
                            ) : (
                                <h1 className="page-title"
                                    title={canManage ? "Click to edit name" : undefined}
                                    onClick={() => { if (canManage) { setNameInput(agent.name); setEditingName(true); } }}
                                    style={{ cursor: canManage ? 'text' : 'default', borderBottom: canManage ? '1px dashed transparent' : 'none', display: 'inline-block', marginBottom: '0' }}
                                    onMouseEnter={e => { if (canManage) e.currentTarget.style.borderBottomColor = 'var(--text-tertiary)'; }}
                                    onMouseLeave={e => { if (canManage) e.currentTarget.style.borderBottomColor = 'transparent'; }}
                                >
                                    {agent.name}
                                </h1>
                            )}
                            <p className="page-subtitle" style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '4px' }}>
                                <span className={`status-dot ${statusKey}`} />
                                {t(`agent.status.${statusKey}`)}
                                {canManage && editingRole ? (
                                    <textarea
                                        autoFocus
                                        value={roleInput}
                                        onChange={e => setRoleInput(e.target.value)}
                                        onBlur={async () => {
                                            setEditingRole(false);
                                            if (roleInput !== agent.role_description) {
                                                await agentApi.update(id!, { role_description: roleInput } as any);
                                                queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                            }
                                        }}
                                        onKeyDown={async e => {
                                            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); (e.target as HTMLTextAreaElement).blur(); }
                                            if (e.key === 'Escape') { setEditingRole(false); setRoleInput(agent.role_description || ''); }
                                        }}
                                        rows={2}
                                        style={{
                                            background: 'var(--bg-elevated)', border: '1px solid var(--accent-primary)',
                                            borderRadius: '6px', color: 'var(--text-primary)', fontSize: '13px',
                                            padding: '6px 10px', width: 'min(500px, 50vw)', outline: 'none',
                                            resize: 'vertical', lineHeight: '1.5', fontFamily: 'inherit',
                                        }}
                                    />
                                ) : (
                                    <span
                                        title={canManage ? (agent.role_description || 'Click to edit') : (agent.role_description || '')}
                                        onClick={() => { if (canManage) { setRoleInput(agent.role_description || ''); setEditingRole(true); } }}
                                        style={{ cursor: canManage ? 'text' : 'default', borderBottom: canManage ? '1px dashed transparent' : 'none', maxWidth: '38vw', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block', verticalAlign: 'middle' }}
                                        onMouseEnter={e => { if (canManage) e.currentTarget.style.borderBottomColor = 'var(--text-tertiary)'; }}
                                        onMouseLeave={e => { if (canManage) e.currentTarget.style.borderBottomColor = 'transparent'; }}
                                    >
                                        {agent.role_description ? `· ${agent.role_description}` : (canManage ? <span style={{ color: 'var(--text-tertiary)', fontSize: '12px' }}>· {t('agent.fields.role', 'Click to add a description...')}</span> : null)}
                                    </span>
                                )}
                                {(agent as any).is_expired && (
                                    <span style={{ background: 'var(--error)', color: '#fff', padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 }}>Expired</span>
                                )}
                                {(agent as any).agent_type === 'openclaw' && (
                                    <span style={{
                                        fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
                                        background: 'linear-gradient(135deg, #6366f1, #8b5cf6)', color: '#fff', fontWeight: 600,
                                        letterSpacing: '0.5px',
                                    }}>OpenClaw · Lab</span>
                                )}
                                {!(agent as any).is_expired && (agent as any).expires_at && (
                                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                        Expires: {new Date((agent as any).expires_at).toLocaleString()}
                                    </span>
                                )}
                                {isAdmin && (
                                    <button
                                        onClick={openExpiryModal}
                                        title="Edit expiry time"
                                        style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '11px', color: 'var(--text-tertiary)', padding: '1px 4px', borderRadius: '4px', lineHeight: 1 }}
                                        onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-secondary)')}
                                        onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                                    >✏️ {t((agent as any).expires_at || (agent as any).is_expired ? 'agent.settings.expiry.renew' : 'agent.settings.expiry.setExpiry')}</button>
                                )}
                            </p>
                        </div>
                    </div>
                    <div style={{ display: 'flex', gap: '8px' }}>
                        <button className="btn btn-primary" onClick={() => setActiveTab('chat')}>{t('agent.actions.chat')}</button>
                        {(agent as any)?.agent_type !== 'openclaw' && (
                            <>
                                {agent.status === 'stopped' ? (
                                    <button className="btn btn-secondary" onClick={async () => { await agentApi.start(id!); queryClient.invalidateQueries({ queryKey: ['agent', id] }); }}>{t('agent.actions.start')}</button>
                                ) : agent.status === 'running' ? (
                                    <button className="btn btn-secondary" onClick={async () => { await agentApi.stop(id!); queryClient.invalidateQueries({ queryKey: ['agent', id] }); }}>{t('agent.actions.stop')}</button>
                                ) : null}
                            </>
                        )}
                    </div>
                </div>

                {/* Tabs */}
                <div className="tabs">
                    {TABS.filter(tab => {
                        // 'use' access: hide settings and approvals tabs
                        if ((agent as any)?.access_level === 'use') {
                            if (tab === 'settings' || tab === 'approvals') return false;
                        }
                        // OpenClaw agents: only show status, chat, activityLog, settings
                        if ((agent as any)?.agent_type === 'openclaw') {
                            return ['status', 'relationships', 'chat', 'activityLog', 'settings'].includes(tab);
                        }
                        return true;
                    }).map((tab) => (
                        <div key={tab} className={`tab ${activeTab === tab ? 'active' : ''}`} onClick={() => setActiveTab(tab)}>
                            {t(`agent.tabs.${tab}`)}
                        </div>
                    ))}
                </div>

                {/* ── Enhanced Status Tab ── */}
                {activeTab === 'status' && (() => {
                    // Format date helper
                    const formatDate = (d: string) => {
                        try { return new Date(d).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); } catch { return d; }
                    };
                    // Get model label
                    const primaryModel = llmModels.find((m: any) => m.id === agent.primary_model_id);
                    const modelLabel = primaryModel ? (primaryModel.label || primaryModel.model) : '—';
                    const modelProvider = primaryModel ? primaryModel.provider : '—';

                    return (
                        <div>
                            {/* Metric cards */}
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginBottom: '24px' }}>
                                <div className="card">
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.tabs.status')}</div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        <span className={`status-dot ${statusKey}`} />
                                        <span style={{ fontSize: '16px', fontWeight: 500 }}>{t(`agent.status.${statusKey}`)}</span>
                                    </div>
                                </div>
                                <div className="card">
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.settings.today')} Token</div>
                                    <div style={{ fontSize: '22px', fontWeight: 600 }}>{formatTokens(agent.tokens_used_today)}</div>
                                    {agent.max_tokens_per_day && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{t('agent.settings.noLimit')} {formatTokens(agent.max_tokens_per_day)}</div>}
                                </div>
                                <div className="card">
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.settings.month')} Token</div>
                                    <div style={{ fontSize: '22px', fontWeight: 600 }}>{formatTokens(agent.tokens_used_month)}</div>
                                    {agent.max_tokens_per_month && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{t('agent.settings.noLimit')} {formatTokens(agent.max_tokens_per_month)}</div>}
                                </div>
                                {/* Native agent metrics */}
                                {(agent as any)?.agent_type !== 'openclaw' && (<>
                                    <div className="card">
                                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.status.llmCallsToday')}</div>
                                        <div style={{ fontSize: '22px', fontWeight: 600 }}>{((agent as any).llm_calls_today || 0).toLocaleString()}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{t('agent.status.max')}: {((agent as any).max_llm_calls_per_day || 100).toLocaleString()}</div>
                                    </div>
                                    <div className="card">
                                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.status.totalToken')}</div>
                                        <div style={{ fontSize: '22px', fontWeight: 600 }}>{formatTokens((agent as any).tokens_used_total || 0)}</div>
                                    </div>
                                    {metrics && (
                                        <>
                                            <div className="card">
                                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.tasks.done')}</div>
                                                <div style={{ fontSize: '22px', fontWeight: 600 }}>{metrics.tasks?.done || 0}/{metrics.tasks?.total || 0}</div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}> {metrics.tasks?.completion_rate || 0}%</div>
                                            </div>
                                            <div className="card">
                                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.status.pending')}</div>
                                                <div style={{ fontSize: '22px', fontWeight: 600, color: metrics.approvals?.pending > 0 ? 'var(--warning)' : 'inherit' }}>{metrics.approvals?.pending || 0}</div>
                                            </div>
                                            <div className="card" style={{ position: 'relative' }}>
                                                <div className="metric-tooltip-trigger" style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px', cursor: 'help', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                                                    {t('agent.status.24hActions')}
                                                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="8" cy="8" r="6.5" /><path d="M8 7v4M8 5.5v0" /></svg>
                                                    <span className="metric-tooltip">{t('agent.status.24hActionsTooltip')}</span>
                                                </div>
                                                <div style={{ fontSize: '22px', fontWeight: 600 }}>{metrics.activity?.actions_last_24h || 0}</div>
                                            </div>
                                        </>
                                    )}
                                </>)}
                                {/* OpenClaw-specific metrics */}
                                {(agent as any)?.agent_type === 'openclaw' && (
                                    <div className="card">
                                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>
                                            {t('agent.openclaw.lastSeen')}
                                        </div>
                                        <div style={{ fontSize: '16px', fontWeight: 500 }}>
                                            {(agent as any).openclaw_last_seen
                                                ? new Date((agent as any).openclaw_last_seen).toLocaleString()
                                                : t('agent.openclaw.notConnected')}
                                        </div>
                                    </div>
                                )}
                            </div>

                            {/* Agent Profile & Model Info */}
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '24px' }}>
                                <div className="card">
                                    <h3 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '12px' }}>{t('agent.profile.title')}</h3>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', gap: '12px' }}>
                                            <span style={{ color: 'var(--text-tertiary)', flexShrink: 0 }}>{t('agent.fields.role')}</span>
                                            <span title={agent.role_description || ''} style={{ textAlign: 'right', overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as any }}>{agent.role_description || '—'}</span>
                                        </div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.profile.created')}</span>
                                            <span>{agent.created_at ? formatDate(agent.created_at) : '—'}</span>
                                        </div>
                                        {(agent as any).creator_username && (
                                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                                <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.fields.createdBy', 'Created by')}</span>
                                                <span style={{ color: 'var(--text-secondary)' }}>@{(agent as any).creator_username}</span>
                                            </div>
                                        )}
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.profile.lastActive')}</span>
                                            <span>{agent.last_active_at ? formatDate(agent.last_active_at) : '—'}</span>
                                        </div>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                            <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.profile.timezone')}</span>
                                            <span>{(agent as any).effective_timezone || agent.timezone || 'UTC'}</span>
                                        </div>
                                    </div>
                                </div>
                                {(agent as any)?.agent_type !== 'openclaw' ? (
                                    <div className="card">
                                        <h3 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '12px' }}>{t('agent.modelConfig.title')}</h3>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                                <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.modelConfig.model')}</span>
                                                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px' }}>{modelLabel}</span>
                                            </div>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                                <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.modelConfig.provider')}</span>
                                                <span style={{ textTransform: 'capitalize' }}>{modelProvider}</span>
                                            </div>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                                <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.modelConfig.contextRounds')}</span>
                                                <span>{(agent as any).context_window_size || 100}</span>
                                            </div>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="card">
                                        <h3 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '12px' }}>
                                            {t('agent.openclaw.connection')}
                                        </h3>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                                <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.openclaw.type')}</span>
                                                <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                    <span style={{
                                                        fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
                                                        background: 'linear-gradient(135deg, #6366f1, #8b5cf6)', color: '#fff', fontWeight: 600,
                                                    }}>OpenClaw</span>
                                                    Lab
                                                </span>
                                            </div>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                                <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.openclaw.lastSeen')}</span>
                                                <span>{(agent as any).openclaw_last_seen
                                                    ? new Date((agent as any).openclaw_last_seen).toLocaleString()
                                                    : t('agent.openclaw.never')}
                                                </span>
                                            </div>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                                                <span style={{ color: 'var(--text-tertiary)' }}>{t('agent.openclaw.model')}</span>
                                                <span style={{ color: 'var(--text-secondary)' }}>{t('agent.openclaw.managedBy')}</span>
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>

                            {/* Recent Activity */}
                            {activityLogs && activityLogs.length > 0 && (
                                <div className="card">
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                        <h3 style={{ fontSize: '14px', fontWeight: 600 }}>{t('agent.activity.recent', 'Recent Activity')}</h3>
                                        <button className="btn btn-ghost" style={{ fontSize: '12px' }} onClick={() => setActiveTab('activityLog')}>View All →</button>
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                        {activityLogs.slice(0, 5).map((log: any, i: number) => (
                                            <div key={i} style={{ display: 'flex', gap: '12px', alignItems: 'flex-start', padding: '6px 0', borderBottom: i < 4 ? '1px solid var(--border-subtle)' : 'none' }}>
                                                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', minWidth: '60px', flexShrink: 0 }}>
                                                    {new Date(log.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                                </span>
                                                <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{log.summary || log.action_type}</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Quick Actions */}
                            <div style={{ display: 'flex', gap: '10px', marginTop: '20px' }}>
                                <button className="btn btn-secondary" onClick={() => setActiveTab('chat')}>{t('agent.actions.chat')}</button>
                                {(agent as any)?.agent_type !== 'openclaw' && <button className="btn btn-secondary" onClick={() => setActiveTab('aware')}>{t('agent.tabs.aware')}</button>}
                                <button className="btn btn-secondary" onClick={() => setActiveTab('settings')}>{t('agent.tabs.settings')}</button>
                            </div>
                        </div>
                    );
                })()}

                {/* ── Aware Tab ── */}
                {activeTab === 'aware' && (() => {
                    // Parse focus.md into focus items with multi-line descriptions
                    const raw = focusFile?.content || '';
                    const lines = raw.split('\n');
                    const focusItems: { id: string; name: string; description: string; done: boolean; inProgress: boolean }[] = [];
                    let currentItem: any = null;
                    for (const line of lines) {
                        const match = line.match(/^\s*-\s*\[([ x/])\]\s*(.+)/i);
                        if (match) {
                            if (currentItem) focusItems.push(currentItem);
                            const marker = match[1];
                            const fullText = match[2].trim();
                            // Split on first colon: "identifier: description"
                            const colonIdx = fullText.indexOf(':');
                            const itemName = colonIdx > 0 ? fullText.substring(0, colonIdx).trim() : fullText;
                            const itemDesc = colonIdx > 0 ? fullText.substring(colonIdx + 1).trim() : '';
                            currentItem = {
                                id: itemName,
                                name: itemName,
                                description: itemDesc,
                                done: marker.toLowerCase() === 'x',
                                inProgress: marker === '/',
                            };
                        } else if (currentItem && line.trim() && /^\s{2,}/.test(line)) {
                            // Indented continuation line = description
                            currentItem.description = currentItem.description
                                ? currentItem.description + ' ' + line.trim()
                                : line.trim();
                        }
                    }
                    if (currentItem) focusItems.push(currentItem);

                    // Helper: convert trigger config to natural language
                    const triggerToHuman = (trig: any): string => {
                        const isZh = i18n.language?.startsWith('zh');
                        if (trig.type === 'cron' && trig.config?.expr) {
                            const expr = trig.config.expr;
                            const parts = expr.split(' ');
                            if (parts.length >= 5) {
                                const [min, hour, dom, , dow] = parts;
                                const timeStr = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;
                                const dayNames = isZh
                                    ? ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
                                    : ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
                                if (dom !== '*' && dow === '*' && min !== '*' && hour !== '*') {
                                    const days = dom.split(',').join(isZh ? '、' : ', ');
                                    return isZh ? `每月 ${days} 日 ${timeStr}` : `Every month on day ${days} at ${timeStr}`;
                                }
                                if (dow === '*' && min !== '*' && hour !== '*') return isZh ? `每天 ${timeStr}` : `Every day at ${timeStr}`;
                                if (dow === '1-5' && min !== '*' && hour !== '*') return isZh ? `工作日 ${timeStr}` : `Weekdays at ${timeStr}`;
                                if ((dow === '0' || dow === '7') && min !== '*' && hour !== '*') return isZh ? `每周日 ${timeStr}` : `Sundays at ${timeStr}`;
                                if (/^[1-6]$/.test(dow) && min !== '*' && hour !== '*') return isZh ? `每${dayNames[Number(dow)]} ${timeStr}` : `${dayNames[Number(dow)]}s at ${timeStr}`;
                                if (hour === '*' && min === '0') {
                                    if (dow === '1-5') return isZh ? '工作日每小时' : 'Every hour on weekdays';
                                    return isZh ? '每小时' : 'Every hour';
                                }
                                if (hour === '*' && min !== '*') return isZh ? `每小时第 ${min.padStart(2, '0')} 分钟` : `Every hour at :${min.padStart(2, '0')}`;
                            }
                            return isZh ? `Cron：${expr}` : `Cron: ${expr}`;
                        }
                        if (trig.type === 'once' && trig.config?.at) {
                            try {
                                return isZh
                                    ? `一次性：${new Date(trig.config.at).toLocaleString()}`
                                    : `Once at ${new Date(trig.config.at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`;
                            } catch { return isZh ? `一次性：${trig.config.at}` : `Once at ${trig.config.at}`; }
                        }
                        if (trig.type === 'interval' && trig.config?.minutes) {
                            const m = trig.config.minutes;
                            return isZh ? `每 ${m >= 60 ? `${m / 60} 小时` : `${m} 分钟`}` : (m >= 60 ? `Every ${m / 60}h` : `Every ${m} min`);
                        }
                        if (trig.type === 'poll') return `${isZh ? '轮询' : 'Poll'}: ${trig.config?.url?.substring(0, 40) || 'URL'}`;
                        if (trig.type === 'on_message') {
                            const sender = trig.config?.from_agent_name || trig.config?.from_user_name || (isZh ? '未知对象' : 'unknown');
                            return isZh ? `收到 ${sender} 的消息时` : `On message from ${sender}`;
                        }
                        if (trig.type === 'webhook') {
                            return `Webhook${trig.config?.token ? ` (${trig.config.token.substring(0, 6)}...)` : ''}`;
                        }
                        return trig.type;
                    };

                    const triggerReasonText = (trig: any): string | null => {
                        if (!i18n.language?.startsWith('zh')) return trig.reason || null;
                        if (trig.name === 'daily_okr_report') {
                            return '系统触发器：如果启用了日报，收集成员进展、更新滞后的 KR，并生成日报。';
                        }
                        if (trig.name === 'weekly_okr_report') {
                            return '系统触发器：如果启用了周报，收集成员进展、更新滞后的 KR，并生成周报。';
                        }
                        if (trig.name === 'biweekly_okr_checkin') {
                            return '系统触发器：每月 1 日和 15 日进行 OKR 例行检查。';
                        }
                        if (trig.name === 'monthly_okr_report') {
                            return '系统触发器：每月 1 日生成 OKR 月度进展汇报。';
                        }
                        return trig.reason || null;
                    };

                    // Group triggers by focus_ref
                    const triggersByFocus: Record<string, any[]> = {};
                    const standaloneTriggers: any[] = [];
                    for (const trig of awareTriggers) {
                        if (trig.focus_ref && focusItems.some(f => f.name === trig.focus_ref)) {
                            if (!triggersByFocus[trig.focus_ref]) triggersByFocus[trig.focus_ref] = [];
                            triggersByFocus[trig.focus_ref].push(trig);
                        } else {
                            standaloneTriggers.push(trig);
                        }
                    }

                    // Group activity logs by trigger name -> focus_ref
                    const triggerLogsByFocus: Record<string, any[]> = {};
                    const triggerNameToFocus: Record<string, string> = {};
                    for (const trig of awareTriggers) {
                        if (trig.focus_ref) triggerNameToFocus[trig.name] = trig.focus_ref;
                    }
                    const triggerRelatedLogs = activityLogs.filter((log: any) =>
                        log.action_type === 'trigger_fired' || log.action_type === 'trigger_created' ||
                        log.action_type === 'trigger_updated' || log.action_type === 'trigger_cancelled' ||
                        log.summary?.includes('trigger')
                    );
                    for (const log of triggerRelatedLogs) {
                        // Try to match log to a focus item via trigger name in the summary
                        let matched = false;
                        for (const [trigName, focusName] of Object.entries(triggerNameToFocus)) {
                            if (log.summary?.includes(trigName) || log.detail?.tool === trigName) {
                                if (!triggerLogsByFocus[focusName]) triggerLogsByFocus[focusName] = [];
                                triggerLogsByFocus[focusName].push(log);
                                matched = true;
                                break;
                            }
                        }
                        if (!matched) {
                            if (!triggerLogsByFocus['__unmatched__']) triggerLogsByFocus['__unmatched__'] = [];
                            triggerLogsByFocus['__unmatched__'].push(log);
                        }
                    }

                    const hasFocusItems = focusItems.length > 0;
                    const hasStandalone = standaloneTriggers.length > 0;

                    // Split focus items: active first, completed separately
                    const activeFocusItems = focusItems.filter(f => !f.done);
                    const completedFocusItems = focusItems.filter(f => f.done);
                    const visibleActiveFocus = showAllFocus ? activeFocusItems : activeFocusItems.slice(0, SECTION_PAGE_SIZE);
                    const hiddenActiveCount = activeFocusItems.length - visibleActiveFocus.length;

                    // Render a focus item row
                    const renderFocusItem = (item: typeof focusItems[0]) => {
                        const isExpanded = expandedFocus === item.id;
                        const itemTriggers = triggersByFocus[item.name] || [];
                        const itemLogs = triggerLogsByFocus[item.name] || [];
                        const displayTitle = item.description || item.name;
                        const displaySubtitle = item.description ? item.name : null;

                        return (
                            <div key={item.id} style={{
                                borderRadius: '8px',
                                border: '1px solid var(--border-subtle)',
                                overflow: 'hidden',
                                marginBottom: '6px',
                                background: 'var(--bg-primary)',
                            }}>
                                {/* Focus Item Header */}
                                <div
                                    onClick={() => setExpandedFocus(isExpanded ? null : item.id)}
                                    style={{
                                        padding: '12px 16px',
                                        display: 'flex',
                                        alignItems: 'flex-start',
                                        gap: '12px',
                                        cursor: 'pointer',
                                        transition: 'background 0.15s',
                                    }}
                                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-secondary)')}
                                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                                >
                                    {/* Status indicator */}
                                    <div style={{
                                        width: '8px', height: '8px', borderRadius: '50%', marginTop: '5px', flexShrink: 0,
                                        background: item.done ? 'var(--success, #10b981)' : item.inProgress ? 'var(--accent-primary)' : 'var(--border-subtle)',
                                    }} />
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{
                                            fontSize: '13px', fontWeight: 500, lineHeight: '20px',
                                            textDecoration: item.done ? 'line-through' : 'none',
                                            color: item.done ? 'var(--text-tertiary)' : 'var(--text-primary)',
                                        }}>{displayTitle}</div>
                                        {displaySubtitle && (
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'monospace', marginTop: '2px' }}>
                                                {displaySubtitle}
                                            </div>
                                        )}
                                    </div>
                                    {/* Trigger count badge */}
                                    {itemTriggers.length > 0 && (
                                        <span style={{
                                            fontSize: '11px', color: 'var(--text-tertiary)',
                                            padding: '2px 8px', borderRadius: '10px',
                                            background: 'var(--bg-secondary)',
                                            whiteSpace: 'nowrap',
                                        }}>
                                            {i18n.language?.startsWith('zh')
                                                ? `${itemTriggers.length} 个触发器`
                                                : `${itemTriggers.length} trigger${itemTriggers.length > 1 ? 's' : ''}`}
                                        </span>
                                    )}
                                    {/* Expand arrow */}
                                    <span style={{
                                        fontSize: '11px', color: 'var(--text-tertiary)',
                                        transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
                                        transition: 'transform 0.15s',
                                        marginTop: '4px',
                                    }}>&#9654;</span>
                                </div>

                                {/* Expanded content */}
                                {isExpanded && (
                                    <div style={{ padding: '0 16px 12px 36px', borderTop: '1px solid var(--border-subtle)' }}>
                                        {/* Nested Triggers */}
                                        {itemTriggers.length > 0 && (
                                            <div style={{ marginTop: '12px' }}>
                                                {itemTriggers.map((trig: any) => (
                                                    <div key={trig.id} style={{
                                                        display: 'flex', alignItems: 'center', gap: '10px',
                                                        padding: '8px 12px', marginBottom: '4px',
                                                        borderRadius: '6px', background: 'var(--bg-secondary)',
                                                        opacity: trig.is_enabled ? 1 : 0.5,
                                                    }}>
                                                        <div style={{ flex: 1 }}>
                                                            <div style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-primary)' }}>
                                                                {triggerToHuman(trig)}
                                                            </div>
                                                            {triggerReasonText(trig) && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{triggerReasonText(trig)}</div>}
                                                            <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '2px', fontFamily: 'monospace' }}>
                                                                {trig.type === 'cron' ? trig.config?.expr : ''}{' '}
                                                            </div>
                                                        </div>
                                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                                                            {t('agent.aware.fired', { count: trig.fire_count })}
                                                        </span>
                                                        {!trig.is_enabled && (
                                                            <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>{t('agent.aware.disabled')}</span>
                                                        )}
                                                        <div style={{ display: 'flex', gap: '4px' }}>
                                                            <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px' }}
                                                                onClick={async (e) => {
                                                                    e.stopPropagation();
                                                                    await triggerApi.update(id!, trig.id, { is_enabled: !trig.is_enabled });
                                                                    refetchTriggers();
                                                                }}>
                                                                {trig.is_enabled ? t('agent.aware.disable') : t('agent.aware.enable')}
                                                            </button>
                                                            <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px', color: 'var(--error)' }}
                                                                onClick={async (e) => {
                                                                    e.stopPropagation();
                                                                    if (confirm(t('agent.aware.deleteTriggerConfirm', { name: trig.name }))) {
                                                                        await triggerApi.delete(id!, trig.id);
                                                                        refetchTriggers();
                                                                    }
                                                                }}>
                                                                {t('common.delete', 'Delete')}
                                                            </button>
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        )}

                                        {/* Activity Logs for this focus */}
                                        {itemLogs.length > 0 && (
                                            <div style={{ marginTop: '12px' }}>
                                                <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: '6px' }}>
                                                    {t('agent.aware.reflections')}
                                                </div>
                                                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                                    {itemLogs.slice(0, 10).map((log: any) => (
                                                        <div key={log.id} style={{
                                                            padding: '6px 12px', borderRadius: '6px',
                                                            background: 'var(--bg-secondary)',
                                                            borderLeft: '2px solid var(--border-subtle)',
                                                        }}>
                                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '2px' }}>
                                                                <span style={{
                                                                    fontSize: '10px', padding: '1px 5px', borderRadius: '3px',
                                                                    background: log.action_type === 'trigger_fired' ? 'rgba(var(--accent-primary-rgb, 99,102,241), 0.1)' : 'var(--bg-tertiary, #e5e7eb)',
                                                                    color: log.action_type === 'trigger_fired' ? 'var(--accent-primary)' : 'var(--text-tertiary)',
                                                                    fontWeight: 500,
                                                                }}>{log.action_type?.replace('trigger_', '')}</span>
                                                                <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                                                                    {new Date(log.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                                                                </span>
                                                            </div>
                                                            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{log.summary}</div>
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}

                                        {itemTriggers.length === 0 && itemLogs.length === 0 && (
                                            <div style={{ padding: '12px 0', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                                                {t('agent.aware.noTriggers')}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    };

                    return (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                            {/* ── Focus Section ── */}
                            <div className="card" style={{ marginBottom: '16px', padding: '16px' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                    <div>
                                        <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 600 }}>{t('agent.aware.focus')}</h4>
                                        <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('agent.aware.focusDesc')}</span>
                                    </div>
                                    {hasFocusItems && (
                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            {i18n.language?.startsWith('zh')
                                                ? `${activeFocusItems.length} 个进行中${completedFocusItems.length > 0 ? ` · ${completedFocusItems.length} 个已完成` : ''}`
                                                : `${activeFocusItems.length} active${completedFocusItems.length > 0 ? ` · ${completedFocusItems.length} done` : ''}`}
                                        </span>
                                    )}
                                </div>

                                {/* Active Focus Items */}
                                {visibleActiveFocus.map(renderFocusItem)}

                                {/* Show more active items */}
                                {hiddenActiveCount > 0 && (
                                    <button
                                        onClick={() => setShowAllFocus(true)}
                                        className="btn btn-ghost"
                                        style={{ width: '100%', fontSize: '12px', color: 'var(--text-tertiary)', padding: '8px', marginTop: '4px' }}
                                    >
                                        {t('agent.aware.showMore', { count: hiddenActiveCount })}
                                    </button>
                                )}
                                {showAllFocus && activeFocusItems.length > SECTION_PAGE_SIZE && (
                                    <button
                                        onClick={(e) => { setShowAllFocus(false); e.currentTarget.closest('.card')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}
                                        className="btn btn-ghost"
                                        style={{ width: '100%', fontSize: '12px', color: 'var(--text-tertiary)', padding: '8px', marginTop: '4px' }}
                                    >
                                        {t('agent.aware.showLess')}
                                    </button>
                                )}

                                {/* Completed Focus Items — auto-collapsed */}
                                {completedFocusItems.length > 0 && (
                                    <>
                                        <button
                                            onClick={() => setShowCompletedFocus(!showCompletedFocus)}
                                            className="btn btn-ghost"
                                            style={{
                                                width: '100%', fontSize: '12px', color: 'var(--text-tertiary)',
                                                padding: '8px', marginTop: '8px',
                                                borderTop: '1px solid var(--border-subtle)',
                                                borderRadius: 0,
                                            }}
                                        >
                                            {showCompletedFocus
                                                ? t('agent.aware.hideCompleted')
                                                : t('agent.aware.showCompleted', { count: completedFocusItems.length })
                                            }
                                        </button>
                                        {showCompletedFocus && completedFocusItems.map(renderFocusItem)}
                                    </>
                                )}

                                {/* Empty state */}
                                {!hasFocusItems && (
                                    <div style={{
                                        padding: '24px', textAlign: 'center', color: 'var(--text-tertiary)',
                                        border: '1px dashed var(--border-subtle)', borderRadius: '8px',
                                    }}>
                                        {t('agent.aware.focusEmpty')}
                                    </div>
                                )}
                            </div>
                            {/* ── Standalone Triggers Card ── */}
                            {hasStandalone && (
                                <div className="card" style={{ marginBottom: '16px', padding: '16px' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                        <div>
                                            <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 600 }}>{t('agent.aware.standaloneTriggers')}</h4>
                                        </div>
                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            {i18n.language?.startsWith('zh')
                                                ? `${standaloneTriggers.length} 个触发器`
                                                : `${standaloneTriggers.length} trigger${standaloneTriggers.length > 1 ? 's' : ''}`}
                                        </span>
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                        {[...standaloneTriggers].sort((a: any, b: any) => (b.is_enabled ? 1 : 0) - (a.is_enabled ? 1 : 0)).slice(0, showAllTriggers ? undefined : SECTION_PAGE_SIZE).map((trig: any) => (
                                            <div key={trig.id} style={{
                                                padding: '10px 14px', borderRadius: '8px',
                                                border: '1px solid var(--border-subtle)',
                                                display: 'flex', alignItems: 'center', gap: '10px',
                                                opacity: trig.is_enabled ? 1 : 0.5,
                                                background: 'var(--bg-primary)',
                                            }}>
                                                <div style={{ flex: 1 }}>
                                                    <div style={{ fontSize: '13px', fontWeight: 500 }}>{triggerToHuman(trig)}</div>
                                                    {triggerReasonText(trig) && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{triggerReasonText(trig)}</div>}
                                                    <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', fontFamily: 'monospace', marginTop: '2px' }}>
                                                        {trig.name}{trig.type === 'cron' ? ` · ${trig.config?.expr}` : ''}
                                                    </div>
                                                </div>
                                                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                                                    {t('agent.aware.fired', { count: trig.fire_count })}
                                                </span>
                                                {!trig.is_enabled && (
                                                    <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>{t('agent.aware.disabled')}</span>
                                                )}
                                                <div style={{ display: 'flex', gap: '4px' }}>
                                                    <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px' }}
                                                        onClick={async () => {
                                                            await triggerApi.update(id!, trig.id, { is_enabled: !trig.is_enabled });
                                                            refetchTriggers();
                                                        }}>
                                                        {trig.is_enabled ? t('agent.aware.disable') : t('agent.aware.enable')}
                                                    </button>
                                                    <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px', color: 'var(--error)' }}
                                                        onClick={async () => {
                                                            if (confirm(t('agent.aware.deleteTriggerConfirm', { name: trig.name }))) {
                                                                await triggerApi.delete(id!, trig.id);
                                                                refetchTriggers();
                                                            }
                                                        }}>
                                                        {t('common.delete', 'Delete')}
                                                    </button>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                    {standaloneTriggers.length > SECTION_PAGE_SIZE && (
                                        <button
                                            onClick={(e) => { const collapse = showAllTriggers; setShowAllTriggers(!showAllTriggers); if (collapse) e.currentTarget.closest('.card')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}
                                            className="btn btn-ghost"
                                            style={{ width: '100%', fontSize: '12px', color: 'var(--text-tertiary)', padding: '8px', marginTop: '4px' }}
                                        >
                                            {showAllTriggers
                                                ? (i18n.language?.startsWith('zh') ? '收起' : 'Show less')
                                                : (i18n.language?.startsWith('zh') ? `显示更多 ${standaloneTriggers.length - SECTION_PAGE_SIZE} 项...` : `Show ${standaloneTriggers.length - SECTION_PAGE_SIZE} more...`)
                                            }
                                        </button>
                                    )}
                                </div>
                            )}

                            {/* Raw markdown toggle */}
                            {raw && (
                                <details style={{ marginTop: '4px', marginBottom: '16px' }}>
                                    <summary style={{ fontSize: '11px', color: 'var(--text-tertiary)', cursor: 'pointer' }}>{t('agent.aware.viewRawMarkdown')}</summary>
                                    <pre style={{ fontSize: '11px', marginTop: '8px', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px', whiteSpace: 'pre-wrap', maxHeight: '300px', overflow: 'auto' }}>{raw}</pre>
                                </details>
                            )}

                            {reflectionSessions.length > 0 && (() => {
                                const totalPages = Math.ceil(reflectionSessions.length / REFLECTIONS_PAGE_SIZE);
                                const pageStart = reflectionPage * REFLECTIONS_PAGE_SIZE;
                                const visibleSessions = reflectionSessions.slice(pageStart, pageStart + REFLECTIONS_PAGE_SIZE);
                                return (
                                    <div className="card" style={{ padding: '16px' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                            <div>
                                                <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 600 }}>{t('agent.aware.reflections')}</h4>
                                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('agent.aware.reflectionsDesc')}</span>
                                            </div>
                                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                {reflectionSessions.length} session{reflectionSessions.length > 1 ? 's' : ''}
                                            </span>
                                        </div>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                            {visibleSessions.map((session: any) => {
                                                const isExpanded = expandedReflection === session.id;
                                                const msgs = reflectionMessages[session.id] || [];
                                                return (
                                                    <div key={session.id} style={{
                                                        borderRadius: '8px',
                                                        border: '1px solid var(--border-subtle)',
                                                        overflow: 'hidden',
                                                        background: 'var(--bg-primary)',
                                                    }}>
                                                        <div
                                                            onClick={async () => {
                                                                if (isExpanded) {
                                                                    setExpandedReflection(null);
                                                                    return;
                                                                }
                                                                setExpandedReflection(session.id);
                                                                if (!reflectionMessages[session.id]) {
                                                                    try {
                                                                        const tkn = localStorage.getItem('token');
                                                                        const res = await fetch(`/api/agents/${id}/sessions/${session.id}/messages`, {
                                                                            headers: { Authorization: `Bearer ${tkn}` },
                                                                        });
                                                                        if (res.ok) {
                                                                            const data = await res.json();
                                                                            setReflectionMessages(prev => ({ ...prev, [session.id]: data }));
                                                                        }
                                                                    } catch { /* ignore */ }
                                                                }
                                                            }}
                                                            style={{
                                                                padding: '10px 16px',
                                                                display: 'flex', alignItems: 'center', gap: '10px',
                                                                cursor: 'pointer', transition: 'background 0.15s',
                                                            }}
                                                            onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-secondary)')}
                                                            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                                                        >
                                                            <div style={{
                                                                width: '6px', height: '6px', borderRadius: '50%',
                                                                background: 'var(--accent-primary)', flexShrink: 0,
                                                            }} />
                                                            <div style={{ flex: 1, minWidth: 0 }}>
                                                                <div style={{ fontSize: '12px', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                                    {(session.title || 'Trigger execution').replace(/^🤖\s*/, '')}
                                                                </div>
                                                                <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginTop: '1px' }}>
                                                                    {new Date(session.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                                                                    {session.message_count > 0 && ` · ${session.message_count} msg`}
                                                                </div>
                                                            </div>
                                                            <span style={{
                                                                fontSize: '11px', color: 'var(--text-tertiary)',
                                                                transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
                                                                transition: 'transform 0.15s',
                                                            }}>&#9654;</span>
                                                        </div>
                                                        {isExpanded && (
                                                            <div style={{ padding: '0 16px 12px', borderTop: '1px solid var(--border-subtle)' }}>
                                                                {msgs.length === 0 ? (
                                                                    <div style={{ padding: '12px 0', fontSize: '12px', color: 'var(--text-tertiary)' }}>Loading...</div>
                                                                ) : (
                                                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '8px' }}>
                                                                        {msgs.map((msg: any, mi: number) => {
                                                                            if (msg.role === 'tool_call') {
                                                                                const tName = msg.toolName || (() => { try { return JSON.parse(msg.content || '{}').name; } catch { return ''; } })() || 'tool';
                                                                                const tArgs = msg.toolArgs || (() => { try { return JSON.parse(msg.content || '{}').args; } catch { return {}; } })();
                                                                                const tResult = msg.toolResult || '';
                                                                                const argsStr = typeof tArgs === 'string' ? tArgs : JSON.stringify(tArgs || {}, null, 2);
                                                                                const resultStr = typeof tResult === 'string' ? tResult : JSON.stringify(tResult, null, 2);
                                                                                const hasDetail = argsStr.length > 60 || resultStr;
                                                                                const Tag = hasDetail ? 'details' : 'div';
                                                                                const HeaderTag = hasDetail ? 'summary' : 'div';
                                                                                return (
                                                                                    <Tag key={mi} style={{ borderRadius: '6px', background: 'var(--bg-secondary)', overflow: 'hidden' }}>
                                                                                        <HeaderTag style={{
                                                                                            padding: '5px 10px',
                                                                                            fontSize: '11px', cursor: hasDetail ? 'pointer' : 'default',
                                                                                            display: 'flex', alignItems: 'center', gap: '8px',
                                                                                            listStyle: 'none',
                                                                                            WebkitAppearance: 'none',
                                                                                        } as any}>
                                                                                            {hasDetail && <span style={{ fontSize: '8px', color: 'var(--text-tertiary)', flexShrink: 0 }}>&#9654;</span>}
                                                                                            <span style={{
                                                                                                fontWeight: 600, fontSize: '10px', color: 'var(--text-primary)',
                                                                                                padding: '1px 6px', borderRadius: '3px',
                                                                                                background: 'var(--bg-tertiary, rgba(0,0,0,0.06))',
                                                                                                flexShrink: 0, fontFamily: 'monospace',
                                                                                            }}>{tName}</span>
                                                                                            <span style={{
                                                                                                color: 'var(--text-tertiary)', fontFamily: 'monospace', fontSize: '10px',
                                                                                                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                                                                            }}>
                                                                                                {argsStr.replace(/\n/g, ' ').substring(0, 60)}{argsStr.length > 60 ? '...' : ''}
                                                                                            </span>
                                                                                        </HeaderTag>
                                                                                        {hasDetail && (
                                                                                            <div style={{
                                                                                                padding: '8px 10px', borderTop: '1px solid var(--border-subtle)',
                                                                                                fontFamily: 'monospace', fontSize: '10px', lineHeight: 1.5,
                                                                                                whiteSpace: 'pre-wrap', maxHeight: '200px', overflow: 'auto',
                                                                                                color: 'var(--text-secondary)',
                                                                                            }}>
                                                                                                {argsStr}
                                                                                                {resultStr && (
                                                                                                    <>
                                                                                                        <div style={{ borderTop: '1px dashed var(--border-subtle)', margin: '6px 0', opacity: 0.5 }} />
                                                                                                        <span style={{ color: 'var(--text-tertiary)' }}>→ </span>{resultStr.substring(0, 500)}
                                                                                                    </>
                                                                                                )}
                                                                                            </div>
                                                                                        )}
                                                                                    </Tag>
                                                                                );
                                                                            }
                                                                            if (msg.role === 'tool_result') {
                                                                                const tName = msg.toolName || (() => { try { return JSON.parse(msg.content || '{}').name; } catch { return ''; } })() || 'result';
                                                                                const tResult = msg.toolResult || msg.content || '';
                                                                                const resultStr = typeof tResult === 'string' ? tResult : JSON.stringify(tResult, null, 2);
                                                                                if (!resultStr) return null;
                                                                                return (
                                                                                    <details key={mi} style={{ borderRadius: '6px', background: 'var(--bg-secondary)', overflow: 'hidden' }}>
                                                                                        <summary style={{
                                                                                            padding: '5px 10px',
                                                                                            fontSize: '11px', cursor: 'pointer',
                                                                                            display: 'flex', alignItems: 'center', gap: '8px',
                                                                                            listStyle: 'none',
                                                                                            WebkitAppearance: 'none',
                                                                                        } as any}>
                                                                                            <span style={{ fontSize: '8px', color: 'var(--text-tertiary)', flexShrink: 0 }}>&#9654;</span>
                                                                                            <span style={{
                                                                                                fontWeight: 600, fontSize: '10px', color: 'var(--text-primary)',
                                                                                                padding: '1px 6px', borderRadius: '3px',
                                                                                                background: 'var(--bg-tertiary, rgba(0,0,0,0.06))',
                                                                                                flexShrink: 0, fontFamily: 'monospace',
                                                                                            }}>{tName}</span>
                                                                                            <span style={{
                                                                                                color: 'var(--text-tertiary)', fontFamily: 'monospace', fontSize: '10px',
                                                                                                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                                                                            }}>
                                                                                                → {resultStr.replace(/\n/g, ' ').substring(0, 80)}
                                                                                            </span>
                                                                                        </summary>
                                                                                        <div style={{
                                                                                            padding: '8px 10px', borderTop: '1px solid var(--border-subtle)',
                                                                                            fontFamily: 'monospace', fontSize: '10px', lineHeight: 1.5,
                                                                                            whiteSpace: 'pre-wrap', maxHeight: '200px', overflow: 'auto',
                                                                                            color: 'var(--text-secondary)',
                                                                                        }}>
                                                                                            {resultStr.substring(0, 1000)}
                                                                                        </div>
                                                                                    </details>
                                                                                );
                                                                            }
                                                                            if (msg.role === 'assistant') {
                                                                                return (
                                                                                    <div key={mi} style={{
                                                                                        padding: '8px 10px', borderRadius: '6px',
                                                                                        background: 'var(--bg-secondary)',
                                                                                        fontSize: '12px', color: 'var(--text-primary)',
                                                                                        whiteSpace: 'pre-wrap', lineHeight: '1.5',
                                                                                        maxHeight: '200px', overflow: 'auto',
                                                                                    }}>
                                                                                        {msg.content}
                                                                                    </div>
                                                                                );
                                                                            }
                                                                            if (msg.role === 'user') {
                                                                                return (
                                                                                    <div key={mi} style={{
                                                                                        padding: '6px 10px', borderRadius: '6px',
                                                                                        background: 'var(--bg-secondary)',
                                                                                        borderLeft: '2px solid var(--border-subtle)',
                                                                                        fontSize: '11px', color: 'var(--text-secondary)',
                                                                                        whiteSpace: 'pre-wrap', maxHeight: '100px', overflow: 'auto',
                                                                                    }}>
                                                                                        {(msg.content || '').substring(0, 300)}
                                                                                    </div>
                                                                                );
                                                                            }
                                                                            return null;
                                                                        })}
                                                                    </div>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                        {/* Pagination controls */}
                                        {totalPages > 1 && (
                                            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px', marginTop: '12px', paddingTop: '8px', borderTop: '1px solid var(--border-subtle)' }}>
                                                <button
                                                    onClick={() => { setReflectionPage(p => Math.max(0, p - 1)); setExpandedReflection(null); }}
                                                    disabled={reflectionPage === 0}
                                                    className="btn btn-ghost"
                                                    style={{ fontSize: '12px', padding: '4px 10px', opacity: reflectionPage === 0 ? 0.3 : 1 }}
                                                >
                                                    {i18n.language?.startsWith('zh') ? '上一页' : 'Prev'}
                                                </button>
                                                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontVariantNumeric: 'tabular-nums' }}>
                                                    {reflectionPage + 1} / {totalPages}
                                                </span>
                                                <button
                                                    onClick={() => { setReflectionPage(p => Math.min(totalPages - 1, p + 1)); setExpandedReflection(null); }}
                                                    disabled={reflectionPage >= totalPages - 1}
                                                    className="btn btn-ghost"
                                                    style={{ fontSize: '12px', padding: '4px 10px', opacity: reflectionPage >= totalPages - 1 ? 0.3 : 1 }}
                                                >
                                                    {i18n.language?.startsWith('zh') ? '下一页' : 'Next'}
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                );
                            })()}
                        </div>
                    );
                })()}


                {/* ── Mind Tab (Soul + Memory + Heartbeat) ── */}
                {
                    activeTab === 'mind' && (() => {
                        const adapter: FileBrowserApi = {
                            list: (p) => fileApi.list(id!, p),
                            read: (p) => fileApi.read(id!, p),
                            write: (p, c) => fileApi.write(id!, p, c),
                            delete: (p) => fileApi.delete(id!, p),
                            downloadUrl: (p) => fileApi.downloadUrl(id!, p),
                        };
                        return (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
                                {/* Soul Section */}
                                <div>
                                    <h3 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        🧬 {t('agent.soul.title')}
                                    </h3>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                        {t('agent.mind.soulDesc', 'Core identity, personality, and behavior boundaries.')}
                                    </p>
                                    <FileBrowser api={adapter} singleFile="soul.md" title="" features={{ edit: (agent as any)?.access_level !== 'use' }} />
                                </div>

                                {/* Memory Section */}
                                <div>
                                    <h3 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        🧠 {t('agent.memory.title')}
                                    </h3>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                        {t('agent.mind.memoryDesc', 'Persistent memory accumulated through conversations and experiences.')}
                                    </p>
                                    <FileBrowser api={adapter} rootPath="memory" readOnly features={{}} />
                                </div>

                                {/* Heartbeat Section */}
                                <div>
                                    <h3 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        💓 {t('agent.mind.heartbeatTitle', 'Heartbeat')}
                                    </h3>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                        {t('agent.mind.heartbeatDesc', 'Instructions for periodic awareness checks. The agent reads this file during each heartbeat.')}
                                    </p>
                                    <FileBrowser api={adapter} singleFile="HEARTBEAT.md" title="" features={{ edit: (agent as any)?.access_level !== 'use' }} />
                                </div>
                            </div>
                        );
                    })()
                }

                {/* ── Tools Tab ── */}
                {
                    activeTab === 'tools' && (
                        <div>
                            <div style={{ marginBottom: '16px' }}>
                                <h3 style={{ marginBottom: '4px' }}>{t('agent.toolMgmt.title')}</h3>
                                <p style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>{t('agent.toolMgmt.description')}</p>
                            </div>
                            <ToolsManager agentId={id!} canManage={canManage} />
                        </div>
                    )
                }

                {/* ── Skills Tab ── */}
                {
                    activeTab === 'skills' && (() => {
                        const adapter: FileBrowserApi = {
                            list: (p) => fileApi.list(id!, p),
                            read: (p) => fileApi.read(id!, p),
                            write: (p, c) => fileApi.write(id!, p, c),
                            delete: (p) => fileApi.delete(id!, p),
                            upload: (file, path, onProgress) => fileApi.upload(id!, file, path, onProgress),
                            downloadUrl: (p) => fileApi.downloadUrl(id!, p),
                        };
                        return (
                            <div>
                                <div style={{ marginBottom: '16px' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                        <div>
                                            <h3 style={{ marginBottom: '4px' }}>{t('agent.skills.title')}</h3>
                                            <p style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>{t('agent.skills.description')}</p>
                                        </div>
                                        <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
                                            <button
                                                className="btn btn-secondary"
                                                style={{ fontSize: '13px' }}
                                                onClick={() => { setShowAgentUrlImport(true); setAgentUrlInput(''); }}
                                            >
                                                Import from URL
                                            </button>
                                            <button
                                                className="btn btn-secondary"
                                                style={{ fontSize: '13px' }}
                                                onClick={() => { setShowAgentClawhub(true); setAgentClawhubQuery(''); setAgentClawhubResults([]); }}
                                            >
                                                Browse ClawHub
                                            </button>
                                            <button
                                                className="btn btn-primary"
                                                style={{ display: 'flex', alignItems: 'center', gap: '6px', whiteSpace: 'nowrap' }}
                                                onClick={() => setShowImportSkillModal(true)}
                                            >
                                                Import from Presets
                                            </button>
                                        </div>
                                    </div>
                                    <div style={{ marginTop: '8px', padding: '10px 14px', background: 'var(--bg-secondary)', borderRadius: '8px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                                        <strong>Skill Format:</strong><br />
                                        • <code>skills/my-skill/SKILL.md</code> — {t('agent.skills.folderFormat', 'Each skill is a folder with a SKILL.md file and optional auxiliary files (scripts/, examples/)')}
                                    </div>
                                </div>
                                <FileBrowser api={adapter} rootPath="skills" features={{ newFile: true, edit: true, delete: true, newFolder: true, upload: true, directoryNavigation: true }} title={t('agent.skills.skillFiles')} />

                                {/* Browse ClawHub Modal */}
                                {showAgentClawhub && (
                                    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowAgentClawhub(false)}>
                                        <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', maxWidth: '600px', width: '90%', maxHeight: '70vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                                <h3>Browse ClawHub</h3>
                                                <button onClick={() => setShowAgentClawhub(false)} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)', padding: '4px 8px' }}>x</button>
                                            </div>
                                            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: '0 0 12px' }}>
                                                Search and install skills from ClawHub directly into this agent's workspace.
                                            </p>
                                            <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
                                                <input
                                                    className="input"
                                                    placeholder="Search skills..."
                                                    value={agentClawhubQuery}
                                                    onChange={e => setAgentClawhubQuery(e.target.value)}
                                                    onKeyDown={e => {
                                                        if (e.key === 'Enter' && agentClawhubQuery.trim()) {
                                                            setAgentClawhubSearching(true);
                                                            skillApi.clawhub.search(agentClawhubQuery).then(r => { setAgentClawhubResults(r); setAgentClawhubSearching(false); }).catch(() => setAgentClawhubSearching(false));
                                                        }
                                                    }}
                                                    style={{ flex: 1, fontSize: '13px' }}
                                                />
                                                <button
                                                    className="btn btn-primary"
                                                    style={{ fontSize: '13px' }}
                                                    disabled={!agentClawhubQuery.trim() || agentClawhubSearching}
                                                    onClick={() => {
                                                        setAgentClawhubSearching(true);
                                                        skillApi.clawhub.search(agentClawhubQuery).then(r => { setAgentClawhubResults(r); setAgentClawhubSearching(false); }).catch(() => setAgentClawhubSearching(false));
                                                    }}
                                                >
                                                    {agentClawhubSearching ? 'Searching...' : 'Search'}
                                                </button>
                                            </div>
                                            <div style={{ flex: 1, overflowY: 'auto' }}>
                                                {agentClawhubResults.length === 0 && !agentClawhubSearching && (
                                                    <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-tertiary)', fontSize: '13px' }}>Search ClawHub to find skills</div>
                                                )}
                                                {agentClawhubResults.map((r: any) => (
                                                    <div key={r.slug} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', borderRadius: '8px', marginBottom: '6px', border: '1px solid var(--border-subtle)', background: 'var(--bg-secondary)' }}>
                                                        <div style={{ flex: 1 }}>
                                                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                                <span style={{ fontWeight: 600, fontSize: '13px' }}>{r.displayName || r.slug}</span>
                                                                {r.version && <span style={{ fontSize: '10px', color: 'var(--accent-text)', background: 'var(--accent-subtle)', padding: '1px 5px', borderRadius: '4px' }}>v{r.version}</span>}
                                                            </div>
                                                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{r.summary?.substring(0, 100)}{r.summary?.length > 100 ? '...' : ''}</div>
                                                            {r.updatedAt && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px', opacity: 0.7 }}>Updated {new Date(r.updatedAt).toLocaleDateString()}</div>}
                                                        </div>
                                                        <button
                                                            className="btn btn-secondary"
                                                            style={{ fontSize: '12px', padding: '5px 12px', marginLeft: '12px' }}
                                                            disabled={agentClawhubInstalling === r.slug}
                                                            onClick={async () => {
                                                                setAgentClawhubInstalling(r.slug);
                                                                try {
                                                                    const res = await skillApi.agentImport.fromClawhub(id!, r.slug);
                                                                    alert(`Installed "${r.displayName || r.slug}" (${res.files_written} files)`);
                                                                    queryClient.invalidateQueries({ queryKey: ['files', id, 'skills'] });
                                                                } catch (err: any) {
                                                                    alert(`Import failed: ${err?.message || err}`);
                                                                } finally {
                                                                    setAgentClawhubInstalling(null);
                                                                }
                                                            }}
                                                        >
                                                            {agentClawhubInstalling === r.slug ? 'Installing...' : 'Install'}
                                                        </button>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    </div>
                                )}

                                {/* Import from URL Modal */}
                                {showAgentUrlImport && (
                                    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowAgentUrlImport(false)}>
                                        <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', maxWidth: '500px', width: '90%', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                                <h3>Import from GitHub URL</h3>
                                                <button onClick={() => setShowAgentUrlImport(false)} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)', padding: '4px 8px' }}>x</button>
                                            </div>
                                            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: '0 0 12px' }}>
                                                Paste a GitHub URL pointing to a skill directory (must contain SKILL.md).
                                            </p>
                                            <input
                                                className="input"
                                                placeholder="https://github.com/owner/repo/tree/main/path/to/skill"
                                                value={agentUrlInput}
                                                onChange={e => setAgentUrlInput(e.target.value)}
                                                style={{ width: '100%', fontSize: '13px', marginBottom: '12px', boxSizing: 'border-box' }}
                                            />
                                            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                                                <button className="btn btn-secondary" onClick={() => setShowAgentUrlImport(false)}>Cancel</button>
                                                <button
                                                    className="btn btn-primary"
                                                    disabled={!agentUrlInput.trim() || agentUrlImporting}
                                                    onClick={async () => {
                                                        setAgentUrlImporting(true);
                                                        try {
                                                            const res = await skillApi.agentImport.fromUrl(id!, agentUrlInput.trim());
                                                            alert(`Imported ${res.files_written} files`);
                                                            queryClient.invalidateQueries({ queryKey: ['files', id, 'skills'] });
                                                            setShowAgentUrlImport(false);
                                                        } catch (err: any) {
                                                            alert(`Import failed: ${err?.message || err}`);
                                                        } finally {
                                                            setAgentUrlImporting(false);
                                                        }
                                                    }}
                                                >
                                                    {agentUrlImporting ? 'Importing...' : 'Import'}
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                )}

                                {/* Import from Presets Modal */}
                                {showImportSkillModal && (
                                    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowImportSkillModal(false)}>
                                        <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', maxWidth: '600px', width: '90%', maxHeight: '70vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                                                <h3>{t('agent.skills.importPreset', 'Import from Presets')}</h3>
                                                <button onClick={() => setShowImportSkillModal(false)} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)', padding: '4px 8px' }}>✕</button>
                                            </div>
                                            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: '0 0 16px' }}>
                                                {t('agent.skills.importDesc', 'Select a preset skill to import into this agent. All skill files will be copied to the agent\'s skills folder.')}
                                            </p>
                                            <div style={{ flex: 1, overflowY: 'auto' }}>
                                                {!globalSkillsForImport ? (
                                                    <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-tertiary)' }}>Loading...</div>
                                                ) : globalSkillsForImport.length === 0 ? (
                                                    <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-tertiary)' }}>No preset skills available</div>
                                                ) : (
                                                    globalSkillsForImport.map((skill: any) => (
                                                        <div
                                                            key={skill.id}
                                                            style={{
                                                                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                                                padding: '12px 14px', borderRadius: '8px', marginBottom: '8px',
                                                                border: '1px solid var(--border-subtle)', background: 'var(--bg-secondary)',
                                                                transition: 'border-color 0.15s',
                                                            }}
                                                            onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--accent-primary)')}
                                                            onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--border-subtle)')}
                                                        >
                                                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flex: 1 }}>
                                                                <span style={{ fontSize: '20px' }}>{skill.icon || '📋'}</span>
                                                                <div>
                                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>{skill.name}</div>
                                                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                                        {skill.description?.substring(0, 100)}{skill.description?.length > 100 ? '...' : ''}
                                                                    </div>
                                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                                        📁 {skill.folder_name}
                                                                        {skill.is_default && <span style={{ marginLeft: '8px', color: 'var(--accent-primary)', fontWeight: 600 }}>✓ Default</span>}
                                                                    </div>
                                                                </div>
                                                            </div>
                                                            <button
                                                                className="btn btn-secondary"
                                                                style={{ whiteSpace: 'nowrap', fontSize: '12px', padding: '6px 14px' }}
                                                                disabled={importingSkillId === skill.id}
                                                                onClick={async () => {
                                                                    setImportingSkillId(skill.id);
                                                                    try {
                                                                        const res = await fileApi.importSkill(id!, skill.id);
                                                                        alert(`✅ Imported "${skill.name}" (${res.files_written} files)`);
                                                                        queryClient.invalidateQueries({ queryKey: ['files', id, 'skills'] });
                                                                        setShowImportSkillModal(false);
                                                                    } catch (err: any) {
                                                                        alert(`❌ Import failed: ${err?.message || err}`);
                                                                    } finally {
                                                                        setImportingSkillId(null);
                                                                    }
                                                                }}
                                                            >
                                                                {importingSkillId === skill.id ? '⏳ ...' : '⬇️ Import'}
                                                            </button>
                                                        </div>
                                                    ))
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })()
                }

                {/* ── Relationships Tab ── */}
                {
                    activeTab === 'relationships' && (
                        <RelationshipEditor agentId={id!} readOnly={(agent as any)?.access_level === 'use'} />
                    )
                }

                {/* ── Workspace Tab ── */}
                {
                    activeTab === 'workspace' && (() => {
                        const adapter: FileBrowserApi = {
                            list: (p) => fileApi.list(id!, p),
                            read: (p) => fileApi.read(id!, p),
                            write: (p, c) => fileApi.write(id!, p, c),
                            delete: (p) => fileApi.delete(id!, p),
                            upload: (file, path, onProgress) => fileApi.upload(id!, file, path + '/', onProgress),
                            downloadUrl: (p) => fileApi.downloadUrl(id!, p),
                        };
                        return <FileBrowser api={adapter} rootPath="workspace" features={{ upload: true, newFile: true, newFolder: true, edit: true, delete: true, directoryNavigation: true }} />;
                    })()
                }

                {
                    activeTab === 'chat' && (
                        <div
                            style={{
                                display: 'flex',
                                gap: 0,
                                flex: 1,
                                minHeight: 0,
                                height: 'calc(100vh - 206px)',
                                border: '1px solid color-mix(in srgb, var(--border-subtle) 55%, var(--bg-primary))',
                                borderRadius: '12px',
                                overflow: 'hidden',
                            }}
                        >
                            {/* ── Left: session sidebar ── */}
                            <div className={`session-sidebar ${sessionListCollapsed ? 'collapsed' : ''}`} style={{ width: sessionListCollapsed ? '0px' : '220px', transition: 'width 0.2s ease', flexShrink: 0, minHeight: 0, borderRight: sessionListCollapsed ? 'none' : '1px solid var(--border-subtle)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                                {/* ── Header: title + collapse, then new session ── */}
                                <div style={{ flexShrink: 0 }}>
                                    <div
                                        style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'space-between',
                                            gap: '8px',
                                            padding: '10px 12px 8px',
                                            minHeight: '40px',
                                            boxSizing: 'border-box',
                                        }}
                                    >
                                        <span
                                            style={{
                                                fontSize: '14px',
                                                fontWeight: 600,
                                                color: 'var(--text-primary)',
                                                lineHeight: '1.25',
                                                flex: 1,
                                                minWidth: 0,
                                            }}
                                        >
                                            {t('agent.chat.sessionListTitle')}
                                        </span>
                                        {!sessionListCollapsed && (
                                            <button
                                                type="button"
                                                onClick={() => setSessionListCollapsed(true)}
                                                className="session-sidebar-collapseBtn"
                                                style={{
                                                    display: 'inline-flex',
                                                    alignItems: 'center',
                                                    justifyContent: 'center',
                                                    background: 'none',
                                                    border: 'none',
                                                    color: 'var(--text-tertiary)',
                                                    cursor: 'pointer',
                                                    padding: '4px',
                                                    borderRadius: '4px',
                                                    flexShrink: 0,
                                                    lineHeight: 0,
                                                }}
                                                title={t('agent.chat.collapseSidebar')}
                                                onMouseEnter={(e) => {
                                                    e.currentTarget.style.background = 'var(--bg-secondary)';
                                                }}
                                                onMouseLeave={(e) => {
                                                    e.currentTarget.style.background = 'none';
                                                }}
                                            >
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                                    <path d="M15 18l-6-6 6-6" />
                                                </svg>
                                            </button>
                                        )}
                                    </div>
                                    {!canViewAllAgentChatSessions && (
                                        <div style={{ padding: '0 12px 8px' }}>
                                            <button
                                                type="button"
                                                onClick={createNewSession}
                                                className="new-session-btn"
                                            >
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ display: 'block', flexShrink: 0 }}>
                                                    <line x1="12" y1="5" x2="12" y2="19" />
                                                    <line x1="5" y1="12" x2="19" y2="12" />
                                                </svg>
                                                <span>{t('agent.chat.newSession')}</span>
                                            </button>
                                        </div>
                                    )}
                                </div>

                                {canViewAllAgentChatSessions && (
                                    <div className="session-sidebar-segment-control" role="tablist">
                                        <div
                                            role="tab"
                                            tabIndex={0}
                                            aria-selected={chatScope === 'mine'}
                                            className={`segment-item ${chatScope === 'mine' ? 'active' : ''}`}
                                            onClick={onAdminTabMine}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter' || e.key === ' ') {
                                                    e.preventDefault();
                                                    onAdminTabMine();
                                                }
                                            }}
                                        >
                                            {t('agent.chat.mySessions')}
                                        </div>
                                        <div
                                            role="tab"
                                            tabIndex={0}
                                            aria-selected={chatScope === 'all'}
                                            className={`segment-item ${chatScope === 'all' ? 'active' : ''}`}
                                            onClick={onAdminTabOthers}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter' || e.key === ' ') {
                                                    e.preventDefault();
                                                    onAdminTabOthers();
                                                }
                                            }}
                                        >
                                            {t('agent.chat.otherUsersTab')}
                                        </div>
                                    </div>
                                )}

                                <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                                    {(!canViewAllAgentChatSessions || chatScope === 'mine') ? (
                                        <>
                                            {canViewAllAgentChatSessions && (
                                                <div style={{ padding: '0 12px 8px', flexShrink: 0 }}>
                                                    <button
                                                        type="button"
                                                        onClick={createNewSession}
                                                        className="new-session-btn"
                                                    >
                                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ display: 'block', flexShrink: 0 }}>
                                                            <line x1="12" y1="5" x2="12" y2="19" />
                                                            <line x1="5" y1="12" x2="19" y2="12" />
                                                        </svg>
                                                        <span>{t('agent.chat.newSession')}</span>
                                                    </button>
                                                </div>
                                            )}
                                            <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '4px 0' }}>
                                                {sessionsLoading ? (
                                                    <div style={{ padding: '20px 12px', fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('common.loading')}</div>
                                                ) : sessions.length === 0 ? (
                                                    <div style={{ padding: '20px 12px', fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('agent.chat.noSessionsYet')}<br />{t('agent.chat.clickToStart')}</div>
                                                ) : sessions.map((s: any) => {
                                                    const isActive = activeSession?.id === s.id && (chatScope === 'mine' || !canViewAllAgentChatSessions);
                                                    const channelLabel: Record<string, string> = {
                                                        feishu: t('common.channels.feishu'),
                                                        discord: t('common.channels.discord'),
                                                        slack: t('common.channels.slack'),
                                                        wechat: t('common.channels.wechat'),
                                                        dingtalk: t('common.channels.dingtalk'),
                                                        wecom: t('common.channels.wecom'),
                                                    };
                                                    const chLabel = channelLabel[s.source_channel];
                                                    return (
                                                        <div key={s.id} onClick={() => { setChatScope('mine'); selectSession(s, 'mine'); }}
                                                            className="session-item"
                                                            style={{ padding: '8px 12px', cursor: 'pointer', borderLeft: isActive ? '2px solid var(--accent-primary)' : '2px solid transparent', background: isActive ? 'var(--bg-secondary)' : 'transparent', marginBottom: '1px', display: 'flex', alignItems: 'center', gap: '4px' }}
                                                            onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--bg-secondary)'; }}
                                                            onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}>
                                                            <div style={{ flex: 1, minWidth: 0 }}>
                                                                <div style={{ display: 'flex', alignItems: 'center', gap: '5px', marginBottom: '2px' }}>
                                                                    <div style={{ fontSize: '12px', fontWeight: isActive ? 600 : 400, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>{s.title}</div>
                                                                    {s.is_primary && (
                                                                        <span style={{
                                                                            fontSize: '9px',
                                                                            padding: '1px 4px',
                                                                            borderRadius: '3px',
                                                                            background: 'var(--bg-tertiary)',
                                                                            color: 'var(--text-secondary)',
                                                                            flexShrink: 0,
                                                                            border: '1px solid var(--border-subtle)',
                                                                        }}>
                                                                            {i18n.language === 'zh' ? '主会话' : 'Primary'}
                                                                        </span>
                                                                    )}
                                                                    {s.unread_count > 0 && (
                                                                        <span style={{
                                                                            minWidth: s.unread_count > 9 ? '18px' : '14px',
                                                                            height: s.unread_count > 9 ? '18px' : '14px',
                                                                            padding: s.unread_count > 9 ? '0 4px' : '0',
                                                                            borderRadius: '999px',
                                                                            background: 'var(--text-primary)',
                                                                            color: 'var(--bg-primary)',
                                                                            fontSize: '10px',
                                                                            fontWeight: 600,
                                                                            display: 'flex',
                                                                            alignItems: 'center',
                                                                            justifyContent: 'center',
                                                                            flexShrink: 0,
                                                                        }}>
                                                                            {s.unread_count > 99 ? '99+' : s.unread_count}
                                                                        </span>
                                                                    )}
                                                                    {chLabel && <span style={{ fontSize: '9px', padding: '1px 4px', borderRadius: '3px', background: 'var(--bg-tertiary)', color: 'var(--text-tertiary)', flexShrink: 0 }}>{chLabel}</span>}
                                                                </div>
                                                                <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                                    {s.last_message_at
                                                                        ? new Date(s.last_message_at).toLocaleString(i18n.language === 'zh' ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                                                                        : new Date(s.created_at).toLocaleString(i18n.language === 'zh' ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric' })}
                                                                    {s.message_count > 0 && <span className="session-msg-count" style={{ marginLeft: 'auto' }}>{s.message_count}</span>}
                                                                </div>
                                                            </div>
                                                            <button className="session-del-btn" onClick={(e) => { e.stopPropagation(); deleteSession(s.id); }}
                                                                title={t('chat.deleteSession', 'Delete session')}>
                                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>
                                                            </button>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </>
                                    ) : (
                                        <>
                                            <div style={{ padding: '0 12px 8px', flexShrink: 0 }}>
                                                <select
                                                    value={allUserFilter}
                                                    onChange={(e) => setAllUserFilter(e.target.value)}
                                                    style={{ width: '100%', height: '28px', fontSize: '12px', backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)', borderRadius: '6px', color: 'var(--text-primary)' }}
                                                >
                                                    <option value="">{t('agent.chat.allUsers')}</option>
                                                    {otherUserPickerOptions.map(([uid, label]) => (
                                                        <option key={uid} value={uid}>{label}</option>
                                                    ))}
                                                </select>
                                            </div>
                                            <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '4px 0' }}>
                                                {allSessionsLoading ? (
                                                    <div style={{ padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                                        {[...Array(3)].map((_, i) => (
                                                            <div key={i} style={{ padding: '6px 0', animation: 'pulse 1.5s ease-in-out infinite', animationDelay: `${i * 0.1}s` }}>
                                                                <div style={{ height: '12px', width: `${70 + (i % 3) * 10}%`, background: 'var(--bg-tertiary)', borderRadius: '4px', marginBottom: '6px' }} />
                                                                <div style={{ height: '10px', width: `${40 + (i % 4) * 8}%`, background: 'var(--bg-tertiary)', borderRadius: '3px', opacity: 0.6 }} />
                                                            </div>
                                                        ))}
                                                    </div>
                                                ) : othersListForPicker.length === 0 ? (
                                                    <div style={{ padding: '16px 12px', fontSize: '12px', color: 'var(--text-tertiary)', textAlign: 'center' }}>{t('agent.chat.noSessionsYet')}</div>
                                                ) : (
                                                    othersListForPicker.map((s: any) => {
                                                        const isActive = activeSession?.id === s.id && chatScope === 'all';
                                                        const channelLabel: Record<string, string> = {
                                                            feishu: t('common.channels.feishu'),
                                                            discord: t('common.channels.discord'),
                                                            slack: t('common.channels.slack'),
                                                            wechat: t('common.channels.wechat'),
                                                            dingtalk: t('common.channels.dingtalk'),
                                                            wecom: t('common.channels.wecom'),
                                                        };
                                                        const chLabel = channelLabel[s.source_channel];
                                                        return (
                                                            <div key={s.id} onClick={() => selectSession(s, 'all')}
                                                                className="session-item"
                                                                style={{ padding: '6px 12px', cursor: 'pointer', borderLeft: isActive ? '2px solid var(--accent-primary)' : '2px solid transparent', background: isActive ? 'var(--bg-secondary)' : 'transparent', position: 'relative' }}
                                                                onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--bg-secondary)'; }}
                                                                onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}>
                                                                <div style={{ display: 'flex', alignItems: 'center', gap: '5px', marginBottom: '1px' }}>
                                                                    <div style={{ fontSize: '11px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-primary)', flex: 1 }}>{s.title}</div>
                                                                    {s.is_primary && (
                                                                        <span style={{
                                                                            fontSize: '9px',
                                                                            padding: '1px 4px',
                                                                            borderRadius: '3px',
                                                                            background: 'var(--bg-tertiary)',
                                                                            color: 'var(--text-secondary)',
                                                                            flexShrink: 0,
                                                                            border: '1px solid var(--border-subtle)',
                                                                        }}>
                                                                            {i18n.language === 'zh' ? '主会话' : 'Primary'}
                                                                        </span>
                                                                    )}
                                                                    {s.unread_count > 0 && (
                                                                        <span style={{
                                                                            minWidth: s.unread_count > 9 ? '18px' : '14px',
                                                                            height: s.unread_count > 9 ? '18px' : '14px',
                                                                            padding: s.unread_count > 9 ? '0 4px' : '0',
                                                                            borderRadius: '999px',
                                                                            background: 'var(--text-primary)',
                                                                            color: 'var(--bg-primary)',
                                                                            fontSize: '10px',
                                                                            fontWeight: 600,
                                                                            display: 'flex',
                                                                            alignItems: 'center',
                                                                            justifyContent: 'center',
                                                                            flexShrink: 0,
                                                                        }}>
                                                                            {s.unread_count > 99 ? '99+' : s.unread_count}
                                                                        </span>
                                                                    )}
                                                                    {chLabel && <span style={{ fontSize: '9px', padding: '1px 4px', borderRadius: '3px', background: 'var(--bg-tertiary)', color: 'var(--text-tertiary)', flexShrink: 0 }}>{chLabel}</span>}
                                                                </div>
                                                                <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', display: 'flex', gap: '4px' }}>
                                                                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{s.username || ''}</span>
                                                                    <span style={{ flexShrink: 0 }}>{s.last_message_at ? new Date(s.last_message_at).toLocaleString(i18n.language === 'zh' ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}{s.message_count > 0 ? ` · ${s.message_count}` : ''}</span>
                                                                </div>
                                                            </div>
                                                        );
                                                    })
                                                )}
                                            </div>
                                        </>
                                    )}
                                </div>
                            </div>

                            {/* ── Right: chat/message area ── */}
                            <div className={`agent-chat-area ${livePanelVisible ? 'has-live-panel' : ''}`} style={{ flex: 1, display: 'flex', flexDirection: 'row', position: 'relative', minWidth: 0, overflow: 'hidden' }}>
                                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', position: 'relative', minWidth: 0, overflow: 'hidden' }}>
                                    {sessionListCollapsed && (
                                        <button onClick={() => setSessionListCollapsed(false)} style={{ position: 'absolute', top: '12px', left: '12px', zIndex: 10, width: '28px', height: '28px', borderRadius: '6px', background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)', cursor: 'pointer' }} title="Show chat sessions" onMouseEnter={e => e.currentTarget.style.background='var(--bg-secondary)'} onMouseLeave={e => e.currentTarget.style.background='var(--bg-elevated)'}>
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="3" x2="9" y2="21"></line></svg>
                                        </button>
                                    )}
                                {!activeSession ? (
                                    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: '13px', flexDirection: 'column', gap: '8px' }}>
                                        <div>{t('agent.chat.noSessionSelected')}</div>
                                        {!isViewingOtherUsersSessions && (
                                            <button className="btn btn-secondary" onClick={createNewSession} style={{ fontSize: '12px' }}>{t('agent.chat.startNewSession')}</button>
                                        )}
                                    </div>
                                ) : !isWritableSession(activeSession) ? (
                                    /* ── Read-only history view (other user's session or agent-to-agent) ── */
                                    <>
                                        <div
                                            style={{
                                                position: 'absolute',
                                                top: '12px',
                                                left: sessionListCollapsed ? '52px' : '16px',
                                                zIndex: 10,
                                                fontSize: '11px',
                                                color: 'var(--text-tertiary)',
                                                padding: '4px 8px',
                                                background: 'var(--bg-secondary)',
                                                borderRadius: '4px',
                                                pointerEvents: 'none',
                                            }}
                                        >
                                            {activeSession.source_channel === 'agent' ? `🤖 Agent Conversation · ${activeSession.username || 'Agents'}` : `Read-only · ${activeSession.username || 'User'}`}
                                        </div>
                                        <div ref={historyContainerRef} onScroll={handleHistoryScroll} style={{ flex: 1, overflowY: 'auto', padding: '48px 16px 12px' }}>
                                            {(() => {
                                                // For A2A sessions, determine which participant is "this agent" (left side)
                                                // Use agent.name matching against sender_name from messages
                                                const isA2A = activeSession.source_channel === 'agent' || activeSession.participant_type === 'agent';
                                                const isHumanReadonly = !isA2A && !activeSession.is_group;
                                                const thisAgentName = (agent as any)?.name;
                                                // Find this agent's participant_id from loaded messages
                                                const thisAgentPid = isA2A && thisAgentName
                                                    ? historyMsgs.find((m: any) => m.sender_name === thisAgentName)?.participant_id
                                                    : null;
                                                return historyMsgs.map((m: any, i: number) => {
                                                    // Determine if this message is from "this agent" (left) or peer (right)
                                                    // Actually, "this agent" should be on the RIGHT (like 'me'), and peer on the LEFT
                                                    const isLeft = isA2A && thisAgentPid
                                                        ? m.participant_id !== thisAgentPid
                                                        : m.role === 'assistant';
                                                    if (m.role === 'tool_call') {
                                                        const tName = m.toolName || (() => { try { return JSON.parse(m.content || '{}').name; } catch { return 'tool'; } })();
                                                        const tArgs = m.toolArgs || (() => { try { return JSON.parse(m.content || '{}').args; } catch { return {}; } })();
                                                        const tResult = m.toolResult ?? (() => { try { return JSON.parse(m.content || '{}').result; } catch { return ''; } })();
                                                        return (
                                                            <div key={i} style={{ display: 'flex', gap: '8px', marginBottom: '6px', paddingLeft: '36px', minWidth: 0 }}>
                                                                <details style={{ flex: 1, minWidth: 0, borderRadius: '8px', background: 'var(--accent-subtle)', border: '1px solid var(--accent-subtle)', fontSize: '12px', overflow: 'hidden' }}>
                                                                    <summary style={{ padding: '6px 10px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', userSelect: 'none', listStyle: 'none', overflow: 'hidden' }}>
                                                                        <span style={{ fontSize: '13px' }}>⚡</span>
                                                                        <span style={{ fontWeight: 600, color: 'var(--accent-text)' }}>{tName}</span>
                                                                        {tArgs && typeof tArgs === 'object' && Object.keys(tArgs).length > 0 && <span style={{ color: 'var(--text-tertiary)', fontSize: '11px', fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{`(${Object.entries(tArgs).map(([k, v]) => `${k}: ${typeof v === 'string' ? v.slice(0, 30) : JSON.stringify(v)}`).join(', ')})`}</span>}
                                                                    </summary>
                                                                    {tResult && <div style={{ padding: '4px 10px 8px' }}><div style={{ color: 'var(--text-secondary)', fontSize: '11px', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '240px', overflow: 'auto', background: 'rgba(0,0,0,0.15)', borderRadius: '4px', padding: '4px 6px' }}>{tResult}</div></div>}
                                                                </details>
                                                            </div>
                                                        );
                                                    }

                                                    {/* Assistant message with no content: show inline thinking or skip */ }
                                                    if (m.role === 'assistant' && !m.content?.trim()) {
                                                        if (m.thinking) {
                                                            return (
                                                                <div key={i} style={{ paddingLeft: '36px', marginBottom: '6px' }}>
                                                                    <details style={{
                                                                        fontSize: '12px',
                                                                        background: 'rgba(147, 130, 220, 0.08)', borderRadius: '6px',
                                                                        border: '1px solid rgba(147, 130, 220, 0.15)',
                                                                    }}>
                                                                        <summary style={{
                                                                            padding: '6px 10px', cursor: 'pointer',
                                                                            color: 'rgba(147, 130, 220, 0.9)', fontWeight: 500,
                                                                            userSelect: 'none', display: 'flex', alignItems: 'center', gap: '4px',
                                                                        }}>Thinking</summary>
                                                                        <div style={{
                                                                            padding: '4px 10px 8px',
                                                                            fontSize: '12px', lineHeight: '1.6',
                                                                            color: 'var(--text-secondary)',
                                                                            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                                                            maxHeight: '300px', overflow: 'auto',
                                                                        }}>{m.thinking}</div>
                                                                    </details>
                                                                </div>
                                                            );
                                                        }
                                                        return null;
                                                    }
                                                    return (
                                                        <ChatMessageItem
                                                            key={i}
                                                            msg={m}
                                                            i={i}
                                                            isLeft={isLeft}
                                                            t={t}
                                                            senderLabel={isHumanReadonly ? (isLeft ? ((agent as any)?.name || 'Agent') : (activeSession.username || 'User')) : undefined}
                                                            avatarText={isHumanReadonly ? (isLeft ? (((agent as any)?.name || 'Agent')[0]) : ((activeSession.username || 'User')[0])) : undefined}
                                                            forceSenderLabel={isHumanReadonly}
                                                        />
                                                    );
                                                });
                                            })()}
                                        </div>
                                        {showHistoryScrollBtn && (
                                            <button onClick={scrollHistoryToBottom} style={{ position: 'absolute', bottom: '20px', right: '20px', width: '32px', height: '32px', borderRadius: '50%', background: 'var(--bg-elevated)', border: '1px solid var(--border-default)', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', boxShadow: 'var(--shadow-sm)', zIndex: 10 }} title="Scroll to bottom">↓</button>
                                        )}
                                    </>
                                ) : (
                                    /* ── Live WebSocket chat (own session) ── */
                                    <div {...chatDropProps} style={{ flex: 1, display: 'flex', flexDirection: 'column', position: 'relative', minHeight: 0, overflow: 'hidden' }}>
                                        {/* Drop overlay */}
                                        {isChatDragging && (
                                            <div className="drop-zone-overlay">
                                                <div className="drop-zone-overlay__icon">📎</div>
                                                <div className="drop-zone-overlay__text">{t('agent.upload.dropToAttach', 'Drop files to attach (max 10)')}</div>
                                            </div>
                                        )}
                                        <div ref={chatContainerRef} onScroll={handleChatScroll} style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
                                            {chatMessages.length === 0 && (
                                                <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text-tertiary)' }}>
                                                    <div style={{ fontSize: '13px', marginBottom: '4px' }}>{activeSession?.title || t('agent.chat.startChat')}</div>
                                                    <div style={{ fontSize: '12px' }}>{t('agent.chat.startConversation', { name: agent.name })}</div>
                                                    <div style={{ fontSize: '11px', marginTop: '4px', opacity: 0.7 }}>{t('agent.chat.fileSupport')}</div>
                                                </div>
                                            )}
                                            {(() => {
                                                // ── Grouping Algorithm (lookahead-based) ──
                                                //
                                                // Goal: merge all "analysis" steps (thinking + tool calls +
                                                // mid-flow assistant text) into a single AnalysisCard, and
                                                // only emit a real assistant bubble for the *final* answer.
                                                //
                                                // Problem with naive flushing:
                                                //   Claude and minimax sometimes emit an assistant message with
                                                //   real content (e.g. "Let me search…") BETWEEN reasoning and
                                                //   tool calls. The old approach flushed the group on any
                                                //   assistant content, producing multiple fragmented cards.
                                                //
                                                // Solution — two-pass lookahead:
                                                //   Pass 1: pre-classify every message as either
                                                //     "analysis"  — part of the internal reasoning/tool loop
                                                //     "final"     — the actual answer to show the user
                                                //   Classification rule: an assistant message (even with content)
                                                //   is "analysis" if there is *at least one more tool_call
                                                //   somewhere after it in the same sequence*.
                                                //   Pass 2: build GroupedEntry[] based on classifications.

                                                // Pass 1: mark each index as 'analysis' or 'final'
                                                const msgClass: ('analysis' | 'final')[] = new Array(chatMessages.length).fill('final');

                                                // Walk backwards: once we see a tool_call, all preceding
                                                // assistant messages (until the previous user turn or start)
                                                // are reclassified as 'analysis'.
                                                let hasFutureTool = false;
                                                for (let i = chatMessages.length - 1; i >= 0; i--) {
                                                    const msg = chatMessages[i];
                                                    if (msg.role === 'tool_call') {
                                                        msgClass[i] = 'analysis';
                                                        hasFutureTool = true;
                                                    } else if (msg.role === 'user') {
                                                        // User turn resets the lookahead boundary
                                                        hasFutureTool = false;
                                                    } else if (msg.role === 'assistant') {
                                                        if (hasFutureTool) {
                                                            // This assistant message (thinking-only or with content)
                                                            // precedes more tool calls → it's part of the analysis
                                                            msgClass[i] = 'analysis';
                                                        }
                                                        // else: it's a final answer, keep 'final'
                                                    }
                                                }

                                                // Pass 2: build grouped entries
                                                type GroupedEntry =
                                                    | { type: 'analysis_group'; items: AnalysisItem[]; key: number }
                                                    | { type: 'msg'; msg: any; i: number };
                                                const grouped: GroupedEntry[] = [];
                                                let currentGroup: AnalysisItem[] | null = null;
                                                let groupStartKey = 0;
                                                const flushGroup = () => {
                                                    if (currentGroup && currentGroup.length > 0) {
                                                        grouped.push({ type: 'analysis_group', items: currentGroup, key: groupStartKey });
                                                        currentGroup = null;
                                                    }
                                                };
                                                for (let i = 0; i < chatMessages.length; i++) {

                                                    const msg = chatMessages[i];
                                                    if (msgClass[i] === 'analysis') {
                                                        // Open a new group if needed
                                                        if (!currentGroup) { currentGroup = []; groupStartKey = i; }
                                                        if (msg.role === 'tool_call') {
                                                            currentGroup.push({
                                                                type: 'tool',
                                                                name: msg.toolName || 'tool',
                                                                args: msg.toolArgs || {},
                                                                status: msg.toolStatus === 'running' ? 'running' : 'done',
                                                                result: msg.toolResult || undefined,
                                                            });
                                                        } else if (msg.role === 'assistant') {
                                                            // Could be thinking-only OR has content (mid-flow text)
                                                            const thinkingText = msg.thinking || '';
                                                            const contentText = msg.content?.trim() || '';
                                                            // Add thinking block first (if present)
                                                            if (thinkingText) {
                                                                currentGroup.push({ type: 'thinking', content: thinkingText });
                                                            }
                                                            // Add mid-flow content as a thinking block too
                                                            // (displayed with slightly different style to distinguish)
                                                            if (contentText) {
                                                                currentGroup.push({ type: 'thinking', content: contentText });
                                                            }
                                                        }
                                                    } else {
                                                        // 'final': flush any open group first, then emit as chat bubble
                                                        flushGroup();
                                                        grouped.push({ type: 'msg', msg, i });
                                                    }
                                                }
                                                flushGroup(); // flush any trailing group


                                                return grouped.map((entry, entryIdx) => {
                                                    if (entry.type === 'analysis_group') {
                                                        // Group is considered running if it has a running tool,
                                                        // or if it's the very last entry and the agent is still active
                                                        const isLastEntry = entryIdx === grouped.length - 1;
                                                        const hasRunningTool = entry.items.some(
                                                            it => it.type === 'tool' && it.status === 'running'
                                                        );
                                                        const groupIsRunning = hasRunningTool || (isLastEntry && (isWaiting || isStreaming));
                                                        return (
                                                            <AnalysisCard
                                                                key={`ag-${entry.key}`}
                                                                items={entry.items}
                                                                t={t}
                                                                expanded={!!toolGroupExpandedRef.current.get(entry.key)}
                                                                onToggle={() => toggleToolGroup(entry.key)}
                                                                isGroupRunning={groupIsRunning}
                                                            />
                                                        );
                                                    }
                                                    const { msg, i } = entry;
                                                    // All remaining messages have real content; render as chat bubbles
                                                    return (
                                                        <ChatMessageItem
                                                            key={i}
                                                            msg={msg}
                                                            i={i}
                                                            isLeft={msg.role === 'assistant'}
                                                            t={t}
                                                            senderLabel={msg.role === 'assistant' ? ((agent as any)?.name || 'Agent') : (currentUser?.display_name || undefined)}
                                                            avatarText={msg.role === 'assistant' ? (((agent as any)?.name || 'Agent')[0]) : (currentUser?.display_name?.[0] || undefined)}
                                                        />
                                                    );
                                                });
                                            })()
                                            }
                                            {isWaiting && (
                                                <div style={{ display: 'flex', gap: '8px', marginBottom: '8px', animation: 'fadeIn .2s ease' }}>
                                                    <div style={{ width: '28px', height: '28px', borderRadius: '50%', background: 'var(--bg-elevated)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', flexShrink: 0, color: 'var(--text-secondary)', fontWeight: 600 }}>A</div>
                                                    <div style={{ padding: '8px 12px', borderRadius: '12px', background: 'var(--bg-secondary)', fontSize: '13px' }}>
                                                        <div className="thinking-indicator">
                                                            <div className="thinking-dots">
                                                                <span /><span /><span />
                                                            </div>
                                                            <span style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('agent.chat.thinking', 'Thinking...')}</span>
                                                        </div>
                                                    </div>
                                                </div>
                                            )}
                                            <div ref={chatEndRef} />
                                        </div>
                                        {showScrollBtn && (
                                            <button onClick={scrollToBottom} style={{ position: 'absolute', bottom: `${chatScrollBtnBottom}px`, right: '20px', width: '32px', height: '32px', borderRadius: '50%', background: 'var(--bg-elevated)', border: '1px solid var(--border-default)', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', boxShadow: 'var(--shadow-sm)', zIndex: 10 }} title="Scroll to bottom">↓</button>
                                        )}
                                        {/* Transient info banner — e.g. fallback model switch */}
                                        {chatInfoMsg && (
                                            <div style={{ padding: '6px 14px', borderTop: '1px solid rgba(99,102,241,0.25)', background: 'rgba(99,102,241,0.07)', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: 'var(--text-secondary)', animation: 'fadeIn 0.2s ease' }}>
                                                <span style={{ opacity: 0.7 }}>ℹ️</span>
                                                <span style={{ flex: 1 }}>{chatInfoMsg}</span>
                                                <button onClick={() => setChatInfoMsg(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: '14px', lineHeight: 1, padding: '0 2px' }}>✕</button>
                                            </div>
                                        )}
                                        {agentExpired ? (
                                            <div style={{ padding: '7px 16px', borderTop: '1px solid rgba(245,158,11,0.3)', background: 'rgba(245,158,11,0.08)', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: 'rgb(180,100,0)' }}>
                                                <span>⏸</span>
                                                <span>This Agent has <strong>expired</strong> and is off duty. Contact your admin to extend its service.</span>
                                            </div>
                                        ) : !wsConnected && !!currentUser && sessionUserIdStr(activeSession) === viewerUserIdStr() ? (
                                            <div style={{ padding: '3px 16px', display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                <span style={{ display: 'inline-block', width: '5px', height: '5px', borderRadius: '50%', background: 'var(--accent-primary)', opacity: 0.8, animation: 'pulse 1.2s ease-in-out infinite' }} />
                                                Connecting...
                                            </div>
                                        ) : null}
                                        <div ref={chatInputAreaRef} className="chat-input-area" style={{ flexShrink: 0 }}>
                                            <div className="chat-composer">
                                            {(chatUploadDrafts.length > 0 || attachedFiles.length > 0) && (
                                                <div className="chat-composer-attachments">
                                                    {chatUploadDrafts.map((draft) => (
                                                        <div key={draft.id} className="chat-file-pill">
                                                            <div
                                                                className="chat-file-pill__fill"
                                                                style={{ width: `${draft.percent}%` }}
                                                            />
                                                            <div className="chat-file-pill__row">
                                                                {draft.previewUrl ? (
                                                                    <img className="chat-file-pill__thumb" src={draft.previewUrl} alt="" />
                                                                ) : (
                                                                    <span className="chat-file-pill__icon">
                                                                        <IconPaperclip size={14} stroke={1.75} />
                                                                    </span>
                                                                )}
                                                                <span className="chat-file-pill__name">{draft.name}</span>
                                                                <span className="chat-file-pill__size">{formatFileSize(draft.sizeBytes)}</span>
                                                                <span className="chat-file-pill__pct">{draft.percent}%</span>
                                                                <button
                                                                    type="button"
                                                                    className="chat-file-pill__remove"
                                                                    onClick={() => {
                                                                        chatUploadAbortRef.current.get(draft.id)?.();
                                                                    }}
                                                                    title="Cancel upload"
                                                                >
                                                                    ×
                                                                </button>
                                                            </div>
                                                        </div>
                                                    ))}
                                                    {attachedFiles.map((file, idx) => (
                                                        <div
                                                            key={`a-${idx}-${file.name}`}
                                                            className={`chat-file-pill ${file.source === 'workspace_auto' ? 'chat-file-pill--workspace' : ''}`}
                                                            title={file.path || file.name}
                                                        >
                                                            <div className="chat-file-pill__row">
                                                                {file.imageUrl ? (
                                                                    <img className="chat-file-pill__thumb" src={file.imageUrl} alt="" />
                                                                ) : (
                                                                    <span className="chat-file-pill__icon">
                                                                        <IconPaperclip size={14} stroke={1.75} />
                                                                    </span>
                                                                )}
                                                                <span className="chat-file-pill__name">{file.name}</span>
                                                                {file.source === 'workspace_auto' && <span className="chat-file-pill__source">Workspace</span>}
                                                                <button
                                                                    type="button"
                                                                    className="chat-file-pill__remove"
                                                                    onClick={() => {
                                                                        if (file.source === 'workspace_auto' && file.path) dismissedWorkspaceRefPath.current = file.path;
                                                                        setAttachedFiles((prev) => prev.filter((_, i) => i !== idx));
                                                                    }}
                                                                    title="Remove file"
                                                                >
                                                                    ×
                                                                </button>
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                            <div className="chat-composer-input-block">
                                                <textarea
                                                    ref={chatInputRef}
                                                    className="chat-input"
                                                    value={chatInput}
                                                    onChange={e => {
                                                        setChatInput(e.target.value);
                                                        // Auto-grow: reset height then expand to scrollHeight
                                                        const el = e.target;
                                                        el.style.height = 'auto';
                                                        el.style.height = el.scrollHeight + 'px';
                                                    }}
                                                    onKeyDown={e => {
                                                        // Enter sends the message; Shift+Enter inserts a newline
                                                        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !isWaiting && !isStreaming) {
                                                            e.preventDefault();
                                                            sendChatMsg();
                                                        }
                                                    }}
                                                    onPaste={handlePaste}
                                                    placeholder={!wsConnected && !!currentUser && sessionUserIdStr(activeSession) === viewerUserIdStr() ? 'Connecting...' : t('chat.placeholder')}
                                                    disabled={!wsConnected}
                                                    rows={1}
                                                />
                                            </div>
                                            <div className="chat-composer-toolbar">
                                                <input type="file" multiple ref={fileInputRef} onChange={handleChatFile} style={{ display: 'none' }} />
                                                <button
                                                    type="button"
                                                    className="chat-composer-btn"
                                                    onClick={() => fileInputRef.current?.click()}
                                                    disabled={!wsConnected || chatUploadDrafts.length > 0 || isWaiting || isStreaming || attachedFiles.length >= 10}
                                                    title={t('agent.workspace.uploadFile')}
                                                >
                                                    <IconPaperclip size={16} stroke={1.75} />
                                                </button>
                                                {(isStreaming || isWaiting) ? (
                                                    <button
                                                        type="button"
                                                        className="btn btn-stop-generation"
                                                        onClick={() => {
                                                            if (!id || !activeSession?.id) return;
                                                            const activeRuntimeKey = buildSessionRuntimeKey(id, String(activeSession.id));
                                                            const activeSocket = wsMapRef.current[activeRuntimeKey];
                                                            if (activeSocket?.readyState === WebSocket.OPEN) {
                                                                activeSocket.send(JSON.stringify({ type: 'abort' }));
                                                                setIsStreaming(false);
                                                                setIsWaiting(false);
                                                                setSessionUiState(activeRuntimeKey, { isWaiting: false, isStreaming: false });
                                                            }
                                                        }}
                                                        title={t('chat.stop', 'Stop')}
                                                    >
                                                        <span className="stop-icon" />
                                                    </button>
                                                ) : (
                                                    <button
                                                        type="button"
                                                        className="btn btn-primary chat-composer-send"
                                                        onClick={sendChatMsg}
                                                        disabled={!wsConnected || (!chatInput.trim() && attachedFiles.length === 0)}
                                                        title={t('chat.send')}
                                                    >
                                                        <IconSend size={16} stroke={1.75} />
                                                    </button>
                                                )}
                                            </div>
                                        </div>
                                        </div>
                                    </div>
                                )}
                                </div>
                                <AgentSidePanel
                                    liveState={liveState}
                                    workspaceActivePath={workspaceActivePath}
                                    workspaceActivities={workspaceActivities}
                                    workspaceLiveDraft={workspaceLiveDraft}
                                    visible={livePanelVisible}
                                    onToggle={() => {
                                        if (!livePanelVisible) {
                                            setSidePanelTab('workspace');
                                            setLivePanelVisible(true);
                                            setSessionListCollapsed(true);
                                            useAppStore.setState({ sidebarCollapsed: true });
                                        } else {
                                            setLivePanelVisible(false);
                                        }
                                    }}
                                    activeTab={sidePanelTab}
                                    onTabChange={setSidePanelTab}
                                    workspaceLocked={workspacePreviewLocked}
                                    onWorkspaceSelectPath={handleWorkspaceSelectPath}
                                    onWorkspaceToggleLock={handleWorkspaceToggleLock}
                                    onWorkspaceEditingChange={handleWorkspaceEditingChange}
                                    onWorkspacePathDeleted={handleWorkspacePathDeleted}
                                    agentId={id}
                                    sessionId={wsSessionId}
                                    onLiveUpdate={(env, screenshotDataUri) => {
                                        // Refresh the live preview with the final screenshot
                                        // captured by TakeControlPanel on close, so the panel
                                        // reflects the state the user left the browser in.
                                        setLiveState(prev => ({
                                            ...prev,
                                            [env]: { screenshotUrl: screenshotDataUri },
                                        }));
                                    }}
                                />
                            </div>
                        </div>
                    )
                }

                {
                    activeTab === 'activityLog' && (() => {
                        // Category definitions
                        const userActionTypes = ['chat_reply', 'tool_call', 'task_created', 'task_updated', 'file_written', 'error'];
                        const heartbeatTypes = ['heartbeat', 'plaza_post'];
                        const scheduleTypes = ['schedule_run'];
                        const messageTypes = ['feishu_msg_sent', 'agent_msg_sent', 'web_msg_sent'];

                        let filteredLogs = activityLogs;
                        if (logFilter === 'user') {
                            filteredLogs = activityLogs.filter((l: any) => userActionTypes.includes(l.action_type));
                        } else if (logFilter === 'backend') {
                            filteredLogs = activityLogs.filter((l: any) => !userActionTypes.includes(l.action_type));
                        } else if (logFilter === 'heartbeat') {
                            filteredLogs = activityLogs.filter((l: any) => heartbeatTypes.includes(l.action_type));
                        } else if (logFilter === 'schedule') {
                            filteredLogs = activityLogs.filter((l: any) => scheduleTypes.includes(l.action_type));
                        } else if (logFilter === 'messages') {
                            filteredLogs = activityLogs.filter((l: any) => messageTypes.includes(l.action_type));
                        }

                        const filterBtn = (key: string, label: string, indent = false) => (
                            <button
                                key={key}
                                onClick={() => setLogFilter(key)}
                                style={{
                                    padding: indent ? '4px 10px 4px 20px' : '6px 14px',
                                    fontSize: indent ? '11px' : '12px',
                                    fontWeight: logFilter === key ? 600 : 400,
                                    color: logFilter === key ? 'var(--accent-primary)' : 'var(--text-secondary)',
                                    background: logFilter === key ? 'rgba(99,102,241,0.1)' : 'transparent',
                                    border: logFilter === key ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
                                    borderRadius: '6px',
                                    cursor: 'pointer',
                                    transition: 'all 0.15s',
                                    whiteSpace: 'nowrap' as const,
                                }}
                            >
                                {label}
                            </button>
                        );

                        return (
                            <div>
                                <h3 style={{ marginBottom: '12px' }}>{t('agent.activityLog.title')}</h3>

                                {/* Filter tabs */}
                                <div style={{ display: 'flex', gap: '6px', marginBottom: '16px', flexWrap: 'wrap', alignItems: 'center' }}>
                                    {filterBtn('user', '👤 ' + t('agent.activityLog.userActions', 'User Actions'))}
                                    {(agent as any)?.agent_type !== 'openclaw' && (<>
                                        {filterBtn('backend', '⚙️ ' + t('agent.activityLog.backendServices', 'Backend Services'))}
                                        {(logFilter === 'backend' || logFilter === 'heartbeat' || logFilter === 'schedule' || logFilter === 'messages') && (
                                            <>
                                                <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>│</span>
                                                {filterBtn('heartbeat', '💓 ' + t('agent.mind.heartbeatTitle'))}
                                                {filterBtn('schedule', '⏰ ' + t('agent.activityLog.scheduleCron'), true)}
                                                {filterBtn('messages', '📨 ' + t('agent.activityLog.messages'), true)}
                                            </>
                                        )}
                                    </>)}
                                </div>

                                {filteredLogs.length > 0 ? (
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                        {filteredLogs.map((log: any) => {
                                            const icons: Record<string, string> = {
                                                chat_reply: '💬', tool_call: '⚡', feishu_msg_sent: '📤',
                                                agent_msg_sent: '🤖', web_msg_sent: '🌐', task_created: '📋',
                                                task_updated: '✅', file_written: '📝', error: '❌',
                                                schedule_run: '⏰', heartbeat: '💓', plaza_post: '🏛️',
                                            };
                                            const time = log.created_at ? new Date(log.created_at).toLocaleString('zh-CN', {
                                                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
                                            }) : '';
                                            const isExpanded = expandedLogId === log.id;
                                            return (
                                                <div key={log.id}
                                                    onClick={() => setExpandedLogId(isExpanded ? null : log.id)}
                                                    style={{
                                                        padding: '10px 14px', borderRadius: '8px', cursor: 'pointer',
                                                        background: isExpanded ? 'var(--bg-elevated)' : 'var(--bg-secondary)', fontSize: '13px',
                                                        border: isExpanded ? '1px solid var(--accent-primary)' : '1px solid transparent',
                                                        transition: 'all 0.15s ease',
                                                    }}
                                                >
                                                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
                                                        <span style={{ fontSize: '16px', flexShrink: 0, marginTop: '1px' }}>
                                                            {icons[log.action_type] || '·'}
                                                        </span>
                                                        <div style={{ flex: 1, minWidth: 0 }}>
                                                            <div style={{ fontWeight: 500, marginBottom: '2px' }}>{log.summary}</div>
                                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                                {time} · {log.action_type}
                                                                {log.detail && !isExpanded && <span style={{ marginLeft: '8px', color: 'var(--accent-primary)' }}>▸ Details</span>}
                                                            </div>
                                                        </div>
                                                    </div>
                                                    {isExpanded && log.detail && (
                                                        <div style={{ marginTop: '8px', padding: '10px', borderRadius: '6px', background: 'var(--bg-primary)', fontSize: '12px', fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all', lineHeight: '1.6', color: 'var(--text-secondary)', maxHeight: '300px', overflowY: 'auto' }}>
                                                            {Object.entries(log.detail).map(([k, v]: [string, any]) => (
                                                                <div key={k} style={{ marginBottom: '6px' }}>
                                                                    <span style={{ color: 'var(--accent-primary)', fontWeight: 600 }}>{k}:</span>{' '}
                                                                    <span>{typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v)}</span>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })}
                                    </div>
                                ) : (
                                    <div className="card" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)' }}>
                                        {t('agent.activityLog.noRecords')}
                                    </div>
                                )}
                            </div>
                        );
                    })()
                }

                {/* ── Feishu Channel Tab ── */}

                {/* ── Approvals Tab ── */}
                {
                    activeTab === 'approvals' && (() => {
                        const ApprovalsTab = () => {
                            const isChinese = i18n.language?.startsWith('zh');
                            const {
                                data: approvals = [],
                                error: approvalsError,
                                refetch: refetchApprovals,
                            } = useQuery({
                                queryKey: ['agent-approvals', id],
                                queryFn: async () => {
                                    const data = await fetchAuth<any[]>(`/agents/${id}/approvals`);
                                    return Array.isArray(data) ? data : [];
                                },
                                enabled: !!id,
                                refetchInterval: 15000,
                                retry: false,
                            });
                            const resolveMut = useMutation({
                                mutationFn: async ({ approvalId, action }: { approvalId: string; action: string }) => {
                                    const token = localStorage.getItem('token');
                                    return fetch(`/api/agents/${id}/approvals/${approvalId}/resolve`, {
                                        method: 'POST',
                                        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
                                        body: JSON.stringify({ action }),
                                    });
                                },
                                onSuccess: () => {
                                    refetchApprovals();
                                    queryClient.invalidateQueries({ queryKey: ['notifications-unread'] });
                                },
                            });
                            const pending = (approvals as any[]).filter((a: any) => a.status === 'pending');
                            const resolved = (approvals as any[]).filter((a: any) => a.status !== 'pending');
                            const approvalsErrorMessage = approvalsError instanceof Error
                                ? approvalsError.message
                                : (isChinese ? '审批记录加载失败' : 'Failed to load approval records');
                            const statusStyle = (s: string) => ({
                                padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600,
                                background: s === 'approved' ? 'rgba(0,180,120,0.12)' : s === 'rejected' ? 'rgba(255,80,80,0.12)' : 'rgba(255,180,0,0.12)',
                                color: s === 'approved' ? 'var(--success)' : s === 'rejected' ? 'var(--error)' : 'var(--warning)',
                            });
                            return (
                                <div style={{ padding: '20px 24px' }}>
                                    {approvalsError && (
                                        <div style={{
                                            marginBottom: '16px',
                                            padding: '14px 16px',
                                            borderRadius: '8px',
                                            background: 'rgba(255, 180, 0, 0.08)',
                                            border: '1px solid rgba(255, 180, 0, 0.2)',
                                            color: 'var(--text-secondary)',
                                            fontSize: '13px',
                                            lineHeight: 1.5,
                                        }}>
                                            <div style={{ fontWeight: 600, marginBottom: '4px', color: 'var(--warning)' }}>
                                                {isChinese ? '无法加载审批记录' : 'Unable to load approval records'}
                                            </div>
                                            <div>{approvalsErrorMessage}</div>
                                        </div>
                                    )}
                                    {/* Pending */}
                                    {pending.length > 0 && (
                                        <>
                                            <h4 style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--warning)' }}>
                                                {isChinese ? `${pending.length} 个待审批` : `${pending.length} Pending`}
                                            </h4>
                                            {pending.map((a: any) => (
                                                <div key={a.id} style={{
                                                    padding: '14px 16px', marginBottom: '8px', borderRadius: '8px',
                                                    background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)',
                                                }}>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                                                        <span style={statusStyle(a.status)}>{a.status}</span>
                                                        <span style={{ fontSize: '13px', fontWeight: 500 }}>{a.action_type}</span>
                                                        <span style={{ flex: 1 }} />
                                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                            {a.created_at ? new Date(a.created_at).toLocaleString() : ''}
                                                        </span>
                                                    </div>
                                                    {a.details && (
                                                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '10px', lineHeight: '1.5', maxHeight: '80px', overflow: 'hidden' }}>
                                                            {typeof a.details === 'string' ? a.details : JSON.stringify(a.details, null, 2)}
                                                        </div>
                                                    )}
                                                    <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                                                        <button
                                                            className="btn btn-primary"
                                                            style={{ padding: '6px 16px', fontSize: '12px' }}
                                                            onClick={() => resolveMut.mutate({ approvalId: a.id, action: 'approve' })}
                                                            disabled={resolveMut.isPending}
                                                        >
                                                            {isChinese ? '批准' : 'Approve'}
                                                        </button>
                                                        <button
                                                            className="btn btn-danger"
                                                            style={{ padding: '6px 16px', fontSize: '12px' }}
                                                            onClick={() => resolveMut.mutate({ approvalId: a.id, action: 'reject' })}
                                                            disabled={resolveMut.isPending}
                                                        >
                                                            {isChinese ? '拒绝' : 'Reject'}
                                                        </button>
                                                    </div>
                                                </div>
                                            ))}
                                            <div style={{ borderTop: '1px solid var(--border-subtle)', margin: '16px 0' }} />
                                        </>
                                    )}
                                    {/* History */}
                                    <h4 style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                                        {isChinese ? '审批历史' : 'History'}
                                    </h4>
                                    {resolved.length === 0 && pending.length === 0 && (
                                        <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
                                            {isChinese ? '暂无审批记录' : 'No approval records'}
                                        </div>
                                    )}
                                    {resolved.map((a: any) => (
                                        <div key={a.id} style={{
                                            padding: '12px 16px', marginBottom: '6px', borderRadius: '8px',
                                            background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)',
                                            opacity: 0.7,
                                        }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                <span style={statusStyle(a.status)}>{a.status}</span>
                                                <span style={{ fontSize: '12px' }}>{a.action_type}</span>
                                                <span style={{ flex: 1 }} />
                                                <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                                                    {a.resolved_at ? new Date(a.resolved_at).toLocaleString() : ''}
                                                </span>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            );
                        };
                        return <ApprovalsTab />;
                    })()}

                {/* ── Settings Tab ── */}
                {
                    activeTab === 'settings' && (agent as any)?.agent_type === 'openclaw' && (
                        <OpenClawSettings agent={agent} agentId={id!} />
                    )
                }
                {
                    activeTab === 'settings' && (agent as any)?.agent_type !== 'openclaw' && (() => {
                        // Check if form has unsaved changes
                        const hasChanges = (
                            settingsForm.primary_model_id !== (agent?.primary_model_id || '') ||
                            settingsForm.fallback_model_id !== (agent?.fallback_model_id || '') ||
                            settingsForm.context_window_size !== (agent?.context_window_size ?? 100) ||
                            settingsForm.max_tool_rounds !== ((agent as any)?.max_tool_rounds ?? 50) ||
                            String(settingsForm.max_tokens_per_day) !== String(agent?.max_tokens_per_day || '') ||
                            String(settingsForm.max_tokens_per_month) !== String(agent?.max_tokens_per_month || '') ||
                            settingsForm.max_triggers !== ((agent as any)?.max_triggers ?? 20) ||
                            settingsForm.min_poll_interval_min !== ((agent as any)?.min_poll_interval_min ?? 5) ||
                            settingsForm.webhook_rate_limit !== ((agent as any)?.webhook_rate_limit ?? 5)
                        );

                        const handleSaveSettings = async () => {
                            setSettingsSaving(true);
                            setSettingsError('');
                            try {
                                const result: any = await agentApi.update(id!, {
                                    primary_model_id: settingsForm.primary_model_id || null,
                                    fallback_model_id: settingsForm.fallback_model_id || null,
                                    context_window_size: settingsForm.context_window_size,
                                    max_tool_rounds: settingsForm.max_tool_rounds,
                                    max_tokens_per_day: settingsForm.max_tokens_per_day ? Number(settingsForm.max_tokens_per_day) : null,
                                    max_tokens_per_month: settingsForm.max_tokens_per_month ? Number(settingsForm.max_tokens_per_month) : null,
                                    max_triggers: settingsForm.max_triggers,
                                    min_poll_interval_min: settingsForm.min_poll_interval_min,
                                    webhook_rate_limit: settingsForm.webhook_rate_limit,
                                } as any);
                                queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                settingsInitRef.current = false;

                                // Check if any values were clamped by company policy
                                const clamped = result?._clamped_fields;
                                if (clamped && clamped.length > 0) {
                                    const isCh = i18n.language?.startsWith('zh');
                                    const fieldNames: Record<string, string> = isCh
                                        ? { min_poll_interval_min: 'Poll 最短间隔', webhook_rate_limit: 'Webhook 频率限制', heartbeat_interval_minutes: '心跳间隔' }
                                        : { min_poll_interval_min: 'Min Poll Interval', webhook_rate_limit: 'Webhook Rate Limit', heartbeat_interval_minutes: 'Heartbeat Interval' };
                                    const msgs = clamped.map((c: any) => {
                                        const name = fieldNames[c.field] || c.field;
                                        return isCh
                                            ? `${name}: ${c.requested} -> ${c.applied} (公司策略限制)`
                                            : `${name}: ${c.requested} -> ${c.applied} (company policy)`;
                                    });
                                    setSettingsError((isCh ? 'Some values were adjusted:\n' : 'Some values were adjusted:\n') + msgs.join('\n'));
                                    setTimeout(() => setSettingsError(''), 5000);
                                }

                                setSettingsSaved(true);
                                setTimeout(() => setSettingsSaved(false), 2000);
                            } catch (e: any) {
                                setSettingsError(e?.message || 'Failed to save');
                            } finally {
                                setSettingsSaving(false);
                            }
                        };

                        return (
                            <div>
                                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', marginBottom: '8px', position: 'sticky', top: '41px', zIndex: 6, background: 'var(--bg-primary)', paddingTop: '4px', paddingBottom: '8px' }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                        {settingsSaved && <span style={{ fontSize: '12px', color: 'var(--success)' }}>{t('agent.settings.saved', 'Saved')}</span>}
                                        {settingsError && <span style={{ fontSize: '12px', color: settingsError.includes('adjusted') ? 'var(--warning)' : 'var(--error)', whiteSpace: 'pre-line' }}>{settingsError}</span>}
                                        <button
                                            className="btn btn-primary"
                                            disabled={!hasChanges || settingsSaving}
                                            onClick={handleSaveSettings}
                                            style={{
                                                opacity: hasChanges ? 1 : 0.5,
                                                cursor: hasChanges ? 'pointer' : 'default',
                                                padding: '6px 20px',
                                                fontSize: '13px',
                                            }}
                                        >
                                            {settingsSaving ? t('agent.settings.saving', 'Saving...') : t('agent.settings.save', 'Save')}
                                        </button>
                                    </div>
                                </div>

                                {/* Model Selection — native agents only */}
                                {(agent as any)?.agent_type !== 'openclaw' && (
                                    <div className="card" style={{ marginBottom: '12px' }}>
                                        <h4 style={{ marginBottom: '12px' }}>{t('agent.settings.modelConfig')}</h4>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                            <div>
                                                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.primaryModel')}</label>
                                                <select
                                                    className="input"
                                                    value={settingsForm.primary_model_id}
                                                    onChange={(e) => setSettingsForm(f => ({ ...f, primary_model_id: e.target.value }))}
                                                >
                                                    <option value="">--</option>
                                                    {llmModels.filter((m: any) => m.enabled || m.id === settingsForm.primary_model_id).map((m: any) => (
                                                        <option key={m.id} value={m.id}>
                                                            {m.label} ({m.provider}/{m.model}){!m.enabled ? ` [${t('enterprise.llm.disabled', 'Disabled')}]` : ''}
                                                        </option>
                                                    ))}
                                                </select>
                                                {/* Warning if selected model is disabled */}
                                                {settingsForm.primary_model_id && llmModels.some((m: any) => m.id === settingsForm.primary_model_id && !m.enabled) && (
                                                    <div style={{ fontSize: '11px', color: 'var(--error)', marginTop: '4px' }}>
                                                        {t('agent.settings.modelDisabledWarning', 'This model has been disabled by admin. The agent will automatically use the fallback model.')}
                                                    </div>
                                                )}
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.primaryModel')}</div>
                                            </div>
                                            <div>
                                                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.fallbackModel')}</label>
                                                <select
                                                    className="input"
                                                    value={settingsForm.fallback_model_id}
                                                    onChange={(e) => setSettingsForm(f => ({ ...f, fallback_model_id: e.target.value }))}
                                                >
                                                    <option value="">--</option>
                                                    {llmModels.filter((m: any) => m.enabled || m.id === settingsForm.fallback_model_id).map((m: any) => (
                                                        <option key={m.id} value={m.id}>
                                                            {m.label} ({m.provider}/{m.model}){!m.enabled ? ` [${t('enterprise.llm.disabled', 'Disabled')}]` : ''}
                                                        </option>
                                                    ))}
                                                </select>
                                                {/* Warning if selected fallback model is disabled */}
                                                {settingsForm.fallback_model_id && llmModels.some((m: any) => m.id === settingsForm.fallback_model_id && !m.enabled) && (
                                                    <div style={{ fontSize: '11px', color: 'var(--error)', marginTop: '4px' }}>
                                                        {t('agent.settings.modelDisabledWarning', 'This model has been disabled by admin. The agent will automatically use the fallback model.')}
                                                    </div>
                                                )}
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.fallbackModel')}</div>
                                            </div>
                                        </div>
                                    </div>
                                )}

                                {/* Context Window — native agents only */}
                                {(agent as any)?.agent_type !== 'openclaw' && (<>
                                    <div className="card" style={{ marginBottom: '12px' }}>
                                        <h4 style={{ marginBottom: '12px' }}>{t('agent.settings.conversationContext')}</h4>
                                        <div>
                                            <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.maxRounds')}</label>
                                            <input
                                                className="input"
                                                type="number"
                                                min={10}
                                                max={500}
                                                value={settingsForm.context_window_size}
                                                onChange={(e) => setSettingsForm(f => ({ ...f, context_window_size: Math.max(10, Math.min(500, parseInt(e.target.value) || 100)) }))}
                                                style={{ width: '120px' }}
                                            />
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.roundsDesc')}</div>
                                        </div>
                                    </div>

                                    {/* Max Tool Call Rounds */}
                                    <div className="card" style={{ marginBottom: '12px' }}>
                                        <h4 style={{ marginBottom: '12px' }}>🔧 {t('agent.settings.maxToolRounds', 'Max Tool Call Rounds')}</h4>
                                        <div>
                                            <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.maxToolRoundsLabel', 'Maximum rounds per message')}</label>
                                            <input
                                                className="input"
                                                type="number"
                                                min={5}
                                                max={200}
                                                value={settingsForm.max_tool_rounds}
                                                onChange={(e) => setSettingsForm(f => ({ ...f, max_tool_rounds: Math.max(5, Math.min(200, parseInt(e.target.value) || 50)) }))}
                                                style={{ width: '120px' }}
                                            />
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{t('agent.settings.maxToolRoundsDesc', 'How many tool-calling rounds the agent can perform per message (search, write, etc). Default: 50')}</div>
                                        </div>
                                    </div>
                                </>)}

                                {/* Token Limits */}
                                <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '12px' }}>{t('agent.settings.tokenLimits')}</h4>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                                        <div>
                                            <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.dailyLimit')}</label>
                                            <input
                                                className="input"
                                                type="number"
                                                value={settingsForm.max_tokens_per_day}
                                                onChange={(e) => setSettingsForm(f => ({ ...f, max_tokens_per_day: e.target.value }))}
                                                placeholder={t("agent.settings.noLimit")}
                                            />
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                                {t('agent.settings.today')}: {formatTokens(agent?.tokens_used_today || 0)}
                                            </div>
                                        </div>
                                        <div>
                                            <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>{t('agent.settings.monthlyLimit')}</label>
                                            <input
                                                className="input"
                                                type="number"
                                                value={settingsForm.max_tokens_per_month}
                                                onChange={(e) => setSettingsForm(f => ({ ...f, max_tokens_per_month: e.target.value }))}
                                                placeholder={t("agent.settings.noLimit")}
                                            />
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                                {t('agent.settings.month')}: {formatTokens(agent?.tokens_used_month || 0)}
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                {/* Trigger Limits — native agents only */}
                                {(agent as any)?.agent_type !== 'openclaw' && (() => {
                                    const isChinese = i18n.language?.startsWith('zh');
                                    return (
                                        <div className="card" style={{ marginBottom: '12px' }}>
                                            <h4 style={{ marginBottom: '4px' }}>{isChinese ? '触发器限制' : 'Trigger Limits'}</h4>
                                            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                                {isChinese
                                                    ? '控制该 Agent 可以创建的触发器数量和行为限制'
                                                    : 'Limit how many triggers this agent can create and their behavior'}
                                            </p>
                                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px' }}>
                                                <div>
                                                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>
                                                        {isChinese ? '最大触发器数' : 'Max Triggers'}
                                                    </label>
                                                    <input
                                                        className="input"
                                                        type="number"
                                                        min={1}
                                                        max={100}
                                                        value={settingsForm.max_triggers}
                                                        onChange={(e) => setSettingsForm(f => ({ ...f, max_triggers: Math.max(1, Math.min(100, parseInt(e.target.value) || 20)) }))}
                                                        style={{ width: '100%' }}
                                                    />
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                                        {isChinese ? 'Agent 最多可同时拥有的触发器数量' : 'Max active triggers the agent can have'}
                                                    </div>
                                                </div>
                                                <div>
                                                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>
                                                        {isChinese ? 'Poll 最短间隔 (分钟)' : 'Min Poll Interval (min)'}
                                                    </label>
                                                    <input
                                                        className="input"
                                                        type="number"
                                                        min={1}
                                                        max={60}
                                                        value={settingsForm.min_poll_interval_min}
                                                        onChange={(e) => setSettingsForm(f => ({ ...f, min_poll_interval_min: Math.max(1, Math.min(60, parseInt(e.target.value) || 5)) }))}
                                                        style={{ width: '100%' }}
                                                    />
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                                        {isChinese ? '定时轮询外部接口的最短间隔' : 'Minimum interval for polling external URLs'}
                                                    </div>
                                                </div>
                                                <div>
                                                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>
                                                        {isChinese ? 'Webhook 频率限制 (次/分钟)' : 'Webhook Rate Limit (/min)'}
                                                    </label>
                                                    <input
                                                        className="input"
                                                        type="number"
                                                        min={1}
                                                        max={60}
                                                        value={settingsForm.webhook_rate_limit}
                                                        onChange={(e) => setSettingsForm(f => ({ ...f, webhook_rate_limit: Math.max(1, Math.min(60, parseInt(e.target.value) || 5)) }))}
                                                        style={{ width: '100%' }}
                                                    />
                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                                                        {isChinese ? '外部系统每分钟最多可调用的 Webhook 次数' : 'Max webhook calls per minute from external services'}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    );
                                })()}

                                {/* Credentials Management — for AgentBay cookie injection */}
                                <div style={{ marginBottom: '12px' }}>
                                    <AgentCredentials agentId={id!} />
                                </div>

                                {/* Welcome Message */}
                                {(() => {
                                    const isChinese = i18n.language?.startsWith('zh');
                                    const saveWm = async () => {
                                        try {
                                            await agentApi.update(id!, { welcome_message: wmDraft } as any);
                                            queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                            setWmSaved(true);
                                            setTimeout(() => setWmSaved(false), 2000);
                                        } catch { }
                                    };
                                    return (
                                        <div className="card" style={{ marginBottom: '12px' }}>
                                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
                                                <h4 style={{ margin: 0 }}>{isChinese ? '欢迎语' : 'Welcome Message'}</h4>
                                                {wmSaved && <span style={{ fontSize: '12px', color: 'var(--success)' }}>✓ {isChinese ? '已保存' : 'Saved'}</span>}
                                            </div>
                                            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                                {isChinese
                                                    ? '当用户在网页端发起新对话时，Agent 会自动发送的欢迎语。支持 Markdown 语法。留空则不发送。'
                                                    : 'Greeting message sent automatically when a user starts a new web conversation. Supports Markdown. Leave empty to disable.'}
                                            </p>
                                            <textarea
                                                className="input"
                                                rows={4}
                                                value={wmDraft}
                                                onChange={e => setWmDraft(e.target.value)}
                                                onBlur={saveWm}
                                                placeholder={isChinese ? '例如：你好！我是你的 AI 助手，有什么可以帮你的吗？' : "e.g. Hello! I'm your AI assistant. How can I help you?"}
                                                style={{
                                                    width: '100%', minHeight: '80px', resize: 'vertical',
                                                    fontFamily: 'inherit', fontSize: '13px',
                                                }}
                                            />
                                        </div>
                                    );
                                })()}

                                {/* Autonomy Policy — native agents only */}
                                {(agent as any)?.agent_type !== 'openclaw' && <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '4px' }}>{t('agent.settings.autonomy.title')}</h4>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                                        {t('agent.settings.autonomy.description')}
                                    </p>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                        {[
                                            { key: 'read_files', label: t('agent.settings.autonomy.readFiles'), desc: t('agent.settings.autonomy.readFilesDesc') },
                                            { key: 'write_workspace_files', label: t('agent.settings.autonomy.writeFiles'), desc: t('agent.settings.autonomy.writeFilesDesc') },
                                            { key: 'delete_files', label: t('agent.settings.autonomy.deleteFiles'), desc: t('agent.settings.autonomy.deleteFilesDesc') },
                                            { key: 'send_feishu_message', label: t('agent.settings.autonomy.sendFeishu'), desc: t('agent.settings.autonomy.sendFeishuDesc') },
                                            { key: 'web_search', label: t('agent.settings.autonomy.webSearch'), desc: t('agent.settings.autonomy.webSearchDesc') },
                                            { key: 'manage_tasks', label: t('agent.settings.autonomy.manageTasks'), desc: t('agent.settings.autonomy.manageTasksDesc') },
                                        ].map((action) => {
                                            const currentLevel = (agent?.autonomy_policy as any)?.[action.key] || 'L1';
                                            return (
                                                <div key={action.key} style={{
                                                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                                    padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px',
                                                    border: '1px solid var(--border-subtle)',
                                                }}>
                                                    <div style={{ flex: 1 }}>
                                                        <div style={{ fontWeight: 500, fontSize: '13px' }}>{action.label}</div>
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{action.desc}</div>
                                                    </div>
                                                    <select
                                                        className="input"
                                                        value={currentLevel}
                                                        onChange={async (e) => {
                                                            const newPolicy = { ...(agent?.autonomy_policy as any || {}), [action.key]: e.target.value };
                                                            await agentApi.update(id!, { autonomy_policy: newPolicy } as any);
                                                            queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                                        }}
                                                        style={{
                                                            width: '140px', fontSize: '12px',
                                                            color: currentLevel === 'L1' ? 'var(--success)' : currentLevel === 'L2' ? 'var(--warning)' : 'var(--error)',
                                                            fontWeight: 600,
                                                        }}
                                                    >
                                                        <option value="L1">{t('agent.settings.autonomy.l1Auto')}</option>
                                                        <option value="L2">{t('agent.settings.autonomy.l2Notify')}</option>
                                                        <option value="L3">{t('agent.settings.autonomy.l3Approve')}</option>
                                                    </select>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>}

                                {/* Permission Management */}
                                {(() => {
                                    const scopeLabels: Record<string, string> = {
                                        company: '🏢 ' + t('agent.settings.perm.companyWide', 'Company-wide'),
                                        user: '👤 ' + t('agent.settings.perm.onlyMe', 'Only Me'),
                                    };

                                    const handleScopeChange = async (newScope: string) => {
                                        try {
                                            await fetchAuth(`/agents/${id}/permissions`, {
                                                method: 'PUT',
                                                headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({ scope_type: newScope, scope_ids: [], access_level: permData?.access_level || 'use' }),
                                            });
                                            queryClient.invalidateQueries({ queryKey: ['agent-permissions', id] });
                                            queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                        } catch (e) {
                                            console.error('Failed to update permissions', e);
                                        }
                                    };

                                    const handleAccessLevelChange = async (newLevel: string) => {
                                        try {
                                            await fetchAuth(`/agents/${id}/permissions`, {
                                                method: 'PUT',
                                                headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({ scope_type: permData?.scope_type || 'company', scope_ids: permData?.scope_ids || [], access_level: newLevel }),
                                            });
                                            queryClient.invalidateQueries({ queryKey: ['agent-permissions', id] });
                                            queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                        } catch (e) {
                                            console.error('Failed to update access level', e);
                                        }
                                    };

                                    const isOwner = permData?.is_owner ?? false;
                                    const currentScope = permData?.scope_type || 'company';
                                    const currentAccessLevel = permData?.access_level || 'use';

                                    return (
                                        <div className="card" style={{ marginBottom: '12px' }}>
                                            <h4 style={{ marginBottom: '12px' }}>🔒 {t('agent.settings.perm.title', 'Access Permissions')}</h4>
                                            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                                                {t('agent.settings.perm.description', 'Control who can see and interact with this agent. Only the creator or admin can change this.')}
                                            </p>

                                            {/* Scope Selection */}
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '16px' }}>
                                                {(['company', 'user'] as const).map((scope) => (
                                                    <label
                                                        key={scope}
                                                        style={{
                                                            display: 'flex',
                                                            alignItems: 'center',
                                                            gap: '10px',
                                                            padding: '12px 14px',
                                                            borderRadius: '8px',
                                                            cursor: isOwner ? 'pointer' : 'default',
                                                            border: currentScope === scope
                                                                ? '1px solid var(--accent-primary)'
                                                                : '1px solid var(--border-subtle)',
                                                            background: currentScope === scope
                                                                ? 'rgba(99,102,241,0.06)'
                                                                : 'transparent',
                                                            opacity: isOwner ? 1 : 0.7,
                                                            transition: 'all 0.15s',
                                                        }}
                                                    >
                                                        <input
                                                            type="radio"
                                                            name="perm_scope"
                                                            checked={currentScope === scope}
                                                            disabled={!isOwner}
                                                            onChange={() => handleScopeChange(scope)}
                                                            style={{ accentColor: 'var(--accent-primary)' }}
                                                        />
                                                        <div>
                                                            <div style={{ fontWeight: 500, fontSize: '13px' }}>{scopeLabels[scope]}</div>
                                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                                {scope === 'company' && t('agent.settings.perm.companyWideDesc', 'All users in the organization can use this agent')}
                                                                {scope === 'user' && t('agent.settings.perm.onlyMeDesc', 'Only the creator can use this agent')}
                                                            </div>
                                                        </div>
                                                    </label>
                                                ))}
                                            </div>

                                            {/* Access Level for company scope */}
                                            {currentScope === 'company' && isOwner && (
                                                <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px' }}>
                                                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '8px' }}>
                                                        {t('agent.settings.perm.defaultAccess', 'Default Access Level')}
                                                    </label>
                                                    <div style={{ display: 'flex', gap: '8px' }}>
                                                        {[{ val: 'use', label: '👁️ ' + t('agent.settings.perm.useAccess', 'Use'), desc: t('agent.settings.perm.useAccessDesc', 'Task, Chat, Tools, Skills, Workspace') },
                                                        { val: 'manage', label: '⚙️ ' + t('agent.settings.perm.manageAccess', 'Manage'), desc: t('agent.settings.perm.manageAccessDesc', 'Full access including Settings, Mind, Relationships') }].map(opt => (
                                                            <label key={opt.val}
                                                                style={{
                                                                    flex: 1,
                                                                    padding: '10px 12px',
                                                                    borderRadius: '8px',
                                                                    cursor: 'pointer',
                                                                    border: currentAccessLevel === opt.val
                                                                        ? '1px solid var(--accent-primary)'
                                                                        : '1px solid var(--border-subtle)',
                                                                    background: currentAccessLevel === opt.val
                                                                        ? 'rgba(99,102,241,0.06)'
                                                                        : 'transparent',
                                                                    transition: 'all 0.15s',
                                                                }}
                                                            >
                                                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                                    <input type="radio" name="access_level" checked={currentAccessLevel === opt.val}
                                                                        onChange={() => handleAccessLevelChange(opt.val)}
                                                                        style={{ accentColor: 'var(--accent-primary)' }} />
                                                                    <span style={{ fontWeight: 500, fontSize: '13px' }}>{opt.label}</span>
                                                                </div>
                                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px', marginLeft: '20px' }}>{opt.desc}</div>
                                                            </label>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}

                                            {currentScope !== 'company' && permData?.scope_names?.length > 0 && (
                                                <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                    <span style={{ fontWeight: 500 }}>{t('agent.settings.perm.currentAccess', 'Current access')}:</span>{' '}
                                                    {permData.scope_names.map((s: any) => s.name).join(', ')}
                                                </div>
                                            )}

                                            {!isOwner && (
                                                <div style={{ marginTop: '12px', fontSize: '11px', color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
                                                    {t('agent.settings.perm.readOnly', 'Only the creator or admin can change permissions')}
                                                </div>
                                            )}
                                        </div>
                                    );
                                })()}

                                {/* Timezone */}
                                <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        {t('agent.settings.timezone.title', '🌐 Timezone')}
                                    </h4>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                                        {t('agent.settings.timezone.description', 'The timezone used for this agent\'s scheduling, active hours, and time awareness. Defaults to the company timezone if not set.')}
                                    </p>
                                    <div style={{
                                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                        padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px',
                                        border: '1px solid var(--border-subtle)',
                                    }}>
                                        <div>
                                            <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.timezone.current', 'Agent Timezone')}</div>
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                                {agent?.timezone
                                                    ? t('agent.settings.timezone.override', 'Custom timezone for this agent')
                                                    : t('agent.settings.timezone.inherited', 'Using company default timezone')}
                                            </div>
                                        </div>
                                        <select
                                            className="input"
                                            disabled={!canManage}
                                            value={agent?.timezone || ''}
                                            onChange={async (e) => {
                                                if (!canManage) return;
                                                const val = e.target.value || null;
                                                await agentApi.update(id!, { timezone: val } as any);
                                                queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                            }}
                                            style={{ width: '200px', fontSize: '12px', opacity: canManage ? 1 : 0.6 }}
                                        >
                                            <option value="">{t('agent.settings.timezone.default', '↩ Company default')}</option>
                                            {['UTC', 'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Seoul', 'Asia/Singapore', 'Asia/Kolkata', 'Asia/Dubai',
                                                'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow',
                                                'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
                                                'America/Sao_Paulo', 'Australia/Sydney', 'Pacific/Auckland'].map(tz => (
                                                    <option key={tz} value={tz}>{tz}</option>
                                                ))}
                                        </select>
                                    </div>
                                </div>

                                {/* Heartbeat */}
                                <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        {t('agent.settings.heartbeat.title', 'Heartbeat')}
                                    </h4>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                                        {t('agent.settings.heartbeat.description', 'Periodic awareness check — agent proactively monitors the plaza and work environment.')}
                                    </p>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                                        {/* Enable toggle */}
                                        <div style={{
                                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                            padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px',
                                            border: '1px solid var(--border-subtle)',
                                        }}>
                                            <div>
                                                <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.heartbeat.enabled', 'Enable Heartbeat')}</div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.settings.heartbeat.enabledDesc', 'Agent will periodically check plaza and work status')}</div>
                                            </div>
                                            <label style={{ position: 'relative', display: 'inline-block', width: '44px', height: '24px', cursor: canManage ? 'pointer' : 'default' }}>
                                                <input
                                                    type="checkbox"
                                                    checked={agent?.heartbeat_enabled ?? true}
                                                    disabled={!canManage}
                                                    onChange={async (e) => {
                                                        if (!canManage) return;
                                                        await agentApi.update(id!, { heartbeat_enabled: e.target.checked } as any);
                                                        queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                                    }}
                                                    style={{ opacity: 0, width: 0, height: 0 }}
                                                />
                                                <span style={{
                                                    position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
                                                    background: (agent?.heartbeat_enabled ?? true) ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                                                    borderRadius: '12px', transition: 'background 0.2s',
                                                    opacity: canManage ? 1 : 0.6
                                                }}>
                                                    <span style={{
                                                        position: 'absolute', top: '3px',
                                                        left: (agent?.heartbeat_enabled ?? true) ? '23px' : '3px',
                                                        width: '18px', height: '18px', background: 'white',
                                                        borderRadius: '50%', transition: 'left 0.2s',
                                                    }} />
                                                </span>
                                            </label>
                                        </div>

                                        {/* Interval */}
                                        <div style={{
                                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                            padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px',
                                            border: '1px solid var(--border-subtle)',
                                        }}>
                                            <div>
                                                <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.heartbeat.interval', 'Check Interval')}</div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.settings.heartbeat.intervalDesc', 'How often the agent checks for updates')}</div>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                <input
                                                    type="number"
                                                    className="input"
                                                    disabled={!canManage}
                                                    min={1}
                                                    defaultValue={agent?.heartbeat_interval_minutes ?? 120}
                                                    key={agent?.heartbeat_interval_minutes}
                                                    onBlur={async (e) => {
                                                        if (!canManage) return;
                                                        const val = Math.max(1, Number(e.target.value) || 120);
                                                        e.target.value = String(val);
                                                        await agentApi.update(id!, { heartbeat_interval_minutes: val } as any);
                                                        queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                                    }}
                                                    style={{ width: '80px', fontSize: '12px', opacity: canManage ? 1 : 0.6 }}
                                                />
                                                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('common.minutes', 'min')}</span>
                                            </div>
                                        </div>

                                        {/* Active Hours */}
                                        <div style={{
                                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                            padding: '10px 14px', background: 'var(--bg-elevated)', borderRadius: '8px',
                                            border: '1px solid var(--border-subtle)',
                                        }}>
                                            <div>
                                                <div style={{ fontWeight: 500, fontSize: '13px' }}>{t('agent.settings.heartbeat.activeHours', 'Active Hours')}</div>
                                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{t('agent.settings.heartbeat.activeHoursDesc', 'Only trigger heartbeat during these hours (HH:MM-HH:MM)')}</div>
                                            </div>
                                            <input
                                                className="input"
                                                disabled={!canManage}
                                                value={agent?.heartbeat_active_hours ?? '09:00-18:00'}
                                                onChange={async (e) => {
                                                    if (!canManage) return;
                                                    await agentApi.update(id!, { heartbeat_active_hours: e.target.value } as any);
                                                    queryClient.invalidateQueries({ queryKey: ['agent', id] });
                                                }}
                                                style={{ width: '140px', fontSize: '12px', textAlign: 'center', opacity: canManage ? 1 : 0.6 }}
                                                placeholder="09:00-18:00"
                                            />
                                        </div>



                                        {/* Last Heartbeat */}
                                        {agent?.last_heartbeat_at && (
                                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', paddingLeft: '4px' }}>
                                                {t('agent.settings.heartbeat.lastRun', 'Last heartbeat')}: {new Date(agent.last_heartbeat_at).toLocaleString()}
                                            </div>
                                        )}
                                    </div>
                                </div>

                                {/* Channel Config */}
                                <div style={{ marginBottom: "12px" }}>
                                    <ChannelConfig mode="edit" agentId={id!} />
                                </div>

                                {/* Danger Zone */}
                                <div className="card" style={{ borderColor: 'var(--error)' }}>
                                    <h4 style={{ color: 'var(--error)', marginBottom: '12px' }}>{t('agent.settings.danger.title')}</h4>
                                    <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                                        {t('agent.settings.danger.deleteWarning')}
                                    </p>
                                    {
                                        !showDeleteConfirm ? (
                                            <button className="btn btn-danger" onClick={() => setShowDeleteConfirm(true)}>× {t('agent.settings.danger.deleteAgent')}</button>
                                        ) : (
                                            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                                <span style={{ fontSize: '13px', color: 'var(--error)', fontWeight: 600 }}>{t('agent.settings.danger.deleteWarning')}</span>
                                                <button className="btn btn-danger" onClick={async () => {
                                                    try {
                                                        await agentApi.delete(id!);
                                                        queryClient.invalidateQueries({ queryKey: ['agents'] });
                                                        navigate('/');
                                                    } catch (err: any) {
                                                        alert(err?.message || 'Failed to delete agent');
                                                    }
                                                }}>{t('agent.settings.danger.confirmDelete')}</button>
                                                <button className="btn btn-secondary" onClick={() => setShowDeleteConfirm(false)}>{t('common.cancel')}</button>
                                            </div>
                                        )
                                    }
                                </div >
                            </div >
                        )
                    })()
                }
            </div >

            <PromptModal
                open={!!promptModal}
                title={promptModal?.title || ''}
                placeholder={promptModal?.placeholder || ''}
                onCancel={() => setPromptModal(null)}
                onConfirm={async (value) => {
                    const action = promptModal?.action;
                    setPromptModal(null);
                    if (action === 'newFolder') {
                        await fileApi.write(id!, `${workspacePath}/${value}/.gitkeep`, '');
                        queryClient.invalidateQueries({ queryKey: ['files', id, workspacePath] });
                    } else if (action === 'newFile') {
                        await fileApi.write(id!, `${workspacePath}/${value}`, '');
                        queryClient.invalidateQueries({ queryKey: ['files', id, workspacePath] });
                        setViewingFile(`${workspacePath}/${value}`);
                        setFileEditing(true);
                        setFileDraft('');
                    } else if (action === 'newSkill') {
                        const template = `---\nname: ${value}\ndescription: Describe what this skill does\n---\n\n# ${value}\n\n## Overview\nDescribe the purpose and when to use this skill.\n\n## Process\n1. Step one\n2. Step two\n\n## Output Format\nDescribe the expected output format.\n`;
                        await fileApi.write(id!, `skills/${value}/SKILL.md`, template);
                        queryClient.invalidateQueries({ queryKey: ['files', id, 'skills'] });
                        setViewingFile(`skills/${value}/SKILL.md`);
                        setFileEditing(true);
                        setFileDraft(template);
                    }
                }}
            />

            <ConfirmModal
                open={!!deleteConfirm}
                title={t('common.delete')}
                message={`${t('common.delete')}: ${deleteConfirm?.name}?`}
                confirmLabel={t('common.delete')}
                danger
                onCancel={() => setDeleteConfirm(null)}
                onConfirm={async () => {
                    const path = deleteConfirm?.path;
                    setDeleteConfirm(null);
                    if (path) {
                        try {
                            await fileApi.delete(id!, path);
                            setViewingFile(null);
                            setFileEditing(false);
                            queryClient.invalidateQueries({ queryKey: ['files', id, workspacePath] });
                            showToast(t('common.delete'));
                        } catch (err: any) {
                            showToast(t('agent.upload.failed'), 'error');
                        }
                    }
                }}
            />

            {
                uploadToast && (
                    <div style={{
                        position: 'fixed', top: '20px', right: '20px', zIndex: 20000,
                        padding: '12px 20px', borderRadius: '8px',
                        background: uploadToast.type === 'success' ? 'rgba(34, 197, 94, 0.9)' : 'rgba(239, 68, 68, 0.9)',
                        color: '#fff', fontSize: '14px', fontWeight: 500,
                        boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
                    }}>
                        {''}{uploadToast.message}
                    </div>
                )
            }

            {/* ── Expiry Editor Modal (admin only) ── */}
            {
                showExpiryModal && (
                    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 9000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                        onClick={() => setShowExpiryModal(false)}>
                        <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)', borderRadius: '12px', padding: '24px', width: '360px', maxWidth: '90vw' }}
                            onClick={e => e.stopPropagation()}>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
                                <h3 style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>⏰ {t('agent.settings.expiry.title')}</h3>
                                <button onClick={() => setShowExpiryModal(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: '18px', lineHeight: 1 }}>×</button>
                            </div>
                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                                {(agent as any).is_expired
                                    ? <span style={{ color: 'var(--error)', fontWeight: 600 }}>⏰ {t('agent.settings.expiry.expired')}</span>
                                    : (agent as any).expires_at
                                        ? <>{t('agent.settings.expiry.currentExpiry')} <strong>{new Date((agent as any).expires_at).toLocaleString(i18n.language === 'zh' ? 'zh-CN' : 'en-US')}</strong></>
                                        : <span style={{ color: 'var(--success)' }}>{t('agent.settings.expiry.neverExpires')}</span>
                                }
                            </div>
                            <div style={{ marginBottom: '16px' }}>
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '8px' }}>{t('agent.settings.expiry.quickRenew')}</div>
                                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                                    {([
                                        ['+ 24h', 24],
                                        [`+ ${t('agent.settings.expiry.days', { count: 7 })}`, 168],
                                        [`+ ${t('agent.settings.expiry.days', { count: 30 })}`, 720],
                                        [`+ ${t('agent.settings.expiry.days', { count: 90 })}`, 2160],
                                    ] as [string, number][]).map(([label, h]) => (
                                        <button key={h} onClick={() => addHours(h)}
                                            style={{ padding: '4px 10px', borderRadius: '6px', border: '1px solid var(--border-subtle)', background: 'var(--bg-primary)', cursor: 'pointer', fontSize: '12px', color: 'var(--text-primary)' }}>
                                            {label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                            <div style={{ marginBottom: '20px' }}>
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.settings.expiry.customDeadline')}</div>
                                <input type="datetime-local" value={expiryValue} onChange={e => setExpiryValue(e.target.value)}
                                    style={{ width: '100%', padding: '8px 10px', borderRadius: '8px', border: '1px solid var(--border-subtle)', background: 'var(--bg-primary)', color: 'var(--text-primary)', fontSize: '13px', boxSizing: 'border-box' }} />
                            </div>
                            <div style={{ display: 'flex', gap: '8px', justifyContent: 'space-between', alignItems: 'center' }}>
                                <button onClick={() => saveExpiry(true)} disabled={expirySaving}
                                    style={{ padding: '7px 12px', borderRadius: '8px', border: '1px solid var(--border-subtle)', background: 'none', cursor: 'pointer', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                    🔓 {t('agent.settings.expiry.neverExpires')}
                                </button>
                                <div style={{ display: 'flex', gap: '8px' }}>
                                    <button onClick={() => setShowExpiryModal(false)} disabled={expirySaving}
                                        style={{ padding: '7px 14px', borderRadius: '8px', border: '1px solid var(--border-subtle)', background: 'none', cursor: 'pointer', fontSize: '13px', color: 'var(--text-secondary)' }}>
                                        {t('common.cancel')}
                                    </button>
                                    <button onClick={() => saveExpiry(false)} disabled={expirySaving || !expiryValue}
                                        className="btn btn-primary"
                                        style={{ opacity: !expiryValue ? 0.5 : 1 }}>
                                        {expirySaving ? t('agent.settings.expiry.saving') : t('common.save')}
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                )
            }

        </>
    );
}

// Error boundary to catch unhandled React errors and prevent white screen
class AgentDetailErrorBoundary extends Component<{ children: React.ReactNode }, { hasError: boolean; error: Error | null }> {
    constructor(props: { children: React.ReactNode }) {
        super(props);
        this.state = { hasError: false, error: null };
    }
    static getDerivedStateFromError(error: Error) {
        return { hasError: true, error };
    }
    componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error('AgentDetail crash caught by error boundary:', error, errorInfo);
    }
    render() {
        if (this.state.hasError) {
            return (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '60vh', gap: '16px' }}>
                    <div style={{ fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>Something went wrong</div>
                    <div style={{ fontSize: '13px', color: 'var(--text-tertiary)', maxWidth: '400px', textAlign: 'center' }}>
                        {this.state.error?.message || 'An unexpected error occurred while loading this page.'}
                    </div>
                    <button
                        className="btn btn-primary"
                        onClick={() => { this.setState({ hasError: false, error: null }); window.location.reload(); }}
                        style={{ marginTop: '8px' }}
                    >
                        Reload Page
                    </button>
                </div>
            );
        }
        return this.props.children;
    }
}

// Wrap the AgentDetail component with error boundary
export default function AgentDetailWithErrorBoundary() {
    return (
        <AgentDetailErrorBoundary>
            <AgentDetailInner />
        </AgentDetailErrorBoundary>
    );
}
