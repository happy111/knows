"""Logger — AWS Lambda Powertools structured logging.

Provides a configured logger instance for all modules.
"""
from aws_lambda_powertools import Logger

_logger = Logger(service="dashboard")


def get_logger(name: str = "dashboard") -> Logger:
    """Return a child logger scoped to the given module name."""
    return _logger.getChild(name)
