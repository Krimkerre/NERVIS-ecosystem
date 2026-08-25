"""NERVIS — the ecosystem's control plane.

The dashboard, the event hub and the diagnostics surface. NERVIS reads from
every other service and, per §2.1, owns almost none of the data it displays:
what it holds is what only a control plane can hold — the registry, the event
stream, conversations and settings.
"""
