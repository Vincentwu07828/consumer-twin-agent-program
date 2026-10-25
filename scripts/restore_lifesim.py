"""Restore missing upstream modules at the exact revision already in this workspace."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "LifeSim" / "upstream-lifesim"
REPOSITORY = "dfy37/lifesim"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "consumer-lifesim-integration"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main():
    revision = (TARGET / "SOURCE_REVISION.txt").read_text().strip()
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("SOURCE_REVISION 必须为完整提交哈希")
    tree = json.loads(fetch(f"https://api.github.com/repos/{REPOSITORY}/git/trees/{revision}?recursive=1"))
    if tree.get("truncated"):
        raise RuntimeError("上游目录响应不完整")
    records = []
    for entry in tree["tree"]:
        relative = entry["path"]
        wanted = (relative.startswith(("src/tools/", "src/utils/", "src/profiles/", "src/models/", "src/engine/"))
                  and relative.endswith(".py")) or relative in (
                      "requirements.txt", "LICENSE", "LICENSE.md", "LICENSE.txt", "data/language_templates.json")
        if entry["type"] != "blob" or not wanted:
            continue
        destination = (TARGET / relative).resolve()
        if not destination.is_relative_to(TARGET.resolve()):
            raise ValueError("无效上游路径")
        if destination.exists():
            status = "existing_file_preserved"
        else:
            content = fetch(f"https://raw.githubusercontent.com/{REPOSITORY}/{revision}/{relative}")
            git_sha = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
            if git_sha != entry["sha"]:
                raise RuntimeError(f"内容校验失败：{relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            status = "restored_from_upstream"
        records.append({"path": relative, "upstream_blob_sha": entry["sha"], "status": status})
    manifest = {"repository": REPOSITORY, "revision": revision, "files": records,
                "license_files": [e["path"] for e in tree["tree"] if e["path"].upper().startswith("LICENSE")]}
    (TARGET / "RESTORED_SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
