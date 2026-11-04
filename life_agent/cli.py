"""命令行入口：单任务或交互模式驱动 Agent。"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .agent import MODEL_DEFAULT, Agent
from .phone import Phone, PhoneError, compact_ui, configure_console


def _load_env() -> None:
    """读取项目根目录 .env 填充环境变量（凭证不进代码仓库），已设置的变量优先。"""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main(argv: list[str] | None = None) -> int:
    configure_console()
    _load_env()
    parser = argparse.ArgumentParser(
        prog="life-agent",
        description="手机生活服务 Agent：自然语言下指令，自动操作美团等 App（到待支付为止）。",
    )
    parser.add_argument("task", nargs="*", help="自然语言任务；省略则进入交互模式")
    parser.add_argument("--model", help=f"模型（默认取 ANTHROPIC_MODEL 或 {MODEL_DEFAULT}）")
    parser.add_argument("--max-steps", type=int, default=30, help="单任务最大步数（默认 30）")
    parser.add_argument("--serial", help="指定设备序列号（多台设备时）")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只验证手机连接/截图/控件树，不调用模型（不产生费用）",
    )
    args = parser.parse_args(argv)

    try:
        phone = Phone(serial=args.serial)
    except PhoneError as exc:
        print(f"[X] {exc}")
        return 1

    run_dir = Path(__file__).resolve().parent.parent / "runs" / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        return _dry_run(phone, run_dir)

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("[X] 未找到 API 凭证。")
        print("   请在项目根目录 .env 配置 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN 后重试。")
        return 1
    try:
        import anthropic
        client = anthropic.Anthropic()  # 自动读取 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN
    except Exception as exc:  # 凭证缺失/网络配置问题
        print(f"[X] 无法初始化模型客户端：{exc}")
        print("   请检查 .env 中的 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN。")
        return 1

    model = args.model or os.environ.get("ANTHROPIC_MODEL") or MODEL_DEFAULT

    def run_task(task: str) -> None:
        agent = Agent(
            phone=phone,
            client=client,
            model=model,
            run_dir=run_dir,
            max_steps=args.max_steps,
        )
        agent.run(task)

    tasks = [" ".join(args.task).strip()] if args.task else []
    if tasks and tasks[0]:
        try:
            run_task(tasks[0])
        except KeyboardInterrupt:
            print("\n⏹ 已中断（手机停在当前界面）")
        return 0

    print("进入交互模式。输入任务后回车；exit 退出。")
    while True:
        try:
            task = input("\n任务> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        if not task:
            continue
        if task.lower() in ("exit", "quit"):
            return 0
        try:
            run_task(task)
        except KeyboardInterrupt:
            print("\n⏹ 已中断（手机停在当前界面）")


def _dry_run(phone: Phone, run_dir: Path) -> int:
    print(f"设备：{phone.serial}")
    w, h = phone.screen_size()
    print(f"屏幕：{w}x{h}")
    print(f"当前应用：{phone.current_activity()}")
    png = phone.screenshot_png()
    shot = run_dir / "dry_run.png"
    shot.write_bytes(png)
    print(f"截图：{shot}（{len(png) // 1024} KB）")
    nodes = phone.dump_ui()
    print(f"控件树：{len(nodes)} 个节点，其中带标签/可点击的前 15 项：")
    for line in compact_ui(nodes).splitlines()[:15]:
        print(f"  {line}")
    print("\n[OK] 手机控制层正常。去掉 --dry-run 即可开始执行任务（会调用模型产生费用）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
