"""
Mafia — role dealing for the playing-card party game. Pure logic, no I/O.

One player is the moderator and holds no card; everyone else is dealt a role.
The moderator runs the night/day cycle from their phone.

  ROLES       : role ids, roughly in wake-up order
  ROLE_META   : display name + emoji per role
  deal_roles(pids, counts, rng) -> {pid: role}
"""

import random

ROLES = ["mafia", "sheriff", "doctor", "civilian"]

ROLE_META = {
    "mafia":    {"name": "Mafia",    "emoji": "\N{HOCHO}"},
    "sheriff":  {"name": "Sheriff",  "emoji": "\N{SLEUTH OR SPY}"},
    "doctor":   {"name": "Doctor",   "emoji": "\N{SYRINGE}"},
    "civilian": {"name": "Civilian", "emoji": "\N{BUST IN SILHOUETTE}"},
}


def deal_roles(pids, counts, rng=random):
    """Return {pid: role}. `counts` says how many mafia / sheriff / doctor to
    hand out; every remaining player is a civilian. The caller is responsible
    for checking the counts actually fit the player list."""
    pool = list(pids)
    rng.shuffle(pool)
    out = {}
    for role in ("mafia", "sheriff", "doctor"):
        for _ in range(max(0, int(counts.get(role, 0)))):
            if pool:
                out[pool.pop()] = role
    for pid in pool:
        out[pid] = "civilian"
    return out
