import pandas as pd
import argparse
from mip import Model, xsum, maximize, BINARY, GUROBI, MAXIMIZE
from tabulate import tabulate


def read_roster(roster_file, players_out):
    df = pd.read_csv(roster_file)
    df = df[df["Roster Position"] == "UTIL"]
    df = df[~df["Name"].isin(players_out)]
    df = df.reset_index()
    return df


def captain_solution(args):
    df = read_roster(args.roster_file, args.players_out)

    ffpg = df["AvgPointsPerGame"].to_list()
    salary = df["Salary"].to_list()
    salary_cap, I = 50000, range(len(salary))

    m = Model("Captain mode", sense=MAXIMIZE, solver_name=GUROBI)

    util = [m.add_var(var_type=BINARY) for i in I]
    captain = [m.add_var(var_type=BINARY) for i in I]

    m.objective = maximize(
        xsum(ffpg[i] * util[i] for i in I) + xsum(1.5 * ffpg[i] * captain[i] for i in I)
    )

    m += (
        xsum(salary[i] * util[i] for i in I)
        + 1.5 * xsum(salary[i] * captain[i] for i in I)
        <= salary_cap
    )
    m += xsum(util[i] for i in I) == 5
    m += xsum(captain[i] for i in I) == 1
    for i in I:
        m += captain[i] + util[i] <= 1.99

    m.optimize()

    util = [df.at[i, "Name"] for i in I if util[i].x >= 0.99]
    captain = [df.at[i, "Name"] for i in I if captain[i].x >= 0.99]
    table = []
    for role in ("Captain", "Util"):
        if role == "Util":
            table.extend([[role, x] for x in util])
        else:
            table.extend([[role, x] for x in captain])

    # print("util: {}, captain: {}".format(util, captain))
    # print("objective_value: {}".format(m.objective_value))
    print(tabulate(table, headers=["Role", "Player"]))



if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Draftkings")

    parser.add_argument(
        "--model",
        type=str,
        required=True,
        help="Draftkings mode.",
        choices=["Captain", "Classic"],
    )

    parser.add_argument(
        "--roster-file", type=str, required=True, help="Draftkings roster file."
    )

    parser.add_argument(
        "--players_out",
        type=str,
        nargs="+",
        default=[],
        help="Examples: ['Andrew Wiggins', 'Klay Thompson']",
    )

    args = parser.parse_args()

    captain_solution(args)
