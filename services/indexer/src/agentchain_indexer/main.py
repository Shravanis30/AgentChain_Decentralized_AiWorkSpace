"""CLI entrypoint for running the AgentChain Indexer service."""

import asyncio
import logging
import signal
from agentchain_indexer.config import settings
from agentchain_indexer.service import IndexerService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("indexer")


async def main() -> None:
    service = IndexerService(settings)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(service.stop()))

    try:
        await service.run()
    finally:
        await service.stop()


if __name__ == "__main__":
    asyncio.run(main())
