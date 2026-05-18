const CRC32_TABLE = (() => {
    const table = new Uint32Array(256);

    for (let i = 0; i < 256; i += 1) {
        let value = i;
        for (let bit = 0; bit < 8; bit += 1) {
            value = (value & 1) === 1 ? 0xEDB88320 ^ (value >>> 1) : value >>> 1;
        }
        table[i] = value >>> 0;
    }

    return table;
})();

const textEncoder = new TextEncoder();

export type SkillFolderFile = { path: string; file: File };

type SkillZipEntry = {
    name: string;
    nameBytes: Uint8Array;
    bytes: Uint8Array;
    crc: number;
    dosDate: number;
    dosTime: number;
};

export function crc32(bytes: Uint8Array): number {
    let value = 0xFFFFFFFF;

    for (const byte of bytes) {
        value = CRC32_TABLE[(value ^ byte) & 0xFF] ^ (value >>> 8);
    }

    return (value ^ 0xFFFFFFFF) >>> 0;
}

function normalizeArchivePath(path: string): string {
    const normalized = path
        .replace(/\\+/g, '/')
        .replace(/^\/+/, '')
        .split('/')
        .filter((segment) => segment && segment !== '.' && segment !== '..')
        .join('/');

    if (!normalized) {
        throw new Error('Skill folder ZIP entries require a non-empty relative path');
    }

    return normalized;
}

function sanitizeRootName(name: string): string {
    const normalized = name
        .trim()
        .replace(/\.[^.]+$/, '')
        .replace(/[^a-zA-Z0-9_-]+/g, '-')
        .replace(/^-+|-+$/g, '');

    return normalized || 'skill-upload';
}

function toDosDateTime(timestamp: number): { dosDate: number; dosTime: number } {
    const date = new Date(timestamp || Date.now());
    const year = Math.min(Math.max(date.getFullYear(), 1980), 2107);
    const month = date.getMonth() + 1;
    const day = date.getDate();
    const hours = date.getHours();
    const minutes = date.getMinutes();
    const seconds = Math.floor(date.getSeconds() / 2);

    return {
        dosDate: ((year - 1980) << 9) | (month << 5) | day,
        dosTime: (hours << 11) | (minutes << 5) | seconds,
    };
}

function createHeader(length: number): DataView {
    return new DataView(new ArrayBuffer(length));
}

async function createZipEntry(rootFolder: string, item: SkillFolderFile): Promise<SkillZipEntry> {
    const name = `${rootFolder}/${normalizeArchivePath(item.path)}`;
    const bytes = new Uint8Array(await item.file.arrayBuffer());
    const { dosDate, dosTime } = toDosDateTime(item.file.lastModified || Date.now());

    return {
        name,
        nameBytes: textEncoder.encode(name),
        bytes,
        crc: crc32(bytes),
        dosDate,
        dosTime,
    };
}

export async function buildSkillFolderZip(files: SkillFolderFile[], rootFolder: string): Promise<File> {
    const normalizedRoot = sanitizeRootName(rootFolder);
    if (!files.length) {
        throw new Error('Cannot build a skill folder ZIP without files');
    }

    const entries = await Promise.all(files.map((item) => createZipEntry(normalizedRoot, item)));
    const parts: Uint8Array[] = [];
    const centralDirectory: Uint8Array[] = [];
    let offset = 0;

    for (const entry of entries) {
        const localHeader = createHeader(30);
        localHeader.setUint32(0, 0x04034B50, true);
        localHeader.setUint16(4, 20, true);
        localHeader.setUint16(6, 0, true);
        localHeader.setUint16(8, 0, true);
        localHeader.setUint16(10, entry.dosTime, true);
        localHeader.setUint16(12, entry.dosDate, true);
        localHeader.setUint32(14, entry.crc, true);
        localHeader.setUint32(18, entry.bytes.length, true);
        localHeader.setUint32(22, entry.bytes.length, true);
        localHeader.setUint16(26, entry.nameBytes.length, true);
        localHeader.setUint16(28, 0, true);

        const localHeaderBytes = new Uint8Array(localHeader.buffer);
        parts.push(localHeaderBytes, entry.nameBytes, entry.bytes);

        const centralHeader = createHeader(46);
        centralHeader.setUint32(0, 0x02014B50, true);
        centralHeader.setUint16(4, 20, true);
        centralHeader.setUint16(6, 20, true);
        centralHeader.setUint16(8, 0, true);
        centralHeader.setUint16(10, 0, true);
        centralHeader.setUint16(12, entry.dosTime, true);
        centralHeader.setUint16(14, entry.dosDate, true);
        centralHeader.setUint32(16, entry.crc, true);
        centralHeader.setUint32(20, entry.bytes.length, true);
        centralHeader.setUint32(24, entry.bytes.length, true);
        centralHeader.setUint16(28, entry.nameBytes.length, true);
        centralHeader.setUint16(30, 0, true);
        centralHeader.setUint16(32, 0, true);
        centralHeader.setUint16(34, 0, true);
        centralHeader.setUint16(36, 0, true);
        centralHeader.setUint32(38, 0, true);
        centralHeader.setUint32(42, offset, true);

        centralDirectory.push(new Uint8Array(centralHeader.buffer), entry.nameBytes);
        offset += localHeaderBytes.length + entry.nameBytes.length + entry.bytes.length;
    }

    const centralDirectorySize = centralDirectory.reduce((total, chunk) => total + chunk.length, 0);
    const endOfCentralDirectory = createHeader(22);
    endOfCentralDirectory.setUint32(0, 0x06054B50, true);
    endOfCentralDirectory.setUint16(4, 0, true);
    endOfCentralDirectory.setUint16(6, 0, true);
    endOfCentralDirectory.setUint16(8, entries.length, true);
    endOfCentralDirectory.setUint16(10, entries.length, true);
    endOfCentralDirectory.setUint32(12, centralDirectorySize, true);
    endOfCentralDirectory.setUint32(16, offset, true);
    endOfCentralDirectory.setUint16(20, 0, true);

    parts.push(...centralDirectory, new Uint8Array(endOfCentralDirectory.buffer));

    // Quick manual smoke harness:
    // 1. In the browser console, call buildSkillFolderZip([{ path: 'SKILL.md', file }], 'demo-skill').
    // 2. Upload the returned File through the preview/apply folder endpoints and confirm the backend sees demo-skill/SKILL.md.
    return new File(parts as BlobPart[], `${normalizedRoot}.zip`, {
        type: 'application/zip',
        lastModified: Date.now(),
    });
}

export function deriveSkillRootName(files: File[]): string {
    const rootCandidates = files
        .map((file) => file.webkitRelativePath.split('/')[0]?.trim())
        .filter((candidate): candidate is string => Boolean(candidate));

    if (rootCandidates.length === files.length && new Set(rootCandidates).size === 1) {
        return sanitizeRootName(rootCandidates[0]);
    }

    const fallback = files[0]?.webkitRelativePath || files[0]?.name || 'skill-upload';
    return sanitizeRootName(fallback.split('/')[0] || fallback);
}
