import { useEffect, useState } from "react";
import { ActionIcon, Button, Drawer, Input, Switch, Table, Tabs, Tag, TextArea } from "@lobehub/ui/base-ui";
import { ArrowRight, ArrowUp, Bot, ChevronRight, FileText, LayoutGrid, Menu, MessageSquare, Moon, Search, Settings2, Sparkles, Star, Sun } from "lucide-react";
import { useTheme } from "../../contexts/ThemeContext";
import { AgentIdentityAvatar } from "../../components/agent/AgentIdentityAvatar";
import { LobeSelectField } from "../../components/lobe-ui/LobeSelectField";
import type { AgentProfileAvatarRef } from "../../types/agentProfile";
import styles from "./lobeStylePreview.module.css";

type View = "market" | "models" | "chat";
const views = [
  { id: "market" as const, label: "专家市场", icon: LayoutGrid },
  { id: "chat" as const, label: "对话工作区", icon: MessageSquare },
  { id: "models" as const, label: "模型管理", icon: Settings2 },
];
const experts: Array<{ id: string; name: string; description: string; category: string; tasks: number; avatar: AgentProfileAvatarRef }> = [
  { id: "preview-quality", name: "质量合规专家", description: "梳理质量文件与合规要求，让每一次审核都有据可依。", category: "质量与合规", tasks: 128, avatar: "builtin:assistant" },
  { id: "preview-document", name: "文档审核专家", description: "检查文档中的条款、术语与格式，整理需要复核的内容。", category: "文档处理", tasks: 96, avatar: "builtin:document" },
  { id: "preview-research", name: "研究分析助手", description: "从资料中提取关键信息，形成清晰、可追溯的研究结论。", category: "数据分析", tasks: 72, avatar: "builtin:research" },
  { id: "preview-translate", name: "专业翻译助手", description: "处理中英文专业文档，在表达准确的同时保留原有结构。", category: "文档处理", tasks: 84, avatar: "builtin:document" },
  { id: "preview-data", name: "运营数据分析师", description: "发现数据中的变化与异常，把分析转化为下一步行动。", category: "数据分析", tasks: 53, avatar: "builtin:research" },
  { id: "preview-sop", name: "制度与流程助手", description: "快速查找制度和操作流程，回答日常工作中的具体问题。", category: "质量与合规", tasks: 215, avatar: "builtin:assistant" },
];
const initialModels = [
  { id: "claude", name: "Claude Sonnet", provider: "Anthropic", context: "200K", enabled: true, default: true, initials: "A" },
  { id: "gpt", name: "GPT-5", provider: "OpenAI", context: "128K", enabled: true, default: false, initials: "O" },
  { id: "deepseek", name: "DeepSeek V3", provider: "DeepSeek", context: "128K", enabled: true, default: false, initials: "D" },
  { id: "qwen", name: "Qwen3", provider: "通义千问", context: "128K", enabled: false, default: false, initials: "Q" },
];

function Sidebar({ view, onNavigate }: { view: View; onNavigate: (view: View) => void }) {
  return <div className={styles.sidebarContent}>
    <div className={styles.brand}><span className={styles.brandIcon}><Bot size={19} /></span><strong>AI Platform</strong></div>
    <div className={styles.workspaceLabel}>企业工作空间</div>
    <nav aria-label="样稿页面" className={styles.navigation}>
      {views.map(({ id, label, icon: Icon }) => <Button
        key={id} type="text" icon={<Icon size={18} />} data-preview-view={id}
        aria-current={view === id ? "page" : undefined}
        onClick={() => onNavigate(id)} className={view === id ? `${styles.navItem} ${styles.navItemActive}` : styles.navItem}
      >{label}</Button>)}
    </nav>
    <div className={styles.recentSection}>
      <div className={styles.workspaceLabel}>最近使用</div>
      <Button type="text" icon={<FileText size={16} />} onClick={() => onNavigate("chat")} className={styles.navItem}>采购合同审核</Button>
    </div>
    <div className={styles.sidebarFooter}><span className={styles.userAvatar}>预</span><div><strong>预览工作空间</strong><span>示例数据</span></div></div>
  </div>;
}

function Market({ onStart }: { onStart: (expert: typeof experts[number]) => void }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("全部专家");
  const [favorites, setFavorites] = useState<string[]>([]);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const visible = experts.filter(expert =>
    (category === "全部专家" || expert.category === category) &&
    (!favoritesOnly || favorites.includes(expert.id)) &&
    `${expert.name} ${expert.description}`.includes(query.trim()),
  );
  return <div className={styles.page} data-preview-market>
    <div className={styles.pageHeading}><div><h1>专家市场</h1><p>从一个具体任务开始，让专业能力触手可及。</p></div>
      <Button type={favoritesOnly ? "fill" : "default"} icon={<Star size={16} />} aria-pressed={favoritesOnly} onClick={() => setFavoritesOnly(!favoritesOnly)}>我的收藏{favorites.length > 0 ? ` · ${favorites.length}` : ""}</Button>
    </div>
    <div className={styles.catalogToolbar}>
      <Tabs activeKey={category} onChange={setCategory} variant="point" size="middle" style={{ width: "auto", minWidth: 0 }}
        items={["全部专家", "质量与合规", "文档处理", "数据分析"].map(label => ({ key: label, label }))} />
      <Input aria-label="搜索专家" placeholder="搜索专家或能力…" value={query} onChange={event => setQuery(event.target.value)} prefix={<Search size={16} />} allowClear onClear={() => setQuery("")} className={styles.search} />
    </div>
    <div className={styles.resultsLabel}>可用专家 <span>{visible.length}</span></div>
    <div className={styles.expertGrid}>
      {visible.map(expert => <article className={styles.expertCard} key={expert.id} data-preview-expert={expert.id}>
        <div className={styles.cardTop}>
          <AgentIdentityAvatar agentId={expert.id} avatarRef={expert.avatar} name={expert.name} size="sm" />
          <ActionIcon icon={Star} size={{ size: 17, blockSize: 40 }} title={favorites.includes(expert.id) ? "取消收藏" : "收藏专家"}
            aria-label={`${favorites.includes(expert.id) ? "取消收藏" : "收藏"}${expert.name}`} aria-pressed={favorites.includes(expert.id)}
            fill={favorites.includes(expert.id) ? "currentColor" : "none"}
            onClick={() => setFavorites(current => current.includes(expert.id) ? current.filter(id => id !== expert.id) : [...current, expert.id])} />
        </div>
        <h2>{expert.name}</h2><p>{expert.description}</p>
        <div className={styles.cardCategory}>{expert.category}</div>
        <div className={styles.cardFooter}><span>{expert.tasks} 次任务</span><Button type="text" icon={<ArrowRight size={15} />} iconPosition="end" style={{ paddingInline: 8, height: "var(--preview-compact-height)" }} onClick={() => onStart(expert)}>开始对话</Button></div>
      </article>)}
    </div>
    {visible.length === 0 && <div className={styles.emptyState}><Search size={24} /><h2>没有找到合适的专家</h2><p>试试其他关键词，或调整当前筛选。</p><Button onClick={() => { setQuery(""); setCategory("全部专家"); setFavoritesOnly(false); }}>重置筛选</Button></div>}
  </div>;
}

function Models({ onConnection }: { onConnection: () => void }) {
  const [models, setModels] = useState(initialModels);
  const [query, setQuery] = useState("");
  const [saved, setSaved] = useState(true);
  const [compact, setCompact] = useState(() => typeof window.matchMedia === "function" && window.matchMedia("(max-width: 767px)").matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(max-width: 767px)");
    const update = () => setCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return <div className={styles.page} data-preview-models>
    <div className={styles.pageHeading}><div><h1>模型管理</h1><p>选择可用模型，为不同任务提供合适的能力。</p></div><Button icon={<Settings2 size={16} />} onClick={onConnection}>连接设置</Button></div>
    <div className={styles.connectionSummary}><div className={styles.inline}><span className={styles.connectionDot} /><strong>企业模型网关</strong><span className={styles.muted}>连接正常</span></div><Tag>4 个示例模型</Tag></div>
    <div className={styles.modelToolbar}><h2>可用模型 <span>{models.length}</span></h2><Input aria-label="搜索模型" placeholder="搜索模型…" value={query} onChange={event => setQuery(event.target.value)} prefix={<Search size={16} />} className={styles.search} /></div>
    <div className={styles.tableShell}>
      <Table size="middle" rowKey="id" pagination={false}
        dataSource={models.filter(model => `${model.name} ${model.provider}`.toLowerCase().includes(query.trim().toLowerCase()))}
        columns={[
          { title: "模型", key: "name", render: (_value, model) => <div className={styles.modelIdentity}><span className={styles.modelAvatar}>{model.initials}</span><div><strong>{model.name}</strong><span>{model.provider}{model.default && <span className={styles.defaultMark}>默认</span>}</span></div></div> },
          ...(compact ? [] : [{ title: "上下文窗口", dataIndex: "context" }]),
          { title: "状态", key: "status", width: 100, render: (_value, model) => model.enabled ? <Tag color="success">已启用</Tag> : <Tag>未启用</Tag> },
          { title: "启用", key: "enabled", width: 72, render: (_value, model) => <><label className="sr-only" htmlFor={`preview-model-${model.id}`}>启用 {model.name}</label><Switch id={`preview-model-${model.id}`} checked={model.enabled} disabled={model.default} onChange={checked => { setModels(current => current.map(item => item.id === model.id ? { ...item, enabled: checked } : item)); setSaved(false); }} /></> },
        ]} />
    </div>
    <div className={styles.modelFooter}><span role="status">{models.filter(model => model.enabled).length} 个模型已启用 · {saved ? "示例配置已保存" : "有未保存的修改"}</span><Button type="primary" size="large" disabled={saved} onClick={() => setSaved(true)}>保存修改</Button></div>
    <p className={styles.footnote}>此处展示样式和本地交互，模型名称与容量均为示例。</p>
  </div>;
}

function Chat({ expert }: { expert: typeof experts[number] }) {
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<string[]>([]);
  const [model, setModel] = useState("claude");
  const send = () => {
    if (!draft.trim()) return;
    setMessages(current => [...current, draft.trim()]);
    setDraft("");
  };
  return <div className={styles.chatPage} data-preview-chat>
    <div className={styles.chatHeader}><AgentIdentityAvatar agentId={expert.id} avatarRef={expert.avatar} name={expert.name} size="sm" /><div><h1>{expert.name}</h1><span>采购合同审核</span></div><Tag color="success">已完成</Tag></div>
    <div className={styles.thread}>
      <div className={styles.threadInner}>
        <div className={styles.userMessage}><span className={styles.attachment}><FileText size={17} />采购合同_示例.docx</span><p>帮我检查这份采购合同，重点关注付款条款、交付责任和需要补充的约定。</p></div>
        <div className={styles.assistantMessage}>
          <div className={styles.answerAuthor}><Sparkles size={17} /><strong>{expert.name}</strong><span>刚刚</span></div>
          <p>已完成初步审核。合同的主要条款齐全，建议在确认前补充以下三处约定。</p>
          <ol className={styles.findings}>
            <li><strong>明确付款的验收条件</strong><p>建议将付款节点与书面验收结果关联，并约定验收反馈的时限。</p></li>
            <li><strong>补充延期交付的处理方式</strong><p>明确延期通知、补救期限和双方责任，减少执行时的理解差异。</p></li>
            <li><strong>统一附件与正文中的名称</strong><p>检查产品名称、数量单位和交付日期，确保附件与正文一致。</p></li>
          </ol>
          <div className={styles.answerNote}><FileText size={16} /><span>3 项待确认 · 建议交由负责人复核</span></div>
        </div>
        {messages.map((message, index) => <div className={styles.userMessage} key={index} data-preview-local-message><p>{message}</p><span className={styles.localMessageNote}>本地样稿消息</span></div>)}
      </div>
    </div>
    <div className={styles.composerRegion}>
      <form className={styles.composer} onSubmit={event => { event.preventDefault(); send(); }}>
        <TextArea aria-label="输入示例消息" placeholder="继续描述你的任务…" value={draft} onChange={event => setDraft(event.target.value)} variant="borderless" autoSize={{ minRows: 2, maxRows: 4 }}
          styles={{ input: { padding: "4px 0", fontSize: 14, lineHeight: 1.6 } }}
          onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} />
        <div className={styles.composerToolbar}><LobeSelectField aria-label="对话模型" value={model} onChange={setModel} variant="borderless" style={{ maxWidth: 180 }} options={initialModels.filter(item => item.enabled).map(item => ({ value: item.id, label: item.name }))} />
          <Button type="primary" htmlType="submit" shape="circle" aria-label="发送示例消息" icon={<ArrowUp size={19} />} disabled={!draft.trim()} style={{ width: 40, height: 40 }} />
        </div>
      </form>
      <p className={styles.composerHint}>Enter 发送 · Shift + Enter 换行<span>样式预览，不调用模型</span></p>
    </div>
  </div>;
}

export default function LobeStylePreview() {
  const { theme, toggleTheme } = useTheme();
  const [view, setView] = useState<View>(() => views.find(item => item.id === new URLSearchParams(location.search).get("view"))?.id ?? "market");
  const [drawer, setDrawer] = useState<"navigation" | "connection" | null>(null);
  const [expert, setExpert] = useState(experts[1]);
  const navigate = (next: View) => {
    setView(next);
    setDrawer(null);
    history.replaceState(null, "", `?view=${next}`);
  };
  return <div className={styles.shell} data-preview-theme={theme}>
    <aside className={styles.sidebar}><Sidebar view={view} onNavigate={navigate} /></aside>
    <main className={styles.main}>
      <header className={styles.topbar}>
        <div className={styles.inline}><span className={styles.mobileMenu}><ActionIcon icon={Menu} aria-label="打开导航" title="打开导航" size={{ size: 19, blockSize: 40 }} onClick={() => setDrawer("navigation")} /></span><span className={styles.breadcrumb}>工作空间</span><ChevronRight size={13} className={styles.breadcrumb} /><span>{views.find(item => item.id === view)?.label}</span></div>
        <div className={styles.inline}><span className={styles.previewLabel}>样式预览</span><ActionIcon icon={theme === "dark" ? Sun : Moon} aria-label="切换明暗主题" title={theme === "dark" ? "切换浅色" : "切换深色"} size={{ size: 18, blockSize: 40 }} onClick={toggleTheme} /></div>
      </header>
      <div className={view === "chat" ? styles.chatBody : styles.body}>
        {view === "market" && <Market onStart={item => { setExpert(item); navigate("chat"); }} />}
        {view === "models" && <Models onConnection={() => setDrawer("connection")} />}
        {view === "chat" && <Chat key={expert.id} expert={expert} />}
      </div>
    </main>
    <Drawer title={drawer === "navigation" ? "工作空间" : "连接设置"} open={drawer !== null} onClose={() => setDrawer(null)} placement={drawer === "navigation" ? "left" : "right"} width={drawer === "navigation" ? 272 : 420}>
      {drawer === "navigation" ? <div className={styles.drawerNavigation} data-preview-theme={theme}><Sidebar view={view} onNavigate={navigate} /></div> : <div className={styles.connectionForm}>
        <p>用于验收字段、状态与抽屉样式的示例连接。</p>
        <label><span>连接名称</span><Input aria-label="连接名称" defaultValue="企业模型网关" /></label>
        <label><span>API 地址</span><Input aria-label="API 地址" defaultValue="https://gateway.example.invalid/v1" /></label>
        <label><span>API Key</span><Input aria-label="API Key" placeholder="已配置，不显示密钥" readOnly /></label>
        <Tag color="success">连接正常 · 示例状态</Tag>
        <Button type="primary" size="large" onClick={() => setDrawer(null)}>完成</Button>
      </div>}
    </Drawer>
  </div>;
}
