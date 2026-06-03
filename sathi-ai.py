import os
import re
import json        
import threading
import datetime
import queue
import subprocess
import traceback
from pathlib import Path
from typing import Optional

import psutil
import pyttsx3
import speech_recognition as sr
import pyautogui
from tkinter import *
from tkinter import scrolledtext, messagebox

# tray
import pystray
from PIL import Image, ImageDraw

# Try to import google generative ai SDK; fallback to HTTP via requests
try:
    import google.generativeai as genai  # pip install google-generativeai
    HAVE_GENAI_SDK = True
except Exception:
    genai = None
    HAVE_GENAI_SDK = False

import requests  # fallback HTTP client for API calls if needed

# ---------------------- Configuration ----------------------
ASSISTANT_NAME = "sathi"
CHATLOG_PATH = Path("chatlog.json")
CHATLOG_PATH.parent.mkdir(parents=True, exist_ok=True)

GENERATED_FOLDER = Path("generated_functions")
GENERATED_FOLDER.mkdir(exist_ok=True)

# ------------------ YOUR GOOGLE API KEY ------------------
# You provided a key like "AIzaSyAgxxxxxxxxxxxxxxxxxx"
# Keep it secret and run locally. You may also set environment variable GOOGLE_API_KEY to override.
GOOGLE_API_KEY ='AIzaSyCERiCf9lM7YNsy0N3fIYw2Pr9Xp3OU6f0'##"AIzaSyDUgB_0Dh4PGwk4z0kjoPf_W-3y_iyGeDk" #"AIzaSyAgT20BdGyBtRr4fH0KlATQEqqO6pn4J3g"

# Recommended model (change if you have access to other variants)
GEMINI_MODEL = "gemini-2.0-flash"  # try "gemini-1.5-pro" or "gemini-2.0-flash" if available

# Optional: REST base (v1beta commonly used)
GEMINI_REST_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

MAX_TOKENS = 512
TEMPERATURE = 0.6

AUTO_RUN_POLICY = 'ask_each'   # safe default
EXECUTION_TIMEOUT_SEC = 60

ACTION_KEYWORDS = [
    "pair", "bluetooth", "connect", "connect to", "install", "uninstall", "configure",
    "enable", "disable", "scan", "wifi", "hotspot", "send file", "transfer",
]

# ---------------------- Utilities ----------------------
def load_chat_history():
    if not CHATLOG_PATH.exists():
        return []
    try:
        with CHATLOG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_chat_history(history):
    try:
        with CHATLOG_PATH.open("w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("Failed to save chat history:", e)

# ---------------------- Gemini Wrapper ----------------------
class GeminiWrapper:
    def __init__(self, api_key: str, model: str = GEMINI_MODEL):
        self.api_key = api_key
        self.model = model
        # configure SDK if available
        if HAVE_GENAI_SDK:
            try:
                genai.configure(api_key=self.api_key)
            except Exception as e:
                print("Warning: genai.configure failed:", e)

    def chat_completion(self, messages):
        try:
        # Convert history -> prompt
            prompt = self._messages_to_prompt(messages)

        # Universal Gemini 1.5 / 2.0 REST endpoint
            url = f"{GEMINI_REST_BASE}/{self.model}:generateContent?key={self.api_key}"
    
            body = {
                "contents": [
                    {
                        "parts": [{"text": prompt}]
                    }
                ],
                "generationConfig": {
                    "temperature": TEMPERATURE,
                    "maxOutputTokens": MAX_TOKENS,
                }
            }

            r = requests.post(url, json=body, timeout=30)
            r.raise_for_status()
            data = r.json()

        # Extract text safely for all Gemini 2.0 output formats
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except:
                return json.dumps(data)

        except Exception as e:
            return f"[Gemini Error] {str(e)}"


    def _messages_to_prompt(self, messages):
        # combine roles into a readable prompt preserving the system message
        parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                parts.append(f"[SYSTEM]\n{content}\n")
            else:
                parts.append(f"[{role.upper()}]\n{content}\n")
        return "\n".join(parts)

# Initialize wrapper if key present
gemini_wrapper: Optional[GeminiWrapper] = None
if GOOGLE_API_KEY:
    try:
        gemini_wrapper = GeminiWrapper(GOOGLE_API_KEY, model=GEMINI_MODEL)
    except Exception as e:
        print("Failed to initialize Gemini wrapper:", e)
        gemini_wrapper = None
else:
    print("WARNING: GOOGLE_API_KEY not set; Gemini features disabled.")

# ---------------------- TTS ----------------------
engine = pyttsx3.init()
engine_lock = threading.Lock()

def speak(text: str, block: bool = False):
    if not text:
        return
    def _speak():
        try:
            with engine_lock:
                engine.say(text)
                engine.runAndWait()
        except Exception:
            pass
    t = threading.Thread(target=_speak, daemon=True)
    t.start()
    if block:
        t.join()

# ---------------------- Permissions ----------------------
permissions = {"system_control": False}
generated_approvals_path = GENERATED_FOLDER / "approvals.json"
if generated_approvals_path.exists():
    try:
        with generated_approvals_path.open("r", encoding="utf-8") as f:
            generated_approvals = json.load(f)
    except Exception:
        generated_approvals = {}
else:
    generated_approvals = {}

def save_generated_approvals():
    try:
        with generated_approvals_path.open("w", encoding="utf-8") as f:
            json.dump(generated_approvals, f, indent=2)
    except Exception:
        pass

# ---------------------- App Control ----------------------
APP_PATHS = {
    'notepad': r'C:\Windows\System32\notepad.exe',
    'calculator': r'C:\Windows\System32\calc.exe',
    'chrome': r'C:\Program Files\Google\Chrome\Application\chrome.exe',
}

# ---------------------- Chat / AI helpers ----------------------
chat_history_lock = threading.Lock()
chat_history = load_chat_history()
SYSTEM_PROMPT = f"You are {ASSISTANT_NAME}, a helpful desktop assistant. Be concise and practical."

def ask_gemini(prompt_text: str):
    global chat_history
    if not gemini_wrapper:
        return "Gemini not configured. Please set GOOGLE_API_KEY environment variable or the constant in this file."
    with chat_history_lock:
        context = [{"role": "system", "content": SYSTEM_PROMPT}] + chat_history[-12:] + [{"role": "user", "content": prompt_text}]
        answer = gemini_wrapper.chat_completion(context)
        # store in history
        chat_history.append({"role": "user", "content": prompt_text})
        chat_history.append({"role": "assistant", "content": answer})
        save_chat_history(chat_history)
        return answer

# ---------------------- Command Processing ----------------------
def extract_app_name(command_text: str):
    pattern = r"(?:open|close)\s+(?:the\s+)?([\w\s\.\-]+)"
    m = re.search(pattern, command_text, flags=re.IGNORECASE)
    if m:
        app = m.group(1).strip()
        app = re.sub(r"\bplease\b|\bnow\b|\bapp\b|\bbrowser\b","", app, flags=re.IGNORECASE).strip()
        return app.lower()
    return None

def open_app(app_name: str):
    if not permissions['system_control']:
        return "Permission denied for system control."
    if not app_name:
        return "No app specified to open."
    for k in APP_PATHS.keys():
        if k in app_name or app_name in k:
            path = APP_PATHS[k]
            if Path(path).exists():
                try:
                    subprocess.Popen([path])
                    return f"Opening {k}."
                except Exception as e:
                    return f"Failed to open {k}: {e}"
    try:
        subprocess.Popen([app_name])
        return f"Attempted to open {app_name}."
    except Exception as e:
        return f"App '{app_name}' not found or failed to launch: {e}"

def close_app(app_name: str):
    if not permissions['system_control']:
        return "Permission denied for system control."
    if not app_name:
        return "No app specified to close."
    closed = []
    for proc in psutil.process_iter(['pid','name']):
        try:
            if proc.info['name'] and app_name.lower() in proc.info['name'].lower():
                proc.kill()
                closed.append(proc.info['name'])
        except Exception:
            pass
    if closed:
        return f"Closed: {', '.join(set(closed))}"
    return f"No running process matched '{app_name}'."

def system_shutdown():
    if not permissions['system_control']:
        return "Permission denied for shutdown."
    try:
        if os.name == 'nt':
            subprocess.run(["shutdown","/s","/t","5"])
        else:
            subprocess.run(["shutdown","-h","+0"])
        return "Shutdown initiated."
    except Exception as e:
        return f"Failed to shutdown: {e}"

def volume_up():
    if not permissions['system_control']:
        return "Permission denied for volume control."
    try:
        pyautogui.press('volumeup', presses=5, interval=0.05)
        return "Volume increased."
    except Exception as e:
        return f"Volume control failed: {e}"

def volume_down():
    if not permissions['system_control']:
        return "Permission denied for volume control."
    try:
        pyautogui.press('volumedown', presses=5, interval=0.05)
        return "Volume decreased."
    except Exception as e:
        return f"Volume control failed: {e}"

def mute_volume():
    if not permissions['system_control']:
        return "Permission denied for volume control."
    try:
        pyautogui.press('volumemute')
        return "Toggled mute."
    except Exception as e:
        return f"Mute failed: {e}"

# ---------------------- Dynamic Code Generation & Execution ----------------------
def safe_filename_for_task(task_text: str) -> str:
    name = re.sub(r'[^a-zA-Z0-9_\-]', '_', task_text.strip().lower())
    name = name[:200] if len(name) > 200 else name
    return f"{name}.py"

def generate_script_for_task(task_text: str) -> str:
    if not gemini_wrapper:
        raise RuntimeError("Gemini not configured")
    prompt = f"""
You are an assistant that writes safe, minimal, runnable Python scripts to perform a TASK described by the user.

TASK DESCRIPTION:
\"\"\"{task_text}\"\"\"

REQUIREMENTS:
- Output must be ONLY valid Python code (no explanation, no markdown).
- The script must be runnable as `python script.py`.
- Use only Python standard library modules, unless required, then call OS commands via subprocess.
- The script must NOT prompt for user input.
- The script should log steps and print a final success/failure message.
- If the task is destructive, include a top comment warning and do not perform the destructive action.
- Keep the script short.
"""
    # We will ask Gemini to generate code
    messages = [{"role":"system","content":"You generate runnable Python scripts only. No extra text."},
                {"role":"user","content":prompt}]
    code = gemini_wrapper.chat_completion(messages)
    return code

def save_generated_script(filename: Path, code: str):
    header = ("# AUTO-GENERATED BY JARVIS\n"
              f"# Task generated: {datetime.datetime.utcnow().isoformat()}Z\n"
              "# Review this file in generated_functions/ before re-running if unsure.\n\n")
    filename.write_text(header + code, encoding="utf-8")

def run_generated_script(filepath: Path) -> str:
    try:
        completed = subprocess.run([os.sys.executable, str(filepath)],
                                   capture_output=True, text=True, timeout=EXECUTION_TIMEOUT_SEC)
        out = completed.stdout or ""
        err = completed.stderr or ""
        result = ""
        if out:
            result += f"STDOUT:\n{out}\n"
        if err:
            result += f"STDERR:\n{err}\n"
        result += f"Exit code: {completed.returncode}"
        return result
    except subprocess.TimeoutExpired:
        return f"Execution timed out after {EXECUTION_TIMEOUT_SEC} seconds."
    except Exception as e:
        return f"Execution failed: {e}\n{traceback.format_exc()}"

def should_attempt_dynamic(task_text: str) -> bool:
    t = task_text.lower()
    for kw in ACTION_KEYWORDS:
        if kw in t:
            return True
    if "bluetooth" in t or "pair" in t:
        return True
    return False

def execute_or_generate(task_text: str, gui_callback=None) -> str:
    filename = safe_filename_for_task(task_text)
    filepath = GENERATED_FOLDER / filename

    if filepath.exists():
        if AUTO_RUN_POLICY == 'auto':
            approved = True
        elif AUTO_RUN_POLICY == 'ask_once':
            approved = generated_approvals.get(filename, False)
            if not approved:
                approved = messagebox.askyesno(f"{ASSISTANT_NAME} - Execute Generated Script",
                                               f"A script exists for:\n\n{task_text}\n\nRun '{filename}' now?")
                generated_approvals[filename] = approved
                save_generated_approvals()
        else:
            approved = messagebox.askyesno(f"{ASSISTANT_NAME} - Execute Generated Script",
                                           f"A script exists for:\n\n{task_text}\n\nRun '{filename}' now?")
        if not approved:
            return "Execution cancelled by user."
        if gui_callback:
            gui_callback(f"Running saved script: {filename}")
        return run_generated_script(filepath)
    else:
        if gui_callback:
            gui_callback("Generating a new script for your task (requires Gemini).")
        try:
            code = generate_script_for_task(task_text)
        except Exception as e:
            return f"Failed to generate script: {e}"
        try:
            save_generated_script(filepath, code)
        except Exception as e:
            return f"Failed to save generated script: {e}"

        if AUTO_RUN_POLICY == 'auto':
            approved = True
        elif AUTO_RUN_POLICY == 'ask_once':
            approved = messagebox.askyesno(f"{ASSISTANT_NAME} - Execute Generated Script",
                                           f"A new script was generated for:\n\n{task_text}\n\nFile: {filename}\nRun it now?")
            generated_approvals[filename] = approved
            save_generated_approvals()
        else:
            approved = messagebox.askyesno(f"{ASSISTANT_NAME} - Execute Generated Script",
                                           f"A new script was generated for:\n\n{task_text}\n\nFile: {filename}\nRun it now?")
        if not approved:
            return f"Script generated and saved to {filepath}. Execution cancelled by user."
        if gui_callback:
            gui_callback(f"Running generated script: {filename}")
        return run_generated_script(filepath)

# ---------------------- Dispatcher ----------------------
def process_command(text: str, gui_callback=None):
    t = text.strip()
    t_lower = t.lower()

    if any(k in t_lower for k in ("shutdown", "shut down")):
        return system_shutdown()
    if "restart" in t_lower or "reboot" in t_lower:
        return system_shutdown()
    if "volume up" in t_lower or "increase volume" in t_lower:
        return volume_up()
    if "volume down" in t_lower or "decrease volume" in t_lower:
        return volume_down()
    if "mute" in t_lower and "unmute" not in t_lower:
        return mute_volume()

    if "open " in t_lower:
        app = extract_app_name(t)
        return open_app(app)
    if any(k in t_lower for k in ("close ", "kill ", "terminate ")):
        app = extract_app_name(t)
        return close_app(app)

    if should_attempt_dynamic(t):
        if not permissions['system_control']:
            allow = messagebox.askyesno(f"{ASSISTANT_NAME} Permission",
                                        "Generated actions may control your system (open apps, pair devices, etc.).\nAllow system control now?")
            permissions['system_control'] = bool(allow)
        if not permissions['system_control']:
            return "Permission denied for system control. Cannot run generated script."
        try:
            result = execute_or_generate(t, gui_callback=gui_callback)
            return result
        except Exception as e:
            return f"Dynamic execution error: {e}"

    return ask_gemini(t)

# ---------------------- Speech Recognition / Wake Word ----------------------
recognizer = sr.Recognizer()
mic = None
try:
    mic = sr.Microphone()
except Exception:
    mic = None

wakeword = f"hey {ASSISTANT_NAME.lower()}"
voice_queue = queue.Queue()

def audio_callback(recognizer_obj, audio):
    try:
        phrase = recognizer_obj.recognize_google(audio)
    except sr.UnknownValueError:
        return
    except Exception as e:
        print("STT error:", e)
        return
    text = phrase.lower()
    print("Heard (background):", text)
    if wakeword in text:
        voice_queue.put({"type": "wakeword"})
    else:
        voice_queue.put({"type": "speech", "text": text})

def voice_controller(gui_callback=None):
    if not mic:
        return
    stop_listening = recognizer.listen_in_background(mic, audio_callback, phrase_time_limit=5)
    pending_wakeup = False
    try:
        while True:
            item = voice_queue.get()
            if not item:
                continue
            if item.get('type') == 'wakeword':
                pending_wakeup = True
                print("Wakeword detected — waiting for command...")
                speak("Yes?")
                continue
            if item.get('type') == 'speech' and pending_wakeup:
                cmd_text = item.get('text', '')
                pending_wakeup = False
                if gui_callback:
                    gui_callback(f"You (voice): {cmd_text}")
                result = process_command(cmd_text, gui_callback=gui_callback)
                if gui_callback:
                    gui_callback(f"{ASSISTANT_NAME}: {result}")
                speak(result)
    except Exception as e:
        print("Voice controller stopped:", e)
    finally:
        try:
            stop_listening(wait_for_stop=False)
        except Exception:
            pass

# ---------------------- GUI & Tray ----------------------
root = Tk()
root.title(f"{ASSISTANT_NAME} AI Assistant")
root.geometry("900x650")
root.resizable(False, False)

chat_window = scrolledtext.ScrolledText(root, wrap=WORD, font=("Consolas", 12), bg="#111", fg="#eee")
chat_window.pack(fill=BOTH, expand=True, padx=10, pady=10)

input_frame = Frame(root)
input_frame.pack(fill=X, padx=10, pady=5)

input_var = StringVar()
input_entry = Entry(input_frame, textvariable=input_var, font=("Consolas", 14))
input_entry.pack(side=LEFT, fill=X, expand=True, padx=(0,10))

button_frame = Frame(root)
button_frame.pack(fill=X, padx=10, pady=(0,10))

send_button = Button(button_frame, text="Send", width=10)
send_button.pack(side=LEFT, padx=5)

voice_button = Button(button_frame, text="Enable Wake Word Listening", width=28)
voice_button.pack(side=LEFT, padx=5)

perm_button = Button(button_frame, text="Grant System Control", width=18)
perm_button.pack(side=LEFT, padx=5)

def gui_insert(text: str):
    chat_window.insert(END, f"{text}\n")
    chat_window.see(END)

def process_and_display(text):
    try:
        result = process_command(text, gui_callback=gui_insert)
    except Exception as e:
        result = f"Error processing command: {e}"
    gui_insert(f"{ASSISTANT_NAME}: {result}\n")
    speak(result)

def on_enter(event=None):
    user_text = input_var.get().strip()
    if not user_text:
        return
    input_var.set("")
    gui_insert(f"You: {user_text}")
    threading.Thread(target=process_and_display, args=(user_text,), daemon=True).start()

input_entry.bind('<Return>', on_enter)
send_button.config(command=on_enter)

voice_thread_obj = None
voice_listening = {'on': False}

def toggle_wakeword():
    if not mic:
        messagebox.showerror("Microphone", "No microphone found or microphone initialization failed.")
        return
    if voice_listening['on']:
        voice_listening['on'] = False
        voice_button.config(text="Enable Wake Word Listening")
        gui_insert("Wake-word listener disabled.")
    else:
        voice_listening['on'] = True
        voice_button.config(text="Disable Wake Word Listening")
        gui_insert("Wake-word listener enabled.")
        def gui_callback(payload):
            if isinstance(payload, str):
                gui_insert(payload)
        global voice_thread_obj
        voice_thread_obj = threading.Thread(target=voice_controller, args=(gui_callback,), daemon=True)
        voice_thread_obj.start()

voice_button.config(command=toggle_wakeword)

def ask_permission_gui():
    r = messagebox.askyesno(f"{ASSISTANT_NAME} Permission", "Allow system control commands? (Open/Close apps, shutdown, volume, pair devices, etc.)")
    permissions['system_control'] = r
    perm_button.config(text=("Revoke System Control" if r else "Grant System Control"))
    gui_insert(f"System control permission set to {r}")

perm_button.config(command=ask_permission_gui)

def on_closing():
    if messagebox.askokcancel("Quit", "Do you want to exit?"):
        try:
            save_generated_approvals()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            os._exit(0)

root.protocol("WM_DELETE_WINDOW", on_closing)

# Create a simple tray icon image (circle with letter J)
def make_tray_image():
    size = (64, 64)
    image = Image.new('RGBA', size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # background circle
    draw.ellipse((4,4,60,60), fill=(20,20,20,255))
    # letter J
    draw.text((22,14), "J", fill=(255,255,255,255))
    return image

tray_icon = None
def on_tray_open(icon, item):
    root.after(0, show_window)

def on_tray_hide(icon, item):
    root.after(0, hide_window)

def on_tray_quit(icon, item):
    try:
        save_generated_approvals()
    except Exception:
        pass
    try:
        icon.stop()
    except Exception:
        pass
    try:
        root.after(0, root.destroy)
    except Exception:
        os._exit(0)

def start_tray():
    global tray_icon
    image = make_tray_image()
    menu = pystray.Menu(
        pystray.MenuItem("Open Jarvis", on_tray_open),
        pystray.MenuItem("Hide Jarvis", on_tray_hide),
        pystray.MenuItem("Quit", on_tray_quit)
    )
    tray_icon = pystray.Icon("jarvis", image, f"{ASSISTANT_NAME}", menu)
    tray_icon.run()

def hide_window():
    try:
        root.withdraw()
    except Exception:
        pass

def show_window():
    try:
        root.deiconify()
        root.lift()
    except Exception:
        pass

# Welcome content
gui_insert(f"{ASSISTANT_NAME}: Hello! I am your assistant. Say 'Hey {ASSISTANT_NAME}' to start speaking, or type below.")
if chat_history:
    gui_insert(f"Loaded {len(chat_history)} messages from chat history.")
if not GOOGLE_API_KEY:
    gui_insert("Warning: GOOGLE_API_KEY environment variable not set. Gemini features will be disabled until set.")

# Start tray in separate thread
tray_thread = threading.Thread(target=start_tray, daemon=True)
tray_thread.start()

if __name__ == '__main__':
    root.mainloop()

# made by teendev's. https://teendev8.netlify.app/
