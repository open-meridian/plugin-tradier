"""What this version declares beside its role (W8.1, W4.1): its catalogue, its
settings and the storage it asks for.

Built from the plugin's code and used twice: `__main__` registers with it,
and `meridian plugin upload` reads the same one from the built image
(pyproject.toml's `[tool.meridian] declaration`).

**The catalogue** (contracts v18 and v19), six datasets apart, as the lake
plan's Q21 and Q40 have a source's daily and live data and its trades, and from
0.13.0 its option chains:

- `daily`: closes and daily bars, from Tradier's daily history, consolidated
  across exchanges; its day New York's, ending at the 16:00 close; pulled
  for a read and pushed once a session's close is final. Tradier states no
  VWAP or trade count for a day, and documents no limit to its history, so
  the catalogue states none. Not one venue's: the consolidated view.
- `live`: the last trade's price and the consolidated best bid and ask, and
  from contract v19 the consolidated best bid and offer as a quote with its
  sizes in shares, real time for an account holder; pulled for a read; while
  a standing want stands its prices kept current once a minute and its
  quotes streamed. A last price names the exchange its trade printed on, by
  Tradier's code; the catalogue, written in code before any venue is
  resolved, names none. Real time: Live's, no declared delay.
- `live_delayed` (from 0.12.0; ruling 551, one dataset per timeliness): the
  same as `live`, from Tradier's developer sandbox, delayed 15 minutes
  (`declared_delay_ns`), so its staleness and age are judged against the
  delay; recorded only while Environment is Sandbox, as `live` only while it
  is Live. The sandbox streams nothing, so its prices and quotes are kept
  current by reading them.
- `options` and `options_delayed` (from 0.13.0; contract v28, ruling 660):
  an underlying's option chain -- each series' terms
  (`meridian.v1.ListedSeries`), its best bid and offer as a quote keyed by
  the series, and its open interest and volume (`meridian.v1.Statistic`)
  -- read for a want and kept current while a standing want stands, each
  expiration at most once a minute (chains.py). One per timeliness, as the
  live kind: Live's real time, the sandbox's delayed 15 minutes
  (`declared_delay_ns`); apart from `live`, so an admin licenses and
  entitles OPRA's data apart from the equity SIPs'. Not streamed.
- `trades` (contract v19): each of Tradier's time and sales prints with its
  attributes, streamed for the listings under a standing want, and only
  streamed (lake.py says why); apart from the live prices (the plan's Q40),
  so an admin licenses and keeps them apart.

**The terms and pricing Tradier states** (contract 30, ruling 1032;
`terms_and_pricing`), in plain words for each dataset: personal use only
unless a Tradier Partner, market data only to active Tradier Brokerage
account holders and never redistributed, real time at no charge for API
access, the sandbox delayed 15 minutes, OPRA's options data licensed apart
-- never a licence: the licence the deployment's admin sets on Data sources
is the one enforced. A summary, not legal advice; the README says the same.

**How a gap is recovered** (contract 30, `gap_recovery`): `daily` by
`backfill` (Tradier serves the daily history again on a want); `live` and
`trades`, streamed, `stream_only`; `live_delayed` and the chains, served
as they stand now, `none`.

**The storage**, at the edge (decisions/028): Tradier's raw responses, every
row's raw record, and the product map. The account side keeps three kinds
of raw record (kinds.py), each with its own window an admin of the plugin
sets: `responses` (each read of the accounts, their balances, positions and
orders, 30 days), `activity` (from 0.7.0, custody's: each page of an
account's history, seven years) and `trading` (each exchange that places,
changes or cancels, seven years), all archivable. The retention is the longest window, the data
role's raw responses kept within it.
"""

from __future__ import annotations

from meridian import DatasetDeclaration

from .chains import CADENCE_SECONDS as CHAIN_CADENCE_SECONDS
from .chains import DATA_TYPES as CHAIN_DATA_TYPES
from .convert import DAY_END_MINUTE
from .kinds import KINDS
from .lake import DAILY, LIVE, LIVE_CADENCE_SECONDS, LIVE_DELAYED, OPTIONS, OPTIONS_DELAYED, TRADES

VENDOR = "Tradier"
#: How long a raw response is kept: the reach of a re-conversion.
RETENTION_DAYS = max(kind.window_days for kind in KINDS)
#: Tradier's terms and pricing in plain words (contract 30, ruling 1032), as
#: the README summarises them: every dataset's terms end with these.
TRADIER_TERMS = (
    "Unless you are a Tradier Partner, Tradier's APIs are entitled for personal use only. "
    "Market data goes only to active Tradier Brokerage account holders, and is never "
    "redistributed to anyone without one; a personal deployment may keep its own copy for "
    "the account holder's personal use. An account holder's market data is real time and "
    "consolidated from every exchange, at no charge for API access, up to 120 requests a "
    "minute per token; the developer sandbox is delayed 15 minutes."
)
#: The options datasets' words first: OPRA's data, licensed apart.
OPTIONS_TERMS = (
    "US options data is the options exchanges' (OPRA's), licensed apart from stock data. "
)
#: Tradier documents no limit to its daily history: none is stated.
HISTORY_DAYS = 0
#: How far behind real time the developer sandbox serves quotes: 15 minutes.
SANDBOX_DELAY_NS = 15 * 60 * 1_000_000_000

#: The platform's US equity venues (NYSE markets) calendar, read from the
#: platform on 2026-10-14 (the v24 plan's O10): the daily dataset's business
#: dates are the days it says US equity markets are open, so the lake flags a
#: close dated on a closed day (contract v24's `calendar_id`, which waited for
#: this plugin's move off the v23 pins). From 0.13.2 (ruling 768) the option
#: chains' datasets name it too: their open interest and volume are dated by
#: it, and the engine looks to it for a series' sessions where a quote names
#: no venue (ruling 570 (i)).
US_EQUITY_CALENDAR = "CAL-01M4VF0F4XGP3RATCCSKDPZSBP"

CATALOGUE = (
    DatasetDeclaration(
        key=DAILY,
        vendor=VENDOR,
        data_types=[
            "meridian.v1.Price",
            "meridian.v1.Bar",
        ],
        modes=["pull", "push"],
        cadence=86_400,
        history=HISTORY_DAYS,
        terms_and_pricing=TRADIER_TERMS,
        gap_recovery="backfill",
        day_time_zone="America/New_York",
        day_end_minute=DAY_END_MINUTE,
        calendar_id=US_EQUITY_CALENDAR,
    ),
    DatasetDeclaration(
        key=LIVE,
        vendor=VENDOR,
        data_types=["meridian.v1.Price", "meridian.v1.Quote"],
        modes=["pull", "stream"],
        cadence=LIVE_CADENCE_SECONDS,
        terms_and_pricing=TRADIER_TERMS,
        gap_recovery="stream_only",
        day_time_zone="America/New_York",
        day_end_minute=DAY_END_MINUTE,
    ),
    DatasetDeclaration(
        key=LIVE_DELAYED,
        vendor=VENDOR,
        data_types=["meridian.v1.Price", "meridian.v1.Quote"],
        modes=["pull"],
        cadence=LIVE_CADENCE_SECONDS,
        terms_and_pricing=TRADIER_TERMS,
        gap_recovery="none",
        day_time_zone="America/New_York",
        day_end_minute=DAY_END_MINUTE,
        declared_delay_ns=SANDBOX_DELAY_NS,
    ),
    DatasetDeclaration(
        key=OPTIONS,
        vendor=VENDOR,
        data_types=list(CHAIN_DATA_TYPES),
        modes=["pull"],
        cadence=CHAIN_CADENCE_SECONDS,
        terms_and_pricing=OPTIONS_TERMS + TRADIER_TERMS,
        gap_recovery="none",
        day_time_zone="America/New_York",
        day_end_minute=DAY_END_MINUTE,
        calendar_id=US_EQUITY_CALENDAR,
    ),
    DatasetDeclaration(
        key=OPTIONS_DELAYED,
        vendor=VENDOR,
        data_types=list(CHAIN_DATA_TYPES),
        modes=["pull"],
        cadence=CHAIN_CADENCE_SECONDS,
        terms_and_pricing=OPTIONS_TERMS + TRADIER_TERMS,
        gap_recovery="none",
        day_time_zone="America/New_York",
        day_end_minute=DAY_END_MINUTE,
        calendar_id=US_EQUITY_CALENDAR,
        declared_delay_ns=SANDBOX_DELAY_NS,
    ),
    DatasetDeclaration(
        key=TRADES,
        vendor=VENDOR,
        data_types=["meridian.v1.Trade", "meridian.v1.Trade.source_sequence"],
        modes=["stream"],
        terms_and_pricing=TRADIER_TERMS,
        gap_recovery="stream_only",
        day_time_zone="America/New_York",
        day_end_minute=DAY_END_MINUTE,
    ),
)
