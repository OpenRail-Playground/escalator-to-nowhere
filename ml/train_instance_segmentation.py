import argparse
import json
import os
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from pycocotools.coco import COCO
from torch.utils.data import DataLoader, Dataset
from torchvision.models.detection import maskrcnn_resnet50_fpn
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from torchvision.models.detection.mask_rcnn import MaskRCNNPredictor
from torchvision.transforms.functional import pil_to_tensor
from tqdm import tqdm


class CocoInstanceDataset(Dataset):
    def __init__(self, images_dir: str, annotation_file: str):
        self.images_dir = Path(images_dir)
        self.coco = COCO(annotation_file)
        self.image_ids = sorted(self.coco.getImgIds())

        categories = sorted(self.coco.loadCats(self.coco.getCatIds()), key=lambda c: c["id"])
        self.cat_id_to_label = {cat["id"]: idx + 1 for idx, cat in enumerate(categories)}
        self.label_to_name = {idx + 1: cat["name"] for idx, cat in enumerate(categories)}

    def __len__(self):
        return len(self.image_ids)

    def __getitem__(self, idx):
        image_id = self.image_ids[idx]
        image_info = self.coco.loadImgs(image_id)[0]
        image_path = self.images_dir / image_info["file_name"]

        image = Image.open(image_path).convert("RGB")
        image_tensor = pil_to_tensor(image).float() / 255.0

        ann_ids = self.coco.getAnnIds(imgIds=image_id)
        anns = self.coco.loadAnns(ann_ids)

        boxes = []
        labels = []
        masks = []
        areas = []
        iscrowd = []

        for ann in anns:
            if ann.get("iscrowd", 0):
                continue

            x, y, w, h = ann["bbox"]
            if w <= 1 or h <= 1:
                continue

            box = [x, y, x + w, y + h]
            mask = self.coco.annToMask(ann)

            boxes.append(box)
            labels.append(self.cat_id_to_label[ann["category_id"]])
            masks.append(mask)
            areas.append(float(ann.get("area", w * h)))
            iscrowd.append(0)

        if boxes:
            masks_array = np.stack(masks, axis=0).astype(np.uint8, copy=False)
            target = {
                "boxes": torch.tensor(boxes, dtype=torch.float32),
                "labels": torch.tensor(labels, dtype=torch.int64),
                "masks": torch.from_numpy(masks_array),
                "image_id": torch.tensor([image_id], dtype=torch.int64),
                "area": torch.tensor(areas, dtype=torch.float32),
                "iscrowd": torch.tensor(iscrowd, dtype=torch.int64),
            }
        else:
            target = {
                "boxes": torch.zeros((0, 4), dtype=torch.float32),
                "labels": torch.zeros((0,), dtype=torch.int64),
                "masks": torch.zeros((0, image_tensor.shape[1], image_tensor.shape[2]), dtype=torch.uint8),
                "image_id": torch.tensor([image_id], dtype=torch.int64),
                "area": torch.zeros((0,), dtype=torch.float32),
                "iscrowd": torch.zeros((0,), dtype=torch.int64),
            }

        return image_tensor, target


def collate_fn(batch):
    return tuple(zip(*batch))


def build_model(num_classes: int):
    model = maskrcnn_resnet50_fpn(weights="DEFAULT")

    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)

    in_features_mask = model.roi_heads.mask_predictor.conv5_mask.in_channels
    model.roi_heads.mask_predictor = MaskRCNNPredictor(in_features_mask, 256, num_classes)
    return model


def train_one_epoch(model, loader, optimizer, scaler, device):
    model.train()
    running_loss = 0.0

    for images, targets in tqdm(loader, desc="train", leave=False):
        images = [img.to(device, non_blocking=True) for img in images]
        targets = [{k: v.to(device, non_blocking=True) for k, v in t.items()} for t in targets]

        optimizer.zero_grad(set_to_none=True)

        with torch.autocast(device_type=device.type, enabled=(device.type == "cuda")):
            loss_dict = model(images, targets)
            loss = sum(loss_dict.values())

        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()

        running_loss += float(loss.detach().cpu())

    return running_loss / max(len(loader), 1)


def run_quick_val(model, loader, device):
    model.eval()
    total_preds = 0

    with torch.no_grad():
        for images, _ in tqdm(loader, desc="val", leave=False):
            images = [img.to(device, non_blocking=True) for img in images]
            outputs = model(images)
            total_preds += sum(int(o["scores"].shape[0]) for o in outputs)

    return total_preds


def save_checkpoint(path, model, optimizer, epoch, train_loss, class_names):
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "epoch": epoch,
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "train_loss": train_loss,
            "class_names": class_names,
        },
        path,
    )


def parse_args():
    default_device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
    parser = argparse.ArgumentParser(description="Train Mask R-CNN for platform instance segmentation")
    parser.add_argument("--train-images", required=True)
    parser.add_argument("--train-annotations", required=True)
    parser.add_argument("--val-images", default=None)
    parser.add_argument("--val-annotations", default=None)
    parser.add_argument("--output-dir", default="runs/platform_instance")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--num-workers", type=int, default=4)
    parser.add_argument("--prefetch-factor", type=int, default=2)
    parser.add_argument(
        "--persistent-workers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep DataLoader workers alive between epochs (default: enabled)",
    )
    parser.add_argument("--lr", type=float, default=0.005)
    parser.add_argument("--weight-decay", type=float, default=0.0005)
    parser.add_argument("--resume", default=None)
    parser.add_argument("--device", default=default_device)
    return parser.parse_args()


def main():
    args = parse_args()
    device = torch.device(args.device)

    if device.type == "cuda":
        torch.backends.cudnn.benchmark = True
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.set_float32_matmul_precision("high")

    loader_kwargs = {
        "num_workers": args.num_workers,
        "collate_fn": collate_fn,
        "pin_memory": device.type == "cuda",
    }
    if args.num_workers > 0:
        loader_kwargs["persistent_workers"] = args.persistent_workers
        loader_kwargs["prefetch_factor"] = args.prefetch_factor

    train_ds = CocoInstanceDataset(args.train_images, args.train_annotations)
    num_classes = len(train_ds.label_to_name) + 1

    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        shuffle=True,
        **loader_kwargs,
    )

    val_loader = None
    if args.val_images and args.val_annotations:
        val_ds = CocoInstanceDataset(args.val_images, args.val_annotations)
        val_loader = DataLoader(
            val_ds,
            batch_size=1,
            shuffle=False,
            **loader_kwargs,
        )

    model = build_model(num_classes=num_classes).to(device)

    optimizer = torch.optim.SGD(
        [p for p in model.parameters() if p.requires_grad],
        lr=args.lr,
        momentum=0.9,
        weight_decay=args.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.1)
    scaler = torch.amp.GradScaler(enabled=(device.type == "cuda"))

    start_epoch = 0
    best_loss = float("inf")

    if args.resume:
        ckpt = torch.load(args.resume, map_location=device)
        model.load_state_dict(ckpt["model"])
        optimizer.load_state_dict(ckpt["optimizer"])
        start_epoch = int(ckpt["epoch"]) + 1
        best_loss = float(ckpt.get("train_loss", best_loss))

    out_dir = Path(args.output_dir)
    class_names = {str(k): v for k, v in train_ds.label_to_name.items()}

    for epoch in range(start_epoch, args.epochs):
        train_loss = train_one_epoch(model, train_loader, optimizer, scaler, device)
        scheduler.step()

        print(f"epoch={epoch + 1}/{args.epochs} train_loss={train_loss:.4f}")

        if val_loader is not None:
            val_pred_count = run_quick_val(model, val_loader, device)
            print(f"epoch={epoch + 1}/{args.epochs} val_pred_instances={val_pred_count}")

        save_checkpoint(out_dir / "last.pt", model, optimizer, epoch, train_loss, class_names)

        if train_loss < best_loss:
            best_loss = train_loss
            save_checkpoint(out_dir / "best.pt", model, optimizer, epoch, train_loss, class_names)

    meta = {
        "num_classes": num_classes,
        "class_names": class_names,
        "epochs": args.epochs,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Training complete. Best loss: {best_loss:.4f}")


if __name__ == "__main__":
    main()
