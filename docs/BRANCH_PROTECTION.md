# 主分支推送限制（公开仓库，仅所有者可推送）

日期：2026-11-08
仓库：`anjingdtl/shine-video-studio`（`visibility: public`）
目标：**公开可见，但 `main` 只有仓库所有者能更新。**

## 1. 生效配置

仓库 Ruleset `main-owner-only-push`（id `26763523`，`enforcement: active`）：

```json
{
  "name": "main-owner-only-push",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main", "~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [ { "type": "update" } ],
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
  ]
}
```

- `update` 规则锁住 `main` 的 ref 更新：直接 push、force push、删除 `main` 一并被挡。
- bypass 白名单只有 `RepositoryRole` admin（`actor_id=5`）。该仓库当前只有 `anjingdtl`
  一个 admin（`GET /repos/anjingdtl/shine-video-studio/collaborators` 实测：唯一协作者，
  `role_name: admin`），所以「admin 角色」在当前就等于「所有者本人」。
- 条件同时写死 `refs/heads/main` 与 `~DEFAULT_BRANCH`，默认分支若改名，保护自动跟随。

## 2. 为什么用 Ruleset 而不是 classic 分支保护

classic 分支保护的 `restrictions.push.users` 在**个人账户仓库**上被 GitHub 明确拒绝：

```
HTTP 422 Validation Failed
Only organization repositories can have users and team restrictions
```

也就是说「按人限制推送」在个人仓库没有等价 API。Ruleset 的 `update` 规则 +
`RepositoryRole` bypass 白名单是个人仓库可用的等价机制：非 admin 的任何角色都拿不到
bypass，规则对全体生效；owner 是唯一 admin，因此恰好只有 owner 能更新 `main`。

若将来要**按具体某个协作者**授权（而不是按角色），必须先把仓库迁到 Organization，
届时可改用 team/user 级 `restrictions`。

## 3. 权限矩阵

| 主体 | 推 `main` | 合并 PR | 推其他分支 | 开 PR |
|---|---|---|---|---|
| 所有者 `anjingdtl` | ✅（bypass always） | ✅ | ✅ | ✅ |
| 其他 admin/maintain/write 协作者 | ❌ | ❌ | ✅ | ✅ |
| 外部贡献者（fork） | ❌ | ❌ | 仅自己的 fork | ✅ |

外部贡献者的正常路径是 fork → PR，由所有者合并。

## 4. 验收证据

### 4.1 规则确实会拦截（真实拒绝，非纸面配置）

临时把 `bypass_actors` 清空（规则对包括 owner 在内的所有人生效），再用一个空提交
真实推送到 `main`：

```
remote: error: GH013: Repository rule violations found for refs/heads/main.
remote: - Cannot update this protected ref.
 ! [remote rejected] probe/main-owner-only -> main (push declined due to repository rule violations)
PUSH_EXIT=1
```

拒绝后远端 `main` 的 sha **未发生变化**（`b0d0373c7646791ac5c744dd60304bee9d2d6595`），
探针分支随即删除 —— 全过程零污染。

### 4.2 白名单恢复后所有者照常工作

- `GET /repos/anjingdtl/shine-video-studio/rulesets/26763523` → `current_user_can_bypass: always`
- 所有者真实推送非 `main` 分支成功（`[new branch] probe/owner-write-check`），随后删除
- 恢复 bypass 后 `rules/branches/main` 显示 `update` 规则生效于 `refs/heads/main`

### 4.3 边界声明

本机只有 owner 一个账号，**没有第二个账号**，因此「非 owner 被拒」这一条没有以
另一个身份做端到端实测；结论依据是 GitHub 规则引擎语义（`update` 规则对所有
非 bypass 主体生效）+ 协作者列表实测（admin 仅 owner 一人）。
若日后引入第二个账号，应补一次跨账号复验。

## 5. 运维操作

查看当前状态：

```powershell
gh api repos/anjingdtl/shine-video-studio/rulesets
gh api repos/anjingdtl/shine-video-studio/rules/branches/main
gh api repos/anjingdtl/shine-video-studio/collaborators
```

临时放开（仅用于本人紧急直推，收工必须改回）：

```powershell
$body = @'
{ "name": "main-owner-only-push", "target": "branch", "enforcement": "active",
  "bypass_actors": [], "conditions": { "ref_name": { "include": ["refs/heads/main","~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [ { "type": "update" } ] }
'@
$p = Join-Path $env:TEMP 'ruleset_nobypass.json'
[System.IO.File]::WriteAllText($p, $body, (New-Object System.Text.UTF8Encoding($false)))
gh api -X PUT repos/anjingdtl/shine-video-studio/rulesets/26763523 --input $p
```

恢复（本文件的生效状态，`bypass_actors` 里有 `actor_id: 5`）：

```powershell
$body = @'
{ "name": "main-owner-only-push", "target": "branch", "enforcement": "active",
  "bypass_actors": [ { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" } ],
  "conditions": { "ref_name": { "include": ["refs/heads/main","~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [ { "type": "update" } ] }
'@
$p = Join-Path $env:TEMP 'ruleset_restore.json'
[System.IO.File]::WriteAllText($p, $body, (New-Object System.Text.UTF8Encoding($false)))
gh api -X PUT repos/anjingdtl/shine-video-studio/rulesets/26763523 --input $p
```

给某个协作者开口子：把该协作者升级为 admin 会一并放开 `main`，属于扩大暴露面，
请优先让他提 PR、由所有者合并。

## 6. 纪律

- 被拒的推送是**正确结果**。不要为了让推送通过而删除 ruleset、清空 bypass 白名单或
  改用 `--force`。
- 改 ruleset 之前先确认当前 `bypass_actors`，改完立刻读回确认，避免把 owner 自己
  挡在门外（owner 仍可自行改回，因为 ruleset 管理不受自身规则约束）。
