type CryptoSource = Partial<Pick<Crypto, 'randomUUID' | 'getRandomValues'>>;

export function createRandomUUID(
    source: CryptoSource | null = globalThis.crypto,
): string {
    if (source && typeof source.randomUUID === 'function') {
        return source.randomUUID();
    }
    if (!source || typeof source.getRandomValues !== 'function') {
        throw new Error('A secure random source is unavailable in this browser.');
    }

    const bytes = new Uint8Array(16);
    source.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;

    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0'));
    return [
        hex.slice(0, 4).join(''),
        hex.slice(4, 6).join(''),
        hex.slice(6, 8).join(''),
        hex.slice(8, 10).join(''),
        hex.slice(10, 16).join(''),
    ].join('-');
}
