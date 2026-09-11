"""Source freshness: has the publisher changed anything since a pack was captured?

Reports only. It never re-collects and never auto-updates - silently re-ingesting
a pack under a running tenant changes answers with no warning and no way to
attribute the change. Check -> notify -> publish a new pack version -> install.
"""
