/**
 * Unified clipboard copy logic to handle both secure environments (HTTPS)
 * and insecure environments (e.g. dev servers accessed via IP address).
 */
export async function copyToClipboard(text: string): Promise<boolean> {
    if (navigator.clipboard && window.isSecureContext) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (e) {
            console.error('Clipboard API failed', e);
        }
    }
    // Fallback for non-HTTPS dev environments where clipboard API is unavailable
    try {
        const textArea = document.createElement("textarea");
        textArea.value = text;
        textArea.style.position = "fixed";
        textArea.style.opacity = "0";
        textArea.style.left = "-9999px";
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        const successful = document.execCommand('copy');
        document.body.removeChild(textArea);
        return successful;
    } catch (err) {
        console.error('Fallback copy failed', err);
        return false;
    }
}
