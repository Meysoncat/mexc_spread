"""Middleware for clock skew detection on HTTP responses.

Provides automatic skew detection for all HTTP requests/responses through httpx.
"""

from __future__ import annotations

import httpx
import logging

from mexc_monitor.clock_skew import ClockSkewDetector

logger = logging.getLogger(__name__)

detector: ClockSkewDetector | None = None


def _get_detector() -> ClockSkewDetector:
    """Get global clock skew detector (lazy initialization)."""
    global detector
    if detector is None:
        from mexc_monitor.backend.main import detector as global_detector
        detector = global_detector
    return detector


class ClockSkewClient(httpx.Client):
    """httpx.Client with automatic clock skew detection.

    All HTTP responses will be scanned for Date headers and used to update
    the global skew detector. This is passive - no manual calls required.
    """

    def __init__(self, *args, exchange: str = "generic", **kwargs):
        self._detector = _get_detector()
        self.exchange = exchange
        super().__init__(*args, **kwargs)

    def handle_response(self, response: httpx.Response) -> httpx.Response:
        """Detect clock skew from response headers."""
        if self._detector:
            try:
                self._detector.check_from_response(self.exchange, response.headers)
            except Exception:
                logger.debug("Failed to parse Date header for skew detection", exc_info=True)
        return response

    # Override the main request methods to call handle_response
    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        self.exchange = kwargs.get("exchange", self.exchange)
        response = super().request(method, url, **kwargs)
        return self.handle_response(response)

    def get(self, url: str, **kwargs) -> httpx.Response:
        self.exchange = kwargs.get("exchange", self.exchange)
        response = super().get(url, **kwargs)
        return self.handle_response(response)

    def post(self, url: str, **kwargs) -> httpx.Response:
        self.exchange = kwargs.get("exchange", self.exchange)
        response = super().post(url, **kwargs)
        return self.handle_response(response)

    def put(self, url: str, **kwargs) -> httpx.Response:
        self.exchange = kwargs.get("exchange", self.exchange)
        response = super().put(url, **kwargs)
        return self.handle_response(response)

    def delete(self, url: str, **kwargs) -> httpx.Response:
        self.exchange = kwargs.get("exchange", self.exchange)
        response = super().delete(url, **kwargs)
        return self.handle_response(response)


def create_clock_skew_middleware(app: FastAPI):
    """Create FastAPI middleware for clock skew detection."""
    detector_instance = _get_detector()

    async def skew_middleware(request: Request, call_next):
        """Middleware to detect clock skew on all requests."""
        response = await call_next(request)

        try:
            detector_instance.check_from_response("generic", response.headers)
        except Exception:
            logger.debug("Failed to parse Date header for skew detection", exc_info=True)

        return response

    return skew_middleware


__all__ = [
    "ClockSkewClient",
    "create_clock_skew_middleware",
    "_get_detector",
]