# Platform Instance Segmentation (PyTorch)

This folder contains a complete PyTorch instance-segmentation pipeline using Mask R-CNN.

## 1. Install

```bash
cd ml
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Dataset template

Starter folders are already included:

```text
ml/data/
  train/
    images/
    labels/
  val/
    images/
    labels/
  test/
    images/
    labels/
```

## 3. Export training samples from the map UI

Use the Export Images button in the web map.

For each exported tile, the app now downloads two files with the same base name:

- `*.png` aerial tile image
- `*.geojson` platform polygon label in pixel coordinates (0..255)

Alternative (recommended): use **Export ML ZIP** in the UI.

It downloads one file: `platform_ml_export.zip` with:

- `raw/images/*.png`
- `raw/labels/*.geojson`
- `raw/manifest.json`

Unzip this archive under `ml/` so `ml/raw/images` and `ml/raw/labels` exist.

Place them into split folders, for example:

- `ml/data/train/images/*.png`
- `ml/data/train/labels/*.geojson`

## 4. Convert to COCO

Convert each split to COCO JSON:

```bash
python prepare_coco_from_geojson.py --dataset-dir data/train --output data/train/annotations.json
python prepare_coco_from_geojson.py --dataset-dir data/val --output data/val/annotations.json
```

## 5. Split by station (recommended)

To avoid leakage, split using station IDs first:

```bash
python split_dataset_by_station.py \
  --raw-dir raw \
  --output-root data \
  --train-ratio 0.7 \
  --val-ratio 0.15 \
  --test-ratio 0.15 \
  --seed 42 \
  --clean-output
```

Equivalent explicit form:

```bash
python split_dataset_by_station.py \
  --source-images raw/images \
  --source-labels raw/labels \
  --output-root data \
  --train-ratio 0.7 \
  --val-ratio 0.15 \
  --test-ratio 0.15 \
  --seed 42 \
  --clean-output
```

This writes:

- `data/train/images`, `data/train/labels`
- `data/val/images`, `data/val/labels`
- `data/test/images`, `data/test/labels`

Then convert train/val labels to COCO as in step 4.

## 6. Train

```bash
python train_instance_segmentation.py \
  --train-images data/train/images \
  --train-annotations data/train/annotations.json \
  --val-images data/val/images \
  --val-annotations data/val/annotations.json \
  --output-dir runs/platform_instance \
  --epochs 30 \
  --batch-size 2
```

Outputs:

- `best.pt` best training loss checkpoint
- `last.pt` latest checkpoint

## 7. Run inference

Single image:

```bash
python infer_instance_segmentation.py \
  --weights runs/platform_instance/best.pt \
  --image path/to/image.png \
  --output path/to/prediction.png
```

Folder inference:

```bash
python infer_instance_segmentation.py \
  --weights runs/platform_instance/best.pt \
  --images-dir path/to/images \
  --output-dir path/to/predictions
```

## Notes

- The model expects COCO polygons/masks and learns platform instances.
- For best generalization, split by station/area (not random tiles).
- Keep train/val geospatially separated.
