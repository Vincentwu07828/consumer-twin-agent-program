"""Fetch a pinned embedding model and bounded official TaobaoAd CSV samples, then train locally."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request
import _bootstrap

from consumer_behavior.data import read_rows, inspect_rows
from consumer_behavior.training import train_behavior

ROOT = _bootstrap.ROOT
DATASET = "reczoo/TaobaoAd_x1"
EMBEDDING = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "consumer-lifesim-local-setup"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def sample_csv(filename, revision, rows=20000):
    url = f"https://huggingface.co/datasets/{DATASET}/resolve/{revision}/{filename}"
    destination = ROOT / "data/raw" / f"taobaoad_{Path(filename).stem}_sample.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Closing the response early stops consumption of a multi-GB original file.
    request = urllib.request.Request(url, headers={"User-Agent": "consumer-lifesim-local-setup", "Range": "bytes=0-33554431"})
    with urllib.request.urlopen(request, timeout=60) as response:
        stream = io.TextIOWrapper(response, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "clk" not in reader.fieldnames:
            raise ValueError("上游文件不是预期的点击 CSV")
        count = 0
        with destination.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("下载窗口不足或 CSV 行不完整")
                writer.writerow(row)
                count += 1
                if count == rows:
                    break
        if count < rows:
            raise ValueError(f"下载的完整记录不足：{count}")
    return {"path": str(destination.relative_to(ROOT)), "url": url, "rows": count,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "sampling": "official_file_prefix"}


def main():
    dataset_info = fetch_json(f"https://huggingface.co/api/datasets/{DATASET}")
    model_info = fetch_json(f"https://huggingface.co/api/models/{EMBEDDING}")
    dataset_revision, model_revision = dataset_info["sha"], model_info["sha"]
    names = {item["rfilename"] for item in dataset_info["siblings"]}
    if not {"train.csv", "test.csv"} <= names:
        raise ValueError("上游没有独立的官方 train.csv / test.csv")
    report_dir = ROOT / "reports/generated"
    report_dir.mkdir(parents=True, exist_ok=True)
    sources = {"dataset": DATASET, "dataset_revision": dataset_revision,
        "embedding": EMBEDDING, "embedding_revision": model_revision,
        "usage": "local_research_prototype", "full_dataset_downloaded": False,
        "license_note": "Dataset card does not declare a standalone license; original source is Tianchi dataId=56",
        "samples": [sample_csv(filename, dataset_revision) for filename in ("train.csv", "test.csv")]}
    (report_dir / "resource_sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stage": "samples_downloaded", "dataset_revision": dataset_revision}, ensure_ascii=False), flush=True)
    train, test = [read_rows(ROOT / sample["path"], sample["rows"]) for sample in sources["samples"]]
    for name, rows in (("train", train), ("test", test)):
        (report_dir / f"inspection_{name}.json").write_text(json.dumps(inspect_rows(rows), ensure_ascii=False, indent=2), encoding="utf-8")
    version = f"{DATASET}@{dataset_revision}:official-prefix-20000-per-file"
    report = train_behavior(train, test, ROOT / "models/click_v1", data_version=version)
    print(json.dumps({"stage": "trained", "validation_improved": report["validation_improved"],
        "runtime_behavior": report["runtime_behavior"], "test_log_loss": report["models"]["full"]["test"]["log_loss"]}, ensure_ascii=False), flush=True)
    cli = shutil.which("hf")
    if not cli:
        raise RuntimeError("未找到 hf 下载工具")
    env = dict(os.environ, HF_HOME=str(ROOT / "runtime/hf_cache"), HF_HUB_DISABLE_XET="1")
    files = [item["rfilename"] for item in model_info["siblings"]
             if not item["rfilename"].startswith(("onnx/", "openvino/"))
             and (item["rfilename"].endswith((".json", ".safetensors", ".txt", ".md"))
                  or item["rfilename"] in ("sentencepiece.bpe.model", "LICENSE"))]
    subprocess.run([cli, "download", EMBEDDING, *files, "--revision", model_revision,
        "--local-dir", str(ROOT / "models/embedding_multilingual"),
        "--max-workers", "2"], check=True, env=env)
    print("consumer_resources_ready", flush=True)


if __name__ == "__main__":
    main()
