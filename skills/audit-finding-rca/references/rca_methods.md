# 根因分析方法

Use this reference when choosing RCA method or deepening an analysis.

## Method Selection

```text
单一明确因果链 -> 5-Why
多因素交叉影响 -> 4M1E
表面原因明确但需系统深挖 -> 4M1E + 5-Why
```

## 4M1E / 6M Questions

| 维度 | Diagnostic questions |
|------|----------------------|
| 人 Man | 岗位资质是否确认？培训是否覆盖实际场景？是否严格执行 SOP？人员配置和监督是否充足？ |
| 机 Machine | 设备是否确认、校准、维护？自动控制、报警、备件是否有效？ |
| 料 Material | 物料是否合格？供应商是否受控？储存、发放、退回、追溯是否完整？ |
| 法 Method | SOP/批记录/检验规程是否现行、清晰、可执行？变更是否评估批准？ |
| 环 Environment | 洁净级别、温湿度、压差、环境监测、清洁消毒是否受控？ |
| 测 Measurement | 检验方法、取样代表性、仪器计量、OOS/OOT、数据完整性是否可靠？ |

Judgment labels:

- **排除**：有证据证明该维度无异常或无关联。
- **部分相关**：存在薄弱点，但不是主因。
- **根本原因**：纠正该维度后，同类问题可被预防。

## 5-Why Rules

1. Start from the observed gap, not a restatement.
2. Each why must logically follow from the previous answer.
3. Stop at an actionable process, system, document, training, validation, or governance cause.
4. Do not stop at "人员疏忽", "管理不到位", or "意识不足"; ask what system condition allowed it.

Example:

```text
Why-1: 操作人员未记录关键参数。
Why-2: 批记录未标注该参数仍需人工记录。
Why-3: 自动化系统变更后，批记录未同步核对覆盖范围。
Why-4: 批记录管理 SOP 未规定自动化变更后的记录表单影响评估。
Root cause: 批记录管理流程未纳入自动化系统覆盖范围变更后的同步评估要求。
```

## CDMO / MAH Boundary

- CDMO-owned: local SOP, production execution, facility/equipment maintenance, QC testing, warehouse controls, operator training.
- MAH-owned: release responsibility, post-marketing surveillance, product lifecycle quality system, customer-owned process or method design.
- Shared: quality agreement, change approval, deviation/CAPA approval, OOS communication, periodic audit follow-up.
