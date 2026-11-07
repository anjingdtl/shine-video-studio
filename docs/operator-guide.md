# 操作员指南

面向在本机运行 Shine 视频工作台的操作员。覆盖：启停、真实模式与 Mock 的区分、
配置 LLM、导入资产并冻结风格、提交一个 H3 镜头、查看视频与 QC、返工与正式审片、
导出样片，以及出问题时先看哪里。

关联文档：[本地环境验证报告](LOCAL_ENVIRONMENT_VALIDATION.md)、
[真实 H3 验收报告](LOCAL_REAL_H3_ACCEPTANCE.md)、
[V1.3 收尾报告](LOCAL_V1.3_CLOSEOUT_REPORT.md)。

> 本轮尚未完成的操作员动作集中在 [V1.3 收尾报告第 8.2 节](LOCAL_V1.3_CLOSEOUT_REPORT.md#82-剩余操作项按建议顺序)。
> 本轮三个真实镜头的**正式逐项审片已于 2026-10-25 完成**：操作员 `shine-owner` 通过真实
> 操作员 API 逐项审阅并签署，语义 / 风格 / 终审门与 B 的音频门是**人工**关闭的，不是
> 模型覆盖。

---

## 1. 启动与停止

### 1.1 准备操作员凭据（只要用到审片/冻结/发布就必须配）

API 进程环境里没有下面两个变量时，所有需要人工授权的端点一律拒绝：

```powershell
$env:SHINE_OPERATOR_ID = Read-Host '授权操作员标识'
$operatorCredential = Read-Host '操作员随机凭据（至少32字符）' -MaskInput
if ($operatorCredential.Length -lt 32) { throw '凭据过短' }
$env:SHINE_OPERATOR_TOKEN_SHA256 = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($operatorCredential))
).ToLower()
Remove-Variable operatorCredential
```

- 身份由服务端配置决定，客户端不接受 username。
- 原文只放在你的密码管理器里；只把 SHA-256 摘要交给 API。
- 摘要不要写进 `.env`、数据库、Git、日志、URL 或浏览器 localStorage。
- 改操作员或凭据摘要需要本机管理员改 API 进程环境并重启 API。
- 一次性完成"停栈 → 重启 → 审片 → 重装样片"的现成脚本见
  [第 6.4 节](#64-正式逐项审片)。脚本里凭据只活在进程内存中，API 只保存它的
  SHA-256 摘要。

### 1.2 启动

```powershell
.\scripts\start_shine.ps1 -Provider comfyui -ApiPort 8100 -WebPort 5175 `
  -DataDir 'F:\ClaudeWorkSpace\projects\shine-video-studio\.local-acceptance\h3run'
```

| 参数 | 含义 |
| --- | --- |
| `-Provider mock` | Mock 模式：只模拟调度与 QC，**永远不会**产生视频（默认） |
| `-Provider comfyui` | 真实模式：提交到本机 ComfyUI |
| `-ApiPort` / `-WebPort` | API 与前端端口，默认 8000 / 5173 |
| `-DataDir` | 数据目录；数据库、媒体、日志都在这里 |
| `-Install` | 强制重装依赖 |
| `-NoWorker` | 不启动 worker（只跑 API 与前端） |

脚本会：检查环境 → 迁移数据库（`alembic upgrade head`）→ 以隐藏进程启动 API、worker、
前端 → 把每个进程的 PID、启动时间、工作目录、命令行写进
`<data_dir>/run/processes.json` → 打印日志目录与停止命令。

```
Shine API:      http://127.0.0.1:8100/health
Shine Web:      http://127.0.0.1:5175
Worker mode:    comfyui
Logs:           <data_dir>/run/logs
Process record: <data_dir>/run/processes.json
```

API 与前端只监听 `127.0.0.1`。在没有完整用户认证之前不要暴露到局域网或互联网。

**端口被占用时脚本会拒绝启动**，并打印占用者 pid 与停止命令。这不是坏事：硬启动
会让新进程带着绑定错误死掉，而**旧的服务继续在同一个端口上按旧的凭据应答** ——
看起来就像"新配置没生效"。先停掉旧栈（或换一个 `-ApiPort`）再启动。

清单里的 `pid` 是启动器 pid，`listener_pid` 才是**真正占用端口的那个进程**：uv 管理的
虚拟环境会把基础解释器作为子进程重新执行，两者不是一个 pid。停止脚本按 `listener_pid`
收尾，所以别拿 `pid` 去判断服务是否还活着。

### 1.3 停止

```powershell
uv run --project backend python scripts/stop_shine.py --data-dir "$env:SHINE_DATA_DIR"
```

它只停止 `processes.json` 里记录的进程：先对 SQLite 做 WAL 检查点，再终止进程
（包括记录在案的 `listener_pid`），**从不**碰 ComfyUI 或任何无关进程。ComfyUI 实例
由你自己管理，本脚本不负责。停止后如果端口仍被占用，它会打印
`[warning] port <n> is still held by pid <n>` —— 看到这行就说明还有一个服务活着，
先处理它再重启，否则下一次启动会撞端口。

### 1.4 日志位置

| 日志 | 路径 |
| --- | --- |
| API | `<data_dir>/run/logs/api.out.log` / `api.err.log` |
| worker | `<data_dir>/run/logs/worker.out.log` / `worker.err.log` |
| 前端 | `<data_dir>/run/logs/web.out.log` / `web.err.log` |
| ComfyUI | 自己启动时指定的 stderr 文件 |

---

## 2. 真实模式还是 Mock

唯一权威依据是 `GET /api/v1/system/status`：

```powershell
Invoke-WebRequest 'http://127.0.0.1:8100/api/v1/system/status' -UseBasicParsing | Select-Object -ExpandProperty Content
```

| 字段 | 含义 |
| --- | --- |
| `render_mode` | `comfyui` = 真实渲染；`mock` = 模拟 |
| `h3_connected` | 渲染端可达。**不等于**模型可用或画质通过 |
| `mock_provider_available` | Mock 适配器存在，不代表当前在用 |
| `worker_online` | 有 worker 在心跳 |
| `pending_jobs` / `running_jobs` / `failed_jobs` / `blocked_jobs` | 队列概况；`blocked_jobs` 是被规则/连续性/映射挡住，不是崩溃 |

本轮真实运行时的实测值：

```json
{"api_online":true,"worker_online":true,"mock_provider_available":true,"h3_connected":true,
 "render_mode":"comfyui","pending_jobs":0,"running_jobs":0,"failed_jobs":0,"blocked_jobs":5}
```

其它判断依据：

- job 的 `quality_verification` 字段写的是证据来源（`real` / `mock`）。
- 产物的 `is_mock` 字段同理。
- **最终结果只能看 `final` 门**。`technical` 通过、`final` 仍是 `needs_review`，就说明
  这条片子还没验收。

另外三条硬规则：

1. ComfyUI 地址必须是回环。因为参考素材的字节会传给渲染端，远程渲染需要显式设置
   `SHINE_ALLOW_REMOTE_COMFYUI=1`。
2. 不要为了"让测试通过"而关掉预制造成本闸门（`comfyops.preproduction.enforce`）。
3. `SHINE_OFFLINE_SIMULATION` 只用于隔离的测试数据，不能把 Fake 证据说成真实验证。

---

## 3. 配置 LLM

### 3.1 建立服务商档案

```powershell
$body = @{
  name     = 'minimax-openai-compatible'
  kind     = 'openai_compatible'
  base_url = 'https://api.minimax.cn/v1'
  model    = 'MiniMax-M3.1-flash'
  api_key  = '<操作员授权的本地测试密钥>'
  enabled  = $true
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8100/api/v1/provider-profiles' `
  -ContentType 'application/json' -Body $body
```

或单独更新密钥：`PUT /api/v1/provider-profiles/{profile_id}/key`，体为 `{"api_key": "..."}`。

**密钥边界**：密钥写入 Windows DPAPI 秘密存储（`secret_storage: dpapi`），API 从不返回它，
它不会进入 SQLite、Git、日志或前端响应。不要把密钥写进 `.env`、提交到仓库或贴进工单。

### 3.2 连通性测试与角色路由

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8100/api/v1/provider-profiles/{id}/test'
# {"ok": true, "message": "连接检查通过：服务可达且模型目录列出了配置的 Model ID"}
```

角色路由通过 `agent_routes` 表管理，12 个角色（scriptwriter、director、
cinematographer、editor、continuity_supervisor、sound_director、producer、comfyops、
script_planner、shot_planner、repair_planner、qc_reviewer）都指向该档案。路由是显式的，
不会静默回落到另一个服务商。

### 3.3 两个已知现象

- 模型目录里没有配置的 Model ID 时，连接测试会**判定为未通过**（稳定错误码
  `LLM_MODEL_NOT_LISTED`），但同时保留 `reachable: true`，界面会列出服务商返回的候选
  Model ID。系统不做未经授权的大小写模糊匹配：请把配置里的 Model ID 改成服务商返回的
  写法（例如 `MiniMax-M3.1-Flash`），再测一次。连接本身是成功的。
- 剧组的角色如果返回 `LLM_FORMAT`，说明模型输出没满足该角色的结构化契约（HTTP 422）。
  这不是环境坏了；先看返回内容与 schema 的差异。

  > 2026-10-25 18:01:13 的提交 `2df8aff` 修掉了这一类失败的一个根因：以前 schema 只交给
  > 服务商做校验、**没有**下发给模型，而指令却让模型"只返回所给的 JSON 契约"。现在
  > schema 会作为最后一段附在请求里。修复后 `scriptwriter` 已实测成功产出
  > `ScriptBreakdown`（HTTP 201）。如果你的 API 进程是在该提交之前启动的，先重启 API
  > 再试。详见 [V1.3 收尾报告 5.1](LOCAL_V1.3_CLOSEOUT_REPORT.md#51-llm_format-的根因与修复本轮后续提交)。

---

## 4. 导入资产并冻结风格

### 4.1 导入

```powershell
# 上传到项目（multipart）
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8100/api/v1/projects/$projectId/assets" -Form @{
  file = Get-Item 'D:\素材\靓靓战术001.png'
}
```

导入后设置角色/实体/标签（`PATCH /api/v1/assets/{asset_id}/metadata`），再由人工批准
（`POST /api/v1/assets/{asset_id}/approve`）与锁定（`POST /api/v1/assets/{asset_id}/lock`）。
两个端点的请求体都是 `Decision`：

```json
{ "grant_id": "<授权凭据>", "snapshot": "<当前快照>", "evidence": "<人工判据>" }
```

只导入**你自己拥有或已获授权**的素材。AI 可以生成候选资产行，但**只有经认证的人工
授权**才能把候选提升为 `Asset` 主档或冻结音色档案。

### 4.2 冻结 Style Bible

Style Bible 有七个维度，全部必填：

```powershell
$style = @{
  photography_language = '...'
  shot_blocking        = '...'
  color_palette        = '...'
  lighting_tone        = '...'
  lens_texture         = '...'
  editing_rhythm       = '...'
  sound_performance    = '...'
} | ConvertTo-Json
Invoke-RestMethod -Method Put -Uri "http://127.0.0.1:8100/api/v1/projects/$projectId/style/draft" `
  -ContentType 'application/json' -Body $style

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8100/api/v1/projects/$projectId/style/freeze" `
  -ContentType 'application/json' -Body '{"grant_id":"...","snapshot":"...","approval_note":"..."}'
```

冻结后**每个新镜头都会引用当前的冻结版本**。已冻结的版本不会被改写；改风格就是新冻结
一个版本。

> **重要**：官方 H3 规则 `H3_ENGLISH` 要求视觉描述用英文。Style Bible 里的七个维度会被
> 编译器**原样**嵌进提示词的 `detailed_description`，所以用中文写维度会直接被规则以
> HTTP 422 拒绝：`CJK found outside dialogue or quoted visible text`。补救办法就是把这
> 七项改成英文（对白和画面可见文字保持原语言）。本轮就是这么处理的：v1 是中文，被拒；
> 冻结了英文的 v2 之后镜头 C 才编过。

改风格前后可以先看影响面：`GET /api/v1/projects/{id}/style/impact`。

---

## 5. 提交一个 H3 镜头

### 5.1 先确认档案

```powershell
Invoke-RestMethod 'http://127.0.0.1:8100/api/v1/workflow-profiles' |
  Select-Object id, name, mode, graph_sha256
```

本轮登记的五个档案（都通过了实时 `object_info` 校验）：

| 名称 | 用途 |
| --- | --- |
| `local-h3-ref2va-2img` | 历史档案，原生 20 步，480x864 |
| `local-h3-ref2va-2img-1aud` | 历史档案 + 一条音频参考 |
| `local-h3-ref2va-pdd8-2img` | **推荐**：PDD 8 步，模型原生 1344x768 |
| `local-h3-ref2va-pdd8-2img-1aud` | 同上 + 一条音频参考 |
| `local-h3-ref2va-pdd8-upscale-2img` | 同上 + 3D 潜空间 1.5 倍超分与 PDD 尾部细化 |

参考槽位（`parameter_map`）固定是 `reference_subject_1`（人物）、`reference_picture_1`
（画面/关键帧）、`reference_audio_1`（音色，带对白时用）。**没有映射的槽位会以
`PENDING_WORKFLOW_MAPPING` 阻断渲染**，不会被静默丢掉。

### 5.2 建镜头

```powershell
$shot = @{
  name = 'A-PDD8-7s'
  spec = @{
    story_beat     = '[Shot 1] <Subject 1> 在雨夜霓虹大道上向镜头走来……'
    duration_target_s = 7.0
    shot_priority  = 'A'
    camera         = @{ shot_size = 'medium_shot'; movement = 'slow_dolly_in' }
    characters     = @('CHAR_LIANG')
    dialogue       = @()
    sound          = @{ environment = 'steady night rain'; ambience = 'low city neon buzz'; music_policy = 'low_or_none' }
  }
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8100/api/v1/projects/$projectId/shots" `
  -ContentType 'application/json' -Body $shot
```

绑定素材：`PUT /api/v1/shots/{shot_id}/bindings`，体为 `{"bindings":[...]}`。绑定是权威的
—— job 不允许换用另一套素材。

预览编译结果（不提交渲染）：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8100/api/v1/shots/$shotId/compile"
```

编译器会把全局资产 ID 重映射成请求内的标签 `<Subject 1>` / `<Picture 2>` / `<Audio 1>`。
标签编号只在当前请求内有效。

### 5.3 建 job

```powershell
$job = @{
  shot_id            = $shotId
  provider           = 'comfyui'
  generation_mode    = 'Ref2VA'
  workflow_profile_id= '18e2f8d3-ca1d-4f0d-83ae-0bffc1b4b5f2'
  references         = @(
    @{ asset_id='ad0ec279-a9f1-4a57-9f0a-08cc093aeca9'; use_as='subject' }
  )
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8100/api/v1/projects/$projectId/jobs" `
  -ContentType 'application/json' -Body $job
```

依赖镜头加 `depends_on = @('<上游 job id>')`，并把末帧参考资产的 `use_as` 设为
`picture`、`frame_role` 设为 `last_frame`。

提交前请确认：

- `provider` 是 `comfyui`（不是 `mock`）；
- `workflow_profile_id` 是你真正要用的那个档案；
- 素材都已批准并锁定；
- 项目已冻结规则包与 Style Bible。

---

## 6. 看视频、QC、返工与正式审片

### 6.1 看状态

```powershell
Invoke-RestMethod "http://127.0.0.1:8100/api/v1/projects/$projectId/jobs"
Invoke-RestMethod "http://127.0.0.1:8100/api/v1/jobs/$jobId/quality-gates"
```

### 6.2 五道门怎么看

| 门 | 谁在判 | 未通过时 |
| --- | --- | --- |
| `technical` | 机器（ffprobe/ffmpeg），确定性 | 硬错，绝不自动放行 |
| `semantic` | VLM，需要授权的远程评审档案 | 需要评审 |
| `audio` | ASR + 人工试听 | 需要评审 |
| `style` | 与冻结 Style Bible 比对 | 需要评审 |
| `final` | 汇总 | `needs_review` |

**缺失就是缺失，不会被记成通过。** 只有全部必需门都通过，镜头才会走合法路径到
`PASSED`（并标记 `manual_verified`）。

没有配置远程评审档案时，`semantic` 是 `not_run` +
`SEMANTIC_REVIEW_NOT_CONFIGURED`，`style` 是 `not_run` + `STYLE_REVIEW_NOT_COVERED`，
带对白的镜头 `audio` 是 `required` + `not_run` + `ASR_NOT_CONFIGURED`。

这时这些门只能由**人工逐项审阅**关闭：操作员在审片端点签署后，系统会**追加**一组
新的门禁记录（worker 原来那组原样保留）。人工签署只代表人的判断 —— 报告与对外结论
里要写清"由人工关闭"，不能写成模型评审。

### 6.3 返工

- 自动返工**必须**产出不同的请求修订（重新编译的提示词、重建的清单或改了参数）。
- 返工是**有类型、有边界**的，并且必须保护已通过的版本。
- 上游重渲染会让下游依赖失效，下游有效性会被重新计算；**已通过的镜头版本和它们的
  产物不会被覆盖**。
- 不需要无人值守跑完整部剧，也不要用离线测试声称 H3 验收通过。

### 6.4 正式逐项审片

#### 6.4.1 一条命令完成审片（本轮验证过的流程）

```powershell
.\.local-acceptance\scripts\finish_operator_review.ps1
```

脚本按顺序做这些事：

1. **停掉当前这套 Shine 栈** —— 只停它自己启动的进程，不碰 ComfyUI。
2. **问你要操作员标识与凭据** —— 两项都可以直接回车：标识默认 `shine-owner`；凭据
   留空就自动生成一个 32 字节随机值。
3. **在空闲端口上以真实模式重启整栈**（API + worker + 前端）。端口被旧进程占着时
   它换端口，而不是硬撞。
4. **自检** —— 确认 API 确实接受这份凭据，并且回答请求的就是本次启动的那个进程。
5. **记录三个当前镜头的逐项人工审片** —— 十项分项，理由取自 API 返回的实时绑定
   （资产、风格版本、提示词、产物哈希），不是写死的文本。
6. **重新装配交付样片** —— 让它反映审片后的状态。
7. **打印最终门禁状态**。

凭据与身份的处理边界：

- 凭据原文**只存在于这次进程的内存里**，脚本结束时清掉，不写进任何文件。
- API 只保存它的 **SHA-256 摘要**（`SHINE_OPERATOR_TOKEN_SHA256`），不保存原文。
- 脚本会把自动生成的凭据打印**一次**，请把它保存到密码管理器：下次审片要用同一份
  凭据，否则 API 会拒绝新的凭据。
- 脚本按**镜头名**找当前的 job（每个镜头名最新创建的那个），不写死 job id —— 镜头
  重渲染之后旧 job 就不再具备审片资格，写死的 id 会失效。

#### 6.4.2 手动提交审片

```powershell
# 取快照
Invoke-RestMethod -Headers @{ Authorization = "Bearer $credential" } `
  -Uri "http://127.0.0.1:8100/api/v1/operator/jobs/$jobId"

# 取视频（会重新校验归属与文件哈希）
Invoke-WebRequest -Headers @{ Authorization = "Bearer $credential" } `
  -Uri "http://127.0.0.1:8100/api/v1/operator/jobs/$jobId/media" -OutFile "$env:TEMP\shot.mp4"

# 提交审片
Invoke-RestMethod -Method Post -Headers @{ Authorization = "Bearer $credential" } `
  -Uri "http://127.0.0.1:8100/api/v1/operator/jobs/$jobId/review" -ContentType 'application/json' -Body (@{
    snapshot   = '<上一步拿到的 snapshot>'
    request_id = '<唯一 id>'
    watched    = $true
    listened   = $true
    items      = @{
      identity     = @{ status='PASS'; evidence='...' }
      costume      = @{ status='PASS'; evidence='...' }
      scene_props  = @{ status='PASS'; evidence='...' }
      camera_action= @{ status='PASS'; evidence='...' }
      style        = @{ status='PASS'; evidence='...' }
      dialogue     = @{ status='PASS'; evidence='...' }
      voice        = @{ status='PASS'; evidence='...' }
      lip_sync     = @{ status='PASS'; evidence='...' }
      continuity   = @{ status='NOT_APPLICABLE'; evidence='...' }
      other        = @{ status='NOT_APPLICABLE'; evidence='...' }
    }
  } | ConvertTo-Json -Depth 8)
```

规则：

- 十个分项一个都不能少（`identity`、`costume`、`scene_props`、`camera_action`、
  `style`、`dialogue`、`voice`、`lip_sync`、`continuity`、`other`），多一个少一个都会被
  拒绝；每项填 `status`（`PASS` / `FAIL` / `NOT_REVIEWED` / `NOT_APPLICABLE`）与
  `evidence`。
- `request_id` 必须是 16–64 位的字母数字或连字符，且每次审片唯一；重复提交会被拒绝。
- 已审项与不适用项都要写明画面、时间点、实际听到的台词或不适用理由。**不适用不能
  代替必需检查。**
- 带对白的镜头必须**完整观看并试听**才能补齐 audio 门；音轨存在、能量正常或一句泛泛
  的备注都不算数。
- 任一分项 `FAIL` 或必需项未审，镜头保持 `NEEDS_REVIEW`。
- 每次人工审片会追加独立的 QC 报告、五门记录与 `OPERATOR_REVIEW_RECORDED` 审计，
  **不会覆盖** worker 的原始报告。
- 人工关闭的是**语义 / 风格 / 终审**这三道门，以及对白镜头的**音频**门 —— 它们是
  人的判断，不是 VLM 或 ASR 的结论。不要把人工签署说成模型评审。
- Mock 作业没有真实媒体，不接受这种真人媒体质量审批。

本轮的实际结果（2026-10-25 11:30 UTC）：三个镜头全部 `PASSED`、
`quality_verification = manual_verified`，A 与 C 的对白三项记为 `NOT_APPLICABLE`
（它们的 shot spec 没有对白），B 的 `watched` 与 `listened` 都为 true。详见
[真实 H3 验收报告第 6 节](LOCAL_REAL_H3_ACCEPTANCE.md#6-五道质量门)。

---

## 7. 导出样片

```powershell
# 由编辑 Agent 的 EDL 装配（每个片段都要带 reason）
$edl = @{
  schema_version = '1.1'
  fps = 24.0
  clips = @(
    @{ job_id='...'; attempt_id='...'; artifact_id='...'; in_s=0.0; out_s=7.29; transition='CUT'; reason='A full segment' }
  )
} | ConvertTo-Json -Depth 6
$delivery = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8100/api/v1/projects/$projectId/deliveries" `
  -ContentType 'application/json' -Body $edl

# 下载成片
Invoke-WebRequest "http://127.0.0.1:8100/api/v1/deliveries/$($delivery.artifact_id)/media" -OutFile "$env:TEMP\reel.mp4"

# 列出本项目全部交付
Invoke-RestMethod "http://127.0.0.1:8100/api/v1/projects/$projectId/deliveries"
```

装配前系统会对每个来源重新校验：同一项目、非 stale job 的**当前** attempt、**已通过
的真实 technical 门**、落盘文件 SHA-256 仍匹配、in/out 落在实测时长内。任何一条不满足
就整体拒绝。

成片按内容寻址存放在 `artifacts/<project>/delivery/`，因此**不会覆盖**已通过的镜头
产物；每段都记录来源 job id、attempt id、artifact id、artifact sha256 和来源 job 状态。
`review_state` 只有在全部来源 job 都是 `PASSED` 时才是 `all_passed`，否则是
`pending_review` —— 未评审的片子不会被当成已验收。

审片之后要重新装配一次，样片才会反映审片后的状态（先前的成片仍以 `pending_review`
留在盘上，因为交付是内容寻址的）。本轮的结果：

| 项 | 值 |
| --- | --- |
| Artifact ID | `e6b264aa-bd7a-465c-9e43-4092b69b8266` |
| SHA-256 | `4ce496542626c0036e44227cae9c5e28d44c734a3b89bb0e6fe76e925d7fb98e` |
| 时长 / 帧数 | 21.895996 s / 525 帧，1344x768，24 fps |
| 音视频流 | 恰好一条 h264 视频流 + 一条 aac 48 kHz 立体声音频流 |
| `review_state` | **`all_passed`**（三个来源 job 全部 `PASSED`） |
| 导出副本 | `.local-acceptance/media/delivery-sample.mp4`（本机路径，不进 Git） |

导出到 Git 之外：媒体文件不要提交到仓库（`.gitignore` 已排除本地验收目录与 `*.mp4`）。

---

## 8. 出问题时先看哪里

| 现象 | 先查 |
| --- | --- |
| job 一直不动 | `GET /api/v1/system/status` 看 `blocked_jobs` / `worker_online`；再看 worker 日志 |
| job 失败，`COMFY_JOB_NOT_FOUND` | 正常情况由 worker 在 30 秒宽限后按 history 对账恢复；确认 worker 在跑 |
| 队列卡住，后续 job 全被挡 | `GET /api/v1/comfyops/resource-status` 看租约状态（`ACTIVE` / `RECONCILE_REQUIRED` / `RELEASED`） |
| 渲染端没连上 | `h3_connected` 与 ComfyUI 的 `/system_stats`；ComfyUI 必须监听回环 |
| 提交被拒 `PENDING_WORKFLOW_MAPPING` | 档案的 `parameter_map` 缺这个参考槽位，或 `media_transfers` 没声明对应族 |
| 提交被拒 `CJK found outside dialogue or quoted visible text` | Style Bible 的维度是中文，改成英文后重新冻结一个新版本 |
| 连续性被拒 | 档案声明 `hard_continuity: PENDING_HARD_CONTINUITY_TEST`、`video_reference: SUPPORTED_NODE_UNVERIFIED_TRANSFER`；这两项不能绕过 |
| 审片端点 403 | 没配 `SHINE_OPERATOR_ID` / `SHINE_OPERATOR_TOKEN_SHA256`，或凭据错，或用的是 Agent 身份 |
| 审片端点 403，但凭据明明是对的 | 端口上跑的是**旧**服务，它还在用旧凭据。按 `processes.json` 里的 `listener_pid` 找到那个进程结束它，再重启 |
| 启动时报"端口已被占用" | 旧栈还活着。先 `stop_shine.py`，再看它有没有打印 `[warning] port <n> is still held by pid <n>` |
| 候选资产导入 409 `LICENSE_NOT_APPROVED` | 先由操作员批准对应的权利记录 |
| 预制造成本一直 `BLOCKED` | `GET /api/v1/projects/{id}/preproduction/requirements` 看具体缺口码（常见 `ASSET_OR_VOICE_MISSING`） |
| 组团角色 422 `LLM_FORMAT` | 模型输出没满足该角色的结构化契约；不是环境故障 |
| 交付装配被拒 | 错误码会指明原因：`DELIVERY_SOURCE_TECHNICAL_GATE_NOT_PASSED`、`DELIVERY_SOURCE_FILE_HASH_MISMATCH`、`DELIVERY_CLIP_OUTSIDE_SOURCE_DURATION`、`DELIVERY_SOURCE_IDENTITY_MISMATCH` 等 |

排查时的通用做法：

1. 用 `GET /api/v1/jobs/{id}` 和 `GET /api/v1/jobs/{id}/quality-gates` 看当前 attempt 与
   门禁记录；不要从旧 attempt 借用通过状态。
2. 看审计：`OPERATOR_REVIEW_RECORDED`、`COMFY_RECONCILED_HISTORY`、
   `COMFY_OUTPUT_RECONCILIATION_REQUIRED`、`DELIVERY_ASSEMBLED` 等事件记录了每一次
   状态迁移。
3. 看日志：`<data_dir>/run/logs/` 下的 api / worker 日志，ComfyUI 的 stderr。
4. 不要用改数据库、改报告或绕过闸门的方式"修好"一个失败的检查。

---

## 9. 本轮遗留的操作员待办

以下动作**本轮没有执行**，需要操作员本人完成。按建议顺序与精确端点见
[V1.3 收尾报告第 8.2 节](LOCAL_V1.3_CLOSEOUT_REPORT.md#82-剩余操作项按建议顺序)。

1. 批准两条权利记录（asset / voice_design）。
2. 重新导入候选资产 → QC → 人工提升为主档。
3. 导入音色档案 → 试听 → 人工批准并冻结。
4. 冻结预制作清单（先让 readiness 不再是 `BLOCKED`）。
5. 发布工作流能力草案。
6. 可选：自己重新撰写 Style Bible（当前 v2 已是英文版）。

**已完成**：对本轮三个真实镜头的正式逐项审片（2026-10-25 11:30 UTC），
`ba4bb63d-475a-4802-a469-0c4ce166fc97`（A）、
`a2718d14-4a81-4046-8416-e16ce2b8598f`（B）、
`ffffe1d8-86e7-4593-acaa-91ff453b6b31`（C），三个都是 `PASSED` /
`manual_verified`。A 与 B 的**第一版**（`ca3754cc…` / `dee0263c…`）已被重渲染取代，
不再具备审片资格。流程见 [第 6.4 节](#64-正式逐项审片)。

另外，若要给 ComfyOps 配置本机探测范围，需要设置 `SHINE_COMFY_PROBE_ROOTS` 与
`SHINE_COMFY_PROBE_PORTS`；不配置时相关端点返回 `LOCAL_PROBE_SCOPE_NOT_CONFIGURED`，
属配置项待办。

## 10. 串行模型阶段与本地机器审片

所有工种与模型共用串行准入。上一步结束后先实际卸载，再执行下一步；显存预算够用也不并行。
未知、失联或卸载失败的资源保留阻断。不要手工删除 ResourceLease 或重放不确定调用。

在“模型与系统”将明确的本地 OpenAI-compatible Provider 分别路由到“画面审片”和“语音核对”。
本地画面和语音审片服务须提供可核验的 `/free` 和 `/health`。卸载后，`/free` 必须确认已分配
显存为零；`/health` 还须确认两个模型均未加载、已分配及保留显存均为零。缺失字段、服务失联
或任一检查不通过时保留阻断，不能进入下一模型。自定义本地审片服务需要实现这些字段。
这些检查不证明审片进程的宿主内存已全部归还；需要进程级释放时仍须使用受管的串行交接。
文本 `qc_reviewer` 路由不会代替图像或音频评审；远程派生媒体仍须项目明确许可。

`start_shine.ps1` 可额外接收 `-ReviewPython`、`-ReviewManifest`、`-ReviewRuntimePackages`（三项一起指定）
及可选 `-ReviewPort 8116`。解释器/模型清单/依赖目录必须已经存在，启动器不下载或安装模型。
review 通过哈希校验与健康检查后才启动 Worker；它与 API/Web 一起进入同一进程身份清单，由 `stop_shine.py` 统一关闭。

Windows 本地审片还可在上述参数之外显式加 `-IsolatedReviewProcess`：每次画面或语音请求
在受管子进程中执行，子进程退出后才返回结果；进程失联、超时或身份变化会阻断后续释放确认，
不自动重放请求。此模式启动和每次审片都需要重新核验模型文件，耗时较长。它有独立执行器
指纹，必须重新做真实能力校准并发布，不能沿用旧进程模式的资格。该选项只处理审片子进程，
不会自动停止或重启 ComfyUI，也不能证明外部程序已经释放内存或显存。

若需保留现有审片端点并单独使用另一模型，可额外指定 `-AdditionalReviewManifest` 和
`-AdditionalReviewPort`（默认 8117）；它复用已指定的审片解释器和依赖目录，始终使用
进程隔离模式，并由同一启动清单管理。该端点不会自动替换任何 Provider 或已冻结计划，
需要明确配置路由并完成相应能力校准。

真实渲染已完成而机器审片暂时不可用时，可在该 Job 详情使用“仅重跑审片（保留视频）”。
入口最多两次，保留原 Attempt/Artifact，追加新的质检结果；硬失败、旧版本、文件变化和重复请求会被拒绝。
这不是人工批准入口，也不重新提交 H3。

失联调用的对账在“制片准备 → 环境中心 → 读取模型调用与失联记录”。旧审片记录没有 cleanup Provider 时，
先填写实际历史 Provider ID，生成固定目标提案并核对 model / profile / URL 哈希。随后由操作员输入本机凭据和
具体核验依据，执行“核验队列与释放后放弃丢失结果（不重投）”。服务会重查 owner 已退出、Provider 未改变及真实释放。
失败继续保留阻断；成功只记录丢失结果被放弃，不自动重投、不批准镜头，也不替代资产/音色/工作流的人工批准。

### 10.1 Qwen SDK 按角色隔离

Qwen TTS 0.1.1 固定 Transformers 4.57.3，ASR 0.0.6 固定 4.57.6；分别使用独立依赖目录。
管理员在 API 启动环境设置 `SHINE_VOICE_ROLE_RUNTIMES` JSON，按 `design`、`base`、`asr`、`align`
映射对象。每个对象包含 `python`（绝对路径的直接解释器，不使用 Windows venv redirector）、
`python_sha256`、`packages_dir`（绝对路径）、`versions`（至少对应 SDK / transformers / torch 的精确版本）、
可选 `binaries`（绝对二进制路径到 SHA-256，例如 SoX）、`timeout_s`（1–900）和 `budget_mb`（1–16384）。
解释器/辅助二进制、SDK 版本与模型文件变化均会阻断执行；不安装未知版本或自动回退。

`SHINE_VOICE_SDK_PACKAGES` 仍须映射到已验证文件的 ModelPackage ID；配置目录本身不批准模型包或替代显存测量。
未登记资源需求的包继续阻断。API 会为一次对白的合成/识别/对齐持有覆盖各角色的串行租约，
每一步使用独立进程并等待退出。父 API 不载入 TTS/ASR SDK，因此它们的固定依赖不会互相覆盖。
语音正文与参考样本通过私有 stdin 传递，不出现在命令行；没有新克隆 HTTP 入口。

本机隔离验收目录已有 `voice-role-runtimes.json` 配置草案及 `voice-role-runtime-probe.json` 导入记录。
这些文件未自动启用，也不证明 Voice Profile/字幕/音色/口型通过；必须先完成对应安装、权利、主档和音色审批。

### 在页面恢复失败的包安装

在“制片准备 → 模型与节点”中查看当前项目的计划，点击“查看安装尝试”，核对尝试 ID、状态及错误。
`FAILED_RECONCILE_REQUIRED` 不会自动重试。选择本地模式，输入本人的操作员凭据、核验依据并确认范围后，
可点击“核对安装失败（不重试）”。后端使用独立的一次性 `install_reconcile` 批准令牌，在文件锁下检查
原计划和包摘要、活动尝试、未完成交换及目标文件；未知文件状态会拒绝，且不消耗该批准。

成功状态为 `RECONCILED_NO_RETRY`。包内容与运行验证级别保持原状，旧错误和尝试记录保留。
需要继续安装时，填写已核对的磁盘预算，再点击“从原清单新建待批准计划”；新计划为 `AWAITING_APPROVAL`，
须重新核对并批准其哈希后执行。页面不会重放旧尝试、沿用旧批准或自动批准新计划。
该入口只处理包安装；实例生命周期和丢失模型调用仍使用各自的对账入口。

### 文本输出预算（2026-10-27）

OpenAI-compatible 配置可选填最大输出 token 和思考深度；留空沿用服务商默认值。MiniMax M3.1 的 token 上限包含思考和回答，不能据此保证响应时间或质量。新 QC 预算验证拟使用 8192 / low，其他角色配置不变；截断会拒绝，不能通过提高预算自动重发未知请求。参数来源：[MiniMax 官方 OpenAPI](https://platform.minimax.cn/docs/api-reference/text/api/openapi-chat-openai.json)、[模型调用说明](https://platform.minimax.io/docs/guides/text-generation)。
