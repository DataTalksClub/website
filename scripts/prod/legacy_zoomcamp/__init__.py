"""Local-only import tooling for pre-2024 Zoomcamp scoring history.

The course management platform only carries data from 2024 onward. The years
before that (2021-2023) live in the commit history of the separate
``DataTalksClub/zoomcamp-scoring`` repository, in a real learner's plaintext
email/name for every homework, project, and certificate.

Every importer in this package reads from an external checkout of that
repository (never vendored into this repository) and writes only anonymized,
locally generated identities into the database: see ``identity.py``. No
plaintext email or real name is ever persisted.
"""
