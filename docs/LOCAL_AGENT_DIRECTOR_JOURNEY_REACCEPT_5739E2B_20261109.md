# 导演模式复验与剩余收尾提示词

2026-11-09，复验 `main@5739e2b`，与 `origin/main` 一致。结论为 **`PARTIAL_SOFTWARE_ACCEPTANCE`**。此前的正向回归已通过，仍有 7 组入口或状态交接缺陷需要收尾。真实 Windows 安装、真实下载、真实 LLM/H3 和多集交付待当前版本验收。

## 已复验通过

- 已重新构建当前提交的 Windows 包。输出目录走实际 8.3 路径 `C:\Users\ADMINI~1\...`，解包逐文件验签 740 个文件通过（J01）。已有 `releases/ShineVideoStudio-20261109-5739e2b-win64.zip` SHA-256 为 `adf75fdab5263f6603a82cf56c3ae186145aa9be05fa129eed1002363debec97`，匹配同名哈希文件。
- 两批定向后端测试分别 115 和 43 项通过。包含安装回执、文本模型改派后继续原大纲任务、场次模式剧本依据、空可选画面槽、已批准绑定的事务行为、无宽高映射拒绝、镜头修订后保留 UNKNOWN、跨包 Run 归属、素材中心及离线 E2E。
- 前端 build、32 个测试文件共 201 项、Ruff 均通过。远端 `web-e2e` 完整通过，21 项流程，`MOCK_VERIFIED`，`api_errors=[]`（J12）。截稿时远端两项完整后端任务仍运行。本轮本机复验运行的是定向套件。 [远端 CI](https://github.com/anjingdtl/shine-video-studio/actions/runs/42595580119)
- 清楚保留上一轮实现已验证的能力。当前软件证据来自隔离 SQLite、网络桩和合成素材，无真实 Provider/H3 提交。以下反例以观察到缺陷为成功条件。

## 交给本地 Agent 的续修任务

从当前 `main` 核对 HEAD、工作树、CI 和 `AGENTS.md`。承接 [J01–J12 审查](ADVERSARIAL_REVIEW_DIRECTOR_JOURNEY_185C6A6_20261109.md)、[原收尾提示词](LOCAL_AGENT_DIRECTOR_JOURNEY_CLOSEOUT_185C6A6_20261109.md) 和 [上一轮实现报告](DIRECTOR_JOURNEY_CLOSEOUT_185C6A6_20261109.md)。以下是二次复验的剩余项。先逐项复现再改，已通过的合同沿用。用小改动收束状态与入口，避免给用户新增审批层。

### R01 · P0 · 按镜头分组仍能用 B 的剧本包装 A 的镜头（J06）

- **复现**：导入并确认 A 大纲，用「按已有镜头分组」确认 A 的计划和包。再导入 B 并切换到已确认的 B 大纲。新包 201、确认 200、`startable=true`。新包的 outline 是 B，分集计划 ID 仍为 A。修复后的按场次模式回归通过，兼容路径仍无来源约束。
- **修复**：用同一个当前制作依据解析普通按镜头、按场次物化和四阶段按卷。兼容计划在有导入剧本时记录实际依据，按现有镜头来源与 apply 回执核对，换剧本时从已变化的环节续办。新生成按镜头计划也不能把项目里旧镜头自动归到新剧本。无剧本的手工项目保留直接按镜头，旧包、旧片继续按原 ID 回看。旧行有可信证据则自动衔接，证据缺失只指向已有确认或重建动作。
- **验收**：两种普通分集入口分别测试 A、B 未选、切换 B、B 物化并开拍。四阶段卷保留自己的边界与来源。已有 A 镜头不能通过重新分组被无声认作 B。 [plan_basis_blockers](../backend/shine/director/package.py#L236)

### R02 · P0 · W6A8 生产准入把「文件存在」当作「SHA 已核验」（J04）

- **复现**：使用实际候选 profile，模拟节点验证与受管配置，用几字节替代固定权重。在预期 SHA 命名的缓存文件中放入同长错误字节。就绪 API 报 `prepared=true, verified=false`，但生产准入 API 返回 `selectable=true, blockers=[]`。
- **修复**：生产准入从现有权重证据取 `verified` 和能供当前 renderer 消费的部署关系。`prepared` 只展示已发现字节。已有有效 pin 与可用目录直接复用。区分已核验缓存、实际绑定目录、当前实例，切换受管实例后重查消费关系，避免为正常提交反复全量哈希。缺文件/错哈希直接引向已有的本机核对或导入。候选保留原身份和 `auto_download_allowed=false`。
- **验收**：相同长度错 SHA、旧 revision、只在另一目录或未绑定缓存、有效原位核验，对照就绪与生产准入，并经实际建包、Series 和 Job 入口验证一致。 [profile_gate.py](../backend/shine/comfyops/profile_gate.py#L118)、[variant_readiness.py](../backend/shine/comfyops/variant_readiness.py#L107)

### R03 · P0 · 导演包与素材采用仍会接受实际不可执行的条件（J07、J09）

- **复现 A**：导演包传 `output={fps:30}`，201、可确认、`startable=true`。同一输出直接走 Series 返回 422，因 H3 `OutputSpec` 要求 24 fps。现补丁只覆盖宽高映射。
- **复现 B**：已批准锁定的全局角色版本分配到项目，随后撤销权利。逐镜采用预览 `blocked=[]`、应用 201 并写入新 revision。原本使用同一素材的确认包仍 `startable=true`，但提交真实 Job 时才 409。
- **修复**：共用既有 `OutputSpec`、H3 编译器、profile gate 和引用权利事实。把格式、模式、宽高、时长与对白/切点，以及已声明引用的有效权利与内容身份用于相应的预览、采用和可执行性。包草案可以展示缺项，确认或启动只按照实际执行合同判定。用户已明确选择的方案保持原样，动态前镜引用继续等待实际产物。素材已撤权时拒绝新采用，保留旧回执和历史片，给出换用已有授权版本的直接入口。
- **验收**：正确输出通过，错误 fps、非法时长、映射缺失在产生 Run 之前已有可操作的原因。素材撤权后采用不能写 revision，冻结后撤权不能继续显示可开拍。核对该镜与其他独立镜头的边界。 [package.py](../backend/shine/director/package.py#L710)、[api_asset_center.py](../backend/shine/api_asset_center.py#L2015)

### R04 · P0 · 一键请求写入所有绑定，但只准入第一镜（J08）

- **复现**：两镜 Series 请求内，第一镜在合法主体外增一张已批准画面，第二镜点名人物但无主体，明确发送空绑定。服务返回 201，第一镜新增绑定已入库，第二镜留空。当前测试只覆盖「第一镜缺主体」或「第二镜提交未审批素材」，后者在写绑定时本来会失败。
- **修复**：按用户选择的镜头及确切 revision/绑定快照检查全批。复用每镜静态执行判定，现有动态连续性仍由 Worker 等待。服务端确认前端快照未被其他标签页改写，再在同一事务应用明确授权的绑定和入队。对无效的后镜给具体原因，保留可运行范围供用户直接继续，避免整项目停摆。若只使用现有绑定，避免借提交顺手重写它们。
- **验收**：把关键主体缺失、引用槽不足和绑定版本漂移放在第二镜；拒绝的范围无半写入、无意外 Job。两镜均合法时正常入队。仅空可选画面槽仍能开拍。 [main.py](../backend/shine/main.py#L1102)、[production_series.py](../backend/shine/comfyops/production_series.py#L220)

### R05 · P0 · Series 与单镜 Job 对拍摄归属的判定不对称（J10）

- **复现**：Series 已经为第一镜生成一个排队 Job，再对该镜发一次单镜 Job 请求，带一个新 `request_key`。两次入口都返回 201，生成不同的 Job ID。本轮只验证入队，未实际发出重复外部调用。
- **修复**：单镜、Series、Run 派发在同一权威入口检查活动归属，识别已预定但尚未建 Job 的镜头。属于同一请求时重接已有回执，独立发起撞上活动拍摄时给原任务入口。内部派发允许按自己持有的归属建 Job。合法新拍在旧尝试终态且外部结果已核对后使用新身份。普通请求恢复要覆盖刷新或断连后原请求的身份，兼容已有客户端。沿用修订后保留 `UNKNOWN` 的修复，继续复测依赖失效、冻结风格和外部提交重启窗口。
- **验收**：Series→Job、Job→Series、Run→直调，以及并发请求、丢响应再进入，都没有第二次活动拍摄。不同镜头和确已核对的显式新拍可继续。 [services.py](../backend/shine/services.py#L945)、[production_series.py](../backend/shine/comfyops/production_series.py#L220)

### R06 · P1 · 首次启用真实制作向导仍跳过可重接的安装回执（J02）

- **复现**：首次向导的计划列表返回 `INSTALLING`，目标查询已能返回已知 `DOWNLOADING` 尝试。实际 React 组件直接提示「存在执行中或需要对账」并引去专业设置，零次目标查询、零次重投。后端已有按目标查找能力，基础配置也已能读完整回执。
- **修复**：首次向导与基础配置自动准备共用目标回执的读取和续接。已知运行中的尝试在原界面显示进度，失败/暂停显示对应操作，完成后沿原步骤继续准备实例。结果真正不明时显示现有对账动作。区分可重接、需要对账与已结束，避免仅凭计划状态弹到专业模式。切作品后不自动追加副作用。
- **验收**：下载中/校验中关页或清空浏览器存储，再开向导仍读同一 attempt，零次重复执行。测试完成但初次回执丢失、暂停及真正未知的分支。另确认 LLM 缺配置、状态接口失联都不挡 H3 安装路径。 [RealProductionWizard.tsx](../frontend/src/components/RealProductionWizard.tsx#L187)、[setupExecution.ts](../frontend/src/setupExecution.ts#L181)

### R07 · P1 · 指定只跑一集，旅程仍把整包镜头算成待制作（J11）

- **复现**：同一确认包两集，各一镜。Run 指定只拍第一集，Run 的 `scope.shot_count=1`。按 `run_id` 查 Journey，包 ID 正确，但 `scope.shot_count=2`、生产阶段两条 `SHOT_NOT_PRODUCED`。跨包 A/B 混用的原反例已通过，部分集 Run 仍有两套统计范围。
- **修复**：当前 Run 的选集和镜头来自其已冻结的 scope/episode 行。整包概况在独立字段表达。进度分母、可操作 Run、后期素材与下载跟着同一选定范围。快切包、项目和 Run 时丢弃旧异步结果。保留从项目入口进入历史包和完整作品的导航。
- **验收**：两集只跑第一集，真实范围只出现该集的一镜和该集回执。显式第二集或整包时各自正确。历史链接播放和下载按原 Run/集/交付 ID，切换时不串片。 [journey.py](../backend/shine/director/journey.py#L276)

## 验收和交付

1. 优先修 R01–R05 的软件合同，再关闭 R06、R07 的入口和显示。对每条反例加一条正向回归。若临时目录保留了本轮后端脚本，可从仓库 `backend` 目录运行下面的命令。前端 R06 的反例基于 `realWizard.test.tsx`，令计划返回 `INSTALLING`，目标回执返回 `DOWNLOADING`，统计回执查询及 execute 调用。

   ```powershell
   $env:PYTHON_DOTENV_DISABLED = '1'
   $env:SHINE_DATA_DIR = Join-Path $env:TEMP 'shine-reaccept-5739e2b'
   $env:PYTHONPATH = (Get-Location).Path
   $probe = Join-Path $env:TEMP 'test_shine_reaccept_5739e2b.py'
   .venv/Scripts/python.exe -m pytest -s -q --rootdir $env:TEMP --confcutdir $env:TEMP $probe
   ```

2. 按 `AGENTS.md` 和原收尾任务的测试清单跑定向与完整后端、空库与旧库副本迁移、Ruff、前端构建/单测、完整浏览器 smoke。当前已有发布包仅是复验样本，代码修完后从新 committed tree 重新构建验签。输出包与回归均记录确切 commit。涉及 `client_review_identity` 的文件按现行要求处理校准版本。
3. 真机验收开工前核对进程、PID/端口、GPU 租约、profile、DB 和队列归属。使用隔离新机/VM 完整安装并验证重复启动。验证真实依赖/权重下载、断点恢复及已有环境原位复用。使用已授权 Provider 和合成剧本从导入、大纲到分镜；用合法样本走素材中心分配和逐镜采用。从导演模式选 Ref2VA，运行有对白与无对白的多镜样例，至少两集分别产出、解码和下载，记录 task/Attempt/Series/Run、graph hash、媒体 SHA 和 GPU lease 释放。W6A8、Qwen、固定音色和建议审片分别记录能力证据，缺少可选能力时 H3 主路径仍继续。
4. 交付修复 commit/PR、当前版本测试包与 SHA-256、R01–R07 对照报告。分开标明软件通过、真实安装、真实 LLM/H3、多集交付与人工验收。没有适用机器、权重、Provider 或人工证据时，明确剩余动作和原因。保留用户现有安装、历史成片、授权和未知提交。生产功能合入及推送依仓库规则。
