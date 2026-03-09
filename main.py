import sys
import logging
import argparse

import requests
import pandas as pd
from mip import Model, xsum, BINARY, GUROBI, MAXIMIZE, OptimizationStatus
from tabulate import tabulate

# ── DraftKings API ──────────────────────────────────────────────────────────

CONTESTS_URL = "https://www.draftkings.com/lobby/getcontests?sport=NBA"
DRAFTABLES_URL = (
    "https://api.draftkings.com/draftgroups/v1/draftgroups/{group_id}/draftables"
)

GAME_TYPE_IDS = {
    "Classic": {1, 158},
    "Captain": {96, 159},
}

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
    "Accept": "*/*",
    "Origin": "https://www.draftkings.com",
    "Referer": "https://www.draftkings.com/",
}

# ── DraftKings rules ───────────────────────────────────────────────────────

SALARY_CAP = 50_000

POSITIONS = ["PG", "SG", "SF", "PF", "C"]

POSITION_CONSTRAINTS = {
    "PG": (1, 2),
    "SG": (1, 2),
    "G":  (3, 4),
    "SF": (1, 2),
    "PF": (1, 2),
    "F":  (3, 4),
    "C":  (1, 2),
}

COMPOSITE_GROUPS = {
    "G": ["PG", "SG"],
    "F": ["SF", "PF"],
}

# ── API helpers ─────────────────────────────────────────────────────────────


def _api_get(url):
    resp = requests.get(url, headers=REQUEST_HEADERS)
    resp.raise_for_status()
    return resp.json()


def fetch_roster(mode, draft_group_id=None):
    if draft_group_id is None:
        draft_group_id = _pick_draft_group(mode)

    print(f"Fetching draftables for draft group {draft_group_id} ...")
    data = _api_get(DRAFTABLES_URL.format(group_id=draft_group_id))

    fppg_id = _fppg_stat_id(data.get("draftStats", []))
    return _to_dataframe(data.get("draftables", []), fppg_id, mode)


def _pick_draft_group(mode):
    print(f"Fetching NBA {mode} contests from DraftKings ...")
    data = _api_get(CONTESTS_URL)

    wanted = GAME_TYPE_IDS[mode]
    groups = sorted(
        (dg for dg in data.get("DraftGroups", []) if dg.get("GameTypeId") in wanted),
        key=lambda dg: dg.get("GameCount", 0),
        reverse=True,
    )

    if not groups:
        sys.exit(f"No {mode} draft groups found for NBA today.")

    best = groups[0]
    print(f"  Selected draft group {best['DraftGroupId']}"
          f"  ({best.get('GameCount', '?')} games, main slate)")

    for dg in groups[1:]:
        print(f"    Also available: {dg['DraftGroupId']}"
              f"  ({dg.get('GameCount', '?')} games)")
    if len(groups) > 1:
        print("  Use --draft-group <id> to pick a different one.")

    return best["DraftGroupId"]


def _fppg_stat_id(draft_stats):
    return next(
        (s.get("id") for s in draft_stats if s.get("abbr", "").upper() == "FPPG"),
        None,
    )


_fppg_fallback_warned = False


def _extract_fppg(attrs, fppg_id):
    global _fppg_fallback_warned

    if fppg_id is not None:
        for a in attrs:
            if a.get("id") == fppg_id:
                try:
                    return float(a.get("value", 0))
                except (ValueError, TypeError):
                    return 0.0
    else:
        if not _fppg_fallback_warned:
            logging.warning("FPPG stat not found in draftStats; "
                            "using first available sortValue as fallback.")
            _fppg_fallback_warned = True
        for a in attrs:
            if a.get("sortValue") is not None:
                try:
                    return float(a["sortValue"])
                except (ValueError, TypeError):
                    pass
    return 0.0


def _to_dataframe(draftables, fppg_id, mode):
    rows = [
        {
            "Position": (pos := p.get("position", "")),
            "Name": p.get("displayName", ""),
            "Salary": p.get("salary", 0),
            "AvgPointsPerGame": _extract_fppg(
                p.get("draftStatAttributes", []), fppg_id
            ),
            "Roster Position": (
                ("CPT" if pos.upper() in ("CPT", "CAPTAIN") else "UTIL")
                if mode == "Captain" else "UTIL"
            ),
            "TeamAbbrev": p.get("teamAbbreviation", ""),
        }
        for p in draftables
    ]
    df = pd.DataFrame(rows)
    print(f"Loaded {len(df)} player entries.")
    return df


_COLUMN_ALIASES = {
    "RosterPosition": "Roster Position",
    "Roster_Position": "Roster Position",
    "FPPG": "AvgPointsPerGame",
    "Avg Pts": "AvgPointsPerGame",
}


def read_roster_csv(path, exclude):
    df = pd.read_csv(path)
    df.rename(columns=_COLUMN_ALIASES, inplace=True)
    return df[~df["Name"].isin(exclude)].reset_index(drop=True)


# ── Solver helpers ──────────────────────────────────────────────────────────


def _new_model():
    return Model(sense=MAXIMIZE, solver_name=GUROBI)


def _solve(model):
    model.optimize()
    if model.status not in (OptimizationStatus.OPTIMAL, OptimizationStatus.FEASIBLE):
        sys.exit(f"No feasible lineup found (status: {model.status}).")


def _picked(variables, threshold=0.99):
    return [i for i, v in enumerate(variables) if v.x >= threshold]


# ── Mode solvers ────────────────────────────────────────────────────────────


def captain_solution(df):
    df = df[df["Roster Position"] == "UTIL"].drop_duplicates(subset=["Name"]).reset_index(drop=True)
    if df.empty:
        sys.exit("No UTIL players in roster; cannot build Captain lineup.")
    fppg = df["AvgPointsPerGame"].to_list()
    sal = df["Salary"].to_list()
    I = range(len(df))

    m = _new_model()
    u = [m.add_var(var_type=BINARY) for _ in I]
    c = [m.add_var(var_type=BINARY) for _ in I]

    m.objective = (
        xsum(fppg[i] * u[i] for i in I)
        + xsum(1.5 * fppg[i] * c[i] for i in I)
    )

    m += xsum(sal[i] * u[i] + 1.5 * sal[i] * c[i] for i in I) <= SALARY_CAP
    m += xsum(u[i] for i in I) == 5
    m += xsum(c[i] for i in I) == 1
    for i in I:
        m += c[i] + u[i] <= 1

    _solve(m)

    table = [["Captain", df.at[i, "Name"]] for i in _picked(c)] + \
            [["Util",    df.at[i, "Name"]] for i in _picked(u)]
    print(tabulate(table, headers=["Role", "Player"]))


def classic_solution(df):
    df = df.drop_duplicates(subset=["Name"]).reset_index(drop=True)

    eligible = {
        pos: [i for i in range(len(df)) if pos in str(df.at[i, "Position"])]
        for pos in POSITIONS
    }
    has_position = set().union(*eligible.values())
    if not has_position:
        sys.exit("No players with recognized positions; cannot build Classic lineup.")
    df = df.loc[sorted(has_position)].reset_index(drop=True)

    eligible = {
        pos: [i for i in range(len(df)) if pos in str(df.at[i, "Position"])]
        for pos in POSITIONS
    }
    for name, members in COMPOSITE_GROUPS.items():
        eligible[name] = list({i for member in members for i in eligible[member]})

    for group, (lo, _) in POSITION_CONSTRAINTS.items():
        if lo > 0 and not eligible.get(group):
            sys.exit(f"No players eligible for position {group}; "
                     "cannot build a valid Classic lineup.")

    fppg = df["AvgPointsPerGame"].to_list()
    sal = df["Salary"].to_list()
    I = range(len(df))

    m = _new_model()
    x = [m.add_var(var_type=BINARY) for _ in I]

    m.objective = xsum(fppg[i] * x[i] for i in I)

    m += xsum(sal[i] * x[i] for i in I) <= SALARY_CAP
    m += xsum(x[i] for i in I) == 8

    for group, (lo, hi) in POSITION_CONSTRAINTS.items():
        m += xsum(x[i] for i in eligible[group]) >= lo
        m += xsum(x[i] for i in eligible[group]) <= hi

    _solve(m)

    display_pos = df["Position"].apply(
        lambda p: next((pos for pos in POSITIONS if pos in str(p)), str(p))
    )
    table = [[display_pos[i], df.at[i, "Name"]] for i in _picked(x)]
    print(tabulate(table, headers=["Position", "Player"]))


# ── CLI ─────────────────────────────────────────────────────────────────────

MODES = {
    "Captain": captain_solution,
    "Classic": classic_solution,
}


def main():
    parser = argparse.ArgumentParser(description="DraftKings lineup optimizer")
    parser.add_argument("--mode", required=True, choices=MODES,
                        help="Contest mode: Captain (Showdown) or Classic")
    parser.add_argument("--draft-group", type=int, default=None,
                        help="Draft group ID (auto-selected if omitted)")
    parser.add_argument("--roster-file", default=None,
                        help="Local DKSalaries CSV (fetches from DK if omitted)")
    parser.add_argument("--players-out", nargs="+", default=[],
                        help="Players to exclude")
    args = parser.parse_args()

    if args.roster_file:
        df = read_roster_csv(args.roster_file, args.players_out)
    else:
        df = fetch_roster(args.mode, args.draft_group)
        if args.players_out:
            df = df[~df["Name"].isin(args.players_out)].reset_index(drop=True)

    MODES[args.mode](df)


if __name__ == "__main__":
    main()
