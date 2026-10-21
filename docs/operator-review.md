# V1.2.1 本地人工审片

审片入口：生产监控选择真实 `NEEDS_REVIEW` 任务。API 与 Web 默认仅监听
`127.0.0.1`。普通 API 尚无完整用户认证，不能开放到局域网或互联网。

## 操作员授权

本地管理员在启动 API 的终端配置 `SHINE_OPERATOR_ID` 和
`SHINE_OPERATOR_TOKEN_SHA256`。身份取自服务端配置，客户端不接受 username。
使用至少 32 字符的随机凭据，保存在操作员自己的密码管理器中。只将摘要传入 API；
不把原始凭据写入 `.env`、数据库、Git、日志、URL 或 localStorage。

PowerShell 7 中，在启动 API 的同一终端执行（输入已有的随机凭据）：

```powershell
$env:SHINE_OPERATOR_ID = Read-Host '授权操作员标识'
$operatorCredential = Read-Host '操作员随机凭据（至少32字符）' -MaskInput
if ($operatorCredential.Length -lt 32) { throw '凭据过短' }
$env:SHINE_OPERATOR_TOKEN_SHA256 = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($operatorCredential))
).ToLower()
Remove-Variable operatorCredential
uv run --project backend --locked uvicorn shine.main:app --host 127.0.0.1 --port 8000
```

这两个变量缺失时，审片默认拒绝。改变操作员或凭据摘要需要本机管理员修改 API
进程环境并重启 API。Agent Runtime 不持有该凭据，也无操作员工具。
此机制信任本机 OS/管理员；不能防御拥有同一 OS 管理权限的恶意进程。

## 查看与提交

- `GET /api/v1/operator/jobs/{id}`：认证后取得完整绑定快照及快照摘要。
- `GET /api/v1/operator/jobs/{id}/media`：再次验证当前归属和文件哈希后返回视频；禁止缓存。
- `POST /api/v1/operator/jobs/{id}/review`：Bearer 凭据 + `OperatorDecision`。

请求必须带 `snapshot`、唯一 `request_id`、`watched`、`listened`，以及
`identity/costume/scene_props/camera_action/style/dialogue/voice/lip_sync/continuity/other`
十项。每项分别填 `PASS/FAIL/NOT_REVIEWED/NOT_APPLICABLE` 和具体证据。
已审项及不适用项必须写明画面、时间点、实际听到的台词或不适用理由。
不适用不能替代必需检查。对白镜头必须人工完整观看并试听，才能补齐 audio 门。

复核绑定包含 Job/state_version/request_revision、最高 attempt_no、原始请求指纹、
当前 Shot 修订、冻结 Style ID/hash/内容、RulePack ID/hash/锁、Artifact ID/归属/hash/大小、
ComfyUI Prompt ID/提交图 hash、质量策略和已有门禁证据。历史未锁规则包的请求不会
被偷偷补上新的锁；若当前项目已新增锁，旧请求被拒绝。

提交时只读取 ComfyUI History，不调用 `/prompt`。History 必须表示完成成功，且
Prompt ID 与实际提交图 hash 相符。重新运行技术 QC、重新计算文件哈希，然后取得
SQLite `BEGIN IMMEDIATE` 写锁并重新验证全部快照，最后条件更新 state_version。
并发变化、复用请求、错误 Attempt、损坏媒体、过期修订或缺少 QC 均拒绝。

每次人工操作追加独立 QCReport、五门记录及 `OPERATOR_REVIEW_RECORDED` 审计，
不覆盖 Worker 原始报告。FAIL 与缺失检查保持 `NEEDS_REVIEW`；仅全部必需门通过
才沿合法路径到 PASSED，并标记 `manual_verified`，不能当成模型审片。
Mock 无真实媒体，不接受这种真人媒体质量审批。

维护脚本 `scripts/record_operator_review.py JOB --decision decision.json` 仅是同一 API
的客户端，交互式读取凭据，不再直接写数据库、接受笼统备注或选择第一个 Attempt。

## 本轮证据边界

2026-10-21 在隔离的 V1.2 数据库副本上验证了登录、当前绑定和测试 B 视频预览；
没有由 Agent 点击通过或代替用户写回审批。用户本轮确认声音、音色、对白逐字准确和
口型同步，这作为人工反馈记入收尾报告，不冒充已经通过新服务写入的逐项审批事件。
原三个样本 480×864 的画质被用户拒绝，因此不能作正式短剧画质通过证据。
