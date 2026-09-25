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

    # Leave-one-out: best similarity to each *other* labeled face, per person.
    sims = x @ x.T
    np.fill_diagonal(sims, -1.0)
    person_ids = np.unique(labels)
    best_per_person = np.stack([sims[:, labels == p].max(axis=1) for p in person_ids], axis=1)
    predicted = person_ids[best_per_person.argmax(axis=1)]
    best_score = best_per_person.max(axis=1)
    correct = predicted == labels

    print("Atribuição (rosto novo x pessoas conhecidas):")
    print(f"{'limiar':>7} {'precisão':>9} {'cobertura':>10}")
    for t in np.arange(0.30, 0.81, 0.025):
        taken = best_score >= t
        if not taken.any():
            continue
        print(f"{t:7.3f} {correct[taken].mean():9.3f} {taken.mean():10.3f}")
    print("\nSugestão: T_auto = menor limiar com precisão >= 0,99;")
    print("          T_suggest = menor limiar com precisão >= 0,90.\n")

    print("Agrupamento DBSCAN (min_samples=3) sobre o gabarito:")
    print(f"{'eps':>6} {'ARI':>6} {'grupos':>7} {'ruído':>6}")
    for eps in np.arange(0.30, 0.71, 0.05):
        found = dbscan_cosine(x, eps=float(eps), min_samples=3)
        clustered = found != -1
        ari = adjusted_rand_score(labels[clustered], found[clustered]) if clustered.any() else 0
        print(f"{eps:6.2f} {ari:6.3f} {found.max() + 1:7d} {(~clustered).mean():6.2f}")


if __name__ == "__main__":
    main()
