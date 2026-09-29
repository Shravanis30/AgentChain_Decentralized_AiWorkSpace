import asyncio
import logging
import signal
import sys

from agentchain_worker.config import worker_config
from agentchain_worker.worker import AgentExecutionWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("agentchain.worker.main")


async def main() -> None:
    worker = AgentExecutionWorker(config=worker_config)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Termination signal received. Initiating graceful shutdown...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Signal handling on Windows / limited environments
            pass

    await worker.start()
    logger.info(f"Worker {worker.worker_id} is running and listening for jobs...")

    await stop_event.wait()
    await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
