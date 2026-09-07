# LineupIQ

DraftKings NBA lineup optimizer that finds the salary-cap-optimal roster using mixed-integer programming.

Supports **Captain (Showdown)** and **Classic** contest modes. Player data is fetched live from the DraftKings API.

## Setup

Requires Python 3.12+. Uses [Gurobi](https://www.gurobi.com/) when available (free academic license), otherwise falls back to the bundled CBC solver.

```bash
uv sync
```

Dependencies (`pandas`, `python-mip`, `tabulate`, `requests`, `nba_api`) are declared in `pyproject.toml` and installed automatically.

## Usage

### Auto-detect mode and optimize

```bash
uv run main.py
```

When `--mode` is omitted, the tool fetches the DraftKings lobby and picks a mode automatically: **Captain** if any Showdown slates exist, otherwise **Classic**.

### Specify a mode

```bash
# Captain (Showdown) mode — 1 CPT + 5 UTIL, $50k cap
uv run main.py --mode Captain

# Classic NBA mode — 8 players with position constraints, $50k cap
uv run main.py --mode Classic
```

The tool auto-selects a slate: for Captain it prefers single-game Showdown, for Classic it picks the largest slate. A direct DraftKings lobby link is printed for the selected slate. To pick a different draft group:

```bash
uv run main.py --mode Classic --draft-group 12345
```

### Exclude specific players

```bash
uv run main.py --mode Captain --players-out "LeBron James" "Stephen Curry"
```

### Weight projections by matchup

```bash
uv run main.py --oprk-weight 0.1
```

Scales each player's FPPG by DraftKings' Opponent Rank (OPRK): easier matchups get a boost, tougher ones a penalty. Default `0` (off).

### Review results and track accuracy

Every optimized lineup is saved to `history.csv`. After the games finish:

```bash
uv run main.py --review   # fetch actual box scores (nba_api) for all pending lineups
uv run main.py --stats    # projected vs. actual summary across all reviewed lineups
```

### List available draft groups (debug)

```bash
uv run main.py --list-draft-groups
```

Prints all NBA draft groups with their GameTypeId and game count.

## Contest Modes

### Captain (Showdown)

Pick 6 players from a single-game pool:

- 1 **Captain** — earns 1.5x fantasy points, costs 1.5x salary
- 5 **UTIL** — standard points and salary
- Salary cap: **$50,000**
- A player cannot fill both Captain and UTIL
- Lineup must include players from both teams

### Classic NBA

Fill 8 roster slots, each with exactly one eligible player:

| Slot | Eligible positions |
|------|--------------------|
| PG   | PG                 |
| SG   | SG                 |
| SF   | SF                 |
| PF   | PF                 |
| C    | C                  |
| G    | PG, SG             |
| F    | SF, PF             |
| UTIL | any                |

- Salary cap: **$50,000**
- Dual-position players (e.g. PG/SG) are eligible for all listed positions
- Lineup must include players from at least 2 different games

## How It Works

Both modes are formulated as binary integer programs and solved via `python-mip` (Gurobi when installed, CBC otherwise):

- **Decision variables**: binary (0/1) — whether each player is selected
- **Objective**: maximize total projected fantasy points (FPPG)
- **Constraints**: salary cap, player count, roster-slot assignment (Classic), mutual exclusion and 2-team minimum (Captain), 2-game minimum (Classic)

The solver finds a provably optimal lineup in under a second.

## Website (GitHub Pages)

Today's optimal lineup is published at **https://leelening.github.io/lineupiq/**.

A GitHub Actions workflow (`.github/workflows/pages.yml`) runs `site/build.py` twice a day (10:00 and 17:00 ET), on every push to `main`, and on demand via *Actions → Build & deploy LineupIQ site → Run workflow* (optionally with a mode / draft group). Each run does what the CLI does, unattended: it first fills in actual FPPG for any pending past lineups (`--review`, via `nba_api`), then solves today's slate with `main.py` and saves it to `history.csv`, commits `history.csv` back to `main` if it changed, and deploys `site/index.html` with `data/lineup.json` and `data/history.json` (the **History** tab, projected vs. actual per lineup). Pass `--no-save` / `--no-review` to `site/build.py` to skip the write-back. The page footer shows the optimizer version, derived automatically from git as `<pyproject version>.<commits touching main.py>+<sha>` — it changes only when the optimization code changes, not for README or site edits.

One-time setup: in the repo go to **Settings → Pages** and set *Source* to **GitHub Actions**.

To preview locally:

```bash
uv run site/build.py --out _site && cp site/index.html _site/ && python -m http.server -d _site 8000
```

## Project Structure

```
main.py              Optimizer (API client, solvers, history/review, CLI)
history.csv          Saved lineups with projected and actual FPPG
site/                GitHub Pages site (build.py + index.html)
.github/workflows/   Pages build & deploy workflow
pyproject.toml       Dependencies and project metadata
```

## License

MIT — see [LICENSE](LICENSE). For entertainment only; projections are DraftKings FPPG averages, not guarantees.
