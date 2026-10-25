"""Register one of your belongings so RecallAI can recognise it.

Examples
  python -m recallai.vision.register                       # new item, interactive
  python -m recallai.vision.register --name "My Bag" --category backpack
  python -m recallai.vision.register --id 3617519a583a     # add photos to an existing item
  python -m recallai.vision.register --id 3617519a583a --from-images bag1.jpg bag2.jpg
  python -m recallai.vision.register --list

Webcam controls
  S  = save a photo, auto-cropped to the detected object (green box)
  B  = draw the box yourself, then ENTER/SPACE (C cancels)
  Q  = finish
Aim for 5-8 photos from different sides and in different lighting.
"""

import argparse
import difflib
import json
import shutil
import uuid

import cv2

from recallai import paths

BELONGINGS_DIR = paths.BELONGINGS_DIR
CAMERA_INDEX = 1


def load_model():
    from ultralytics import YOLO
    return YOLO(str(paths.YOLO_PATH))


def validate_category(category, names):
    """Return the category if YOLO knows it, else raise with suggestions."""
    category = category.strip().lower()
    valid = set(names.values())
    if category in valid:
        return category
    close = difflib.get_close_matches(category, valid, n=3)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise SystemExit(f"'{category}' is not a YOLO category.{hint}")


def largest_box(model, frame, category, conf=0.25):
    """Biggest detection of `category` (or a class YOLO confuses it with,
    e.g. mouse/cell phone) in the frame, as (x1,y1,x2,y2), or None."""
    from recallai.vision.recognition import related_categories
    accepted = related_categories(category)
    result = model(frame, conf=conf, verbose=False)[0]
    best, best_area = None, 0
    for box in result.boxes:
        if model.names[int(box.cls[0])] not in accepted:
            continue
        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
        area = (x2 - x1) * (y2 - y1)
        if area > best_area:
            best, best_area = (x1, y1, x2, y2), area
    return best


def next_index(folder):
    nums = [int(p.stem.split("_")[1]) for p in folder.glob("reference_*.jpg")
            if p.stem.split("_")[1].isdigit()]
    return max(nums, default=0) + 1


def save_reference(folder, crop):
    path = folder / f"reference_{next_index(folder)}.jpg"
    if not cv2.imwrite(str(path), crop):
        raise IOError(f"Could not write {path}")
    return path


def create_item(name, category):
    item_id = uuid.uuid4().hex[:12]
    folder = BELONGINGS_DIR / item_id
    folder.mkdir(parents=True)
    info = {"id": item_id, "name": name, "category": category}
    (folder / "info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return folder, info


def load_item(item_id):
    folder = BELONGINGS_DIR / item_id
    info_path = folder / "info.json"
    if not info_path.is_file():
        raise SystemExit(f"No belonging with id {item_id}. Try --list.")
    return folder, json.loads(info_path.read_text(encoding="utf-8"))


def import_images(model, folder, category, paths):
    """Add reference photos from image files, auto-cropping with YOLO."""
    saved = 0
    for p in paths:
        img = cv2.imread(str(p))
        if img is None:
            print(f"  skip {p}: not an image")
            continue
        box = largest_box(model, img, category)
        if box is None:
            print(f"  {p}: no {category} detected, using the whole image")
            crop = img
        else:
            from recallai.vision.recognition import crop_box
            crop = crop_box(img, box)
        print("  saved", save_reference(folder, crop).name)
        saved += 1
    return saved


def capture_from_webcam(model, folder, info, camera_index):
    from recallai.vision.recognition import crop_box

    camera = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not camera.isOpened():
        raise SystemExit(f"Could not open camera {camera_index}")

    count = 0
    print(__doc__.split("Webcam controls")[1])
    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                break
            box = largest_box(model, frame, info["category"])
            preview = frame.copy()
            if box:
                x1, y1, x2, y2 = (int(v) for v in box)
                cv2.rectangle(preview, (x1, y1), (x2, y2), (0, 255, 0), 2)
            status = (f"{info['name']}: {count} saved | "
                      + ("S=save  " if box else f"no {info['category']} seen  ")
                      + "B=draw box  Q=done")
            cv2.putText(preview, status, (10, 25), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 0) if box else (0, 165, 255), 2)
            cv2.imshow("Register Belonging", preview)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break
            crop = None
            if key == ord("s"):
                if box is None:
                    print(f"No {info['category']} detected - press B to draw the box.")
                    continue
                crop = crop_box(frame, box)
            elif key == ord("b"):
                x, y, w, h = map(int, cv2.selectROI(
                    "Draw box, then ENTER", frame, fromCenter=False))
                cv2.destroyWindow("Draw box, then ENTER")
                if w and h:
                    crop = frame[y:y + h, x:x + w]
                else:
                    print("Box cancelled.")
            if crop is not None and crop.size:
                count += 1
                print("Saved:", save_reference(folder, crop).name)
    finally:
        camera.release()
        cv2.destroyAllWindows()
    return count


def list_items():
    from recallai.vision.recognition import load_belongings
    items = load_belongings(BELONGINGS_DIR)
    if not items:
        print("Nothing registered yet.")
    for b in items:
        flag = "" if b.reference_paths else "   <-- needs photos"
        print(f"{b.id}  {b.name:<20} {b.category:<12} "
              f"{len(b.reference_paths)} photos{flag}")


def main():
    ap = argparse.ArgumentParser(description="Register a belonging")
    ap.add_argument("--name")
    ap.add_argument("--category")
    ap.add_argument("--id", help="add photos to an existing belonging")
    ap.add_argument("--from-images", nargs="+", metavar="FILE")
    ap.add_argument("--camera", type=int, default=CAMERA_INDEX)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        return list_items()

    model = load_model()
    new_item = args.id is None
    if new_item:
        name = args.name or input("Belonging name (e.g. My Black Backpack): ").strip()
        category = args.category or input("YOLO category (e.g. backpack): ")
        if not name:
            raise SystemExit("Name is required")
        category = validate_category(category, model.names)
        folder, info = create_item(name, category)
    else:
        folder, info = load_item(args.id)

    if args.from_images:
        count = import_images(model, folder, info["category"], args.from_images)
    else:
        count = capture_from_webcam(model, folder, info, args.camera)

    total = len(list(folder.glob("reference_*.jpg")))
    if total == 0:
        if new_item:
            shutil.rmtree(folder)
        raise SystemExit("No photos were saved, so nothing was registered.")

    print(f"{info['name']} [{info['id']}]: +{count} photos, {total} total.")
    if total < 5:
        print("Tip: 5-8 photos from different angles recognise much better.")

    # Pre-compute embeddings so webcam.py starts fast
    from recallai.vision.recognition import BelongingMatcher
    BelongingMatcher()


if __name__ == "__main__":
    main()
