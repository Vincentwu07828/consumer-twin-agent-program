import argparse
import json
from pathlib import Path
import _bootstrap
from consumer_behavior.api import create_http_server
from consumer_behavior.service import RestaurantAgentService
from consumer_behavior.storage import EventStore


def main():
    parser = argparse.ArgumentParser(description="餐厅推荐后端：问卷、意图提示、推荐与反馈")
    parser.add_argument("--config", default="config/consumer_lifesim.local.json")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    path = Path(config.get("store_path", "runtime/lifesim_consumer.sqlite3"))
    store = EventStore(path if path.is_absolute() else _bootstrap.ROOT / path)
    server = create_http_server(RestaurantAgentService(config, store), port=args.port)
    print(f"餐厅推荐后端已启动：http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
