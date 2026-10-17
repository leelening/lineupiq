"""Build the static data file for the LineupIQ GitHub Pages site.

Reuses the optimizer in ``main.py`` unchanged: fetches the DraftKings lobby,
solves every single-game Showdown plus the main Classic slate, and writes
``<out>/data/lineup.json`` (a ``slates`` list) for ``site/index.html`` to render.
``history.csv`` is also exported to ``<out>/data/history.json`` so the site
can browse past lineups with projected vs. actual fantasy points.

Days with no NBA slate (offseason, All-Star break) are not an error: a JSON
file with ``status: "no_games"`` is written so the site still deploys.

Usage:
    uv run site/build.py [--out _site] [--mode Captain|Classic] [--draft-group ID]
"""

import argparse
import io
import json
import sys
import traceback
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as liq  # noqa: E402


# Files that define the optimizer itself. Only changes to these bump the version;
# edits to the README, the site, or the workflow do not.
OPTIMIZER_FILES = ["main.py", "pyproject.toml"]


def tool_version():
    """Auto-derived optimizer version: <base>.<N>+<sha>.

    base = ``project.version`` in pyproject.toml, N = number of commits that
    touched the optimizer code, sha = the last such commit — e.g.
    ``0.1.0.12+2762c2f``. Requires full git history in CI
    (actions/checkout ``fetch-depth: 0``).
    """
    import subprocess
    import tomllib

    try:
        base = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    except Exception:
        base = "0"

    def git(*args):
        try:
            return subprocess.check_output(
                ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            return ""

    count = git("rev-list", "--count", "HEAD", "--", *OPTIMIZER_FILES)
    sha = git("log", "-1", "--format=%h", "--abbrev=7", "--", *OPTIMIZER_FILES)
    changed = git("log", "-1", "--format=%cs", "--", *OPTIMIZER_FILES)  # YYYY-MM-DD
    dirty = bool(git("status", "--porcelain", "--", *OPTIMIZER_FILES))

    version = base
    if count:
        version += f".{count}"
    if sha:
        version += f"+{sha}"
    if dirty:
        version += ".dirty"
    return {
        "version": version,
        "commit": sha or None,
        "commit_count": int(count) if count else None,
        "code_changed": changed or None,
        "files": OPTIMIZER_FILES,
    }


def _slate_info(lobby, draft_group_id):
    """Human-readable slate label for a draft group id from lobby data."""
    for dg in lobby.get("DraftGroups", []):
        if dg.get("DraftGroupId") == draft_group_id:
            return liq._draft_group_label(dg), dg.get("GameCount")
    return f"Draft group {draft_group_id}", None


def review_history():
    """Fill in actual FPPG for finished games in history.csv (main.py --review).

    Never fatal: stats.nba.com may be slow or block cloud IPs; we just report.
    Returns the captured log text.
    """
    log = io.StringIO()
    try:
        with redirect_stdout(log):
            liq._review()
    except SystemExit as e:
        print(str(e), file=log)
    except Exception as e:
        print(f"Review failed: {type(e).__name__}: {e}", file=log)
    return log.getvalue()


def solve_slate(lobby, mode, draft_group, players_out=(), oprk_weight=0.0, save=True,
                seen_games=None):
    """Solve one draft group and return its site entry, or None when it covers
    exactly the same games as a Showdown already in ``seen_games``."""
    log = io.StringIO()
    with redirect_stdout(log):
        df, draft_group, game_date = liq.fetch_roster(mode, draft_group, lobby_data=lobby)
        # In-Game Showdowns use their own competition ids, so match on teams.
        game = (game_date, frozenset(df["TeamAbbrev"]) - {""})
        if seen_games is not None and mode == "Captain" and game[1]:
            if game in seen_games:
                return None
            seen_games.add(game)
        if players_out:
            df = df[~df["Name"].isin(players_out)].reset_index(drop=True)
        teams = liq._teams_from_draftables(
            [{"teamAbbreviation": t} for t in df["TeamAbbrev"].unique()]
        )
        lineup = liq.MODES[mode](df, oprk_weight=oprk_weight)
        if save and lineup:
            # Same write-back as the CLI: history.csv keyed by draft_group.
            liq._save_lineup(lineup, draft_group, game_date, oprk_weight)

    label, game_count = _slate_info(lobby, draft_group)
    return {
        "status": "ok",
        "mode": mode,
        "game_date": game_date,
        "draft_group": draft_group,
        "slate": label,
        "game_count": game_count,
        "teams": teams,
        "lobby_url": liq.LOBBY_DRAFT_GROUP_URL.format(group_id=draft_group),
        "salary_cap": liq.SALARY_CAP,
        "total_salary": sum(p["salary"] for p in lineup),
        "total_projected_fppg": round(sum(p["projected_fppg"] for p in lineup), 1),
        "players_out": list(players_out),
        "oprk_weight": oprk_weight,
        "player_pool": int(df["Name"].nunique()),
        "lineup": lineup,
        "log": log.getvalue(),
    }


def build(mode=None, draft_group=None, players_out=(), oprk_weight=0.0, save=True):
    """Solve every NBA Showdown game plus the main Classic slate (or just the
    requested mode / draft group). One failing slate does not block the rest."""
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lobby = liq._api_get(liq.CONTESTS_URL)

    if draft_group is not None:
        detected = liq._mode_for_draft_group(lobby, draft_group)
        if detected is None:
            raise SystemExit(f"Draft group {draft_group} not found in today's lobby.")
        slates = [(mode or detected, {"DraftGroupId": draft_group})]
    else:
        slates = liq._slates_to_solve(lobby, mode)

    if not slates:
        return {
            "status": "no_games",
            "generated_at": generated_at,
            "message": "No NBA Captain or Classic slates on DraftKings today.",
        }

    results, seen_games = [], set()
    for slate_mode, dg in slates:
        dg_id = dg["DraftGroupId"]
        try:
            entry = solve_slate(
                lobby, slate_mode, dg_id, players_out, oprk_weight, save, seen_games
            )
        except SystemExit as e:  # main.py signals "no feasible lineup" via sys.exit(msg)
            entry = {"status": "no_lineup", "message": str(e)}
        except Exception as e:  # network / API failure for this slate only
            traceback.print_exc()
            entry = {"status": "error", "message": f"{type(e).__name__}: {e}"}
        if entry is None:
            print(f"Skipped draft group {dg_id}: same game as an earlier Showdown")
            continue
        if entry["status"] != "ok":
            label, _ = _slate_info(lobby, dg_id)
            entry |= {"mode": slate_mode, "draft_group": dg_id, "slate": label}
            print(f"Draft group {dg_id}: {entry['message']}")
        results.append(entry)

    if not any(r["status"] == "ok" for r in results):
        return {
            "status": "no_games",
            "generated_at": generated_at,
            "message": "; ".join(r["message"] for r in results) or "No lineup available today.",
        }
    # Earliest games first; Classic after the Showdowns on the same date.
    results.sort(key=lambda r: (r.get("game_date") or "9999", r["mode"] == "Classic"))
    return {"status": "ok", "generated_at": generated_at, "slates": results}


def build_history():
    """Convert history.csv into a list of past lineups (newest first)."""
    if not liq.HISTORY_FILE.exists():
        return {"summary": None, "lineups": []}
    import pandas as pd

    df = pd.read_csv(liq.HISTORY_FILE, dtype=str).fillna("")
    lineups = []
    for (d, dg), grp in df.groupby(["date", "draft_group"], sort=False):
        players = []
        for _, r in grp.iterrows():
            proj = float(r["projected_fppg"]) if r["projected_fppg"] else 0.0
            act = float(r["actual_fppg"]) if r["actual_fppg"] != "" else None
            players.append(
                {
                    "role": r["role"],
                    "name": r["name"],
                    "team": r["team"],
                    "salary": int(float(r["salary"])) if r["salary"] else 0,
                    "projected_fppg": proj,
                    "actual_fppg": act,
                    "game_date": r["game_date"],
                }
            )
        roles = {p["role"] for p in players}
        mode = "Captain" if roles & {"Captain", "Util"} else "Classic"
        reviewed = all(p["actual_fppg"] is not None for p in players)
        proj_total = round(sum(p["projected_fppg"] for p in players), 1)
        act_total = round(sum(p["actual_fppg"] for p in players), 1) if reviewed else None
        lineups.append(
            {
                "date": d,
                "league": grp["league"].iloc[0] if "league" in grp and grp["league"].iloc[0] else "NBA",
                "draft_group": int(dg),
                "mode": mode,
                "oprk_weight": float(grp["oprk_weight"].iloc[0] or 0),
                "teams": ", ".join(sorted({p["team"] for p in players if p["team"]})),
                "lobby_url": liq.LOBBY_DRAFT_GROUP_URL.format(group_id=dg),
                "salary_cap": liq.SALARY_CAP,
                "total_salary": sum(p["salary"] for p in players),
                "total_projected_fppg": proj_total,
                "total_actual_fppg": act_total,
                "reviewed": reviewed,
                "lineup": players,
            }
        )
    lineups.sort(key=lambda L: (L["date"], L["draft_group"]), reverse=True)

    done = [L for L in lineups if L["reviewed"]]
    summary = None
    if done:
        tp = sum(L["total_projected_fppg"] for L in done)
        ta = sum(L["total_actual_fppg"] for L in done)
        summary = {
            "lineups_reviewed": len(done),
            "lineups_total": len(lineups),
            "avg_accuracy_pct": round(ta / tp * 100, 1) if tp else None,
            "mean_abs_error": round(
                sum(abs(L["total_actual_fppg"] - L["total_projected_fppg"]) for L in done)
                / len(done),
                1,
            ),
        }
    return {"summary": summary, "lineups": lineups}


def main():
    ap = argparse.ArgumentParser(description="Build LineupIQ site data")
    ap.add_argument("--out", default="_site", help="Output directory (default _site)")
    ap.add_argument("--mode", choices=list(liq.MODES), default=None)
    ap.add_argument("--draft-group", type=int, default=None)
    ap.add_argument("--players-out", nargs="+", default=[])
    ap.add_argument("--oprk-weight", type=float, default=0.0)
    ap.add_argument("--no-save", action="store_true", help="Don't write today's lineup to history.csv")
    ap.add_argument("--no-review", action="store_true", help="Skip fetching actuals for past lineups")
    args = ap.parse_args()

    review_log = ""
    if not args.no_review:
        review_log = review_history()
        print(review_log, end="")

    try:
        data = build(
            args.mode, args.draft_group, args.players_out, args.oprk_weight, save=not args.no_save
        )
    except SystemExit as e:
        # main.py signals "no slate / no feasible lineup" via sys.exit(msg).
        data = {
            "status": "no_games",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": str(e) or "No lineup available today.",
        }
    except Exception as e:  # network / API failures — still deploy the site
        traceback.print_exc()
        data = {
            "status": "error",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": f"{type(e).__name__}: {e}",
        }

    if review_log:
        data["review_log"] = review_log
    data["tool"] = tool_version()

    out = Path(args.out) / "data"
    out.mkdir(parents=True, exist_ok=True)
    (out / "lineup.json").write_text(json.dumps(data, indent=2))
    n_slates = sum(s["status"] == "ok" for s in data.get("slates", []))
    print(f"[{data['status']}] wrote {out / 'lineup.json'} ({n_slates} lineups)")

    history = build_history()
    (out / "history.json").write_text(json.dumps(history, indent=2))
    n = len(history["lineups"]) if history else 0
    print(f"wrote {out / 'history.json'} ({n} past lineups)")


if __name__ == "__main__":
    main()
