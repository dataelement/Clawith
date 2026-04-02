---
name: "i18n-helper"
description: "Helps add and manage internationalization (i18n) for the Clawith project. Invoke when adding new UI text, translating content, or working with i18n files."
---

# Internationalization (i18n) Helper

This skill provides guidelines and best practices for adding internationalization support to the Clawith project.

## Project i18n Structure

The Clawith project uses **i18next** for internationalization with the following structure:

```
frontend/src/i18n/
├── en.json          # English translations
├── zh.json          # Chinese translations
└── index.ts         # i18n configuration
```

## When to Add i18n

**ALWAYS add internationalization when:**
- Adding new UI text (buttons, labels, titles, descriptions)
- Creating new pages or components with text content
- Adding error messages or notifications
- Creating tooltips, placeholders, or help text
- Adding table headers or column names

## Step-by-Step Process

### 1. Add Translation Keys

**In `frontend/src/i18n/en.json`:**
```json
{
  "moduleName": {
    "sectionName": {
      "title": "Title in English",
      "description": "Description text",
      "button": {
        "save": "Save",
        "cancel": "Cancel"
      }
    }
  }
}
```

**In `frontend/src/i18n/zh.json`:**
```json
{
  "moduleName": {
    "sectionName": {
      "title": "中文标题",
      "description": "描述文本",
      "button": {
        "save": "保存",
        "cancel": "取消"
      }
    }
  }
}
```

### 2. Use Translations in Components

**Import the translation hook:**
```tsx
import { useTranslation } from 'react-i18next';

function MyComponent() {
  const { t } = useTranslation();
  
  return (
    <div>
      <h1>{t('moduleName.sectionName.title')}</h1>
      <p>{t('moduleName.sectionName.description')}</p>
      <button>{t('moduleName.sectionName.button.save')}</button>
    </div>
  );
}
```

**With fallback text:**
```tsx
{t('moduleName.key', 'Default English Text')}
```

## Naming Conventions

### Module Structure
Organize translations by feature/module:
- `common` - Shared UI elements (buttons, labels, messages)
- `admin` - Admin panel
- `agent` - Agent-related features
- `company` - Company management
- `enterprise` - Enterprise settings
- `platformDashboard` - Platform dashboard

### Key Naming Rules
1. **Use camelCase** for keys: `userName`, `createAgent`
2. **Be descriptive**: `button.save` not `btn.sv`
3. **Group related keys**: 
   ```json
   "button": { "save": "...", "cancel": "..." }
   ```
4. **Use consistent suffixes**:
   - `.title` - Page/section titles
   - `.description` - Descriptive text
   - `.placeholder` - Input placeholders
   - `.tooltip` - Tooltip text
   - `.label` - Form labels
   - `.message` - Status/error messages

## Best Practices

### 1. Always Provide Both Languages
Never add only English or only Chinese. Both `en.json` and `zh.json` must have the same keys.

### 2. Use Interpolation for Dynamic Content
```json
{
  "welcome": "Welcome, {{name}}!",
  "itemsCount": "{{count}} items found"
}
```

```tsx
t('welcome', { name: userName })
t('itemsCount', { count: items.length })
```

### 3. Keep Translations Organized
- Group by module/feature
- Maintain alphabetical order within groups
- Use nested structure for related keys

### 4. Avoid Hard-coded Text
❌ **Bad:**
```tsx
<button>Submit</button>
```

✅ **Good:**
```tsx
<button>{t('common.button.submit')}</button>
```

### 5. Use Fallback Text for New Features
```tsx
{t('newFeature.title', 'New Feature Title')}
```

## Common Patterns

### Page Titles and Subtitles
```json
{
  "admin": {
    "platformSettings": "Platform Settings",
    "platformSettingsDesc": "Manage platform-wide settings and company tenants."
  }
}
```

### Form Labels and Placeholders
```json
{
  "form": {
    "email": {
      "label": "Email Address",
      "placeholder": "Enter your email"
    }
  }
}
```

### Error Messages
```json
{
  "error": {
    "required": "This field is required",
    "invalidEmail": "Please enter a valid email address"
  }
}
```

### Success Messages
```json
{
  "success": {
    "saved": "Changes saved successfully",
    "created": "{{item}} created successfully"
  }
}
```

## Validation Checklist

Before committing i18n changes, verify:

- [ ] Both `en.json` and `zh.json` have the same keys
- [ ] JSON syntax is valid (no trailing commas, proper quotes)
- [ ] Keys follow naming conventions (camelCase, descriptive)
- [ ] Translations are accurate and contextually appropriate
- [ ] No hard-coded text remains in components
- [ ] Interpolation variables are used correctly
- [ ] Fallback text is provided for new features

## Tools and Utilities

### Check for Missing Translations
```bash
# Compare keys between en.json and zh.json
diff <(jq -S 'keys' frontend/src/i18n/en.json) <(jq -S 'keys' frontend/src/i18n/zh.json)
```

### Validate JSON Syntax
```bash
# Check JSON syntax
jq . frontend/src/i18n/en.json > /dev/null && echo "en.json is valid"
jq . frontend/src/i18n/zh.json > /dev/null && echo "zh.json is valid"
```

## Examples from the Project

### Platform Dashboard
```json
{
  "platformDashboard": {
    "timeRange": {
      "last7Days": "Last 7 Days",
      "last30Days": "Last 30 Days"
    },
    "metrics": {
      "avgTokensPerSession": {
        "label": "Avg Tokens / Session",
        "tooltip": "Average token consumption per chat session..."
      }
    }
  }
}
```

### Admin Module
```json
{
  "admin": {
    "tab": {
      "dashboard": "Dashboard",
      "platform": "Platform",
      "companies": "Companies"
    },
    "platformSettings": "Platform Settings",
    "platformSettingsDesc": "Manage platform-wide settings..."
  }
}
```

## Troubleshooting

### Translation Not Showing
1. Check if the key exists in both `en.json` and `zh.json`
2. Verify the key path matches exactly
3. Ensure JSON syntax is valid
4. Check browser console for i18n errors

### Language Not Switching
1. Verify language files are properly loaded
2. Check i18n configuration in `frontend/src/i18n/index.ts`
3. Ensure language selector is working correctly

### Missing Interpolation
1. Check that variables are passed to `t()` function
2. Verify variable names match in translation string
3. Example: `t('greeting', { name: 'John' })` for `"greeting": "Hello {{name}}"`

## Related Files

- `frontend/src/i18n/index.ts` - i18n configuration
- `frontend/src/i18n/en.json` - English translations
- `frontend/src/i18n/zh.json` - Chinese translations
- `.agents/rules/design_and_dev.md` - Development guidelines

## Additional Resources

- [i18next Documentation](https://www.i18next.com/)
- [React i18next](https://react.i18next.com/)
- Project Architecture: `ARCHITECTURE_SPEC_EN.md`
