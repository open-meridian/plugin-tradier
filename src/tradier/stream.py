"""Tradier's market data stream, over WebSocket, with reconnect (contract
v19; plans/the-lake-prices-the-book, row 19).

What is streamed is what the lake keeps current: the listings under a
standing want (lake.py says which), and nothing while there is none;
nothing of it is kept here. A standing want is heard once, when the lake
asks: contract v19 gives a want no catch-up read and the lake does not ask
again while it stands, so a process started anew streams nothing until a
want is asked anew (raised with the lake's plan).

**Tradier's protocol:** a session is made by REST (`POST
/markets/events/session`, with the token), and its ID, good for five
minutes until a stream connects with it, opens the WebSocket at
`wss://ws.tradier.com/v1/markets/events`: the client sends the symbols, the
session's ID and the events it wants (`timesale` and `quote`, each print
and the consolidated best bid and offer; `validOnly` false, so a cancel and
a correction arrive too), and sends them again, whole, to change them.
Tradier answers with events, one a line. An error naming the session or the
token closes the session; a new one is made for the next.

**Reconnect**, the SDK's supervisor's from 0.14.0 (`helpers.sessions`,
ruling 1027). A connection that breaks, closes or goes silent is opened
again with a new session, a second later, then doubling to a minute, each
wait within a fifth of itself; the
subscriptions are sent again from what is wanted. The gap between two
sessions is not caught up: Tradier's time and sales history names no print
by its sequence number, so reading it again would record a trade twice. Each
reconnect is counted on the Connection page.

**Every message is a raw record first**, a line of the session's record
(raw.StreamLog), before anything converts it; neither the token nor the
session's ID is ever in one.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from decimal import Decimal
from typing import Any, Protocol

from meridian.helpers import sessions

from . import convert, ws
from .convert import SourceQuote, SourceTrade, tradier_symbol
from .raw import RawStore, StreamLog
from .vendor import NS, STREAM

log = logging.getLogger("tradier.stream")

#: How long a session may say nothing before it is taken for broken.
SILENT_SECONDS = 120.0
#: How often what is heard is flushed to the lake, at the most.
FLUSH_SECONDS = 0.5
#: After the token is refused, how long before it is tried again.
REFUSED_WAIT = 300.0
#: The events streamed: each print, and the consolidated best bid and offer.
EVENTS = ("timesale", "quote")

Stated = SourceTrade | SourceQuote


class Refused(Exception):
    """Tradier refused the token: tried again only after a while."""


class FeedError(Exception):
    """Tradier said something went wrong in the session."""


class Feed(Protocol):
    """One session with a stream: opened, subscribed, read, closed."""

    name: str

    async def open(self) -> None: ...

    async def subscribe(self, symbols: Sequence[str]) -> None: ...

    async def receive(self) -> list[Stated]: ...

    async def close(self) -> None: ...


class TradierFeed:
    """Feed on Tradier's stream, every message kept."""

    name = "tradier"

    def __init__(
        self,
        session: Callable[[], Awaitable[str]],
        store: RawStore,
        connect: Callable[..., Awaitable[Any]] = ws.connect,
        url: str = STREAM,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.make_session, self.store = session, store
        self.connect, self.url, self.clock_ns = connect, url, clock_ns
        self.socket: Any = None
        self.log: StreamLog | None = None
        self.session_id = ""
        self.subscribed: list[str] = []

    async def open(self) -> None:
        self.subscribed = []
        self.session_id = await self.make_session()
        now = self.clock_ns()
        day = time.strftime("%Y%m%d", time.gmtime(now // NS))
        self.log = StreamLog(self.store, f"stream/{day}/{now}")
        self.socket = await self.connect(self.url)

    async def subscribe(self, symbols: Sequence[str]) -> None:
        wanted = sorted(symbols)
        if wanted == self.subscribed or not wanted:
            self.subscribed = wanted
            return
        await self.socket.send(
            json.dumps(
                {
                    "symbols": [tradier_symbol(s) for s in wanted],
                    "sessionid": self.session_id,
                    "filter": list(EVENTS),
                    "linebreak": True,
                    "validOnly": False,
                    "advancedDetails": True,
                }
            )
        )
        self.subscribed = wanted

    async def receive(self) -> list[Stated]:
        out: list[Stated] = []
        received = self.clock_ns()
        for line in (await self.socket.recv()).splitlines():
            if not line.strip():
                continue
            try:
                message = json.loads(line, parse_float=Decimal)
            except ValueError:
                raise FeedError(f"not JSON: {line[:80]!r}") from None
            if isinstance(message, dict) and "error" in message:
                # A session expired or refused: the next is a new one.
                raise FeedError(str(message.get("error"))[:200])
            key = ""
            if self.log is not None:
                key = self.log.keep({"received_at_ns": received, "message": _textual(message)})
            out.extend(convert.streamed(message, key))
        return out

    async def close(self) -> None:
        if self.socket is not None:
            with contextlib.suppress(Exception):
                await self.socket.close()
        if self.log is not None:
            self.log.close()
        self.socket, self.log, self.session_id = None, None, ""


def _textual(value: Any) -> Any:
    """A parsed message with each number as its text, as it arrived."""
    if isinstance(value, dict):
        return {k: _textual(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_textual(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


class Streamer(sessions.Supervisor):
    """Tradier's stream kept open by the SDK's supervisor (open-meridian
    0.35, ruling 1027: `helpers.sessions.Supervisor` over the plugin's own
    feed, its `Link`): one session open while anything is wanted,
    subscribed to what is; what it hears handed to `heard` and flushed with
    `flush`. A refusal (the token) is waited out `REFUSED_WAIT`; any other
    break the supervisor's backoff. No gap is caught up (see the module)."""

    def __init__(
        self,
        make_feed: Callable[[], Feed | None],
        heard: Callable[[list[Stated]], Awaitable[None]],
        flush: Callable[[], Awaitable[None]],
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("silent_seconds", SILENT_SECONDS)
        super().__init__(
            make_feed,
            heard=heard,
            flush=flush,
            session="stream",
            refused=(Refused,),
            refused_seconds=REFUSED_WAIT,
            flush_seconds=FLUSH_SECONDS,
            **kwargs,
        )
