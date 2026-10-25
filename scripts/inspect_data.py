import argparse
import json
from pathlib import Path
import _bootstrap
from consumer_behavior.data import read_rows, inspect_rows


def main():
    parser = argparse.ArgumentParser(description="有界检查 TaobaoAd 本地 CSV/JSONL")
    parser.add_argument("--input", required=True)
    parser.add_argument("--max-rows", type=int, default=10000)
    parser.add_argument("--output", default="reports/generated/inspection.json")
    args = parser.parse_args()
    report = inspect_rows(read_rows(args.input, args.max_rows))
    report.update(input_file=str(Path(args.input).resolve()), sampling="bounded_prefix", max_rows=args.max_rows)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rows": report["rows"], "click_rate": report["click_rate"],
                      "report": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
