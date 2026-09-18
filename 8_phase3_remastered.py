from http import client

import cv2
import os
import threading
from pygame import key
import speech_recognition as sr
import asyncio
import edge_tts
import pygame
import uuid
import time
import psutil 
import json
from groq import Groq
from rapidfuzz import process, fuzz
from ultralytics import YOLO

# ==========================================
# PHASE 4.1 MASTER - GROQ LLM & AUTOPILOT
# ==========================================

# 1. FORCE ENVIRONMENT VARIABLE USAGE
# If this variable is missing, the script dies immediately instead of guessing.
api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    raise ValueError("CRITICAL FAILURE: GROQ_API_KEY environment variable is not set. Do not hardcode your key.")

# --- Initialize Groq Client ---
groq_client = Groq(api_key=api_key)

# 2. FIXED DIAGNOSTIC TEST
try:
    # Changed 'client.models.list()' to 'groq_client.models.list()'
    models = groq_client.models.list()
    print("✅ [GROQ INIT] SUCCESS: Connected to API.")
except Exception as e:
    print(f"❌ [GROQ INIT] FAILED: The exact error from Groq is: {e}")
    raise SystemExit("System terminating. Fix your API key or network before continuing.")

# --- GLOBAL SHARED MEMORY ---
system_status = "AUTONOMOUS PATROL - VIDEO NAVIGATION"
latest_command = "MOVE_FORWARD"
intended_trajectory = "MOVE_FORWARD"
camera_mode_request = None

# --- TELEMETRY DATA ---
gps_lat = None
gps_lon = None

# --- CONFIGURATION ---
VOICE_PROFILE = "en-IN-NeerjaNeural"
TARGET_CLASSES = [0, 2, 4, 7, 24, 28] 
VIDEO_PLAYLIST = [
    'test_etihad.f137.mp4', 
    'Ambient_video_relaxing_at_SFO_Terminal_3_Background_video_People.mp4', 
    'test_ramp.f401.mp4', 
    'People_Walking_Free_Stock_Footage,_Royalty_Free_No_Copyright_Con.mp4'
]

import pygame
pygame.mixer.init()
# ==========================================
# RIGHT BRAIN: AUDIO & NLP (Runs in Background)
# ==========================================
async def generate_audio(text, filename):
    communicate = edge_tts.Communicate(text, VOICE_PROFILE)
    await communicate.save(filename)

def _speak_sync(text):
    unique_audio_file = f"speech_{uuid.uuid4().hex}.mp3"
    print(f"\n🔊 [AEROGUARD]: {text}")
    
    asyncio.run(generate_audio(text, unique_audio_file))

    if pygame.mixer.music.get_busy():
        pygame.mixer.music.stop()

    try:
        pygame.mixer.music.load(unique_audio_file)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
        pygame.mixer.music.unload()
    except Exception:
        pass
    finally:
        if os.path.exists(unique_audio_file):
            try:
                os.remove(unique_audio_file)
            except:
                pass

def speak(text):
    threading.Thread(target=_speak_sync, args=(text,), daemon=True).start()

def extract_intent(spoken_text):
    print(f"\n🧠 [GROQ LLM] >> Analyzing context: '{spoken_text}'...")
    
    system_prompt = """
    You are the brain of an autonomous airport rover. 
    Analyze the user's command and output a pure JSON object.
    
    You must extract two things:
    1. "ui_action": The physical movement required. MUST be exactly one of: [MOVE_FORWARD, MOVE_BACKWARD, TURN_LEFT, TURN_RIGHT, EMERGENCY_STOP, SLEEP_MODE, IDLE, UNKNOWN].
    2. "verbal_response": A cool, professional, robotic confirmation sentence acknowledging the specific command. If they mention a specific object (like a plane, gate, or bag), include it in the response.
    
    ONLY output valid JSON. No markdown formatting, no other text.
    """
    
    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": spoken_text}
            ],
            model="llama-3.1-8b-instant",
            temperature=0.0,
        )
        
        response_text = chat_completion.choices[0].message.content.strip()
        
        if response_text.startswith("```json"):
            response_text = response_text[7:-3].strip()
        elif response_text.startswith("```"):
            response_text = response_text[3:-3].strip()
            
        return json.loads(response_text)
        
    except Exception as e:
        # Stop printing the traceback, just print the actual error from the API cleanly
        print(f"\n❌ [CRITICAL GROQ ERROR]: Read this carefully -> {e}\n")
        return {"ui_action": "UNKNOWN", "verbal_response": "Diagnostic failure. Check terminal for exact API error."}

def audio_engine():
    global system_status, latest_command, intended_trajectory, camera_mode_request # <--- Access memory
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = False
    recognizer.energy_threshold = 2000 
    recognizer.pause_threshold = 0.8 

    print("\n🎙️ [AUDIO THREAD] >> Booted and listening in background...")

    with sr.Microphone() as source:
        while True: 
            try:
                audio = recognizer.listen(source, timeout=None, phrase_time_limit=4)
                spoken_text = recognizer.recognize_google(audio).lower()
                
                if "system online" in spoken_text:
                    system_status = "SYSTEM ONLINE - Awaiting Command"
                    speak("System online. Diagnostics green. Ready for command.")
                    
                    while True:
                        try:
                            audio_cmd = recognizer.listen(source, timeout=10, phrase_time_limit=10)
                            command_text = recognizer.recognize_google(audio_cmd).lower()
                            
                            command_json = extract_intent(command_text)
                            action = command_json.get("ui_action", "UNKNOWN")
                            response = command_json.get("verbal_response", "Acknowledged.")
                            
                            # [NEW] Handle Camera Toggles Independently
                            if action in ["SWITCH_LIVE", "SWITCH_SIM"]:
                                global camera_mode_request
                                camera_mode_request = action
                                speak(response)
                                
                            # [EXISTING] Handle Sleep
                            elif action == "SLEEP_MODE":
                                system_status = "STANDBY - Awaiting Wake Word"
                                latest_command = "IDLE"
                                intended_trajectory = "IDLE"
                                speak("Entering standby mode.")
                                break 
                                
                            # [EXISTING] Handle Movement
                            elif action != "UNKNOWN":
                                latest_command = action
                                intended_trajectory = action 
                                speak(response)
                            else:
                                speak(response)

                        except sr.WaitTimeoutError:
                            pass 
                        except sr.UnknownValueError:
                            pass
                        except sr.RequestError:
                            break
            except sr.UnknownValueError:
                pass 
            except sr.RequestError:
                pass

def open_video_source(use_camera, video_index=0):
    if use_camera:
        cap = cv2.VideoCapture(0)

        if cap.isOpened():
            success, _ = cap.read()

            if success:
                print("[SOURCE] Live camera activated.")
                return cap, True

            cap.release()

    print("[SOURCE] Simulation video activated.")
    return cv2.VideoCapture(VIDEO_PLAYLIST[video_index]), False


# ==========================================
# LEFT BRAIN: VISION & AI (Runs on Main Thread)
# ==========================================
def vision_engine():
    global system_status, latest_command, gps_lat, gps_lon, intended_trajectory, camera_mode_request # <--- Access memory
    print("\n👁️ [VISION THREAD] >> Loading YOLOv8 Nano Neural Network...")
    model = YOLO('yolov8n.pt')

    video_index = 0
    using_live_camera = False

    cap, using_live_camera = open_video_source(False, video_index)

    fps_target = cap.get(cv2.CAP_PROP_FPS)
    wait_time = int(1000/fps_target) if fps_target > 0 else 30

    print("🛡️ [SYSTEM READY] >> Aeroguard is fully autonomous. Press 'Esc' to terminate.")

    smooth_x = 0.0
    smooth_y = 0.0
    smooth_radius = 20.0
    
    prev_time = time.time()

    while True:
        success, frame = cap.read()
        if not success:
            if using_live_camera: 
                print("[WARNING] Camera frame lost.")
                continue
            else:
                # FIXED: Only rotate the video if the current simulation video ends
                video_index = (video_index + 1) % len(VIDEO_PLAYLIST)
                cap.release() 
                cap = cv2.VideoCapture(VIDEO_PLAYLIST[video_index]) 
                wait_time = int(1000 / cap.get(cv2.CAP_PROP_FPS))
                continue

        # This will now actually execute
        frame = cv2.resize(frame, (1280, 720))
        h, w, _ = frame.shape

        curr_time = time.time()
        actual_fps = 1 / (curr_time - prev_time) if (curr_time - prev_time) > 0 else 0
        prev_time = curr_time

        if latest_command in ["MOVE_FORWARD", "MOVE_BACKWARD", "TURN_LEFT", "TURN_RIGHT"] and gps_lat is not None and gps_lon is not None:
            gps_lat += 0.00002 if latest_command == "MOVE_FORWARD" else -0.00002
            gps_lon += 0.00001 if latest_command == "TURN_RIGHT" else -0.00001

        results = model(frame, conf=0.5, classes=TARGET_CLASSES, verbose=False)
        annotated_frame = results[0].plot()

        cv2.putText(annotated_frame, f"STATUS: {system_status}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.putText(annotated_frame, f"LAST COMMAND: {latest_command.upper()}", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

        telemetry_y = h - 120
        cv2.putText(annotated_frame, f"FPS: {int(actual_fps)}", (20, telemetry_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 2)
        
        battery = psutil.sensors_battery()
        if battery:
            battery_level = battery.percent
            power_status = "[AC]" if battery.power_plugged else "[BATT]"
        else:
            battery_level = 100.0 
            power_status = "[AC]"
            
        bat_color = (0, 255, 0) if battery_level > 20 else (0, 0, 255)
        cv2.putText(annotated_frame, f"PWR: {battery_level:.0f}% {power_status}", (20, telemetry_y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, bat_color, 2)
        gps_text = f"GPS: {gps_lat:.5f}, {gps_lon:.5f}" if gps_lat is not None and gps_lon is not None else "GPS: UNAVAILABLE"
        cv2.putText(annotated_frame, gps_text, (20, telemetry_y + 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 200, 0), 2)

        cx, cy = w // 2, h - 100 
        
        target_x = 0.0
        target_y = 0.0
        target_radius = 20.0
        hud_color = (150, 150, 150)
        hud_text = "MOTORS IDLE"

        if latest_command == "MOVE_FORWARD":
            target_y = -40.0
            target_radius = 60.0
            hud_color = (0, 255, 0)
            hud_text = "THROTTLE: FWD"
        elif latest_command == "MOVE_BACKWARD":
            target_y = 40.0
            target_radius = 60.0
            hud_color = (0, 165, 255)
            hud_text = "THROTTLE: REV"
        elif latest_command == "TURN_LEFT":
            target_x = -40.0
            target_radius = 60.0
            hud_color = (255, 255, 0)
            hud_text = "STEERING: LEFT"
        elif latest_command == "TURN_RIGHT":
            target_x = 40.0
            target_radius = 60.0
            hud_color = (255, 255, 0)
            hud_text = "STEERING: RIGHT"
        elif latest_command == "EMERGENCY_STOP":
            target_radius = 55.0
            hud_color = (0, 0, 255)
            hud_text = "BRAKES ENGAGED"

        smooth_x += (target_x - smooth_x) * 0.12
        smooth_y += (target_y - smooth_y) * 0.12
        smooth_radius += (target_radius - smooth_radius) * 0.12

        if latest_command == "EMERGENCY_STOP":
            cv2.circle(annotated_frame, (cx, cy), int(smooth_radius), (0, 0, 255), -1)
        else:
            cv2.circle(annotated_frame, (cx, cy), int(smooth_radius), (50, 50, 50), -1)
            cv2.circle(annotated_frame, (cx, cy), int(smooth_radius), (255, 255, 255), 2)

        if abs(smooth_x) > 0.5 or abs(smooth_y) > 0.5:
            cv2.arrowedLine(annotated_frame, (cx, cy), (int(cx + smooth_x), int(cy + smooth_y)), hud_color, 7, tipLength=0.4)
            
        cv2.putText(annotated_frame, hud_text, (cx - 80, cy - 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, hud_color, 2)

        radar_radius = 100
        radar_cx, radar_cy = w - radar_radius - 20, radar_radius + 20
        
        cv2.circle(annotated_frame, (radar_cx, radar_cy), radar_radius, (0, 30, 0), -1)
        cv2.circle(annotated_frame, (radar_cx, radar_cy), radar_radius, (0, 255, 0), 2)
        cv2.line(annotated_frame, (radar_cx, radar_cy - radar_radius), (radar_cx, radar_cy + radar_radius), (0, 100, 0), 1)
        cv2.line(annotated_frame, (radar_cx - radar_radius, radar_cy), (radar_cx + radar_radius, radar_cy), (0, 100, 0), 1)
        cv2.circle(annotated_frame, (radar_cx, radar_cy), 4, (0, 255, 0), -1)

        path_clear = True
        evasion_action = None
        threat_name = ""

        for box in results[0].boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            obj_cx = (x1 + x2) / 2
            obj_cy = y2 
            
            map_x = int(((obj_cx / w) - 0.5) * (radar_radius * 2))
            map_y = int(-1 * (radar_radius - ((obj_cy / h) * radar_radius)))
            
            if (map_x**2 + map_y**2) <= radar_radius**2:
                cv2.circle(annotated_frame, (radar_cx + map_x, radar_cy + map_y), 4, (0, 0, 255), -1)

            if intended_trajectory not in ["IDLE", "EMERGENCY_STOP", "SLEEP_MODE"] and path_clear:
                box_height = y2 - y1
                height_ratio = box_height / h
                class_id = int(box.cls[0].item())
                
                brake_threshold = 0.6 if class_id in [0, 2, 4, 7] else 0.4
                
                if height_ratio > brake_threshold:
                    path_clear = False
                    threat_name = model.names[class_id]
                    
                    screen_center_left = w * 0.33
                    screen_center_right = w * 0.66
                    
                    if obj_cx < screen_center_left:
                        evasion_action = "TURN_RIGHT"
                    elif obj_cx > screen_center_right:
                        evasion_action = "TURN_LEFT"
                    else:
                        evasion_action = "EMERGENCY_STOP"

        if not path_clear:
            if latest_command != evasion_action:
                latest_command = evasion_action
                if evasion_action == "EMERGENCY_STOP":
                    speak(f"Critical proximity. Brakes engaged to avoid {threat_name}.")
                else:
                    dir_text = "right" if evasion_action == "TURN_RIGHT" else "left"
                    speak(f"Proximity alert. Dodging {dir_text} to avoid {threat_name}.")
        else:
            if latest_command != intended_trajectory:
                latest_command = intended_trajectory
                if intended_trajectory not in ["IDLE", "EMERGENCY_STOP", "SLEEP_MODE"]:
                    speak(f"Path clear. Resuming original trajectory.")

        cv2.imshow("Aeroguard HUD - Master System", annotated_frame)
        key = cv2.waitKey(1) & 0xFF



        # ==========================================
        # NEW: VOICE-ACTIVATED CAMERA SWITCHING
        # ==========================================
        if camera_mode_request == "SWITCH_LIVE":
            cap.release()
            cap, using_live_camera = open_video_source(True)
            wait_time = 1
            camera_mode_request = None
            speak("Switching to live surveillance camera.")# Empty the mailbox
            
        elif camera_mode_request == "SWITCH_SIM":
            cap.release()
            video_index = 0
            cap = cv2.VideoCapture(VIDEO_PLAYLIST[video_index])
            if not cap.isOpened():
                print("[ERROR] Could not open video source.")
                continue
            using_live_camera = False
            fps_target = cap.get(cv2.CAP_PROP_FPS)
            wait_time = int(1000 / fps_target) if fps_target > 0 else 30
            speak("Switching to simulation footage.")
            camera_mode_request = None # Empty the mailbox

        # ==========================================
        # EXISTING: KEYBOARD SWITCHING
        # ==========================================
        # ESC
        if key == 27:
            print("\n>> Master System shutting down...")
            break
        # C = Live Camera
        elif key == ord('c'):
            cap.release()
            cap, using_live_camera = open_video_source(True)
            wait_time = 1
            speak("Switching to live surveillance camera.")
        # V = Video Simulation
        elif key == ord('v'):
            cap.release()
            video_index = 0
            cap = cv2.VideoCapture(VIDEO_PLAYLIST[video_index])
            if not cap.isOpened():
                print("[ERROR] Could not open video source.")
                continue
            using_live_camera = False
            fps_target = cap.get(cv2.CAP_PROP_FPS)
            wait_time = int(1000 / fps_target) if fps_target > 0 else 30
            speak("Switching to simulation footage.")
# ==========================================
# BOOT SEQUENCE
# ==========================================
if __name__ == "__main__":
    print("\n" + "="*50)
    print("🚀 INITIATING AEROGUARD MASTER LAUNCH SEQUENCE")
    print("="*50)
    
    for file in os.listdir('.'):
        if file.startswith("speech_") and file.endswith(".mp3"):
            try:
                os.remove(file)
            except:
                pass
                
    audio_thread = threading.Thread(target=audio_engine, daemon=True)
    audio_thread.start()
    
    vision_engine()