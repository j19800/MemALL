# 结果验收（全维度）：记忆质量 / 可读性 / 存入机制 / 消费 / 自进化 / 智能化 / MCP 可靠性

> **文档状态**：现行（2026-11-11 凌晨）。视角：不看代码有无 bug，只看**产出能不能用**。
> 方法：用覆盖真实场景的多样化输入灌入真实 DB，逐维度量产出。

## 总评

| 维度 | 评级 | 关键证据 |
|---|---|---|
| **MCP 工具可靠性** | ✅ 良好 | 14/14：10 类非法输入结构化报错不崩溃；20 并发写入 20/20 无重复 id；幂等去重；检索确定性稳定；未知工具安全报错 |
| **存入机制** | ✅ 基本可用 | 去重准确（0 重复 hash 组）；并发安全；短文本正确拒收 |
| **人类可读性** | ⚠️ 有漏洞 | 8 条中 7 条可读；1 条不可读仍入库；JSON 垃圾被"美化"成 `a=1，b=2…` 后通过检查 |
| **记忆质量（门禁实效）** | ❌ **门禁只标注不拦截** | 6 条 `review` 记忆中 **5 条仍可被正常检索** |
| **记忆消费** | ❌ **注入严重不足** | 灌入 5 条不同类型工作记忆后，`session_start` 的 persona/identity/l7/decision_arcs **全为空**；只捡到 1 条待办 |
| **自进化** | ❌ **实质未工作** | 同主题决策变更/矛盾内容，`resolve.decision` **全部 NOOP（new_slot）**，`memory_status` 全 None，无 SUPERSEDE/UPDATE/CONFLICT |
| **智能化（分类/槽位）** | ❌ **很弱** | 自动分类准确率 **1/5**；`classify_step` 只纠正 1/4 且标签错（偏好→L7、修复→problem）；槽位带整句内容 |

## 逐维度详述

### 1) MCP 工具可靠性 ✅
- 非法输入 10 类全部安全：空 content / 缺 action / 未知 action / 非法 level / 超长 2 万字符 / 非数字 id / 不存在 id / 空 query / 未知 mode / 空 kb_ingest。
- 4 线程 × 5 条并发写入：20/20 成功，id 无重复。
- 相同内容重复 capture → 返回同一 id（去重生效）。
- 同 query 连续 3 次结果完全一致。
- 结论：**这一层可以放心接入。**

### 2) 存入机制 ✅
- 无重复 content_hash 组；短文本（"用 Redis。"）正确 `rejected`。
- 但见下方"质量门禁"：准入判 `review` 后不阻断。

### 3) 人类可读性 ⚠️
- 正常内容可读性好（决策/偏好/事实类 quality 5.4–6.9）。
- **漏洞 1**：`ABC-123-XYZ-999` 被判 `no_sentence_structure` 却仍入库并被检索。
- **漏洞 2**：`{"a":1,...}` → 归一化成 `a=1，b=2，c=3...`，被判**可读=ok**。结构化数据被伪装成自然语言，反而骗过检查。
- **漏洞 3**：`{{user_name}}...` 模板泄漏仅标 review，subject 还残留半截 `user_name}}`。

### 4) 记忆质量：门禁不拦截 ❌（最直接的短板）
实测 6 条 `gate=review`，其中 5 条可被 `search` 正常召回。
- `review` 语义上应"待复核、先别用"，实际只是装饰性标签。
- 直接后果：检索结果里会混入 `a=1，b=2…` 这类被美化过的垃圾。

### 5) 记忆消费 ❌ 注入严重不足
灌入 5 条覆盖决策/偏好/事实/待办/修复的记忆后，`session_start(auto_inject=True)` 的注入内容：
- `persona` → `{"error": "no memories for cons_agent", "sample_size": 0}`
- `identity_traits` → `{"l1_identity": [], "l7_preferences": []}`
- `decision_arcs` → `{"open": [], "in_progress": [], "closed": []}`
- `l7_instructions` → 0 字符
- 实际有内容的只有 `pending_tasks`(1) 和 `l5_active_global`(1)——**同一条待办**
- `injection_formatted` 仅 98 字符，只有一句 `[TODO] ...` + `[NARRATIVE] 新会话开始，有 1 件待办未处理。`
- `l4_recent` / `l5_active` / `l3_matched` 全为 0。

**结论**：消费层几乎没把已有记忆用起来。决策、偏好、修复经验都没进入会话上下文。

（说明：`continuity_pack` 反而正常，能返回 5 条 `active_state`——说明数据在，是 `session_start` 的注入选取有问题。）

### 6) 自进化 ❌ 实质未工作
- 决策演进：`"数据库用 PostgreSQL"` → `"数据库改为 MySQL"`，两次都是 `NOOP / new_slot`；`memory_status` 均 None，**没有 SUPERSEDE/UPDATE**。
- 冲突识别：`"API 网关使用 REST"` vs `"API 网关不使用 REST，改用 GraphQL"` → 两条都是 `NOOP`，**没有 CONFLICT/REVIEW**。
- 蒸馏：`distill_step` 有产出（distilled 1）；`reflect_step` 扫描 0 条（`cold_skipped 2`）。

**根因（已定位）**：`slot_key` 把**整句内容**编码进去了：
```
slot:config:[L4_会话]老陈:_数据库用_PostgreSQL
slot:config:[L4_会话]老陈:_数据库改为_MySQL，因为许可成本更低
```
同主题两句话 → 两个不同槽位 → 永远 `new_slot` → 自进化链路（UPDATE/SUPERSEDE/CONFLICT/REVIEW）**永远不触发**。
更糟：槽位还混入了 `[L4 会话]老陈:` 这种机械前缀，既污染语义又让跨会话无法对齐。

### 7) 智能化 ❌ 很弱
- **自动分类准确率 1/5**：项目决策→general、用户偏好→general、bug 修复→general、待办→general，全落 `general`；`level` 全 `P2`。
- 根因：`capture()` **不做单条自动分类**（只有批量 `classify_step`），不传 category 就是默认 `general`。
- `classify_step` 补救也很弱：4 条只改 1 条，且把"用户偏好"标成 **L7**、"修复 bug"标成 **problem**（期望 fix）。
- `slot_key` 生成 = 分类前缀 + 整句内容，见上。

## 结论排序（该先修什么）

1. **【最高】槽位生成**——它是自进化的总闸。不修，UPDATE/SUPERSEDE/CONFLICT 全部形同虚设。应改为**主题级抽象槽位**（如 `slot:config:database`），而不是编码整句内容 + 机械前缀。
2. **【高】质量门禁实效**——`review` 应默认不参与检索（或强降权），否则低质量记忆污染结果。
3. **【高】消费注入**——`session_start` 只捡到 1 条待办，决策/偏好/经验都没注入。这是"记忆有用"的核心体验。
4. **【中】智能化分类**——准确率 1/5，且 capture 不做单条分类；`classify_step` 标签错。
5. **【中】可读性漏洞**——不可读内容仍入库；JSON 被"美化"骗过检查；模板残留。
6. **【低】subject 质量**——`[L4 会话]老陈: ` 机械前缀 + 60 字符硬截断（8/8 带前缀，6/8 硬截断）。

## 可以说"好用"的部分
- MCP 工具层（异常/并发/幂等/确定性）扎实，可以放心接入。
- 存入机制的去重与并发正确。
- 正常内容的可读性打分合理。
- `continuity_pack` 能正确返回活跃状态（数据链路是通的）。

> 本报告为**结果验收**，未改动任何代码。修复需先定策略（尤其槽位抽象规则、review 检索策略），会影响现有消费方。
