"""Add template source keys and virtual org tables.

Revision ID: 20260321_add_virtual_org
Revises: add_published_pages
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260321_add_virtual_org"
down_revision = "add_published_pages"
branch_labels = None
depends_on = None


SOURCE_KEY_BACKFILL_ROWS = [
    {
        "name": "人类学家",
        "source_key": "academic/academic-anthropologist.md"
    },
    {
        "name": "地理学家",
        "source_key": "academic/academic-geographer.md"
    },
    {
        "name": "历史学家",
        "source_key": "academic/academic-historian.md"
    },
    {
        "name": "叙事学家",
        "source_key": "academic/academic-narratologist.md"
    },
    {
        "name": "心理学家",
        "source_key": "academic/academic-psychologist.md"
    },
    {
        "name": "学习规划师",
        "source_key": "academic/academic-study-planner.md"
    },
    {
        "name": "品牌守护者",
        "source_key": "design/design-brand-guardian.md"
    },
    {
        "name": "图像提示词工程师",
        "source_key": "design/design-image-prompt-engineer.md"
    },
    {
        "name": "包容性视觉专家",
        "source_key": "design/design-inclusive-visuals-specialist.md"
    },
    {
        "name": "UI 设计师",
        "source_key": "design/design-ui-designer.md"
    },
    {
        "name": "UX 架构师",
        "source_key": "design/design-ux-architect.md"
    },
    {
        "name": "UX 研究员",
        "source_key": "design/design-ux-researcher.md"
    },
    {
        "name": "视觉叙事师",
        "source_key": "design/design-visual-storyteller.md"
    },
    {
        "name": "趣味注入师",
        "source_key": "design/design-whimsy-injector.md"
    },
    {
        "name": "AI 数据修复工程师",
        "source_key": "engineering/engineering-ai-data-remediation-engineer.md"
    },
    {
        "name": "AI 工程师",
        "source_key": "engineering/engineering-ai-engineer.md"
    },
    {
        "name": "自主优化架构师",
        "source_key": "engineering/engineering-autonomous-optimization-architect.md"
    },
    {
        "name": "后端架构师",
        "source_key": "engineering/engineering-backend-architect.md"
    },
    {
        "name": "代码审查员",
        "source_key": "engineering/engineering-code-reviewer.md"
    },
    {
        "name": "数据工程师",
        "source_key": "engineering/engineering-data-engineer.md"
    },
    {
        "name": "数据库优化师",
        "source_key": "engineering/engineering-database-optimizer.md"
    },
    {
        "name": "DevOps 自动化师",
        "source_key": "engineering/engineering-devops-automator.md"
    },
    {
        "name": "钉钉集成开发工程师",
        "source_key": "engineering/engineering-dingtalk-integration-developer.md"
    },
    {
        "name": "嵌入式固件工程师",
        "source_key": "engineering/engineering-embedded-firmware-engineer.md"
    },
    {
        "name": "嵌入式 Linux 驱动工程师",
        "source_key": "engineering/engineering-embedded-linux-driver-engineer.md"
    },
    {
        "name": "飞书集成开发工程师",
        "source_key": "engineering/engineering-feishu-integration-developer.md"
    },
    {
        "name": "FPGA/ASIC 数字设计工程师",
        "source_key": "engineering/engineering-fpga-digital-design-engineer.md"
    },
    {
        "name": "前端开发者",
        "source_key": "engineering/engineering-frontend-developer.md"
    },
    {
        "name": "Git 工作流大师",
        "source_key": "engineering/engineering-git-workflow-master.md"
    },
    {
        "name": "故障响应指挥官",
        "source_key": "engineering/engineering-incident-response-commander.md"
    },
    {
        "name": "IoT 方案架构师",
        "source_key": "engineering/engineering-iot-solution-architect.md"
    },
    {
        "name": "移动应用开发者",
        "source_key": "engineering/engineering-mobile-app-builder.md"
    },
    {
        "name": "快速原型师",
        "source_key": "engineering/engineering-rapid-prototyper.md"
    },
    {
        "name": "安全工程师",
        "source_key": "engineering/engineering-security-engineer.md"
    },
    {
        "name": "高级开发者",
        "source_key": "engineering/engineering-senior-developer.md"
    },
    {
        "name": "软件架构师",
        "source_key": "engineering/engineering-software-architect.md"
    },
    {
        "name": "Solidity 智能合约工程师",
        "source_key": "engineering/engineering-solidity-smart-contract-engineer.md"
    },
    {
        "name": "SRE (站点可靠性工程师)",
        "source_key": "engineering/engineering-sre.md"
    },
    {
        "name": "技术文档工程师",
        "source_key": "engineering/engineering-technical-writer.md"
    },
    {
        "name": "威胁检测工程师",
        "source_key": "engineering/engineering-threat-detection-engineer.md"
    },
    {
        "name": "微信小程序开发者",
        "source_key": "engineering/engineering-wechat-mini-program-developer.md"
    },
    {
        "name": "财务预测分析师",
        "source_key": "finance/finance-financial-forecaster.md"
    },
    {
        "name": "金融风控分析师",
        "source_key": "finance/finance-fraud-detector.md"
    },
    {
        "name": "发票管理专家",
        "source_key": "finance/finance-invoice-manager.md"
    },
    {
        "name": "Blender 插件工程师",
        "source_key": "game-development/blender/blender-addon-engineer.md"
    },
    {
        "name": "游戏音频工程师",
        "source_key": "game-development/game-audio-engineer.md"
    },
    {
        "name": "游戏设计师",
        "source_key": "game-development/game-designer.md"
    },
    {
        "name": "Godot 游戏脚本开发者",
        "source_key": "game-development/godot/godot-gameplay-scripter.md"
    },
    {
        "name": "Godot 多人游戏工程师",
        "source_key": "game-development/godot/godot-multiplayer-engineer.md"
    },
    {
        "name": "Godot Shader 开发者",
        "source_key": "game-development/godot/godot-shader-developer.md"
    },
    {
        "name": "关卡设计师",
        "source_key": "game-development/level-designer.md"
    },
    {
        "name": "叙事设计师",
        "source_key": "game-development/narrative-designer.md"
    },
    {
        "name": "Roblox 虚拟形象创作者",
        "source_key": "game-development/roblox-studio/roblox-avatar-creator.md"
    },
    {
        "name": "Roblox 体验设计师",
        "source_key": "game-development/roblox-studio/roblox-experience-designer.md"
    },
    {
        "name": "Roblox 系统脚本工程师",
        "source_key": "game-development/roblox-studio/roblox-systems-scripter.md"
    },
    {
        "name": "技术美术",
        "source_key": "game-development/technical-artist.md"
    },
    {
        "name": "Unity 架构师",
        "source_key": "game-development/unity/unity-architect.md"
    },
    {
        "name": "Unity 编辑器工具开发者",
        "source_key": "game-development/unity/unity-editor-tool-developer.md"
    },
    {
        "name": "Unity 多人游戏工程师",
        "source_key": "game-development/unity/unity-multiplayer-engineer.md"
    },
    {
        "name": "Unity Shader Graph 美术师",
        "source_key": "game-development/unity/unity-shader-graph-artist.md"
    },
    {
        "name": "Unreal 多人游戏架构师",
        "source_key": "game-development/unreal-engine/unreal-multiplayer-architect.md"
    },
    {
        "name": "Unreal 系统工程师",
        "source_key": "game-development/unreal-engine/unreal-systems-engineer.md"
    },
    {
        "name": "Unreal 技术美术",
        "source_key": "game-development/unreal-engine/unreal-technical-artist.md"
    },
    {
        "name": "Unreal 世界构建师",
        "source_key": "game-development/unreal-engine/unreal-world-builder.md"
    },
    {
        "name": "绩效管理专家",
        "source_key": "hr/hr-performance-reviewer.md"
    },
    {
        "name": "招聘专家",
        "source_key": "hr/hr-recruiter.md"
    },
    {
        "name": "Backend Architect",
        "source_key": "integrations/mcp-memory/backend-architect-with-memory.md"
    },
    {
        "name": "合同审查专家",
        "source_key": "legal/legal-contract-reviewer.md"
    },
    {
        "name": "制度文件撰写专家",
        "source_key": "legal/legal-policy-writer.md"
    },
    {
        "name": "AI Citation Strategist",
        "source_key": "marketing/marketing-ai-citation-strategist.md"
    },
    {
        "name": "应用商店优化师",
        "source_key": "marketing/marketing-app-store-optimizer.md"
    },
    {
        "name": "百度 SEO 专家",
        "source_key": "marketing/marketing-baidu-seo-specialist.md"
    },
    {
        "name": "Bilibili Content Strategist",
        "source_key": "marketing/marketing-bilibili-content-strategist.md"
    },
    {
        "name": "B站内容策略师",
        "source_key": "marketing/marketing-bilibili-strategist.md"
    },
    {
        "name": "图书联合作者",
        "source_key": "marketing/marketing-book-co-author.md"
    },
    {
        "name": "轮播图增长引擎",
        "source_key": "marketing/marketing-carousel-growth-engine.md"
    },
    {
        "name": "中国电商运营专家",
        "source_key": "marketing/marketing-china-ecommerce-operator.md"
    },
    {
        "name": "内容创作者",
        "source_key": "marketing/marketing-content-creator.md"
    },
    {
        "name": "跨境电商运营专家",
        "source_key": "marketing/marketing-cross-border-ecommerce.md"
    },
    {
        "name": "抖音策略师",
        "source_key": "marketing/marketing-douyin-strategist.md"
    },
    {
        "name": "电商运营师",
        "source_key": "marketing/marketing-ecommerce-operator.md"
    },
    {
        "name": "增长黑客",
        "source_key": "marketing/marketing-growth-hacker.md"
    },
    {
        "name": "Instagram 策展师",
        "source_key": "marketing/marketing-instagram-curator.md"
    },
    {
        "name": "知识付费产品策划师",
        "source_key": "marketing/marketing-knowledge-commerce-strategist.md"
    },
    {
        "name": "快手策略师",
        "source_key": "marketing/marketing-kuaishou-strategist.md"
    },
    {
        "name": "LinkedIn 内容创作专家",
        "source_key": "marketing/marketing-linkedin-content-creator.md"
    },
    {
        "name": "直播电商主播教练",
        "source_key": "marketing/marketing-livestream-commerce-coach.md"
    },
    {
        "name": "播客内容策略师",
        "source_key": "marketing/marketing-podcast-strategist.md"
    },
    {
        "name": "私域流量运营师",
        "source_key": "marketing/marketing-private-domain-operator.md"
    },
    {
        "name": "Reddit 社区运营",
        "source_key": "marketing/marketing-reddit-community-builder.md"
    },
    {
        "name": "SEO专家",
        "source_key": "marketing/marketing-seo-specialist.md"
    },
    {
        "name": "短视频剪辑指导师",
        "source_key": "marketing/marketing-short-video-editing-coach.md"
    },
    {
        "name": "社交媒体策略师",
        "source_key": "marketing/marketing-social-media-strategist.md"
    },
    {
        "name": "TikTok 策略师",
        "source_key": "marketing/marketing-tiktok-strategist.md"
    },
    {
        "name": "Twitter 互动官",
        "source_key": "marketing/marketing-twitter-engager.md"
    },
    {
        "name": "微信公众号管理",
        "source_key": "marketing/marketing-wechat-official-account.md"
    },
    {
        "name": "微信公众号运营",
        "source_key": "marketing/marketing-wechat-operator.md"
    },
    {
        "name": "微博运营策略师",
        "source_key": "marketing/marketing-weibo-strategist.md"
    },
    {
        "name": "微信视频号运营策略师",
        "source_key": "marketing/marketing-weixin-channels-strategist.md"
    },
    {
        "name": "小红书运营专家",
        "source_key": "marketing/marketing-xiaohongshu-operator.md"
    },
    {
        "name": "小红书专家",
        "source_key": "marketing/marketing-xiaohongshu-specialist.md"
    },
    {
        "name": "知乎策略师",
        "source_key": "marketing/marketing-zhihu-strategist.md"
    },
    {
        "name": "付费媒体审计师",
        "source_key": "paid-media/paid-media-auditor.md"
    },
    {
        "name": "广告创意策略师",
        "source_key": "paid-media/paid-media-creative-strategist.md"
    },
    {
        "name": "社交广告策略师",
        "source_key": "paid-media/paid-media-paid-social-strategist.md"
    },
    {
        "name": "PPC 竞价策略师",
        "source_key": "paid-media/paid-media-ppc-strategist.md"
    },
    {
        "name": "程序化广告采买专家",
        "source_key": "paid-media/paid-media-programmatic-buyer.md"
    },
    {
        "name": "搜索词分析师",
        "source_key": "paid-media/paid-media-search-query-analyst.md"
    },
    {
        "name": "追踪与归因专家",
        "source_key": "paid-media/paid-media-tracking-specialist.md"
    },
    {
        "name": "行为助推引擎",
        "source_key": "product/product-behavioral-nudge-engine.md"
    },
    {
        "name": "反馈分析师",
        "source_key": "product/product-feedback-synthesizer.md"
    },
    {
        "name": "产品经理",
        "source_key": "product/product-manager.md"
    },
    {
        "name": "Sprint 排序师",
        "source_key": "product/product-sprint-prioritizer.md"
    },
    {
        "name": "趋势研究员",
        "source_key": "product/product-trend-researcher.md"
    },
    {
        "name": "实验追踪员",
        "source_key": "project-management/project-management-experiment-tracker.md"
    },
    {
        "name": "Jira工作流管家",
        "source_key": "project-management/project-management-jira-workflow-steward.md"
    },
    {
        "name": "项目牧羊人",
        "source_key": "project-management/project-management-project-shepherd.md"
    },
    {
        "name": "工作室运营",
        "source_key": "project-management/project-management-studio-operations.md"
    },
    {
        "name": "工作室制片人",
        "source_key": "project-management/project-management-studio-producer.md"
    },
    {
        "name": "高级项目经理",
        "source_key": "project-management/project-manager-senior.md"
    },
    {
        "name": "客户拓展策略师",
        "source_key": "sales/sales-account-strategist.md"
    },
    {
        "name": "销售教练",
        "source_key": "sales/sales-coach.md"
    },
    {
        "name": "赢单策略师",
        "source_key": "sales/sales-deal-strategist.md"
    },
    {
        "name": "Discovery 教练",
        "source_key": "sales/sales-discovery-coach.md"
    },
    {
        "name": "售前工程师",
        "source_key": "sales/sales-engineer.md"
    },
    {
        "name": "Outbound 策略师",
        "source_key": "sales/sales-outbound-strategist.md"
    },
    {
        "name": "Pipeline 分析师",
        "source_key": "sales/sales-pipeline-analyst.md"
    },
    {
        "name": "投标策略师",
        "source_key": "sales/sales-proposal-strategist.md"
    },
    {
        "name": "macOS Metal 空间工程师",
        "source_key": "spatial-computing/macos-spatial-metal-engineer.md"
    },
    {
        "name": "终端集成专家",
        "source_key": "spatial-computing/terminal-integration-specialist.md"
    },
    {
        "name": "visionOS 空间工程师",
        "source_key": "spatial-computing/visionos-spatial-engineer.md"
    },
    {
        "name": "XR 座舱交互专家",
        "source_key": "spatial-computing/xr-cockpit-interaction-specialist.md"
    },
    {
        "name": "XR 沉浸式开发者",
        "source_key": "spatial-computing/xr-immersive-developer.md"
    },
    {
        "name": "XR 界面架构师",
        "source_key": "spatial-computing/xr-interface-architect.md"
    },
    {
        "name": "应付账款智能体",
        "source_key": "specialized/accounts-payable-agent.md"
    },
    {
        "name": "身份信任架构师",
        "source_key": "specialized/agentic-identity-trust.md"
    },
    {
        "name": "智能体编排者",
        "source_key": "specialized/agents-orchestrator.md"
    },
    {
        "name": "自动化治理架构师",
        "source_key": "specialized/automation-governance-architect.md"
    },
    {
        "name": "区块链安全审计师",
        "source_key": "specialized/blockchain-security-auditor.md"
    },
    {
        "name": "合规审计师",
        "source_key": "specialized/compliance-auditor.md"
    },
    {
        "name": "企业培训课程设计师",
        "source_key": "specialized/corporate-training-designer.md"
    },
    {
        "name": "数据整合师",
        "source_key": "specialized/data-consolidation-agent.md"
    },
    {
        "name": "高考志愿填报顾问",
        "source_key": "specialized/gaokao-college-advisor.md"
    },
    {
        "name": "政务数字化售前顾问",
        "source_key": "specialized/government-digital-presales-consultant.md"
    },
    {
        "name": "医疗健康营销合规师",
        "source_key": "specialized/healthcare-marketing-compliance.md"
    },
    {
        "name": "身份图谱操作员",
        "source_key": "specialized/identity-graph-operator.md"
    },
    {
        "name": "LSP 索引工程师",
        "source_key": "specialized/lsp-index-engineer.md"
    },
    {
        "name": "提示词工程师",
        "source_key": "specialized/prompt-engineer.md"
    },
    {
        "name": "Recruitment Specialist",
        "source_key": "specialized/recruitment-specialist.md"
    },
    {
        "name": "报告分发师",
        "source_key": "specialized/report-distribution-agent.md"
    },
    {
        "name": "销售数据提取师",
        "source_key": "specialized/sales-data-extraction-agent.md"
    },
    {
        "name": "AI 治理政策专家",
        "source_key": "specialized/specialized-ai-policy-writer.md"
    },
    {
        "name": "文化智能策略师",
        "source_key": "specialized/specialized-cultural-intelligence-strategist.md"
    },
    {
        "name": "开发者布道师",
        "source_key": "specialized/specialized-developer-advocate.md"
    },
    {
        "name": "文档生成器",
        "source_key": "specialized/specialized-document-generator.md"
    },
    {
        "name": "French Consulting Market Navigator",
        "source_key": "specialized/specialized-french-consulting-market.md"
    },
    {
        "name": "Korean Business Navigator",
        "source_key": "specialized/specialized-korean-business-navigator.md"
    },
    {
        "name": "MCP 构建器",
        "source_key": "specialized/specialized-mcp-builder.md"
    },
    {
        "name": "会议效率专家",
        "source_key": "specialized/specialized-meeting-assistant.md"
    },
    {
        "name": "模型 QA 专家",
        "source_key": "specialized/specialized-model-qa.md"
    },
    {
        "name": "动态定价策略师",
        "source_key": "specialized/specialized-pricing-optimizer.md"
    },
    {
        "name": "企业风险评估师",
        "source_key": "specialized/specialized-risk-assessor.md"
    },
    {
        "name": "Salesforce 架构师",
        "source_key": "specialized/specialized-salesforce-architect.md"
    },
    {
        "name": "工作流架构师",
        "source_key": "specialized/specialized-workflow-architect.md"
    },
    {
        "name": "留学规划顾问",
        "source_key": "specialized/study-abroad-advisor.md"
    },
    {
        "name": "Supply Chain Strategist",
        "source_key": "specialized/supply-chain-strategist.md"
    },
    {
        "name": "ZK 管家",
        "source_key": "specialized/zk-steward.md"
    },
    {
        "name": "库存预测专家",
        "source_key": "supply-chain/supply-chain-inventory-forecaster.md"
    },
    {
        "name": "物流路线优化师",
        "source_key": "supply-chain/supply-chain-route-optimizer.md"
    },
    {
        "name": "供应商评估专家",
        "source_key": "supply-chain/supply-chain-vendor-evaluator.md"
    },
    {
        "name": "数据分析师",
        "source_key": "support/support-analytics-reporter.md"
    },
    {
        "name": "高管摘要师",
        "source_key": "support/support-executive-summary-generator.md"
    },
    {
        "name": "财务追踪员",
        "source_key": "support/support-finance-tracker.md"
    },
    {
        "name": "基础设施运维师",
        "source_key": "support/support-infrastructure-maintainer.md"
    },
    {
        "name": "法务合规员",
        "source_key": "support/support-legal-compliance-checker.md"
    },
    {
        "name": "招聘运营专家",
        "source_key": "support/support-recruitment-specialist.md"
    },
    {
        "name": "供应链采购策略师",
        "source_key": "support/support-supply-chain-strategist.md"
    },
    {
        "name": "客服响应者",
        "source_key": "support/support-support-responder.md"
    },
    {
        "name": "无障碍审核员",
        "source_key": "testing/testing-accessibility-auditor.md"
    },
    {
        "name": "API 测试员",
        "source_key": "testing/testing-api-tester.md"
    },
    {
        "name": "嵌入式测试工程师",
        "source_key": "testing/testing-embedded-qa-engineer.md"
    },
    {
        "name": "证据收集者",
        "source_key": "testing/testing-evidence-collector.md"
    },
    {
        "name": "性能基准师",
        "source_key": "testing/testing-performance-benchmarker.md"
    },
    {
        "name": "现实检验者",
        "source_key": "testing/testing-reality-checker.md"
    },
    {
        "name": "测试结果分析师",
        "source_key": "testing/testing-test-results-analyzer.md"
    },
    {
        "name": "工具评估师",
        "source_key": "testing/testing-tool-evaluator.md"
    },
    {
        "name": "工作流优化师",
        "source_key": "testing/testing-workflow-optimizer.md"
    }
]


def get_source_key_backfill_rows() -> list[dict[str, str]]:
    return [dict(row) for row in SOURCE_KEY_BACKFILL_ROWS]


def _backfill_source_keys() -> int:
    connection = op.get_bind()
    update_stmt = sa.text(
        """
        UPDATE agent_templates
        SET source_key = :source_key
        WHERE name = :name
          AND (:source_key <> '')
          AND (source_key IS NULL OR source_key = '')
        """
    )

    executed_count = 0
    for row in get_source_key_backfill_rows():
        connection.execute(update_stmt, row)
        executed_count += 1

    return executed_count


def upgrade() -> None:
    op.execute("ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS source_key VARCHAR(255)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_templates_source_key ON agent_templates(source_key)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS virtual_departments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(200) NOT NULL,
            slug VARCHAR(80) NOT NULL,
            parent_id UUID REFERENCES virtual_departments(id) ON DELETE SET NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            org_level VARCHAR(30) NOT NULL DEFAULT 'department',
            is_core BOOLEAN NOT NULL DEFAULT TRUE,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_virtual_departments_tenant_slug "
        "ON virtual_departments(tenant_id, slug)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_virtual_departments_tenant_id ON virtual_departments(tenant_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_virtual_org (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            department_id UUID NOT NULL REFERENCES virtual_departments(id) ON DELETE RESTRICT,
            template_id UUID REFERENCES agent_templates(id),
            title VARCHAR(200) NOT NULL DEFAULT '',
            level VARCHAR(10) NOT NULL DEFAULT 'L3' CHECK (level IN ('L1', 'L2', 'L3', 'L4', 'L5')),
            org_bucket VARCHAR(20) NOT NULL DEFAULT 'core' CHECK (org_bucket IN ('core', 'expert')),
            manager_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
            is_primary BOOLEAN NOT NULL DEFAULT TRUE,
            is_org_primary_instance BOOLEAN NOT NULL DEFAULT FALSE,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            is_locked BOOLEAN NOT NULL DEFAULT FALSE,
            notes TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_virtual_org_agent_id ON agent_virtual_org(agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_virtual_org_department_id ON agent_virtual_org(department_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_virtual_org_template_id ON agent_virtual_org(template_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_virtual_org_tenant_id ON agent_virtual_org(tenant_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_virtual_org_primary_per_agent "
        "ON agent_virtual_org(agent_id) WHERE is_primary"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_virtual_org_primary_instance_per_template "
        "ON agent_virtual_org(tenant_id, template_id) "
        "WHERE is_org_primary_instance AND template_id IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_virtual_tags (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            tag VARCHAR(80) NOT NULL,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_virtual_tags_agent_id ON agent_virtual_tags(agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_virtual_tags_tenant_id ON agent_virtual_tags(tenant_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_virtual_tags_tenant_agent_tag "
        "ON agent_virtual_tags(tenant_id, agent_id, tag)"
    )

    _backfill_source_keys()


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_agent_virtual_tags_tenant_agent_tag")
    op.execute("DROP INDEX IF EXISTS ix_agent_virtual_tags_tenant_id")
    op.execute("DROP INDEX IF EXISTS ix_agent_virtual_tags_agent_id")
    op.execute("DROP TABLE IF EXISTS agent_virtual_tags")

    op.execute("DROP INDEX IF EXISTS uq_agent_virtual_org_primary_instance_per_template")
    op.execute("DROP INDEX IF EXISTS uq_agent_virtual_org_primary_per_agent")
    op.execute("DROP INDEX IF EXISTS ix_agent_virtual_org_tenant_id")
    op.execute("DROP INDEX IF EXISTS ix_agent_virtual_org_template_id")
    op.execute("DROP INDEX IF EXISTS ix_agent_virtual_org_department_id")
    op.execute("DROP INDEX IF EXISTS ix_agent_virtual_org_agent_id")
    op.execute("DROP TABLE IF EXISTS agent_virtual_org")

    op.execute("DROP INDEX IF EXISTS ix_virtual_departments_tenant_id")
    op.execute("DROP INDEX IF EXISTS uq_virtual_departments_tenant_slug")
    op.execute("DROP TABLE IF EXISTS virtual_departments")

    op.execute("DROP INDEX IF EXISTS ix_agent_templates_source_key")
    op.execute("ALTER TABLE agent_templates DROP COLUMN IF EXISTS source_key")
