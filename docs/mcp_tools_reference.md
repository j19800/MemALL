# MemALL MCP 工具参考（自动生成）

> **文档状态**：现行 · 自动生成 — 由代码生成，勿手改；与代码不一致时以代码为准

> 本文件由 `scripts/gen_mcp_tools_doc.py` 从 `memall.mcp.adapter` 的 TOOL_DEFINITIONS 生成，共 **8** 个工具。
> **请勿手改本文件**——重新运行生成器即可。

## `memall_discussion`

Create multi-agent discussions, record stances, check status. Actions: create | respond | status

### Parameters

| name | type | required | description |
|---|---|---|---|
| `action` | string | yes | `create | respond | status` |
| `title` | string |  | Discussion title (create) |
| `background` | string |  | Problem description |
| `options` | array |  | Solution options |
| `participants` | array |  |  |
| `open_questions` | array |  |  |
| `recommendation` | string |  |  |
| `convergence_rule` | string |  | `unanimous | majority | any` |
| `timeout_hours` | integer |  |  |
| `action_items` | array |  |  |
| `discussion_id` | integer |  | Discussion memory ID (respond/status) |
| `agent_name` | string |  | Agent responding |
| `stance` | string |  | `agree | disagree | abstain` |
| `arguments` | string |  | Reasoning text |
| `round_num` | integer |  |  |

## `memall_federation`

Cross-agent knowledge federation: query, publish, resolve conflicts, inject/extract, push deliver. Actions: query | publish | conflicts | inject | extract | deliver

### Parameters

| name | type | required | description |
|---|---|---|---|
| `action` | string | yes | `query | publish | conflicts | inject | extract | deliver` |
| `query` | string |  | Search keyword |
| `agent_name` | string |  |  |
| `category` | string |  |  |
| `trust_level` | string |  | `trusted | family | shared | public` |
| `project` | string |  |  |
| `limit` | integer |  |  |
| `memory_id` | integer |  |  |
| `source_agent` | string |  |  |
| `session_id` | string |  |  |
| `target_agent` | string |  |  |
| `content` | string |  |  |
| `event_type` | string |  |  |
| `source` | string |  |  |

## `memall_hooks_recent`

Show recent async hook activity (pipeline runs, notifications, reminder checks, etc.)

### Parameters

| name | type | required | description |
|---|---|---|---|
| `n` | integer |  | Number of recent events (default 10) |

## `memall_msg`

Real-time delivery over the memall-bus (an independent resident process). The L5 task card stays the single source of truth — the bus carries a POINTER (memory_id), not the task body. Actions: send | inbox | ack | status

### Parameters

| name | type | required | description |
|---|---|---|---|
| `action` | string |  | `send | inbox | ack | status` |
| `agent_name` | string |  | Calling agent (also the bus identity) |
| `to` | string |  | Recipient agent (send action) |
| `payload` | object |  | Message payload — prefer {memory_id: N} |
| `type` | string |  | Message type: task | ack | report | verdict | msg |
| `ids` | array |  | Message ids to mark read (ack action) |

## `memall_persona`

Query agent persona/profile/identity, ask digital twin, preload profile, search profiles, foresight prediction. Actions: persona | persona_profile | identity | ask | profile_preload (预加载画像) | profile_search (搜画像) | foresight (前瞻/预测)

### Parameters

| name | type | required | description |
|---|---|---|---|
| `action` | string | yes | `persona | persona_profile | identity | ask | profile_preload | 预加载画像 | 加载画像 | 画像 | profile_search | 搜画像 | 搜索画像 | 查画像 | 查找画像 | foresight | 前瞻 | 预测 | 猜我需要 | 下一步` |
| `agent_name` | string |  | Agent name to query |
| `query` | string |  | Search query for profile_search/foresight |
| `question` | string |  | Question for ask action |
| `mode` | string |  | `stance | pattern | predict` |
| `subject` | string |  | Subject/agent context |
| `layer` | string |  | `1 | 2 | 3 | all` |
| `evolution` | boolean |  |  |
| `window_days` | integer |  |  |

## `memall_read`

Search/retrieve memories, trace provenance, explore graph, timeline, federated queries, natural language chat search, post-compaction recap. Actions: retrieve | search | unified_search | chat | vector_search | hybrid_search | batch_search | trace | traverse | timeline | fed_query | fed_conflicts | recap

### Parameters

| name | type | required | description |
|---|---|---|---|
| `action` | string | yes | `retrieve | search | unified_search | chat | nl_search | 自然语言搜索 | vector_search | hybrid_search | batch_search | trace | traverse | timeline | fed_query | fed_conflicts | recap | task_list | goal_status` |
| `query` | string |  | Search query |
| `queries` | array |  | Queries for batch_search |
| `mode` | string |  | `auto | direct | fts5 | vector | hybrid` |
| `top_k` | integer |  |  |
| `rrf_k` | integer |  |  |
| `category` | string |  |  |
| `level` | string |  |  |
| `owner` | string |  |  |
| `agent_name` | string |  |  |
| `viewer` | string |  | Viewer identity for trust filtering / personalisation |
| `project` | string |  |  |
| `limit` | integer |  |  |
| `tags` | array |  | Exact-match tag filter (any by default) |
| `tags_all` | boolean |  | Require every listed tag instead of any |
| `rerank` | boolean |  |  |
| `node_id` | integer |  | Starting memory ID for traverse |
| `depth` | integer |  |  |
| `relation_filter` | string |  |  |
| `memory_id` | integer |  | Memory ID for trace |
| `hours` | integer |  |  |
| `trust_level` | string |  |  |
| `facet_fields` | array |  | Fields to aggregate as facets: level|category|project|agent_name|owner|stream|visibility|memory_status|confidence, or ['auto'] |
| `facet_fields_str` | string |  | Comma-separated facet fields (clients that cannot send arrays) |
| `expand_query` | boolean |  | Expand query with synonyms / recent query history |
| `suggest` | boolean |  | Return query completions from the corpus vocabulary |
| `explain` | boolean |  | Return a per-result score breakdown (matched terms, ranks, boosts) |
| `cluster` | boolean |  | Group results into topic clusters |
| `personalize` | boolean |  | Rank results by per-agent interaction affinity |
| `context_boost` | boolean |  | Boost results matching the active project / category |
| `session_id` | string |  | Enable per-session novelty demotion for repeat queries |

## `memall_system`

Pipeline, sessions, gateway, hub sync, DB maintenance, security, adaptive, onboarding, reflection, index rebuild, daily digest, hot topics. Actions: run_pipeline | distill | gateway | hub_connect | hub_sync | session_start | session_end | session_summary | db | security | adaptive | onboarding | reflect | index_rebuild | digest (每日摘要/日报/总结) | hot (热门/热点/热榜)

### Parameters

| name | type | required | description |
|---|---|---|---|
| `action` | string | yes | `run_pipeline | distill | gateway | hub_connect | hub_sync | session_start | session_end | session_summary | db | security | adaptive | onboarding | reflect | index_rebuild | digest | 每日摘要 | 日报 | 总结 | hot | 热门 | 热点 | 热榜` |
| `distill_sub_action` | string |  | `list | summarize` — distill only |
| `gateway_sub_action` | string |  | `status | start | stop | export | import | discover | pair | peers | federated` — gateway only |
| `db_sub_action` | string |  | `optimize | stats | vacuum | archive_stats | archive_vacuum | backfill_thread | backfill_project | backfill_tags | dedupe_l9 | dedupe_l10` — db only |
| `security_sub_action` | string |  | `audit | permit | check | score | list` — security only |
| `adaptive_sub_action` | string |  | `clean | index | distill | all | report` — adaptive only |
| `onboarding_sub_action` | string |  | `status | start | reset | submit_step | skip` — onboarding only |
| `reflect_sub_action` | string |  | `agree | disagree | probe` — reflect only |
| `sub_action` | string |  | Deprecated alias — use the <action>_sub_action field that matches your action |
| `confirm` | boolean |  | Required to execute destructive db ops (vacuum, archive_vacuum, backfill_*, dedupe_l9/l10) |
| `dry_run` | boolean |  | Preview-only pass for db backfill_*/dedupe_* |
| `session_id` | string |  | Session ID |
| `agent_name` | string |  |  |
| `auto_inject` | boolean |  |  |
| `auto_extract` | boolean |  |  |
| `include_reflect` | boolean |  |  |
| `include_distill` | boolean |  |  |
| `include_integrate` | boolean |  |  |
| `include_persona` | boolean |  |  |
| `timeout` | integer |  |  |
| `limit` | integer |  |  |
| `group_id` | integer |  |  |
| `summary` | string |  |  |
| `insight` | string |  |  |
| `gap` | string |  |  |
| `next` | string |  |  |
| `port` | integer |  |  |
| `file_path` | string |  |  |
| `address` | string |  |  |
| `max_peers` | integer |  |  |
| `direction` | string |  | `bidirectional | to_hub | from_hub` |
| `force` | boolean |  | Force index rebuild |
| `user_id` | string |  |  |
| `step` | integer |  |  |
| `input_data` | object |  |  |
| `memory_id` | integer |  |  |
| `context` | string |  |  |
| `level` | string |  | `public | trusted | private` |
| `requester` | string |  |  |
| `target` | string |  |  |

## `memall_write`

Write/capture/search/update memories, create connections, manage forgetting & ops. Actions: capture | smart_store | store_batch | batch_capture | update | connect | forget | ops | record_interaction | task_claim | task_resolve | task_block | quick (快速记)

### Parameters

| name | type | required | description |
|---|---|---|---|
| `action` | string | yes | `capture | smart_store | store_batch | batch_capture | update | connect | forget | ops | record_interaction | task_claim | task_resolve | task_block | quick | 记一下 | 快速记 | 随手记` |
| `content` | string |  | Memory content |
| `owner` | string |  |  |
| `agent_name` | string |  |  |
| `subject` | string |  |  |
| `summary` | string |  |  |
| `project` | string |  |  |
| `category` | string |  |  |
| `level` | string |  | `P0 | P1 | P2 | L1 | L2 | L3 | L4 | L5 | L6 | L7 | L8 | L9 | L10 | L11` |
| `metadata` | string |  | JSON metadata string |
| `thread_id` | integer |  | Parent memory ID for thread context |
| `memory_id` | integer |  | Memory ID (update/connect/forget/record_interaction) |
| `source_id` | integer |  |  |
| `target_id` | integer |  |  |
| `relation_type` | string |  | `extends | contradicts | refines | cites | supersedes` |
| `weight` | number |  |  |
| `forget_sub_action` | string |  | `expired | low_value | review | stats | all` — Sub-action for forget |
| `ops_sub_action` | string |  | `merge | split | tag | batch_tag | archive | restore | dedup | undo` — Sub-action for ops |
| `sub_action` | string |  | Deprecated alias — use forget_sub_action / ops_sub_action |
| `days` | integer |  |  |
| `confirm` | boolean |  | Required to execute destructive ops (forget expired/low_value/all, ops merge/split/dedup/archive) |
| `dry_run` | boolean |  | Preview-only pass for ops archive/dedup |
| `tags` | array |  |  |
| `creator` | string |  | Who created this memory (provenance) |
| `confidence` | number |  |  |
| `visibility` | string |  | `public | shared | family | trusted | private` — Access level; non-human agents are clamped to their allowed write level |
| `stream` | string |  | knowledge (default) or ledger |
| `occurred_at` | string |  | Event time (ISO 8601); defaults to now |
| `supersedes` | array |  | Ids of memories this one supersedes |
| `dedup_threshold` | number |  |  |
| `items` | array |  | Items for store_batch and batch_capture |
| `delimiter` | string |  |  |
| `mode` | string |  | `add | set | remove` |
| `threshold` | number |  |  |
| `interaction_type` | string |  | `click | dwell | impression | copy | skip` — Kind of search interaction (record_interaction) |
| `dwell_ms` | integer |  | Dwell time in ms (record_interaction, interaction_type=dwell) |
| `rank_position` | integer |  | 1-based rank of the clicked result (record_interaction) |
| `task_id` | integer |  | L5 task card id (task_claim/task_resolve/task_block) |
| `result` | string |  | Closure evidence stored on resolve (task_resolve) |
| `reason` | string |  | Why the card is blocked, needs a human (task_block) |
| `session_id` | string |  |  |
