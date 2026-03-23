# 项目国际化与 Emoji 美化

## 何时使用此技能
当用户需要为项目添加国际化支持或美化界面元素时使用此技能。适用于前端项目，特别是使用 React 和 i18next 库的项目。

---

## 分步协议

### 步骤 1 — 分析项目结构
1. 检查项目是否已经配置了 i18next 国际化库
2. 查看现有的翻译文件结构和位置
3. 识别需要国际化的界面元素

### 步骤 2 — 添加国际化支持

#### 配置 i18next（如果尚未配置）
```javascript
// src/i18n/index.ts
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import zh from './zh.json';
import en from './en.json';

i18n
  .use(initReactI18next)
  .init({
    resources: {
      en: {
        translation: en
      },
      zh: {
        translation: zh
      }
    },
    lng: localStorage.getItem('language') || 'zh',
    fallbackLng: 'zh',
    interpolation: {
      escapeValue: false
    }
  });

export default i18n;
```

#### 在组件中使用国际化
```javascript
// 在组件中导入
import { useTranslation } from 'react-i18next';

// 在组件中使用
const { t } = useTranslation();

// 替换硬编码文本
<div>{t('key', '默认文本')}</div>
```

### 步骤 3 — 添加翻译文件

#### 中文翻译文件 (src/i18n/zh.json)
```json
{
  "common": {
    "save": "保存",
    "cancel": "取消",
    "confirm": "确认"
  },
  "agent": {
    "tools": {
      "config": "配置"
    }
  }
}
```

#### 英文翻译文件 (src/i18n/en.json)
```json
{
  "common": {
    "save": "Save",
    "cancel": "Cancel",
    "confirm": "Confirm"
  },
  "agent": {
    "tools": {
      "config": "Config"
    }
  }
}
```

### 步骤 4 — 添加 Emoji 美化
1. 为标题和标签添加适当的 Emoji
2. 确保 Emoji 在中英文环境下都能正常显示
3. 选择与内容相关的 Emoji，例如：
   - 📋 用于列表和表单
   - 🎨 用于设计和模板
   - 🤖 用于模型和 AI 相关
   - 📅 用于日期和时间
   - 📈 用于统计和数据

### 步骤 5 — 验证和测试
1. 运行项目，确保国际化功能正常工作
2. 切换语言，验证翻译是否正确显示
3. 检查 Emoji 是否正确显示

---

## 不要做什么
- ❌ 不要在翻译文件中硬编码 Emoji，应该在翻译文本中包含 Emoji
- ❌ 不要使用不相关的 Emoji，应该选择与内容相关的 Emoji
- ❌ 不要忽略 RTL（从右到左）语言的支持
- ❌ 不要在翻译文件中包含敏感信息
- ❌ 不要忘记为新添加的文本添加翻译