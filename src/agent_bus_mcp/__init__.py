"""A small least-privilege durable task queue and MCP bridge."""

from .queue import DurableQueue, QueueRefused

__all__ = ["DurableQueue", "QueueRefused"]
"""Agent Bus MCP: a vendor-neutral durable task bridge."""

__version__ = "0.1.0"
