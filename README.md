# TRENCHES XDROPS FARM

RISK TAKER FOR LIFE 

*Version française : [README_FR.md](README_FR.md)*


Quotes a post-only bid and ask one tick apart on one OKX spot pair, lets others
fill them, and sells back to flat. Spot cash only: it never borrows, never
shorts, never spends more than the free balance it sees, and never takes
liquidity while running. One file, standard library only.

## What it costs

You pay the maker fee on every dollar of volume plus roughly half a tick of
price drift per round trip. At an 8bp maker fee that is about $8-10 per $10,000
traded. At the default `TARGET_VOLUME_USD = 5000` expect roughly $4-5
total. The bot hard-halts if realised loss exceeds
`LOSS_CAP_MULT x` the priced budget (~$6 at defaults); the cap arms only
after 4x `INVENTORY_BAND_USD` of volume has traded. Defaults are deliberately
small: at most $140 is ever exposed (two quotes plus the inventory band). Raise
the sizes only once you have watched a full run.

Check your pair's minimum order size before shrinking `CLIP_USD` further - a
clip below it means the bot can never quote.

## Requirements

Everything you need lives in ONE folder: `windows/`, `mac/` or `linux/` (bot +
installer + launcher).

Python 3.9 or newer. Windows: double-click **`windows\INSTALL.bat`** - it
checks Python and installs it if missing. Mac: double-click **`mac/install.command`**
(right-click > Open the first time; macOS blocks unknown files on plain double-click).
Linux: run **`bash linux/install.sh`** - it installs Python through apt, dnf,
pacman, zypper or apk, whichever your distribution has.
Nothing else - no pip installs.

## Create an OKX API key

- EEA (European) accounts: eea.okx.com -> Profile -> API keys.
- All other accounts: www.okx.com, and change `HOST` at the bottom of bot.py to
  `https://www.okx.com`. An EEA key does not work against www and vice versa.
- Permissions: **Read + Trade only. Never enable Withdraw.**
- Set an IP allowlist on the key (your own IP) if you can.

## Configure

Open the bot.py **inside your folder** (`windows/`, `mac/` or `linux/`), scroll to the
bottom banner `1. YOUR SETTINGS`, and paste your
three values into `API_KEY` / `API_SECRET` / `API_PASSPHRASE`. The keys never
leave this file on your machine.

Then set, in the same place:

| Knob | Meaning |
|---|---|
| `SYMBOL` | spot pair, BASE-QUOTE, e.g. `GRVT-USDC`. Check which pair the campaign actually counts - on RE only the USDC pair earned, USDT volume counted for nothing |
| `HOST` | `eea.okx.com` (European account) or `www.okx.com` (global) - separate namespaces, a key from one does not work on the other |
| `TARGET_VOLUME_USD` | stop after this much volume (buy + sell legs both count) |
| `CLIP_USD` | size of one quote; fund ~2x this in the quote currency to hold both sides |
| `CLIP_USD_QUIET` | smaller clip used when the tape is quiet (the busy/quiet threshold self-measures from the symbol's 24h tape) |
| `INVENTORY_BAND_USD` | hard ceiling on directional exposure; buying tapers to zero at it |
| `BUSY_PAUSE_SECONDS` | pause after placing or cancelling; the full `POLL` wait only applies when idle |
| `LOSS_CAP_MULT` | the halt line in $ per $10k of volume: `REFUSE_IF_TAKER_BP_OVER x this`. At 10.0 x 1.20 it stops near $12/$10k. Set it just under what your reward pays per $10k |
| `REFUSE_IF_MAKER_BP_OVER` | **not your fees.** The bot reads your real tier from OKX at startup and refuses to run if it is worse than this |
| `REFUSE_IF_TAKER_BP_OVER` | same on the taker side. Also the base of the halt line above |

Fund the account with at least 2x `CLIP_USD` of the quote currency plus
`INVENTORY_BAND_USD` of headroom.

The bot restates all of it before asking for `YES`: target volume, quote size,
expected fees and the halt line, each also given as **$ per $10k of volume** -
the only unit comparable to what a campaign pays.

Five finer settings (`WARMUP_VOLUME_USD`, `SPEND_FRACTION`,
`MAX_RUNTIME_SECONDS`, `UNWIND_SECONDS`, `QUIET_FLOW_USD_PER_MIN`) keep their
defaults inside `_config`, whose docstring documents each one, its contract and
its value. The one that surprises people most: the loss cap only arms after
**4x `INVENTORY_BAND_USD`** of volume, so a bot losing in its first minutes
will not halt straight away - deliberately, since early PnL is one lucky or
unlucky fill rather than signal.

## Run

- Windows: double-click **`windows\START.bat`**.
- Mac: double-click **`mac/start.command`** (or `python3 bot.py` from Terminal).
- Linux: run **`./linux/start.sh`** (or `python3 bot.py`).

It prints the preflight - target, quote sizes, expected fees and halt line, each
in $ and in $ per $10k - and asks you to type `YES` before placing any order
(skipped when input is not a terminal, so headless deploys don't hang).

## The panel

- `volume` - session volume done, $/min pace, and your share of the tape
- `cost` - realised loss so far, $ per $10k traded, and the halt line
- `fees / drift` - fee component vs price-drift component of the cost
- `inv` - current inventory in $ against `INVENTORY_BAND_USD`
- `book` - best bid/ask and the spread in ticks
- `last` - the last action or error

The bot writes no files: everything, errors included, stays in this panel.
A long exchange error wraps across the `last` rows instead of being cut off.

## Stop safely

Press Ctrl+C ONCE. The bot cancels its orders and sells back to flat
(post-only) for up to `UNWIND_SECONDS`, then prints a final report including
`base left` (anything it could not sell). Extra Ctrl+C presses are absorbed on
purpose - do not mash keys at an apparently frozen screen.

Orders are also cancelled on any other exit the interpreter still controls: an
unhandled error, a plain `exit`, or a SIGTERM on Mac and Linux. The one case
nothing can cover is a hard kill (`taskkill /F`, End Task, a power cut) - there
the process dies without running any code, and the orders stay until you start
the bot again, which cancels them.

## When it stops by itself

target reached | loss cap (after warmup) |
10 consecutive exchange failures | no usable order book for 120s | account
fees worse than `REFUSE_IF_MAKER_BP_OVER` / `REFUSE_IF_TAKER_BP_OVER` at
startup.

## Shared state & assumptions (read before running on a funded account)

1. **The bot only trades what it buys itself.** At startup it snapshots your
   existing base-coin balance as a baseline and never sells below it. Coins you
   held before are yours; the bot manages only its own inventory on top.
2. **One bot per account + symbol.** Starting it cancels ALL open regular
   orders on the symbol (a clean slate; TP/SL, grid-bot and other algo orders
   are not touched). Two instances on one account will cancel each other's
   quotes. More generally: never point this bot at a base asset that any other
   bot (grid, DCA) or your own manual trading also touches - their fills would
   be mistaken for this bot's inventory. Other pairs on the same account are
   fine; they only share the quote-currency wallet.
3. **Fees:** built for VIP0/VIP1 (8bp maker / 10bp taker). It reads your real
   tier at startup and refuses to run if yours is worse than
   REFUSE_IF_MAKER_BP_OVER / REFUSE_IF_TAKER_BP_OVER. Those two are guards,
   not fee settings - raising them does not make trading cheaper, it only
   removes the alarm.
4. **Restarting resets the loss cap.** PnL, volume and the halt budget are
   per-session. Restarting after a halt arms a fresh loss budget - do not
   restart repeatedly into a market that keeps halting you.
5. **A halt is a feature.** When it stops on the loss cap it prints why and
   what to do; the usual cause is a trending market, and the usual fix is
   waiting for calmer or busier tape.
6. **LOSS_CAP_MULT encodes your reward math.** This bot deliberately pays
   maker fees (~$8 per $10,000 traded at 8bp) to print volume. It only makes
   sense when something (a campaign, a rebate) pays you more per $10k than the
   halt line. Set LOSS_CAP_MULT so that halt = what your reward is worth:
   halt $/10k = REFUSE_IF_TAKER_BP_OVER x LOSS_CAP_MULT.
7. **Calibration is per-symbol and automatic where possible.** The quiet/hot
   flow threshold auto-measures from the symbol's own 24h tape at startup
   automatically. Clip sizes and the inventory band are yours
   to size against your wallet.
8. **Host matters.** Default is OKX Europe (eea.okx.com). A global OKX account
   lives on www.okx.com - a different account namespace; set HOST accordingly.

## Troubleshooting

- `50102 Timestamp request expired`: sync your computer clock. (The bot already
  forces IPv4 because an IPv6 stall to eea.okx.com causes this same error.)
- Mac `CERTIFICATE_VERIFY_FAILED`: run
  `/Applications/Python 3.x/Install Certificates.command` (python.org installs).
- `fill in API_KEY / API_SECRET / API_PASSPHRASE`: the keys at the bottom of
  bot.py are still empty.
- HTTP 401 **when placing an order, after the panel already appeared**: the key
  is valid but read-only. Startup reads your fees and balance with the same
  signature, so if it got that far, tick **Trade** on the key (OKX > Profile >
  API). The bot now stops and says so instead of retrying forever.
- HTTP 401 **at startup**: key/secret/passphrase mismatch, an EEA key used
  against www.okx.com (or vice versa), or an IP allowlist that does not list
  you. The bot connects over **IPv4**, so compare the key's allowlist against
  <https://api.ipify.org> - a page answering in IPv6 shows an address that can
  never match an IPv4 entry.
