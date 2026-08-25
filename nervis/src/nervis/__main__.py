"""So `python -m nervis` works.

Present because its absence in a sibling service went unnoticed until a
scheduled task failed on `python3 -m sirvis`: a console script covers the
installed case and nothing covers the module case, and the two look identical
until one is the only one available.
"""

from nervis.cli import main

raise SystemExit(main())
