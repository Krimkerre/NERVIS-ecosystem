"""Reading the other services, without owning any of what they say.

§2.1: *most specialized data stays fetched from the authoritative service*.
Nothing in this package stores, caches or persists a peer's answer — a read
here is one HTTP call whose result is handed straight to the caller and then
forgotten. A NERVIS that kept its own copy of RAVIS's providers would
eventually disagree with RAVIS about them, and be believed.
"""
