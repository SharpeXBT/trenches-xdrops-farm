"""OKX EEA spot volume farm.

Quotes a post-only bid and ask one tick apart inside the book and lets others
fill them, then sells back to flat. Spot cash only: it can never borrow, never
go short, and never spend more than the free balance it can see.

To use it: fill in API_KEY / API_SECRET / API_PASSPHRASE at the bottom of this
file, set SYMBOL and TARGET_VOLUME_USD, then run `python bot.py`.

Expected cost is the maker fee on the volume it does - about $8 per $10,000
traded at 8bp - plus roughly half a tick per round trip. It halts if the
realised loss exceeds LOSS_CAP_MULT times that, but the cap is not armed until
WARMUP_VOLUME_USD of volume has traded. It stops at the volume target, at the
loss cap, or on Ctrl+C, and then tries to sell back to flat (post-only, for
up to two minutes). Any residual it could not sell is printed as `base left`.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 9):
    raise SystemExit("bot.py needs Python 3.9 or newer; you are running "
                     + sys.version.split()[0] + " - install from python.org")

import atexit
import base64
import hashlib
import hmac
import json
import os
import re
import signal
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import NamedTuple


def _force_ipv4() -> None:
    # eea.okx.com advertises an IPv6 route that never completes: without this every
    # call takes ~40s, past OKX's 30s signature window -> "50102 Timestamp request
    # expired". It looks like clock skew and is not. Do not remove.
    real = socket.getaddrinfo
    socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: real(h, p, socket.AF_INET, t, pr, fl)


_force_ipv4()

_ORDER_ALREADY_GONE = ("51400", "51401", "51402")
_SWALLOW = (RuntimeError, ValueError, AttributeError, TypeError, KeyError,
            IndexError, ArithmeticError, KeyboardInterrupt)

Level = tuple[Decimal, Decimal]


class Config(NamedTuple):
    host: str
    symbol: str
    base_ccy: str
    quote_ccy: str
    tag: str
    key: str
    secret: str
    passphrase: str
    clip_usd: Decimal
    target_volume_usd: Decimal
    inventory_band_usd: Decimal
    spend_fraction: Decimal
    maker_bp_max: Decimal
    taker_bp_max: Decimal
    loss_cap_mult: Decimal
    warmup_volume_usd: Decimal
    max_runtime_s: float
    unwind_s: float
    poll_s: float
    clip_usd_quiet: Decimal
    quiet_flow_per_min: Decimal
    run_tag: str = ""


class Instrument(NamedTuple):
    tick: Decimal
    lot: Decimal
    min_sz: Decimal


class Fees(NamedTuple):
    maker_bp: Decimal
    taker_bp: Decimal


class Balances(NamedTuple):
    sellable: Decimal
    owned: Decimal
    quote: Decimal


class Resting(NamedTuple):
    side: str
    ord_id: str
    px: Decimal
    size: Decimal


class State(NamedTuple):
    bids: list[Level]
    asks: list[Level]
    ext_bids: list[Level]
    ext_asks: list[Level]
    ext_mid: Decimal
    avail_base: Decimal
    total_base: Decimal
    free_quote: Decimal
    resting: dict[str, Resting]


class Tally(NamedTuple):
    volume: Decimal
    n_fills: int
    n_maker: int
    inv_from_fills: Decimal
    fees: Decimal
    gross: Decimal
    net: Decimal


class Action(NamedTuple):
    kind: str
    why: str
    side: str = ""
    price: Decimal = Decimal(0)
    size: Decimal = Decimal(0)
    ord_id: str = ""


# ---------------------------------------------------------------- primitives

def _number(name: str, value, minimum: Decimal, maximum: Decimal | None = None) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    try:
        d = Decimal(str(value))
    except InvalidOperation as e:
        raise ValueError(f"{name} must be a decimal number, got {value!r}") from e
    if not d.is_finite():
        raise ValueError(f"{name} must be finite, got {value!r}")
    if d < minimum or (maximum is not None and d > maximum):
        bound = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        raise ValueError(f"{name} must be {bound}, got {d}")
    return d


def _dec(row: dict, key: str, name: str, default: Decimal | None = None) -> Decimal:
    raw = row.get(key) if isinstance(row, dict) else None
    if raw in ("", None):
        if default is None:
            raise ValueError(f"OKX response is missing {name}: {str(row)[:120]}")
        return default
    try:
        value = Decimal(str(raw))
    except InvalidOperation as e:
        raise ValueError(f"OKX returned a non-numeric {name}={raw!r}") from e
    if not value.is_finite():
        raise ValueError(f"OKX returned a non-finite {name}={raw!r}")
    return value


def _floor_lot(size: Decimal, lot: Decimal) -> Decimal:
    if lot <= 0:
        raise ValueError(f"lot size must be positive, got {lot}")
    return (size / lot).quantize(Decimal(1), rounding=ROUND_DOWN) * lot


def _external_book(levels: list[Level], mine: dict[Decimal, Decimal]) -> list[Level]:
    out: list[Level] = []
    for px, sz in levels:
        rest = sz - mine.get(px, Decimal(0))
        if rest > 0:
            out.append((px, rest))
    return out


def _quote_prices(ext_bid: Decimal, ext_ask: Decimal, tick: Decimal) -> tuple[Decimal, Decimal] | None:
    # post_only that would cross is REJECTED, not repriced, so both prices must
    # land strictly inside: bid = ext_bid+1t, ask = bid+1t (or ext_ask-1t when
    # diming), ask < ext_ask -> at least 3 ticks of room.
    if ext_ask - ext_bid < tick * 3:
        # No room inside, so JOIN both touches rather than stand aside. A
        # post-only at the best bid does not cross - it queues behind what is
        # already there. Standing aside here costs the whole busy regime: the
        # book is tightest exactly when the tape is heaviest, and RE-USDC sits
        # under 3 ticks ~40% of the time.
        return ext_bid, ext_ask
    bid = ext_bid + tick
    ask = bid + tick
    if ask >= ext_ask or ask <= bid:
        return None
    return bid, ask


# --------------------------------------------------------------- pure kernel

def _credentials(api_key: str, api_secret: str, api_passphrase: str) -> tuple[str, str, str]:
    key = api_key or os.environ.get("OKX_API_KEY", "")
    secret = api_secret or os.environ.get("OKX_API_SECRET", "")
    phrase = api_passphrase or os.environ.get("OKX_API_PASSPHRASE", "")
    if key and secret and phrase:
        return key, secret, phrase
    raise ValueError("open bot.py, scroll to the bottom, and fill in "
                     "API_KEY / API_SECRET / API_PASSPHRASE")


def _config(host: str, symbol: str, api_key: str, api_secret: str,
            api_passphrase: str, clip_usd, target_volume_usd,
            inventory_band_usd, loss_cap_mult, clip_usd_quiet=None,
            maker_bp_max=8.0, taker_bp_max=10.0, spend_fraction=Decimal("0.98"),
            warmup_volume_usd=None, max_runtime_s=0, unwind_s=120, poll_s=1.0,
            quiet_flow_per_min=0, tag="REF") -> Config:
    if not isinstance(symbol, str) or symbol.count("-") != 1 or not all(symbol.split("-")):
        raise ValueError(f"SYMBOL must be BASE-QUOTE, e.g. 'RE-USDC', got {symbol!r}")
    if not isinstance(host, str) or not host.startswith("https://"):
        raise ValueError(f"HOST must be an https URL, got {host!r}")
    if not isinstance(tag, str) or not (tag.isascii() and tag.isalnum()) or len(tag) > 14:
        raise ValueError(f"TAG must be 1-14 alphanumeric chars (clOrdId prefix), got {tag!r}")
    base_ccy, quote_ccy = symbol.split("-")
    clip = _number("CLIP_USD", clip_usd, Decimal(1))
    band = _number("INVENTORY_BAND_USD", inventory_band_usd, Decimal(1))
    if clip > band:
        raise ValueError(f"CLIP_USD {clip} exceeds INVENTORY_BAND_USD {band}; one fill would breach the band")
    clip_quiet = clip if clip_usd_quiet is None else _number("CLIP_USD_QUIET", clip_usd_quiet, Decimal(1), clip)
    return Config(
        host, symbol, base_ccy, quote_ccy, tag,
        *_credentials(api_key, api_secret, api_passphrase),
        clip,
        _number("TARGET_VOLUME_USD", target_volume_usd, Decimal(1)),
        band,
        _number("SPEND_FRACTION", spend_fraction, Decimal("0.5"), Decimal(1)),
        _number("MAKER_BP_MAX", maker_bp_max, Decimal(0), Decimal(100)),
        _number("TAKER_BP_MAX", taker_bp_max, Decimal(0), Decimal(100)),
        _number("LOSS_CAP_MULT", loss_cap_mult, Decimal("0.1"), Decimal(5)),
        band * 4 if warmup_volume_usd is None else _number("WARMUP_VOLUME_USD", warmup_volume_usd, Decimal(0)),
        float(_number("MAX_RUNTIME_SECONDS", max_runtime_s, Decimal(0))),
        float(_number("UNWIND_SECONDS", unwind_s, Decimal(10))),
        float(_number("POLL_SECONDS", poll_s, Decimal("0.2"), Decimal(60))),
        clip_quiet,
        _number("QUIET_FLOW_USD_PER_MIN", quiet_flow_per_min, Decimal(0)))


def _affordable(side: str, price: Decimal, avail_base: Decimal, free_quote: Decimal,
                inst: Instrument, clip_usd: Decimal, spend_fraction: Decimal) -> Decimal:
    if not isinstance(price, Decimal):
        raise TypeError(f"_affordable() needs a Decimal price, got {type(price).__name__}")
    if not price.is_finite() or price <= 0:
        raise ValueError(f"_affordable() needs a positive finite price, got {price}")
    wanted = _floor_lot(clip_usd / price, inst.lot)
    if side == "buy":
        ceiling = _floor_lot(free_quote * spend_fraction / price, inst.lot)
    elif side == "sell":
        ceiling = _floor_lot(avail_base * spend_fraction, inst.lot)
    else:
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    size = min(wanted, ceiling)
    return size if size >= inst.min_sz else Decimal(0)


def _tally(ledger: list[dict], mark: Decimal, base_ccy: str) -> Tally:
    if not isinstance(mark, Decimal):
        raise TypeError(f"_tally(mark=...) needs a Decimal price, got {type(mark).__name__}")
    if not mark.is_finite() or mark <= 0:
        raise ValueError(f"_tally(mark=...) needs a positive finite price to value inventory, got {mark}")
    vol = quote_flow = inv = fees = Decimal(0)
    n_maker = 0
    for f in ledger:
        px, sz = _dec(f, "fillPx", "fillPx"), _dec(f, "fillSz", "fillSz")
        if px <= 0 or sz <= 0:
            raise ValueError(f"fill {f.get('tradeId')} has fillPx={px} fillSz={sz}; both must be positive")
        vol += px * sz
        n_maker += f.get("execType") == "M"
        side = f.get("side")
        if side == "buy":
            quote_flow -= px * sz
            inv += sz
        elif side == "sell":
            quote_flow += px * sz
            inv -= sz
        else:
            raise ValueError(f"fill {f.get('tradeId')} has side={side!r}, expected 'buy' or 'sell'")
        fee = _dec(f, "fee", "fee", Decimal(0))
        if f.get("feeCcy") == base_ccy:
            # OKX already deducted this fee from the size it credited, so it hits
            # inventory ONLY. Also charging it to quote_flow double-counts and
            # reports 2x the true cost (an 8bp fill reads as 16bp, trips the cap).
            inv += fee
            fees -= fee * px
        else:
            quote_flow += fee
            fees -= fee
    net = quote_flow + inv * mark
    return Tally(vol, len(ledger), n_maker, inv, fees, net + fees, net)


def _build_state(bids: list[Level], asks: list[Level], bal: Balances,
                 live: list[Resting]) -> State:
    mine: dict[str, dict[Decimal, Decimal]] = {"buy": {}, "sell": {}}
    for o in live:
        mine[o.side][o.px] = mine[o.side].get(o.px, Decimal(0)) + o.size
    ext_bids = _external_book(bids, mine["buy"])
    ext_asks = _external_book(asks, mine["sell"])
    # Our own quotes sit at the top of the raw book, so the mid must be taken
    # after our resting size is removed or it feeds back on our own price.
    ext_mid = ((ext_bids[0][0] + ext_asks[0][0]) / 2 if ext_bids and ext_asks
               else (bids[0][0] + asks[0][0]) / 2)
    return State(bids, asks, ext_bids, ext_asks, ext_mid,
                 bal.sellable, bal.owned, bal.quote, {o.side: o for o in live})


def _decide(st: State, inst: Instrument, cfg: Config, reduce_only: bool) -> Action:
    if not st.ext_bids or not st.ext_asks:
        return Action("wait", "no external book")
    ext_bid, ext_ask = st.ext_bids[0][0], st.ext_asks[0][0]
    inv_usd = st.total_base * st.ext_mid
    want_buy = not reduce_only and inv_usd <= cfg.inventory_band_usd
    want_sell = st.total_base >= inst.min_sz

    if not want_buy and "buy" in st.resting:
        held = st.resting["buy"]
        return Action("cancel", "not buying", side="buy", price=held.px, ord_id=held.ord_id)
    pair = _quote_prices(ext_bid, ext_ask, inst.tick)
    if pair is None:
        return Action("wait", f"spread {int((ext_ask - ext_bid) / inst.tick)}t, no room")
    bid, ask = pair
    starved = ""
    # the ask offers the WHOLE position and the bid tapers to zero at the band,
    # so inventory drains at full size while accumulation slows as risk grows.
    sell_target = max(cfg.clip_usd, inv_usd)
    buy_target = cfg.clip_usd * max(Decimal(0), 1 - inv_usd / cfg.inventory_band_usd)
    for side, px, want, target in (("sell", ask, want_sell, sell_target),
                                   ("buy", bid, want_buy, buy_target)):
        held = st.resting.get(side)
        if held and held.px != px:
            return Action("cancel", "stale", side=side, price=held.px, ord_id=held.ord_id)
        if want and not held:
            size = _affordable(side, px, st.avail_base, st.free_quote, inst,
                               target, cfg.spend_fraction)
            if size > 0:
                return Action("quote", "room to quote", side=side, price=px, size=size)
            starved = side
    if starved:
        return Action("wait", f"{starved} size 0 - raise CLIP_USD or fund the account")
    return Action("wait", "quotes in place")


def _budget(volume: Decimal, taker_bp: Decimal, tick: Decimal, mid: Decimal) -> Decimal:
    return volume * (taker_bp + tick / mid * 10000 / 2) / 10000


def _halt_reason(pnl: Tally, budget: Decimal, elapsed: float, cfg: Config) -> str:
    if pnl.volume >= cfg.warmup_volume_usd and -pnl.net > budget * cfg.loss_cap_mult:
        return "loss cap"
    if pnl.volume >= cfg.target_volume_usd:
        return "target reached"
    if cfg.max_runtime_s and elapsed > cfg.max_runtime_s:
        return "time up"
    return ""


def _wrap(text: str, width: int) -> list[str]:
    """Split text into lines of at most `width`, breaking on spaces.

    The panel is the only output this bot has, so an exchange error has to be
    readable inside it. Truncating it to one row cut the message off exactly
    where the exchange puts its reason code.
    """
    if width < 1:
        raise ValueError(f"width must be positive, got {width}")
    out: list[str] = []
    line = ""
    for word in text.split():
        while len(word) > width:                 # a URL or a blob with no spaces
            if line:                             # flush first, or the chunks
                out.append(line)                 # jump ahead of pending text
                line = ""
            out.append(word[:width])
            word = word[width:]
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


CREDIT_X = "@thatismyquant"       # the bot's author, on X
CREDIT_TG = "t.me/thatsmyquantpublic"   # the Telegram group it was shared in


def _panel(cfg: Config, pnl: Tally, mkt: Decimal | None, st: State, inst: Instrument,
           budget: Decimal, mode: str, last: str, elapsed: float,
           now: datetime, fancy: bool = False) -> str:
    W = 58
    vol = pnl.volume
    per_min = vol / Decimal(str(max(elapsed / 60, 0.5)))
    cost = -pnl.net
    per10k = cost / vol * 10000 if vol else Decimal(0)
    halt10k = budget * cfg.loss_cap_mult / vol * 10000 if vol else cfg.taker_bp_max * cfg.loss_cap_mult
    share = f" | {vol / mkt * 100:.1f}% tape" if mkt else ""
    drift = cost - pnl.fees
    invusd = st.total_base * st.ext_mid
    invfrac = float(invusd / cfg.inventory_band_usd)
    bid, ask = st.bids[0][0], st.asks[0][0]
    quotes = " ".join(x for x, sd in (("bid", "buy"), ("ask", "sell")) if sd in st.resting) or "idle"
    clock = f"{int(elapsed) // 3600:d}:{int(elapsed) % 3600 // 60:02d}:{int(elapsed) % 60:02d}"

    if fancy:
        DIM, BOLD, CYN, GRN, YEL, RED, R = ("\x1b[90m", "\x1b[1m", "\x1b[96m",
                                            "\x1b[92m", "\x1b[93m", "\x1b[91m", "\x1b[0m")
        TL, TR, BL, BR, H, V = "╭", "╮", "╰", "╯", "─", "│"
        ML, MR = "├", "┤"
        FULL, EMPTY = "█", "░"
    else:
        DIM = BOLD = CYN = GRN = YEL = RED = R = ""
        TL, TR, BL, BR, H, V = "+", "+", "+", "+", "-", "|"
        ML, MR = "+", "+"
        FULL, EMPTY = "#", "."

    hot = float(per10k) / float(halt10k) if halt10k else 0.0
    cost_c = GRN if hot < 0.6 else YEL if hot < 0.85 else RED
    inv_c = GRN if invfrac < 0.6 else YEL if invfrac < 0.9 else RED
    g = max(0, min(10, int(round(invfrac * 10))))
    gauge = FULL * g + EMPTY * (10 - g)

    def vis(txt: str) -> int:
        return len(re.sub("\x1b" + r"\[[0-9;]*m", "", txt))

    def row(txt: str) -> str:
        return f"{DIM}{V}{R} " + txt + " " * max(0, W - vis(txt) - 3) + f"{DIM}{V}{R}"

    wrapped = _wrap(last, W - 13) or [""]
    last_rows = ([row(f"{DIM}last{R}     {wrapped[0]}")]
                 + [row(" " * 9 + w) for w in wrapped[1:8]])

    title = f"{BOLD}{CYN}{cfg.symbol} FARM{R}"
    state = f"{GRN if 'running' in mode.lower() else YEL}{mode.upper()}{R}"
    lines = [
        f"{DIM}{TL}{H * (W - 2)}{TR}{R}",
        row(f"{BOLD}{CYN}{CREDIT_X}{R}{DIM} on X{R}   {DIM}{V}{R}   "
            f"{BOLD}{CREDIT_TG}{R}"),
        f"{DIM}{ML}{H * (W - 2)}{MR}{R}",
        row(f"{title}   {DIM}{now:%H:%M:%S} | up {clock}{R}"),
        row(f"{state}{DIM} | quoting {R}{quotes}"),
        row(""),
        row(f"{DIM}volume{R}   {BOLD}${vol:>10,.0f}{R}   ${per_min:,.0f}/min{DIM}{share}{R}"),
        row(f"{DIM}cost{R}     {cost_c}${cost:>10,.2f}{R}   {cost_c}${per10k:.2f}{R}/$10k  {DIM}halt ${halt10k:.2f}{R}"),
        row(f"{DIM}         fees ${pnl.fees:,.2f} | drift ${drift:+,.2f}{R}"),
        row(f"{DIM}inv{R}      {inv_c}${invusd:>10,.0f}{R}   {inv_c}{gauge}{R} {DIM}of ${cfg.inventory_band_usd:,.0f}{R}"),
        row(""),
        row(f"{DIM}book{R}     {bid} {DIM}/{R} {ask}  {DIM}{int((ask - bid) / inst.tick)}t{R}"),
        *last_rows,
        f"{DIM}{BL}{H * (W - 2)}{BR}{R}",
    ]
    return "\n".join(lines)


def _final_report(pnl: Tally, marked: bool, total_base: Decimal | None) -> str:
    per10k = -pnl.net / pnl.volume * 10000 if pnl.volume else Decimal(0)
    held = "unknown" if total_base is None else f"{total_base.normalize():f}"
    note = "" if marked else " (unmarked: no exit price seen)"
    return (f"\n FINAL   volume ${pnl.volume:,.2f}   net ${pnl.net:+,.4f}"
            f"   (${per10k:.2f}/$10k){note}\n"
            f"         fees ${pnl.fees:,.4f}   fills {pnl.n_fills} ({pnl.n_maker} maker)"
            f"   base left {held}")


# ------------------------------------------------------------------ IO shell


def _vt() -> bool:
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        if not k.SetConsoleMode(h, mode.value | 4):
            return False
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        return True
    except Exception:
        return False


def _can_utf8() -> bool:
    try:
        "╭│├█░".encode(getattr(sys.stdout, "encoding", "") or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


_AUTH_OK = False


def _diagnose_401(method: str, path: str, detail: str) -> str:
    """Name the ONE cause that fits, from whether a private call ever succeeded.

    Both causes return the same HTTP 401, and telling a user to fix the wrong
    one costs them the session. The discriminator is free: reads and writes are
    signed identically, so a key that has already read cannot be mis-signed.
    """
    head = (f"OKX refused {method} {path} with HTTP 401.\n"
            f"  OKX said: {detail}\n")
    if _AUTH_OK:
        return head + (
            "  An earlier private call with this same key SUCCEEDED, so the key,\n"
            "  secret, passphrase, clock and IP allowlist are all correct.\n"
            "  What is missing is permission:\n"
            "    OKX > Profile > API > your key > tick TRADE (leave Withdraw off).\n"
            "    A read-only key reads balances but cannot place orders.\n"
            "  If you just ticked it, wait a minute and start again.")
    return head + (
        "  This is the FIRST private call, and it failed - so the problem is\n"
        "  the credentials or where you are calling from, not permissions:\n"
        "    1. IP allowlist: if the key has one, it must list the IPv4 you\n"
        "       run from. This bot always connects over IPv4, so read it\n"
        "       at https://api.ipify.org - a page that answers in IPv6\n"
        "       shows an address that can never match an IPv4 entry.\n"
        "    2. API_KEY / API_SECRET / API_PASSPHRASE: retype all three; the\n"
        "       passphrase is the one you chose, not your login password.\n"
        "    3. HOST: an eea.okx.com key does not work on www.okx.com, and\n"
        "       vice versa - they are separate accounts.")


def _request(cfg: Config, method: str, path: str, body: dict | None = None,
             private: bool = True) -> list:
    payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if private:
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
        digest = hmac.new(cfg.secret.encode(), (ts + method + path + payload).encode(),
                          hashlib.sha256).digest()
        headers.update({"OK-ACCESS-KEY": cfg.key, "OK-ACCESS-SIGN": base64.b64encode(digest).decode(),
                        "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": cfg.passphrase})
    req = urllib.request.Request(cfg.host + path, data=payload.encode() if payload else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            parsed = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:200]
        # 401 is never transient, and retrying it just spins forever. Which of
        # the two causes it is depends entirely on whether ANY private call has
        # worked yet: the same signature covers reads and writes, so a key that
        # has read your balance can only be failing on permission.
        if e.code == 401:
            raise PermissionError(_diagnose_401(method, path, detail)) from e
        raise RuntimeError(f"{method} {path} HTTP {e.code}: {detail}") from e
    except Exception as e:
        msg = f"{method} {path} failed: {type(e).__name__} {e}"
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            msg += " (Mac python.org install: run /Applications/Python 3.x/Install Certificates.command)"
        raise RuntimeError(msg) from e
    if private:
        global _AUTH_OK
        _AUTH_OK = True
    if not isinstance(parsed, dict) or parsed.get("code") not in ("0", 0):
        raise RuntimeError(f"{method} {path} okx error: {str(parsed)[:200]}")
    data = parsed.get("data")
    if not isinstance(data, list):
        raise ValueError(f"{method} {path} returned no data array: {str(parsed)[:160]}")
    return data


def _instrument(cfg: Config) -> Instrument:
    d = _request(cfg, "GET", f"/api/v5/public/instruments?instType=SPOT&instId={cfg.symbol}",
                 private=False)
    if not d or not isinstance(d[0], dict):
        raise ValueError(f"{cfg.symbol} is not a live spot instrument on {cfg.host}")
    return Instrument(_dec(d[0], "tickSz", "tickSz"), _dec(d[0], "lotSz", "lotSz"),
                      _dec(d[0], "minSz", "minSz"))


def _live_fees(cfg: Config) -> Fees:
    d = _request(cfg, "GET", f"/api/v5/account/trade-fee?instType=SPOT&instId={cfg.symbol}")
    if not d or not isinstance(d[0], dict):
        raise ValueError(f"trade-fee returned nothing usable: {str(d)[:160]}")
    return Fees(-_dec(d[0], "maker", "maker fee") * 10000, -_dec(d[0], "taker", "taker fee") * 10000)


def _order_book(cfg: Config) -> tuple[list[Level], list[Level]]:
    d = _request(cfg, "GET", f"/api/v5/market/books?instId={cfg.symbol}&sz=20", private=False)
    if not d or not isinstance(d[0], dict):
        return [], []

    def side(rows) -> list[Level]:
        return [(Decimal(str(r[0])), Decimal(str(r[1]))) for r in rows]

    try:
        return side(d[0]["bids"]), side(d[0]["asks"])
    except (KeyError, IndexError, TypeError, InvalidOperation) as e:
        raise ValueError(f"OKX book for {cfg.symbol} is malformed: {str(d)[:160]}") from e


def _open_orders(cfg: Config) -> list[Resting]:
    d = _request(cfg, "GET", f"/api/v5/trade/orders-pending?instType=SPOT&instId={cfg.symbol}")
    out = []
    for o in d:
        if not isinstance(o, dict):
            continue
        if not str(o.get("clOrdId", "")).startswith(cfg.tag):
            continue
        side = o.get("side")
        if side not in ("buy", "sell") or not o.get("ordId"):
            raise ValueError(f"OKX pending order is unusable: {str(o)[:160]}")
        out.append(Resting(side, str(o["ordId"]), _dec(o, "px", "order px"),
                           _dec(o, "sz", "order sz") - _dec(o, "accFillSz", "accFillSz", Decimal(0))))
    return out


def _balances(cfg: Config) -> Balances:
    d = _request(cfg, "GET", f"/api/v5/account/balance?ccy={cfg.base_ccy},{cfg.quote_ccy}")
    if not d or not isinstance(d[0], dict) or "details" not in d[0]:
        raise ValueError(f"OKX balance response has no details for {cfg.base_ccy}/{cfg.quote_ccy}; "
                         f"cannot distinguish a zero balance from a missing one: {str(d)[:160]}")
    avail: dict[str, Decimal] = {}
    owned: dict[str, Decimal] = {}
    for det in d[0]["details"]:
        ccy = det.get("ccy") if isinstance(det, dict) else None
        if ccy not in (cfg.base_ccy, cfg.quote_ccy):
            continue
        free = max(Decimal(0), _dec(det, "availBal", f"{ccy} availBal", Decimal(0)))
        avail[ccy] = free
        owned[ccy] = _dec(det, "cashBal", f"{ccy} cashBal", free)
    return Balances(avail.get(cfg.base_ccy, Decimal(0)), owned.get(cfg.base_ccy, Decimal(0)),
                    avail.get(cfg.quote_ccy, Decimal(0)))


def _fills(cfg: Config) -> list[dict]:
    out = []
    for f in _request(cfg, "GET",
                      f"/api/v5/trade/fills?instType=SPOT&instId={cfg.symbol}&limit=100"):
        if not isinstance(f, dict):
            raise ValueError(f"OKX fill is not an object: {str(f)[:160]}")
        if not f.get("tradeId"):
            raise ValueError(f"OKX fill has no tradeId, cannot deduplicate it: {str(f)[:160]}")
        if f.get("side") not in ("buy", "sell"):
            raise ValueError(f"OKX fill {f.get('tradeId')} has side={f.get('side')!r}")
        _dec(f, "fillPx", "fillPx")
        _dec(f, "fillSz", "fillSz")
        out.append(f)
    return out


def _place(cfg: Config, side: str, price: Decimal, size: Decimal, ord_type: str) -> None:
    if side not in ("buy", "sell") or ord_type not in ("post_only", "ioc"):
        raise ValueError(f"refusing order: side={side!r} ord_type={ord_type!r}")
    if (not (isinstance(price, Decimal) and isinstance(size, Decimal))
            or not (price.is_finite() and size.is_finite()) or price <= 0 or size <= 0):
        raise ValueError(f"refusing order {side} {size}@{price}: "
                         f"price and size must be positive and finite")
    body = {"instId": cfg.symbol, "tdMode": "cash", "side": side, "ordType": ord_type,
            "px": str(price), "sz": str(size),
            # account-wide STP is mandatory; if a bug ever crosses our own quote,
            # cancel_taker kills the taker, not the resting maker.
            "stpMode": "cancel_taker",
            "clOrdId": f"{cfg.run_tag or cfg.tag}{int(time.time() * 1000) % 10**10}"}
    d = _request(cfg, "POST", "/api/v5/trade/order", body)
    if not d or not isinstance(d[0], dict):
        raise RuntimeError(f"{side} {size}@{price}: no usable response: {str(d)[:120]}")
    if d[0].get("sCode") not in ("0", 0):
        raise RuntimeError(f"{side} {size}@{price}: {d[0].get('sMsg')}")


def _cancel(cfg: Config, ord_id: str) -> None:
    try:
        _request(cfg, "POST", "/api/v5/trade/cancel-order",
                 {"instId": cfg.symbol, "ordId": ord_id})
    except RuntimeError as e:
        if not any(c in str(e) for c in _ORDER_ALREADY_GONE):
            raise


def _cancel_symbol(cfg: Config) -> None:
    d = _request(cfg, "GET", f"/api/v5/trade/orders-pending?instType=SPOT&instId={cfg.symbol}")
    failed = None
    for o in d:
        if not isinstance(o, dict) or not o.get("ordId"):
            continue
        try:
            _cancel(cfg, str(o["ordId"]))
        except (RuntimeError, ValueError, AttributeError, TypeError) as e:
            failed = e
    if failed is not None:
        raise RuntimeError(f"some orders survived cancellation: {failed}")


def _cancel_all(cfg: Config) -> None:
    failed = None
    for o in _open_orders(cfg):
        try:
            _cancel(cfg, o.ord_id)
        except (RuntimeError, ValueError, AttributeError, TypeError) as e:
            failed = e
    if failed is not None:
        raise RuntimeError(f"some orders survived cancellation: {failed}")


def _market_volume(cfg: Config) -> dict[int, Decimal] | None:
    try:
        d = _request(cfg, "GET", f"/api/v5/market/candles?instId={cfg.symbol}&bar=1m&limit=100",
                     private=False)
    except (RuntimeError, ValueError):
        return None
    out: dict[int, Decimal] = {}
    for c in d:
        if not isinstance(c, list) or len(c) < 8:
            continue
        try:
            ts, vol = int(c[0]), Decimal(str(c[7]))
        except (ValueError, TypeError, InvalidOperation):
            continue
        if vol.is_finite():
            out[ts] = vol
    return out


def _read_state(cfg: Config) -> State | None:
    bids, asks = _order_book(cfg)
    if not bids or not asks or asks[0][0] < bids[0][0]:
        return None
    return _build_state(bids, asks, _balances(cfg), _open_orders(cfg))


# --------------------------------------------------------------- orchestration

def _collect(cfg: Config, seen: set[str], ledger: list[dict]) -> None:
    for f in _fills(cfg):
        if f["tradeId"] not in seen:
            seen.add(f["tradeId"])
            ledger.append(f)


def _quietly(fn, *args) -> bool:
    # Runs while real orders may be resting: nothing here may propagate, including
    # a second Ctrl+C from a user mashing keys at an apparently frozen screen.
    # Escaping this path leaves live orders on the book and prints no report.
    try:
        fn(*args)
        return True
    except _SWALLOW:
        return False


def _unwind_step(cfg: Config, inst: Instrument, seen: set[str], ledger: list[dict],
                 baseline: Decimal) -> bool:
    st = _read_state(cfg)
    if st is None:
        return False
    st = st._replace(total_base=max(Decimal(0), st.total_base - baseline))
    _collect(cfg, seen, ledger)
    if st.total_base < inst.min_sz:
        return True
    action = _decide(st, inst, cfg, reduce_only=True)
    if action.kind == "quote":
        _place(cfg, action.side, action.price, action.size, "post_only")
    elif action.kind == "cancel":
        _cancel(cfg, action.ord_id)
    return False


def _unwind(cfg: Config, inst: Instrument, seen: set[str], ledger: list[dict],
            baseline: Decimal) -> None:
    flat = False
    _quietly(_cancel_all, cfg)
    deadline = time.time() + cfg.unwind_s
    while time.time() < deadline and not flat:
        try:
            flat = _unwind_step(cfg, inst, seen, ledger, baseline)
        except _SWALLOW:
            pass
        _quietly(time.sleep, cfg.poll_s)
    for _ in range(3):
        if _quietly(_cancel_all, cfg):
            break
        _quietly(time.sleep, 1)
    _quietly(_collect, cfg, seen, ledger)


_LIVE: list = []


def _arm_exit_guard(cfg: Config) -> None:
    """Cancel this symbol's orders on any exit the interpreter still controls.

    Ctrl+C was already handled by run()'s finally, but three exits were not:
    a SIGTERM, a Ctrl+Break, and any path where the unwind itself dies after
    re-placing a reducing order. Those left live orders on the book. This is a
    last-resort net, not a replacement: it runs after the normal unwind and
    usually finds nothing to do.
    """
    _LIVE.append(cfg)

    def sweep() -> None:
        for c in _LIVE:
            _quietly(_cancel_symbol, c)
        _LIVE.clear()

    atexit.register(sweep)
    for name in ("SIGTERM", "SIGBREAK", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue                      # not every signal exists on every OS
        try:
            # turn the signal into the KeyboardInterrupt run() already handles,
            # so the full unwind runs rather than just this bare sweep
            signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        except (ValueError, OSError):
            pass                          # not the main thread, or not supported


def run(cfg: Config) -> None:
    cfg = cfg._replace(run_tag=f"{cfg.tag}{datetime.now():%d%H%M%S}")
    _arm_exit_guard(cfg)
    fancy = sys.stdout.isatty() and (os.name != "nt" or _vt()) and _can_utf8()
    print(f"session order-id prefix: {cfg.run_tag}")
    last_stat = 0.0
    inst = _instrument(cfg)
    fees = _live_fees(cfg)
    if fees.maker_bp > cfg.maker_bp_max or fees.taker_bp > cfg.taker_bp_max:
        raise ValueError(f"account fees are maker {fees.maker_bp}bp / taker {fees.taker_bp}bp, worse "
                         f"than the configured {cfg.maker_bp_max}/{cfg.taker_bp_max}; raise them or stop")
    _quietly(_cancel_symbol, cfg)  # clean slate on this asset: any resting regular order goes
    baseline_base = _balances(cfg).owned
    if baseline_base >= inst.min_sz:
        print(f" existing {baseline_base:f} {cfg.base_ccy} treated as YOURS - the bot only "
              f"trades what it buys itself")
    if cfg.quiet_flow_per_min == 0:
        try:
            d = _request(cfg, "GET", f"/api/v5/market/candles?instId={cfg.symbol}&bar=1H&limit=24",
                         private=False)
            day = sum(Decimal(str(c[7])) for c in d if isinstance(c, list) and len(c) >= 8)
        except (RuntimeError, ValueError, InvalidOperation):
            day = Decimal(0)
        auto = (day / 1440 * 2 if day > 0 else Decimal(3000)).quantize(Decimal(1))
        cfg = cfg._replace(quiet_flow_per_min=auto)
        print(f" flow threshold auto-set to ${auto:,}/min (2x this symbol's 24h average)")
    started = time.time()
    started_ms = int(started * 1000)
    seen = {f["tradeId"] for f in _fills(cfg)}
    ledger: list[dict] = []
    pnl = _tally(ledger, Decimal(1), cfg.base_ccy)
    mkt: Decimal | None = None
    budget = Decimal(0)
    last_mid = Decimal(0)
    cycles = fails = empty = 0
    last, mode = "starting", "RUNNING"
    candle_vols: dict[int, Decimal] = {}
    clip_eff = cfg.clip_usd_quiet
    try:
        while True:
            st = None
            try:
                st = _read_state(cfg)
                if st is not None:
                    st = st._replace(total_base=max(Decimal(0), st.total_base - baseline_base))
                    _collect(cfg, seen, ledger)
                    pnl = _tally(ledger, st.ext_mid, cfg.base_ccy)
                    budget = _budget(pnl.volume, cfg.taker_bp_max, inst.tick, st.ext_mid)
                    cycles += 1
                    if cycles % 10 == 1:
                        fetched = _market_volume(cfg)
                        if fetched is not None:
                            candle_vols.update(fetched)
                            mkt = sum((v for t, v in candle_vols.items()
                                       if t >= started_ms), Decimal(0))
                            flow = sum((v for t, v in candle_vols.items()
                                        if t >= int((time.time() - 300) * 1000)), Decimal(0)) / 5
                            if flow >= cfg.quiet_flow_per_min:
                                clip_eff = cfg.clip_usd
                            elif flow < cfg.quiet_flow_per_min * Decimal("0.6"):
                                clip_eff = cfg.clip_usd_quiet
            except (RuntimeError, ValueError) as e:
                fails += 1
                last = f"! {str(e)[:56]}"
                if fails >= 10:
                    raise RuntimeError(f"10 consecutive exchange failures, last: {e}") from e
                time.sleep(cfg.poll_s)
                continue
            fails = 0
            if st is None:
                empty += 1
                if empty * cfg.poll_s > 120:
                    raise ValueError(f"{cfg.symbol} has had no usable book for 120s on {cfg.host}; "
                                     f"halting and unwinding")
                time.sleep(cfg.poll_s)
                continue
            empty = 0
            last_mid = st.ext_mid
            active = cfg._replace(clip_usd=clip_eff)
            elapsed = time.time() - started
            reason = _halt_reason(pnl, budget, elapsed, cfg)
            mode = "SELLING TO FLAT" if reason else f"RUNNING clip ${int(clip_eff)}"
            body = _panel(cfg, pnl, mkt, st, inst, budget, mode, last,
                          elapsed, datetime.now(), fancy)
            if fancy:
                print("\x1b[H\x1b[2J" + body, flush=True)
            elif cycles % 30 == 1:
                print(body, flush=True)
            if time.time() - last_stat >= 60:
                last_stat = time.time()
            if reason:
                print(f" stopping: {reason}")
                if "loss cap" in reason:
                    for ln in (" The session loss passed LOSS_CAP_MULT x the expected cost -",
                               " usually a trending market marking inventory down, not a bug.",
                               " To restart: run `python bot.py` again (it cancels this asset's",
                               " open orders and starts a FRESH loss budget - the cap does not",
                               " carry over). Better: wait for calmer or busier tape first."):
                        print(ln)
                break
            action = _decide(st, inst, active, reduce_only=False)
            try:
                if action.kind == "quote":
                    _place(cfg, action.side, action.price, action.size, "post_only")
                    last = f"quote {action.side} {action.size} @ {action.price}"
                elif action.kind == "cancel":
                    _cancel(cfg, action.ord_id)
                    last = f"cancel {action.side} @ {action.price} ({action.why})"
                else:
                    last = f"wait ({action.why})"
            except (RuntimeError, ValueError) as e:
                # the panel truncates to 56 columns, which cuts the exchange's
                # own reason off; run.log keeps it so a failure can be diagnosed
                last = f"! {str(e)[:56]}"
            # one action per pass, but only pause when there is nothing to do:
            # after placing or cancelling, the other side usually needs work too,
            # and sleeping a full poll there leaves the book half-quoted.
            time.sleep(cfg.poll_s if action.kind == "wait" else BUSY_PAUSE_SECONDS)
    except KeyboardInterrupt:
        last = "interrupted"
    finally:
        _unwind(cfg, inst, seen, ledger, baseline_base)
        mark = last_mid
        try:
            bids, _ = _order_book(cfg)
            if bids:
                mark = bids[0][0]
        except _SWALLOW:
            pass
        total_base = None
        try:
            total_base = _balances(cfg).owned
        except _SWALLOW:
            pass
        if mark > 0:
            try:
                pnl = _tally(ledger, mark, cfg.base_ccy)
            except _SWALLOW:
                pass
        report = _final_report(pnl, mark > 0, total_base)
        print(report)


def _selfcheck() -> None:
    tick = Decimal("0.0001")
    assert _quote_prices(Decimal("1.0000"), Decimal("1.0003"), tick) == (Decimal("1.0001"), Decimal("1.0002"))
    assert _quote_prices(Decimal("1.0000"), Decimal("1.0002"), tick) == (Decimal("1.0000"),
                                                                         Decimal("1.0002"))
    assert _quote_prices(Decimal("1.0000"), Decimal("1.0001"), tick) == (Decimal("1.0000"),
                                                                         Decimal("1.0001"))
    assert _floor_lot(Decimal("7.9"), Decimal(1)) == Decimal(7)
    assert _external_book([(Decimal(1), Decimal(5))], {Decimal(1): Decimal(5)}) == []
    t = _tally([{"fillPx": "1", "fillSz": "10", "side": "buy", "fee": "-0.008",
                 "feeCcy": "RE", "execType": "M", "tradeId": "1"}], Decimal(1), "RE")
    assert t.net == Decimal("-0.008") and t.fees == Decimal("0.008")  # -0.016 = base fee double-counted


# =============================================================================
#  1. YOUR SETTINGS - the only things you MUST fill in
# =============================================================================

API_KEY = ""                     # OKX API key   (trade permission, NO withdrawal)
API_SECRET = ""                  # OKX API secret
API_PASSPHRASE = ""              # OKX API passphrase

SYMBOL = "RE-USDC"               # the spot pair to farm, BASE-QUOTE
TARGET_VOLUME_USD = 5000         # stop after this much volume (buys + sells count)

# =============================================================================
#  2. TUNING - safe defaults; change only if you know why
# =============================================================================

HOST = "https://eea.okx.com"     # OKX Europe; global accounts use https://www.okx.com
CLIP_USD = 20                    # quote size when the market is busy
CLIP_USD_QUIET = 10              # quote size when it is quiet
INVENTORY_BAND_USD = 100         # hard ceiling on how much base coin the bot may hold
BUSY_PAUSE_SECONDS = 0.15        # pause after an action (vs POLL when idle)
LOSS_CAP_MULT = 1.20             # halts at TAKER fee x this per $10k of volume;
                                 # set it just under what your reward pays per $10k
MAKER_BP_MAX = 8.0               # refuse to start if your real maker fee is worse
TAKER_BP_MAX = 10.0              # than this. Read yours at OKX > Fees, or let the
                                 # bot tell you: it prints both at startup.

if __name__ == "__main__":
    _selfcheck()
    try:
        cfg = _config(host=HOST, symbol=SYMBOL, api_key=API_KEY, api_secret=API_SECRET,
                      api_passphrase=API_PASSPHRASE,
                      clip_usd=CLIP_USD, target_volume_usd=TARGET_VOLUME_USD,
                      inventory_band_usd=INVENTORY_BAND_USD, loss_cap_mult=LOSS_CAP_MULT,
                      clip_usd_quiet=CLIP_USD_QUIET,
                      maker_bp_max=MAKER_BP_MAX, taker_bp_max=TAKER_BP_MAX)
        if sys.stdin.isatty() and os.environ.get("OKX_FARM_YES", "").strip() != "1":
            fee_est = cfg.target_volume_usd * cfg.maker_bp_max / 10000
            cap_est = cfg.target_volume_usd * cfg.taker_bp_max / 10000 * cfg.loss_cap_mult
            print(f"LIVE on {cfg.symbol} ({cfg.host}) toward ${cfg.target_volume_usd:,.0f} volume;"
                  f" expected fees up to ~${fee_est:,.0f}, hard halt near ${cap_est:,.0f}.")
            try:
                ok = input("Type YES to start: ").strip()
            except (EOFError, KeyboardInterrupt):
                ok = ""
            # accept yes/Yes/YES: a case-sensitive gate reads as a broken bot to
            # someone who has not seen the code, and they give up before they
            # ever find out whether their API keys work
            if ok.upper() != "YES":
                raise SystemExit("aborted")
        run(cfg)
    except PermissionError as e:
        raise SystemExit(f"\nSTOPPED - {e}")
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f"error: {e}")
