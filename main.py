import sys
import logging
import argparse

import requests
import pandas as pd
from mip import Model, xsum, BINARY, CBC, GUROBI, MAXIMIZE, OptimizationStatus
from mip import gurobi as _gurobi_module
from tabulate import tabulate

# ── DraftKings API ──────────────────────────────────────────────────────────

CONTESTS_URL = "https://www.draftkings.com/lobby/getcontests?sport=NBA"
DRAFTABLES_URL = (
    "https://api.draftkings.com/draftgroups/v1/draftgroups/{group_id}/draftables"
)
# Lobby link to view contests for a draft group (hash format used by DK).
LOBBY_DRAFT_GROUP_URL = "https://www.draftkings.com/lobby#/NBA/{group_id}"

# Mapping from contest gameType names to our mode names, via GameTypeId.
# Discovered by cross-referencing Contests[].gameType with DraftGroups[].GameTypeId.
GAME_TYPE_IDS = {
    "Classic": {1, 70, 158},
    "Captain": {81, 96, 112, 159},
}

GAME_TYPE_LABELS = {
    1: "Classic",
    70: "Classic",
    73: "Tiers",
    81: "Showdown Captain Mode",
    96: "Showdown Captain Mode",
    112: "In-Game Showdown",
    158: "Classic",
    159: "Showdown Captain Mode",
    188: "Snake",
    193: "Snake Showdown",
    343: "Single Stat - Points",
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


def fetch_roster(mode, draft_group_id=None, lobby_data=None):
    draftables_data = None
    if draft_group_id is None:
        draft_group_id, draftables_data = _pick_draft_group(mode, lobby_data)

    if draftables_data is not None:
        data = draftables_data
        print(f"Using draftables for draft group {draft_group_id} ...")
    else:
        print(f"Fetching draftables for draft group {draft_group_id} ...")
        data = _api_get(DRAFTABLES_URL.format(group_id=draft_group_id))
        print(f"  Lobby: {LOBBY_DRAFT_GROUP_URL.format(group_id=draft_group_id)}")

    fppg_id = _fppg_stat_id(data.get("draftStats", []))
    return _to_dataframe(data.get("draftables", []), fppg_id, mode)


def _draft_group_label(dg):
    """Human-readable slate description from draft group (GameTypeId + GameCount)."""
    gt = dg.get("GameTypeId")
    n = dg.get("GameCount")
    if gt is not None:
        label = GAME_TYPE_LABELS.get(gt, f"GameType {gt}")
    else:
        label = "Single-game Showdown" if n == 1 else "Showdown"
    if n is not None and not (n == 1 and "Single-game" in label):
        label += f" ({n} game{'s' if n != 1 else ''})"
    return label


def _teams_from_draftables(draftables):
    """Build teams string from draftables (e.g. 'NYK @ LAC' or 'ATL, BOS, ...')."""
    abbrevs = sorted({p.get("teamAbbreviation") or "" for p in draftables} - {""})
    if not abbrevs:
        return None
    if len(abbrevs) == 2:
        return f"{abbrevs[0]} @ {abbrevs[1]}"
    return ", ".join(abbrevs)


def _groups_for_mode(data, mode):
    """Return list of draft groups for this mode from lobby data (may be empty)."""
    wanted = GAME_TYPE_IDS[mode]
    groups = [
        dg for dg in data.get("DraftGroups", []) if dg.get("GameTypeId") in wanted
    ]
    if not groups and mode == "Captain":
        dg_ids = set()
        for c in data.get("Contests", []):
            gt = c.get("GameTypeId") or c.get("gt") or c.get("gameTypeId")
            if gt is not None and gt in wanted:
                dg_ids.add(c.get("dg") or c.get("DraftGroupId") or c.get("draftGroupId"))
        dg_ids.discard(None)
        dg_by_id = {dg["DraftGroupId"]: dg for dg in data.get("DraftGroups", [])}
        groups = [dg_by_id[dg_id] for dg_id in dg_ids if dg_id in dg_by_id]
        if not groups and dg_ids:
            groups = [{"DraftGroupId": dg_id, "GameCount": 1} for dg_id in sorted(dg_ids)]
    return groups


def _choose_mode_from_lobby(data):
    """Pick Captain or Classic based on available draft groups. Prefer Captain when both exist."""
    has_captain = bool(_groups_for_mode(data, "Captain"))
    has_classic = bool(_groups_for_mode(data, "Classic"))
    if has_captain:
        return "Captain"
    if has_classic:
        return "Classic"
    return None


def _pick_draft_group(mode, lobby_data=None):
    data = lobby_data if lobby_data is not None else _api_get(CONTESTS_URL)
    print(f"Fetching NBA {mode} contests from DraftKings ...")

    groups = _groups_for_mode(data, mode)
    # Captain: prefer single-game Showdown; Classic: prefer largest slate.
    sort_key = (
        (lambda dg: (0 if dg.get("GameCount") == 1 else 1, -dg.get("GameCount", 0)))
        if mode == "Captain"
        else (lambda dg: -dg.get("GameCount", 0))
    )
    groups = sorted(groups, key=sort_key)

    if not groups:
        sys.exit(f"No {mode} draft groups found for NBA today.")

    best = groups[0]
    best_id = best["DraftGroupId"]
    # Fetch draftables for selected group to show teams (and reuse in fetch_roster to avoid double fetch).
    draftables_data = None
    try:
        draftables_data = _api_get(DRAFTABLES_URL.format(group_id=best_id))
        teams_str = _teams_from_draftables(draftables_data.get("draftables", []))
    except Exception:
        teams_str = None
    teams_part = f" — {teams_str}" if teams_str else ""
    print(f"  Selected: {_draft_group_label(best)}{teams_part} (id {best_id})")
    print(f"  Lobby: {LOBBY_DRAFT_GROUP_URL.format(group_id=best_id)}")

    for dg in groups[1:]:
        print(f"    Also available: {_draft_group_label(dg)} (id {dg['DraftGroupId']})")
    if len(groups) > 1:
        print("  Use --draft-group <id> to pick a different one.")

    return best_id, draftables_data


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


EXCLUDED_STATUSES = {"OUT", "Q"}


def _to_dataframe(draftables, fppg_id, mode):
    # In Captain/Showdown mode, each player appears twice: once for the CPT slot
    # (higher salary = 1.5x) and once for UTIL (base salary). Identify CPT entries
    # by finding the minimum salary per player — any entry above that is a CPT entry.
    min_salary_by_name = {}
    if mode == "Captain":
        for p in draftables:
            name = p.get("displayName", "")
            sal = p.get("salary", 0)
            if name not in min_salary_by_name or sal < min_salary_by_name[name]:
                min_salary_by_name[name] = sal

    rows = [
        {
            "Position": p.get("position", ""),
            "Name": (name := p.get("displayName", "")),
            "Salary": (sal := p.get("salary", 0)),
            "AvgPointsPerGame": _extract_fppg(
                p.get("draftStatAttributes", []), fppg_id
            ),
            "Roster Position": (
                "CPT" if (min_salary_by_name and sal > min_salary_by_name.get(name, sal)) else "UTIL"
            ),
            "TeamAbbrev": p.get("teamAbbreviation", ""),
            "Status": p.get("status", "None"),
        }
        for p in draftables
    ]
    df = pd.DataFrame(rows)
    total = len(df)
    excluded = df[df["Status"].isin(EXCLUDED_STATUSES)]
    if not excluded.empty:
        names = excluded.drop_duplicates(subset=["Name"]).sort_values("Name")
        for _, row in names.iterrows():
            print(f"  Excluding {row['Name']} ({row['Status']})")
        df = df[~df["Status"].isin(EXCLUDED_STATUSES)].reset_index(drop=True)
    print(f"Loaded {len(df)} player entries ({total - len(df)} excluded as OUT/Q).")
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
    if _gurobi_module.has_gurobi:
        m = Model(sense=MAXIMIZE, solver_name=GUROBI)
    else:
        m = Model(sense=MAXIMIZE, solver_name=CBC)
    m.verbose = 0
    return m


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

    cpt_idx = _picked(c)
    util_idx = _picked(u)
    table = [
        ["Captain", df.at[i, "Name"], f"${1.5 * sal[i]:,.0f}", f"{fppg[i]:.1f}"]
        for i in cpt_idx
    ] + [
        ["Util", df.at[i, "Name"], f"${sal[i]:,}", f"{fppg[i]:.1f}"]
        for i in util_idx
    ]
    total_sal = sum(1.5 * sal[i] for i in cpt_idx) + sum(sal[i] for i in util_idx)
    total_pts = sum(1.5 * fppg[i] for i in cpt_idx) + sum(fppg[i] for i in util_idx)
    table.append(["", "TOTAL", f"${total_sal:,.0f} / ${SALARY_CAP:,}", f"{total_pts:.1f}"])
    print(tabulate(table, headers=["Role", "Player", "Salary", "FPPG"]))


def _has_position(position_str, pos):
    return pos in str(position_str).split("/")


def classic_solution(df):
    df = df.drop_duplicates(subset=["Name"]).reset_index(drop=True)

    eligible = {
        pos: [i for i in range(len(df)) if _has_position(df.at[i, "Position"], pos)]
        for pos in POSITIONS
    }
    has_position = set().union(*eligible.values())
    if not has_position:
        sys.exit("No players with recognized positions; cannot build Classic lineup.")
    df = df.loc[sorted(has_position)].reset_index(drop=True)

    eligible = {
        pos: [i for i in range(len(df)) if _has_position(df.at[i, "Position"], pos)]
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
        lambda p: next((pos for pos in POSITIONS if _has_position(p, pos)), str(p))
    )
    idx = _picked(x)
    table = [
        [display_pos[i], df.at[i, "Name"], f"${sal[i]:,}", f"{fppg[i]:.1f}"]
        for i in idx
    ]
    total_sal = sum(sal[i] for i in idx)
    total_pts = sum(fppg[i] for i in idx)
    table.append(["", "TOTAL", f"${total_sal:,.0f} / ${SALARY_CAP:,}", f"{total_pts:.1f}"])
    print(tabulate(table, headers=["Position", "Player", "Salary", "FPPG"]))


# ── CLI ─────────────────────────────────────────────────────────────────────

MODES = {
    "Captain": captain_solution,
    "Classic": classic_solution,
}


def main():
    parser = argparse.ArgumentParser(description="DraftKings lineup optimizer")
    parser.add_argument("--mode", required=False, choices=list(MODES),
                        help="Contest mode: Captain or Classic (auto from available games if omitted)")
    parser.add_argument("--draft-group", type=int, default=None,
                        help="Draft group ID (auto-selected if omitted)")
    parser.add_argument("--roster-file", default=None,
                        help="Local DKSalaries CSV (fetches from DK if omitted)")
    parser.add_argument("--players-out", nargs="+", default=[],
                        help="Players to exclude")
    parser.add_argument("--list-draft-groups", action="store_true",
                        help="Fetch lobby and list all NBA draft groups with GameTypeId (debug)")
    args = parser.parse_args()

    if args.list_draft_groups:
        data = _api_get(CONTESTS_URL)
        dgs = data.get("DraftGroups", [])
        print(f"NBA draft groups ({len(dgs)} total):")
        for dg in sorted(dgs, key=lambda x: (x.get("GameTypeId", 0), -x.get("GameCount", 0))):
            print(f"  DraftGroupId={dg.get('DraftGroupId')}  GameTypeId={dg.get('GameTypeId')}  "
                  f"GameCount={dg.get('GameCount')}  Start={str(dg.get('ContestStartTime', ''))[:19]}")
        gt_ids = sorted(set(dg.get("GameTypeId") for dg in dgs))
        print(f"Unique GameTypeIds in response: {gt_ids}")
        return

    if args.mode is None and args.roster_file:
        parser.error("--mode is required when using --roster-file")

    lobby_data = None
    if args.mode is None:
        data = _api_get(CONTESTS_URL)
        args.mode = _choose_mode_from_lobby(data)
        if args.mode is None:
            sys.exit("No NBA Captain or Classic draft groups found for today.")
        print(f"Auto-selected mode: {args.mode} (from available games)")
        lobby_data = data

    if args.roster_file:
        df = read_roster_csv(args.roster_file, args.players_out)
    else:
        df = fetch_roster(args.mode, args.draft_group, lobby_data=lobby_data)
        if args.players_out:
            df = df[~df["Name"].isin(args.players_out)].reset_index(drop=True)

    MODES[args.mode](df)


if __name__ == "__main__":
    main()
