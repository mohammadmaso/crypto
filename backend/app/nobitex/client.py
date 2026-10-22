"""Nobitex REST client (apidocs.nobitex.ir).

Auth: Ed25519 API key. Each private request carries
  Nobitex-Key: <public key>
  Nobitex-Timestamp: <unix seconds>
  Nobitex-Signature: urlsafe_b64(ed25519_sign(timestamp + METHOD + path?query + raw_body))
The signed body bytes must be exactly the bytes sent, so bodies are serialised once, compactly.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..config import USER_AGENT, Secrets

log = logging.getLogger(__name__)


class NobitexError(Exception):
    def __init__(self, message: str, code: str | None = None, status: int | None = None, payload: Any = None):
        super().__init__(f"{code or 'error'}: {message}")
        self.code = code
        self.status = status
        self.payload = payload


def sign(private_key_b64: str, timestamp: str, method: str, full_path: str, body: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(private_key_b64))
    payload = f"{timestamp}{method.upper()}{full_path}{body}".encode()
    return base64.urlsafe_b64encode(key.sign(payload)).decode()


@dataclass
class _Bucket:
    """Token bucket for the shared order limit (300 per 10 minutes)."""
    capacity: int
    per_seconds: float
    tokens: float = 0.0
    updated: float = 0.0

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()

    async def take(self) -> None:
        while True:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.capacity / self.per_seconds)
            self.updated = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            await asyncio.sleep((1 - self.tokens) * self.per_seconds / self.capacity)


ORDER_PATHS = ("/market/orders/add", "/margin/orders/add")


class NobitexClient:
    def __init__(self, base_url: str, secrets: Secrets | None = None, proxy: str | None = None, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.secrets = secrets or Secrets()
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, proxy=proxy or None,
                                       headers={"User-Agent": USER_AGENT})
        self._orders = _Bucket(capacity=280, per_seconds=600)

    async def close(self) -> None:
        await self._http.aclose()

    async def request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
                      auth: bool = False, retries: int = 3) -> dict:
        method = method.upper()
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        full_path = f"{path}?{query}" if query else path
        raw = json.dumps(body, separators=(",", ":")) if body is not None else ""
        if path in ORDER_PATHS or path.endswith("/close"):
            await self._orders.take()
        for attempt in range(retries + 1):
            headers = {"Content-Type": "application/json"} if raw else {}
            if auth:
                if not self.secrets.configured:
                    raise NobitexError("API key not configured", code="NoApiKey")
                ts = str(int(time.time()))
                headers |= {"Nobitex-Key": self.secrets.api_key, "Nobitex-Timestamp": ts,
                            "Nobitex-Signature": sign(self.secrets.api_secret, ts, method, full_path, raw)}
            try:
                r = await self._http.request(method, full_path, content=raw or None, headers=headers)
            except httpx.TransportError as e:
                if attempt < retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise NobitexError(str(e), code="Transport") from e
            try:
                data = r.json()
            except ValueError:
                data = {"status": "failed", "message": r.text[:300]}
            if r.status_code == 429:
                wait = float(data.get("backOff", 10)) if isinstance(data, dict) else 10.0
                log.warning("Nobitex rate limit on %s, backing off %.0fs", path, wait)
                if attempt < retries:
                    await asyncio.sleep(wait)
                    continue
            if r.status_code >= 500 and attempt < retries:
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code >= 400 or (isinstance(data, dict) and data.get("status") == "failed"):
                raise NobitexError(str(data.get("message") or data.get("detail") or r.text[:300]),
                                   code=data.get("code"), status=r.status_code, payload=data)
            return data
        raise NobitexError("retries exhausted", code="Retries")

    # ---------- public market data ----------
    async def udf_history(self, symbol: str, resolution: str, frm: int, to: int) -> dict:
        return await self.request("GET", "/market/udf/history",
                                  params={"symbol": symbol, "resolution": resolution, "from": frm, "to": to})

    async def stats(self, src: str | None = None, dst: str | None = None) -> dict:
        return (await self.request("GET", "/market/stats", params={"srcCurrency": src, "dstCurrency": dst}))["stats"]

    async def orderbook(self, symbol: str) -> dict:
        return await self.request("GET", f"/v3/orderbook/{symbol}")

    async def options(self) -> dict:
        return (await self.request("GET", "/v2/options"))["nobitex"]

    async def margin_markets(self) -> dict:
        return (await self.request("GET", "/margin/markets/list", auth=self.secrets.configured))["markets"]

    # ---------- private ----------
    async def wallets(self, kind: str = "spot") -> list[dict]:
        return (await self.request("GET", "/users/wallets/list", params={"type": kind}, auth=True))["wallets"]

    async def profile(self) -> dict:
        return await self.request("GET", "/users/profile", auth=True)

    async def add_order(self, **order: Any) -> dict:
        return (await self.request("POST", "/market/orders/add", body=_strs(order), auth=True))["order"]

    async def order_status(self, order_id: int | None = None, client_order_id: str | None = None) -> dict:
        body = {"id": order_id} if order_id is not None else {"clientOrderId": client_order_id}
        return (await self.request("POST", "/market/orders/status", body=body, auth=True))["order"]

    async def cancel_order(self, order_id: int | None = None, client_order_id: str | None = None) -> dict:
        body = {"status": "canceled"} | ({"order": order_id} if order_id is not None else {"clientOrderId": client_order_id})
        return await self.request("POST", "/market/orders/update-status", body=body, auth=True)

    async def transfer(self, currency: str, amount: str, src: str, dst: str) -> dict:
        return await self.request("POST", "/wallets/transfer",
                                  body={"currency": currency, "amount": amount, "src": src, "dst": dst}, auth=True)

    async def delegation_limit(self, symbol: str) -> dict:
        return await self.request("GET", "/margin/v2/delegation-limit", params={"symbol": symbol}, auth=True)

    async def add_margin_order(self, **order: Any) -> dict:
        return (await self.request("POST", "/margin/orders/add", body=_strs(order), auth=True))["order"]

    async def positions(self, status: str = "active", src: str | None = None, dst: str | None = "usdt") -> list[dict]:
        out, page = [], 1
        while True:
            d = await self.request("GET", "/positions/list", auth=True,
                                   params={"status": status, "srcCurrency": src, "dstCurrency": dst, "page": page})
            out += d.get("positions", [])
            if not d.get("hasNext"):
                return out
            page += 1

    async def position_status(self, position_id: int) -> dict:
        return (await self.request("GET", f"/positions/{position_id}/status", auth=True))["position"]

    async def close_position(self, position_id: int, **order: Any) -> dict:
        return await self.request("POST", f"/positions/{position_id}/close", body=_strs(order), auth=True)


def _strs(d: dict) -> dict:
    """Monetary values as strings (docs: best precision)."""
    return {k: (format(Decimal(repr(v)), "f") if isinstance(v, float) else str(v) if isinstance(v, (int, Decimal)) else v)
            for k, v in d.items() if v is not None}
