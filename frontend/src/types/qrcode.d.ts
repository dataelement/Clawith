declare module 'qrcode' {
    export interface QRCodeToDataURLOptions {
        width?: number;
        margin?: number;
        color?: {
            dark?: string;
            light?: string;
        };
    }

    export function toDataURL(text: string, options?: QRCodeToDataURLOptions): Promise<string>;
}
