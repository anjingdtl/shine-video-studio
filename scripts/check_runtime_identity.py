"""Read-only: does this data directory still serve real production?

「检索过、验证过、激活过」这件事必须跨一次目录搬迁存活。这个脚本只走公开入口
（``resolve_runtime`` / ``runtime_report``），因此可以直接用来判定「这台机器现在
到底是不是真实制作」，也可以用来对比修复前后的行为。

    backend/.venv/Scripts/python.exe scripts/check_runtime_identity.py
    backend/.venv/Scripts/python.exe scripts/check_runtime_identity.py --data-dir D:\\Shine\\data

退出码：0 = 真实制作可用；1 = 没有可用配置或被阻断（打印原因）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", help="要检查的数据目录（默认读 SHINE_DATA_DIR /.env）")
    args = parser.parse_args(argv)

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "backend"))
    if args.data_dir:
        import os

        os.environ["SHINE_DATA_DIR"] = str(Path(args.data_dir).resolve())

    from fastapi import HTTPException

    from shine.comfyops.runtime_configuration import resolve_runtime, runtime_report
    from shine.config import Settings
    from shine.db import make_engine, make_session_factory

    settings = Settings.from_env()
    db = make_session_factory(make_engine(settings.database_url))()
    print("data_dir:", settings.data_dir)
    print("comfyops:", settings.comfyops_dir)

    try:
        effective = resolve_runtime(db, settings)
    except HTTPException as exc:
        print("resolve : BLOCKED", exc.detail)
        report = runtime_report(db, settings)
        print("report  :", report["status"], "/", report["render_provider"], "/",
              report["error"], "/ services_verified=", report["services_verified"])
        return 1

    print("resolve : OK  provider=", effective.render_provider,
          " recipe=", effective.managed_recipe_path)
    report = runtime_report(db, settings)
    print("report  :", report["status"], "/", report["render_provider"], "/", report["error"],
          "/ services_verified=", report["services_verified"])
    return 0 if report["status"] == "ACTIVE" and report["services_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
