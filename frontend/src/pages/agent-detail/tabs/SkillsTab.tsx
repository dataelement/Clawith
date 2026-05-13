import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { IconDownload, IconFolder, IconTools } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';

import { useDialog } from '../../../components/Dialog/DialogProvider';
import type { FileBrowserApi } from '../../../components/FileBrowser';
import FileBrowser from '../../../components/FileBrowser';
import { useToast } from '../../../components/Toast/ToastProvider';
import { fileApi, skillApi } from '../../../services/api';

type SafeDisplayIcon = (icon?: string | null, fallback?: ReactNode) => ReactNode;

interface Props {
    agentId: string;
    canManage: boolean;
    safeDisplayIcon: SafeDisplayIcon;
    showAgentClawhub: boolean;
    setShowAgentClawhub: Dispatch<SetStateAction<boolean>>;
    agentClawhubQuery: string;
    setAgentClawhubQuery: Dispatch<SetStateAction<string>>;
    agentClawhubResults: any[];
    setAgentClawhubResults: Dispatch<SetStateAction<any[]>>;
    agentClawhubSearching: boolean;
    setAgentClawhubSearching: Dispatch<SetStateAction<boolean>>;
    agentClawhubInstalling: string | null;
    setAgentClawhubInstalling: Dispatch<SetStateAction<string | null>>;
    showAgentUrlImport: boolean;
    setShowAgentUrlImport: Dispatch<SetStateAction<boolean>>;
    agentUrlInput: string;
    setAgentUrlInput: Dispatch<SetStateAction<string>>;
    agentUrlImporting: boolean;
    setAgentUrlImporting: Dispatch<SetStateAction<boolean>>;
    showImportSkillModal: boolean;
    setShowImportSkillModal: Dispatch<SetStateAction<boolean>>;
    globalSkillsForImport: any[] | undefined;
    importingSkillId: string | null;
    setImportingSkillId: Dispatch<SetStateAction<string | null>>;
}

export default function SkillsTab(props: Props) {
    const {
        agentId,
        canManage,
        safeDisplayIcon,
        showAgentClawhub,
        setShowAgentClawhub,
        agentClawhubQuery,
        setAgentClawhubQuery,
        agentClawhubResults,
        setAgentClawhubResults,
        agentClawhubSearching,
        setAgentClawhubSearching,
        agentClawhubInstalling,
        setAgentClawhubInstalling,
        showAgentUrlImport,
        setShowAgentUrlImport,
        agentUrlInput,
        setAgentUrlInput,
        agentUrlImporting,
        setAgentUrlImporting,
        showImportSkillModal,
        setShowImportSkillModal,
        globalSkillsForImport,
        importingSkillId,
        setImportingSkillId,
    } = props;
    const { t } = useTranslation();
    const dialog = useDialog();
    const toast = useToast();
    const queryClient = useQueryClient();
    const adapter: FileBrowserApi = {
        list: (path) => fileApi.list(agentId, path),
        read: (path) => fileApi.read(agentId, path),
        write: (path, content) => fileApi.write(agentId, path, content),
        delete: (path) => fileApi.delete(agentId, path),
        upload: (file, path, onProgress) => fileApi.upload(agentId, file, path, onProgress),
        downloadUrl: (path) => fileApi.downloadUrl(agentId, path),
    };

    const searchClawHub = () => {
        setAgentClawhubSearching(true);
        skillApi.clawhub.search(agentClawhubQuery)
            .then((results) => {
                setAgentClawhubResults(results);
                setAgentClawhubSearching(false);
            })
            .catch(() => setAgentClawhubSearching(false));
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

            <FileBrowser api={adapter} rootPath="skills" features={{ newFile: true, edit: true, delete: canManage, newFolder: true, upload: true, directoryNavigation: true }} title={t('agent.skills.skillFiles')} />

            {showAgentClawhub && (
                <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowAgentClawhub(false)}>
                    <div onClick={(e) => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', maxWidth: '600px', width: '90%', maxHeight: '70vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                            <h3>Browse ClawHub</h3>
                            <button onClick={() => setShowAgentClawhub(false)} style={{ background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer', color: 'var(--text-secondary)', padding: '4px 8px' }}>x</button>
                        </div>
                        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: '0 0 12px' }}>
                            Search and install skills from ClawHub directly into this agent&apos;s workspace.
                        </p>
                        <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
                            <input
                                className="input"
                                placeholder="Search skills..."
                                value={agentClawhubQuery}
                                onChange={(e) => setAgentClawhubQuery(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' && agentClawhubQuery.trim()) searchClawHub();
                                }}
                                style={{ flex: 1, fontSize: '13px' }}
                            />
                            <button
                                className="btn btn-primary"
                                style={{ fontSize: '13px' }}
                                disabled={!agentClawhubQuery.trim() || agentClawhubSearching}
                                onClick={searchClawHub}
                            >
                                {agentClawhubSearching ? 'Searching...' : 'Search'}
                            </button>
                        </div>
                        <div style={{ flex: 1, overflowY: 'auto' }}>
                            {agentClawhubResults.length === 0 && !agentClawhubSearching && (
                                <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-tertiary)', fontSize: '13px' }}>Search ClawHub to find skills</div>
                            )}
                            {agentClawhubResults.map((result: any) => (
                                <div key={result.slug} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', borderRadius: '8px', marginBottom: '6px', border: '1px solid var(--border-subtle)', background: 'var(--bg-secondary)' }}>
                                    <div style={{ flex: 1 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                            <span style={{ fontWeight: 600, fontSize: '13px' }}>{result.displayName || result.slug}</span>
                                            {result.version && <span style={{ fontSize: '10px', color: 'var(--accent-text)', background: 'var(--accent-subtle)', padding: '1px 5px', borderRadius: '4px' }}>v{result.version}</span>}
                                        </div>
                                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '2px' }}>{result.summary?.substring(0, 100)}{result.summary?.length > 100 ? '...' : ''}</div>
                                        {result.updatedAt && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px', opacity: 0.7 }}>Updated {new Date(result.updatedAt).toLocaleDateString()}</div>}
                                    </div>
                                    <button
                                        className="btn btn-secondary"
                                        style={{ fontSize: '12px', padding: '5px 12px', marginLeft: '12px' }}
                                        disabled={agentClawhubInstalling === result.slug}
                                        onClick={async () => {
                                            setAgentClawhubInstalling(result.slug);
                                            try {
                                                const response = await skillApi.agentImport.fromClawhub(agentId, result.slug);
                                                toast.success(`已安装 "${result.displayName || result.slug}"（${response.files_written} 个文件）`);
                                                queryClient.invalidateQueries({ queryKey: ['files', agentId, 'skills'] });
                                            } catch (err: any) {
                                                await dialog.alert('安装失败', { type: 'error', details: String(err?.message || err) });
                                            } finally {
                                                setAgentClawhubInstalling(null);
                                            }
                                        }}
                                    >
                                        {agentClawhubInstalling === result.slug ? 'Installing...' : 'Install'}
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}

            {showAgentUrlImport && (
                <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowAgentUrlImport(false)}>
                    <div onClick={(e) => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', maxWidth: '500px', width: '90%', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
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
                            onChange={(e) => setAgentUrlInput(e.target.value)}
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
                                        const response = await skillApi.agentImport.fromUrl(agentId, agentUrlInput.trim());
                                        toast.success(`已导入 ${response.files_written} 个文件`);
                                        queryClient.invalidateQueries({ queryKey: ['files', agentId, 'skills'] });
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

            {showImportSkillModal && (
                <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowImportSkillModal(false)}>
                    <div onClick={(e) => e.stopPropagation()} style={{ background: 'var(--bg-primary)', borderRadius: '12px', padding: '24px', maxWidth: '600px', width: '90%', maxHeight: '70vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
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
                                        onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--accent-primary)')}
                                        onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border-subtle)')}
                                    >
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flex: 1 }}>
                                            <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)' }}>
                                                {safeDisplayIcon(skill.icon, <IconTools size={20} stroke={1.8} />)}
                                            </span>
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
                                                    const response = await fileApi.importSkill(agentId, skill.id);
                                                    toast.success(`已导入 "${skill.name}"（${response.files_written} 个文件）`);
                                                    queryClient.invalidateQueries({ queryKey: ['files', agentId, 'skills'] });
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
}
