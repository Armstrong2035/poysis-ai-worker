"""Structured marketing data ingestion and evidence-based opportunity detection."""

from .service import MarketingService, Observation, parse_ga4, parse_search_console

__all__ = ["MarketingService", "Observation", "parse_ga4", "parse_search_console"]
