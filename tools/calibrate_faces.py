"""Dev only: calibrate T_auto, T_suggest and the DBSCAN eps against faces you labeled.

Ground truth = faces of *named* people with source 'user' or 'cluster' (after you
reviewed them with `aipdm faces export`, fixed mistakes with `people merge`, etc.).
Reports precision/recall per threshold so the defaults in FaceSettings can be set.

Usage: uv run python tools/calibrate_faces.py [--db CAMINHO]
"""

import argparse
from pathlib import Path

import numpy as np
from sklearn.metrics import adjusted_rand_score

from aipdm.core import db, paths
from aipdm.core.cluster import dbscan_cosine
from aipdm.core.faces import from_blobs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    db_path = args.db or paths.latest_db()
    if db_path is None:
        raise SystemExit("Nenhum banco encontrado.")
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT fa.person_id, fa.embedding FROM faces fa JOIN people p ON p.id = fa.person_id"
        " WHERE p.name IS NOT NULL AND fa.assign_source IN ('user', 'cluster')"
    ).fetchall()
    labels = np.array([r[0] for r in rows])
    people = len(set(labels.tolist()))
    if people < 2:
        raise SystemExit("Nomeie pelo menos 2 pessoas (aipdm people name) antes de calibrar.")
    x = from_blobs([r[1] for r in rows])
    print(f"Gabarito: {len(x)} rostos de {people} pessoas nomeadas\n")

    # Impostor scores: best similarity of each face to any face of ANOTHER person.
    # (A leave-one-out "same person" score would be circular: the named groups came from
    # DBSCAN, so every face already has a close neighbour inside its own group.)
    sims = x @ x.T
    impostor = np.where(labels[:, None] == labels[None, :], -1.0, sims).max(axis=1)
    print("Risco de confundir pessoas (T_auto acima de todos; T_suggest tolera alguns):")
    print(f"{'limiar':>7} {'rostos com outra pessoa acima':>31}")
    for t in np.arange(0.50, 0.76, 0.025):
        print(f"{t:7.3f} {100 * (impostor >= t).mean():30.2f}%")
    print(f"maior semelhança entre pessoas diferentes: {impostor.max():.3f}\n")

    orphans = from_blobs(
        [
            r[0]
            for r in conn.execute(
                "SELECT fa.embedding FROM faces fa JOIN files f ON f.id = fa.file_id"
                " WHERE fa.person_id IS NULL AND f.is_sticker = 0"
            )
        ]
    )
    if len(orphans):
        person_ids = np.unique(labels)
        best = orphans @ x.T
        per_person = np.stack([best[:, labels == p].max(axis=1) for p in person_ids], axis=1)
        top = -np.sort(-per_person, axis=1)
        second = top[:, 1] if top.shape[1] > 1 else np.full(len(top), -1.0)
        print(f"Rostos sem grupo ({len(orphans)}) que seriam sugeridos/atribuídos:")
        print(f"{'limiar':>7} {'rostos':>7} {'ambíguos':>9}")
        for t in np.arange(0.50, 0.76, 0.05):
            hits, ambiguous = (top[:, 0] >= t), (top[:, 0] >= t) & (second >= t)
            print(f"{t:7.2f} {hits.sum():7d} {ambiguous.sum():9d}")
        print()

    print("Agrupamento DBSCAN (min_samples=3) sobre o gabarito:")
    print(f"{'eps':>6} {'ARI':>6} {'grupos':>7} {'ruído':>6}")
    for eps in np.arange(0.30, 0.71, 0.05):
        found = dbscan_cosine(x, eps=float(eps), min_samples=3)
        clustered = found != -1
        ari = adjusted_rand_score(labels[clustered], found[clustered]) if clustered.any() else 0
        print(f"{eps:6.2f} {ari:6.3f} {found.max() + 1:7d} {(~clustered).mean():6.2f}")


if __name__ == "__main__":
    main()
