"""Small synchronous research SDK. Explicit requests only; ambiguous writes are never retried."""

from __future__ import annotations

from urllib.parse import urlencode, urlsplit

from .vendors import HttpJson


class ResearchClient:
    def __init__(self, origin: str, authorization: str, *, transport=None):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or parsed.port == 0
        ):
            raise ValueError("https_api_origin_required")
        if (
            not isinstance(authorization, str)
            or not authorization.startswith(("Bearer ", "Session "))
            or not 8 <= len(authorization) <= 16384
            or not authorization.isascii()
            or any(ord(c) < 32 for c in authorization)
        ):
            raise ValueError("valid_authorization_header_required")
        self._origin, self._authorization = origin.rstrip("/"), authorization
        self._transport = transport or HttpJson()

    def _request(self, path: str, body=None):
        result = self._transport.request(
            "GET" if body is None else "POST",
            self._origin + "/control-plane" + path,
            headers={"Authorization": self._authorization},
            body=body,
        )
        if not isinstance(result, dict):
            raise TypeError("invalid_research_api_response")
        return result

    def dashboard(self):
        return self._request("/dashboard")

    def reports(self):
        return self._request("/reports")

    def ingest_market_view(self, record: dict):
        if not isinstance(record, dict):
            raise TypeError("market_view_record_required")
        return self._request("/market/views", record)

    def market_views(self, kind: str, entity: str, *, as_of: str | None = None, limit: int = 30):
        if (
            kind not in {"options", "news"}
            or not isinstance(entity, str)
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError("invalid_market_view_query")
        query = {"entity": entity, "limit": limit}
        if as_of is not None:
            query["as_of"] = as_of
        return self._request("/market/views/" + kind + "?" + urlencode(query))

    def price_history(
        self, provider: str, symbol: str, start: str, end: str, asset_class: str, *, regime=False
    ):
        if type(regime) is not bool:
            raise ValueError("invalid_regime_query")
        return self._request(
            "/market/"
            + ("regime" if regime else "bars")
            + "?"
            + urlencode(
                {
                    "provider": provider,
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                    "asset_class": asset_class,
                }
            )
        )
