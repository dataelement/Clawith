import { useState, type CSSProperties } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
    IconActivityHeartbeat, IconAlertTriangle, IconArrowUpRight, IconChartBar,
    IconFileAnalytics, IconRefresh, IconRobot, IconSparkles, IconTarget,
} from '@tabler/icons-react';
import { aiOperationsApi } from '../services/aiOperationsApi';

const number = new Intl.NumberFormat('zh-CN');

function relativeTime(value: string): string {
    const minutes = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60_000));
    if (minutes < 1) return '刚刚';
    if (minutes < 60) return `${minutes} 分钟前`;
    if (minutes < 1_440) return `${Math.floor(minutes / 60)} 小时前`;
    return `${Math.floor(minutes / 1_440)} 天前`;
}

function Metric({ label, value, accent, detail }: { label: string; value: string | number; accent: string; detail: string }) {
    return <div style={{ ...styles.metric, borderTopColor: accent }}>
        <span style={styles.metricLabel}>{label}</span>
        <strong style={styles.metricValue}>{value}</strong>
        <span style={styles.metricDetail}>{detail}</span>
    </div>;
}

export default function AIOperations() {
    const [days, setDays] = useState<7 | 30 | 90>(30);
    const navigate = useNavigate();
    const { data, isLoading, isFetching, error, refetch } = useQuery({
        queryKey: ['ai-operations', days],
        queryFn: () => aiOperationsApi.get(days),
        refetchInterval: 60_000,
    });

    if (isLoading) return <div style={styles.loading}>正在读取 AI 运行账本…</div>;
    if (error || !data) return <div style={styles.loading}><IconAlertTriangle size={22} /><span>运营数据暂时不可用。</span><button onClick={() => void refetch()} style={styles.button}>重试</button></div>;

    const maxDaily = Math.max(1, ...data.daily.map((item) => item.success + item.failed + item.cancelled));
    const health = data.overview.success_rate;

    return <main style={styles.page}>
        <section style={styles.hero}>
            <div style={{ position: 'relative', zIndex: 1 }}>
                <div style={styles.eyebrow}><IconActivityHeartbeat size={15} /> AI OPERATIONS CENTER <span style={styles.live}><i /> LIVE</span></div>
                <h1 style={styles.title}>让每一次 AI 运行<br /><em>都可被看见、复盘与优化。</em></h1>
                <p style={styles.subtitle}>以不可变运行事件为准，实时观察团队的交付质量、失败原因与生成式报告。</p>
            </div>
            <div style={styles.healthWrap}>
                <div style={{ ...styles.healthGauge, background: `conic-gradient(#37d7a5 ${health}%, rgba(170,195,202,.18) 0)` }}><div style={styles.healthInner}><strong>{health}%</strong><span>运行健康度</span></div></div>
                <span style={styles.healthNote}>{health >= 95 ? '运行状态优秀' : health >= 80 ? '建议关注失败任务' : '需要立即处理'}</span>
            </div>
        </section>

        <section style={styles.toolbar}>
            <div><span style={styles.sectionKicker}>运行概览</span><h2 style={styles.sectionTitle}>过去 {days} 天的 AI 交付</h2></div>
            <div style={styles.toolbarActions}>
                <div style={styles.periods}>{([7, 30, 90] as const).map((item) => <button key={item} onClick={() => setDays(item)} style={{ ...styles.period, ...(item === days ? styles.periodActive : {}) }}>{item} 天</button>)}</div>
                <button onClick={() => void refetch()} style={styles.refresh} title="刷新数据"><IconRefresh size={15} className={isFetching ? 'spin' : ''} /></button>
            </div>
        </section>

        <section style={styles.metricsGrid}>
            <Metric label="总运行次数" value={number.format(data.overview.total_runs)} accent="#4eb6ff" detail="含成功、失败与取消" />
            <Metric label="成功率" value={`${data.overview.success_rate}%`} accent="#37d7a5" detail={`${number.format(data.overview.success)} 次成功交付`} />
            <Metric label="失败率" value={`${data.overview.failure_rate}%`} accent="#ff7a71" detail={`${number.format(data.overview.failed)} 个待复盘故障`} />
            <Metric label="生成式报告" value={number.format(data.reports.length)} accent="#f7bd5e" detail="本周期已沉淀结果记录" />
        </section>

        <section style={styles.grid}>
            <div style={{ ...styles.panel, gridColumn: 'span 2' }}>
                <div style={styles.panelHeader}><div><span style={styles.sectionKicker}>交付脉冲</span><h2 style={styles.panelTitle}>每日运行质量</h2></div><IconChartBar size={20} color="#4eb6ff" /></div>
                <div style={styles.chart}>{data.daily.map((item, index) => {
                    const total = item.success + item.failed + item.cancelled;
                    return <div key={item.date} style={styles.barColumn} title={`${item.date}: ${total} 次运行`}>
                        <div style={styles.barStack}>
                            <div style={{ height: `${(item.success / maxDaily) * 100}%`, background: '#38cfa1' }} />
                            <div style={{ height: `${(item.failed / maxDaily) * 100}%`, background: '#f27f77' }} />
                            <div style={{ height: `${(item.cancelled / maxDaily) * 100}%`, background: '#98a9b0' }} />
                        </div>
                        {(data.daily.length <= 14 || index % Math.ceil(data.daily.length / 7) === 0) && <span style={styles.barLabel}>{item.date.slice(5)}</span>}
                    </div>;
                })}</div>
                <div style={styles.legend}><span><i style={{ background: '#38cfa1' }} />成功</span><span><i style={{ background: '#f27f77' }} />失败</span><span><i style={{ background: '#98a9b0' }} />已取消</span></div>
            </div>
            <div style={styles.panel}>
                <div style={styles.panelHeader}><div><span style={styles.sectionKicker}>模型表现</span><h2 style={styles.panelTitle}>稳定性排行</h2></div><IconSparkles size={20} color="#f7bd5e" /></div>
                <div style={styles.rankList}>{data.models.length ? data.models.slice(0, 5).map((model, index) => <div key={model.model_id || model.model_name} style={styles.rankRow}><span style={styles.rank}>{String(index + 1).padStart(2, '0')}</span><div style={{ minWidth: 0, flex: 1 }}><strong style={styles.ellipsis}>{model.model_name}</strong><small>{model.provider} · {model.total} 次</small></div><span style={{ ...styles.rate, color: model.success_rate >= 90 ? '#37d7a5' : '#f7bd5e' }}>{model.success_rate}%</span></div>) : <Empty text="本周期暂无模型运行" />}</div>
            </div>
        </section>

        <section style={styles.grid}>
            <div style={styles.panel}>
                <div style={styles.panelHeader}><div><span style={styles.sectionKicker}>故障雷达</span><h2 style={styles.panelTitle}>最近失败效果</h2></div><IconAlertTriangle size={20} color="#ff7a71" /></div>
                <div style={styles.feed}>{data.failures.length ? data.failures.slice(0, 6).map((failure) => <button key={failure.run_id} onClick={() => failure.agent_id && navigate(`/agents/${failure.agent_id}`)} style={styles.failureRow}>
                    <span style={styles.failureDot} /><div style={{ minWidth: 0, flex: 1 }}><strong style={styles.ellipsis}>{failure.agent_name}</strong><p style={styles.ellipsis}>{failure.error_code} · {failure.goal}</p><small>{relativeTime(failure.created_at)} · {failure.model_name}</small></div><IconArrowUpRight size={16} color="#9fb0b7" />
                </button>) : <Empty text="太好了，本周期没有失败 Run。" />}</div>
            </div>
            <div style={{ ...styles.panel, gridColumn: 'span 2' }}>
                <div style={styles.panelHeader}><div><span style={styles.sectionKicker}>生成档案</span><h2 style={styles.panelTitle}>AI 生成式报告记录</h2></div><IconFileAnalytics size={20} color="#c6a8ff" /></div>
                <div style={styles.reportList}>{data.reports.length ? data.reports.slice(0, 6).map((report) => <button key={report.run_id} onClick={() => report.agent_id && navigate(`/agents/${report.agent_id}`)} style={styles.reportRow}>
                    <span style={styles.reportIcon}><IconTarget size={16} /></span><div style={{ minWidth: 0, flex: 1 }}><strong style={styles.ellipsis}>{report.title}</strong><small>{report.report_type} · {report.agent_name}</small></div><span style={styles.reportTime}>{relativeTime(report.created_at)}</span><IconArrowUpRight size={16} color="#9fb0b7" />
                </button>) : <Empty text="完成的 AI 任务会自动沉淀到这里。" />}</div>
            </div>
        </section>

        <section style={styles.panel}>
            <div style={styles.panelHeader}><div><span style={styles.sectionKicker}>智能体质量</span><h2 style={styles.panelTitle}>谁在稳定交付</h2></div><IconRobot size={20} color="#4eb6ff" /></div>
            <div style={styles.agentTable}>{data.agents.length ? data.agents.slice(0, 8).map((agent) => <button key={agent.agent_id || agent.agent_name} onClick={() => agent.agent_id && navigate(`/agents/${agent.agent_id}`)} style={styles.agentRow}>
                <span style={styles.agentAvatar}>{agent.agent_name.slice(0, 1)}</span><strong>{agent.agent_name}</strong><span>{agent.total} 次运行</span><div style={styles.track}><i style={{ width: `${agent.success_rate}%`, background: agent.success_rate >= 90 ? '#37d7a5' : '#f7bd5e' }} /></div><b>{agent.success_rate}%</b><IconArrowUpRight size={15} color="#9fb0b7" />
            </button>) : <Empty text="开始运行 AI 任务后，这里将显示交付排行。" />}</div>
        </section>
    </main>;
}

function Empty({ text }: { text: string }) { return <div style={styles.empty}>{text}</div>; }

const styles: Record<string, CSSProperties> = {
    page: { maxWidth: 1240, margin: '0 auto', padding: '34px 30px 64px', color: 'var(--text-primary)' },
    loading: { minHeight: 320, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 10, color: 'var(--text-secondary)' },
    hero: { minHeight: 260, padding: '36px 40px', borderRadius: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 28, overflow: 'hidden', position: 'relative', color: '#eaf7f5', background: 'radial-gradient(circle at 84% 25%, rgba(49, 205, 166, .22), transparent 26%), radial-gradient(circle at 15% 100%, rgba(78, 182, 255, .18), transparent 32%), #102529', boxShadow: '0 18px 45px rgba(8, 38, 39, .18)' },
    eyebrow: { display: 'flex', alignItems: 'center', gap: 7, color: '#8ab7b0', letterSpacing: '.14em', fontSize: 11, fontWeight: 800 },
    live: { marginLeft: 8, padding: '4px 7px', borderRadius: 99, color: '#42e6b4', background: 'rgba(57, 222, 171, .12)', letterSpacing: '.08em', fontSize: 10 },
    title: { margin: '17px 0 12px', fontFamily: 'Georgia, STKSong, serif', fontSize: 'clamp(29px, 4vw, 46px)', letterSpacing: '-.045em', lineHeight: 1.06 },
    subtitle: { maxWidth: 580, margin: 0, color: '#a7c3c0', lineHeight: 1.65, fontSize: 14 },
    healthWrap: { position: 'relative', zIndex: 1, textAlign: 'center', flex: '0 0 auto' }, healthGauge: { width: 136, height: 136, borderRadius: '50%', display: 'grid', placeItems: 'center', padding: 10 }, healthInner: { width: '100%', height: '100%', borderRadius: '50%', display: 'grid', placeContent: 'center', gap: 4, background: '#102529' }, healthNote: { display: 'block', marginTop: 10, color: '#b9d2ce', fontSize: 12 },
    toolbar: { display: 'flex', justifyContent: 'space-between', alignItems: 'end', gap: 20, margin: '34px 0 17px' }, toolbarActions: { display: 'flex', gap: 9, alignItems: 'center' }, periods: { display: 'flex', padding: 3, border: '1px solid var(--border)', borderRadius: 10, background: 'var(--bg-secondary)' }, period: { border: 0, background: 'transparent', color: 'var(--text-secondary)', font: 'inherit', fontSize: 12, padding: '6px 10px', borderRadius: 7, cursor: 'pointer' }, periodActive: { color: '#edfdf9', background: '#1f5752', fontWeight: 700 }, refresh: { width: 33, height: 33, display: 'grid', placeItems: 'center', borderRadius: 9, border: '1px solid var(--border)', color: 'var(--text-secondary)', background: 'var(--bg-card)', cursor: 'pointer' }, button: { border: '1px solid var(--border)', padding: '6px 10px', borderRadius: 8, background: 'var(--bg-card)', color: 'inherit', cursor: 'pointer' },
    sectionKicker: { display: 'block', color: '#6d8990', textTransform: 'uppercase', fontSize: 10, fontWeight: 800, letterSpacing: '.13em' }, sectionTitle: { margin: '5px 0 0', fontSize: 20, letterSpacing: '-.025em' },
    metricsGrid: { display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12 }, metric: { minHeight: 120, padding: '18px 19px', display: 'flex', flexDirection: 'column', border: '1px solid var(--border)', borderTop: '3px solid', borderRadius: 14, background: 'var(--bg-card)' }, metricLabel: { color: 'var(--text-secondary)', fontSize: 12 }, metricValue: { margin: '10px 0 6px', fontSize: 27, letterSpacing: '-.055em' }, metricDetail: { color: 'var(--text-tertiary)', fontSize: 11 },
    grid: { display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 14, marginTop: 14 }, panel: { minWidth: 0, padding: 20, border: '1px solid var(--border)', borderRadius: 16, background: 'var(--bg-card)' }, panelHeader: { display: 'flex', justifyContent: 'space-between', alignItems: 'start', gap: 16 }, panelTitle: { margin: '4px 0 0', fontSize: 16, letterSpacing: '-.025em' },
    chart: { height: 178, display: 'flex', alignItems: 'end', gap: 3, margin: '22px 0 4px' }, barColumn: { flex: 1, height: '100%', minWidth: 3, display: 'flex', flexDirection: 'column', justifyContent: 'end', alignItems: 'center', gap: 7 }, barStack: { width: '100%', height: 144, display: 'flex', flexDirection: 'column-reverse', justifyContent: 'end', overflow: 'hidden', borderRadius: '4px 4px 1px 1px', background: 'rgba(116, 147, 154, .07)' }, barLabel: { fontSize: 9, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }, legend: { display: 'flex', gap: 13, color: 'var(--text-secondary)', fontSize: 11 },
    rankList: { marginTop: 16 }, rankRow: { display: 'flex', alignItems: 'center', gap: 10, padding: '11px 0', borderBottom: '1px solid var(--border)' }, rank: { color: '#8ba0a5', fontSize: 10, fontFamily: 'ui-monospace, monospace' }, rate: { fontSize: 13, fontWeight: 800 }, ellipsis: { display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
    feed: { marginTop: 12 }, failureRow: { width: '100%', display: 'flex', alignItems: 'center', gap: 9, textAlign: 'left', padding: '11px 0', border: 0, borderBottom: '1px solid var(--border)', background: 'transparent', color: 'inherit', cursor: 'pointer' }, failureDot: { width: 7, height: 7, borderRadius: '50%', flex: '0 0 auto', background: '#f27f77', boxShadow: '0 0 0 4px rgba(242,127,119,.11)' }, reportList: { marginTop: 12 }, reportRow: { width: '100%', display: 'flex', alignItems: 'center', gap: 11, padding: '10px 0', textAlign: 'left', border: 0, borderBottom: '1px solid var(--border)', background: 'transparent', color: 'inherit', cursor: 'pointer' }, reportIcon: { width: 29, height: 29, display: 'grid', placeItems: 'center', borderRadius: 8, color: '#a57df0', background: 'rgba(165,125,240,.12)' }, reportTime: { color: 'var(--text-tertiary)', fontSize: 11, whiteSpace: 'nowrap' },
    agentTable: { marginTop: 13 }, agentRow: { width: '100%', display: 'grid', gridTemplateColumns: '28px minmax(110px, 1fr) 84px minmax(80px, 1fr) 48px 16px', alignItems: 'center', gap: 12, padding: '10px 0', border: 0, borderBottom: '1px solid var(--border)', background: 'transparent', color: 'inherit', textAlign: 'left', cursor: 'pointer', fontSize: 12 }, agentAvatar: { width: 28, height: 28, display: 'grid', placeItems: 'center', borderRadius: 8, color: '#d7f5ef', background: '#276760', fontWeight: 800 }, track: { height: 5, overflow: 'hidden', borderRadius: 8, background: 'rgba(119, 149, 154, .18)' },
    empty: { padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 },
};
