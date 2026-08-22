# archive

Superseded copies. Kept for history, **not** as a source of truth — if you want to
change any of this, change the live version and leave these alone.

This directory exists because every file in it duplicates content that now lives
somewhere else, and duplicated documents in this project have drifted within a day
before. Nothing here is loaded, linked or built from.

| file | what it was | where the live version is |
|---|---|---|
| `nervis-ecosystem-template.html` | the NERVIS template, 216 KB, as it stood before the screens were built | `../template/index.html` |
| `clarvis_chromed.html` | the Clarvis avatar — **byte-identical** to the canonical copy (`md5 43f150b5…`) | `../template/avatars/clarvis.html` |
| `miku-jarvis-avatar.html` | the Miku avatar, since embedded into the template as its fifth | `../template/avatars/miku.html` |
| `ecosystem-avatars.html` | a side-by-side viewer with its own inlined copies of the four avatars, already diverged from them | `../template/index.html` renders all five in the app |

The specs in the parent directory used to have the same problem — a second copy inside
the template, kept in step by hand. Merging the two repositories removed it. There is
now one of each document, and `template/` reads them from one directory up.
