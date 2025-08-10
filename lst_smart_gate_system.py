# ===================================================================================
# Gate Control System for Raspberry Pi - NO IR SENSOR
# Version: 7.3
#
# --- SYSTEM NOTES ---
# 1. IR SENSOR REMOVED: This version of the code has completely removed the IR
#    sensor and all associated unauthorized access detection logic.
#
# 2. ACTIVE-LOW COMPONENTS: This code assumes your LEDs, Buzzers, and Relay are
#    "active-low." This means they turn ON when the GPIO pin goes LOW (0V) and
#    turn OFF when the pin goes HIGH (3.3V). This is to fix the issue where
#    components turn on immediately at boot.
#
# 3. LOGIC LEVEL SHIFTERS (Reminder): For safety and to prevent damage,
#    use a logic level shifter for any 5V components (like the PN532) that
#    communicate with the Raspberry Pi's 3.3V GPIO pins.
# ===================================================================================

import time
import threading
import logging
from datetime import datetime, timedelta
import board
import busio
import adafruit_pn532.i2c as PN532
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from collections import deque
import queue
import json
import os
from typing import Dict, List, Optional, Tuple, Set, Any
import signal
import sys
from dataclasses import dataclass
from enum import Enum

# Try to import RPi.GPIO, fallback to mock for testing
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    print("Warning: RPi.GPIO not available. Using mock GPIO for testing.")
    GPIO_AVAILABLE = False
    class MockGPIO:
        BCM, OUT, IN, HIGH, LOW, PUD_UP, PUD_DOWN, RISING, FALLING, BOTH = "BCM", "OUT", "IN", 1, 0, "PUD_UP", "PUD_DOWN", "RISING", "FALLING", "BOTH"
        @staticmethod
        def setmode(mode): pass
        @staticmethod
        def setup(pin, mode, pull_up_down=None): pass
        @staticmethod
        def output(pin, state): pass
        @staticmethod
        def input(pin): return 1
        @staticmethod
        def PWM(pin, frequency): return MockPWM()
        @staticmethod
        def add_event_detect(pin, edge, callback=None, bouncetime=None): pass
        @staticmethod
        def remove_event_detect(pin): pass
        @staticmethod
        def cleanup(): pass
    class MockPWM:
        def start(self, duty_cycle): pass
        def ChangeDutyCycle(self, duty_cycle): pass
        def stop(self): pass
    GPIO = MockGPIO()

# Configure logging
from logging.handlers import RotatingFileHandler
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('gate_system.log', maxBytes=1024*1024, backupCount=3),
        logging.StreamHandler()
    ]
)

# System configuration
CONFIG_FILE = 'gate_config.json'
DEFAULT_CONFIG = {
    'security': {'max_attempts': 3, 'lockout_time': 300, 'card_cooldown': 5, 'auto_close_delay': 10},
    'hardware': {'servo_frequency': 50, 'servo_open_duty': 7.5, 'servo_close_duty': 2.5},
    'logging': {'max_log_size': 1024 * 1024, 'backup_count': 3, 'log_level': 'INFO'}
}

# Define enums
class GateState(Enum):
    CLOSED, OPENING, OPEN, CLOSING, STOPPED, ERROR, UNKNOWN = "CLOSED", "OPENING", "OPEN", "CLOSING", "STOPPED", "ERROR", "UNKNOWN"
class LockState(Enum):
    LOCKED, UNLOCKED, UNKNOWN = "LOCKED", "UNLOCKED", "UNKNOWN"
class SecurityLevel(Enum):
    NORMAL, HIGH, EMERGENCY = "NORMAL", "HIGH", "EMERGENCY"

@dataclass
class SecurityConfig: max_attempts: int; lockout_time: int; card_cooldown: int; auto_close_delay: int
@dataclass
class HardwareConfig: servo_frequency: int; servo_open_duty: float; servo_close_duty: float
@dataclass
class LoggingConfig: max_log_size: int; backup_count: int; log_level: str

class ConfigurationManager:
    def __init__(self) -> None:
        self.config = self.load_config(); self.validate_config()
    def load_config(self) -> Dict[str, Any]:
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f: config = json.load(f)
                logging.info("Configuration loaded successfully"); return config
            else:
                logging.info("No config file found, using defaults"); return DEFAULT_CONFIG.copy()
        except Exception as e:
            logging.error(f"Error loading configuration: {e}"); return DEFAULT_CONFIG.copy()
    def save_config(self) -> None:
        try:
            with open(CONFIG_FILE, 'w') as f: json.dump(self.config, f, indent=4)
            logging.info("Configuration saved successfully")
        except Exception as e: logging.error(f"Error saving configuration: {e}")
    def validate_config(self) -> None: # Basic validation and default-filling
        for section, values in DEFAULT_CONFIG.items():
            if section not in self.config: self.config[section] = {}
            for key, default_value in values.items():
                if key not in self.config[section]: self.config[section][key] = default_value
        self.save_config()
    def get_security_config(self) -> SecurityConfig: return SecurityConfig(**self.config['security'])
    def get_hardware_config(self) -> HardwareConfig: return HardwareConfig(**self.config['hardware'])
    def get_logging_config(self) -> LoggingConfig: return LoggingConfig(**self.config['logging'])

# ==============================================================================
# === HARDWARE DEFINITIONS UPDATED (IR SENSOR REMOVED) =========================
# ==============================================================================
HARDWARE_PINS = {
    'SERVO_PIN': 18,            # GPIO18 for servo motor control (Signal)
    'RELAY_PIN': 17,            # GPIO17 for relay control (IN) -> Controls the 12V lock
    'GREEN_LED_BUZZER_PIN': 22, # GPIO22 for Green LED and Buzzer 1
    'RED_LED_BUZZER_PIN': 27,   # GPIO27 for Red LED and Buzzer 2
}

# ==============================================================================
# === HARDWARE CONTROLLER REVISED (NO IR SENSOR, INVERTED LED LOGIC) ===========
# ==============================================================================
class RPiHardwareController:
    def __init__(self, config: HardwareConfig) -> None:
        self.config = config
        self.running = True
        self.gate_state, self.lock_state = GateState.CLOSED, LockState.LOCKED
        self.servo_pwm = None
        self.event_queue: queue.Queue[str] = queue.Queue()
        self.initialize_gpio()
        logging.info("RPi Hardware Controller initialized successfully")

    def initialize_gpio(self) -> None:
        try:
            GPIO.setmode(GPIO.BCM)
            # Setup all output pins
            for pin in HARDWARE_PINS.values(): GPIO.setup(pin, GPIO.OUT)
            
            # Initialize servo PWM
            self.servo_pwm = GPIO.PWM(HARDWARE_PINS['SERVO_PIN'], self.config.servo_frequency)
            self.servo_pwm.start(self.config.servo_close_duty)
            
            # Set output pins to their 'off' state. For active-low, 'off' is HIGH.
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.HIGH)            # Relay OFF = Locked
            GPIO.output(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], GPIO.HIGH) # Turn green LED/Buzzer OFF
            GPIO.output(HARDWARE_PINS['RED_LED_BUZZER_PIN'], GPIO.HIGH)   # Turn red LED/Buzzer OFF
            
            # No event detection is needed as the only sensor (IR) has been removed.
            logging.info("GPIO initialization completed successfully (No event detection).")
            
        except Exception as e:
            logging.error(f"Error initializing GPIO: {e}"); raise

    def open_gate(self) -> bool:
        try:
            if self.gate_state == GateState.OPEN: return True
            logging.info("Opening gate..."); self.gate_state = GateState.OPENING
            self.unlock_gate(); time.sleep(0.5)
            self.servo_pwm.ChangeDutyCycle(self.config.servo_open_duty); time.sleep(1.5)
            self.servo_pwm.ChangeDutyCycle(0)
            self.gate_state = GateState.OPEN
            self.event_queue.put("GATE_OPENED")
            logging.info("Gate is now assumed to be OPEN"); return True
        except Exception as e:
            logging.error(f"Error opening gate: {e}"); self.gate_state = GateState.ERROR; return False

    def close_gate(self) -> bool:
        try:
            if self.gate_state == GateState.CLOSED: return True
            logging.info("Closing gate..."); self.gate_state = GateState.CLOSING
            self.servo_pwm.ChangeDutyCycle(self.config.servo_close_duty); time.sleep(1.5)
            self.servo_pwm.ChangeDutyCycle(0)
            self.gate_state = GateState.CLOSED
            logging.info("Gate is now assumed to be CLOSED"); time.sleep(0.5)
            self.lock_gate()
            self.event_queue.put("GATE_CLOSED"); return True
        except Exception as e:
            logging.error(f"Error closing gate: {e}"); self.gate_state = GateState.ERROR; return False

    def lock_gate(self) -> bool: # Turns relay OFF (assumes active-low)
        try:
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.HIGH)
            self.lock_state = LockState.LOCKED; logging.info("Gate locked (Relay OFF)"); return True
        except Exception as e: logging.error(f"Error locking gate: {e}"); return False

    def unlock_gate(self) -> bool: # Turns relay ON (assumes active-low)
        try:
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.LOW)
            self.lock_state = LockState.UNLOCKED; logging.info("Gate unlocked (Relay ON)"); return True
        except Exception as e: logging.error(f"Error unlocking gate: {e}"); return False
            
    def _flash_pin(self, pin_num: int, duration: float): # Inverted logic
        try:
            GPIO.output(pin_num, GPIO.LOW)  # Turn ON
            time.sleep(duration)
            GPIO.output(pin_num, GPIO.HIGH) # Turn OFF
        except Exception as e: logging.error(f"Error flashing pin {pin_num}: {e}")

    def access_granted_feedback(self) -> None:
        self._flash_pin(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], 0.2)

    def access_denied_feedback(self) -> None:
        self._flash_pin(HARDWARE_PINS['RED_LED_BUZZER_PIN'], 1.0)

    def get_status(self) -> Dict[str, Any]:
        try:
            return {
                'gate_state': self.gate_state.value,
                'lock_state': self.lock_state.value,
                'connected': True,
                'last_update': datetime.now().isoformat()
            }
        except Exception as e:
            logging.error(f"Error getting hardware status: {e}"); return {'gate_state': 'ERROR', 'connected': False, 'error': str(e)}

    def cleanup(self) -> None:
        try:
            self.running = False
            if self.servo_pwm: self.servo_pwm.stop()
            # Set all outputs to their OFF state (HIGH for active-low)
            for pin in HARDWARE_PINS.values(): GPIO.output(pin, GPIO.HIGH)
            GPIO.cleanup()
            logging.info("GPIO cleanup completed")
        except Exception as e: logging.error(f"Error during GPIO cleanup: {e}")

# ==============================================================================
# === CORE LOGIC CLASSES (LARGELY UNCHANGED) ===================================
# ==============================================================================
class NFCCardManager:
    def __init__(self) -> None:
        self.pn532, self.authorized_cards, self.card_names, self.last_card_time, self.failed_attempts, self.lockout_until = None, set(), {}, {}, {}, {}
        self.load_authorized_cards(); self.initialize_nfc()
    def initialize_nfc(self) -> None:
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            self.pn532 = PN532.PN532_I2C(i2c, debug=False)
            ic, ver, rev, support = self.pn532.firmware_version
            logging.info(f"Found PN532 with firmware version: {ver}.{rev}")
            self.pn532.SAM_configuration(); logging.info("NFC reader initialized successfully")
        except Exception as e: logging.error(f"Error initializing NFC reader: {e}"); self.pn532 = None
    def load_authorized_cards(self) -> None:
        try:
            if os.path.exists('authorized_cards.json'):
                with open('authorized_cards.json', 'r') as f: data = json.load(f)
                self.authorized_cards, self.card_names = set(data.get('cards', [])), data.get('names', {})
                logging.info(f"Loaded {len(self.authorized_cards)} authorized cards")
            else: self.save_authorized_cards()
        except Exception as e: logging.error(f"Error loading authorized cards: {e}")
    def save_authorized_cards(self) -> None:
        try:
            with open('authorized_cards.json', 'w') as f: json.dump({'cards': list(self.authorized_cards), 'names': self.card_names}, f, indent=4)
        except Exception as e: logging.error(f"Error saving authorized cards: {e}")
    def read_card(self) -> Optional[Tuple[str, str]]:
        if not self.pn532: return None
        try:
            uid = self.pn532.read_passive_target(timeout=0.5)
            if uid is not None:
                card_uid = ''.join([hex(i)[2:].upper().zfill(2) for i in uid])
                card_name = self.card_names.get(card_uid, "Unknown Card")
                logging.info(f"Card detected: {card_uid} ({card_name})"); return card_uid, card_name
        except Exception: pass # Suppress frequent "did not receive ACK" errors on read timeout
        return None
    def is_card_authorized(self, card_uid: str, security_config: SecurityConfig) -> bool:
        current_time = datetime.now()
        if card_uid in self.lockout_until and current_time < self.lockout_until[card_uid]: return False
        if card_uid in self.last_card_time and (current_time - self.last_card_time[card_uid]).total_seconds() < security_config.card_cooldown: return False
        self.last_card_time[card_uid] = current_time
        if card_uid in self.authorized_cards: return True
        self.failed_attempts[card_uid] = self.failed_attempts.get(card_uid, 0) + 1
        if self.failed_attempts[card_uid] >= security_config.max_attempts:
            self.lockout_until[card_uid] = current_time + timedelta(seconds=security_config.lockout_time)
        return False
    def add_card(self, card_uid: str, card_name: str) -> None: self.authorized_cards.add(card_uid); self.card_names[card_uid] = card_name; self.save_authorized_cards()
    def remove_card(self, card_uid: str) -> None:
        if card_uid in self.authorized_cards: self.authorized_cards.remove(card_uid); self.card_names.pop(card_uid, None); self.save_authorized_cards()

class AccessLogManager:
    def __init__(self, max_entries: int = 1000) -> None:
        self.max_entries = max_entries; self.access_log: deque = deque(maxlen=max_entries); self.log_file = 'access_log.json'; self.load_log()
    def load_log(self) -> None:
        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r') as f: self.access_log = deque(json.load(f), maxlen=self.max_entries)
        except Exception as e: logging.error(f"Error loading access log: {e}")
    def save_log(self) -> None:
        try:
            with open(self.log_file, 'w') as f: json.dump(list(self.access_log), f, indent=2)
        except Exception as e: logging.error(f"Error saving access log: {e}")
    def log_access(self, card_uid: str, card_name: str, granted: bool, reason: str = "") -> None:
        entry = {'timestamp': datetime.now().isoformat(), 'card_uid': card_uid, 'card_name': card_name, 'access_granted': granted, 'reason': reason}
        self.access_log.append(entry); self.save_log()
        logging.info(f"Access {'GRANTED' if granted else 'DENIED'}: {card_name} ({card_uid}) - {reason}")
    def log_event(self, event_type: str, description: str) -> None:
        entry = {'timestamp': datetime.now().isoformat(), 'event_type': event_type, 'description': description}
        self.access_log.append(entry); self.save_log(); logging.info(f"Event logged: {event_type} - {description}")
    def get_recent_entries(self, count: int = 50) -> List[Dict[str, Any]]: return list(self.access_log)[-count:]

class GateControlSystem:
    def __init__(self) -> None:
        self.config_manager = ConfigurationManager()
        self.security_config = self.config_manager.get_security_config()
        self.hardware_config = self.config_manager.get_hardware_config()
        self.hardware_controller = RPiHardwareController(self.hardware_config)
        self.nfc_manager = NFCCardManager()
        self.access_log = AccessLogManager()
        self.running, self.security_level, self.last_access_time, self.auto_close_timer = True, SecurityLevel.NORMAL, None, None
        self.start_control_loop()
    def start_control_loop(self) -> None:
        control_thread = threading.Thread(target=self._control_loop, name="Main Control", daemon=True); control_thread.start()
        logging.info("Started main control loop")
    def _control_loop(self) -> None:
        while self.running:
            try:
                card_data = self.nfc_manager.read_card()
                if card_data: self._handle_card_access(*card_data)
                self._process_hardware_events()
                time.sleep(0.1)
            except Exception as e: logging.error(f"Error in main control loop: {e}"); time.sleep(1)
    def _handle_card_access(self, card_uid: str, card_name: str) -> None:
        if self.nfc_manager.is_card_authorized(card_uid, self.security_config): self._grant_access(card_uid, card_name)
        else: self._deny_access(card_uid, card_name, "Unauthorized card")
    def _grant_access(self, card_uid: str, card_name: str) -> None:
        logging.info(f"Access granted to {card_name} ({card_uid})")
        self.access_log.log_access(card_uid, card_name, True, "Valid card")
        threading.Thread(target=self.hardware_controller.access_granted_feedback).start()
        if self.hardware_controller.open_gate(): self.last_access_time = datetime.now(); self._set_auto_close_timer()
    def _deny_access(self, card_uid: str, card_name: str, reason: str) -> None:
        logging.warning(f"Access denied to {card_name} ({card_uid}): {reason}")
        self.access_log.log_access(card_uid, card_name, False, reason)
        threading.Thread(target=self.hardware_controller.access_denied_feedback).start()
    def _process_hardware_events(self) -> None: # Simplified as there are no sensor events
        try:
            event = self.hardware_controller.event_queue.get_nowait()
            if event == "GATE_CLOSED": self._handle_gate_closed()
            elif event == "GATE_OPENED": self._handle_gate_opened()
        except queue.Empty: pass
        except Exception as e: logging.error(f"Error processing hardware events: {e}")
    def _handle_gate_closed(self) -> None:
        logging.info("Gate is now closed.")
        if self.auto_close_timer: self.auto_close_timer.cancel(); self.auto_close_timer = None
    def _handle_gate_opened(self) -> None:
        logging.info("Gate is now open.")
        if not (self.auto_close_timer and self.auto_close_timer.is_alive()): self._set_auto_close_timer()
    def _set_auto_close_timer(self) -> None:
        if self.auto_close_timer: self.auto_close_timer.cancel()
        delay = self.security_config.auto_close_delay
        self.auto_close_timer = threading.Timer(delay, self._auto_close_gate); self.auto_close_timer.start()
        logging.info(f"Auto-close timer set for {delay} seconds")
    def _auto_close_gate(self) -> None:
        if self.hardware_controller.gate_state == GateState.OPEN:
            logging.info("Auto-closing gate due to timer expiry."); self.hardware_controller.close_gate()
        self.auto_close_timer = None
    def manual_open_gate(self) -> bool:
        self.access_log.log_event("MANUAL_OPEN", "Gate opened manually"); return self.hardware_controller.open_gate()
    def manual_close_gate(self) -> bool:
        self.access_log.log_event("MANUAL_CLOSE", "Gate closed manually"); return self.hardware_controller.close_gate()
    def emergency_stop(self) -> None:
        self.access_log.log_event("EMERGENCY_STOP", "Emergency stop activated")
        if self.auto_close_timer: self.auto_close_timer.cancel(); self.auto_close_timer = None
        if self.hardware_controller.servo_pwm: self.hardware_controller.servo_pwm.ChangeDutyCycle(0)
        self.hardware_controller.gate_state = GateState.STOPPED; self.hardware_controller.lock_gate()
    def get_system_status(self) -> Dict[str, Any]:
        hardware_status = self.hardware_controller.get_status()
        timer_active = self.auto_close_timer is not None and self.auto_close_timer.is_alive()
        return {'system_running': self.running, 'hardware': hardware_status, 'nfc_reader_available': self.nfc_manager.pn532 is not None, 'authorized_cards_count': len(self.nfc_manager.authorized_cards), 'auto_close_timer_active': timer_active, 'timestamp': datetime.now().isoformat()}
    def shutdown(self) -> None:
        if not self.running: return
        logging.info("Shutting down gate control system"); self.running = False
        if self.auto_close_timer: self.auto_close_timer.cancel()
        self.hardware_controller.cleanup(); logging.info("Gate control system shutdown complete")

class GateControlGUI:
    def __init__(self, gate_system: GateControlSystem) -> None:
        self.gate_system, self.root = gate_system, tk.Tk()
        self.root.title("Gate Control System"); self.root.geometry("800x600"); self.create_widgets(); self.update_status(); self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    def create_widgets(self) -> None:
        main_frame = ttk.Frame(self.root, padding="10"); main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1); self.root.rowconfigure(0, weight=1)
        ttk.Label(main_frame, text="Gate Control System", font=("Arial", 16, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 10))
        status_frame = ttk.LabelFrame(main_frame, text="System Status", padding="10"); status_frame.grid(row=1, column=0, sticky=(tk.W, tk.E)); main_frame.columnconfigure(0, weight=1)
        self.status_text = tk.Text(status_frame, height=8, width=80, wrap=tk.WORD); self.status_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        control_frame = ttk.LabelFrame(main_frame, text="Manual Controls", padding="10"); control_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=10)
        ttk.Button(control_frame, text="Open Gate", command=self.open_gate).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Close Gate", command=self.close_gate).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Emergency Stop", command=self.emergency_stop).pack(side=tk.LEFT, padx=5)
        log_frame = ttk.LabelFrame(main_frame, text="Recent Access Log", padding="10"); log_frame.grid(row=3, column=0, sticky=(tk.W, tk.E, tk.N, tk.S)); main_frame.rowconfigure(3, weight=1)
        self.log_text = tk.Text(log_frame, height=10, width=80, wrap=tk.WORD); self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    def update_status(self) -> None:
        try:
            status = self.gate_system.get_system_status(); self.status_text.delete(1.0, tk.END)
            status_lines = [f"System Running: {status.get('system_running', 'Unknown')}", f"Gate State: {status.get('hardware', {}).get('gate_state', 'Unknown')}", f"Lock State: {status.get('hardware', {}).get('lock_state', 'Unknown')}", f"NFC Reader: {'Available' if status.get('nfc_reader_available') else 'Not Available'}", f"Authorized Cards: {status.get('authorized_cards_count', 0)}", f"Auto-Close Timer: {'Active' if status.get('auto_close_timer_active') else 'Inactive'}"]
            self.status_text.insert(tk.END, "\n".join(status_lines)); self.update_access_log()
        except Exception as e: logging.error(f"Error updating GUI status: {e}")
        self.root.after(1000, self.update_status)
    def update_access_log(self) -> None:
        try:
            recent_entries = self.gate_system.access_log.get_recent_entries(15); self.log_text.delete(1.0, tk.END)
            for entry in reversed(recent_entries):
                ts = entry.get('timestamp', 'Unknown').split('.')[0].replace('T', ' ')
                line = f"[{ts}] {'GRANTED' if entry.get('access_granted') else 'DENIED'}: {entry.get('card_name')} ({entry.get('reason')})\n" if 'card_uid' in entry else f"[{ts}] EVENT: {entry.get('event_type')} - {entry.get('description')}\n"
                self.log_text.insert(tk.END, line)
        except Exception as e: logging.error(f"Error updating access log: {e}")
    def open_gate(self) -> None: threading.Thread(target=lambda: self.gate_system.manual_open_gate(), daemon=True).start()
    def close_gate(self) -> None: threading.Thread(target=lambda: self.gate_system.manual_close_gate(), daemon=True).start()
    def emergency_stop(self) -> None:
        if messagebox.askyesno("Emergency Stop", "Are you sure? This will stop all operations and lock the gate."):
            self.gate_system.emergency_stop(); messagebox.showinfo("Emergency Stop", "Emergency Stop Activated.")
    def on_closing(self):
        if messagebox.askokcancel("Quit", "Do you want to shut down the gate system?"): self.gate_system.shutdown(); self.root.destroy()
    def run(self) -> None: self.root.mainloop()

gate_system_instance = None
def signal_handler(signum, frame):
    logging.info(f"Received signal {signum}, initiating shutdown...")
    if gate_system_instance: gate_system_instance.shutdown()
    sys.exit(0)

def main():
    global gate_system_instance
    signal.signal(signal.SIGINT, signal_handler); signal.signal(signal.SIGTERM, signal_handler)
    try:
        logging.info("Starting Gate Control System")
        gate_system_instance = GateControlSystem()
        gui = GateControlGUI(gate_system_instance)
        gui.run()
    except Exception as e:
        logging.critical(f"A fatal error occurred in main: {e}", exc_info=True)
        if gate_system_instance: gate_system_instance.shutdown()
        sys.exit(1)
    finally:
        logging.info("Application has been shut down.")

if __name__ == "__main__":
    main()
