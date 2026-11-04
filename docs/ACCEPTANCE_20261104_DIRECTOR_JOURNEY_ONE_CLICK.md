# 2026-11-04 导演模式全旅程 + 一键开拍 + 语言修复 验收记录

本记录覆盖同日连续三轮实测（真实本机 H3，PDD 8 步加速），全部在同一工作台内完成，
无任何流程靠脚本直连后端驱动。

## 一、真实用户全旅程（第三轮终态）

作品「浏览器全流程演示」（d098d429-075d-4a3a-abed-e850f289c5af）：

| 步骤 | 结果 |
| --- | --- |
| 我的作品 → 新建作品 | 状态条打勾推进，下一步条全程指引 |
| 上传人物/场景/音色素材 → 确认使用 | 3 项 approved+locked |
| 整套风格「自然写实」→ 批准并冻结 | V1，规则包随冻结一并绑定（合并动作） |
| 粘贴无对白剧本 → 生成 AI 分镜 | 5 工种真实 LLM 调用全部 SUCCEEDED；摄影一次被拒后链内自动重试成功 |
| 人物对照（阿德 ↔ ade.png）→ 确认并写入名字 | 名字写入 master 并冻结，自动从导演续跑 |
| 应用为正式分镜 | 2 镜进入制作列表 |
| 一键开拍（自动适配） | 自动选方案 + 按方案槽位自动补齐参考 + 自动勾选 + 提交，一次点击 |
| 真实 H3 渲染 ×2 镜 | 三轮序列全部 COMPLETED 2/2（0f57b8f4 / b85742d1 / a7a76b4b） |
| 自动装配交付 | 三次自动交付（18.85s / 1344×768），最新置顶带「最新」徽标，历史折叠 |

计时器实测：本镜/总计按服务端 UTC 时间戳逐秒走动（曾出现 8 小时时区偏差，
根因是 naive UTC 时间串被按本地时区解析，已修复并以 `Z` 后缀归一）。

## 二、外语呢喃根因与修复（用户报告驱动）

现象：无对白镜头成片出现不明外语呢喃。逐层排查：

1. 语言设置链路全部正确：project.language=Chinese（默认即 Chinese）、
   `project_language=Chinese` 进入 RenderRequest、无对白合同
   "do not produce speech, voice-over or vocal sounds of any kind" 在提示词内。
2. 第一层修复（提示词自相矛盾）：冻结风格「声音表演」维度
   （"Use the project's specified language and confirmed vocal character…"）
   在无对白镜头与禁声合同冲突，诱导模型开口。`H3PromptCompiler` 修复：
   无对白镜头不再引用该维度（风格句改为 "no spoken dialogue in this shot"，
   声景句不再引用 frozen sound rule）。回归测试
   `test_a_dialogue_free_shot_never_quotes_the_speech_style_dimension`。
3. 第二层修复（真正的诱因）：镜头携带 12 秒真人音色参考（按方案 audio 槽自动补齐）
   ——H3 把人声样本当语音线索，无视禁声文本。修复：
   - `validate_declared_transfer_families(dialogue_free=…)：无对白镜头豁免 audio family`，
     提交图靠既有 `prune_unused_reference_nodes` 剪掉音频装载节点（无 placeholder）；
   - 前端一键适配：无对白镜头不再自动补人声参考。
4. 第三轮成片（无音频输入）由用户亲耳确认：**外语呢喃消失**。
   已产出的历史成片按产品规则不重写，以多次重新交付保留对照。

证据：series a7a76b4b（COMPLETED 2/2）→ delivery 84e91ef7；用户确认于 2026-11-04 晚。

## 三、拍摄质量三档（上游既有标准图，无自造工作流）

部署清单 1.1.0 → 1.2.0，`workflow_profiles` 纳入上游既有变体：

- 快速：local-h3-ref2va-pdd8-2img-1aud（PDD 8 步，ShineH3SamplingMode=PDD 8 steps，
  MiniMaxH3PDDAccApply 供 BasicGuider.model 与 SamplerCustomAdvanced.sigmas）
- 高质量：local-h3-ref2va-2img-1aud（原生 20 步；说明耗时约 2~3 倍）
- 超清：local-h3-ref2va-pdd8-upscale-2img（8 步 + MinimaxH3LatentUpscaler3DRefineHandoff
  画面放大；组件 Comfyui_Minimax_h3_latent_Upscaler 已在部署清单）

一键开拍卡内三档 radio；缺档标「未登记」，`POST /setup/profiles` 幂等补齐。

## 四、孤儿 H3 租约死锁修复

worker 在「取得 h3: 租约之后、写入受管启动记录之前」崩溃会留下无记录可证的
RECONCILE_REQUIRED 租约，旧逻辑永不可能释放 → 全局制作死锁（本日实测触发）。
`_release_orphan_h3_lease`：job 从未真实渲染（无 attempts/provider_job_id）、
无任何带活进程的启动记录、无 handoff-reservation、受管端口空闲 → 三证齐全才
释放并写审计 COMFY_ORPHAN_H3_LEASE_RELEASED；任何不确定保留阻断。
回归测试 2 条（test_managed_process_release.py），并保持
test_handoff_lease_transfer 的「预订必须保守」语义。

## 五、测试与验证命令（全部实际执行）

- backend：`uv run pytest tests/`（全量）；ruff check shine tests 通过
- 分批：production_series / comfy_handoff_journal / handoff_lease_transfer /
  advisory_queue / managed_process_release / offline_e2e / h3_compiler /
  output_spec_and_identity / reference_slot_pruning / package_entrypoints 全绿
- frontend：`npx tsc --noEmit`、`vitest run`（115/115，含 6 处 fixture 适配与
  2 处行为更新断言）、`npm run build`
- 安装器：install_once.ps1 新增 New-DesktopShortcut（桌面「Shine 视频工作台」指向
  START_APP.cmd，幂等覆盖，失败不阻断；PS1 保持 UTF-8 BOM）

## 六、诚实边界

- 计时器的逐秒走动已在真实渲染窗口目验（171s→28922s 连续读数），但未录制视频留档。
- 高质量（原生 20 步）与超清（放大）两档未做真实整片渲染实测（登记/选择/匹配已验证）；
  首次使用时按进度卡观察即可。
- VLM/ASR 建议式审片在本次三轮中 UNAVAILABLE（审片模型未随栈启动），不影响出片，
  结论留待人工复核——与产品规则一致。
