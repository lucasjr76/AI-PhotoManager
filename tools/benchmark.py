"""Dev only: time each stage on N images of a folder (read-only) and extrapolate RNF-4.

Usage: uv run python tools/benchmark.py <pasta> [--n 200] [--workers 3]
RNF-4 target: 1000 photos in < 10 min on a 4-core notebook (3 workers).
"""

import argparse
import tempfile
import time
from datetime import date
from functools import partial
from itertools import batched
from pathlib import Path

from aipdm.core.faces import FaceSettings
from aipdm.core.paths import models_dir
from aipdm.core.scanner import (
    CHUNK_SIZE,
    KIND_STAGES,
    Task,
    WorkerConfig,
    kind_of,
    process_chunk,
    walk,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pasta", type=Path)
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    root = args.pasta.resolve()
    images = [
        rel
        for rel, _, _ in sorted(walk(root))
        if kind_of(root / rel) == "image" and "/STK-" not in f"/{rel}"
    ][: args.n]
    print(f"{len(images)} imagens de {root}")

    with tempfile.TemporaryDirectory() as tmp:
        tasks = [
            Task(i, root / rel, "image", Path(tmp) / f"{i}.jpg", KIND_STAGES["image"], ())
            for i, rel in enumerate(images)
        ]
        # Single process, all threads: per-stage cost.
        config = WorkerConfig(models_dir(), 0, FaceSettings(), date.today())
        process_chunk(tasks[:1], config)  # load models before timing
        started = time.perf_counter()
        results = [r for chunk in batched(tasks, CHUNK_SIZE) for r in process_chunk(chunk, config)]
        single = time.perf_counter() - started
        totals: dict[str, float] = {}
        for r in results:
            for stage, sec in r.seconds.items():
                totals[stage] = totals.get(stage, 0.0) + sec
        print("\nCusto por imagem (1 processo, todos os núcleos):")
        for stage, sec in sorted(totals.items(), key=lambda kv: -kv[1]):
            print(f"  {stage:<8} {1000 * sec / len(results):8.1f} ms")
        print(f"  total    {1000 * single / len(results):8.1f} ms")
        faces = sum(len(r.faces or []) for r in results)
        with_text = sum(1 for r in results if r.pages)
        print(f"Rostos: {faces}   Imagens com texto (OCR): {with_text}")

        # Worker pool, 1 thread per session: what `aipdm index --workers N` does.
        from concurrent.futures import ProcessPoolExecutor
        from multiprocessing import get_context

        config = WorkerConfig(models_dir(), 1, FaceSettings(), date.today())
        with ProcessPoolExecutor(args.workers, get_context("spawn")) as pool:
            list(pool.map(partial(process_chunk, config=config), [tasks[:1]] * args.workers))
            started = time.perf_counter()
            list(pool.map(partial(process_chunk, config=config), batched(tasks, CHUNK_SIZE)))
            pooled = time.perf_counter() - started
        per_1000 = pooled / len(tasks) * 1000 / 60
        print(f"\n{args.workers} workers: {len(tasks) / pooled:.1f} imagens/s")
        print(f"Estimativa para 1000 fotos: {per_1000:.1f} min (meta RNF-4: < 10 min em 4 núcleos)")


if __name__ == "__main__":
    main()
