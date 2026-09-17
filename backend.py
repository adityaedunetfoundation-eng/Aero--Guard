import os
import tempfile
from threading import Lock

import cv2
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    from ultralytics import YOLO
except (ImportError, OSError) as error:
    YOLO = None
    YOLO_IMPORT_ERROR = str(error)
else:
    YOLO_IMPORT_ERROR = ""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
CORS(app)

MODEL_PATH = os.path.join(BASE_DIR, "yolov8n.pt")
TARGET_CLASSES = [0, 2, 4, 7, 24, 28]
model = None
model_lock = Lock()


@app.get("/")
def home_page():
    return send_from_directory(BASE_DIR, "home.html")


def get_model():
    global model
    if YOLO is None:
        raise RuntimeError(f"YOLO is unavailable in this environment: {YOLO_IMPORT_ERROR}")
    if model is None:
        with model_lock:
            if model is None:
                model = YOLO(MODEL_PATH)
    return model


def serialize_result(result):
    detections = []
    names = result.names
    for box in result.boxes:
        class_id = int(box.cls[0].item())
        confidence = float(box.conf[0].item())
        x1, y1, x2, y2 = [round(float(value), 2) for value in box.xyxy[0].tolist()]
        detections.append({
            "label": names[class_id],
            "confidence": round(confidence * 100, 1),
            "box": [x1, y1, x2, y2],
        })
    return detections


def run_image(frame):
    predictions = get_model()(frame, conf=0.35, classes=TARGET_CLASSES, verbose=False)
    detections = serialize_result(predictions[0])
    return {
        "detections": detections,
        "count": len(detections),
        "highest_confidence": max((item["confidence"] for item in detections), default=0),
    }


@app.after_request
def add_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.get("/api/health")
def health():
    return jsonify({
        "status": "online",
        "model_available": YOLO is not None and os.path.exists(MODEL_PATH),
        "model": "yolov8n.pt",
        "model_error": YOLO_IMPORT_ERROR,
    })


@app.get("/api/status")
def status():
    return jsonify({
        "mission": "STANDBY",
        "source": "Simulation feed",
        "battery": 87,
        "gps": None,
        "uplink_ms": 42,
    })


@app.post("/api/detect-image")
def detect_image():
    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify({"error": "No image file provided"}), 400
    data = uploaded.read()
    frame = cv2.imdecode(__import__("numpy").frombuffer(data, dtype=__import__("numpy").uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify({"error": "Could not decode image"}), 400
    try:
        return jsonify(run_image(frame))
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 503


@app.post("/api/detect-video")
def detect_video():
    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify({"error": "No video file provided"}), 400
    suffix = os.path.splitext(uploaded.filename or "clip.mp4")[1] or ".mp4"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
            uploaded.save(temporary.name)
            temporary_path = temporary.name
        capture = cv2.VideoCapture(temporary_path)
        frame_total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        sample_step = max(frame_total // 6, 1)
        frame_index = 0
        detections = []
        while True:
            success, frame = capture.read()
            if not success:
                break
            if frame_index % sample_step == 0 and len(detections) < 6:
                try:
                    detections.extend(run_image(frame)["detections"])
                except RuntimeError as error:
                    return jsonify({"error": str(error)}), 503
            frame_index += 1
        capture.release()
        unique = {}
        for item in detections:
            key = item["label"]
            if key not in unique or item["confidence"] > unique[key]["confidence"]:
                unique[key] = item
        result = list(unique.values())
        return jsonify({
            "frames_sampled": min(frame_index, 6),
            "detections": result,
            "count": len(result),
            "highest_confidence": max((item["confidence"] for item in result), default=0),
        })
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
