import {
    IconFile,
    IconFileText,
    IconMarkdown,
    IconPhoto,
    IconFileTypePdf,
} from '@tabler/icons-react';
import type { Icon } from '@tabler/icons-react';

const TEXT_EXTS = [
    '.txt', '.md', '.csv', '.json', '.xml', '.yaml', '.yml',
    '.js', '.ts', '.tsx', '.jsx', '.py', '.html', '.css', '.sh',
    '.log', '.gitkeep', '.env', '.toml', '.ini', '.conf',
];

const IMAGE_EXTS = [
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico',
];

const MARKDOWN_EXTS = ['.md', '.markdown'];
const PDF_EXTS = ['.pdf'];

export type FileTypeKind = 'image' | 'pdf' | 'markdown' | 'text' | 'other';

export function isTextFile(name: string): boolean {
    const n = name.toLowerCase();
    if (TEXT_EXTS.some(ext => n.endsWith(ext))) return true;
    const base = n.split('/').pop() || '';
    return !base.includes('.') || base.startsWith('.');
}

export function isImage(name: string): boolean {
    const n = name.toLowerCase();
    return IMAGE_EXTS.some(ext => n.endsWith(ext));
}

export function isPdf(name: string): boolean {
    return name.toLowerCase().endsWith('.pdf');
}

export function classifyFile(name: string, mimeType?: string): FileTypeKind {
    const n = name.toLowerCase();
    const mime = (mimeType || '').toLowerCase();

    if (PDF_EXTS.some(ext => n.endsWith(ext)) || mime === 'application/pdf') {
        return 'pdf';
    }
    if (MARKDOWN_EXTS.some(ext => n.endsWith(ext))) {
        return 'markdown';
    }
    if (mime.startsWith('image/') || IMAGE_EXTS.some(ext => n.endsWith(ext))) {
        return 'image';
    }
    if (
        mime.startsWith('text/') ||
        mime === 'application/json' ||
        mime === 'application/xml' ||
        TEXT_EXTS.some(ext => n.endsWith(ext))
    ) {
        return 'text';
    }
    return 'other';
}

export function fileTypeIcon(kind: FileTypeKind): Icon {
    switch (kind) {
        case 'image':    return IconPhoto;
        case 'pdf':      return IconFileTypePdf;
        case 'markdown': return IconMarkdown;
        case 'text':     return IconFileText;
        default:         return IconFile;
    }
}
