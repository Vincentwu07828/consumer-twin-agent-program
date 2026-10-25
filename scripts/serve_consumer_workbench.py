"""Run real LifeSim/DeepSeek research API and local frontend on one origin."""
import argparse
import json
import sys
from pathlib import Path
import _bootstrap
from consumer_behavior.research_api import create_workbench_server
from consumer_behavior.research_service import ConsumerResearchService
from consumer_behavior.storage import EventStore


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="LifeSim消费者孪生科研工作台")
    parser.add_argument("--config", default="config/consumer_lifesim.local.json")
    parser.add_argument("--port", type=int, default=5173)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    store = EventStore(_bootstrap.ROOT / "runtime/consumer_research.sqlite3")
    service = ConsumerResearchService(config, store)
    server = create_workbench_server(service, _bootstrap.ROOT / "sites/mirra-consumer-demo/dist", port=args.port)
    print(f"LifeSim消费者孪生工作台：http://127.0.0.1:{args.port}/", flush=True)
    print("模型与嵌入将在实际对话时调用；密钥仅由后端读取。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
