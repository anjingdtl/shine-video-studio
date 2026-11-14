# 启动器的 GitHub Actions 工作流

这里存放 Shine Launcher 自己的持续集成工作流**源文件**。GitHub 只运行位于
`.github/workflows/` 下的工作流，因此实际生效的副本由安装脚本放入：

```powershell
pwsh -File launcher/packaging/ci/install_workflows.ps1
```

校验已安装的副本是否与源文件逐字节一致：

```powershell
pwsh -File launcher/packaging/ci/install_workflows.ps1 -Verify
```

## 文件

| 文件 | 作用 |
| --- | --- |
| `launcher-windows.yml` | Windows x64 构建、单测、Clippy、Release EXE、smoke-check 与 self-test 自检、ZIP 打包与校验，并把验证过的 EXE 发布到 Clone 根目录 |
| `launcher-release.yml` | 正式发布流程：从 tag 生成部署清单 → 用 GitHub Secret 中的私钥签名 → 用仓库内的公钥信任锚复核 → 产出签名清单。缺密钥时**失败关闭**，不发布未签名产物 |

## 为什么源文件不直接放在 .github/workflows

发布工作流需要向 `.github/workflows/` 写入文件。自动化身份（GitHub App）
默认被限制在该目录之外，无法写入工作流目录本身；把源文件放在这里，由有仓库
写权限的本地流程安装，既保留"启动器拥有自己的 CI 定义"这一结构，又不阻塞
本次合入。

## 密钥

发布流程使用两个 GitHub Secret：

- `SHINE_STUDIO_RELEASE_SIGNING_KEY` —— 工作台部署清单签名
- `SHINE_LAUNCHER_RELEASE_SIGNING_KEY` —— 启动器部署清单签名

两者都是 base64 编码的 32 字节 Ed25519 种子。**私钥不得提交仓库。** 仓库内
只保存对应的公钥（`config/launcher/trusted-publishers.json`），启动器校验时
只认公钥。缺少密钥时发布流程会失败关闭。
