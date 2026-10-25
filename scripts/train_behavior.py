import argparse
import json
from pathlib import Path
import _bootstrap
from consumer_behavior.data import read_rows
from consumer_behavior.training import train_behavior


def main():
    parser = argparse.ArgumentParser(description="训练点击基线、逻辑回归及两项消融")
    parser.add_argument("--train", required=True, help="官方训练 CSV/JSONL")
    parser.add_argument("--test", required=True, help="官方测试 CSV/JSONL，不使用 Viewer 合并 split")
    parser.add_argument("--data-version", required=True, help="数据修订号与本地文件版本")
    parser.add_argument("--max-rows", type=int, default=50000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="models/click_v1")
    args = parser.parse_args()
    if Path(args.train).resolve() == Path(args.test).resolve():
        parser.error("训练文件和测试文件不能相同")
    report = train_behavior(read_rows(args.train, args.max_rows), read_rows(args.test, args.max_rows),
                            args.output, data_version=args.data_version, seed=args.seed)
    print(json.dumps({"model_version": report["model_version"], "rows": report["rows"],
        "validation_improved": report["validation_improved"], "runtime_behavior": report["runtime_behavior"],
        "test": report["models"]["full"]["test"], "output": args.output}, ensure_ascii=False))


if __name__ == "__main__":
    main()
