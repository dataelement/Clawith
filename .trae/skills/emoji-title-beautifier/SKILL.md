---
name: "emoji-title-beautifier"
description: "Helps add emojis to UI titles and labels for visual enhancement. Invoke when user wants to beautify titles, add emojis, or improve UI visual appeal."
---

# Emoji Title Beautifier

This skill provides guidelines and best practices for adding emojis to UI titles, labels, and other text elements to enhance visual appeal and user experience.

## When to Add Emojis

**Add emojis when:**
- Enhancing page titles and section headers
- Improving table column headers
- Adding visual indicators to navigation items
- Making labels more intuitive and recognizable
- Improving the visual hierarchy of UI elements

**Avoid emojis when:**
- In formal or legal text
- In error messages that need to be clear and professional
- In places where screen readers might struggle
- When it could distract from critical information

## Emoji Selection Guidelines

### Category-Based Emoji Mapping

#### Business & Organization
| Emoji | Meaning | Use Case |
|-------|---------|----------|
| 🏢 | Office Building | Company, organization, enterprise |
| 👥 | People | Users, team members, contacts |
| 👤 | Person | User profile, individual |
| 🤝 | Handshake | Partnership, agreement |

#### Technology & Development
| Emoji | Meaning | Use Case |
|-------|---------|----------|
| 🤖 | Robot | AI agents, bots, automation |
| ⚙️ | Gear | Settings, configuration, operations |
| 🔧 | Wrench | Tools, maintenance, repair |
| 💻 | Computer | Development, coding, technical |
| 🔌 | Plug | Integration, connection, API |

#### Security & Authentication
| Emoji | Meaning | Use Case |
|-------|---------|----------|
| 🔐 | Lock with Key | Security, authentication, SSO |
| 🔒 | Lock | Privacy, secure, protected |
| 🔑 | Key | Password, access, credentials |
| 🛡️ | Shield | Protection, security features |

#### Communication
| Emoji | Meaning | Use Case |
|-------|---------|----------|
| 📧 | Email | Email address, messaging |
| 💬 | Speech Bubble | Chat, messages, comments |
| 📢 | Loudspeaker | Announcements, notifications |
| ✉️ | Envelope | Mail, invitations |

#### Data & Analytics
| Emoji | Meaning | Use Case |
|-------|---------|----------|
| 📊 | Chart | Statistics, analytics, usage |
| 📈 | Increasing Chart | Growth, trends, progress |
| 📉 | Decreasing Chart | Decline, reduction |
| 📋 | Clipboard | Reports, lists, documents |

#### Time & Status
| Emoji | Meaning | Use Case |
|-------|---------|----------|
| 📅 | Calendar | Date, schedule, time |
| ⏰ | Clock | Time, deadline, schedule |
| ⚡ | Lightning | Status, active, fast |
| ✅ | Check Mark | Completed, success, active |
| ❌ | Cross Mark | Disabled, failed, inactive |

#### Content & Media
| Emoji | Meaning | Use Case |
|-------|---------|----------|
| 📝 | Memo | Notes, documentation, content |
| 📄 | Page | Documents, files |
| 📁 | Folder | Directory, file organization |
| 🖼️ | Frame | Images, media |

#### Navigation & Actions
| Emoji | Meaning | Use Case |
|-------|---------|----------|
| 🏠 | House | Home, dashboard, main page |
| ➡️ | Arrow | Navigation, next, proceed |
| ⬆️ | Up Arrow | Upload, increase, move up |
| 🔄 | Counter-clockwise | Refresh, reset, sync |

## Implementation Process

### Step 1: Identify Text Elements

Identify which UI elements need emoji enhancement:
- Page titles
- Section headers
- Table column headers
- Navigation items
- Form labels
- Button text (use sparingly)

### Step 2: Choose Appropriate Emojis

1. **Match the context**: Select emojis that accurately represent the content
2. **Consider cultural differences**: Some emojis may have different meanings in different cultures
3. **Maintain consistency**: Use the same emoji for the same concept throughout the application
4. **Keep it simple**: Don't overuse emojis; one per element is usually sufficient

### Step 3: Add to Internationalization Files

**In `frontend/src/i18n/en.json`:**
```json
{
  "moduleName": {
    "title": "📊 Dashboard",
    "users": "👥 Users",
    "settings": "⚙️ Settings"
  }
}
```

**In `frontend/src/i18n/zh.json`:**
```json
{
  "moduleName": {
    "title": "📊 仪表盘",
    "users": "👥 用户数",
    "settings": "⚙️ 设置"
  }
}
```

### Step 4: Verify Consistency

Ensure both language files have the same emojis in corresponding keys.

## Best Practices

### 1. Consistency is Key
Use the same emoji for the same concept throughout the application:
- ✅ Good: Always use 🏢 for "Company"
- ❌ Bad: Use 🏢 in one place and 🏛️ in another

### 2. Semantic Relevance
Choose emojis that semantically match the content:
- ✅ Good: 📧 for email addresses
- ❌ Bad: 📧 for phone numbers

### 3. Visual Balance
Don't overcrowd the UI with emojis:
- Use emojis primarily for headers and labels
- Avoid emojis in body text or descriptions
- Keep button text clean (emojis optional)

### 4. Accessibility Considerations
- Emojis should enhance, not replace text
- Ensure screen readers can still understand the content
- Provide text alternatives where necessary

### 5. Professional Tone
Maintain professionalism:
- Use standard, widely-recognized emojis
- Avoid overly casual or playful emojis in business contexts
- Consider the target audience

## Common Patterns

### Navigation Tabs
```json
{
  "tab": {
    "dashboard": "📊 Dashboard",
    "platform": "⚙️ Platform",
    "companies": "🏢 Companies"
  }
}
```

### Table Headers
```json
{
  "table": {
    "company": "🏢 Company",
    "users": "👥 Users",
    "agents": "🤖 Agents",
    "status": "⚡ Status",
    "action": "⚙️ Action"
  }
}
```

### Form Labels
```json
{
  "form": {
    "email": "📧 Email Address",
    "password": "🔑 Password",
    "name": "👤 Name"
  }
}
```

### Status Indicators
```json
{
  "status": {
    "active": "✅ Active",
    "inactive": "❌ Inactive",
    "pending": "⏳ Pending"
  }
}
```

## Emoji Selection by Category

### Dashboard & Analytics
- 📊 Dashboard, analytics, statistics
- 📈 Growth, increase, progress
- 📉 Decrease, decline
- 🎯 Goals, targets, objectives

### User Management
- 👥 Users, team, group
- 👤 Individual user, profile
- 🔐 Authentication, security
- 🎫 Access, permissions

### Content Management
- 📝 Notes, documentation
- 📄 Documents, files
- 📁 Folders, directories
- 🖼️ Images, media

### Communication
- 📧 Email
- 💬 Chat, messages
- 📢 Announcements
- 🔔 Notifications

### Settings & Configuration
- ⚙️ Settings, configuration
- 🔧 Tools, maintenance
- 🎨 Appearance, themes
- 🔌 Integrations, plugins

### Status & Actions
- ✅ Success, completed, active
- ❌ Error, failed, inactive
- ⏳ Pending, loading
- 🔄 Refresh, sync, reset

## Testing Checklist

Before finalizing emoji additions:

- [ ] Emojis are semantically appropriate
- [ ] Both language files have matching emojis
- [ ] Emojis render correctly in all target browsers
- [ ] Emojis don't break layout or alignment
- [ ] Screen readers can handle the content
- [ ] Emojis are consistent across the application
- [ ] Professional tone is maintained
- [ ] Visual hierarchy is improved, not cluttered

## Examples from the Clawith Project

### Admin Module
```json
{
  "admin": {
    "tab": {
      "dashboard": "📊 Dashboard",
      "platform": "⚙️ Platform",
      "companies": "🏢 Companies"
    },
    "company": "🏢 Company",
    "sso": "🔐 SSO",
    "orgAdmin": "📧 Admin Email",
    "users": "👥 Users",
    "agents": "🤖 Agents",
    "tokens": "📊 Token Usage",
    "createdAt": "📅 Created",
    "status": "⚡ Status",
    "action": "⚙️ Action"
  }
}
```

### Platform Dashboard
```json
{
  "platformDashboard": {
    "metrics": {
      "avgTokensPerSession": {
        "label": "📊 Avg Tokens / Session"
      },
      "retention7Day": {
        "label": "📈 7-Day Retention"
      }
    }
  }
}
```

## Troubleshooting

### Issue: Emojis Not Displaying
**Solution**: 
- Check browser compatibility
- Ensure UTF-8 encoding in HTML
- Verify JSON file encoding

### Issue: Emojis Breaking Layout
**Solution**:
- Adjust column widths
- Use CSS to control emoji size
- Consider using emoji at the start of text only

### Issue: Inconsistent Emojis
**Solution**:
- Create an emoji style guide
- Document emoji usage in comments
- Review all translations for consistency

## Related Documentation

- [Internationalization (i18n) Helper](/.trae/skills/i18n-helper/SKILL.md)
- [Email Templates I18N Guide](/DEV/docs/EMAIL_TEMPLATES_I18N.md)
- [Bug Fixes Log](/DEV/docs/BUG_FIXES.md)

## Additional Resources

- [Full Emoji List](https://unicode.org/emoji/charts/full-emoji-list.html)
- [Emojipedia](https://emojipedia.org/)
- [Emoji Design Guidelines](https://unicode.org/emoji/charts/emoji-design-guidelines.html)

---

**Last Updated**: 2026-04-03  
**Version**: 1.0  
**Author**: Clawith Development Team
