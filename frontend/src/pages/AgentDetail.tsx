import React, { useState, useEffect, useMemo, useRef, useCallback, Component, ErrorInfo } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import ConfirmModal from '../components/ConfirmModal';
import { useDialog } from '../components/Dialog/DialogProvider';
import { useToast } from '../components/Toast/ToastProvider';
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
import { activityApi, agentApi, channelApi, enterpriseApi, fileApi, focusApi, scheduleApi, skillApi, taskApi, tenantApi, triggerApi, uploadFileWithProgress } from '../services/api';
import type { FocusApiItem } from '../services/api';
import ModelSwitcher from '../components/ModelSwitcher';
import { useAppStore } from '../stores';
import { useAuthStore } from '../stores';
import { copyToClipboard } from '../utils/clipboard';
import { formatFileSize } from '../utils/formatFileSize';
import {
    IconBrain,
    IconBrowser,
    IconBuilding,
    IconCheck,
    IconChevronDown,
    IconClock,
    IconDna,
    IconDownload,
    IconEye,
    IconFileText,
    IconFolder,
    IconHeartbeat,
    IconLock,
    IconMailForward,
    IconMessageCircle,
    IconPaperclip,
    IconPlugConnected,
    IconRobot,
    IconSearch,
    IconSend,
    IconSettings,
    IconTerminal2,
    IconTools,
    IconUser,
    IconWorld,
    IconBolt,
    IconAlertTriangle,
} from '@tabler/icons-react';
import { useDropZone } from '../hooks/useDropZone';

const TABS = ['status', 'aware', 'mind', 'tools', 'skills', 'relationships', 'workspace', 'chat', 'activityLog', 'approvals', 'settings'] as const;

const WORKSPACE_TOOLS = new Set([
    'write_file',
    'edit_file',
    'move_file',
    'delete_file',
    'convert_markdown_to_docx',
    'convert_csv_to_xlsx',
    'convert_markdown_to_pdf',
    'convert_html_to_pdf',
    'convert_html_to_pptx',
]);

const AWARE_TOOLS = new Set(['set_trigger', 'update_trigger', 'cancel_trigger', 'list_triggers', 'list_focus_items', 'upsert_focus_item', 'complete_focus_item']);
const EMOJI_RE = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u;
const trimLeadingPictograph = (value: string) => value.replace(/^\p{Extended_Pictographic}\s*/u, '');
const formatReflectionTitle = (value: string | undefined, isZh: boolean) => {
    const clean = trimLeadingPictograph(value || 'Trigger execution').trim();
    const legacyMatch = clean.match(/^内心独白[:：]\s*(.*)$/);
    if (legacyMatch) return isZh ? `内心独白：${legacyMatch[1]}` : `Reflection: ${legacyMatch[1]}`;
    return clean;
};
const safeDisplayIcon = (icon?: string | null, fallback: React.ReactNode = <IconTools size={18} stroke={1.8} />) =>
    icon && !EMOJI_RE.test(icon) ? icon : fallback;

type FocusItem = {
    id: string;
    name: string;
    description: string;
    done: boolean;
    inProgress: boolean;
    section: 'active' | 'system' | 'completed';
    synthetic?: boolean;
    system?: boolean;
};

function focusItemFromApi(item: FocusApiItem): FocusItem {
    const done = item.status === 'completed';
    const system = item.kind === 'system';
    return {
        id: item.id,
        name: item.key,
        description: item.description || item.key,
        done,
        inProgress: !done,
        section: done ? 'completed' : (system ? 'system' : 'active'),
        system,
    };
}

function isFocusPath(path?: string | null): boolean {
    if (!path) return false;
    const normalized = path.replace(/^\/+/, '').toLowerCase();
    return normalized === 'focus.md' || normalized.endsWith('/focus.md');
}

function workspaceActionForTool(tool: string): WorkspaceLiveDraft['action'] {
    if (tool === 'edit_file') return 'edit';
    if (tool === 'move_file') return 'move';
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
    const destinationPath = getString('destination_path');
    const path = destinationPath || getString('path') || getString('target_path') || sourcePath;
    let content = getString('content');
    if (tool === 'edit_file') content = getString('new_string') || content;
    return { path, content };
}

function parseFocusItems(raw: string): FocusItem[] {
    const lines = raw.split('\n');
    const focusItems: FocusItem[] = [];
    let currentItem: FocusItem | null = null;
    let currentSection: FocusItem['section'] = 'active';
    for (const line of lines) {
        const heading = line.match(/^##\s+(.+?)\s*$/);
        if (heading) {
            const title = heading[1].trim().toLowerCase();
            if (title === '已完成' || title === 'completed') currentSection = 'completed';
            else if (title === '系统 focus' || title === 'system focus' || title === 'system') currentSection = 'system';
            else if (title === '进行中' || title === 'in progress' || title === 'active') currentSection = 'active';
            continue;
        }
        const match = line.match(/^\s*-\s*\[([ x/])\]\s*(.+)/i);
        if (match) {
            if (currentItem) focusItems.push(currentItem);
            const marker = match[1];
            const fullText = match[2].trim();
            const systemKeyMatch = fullText.match(/^(system:[^:]+)\s*:\s*(.*)$/);
            const colonIdx = systemKeyMatch ? -1 : fullText.indexOf(':');
            const itemName = colonIdx > 0 ? fullText.substring(0, colonIdx).trim() : fullText;
            const itemDesc = colonIdx > 0 ? fullText.substring(colonIdx + 1).trim() : '';
            currentItem = {
                id: systemKeyMatch ? systemKeyMatch[1] : itemName,
                name: systemKeyMatch ? systemKeyMatch[1] : itemName,
                description: systemKeyMatch ? systemKeyMatch[2] : itemDesc,
                done: marker.toLowerCase() === 'x' || currentSection === 'completed',
                inProgress: marker === '/',
                section: systemKeyMatch ? 'system' : currentSection,
                system: currentSection === 'system' || !!systemKeyMatch,
            };
        } else if (currentItem && line.trim() && /^\s{2,}/.test(line)) {
            currentItem.description = currentItem.description
                ? `${currentItem.description} ${line.trim()}`
                : line.trim();
        }
    }
    if (currentItem) focusItems.push(currentItem);
    return focusItems;
}

function isOkrSystemTrigger(trig: any): boolean {
    if (!trig?.is_system) return false;
    const name = String(trig.name || '');
    return /(^|_)(okr|daily_okr|weekly_okr|biweekly_okr|monthly_okr|okr_collection|okr_report)/i.test(name);
}

function focusKeyFromTrigger(trig: any): string {
    if (trig?.focus_ref) return String(trig.focus_ref);
    if (isOkrSystemTrigger(trig)) return 'system:okr_reports';
    if (trig?.is_system) return `system:${String(trig.name || 'trigger')}`;
    return String(trig.name || trig.reason || 'trigger_focus');
}

function synthesizeFocusForTrigger(trig: any): FocusItem {
    const key = focusKeyFromTrigger(trig);
    const isSystem = !!trig.is_system || key.startsWith('system:');
    return {
        id: `synthetic:${key}`,
        name: key,
        description: key === 'system:okr_reports'
            ? 'OKR 自动汇总、日报收集与周期报告'
            : trig.reason || trig.name || key,
        done: !trig.is_enabled && !isSystem,
        inProgress: false,
        section: isSystem ? 'system' : 'active',
        synthetic: true,
        system: isSystem,
    };
}

function parseAgentBayTransferArgs(rawArgs: any): NonNullable<LivePreviewState['transfer']> {
    const parsed = typeof rawArgs === 'string'
        ? (() => {
            try { return JSON.parse(rawArgs || '{}'); } catch { return {}; }
        })()
        : (rawArgs || {});
    return {
        fromType: typeof parsed.from_type === 'string' ? parsed.from_type : undefined,
        fromPath: typeof parsed.from_path === 'string' ? parsed.from_path : undefined,
        toType: typeof parsed.to_type === 'string' ? parsed.to_type : undefined,
        toPath: typeof parsed.to_path === 'string' ? parsed.to_path : undefined,
        updatedAt: Date.now(),
    };
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

const formatTokensParts = (n: number): { value: string; unit: string } => {
    if (!n) return { value: '0', unit: '' };
    if (n >= 1000000) return { value: (n / 1000000).toFixed(1), unit: 'M' };
    if (n >= 1000) return { value: (n / 1000).toFixed(1), unit: 'K' };
    return { value: String(n), unit: '' };
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
    const dialog = useDialog();
    const toast = useToast();
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
    const [expandedCategories, setExpandedCategories] = useState<Set<string>>(() => new Set());
    const [toolSearch, setToolSearch] = useState('');
    const [toolStatusFilter, setToolStatusFilter] = useState<'all' | 'enabled' | 'disabled' | 'configured'>('all');
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
        } catch (e: any) { toast.error('保存失败', { details: String(e?.message || e) }); }
        setConfigSaving(false);
    };

    if (loading) return <div style={{ color: 'var(--text-tertiary)', padding: '20px' }}>{t('common.loading')}</div>;

    // Company tools = platform presets (builtin) + company admin-added tools (admin)
    const companyTools = tools.filter(t => t.source === 'builtin' || t.source === 'admin');
    const agentInstalledTools = tools.filter(t => t.source === 'agent');

    const mcpGroupKey = (tool: any) => {
        const serverName = String(tool.mcp_server_name || '').trim();
        return tool.type === 'mcp' && serverName
            ? `mcp:${serverName.toLowerCase()}`
            : (tool.category || 'general');
    };

    const getToolGroupMeta = (groupKey: string, toolsInGroup: any[]) => {
        const first = toolsInGroup.find((tool) => tool.type === 'mcp' && tool.mcp_server_name) || toolsInGroup[0];
        if (groupKey.startsWith('mcp:') && first?.mcp_server_name) {
            return {
                label: first.mcp_server_name,
                description: t('agent.tools.mcpGroupDescription', 'Tools from {{name}}', { name: first.mcp_server_name }),
                iconCategory: 'custom',
                configCategory: first.category || 'custom',
            };
        }
        return {
            label: categoryLabels[groupKey] || groupKey,
            description: categoryDescriptions[groupKey] || 'Tools in this category',
            iconCategory: groupKey,
            configCategory: groupKey,
        };
    };

    const groupByCategory = (toolList: any[]) =>
        toolList.reduce((acc: Record<string, any[]>, t) => {
            const cat = mcpGroupKey(t);
            (acc[cat] = acc[cat] || []).push(t);
            return acc;
        }, {});

    const categoryLabels = getCategoryLabels(t);
    const categoryDescriptions: Record<string, string> = {
        agentbay: 'Browser and cloud computer automation',
        file: 'Read, write, convert, and manage workspace files',
        communication: 'Messages and cross-channel collaboration',
        search: 'Web and knowledge search tools',
        code: 'Code execution and development utilities',
        aware: 'Triggers, reminders, and awareness workflows',
        email: 'Email reading and sending tools',
        feishu: 'Feishu / Lark messaging and collaboration',
        okr: 'Objectives, key results, and progress reporting',
        social: 'Social publishing and community workflows',
        discovery: 'Tool and capability discovery',
        custom: 'Company-added or MCP tools',
        general: 'General purpose tools',
    };
    const renderCategoryIcon = (category: string, size = 15) => {
        const style = { color: 'var(--text-tertiary)' };
        switch (category) {
            case 'agentbay': return <IconBrowser size={size} stroke={1.8} style={style} />;
            case 'file': return <IconFileText size={size} stroke={1.8} style={style} />;
            case 'communication':
            case 'feishu':
            case 'email':
            case 'social':
                return <IconMessageCircle size={size} stroke={1.8} style={style} />;
            case 'search':
            case 'discovery':
                return <IconSearch size={size} stroke={1.8} style={style} />;
            case 'code': return <IconTerminal2 size={size} stroke={1.8} style={style} />;
            case 'aware': return <IconClock size={size} stroke={1.8} style={style} />;
            case 'custom': return <IconSettings size={size} stroke={1.8} style={style} />;
            default: return <IconTools size={size} stroke={1.8} style={style} />;
        }
    };

    const switchTrack = (enabled: boolean, mixed = false) => ({
        position: 'absolute' as const,
        inset: 0,
        background: enabled ? 'var(--accent-primary)' : mixed ? 'var(--border-default)' : 'var(--bg-tertiary)',
        borderRadius: '11px',
        transition: 'background 0.2s',
    });

    const switchKnob = (enabled: boolean) => ({
        position: 'absolute' as const,
        left: enabled ? '20px' : '2px',
        top: '2px',
        width: '18px',
        height: '18px',
        background: '#fff',
        borderRadius: '50%',
        transition: 'left 0.2s',
        boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
    });

    const toggleCategoryExpanded = (category: string) => {
        setExpandedCategories(prev => {
            const next = new Set(prev);
            if (next.has(category)) next.delete(category);
            else next.add(category);
            return next;
        });
    };

    const bulkToggleCategory = async (catTools: any[], enabled: boolean) => {
        const catToolIds = new Set(catTools.map(t => t.id));
        setTools(prev => prev.map(t => catToolIds.has(t.id) ? { ...t, enabled } : t));
        try {
            const token = localStorage.getItem('token');
            const payload = Array.from(catToolIds).map(id => ({ tool_id: id, enabled }));
            await fetch(`/api/tools/agents/${agentId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify(payload),
            });
        } catch (err: any) {
            console.error('Bulk update failed', err);
            loadTools();
        }
    };

    const renderToolRow = (tool: any, category: string) => {
        const hasConfig = tool.config_schema?.fields?.length > 0 || tool.type === 'mcp';
        const hasAgentOverride = tool.agent_config && Object.keys(tool.agent_config).length > 0;
        const isGlobalCategoryConfig = category === 'agentbay' && tool.name === 'agentbay_browser_navigate';
        return (
            <div key={tool.id} style={{
                display: 'grid',
                gridTemplateColumns: 'minmax(0, 1fr) auto',
                alignItems: 'center',
                gap: '12px',
                padding: '10px 14px',
                borderTop: '1px solid var(--border-subtle)',
                background: 'var(--bg-primary)',
            }}>
                <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0 }}>
                        <span style={{ fontWeight: 500, fontSize: '13px', color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tool.display_name}</span>
                        {tool.type === 'mcp' && (
                            <span style={{ fontSize: '10px', background: 'var(--primary)', color: '#fff', borderRadius: '4px', padding: '1px 5px', flexShrink: 0 }}>MCP</span>
                        )}
                        {tool.type === 'builtin' && (
                            <span style={{ fontSize: '10px', background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', borderRadius: '4px', padding: '1px 5px', flexShrink: 0 }}>Built-in</span>
                        )}
                        {hasAgentOverride && (
                            <span style={{ fontSize: '10px', background: 'rgba(99,102,241,0.15)', color: 'var(--accent-color)', borderRadius: '4px', padding: '1px 5px', flexShrink: 0 }}>{t('enterprise.tools.configured', 'Configured')}</span>
                        )}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {tool.description}
                        {tool.mcp_server_name && <span> · {tool.mcp_server_name}</span>}
                    </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                    {canManage && hasConfig && !isGlobalCategoryConfig && (
                        <button
                            onClick={() => openConfig(tool)}
                            style={{ background: 'none', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '3px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)', display: 'inline-flex', alignItems: 'center', gap: '4px' }}
                            title={t('agent.tools.configurePerAgent', 'Configure per-agent settings')}
                        ><IconSettings size={12} stroke={1.8} /> {t('agent.tools.config', 'Config')}</button>
                    )}
                    {canManage && tool.source === 'agent' && tool.agent_tool_id && (
                        <button
                            onClick={async () => {
                                const ok = await dialog.confirm(
                                    t('agent.tools.confirmDelete', `Remove "${tool.display_name}" from this agent?`),
                                    { danger: true, confirmLabel: '移除' },
                                );
                                if (!ok) return;
                                setDeletingToolId(tool.id);
                                try {
                                    const token = localStorage.getItem('token');
                                    const res = await fetch(`/api/tools/agent-tool/${tool.agent_tool_id}`, {
                                        method: 'DELETE',
                                        headers: { Authorization: `Bearer ${token}` },
                                    });
                                    if (res.ok) await loadTools();
                                    else toast.error(t('agent.tools.deleteFailed', 'Delete failed'));
                                } catch (e: any) { toast.error(t('agent.tools.deleteFailed', 'Delete failed'), { details: String(e?.message || e) }); }
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
                            <span style={switchTrack(tool.enabled)}>
                                <span style={switchKnob(tool.enabled)} />
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
    };

    const renderToolGroup = (groupedTools: Record<string, any[]>, allGroupedTools: Record<string, any[]>) =>
        Object.entries(groupedTools)
            .sort(([a, aTools], [b, bTools]) => {
                const aMeta = getToolGroupMeta(a, allGroupedTools[a] || aTools);
                const bMeta = getToolGroupMeta(b, allGroupedTools[b] || bTools);
                return aMeta.label.localeCompare(bMeta.label);
            })
            .map(([category, catTools]) => {
                const allCatTools = allGroupedTools[category] || catTools;
                const meta = getToolGroupMeta(category, allCatTools);
                const label = meta.label;
                const enabledCount = allCatTools.filter((tool: any) => tool.enabled).length;
                const configuredCount = allCatTools.filter((tool: any) => tool.agent_config && Object.keys(tool.agent_config).length > 0).length;
                const allEnabled = allCatTools.length > 0 && enabledCount === allCatTools.length;
                const mixed = enabledCount > 0 && enabledCount < allCatTools.length;
                const expanded = expandedCategories.has(category) || !!toolSearch.trim();
                const visibleCount = (catTools as any[]).length;
                return (
                    <div key={category} style={{
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '8px',
                        overflow: 'hidden',
                        background: 'var(--bg-primary)',
                    }}>
                        <div
                            role="button"
                            tabIndex={0}
                            onClick={() => toggleCategoryExpanded(category)}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault();
                                    toggleCategoryExpanded(category);
                                }
                            }}
                            style={{
                                width: '100%',
                                background: 'var(--bg-secondary)',
                                padding: '13px 16px',
                                display: 'grid',
                                gridTemplateColumns: '1fr auto',
                                gap: '14px',
                                alignItems: 'center',
                                cursor: 'pointer',
                                textAlign: 'left',
                                boxSizing: 'border-box',
                            }}
                        >
                            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0 }}>
                                <IconChevronDown
                                    size={16}
                                    style={{
                                        transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)',
                                        transition: 'transform 120ms ease',
                                        color: 'var(--text-tertiary)',
                                        flexShrink: 0,
                                    }}
                                />
                                <span style={{
                                    width: '28px',
                                    height: '28px',
                                    borderRadius: '7px',
                                    border: '1px solid var(--border-subtle)',
                                    background: 'var(--bg-primary)',
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    flexShrink: 0,
                                }}>{renderCategoryIcon(meta.iconCategory, 16)}</span>
                                <div style={{ minWidth: 0 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                        <span style={{ fontSize: '13px', fontWeight: 650, color: 'var(--text-primary)' }}>{label}</span>
                                        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            {allCatTools.length} tools · {enabledCount} enabled
                                            {visibleCount !== allCatTools.length ? ` · ${visibleCount} shown` : ''}
                                        </span>
                                        {configuredCount > 0 && (
                                            <span style={{ fontSize: '10px', background: 'rgba(99,102,241,0.15)', color: 'var(--accent-color)', borderRadius: '4px', padding: '1px 5px' }}>
                                                {configuredCount} configured
                                            </span>
                                        )}
                                    </div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {meta.description}
                                    </div>
                                </div>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }} onClick={(e) => e.stopPropagation()}>
                                {CATEGORY_CONFIG_SCHEMAS[meta.configCategory] && canManage && (
                                    <button
                                        type="button"
                                        onClick={() => openCategoryConfig(meta.configCategory)}
                                        style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: '6px', padding: '4px 8px', fontSize: '11px', cursor: 'pointer', color: 'var(--text-secondary)', display: 'inline-flex', alignItems: 'center', gap: '4px' }}
                                        title={t('agent.tools.configureCategory', 'Configure {{category}}', { category: label })}
                                    ><IconSettings size={12} stroke={1.8} /> {t('agent.tools.config', 'Config')}</button>
                                )}
                                {canManage && (
                                    <label style={{ position: 'relative', display: 'inline-block', width: '40px', height: '22px', cursor: 'pointer', flexShrink: 0 }} title={t('agent.tools.enableDisableAll', 'Enable/Disable all {{category}} tools', { category: label })}>
                                        <input type="checkbox"
                                            checked={allEnabled}
                                            onChange={(e) => void bulkToggleCategory(allCatTools, e.target.checked)}
                                            style={{ opacity: 0, width: 0, height: 0 }} />
                                        <span style={switchTrack(allEnabled, mixed)}>
                                            <span style={switchKnob(allEnabled)} />
                                        </span>
                                    </label>
                                )}
                            </div>
                        </div>
                        {expanded && (
                            <div>
                                {(catTools as any[]).map((tool: any) => renderToolRow(tool, category))}
                            </div>
                        )}
                    </div>
                );
            });

    const activeTools = toolTab === 'company' ? companyTools : agentInstalledTools;
    const normalizedToolSearch = toolSearch.trim().toLowerCase();
    const matchesToolSearch = (tool: any) => {
        if (!normalizedToolSearch) return true;
        const category = tool.category || 'general';
        const haystack = [
            tool.name,
            tool.display_name,
            tool.description,
            tool.mcp_server_name,
            category,
            categoryLabels[category],
        ].filter(Boolean).join(' ').toLowerCase();
        return haystack.includes(normalizedToolSearch);
    };
    const matchesStatusFilter = (tool: any) => {
        if (toolStatusFilter === 'enabled') return !!tool.enabled;
        if (toolStatusFilter === 'disabled') return !tool.enabled;
        if (toolStatusFilter === 'configured') return !!(tool.agent_config && Object.keys(tool.agent_config).length > 0);
        return true;
    };
    const filteredTools = activeTools.filter(tool => matchesToolSearch(tool) && matchesStatusFilter(tool));
    const groupedActiveTools = groupByCategory(activeTools);
    const groupedFilteredTools = groupByCategory(filteredTools);
    const hasFilters = !!normalizedToolSearch || toolStatusFilter !== 'all';

    return (
        <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div className="tool-source-tabs" role="tablist" aria-label={t('agent.tools.sourceTabs', 'Tool sources')}>
                    <button
                        type="button"
                        role="tab"
                        aria-selected={toolTab === 'company'}
                        className={toolTab === 'company' ? 'active' : ''}
                        onClick={() => setToolTab('company')}
                    >
                        <span>{t('agent.tools.companyTools', 'Company Tools')}</span>
                        <span className="tool-source-tab-count">{companyTools.length}</span>
                    </button>
                    <button
                        type="button"
                        role="tab"
                        aria-selected={toolTab === 'installed'}
                        className={toolTab === 'installed' ? 'active' : ''}
                        onClick={() => setToolTab('installed')}
                    >
                        <span>{t('agent.tools.agentInstalled', 'Agent Self-Installed Tools')}</span>
                        <span className="tool-source-tab-count">{agentInstalledTools.length}</span>
                    </button>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                    <div style={{ position: 'relative', flex: '1 1 260px', minWidth: '220px' }}>
                        <IconSearch size={15} style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
                        <input
                            value={toolSearch}
                            onChange={(e) => setToolSearch(e.target.value)}
                            placeholder={t('agent.tools.searchTools', 'Search tools...')}
                            style={{
                                width: '100%',
                                boxSizing: 'border-box',
                                border: '1px solid var(--border-subtle)',
                                borderRadius: '8px',
                                background: 'var(--bg-primary)',
                                color: 'var(--text-primary)',
                                padding: '8px 10px 8px 32px',
                                fontSize: '13px',
                                outline: 'none',
                            }}
                        />
                    </div>
                    {(['all', 'enabled', 'disabled', 'configured'] as const).map(filter => (
                        <button
                            key={filter}
                            type="button"
                            onClick={() => setToolStatusFilter(filter)}
                            style={{
                                border: '1px solid var(--border-subtle)',
                                borderRadius: '999px',
                                background: toolStatusFilter === filter ? 'var(--text-primary)' : 'var(--bg-primary)',
                                color: toolStatusFilter === filter ? 'var(--bg-primary)' : 'var(--text-secondary)',
                                padding: '6px 10px',
                                fontSize: '11px',
                                cursor: 'pointer',
                            }}
                        >
                            {filter === 'all' ? t('common.all', 'All')
                                : filter === 'enabled' ? t('common.enabled', 'Enabled')
                                    : filter === 'disabled' ? t('common.disabled', 'Disabled')
                                        : t('agent.tools.configured', 'Configured')}
                        </button>
                    ))}
                    <button
                        type="button"
                        onClick={() => {
                            const categories = Object.keys(groupedActiveTools);
                            setExpandedCategories(prev => prev.size >= categories.length ? new Set() : new Set(categories));
                        }}
                        style={{
                            border: '1px solid var(--border-subtle)',
                            borderRadius: '8px',
                            background: 'var(--bg-primary)',
                            color: 'var(--text-secondary)',
                            padding: '6px 10px',
                            fontSize: '11px',
                            cursor: 'pointer',
                        }}
                    >
                        {expandedCategories.size >= Object.keys(groupedActiveTools).length ? t('agent.tools.collapseAll', 'Collapse all') : t('agent.tools.expandAll', 'Expand all')}
                    </button>
                </div>

                {/* Tool List */}
                {filteredTools.length > 0 ? (
                    renderToolGroup(groupedFilteredTools, groupedActiveTools)
                ) : (
                    <div className="card" style={{ textAlign: 'center', padding: '30px', color: 'var(--text-tertiary)' }}>
                        {hasFilters ? t('agent.tools.noMatchingTools', 'No matching tools') : toolTab === 'installed' ? t('agent.tools.noInstalled', 'No agent-installed tools yet') : t('agent.tools.noCompany', 'No company-configured tools')}
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
                                    <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}><IconSettings size={20} stroke={1.8} /> {title}</h3>
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
                                                if (data.ok) {
                                                    await dialog.alert(data.message || '测试成功', { type: 'success', title: '连通性测试' });
                                                } else {
                                                    await dialog.alert('测试失败', { type: 'error', title: '连通性测试', details: typeof data.error === 'string' ? data.error : JSON.stringify(data, null, 2) });
                                                }
                                            } catch (e: any) { await dialog.alert('测试失败', { type: 'error', title: '连通性测试', details: String(e?.message || e) }); }
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
    }).then(async r => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
            throw new Error(data?.detail || data?.message || `Request failed (${r.status})`);
        }
        return data;
    });
}

type AccessUser = {
    id: string;
    name: string;
    username?: string;
    email?: string;
    access_level: 'use' | 'manage';
    is_required?: boolean;
    required_reason?: 'creator' | 'company_admin' | string | null;
};
type AccessUserCandidate = { id: string; name: string; username?: string; email?: string };

function AccessPermissionsPanel({
    agentId,
    permData,
    canManage,
    queryClient,
}: {
    agentId: string;
    permData: any;
    canManage: boolean;
    queryClient: any;
}) {
    const { t, i18n } = useTranslation();
    const isChinese = i18n.language?.startsWith('zh');
    const canManagePermissions = permData?.can_manage ?? canManage;
    const isOwner = permData?.is_owner ?? false;
    const creatorId = permData?.creator_id ? String(permData.creator_id) : null;
    const currentScope = permData?.scope_type === 'user' ? 'private' : (permData?.scope_type || 'company');
    const currentAccessLevel = permData?.access_level || 'use';
    const [localScope, setLocalScope] = useState(currentScope);
    const [localAccessLevel, setLocalAccessLevel] = useState(currentAccessLevel);
    const [savingScope, setSavingScope] = useState<string | null>(null);
    const [permissionError, setPermissionError] = useState<string | null>(null);
    const [userSearch, setUserSearch] = useState('');
    const [showUserDropdown, setShowUserDropdown] = useState(false);
    const userSearchRef = useRef<HTMLDivElement | null>(null);
    const userAccess: AccessUser[] = (permData?.user_access || []).map((u: any) => ({
        id: u.id,
        name: u.name,
        username: u.username,
        email: u.email,
        access_level: u.access_level === 'manage' ? 'manage' : 'use',
        is_required: !!u.is_required,
        required_reason: u.required_reason || null,
    }));
    const toPermissionPayloadScope = (scope: string) => scope === 'private' ? 'user' : scope;

    const { data: candidates } = useQuery({
        queryKey: ['agent-permission-candidates', agentId, userSearch],
        queryFn: () => fetchAuth<{ users: AccessUserCandidate[]; agents: any[] }>(`/agents/${agentId}/permissions/candidates${userSearch.trim() ? `?search=${encodeURIComponent(userSearch.trim())}` : ''}`),
        enabled: !!agentId && canManagePermissions,
    });

    useEffect(() => {
        setLocalScope(currentScope);
        setLocalAccessLevel(currentAccessLevel);
    }, [currentScope, currentAccessLevel]);

    const savePermissions = async (payload: any) => {
        setPermissionError(null);
        await fetchAuth(`/agents/${agentId}/permissions`, {
            method: 'PUT',
            body: JSON.stringify(payload),
        });
        queryClient.invalidateQueries({ queryKey: ['agent-permissions', agentId] });
        queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
        queryClient.invalidateQueries({ queryKey: ['agents'] });
    };

    const scopeOptions = [
        {
            value: 'company',
            icon: <IconBuilding size={14} stroke={1.8} />,
            label: t('agent.settings.perm.companyWide', 'Company-wide'),
            desc: isChinese ? '所有平台用户和所有 Agent 都可以访问；可参与 Plaza。' : 'All platform users and all agents can access it; Plaza is enabled.',
        },
        {
            value: 'private',
            icon: <IconUser size={14} stroke={1.8} />,
            label: t('agent.settings.perm.onlyMe', 'Only Me'),
            desc: isChinese ? '只有创建者可以使用和管理；不可参与 Plaza。' : 'Only the creator can use and manage it; Plaza is disabled.',
        },
        {
            value: 'custom',
            icon: <IconLock size={14} stroke={1.8} />,
            label: isChinese ? '指定访问' : 'Custom',
            desc: isChinese ? '指定可访问的平台用户；不可参与 Plaza。Agent 关系请在“关系”里配置。' : 'Choose platform users explicitly; Plaza is disabled. Agent relationships are configured in Relationships.',
        },
    ] as const;

    const accessLevels = [
        { val: 'use', label: <><IconEye size={13} stroke={1.8} /> {t('agent.settings.perm.useAccess', 'Use')}</>, desc: t('agent.settings.perm.useAccessDesc', 'Task, Chat, Tools, Skills, Workspace') },
        { val: 'manage', label: <><IconSettings size={13} stroke={1.8} /> {t('agent.settings.perm.manageAccess', 'Manage')}</>, desc: t('agent.settings.perm.manageAccessDesc', 'Full access including Settings, Mind, Relationships') },
    ];

    const setScope = async (scope: string) => {
        if (!canManagePermissions) return;
        if (scope === 'private' && !isOwner) {
            setPermissionError(isChinese ? '仅创建者可以切换为“仅我可见”，否则管理员会立即失去管理入口。' : 'Only the creator can switch to Only Me, otherwise the manager would lose access immediately.');
            return;
        }
        const previousScope = localScope;
        setLocalScope(scope);
        setSavingScope(scope);
        try {
            await savePermissions({
                scope_type: toPermissionPayloadScope(scope),
                access_level: localAccessLevel,
                user_access: userAccess,
            });
        } catch (e) {
            setLocalScope(previousScope);
            setPermissionError(e instanceof Error ? e.message : String(e));
            console.error('Failed to update permissions', e);
        } finally {
            setSavingScope(null);
        }
    };

    const setCompanyAccessLevel = async (level: string) => {
        const previousLevel = localAccessLevel;
        setLocalAccessLevel(level);
        setSavingScope(`level:${level}`);
        try {
            await savePermissions({
                scope_type: toPermissionPayloadScope(localScope),
                access_level: level,
                user_access: userAccess,
            });
        } catch (e) {
            setLocalAccessLevel(previousLevel);
            setPermissionError(e instanceof Error ? e.message : String(e));
            console.error('Failed to update access level', e);
        } finally {
            setSavingScope(null);
        }
    };

    const addUser = (userId: string) => {
        const candidate = candidates?.users?.find(u => u.id === userId);
        if (!candidate || userAccess.some(u => u.id === userId)) return;
        savePermissions({
            scope_type: 'custom',
            access_level: currentAccessLevel,
            user_access: [...userAccess, { ...candidate, access_level: creatorId === userId ? 'manage' : 'use' }],
        }).catch(e => console.error('Failed to add user access', e));
    };

    const isLockedAccessUser = (user: AccessUser) => user.is_required || creatorId === user.id;
    const lockedAccessTitle = (user: AccessUser) => {
        if (user.required_reason === 'company_admin') {
            return isChinese ? '公司管理员会自动保留管理权限' : 'Company admins automatically keep manage access';
        }
        return isChinese ? '创建者始终保留管理权限' : 'The creator always keeps manage access';
    };

    const updateUserLevel = (userId: string, level: 'use' | 'manage') => {
        const target = userAccess.find(u => u.id === userId);
        if (target && isLockedAccessUser(target)) return;
        savePermissions({
            scope_type: 'custom',
            access_level: currentAccessLevel,
            user_access: userAccess.map(u => u.id === userId ? { ...u, access_level: level } : u),
        }).catch(e => console.error('Failed to update user access', e));
    };

    const removeUser = (userId: string) => {
        const target = userAccess.find(u => u.id === userId);
        if (target && isLockedAccessUser(target)) return;
        savePermissions({
            scope_type: 'custom',
            access_level: currentAccessLevel,
            user_access: userAccess.filter(u => u.id !== userId),
        }).catch(e => console.error('Failed to remove user access', e));
    };

    const toggleUser = (user: AccessUserCandidate) => {
        if (userAccess.some(existing => existing.id === user.id)) return;
        addUser(user.id);
    };

    const visibleUserResults = candidates?.users || [];

    return (
        <div className="card" style={{ marginBottom: '12px' }}>
            <h4 style={{ marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <IconLock size={16} stroke={1.8} /> {t('agent.settings.perm.title', 'Access Permissions')}
            </h4>
            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                {t('agent.settings.perm.description', 'Control who can see and interact with this agent. Only the creator or admin can change this.')}
            </p>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '16px' }}>
                {scopeOptions.map((scope) => {
                    const disabled = !canManagePermissions || (scope.value === 'private' && !isOwner);
                    const selected = localScope === scope.value;
                    return (
                    <button
                        key={scope.value}
                        type="button"
                        disabled={!canManagePermissions}
                        onClick={() => setScope(scope.value)}
                        style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '10px',
                            width: '100%',
                            textAlign: 'left',
                            padding: '12px 14px',
                            borderRadius: '8px',
                            cursor: disabled ? 'not-allowed' : 'pointer',
                            border: selected ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
                            background: selected ? 'rgba(99,102,241,0.06)' : 'transparent',
                            opacity: disabled ? 0.55 : 1,
                            transition: 'all 0.15s',
                        }}
                    >
                        <input
                            type="radio"
                            name="perm_scope"
                            checked={selected}
                            disabled={disabled}
                            readOnly
                            style={{ accentColor: 'var(--accent-primary)' }}
                        />
                        <div>
                            <div style={{ fontWeight: 500, fontSize: '13px', display: 'flex', alignItems: 'center', gap: '5px' }}>
                                {scope.icon} {scope.label}
                                {savingScope === scope.value && <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontWeight: 400 }}>{isChinese ? '保存中...' : 'Saving...'}</span>}
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{scope.desc}</div>
                        </div>
                    </button>
                );})}
            </div>

            {permissionError && (
                <div style={{ margin: '-4px 0 12px', fontSize: '12px', color: 'var(--error)' }}>
                    {permissionError}
                </div>
            )}

            {localScope === 'company' && canManagePermissions && (
                <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px' }}>
                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '8px' }}>
                        {t('agent.settings.perm.defaultAccess', 'Default Access Level')}
                    </label>
                    <div style={{ display: 'flex', gap: '8px' }}>
                        {accessLevels.map(opt => (
                            <label key={opt.val}
                                style={{
                                    flex: 1,
                                    padding: '10px 12px',
                                    borderRadius: '8px',
                                    cursor: 'pointer',
                                    border: localAccessLevel === opt.val ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
                                    background: localAccessLevel === opt.val ? 'rgba(99,102,241,0.06)' : 'transparent',
                                    transition: 'all 0.15s',
                                }}
                            >
                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                    <input
                                        type="radio"
                                        name="access_level"
                                        checked={localAccessLevel === opt.val}
                                        onChange={() => setCompanyAccessLevel(opt.val)}
                                        style={{ accentColor: 'var(--accent-primary)' }}
                                    />
                                    <span style={{ fontWeight: 500, fontSize: '13px', display: 'inline-flex', alignItems: 'center', gap: '5px' }}>{opt.label}</span>
                                </div>
                                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '4px', marginLeft: '20px' }}>{opt.desc}</div>
                            </label>
                        ))}
                    </div>
                </div>
            )}

            {localScope === 'custom' && canManagePermissions && (
                <div
                    style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px' }}
                    onMouseDownCapture={(e) => {
                        const target = e.target as Node;
                        if (userSearchRef.current && !userSearchRef.current.contains(target)) {
                            setShowUserDropdown(false);
                        }
                    }}
                >
                    <div>
                        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <IconUser size={14} stroke={1.8} /> {isChinese ? '平台用户' : 'Platform Users'}
                        </div>
                        <div ref={userSearchRef} style={{ position: 'relative', marginBottom: '8px', maxWidth: '520px' }}>
                            <input
                                className="input"
                                value={userSearch}
                                onChange={(e) => {
                                    setUserSearch(e.target.value);
                                    setShowUserDropdown(true);
                                }}
                                onFocus={() => setShowUserDropdown(true)}
                                placeholder={isChinese ? '搜索用户姓名或邮箱...' : 'Search users by name or email...'}
                                style={{ fontSize: '12px', width: '100%' }}
                            />
                            {showUserDropdown && visibleUserResults.length > 0 && (
                                <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)', borderRadius: '6px', marginTop: '4px', maxHeight: '220px', overflowY: 'auto', zIndex: 20, boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}>
                                    {visibleUserResults.map(u => {
                                        const checked = userAccess.some(existing => existing.id === u.id);
                                        const existingUser = userAccess.find(existing => existing.id === u.id);
                                        return (
                                            <div
                                                key={u.id}
                                                style={{ padding: '8px 12px', cursor: checked ? 'default' : 'pointer', fontSize: '13px', borderBottom: '1px solid var(--border-subtle)', display: 'flex', alignItems: 'flex-start', gap: '8px', opacity: checked ? 0.72 : 1 }}
                                                onClick={() => toggleUser(u)}
                                                onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-elevated)')}
                                                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                                            >
                                                <input type="checkbox" checked={checked} readOnly disabled={checked} style={{ marginTop: '2px' }} />
                                                <div style={{ minWidth: 0, flex: 1 }}>
                                                    <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'flex', gap: '6px', alignItems: 'center' }}>
                                                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{u.name}</span>
                                                        {checked && (
                                                            <span className="badge" style={{ fontSize: '10px', flexShrink: 0 }}>
                                                                {existingUser?.is_required
                                                                    ? (existingUser.required_reason === 'company_admin' ? (isChinese ? '管理员' : 'Admin') : (isChinese ? '创建者' : 'Creator'))
                                                                    : (isChinese ? '已添加' : 'Added')}
                                                            </span>
                                                        )}
                                                    </div>
                                                    {(u.email || u.username) && (
                                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                            {[u.username, u.email].filter(Boolean).join(' · ')}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                            {showUserDropdown && userSearch.trim() && visibleUserResults.length === 0 && (
                                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '6px' }}>
                                    {t('agent.detail.noSearchResults', 'No available results')}
                                </div>
                            )}
                        </div>
                        {userAccess.length > 0 && (
                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>
                                {isChinese ? '已授权用户' : 'Granted users'}
                            </div>
                        )}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                            {userAccess.map(u => {
                                const isCreatorUser = creatorId === u.id;
                                const lockedUser = isLockedAccessUser(u);
                                return (
                                <div key={u.id} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px', border: '1px solid var(--border-subtle)', borderRadius: '8px' }}>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ fontSize: '12px', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {u.name}
                                            {isCreatorUser && (
                                                <span className="badge" style={{ fontSize: '10px', marginLeft: '6px' }}>
                                                    {isChinese ? '创建者' : 'Creator'}
                                                </span>
                                            )}
                                            {!isCreatorUser && u.required_reason === 'company_admin' && (
                                                <span className="badge" style={{ fontSize: '10px', marginLeft: '6px' }}>
                                                    {isChinese ? '管理员' : 'Admin'}
                                                </span>
                                            )}
                                        </div>
                                        {u.email && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{u.email}</div>}
                                    </div>
                                    <select
                                        className="input"
                                        value={lockedUser ? 'manage' : u.access_level}
                                        disabled={lockedUser}
                                        onChange={(e) => updateUserLevel(u.id, e.target.value as 'use' | 'manage')}
                                        style={{ width: '92px', fontSize: '12px', opacity: lockedUser ? 0.65 : 1, cursor: lockedUser ? 'not-allowed' : 'pointer' }}
                                        title={lockedUser ? lockedAccessTitle(u) : undefined}
                                    >
                                        <option value="use">{t('agent.settings.perm.useAccess', 'Use')}</option>
                                        <option value="manage">{t('agent.settings.perm.manageAccess', 'Manage')}</option>
                                    </select>
                                    <button
                                        className="btn btn-ghost btn-sm"
                                        disabled={lockedUser}
                                        onClick={() => removeUser(u.id)}
                                        title={lockedUser ? lockedAccessTitle(u) : undefined}
                                        style={{ opacity: lockedUser ? 0.35 : 1, cursor: lockedUser ? 'not-allowed' : 'pointer' }}
                                    >
                                        ×
                                    </button>
                                </div>
                            );})}
                            {userAccess.length === 0 && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{isChinese ? '尚未指定用户。创建者会自动保留管理权限。' : 'No users selected. The creator keeps manage access automatically.'}</div>}
                        </div>
                    </div>
                </div>
            )}

            {localScope !== 'company' && (
                <div style={{ marginTop: '12px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                    {isChinese ? '非全公司可见的 Agent 不会出现在 Plaza，也不能在 Plaza 发布或评论。' : 'Agents that are not company-wide cannot view, post, or comment in Plaza.'}
                </div>
            )}

            {!canManagePermissions && (
                <div style={{ marginTop: '12px', fontSize: '11px', color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
                    {t('agent.settings.perm.readOnly', 'Only the creator or admin can change permissions')}
                </div>
            )}
        </div>
    );
}

// ── Pulse LED keyframe (shared with Chat.tsx, guarded by ID) ──────────────
const _PULSE_STYLE_ID = 'cw-tool-pulse-style';
if (typeof document !== 'undefined' && !document.getElementById(_PULSE_STYLE_ID)) {
    const _s = document.createElement('style');
    _s.id = _PULSE_STYLE_ID;
    _s.textContent = `
        @keyframes cw-pulse-led {
            0%, 100% { opacity: 1; transform: scale(1); box-shadow: 0 0 0 0 rgba(107,114,128,0.45); }
            50%       { opacity: 0.55; transform: scale(1.5); box-shadow: 0 0 0 4px rgba(107,114,128,0); }
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

type AnalysisToolMeta = {
    title: string;
    label: string;
    target?: string;
    kind: 'command' | 'file' | 'search' | 'browser' | 'message' | 'agent' | 'mcp' | 'unknown';
};

function getToolProvider(name: string): string {
    const lower = (name || '').toLowerCase();
    if (lower.startsWith('agentbay_')) return 'AgentBay';
    if (lower.includes('tavily')) return 'Tavily';
    if (lower.includes('jina')) return 'Jina';
    if (lower.includes('duckduckgo')) return 'DuckDuckGo';
    if (lower.includes('exa')) return 'Exa';
    if (lower.includes('google')) return 'Google';
    if (lower.includes('bing')) return 'Bing';
    if (lower.includes('e2b')) return 'E2B';
    if (lower.startsWith('feishu_') || lower.includes('lark')) return 'Feishu';
    if (lower.startsWith('mcp_') || lower.includes(':')) return 'MCP';
    if (lower.includes('web_search') || lower.includes('read_webpage')) return 'Built-in';
    return 'Built-in';
}

function titleCaseToolName(name: string): string {
    return (name || 'tool')
        .replace(/^mcp[_:-]/i, '')
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .replace(/\b\w/g, ch => ch.toUpperCase());
}

function basename(path?: string): string {
    if (!path) return '';
    const clean = String(path).split('?')[0].replace(/\\/g, '/');
    return clean.split('/').filter(Boolean).pop() || clean;
}

function firstString(...values: any[]): string | undefined {
    for (const value of values) {
        if (typeof value === 'string' && value.trim()) return value.trim();
    }
    return undefined;
}

function getToolMeta(item: Extract<AnalysisItem, { type: 'tool' }>): AnalysisToolMeta {
    const name = item.name || 'tool';
    const args = item.args && typeof item.args === 'object' && !Array.isArray(item.args) ? item.args : {};
    const resultText = typeof item.result === 'string' ? item.result : '';
    const path = firstString(args.output_path, args.path, args.file_path, args.filename, args.name);
    const url = firstString(args.url, args.link, args.uri);
    const query = firstString(args.query, args.q, args.keyword, args.search);
    const recipient = firstString(args.to, args.recipient, args.user, args.channel, args.agent_name);
    const target = path || url || query || recipient;
    const lower = name.toLowerCase();

    if (lower.includes('write_file') || lower.includes('create_file')) {
        return { title: path ? `Created ${basename(path)}` : 'Created a file', label: 'Workspace', target: path, kind: 'file' };
    }
    if (lower.includes('edit_file') || lower.includes('update_file')) {
        return { title: path ? `Updated ${basename(path)}` : 'Updated a file', label: 'Workspace', target: path, kind: 'file' };
    }
    if (lower.includes('move_file')) {
        const destinationPath = firstString(args.destination_path, args.to_path, args.target_path);
        const sourcePath = firstString(args.source_path, args.from_path, args.path);
        const titlePath = destinationPath || sourcePath;
        return { title: titlePath ? `Moved ${basename(titlePath)}` : 'Moved a file', label: 'Workspace', target: titlePath, kind: 'file' };
    }
    if (lower.includes('delete_file')) {
        return { title: path ? `Deleted ${basename(path)}` : 'Deleted a file', label: 'Workspace', target: path, kind: 'file' };
    }
    if (lower.startsWith('convert_') || lower.includes('convert_')) {
        return { title: path ? `Converted ${basename(path)}` : titleCaseToolName(name), label: 'Workspace', target: path, kind: 'file' };
    }
    if (lower.includes('read_webpage') || lower.includes('browser') || lower.includes('webpage')) {
        return { title: url ? `Read ${url.replace(/^https?:\/\//, '').split('/')[0]}` : titleCaseToolName(name), label: 'Browser', target: url, kind: 'browser' };
    }
    if (lower.includes('search')) {
        return { title: query ? `Searched ${query}` : titleCaseToolName(name), label: 'Search', target: query, kind: 'search' };
    }
    if (lower.includes('send_') || lower.includes('message')) {
        return { title: recipient ? `Sent message to ${recipient}` : titleCaseToolName(name), label: 'Message', target: recipient, kind: 'message' };
    }
    if (lower.includes('agent')) {
        return { title: titleCaseToolName(name), label: 'Agent', target, kind: 'agent' };
    }
    if (lower.includes('mcp') || lower.includes(':')) {
        return { title: titleCaseToolName(name), label: 'MCP', target, kind: 'mcp' };
    }
    if (/created|saved|updated|wrote/i.test(resultText) && path) {
        return { title: `Updated ${basename(path)}`, label: 'Workspace', target: path, kind: 'file' };
    }
    return { title: titleCaseToolName(name), label: 'Tool', target, kind: 'command' };
}

function getToolIcon(kind: AnalysisToolMeta['kind']) {
    switch (kind) {
        case 'file': return IconFileText;
        case 'search': return IconSearch;
        case 'browser': return IconBrowser;
        case 'message': return IconMessageCircle;
        case 'agent': return IconBrain;
        case 'mcp': return IconTools;
        case 'command':
        case 'unknown':
        default:
            return IconTerminal2;
    }
}

function describeAnalysis(items: AnalysisItem[], t: (k: string, opts?: any) => string): string {
    const toolItems = items.filter(i => i.type === 'tool') as Extract<AnalysisItem, { type: 'tool' }>[];
    if (toolItems.length === 0) return t('agent.chat.thoughtProcess');

    let created = 0;
    let updated = 0;
    let deleted = 0;
    let commands = 0;
    let agents = 0;
    const agentMessageTools = new Set([
        'send_message_to_agent',
        'send_file_to_agent',
    ]);
    for (const item of toolItems) {
        const name = item.name.toLowerCase();
        if (name.includes('write_file') || name.includes('create_file')) created += 1;
        else if (name.includes('edit_file') || name.includes('update_file') || name.includes('move_file') || name.startsWith('convert_')) updated += 1;
        else if (name.includes('delete_file')) deleted += 1;
        else if (agentMessageTools.has(name)) agents += 1;
        else commands += 1;
    }

    const parts: string[] = [];
    if (created) parts.push(t('agent.chat.createdFiles', { count: created }));
    if (updated) parts.push(t('agent.chat.updatedFiles', { count: updated }));
    if (deleted) parts.push(t('agent.chat.deletedFiles', { count: deleted }));
    if (commands) parts.push(t('agent.chat.ranCommands', { count: commands }));
    if (agents) parts.push(t('agent.chat.ranAgents', { count: agents }));
    if (!parts.length) parts.push(t('agent.chat.ranCommands', { count: toolItems.length }));
    return parts.join(', ');
}

function AnalysisCard({
    items, t, expanded, onToggle, isGroupRunning,
}: {
    items: AnalysisItem[];
    t: (k: string, opts?: any) => string;
    expanded: boolean;
    onToggle: () => void;
    /** True when parent isWaiting/isStreaming AND this is the last active group */
    isGroupRunning: boolean;
}) {
    const toolItems = items.filter(i => i.type === 'tool') as Extract<AnalysisItem, { type: 'tool' }>[];
    const hasTools = toolItems.length > 0;
    const hasRunningTool = toolItems.some(tc => tc.status === 'running');
    const isRunning = hasRunningTool || (!hasTools && isGroupRunning);
    const runningTool = [...toolItems].reverse().find(tc => tc.status === 'running') ?? null;
    const headerTitle = isRunning && runningTool ? getToolMeta(runningTool).title : describeAnalysis(items, t);

    return (
        <div className={`analysis-trace${expanded ? ' analysis-trace--open' : ''}${isRunning ? ' analysis-trace--running' : ''}`}>
            <div className="analysis-trace-shell">
                <button
                    className="analysis-trace-header"
                    onClick={onToggle}
                >
                    <span className="analysis-trace-signal" aria-hidden="true">
                        <span />
                        <span />
                        <span />
                    </span>
                    <span className="analysis-trace-title">
                        {headerTitle}
                    </span>
                    <IconChevronDown
                        className="analysis-trace-chevron"
                        size={15}
                        stroke={1.8}
                    />
                </button>
                {expanded && (
                    <div className="analysis-trace-body">
                        {items.map((item, idx) => {
                            const isLast = idx === items.length - 1;
                            if (item.type === 'thinking') {
                                const itemPreview = item.content.length > 360 ? item.content.slice(0, 360).trimEnd() + '...' : item.content;
                                return (
                                    <div key={idx} className="analysis-trace-row">
                                        <div className="analysis-trace-node-wrap">
                                            <div className="analysis-trace-node analysis-trace-node--thought">
                                                <IconClock size={18} stroke={1.65} />
                                            </div>
                                            {!isLast && <div className="analysis-trace-rail" />}
                                        </div>
                                        <div className="analysis-trace-row-content" style={{ paddingBottom: isLast ? 0 : '18px' }}>
                                            <div style={{ fontSize: '13px', lineHeight: 1.5, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                                                {itemPreview}
                                            </div>
                                            {item.content.length > itemPreview.length && (
                                                <details style={{ marginTop: '8px' }}>
                                                    <summary style={{ cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: '12px', listStyle: 'none' }}>
                                                        {t('agent.chat.showMore')}
                                                    </summary>
                                                    <div style={{ marginTop: '8px', color: 'var(--text-secondary)', fontSize: '13px', lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                                                        {item.content}
                                                    </div>
                                                </details>
                                            )}
                                        </div>
                                    </div>
                                );
                            }

                            const tc = item;
                            const running = tc.status === 'running';
                            const meta = getToolMeta(tc);
                            const ToolIcon = getToolIcon(meta.kind);
                            const provider = getToolProvider(tc.name);
                            const argsStr = tc.args && Object.keys(tc.args).length > 0
                                ? JSON.stringify(tc.args, null, 2) : '';
                            const hasDetail = true;
                            return (
                                <div key={idx} className={`analysis-trace-row${running ? ' analysis-trace-row--running' : ''}`}>
                                    <div className="analysis-trace-node-wrap">
                                        <div
                                            className={`analysis-trace-node analysis-trace-node--tool analysis-tool-icon${running ? ' analysis-tool-icon--running' : ''}`}
                                        >
                                            <ToolIcon size={18} stroke={1.65} />
                                        </div>
                                        {!isLast && <div className="analysis-trace-rail" />}
                                    </div>
                                    <div className="analysis-trace-row-content" style={{ paddingBottom: isLast ? 0 : '18px' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                                            <div style={{
                                                minWidth: 0,
                                                overflow: 'hidden',
                                                textOverflow: 'ellipsis',
                                                whiteSpace: 'nowrap',
                                                color: running ? 'var(--text-secondary)' : 'var(--text-tertiary)',
                                                fontSize: '13px',
                                                lineHeight: 1.5,
                                            }}>
                                                {meta.title}
                                            </div>
                                            {running && (
                                                <span style={{ color: 'var(--text-tertiary)', fontSize: '12px', flexShrink: 0 }}>
                                                    {t('common.loading')}
                                                </span>
                                            )}
                                        </div>
                                        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px', marginTop: '8px' }}>
                                            <span style={{
                                                display: 'inline-flex',
                                                alignItems: 'center',
                                                height: '24px',
                                                padding: '0 10px',
                                                borderRadius: '7px',
                                                background: 'color-mix(in srgb, var(--bg-secondary) 72%, var(--bg-primary))',
                                                color: 'var(--text-tertiary)',
                                                fontSize: '12px',
                                                lineHeight: 1,
                                            }}>
                                                {meta.label}
                                            </span>
                                            {meta.target && (
                                                <span style={{
                                                    display: 'inline-flex',
                                                    alignItems: 'center',
                                                    maxWidth: 'min(520px, 100%)',
                                                    height: '24px',
                                                    padding: '0 10px',
                                                    borderRadius: '7px',
                                                    background: 'var(--bg-secondary)',
                                                    color: 'var(--text-secondary)',
                                                    fontSize: '12px',
                                                    lineHeight: 1,
                                                    overflow: 'hidden',
                                                    textOverflow: 'ellipsis',
                                                    whiteSpace: 'nowrap',
                                                }}>
                                                    {meta.target}
                                                </span>
                                            )}
                                        </div>
                                    {hasDetail && (
                                        <details style={{ marginTop: '8px' }}>
                                            <summary style={{
                                                cursor: 'pointer',
                                                color: 'var(--text-tertiary)',
                                                fontSize: '12px',
                                                listStyle: 'none',
                                                userSelect: 'none',
                                            }}>
                                                {t('agent.chat.viewDetails')}
                                            </summary>
                                            <div style={{ marginTop: '8px' }}>
                                            <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px', marginBottom: '8px' }}>
                                                <span style={{
                                                    display: 'inline-flex',
                                                    alignItems: 'center',
                                                    height: '22px',
                                                    padding: '0 8px',
                                                    borderRadius: '6px',
                                                    background: 'var(--bg-secondary)',
                                                    color: 'var(--text-tertiary)',
                                                    fontSize: '11px',
                                                    lineHeight: 1,
                                                }}>
                                                    {t('agent.chat.provider', 'Provider')}: {provider}
                                                </span>
                                                <span style={{
                                                    display: 'inline-flex',
                                                    alignItems: 'center',
                                                    maxWidth: '100%',
                                                    height: '22px',
                                                    padding: '0 8px',
                                                    borderRadius: '6px',
                                                    background: 'var(--bg-secondary)',
                                                    color: 'var(--text-secondary)',
                                                    fontFamily: 'var(--font-mono)',
                                                    fontSize: '11px',
                                                    lineHeight: 1,
                                                    overflow: 'hidden',
                                                    textOverflow: 'ellipsis',
                                                    whiteSpace: 'nowrap',
                                                }}>
                                                    {t('agent.chat.toolName', 'Tool')}: {tc.name || 'tool'}
                                                </span>
                                            </div>
                                            {argsStr && (
                                                <div style={{
                                                    fontFamily: 'var(--font-mono)', fontSize: '10px',
                                                    color: 'var(--text-tertiary)', whiteSpace: 'pre-wrap',
                                                    wordBreak: 'break-all', maxHeight: '80px', overflowY: 'auto',
                                                    background: 'var(--bg-secondary)', borderRadius: '4px',
                                                    padding: '4px 6px', marginBottom: tc.result ? '4px' : 0,
                                                }}>{argsStr}</div>
                                            )}
                                            {tc.result && (
                                                <div style={{
                                                    fontSize: '10px', color: 'var(--text-secondary)',
                                                    whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                                                    maxHeight: '120px', overflowY: 'auto',
                                                    borderTop: argsStr ? '1px solid var(--border-subtle)' : 'none',
                                                    paddingTop: argsStr ? '4px' : 0,
                                                }}>
                                                    {tc.result.length > 500 ? tc.result.slice(0, 500) + '…' : tc.result}
                                                </div>
                                            )}
                                            </div>
                                        </details>
                                    )}
                                    </div>
                                </div>
                            );
                        })}
                        {isRunning && (
                            <div className="analysis-trace-row analysis-trace-row--done">
                                <div className="analysis-trace-node-wrap">
                                    <div className="analysis-trace-node analysis-trace-node--done analysis-trace-node--pending">
                                    <IconClock size={18} stroke={1.65} />
                                    </div>
                                </div>
                                <div style={{ color: 'var(--text-tertiary)', fontSize: '13px', lineHeight: 1.5 }}>
                                    {t('agent.chat.inProgress')}
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

function ThoughtDisclosure({
    content,
    t,
    streaming = false,
}: {
    content: string;
    t: (k: string, opts?: any) => string;
    streaming?: boolean;
}) {
    const [expanded, setExpanded] = React.useState(false);
    const text = content.trim();
    if (!text) return null;

    return (
        <details
            className={`thought-disclosure analysis-trace thought-trace${streaming ? ' analysis-trace--running' : ''}`}
            open={expanded}
            onToggle={(event) => setExpanded(event.currentTarget.open)}
        >
            <summary className="analysis-trace-shell analysis-trace-header thought-trace-header">
                <span className="analysis-trace-signal thought-trace-signal" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                </span>
                <span className="analysis-trace-title">
                    {streaming ? t('agent.chat.thinkingLabel') : t('agent.chat.thoughtProcess')}
                </span>
                <IconChevronDown
                    className="thought-disclosure-chevron analysis-trace-chevron"
                    size={14}
                    stroke={1.8}
                />
            </summary>
            <div className="analysis-trace-body thought-trace-body">
                <div className="analysis-trace-row">
                    <div className="analysis-trace-node-wrap">
                        <div
                            className={`analysis-trace-node analysis-trace-node--thought${streaming ? ' cw-running-led' : ''}`}
                        >
                            <IconClock size={18} stroke={1.65} />
                        </div>
                        <div className="analysis-trace-rail" />
                    </div>
                    <div style={{
                        paddingBottom: '14px',
                        color: 'var(--text-secondary)',
                        fontSize: '13px',
                        lineHeight: 1.5,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        maxHeight: '260px',
                        overflow: 'auto',
                        minWidth: 0,
                    }}>
                        {text}
                    </div>
                </div>
                {streaming && (
                    <div className="analysis-trace-row analysis-trace-row--done">
                        <div className="analysis-trace-node-wrap">
                            <div className="analysis-trace-node analysis-trace-node--done analysis-trace-node--pending">
                            <IconClock size={18} stroke={1.65} />
                            </div>
                        </div>
                        <div style={{ color: 'var(--text-tertiary)', fontSize: '13px', lineHeight: 1.5 }}>
                            {t('agent.chat.inProgress')}
                        </div>
                    </div>
                )}
            </div>
        </details>
    );
}








function RelationshipEditor({ agentId, readOnly = false }: { agentId: string; readOnly?: boolean }) {
    const { t, i18n } = useTranslation();
    const isChinese = i18n.language?.startsWith('zh');
    const humanSearchRef = useRef<HTMLDivElement>(null);
    const agentSearchRef = useRef<HTMLDivElement>(null);
    const getHumanMemberSourceLabel = useCallback((member: any) => {
        const providerName = (member?.provider_name || '').trim();
        const providerType = (member?.provider_type || '').trim().toLowerCase();
        if (!providerName || providerType === 'platform' || providerType === 'web' || providerName.toLowerCase() === 'web') {
            return isChinese ? '平台用户' : 'Platform User';
        }
        return providerName;
    }, [isChinese]);

    const renderHumanMemberSourceBadge = useCallback((member: any) => {
        const providerName = (member?.provider_name || '').trim();
        const providerType = (member?.provider_type || '').trim().toLowerCase();
        const isPlatformUser = !providerName || providerType === 'platform' || providerType === 'web' || providerName.toLowerCase() === 'web';
        const showPlatformBadge = Boolean(member?.is_platform_user) && !isPlatformUser;
        const badgeStyle = (platform: boolean): React.CSSProperties => ({
            display: 'inline-flex',
            alignItems: 'center',
            padding: '1px 6px',
            borderRadius: '999px',
            fontSize: '10px',
            fontWeight: 600,
            marginRight: '6px',
            background: platform ? 'rgba(99,102,241,0.10)' : 'rgba(16,185,129,0.10)',
            color: platform ? 'rgb(79,70,229)' : 'rgb(16,185,129)',
            border: platform ? '1px solid rgba(99,102,241,0.18)' : '1px solid rgba(16,185,129,0.18)',
        });
        return (
            <>
                <span style={badgeStyle(isPlatformUser)}>
                    {getHumanMemberSourceLabel(member)}
                </span>
                {showPlatformBadge && (
                    <span style={badgeStyle(true)}>
                        {isChinese ? '平台用户' : 'Platform User'}
                    </span>
                )}
            </>
        );
    }, [getHumanMemberSourceLabel, isChinese]);

    const getRestrictedTitle = useCallback((reason?: string | null) => {
        const reasonText = reason ? ` (${reason})` : '';
        return isChinese
            ? `当关系目标不存在、停用/过期，或当前访问权限不再允许这个 Agent 与该用户/Agent 互动时，会显示为 restricted。关系记录会保留，但运行时不会使用。${reasonText}`
            : `Restricted means the target is missing, inactive/expired, or current access permissions no longer allow this agent to interact with that user/agent. The record is kept, but runtime use is blocked.${reasonText}`;
    }, [isChinese]);

    const [restrictedTooltip, setRestrictedTooltip] = useState<{ text: string; x: number; y: number } | null>(null);
    const showRestrictedTooltip = useCallback((event: React.SyntheticEvent<HTMLElement>, reason?: string | null) => {
        const rect = event.currentTarget.getBoundingClientRect();
        const tooltipWidth = Math.min(320, Math.max(220, window.innerWidth - 32));
        const x = Math.min(
            Math.max(rect.left + rect.width / 2, 16 + tooltipWidth / 2),
            window.innerWidth - 16 - tooltipWidth / 2,
        );
        setRestrictedTooltip({
            text: getRestrictedTitle(reason),
            x,
            y: rect.top - 8,
        });
    }, [getRestrictedTitle]);
    const hideRestrictedTooltip = useCallback(() => setRestrictedTooltip(null), []);

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
    const relatedMemberById = useMemo(() => {
        const map = new Map<string, any>();
        relationships.forEach((r: any) => {
            if (r.member_id) map.set(r.member_id, r);
        });
        return map;
    }, [relationships]);

    const visibleMemberResults = useMemo(
        () => searchResults,
        [searchResults],
    );
    const visibleAgentResults = useMemo(
        () => agentSearchResults.filter((a: any) => !relatedAgentIds.has(a.id)),
        [agentSearchResults, relatedAgentIds],
    );

    const loadOrgMembers = async (keyword = '') => {
        const query = keyword.trim() ? `?search=${encodeURIComponent(keyword.trim())}` : '';
        const results = await fetchAuth<any[]>(`/agents/${agentId}/relationships/member-candidates${query}`);
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
            {restrictedTooltip && (
                <div
                    style={{
                        position: 'fixed',
                        left: restrictedTooltip.x,
                        top: restrictedTooltip.y,
                        transform: 'translate(-50%, -100%)',
                        zIndex: 10000,
                        width: 'max-content',
                        maxWidth: 'min(320px, calc(100vw - 32px))',
                        padding: '8px 10px',
                        borderRadius: '8px',
                        border: '1px solid var(--border-subtle)',
                        background: 'var(--bg-primary)',
                        color: 'var(--text-primary)',
                        boxShadow: '0 10px 30px rgba(0,0,0,0.16)',
                        fontSize: '12px',
                        lineHeight: 1.45,
                        whiteSpace: 'normal',
                        overflowWrap: 'anywhere',
                        wordBreak: 'break-word',
                        pointerEvents: 'none',
                    }}
                >
                    {restrictedTooltip.text}
                </div>
            )}
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
                                        <div style={{ fontWeight: 600, fontSize: '13px' }}>
                                            {r.member?.name || '?'} <span className="badge" style={{ fontSize: '10px', marginLeft: '4px' }}>{r.relation_label}</span>
                                            {r.access_status && r.access_status !== 'active' && (
                                                <span
                                                    className="badge"
                                                    onMouseEnter={(event) => showRestrictedTooltip(event, r.access_status_reason)}
                                                    onMouseLeave={hideRestrictedTooltip}
                                                    onFocus={(event) => showRestrictedTooltip(event, r.access_status_reason)}
                                                    onBlur={hideRestrictedTooltip}
                                                    tabIndex={0}
                                                    style={{ fontSize: '10px', marginLeft: '4px', color: 'var(--warning)', background: 'rgba(245,158,11,0.12)', cursor: 'help' }}
                                                >
                                                    {r.access_status}
                                                </span>
                                            )}
                                        </div>
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
                                        const existingRelationship = relatedMemberById.get(m.id);
                                        const alreadyAdded = Boolean(existingRelationship);
                                        const checked = alreadyAdded || selectedMemberIds.has(m.id);
                                        return (
                                            <div
                                                key={m.id}
                                                style={{
                                                    padding: '8px 12px',
                                                    cursor: alreadyAdded ? 'default' : 'pointer',
                                                    fontSize: '13px',
                                                    borderBottom: '1px solid var(--border-subtle)',
                                                    display: 'flex',
                                                    alignItems: 'flex-start',
                                                    gap: '8px',
                                                    opacity: alreadyAdded ? 0.72 : 1,
                                                }}
                                                onClick={() => {
                                                    if (!alreadyAdded) toggleMemberSelection(m);
                                                }}
                                                onMouseEnter={e => (e.currentTarget.style.background = alreadyAdded ? 'transparent' : 'var(--bg-elevated)')}
                                                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                                                <input type="checkbox" checked={checked} disabled={alreadyAdded} readOnly style={{ marginTop: '2px' }} />
                                                <div style={{ minWidth: 0, flex: 1 }}>
                                                    <div style={{ fontWeight: 500 }}>
                                                        {m.name}
                                                        {alreadyAdded && (
                                                            <span className="badge" style={{ fontSize: '10px', marginLeft: '6px', color: 'var(--text-tertiary)', background: 'var(--bg-elevated)' }}>
                                                                {isChinese ? '已添加' : 'Added'}
                                                            </span>
                                                        )}
                                                        {alreadyAdded && existingRelationship?.relation_label && (
                                                            <span className="badge" style={{ fontSize: '10px', marginLeft: '4px' }}>
                                                                {existingRelationship.relation_label}
                                                            </span>
                                                        )}
                                                    </div>
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
                                borderRadius: '8px',
                                border: `1px solid ${r.access_status && r.access_status !== 'active' ? 'rgba(245,158,11,0.35)' : 'rgba(16,185,129,0.3)'}`,
                                background: r.access_status && r.access_status !== 'active' ? 'rgba(245,158,11,0.06)' : 'rgba(16,185,129,0.05)', overflow: 'hidden',
                                opacity: deletingIds.has(r.id) ? 0.4 : 1,
                                transition: 'opacity 0.2s ease',
                                pointerEvents: deletingIds.has(r.id) ? 'none' : 'auto',
                            }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px' }}>
                                    <div style={{ width: '36px', height: '36px', borderRadius: '50%', background: 'rgba(16,185,129,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', flexShrink: 0 }}>A</div>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{ fontWeight: 600, fontSize: '13px' }}>
                                            {r.target_agent?.name || '?'} <span className="badge" style={{ fontSize: '10px', marginLeft: '4px', background: 'rgba(16,185,129,0.15)', color: 'rgb(16,185,129)' }}>{r.relation_label}</span>
                                            {r.access_status && r.access_status !== 'active' && (
                                                <span
                                                    className="badge"
                                                    onMouseEnter={(event) => showRestrictedTooltip(event, r.access_status_reason)}
                                                    onMouseLeave={hideRestrictedTooltip}
                                                    onFocus={(event) => showRestrictedTooltip(event, r.access_status_reason)}
                                                    onBlur={hideRestrictedTooltip}
                                                    tabIndex={0}
                                                    style={{ fontSize: '10px', marginLeft: '4px', color: 'var(--warning)', background: 'rgba(245,158,11,0.12)', cursor: 'help' }}
                                                >
                                                    {r.access_status}
                                                </span>
                                            )}
                                        </div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                            {r.target_agent?.role_description || 'Agent'}
                                            {r.access_status_reason ? ` · ${r.access_status_reason}` : ''}
                                        </div>
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
    const dialog = useDialog();
    const toast = useToast();
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const location = useLocation();
    const validTabs = ['status', 'aware', 'mind', 'tools', 'skills', 'relationships', 'workspace', 'chat', 'activityLog', 'approvals', 'settings'];
    const settingsTabs = validTabs.filter(tab => !['aware', 'workspace', 'chat'].includes(tab));
    const isSettingsRoute = location.pathname.endsWith('/settings');
    const isChatRoute = !isSettingsRoute;
    const hashTab = location.hash?.replace('#', '');
    const [activeTab, setActiveTabRaw] = useState<string>(
        isSettingsRoute && hashTab && settingsTabs.includes(hashTab) ? hashTab : (isSettingsRoute ? 'status' : 'chat')
    );

    const setActiveTab = (tab: string) => {
        if (tab === 'chat') {
            setActiveTabRaw('chat');
            if (id) navigate(`/agents/${id}/chat`);
            return;
        }
        const nextTab = settingsTabs.includes(tab) ? tab : 'status';
        setActiveTabRaw(nextTab);
        if (id && !location.pathname.endsWith('/settings')) {
            navigate(`/agents/${id}/settings#${nextTab}`);
        } else {
            window.history.replaceState(null, '', `#${nextTab}`);
        }
    };

    useEffect(() => {
        if (isChatRoute) {
            if (activeTab !== 'chat') setActiveTabRaw('chat');
            return;
        }
        const nextTab = hashTab && settingsTabs.includes(hashTab) ? hashTab : 'status';
        if (activeTab !== nextTab) setActiveTabRaw(nextTab);
    }, [location.pathname, location.hash]);

    const { data: agent, isLoading } = useQuery({
        queryKey: ['agent', id],
        queryFn: () => agentApi.get(id!),
        enabled: !!id,
    });

    // Tenant default model — used to render the "默认" tag and as a visual
    // fallback when an agent has no explicit primary model.
    const { data: myTenant } = useQuery({
        queryKey: ['tenant', 'me'],
        queryFn: () => tenantApi.me(),
        staleTime: 5 * 60 * 1000,
    });

    // Chat-side picker. Source-of-truth is agent.primary_model_id; the
    // picker mirrors it bidirectionally:
    //   - User picks model in chat → handleModelChange PATCHes the agent.
    //   - Agent's saved default changes elsewhere (settings page, tenant
    //     default migration) → useEffect below pulls the new value in.
    // Earlier draft only synced on first mount (`overrideModelId === null`)
    // which left the chat picker stuck on a stale value when the agent
    // default was updated by another path.
    const [overrideModelId, setOverrideModelId] = useState<string | null>(null);
    useEffect(() => {
        if (agent?.primary_model_id && agent.primary_model_id !== overrideModelId) {
            setOverrideModelId(agent.primary_model_id);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [agent?.primary_model_id]);

    const handleModelChange = useCallback(async (newModelId: string | null) => {
        setOverrideModelId(newModelId);
        if (!id || !newModelId || newModelId === agent?.primary_model_id) return;
        try {
            await agentApi.update(id, { primary_model_id: newModelId });
            queryClient.invalidateQueries({ queryKey: ['agent', id] });
        } catch {
            setOverrideModelId(agent?.primary_model_id || null);
        }
    }, [id, agent?.primary_model_id]);

    // Track onboarding kickoff per (agent, session) so the agent only greets
    // once per session. The agent opens the conversation itself — no visible
    // user message — by sending a tagged trigger the backend filters out.
    const onboardingKickoffRef = useRef<Set<string>>(new Set());
    const [livePanelVisible, setLivePanelVisible] = useState(false);
    const [sidePanelTab, setSidePanelTab] = useState<SidePanelTab>('workspace');
    const awarePanelVisible = activeTab === 'chat' && livePanelVisible && sidePanelTab === 'aware';
    const awareDataActive = activeTab === 'aware' || awarePanelVisible;

    // ── Aware tab data: triggers ──
    const { data: awareTriggers = [], refetch: refetchTriggers } = useQuery({
        queryKey: ['triggers', id],
        queryFn: () => triggerApi.list(id!),
        enabled: !!id && awareDataActive,
        refetchInterval: awareDataActive ? 5000 : false,
    });

    // ── Aware tab data: structured Focus ──
    const { data: focusRecords = [], refetch: refetchFocusItems } = useQuery({
        queryKey: ['focus', id],
        queryFn: () => focusApi.list(id!, true),
        enabled: !!id && awareDataActive,
        refetchInterval: awareDataActive ? 5000 : false,
    });

    // ── Aware tab data: task_history.md ──
    const { data: taskHistoryFile } = useQuery({
        queryKey: ['file', id, 'task_history.md'],
        queryFn: () => fileApi.read(id!, 'task_history.md').catch(() => null),
        enabled: !!id && awareDataActive,
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
        enabled: !!id && awareDataActive,
        refetchInterval: awareDataActive ? 10000 : false,
    });

    // ── Aware tab state ──
    const [expandedFocusIds, setExpandedFocusIds] = useState<Set<string>>(() => new Set());
    const [expandedReflection, setExpandedReflection] = useState<string | null>(null);
    const [reflectionMessages, setReflectionMessages] = useState<Record<string, any[]>>({});
    const [showAllFocus, setShowAllFocus] = useState(false);
    const [showCompletedFocus, setShowCompletedFocus] = useState(false);
    const [showAllReflections, setShowAllReflections] = useState(false);
    const [awareView, setAwareView] = useState<'list' | 'calendar'>('list');
    const [awareCalendarMode, setAwareCalendarMode] = useState<'day' | 'week' | 'month'>('week');
    const [awareCalendarDate, setAwareCalendarDate] = useState<Date>(() => new Date());
    const [reflectionPage, setReflectionPage] = useState(0);
    const REFLECTIONS_PAGE_SIZE = 10;
    const SECTION_PAGE_SIZE = 5;

    const toggleExpandedFocus = (focusId: string) => {
        setExpandedFocusIds(prev => {
            const next = new Set(prev);
            if (next.has(focusId)) next.delete(focusId);
            else next.add(focusId);
            return next;
        });
    };

    const loadReflectionMessages = async (sessionId: string) => {
        if (!id || reflectionMessages[sessionId]) return;
        try {
            const tkn = localStorage.getItem('token');
            const res = await fetch(`/api/agents/${id}/sessions/${sessionId}/messages`, {
                headers: { Authorization: `Bearer ${tkn}` },
            });
            if (res.ok) {
                const data = await res.json();
                setReflectionMessages(prev => ({ ...prev, [sessionId]: data }));
            }
        } catch {
            // Reflection details are informational; keep the list usable if loading fails.
        }
    };

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
    const [scopeDropdownOpen, setScopeDropdownOpen] = useState(false);
    const scopeDropdownRef = useRef<HTMLDivElement>(null);
    const [historyMsgs, setHistoryMsgs] = useState<any[]>([]);
    const [sessionsLoading, setSessionsLoading] = useState(false);
    const [allSessionsLoading, setAllSessionsLoading] = useState(false);
    const [agentExpired, setAgentExpired] = useState(false);
    // Websocket chat state (for 'me' conversation)
    const token = useAuthStore((s) => s.token);
    const currentUser = useAuthStore((s) => s.user);
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
    const isAgentChatSession = (s: any) =>
        String(s?.source_channel || '').toLowerCase() === 'agent' ||
        String(s?.participant_type || '').toLowerCase() === 'agent';

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
            if (isAgentChatSession(s)) return true;
            const su = sessionUserIdStr(s);
            if (vu && su === vu) return false;
            return true;
        });
    }, [allSessions, currentUser?.id]);

    const othersListForPicker = otherUsersSessions;

    useEffect(() => {
        if (!canViewAllAgentChatSessions && chatScope === 'all') setChatScope('mine');
    }, [canViewAllAgentChatSessions, chatScope]);

    useEffect(() => {
        if (!scopeDropdownOpen) return;
        const handler = (e: MouseEvent) => {
            if (scopeDropdownRef.current && !scopeDropdownRef.current.contains(e.target as Node)) setScopeDropdownOpen(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [scopeDropdownOpen]);

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
        if (activeSession && sessionUserIdStr(activeSession) !== viewerUserIdStr()) clearChatSelection();
    };

    const onAdminTabOthers = () => {
        setChatScope('all');
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
        const writable = isWritableSession(sess, scopeOverride);
        activeSessionIdRef.current = sess.id;
        isFirstLoad.current = true;
        isNearBottom.current = true;
        userPinnedAwayFromBottomRef.current = false;
        pendingLiveInitialScrollRef.current = writable;
        pendingHistoryInitialScrollRef.current = !writable;
        setChatMessages([]);
        setHistoryMsgs([]);
        setIsStreaming(runtimeState.isStreaming);
        setIsWaiting(runtimeState.isWaiting);
        setActiveSession(sess);
        setAgentExpired(false);
        syncActiveSocketState(sess, targetAgentId);
        if (writable) scheduleComposerFocus();

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
                ...(m.toolName && { toolName: m.toolName, toolArgs: m.toolArgs, toolStatus: m.toolStatus, toolResult: m.toolResult, toolThinking: m.toolThinking }),
                ...(m.thinking && { thinking: m.thinking }),
                ...(m.created_at && { timestamp: m.created_at }),
                ...(m.id && { id: m.id }),
            }));

            if (writable) {
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
                toast.error('创建会话失败', { details: String(err.detail || `HTTP ${res.status}`) });
            }
        } catch (err: any) {
            console.error('Failed to create session:', err);
            toast.error('创建会话失败', { details: String(err.message || err) });
        }
    };

    const deleteSession = async (sessionId: string) => {
        const ok = await dialog.confirm(
            t('chat.deleteConfirm', 'Delete this session and all its messages? This cannot be undone.'),
            { title: '删除会话', danger: true, confirmLabel: '删除' },
        );
        if (!ok) return;
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
            toast.error('删除失败', { details: String(e?.message || e) });
        }
    };

    // Expiry editor modal state
    const [showExpiryModal, setShowExpiryModal] = useState(false);
    const [expiryValue, setExpiryValue] = useState('');       // datetime-local string or ''
    const [expiryQuickHours, setExpiryQuickHours] = useState<number | null>(null);
    const [expirySaving, setExpirySaving] = useState(false);

    const openExpiryModal = () => {
        const cur = (agent as any)?.expires_at;
        // Convert ISO to datetime-local format (YYYY-MM-DDTHH:MM)
        setExpiryValue(cur ? new Date(cur).toISOString().slice(0, 16) : '');
        setExpiryQuickHours(null);
        setShowExpiryModal(true);
    };

    const addHours = (h: number) => {
        const base = (agent as any)?.expires_at ? new Date((agent as any).expires_at) : new Date();
        const next = new Date(base.getTime() + h * 3600_000);
        setExpiryValue(next.toISOString().slice(0, 16));
        setExpiryQuickHours(h);
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
        } catch (e: any) { toast.error('保存失败', { details: String(e?.message || e) }); }
        setExpirySaving(false);
    };
    interface ChatMsg { role: 'user' | 'assistant' | 'tool_call'; content: string; fileName?: string; toolName?: string; toolCallId?: string; toolArgs?: any; toolStatus?: 'running' | 'done'; toolResult?: string; toolThinking?: string; thinking?: string; imageUrl?: string; timestamp?: string; }
    const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);
    const getToolTargetKey = (args: any): string => {
        if (!args) return '';
        const parsed = typeof args === 'string'
            ? (() => {
                try { return JSON.parse(args); } catch { return null; }
            })()
            : args;
        if (!parsed || typeof parsed !== 'object') return '';
        const value = parsed.path
            || parsed.file_path
            || parsed.output_path
            || parsed.target_path
            || parsed.filename
            || parsed.url
            || parsed.query
            || parsed.name
            || '';
        return typeof value === 'string' ? value.trim() : '';
    };
    const upsertToolCallMessage = (toolMsg: ChatMsg) => {
        setChatMessages(prev => {
            const incomingTarget = getToolTargetKey(toolMsg.toolArgs);
            const sameTool = (msg: ChatMsg) => (
                msg.role === 'tool_call'
                && msg.toolName === toolMsg.toolName
                && msg.toolStatus === 'running'
                && (
                    (!!toolMsg.toolCallId && !!msg.toolCallId && msg.toolCallId === toolMsg.toolCallId)
                    || (!!incomingTarget && getToolTargetKey(msg.toolArgs) === incomingTarget)
                    || (!toolMsg.toolCallId && !incomingTarget)
                )
            );
            const runningIdx = [...prev].reverse().findIndex(sameTool);
            if (runningIdx >= 0) {
                const idx = prev.length - 1 - runningIdx;
                return [...prev.slice(0, idx), { ...prev[idx], ...toolMsg }, ...prev.slice(idx + 1)];
            }
            return [...prev, toolMsg];
        });
    };
    // Transient info banner (e.g. fallback model switch notification)
    const [chatInfoMsg, setChatInfoMsg] = useState<string | null>(null);
    const chatInfoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    // Stable expanded-state map for tool groups — keyed by groupStartIndex.
    // Stored in a ref so it survives parent re-renders without causing extra renders.
    const toolGroupExpandedRef = useRef<Map<number, boolean>>(new Map());
    const [toolGroupExpandedVersion, setToolGroupExpandedVersion] = useState(0);
    const toggleToolGroup = (key: number) => {
        const m = toolGroupExpandedRef.current;
        const nextExpanded = !m.get(key);
        m.set(key, nextExpanded);
        setToolGroupExpandedVersion(v => v + 1); // trigger re-render
        if (nextExpanded) {
            scheduleLiveScrollToBottom();
        }
    };
    const [liveState, setLiveState] = useState<LivePreviewState>({});
    const [workspaceActivePath, setWorkspaceActivePath] = useState<string | null>(null);
    const [workspaceLockedPath, setWorkspaceLockedPath] = useState<string | null>(null);
    const [workspaceActivities, setWorkspaceActivities] = useState<WorkspaceActivity[]>([]);
    const [workspaceLiveDraft, setWorkspaceLiveDraft] = useState<WorkspaceLiveDraft | null>(null);
    const workspaceEditingRef = useRef(false);
    const workspaceLockedPathRef = useRef<string | null>(null);
    const [wsSessionId, setWsSessionId] = useState<string>('');
    const [sessionListCollapsed, setSessionListCollapsed] = useState(false);
    const livePanelAutoCollapsedRef = useRef(false);
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
        modelId?: string | null;
    };
    const [attachedFiles, setAttachedFiles] = useState<AttachedFileRef[]>([]);
    const dismissedWorkspaceRefPath = useRef<string | null>(null);
    const pendingChatSendRef = useRef<PendingChatMessage | null>(null);
    const wsRef = useRef<WebSocket | null>(null);

    // Onboarding kickoff: once WS is connected and the session is empty, and
    // this viewer has never been onboarded to this agent, fire a tagged trigger
    // exactly once per (agent, session). Backend swallows the user turn and
    // streams the assistant greeting. Founding vs welcoming content is decided
    // server-side based on whether anyone else has been onboarded to this
    // agent before.
    useEffect(() => {
        if (!wsConnected || !id || !activeSession?.id) return;
        if (!agent || agent.onboarded_for_me !== false) return;
        if (chatMessages.length > 0) return;
        const runtimeKey = buildSessionRuntimeKey(id, String(activeSession.id));
        if (onboardingKickoffRef.current.has(runtimeKey)) return;
        const socket = wsMapRef.current[runtimeKey];
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        onboardingKickoffRef.current.add(runtimeKey);
        setIsWaiting(true);
        setIsStreaming(false);
        socket.send(JSON.stringify({
            content: '',
            kind: 'onboarding_trigger',
            model_id: overrideModelId,
        }));
    }, [wsConnected, id, activeSession?.id, agent?.onboarded_for_me, chatMessages.length, overrideModelId]);

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
    const collapseSidebarsForLivePanel = useCallback(() => {
        if (livePanelAutoCollapsedRef.current) return;
        livePanelAutoCollapsedRef.current = true;
        setSessionListCollapsed(true);
        useAppStore.setState({ sidebarCollapsed: true });
    }, []);
    useEffect(() => {
        if (!livePanelVisible) {
            livePanelAutoCollapsedRef.current = false;
        }
    }, [livePanelVisible]);
    const togglePreviewPanel = useCallback((tab: SidePanelTab) => {
        setLivePanelVisible((visible) => {
            if (visible && sidePanelTab === tab) {
                livePanelAutoCollapsedRef.current = false;
                return false;
            }
            setSidePanelTab(tab);
            collapseSidebarsForLivePanel();
            return true;
        });
    }, [collapseSidebarsForLivePanel, sidePanelTab]);

    const openAwarePanel = useCallback(() => {
        if (!allowLivePanelAutoFocus()) return;
        setSidePanelTab('aware');
        setLivePanelVisible(true);
        collapseSidebarsForLivePanel();
    }, [allowLivePanelAutoFocus, collapseSidebarsForLivePanel]);

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
            if (location.pathname.endsWith('/settings')) {
                window.history.replaceState(null, '', `#${activeTab}`);
            }
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
        setSessions([]);
        setAllSessions([]);
        setAgentExpired(false);
        settingsInitRef.current = false;
    }, [id]);

    // Switching login account or token must not leave another user's sessions/messages in memory.
    useEffect(() => {
        setSessions([]);
        setAllSessions([]);
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

        const lang = (i18n.language || 'en').toLowerCase().startsWith('zh') ? 'zh' : 'en';
        const ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat/${agentId}?token=${authToken}${sessionParam}&lang=${lang}`);
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
            // Onboarding lock fired (or trigger was rejected because the pair
            // was already onboarded). Either way, invalidate the cached agent
            // record so the kickoff effect stops thinking a new session needs
            // onboarding. Fire early and unconditionally — the event is cheap.
            if (d.type === 'onboarded') {
                queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
                return;
            }
            const isActiveRuntime = currentAgentIdRef.current === agentId && activeSessionIdRef.current === sessionId;
            if (['thinking', 'chunk', 'workspace_draft', 'tool_call', 'done', 'error', 'quota_exceeded'].includes(d.type)) {
                const nextStreaming = ['thinking', 'chunk', 'workspace_draft', 'tool_call'].includes(d.type);
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

            if (['thinking', 'chunk', 'workspace_draft', 'tool_call', 'done', 'error', 'quota_exceeded'].includes(d.type)) {
                setIsWaiting(false);
                if (['thinking', 'chunk', 'workspace_draft', 'tool_call'].includes(d.type)) setIsStreaming(true);
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
                    if (isFocusPath(draft.path)) {
                        openAwarePanel();
                    } else if (allowLivePanelAutoFocus()) {
                        setSidePanelTab('workspace');
                        setLivePanelVisible(true);
                        collapseSidebarsForLivePanel();
                    }
                    let toolArgs: any = parsedDraft;
                    try {
                        toolArgs = JSON.parse(d.arguments || '{}');
                    } catch {
                        toolArgs = parsedDraft;
                    }
                    upsertToolCallMessage({
                        role: 'tool_call',
                        content: '',
                        toolName: d.name,
                        toolCallId: draft.id,
                        toolArgs,
                        toolStatus: 'running',
                    });
                }
            } else if (d.type === 'tool_call') {
                if (AWARE_TOOLS.has(d.name)) {
                    openAwarePanel();
                    if (d.status === 'done') {
                        refetchTriggers();
                        refetchFocusItems();
                        queryClient.invalidateQueries({ queryKey: ['focus', id] });
                    }
                }
                if (d.name === 'agentbay_file_transfer') {
                    const transfer = parseAgentBayTransferArgs(d.args);
                    setLiveState(prev => ({
                        ...prev,
                        transfer: {
                            ...prev.transfer,
                            ...transfer,
                            status: d.status === 'done' ? 'done' : 'running',
                            result: d.status === 'done' && typeof d.result === 'string' ? d.result : prev.transfer?.result,
                            updatedAt: Date.now(),
                        },
                    }));
                    if (allowLivePanelAutoFocus()) {
                        setSidePanelTab('transfer');
                        setLivePanelVisible(true);
                        collapseSidebarsForLivePanel();
                    }
                }
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
                        if (isFocusPath(draft.path)) {
                            openAwarePanel();
                        } else if (allowLivePanelAutoFocus()) {
                            setSidePanelTab('workspace');
                            setLivePanelVisible(true);
                            collapseSidebarsForLivePanel();
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
                        collapseSidebarsForLivePanel();
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
                    if (isFocusPath(activity.path)) {
                        openAwarePanel();
                        refetchFocusItems();
                        queryClient.invalidateQueries({ queryKey: ['focus', id] });
                    } else if (allowLivePanelAutoFocus()) {
                        setSidePanelTab('workspace');
                        setLivePanelVisible(true);
                        collapseSidebarsForLivePanel();
                    }
                    queryClient.invalidateQueries({ queryKey: ['files', id, workspacePath] });
                }
                upsertToolCallMessage({
                    role: 'tool_call',
                    content: '',
                    toolName: d.name,
                    toolCallId: String(d.call_id || d.id || d.index || ''),
                    toolArgs: d.args,
                    toolStatus: d.status,
                    toolResult: d.result,
                    toolThinking: d.reasoning_content,
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
                if (canViewAllAgentChatSessions && (scopeDropdownOpen || chatScope === 'all' || allSessions.length > 0)) {
                    fetchAllSessions();
                }
                queryClient.invalidateQueries({ queryKey: ['agents'] });
            } else if (d.type === 'error' || d.type === 'quota_exceeded') {
                const msg = d.content || d.detail || d.message || 'Request denied';
                setChatMessages(prev => {
                    const last = prev[prev.length - 1];
                    const warningText = `Warning: ${msg}`;
                    if (last && last.role === 'assistant' && last.content === warningText) return prev;
                    return [...prev, parseChatMsg({ role: 'assistant', content: warningText })];
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
            file_name: payload.fileName,
            model_id: payload.modelId,
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
    const pendingLiveInitialScrollRef = useRef(false);
    const pendingHistoryInitialScrollRef = useRef(false);
    const liveAutoFollowUntilRef = useRef(0);
    const userPinnedAwayFromBottomRef = useRef(false);
    const liveScrollJobRef = useRef(0);
    const liveScrollTimersRef = useRef<number[]>([]);
    const chatTouchStartYRef = useRef<number | null>(null);
    const [showScrollBtn, setShowScrollBtn] = useState(false);
    const [chatScrollBtnBottom, setChatScrollBtnBottom] = useState(96);
    // Read-only history scroll-to-bottom
    const historyContainerRef = useRef<HTMLDivElement>(null);
    const [showHistoryScrollBtn, setShowHistoryScrollBtn] = useState(false);
    const scheduleComposerFocus = useCallback(() => {
        let attempts = 0;
        const focusWhenReady = () => {
            const el = chatInputRef.current;
            if (!el || activeTab !== 'chat') {
                if (attempts++ < 8) requestAnimationFrame(focusWhenReady);
                return;
            }
            el.focus({ preventScroll: true });
            const caret = el.value.length;
            try {
                el.setSelectionRange(caret, caret);
            } catch { }
        };
        requestAnimationFrame(focusWhenReady);
    }, [activeTab]);
    const cancelLiveAutoFollow = useCallback(() => {
        liveAutoFollowUntilRef.current = 0;
        liveScrollJobRef.current += 1;
        liveScrollTimersRef.current.forEach((timer) => window.clearTimeout(timer));
        liveScrollTimersRef.current = [];
    }, []);
    const pinChatAwayFromBottom = useCallback(() => {
        cancelLiveAutoFollow();
        userPinnedAwayFromBottomRef.current = true;
        isNearBottom.current = false;
        setShowScrollBtn(true);
    }, [cancelLiveAutoFollow]);
    const scheduleLiveScrollToBottom = useCallback(() => {
        if (userPinnedAwayFromBottomRef.current) return;
        cancelLiveAutoFollow();
        const jobId = liveScrollJobRef.current;
        liveAutoFollowUntilRef.current = Date.now() + 1500;
        let attempts = 0;
        const scroll = () => {
            if (jobId !== liveScrollJobRef.current) return;
            if (userPinnedAwayFromBottomRef.current) return;
            const el = chatContainerRef.current;
            if (el) el.scrollTop = el.scrollHeight;
            setShowScrollBtn(false);
            if (attempts++ < 2) requestAnimationFrame(scroll);
        };
        requestAnimationFrame(scroll);
        liveScrollTimersRef.current = [
            window.setTimeout(scroll, 80),
            window.setTimeout(scroll, 220),
        ];
    }, [cancelLiveAutoFollow]);
    useEffect(() => {
        return () => cancelLiveAutoFollow();
    }, [cancelLiveAutoFollow]);
    const scheduleHistoryScrollToBottom = useCallback(() => {
        let attempts = 0;
        const scroll = () => {
            const el = historyContainerRef.current;
            if (el) el.scrollTop = el.scrollHeight;
            setShowHistoryScrollBtn(false);
            if (attempts++ < 8) requestAnimationFrame(scroll);
        };
        requestAnimationFrame(scroll);
        window.setTimeout(scroll, 120);
        window.setTimeout(scroll, 360);
    }, []);
    const handleHistoryScroll = () => {
        const el = historyContainerRef.current;
        if (!el) return;
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        setShowHistoryScrollBtn(distFromBottom > 200);
    };
    const scrollHistoryToBottom = () => {
        scheduleHistoryScrollToBottom();
    };
    useEffect(() => {
        if (activeTab === 'chat' && activeSession && isWritableSession(activeSession)) {
            scheduleComposerFocus();
        }
    }, [activeTab, activeSession?.id, scheduleComposerFocus]);
    // Auto-show button when history messages overflow the container
    useEffect(() => {
        const el = historyContainerRef.current;
        if (!el) return;
        // Use a small timeout to let the DOM render the messages first
        const timer = setTimeout(() => {
            if (pendingHistoryInitialScrollRef.current && historyMsgs.length > 0) {
                pendingHistoryInitialScrollRef.current = false;
                scheduleHistoryScrollToBottom();
                return;
            }
            const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
            setShowHistoryScrollBtn(distFromBottom > 200);
        }, 100);
        return () => clearTimeout(timer);
    }, [historyMsgs, activeSession?.id, scheduleHistoryScrollToBottom]);
    // Memoized component for each chat message to avoid re-renders while typing
    const ChatMessageItem = React.useMemo(() => React.memo(({
        msg, i, isLeft, t, senderLabel, avatarText, forceSenderLabel = false, hideAvatar = false,
    }: {
        msg: any;
        i: number;
        isLeft: boolean;
        t: any;
        senderLabel?: string;
        avatarText?: string;
        forceSenderLabel?: boolean;
        hideAvatar?: boolean;
    }) => {
        const fe = msg.fileName?.split('.').pop()?.toLowerCase() ?? '';
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
                <div className="chat-msg-timestamp">
                    {timeStr}
                    {msg.content && <CopyMessageButton text={msg.content} />}
                </div>
            );
        })() : null;

        return (
            <div key={i} className={`chat-msg-row${isLeft ? '' : ' chat-msg-row--user'}`}>
                <div
                    className={`chat-msg-avatar${isLeft ? '' : ' chat-msg-avatar--user'}`}
                    style={hideAvatar ? { visibility: 'hidden' } : undefined}
                >
                    {resolvedAvatarText}
                </div>
                <div className="chat-msg-col">
                    <div className={isLeft ? '' : 'chat-msg-user-line'}>
                        <div className={`chat-msg-bubble${isLeft ? '' : ' chat-msg-bubble--user'}${(msg as any)._streaming && !msg.content && !msg.thinking ? ' chat-msg-bubble--thinking' : ''}`}>
                            {showSenderLabel && <div className="chat-msg-sender">{resolvedSenderLabel}</div>}
                            {isImage ? (
                                <div style={{ marginBottom: '4px' }}>
                                    <img src={msg.imageUrl} alt={msg.fileName} style={{ maxWidth: '200px', maxHeight: '150px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }} loading="lazy" />
                                </div>
                            ) : (msg.fileName && (
                                <div className="chat-msg-file-chip" style={{ marginBottom: msg.content ? '4px' : '0' }}>
                                    <IconPaperclip size={14} stroke={1.8} />
                                    <span style={{ fontWeight: 500, color: 'var(--text-primary)', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{msg.fileName}</span>
                                </div>
                            ))}
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
                            {msg.role === 'assistant' ? (
                                (msg as any)._streaming && !msg.content && !msg.thinking ? (
                                    <div className="thinking-indicator">
                                        <div className="thinking-dots"><span /><span /><span /></div>
                                        <span style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>{t('agent.chat.thinking', 'Thinking...')}</span>
                                    </div>
                                ) : <MarkdownRenderer content={displayContent} />
                            ) : <MarkdownRenderer content={displayContent} />}
                        </div>
                    </div>
                    {timestampHtml}
                </div>
            </div>
        );
    }), [t]);

    const handleChatScroll = () => {
        const el = chatContainerRef.current;
        if (!el) return;
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        isNearBottom.current = distFromBottom < 160;
        userPinnedAwayFromBottomRef.current = distFromBottom > 260;
        if (userPinnedAwayFromBottomRef.current) {
            cancelLiveAutoFollow();
        }
        setShowScrollBtn(distFromBottom > 200);
    };
    const handleChatWheelCapture = (event: React.WheelEvent<HTMLDivElement>) => {
        const el = chatContainerRef.current;
        if (!el) return;
        if (event.deltaY < 0 && el.scrollTop > 0) {
            pinChatAwayFromBottom();
        }
    };
    const handleChatTouchStartCapture = (event: React.TouchEvent<HTMLDivElement>) => {
        chatTouchStartYRef.current = event.touches[0]?.clientY ?? null;
    };
    const handleChatTouchMoveCapture = (event: React.TouchEvent<HTMLDivElement>) => {
        const startY = chatTouchStartYRef.current;
        const currentY = event.touches[0]?.clientY;
        const el = chatContainerRef.current;
        if (startY == null || currentY == null || !el) return;
        if (currentY - startY > 6 && el.scrollTop > 0) {
            pinChatAwayFromBottom();
        }
    };
    const scrollToBottom = () => {
        userPinnedAwayFromBottomRef.current = false;
        scheduleLiveScrollToBottom();
    };
    useEffect(() => {
        if (activeTab !== 'chat' || !activeSession || !isWritableSession(activeSession)) return;
        const el = chatContainerRef.current;
        if (!el) return;
        const shouldFollow = () => (
            !userPinnedAwayFromBottomRef.current &&
            (isNearBottom.current || Date.now() < liveAutoFollowUntilRef.current)
        );
        const maybeFollow = () => {
            if (shouldFollow()) scheduleLiveScrollToBottom();
        };
        const mutationObserver = new MutationObserver(maybeFollow);
        mutationObserver.observe(el, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['open', 'class', 'style'],
        });
        let resizeObserver: ResizeObserver | null = null;
        if (typeof ResizeObserver !== 'undefined') {
            resizeObserver = new ResizeObserver(maybeFollow);
            resizeObserver.observe(el);
            Array.from(el.children).forEach(child => resizeObserver?.observe(child));
        }
        return () => {
            mutationObserver.disconnect();
            resizeObserver?.disconnect();
        };
    }, [activeTab, activeSession?.id, scheduleLiveScrollToBottom]);
    useEffect(() => {
        if (!chatEndRef.current) return;
        if (pendingLiveInitialScrollRef.current && chatMessages.length > 0) {
            pendingLiveInitialScrollRef.current = false;
            isFirstLoad.current = false;
            isNearBottom.current = true;
            scheduleLiveScrollToBottom();
            return;
        }
        if (isFirstLoad.current && chatMessages.length > 0) {
            // First load: instant jump to bottom, no animation
            scheduleLiveScrollToBottom();
            isFirstLoad.current = false;
            return;
        }
        if (isNearBottom.current) {
            scheduleLiveScrollToBottom();
        }
    }, [chatMessages, scheduleLiveScrollToBottom]);

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
                filesDisplay += `[Attachment: ${file.name}] `;
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
            modelId: overrideModelId,
        };

        setChatInput('');
        userPinnedAwayFromBottomRef.current = false;
        isNearBottom.current = true;
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
            toast.warning('最多可附加 10 个文件');
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
                if (err?.message !== 'Upload cancelled') toast.error(t('agent.upload.failed'), { details: String(err?.message || err) });
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
            toast.warning('最多可附加 10 个文件');
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
                if (err?.message !== 'Upload cancelled') toast.error(t('agent.upload.failed'), { details: String(err?.message || err) });
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
                    toast.error(t('agent.upload.failed'), { details: String(err?.message || '') });
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
            toast.error('创建计划任务失败', { details: String(msg) });
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
            showToast('Schedule triggered — executing in background', 'success');
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
    const [infoCardOpen, setInfoCardOpen] = useState(false);
    const infoCardCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const clearCardCloseTimer = () => { if (infoCardCloseTimer.current) { clearTimeout(infoCardCloseTimer.current); infoCardCloseTimer.current = null; } };
    const scheduleCardClose = () => { clearCardCloseTimer(); infoCardCloseTimer.current = setTimeout(() => setInfoCardOpen(false), 180); };
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
    const canManage = (agent as any).access_level === 'manage';
    const formatAgentDate = (d?: string | null) => {
        if (!d) return '—';
        try { return new Date(d).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); } catch { return d; }
    };
    const primaryModel = llmModels.find((m: any) => m.id === agent.primary_model_id);
    const modelLabel = primaryModel ? (primaryModel.label || primaryModel.model) : '—';
    const modelProvider = primaryModel ? primaryModel.provider : '—';
    const todayParts = formatTokensParts(agent.tokens_used_today || 0);
    const monthParts = formatTokensParts(agent.tokens_used_month || 0);
    const totalParts = formatTokensParts((agent as any).tokens_used_total || 0);
    const cacheReadToday = (agent as any).cache_read_tokens_today || metrics?.tokens?.cache_read_today || 0;
    const cacheReadMonth = (agent as any).cache_read_tokens_month || metrics?.tokens?.cache_read_month || 0;
    const cacheReadTotal = (agent as any).cache_read_tokens_total || metrics?.tokens?.cache_read_total || 0;
    const cacheHitRateToday = (agent.tokens_used_today || 0) > 0 ? Math.round((cacheReadToday / (agent.tokens_used_today || 1)) * 100) : 0;
    const cacheHitRateMonth = (agent.tokens_used_month || 0) > 0 ? Math.round((cacheReadMonth / (agent.tokens_used_month || 1)) * 100) : 0;
    const cacheHitRateTotal = ((agent as any).tokens_used_total || 0) > 0 ? Math.round((cacheReadTotal / ((agent as any).tokens_used_total || 1)) * 100) : 0;
    const expiryLabel = (agent as any).is_expired
        ? t('agent.settings.expiry.expired')
        : (agent as any).expires_at
            ? new Date((agent as any).expires_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
            : t('agent.settings.expiry.neverExpires');
    const renderAgentInfoCard = () => (
        <div className={`agent-info-card${infoCardOpen ? ' agent-info-card--open' : ''}`}>
            <div className="agent-info-card-inner">
                <div className="agent-info-card-glow" />
                <div className="agent-info-card-grid">
                    {/* Agent Profile */}
                    <div className="agent-info-card-section">
                        <div className="agent-info-card-section-header">
                            <span className="agent-info-section-icon agent-info-section-icon--indigo">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="8" r="4"/><path d="M20 21a8 8 0 0 0-16 0"/></svg>
                            </span>
                            <span className="agent-info-card-section-title">{t('agent.profile.title', 'Agent Profile')}</span>
                        </div>
                        <div className="agent-info-card-body">
                            <div className="agent-info-profile-panel">
                                {agent.role_description && (
                                    <div className="agent-info-profile-role" title={agent.role_description}>{agent.role_description}</div>
                                )}
                                <div className="agent-info-meta-list agent-info-profile-meta">
                                    <div className="agent-info-meta-row">
                                        <span>{t('agent.profile.created')}</span>
                                        <span>{formatAgentDate(agent.created_at)}</span>
                                    </div>
                                    <div className="agent-info-meta-row">
                                        <span>{t('agent.fields.createdBy', 'Created by')}</span>
                                        <span>{(agent as any).creator_username ? `@${(agent as any).creator_username}` : '—'}</span>
                                    </div>
                                    <div className="agent-info-meta-row">
                                        <span>{t('agent.profile.timezone')}</span>
                                        <span>{(agent as any).effective_timezone || agent.timezone || 'UTC'}</span>
                                    </div>
                                    <div className="agent-info-meta-row">
                                        <span>{t('agent.settings.expiry.title')}</span>
                                        <span className={(agent as any).is_expired ? 'agent-info-expiry--expired' : ''}>{expiryLabel}</span>
                                    </div>
                                </div>
                                {canManage && (
                                    <button
                                        type="button"
                                        className="agent-info-expiry-button"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            openExpiryModal();
                                        }}
                                    >
                                        {t('agent.settings.expiry.title')}
                                    </button>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="agent-info-card-section agent-info-card-section--stacked">
                        {/* Model Configuration */}
                        <div className="agent-info-subsection">
                            <div className="agent-info-card-section-header">
                                <span className="agent-info-section-icon agent-info-section-icon--indigo">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
                                </span>
                                <span className="agent-info-card-section-title">{t('agent.modelConfig.title', 'Configuration')}</span>
                            </div>
                            <div className="agent-info-card-body agent-info-card-body--compact">
                                <div className="agent-info-model-card">
                                    <div className="agent-info-model-card-text">
                                        <span className="agent-info-model-card-label">{t('agent.modelConfig.model')}</span>
                                        <span className="agent-info-model-card-name" title={modelLabel}>{modelLabel}</span>
                                    </div>
                                </div>
                                <div className="agent-info-meta-list">
                                    <div className="agent-info-meta-row">
                                        <span>{t('agent.modelConfig.provider', 'Provider')}</span>
                                        <span>{modelProvider}</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                        {/* Token Usage */}
                        <div className="agent-info-subsection">
                            <div className="agent-info-card-section-header">
                                <span className="agent-info-section-icon agent-info-section-icon--blue">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
                                </span>
                                <span className="agent-info-card-section-title">Token</span>
                            </div>
                            <div className="agent-info-card-body agent-info-card-body--compact">
                                <div className="agent-info-token-glass">
                                    <div className="agent-info-token-hero">
                                        <span className="agent-info-token-hero-label">{t('agent.settings.today')}</span>
                                        <span className="agent-info-token-hero-value">
                                            {todayParts.value}
                                            {todayParts.unit && <span className="agent-info-token-hero-unit">{todayParts.unit}</span>}
                                        </span>
                                    </div>
                                    <div className="agent-info-token-stats">
                                        <div className="agent-info-stat-item">
                                            <span className="agent-info-stat-label">{t('agent.settings.month')}</span>
                                            <span className="agent-info-stat-value">
                                                {monthParts.value}
                                                {monthParts.unit && <span className="agent-info-stat-unit">{monthParts.unit}</span>}
                                            </span>
                                        </div>
                                        <div className="agent-info-stat-item">
                                            <span className="agent-info-stat-label">Cache</span>
                                            <span className="agent-info-stat-value" title={`Today cache hit: ${formatTokens(cacheReadToday)} · ${cacheHitRateToday}%`}>
                                                {formatTokens(cacheReadToday)}
                                                <span className="agent-info-stat-unit">{cacheHitRateToday}%</span>
                                            </span>
                                        </div>
                                        <div className="agent-info-stat-item">
                                            <span className="agent-info-stat-label">{t('agent.status.totalToken')}</span>
                                            <span className="agent-info-stat-value">
                                                {totalParts.value}
                                                {totalParts.unit && <span className="agent-info-stat-unit">{totalParts.unit}</span>}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
    const renderAwarePreview = () => {
        const focusItems = focusRecords.map(focusItemFromApi);
        const isZh = i18n.language?.startsWith('zh');
        const formatTrigger = (trig: any) => {
            if (trig.type === 'cron' && trig.config?.expr) return `Cron ${trig.config.expr}`;
            if (trig.type === 'interval' && trig.config?.minutes) return isZh ? `每 ${trig.config.minutes} 分钟` : `Every ${trig.config.minutes} min`;
            if (trig.type === 'once' && trig.config?.at) return new Date(trig.config.at).toLocaleString();
            return trig.name || trig.type;
        };
        const triggerTitle = (trig: any) => String(trig.reason || trig.name || trig.type || '').trim();
        const triggerMeta = (trig: any) => {
            const schedule = formatTrigger(trig);
            if (!trig.reason || schedule === trig.reason) return schedule;
            return schedule;
        };
        const triggerTooltip = (trig: any) => {
            const title = triggerTitle(trig);
            const meta = triggerMeta(trig);
            const parts = [title, meta];
            if (trig.reason && trig.reason !== title) parts.push(String(trig.reason));
            if (trig.name && trig.name !== title) parts.push(String(trig.name));
            return Array.from(new Set(parts.filter(Boolean))).join('\n');
        };
        const triggersByFocus: Record<string, any[]> = {};
        const focusNames = new Set(focusItems.map((item) => item.name));
        for (const trig of awareTriggers as any[]) {
            if (trig.focus_ref && focusNames.has(trig.focus_ref)) {
                if (!triggersByFocus[trig.focus_ref]) triggersByFocus[trig.focus_ref] = [];
                triggersByFocus[trig.focus_ref].push(trig);
            } else {
                const synthetic = synthesizeFocusForTrigger(trig);
                if (!triggersByFocus[synthetic.name]) triggersByFocus[synthetic.name] = [];
                triggersByFocus[synthetic.name].push(trig);
            }
        }
        const displayFocusItems = focusItems;
        const activeFocusItems = displayFocusItems.filter(item => !item.done && !item.system);
        const systemFocusItems = displayFocusItems.filter(item => !item.done && item.system);
        const completedFocusItems = displayFocusItems.filter(item => item.done);
        const renderTriggerDot = (done: boolean, label: string) => (
            <span className={`aware-side-status-dot ${done ? 'done' : 'active'}`} aria-label={label} />
        );
        const renderFocusItem = (item: FocusItem) => {
            const isExpanded = expandedFocusIds.has(item.id);
            const itemTriggers = triggersByFocus[item.name] || [];
            return (
                <div key={item.id} className={`aware-side-focus ${item.done ? 'done' : ''}`}>
                    <button className="aware-side-focus-head" type="button" onClick={() => toggleExpandedFocus(item.id)}>
                        <div className="aware-side-trigger-main">
                            <div className="aware-side-item-title">
                                <span>{item.description || item.name}</span>
                                {item.done && (
                                    <span className="aware-side-focus-badge done">
                                        {t('agent.aware.completed')}
                                    </span>
                                )}
                            </div>
                            {item.description && <div className="aware-side-item-meta">{item.name}</div>}
                        </div>
                        <span className="aware-side-count">
                            {isZh ? `${itemTriggers.length} 个` : itemTriggers.length}
                        </span>
                        <span className={`aware-side-chevron ${isExpanded ? 'open' : ''}`}>▶</span>
                    </button>
                    {isExpanded && (
                        <div className="aware-side-nested">
                            {itemTriggers.length === 0 ? (
                                <div className="aware-side-empty compact">{t('agent.aware.noTriggers')}</div>
                            ) : itemTriggers.map((trig: any) => (
                                <div key={trig.id} className={`aware-side-trigger ${trig.is_enabled ? '' : 'done'}`}>
                                    {renderTriggerDot(!trig.is_enabled, trig.is_enabled ? t('agent.aware.inProgress') : t('agent.aware.completed'))}
                                    <div className="aware-side-trigger-main">
                                        <div className="aware-side-item-title">{triggerTitle(trig)}</div>
                                        <div className="aware-side-item-meta">{triggerMeta(trig)}</div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            );
        };
        const renderFocusGroup = (title: string, items: FocusItem[]) => {
            if (items.length === 0) return null;
            return (
                <div className="aware-side-focus-group">
                    <div className="aware-side-subtitle">{title}</div>
                    {items.slice(0, 12).map(renderFocusItem)}
                </div>
            );
        };
        const parseTriggerTime = (trig: any): Date | null => {
            if (trig.type === 'once' && trig.config?.at) {
                const date = new Date(trig.config.at);
                return Number.isNaN(date.getTime()) ? null : date;
            }
            return null;
        };
        const startOfDay = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());
        const today = startOfDay(new Date());
        const calendarAnchor = startOfDay(awareCalendarDate);
        const calendarDays = (() => {
            if (awareCalendarMode === 'day') return [calendarAnchor];
            if (awareCalendarMode === 'month') {
                const first = new Date(calendarAnchor.getFullYear(), calendarAnchor.getMonth(), 1);
                return Array.from({ length: 31 }, (_, idx) => new Date(first.getFullYear(), first.getMonth(), first.getDate() + idx))
                    .filter(date => date.getMonth() === first.getMonth());
            }
            const weekStart = new Date(calendarAnchor);
            weekStart.setDate(calendarAnchor.getDate() - ((calendarAnchor.getDay() + 6) % 7));
            return Array.from({ length: 7 }, (_, idx) => new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + idx));
        })();
        const calendarRangeLabel = (() => {
            if (awareCalendarMode === 'day') {
                return calendarAnchor.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric', weekday: 'short' });
            }
            if (awareCalendarMode === 'month') {
                return calendarAnchor.toLocaleDateString(undefined, { year: 'numeric', month: 'long' });
            }
            const first = calendarDays[0];
            const last = calendarDays[calendarDays.length - 1];
            return `${first.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} - ${last.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}`;
        })();
        const shiftCalendar = (direction: -1 | 1) => {
            setAwareCalendarDate(prev => {
                const next = new Date(prev);
                if (awareCalendarMode === 'day') next.setDate(next.getDate() + direction);
                else if (awareCalendarMode === 'week') next.setDate(next.getDate() + direction * 7);
                else next.setMonth(next.getMonth() + direction);
                return next;
            });
        };
        const timedTriggers = (awareTriggers as any[]).filter((trig) => ['once', 'cron', 'interval'].includes(trig.type));
        const recurringTriggers = timedTriggers.filter((trig) => !parseTriggerTime(trig));
        const triggersForDay = (day: Date) => timedTriggers.filter((trig) => {
            const when = parseTriggerTime(trig);
            return !!when && startOfDay(when).getTime() === day.getTime();
        });
        const renderCalendar = () => (
            <div className="aware-calendar">
                <div className="aware-calendar-header">
                    <div className="aware-calendar-toolbar">
                        {(['day', 'week', 'month'] as const).map(mode => (
                            <button
                                key={mode}
                                type="button"
                                className={`aware-view-button ${awareCalendarMode === mode ? 'active' : ''}`}
                                onClick={() => setAwareCalendarMode(mode)}
                            >
                                {isZh ? ({ day: '日', week: '周', month: '月' } as const)[mode] : mode}
                            </button>
                        ))}
                    </div>
                    <div className="aware-calendar-nav">
                        <button type="button" className="aware-calendar-nav-button" onClick={() => shiftCalendar(-1)} aria-label={isZh ? '上一段时间' : 'Previous'}>
                            ‹
                        </button>
                        <button type="button" className="aware-calendar-range" onClick={() => setAwareCalendarDate(new Date())}>
                            {calendarRangeLabel}
                        </button>
                        <button type="button" className="aware-calendar-nav-button" onClick={() => shiftCalendar(1)} aria-label={isZh ? '下一段时间' : 'Next'}>
                            ›
                        </button>
                    </div>
                </div>
                <div className={`aware-calendar-grid mode-${awareCalendarMode}`}>
                    {calendarDays.map((day) => {
                        const items = triggersForDay(day);
                        const isToday = day.getTime() === today.getTime();
                        return (
                            <div key={day.toISOString()} className={`aware-calendar-day ${isToday ? 'is-today' : ''}`}>
                                <div className="aware-calendar-day-label">
                                    {day.toLocaleDateString(undefined, awareCalendarMode === 'month' ? { day: 'numeric' } : { weekday: 'short', month: 'numeric', day: 'numeric' })}
                                    {isToday && <span className="aware-calendar-today-pill">{isZh ? '今天' : 'Today'}</span>}
                                </div>
                                {items.length === 0 ? (
                                    <div className="aware-calendar-empty">-</div>
                                ) : items.slice(0, 3).map((trig: any) => (
                                    <div key={trig.id} className="aware-calendar-event" data-tooltip={triggerTooltip(trig)} aria-label={triggerTooltip(trig)}>
                                        {renderTriggerDot(!trig.is_enabled, trig.is_enabled ? t('agent.aware.inProgress') : t('agent.aware.completed'))}
                                        <span className="aware-calendar-event-body">
                                            <span className="aware-calendar-event-title">{triggerTitle(trig)}</span>
                                            <span className="aware-calendar-event-meta">{triggerMeta(trig)}</span>
                                        </span>
                                    </div>
                                ))}
                                {items.length > 3 && <div className="aware-calendar-more">+{items.length - 3}</div>}
                            </div>
                        );
                    })}
                </div>
                {recurringTriggers.length > 0 && (
                    <div className="aware-calendar-recurring">
                        <div className="aware-side-subtitle">{isZh ? '重复计划' : 'Recurring'}</div>
                        {recurringTriggers.slice(0, 6).map((trig: any) => (
                            <div key={trig.id} className="aware-calendar-event recurring" data-tooltip={triggerTooltip(trig)} aria-label={triggerTooltip(trig)}>
                                {renderTriggerDot(!trig.is_enabled, trig.is_enabled ? t('agent.aware.inProgress') : t('agent.aware.completed'))}
                                <span className="aware-calendar-event-body">
                                    <span className="aware-calendar-event-title">{triggerTitle(trig)}</span>
                                    <span className="aware-calendar-event-meta">{triggerMeta(trig)}</span>
                                </span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        );
        const reflectionPreview = (msg: any) => {
            if (!msg) return '';
            if (msg.role === 'tool_call') {
                const name = msg.toolName || (() => { try { return JSON.parse(msg.content || '{}').name; } catch { return ''; } })() || 'tool';
                return isZh ? `调用工具：${name}` : `Tool call: ${name}`;
            }
            if (msg.role === 'tool_result') {
                const name = msg.toolName || (() => { try { return JSON.parse(msg.content || '{}').name; } catch { return ''; } })() || 'result';
                return isZh ? `工具结果：${name}` : `Tool result: ${name}`;
            }
            return String(msg.content || '').replace(/\s+/g, ' ').trim();
        };
        return (
            <div className="aware-side-preview">
                <div className="aware-side-section">
                    <div className="aware-side-title-row">
                        <div className="aware-side-section-title">{t('agent.aware.focus')}</div>
                        <div className="aware-view-switch">
                            <button
                                type="button"
                                className={`aware-view-button ${awareView === 'list' ? 'active' : ''}`}
                                onClick={() => setAwareView('list')}
                            >
                                {isZh ? '列表' : 'List'}
                            </button>
                            <button
                                type="button"
                                className={`aware-view-button ${awareView === 'calendar' ? 'active' : ''}`}
                                onClick={() => setAwareView('calendar')}
                            >
                                {isZh ? '日历' : 'Calendar'}
                            </button>
                        </div>
                    </div>
                    {awareView === 'calendar' ? renderCalendar() : (
                        displayFocusItems.length === 0 ? (
                            <div className="aware-side-empty">{t('agent.aware.focusEmpty')}</div>
                        ) : (
                            <>
                                {renderFocusGroup(isZh ? '进行中' : 'In progress', activeFocusItems)}
                                {renderFocusGroup(isZh ? '系统 Focus' : 'System Focus', systemFocusItems)}
                                {completedFocusItems.length > 0 && (
                                    <div className="aware-side-focus-group">
                                        <button
                                            type="button"
                                            className="aware-side-collapse"
                                            onClick={() => setShowCompletedFocus(!showCompletedFocus)}
                                        >
                                            <span>{showCompletedFocus ? (isZh ? '收起已完成' : 'Hide completed') : (isZh ? `已完成 (${completedFocusItems.length})` : `Completed (${completedFocusItems.length})`)}</span>
                                            <span className={`aware-side-chevron ${showCompletedFocus ? 'open' : ''}`}>▶</span>
                                        </button>
                                        {showCompletedFocus && completedFocusItems.slice(0, 12).map(renderFocusItem)}
                                    </div>
                                )}
                            </>
                        )
                    )}
                </div>
                <div className="aware-side-section">
                    <div className="aware-side-section-title">{t('agent.aware.reflections')}</div>
                    {(reflectionSessions as any[]).length === 0 ? (
                        <div className="aware-side-empty">{isZh ? '暂无自主思考记录' : 'No reflections yet'}</div>
                    ) : (reflectionSessions as any[]).slice(0, 10).map((session: any) => {
                        const isExpanded = expandedReflection === session.id;
                        const msgs = reflectionMessages[session.id] || [];
                        return (
                            <div key={session.id} className="aware-side-reflection">
                                <button
                                    type="button"
                                    className="aware-side-reflection-head"
                                    onClick={async () => {
                                        if (isExpanded) {
                                            setExpandedReflection(null);
                                            return;
                                        }
                                        setExpandedReflection(session.id);
                                        await loadReflectionMessages(session.id);
                                    }}
                                >
                                    <span className="aware-side-dot active" />
                                    <div className="aware-side-trigger-main">
                                        <div className="aware-side-item-title">{formatReflectionTitle(session.title, !!isZh)}</div>
                                        <div className="aware-side-item-meta">
                                            {new Date(session.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                                            {session.message_count > 0 ? ` · ${session.message_count}` : ''}
                                        </div>
                                    </div>
                                    <span className={`aware-side-chevron ${isExpanded ? 'open' : ''}`}>▶</span>
                                </button>
                                {isExpanded && (
                                    <div className="aware-side-reflection-detail">
                                        {msgs.length === 0 ? (
                                            <div className="aware-side-empty compact">{isZh ? '正在加载...' : 'Loading...'}</div>
                                        ) : msgs.map((msg: any, index: number) => {
                                            const isTool = msg.role === 'tool_call' || msg.role === 'tool_result';
                                            const toolName = isTool
                                                ? (msg.toolName || (() => { try { return JSON.parse(msg.content || '{}').name; } catch { return ''; } })() || 'tool')
                                                : '';
                                            const toolArgs = isTool
                                                ? (msg.toolArgs || (() => { try { return JSON.parse(msg.content || '{}').args; } catch { return {}; } })())
                                                : null;
                                            const toolResult = isTool ? (msg.toolResult || '') : '';
                                            const argsText = typeof toolArgs === 'string' ? toolArgs : JSON.stringify(toolArgs || {}, null, 2);
                                            const resultText = typeof toolResult === 'string' ? toolResult : JSON.stringify(toolResult, null, 2);
                                            const body = String(msg.content || '');
                                            return (
                                                <details key={index} className={`aware-side-reflection-message role-${msg.role}`}>
                                                    <summary style={{ display: 'flex', gap: '8px', alignItems: 'center', cursor: 'pointer', listStyle: 'none' } as any}>
                                                        <span className="aware-side-reflection-role">{msg.role}</span>
                                                        <span className="aware-side-reflection-text">{reflectionPreview(msg).slice(0, 180)}</span>
                                                    </summary>
                                                    <div style={{
                                                        marginTop: '6px',
                                                        paddingTop: '6px',
                                                        borderTop: '1px solid var(--border-subtle)',
                                                        whiteSpace: 'pre-wrap',
                                                        maxHeight: '260px',
                                                        overflow: 'auto',
                                                        fontFamily: isTool ? 'monospace' : undefined,
                                                        fontSize: isTool ? '10px' : '11px',
                                                        lineHeight: 1.5,
                                                        color: 'var(--text-secondary)',
                                                    }}>
                                                        {isTool ? (
                                                            <>
                                                                <div style={{ color: 'var(--text-tertiary)', marginBottom: '4px' }}>{toolName}</div>
                                                                <div style={{ color: 'var(--text-tertiary)', marginBottom: '4px' }}>{isZh ? '参数' : 'Arguments'}</div>
                                                                {argsText || '{}'}
                                                                {resultText && (
                                                                    <>
                                                                        <div style={{ borderTop: '1px dashed var(--border-subtle)', margin: '8px 0', opacity: 0.5 }} />
                                                                        <div style={{ color: 'var(--text-tertiary)', marginBottom: '4px' }}>{isZh ? '结果' : 'Result'}</div>
                                                                        {resultText}
                                                                    </>
                                                                )}
                                                            </>
                                                        ) : body}
                                                    </div>
                                                </details>
                                            );
                                        })}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>
        );
    };

    return (
        <>
            <div className={`agent-detail-page ${activeTab === 'chat' ? 'agent-detail-page--chat' : 'agent-detail-page--settings'}`}>
                {/* Header */}
                <div className="page-header agent-detail-header">
                    {activeTab === 'chat' ? <div
                        className="agent-detail-identity agent-detail-identity--compact"
                        onMouseEnter={clearCardCloseTimer}
                        onMouseLeave={scheduleCardClose}
                    >
                        <div className="agent-detail-identity-trigger">
                        <div className="agent-detail-avatar">{(Array.from(agent.name || 'A')[0] as string || 'A').toUpperCase()}</div>
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
                        </div>
                        <button
                            className={`agent-info-chevron${infoCardOpen ? ' agent-info-chevron--open' : ''}`}
                            onClick={e => { e.stopPropagation(); setInfoCardOpen(prev => !prev); }}
                            aria-label="Toggle agent info"
                        >
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6"/></svg>
                        </button>
                        </div>
                        {renderAgentInfoCard()}
                    </div> : <div />}
                    <div className="agent-detail-actions">
                        {activeTab === 'chat' && (
                            <>
                                <button
                                    className={`btn btn-ghost agent-top-action ${livePanelVisible && sidePanelTab === 'workspace' ? 'active' : ''}`}
                                    onClick={() => togglePreviewPanel('workspace')}
                                >
                                    <IconFolder size={16} stroke={1.7} />
                                    <span>{t('agent.tabs.workspace')}</span>
                                </button>
                                {(agent as any)?.agent_type !== 'openclaw' && (
                                    <button
                                        className={`btn btn-ghost agent-top-action ${livePanelVisible && sidePanelTab === 'aware' ? 'active' : ''}`}
                                        onClick={() => togglePreviewPanel('aware')}
                                    >
                                        <IconBrain size={16} stroke={1.7} />
                                        <span>{t('agent.tabs.aware')}</span>
                                    </button>
                                )}
                                <button
                                    className={`btn btn-ghost agent-top-action ${isSettingsRoute ? 'active' : ''}`}
                                    onClick={() => navigate(`/agents/${id}/settings`)}
                                >
                                    <IconSettings size={16} stroke={1.7} />
                                    <span>{t('agent.tabs.settings')}</span>
                                </button>
                            </>
                        )}
                        {activeTab === 'chat' && (agent as any)?.agent_type !== 'openclaw' && (
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
                {activeTab !== 'chat' && <div className="tabs">
                    {TABS.filter(tab => {
                        if (['aware', 'workspace', 'chat'].includes(tab)) return false;
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
                    <button className="btn btn-ghost agent-top-action agent-tabs-chat-action" onClick={() => setActiveTab('chat')}>
                        <IconMessageCircle size={16} stroke={1.7} />
                        <span>{t('agent.actions.chat')}</span>
                    </button>
                </div>}

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
                                <div className="card">
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>Cache Hit</div>
                                    <div style={{ fontSize: '22px', fontWeight: 600 }}>{formatTokens(cacheReadToday)}</div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                        Today {cacheHitRateToday}% · Month {formatTokens(cacheReadMonth)} ({cacheHitRateMonth}%)
                                    </div>
                                </div>
                                {/* Native agent metrics */}
                                {(agent as any)?.agent_type !== 'openclaw' && (<>
                                    <div className="card">
                                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>{t('agent.status.llmCallsToday')}</div>
                                        <div style={{ fontSize: '22px', fontWeight: 600 }}>{((agent as any).llm_calls_today || 0).toLocaleString()}</div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{t('agent.status.max')}: {((agent as any).max_llm_calls_per_day || 1000).toLocaleString()}</div>
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
                                <button className="btn btn-secondary" onClick={() => setActiveTab('settings')}>{t('agent.tabs.settings')}</button>
                            </div>
                        </div>
                    );
                })()}

                {/* ── Aware Tab ── */}
                {activeTab === 'aware' && (() => {
                    // Structured Focus items from the backend database
                    const focusItems = focusRecords.map(focusItemFromApi);
                    const isZh = i18n.language?.startsWith('zh');

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
                    const focusNames = new Set(focusItems.map((item) => item.name));
                    for (const trig of awareTriggers) {
                        if (trig.focus_ref && focusNames.has(trig.focus_ref)) {
                            if (!triggersByFocus[trig.focus_ref]) triggersByFocus[trig.focus_ref] = [];
                            triggersByFocus[trig.focus_ref].push(trig);
                        } else {
                            const synthetic = synthesizeFocusForTrigger(trig);
                            if (!triggersByFocus[synthetic.name]) triggersByFocus[synthetic.name] = [];
                            triggersByFocus[synthetic.name].push(trig);
                        }
                    }
                    const displayFocusItems = focusItems;

                    // Group activity logs by trigger name -> focus_ref
                    const triggerLogsByFocus: Record<string, any[]> = {};
                    const triggerNameToFocus: Record<string, string> = {};
                    for (const trig of awareTriggers) {
                        triggerNameToFocus[trig.name] = trig.focus_ref || focusKeyFromTrigger(trig);
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

                    const hasFocusItems = displayFocusItems.length > 0;

                    // Split focus items: active first, completed separately
                    const activeFocusItems = displayFocusItems.filter(f => !f.done && !f.system);
                    const systemFocusItems = displayFocusItems.filter(f => !f.done && f.system);
                    const completedFocusItems = displayFocusItems.filter(f => f.done);
                    const visibleActiveFocus = showAllFocus ? activeFocusItems : activeFocusItems.slice(0, SECTION_PAGE_SIZE);
                    const hiddenActiveCount = activeFocusItems.length - visibleActiveFocus.length;
                    const renderTriggerDot = (done: boolean, label: string) => (
                        <span className={`aware-side-status-dot ${done ? 'done' : 'active'}`} aria-label={label} />
                    );

                    // Render a focus item row
                    const renderFocusItem = (item: FocusItem) => {
                        const isExpanded = expandedFocusIds.has(item.id);
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
                                opacity: item.done ? 0.74 : 1,
                            }}>
                                {/* Focus Item Header */}
                                <div
                                    onClick={() => toggleExpandedFocus(item.id)}
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
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div style={{
                                            fontSize: '13px', fontWeight: 500, lineHeight: '20px',
                                            textDecoration: item.done ? 'line-through' : 'none',
                                            color: item.done ? 'var(--text-tertiary)' : 'var(--text-primary)',
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: '8px',
                                            flexWrap: 'wrap',
                                        }}>
                                            <span>{displayTitle}</span>
                                            {item.done && (
                                                <span className="aware-side-focus-badge done">
                                                    {t('agent.aware.completed')}
                                                </span>
                                            )}
                                        </div>
                                        {displaySubtitle && (
                                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'monospace', marginTop: '2px' }}>
                                                {displaySubtitle}
                                            </div>
                                        )}
                                    </div>
                                    {/* Trigger count badge */}
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
                                                        {renderTriggerDot(!trig.is_enabled, trig.is_enabled ? t('agent.aware.inProgress') : t('agent.aware.completed'))}
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
                                                        <span style={{ fontSize: '10px', color: trig.is_enabled ? 'var(--accent-primary)' : 'var(--success, #10b981)' }}>
                                                            {trig.is_enabled ? t('agent.aware.inProgress') : t('agent.aware.completed')}
                                                        </span>
                                                        <div style={{ display: 'flex', gap: '4px' }}>
                                                            {!trig.is_system && <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: '11px', color: 'var(--error)' }}
                                                                onClick={async (e) => {
                                                                    e.stopPropagation();
                                                                    const ok = await dialog.confirm(t('agent.aware.deleteTriggerConfirm', { name: trig.name }), { title: '删除触发器', danger: true, confirmLabel: '删除' });
                                                                    if (ok) {
                                                                        await triggerApi.delete(id!, trig.id);
                                                                        refetchTriggers();
                                                                    }
                                                                }}>
                                                                {t('common.delete', 'Delete')}
                                                            </button>}
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
                                                ? `${activeFocusItems.length} 个进行中${systemFocusItems.length > 0 ? ` · ${systemFocusItems.length} 个系统` : ''}${completedFocusItems.length > 0 ? ` · ${completedFocusItems.length} 个已完成` : ''}`
                                                : `${activeFocusItems.length} active${systemFocusItems.length > 0 ? ` · ${systemFocusItems.length} system` : ''}${completedFocusItems.length > 0 ? ` · ${completedFocusItems.length} done` : ''}`}
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

                                {/* System Focus Items */}
                                {systemFocusItems.length > 0 && (
                                    <div style={{ marginTop: '10px', paddingTop: '10px', borderTop: '1px solid var(--border-subtle)' }}>
                                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontWeight: 600, marginBottom: '6px' }}>
                                            {i18n.language?.startsWith('zh') ? '系统 Focus' : 'System Focus'}
                                        </div>
                                        {systemFocusItems.map(renderFocusItem)}
                                    </div>
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
                                                                await loadReflectionMessages(session.id);
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
                                                                    {formatReflectionTitle(session.title, !!isZh)}
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
                                                                                                {argsStr.replace(/\n/g, ' ').substring(0, 60)}{argsStr.length > 60 ? '...' : ''}
                                                                                            </span>
                                                                                        </summary>
                                                                                        <div style={{
                                                                                            padding: '8px 10px', borderTop: '1px solid var(--border-subtle)',
                                                                                            fontFamily: 'monospace', fontSize: '10px', lineHeight: 1.5,
                                                                                            whiteSpace: 'pre-wrap', maxHeight: '260px', overflow: 'auto',
                                                                                            color: 'var(--text-secondary)',
                                                                                        }}>
                                                                                            <div style={{ color: 'var(--text-tertiary)', marginBottom: '4px' }}>{isZh ? '参数' : 'Arguments'}</div>
                                                                                            {argsStr || '{}'}
                                                                                            {resultStr && (
                                                                                                <>
                                                                                                    <div style={{ borderTop: '1px dashed var(--border-subtle)', margin: '8px 0', opacity: 0.5 }} />
                                                                                                    <div style={{ color: 'var(--text-tertiary)', marginBottom: '4px' }}>{isZh ? '结果' : 'Result'}</div>
                                                                                                    {resultStr.substring(0, 1000)}
                                                                                                </>
                                                                                            )}
                                                                                        </div>
                                                                                    </details>
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
                                        <IconDna size={18} stroke={1.8} /> {t('agent.soul.title')}
                                    </h3>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                        {t('agent.mind.soulDesc', 'Core identity, personality, and behavior boundaries.')}
                                    </p>
                                    <FileBrowser api={adapter} singleFile="soul.md" title="" features={{ edit: (agent as any)?.access_level !== 'use' }} />
                                </div>

                                {/* Memory Section */}
                                <div>
                                    <h3 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        <IconBrain size={18} stroke={1.8} /> {t('agent.memory.title')}
                                    </h3>
                                    <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
                                        {t('agent.mind.memoryDesc', 'Persistent memory accumulated through conversations and experiences.')}
                                    </p>
                                    <FileBrowser api={adapter} rootPath="memory" readOnly features={{}} />
                                </div>

                                {/* Heartbeat Section */}
                                <div>
                                    <h3 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        <IconHeartbeat size={18} stroke={1.8} /> {t('agent.mind.heartbeatTitle', 'Heartbeat')}
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
                                                                    toast.success(`已安装 "${r.displayName || r.slug}"（${res.files_written} 个文件）`);
                                                                    queryClient.invalidateQueries({ queryKey: ['files', id, 'skills'] });
                                                                } catch (err: any) {
                                                                    await dialog.alert('安装失败', { type: 'error', details: String(err?.message || err) });
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
                                                            toast.success(`已导入 ${res.files_written} 个文件`);
                                                            queryClient.invalidateQueries({ queryKey: ['files', id, 'skills'] });
                                                            setShowAgentUrlImport(false);
                                                        } catch (err: any) {
                                                            await dialog.alert('导入失败', { type: 'error', details: String(err?.message || err) });
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
                                                                <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)' }}>{safeDisplayIcon(skill.icon, <IconTools size={20} stroke={1.8} />)}</span>
                                                                <div>
                                                                    <div style={{ fontWeight: 600, fontSize: '14px' }}>{skill.name}</div>
                                                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                                        {skill.description?.substring(0, 100)}{skill.description?.length > 100 ? '...' : ''}
                                                                    </div>
                                                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                                                                        <IconFolder size={12} stroke={1.8} /> {skill.folder_name}
                                                                        {skill.is_default && <span style={{ marginLeft: '8px', color: 'var(--accent-primary)', fontWeight: 600 }}>✓ Default</span>}
                                                                    </div>
                                                                </div>
                                                            </div>
                                                            <button
                                                                className="btn btn-secondary"
                                                                style={{ whiteSpace: 'nowrap', fontSize: '12px', padding: '6px 14px', display: 'inline-flex', alignItems: 'center', gap: '5px' }}
                                                                disabled={importingSkillId === skill.id}
                                                                onClick={async () => {
                                                                    setImportingSkillId(skill.id);
                                                                    try {
                                                                        const res = await fileApi.importSkill(id!, skill.id);
                                                                        toast.success(`已导入 "${skill.name}"（${res.files_written} 个文件）`);
                                                                        queryClient.invalidateQueries({ queryKey: ['files', id, 'skills'] });
                                                                        setShowImportSkillModal(false);
                                                                    } catch (err: any) {
                                                                        await dialog.alert('导入失败', { type: 'error', details: String(err?.message || err) });
                                                                    } finally {
                                                                        setImportingSkillId(null);
                                                                    }
                                                                }}
                                                            >
                                                                {importingSkillId === skill.id ? 'Importing...' : <><IconDownload size={13} stroke={1.8} /> Import</>}
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
                        <RelationshipEditor agentId={id!} readOnly={!canManage} />
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
                            className="agent-chat-shell"
                            style={{
                                display: 'flex',
                                gap: 0,
                                flex: 1,
                                minHeight: 0,
                                height: 'calc(100vh - 100px)',
                                margin: '0 8px 8px',
                                border: '1px solid rgba(0, 0, 0, 0.06)',
                                borderRadius: '12px',
                                overflow: 'hidden',
                                boxShadow: '0 2px 8px rgba(0, 0, 0, 0.04)',
                            }}
                        >
                            {/* ── Left: session sidebar ── */}
                            <div className={`session-sidebar ${sessionListCollapsed ? 'collapsed' : ''}`} style={{ width: sessionListCollapsed ? '0px' : '220px', transition: 'width 0.2s ease', flexShrink: 0, minHeight: 0, borderRight: sessionListCollapsed ? 'none' : '1px solid var(--border-subtle)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                                {/* ── Header: scope dropdown + collapse ── */}
                                <div style={{ flexShrink: 0 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '4px', padding: '10px 8px 8px 12px', minHeight: '40px', boxSizing: 'border-box' }}>
                                        {canViewAllAgentChatSessions ? (
                                            <div className="scope-dropdown" ref={scopeDropdownRef}>
                                                <button
                                                    className="scope-dropdown-trigger"
                                                    onClick={() => {
                                                        const nextOpen = !scopeDropdownOpen;
                                                        setScopeDropdownOpen(nextOpen);
                                                        if (nextOpen && !allSessions.length) fetchAllSessions();
                                                    }}
                                                >
                                                    <span className="scope-dropdown-label">
                                                        {chatScope === 'mine'
                                                            ? t('agent.chat.mySessions')
                                                            : t('agent.chat.otherSessions', '其他会话')
                                                        }
                                                    </span>
                                                    <svg className={`scope-dropdown-chevron${scopeDropdownOpen ? ' scope-dropdown-chevron--open' : ''}`} width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6"/></svg>
                                                </button>
                                                {scopeDropdownOpen && (
                                                    <div className="scope-dropdown-menu">
                                                        <div
                                                            className={`scope-dropdown-item${chatScope === 'mine' ? ' scope-dropdown-item--active' : ''}`}
                                                            onClick={() => { onAdminTabMine(); setScopeDropdownOpen(false); }}
                                                        >{t('agent.chat.mySessions')}</div>
                                                        <div
                                                            className={`scope-dropdown-item${chatScope === 'all' ? ' scope-dropdown-item--active' : ''}`}
                                                            onClick={() => { onAdminTabOthers(); setScopeDropdownOpen(false); }}
                                                        >{t('agent.chat.otherSessions', '其他会话')}</div>
                                                    </div>
                                                )}
                                            </div>
                                        ) : (
                                            <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', lineHeight: '1.25', flex: 1, minWidth: 0 }}>
                                                {t('agent.chat.mySessions')}
                                            </span>
                                        )}
                                        {!sessionListCollapsed && (
                                            <button
                                                type="button"
                                                onClick={() => setSessionListCollapsed(true)}
                                                className="session-sidebar-toggle-btn"
                                                title={t('agent.chat.collapseSidebar')}
                                            >
                                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg>
                                            </button>
                                        )}
                                    </div>
                                    {(!canViewAllAgentChatSessions || chatScope === 'mine') && (
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

                                <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                                    {(!canViewAllAgentChatSessions || chatScope === 'mine') ? (
                                        <>
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
                                        <button onClick={() => setSessionListCollapsed(false)} className="session-sidebar-toggle-btn session-sidebar-toggle-btn--floating" title="Show chat sessions">
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg>
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
                                            {activeSession.source_channel === 'agent' ? (
                                                <><IconRobot size={13} stroke={1.8} /> Agent Conversation · {activeSession.username || 'Agents'}</>
                                            ) : (
                                                <>Read-only · {activeSession.username || 'User'}</>
                                            )}
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
                                                                        <IconBolt size={13} stroke={1.8} />
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
                                                                <ThoughtDisclosure key={i} content={m.thinking} t={t} />
                                                            );
                                                        }
                                                        return null;
                                                    }
                                                    return (
                                                        <React.Fragment key={i}>
                                                            {m.role === 'assistant' && m.thinking && (
                                                                <ThoughtDisclosure content={m.thinking} t={t} />
                                                            )}
                                                            <ChatMessageItem
                                                                msg={{ ...m, thinking: undefined }}
                                                                i={i}
                                                                isLeft={isLeft}
                                                                t={t}
                                                                senderLabel={isHumanReadonly ? (isLeft ? ((agent as any)?.name || 'Agent') : (activeSession.username || 'User')) : undefined}
                                                                avatarText={isHumanReadonly ? (isLeft ? (((agent as any)?.name || 'Agent')[0]) : ((activeSession.username || 'User')[0])) : undefined}
                                                                forceSenderLabel={isHumanReadonly}
                                                            />
                                                        </React.Fragment>
                                                    );
                                                });
                                            })()}
                                        </div>
                                        {showHistoryScrollBtn && (
                                            <button onClick={scrollHistoryToBottom} className="chat-scroll-btn" style={{ bottom: '20px' }} title="Scroll to bottom">↓</button>
                                        )}
                                    </>
                                ) : (
                                    /* ── Live WebSocket chat (own session) ── */
                                    <div {...chatDropProps} style={{ flex: 1, display: 'flex', flexDirection: 'column', position: 'relative', minHeight: 0, overflow: 'hidden' }}>
                                        {/* Drop overlay */}
                                        {isChatDragging && (
                                            <div className="drop-zone-overlay">
                                                <div className="drop-zone-overlay__icon"><IconPaperclip size={28} stroke={1.8} /></div>
                                                <div className="drop-zone-overlay__text">{t('agent.upload.dropToAttach', 'Drop files to attach (max 10)')}</div>
                                            </div>
                                        )}
                                        <div
                                            ref={chatContainerRef}
                                            onScroll={handleChatScroll}
                                            onWheelCapture={handleChatWheelCapture}
                                            onTouchStartCapture={handleChatTouchStartCapture}
                                            onTouchMoveCapture={handleChatTouchMoveCapture}
                                            style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}
                                        >
                                            {chatMessages.length === 0 && (
                                                <div className="chat-empty-state">
                                                    <div className="chat-empty-state__title">{activeSession?.title || t('agent.chat.startChat')}</div>
                                                    <div className="chat-empty-state__subtitle">{t('agent.chat.startConversation', { name: agent.name })}</div>
                                                    <div className="chat-empty-state__hint">{t('agent.chat.fileSupport')}</div>
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
                                                            if (msg.toolThinking?.trim()) {
                                                                const lastItem = currentGroup[currentGroup.length - 1];
                                                                if (!(lastItem?.type === 'thinking' && lastItem.content === msg.toolThinking)) {
                                                                    currentGroup.push({ type: 'thinking', content: msg.toolThinking });
                                                                }
                                                            }
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
                                                        if (msg.role === 'assistant' && msg.thinking && currentGroup?.some(item => item.type === 'tool')) {
                                                            currentGroup.push({ type: 'thinking', content: msg.thinking });
                                                            const contentText = msg.content?.trim() || '';
                                                            flushGroup();
                                                            if (contentText) grouped.push({ type: 'msg', msg: { ...msg, thinking: undefined }, i });
                                                            continue;
                                                        }
                                                        flushGroup();
                                                        grouped.push({ type: 'msg', msg, i });
                                                    }
                                                }
                                                flushGroup(); // flush any trailing group


                                                return grouped.map((entry, entryIdx) => {
                                                    const previousEntry = grouped[entryIdx - 1];
                                                    const hideAssistantAvatar = entry.type === 'msg'
                                                        && entry.msg.role === 'assistant'
                                                        && previousEntry?.type === 'analysis_group';
                                                    if (entry.type === 'analysis_group') {
                                                        // Group is considered running if it has a running tool,
                                                        // or if it's the very last entry and the agent is still active
                                                        const isLastEntry = entryIdx === grouped.length - 1;
                                                        const hasRunningTool = entry.items.some(
                                                            it => it.type === 'tool' && it.status === 'running'
                                                        );
                                                        const hasToolItems = entry.items.some(it => it.type === 'tool');
                                                        const groupIsRunning = hasRunningTool || (!hasToolItems && isLastEntry && (isWaiting || isStreaming));
                                                        return (
                                                            <div key={`ag-${entry.key}`} className="chat-msg-row chat-msg-row--analysis">
                                                                <div className="chat-msg-avatar">{(((agent as any)?.name || 'Agent')[0])}</div>
                                                                <AnalysisCard
                                                                    items={entry.items}
                                                                    t={t}
                                                                    expanded={toolGroupExpandedRef.current.has(entry.key) ? !!toolGroupExpandedRef.current.get(entry.key) : false}
                                                                    onToggle={() => toggleToolGroup(entry.key)}
                                                                    isGroupRunning={groupIsRunning}
                                                                />
                                                            </div>
                                                        );
                                                    }
                                                    const { msg, i } = entry;
                                                    // All remaining messages have real content; render as chat bubbles
                                                    if (msg.role === 'assistant' && msg.thinking) {
                                                        const contentText = msg.content?.trim() || '';
                                                        return (
                                                            <React.Fragment key={i}>
                                                                <ThoughtDisclosure
                                                                    content={msg.thinking}
                                                                    t={t}
                                                                    streaming={!!((msg as any)._streaming && !contentText)}
                                                                />
                                                                {contentText && (
                                                                    <ChatMessageItem
                                                                        msg={{ ...msg, thinking: undefined }}
                                                                        i={i}
                                                                        isLeft
                                                                        t={t}
                                                                        senderLabel={(agent as any)?.name || 'Agent'}
                                                                        avatarText={((agent as any)?.name || 'Agent')[0]}
                                                                        hideAvatar={hideAssistantAvatar}
                                                                    />
                                                                )}
                                                            </React.Fragment>
                                                        );
                                                    }
                                                    return (
                                                        <ChatMessageItem
                                                            key={i}
                                                            msg={msg}
                                                            i={i}
                                                            isLeft={msg.role === 'assistant'}
                                                            t={t}
                                                            senderLabel={msg.role === 'assistant' ? ((agent as any)?.name || 'Agent') : (currentUser?.display_name || undefined)}
                                                            avatarText={msg.role === 'assistant' ? (((agent as any)?.name || 'Agent')[0]) : (currentUser?.display_name?.[0] || undefined)}
                                                            hideAvatar={hideAssistantAvatar}
                                                        />
                                                    );
                                                });
                                            })()
                                            }
                                            {isWaiting && (
                                                <div className="chat-msg-row">
                                                    <div className="chat-msg-avatar">A</div>
                                                    <div className="chat-msg-bubble chat-msg-bubble--thinking">
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
                                            <button onClick={scrollToBottom} className="chat-scroll-btn" style={{ bottom: `${chatScrollBtnBottom}px` }} title="Scroll to bottom">↓</button>
                                        )}
                                        {/* Transient info banner — e.g. fallback model switch */}
                                        {chatInfoMsg && (
                                            <div style={{ padding: '6px 14px', borderTop: '1px solid var(--border-subtle)', background: 'var(--bg-secondary)', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: 'var(--text-secondary)', animation: 'fadeIn 0.2s ease' }}>
                                                <span style={{ opacity: 0.7 }}>ℹ️</span>
                                                <span style={{ flex: 1 }}>{chatInfoMsg}</span>
                                                <button onClick={() => setChatInfoMsg(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: '14px', lineHeight: 1, padding: '0 2px' }}>✕</button>
                                            </div>
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
                                                <ModelSwitcher
                                                    value={overrideModelId}
                                                    onChange={handleModelChange}
                                                    tenantDefaultId={myTenant?.default_model_id || null}
                                                    disabled={!wsConnected}
                                                />
                                                <div style={{ flex: 1 }} />
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
                                    onToggle={() => setLivePanelVisible(false)}
                                    activeTab={sidePanelTab}
                                    onTabChange={setSidePanelTab}
                                    awareContent={renderAwarePreview()}
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

                        const filterBtn = (key: string, label: React.ReactNode, indent = false) => (
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
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: '5px',
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
                                    {filterBtn('user', <><IconUser size={13} stroke={1.8} /> {t('agent.activityLog.userActions', 'User Actions')}</>)}
                                    {(agent as any)?.agent_type !== 'openclaw' && (<>
                                        {filterBtn('backend', <><IconSettings size={13} stroke={1.8} /> {t('agent.activityLog.backendServices', 'Backend Services')}</>)}
                                        {(logFilter === 'backend' || logFilter === 'heartbeat' || logFilter === 'schedule' || logFilter === 'messages') && (
                                            <>
                                                <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>│</span>
                                                {filterBtn('heartbeat', <><IconHeartbeat size={13} stroke={1.8} /> {t('agent.mind.heartbeatTitle')}</>)}
                                                {filterBtn('schedule', <><IconClock size={13} stroke={1.8} /> {t('agent.activityLog.scheduleCron')}</>, true)}
                                                {filterBtn('messages', <><IconMailForward size={13} stroke={1.8} /> {t('agent.activityLog.messages')}</>, true)}
                                            </>
                                        )}
                                    </>)}
                                </div>

                                {filteredLogs.length > 0 ? (
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                        {filteredLogs.map((log: any) => {
                                            const icons: Record<string, React.ReactNode> = {
                                                chat_reply: <IconMessageCircle size={16} stroke={1.8} />,
                                                tool_call: <IconBolt size={16} stroke={1.8} />,
                                                feishu_msg_sent: <IconSend size={16} stroke={1.8} />,
                                                agent_msg_sent: <IconRobot size={16} stroke={1.8} />,
                                                web_msg_sent: <IconWorld size={16} stroke={1.8} />,
                                                task_created: <IconFileText size={16} stroke={1.8} />,
                                                task_updated: <IconCheck size={16} stroke={1.8} />,
                                                file_written: <IconFileText size={16} stroke={1.8} />,
                                                error: <IconAlertTriangle size={16} stroke={1.8} />,
                                                schedule_run: <IconClock size={16} stroke={1.8} />,
                                                heartbeat: <IconHeartbeat size={16} stroke={1.8} />,
                                                plaza_post: <IconBuilding size={16} stroke={1.8} />,
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
                                                        <span style={{ width: '18px', height: '18px', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: '1px', color: 'var(--text-tertiary)' }}>
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
                            const { data: approvals = [], refetch: refetchApprovals } = useQuery({
                                queryKey: ['agent-approvals', id],
                                queryFn: () => fetchAuth<any[]>(`/agents/${id}/approvals`),
                                enabled: !!id,
                                refetchInterval: 15000,
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
                            const statusStyle = (s: string) => ({
                                padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600,
                                background: s === 'approved' ? 'rgba(0,180,120,0.12)' : s === 'rejected' ? 'rgba(255,80,80,0.12)' : 'rgba(255,180,0,0.12)',
                                color: s === 'approved' ? 'var(--success)' : s === 'rejected' ? 'var(--error)' : 'var(--warning)',
                            });
                            return (
                                <div style={{ padding: '20px 24px' }}>
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
                                <div className="agent-settings-savebar">
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
                                                            {m.label || m.model}
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
                                                            {m.label || m.model}
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
                                        <h4 style={{ marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '6px' }}><IconTools size={16} stroke={1.8} /> {t('agent.settings.maxToolRounds', 'Max Tool Call Rounds')}</h4>
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
                                <AccessPermissionsPanel
                                    agentId={id!}
                                    permData={permData}
                                    canManage={canManage}
                                    queryClient={queryClient}
                                />

                                {/* Timezone */}
                                <div className="card" style={{ marginBottom: '12px' }}>
                                    <h4 style={{ marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        <IconWorld size={16} stroke={1.8} /> {t('agent.settings.timezone.title', 'Timezone')}
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
                                                        await dialog.alert('删除数字员工失败', { type: 'error', details: String(err?.message || err) });
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
                    <div className="agent-expiry-modal-backdrop"
                        onClick={() => setShowExpiryModal(false)}>
                        <div className="agent-expiry-modal"
                            onClick={e => e.stopPropagation()}>
                            <div className="agent-expiry-modal-header">
                                <div>
                                    <h3>{t('agent.settings.expiry.title')}</h3>
                                    <div className="agent-expiry-current">
                                        {(agent as any).is_expired
                                            ? <span className="agent-expiry-status agent-expiry-status--expired">{t('agent.settings.expiry.expired')}</span>
                                            : (agent as any).expires_at
                                                ? <>{t('agent.settings.expiry.currentExpiry')} <strong>{new Date((agent as any).expires_at).toLocaleString(i18n.language === 'zh' ? 'zh-CN' : 'en-US')}</strong></>
                                                : <span className="agent-expiry-status">{t('agent.settings.expiry.neverExpires')}</span>
                                        }
                                    </div>
                                </div>
                                <button className="agent-expiry-close" onClick={() => setShowExpiryModal(false)} aria-label={t('common.close', 'Close')}>×</button>
                            </div>
                            <div className="agent-expiry-section">
                                <div className="agent-expiry-label">{t('agent.settings.expiry.quickRenew')}</div>
                                <div className="agent-expiry-quick-actions">
                                    {([
                                        ['+ 24h', 24],
                                        [`+ ${t('agent.settings.expiry.days', { count: 7 })}`, 168],
                                        [`+ ${t('agent.settings.expiry.days', { count: 30 })}`, 720],
                                        [`+ ${t('agent.settings.expiry.days', { count: 90 })}`, 2160],
                                    ] as [string, number][]).map(([label, h]) => (
                                        <button key={h} onClick={() => addHours(h)}
                                            className={`agent-expiry-chip${expiryQuickHours === h ? ' agent-expiry-chip--selected' : ''}`}
                                            aria-pressed={expiryQuickHours === h}>
                                            {label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                            <div className="agent-expiry-section">
                                <div className="agent-expiry-label">{t('agent.settings.expiry.customDeadline')}</div>
                                <input type="datetime-local" value={expiryValue} onChange={e => {
                                    setExpiryValue(e.target.value);
                                    setExpiryQuickHours(null);
                                }}
                                    className="agent-expiry-input" />
                            </div>
                            <div className="agent-expiry-actions">
                                <button onClick={() => saveExpiry(true)} disabled={expirySaving}
                                    className="agent-expiry-secondary-action">
                                    {t('agent.settings.expiry.neverExpires')}
                                </button>
                                <div className="agent-expiry-action-group">
                                    <button onClick={() => setShowExpiryModal(false)} disabled={expirySaving}
                                        className="agent-expiry-secondary-action">
                                        {t('common.cancel')}
                                    </button>
                                    <button onClick={() => saveExpiry(false)} disabled={expirySaving || !expiryValue}
                                        className="agent-expiry-primary-action">
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
