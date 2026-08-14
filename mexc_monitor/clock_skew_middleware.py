"""Clock skew detection for outgoing HTTP requests.

Wraps ``httpx.Client`` so every exchange response updates the shared skew
detector without any manual calls at the call sites.
"""

from __future__ import annotations

import httpx
import logging

from mexc_monitor.clock_skew import ClockSkewDetector, get_detector

logger = logging.getLogger(__name__)


def _get_detector() -> ClockSkewDetector:
    """Shared process-wide detector (see :mod:`mexc_monitor.clock_skew`)."""
    return get_detector()


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
                # httpx measures elapsed time for us, so the Date header can be
                # compared against local time without counting network latency
                # as clock skew.
                rtt_ms = response.elapsed.total_seconds() * 1000.0
            except RuntimeError:
                rtt_ms = None
            try:
                self._detector.check_from_response(
                    self.exchange, response.headers, rtt_ms=rtt_ms
                )
            except Exception:
                logger.debug("Failed to parse Date header for skew detection", exc_info=True)
        return response

    # Override the main request methods to call handle_response
    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        self.exchange = kwargs.pop("exchange", self.exchange)
        response = super().request(method, url, **kwargs)
        return self.handle_response(response)

    def get(self, url: str, **kwargs) -> httpx.Response:
        self.exchange = kwargs.pop("exchange", self.exchange)
        response = super().get(url, **kwargs)
        return self.handle_response(response)

    def post(self, url: str, **kwargs) -> httpx.Response:
        self.exchange = kwargs.pop("exchange", self.exchange)
        response = super().post(url, **kwargs)
        return self.handle_response(response)

    def put(self, url: str, **kwargs) -> httpx.Response:
        self.exchange = kwargs.pop("exchange", self.exchange)
        response = super().put(url, **kwargs)
        return self.handle_response(response)

    def delete(self, url: str, **kwargs) -> httpx.Response:
        self.exchange = kwargs.pop("exchange", self.exchange)
        response = super().delete(url, **kwargs)
        return self.handle_response(response)


__all__ = [
    "ClockSkewClient",
    "_get_detector",
]