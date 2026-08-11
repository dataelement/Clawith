import React, { useState, useEffect, useCallback } from 'react';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
    ResponsiveContainer, PieChart, Pie, Cell, BarChart, Bar,
    Legend,
} from 'recharts';
import i18n from '../i18n';

// ─── 页面级翻译系统 ──────────────────────────────────

// 翻译资源
const TRANSLATIONS = {
    en: {
        loading: 'Loading...',
        noData: 'No data',
        cumulative: 'Cumulative',
        new: 'New',
        last7Days: 'Last 7 Days',
        last30Days: 'Last 30 Days',
        avgTokensPerSession: 'Avg Tokens / Session',
        avgTokensPerSessionTooltip: 'Average token consumption per chat session in the last 30 days. Calculated as total tokens / total sessions.',
        retention7Day: '7-Day Retention',
        retentionTooltip: 'Percentage of established companies (>14 days old) that were active last week and remain active this week. {retainedInfo}',
        companies: 'Companies',
        companiesTooltip: 'Cumulative and daily new company registrations',
        users: 'Users',
        usersTooltip: 'Cumulative and daily new user registrations',
        tokenUsage: 'Token Usage',
        tokenUsageTooltip: 'Cumulative and daily token consumption across all agents',
        dailySessions: 'Daily Sessions',
        dailySessionsTooltip: 'Number of new chat sessions created per day and cumulative total',
        activeUsers: 'Active Users',
        activeUsersTooltip: 'DAU: distinct users who sent at least 1 message that day. WAU: distinct users active in a rolling 7-day window. MAU: distinct users active in a rolling 30-day window.',
        dau: 'DAU',
        wau: 'WAU',
        mau: 'MAU',
        channelDistribution: 'Channel Distribution',
        channelDistributionTooltip: 'Distribution of chat sessions by source channel in the last 30 days',
        top10ToolCategories: 'Top 10 Tool Categories',
        top10ToolCategoriesTooltip: 'Most popular tool categories across all active agent configurations',
        enabled: 'Enabled',
        churnWarning: 'Churn Warning',
        churnWarningTooltip: 'Companies that consumed >10M tokens but have had no activity in the past 14 days',
        company: 'Company',
        totalTokens: 'Total Tokens',
        lastActive: 'Last Active',
        daysInactive: 'Days Inactive',
        never: 'Never',
        noChurnWarnings: 'No churn warnings — all active companies are healthy',
        top20CompaniesByTokens: 'Top 20 Companies by Tokens',
        top20CompaniesByTokensTooltip: 'Companies ranked by total cumulative token consumption across all their agents',
        cache: 'Cache',
        top20AgentsByTokens: 'Top 20 Agents by Tokens',
        top20AgentsByTokensTooltip: 'Individual agents ranked by total cumulative token consumption',
    },
    zh: {
        loading: '加载中...',
        noData: '暂无数据',
        cumulative: '累计',
        new: '新增',
        last7Days: '最近7天',
        last30Days: '最近30天',
        avgTokensPerSession: '平均每会话Token',
        avgTokensPerSessionTooltip: '过去30天每个聊天会话的平均Token消耗。计算方式为总Token / 总会话数。',
        retention7Day: '7日留存',
        retentionTooltip: '成立超过14天的公司中，上周活跃且本周仍活跃的比例。{retainedInfo}',
        companies: '公司',
        companiesTooltip: '累计和每日新增公司注册数',
        users: '用户',
        usersTooltip: '累计和每日新增用户注册数',
        tokenUsage: 'Token使用',
        tokenUsageTooltip: '所有Agent的累计和每日Token消耗',
        dailySessions: '每日会话',
        dailySessionsTooltip: '每日新建聊天会话数和累计总数',
        activeUsers: '活跃用户',
        activeUsersTooltip: 'DAU：当天至少发送1条消息的独立用户。WAU：滚动7天窗口内活跃的独立用户。MAU：滚动30天窗口内活跃的独立用户。',
        dau: '日活',
        wau: '周活',
        mau: '月活',
        channelDistribution: '渠道分布',
        channelDistributionTooltip: '过去30天按来源渠道划分的聊天会话分布',
        top10ToolCategories: 'Top 10工具分类',
        top10ToolCategoriesTooltip: '所有活跃Agent配置中最受欢迎的工具分类',
        enabled: '已启用',
        churnWarning: '流失预警',
        churnWarningTooltip: '消费超过1000万Token但过去14天没有活动的公司',
        company: '公司',
        totalTokens: '总Token',
        lastActive: '最后活跃',
        daysInactive: '无活动天数',
        never: '从未',
        noChurnWarnings: '无流失预警 — 所有活跃公司状态良好',
        top20CompaniesByTokens: 'Token Top 20公司',
        top20CompaniesByTokensTooltip: '按所有Agent的累计Token消耗排名的公司',
        cache: '缓存',
        top20AgentsByTokens: 'Token Top 20 Agent',
        top20AgentsByTokensTooltip: '按累计Token消耗排名的单个Agent',
    },
};

// 获取当前语言（标准化）
function getNormalizedLanguage(): 'en' | 'zh' {
    const currentLng = i18n.language;
    if (currentLng?.startsWith('zh')) return 'zh';
    return 'en';
}

// 翻译钩子 - 直接使用项目i18n实例
function usePlatformTranslations() {
    const [lang, setLangState] = useState<'en' | 'zh'>(getNormalizedLanguage);

    useEffect(() => {
        // 监听i18n语言变化事件
        const handleLanguageChanged = () => {
            setLangState(getNormalizedLanguage());
        };
        
        i18n.on('languageChanged', handleLanguageChanged);
        
        return () => {
            i18n.off('languageChanged', handleLanguageChanged);
        };
    }, []);

    const t = useCallback((key: string, params?: Record<string, string | number>) => {
        const text = TRANSLATIONS[lang][key as keyof typeof TRANSLATIONS.en] ?? key;
        if (!params) return text;
        return Object.entries(params).reduce(
            (acc, [k, v]) => acc.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v)),
            text
        );
    }, [lang]);

    return { t, lang };
}

// ─── Helpers ───────────────────────────────────────────────

function formatTokens(n: number | null | undefined): string {
    if (n == null) return '-';
    if (n < 1000) return String(n);
    if (n < 1_000_000) return (n / 1000).toFixed(n < 10_000 ? 1 : 0) + 'K';
    if (n < 1_000_000_000) return (n / 1_000_000).toFixed(n < 10_000_000 ? 1 : 0) + 'M';
    return (n / 1_000_000_000).toFixed(1) + 'B';
}

function formatNumber(n: number | null | undefined): string {
    if (n == null) return '-';
    if (n < 1000) return String(n);
    return n.toLocaleString();
}

// Color palette for pie/bar charts
const CHART_COLORS = [
    '#3b82f6', '#10b981', '#8b5cf6', '#f59e0b', '#ef4444',
    '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1',
];

// ─── InfoTooltip ─────────────────────────────────────────

/**
 * Small (i) icon that shows a tooltip on hover.
 * Pure CSS — no external tooltip library needed.
 */
const InfoTooltip = ({ text }: { text: string }) => (
    <span style={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        cursor: 'help',
        marginLeft: '4px',
    }}>
        <svg
            width="14" height="14" viewBox="0 0 16 16" fill="none"
            style={{ opacity: 0.4 }}
        >
            <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" />
            <text x="8" y="12" textAnchor="middle" fontSize="10" fill="currentColor" fontWeight="600">i</text>
        </svg>
        <span className="info-tooltip-popup">{text}</span>
    </span>
);

// Inject tooltip CSS once (hover-based, no JS state)
const tooltipStyleId = '__info-tooltip-style';
if (typeof document !== 'undefined' && !document.getElementById(tooltipStyleId)) {
    const style = document.createElement('style');
    style.id = tooltipStyleId;
    style.textContent = `
        .info-tooltip-popup {
            visibility: hidden;
            opacity: 0;
            position: absolute;
            bottom: calc(100% + 8px);
            left: 50%;
            transform: translateX(-50%);
            background: var(--bg-elevated, #1e1e2e);
            color: var(--text-secondary, #ccc);
            border: 1px solid var(--border-subtle, #333);
            border-radius: 8px;
            padding: 8px 12px;
            font-size: 11px;
            line-height: 1.5;
            white-space: normal;
            width: 240px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.25);
            transition: opacity 0.15s, visibility 0.15s;
            z-index: 1000;
            pointer-events: none;
        }
        span:hover > .info-tooltip-popup {
            visibility: visible;
            opacity: 1;
        }
    `;
    document.head.appendChild(style);
}

// ─── MetricCard ────────────────────────────────────────────

const MetricCard = ({ label, value, tooltip }: { label: string; value: string; tooltip: string }) => (
    <div className="card" style={{
        flex: 1,
        minWidth: '200px',
        padding: '20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
    }}>
        <div style={{
            display: 'flex',
            alignItems: 'center',
            fontSize: '12px',
            fontWeight: 600,
            color: 'var(--text-tertiary)',
            textTransform: 'uppercase',
            letterSpacing: '0.5px',
        }}>
            {label}
            <InfoTooltip text={tooltip} />
        </div>
        <div style={{
            fontSize: '28px',
            fontWeight: 700,
            color: 'var(--text-primary)',
            fontFamily: 'var(--font-mono, monospace)',
        }}>
            {value}
        </div>
    </div>
);

// ─── Main Component ──────────────────────────────────────

export default function PlatformDashboard() {
    const { t } = usePlatformTranslations();
    const [timeRange, setTimeRange] = useState<30 | 7>(30);
    const [loadingStats, setLoadingStats] = useState(false);
    const [loadingLeaders, setLoadingLeaders] = useState(false);
    const [loadingEnhanced, setLoadingEnhanced] = useState(false);

    const [timeSeriesData, setTimeSeriesData] = useState<any[]>([]);
    const [topCompanies, setTopCompanies] = useState<any[]>([]);
    const [topAgents, setTopAgents] = useState<any[]>([]);
    const [enhanced, setEnhanced] = useState<any>(null);

    const authHeaders = () => {
        const token = localStorage.getItem('token');
        return { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) };
    };

    const fetchTimeSeries = async (days: number) => {
        setLoadingStats(true);
        try {
            const end = new Date();
            const start = new Date();
            start.setDate(start.getDate() - days);
            const res = await fetch(
                `/api/admin/metrics/timeseries?start_date=${start.toISOString()}&end_date=${end.toISOString()}`,
                { headers: authHeaders() }
            );
            if (res.ok) setTimeSeriesData(await res.json());
        } catch (e) { console.error('Failed to load metrics:', e); }
        setLoadingStats(false);
    };

    const fetchLeaderboards = async () => {
        setLoadingLeaders(true);
        try {
            const res = await fetch('/api/admin/metrics/leaderboards', { headers: authHeaders() });
            if (res.ok) {
                const data = await res.json();
                setTopCompanies(data.top_companies || []);
                setTopAgents(data.top_agents || []);
            }
        } catch (e) { console.error('Failed to load leaderboards:', e); }
        setLoadingLeaders(false);
    };

    const fetchEnhanced = async () => {
        setLoadingEnhanced(true);
        try {
            const res = await fetch('/api/admin/metrics/enhanced', { headers: authHeaders() });
            if (res.ok) setEnhanced(await res.json());
        } catch (e) { console.error('Failed to load enhanced metrics:', e); }
        setLoadingEnhanced(false);
    };

    useEffect(() => { fetchTimeSeries(timeRange); }, [timeRange]);
    useEffect(() => { fetchLeaderboards(); fetchEnhanced(); }, []);

    // ─── Chart Tooltip ────────────────────────────────────

    const CustomTooltip = ({ active, payload, label }: any) => {
        if (active && payload && payload.length) {
            return (
                <div style={{
                    background: 'var(--bg-secondary)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '8px',
                    padding: '12px',
                    boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                    fontSize: '12px'
                }}>
                    <div style={{ fontWeight: 600, marginBottom: '8px', color: 'var(--text-secondary)' }}>{label}</div>
                    {payload.map((p: any, i: number) => (
                        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                            <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: p.stroke || p.fill }} />
                            <span style={{ color: 'var(--text-tertiary)' }}>{
                                p.name === 'Cumulative' ? t('cumulative') : 
                                p.name === 'New' ? t('new') : 
                                p.name === 'DAU' ? t('dau') : 
                                p.name === 'WAU' ? t('wau') : 
                                p.name === 'MAU' ? t('mau') : 
                                p.name === 'Enabled' ? t('enabled') : 
                                p.name
                            }:</span>
                            <span style={{ fontWeight: 500 }}>{p.dataKey?.includes('tokens') ? formatTokens(p.value) : formatNumber(p.value)}</span>
                        </div>
                    ))}
                </div>
            );
        }
        return null;
    };

    // ─── Chart Cards ─────────────────────────────────────

    const ChartCard = ({ title, tooltip, dataKeyTotal, dataKeyNew, color }: {
        title: string; tooltip: string; dataKeyTotal: string; dataKeyNew: string; color: string;
    }) => (
        <div className="card" style={{ flex: 1, minWidth: '300px', padding: '20px' }}>
            <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '20px', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center' }}>
                {title}
                <InfoTooltip text={tooltip} />
            </div>
            <div style={{ height: '240px', width: '100%' }}>
                {loadingStats ? (
                    <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: '12px' }}>{t('loading')}</div>
                ) : (
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={timeSeriesData} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-subtle)" />
                            <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--text-tertiary)' }} tickLine={false} axisLine={false} tickFormatter={(val) => val.substring(5)} />
                            <YAxis yAxisId="left" tick={{ fontSize: 10, fill: 'var(--text-tertiary)' }} tickLine={false} axisLine={false} tickFormatter={formatTokens} />
                            <Tooltip content={<CustomTooltip />} />
                            <Line yAxisId="left" type="monotone" dataKey={dataKeyTotal} name="Cumulative" stroke={color} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                            <Line yAxisId="left" type="monotone" dataKey={dataKeyNew} name="New" stroke={color} opacity={0.3} strokeWidth={2} dot={false} strokeDasharray="4 4" />
                        </LineChart>
                    </ResponsiveContainer>
                )}
            </div>
        </div>
    );

    // ─── Multi-Line Chart (for Active Users DAU/WAU/MAU) ─

    const MultiLineChart = ({ title, tooltip, lines }: {
        title: string; tooltip: string;
        lines: { key: string; name: string; color: string }[];
    }) => (
        <div className="card" style={{ flex: 1, minWidth: '300px', padding: '20px' }}>
            <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '20px', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center' }}>
                {title}
                <InfoTooltip text={tooltip} />
            </div>
            <div style={{ height: '240px', width: '100%' }}>
                {loadingStats ? (
                    <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: '12px' }}>{t('loading')}</div>
                ) : (
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={timeSeriesData} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-subtle)" />
                            <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--text-tertiary)' }} tickLine={false} axisLine={false} tickFormatter={(val) => val.substring(5)} />
                            <YAxis tick={{ fontSize: 10, fill: 'var(--text-tertiary)' }} tickLine={false} axisLine={false} />
                            <Tooltip content={<CustomTooltip />} />
                            <Legend iconSize={8} wrapperStyle={{ fontSize: '11px' }} />
                            {lines.map((l) => {
                                // 翻译图例名称
                                let displayName = l.name;
                                if (l.name === 'DAU') displayName = t('dau');
                                if (l.name === 'WAU') displayName = t('wau');
                                if (l.name === 'MAU') displayName = t('mau');
                                return (
                                    <Line key={l.key} type="monotone" dataKey={l.key} name={displayName} stroke={l.color} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                                );
                            })}
                        </LineChart>
                    </ResponsiveContainer>
                )}
            </div>
        </div>
    );

    // ─── Channel Pie Chart ──────────────────────────────────

    const ChannelPieChart = () => {
        const data = enhanced?.channel_distribution || [];
        const total = data.reduce((s: number, d: any) => s + d.count, 0);
        return (
            <div className="card" style={{ flex: 1, minWidth: '300px', padding: '20px' }}>
                <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '20px', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center' }}>
                    {t('channelDistribution')}
                    <InfoTooltip text={t('channelDistributionTooltip')} />
                </div>
                <div style={{ height: '280px', width: '100%' }}>
                    {loadingEnhanced ? (
                        <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: '12px' }}>{t('loading')}</div>
                    ) : data.length === 0 ? (
                        <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: '12px' }}>{t('noData')}</div>
                    ) : (
                        <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                                <Pie
                                    data={data}
                                    dataKey="count"
                                    nameKey="channel"
                                    cx="50%"
                                    cy="50%"
                                    outerRadius={90}
                                    innerRadius={50}
                                    paddingAngle={2}
                                    label={({ channel, count }: any) => `${channel} (${(count * 100 / total).toFixed(0)}%)`}
                                    labelLine={{ stroke: 'var(--text-tertiary)', strokeWidth: 1 }}
                                >
                                    {data.map((_: any, i: number) => (
                                        <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                                    ))}
                                </Pie>
                                <Tooltip content={<CustomTooltip />} />
                            </PieChart>
                        </ResponsiveContainer>
                    )}
                </div>
            </div>
        );
    };

    // ─── Tool Category Bar Chart ──────────────────────────

    const ToolBarChart = () => {
        const data = enhanced?.tool_category_top10 || [];
        return (
            <div className="card" style={{ flex: 1, minWidth: '300px', padding: '20px' }}>
                <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '20px', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center' }}>
                    {t('top10ToolCategories')}
                    <InfoTooltip text={t('top10ToolCategoriesTooltip')} />
                </div>
                <div style={{ height: '280px', width: '100%' }}>
                    {loadingEnhanced ? (
                        <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: '12px' }}>{t('loading')}</div>
                    ) : data.length === 0 ? (
                        <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: '12px' }}>{t('noData')}</div>
                    ) : (
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data} layout="vertical" margin={{ top: 5, right: 20, left: 60, bottom: 5 }}>
                                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="var(--border-subtle)" />
                                <XAxis type="number" tick={{ fontSize: 10, fill: 'var(--text-tertiary)' }} tickLine={false} axisLine={false} />
                                <YAxis dataKey="category" type="category" tick={{ fontSize: 11, fill: 'var(--text-secondary)' }} tickLine={false} axisLine={false} width={55} />
                                <Tooltip content={<CustomTooltip />} />
                                <Bar dataKey="count" name="Enabled" fill="#3b82f6" radius={[0, 4, 4, 0]} barSize={18} />
                            </BarChart>
                        </ResponsiveContainer>
                    )}
                </div>
            </div>
        );
    };

    // ─── Churn Warning Table ───────────────────────────────

    const ChurnTable = () => {
        const data = enhanced?.churn_warnings || [];
        return (
            <div className="card" style={{ padding: '0', overflow: 'hidden' }}>
                <div style={{
                    padding: '20px',
                    fontSize: '13px',
                    fontWeight: 600,
                    color: 'var(--text-secondary)',
                    borderBottom: '1px solid var(--border-subtle)',
                    display: 'flex',
                    alignItems: 'center',
                }}>
                    {t('churnWarning')}
                    <InfoTooltip text={t('churnWarningTooltip')} />
                </div>
                {loadingEnhanced ? (
                    <div style={{ padding: '40px', textAlign: 'center', fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('loading')}</div>
                ) : data.length === 0 ? (
                    <div style={{ padding: '40px', textAlign: 'center', fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('noChurnWarnings')}</div>
                ) : (
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--border-subtle)', color: 'var(--text-tertiary)', fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                                <th style={{ padding: '12px 20px', textAlign: 'left', fontWeight: 600 }}>{t('company')}</th>
                                <th style={{ padding: '12px 20px', textAlign: 'right', fontWeight: 600 }}>{t('totalTokens')}</th>
                                <th style={{ padding: '12px 20px', textAlign: 'right', fontWeight: 600 }}>{t('lastActive')}</th>
                                <th style={{ padding: '12px 20px', textAlign: 'right', fontWeight: 600 }}>{t('daysInactive')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.map((row: any, i: number) => (
                                <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                    <td style={{ padding: '12px 20px', fontWeight: 500 }}>{row.name}</td>
                                    <td style={{ padding: '12px 20px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                        {formatTokens(row.total_tokens)}
                                    </td>
                                    <td style={{ padding: '12px 20px', textAlign: 'right', color: 'var(--text-tertiary)', fontSize: '12px' }}>
                                        {row.last_active ? new Date(row.last_active).toLocaleDateString() : t('never')}
                                    </td>
                                    <td style={{ padding: '12px 20px', textAlign: 'right' }}>
                                        <span style={{
                                            background: (row.days_inactive || 0) > 30 ? 'rgba(239,68,68,0.15)' : 'rgba(245,158,11,0.15)',
                                            color: (row.days_inactive || 0) > 30 ? '#ef4444' : '#f59e0b',
                                            padding: '2px 8px',
                                            borderRadius: '10px',
                                            fontSize: '12px',
                                            fontWeight: 600,
                                        }}>
                                            {row.days_inactive ?? '—'}d
                                        </span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        );
    };

    // ─── Leaderboard ──────────────────────────────────────

    const LeaderboardCard = ({ title, tooltip, items, renderItem }: {
        title: string; tooltip: string; items: any[];
        renderItem: (item: any, i: number) => React.ReactNode;
    }) => (
        <div className="card" style={{ flex: 1, minWidth: '300px', padding: '0', overflow: 'hidden' }}>
            <div style={{
                padding: '20px',
                fontSize: '13px',
                fontWeight: 600,
                color: 'var(--text-secondary)',
                borderBottom: '1px solid var(--border-subtle)',
                display: 'flex',
                alignItems: 'center',
            }}>
                {title}
                <InfoTooltip text={tooltip} />
            </div>
            {loadingLeaders ? (
                <div style={{ padding: '40px', textAlign: 'center', fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('loading')}</div>
            ) : (
                <div>
                    {items.map((item, i) => renderItem(item, i))}
                    {items.length === 0 && <div style={{ padding: '20px', textAlign: 'center', fontSize: '12px', color: 'var(--text-tertiary)' }}>{t('noData')}</div>}
                </div>
            )}
        </div>
    );

    // ─── Render ───────────────────────────────────────────

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
            {/* Time Range Toggle */}
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <div style={{ display: 'flex', background: 'var(--bg-secondary)', padding: '4px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
                    {([7, 30] as const).map((d) => (
                        <button
                            key={d}
                            onClick={() => setTimeRange(d)}
                            style={{
                                padding: '6px 16px', fontSize: '12px', fontWeight: 500, borderRadius: '6px',
                                background: timeRange === d ? 'var(--bg-primary)' : 'transparent',
                                color: timeRange === d ? 'var(--text-primary)' : 'var(--text-tertiary)',
                                boxShadow: timeRange === d ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                                border: 'none', cursor: 'pointer', transition: 'all 0.2s'
                            }}
                        >
                            {d === 7 ? t('last7Days') : t('last30Days')}
                        </button>
                    ))}
                </div>
            </div>

            {/* Summary Cards */}
            <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
                <MetricCard
                    label={t('avgTokensPerSession')}
                    value={enhanced ? formatTokens(enhanced.avg_tokens_per_session_30d) : '-'}
                    tooltip={t('avgTokensPerSessionTooltip')}
                />
                <MetricCard
                    label={t('retention7Day')}
                    value={enhanced ? `${enhanced.retention_rate_7d}%` : '-'}
                    tooltip={t('retentionTooltip', {
                        retainedInfo: enhanced ? `${enhanced.retained_companies} of ${enhanced.last_week_active_companies} companies retained.` : ''
                    })}
                />
            </div>

            {/* Existing Trend Charts */}
            <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
                <ChartCard title={t('companies')} tooltip={t('companiesTooltip')} dataKeyTotal="total_companies" dataKeyNew="new_companies" color="#3b82f6" />
                <ChartCard title={t('users')} tooltip={t('usersTooltip')} dataKeyTotal="total_users" dataKeyNew="new_users" color="#10b981" />
                <ChartCard title={t('tokenUsage')} tooltip={t('tokenUsageTooltip')} dataKeyTotal="total_tokens" dataKeyNew="new_tokens" color="#8b5cf6" />
            </div>

            {/* New Trend Charts: Sessions + Active Users */}
            <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
                <ChartCard
                    title={t('dailySessions')}
                    tooltip={t('dailySessionsTooltip')}
                    dataKeyTotal="total_sessions"
                    dataKeyNew="new_sessions"
                    color="#f59e0b"
                />
                <MultiLineChart
                    title={t('activeUsers')}
                    tooltip={t('activeUsersTooltip')}
                    lines={[
                        { key: 'dau', name: 'DAU', color: '#10b981' },
                        { key: 'wau', name: 'WAU', color: '#3b82f6' },
                        { key: 'mau', name: 'MAU', color: '#8b5cf6' },
                    ]}
                />
            </div>

            {/* Distribution Charts */}
            <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
                <ChannelPieChart />
                <ToolBarChart />
            </div>

            {/* Leaderboards */}
            <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
                <LeaderboardCard
                    title={t('top20CompaniesByTokens')}
                    tooltip={t('top20CompaniesByTokensTooltip')}
                    items={topCompanies}
                    renderItem={(c, i) => (
                        <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 20px', borderBottom: '1px solid var(--border-subtle)', fontSize: '13px' }}>
                            <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', width: '20px' }}>#{i + 1}</span>
                                <span style={{ fontWeight: 500 }}>{c.name}</span>
                            </div>
                            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                <div>{formatTokens(c.tokens)}</div>
                                <div style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                                    {t('cache')} {formatTokens(c.cache_read_tokens || 0)} · {Math.round((c.cache_hit_rate || 0) * 100)}%
                                </div>
                            </div>
                        </div>
                    )}
                />
                <LeaderboardCard
                    title={t('top20AgentsByTokens')}
                    tooltip={t('top20AgentsByTokensTooltip')}
                    items={topAgents}
                    renderItem={(a, i) => (
                        <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 20px', borderBottom: '1px solid var(--border-subtle)', fontSize: '13px' }}>
                            <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                                <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', width: '20px' }}>#{i + 1}</span>
                                <div>
                                    <div style={{ fontWeight: 500 }}>{a.name}</div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>{a.company}</div>
                                </div>
                            </div>
                            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                <div>{formatTokens(a.tokens)}</div>
                                <div style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                                    {t('cache')} {formatTokens(a.cache_read_tokens || 0)} · {Math.round((a.cache_hit_rate || 0) * 100)}%
                                </div>
                            </div>
                        </div>
                    )}
                />
            </div>

            {/* Churn Warning */}
            <ChurnTable />
        </div>
    );
}
