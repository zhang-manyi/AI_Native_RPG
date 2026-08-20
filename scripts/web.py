"""Run the developer web interface (docs/12_Web_Interface.md).

    uv run python scripts/web.py                  # real model if .env has a key
    uv run python scripts/web.py --mock           # offline, scripted replies
    uv run python scripts/web.py --scenario X --npc npc_b

Replaces the terminal demo as the main entry point: the scene page is what a player
sees, the right-hand panel is docs/07 §2.3's Narrative State Panel, and the narrative
tick runs *after* the line is already on screen instead of in front of it.

Needs the ``web`` extra:  pip install -e ".[dev,web]"

⚠️  No authentication, by design. It binds to loopback, and ``/debug/*`` serves the
complete WorldState — every hidden clue and the answer to the mystery. Do not bind it
to 0.0.0.0 or put it behind a reverse proxy; if you ever need to, turn dev mode off
(``--no-dev``) and add auth as a real layer rather than assuming this one is safe.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

# Force UTF-8 on stdio: a Windows console defaults to a legacy code page (GBK here),
# which cannot encode this scenario's Chinese text and turns a log line into a
# UnicodeEncodeError. Done before any import that might print.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Allow running straight from a checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_rpg.config import Settings
from ai_native_rpg.scenario import list_scenarios


def _port_in_use(host: str, port: int) -> bool:
    """Whether something already holds this port.

    A pre-flight check, so a bind failure is explained before the banner claims a URL.
    Racy in principle — someone could grab the port in between — but the real bind
    still raises, so the worst case is the old confusing message.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex((host, port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the scene page and developer panel.")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (keep it loopback)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--mock",
        action="store_true",
        help="force the offline mock client (same as USE_MOCK_LLM=1, but a flag "
        "survives shells that do not propagate env vars)",
    )
    parser.add_argument(
        "--no-dev",
        action="store_true",
        help="serve the player view only: /debug is not registered and the panel is off",
    )
    parser.add_argument("--list", action="store_true", help="list scenario packs and exit")
    # No --reload: it requires an import string rather than an app instance, and the
    # app is built here from parsed arguments. Restarting by hand is cheap now that
    # Ctrl+C works.
    args = parser.parse_args()

    if args.list:
        available = list_scenarios()
        print("可用剧本：" + ("、".join(available) if available else "（scenarios/ 下没有剧本包）"))
        return 0

    # Presence check, not a use: fail with an actionable line rather than a traceback
    # from deep inside the import graph when the optional extra is missing.
    if importlib.util.find_spec("uvicorn") is None:
        print('web 依赖没装。运行：pip install -e ".[dev,web]"')
        return 2

    from ai_native_rpg.web.app import create_app, resolve_dev_mode

    settings = Settings.from_env()
    if args.mock:
        settings = settings.model_copy(update={"use_mock": True})

    dev_mode = resolve_dev_mode(host=args.host, override=False if args.no_dev else None)

    backend = (
        f"{settings.provider} {settings.model}"
        if settings.has_real_backend
        else "MockLLMClient (离线脚本)"
    )
    scenarios = list_scenarios()

    # Check the port before printing the banner. uvicorn's own bind error arrives
    # *after* our header has already announced a URL, which reads as "started, then
    # inexplicably died" — and the usual cause is simply a previous run still holding
    # the port, with a browser's SSE stream keeping it alive (that stream never closes
    # on its own).
    if _port_in_use(args.host, args.port):
        print(f"端口 {args.port} 已被占用——通常是上一次 `python scripts/web.py` 还在跑。")
        print("浏览器开着的 SSE 流会一直保持连接，所以那个进程不会自己退出。")
        print("两个选择：")
        print(f"  1. 换端口：      python scripts/web.py --port {args.port + 1}")
        if sys.platform == "win32":
            print(
                f'  2. 停掉旧进程：  netstat -ano | findstr ":{args.port}"  然后 '
                "Stop-Process -Id <PID>"
            )
        else:
            print(f"  2. 停掉旧进程：  lsof -ti tcp:{args.port} | xargs kill")
        print("重启后记得 Ctrl+F5 强刷，否则浏览器可能用缓存里的旧 js/css。")
        return 2

    print("=" * 72)
    print(f"AI Native RPG — 开发者界面    http://{args.host}:{args.port}")
    print(f"模型：{backend}")
    print(f"剧本：{'、'.join(scenarios) if scenarios else '（无）'}")
    print(f"开发者模式：{'开（/debug 已挂载，面板可见）' if dev_mode else '关（仅玩家视图）'}")
    if dev_mode:
        print("注意：/debug/* 暴露完整世界状态，不要把这个端口暴露到外网。")
    print("=" * 72)

    app = create_app(settings=settings, dev_mode=dev_mode)
    print("Ctrl+C 停止。")
    # Served through StreamAwareServer, not uvicorn.run: open SSE streams have to be
    # closed *before* uvicorn waits on in-flight tasks, or Ctrl+C hangs. See serve.py
    # for why lifespan cleanup cannot do this.
    from ai_native_rpg.web.serve import build_server

    build_server(app, host=args.host, port=args.port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
