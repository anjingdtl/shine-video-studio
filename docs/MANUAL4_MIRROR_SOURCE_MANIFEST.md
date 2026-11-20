# Shine 四阶段镜像来源清单（MANUAL4_MIRROR_SOURCE_MANIFEST）

> 说明：本清单登记 `backend/shine/manual4/` 与 `frontend/src/manual4/` 从七阶既有实现镜像复制/移植的每个单元、来源 commit、源文件 SHA-1（`git hash-object`）、复制日期、差异理由、依赖清单与四阶独立测试。  
> **规则**：四阶不通过运行时 `import` 七阶业务模块达到“复用”；镜像代码不随七阶同步升级，任何调整需在四阶侧独立评审和回归。

| 镜像单元 | 源文件（来源 commit `9be718b`） | 源文件 Git Hash | 四阶镜像文件 | 移植日期 | 差异理由与依赖说明 | 对应四阶测试 |
|---|---|---|---|---|---|---|
| 独立 SQLite 引擎与 WAL/外键连接池 | `backend/shine/db.py` | `ee7176be424952611677248732c0ca3ddb579202` | `backend/shine/manual4/storage/db.py` | 2026-11-20 | 使用独立 `M4Base` 与 `manual4.sqlite` URL，彻底隔离七阶 `Base`/`shine.db` | `backend/tests/manual4/test_p1_platform_routing_and_isolation.py` |
| 安全文件名、路径穿越防护与媒体头校验 | `backend/shine/security.py` | `cbb6797acff596b8d0b0b7a444c7135e5a6298ba` | `backend/shine/manual4/storage/files.py` | 2026-11-20 | 文件落地严格限定于 `<data_dir>/manual4/projects/<id>/`，不读写七阶目录 | `backend/tests/manual4/test_isolation_static.py` |
