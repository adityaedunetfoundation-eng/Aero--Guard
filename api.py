import os
import subprocess
import sys

from backend import app

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VISION_SCRIPT = os.path.join(BASE_DIR, "8_phase3_remastered.py")


def start_vision_engine():
    if not os.path.exists(VISION_SCRIPT):
        raise FileNotFoundError(f"Vision script not found: {VISION_SCRIPT}")
    print("[AEROGUARD] Starting autonomous vision and voice engine...")
    return subprocess.Popen([sys.executable, VISION_SCRIPT], cwd=BASE_DIR)


if __name__ == "__main__":
    vision_process = None
    try:
        vision_process = start_vision_engine()
        print("[AEROGUARD] Dashboard API: http://127.0.0.1:5000")
        app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\n[AEROGUARD] Shutting down...")
    finally:
        if vision_process and vision_process.poll() is None:
            vision_process.terminate()
            vision_process.wait(timeout=10)
        print("[AEROGUARD] All services stopped.")
