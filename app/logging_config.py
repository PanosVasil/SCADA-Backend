import logging

def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(threadName)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("opcua").setLevel(logging.WARNING)
