const MODEL_CACHE_EVENT = 'clawith:model-cache-invalidated';
const MODEL_CACHE_STORAGE_KEY = 'clawith_model_cache_version';

export function notifyModelCacheInvalidated(): void {
    const version = `${Date.now()}:${Math.random()}`;
    localStorage.setItem(MODEL_CACHE_STORAGE_KEY, version);
    window.dispatchEvent(new Event(MODEL_CACHE_EVENT));
}

export function subscribeModelCacheInvalidation(callback: () => void): () => void {
    const onLocal = () => callback();
    const onStorage = (event: StorageEvent) => {
        if (event.key === MODEL_CACHE_STORAGE_KEY) callback();
    };
    window.addEventListener(MODEL_CACHE_EVENT, onLocal);
    window.addEventListener('storage', onStorage);
    return () => {
        window.removeEventListener(MODEL_CACHE_EVENT, onLocal);
        window.removeEventListener('storage', onStorage);
    };
}
