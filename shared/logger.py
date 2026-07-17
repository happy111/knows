"""Shared Logger -- AWS Lambda PowerTools logger wrapper.

Single source of truth for logging across all Lambda modules.
Each module gets a child logger via get_logger(module_name).

Usage:
    from shared.logger import get_logger

    logger = get_logger(__name__)
    logger.info("Hello from %s", __name__)
"""
from aws_lambda_powertools import Logger as PowerToolsLogger

# Root logger (shared across all modules)
_logger = PowerToolsLogger()


def get_logger(name: str = None) -> PowerToolsLogger:
    """Get a logger instance for a module.

    Args:
        name: Module name (optional). When provided, returns a child logger.

    Returns:
        PowerTools Logger instance
    """
    if name:
        return PowerToolsLogger(child=True)
    return _logger
