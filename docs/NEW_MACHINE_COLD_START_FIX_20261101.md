# 安装准备与受管首启交接修复

目标仍为完整新机 PASS。本次修复安装向导的实例身份和启动顺序，不将准备步骤完成视为服务或推理通过。

## 问题与修复

1. 原向导在模型绑定后生成普通 `START`，直接运行 `main.py` 并占据生产端口。现在准备流程为 `CREATE → INSTALL_NODE → BIND_MODELS → 保持停止 → 生成受管配置`。首次生产启动继续由既有 Worker、GPU 租约和 fenced renderer 负责，没有另建启动队列。旧版已启动的实例先生成需人工批准的 `STOP` 计划，只停止记录中具有出生身份的自有进程。
2. 原向导只按名称查找实例，同名 Fake 或其他端口实例可能误判完成。现在使用生命周期的同一 mode/port UUID，并核验名称、根目录、端口、Mock 标记和状态。同一个目录已经属于其他端口时拒绝复用，不覆盖原实例。
3. 节点及模型绑定不再只判断字典非空。缺少固定节点时仅计划缺失节点及其依赖环境；缺少模型家族时继续绑定步骤。既有节点版本或模型绑定与当前清单冲突时拒绝自动改写，要求先对账。
4. 配置端点明确指定实例名称和端口，默认 `shine-h3:8188`，不再取数据库第一条 managed 记录。Fake、运行中或非 STOPPED 实例不能生成正式配置。
5. 配置写入前取得 SQLite writer 锁，与既有资源准入和实例 intent 串行。存在 ACTIVE、RECONCILE_REQUIRED 租约或未对账安装尝试时，在修改适配器、配方和 `.env` 前拒绝操作；租约过期也不视为资源已经释放。

## 证据

- 原实现：新增 8 个反例全部失败，日志 `.local-acceptance/new-machine-goal-20261101/cold-start-before.log`。包括普通 START、同名 Fake/其他端口误认、旧进程跳过 STOP，以及运行中/Fake/资源未对账时仍可写配置。
- 修复后：冷启动合同、新机设置与独立验收合同共 34 项通过，日志 `cold-start-pinned-stages.log`。包含额外的部分节点/模型准备、错误版本/绑定/目录与 NEEDS_REVIEW 反例。
- 全目录 Ruff 与 TypeScript/Vite 构建通过。
- 完整后端：`uv run --project backend --locked pytest backend/tests -q`，1245 passed / 1 skipped / 2 warnings，exit=0。日志 `full-suite-cold-start-final.log`；数字按静默日志状态符统计。唯一 skip 为门控的真实 ComfyUI 探测，两项既有 Starlette/AnyIO 与 SQLAlchemy 警告保留。上轮 1231 项日志独立保留。

这些测试使用隔离数据库和标注的合成文件，未启动真实 H3，也没有把合成实例转为真实安装凭证。

## 仍待建设

配置生成只写文件，不能证明常驻 API/Worker 已切换到新运行配置。仍需完成明确授权的持久激活、服务首启及真实身份探测、审片路由自动配置、L4 证据绑定，再进行完整模型下载和真实生产验收。生成配方不会自动批准资产、冻结风格或关闭质量门。完整新机目标保持 PARTIAL。
