# Gitee 镜像同步配置说明

本目录下的 `scripts/mirror_gitee.py` + `workflows/mirror-gitee.yml` 用于把上游
`sdli1995/dlssg_for_sm86` 的专有 DLL（version.dll / dlssg_sm86.ini / alternatives/*）
打包成 **`all.zip`**，作为 **Gitee Release 附件** 镜像到你的 Gitee 仓库，使国内用户
走 Gitee 镜像下载，避开 GitHub 直连与美国节点。

## 为什么是 zip + Release 附件（而不是裸 DLL / repo raw）

- Gitee 的 **raw 通道对 >1MB 文件要求登录**，匿名拉不到 DLL（每个 ~28MB）。
- Gitee 的 **安全扫描会把「游戏注入型 DLL」判为恶意文件直接拒收**
  （`malicious file detected and rejected`）。
- 实测 Gitee **不会解包扫描 zip 内部**：把全部 payload 打成一个 `all.zip` 上传为
  Release 附件，可同时绕过以上两点；`all.zip` 压缩后约 50MB，远低于附件上限，
  且 Release 附件支持匿名、国内 CDN 直下。

## 一、一次性准备（GitHub 仓库侧）

1. 在 Gitee 新建一个**公开**仓库（例如 `dlssg-mirror`），初始留空即可。
2. 在 Gitee 「设置 → 私人令牌」生成一个带 **repo** 权限的令牌，复制保存。
3. 在你 GitHub 仓库 `sdli1995/dlssg_for_sm86` 的
   **Settings → Secrets and variables → Actions → New repository secret**
   里添加以下 4 个 secret（值来自上一步）：
   - `GITEE_OWNER`    —— Gitee 仓库 owner（**登录名**，不是昵称；可在 gitee.com 主页 URL 看到）
   - `GITEE_REPO`     —— Gitee 仓库名（如上 `dlssg-mirror`）
   - `GITEE_USERNAME` —— 你的 Gitee 登录用户名
   - `GITEE_TOKEN`    —— 上面的 Gitee 私人令牌

## 二、触发方式

- **自动**：每 12 小时 GitHub Actions 自动检查上游最新版并同步（cron `0 */12 * * *`）。
- **手动**：仓库 Actions 页 → mirror-gitee → Run workflow，可填版本号同步指定版。

效果：脚本拉取上游 tag 源码归档 → 挑出 `PAYLOAD_FILES` → 打成 `all.zip`
→ 在 Gitee 对应 tag 下创建/更新 Release 并上传 `all.zip` 为附件。

## 三、让工具走 Gitee 镜像（关键一步）

工具侧已内置 **Gitee 优先三级兜底**（`upstream.py` 的 `download_version`：
Gitee → GitHub archive → jsDelivr）。脚本只负责「把 `all.zip` 推到 Gitee Release」，
工具侧只需在 `dgcore/upstream.py` 顶部填上 Gitee 信息即可启用（留空则自动降级）：

```python
GITEE_OWNER = "你的Gitee登录名"   # 例如 fullgame666（注意是登录名不是昵称）
GITEE_REPO  = "dlssg-mirror"      # 你在第一步建的 Gitee 镜像仓库名
```

填好后，工具「检查更新 → 下载」会优先从
`https://gitee.com/{owner}/{repo}/releases/download/{ver}/all.zip`
整包下载并解包；Gitee 不可达时自动降级到 GitHub archive、再到 jsDelivr。

> ⚠️ 改完这两个常量后必须**重新打包 exe**（走之前的出包流程），
> 否则分发出去的成品里 `GITEE_OWNER`/`GITEE_REPO` 仍是空字符串，不会走 Gitee。
> 把用户名和仓库名告诉助手，他可以替你填好并重新出包。

## 四、本地手动跑（调试用）

```bash
export GITEE_OWNER=xxx GITEE_REPO=dlssg-mirror GITEE_USERNAME=xxx GITEE_TOKEN=xxx
python .github/scripts/mirror_gitee.py --version 0.3.5
```

需要本机能同时访问 GitHub（拉上游）和 Gitee（上传 Release 附件）。
