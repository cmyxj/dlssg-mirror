# Gitee 镜像同步配置说明

本目录下的 `scripts/mirror_gitee.py` + `workflows/mirror-gitee.yml` 用于把上游
`sdli1995/dlssg_for_sm86` 的专有 DLL（version.dll / dlssg_sm86.ini / alternatives/*）
同步到你的 Gitee 仓库，使国内用户走 Gitee 镜像下载，避开 GitHub 直连与美国节点。

## 一、一次性准备（GitHub 仓库侧）

1. 在 Gitee 新建一个**公开**仓库（例如 `dlssg-mirror`），初始留空即可。
2. 在 Gitee 「设置 → 私人令牌」生成一个带 **repo** 权限的令牌，复制保存。
3. 在你 GitHub 仓库 `sdli1995/dlssg_for_sm86` 的
   **Settings → Secrets and variables → Actions → New repository secret**
   里添加以下 4 个 secret（值来自上一步）：
   - `GITEE_OWNER`    —— Gitee 仓库 owner（你的用户名或组织名）
   - `GITEE_REPO`     —— Gitee 仓库名（如上 `dlssg-mirror`）
   - `GITEE_USERNAME` —— 你的 Gitee 登录用户名
   - `GITEE_TOKEN`    —— 上面的 Gitee 私人令牌

## 二、触发方式

- **自动**：每天 UTC 0 点 GitHub Actions 自动检查上游最新版并同步。
- **手动**：仓库 Actions 页 → mirror-gitee → Run workflow，可填版本号同步指定版。

## 三、让工具走 Gitee 镜像（关键一步）

工具侧已内置 **Gitee 优先三级兜底**（`upstream.py` 的 `download_version`：
Gitee → GitHub archive → jsDelivr）。脚本只负责「把 DLL 推到 Gitee」，
你只需在 `dgcore/upstream.py` 顶部填上 Gitee 信息即可启用（留空则自动降级回原逻辑）：

```python
GITEE_OWNER = "你的Gitee用户名"   # 例如 pandaligx
GITEE_REPO  = "dlssg-mirror"      # 你在第一步建的 Gitee 镜像仓库名
```

填好后，工具「检查更新 → 下载」会优先走 Gitee raw
（`https://gitee.com/{owner}/{repo}/raw/{ver}/version.dll` 等），
Gitee 不可达时自动降级到 GitHub archive、再到 jsDelivr。
Gitee 对单文件无 20MB 上限，**version.dll（约 28MB）也能正常取**。

> ⚠️ 改完这两个常量后必须**重新打包 exe**（走之前的出包流程），
> 否则分发出去的成品里 `GITEE_OWNER`/`GITEE_REPO` 仍是空字符串，不会走 Gitee。
> 把用户名和仓库名告诉助手，他可以替你填好并重新出包。

## 四、本地手动跑（调试用）

```bash
export GITEE_OWNER=xxx GITEE_REPO=dlssg-mirror GITEE_USERNAME=xxx GITEE_TOKEN=xxx
python .github/scripts/mirror_gitee.py --version 0.3.5
```

需要本机有 git，且能同时访问 GitHub（拉上游）和 Gitee（推送）。
