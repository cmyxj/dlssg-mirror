#!/usr/bin/env python3
"""把上游 dlssg_for_sm86 的专有 DLL 同步到 Gitee 镜像仓库。

设计对齐 RTX-FG-Manager 的 mirror_gitee.py，但适配本项目 upstream.py 的
下载结构：上游把 DLL 直接提交在仓库根目录（version.dll / dlssg_sm86.ini /
alternatives/*），本脚本把它们推到 Gitee 仓库的【同名 tag】，保留相同目录结构。

下游工具侧（dgcore/upstream.py）只需把：
    MIRROR_FILE_URL = "https://cdn.jsdelivr.net/gh/sdli1995/dlssg_for_sm86@{ver}/{path}"
改成：
    MIRROR_FILE_URL = "https://gitee.com/{owner}/{repo}/raw/{ver}/{path}"
即可让「GitHub 归档不可达 → 兜底走 Gitee 镜像」真正生效（jsDelivr 因单文件
>20MB 被 403，Gitee 无此限制）。

前置条件：
  - 运行环境有 git
  - 已存在一个 Gitee 仓库（公开即可），例如 dlssg-mirror
  - 环境变量：
        GITEE_OWNER       Gitee 仓库 owner（用户名或组织名）
        GITEE_REPO        Gitee 仓库名
        GITEE_USERNAME    你的 Gitee 登录用户名（HTTPS 鉴权用）
        GITEE_TOKEN       Gitee 私人令牌（repo 权限）
    可选：
        GITHUB_REPO       上游仓库，默认 sdli1995/dlssg_for_sm86
        MIRROR_FORCE      设为 1 时即使 tag 已存在也强制重新推送

用法：
    python mirror_gitee.py                 # 同步上游最新版
    python mirror_gitee.py --version 0.3.5 # 同步指定版本
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

GITHUB_REPO_DEFAULT = "sdli1995/dlssg_for_sm86"

# 与 dgcore/upstream.py 的 PAYLOAD_FILES 保持一致（相对仓库根）
PAYLOAD_FILES = (
    "version.dll",
    "dlssg_sm86.ini",
    "alternatives/d3d12.dll",
    "alternatives/dbghelp.dll",
    "alternatives/dinput8.dll",
    "alternatives/dxgi.dll",
    "alternatives/winmm.dll",
)

UA = {"User-Agent": "dlssg-mirror/1.0 (+https://github.com)"}
TIMEOUT = 60
MAX_RETRY = 3


def run(cmd, cwd=None, env=None, check=True):
    print("+ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(r.stdout)
        print(r.stderr, file=sys.stderr)
        raise SystemExit(f"command failed ({r.returncode}): {' '.join(cmd)}")
    return r


def http_get(url: str) -> bytes:
    last = None
    for i in range(1, MAX_RETRY + 1):
        try:
            req = urlrequest.Request(url, headers=UA)
            with urlrequest.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except (HTTPError, URLError, OSError) as e:
            last = e
            print(f"  [retry {i}/{MAX_RETRY}] {url} -> {e}")
    raise last or RuntimeError(f"download failed: {url}")


def latest_tag(repo: str) -> str | None:
    """用 releases.atom 取最新版本号。

    注意：GitHub releases.atom 第一个 <title> 是 feed 级标题
    （如 "Release notes from xxx"），并非版本号。所以优先从
    entry 的 /releases/tag/<ver> 提取，避开 title 命名干扰。
    """
    url = f"https://github.com/{repo}/releases.atom"
    text = http_get(url).decode("utf-8", "ignore")
    tags = re.findall(r"/releases/tag/([^<\">\s]+)", text)
    if tags:
        return tags[0]
    # 兜底：退到 entry 内部的 <title>
    for entry in re.findall(r"<entry>(.*?)</entry>", text, re.S):
        m = re.search(r"<title>(.*?)</title>", entry, re.S)
        if m:
            return m.group(1).strip()
    return None


def download_upstream_archive(repo: str, ver: str, dest: Path) -> None:
    """下载上游 tag 源码归档，挑出 PAYLOAD_FILES 落到 dest（保持相对路径）。"""
    url = f"https://github.com/{repo}/archive/refs/tags/{ver}.zip"
    print(f"下载上游归档 {url}")
    data = http_get(url)
    zip_path = dest / "_up.zip"
    zip_path.write_bytes(data)
    prefix = f"{repo.split('/')[-1]}-{ver}/"
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        for pf in PAYLOAD_FILES:
            cand = [n for n in names if n == prefix + pf or n.endswith("/" + pf)]
            if not cand:
                print(f"  WARN: 上游归档未找到 {pf}，跳过")
                continue
            target = dest / pf
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(cand[0]) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            print(f"  fetched {pf} ({target.stat().st_size:,} bytes)")
    zip_path.unlink(missing_ok=True)

    if not (dest / "version.dll").is_file():
        raise RuntimeError(
            "未从上游归档解出 version.dll，请检查 PAYLOAD_FILES 与上游目录结构是否变化"
        )


def sync_to_gitee(version: str, workdir: Path) -> None:
    owner = os.environ["GITEE_OWNER"]
    repo = os.environ["GITEE_REPO"]
    token = os.environ["GITEE_TOKEN"]
    user = os.environ["GITEE_USERNAME"]
    auth_url = f"https://{user}:{token}@gitee.com/{owner}/{repo}.git"

    local = workdir / "gitee"
    if local.exists():
        shutil.rmtree(local)
    run(["git", "clone", "--depth", "1", auth_url, str(local)])

    for pf in PAYLOAD_FILES:
        src = workdir / pf
        if src.exists():
            dst = local / pf
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    run(["git", "config", "user.email", "mirror@local"], cwd=local)
    run(["git", "config", "user.name", "dlssg-mirror"], cwd=local)
    run(["git", "add", "-A"], cwd=local)

    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=local
    )
    if diff.returncode == 0:
        print("Gitee 仓库无变化（已同步过该版本）")
    else:
        run(["git", "commit", "-m", f"mirror upstream {version}"], cwd=local)
        run(["git", "push", "origin", "HEAD"], cwd=local)

    # 打同名 tag（已存在则按要求强制更新）
    force = os.environ.get("MIRROR_FORCE") == "1"
    tag_exists = subprocess.run(
        ["git", "tag", "-l", version], cwd=local, capture_output=True, text=True
    ).stdout.strip()
    if tag_exists and not force:
        print(f"tag {version} 已存在，跳过打 tag（设 MIRROR_FORCE=1 可强制）")
    else:
        if tag_exists:
            run(["git", "tag", "-d", version], cwd=local)
        run(["git", "tag", version], cwd=local)
        run(["git", "push", "origin", version, "--force"], cwd=local)

    print(f"已同步 {version} -> gitee.com/{owner}/{repo} @{version}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync dlssg payload to Gitee mirror")
    ap.add_argument("--version", default=None, help="指定版本，默认取上游最新")
    ap.add_argument("--github-repo", default=GITHUB_REPO_DEFAULT)
    args = ap.parse_args()

    for k in ("GITEE_OWNER", "GITEE_REPO", "GITEE_TOKEN", "GITEE_USERNAME"):
        if k not in os.environ:
            raise SystemExit(f"缺少环境变量 {k}")

    ver = args.version or latest_tag(args.github_repo)
    if not ver:
        raise SystemExit("无法确定版本号（请检查网络或显式传 --version）")
    print(f"准备镜像版本：{ver}")

    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        download_upstream_archive(args.github_repo, ver, wd)
        sync_to_gitee(ver, wd)
    print("DONE")


if __name__ == "__main__":
    main()
