# RCA/CAPA Writing Patterns

Use this reference for the default answer style and reusable audit-response patterns.

## RCA Style

- Prefer a factual and defensible opening: state what the company has already established, then identify the precise gap.
- Use causal chains rather than direct blame.
- Cite exact SOP numbers, record names, version numbers, equipment names, and standards when available.
- Keep a normal RCA to 1-3 sentences and roughly 80-200 Chinese characters unless the user asks for deeper detail.
- For complex findings, a 4M1E layout is acceptable. If only one dimension is relevant, write "不涉及相关因素" for excluded dimensions.

Avoid these phrases unless the user explicitly wants a blunt internal diagnosis:

- 疏忽、遗漏、缺失、做不到位
- 管理不善、流于形式、监管不力
- 不完善、不到位、不够
- 未能有效、没有做到

Prefer:

| Avoid | Prefer |
|-------|--------|
| 缺失/遗漏 | 未涵盖、未纳入、有待补充 |
| 做不到位/不完善 | 有待优化、有提升空间、可进一步细化 |
| 疏忽/失误 | 因...导致、在实际执行中出现了 |
| 管理不善 | 在...方面有待加强、需进一步完善 |
| 流于形式 | 在实际执行层面尚需进一步落实 |

## CAPA Style

- Use 2-5 numbered actions.
- Each action should include what changes, responsible role/department, expected timing, and verification or follow-up.
- If product quality could be questioned, start by confirming or planning a quality impact assessment.
- For SOP changes, include document number and version transition when known.
- Add horizontal review when the root cause is systemic.

Avoid:

- Single vague action such as "加强培训".
- More than 8 procedural micro-steps.
- Actions without owner, timing, or deliverable.

## Reusable Finding Patterns

### 文件/SOP类

Triggers: SOP, 规程, 文件, 记录, 未规定, 未索引, 未更新, 不一致, 可操作性不足.

RCA pattern:

```text
公司已建立{领域}管理规程{编号}，对{内容}做出了规定。但该规程在{具体方面}尚未涵盖{具体差距}。因{因果解释}，导致{审计发现表现}。
```

CAPA pattern:

```text
1. 由QA牵头修订{文件编号}{旧版本}->{新版本}，补充{具体内容}，预计{时间}完成。
2. 对{相关岗位}开展新版文件培训，并保留培训与考核记录，预计{时间}完成。
3. 横向排查同类 SOP/记录表单，如存在类似条款一并纳入修订。
```

### 验证/确认类

Triggers: IQ/OQ/PQ, 验证方案, 验证报告, 方法验证, 清洁验证, 工艺验证, COA.

Use quality-statement or supplemental-validation actions when the finding challenges validation conclusion.

### 环境监测/年度回顾类

Triggers: PMS, 年度回顾, 趋势分析, 在线监测数据.

RCA should explain whether data existed but was not trended, data was insufficient, or trend criteria were not defined.

### 计算机化系统/数据完整性类

Triggers: DMS, 备份, 权限, 审计追踪, 电子数据, 手动积分.

CAPA normally includes IT/QA SOP revision, configuration or permission review, validation impact assessment, and audit-trail review frequency.

### 设备/设施类

Triggers: 未安装, 未校准, 未确认, 缺少, 环境控制, 洁净区.

First assess quality impact and GMP criticality, then propose installation/procurement/change control/qualification.

## Optional Bilingual Output

If the source finding is in English or the user asks for bilingual output, provide Chinese first and a concise English summary after it unless the user requests another order.
