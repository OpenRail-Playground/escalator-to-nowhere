# Our Cool Project

<!-- TODO: Shortly explain what this project is about -->

## Background

<p align="center">
  <img alt="Hack4Rail Logo" src="img/hack4rail-logo.jpg" width="400"/>
</p>

This project has been initiated during the [Hack4Rail 2026](https://hack4rail.org/), a joint hackathon organised by the railway companies SBB, ÖBB, and DB in partnership with the OpenRail Association.

## Install

<!-- TODO: Explain how a user can install the software -->

## How To Run

### Start server

From the repository root:

```bash
node ml/server.js
```

Default URL: `http://localhost:5510`

You can override the port:

```bash
PORT=8080 node ml/server.js
```

### Start frontend

The frontend is served by the same Node server (no separate frontend process).

1. Start the server (`node ml/server.js`).
2. Open `http://localhost:5510` in your browser.

### Start training

Training lives in [`ml/`](ml/) and is documented in detail in [`ml/README.md`](ml/README.md).

Quick start:

```bash
cd ml
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python train_instance_segmentation.py \
  --train-images data/train/images \
  --train-annotations data/train/annotations.json \
  --val-images data/val/images \
  --val-annotations data/val/annotations.json \
  --output-dir runs/platform_instance \
  --epochs 30 \
  --batch-size 2
```

Model checkpoints are written to `ml/runs/platform_instance/`.

## Platform Instance Segmentation (PyTorch)

A full Mask R-CNN training and inference pipeline is available in [ml/README.md](ml/README.md).

Key scripts:

- [ml/train_instance_segmentation.py](ml/train_instance_segmentation.py)
- [ml/infer_instance_segmentation.py](ml/infer_instance_segmentation.py)
- [ml/prepare_coco_from_geojson.py](ml/prepare_coco_from_geojson.py)

Sample data:
52.559093, 14.350795
52.406681, 13.824663
52.400254, 12.564111
52.403154, 12.156914
52.274524, 11.840258
51.997122, 13.053560
51.842533, 12.634556
51.519756, 12.346535

## License

<!-- If you decide for another license, please change it here, and exchange the LICENSE file -->

The content of this repository is licensed under the [Apache 2.0 license](LICENSE).