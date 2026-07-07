"""Custom HTTP transport with DNS resolution."""

from __future__ import annotations

import httpx
from typing import Any

from mexc_monitor.network.dns_resolver import CustomDNSResolver


class CustomDNSHTTPTransport(httpx.HTTPTransport):
    """HTTP transport с кастомным DNS разрешением."""

    def __init__(
        self,
        dns_resolver: CustomDNSResolver,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self._dns_resolver = dns_resolver

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Handle request with DNS resolution."""
        original_url = request.url

        try:
            # Разрешаем DNS для домена
            resolved_url = self._dns_resolver.get_resolved_url(str(original_url))

            # Заменяем URL на разрешенный
            request.url = httpx.URL(resolved_url)

            return super().handle_request(request)

        finally:
            # Возвращаем оригинальный URL
            request.url = original_url