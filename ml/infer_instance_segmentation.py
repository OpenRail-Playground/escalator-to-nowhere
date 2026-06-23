import argparse
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw
from torchvision.models.detection import maskrcnn_resnet50_fpn
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from torchvision.models.detection.mask_rcnn import MaskRCNNPredictor
from torchvision.transforms.functional import pil_to_tensor


def build_model(num_classes: int):
    model = maskrcnn_resnet50_fpn(weights=None)

    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)

    in_features_mask = model.roi_heads.mask_predictor.conv5_mask.in_channels
    model.roi_heads.mask_predictor = MaskRCNNPredictor(in_features_mask, 256, num_classes)
    return model


def apply_mask(image_np, mask, color=(255, 64, 0), alpha=0.4):
    overlay = image_np.copy()
    overlay[mask] = (overlay[mask] * (1 - alpha) + np.array(color) * alpha).astype(np.uint8)
    return overlay


def run_inference(model, image_path: Path, score_threshold: float, device):
    image = Image.open(image_path).convert("RGB")
    image_tensor = pil_to_tensor(image).float() / 255.0

    with torch.no_grad():
        output = model([image_tensor.to(device)])[0]

    image_np = np.array(image)
    boxes = output["boxes"].cpu().numpy()
    scores = output["scores"].cpu().numpy()
    labels = output["labels"].cpu().numpy()
    masks = output["masks"].cpu().numpy()[:, 0, :, :] if len(output["masks"]) > 0 else np.zeros((0, image_np.shape[0], image_np.shape[1]))

    keep = scores >= score_threshold
    boxes = boxes[keep]
    labels = labels[keep]
    scores = scores[keep]
    masks = masks[keep]

    for mask in masks:
        image_np = apply_mask(image_np, mask > 0.5)

    result = Image.fromarray(image_np)
    draw = ImageDraw.Draw(result)
    for box, label, score in zip(boxes, labels, scores):
        x1, y1, x2, y2 = box.tolist()
        draw.rectangle((x1, y1, x2, y2), outline=(255, 128, 0), width=2)
        draw.text((x1 + 3, y1 + 3), f"{int(label)} {score:.2f}", fill=(255, 255, 255))

    return result


def parse_args():
    parser = argparse.ArgumentParser(description="Run Mask R-CNN inference on platform images")
    parser.add_argument("--weights", required=True)
    parser.add_argument("--image", default=None)
    parser.add_argument("--images-dir", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--output-dir", default="predictions")
    parser.add_argument("--score-threshold", type=float, default=0.5)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.image and not args.images_dir:
        raise ValueError("Provide --image or --images-dir")

    checkpoint = torch.load(args.weights, map_location="cpu")
    class_names = checkpoint.get("class_names", {})
    num_classes = len(class_names) + 1 if class_names else 2

    device = torch.device(args.device)
    model = build_model(num_classes=num_classes)
    model.load_state_dict(checkpoint["model"])
    model.to(device)
    model.eval()

    if args.image:
        out_path = Path(args.output or "prediction.png")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result = run_inference(model, Path(args.image), args.score_threshold, device)
        result.save(out_path)
        print(f"Saved {out_path}")
        return

    images_dir = Path(args.images_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for image_path in sorted(images_dir.glob("*")):
        if image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            continue
        result = run_inference(model, image_path, args.score_threshold, device)
        out_path = out_dir / f"{image_path.stem}_pred.png"
        result.save(out_path)
        print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
