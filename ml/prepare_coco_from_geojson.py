import argparse
import json
from pathlib import Path

from PIL import Image


def flatten_feature_collection(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("type") == "FeatureCollection":
        return data.get("features", [])
    if data.get("type") == "Feature":
        return [data]
    raise ValueError(f"Unsupported GeoJSON format in {path}")


def polygon_coords_to_coco_segmentation(coords):
    segments = []
    for ring in coords:
        flat = []
        for x, y in ring:
            flat.extend([float(x), float(y)])
        if len(flat) >= 6:
            segments.append(flat)
    return segments


def compute_bbox_and_area(coords):
    xs = []
    ys = []
    for ring in coords:
        for x, y in ring:
            xs.append(float(x))
            ys.append(float(y))

    if not xs or not ys:
        return None, None

    min_x = min(xs)
    min_y = min(ys)
    max_x = max(xs)
    max_y = max(ys)
    width = max_x - min_x
    height = max_y - min_y

    # Shoelace area (outer minus holes if any by ring orientation assumption)
    total_area = 0.0
    for ring in coords:
        if len(ring) < 3:
            continue
        area = 0.0
        for i in range(len(ring)):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % len(ring)]
            area += x1 * y2 - x2 * y1
        total_area += abs(area) * 0.5

    return [min_x, min_y, width, height], total_area


def convert(dataset_dir: Path, output_json: Path):
    images_dir = dataset_dir / "images"
    labels_dir = dataset_dir / "labels"

    if not images_dir.exists() or not labels_dir.exists():
        raise FileNotFoundError("Expected dataset structure: <split>/images and <split>/labels")

    image_files = sorted([p for p in images_dir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}])

    coco = {
        "images": [],
        "annotations": [],
        "categories": [{"id": 1, "name": "platform"}],
    }

    ann_id = 1
    image_id = 1

    for image_path in image_files:
        label_path = labels_dir / f"{image_path.stem}.geojson"
        if not label_path.exists():
            image_id += 1
            continue

        with Image.open(image_path) as img:
            width, height = img.size

        coco["images"].append(
            {
                "id": image_id,
                "file_name": image_path.name,
                "width": width,
                "height": height,
            }
        )

        features = flatten_feature_collection(label_path)
        for feature in features:
            geom = feature.get("geometry", {})
            geom_type = geom.get("type")

            polygons = []
            if geom_type == "Polygon":
                polygons = [geom.get("coordinates", [])]
            elif geom_type == "MultiPolygon":
                polygons = geom.get("coordinates", [])
            else:
                continue

            for polygon in polygons:
                segmentation = polygon_coords_to_coco_segmentation(polygon)
                if not segmentation:
                    continue

                bbox, area = compute_bbox_and_area(polygon)
                if not bbox or not area or bbox[2] <= 1 or bbox[3] <= 1:
                    continue

                coco["annotations"].append(
                    {
                        "id": ann_id,
                        "image_id": image_id,
                        "category_id": 1,
                        "segmentation": segmentation,
                        "area": float(area),
                        "bbox": [float(v) for v in bbox],
                        "iscrowd": 0,
                    }
                )
                ann_id += 1

        image_id += 1

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(coco, indent=2), encoding="utf-8")
    print(f"Wrote COCO file: {output_json}")
    print(f"Images: {len(coco['images'])}, Annotations: {len(coco['annotations'])}")


def parse_args():
    parser = argparse.ArgumentParser(description="Convert per-image GeoJSON polygons to COCO annotations")
    parser.add_argument("--dataset-dir", required=True, help="Split directory containing images/ and labels/")
    parser.add_argument("--output", required=True, help="Output COCO json path")
    return parser.parse_args()


def main():
    args = parse_args()
    convert(Path(args.dataset_dir), Path(args.output))


if __name__ == "__main__":
    main()
