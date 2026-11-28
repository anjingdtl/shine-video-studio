/** 错误码 → 一句话原因 + 可选动作（设计方案 §7.2/§7.3）。
 *  取代「；」join 整句一行：每条 blocker 一行，可行动的带「去处理」按钮。
 *  target 是语义导航键，由装配层（App）解析为真实跳转，前端不硬编码路由。 */
export type ErrorActionTarget =
  | 'setup-models' | 'setup-env' | 'assets-stage' | 'plan-stage' | 'package-stage'
  | 'creative-stage' | 'produce-stage' | 'run-stage' | 'library' | 'retry-scene'
  | 'split-plan' | 'consent';

export type ErrorAction = {
  reason: string;
  /** 为什么这一步必须做：必做动作块要向人说清代价，不只说缺什么。 */
  why?: string;
  action?: { label: string; target: ErrorActionTarget };
  /** 当 action 的落点页就是用户当前所在页时改指的动作：真正的上游前置。
   *  例如分集未确认出现在剧本统筹页本身，此时真正缺的是第 1 阶段的大纲确认。 */
  selfLoopAction?: { label: string; target: ErrorActionTarget };
  /** 文案以服务端 message 为准（状态相关码）：前端不拿静态句盖掉后端事实。 */
  serverMessage?: boolean;
};

export const ERROR_ACTIONS: Record<string, ErrorAction> = {
  PENDING_WORKFLOW_MAPPING: { reason: '本作品还没登记生成方案', action: { label: '去基础配置登记', target: 'setup-models' } },
  CHARACTER_SHEET_LORA_REQUIRED: {
    reason: '缺少「角色五视图定妆照」所需的 Qwen 多视角镜头 LoRA 权重',
    why: '角色定妆照不会静默降级为无 LoRA 生图；请先下载并纳管该权重，或关闭「人物生成 16:9 五视图定妆照」开关。',
    action: { label: '去制作环境下载', target: 'setup-env' },
  },
  SCENE_SHEET_LORA_REQUIRED: {
    reason: '缺少「场景四视图母版」所需的 Qwen 多视角镜头 LoRA 权重',
    why: '场景四视图不会静默降级为无 LoRA 生图；请先下载并纳管该权重，或关闭「场景生成 16:9 四视图母版」开关。',
    action: { label: '去制作环境下载', target: 'setup-env' },
  },
  WORKFLOW_SIZE_NOT_MAPPED: { reason: '生成方案没有接入画幅设置', action: { label: '去基础配置登记', target: 'setup-models' } },
  PROFILE_NOT_VALIDATED: { reason: '生成方案还没在本机验证节点', action: { label: '去验证本机节点', target: 'setup-models' } },
  H3_CHARACTER_REFERENCE_MISSING: { reason: '有出场人物没有主素材', action: { label: '去准备素材', target: 'assets-stage' } },
  SHOT_CHARACTER_MASTER_MISSING: { reason: '镜头点名的人物缺主素材', action: { label: '去准备素材', target: 'assets-stage' } },
  SHOT_REFERENCE_MISSING: { reason: '镜头至少需要一张参考图（人物或场景）才能 Ref2VA 开拍', action: { label: '去准备场景图', target: 'assets-stage' } },
  SCRIPT_TEXT_CONSENT_REQUIRED: { reason: '首次发送剧本到云端需同意', action: { label: '查看说明并同意', target: 'consent' } },
  REMOTE_AGENT_CONSENT_REQUIRED: { reason: '首次发送剧本到云端需同意', action: { label: '查看说明并同意', target: 'consent' } },
  SHOT_REVISION_CHANGED: { reason: '镜头已升版，需要新导演包', action: { label: '重新生成导演包', target: 'package-stage' } },
  PIPELINE_EPISODE_SCRIPT_SUPERSEDED: { reason: '分集剧本已被新大纲替代', action: { label: '重做这一卷分集', target: 'package-stage' } },
  SHOT_DURATION_OVER_LIMIT: { reason: '镜头时长超过你设的单镜秒数上限', why: '在剧本统筹调高「单镜秒数上限」，或让这一镜的台词更短。', action: { label: '重试这一场', target: 'retry-scene' } },
  EDITORIAL_TIMING_CONFLICT: { reason: '台词放不进生成段，或生成段时长与剧本切点表对不上', why: '重试会把这条规则明确告诉模型：镜头要装得下它承载的每一句台词。', action: { label: '重试这一场', target: 'retry-scene' } },
  SHOT_LOCATION_UNKNOWN: { reason: '镜头的地点不在已登记的场景里', action: { label: '重试这一场', target: 'retry-scene' } },
  SHOT_DIALOGUE_LONGER_THAN_SHOT: { reason: '这一镜的台词比镜头还长，按原速说不完', why: '落镜不再悄悄压缩台词时间；重新生成会要求导演把镜头拉长到装得下台词。', action: { label: '重试这一场', target: 'retry-scene' } },
  // 戏剧层（V4）：小说先改编成可拍的戏，再进分镜。重试会把服务器算出的数字
  // （第几个动作拍、第几句、多少字）告诉模型，不会悄悄把台词改短。
  DRAMA_LINE_TOO_LONG: {
    reason: '有一句台词太长，一个镜头说不完',
    why: '按正常语速（每秒 4.2 字）单句最多 27 字；重试会告诉编剧是哪一句、多少字，由它拆成两句分到两个动作拍。',
    action: { label: '重试这一集', target: 'retry-scene' },
  },
  DRAMA_BEAT_SPEECH_TOO_LONG: {
    reason: '一个动作拍的台词太多，一个镜头装不下',
    why: '一个动作拍的台词合计最多 31 字；重试会让编剧把其中一句移到另一个动作拍。',
    action: { label: '重试这一集', target: 'retry-scene' },
  },
  ARCHITECTURE_SCENE_COVERAGE: {
    reason: '集纲架构没有给每一场各排一个场次槽位',
    action: { label: '重试这一集', target: 'retry-scene' },
  },
  ARCHITECTURE_EPISODE_COVERAGE: {
    reason: '集纲架构没有逐集覆盖已确认的分集计划',
    action: { label: '重试这一集', target: 'retry-scene' },
  },
  STORY_ARCHITECTURE_SUPERSEDED: {
    reason: '集纲架构已过期：大纲或分集计划在它写好之后变了',
    why: '分镜只能按你最新确认的大纲与分集计划来做；重新生成分镜会先重写集纲架构。',
    action: { label: '去重新生成分镜', target: 'plan-stage' },
  },
  // 场景导演 / 连续性引擎（V4）
  BEAT_NOT_COVERED: { reason: '有戏剧动作拍没有被任何镜头拍到', action: { label: '重试这一场', target: 'retry-scene' } },
  DIALOGUE_NOT_IN_SCENE: { reason: '镜头里有台词不是这一场戏写出来的', action: { label: '重试这一场', target: 'retry-scene' } },
  DIALOGUE_DROPPED: { reason: '这一场戏的台词被漏掉了', action: { label: '重试这一场', target: 'retry-scene' } },
  DIALOGUE_TOO_FAST_TO_SPEAK: { reason: '台词分到的时间按正常语速说不完', action: { label: '重试这一场', target: 'retry-scene' } },
  AXIS_ORDER_FLIPPED: { reason: '人物的左右站位跨过了 180° 轴线', action: { label: '重试这一场', target: 'retry-scene' } },
  AXIS_FACING_REVERSED: { reason: '人物的视线背离了对话对象，轴线错位', action: { label: '重试这一场', target: 'retry-scene' } },
  INTERNAL_CUT_COUNT: { reason: '一个渲染段里有不止一次镜内切', action: { label: '重试这一场', target: 'retry-scene' } },
  INTERNAL_CUT_WINDOW: { reason: '镜内切离镜头开头或结尾太近，两边都看不清', action: { label: '重试这一场', target: 'retry-scene' } },
  EXCLUDED_REFERENCE_USED: { reason: '镜头把风格图或末帧图当成了参考图', action: { label: '重试这一场', target: 'retry-scene' } },
  LLM_TIMEOUT: { reason: '云端模型响应超时', action: { label: '重试这一场', target: 'retry-scene' } },
  LLM_RATE_LIMIT: { reason: '云端模型限流，稍后重试', action: { label: '重试这一场', target: 'retry-scene' } },
  MATERIAL_IDENTITY_AMBIGUOUS: { reason: '同一人物在多场外观描述不一致', action: { label: '按单场拆分计划', target: 'split-plan' } },
  // 「去剧本统筹」必须真的落到剧本统筹（#d/assets）。它原来指向导演包（#d/package），
  //  恢复动作把用户送到比来源更靠后的阶段，绕圈而不推进。
  EPISODE_PLAN_NOT_CONFIRMED: {
    reason: '分集方案尚未确认',
    action: { label: '去剧本统筹', target: 'plan-stage' },
    // 这个阻塞就出现在剧本统筹页本身：此时「去剧本统筹」是零进展的自环，
    // 真正的上游前置是第 1 阶段的大纲确认（该页点「生成分镜书」返回
    // 422 SCRIPT_OUTLINE_NOT_CONFIRMED，/journey 的 script.outline_confirmed=false）。
    selfLoopAction: { label: '去第 1 阶段确认大纲', target: 'creative-stage' },
  },
  PACKAGE_NOT_CONFIRMED: { reason: '导演包尚未确认', action: { label: '去导演包', target: 'package-stage' } },
  NO_PACKAGE: { reason: '尚未生成导演包', action: { label: '去导演包', target: 'package-stage' } },
  SCRIPT_NOT_PREPARED: { reason: '还没有剧本', action: { label: '去创意与剧本', target: 'creative-stage' } },
  // 分镜必须基于一个已冻结的风格版本；风格在第②阶段与分集一起定稿。
  STORYBOARD_STYLE_NOT_FROZEN: {
    reason: '风格还没有冻结',
    why: '分镜必须引用一个已冻结的风格版本，否则镜头与成片的风格无法对齐。',
    action: { label: '去冻结风格', target: 'plan-stage' },
  },
  // 大纲是分集与分镜的共同依据；没确认大纲时，分集与分镜都不能开始。
  SCRIPT_OUTLINE_NOT_CONFIRMED: {
    reason: '剧本大纲还没有确认',
    why: '分集、分镜和导演包都以你确认的那一版大纲为依据，大纲未确认时它们无法成立。',
    action: { label: '去确认大纲', target: 'creative-stage' },
  },
  // 分集方案要先落到已应用的镜头上，才谈得上下游一致。文案由服务端按真实
  // 落镜/待确认状态给出（serverMessage），前端不拿静态句盖掉事实。
  EPISODE_PLAN_NOT_MATERIALIZED: {
    reason: '本集镜头待确认落到分集',
    why: '分集要按场次对应到已应用的镜头，否则这一集的镜头范围不成立。',
    action: { label: '去确认本集镜头', target: 'produce-stage' },
    serverMessage: true,
  },
  IMAGE_RUNTIME_NOT_READY: { reason: '本机出图环境还没就绪', action: { label: '去基础配置', target: 'setup-env' } },
  LLM_NOT_CONFIGURED: { reason: '还没配置文本模型', action: { label: '去配置文本模型', target: 'setup-models' } },
  // 装机与门禁常见拒绝：服务端消息已说清原因，这里补上「去哪里处理」。
  HOST_NOT_QUALIFIED: {
    reason: '这台电脑没有通过这套方案的硬件准入',
    why: '下载权重是存储行为，运行模型才需要算力。门槛对比在基础配置的能力卡上如实展示；可以先准备其它能力。',
    action: { label: '查看硬件门槛', target: 'setup-env' },
  },
  PROFILE_WEIGHTS_NOT_AVAILABLE: { reason: '这套方案的权重还没在本机就位', action: { label: '去基础配置准备', target: 'setup-models' } },
  PROFILE_WEIGHTS_NOT_VERIFIED: { reason: '权重在本机但还没逐字节核验', action: { label: '去核验已有文件', target: 'setup-models' } },
  PROFILE_WEIGHTS_NOT_CONSUMABLE: { reason: '权重已核验但当前渲染器读不到那个目录', action: { label: '去绑定到实例', target: 'setup-models' } },
  PROFILE_NOT_REGISTERED_FOR_PROJECT: { reason: '候选方案还没登记到当前作品', action: { label: '去登记方案', target: 'setup-models' } },
  PROFILE_GRAPH_IDENTITY_CHANGED: { reason: '方案图哈希与变体清单不一致', action: { label: '去基础配置核对', target: 'setup-models' } },
  MANAGED_INSTANCE_NOT_CONFIGURED: { reason: '本机还没有受管渲染器配置', action: { label: '去基础配置准备', target: 'setup-env' } },
  MODEL_VARIANT_NOT_ACCEPTED: { reason: '候选模型还没通过验收，不能用于正式生产', action: { label: '查看模型状态', target: 'setup-models' } },
  VARIANT_NOT_DOWNLOADABLE: { reason: '这个变体未进入自动下载路径', action: { label: '查看候选准备方式', target: 'setup-models' } },
  VARIANT_STARTUP_CONTRACT_NOT_DETERMINED: {
    reason: '所选候选 H3 方案缺少已测定的启动合同',
    why: '在 H3 方案中选择本机已验收的方案后重新整备。候选权重可以核验和保存，启动仍需该方案的真机证据。',
    action: { label: '选择 H3 方案', target: 'setup-models' },
  },
  REAL_RUNTIME_NOT_ACTIVATED: { reason: '真实制作配置还没验证激活', action: { label: '去基础配置完成', target: 'setup-env' } },
  SERVICE_VALIDATION_REQUIRED: { reason: '本机服务或工作流变化，需要重新验证', action: { label: '去重新验证', target: 'setup-env' } },
  ACTIVE_RUNTIME_CONFIGURATION_CHANGED: {
    reason: '本机制作环境的固定文件或运行配方发生了变化，需要重新核对配置',
    why: '在基础配置的「H3 视频生成」卡片点击「一键绑定渲染器并核对运行合同」即可重新生成配置并完成验证激活。',
    action: { label: '去基础配置核对', target: 'setup-env' },
  },
  RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK: {
    reason: '本机资源仍在使用或等待核对',
    why: '提交「核对运行合同」后，后台会等候资源可用再继续。可在「工作栈」查看等待项。',
    action: { label: '去基础配置核对', target: 'setup-env' },
    serverMessage: true,
  },
  ASSET_RIGHTS_NOT_APPROVED: { reason: '素材权利还没确认', action: { label: '去确认素材', target: 'assets-stage' } },
  CHARACTER_NAME_ALREADY_IN_USE: { reason: '这个名字已绑定另一张人物素材', action: { label: '去素材页核对', target: 'assets-stage' } },
  OUTPUT_DIRECTORY_BUSY: { reason: '有活动任务，输出目录暂不能迁移', action: { label: '查看活动任务', target: 'setup-env' } },
  SHOT_SUBMISSION_OUTCOME_UNKNOWN: { reason: '上一次外部提交结果还没核对', why: '可能已发出的请求不会自动重发；先核对原任务，再决定是否重拍。', action: { label: '去进度页核对', target: 'run-stage' } },
  INSTALL_APPROVAL_REQUIRED_OR_PLAN_HASH_CHANGED: {
    reason: '安装计划的批准回执与当前计划对不上',
    why: '再点一次「一键准备」会重新批准同一份计划并从停下的步骤继续；已完成的步骤与已下载的字节都会保留。',
    action: { label: '重新发起一键准备', target: 'setup-env' },
  },
  VARIANT_LORA_UNSUPPORTED: {
    reason: '当前模型量化变体不支持挂载风格 LoRA',
    why: 'V1 版本仅在 INT8 主线启用风格 LoRA 动态串联，W6A8 暂不允许叠加 LoRA。',
    action: { label: '回到制作策略改选 INT8 或移除 LoRA', target: 'package-stage' },
  },
  STYLE_LORA_LIMIT_EXCEEDED: {
    reason: '挂载的风格 LoRA 数量超过了上限（3 个）',
    why: '为了保障 Ref2VA 主体一致性和显存边界，单次渲染最多串联 3 个风格 LoRA。',
    action: { label: '精简风格 LoRA 列表', target: 'package-stage' },
  },
  STYLE_LORA_DUPLICATE: {
    reason: '同一个风格 LoRA 被重复挂载了多次',
    why: '重复串联同一个 LoRA 会导致权重叠加失真且无法冻结唯一策略哈希。',
    action: { label: '删除重复挂载项', target: 'package-stage' },
  },
  STYLE_LORA_STRENGTH_OUT_OF_RANGE: {
    reason: '风格 LoRA 权重超出了允许范围 [0.00, 1.50]',
    why: '过高或负值的 LoRA 强度会破坏 MiniMax-H3 基础动态先验。',
    action: { label: '将强度调回 0.00–1.50 范围内', target: 'package-stage' },
  },
  STYLE_LORA_INVALID_TRIGGER: {
    reason: '风格 LoRA 触发词格式不合法',
    why: '触发词仅支持 ASCII 字母、数字、空格与常用英文标点（最多 160 字符），禁止包含 H3 保留标签。',
    action: { label: '修正或留空触发词', target: 'package-stage' },
  },
  LORA_FILE_MISSING: {
    reason: '台账里的风格 LoRA 文件在磁盘上找不到了',
    why: '可能被移动、删除或未挂载进当前受管模型目录，生产无法按冻结快照继续。',
    action: { label: '刷新 LoRA 台账或重新导入文件', target: 'package-stage' },
  },
  LORA_HASH_MISMATCH: {
    reason: '风格 LoRA 磁盘文件的 SHA-256 与冻结台账不一致',
    why: '禁止静默用被篡改或替换的权重继续渲染，以免同一批次或续镜风格漂移。',
    action: { label: '核实原始权重并重新导入', target: 'package-stage' },
  },
  LORA_NOT_VISIBLE_IN_COMFYUI: {
    reason: '运行中的 ComfyUI 未能看到该风格 LoRA',
    why: 'ComfyUI 可能在新增 extra_model_paths 配置前启动，需要重启或重新绑定受管 ComfyUI 进程后再检测。',
    action: { label: '检查 ComfyUI 并点击「检测可见性」', target: 'package-stage' },
  },
  LORA_CORRUPT_HEADER: {
    reason: '这个 .safetensors 文件头校验失败',
    why: '文件可能下载不完整、损坏或不是标准的 safetensors 权重。',
    action: { label: '更换完整的 safetensors 文件重试', target: 'package-stage' },
  },
  LORA_INVALID_EXTENSION: {
    reason: '只支持 .safetensors 格式的风格 LoRA',
    why: '为了防止不安全反序列化和元数据缺失，非 .safetensors 权重无法入库。',
    action: { label: '选择 .safetensors 文件导入', target: 'package-stage' },
  },
  LORA_IN_USE: {
    reason: '这个风格 LoRA 正在被在途任务或最近生产配置引用',
    why: '防止删除仍在使用的权重导致执行图找不到模型；请等对应任务完成后再移除。',
    action: { label: '等待关联任务结束', target: 'package-stage' },
  },
};

/** 把服务端 blocker 映射为可行动条目；未知码保留原文，不编造动作。 */
export function describeBlocker(
  code: string | undefined,
  message: string,
): {
  reason: string;
  why?: string;
  action?: { label: string; target: ErrorActionTarget };
  selfLoopAction?: { label: string; target: ErrorActionTarget };
} {
  const known = code ? ERROR_ACTIONS[code] : undefined;
  if (known) {
    // 状态相关码（serverMessage）：reason 用服务端按真实状态算出的 message，
    // 静态 reason 只在服务端没给文案或仅回了裸错误码时兜底；动作仍由前端映射。
    const reason = known.serverMessage && message && message !== code ? message : known.reason;
    return { reason, why: known.why, action: known.action, selfLoopAction: known.selfLoopAction };
  }
  return { reason: message || code || '需要处理' };
}
