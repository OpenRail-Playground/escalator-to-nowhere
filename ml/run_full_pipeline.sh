#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RAW_DIR="raw"
OUTPUT_ROOT="data"
TRAIN_RATIO="0.7"
VAL_RATIO="0.15"
TEST_RATIO="0.15"
SEED="42"
NO_CLEAN=0
SKIP_TEST_COCO=0
SKIP_TRAIN=0
SKIP_INFER=0
EPOCHS="30"
BATCH_SIZE="2"
DEFAULT_NUM_WORKERS="4"
if [[ "$(uname -s)" == "Darwin" ]]; then
	DEFAULT_NUM_WORKERS="2"
fi
NUM_WORKERS="$DEFAULT_NUM_WORKERS"
LR="0.005"
RUN_OUTPUT_DIR="runs/platform_instance"
INFER_IMAGES_DIR=""
INFER_OUTPUT_DIR="predictions"
INFER_SCORE_THRESHOLD="0.5"

if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
	PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
else
	PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

print_help() {
	cat <<'EOF'
Run full ML pipeline:
1) Split raw images/labels into train/val/test by station
2) Convert split labels to COCO annotations
3) Train instance-segmentation model
4) Run inference on test (or custom) images

Usage:
	./run_full_pipeline.sh [options]

Options:
	--raw-dir <path>        Raw folder containing images/ and labels/ (default: raw)
	--output-root <path>    Output data root (default: data)
	--train-ratio <float>   Train split ratio (default: 0.7)
	--val-ratio <float>     Val split ratio (default: 0.15)
	--test-ratio <float>    Test split ratio (default: 0.15)
	--seed <int>            Random seed (default: 42)
	--no-clean              Keep existing split files instead of cleaning first
	--skip-test-coco        Skip COCO generation for test split
	--skip-train            Skip training stage
	--skip-infer            Skip inference stage
	--epochs <int>          Training epochs (default: 30)
	--batch-size <int>      Training batch size (default: 2)
	--num-workers <int>     Training/inference data loader workers (default: 2 on macOS, 4 otherwise)
	--lr <float>            Training learning rate (default: 0.005)
	--run-output-dir <path> Training output directory (default: runs/platform_instance)
	--infer-images-dir <path> Images folder for inference (default: <output-root>/test/images)
	--infer-output-dir <path> Output folder for predictions (default: predictions)
	--infer-score-threshold <float> Inference score threshold (default: 0.5)
	--python-bin <path>     Python executable to use (default: ml/.venv/bin/python if available, else python3)
	--help                  Show this help
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--raw-dir)
			RAW_DIR="$2"
			shift 2
			;;
		--output-root)
			OUTPUT_ROOT="$2"
			shift 2
			;;
		--train-ratio)
			TRAIN_RATIO="$2"
			shift 2
			;;
		--val-ratio)
			VAL_RATIO="$2"
			shift 2
			;;
		--test-ratio)
			TEST_RATIO="$2"
			shift 2
			;;
		--seed)
			SEED="$2"
			shift 2
			;;
		--no-clean)
			NO_CLEAN=1
			shift
			;;
		--skip-test-coco)
			SKIP_TEST_COCO=1
			shift
			;;
		--skip-train)
			SKIP_TRAIN=1
			shift
			;;
		--skip-infer)
			SKIP_INFER=1
			shift
			;;
		--epochs)
			EPOCHS="$2"
			shift 2
			;;
		--batch-size)
			BATCH_SIZE="$2"
			shift 2
			;;
		--num-workers)
			NUM_WORKERS="$2"
			shift 2
			;;
		--lr)
			LR="$2"
			shift 2
			;;
		--run-output-dir)
			RUN_OUTPUT_DIR="$2"
			shift 2
			;;
		--infer-images-dir)
			INFER_IMAGES_DIR="$2"
			shift 2
			;;
		--infer-output-dir)
			INFER_OUTPUT_DIR="$2"
			shift 2
			;;
		--infer-score-threshold)
			INFER_SCORE_THRESHOLD="$2"
			shift 2
			;;
		--python-bin)
			PYTHON_BIN="$2"
			shift 2
			;;
		--help|-h)
			print_help
			exit 0
			;;
		*)
			echo "Unknown option: $1"
			echo
			print_help
			exit 1
			;;
	esac
done

if [[ ! -d "$RAW_DIR/images" || ! -d "$RAW_DIR/labels" ]]; then
	echo "Missing raw input folders. Expected:"
	echo "  $RAW_DIR/images"
	echo "  $RAW_DIR/labels"
	echo "Tip: unzip platform_ml_export.zip under ml/ so raw/ exists."
	exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
	echo "Python executable not found: $PYTHON_BIN"
	exit 1
fi

echo "Using Python: $PYTHON_BIN"

if [[ -z "$INFER_IMAGES_DIR" ]]; then
	INFER_IMAGES_DIR="$OUTPUT_ROOT/test/images"
fi

REQUIRED_MODULES=("PIL" "numpy" "tqdm")
if [[ "$SKIP_TRAIN" -eq 0 ]]; then
	REQUIRED_MODULES+=("torch" "torchvision" "pycocotools")
fi
if [[ "$SKIP_INFER" -eq 0 ]]; then
	REQUIRED_MODULES+=("torch" "torchvision")
fi

MODULES_CSV="$(IFS=, ; echo "${REQUIRED_MODULES[*]}")"

if ! MODULES_CSV="$MODULES_CSV" "$PYTHON_BIN" - <<'PY'
import sys
import os

required = [name for name in os.environ.get("MODULES_CSV", "").split(",") if name]
missing = []
for name in required:
	try:
		__import__(name)
	except Exception:
		missing.append(name)

if missing:
	print("Missing Python packages: " + ", ".join(missing))
	sys.exit(1)
PY
then
	echo "Install dependencies first, e.g.:"
	echo "  cd ml && $PYTHON_BIN -m pip install -r requirements.txt"
	exit 1
fi

SPLIT_ARGS=(
	--raw-dir "$RAW_DIR"
	--output-root "$OUTPUT_ROOT"
	--train-ratio "$TRAIN_RATIO"
	--val-ratio "$VAL_RATIO"
	--test-ratio "$TEST_RATIO"
	--seed "$SEED"
)

if [[ "$NO_CLEAN" -eq 1 ]]; then
	SPLIT_ARGS+=(--no-clean-output)
else
	SPLIT_ARGS+=(--clean-output)
fi

echo "[1/2] Splitting raw dataset by station..."
"$PYTHON_BIN" split_dataset_by_station.py "${SPLIT_ARGS[@]}"

echo "[2/4] Converting splits to COCO..."
"$PYTHON_BIN" prepare_coco_from_geojson.py --dataset-dir "$OUTPUT_ROOT/train" --output "$OUTPUT_ROOT/train/annotations.json"
"$PYTHON_BIN" prepare_coco_from_geojson.py --dataset-dir "$OUTPUT_ROOT/val" --output "$OUTPUT_ROOT/val/annotations.json"

if [[ "$SKIP_TEST_COCO" -eq 0 ]]; then
	"$PYTHON_BIN" prepare_coco_from_geojson.py --dataset-dir "$OUTPUT_ROOT/test" --output "$OUTPUT_ROOT/test/annotations.json"
fi

BEST_WEIGHTS="$RUN_OUTPUT_DIR/best.pt"

if [[ "$SKIP_TRAIN" -eq 0 ]]; then
	echo "[3/4] Training model..."
	"$PYTHON_BIN" train_instance_segmentation.py \
		--train-images "$OUTPUT_ROOT/train/images" \
		--train-annotations "$OUTPUT_ROOT/train/annotations.json" \
		--val-images "$OUTPUT_ROOT/val/images" \
		--val-annotations "$OUTPUT_ROOT/val/annotations.json" \
		--output-dir "$RUN_OUTPUT_DIR" \
		--epochs "$EPOCHS" \
		--batch-size "$BATCH_SIZE" \
		--num-workers "$NUM_WORKERS" \
		--lr "$LR"
else
	echo "[3/4] Training skipped (--skip-train)."
fi

if [[ "$SKIP_INFER" -eq 0 ]]; then
	echo "[4/4] Running inference..."
	if [[ ! -f "$BEST_WEIGHTS" ]]; then
		echo "Cannot run inference: weights not found at $BEST_WEIGHTS"
		echo "Run training first or pass --skip-infer."
		exit 1
	fi
	if [[ ! -d "$INFER_IMAGES_DIR" ]]; then
		echo "Cannot run inference: images directory not found at $INFER_IMAGES_DIR"
		exit 1
	fi
	"$PYTHON_BIN" infer_instance_segmentation.py \
		--weights "$BEST_WEIGHTS" \
		--images-dir "$INFER_IMAGES_DIR" \
		--output-dir "$INFER_OUTPUT_DIR" \
		--score-threshold "$INFER_SCORE_THRESHOLD"
else
	echo "[4/4] Inference skipped (--skip-infer)."
fi

echo
echo "Pipeline completed."
echo "Train COCO: $OUTPUT_ROOT/train/annotations.json"
echo "Val COCO:   $OUTPUT_ROOT/val/annotations.json"
if [[ "$SKIP_TEST_COCO" -eq 0 ]]; then
	echo "Test COCO:  $OUTPUT_ROOT/test/annotations.json"
fi
if [[ "$SKIP_TRAIN" -eq 0 ]]; then
	echo "Best model: $BEST_WEIGHTS"
fi
if [[ "$SKIP_INFER" -eq 0 ]]; then
	echo "Predictions: $INFER_OUTPUT_DIR"
fi
