import argparse
import json
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass
class SamplePair:
    stem: str
    image_path: Path
    label_path: Path
    station_id: str


def parse_args():
    parser = argparse.ArgumentParser(
        description="Split exported image/geojson pairs into train/val/test by station id"
    )
    parser.add_argument(
        "--raw-dir",
        default="raw",
        help="Root directory containing images/ and labels/ subfolders (default: raw)",
    )
    parser.add_argument(
        "--source-images",
        default=None,
        help="Directory containing exported image tiles (overrides --raw-dir/images)",
    )
    parser.add_argument(
        "--source-labels",
        default=None,
        help="Directory containing exported geojson labels (overrides --raw-dir/labels)",
    )
    parser.add_argument(
        "--output-root",
        default="data",
        help="Output root with train/val/test folders (default: data)",
    )
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--test-ratio", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move files instead of copying",
    )
    parser.add_argument(
        "--clean-output",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Clean existing split images/labels before writing (default: enabled)",
    )
    return parser.parse_args()


def read_station_id_from_geojson(label_path: Path) -> str:
    try:
        data = json.loads(label_path.read_text(encoding="utf-8"))
        features = data.get("features", [])
        if features:
            props = features[0].get("properties", {})
            station_id = str(props.get("station_id", "")).strip()
            if station_id:
                return station_id
    except Exception:
        pass

    stem = label_path.stem
    parts = stem.split("_")
    if parts:
        return parts[0]
    return "unknown_station"


def collect_pairs(source_images: Path, source_labels: Path) -> List[SamplePair]:
    pairs: List[SamplePair] = []
    labels_by_stem = {p.stem: p for p in source_labels.glob("*.geojson")}

    for image_path in sorted(source_images.iterdir()):
        if image_path.suffix.lower() not in IMAGE_EXTS:
            continue
        stem = image_path.stem
        label_path = labels_by_stem.get(stem)
        if not label_path:
            continue

        station_id = read_station_id_from_geojson(label_path)
        pairs.append(
            SamplePair(
                stem=stem,
                image_path=image_path,
                label_path=label_path,
                station_id=station_id,
            )
        )

    return pairs


def allocate_stations(
    station_ids: List[str],
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
    seed: int,
) -> Dict[str, str]:
    total = train_ratio + val_ratio + test_ratio
    if abs(total - 1.0) > 1e-6:
        raise ValueError("Ratios must sum to 1.0")

    rng = random.Random(seed)
    shuffled = station_ids[:]
    rng.shuffle(shuffled)

    n = len(shuffled)
    n_train = max(1, int(round(n * train_ratio))) if n > 0 else 0
    n_val = int(round(n * val_ratio))

    if n_train + n_val > n:
        n_val = max(0, n - n_train)

    split_map: Dict[str, str] = {}
    for idx, station_id in enumerate(shuffled):
        if idx < n_train:
            split_map[station_id] = "train"
        elif idx < n_train + n_val:
            split_map[station_id] = "val"
        else:
            split_map[station_id] = "test"

    if n >= 3:
        present = {split_map[s] for s in shuffled}
        for required in ("train", "val", "test"):
            if required not in present:
                # Force at least one station into missing split.
                split_map[shuffled[-1]] = required
                present.add(required)

    return split_map


def ensure_output_dirs(output_root: Path, clean_output: bool):
    for split in ("train", "val", "test"):
        split_dir = output_root / split
        images_dir = split_dir / "images"
        labels_dir = split_dir / "labels"

        images_dir.mkdir(parents=True, exist_ok=True)
        labels_dir.mkdir(parents=True, exist_ok=True)

        if clean_output:
            for folder in (images_dir, labels_dir):
                for child in folder.iterdir():
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                    elif child.is_dir():
                        shutil.rmtree(child)


def transfer_file(src: Path, dst: Path, move: bool):
    if move:
        shutil.move(str(src), str(dst))
    else:
        shutil.copy2(str(src), str(dst))


def resolve_sources(
    raw_dir_arg: str,
    source_images_arg: Optional[str],
    source_labels_arg: Optional[str],
) -> Tuple[Path, Path, List[Path]]:
    if source_images_arg and source_labels_arg:
        return Path(source_images_arg), Path(source_labels_arg), []

    script_dir = Path(__file__).resolve().parent
    cwd = Path.cwd()
    raw_arg = Path(raw_dir_arg)

    candidates: List[Path] = []
    if raw_arg.is_absolute():
        candidates.append(raw_arg)
    else:
        candidates.extend(
            [
                cwd / raw_arg,
                script_dir / raw_arg,
                script_dir.parent / raw_arg,
            ]
        )

    seen: set = set()
    unique_candidates: List[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_candidates.append(resolved)

    for candidate in unique_candidates:
        source_images = candidate / "images"
        source_labels = candidate / "labels"
        if source_images.exists() and source_labels.exists():
            return source_images, source_labels, unique_candidates

    fallback_base = unique_candidates[0] if unique_candidates else (cwd / raw_arg).resolve()
    return fallback_base / "images", fallback_base / "labels", unique_candidates


def main():
    args = parse_args()

    source_images, source_labels, raw_candidates = resolve_sources(
        args.raw_dir,
        args.source_images,
        args.source_labels,
    )
    output_root = Path(args.output_root)

    if not source_images.exists() or not source_labels.exists():
        searched = "\n".join(f"  - {p}" for p in raw_candidates) if raw_candidates else "  - (explicit --source-images/--source-labels used)"
        raise FileNotFoundError(
            "Source images/labels directory not found.\n"
            f"Resolved images: {source_images}\n"
            f"Resolved labels: {source_labels}\n"
            "Checked raw directory candidates:\n"
            f"{searched}\n"
            "Expected structure: <raw>/images/*.png and <raw>/labels/*.geojson"
        )

    pairs = collect_pairs(source_images, source_labels)
    if not pairs:
        raise RuntimeError("No matching image/label pairs found")

    stations = sorted({pair.station_id for pair in pairs})
    split_by_station = allocate_stations(
        stations,
        args.train_ratio,
        args.val_ratio,
        args.test_ratio,
        args.seed,
    )

    ensure_output_dirs(output_root, args.clean_output)

    split_counts = {"train": 0, "val": 0, "test": 0}

    for pair in pairs:
        split = split_by_station[pair.station_id]
        out_img = output_root / split / "images" / pair.image_path.name
        out_lbl = output_root / split / "labels" / pair.label_path.name

        transfer_file(pair.image_path, out_img, args.move)
        transfer_file(pair.label_path, out_lbl, args.move)
        split_counts[split] += 1

    summary = {
        "stations": len(stations),
        "pairs": len(pairs),
        "split_counts": split_counts,
        "ratios": {
            "train": args.train_ratio,
            "val": args.val_ratio,
            "test": args.test_ratio,
        },
        "seed": args.seed,
    }

    summary_path = output_root / "split_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
