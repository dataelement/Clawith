import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import zh from './zh.json';
import en from './en.json';

i18n
    .use(LanguageDetector)
    .use(initReactI18next)
    .init({
        resources: {
            zh: { translation: zh },
            en: { translation: en },
        },
        fallbackLng: 'en',
        interpolation: { escapeValue: false },
        supportedLngs: ['en', 'zh'],
        nonExplicitSupportedLngs: true,
        detection: {
            order: ['localStorage', 'navigator'],
            caches: ['localStorage'],
            convertDetectedLanguage: (lng) => {
                if (lng.startsWith('zh')) return 'zh';
                if (lng.startsWith('en')) return 'en';
                return lng;
            },
        },
    });

export default i18n;
