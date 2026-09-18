from backend import app

if __name__ == "__main__":
    try:
        print("[AEROGUARD] Dashboard API: http://127.0.0.1:5000")
        app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\n[AEROGUARD] Shutting down...")
