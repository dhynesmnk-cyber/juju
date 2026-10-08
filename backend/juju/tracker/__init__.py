"""The private bet tracker, absorbed from parlaytracker@c3bd43c.

Bets the two owners placed elsewhere, logged as slips and legs, watched live, settled, and
analysed. It shares Juju's database, worker and shared ingest code. Nothing here is public: the
`/me` section that shows it is behind its own passcodes, and it takes no bets. The plan and its
phases are in docs/plans/integrate-parlaytracker.md. A "SPEC.md" in a docstring here is
parlaytracker's (at c3bd43c).
"""
