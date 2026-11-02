# LLM 上下文预算策略

## 目标

影视 Agent 按各自实际路由到的模型使用上下文能力。1M 窗口模型不再被较小的项目字符数限制截断；未声明窗口的模型也不套用虚构的小型默认值。

## 预算与发送规则

- 连接检测优先读取服务商 `/models` 返回的 `context_window`、`context_window_tokens` 和 `max_output_tokens`。
- 如果服务商没有返回窗口，只有模型 ID 与官方服务域名同时精确匹配时才使用官方窗口回退值。
- 每次 Agent 请求从该角色当前路由的 Profile 解析模型窗口，并在任务记录中保存模型、窗口、来源、该角色上下文的 UTF-8 字节数和 `full_context_without_fixed_truncation` 输入策略。
- 输入内容不按固定字符数截断。剧本规划不再裁去 60,000 字符之后的内容；影视 Agent 和文本审片输入也不再按小型固定字符数裁剪。
- 输入窗口与结构化回复长度是两项独立设置。`max_completion_tokens` 留空时不发送一个较小的固定输出上限；服务商使用自己的默认值。若用户显式设置，该值只限制输出长度，不重写输入上下文窗口。
- 系统不以 UTF-8 字节数伪装成 token 数。只有模型响应的 `usage` 才作为实际输入/输出 token 计量证据；发出前保留每个角色的输入字节数供审计。
- 模型未声明窗口时，应用保留完整输入并让所选模型服务执行其自身上下文校验；服务端拒绝时按真实错误返回，不自动缩短、换模型或改用 Mock。
- 网络结果不确定时，请求保留为待核对状态，不能为了“重试成功”盲目重复发送。

## 官方模型窗口

| 服务商和模型 | 输入上下文窗口 | 输出上限 | 获取方式 |
| --- | ---: | ---: | --- |
| MiniMax M3.1 Flash | 1,000,000 tokens | 本策略不猜测 | 官方模型说明；若目录未给出则按精确模型名回退 |
| DeepSeek `deepseek-flash` / V4.1 Flash | 1,048,576 tokens | 393,216 tokens（服务商目录提供时） | 优先使用 `/models` 元数据；官方目录说明其 1M 上下文 |

MiniMax 官方说明：[M3.1 Flash](https://agent.minimax.io/tools/m3.1-flash)。
DeepSeek 官方说明：[模型目录](https://api-docs.deepseek.com/api/list-models/) · [模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)。

## 验收记录

真实 MiniMax Profile 的模型连接检测成功，工作台显示 1,000,000-token 输入窗口。新的 Agent 请求记录分别保留 `scriptwriter`、`director`、`cinematographer` 的同一模型窗口和各自上下文大小；已完成响应保留真实 provider usage 和结果哈希。此项证明的是所选模型元数据和未截断发送策略，不代表任何一次未知或超时的 Agent 调用已经成功。
