import logging
import sys
from ui.app import ModernTrafficBot


def setup_logging():
    """Configure logging for the application."""
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)

    # Suppress noisy third-party loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('curl_cffi').setLevel(logging.WARNING)


if __name__ == "__main__":
    setup_logging()
    logging.info("Starting MR ADS Traffic Suite...")

    app = ModernTrafficBot()
    app.mainloop()

    logging.info("Application closed.")
