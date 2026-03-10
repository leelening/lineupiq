# LineupIQ

DraftKings NBA lineup optimizer that finds the salary-cap-optimal roster using mixed-integer programming.

Supports **Captain (Showdown)** and **Classic** contest modes. Player data can be fetched live from the DraftKings API or loaded from a local CSV export.

## Setup

Requires Python 3.12+. Uses [Gurobi](https://www.gurobi.com/) when available (free academic license), otherwise falls back to the bundled CBC solver.

```bash
uv sync
```

Dependencies (`pandas`, `python-mip`, `tabulate`, `requests`) are declared in `pyproject.toml` and installed automatically.

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

### Use a local DKSalaries CSV instead

```bash
uv run main.py --mode Classic --roster-file DKSalaries.csv
```

### Exclude specific players

```bash
uv run main.py --mode Captain --players-out "LeBron James" "Stephen Curry"
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

### Classic NBA

Pick 8 players across positions:

| Position | Min | Max |
|----------|-----|-----|
| PG       | 1   | 2   |
| SG       | 1   | 2   |
| G (PG+SG)| 3   | 4   |
| SF       | 1   | 2   |
| PF       | 1   | 2   |
| F (SF+PF)| 3   | 4   |
| C        | 1   | 2   |

- Salary cap: **$50,000**
- Dual-position players (e.g. PG/SG) are eligible for all listed positions

## How It Works

Both modes are formulated as binary integer programs and solved via `python-mip` (Gurobi when installed, CBC otherwise):

- **Decision variables**: binary (0/1) — whether each player is selected
- **Objective**: maximize total projected fantasy points (FPPG)
- **Constraints**: salary cap, player count, position limits, mutual exclusion (Captain mode)

The solver finds a provably optimal lineup in under a second.

## Project Structure

```
main.py              Optimizer (API client, solvers, CLI)
pyproject.toml       Dependencies and project metadata
ref/                 MATLAB reference implementations
doc/                 Background material
```
