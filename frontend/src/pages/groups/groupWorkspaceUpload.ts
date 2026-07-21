export const GROUP_WORKSPACE_TEXT_EXTENSIONS = [
    '.txt', '.md', '.csv', '.json', '.xml', '.yaml', '.yml', '.js', '.ts', '.py',
    '.html', '.css', '.sh', '.log', '.gitkeep', '.env',
] as const;

export const GROUP_WORKSPACE_UPLOAD_ACCEPT = GROUP_WORKSPACE_TEXT_EXTENSIONS.join(',');

export type GroupWorkspaceUploadErrorCode =
    | 'invalid_name'
    | 'unsupported_type'
    | 'invalid_utf8';

export class GroupWorkspaceUploadError extends Error {
    readonly code: GroupWorkspaceUploadErrorCode;

    constructor(code: GroupWorkspaceUploadErrorCode) {
        super(code);
        this.name = 'GroupWorkspaceUploadError';
        this.code = code;
    }
}

function isSupportedTextName(name: string): boolean {
    const lower = name.toLowerCase();
    const base = lower.split('/').pop() || '';
    return GROUP_WORKSPACE_TEXT_EXTENSIONS.some((extension) => lower.endsWith(extension))
        || !base.includes('.')
        || base.startsWith('.');
}

export function groupWorkspaceUploadPath(directory: string, fileName: string): string {
    if (!fileName || fileName === '.' || fileName === '..' || /[\\/]/.test(fileName)) {
        throw new GroupWorkspaceUploadError('invalid_name');
    }
    if (!isSupportedTextName(fileName)) {
        throw new GroupWorkspaceUploadError('unsupported_type');
    }
    return directory ? `${directory.replace(/\/$/, '')}/${fileName}` : fileName;
}

/** Decode without replacement characters so binary/invalid UTF-8 never gets silently corrupted. */
export async function readGroupWorkspaceTextUpload(
    file: Pick<File, 'arrayBuffer'>,
): Promise<string> {
    let content: string;
    try {
        content = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
    } catch {
        throw new GroupWorkspaceUploadError('invalid_utf8');
    }
    if (content.includes('\0')) {
        throw new GroupWorkspaceUploadError('invalid_utf8');
    }
    return content;
}
