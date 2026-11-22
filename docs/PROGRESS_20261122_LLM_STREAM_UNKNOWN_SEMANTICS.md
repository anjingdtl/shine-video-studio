# 2026-11-22 LLM 流式 UNKNOWN 语义收紧(接手轮)

Codex 额度中断后由 ZCode 接手完成收尾。本轮修复对象是流式传输默认开启后,
"被服务端确认过的流"在若干边界形态下被静默替换为一次新的普通请求的问题——
那是对一个可能已被受理(甚至可能已有答案在途)的请求的重放,直接违反 UNKNOWN 门禁
(AGENTS.md「模型调用外部结果必须分类」「UNKNOWN 请求身份安全补充」)。

## 根因

`OpenAICompatibleProvider._complete_stream` 旧语义把"回退一次普通请求"当作安全的兜底,
判据是"是否收到事件 / 是否收到 headers"。两个判据都不成立:

1. **headers 未到 ≠ 请求未送达。** POST 请求体已经写出,代理/网关切断读通道时
   服务端可能已收到并正在生成。旧代码在这种情形返回 `None`,由 `complete` 重发一次
   普通请求——可能买到第二份答案。
2. **200 已到但无事件 ≠ 未生成。** 请求已被受理是事实,重发同样不安全。
3. **有些兼容服务器忽略 `stream=true`,直接对 SSE 请求返回完整 JSON。**
   旧代码把这种响应当"无事件流"丢弃再重发一次;正确做法是复用同一份响应字节。

## 行为矩阵(修复后)

| 形态 | 结果 | 是否再发请求 |
| --- | --- | --- |
| 4xx 拒绝流形状(未受理) | 回退**一次**普通请求 | 是(唯一回退出口之一) |
| `ConnectTimeout/PoolTimeout/ConnectError` 且未收到 headers(连接未建立) | 回退一次普通请求 | 是 |
| 200 + `application/json`(服务器忽略 `stream=true`) | 复用同一响应体走普通解析 | 否 |
| 200 + SSE,收到 headers 后连接断开 | `LLMConnectionError` UNKNOWN | 否 |
| `HTTPError` 且无任何 chunk(含 headers 未到) | `LLMConnectionError` UNKNOWN | 否 |
| 200 + SSE 干净结束,有终止事件(`finish_reason`/`[DONE]`)但无内容 | `LLMFormatError` → `classify_external_outcome` = `RESPONSE_RECEIVED` | 否(重问走既有预算规则) |
| 200 + SSE 结束但无终止事件 | `LLMConnectionError` UNKNOWN | 否 |
| 200 + SSE 只有 `reasoning_content` 无正文 | `LLMConnectionError` UNKNOWN | 否 |
| 流中途断开但已有可见文本 | UNKNOWN + 保留可见文本(既有行为) | 否 |

## 改动清单

- `backend/shine/llm/openai_compatible.py`:`_complete_stream` 返回类型扩展为
  `LLMResponse | httpx.Response | None`;JSON 响应复用;headers 后断开与无 chunk
  `HTTPError` 一律 UNKNOWN;`saw_done` 空流给 `LLMFormatError`;`complete` 只在
  `None`(形状被拒/连接未建立)时才发普通请求。
- `backend/shine/llm/transport_policy.py`:`MAX_FIRST_EVENT_S` 180 → 300。
  300s 常规档等满 300s 才判首事件超时,更长档保留上限;减少慢首事件被误判挂起。
- `backend/shine/llm/base.py`、`transport_policy.streaming_enabled` 文档字符串同步。
- `backend/shine/agents/crew_prompts.py`:scriptwriter 对话率分支新增约束——
  补充台词的说话方必须是该节拍 `characters` 里列出的既有 source 人物,
  不得添加画外说话人(服务器按名字解析说话方,画外新说话人是不可绑定镜头)。
- 测试:`test_llm_streaming_default.py`(新增 JSON 复用、空流参数化、headers 前读超时
  三组用例)、`test_literary_transport_and_ledger.py`(空流改为 stays unknown)、
  `test_transport_policy.py`(300s 断言)、`test_crew_prompt_contract.py`
  (scriptwriter 补充台词说话方契约断言)。

## 验证

- 定向:三个直接测试文件 26 用例、相邻模块(`test_llm_gateway` / `test_llm_multi_model_routing`
  / `test_llm_pinned_task_routing` / `test_llm_context_window` / `test_llm_setup_contract`
  / `test_literary_governor` / `test_crew_prompt_contract`)115 用例、
  扩展(`test_agent_retry_and_rate_limit` / `test_film_unknown_outcomes` /
  `test_literary_acceptance` / `test_creative_script_task_lifecycle`)67 用例,全部通过。
- Ruff:改动文件全部通过。
- 后端 8 分片全量回归:304 文件、8 分片全部 exit `0`、`shards_with_failure_markers=0`,
  wall `1108.6s`(`shards-20261122-231522-576`,WorkRoot 因调用参数转义临时落在仓库内,
  验收后已删除该目录;惯用根 `F:\ShineTest` 未被本轮占用)。

## 边界

- 本轮未改 `worker.py` / `preproduction.py`,不触发 `client_review_identity` 重校准义务。
- 未做真实远端 LLM 的断流注入验证(离线 MockTransport / FakeOpenAIServer 覆盖全部形态);
  真实网关上的行为以既有生产观测为准。
