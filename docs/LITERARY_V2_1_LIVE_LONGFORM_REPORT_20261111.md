# V2.1 真实长篇实测报告（《放开那个女巫》）

日期：2026-11-11
分支：`feat/elastic-literary-v2-low-blocking-20261111`
模型：MiniMax `MiniMax-M3.1-flash`（1,000,000 上下文，reasoning 模型）
数据目录：`.local-acceptance/witch-ui-20261109`（迁移 `f180b0080001`）

---

## 1. 测试对象

| 项 | 值 |
|---|---|
| 文件 | `C:\Users\Administrator\Desktop\AIstudio\放开那个女巫.txt` |
| 大小 | 7,178,905 字节（GB18030） |
| 字符数 | 3,593,737 |
| 章节数 | 1,498（`第0001章` 编号） |
| 分卷标记 | `第一卷 邪月之歌` 等 |

这本书是这类系统真正的输入形态：**单文件、单章 2k–3k 字、上千章、跨卷长线伏笔**。用它之前，短文测试（1.5k 字）掩盖了三个硬上限。

---

## 2. 导入与分卷：没有撑爆预算

| 步骤 | 结果 | 耗时 |
|---|---|---|
| 上传 7MB 文档 | 201，sha256 `7f45fe0b…` | 0.2s |
| 分卷计划 | 84 卷，25,465–44,990 字/卷 | 0.2s |
| 覆盖率 | `complete: true`，0–3,663,445 全覆盖 | — |
| 计划响应体 | 83,114 字节（84 卷 × section_ids） | — |
| 确认分卷 | 200，materialize 84 个卷文档 | 0.1s |

结论：**导入与分卷不构成预算风险**。359 万字没有触发上限，也没有内存/超时问题；单卷被控制在 45k 字以内，正是为了让下游一次能读完。

> 注：3,663,445 与 3,593,737 的差值来自 `\r\n` 规范化后的 offset 统计，覆盖区间接续正确。

---

## 3. 真正的膨胀点：调用次数，不是 token

第一卷 44,009 字，原实现（`MAX_SPANS_PER_SCENE=200` × 256 字，`story` 固定 2,667 字分段）：

| 分段上限 | 段数 |
|---|---|
| 2,666（旧） | **17** |
| 8,000 | 6 |
| 45,000（新） | **1**（717 个可引用 span） |

也就是说：**旧实现每卷要 17 次提取 + 6 次归并 + 2 + 1 ≈ 27 次调用**。
84 卷 × 27 ≈ **2,268 次**；修复后 84 卷 × 1 = **84 次**。

这不是"模型窗口不够"，而是三处与模型能力脱节的硬上限：

1. `MAX_SPANS_PER_SCENE = 200`：行边界让每个 span 远小于 256 字，实际一段只装 ~8k 字；
2. `story` 固定 `(MAX_SCENE_CHARS * 2) // 3 = 2667`，与 1M 窗口无关；
3. `prepare_scenes` 内部又用默认 4,000 分段，与调用方 2,667 错位——重叠区被**读两次、付两次钱**。

---

## 4. 修复后的实测结果

第一卷（44,009 字）一次读完：

```
t=  0s  QUEUED   EXTRACT cursor=0 segment_total=None
t=  5s  RUNNING  EXTRACT cursor=0 segment_total=None
t=205s  RUNNING  MERGE   cursor=0 segment_total=1
t=255s  SUCCEEDED        cursor=1 segment_total=1
```

产物质量（同一 artifact）：

| 项 | 值 |
|---|---|
| production_sections | 20（前言 + 卷名 + 18 章） |
| summary | 290 字 |
| story_line | 9 条（307 字） |
| timeline | 11 条 |
| relationships | 13 条 |
| questions | 8 条 |

覆盖完整：第 1 章到第 18 章逐章有 section，说明"整卷一次读"没有丢章。

---

## 5. 直接测量到的 provider 行为（决定预算公式）

| 实验 | 结果 |
|---|---|
| 44,000 字输入，输出 1.5k | 26,643 prompt token，**6.2s** |
| 12,000 字输入 | 7,436 prompt token，6.2s |
| `thinking: {type: disabled}` | **400 不支持** |
| `reasoning_effort: "minimal"` | 200，reasoning 152 → **17**，4.2s → 2.3s |
| 预算 5,786，实际输出 | **16,345 completion token**（旧字段 `max_tokens` 未下发，provider 忽略 `max_completion_tokens`） |

三条结论：

1. **输入不是瓶颈**：44k 字只要 6 秒、26.6k token，1M 窗口用不到 3%。
2. **推理是瓶颈**：结构化提取里 reasoning 占 70–80%，且不认"思考上限"。
3. **输出字段必须双写**：只发 `max_completion_tokens` 时，预算形同虚设。

---

## 6. 本轮代码修复

| 文件 | 改动 |
|---|---|
| `materialprep/source_spans.py` | `MAX_SPANS_PER_SCENE` 200 → 1,024（44k 字卷 = 1 段） |
| `materialprep/story.py` | 新增 `_extract_segment_chars`：按 `burst_input_limit` 反推分段，上限 200k 字 |
| `materialprep/scenes.py` | 新增 `segment_chars`，与调用方同一窗口 |
| `literary/budget.py` | reasoning 预留 ≥ max(4096, 2×目标)；`MIN_COMPLETION_TOKENS` 512 → 4,096；先判窗口溢出再判额度不足 |
| `llm/gateway.py` | 结构化默认 `reasoning_effort=low`；默认输出上限 131,072 → 16,384；显式预算不再被默认值压低 |
| `llm/openai_compatible.py` | 同时下发 `max_tokens` 与 `max_completion_tokens` |
| `director/pipeline.py` | 蓝图合同补 `observations` 字段 + 提示词结构说明 |
| `literary/drafts.py` | `content_saved` 只在真有正文时为真 |
| `literary/ledger.py` | 抽出 `recorder`，大纲/素材/分镜统一记账 |
| `agents/film.py` | 格式失败记录 `provider_finish_reason` / `provider_text_chars` |

---

## 7. 五维记忆在真实长篇上的表现

同一套提示词，两本书各跑一次蓝图：

| 项目 | observations | 落库 | mode |
|---|---|---|---|
| 《雾港》（1.5k 字） | 23 | 23 | on |
| 《雾港》第二项目 | 19 | 19 | on |

修复前是 `saved: 0` —— 提示词要 `observations`，合同没有该字段，pydantic 直接丢弃。这条只有在真实模型上跑才会暴露，mock 测试因为绕过了 schema 校验而一直"通过"。

---

## 8. 剩余风险（如实记录）

1. **84 卷串行约 5.6 小时**（4 分钟/卷）。这是单 worker、单请求的真实成本。要压缩只有两条路：并行卷级任务，或把每卷再分段并行——都会引入并发与失败隔离的新问题，未在本轮实施。
2. **单卷 205s 逼近 300s 超时**。当前 `timeout_s=300`，长卷在慢时段仍可能撞墙。建议生产 profile 把 `timeout_s` 提到 600，并把 `reasoning_effort=low` 固化到 profile 的 `generation_options`。
3. **全书蓝图（84 卷 digest 归并）未跑完**。归并逻辑已实现并有单测（压缩散文、保留全部 event_id、覆盖缺失具名），但没有 84 份真实 digest 的端到端证据，按纪律计为 `NOT_RUN`。
4. **分镜 ScriptBreakdown 在本机真实模型上连续 3 次 `LLM_FORMAT`**（HTTP 200、无 schema path → JSON 不完整）。已补诊断字段，但根因尚未定位，仍未通过。这是当前"跑到出片"的唯一阻塞点。

---

## 9. 复现命令

```bash
# 服务（真实 provider + comfyui 配置）
export SHINE_DATA_DIR="F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\witch-ui-20261109"
python -m alembic upgrade head          # f180b0070001 -> f180b0080001
python -m uvicorn shine.main:app --host 127.0.0.1 --port 8000 --app-dir backend
python -m shine.worker                  # 另一个终端，PYTHONPATH=backend
```

导入、分卷、确认、授权、排队大纲的完整调用序列见 `.tmp-live/` 下的实测脚本（未入库）。
