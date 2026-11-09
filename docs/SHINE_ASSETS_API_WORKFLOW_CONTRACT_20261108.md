# Shine Assets API 与工作流能力合同

此文档与 [ADR 0008](adr/0008-asset-center-shared-kernel.md)、[ADR 0009](adr/0009-shine-assets-global-data-contract.md) 一起描述实现合同。所有 API 使用 `/api/v1`。本机单人服务仍受 loopback 和本地动作校验保护；它不是面向互联网的多用户身份服务。

## API

| 方法和路径 | 用途和关键条件 |
|---|---|
| `GET /asset-center/capabilities` | 只读工作流、模型、服务状态和能力验证状态。没有本机证据时返回 `PENDING_LOCAL_VALIDATION`。 |
| `GET /asset-center/local-validation-plan` | 返回锁定的官方 Qwen 编辑模板、输入槽、遮罩/输出检查和本机实测证据要求。 |
| `POST /asset-center/uploads` | 校验上传种类、MIME、实际解码、大小、hash 和用途，mask 限 PNG。来源字段可省略。 |
| `GET /asset-center/assets[?q=&category=]` | 搜索无项目全局素材。 |
| `POST /asset-center/assets` | 用已导入的 `media_id` 建立素材容器和待审核候选。 |
| `GET/PATCH /asset-center/assets/{id}` | 详情/带 `expected_state_version` 的元数据更新。 |
| `POST /asset-center/assets/{id}/archive` | 归档容器，不删除字节或旧项目引用。 |
| `GET/POST /asset-center/assets/{id}/versions` | 列版本历史，或从同素材的源版本创建新的候选分支。 |
| `GET /asset-center/versions/{id}` | 读取不可变版本合同、来源清单、授权快照、QC 和输出元数据。 |
| `POST /asset-center/versions/{id}/qc` | 重新哈希、媒体解码和 RGBA/对白质量检查。 |
| `POST /asset-center/versions/{id}/grant` 与 `/promote` | 一次性候选晋升授权；快照过期则拒绝。 |
| `POST /asset-center/assets/{id}/select-version` | 带版本号比较后更新全局默认正式版本。 |
| `POST /asset-center/image-jobs` | 显式创建 T2I、I2I、INPAINT、MULTIREF、WARDROBE 或 RGBA 任务。必须带 `idempotency_key`。 |
| `GET /asset-center/image-jobs/{id}` | 读取持久状态；刷新不会重提任务。 |
| `POST /asset-center/image-jobs/{id}/cancel` | 只允许安全取消尚未开始外部提交的任务。 |
| `POST /asset-center/image-jobs/{id}/reconcile` | 只查询既有 ComfyUI history 并收获已完成结果，不重新提交。 |
| `POST /asset-center/image-jobs/{id}/new-attempt` | 仅在原请求确认 `FAILED + NOT_SENT + no submit intent` 时可用；必须显式确认、新 idempotency key，并重查授权/当前能力。UNKNOWN 一律拒绝。 |
| `POST /asset-center/voice-jobs` 与 `GET /asset-center/voice-jobs/{id}` | 通过本机声音 provider 提交和查看设计、克隆、TTS 状态。克隆从 `reference_media_id` 解析来源记录。 |
| `POST /asset-center/voice-profiles/{id}/grant` 与 `/freeze` | 用户试听并确认，冻结绑定当前快照。 |
| `GET /asset-center/voice-profiles` | 全局音色与冻结状态。 |
| `POST /asset-center/assignments` / `GET /asset-center/assignments` | 按项目创建本地素材身份并查看使用记录。默认 `usage_scope=local`，直接提交分配。 |
| `GET /projects/{id}/asset-center/search` | 导演侧搜索可在本机分配的全局正式版本。 |
| `POST /projects/{id}/assets/{asset_id}/save-to-library` | 把本项目已确认、锁定的真实素材收藏为全局候选。 |
| `POST /asset-center/adoptions/preview` 与 `/adoptions` | 预览和采用到指定镜头 revision。项目内主素材可省略 `library_version_id`。 |

错误对象以稳定 `code` 和中文 `message` 表达；调用方应把 409 当成快照/授权冲突，重新读取后由用户决定下一步。`UNKNOWN`、资源等待和能力缺失不能被伪装为普通失败。

专业模式的候选导入、生成、批量备料与音色创建同样支持省略来源 ID。项目音色的本机来源记录由服务端建立，继续供冻结清单引用。带显式历史来源记录的调用仍按该记录校验。声音克隆保留本机操作标记，候选晋升和音色冻结继续绑定一次性快照。

## 图片请求

```json
{
  "idempotency_key": "client-generated-unique-key",
  "title": "雨伞候选",
  "mode": "MULTIREF",
  "prompt": "保留 image1 的人物，参考 image2 的外套和 image3 的帽子",
  "references": [
    {"version_id": "promoted-version-id", "order": 0, "role": "subject"},
    {"media_id": "authorized-media-id", "order": 1, "role": "garment"},
    {"media_id": "authorized-media-id-2", "order": 2, "role": "hat"}
  ],
  "output": {"width": 1024, "height": 1024},
  "candidate_count": 1,
  "category": "character",
  "validation_only": true
}
```

服务端只接受库内 `media_id` 或已晋升全局 `version_id`，不接受文件路径或任意 URL。参考图顺序从 0 连续递增；官方 UI 模板公开 `images.image_1` 到 `images.image_10`，其中 `image_1` 是底图，支持总数最多 10 张。若目标版本、授权、文件字节 hash、遮罩或当前素材需求发生变化，请求会在排队/提交前被拒绝。

`validation_only=true` 只允许一个样例。任务仍经过本机 ComfyUI、Worker、ResourceLease 和候选 QC；它不会自动晋升，也不会把云端模拟变成 READY。

## Qwen-Image-2.1 图身份

- T2I：沿用现有 `shine.materialprep.qwen_workflow` 锁定图和 `resolve_runtime`。
- Edit：`Comfy-Org/workflow_templates/templates/image_qwen_image_2_1_image_edit.json`，commit `21d7cc4fb9afd983957f2d2d6b1d583860ccb40b`，SHA-256 `c1e6551759de131f083a040da072ac6f032c3c8401ceca202412cd912897d0fc`。
- 模型仓库：`Comfy-Org/Qwen-Image-2.1` revision `aacf31a6d48e707f352ecba714cee0680da3479b`。
- 输出节点：`461`。必须从此节点按本次 `prompt_id` 收获；不能拿预览节点或历史文件代替。
- 版本记录 API graph hash、模板/适配器 hash、模型 revision、每个输入 SHA、顺序/语义槽、授权快照、mask SHA、外部 prompt ID、输出 SHA 和 technical QC。

Edit 提示词附加明确 `<image1>` 至 `<image10>` 槽位说明。`I2I`、`MULTIREF` 和 `WARDROBE` 使用原生编辑图，绝不回退到 T2I。官方图没有独立 mask 节点；INPAINT 把用户真实遮罩 PNG 追加为最后一个 `images.image_N` 输入槽，并将同一 mask 用于输出合成。遮罩占用十个总槽位之一，超过上限会拒绝。白像素取生成结果，黑像素保留原像素；遮罩 SHA、尺寸和来源已存入版本来源清单。只有实际小样证明图节点接受遮罩标注并通过像素级反例后才允许正式提交。

RGBA 有两条不同路径：使用 PNG 底图和用户画布遮罩时，白色保留主体、黑色写透明 alpha，作为确定性本机像素操作；不称作 AI 分割。提示词生成/编辑透明图仍要真实证明输出 PNG 有透明像素。仅有 PNG 扩展名或 Alpha 通道声明不能通过 QC。

## 声音权利与调度

声音任务复用 `QwenLocalVoiceProvider` 或本机 `LocalVoiceHTTPProvider` 及共享 TTS ResourceLease。`AUTHORIZED_CLONE` 在提交前复核样本 SHA 和来源快照。SDK 子进程的身份和退出证明写入声音 attempt。TTS 使用已晋升且试听冻结的全局音色，并执行现有 ASR、词级对齐和 hard-error 检查。音效和音乐支持导入与管理。

图片和声音 GPU 任务统一由现有 Worker 串行执行：项目级图像队列完成一个任务后 Worker 才回到主调度；无项目素材图像与声音任务按入队时间选一个处理，每个 tick 最多执行一项。H3 series 继续保持更高优先级，素材任务不得抢租或释放 H3 renderer。

每项 GPU 任务持有同一 `ResourceLease` 直到执行和清理结束。ComfyUI 清理要求队列为空、`/free` 成功，并由 `system_stats` 确认无活跃 CUDA tensor；声音进程型 provider 要证明推理子进程退出，HTTP provider 要返回 `released: true`。TTS 在合成后先确认清理，再进入 ASR；ASR 清理确认后才进入词级对齐。最后清理确认并执行 GC 后才释放租约。任何清理响应缺失、内存报告未知、租约失效或进程仍存活都会阻断后续阶段，将资源标为 `RECONCILE_REQUIRED`；不会根据超时猜测显存已经释放。

Qwen-Image-2.1 当前来源条款是 2026-09-20 research license。不得自动开放商业用途、转售、再分发或将用户/真人肖像与声音授权混为一谈。声音和图像派生权利分别继承具体源声明/快照。
