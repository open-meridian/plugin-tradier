"""The Tradier plugin: Tradier's US equity and ETF market data in an Open
Meridian deployment's lake, as a data plugin (contract v20;
plans/the-lake-prices-the-book, the retail pair).

It connects to the sidecar it is launched beside, declaring its catalogue,
its settings and its storage (declaration.py) and its pages (page.py); on
each delivery of its settings it makes its Tradier client again with the
token for the environment chosen (or, with sample data, the built-in
exchange; on the `sandbox` environment, Tradier's developer sandbox, delayed
and not streamed) and asks whether Tradier answers it; it reads which of its
datasets a deployment admin has licensed (contract v20), and reports the data
role's figures; then it hears what the lake wants of it and records against
each want or declines it per subject (lake.py), and keeps its timetable:
standing wants kept current, days still forming read again, a final close
pushed; and from 0.13.0 the option chains wanted, each expiration read and
kept current (chains.py). While any standing want stands, it streams Tradier's time and sales
and quotes for the listings wanted (stream.py), reconnecting with a new
session when the stream breaks; on the sandbox, which does not stream, it
opens none, and keeps live prices current as it does without a stream. A
restart's gap is not backfilled: Tradier's time and sales history names no
print by its sequence number, so reading it again would record trades twice.

From 0.6.0 it is also the account's connection for orders, where the
deployment launched it with `connectivity`: it hears each placement naming
its instance and places it at Tradier in the environment chosen (live, or
the developer sandbox's paper trading), reads the account's orders on a
poll, Tradier streaming none on its sandbox, and honours the firm's pauses
and stops, and from 0.7.0 reports the fills of orders placed in Tradier's
own web app on no placement (the SDK's
`meridian.roles.connectivity.Trader`). From 0.7.0, where launched with
`custody`, it is the custodian of each account the token reaches too: it
reads them every Read every, reporting their positions, cash and history
(custody.py). Sample data trades with, and reads, a simulated brokerage
(synthetic_broker.py), never Tradier.

It holds nothing it cannot lose: what each subject's symbol is, and every
raw response, are in the storage the deployment grants it (decisions/028);
a restart reads them back and hears the lake again. The token arrives as a
secret setting, never logged, shown or sent anywhere but Tradier.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import meridian
from meridian import edge
from meridian.errors import NotRegistered
from meridian.roles import Latency
from meridian.roles.connectivity import Sent, Trader, Watched, trading_files

from .broker import Broker
from .convert import spelled
from .custody import MARKET_ZONE, Syncer
from .declaration import DECLARATION, PORT
from .kinds import Store, tend, windows_from
from .kinds import root as kinds_root
from .lake import Lake, Products
from .page import State, figures, pages, use
from .raw import RawStore, storage_root
from .settings import SANDBOX_SAID, Config, config_from
from .source import TradierSource
from .stream import Feed, FeedError, Refused, Streamer, TradierFeed
from .synthetic import SyntheticFeed, SyntheticTransport, exchange
from .synthetic_broker import SyntheticBroker
from .vendor import API, SANDBOX, NoKey, Tradier, Transport, VendorError, urllib_transport
from .venue import TradierVenue

log = logging.getLogger("tradier")

#: How often the timetable is looked at.
TICK_SECONDS = 15
#: How often the datasets' licences are read again.
LICENCES_SECONDS = 60
#: How often connectivity reads an account's orders at Tradier, which
#: streams none on its sandbox; and the simulated brokerage, which steps an
#: order on each read.
POLL_SECONDS = 5
SYNTHETIC_POLL_SECONDS = 1
#: How often custody reads the simulated brokerage, in place of Read every.
SYNTHETIC_READ_SECONDS = 5
#: How often the kinds' windows are tended between deliveries of settings.
TEND_SECONDS = 3600
#: How often the source's health is said again between deliveries of
#: settings, from what Tradier last answered.
HEALTH_SECONDS = 60


class Account:
    """Connectivity's client for the settings as they stand: Tradier's API of
    the environment chosen, or the simulated brokerage."""

    def __init__(self, store: Store, granted: Path | None) -> None:
        self.store, self.granted = store, granted
        self.broker: Broker | None = None
        self.venue: TradierVenue | None = None
        self._synthetic: SyntheticBroker | None = None
        self._said: tuple[object, ...] = ()

    def use(self, config: Config) -> bool:
        """Make the client again where the settings changed what it is; True
        where they did."""
        said = (config.sample_data, config.environment, config.credentials)
        if said == self._said:
            return False
        self._said = said
        if config.sample_data:
            if self._synthetic is None:
                state = self.granted / "synthetic-venue" / "orders.json" if self.granted else None
                self._synthetic = SyntheticBroker(lambda: datetime.now(UTC), state)
            self.broker = Broker(
                self.store, None, SANDBOX, send=self._synthetic, keyless=True, synthetic=True
            )
        elif config.credentials is not None:
            base = SANDBOX if config.paper else API
            self.broker = Broker(self.store, config.credentials, base)
        else:
            self.broker = None
        self.venue = TradierVenue(self.broker) if self.broker is not None else None
        return True


def source_for(
    config: Config, store: RawStore, transport: Transport = urllib_transport
) -> tuple[Tradier | None, TradierSource | None]:
    """The client and the source the settings give: sample data, the token's
    at Tradier or at its developer sandbox, or none while there is no
    token."""
    if config.sample_data:
        tradier = Tradier(store, None, transport=SyntheticTransport(), keyless=True)
        return tradier, TradierSource(tradier, synthetic=True)
    if config.credentials is None:
        return None, None
    base = SANDBOX if config.on_sandbox else API
    tradier = Tradier(store, config.credentials, transport=transport, base=base)
    return tradier, TradierSource(tradier)


def feed_for(state: State, store: RawStore) -> Feed | None:
    """The stream the settings give: synthetic, Tradier's with the token, or
    none while there is no token, or on the sandbox, which does not stream."""
    if state.config.sample_data:
        return SyntheticFeed(store)
    if state.config.on_sandbox:
        return None
    tradier = state.tradier
    if state.config.credentials is None or tradier is None:
        return None

    async def session() -> str:
        try:
            return await asyncio.to_thread(tradier.session)
        except NoKey as missing:
            raise Refused(str(missing)) from None
        except VendorError as refused:
            if refused.status in (401, 403):
                raise Refused(f"{refused.status} {refused.detail}") from None
            raise FeedError(f"{refused.status} {refused.detail}") from None

    return TradierFeed(session, store)


def registered(role: Any) -> bool:
    """Whether `meridian.run` registered `role`: the instance holds its
    role."""
    try:
        role.plugin  # noqa: B018 - raises where it is not registered
    except NotRegistered:
        return False
    return True


async def read_licences(role: Any, state: State) -> None:
    """Which of its own declared datasets a deployment admin has licensed
    (contract v20, W10.9), for the Datasets page: a dataset with none is
    served by its catalogue's default terms until one is set."""
    answered = await role.list_datasets()
    state.licensed = frozenset(licence.dataset for licence in answered.licences)


class Process:
    """What the plugin's role classes share: the state the pages read, the
    raw records, the account's client, the settings applied, and the work
    no one role owns -- the latency reported and the health said by the
    first class started, the account scope followed and the kinds tended by
    the first of custody and connectivity started."""

    def __init__(self) -> None:
        self.granted = edge.storage_dir()
        self.store = RawStore(storage_root(Path(tempfile.mkdtemp(prefix="tradier-raw-"))))
        self.kinds = Store(kinds_root())
        # The sessions' latency (contract v27), shown on Connection's
        # Sessions; and from 0.13.0 (contract v28, rulings 515 and 518) each
        # live row's age at the edge too, all reported every 10 seconds
        # through the first class started (SDK 0.35).
        self.latency = Latency()
        self.state = State(store=self.kinds, latency=self.latency)
        self.account = Account(self.kinds, self.granted)
        self.woken = asyncio.Event()
        self.first: Any = None
        self.desk: Any = None
        self.roles: list[Any] = []

    def connection(self) -> meridian.Plugin | None:
        """The connection, through any class registered on it."""
        for role in self.roles:
            if registered(role):
                held: meridian.Plugin = role.plugin
                return held
        return None

    async def on_settings(self, delivered: Any) -> None:
        """Each delivery of the settings, the first before any class starts:
        the Tradier client made again with the token for the environment
        chosen (or, with sample data, the built-in exchange), the account's
        client too, what is streamed asked again, and the kinds tended."""
        state, market = self.state, self.state.lake
        state.config = config_from(delivered.values, delivered.missing)
        state.values = dict(delivered.values)
        if self.account.use(state.config):
            self.woken.set()
        state.venue = self.account.venue
        seconds = SYNTHETIC_POLL_SECONDS if state.config.sample_data else POLL_SECONDS
        state.polled = f"Polled every {seconds}s: Tradier streams no orders on its sandbox"
        state.tradier, state.source = source_for(state.config, self.store)
        if market is not None:
            market.source = state.source
            market.streaming = not state.config.on_sandbox
            # One dataset per timeliness (ruling 551): the sandbox's live
            # data is delayed 15 minutes, recorded into `live_delayed`.
            market.use_delayed(state.config.on_sandbox)
            if state.config.sample_data and registered(market):
                # The built-in exchange's listings, as a source's own list.
                await market.seed_symbols(sorted(spelled(s) or s for s in exchange()["history"]))
        await self.health(again=True)
        if market is not None:
            market.restream()
        if state.streamer is not None:
            state.streamer.restart()
        plugin = self.connection()
        if plugin is not None:
            await tend_now(plugin, self.kinds, delivered.values)

    async def health(self, again: bool = False) -> None:
        """Say how the source is: a missing token or a refused one is the
        plugin's health, never a ticket. `again` asks Tradier anew;
        otherwise what it last answered is said."""
        plugin = self.connection()
        if plugin is None:
            return
        healthy, detail = await source_health(self.state, again)
        try:
            await plugin.report(healthy=healthy, detail=detail[:200])
        except (meridian.MeridianError, ValueError) as failed:
            log.warning("health not reported: %s", failed)

    def started(self, role: Any) -> None:
        """A class started: the first reports the latency and says the
        health; the first of custody and connectivity follows the account
        scope and tends the kinds."""
        if self.latency.role is None:
            self.latency.role = role
        if self.first is None:
            self.first = role
        if self.desk is None and role.role in ("custody", "connectivity"):
            self.desk = role

    async def shared(self, role: Any) -> None:
        """The work `role` owns of what the classes share (see `started`)."""
        work: list[Coroutine[Any, Any, None]] = []
        if role is self.first:
            work += [self.latency.run(), self._health()]
        if role is self.desk:
            work += [self._scope(), self._tend()]
        if work:
            await asyncio.gather(*work)

    async def _health(self) -> None:
        while True:
            await asyncio.sleep(HEALTH_SECONDS)
            await self.health()

    async def _scope(self) -> None:
        """The account scope, its links (W4.11) and its ems's venue account
        map (W11.22), on start and on every change: the account side and its
        pages hold it from here, and custody reads again at once. A stream
        that ends is raised, so the class's work starts again."""
        plugin = self.connection()
        assert plugin is not None
        async for scope in plugin.account_scope():
            self.state.scope = scope
            self.woken.set()
        raise ConnectionError("the account scope's stream ended")

    async def _tend(self) -> None:
        while True:
            await asyncio.sleep(TEND_SECONDS)
            plugin = self.connection()
            if plugin is not None:
                await tend_now(plugin, self.kinds, self.state.values)


async def source_health(state: State, again: bool) -> tuple[bool, str]:
    """How the source is, in a word for the plugin's health."""
    config = state.config
    if config.sample_data:
        return True, "sample data: the built-in exchange"
    if state.source is None:
        return False, "no Tradier token: enter it in Settings"
    try:
        entitled = (
            await asyncio.to_thread(state.source.entitlements, True)
            if again
            else state.source.known
        )
    except (NoKey, VendorError, OSError) as failed:
        return False, f"Tradier did not answer: {failed}"
    if entitled is None:
        return False, "Tradier not asked yet"
    if not entitled.answered:
        return False, f"Tradier refused the token: {entitled.detail}"
    if config.on_sandbox:
        return True, f"answering at {SANDBOX_SAID}"
    timing = "real time" if entitled.real_time else "delayed"
    return True, f"answering; quotes {timing}, consolidated"


async def tend_now(plugin: meridian.Plugin, kinds: Store, values: Any) -> None:
    """Each kind's units past its window moved as the settings say."""
    try:
        await tend(plugin, kinds, windows_from(values), datetime.now(UTC).date())
    except (meridian.MeridianError, OSError) as failed:
        log.warning("the raw records were not tended this time: %s", failed)


class Market(Lake):
    """The data role's class: Tradier's listings and option chains in the
    lake (lake.py, chains.py), its stream kept open, its timetable kept and
    its licences read in `run`."""

    def __init__(self, process: Process) -> None:
        granted = process.granted
        super().__init__(
            None,
            Products(granted / "products.json" if granted else None),
            series_path=granted / "series.json" if granted else None,
        )
        self.process = process
        self.latency = process.latency

    async def on_start(self) -> None:
        self.process.started(self)

    async def run(self) -> None:
        assert self.streamer is not None
        await asyncio.gather(
            self.streamer.run(), self._timetable(), self._licences(), self.process.shared(self)
        )

    async def _timetable(self) -> None:
        while True:
            try:
                await self.tick()
            except meridian.MeridianError as failed:
                log.warning("the timetable stopped this time: %s", failed)
            await asyncio.sleep(TICK_SECONDS)

    async def _licences(self) -> None:
        """The licences, read on start and every minute."""
        while True:
            try:
                await read_licences(self, self.process.state)
            except meridian.MeridianError as failed:
                log.warning("the datasets' licences were not read this time: %s", failed)
            await asyncio.sleep(LICENCES_SECONDS)


class Custodian(Syncer):
    """The custody role's class: each account the token reaches read every
    Read every (custody.py), or sooner when the settings or the links
    change."""

    def __init__(self, process: Process) -> None:
        state = process.state
        super().__init__(
            lambda: process.account.broker,
            paper=lambda: state.config.paper and not state.config.sample_data,
            stale_after=lambda: state.config.stale_after,
            linked=lambda number: state.scope.link_of(number) is not None,
            currencies=lambda: [dict(row) for row in state.config.account_currency],
        )
        self.process = process

    async def on_start(self) -> None:
        self.process.state.syncer = self
        self.process.started(self)

    async def run(self) -> None:
        await asyncio.gather(self._reads(), self.process.shared(self))

    async def _reads(self) -> None:
        state, woken = self.process.state, self.process.woken
        while True:
            woken.clear()
            try:
                await self.run_once()
            except meridian.MeridianError as failed:
                log.warning("this read of the accounts stopped: %s", failed)
            wait = SYNTHETIC_READ_SECONDS if state.config.sample_data else state.config.poll_seconds
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(woken.wait(), timeout=wait)


class Trading(Trader):
    """The connectivity role's class: the SDK's `Trader` against Tradier (or
    the simulated brokerage), the account's orders read on a poll."""

    def __init__(self, process: Process) -> None:
        state, account = process.state, process.account
        sent, enabled, originated = trading_files(process.granted)
        super().__init__(
            lambda: account.venue,
            now=lambda: datetime.now(UTC),
            sent=Sent(sent),
            enabled=Sent(enabled),
            # The venue account codes its ems mapped on it (W11.22).
            scope=lambda: state.scope,
            watched=Watched(originated),
            market_zone=MARKET_ZONE,
            # Each call to the brokerage API its round trip, a placement its
            # acknowledgement, a fill its delay (contract v27).
            latency=state.latency,
            poll_seconds=lambda: (
                SYNTHETIC_POLL_SECONDS if state.config.sample_data else POLL_SECONDS
            ),
        )
        self.process = process

    async def on_start(self) -> None:
        self.process.state.trader = self
        self.process.started(self)
        await super().on_start()

    async def run(self) -> None:
        await asyncio.gather(super().run(), self.process.shared(self))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    process = Process()
    state = process.state
    market = Market(process)
    state.lake = market
    store = process.store
    state.streamer = market.streamer = Streamer(
        lambda: feed_for(state, store), market.heard, market.flush
    )
    # One class per role it may hold (SDK 0.35): `meridian.run` registers
    # those the instance holds, applies the first settings, and starts each
    # one's `run`, again after a backoff should it fail.
    process.roles = [market, Custodian(process), Trading(process)]
    use(state)
    # The version's whole declaration (declaration.py): its settings, its
    # pages with the levels each serves and their tools, and its
    # documentation. A version registers exactly what it was uploaded with
    # (contract v25).
    meridian.run(
        declaration=DECLARATION,
        roles=process.roles,
        pages=pages,
        port=PORT,
        on_settings=process.on_settings,
        figures=lambda: figures(state),
    )


if __name__ == "__main__":
    main()
