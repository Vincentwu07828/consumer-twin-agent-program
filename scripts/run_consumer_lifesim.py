import argparse
import json
from pathlib import Path
import _bootstrap
from consumer_behavior.lifesim_runtime import create_simulator


def main():
    parser = argparse.ArgumentParser(description="直接运行 LifeSim 个性化 + DeepSeek + 消费者决策")
    parser.add_argument("--config", default="config/consumer_lifesim.local.json")
    parser.add_argument("--mode", choices=["assistant", "simulation"], default="assistant")
    parser.add_argument("--need", help="助手模式的真实用户需求")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="reports/generated/lifesim_consumer_trace.json")
    parser.add_argument("--interactive", action="store_true", help="交互输入消费需求")
    args = parser.parse_args()
    if args.mode == "assistant" and not args.need and not args.interactive:
        parser.error("助手模式请提供 --need")
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    if config.get("example_only"):
        parser.error("这是配置模板。请复制为本地配置，填入画像、授权、商品、嵌入模型与点击模型路径，并移除 example_only")
    if args.interactive and args.mode == "assistant" and not args.need:
        args.need = input("请输入你的消费需求：").strip()
        if not args.need:
            parser.error("消费需求不能为空")
    simulator = create_simulator(config)
    try:
        trace = simulator.run_consumer_episode(mode=args.mode, user_input=args.need, seed=args.seed)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"run_id": trace["run_id"], "response": trace["response"],
            "decision": trace["decision"], "llm_model": trace["llm_model"], "trace_path": str(output)}, ensure_ascii=False, indent=2))
    finally:
        simulator.store.close()


if __name__ == "__main__":
    main()
