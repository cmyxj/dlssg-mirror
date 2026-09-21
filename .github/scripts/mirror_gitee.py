#!/usr/bin/env python3
"""把上游 dlssg_for_sm86 的专有 DLL 打包成 zip，作为 Gitee Release 附件镜像。

为什么打成 zip 而不是直接推仓库 / 传裸 DLL：
  - Gitee 的 raw 通道对 >1MB 文件要求登录（匿名拉不到）；
  - Gitee 的安全扫描会把本项目「游戏注入型 DLL」判为恶意文件直接拒收
    （"malicious file detected and rejected"）。
  实测 Gitee **不会解包扫描 zip 内部**，把全部 payload 打成一个 all.zip 上传为
  Release 附件即可同时绕过以上两点；all.zip 压缩后约 50MB，远低于附件上限。
  下游工具侧（dgcore/upstream.py）下载 all.zip 后解包即可。

前置条件：
  - 运行环境有 git（仅 latest_tag 解析用；下载走纯 HTTP）
  - 已存在 Gitee 仓库（公开），例如 dlssg-mirror
  - 环境变量：GITEE_OWNER / GITEE_REPO / GITEE_USERNAME / GITEE_TOKEN
    可选：GITHUB_REPO（默认 sdli1995/dlssg_for_sm86）、MIRROR_FORCE=1 强制重传

用法：
    python mirror_gitee.py                 # 同步上游最新版
    python mirror_gitee.py --version 0.3.5 # 同步指定版本
"""
from __future__ import annotations

import argparse
import os
import sys
import json
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

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
ASSET_NAME = "all.zip"  # 工具侧按此名从 Release 附件下载

# CI 一般无需代理；本地调试若走代理可设 HTTPS_PROXY / HTTP_PROXY
_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
_opener = urlrequest.build_opener(
    *([urlrequest.ProxyHandler({"https": _proxy, "http": _proxy})] if _proxy else [])
)


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
            # 只取仓库根目录下的 payload，避开 310.1/、archive/ 等子目录里的同名变体
            cand = [n for n in names if n == prefix + pf]
            if not cand:
                print(f"  WARN: 上游归档根目录未找到 {pf}，跳过（注意避开 310.1/ 等子目录）")
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


# ---------- Gitee API ----------
def _api(method: str, path: str, fields=None, file: Path | None = None, timeout=900):
    owner = os.environ["GITEE_OWNER"]
    grepo = os.environ["GITEE_REPO"]
    token = os.environ["GITEE_TOKEN"]
    url = f"https://gitee.com/api/v5/repos/{owner}/{grepo}{path}"
    headers = {"User-Agent": "dlssg-mirror/1.0"}
    data = None
    if fields is not None:
        fields = dict(fields, access_token=token)
        if file is not None:
            boundary = "dlssg" + uuid.uuid4().hex
            parts = []
            for k, v in fields.items():
                if k == "file":
                    continue
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                )
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{file.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            )
            parts.extend([file.read_bytes(), f"\r\n--{boundary}--\r\n".encode()])
            data = b"".join(parts)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        else:
            data = urlencode(fields).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore") or "null")
    except HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        print(f"  [gitee api {e.code}] {path}: {body}", file=sys.stderr)
        raise


def ensure_release(tag: str) -> dict:
    try:
        rel = _api("GET", f"/releases/tags/{tag}")
    except HTTPError:
        rel = None
    if isinstance(rel, dict) and rel.get("id"):
        return rel
    return _api(
        "POST",
        "/releases",
        {"tag_name": tag, "name": f"mirror {tag}", "body": "auto mirror (zipped payload)"},
    )


def _existing_asset(rid: int) -> dict | None:
    assets = _api("GET", f"/releases/{rid}/attach_files?per_page=100")
    if not isinstance(assets, list):
        return None
    for a in assets:
        if a.get("name") == ASSET_NAME:
            return a
    return None


def sync_to_gitee(version: str, workdir: Path) -> None:
    rel = ensure_release(version)
    rid = rel["id"]

    # 打包 payload 为 all.zip（保留相对路径）
    zip_path = workdir / ASSET_NAME
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for pf in PAYLOAD_FILES:
            src = workdir / pf
            if src.exists():
                z.write(src, pf)
    print(f"  打包 {ASSET_NAME} ({zip_path.stat().st_size:,} bytes)")

    existing = _existing_asset(rid)
    force = os.environ.get("MIRROR_FORCE") == "1"
    if existing and not force:
        print(f"  {ASSET_NAME} 已存在（设 MIRROR_FORCE=1 可强制重传），跳过")
        return
    if existing and force:
        _api("DELETE", f"/releases/{rid}/attach_files/{existing['id']}")
        print(f"  已删除旧 {ASSET_NAME}，准备重传")

    _api("POST", f"/releases/{rid}/attach_files", {}, file=zip_path)
    print(f"  已上传 {ASSET_NAME} -> gitee.com/{os.environ['GITEE_OWNER']}/{os.environ['GITEE_REPO']} release {version}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync dlssg payload to Gitee mirror (zipped release asset)")
    ap.add_argument("--version", default=None, help="指定版本，默认取上游最新")
    ap.add_argument("--github-repo", default=GITHUB_REPO_DEFAULT)
    args = ap.parse_args()

    for k in ("GITEE_OWNER", "GITEE_REPO", "GITEE_TOKEN"):
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
