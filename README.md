# LineupIQ

DraftKings NBA lineup optimizer that finds the salary-cap-optimal roster using mixed-integer programming.

Supports **Captain (Showdown)** and **Classic** contest modes. Player data can be fetched live from the DraftKings API or loaded from a local CSV export.

## Setup

Requires Python 3.12+ and [Gurobi](https://www.gurobi.com/) (a free academic license is available).

```bash
uv sync
```

Dependencies (`pandas`, `python-mip`, `tabulate`, `requests`) are declared in `pyproject.toml` and installed automatically.

## Usage

### Fetch today's slate from DraftKings and optimize

```bash
# Captain (Showdown) mode — 1 CPT + 5 UTIL, $50k cap
uv run main.py --mode Captain

# Classic NBA mode — 8 players with position constraints, $50k cap
uv run main.py --mode Classic
```

The tool auto-selects the main slate (largest game count). To pick a different draft group:

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

Both modes are formulated as binary integer programs and solved with Gurobi via `python-mip`:

- **Decision variables**: binary (0/1) — whether each player is selected
- **Objective**: maximize total projected fantasy points (FPPG)
- **Constraints**: salary cap, player count, position limits, mutual exclusion (Captain mode)

The solver finds a provably optimal lineup in under a second.

## Project Structure

```
main.py              Optimizer (API client, solvers, CLI)
pyproject.toml       Dependencies and project metadata
src/matlab/          MATLAB reference implementations
doc/                 Background material
```
