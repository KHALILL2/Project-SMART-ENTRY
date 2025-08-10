# ===================================================================================
# Gate Control System for Raspberry Pi - USER LOGIC REVERSED
# Version: 7.5 (Fixed NameError)
#
# --- SYSTEM NOTES ---
# 1. GATE LOGIC REVERSED: Per user request, the "Open" and "Close" commands have
#    been logically swapped. "Open" now moves the gate and unlocks. "Close"
#    now stops the servo and locks the gate.
#
# 2. ACTIVE-LOW COMPONENTS: This code assumes your LEDs, Buzzers, and Relay are
#    "active-low." This means they turn ON when the GPIO pin goes LOW (0V) and
#    turn OFF when the pin goes HIGH (3.3V).
#
# 3. PULL-UP RESISTORS REQUIRED: To prevent LEDs from turning on at boot, you
#    MUST add a 10kΩ pull-up resistor from 3.3V to each LED/Buzzer GPIO pin
#    (GPIO 22 and GPIO 27).
# ===================================================================================

import time
import threading
import logging
from logging.handlers import RotatingFileHandler  # <-- FIX: THIS LINE WAS ADDED BACK
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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[RotatingFileHandler('gate_system.log', maxBytes=1024*1024, backupCount=3), logging.StreamHandler()])

# System configuration
CONFIG_FILE = 'gate_config.json'
DEFAULT_CONFIG = {'security': {'max_attempts': 3, 'lockout_time': 300, 'card_cooldown': 5, 'auto_close_delay': 10}, 'hardware': {'servo_frequency': 50, 'servo_open_duty': 7.5, 'servo_close_duty': 2.5}, 'logging': {'max_log_size': 1024 * 1024, 'backup_count': 3, 'log_level': 'INFO'}}

# Define enums
class GateState(Enum): CLOSED, OPENING, OPEN, CLOSING, STOPPED, ERROR, UNKNOWN = "CLOSED", "OPENING", "OPEN", "CLOSING", "STOPPED", "ERROR", "UNKNOWN"
class LockState(Enum): LOCKED, UNLOCKED, UNKNOWN = "LOCKED", "UNLOCKED", "UNKNOWN"

@dataclass
class SecurityConfig: max_attempts: int; lockout_time: int; card_cooldown: int; auto_close_delay: int
@dataclass
class HardwareConfig: servo_frequency: int; servo_open_duty: float; servo_close_duty: float

# ==============================================================================
# === HARDWARE DEFINITIONS (NO IR SENSOR) ======================================
# ==============================================================================
HARDWARE_PINS = {'SERVO_PIN': 18, 'RELAY_PIN': 17, 'GREEN_LED_BUZZER_PIN': 22, 'RED_LED_BUZZER_PIN': 27}

# ==============================================================================
# === HARDWARE CONTROLLER WITH REVERSED LOGIC ==================================
# ==============================================================================
class RPiHardwareController:
    def __init__(self, config: HardwareConfig) -> None:
        self.config = config; self.running = True
        self.gate_state, self.lock_state = GateState.STOPPED, LockState.LOCKED
        self.servo_pwm = None; self.event_queue: queue.Queue[str] = queue.Queue()
        self.initialize_gpio()
        logging.info("RPi Hardware Controller initialized with REVERSED logic.")

    def initialize_gpio(self) -> None:
        try:
            GPIO.setmode(GPIO.BCM)
            for pin in HARDWARE_PINS.values(): GPIO.setup(pin, GPIO.OUT)
            self.servo_pwm = GPIO.PWM(HARDWARE_PINS['SERVO_PIN'], self.config.servo_frequency)
            self.servo_pwm.start(0) # Start with servo stopped
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.HIGH)
            GPIO.output(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], GPIO.HIGH)
            GPIO.output(HARDWARE_PINS['RED_LED_BUZZER_PIN'], GPIO.HIGH)
            logging.info("GPIO initialization completed.")
        except Exception as e: logging.error(f"Error initializing GPIO: {e}"); raise

    def open_gate(self) -> bool:
        """
        USER ACTION: "Open Gate". Unlocks the gate and moves servo to desired position.
        """
        try:
            logging.info("COMMAND: OPEN GATE"); self.gate_state = GateState.OPENING
            self.unlock_gate(); time.sleep(0.5)
            # This was the old "close" duty cycle, now used for "open" per user request
            self.servo_pwm.ChangeDutyCycle(self.config.servo_close_duty); time.sleep(1.5)
            self.servo_pwm.ChangeDutyCycle(0) # Stop servo jitter
            self.gate_state = GateState.OPEN
            self.event_queue.put("GATE_OPENED"); logging.info("Gate is now OPEN"); return True
        except Exception as e:
            logging.error(f"Error during open_gate: {e}"); self.gate_state = GateState.ERROR; return False

    def close_gate(self) -> bool:
        """
        USER ACTION: "Close Gate". Stops the servo and locks the gate. Does not move servo.
        """
        try:
            logging.info("COMMAND: CLOSE GATE"); self.gate_state = GateState.CLOSING
            self.servo_pwm.ChangeDutyCycle(0) # Ensure servo is stopped
            self.lock_gate()
            self.gate_state = GateState.CLOSED
            self.event_queue.put("GATE_CLOSED"); logging.info("Gate is now CLOSED/LOCKED"); return True
        except Exception as e:
            logging.error(f"Error during close_gate: {e}"); self.gate_state = GateState.ERROR; return False

    def lock_gate(self) -> bool:
        try:
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.HIGH); self.lock_state = LockState.LOCKED; return True
        except Exception: return False

    def unlock_gate(self) -> bool:
        try:
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.LOW); self.lock_state = LockState.UNLOCKED; return True
        except Exception: return False
            
    def _flash_pin(self, pin_num: int, duration: float):
        try: GPIO.output(pin_num, GPIO.LOW); time.sleep(duration); GPIO.output(pin_num, GPIO.HIGH)
        except Exception as e: logging.error(f"Error flashing pin {pin_num}: {e}")

    def green_feedback(self) -> None: self._flash_pin(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], 0.2)
    def red_feedback(self) -> None: self._flash_pin(HARDWARE_PINS['RED_LED_BUZZER_PIN'], 1.0)

    def cleanup(self) -> None:
        try:
            self.running = False
            if self.servo_pwm: self.servo_pwm.stop()
            for pin in HARDWARE_PINS.values(): GPIO.output(pin, GPIO.HIGH)
            GPIO.cleanup(); logging.info("GPIO cleanup completed")
        except Exception as e: logging.error(f"Error during GPIO cleanup: {e}")

# ==============================================================================
# === CORE LOGIC & GUI (ADAPTED FOR REVERSED LOGIC) ============================
# ==============================================================================
class NFCCardManager:
    def __init__(self) -> None:
        self.pn532, self.authorized_cards, self.card_names, self.last_card_time, self.failed_attempts, self.lockout_until = None, set(), {}, {}, {}, {}
        self.load_authorized_cards(); self.initialize_nfc()
    def initialize_nfc(self) -> None:
        try:
            i2c = busio.I2C(board.SCL, board.SDA); self.pn532 = PN532.PN532_I2C(i2c, debug=False)
            ic, ver, rev, support = self.pn532.firmware_version; logging.info(f"Found PN532 with firmware version: {ver}.{rev}")
            self.pn532.SAM_configuration(); logging.info("NFC reader initialized successfully")
        except Exception as e: logging.error(f"Error initializing NFC reader: {e}"); self.pn532 = None
    def load_authorized_cards(self) -> None:
        try:
            if os.path.exists('authorized_cards.json'):
                with open('authorized_cards.json', 'r') as f: data = json.load(f)
                self.authorized_cards, self.card_names = set(data.get('cards', [])), data.get('names', {})
            else: self.save_authorized_cards()
        except Exception: pass
    def save_authorized_cards(self) -> None:
        try:
            with open('authorized_cards.json', 'w') as f: json.dump({'cards': list(self.authorized_cards), 'names': self.card_names}, f, indent=4)
        except Exception: pass
    def read_card(self) -> Optional[Tuple[str, str]]:
        if not self.pn532: return None
        try:
            uid = self.pn532.read_passive_target(timeout=0.5)
            if uid: card_uid = ''.join([hex(i)[2:].upper().zfill(2) for i in uid]); return card_uid, self.card_names.get(card_uid, "Unknown Card")
        except Exception: pass
        return None
    def is_card_authorized(self, card_uid: str, security_config: SecurityConfig) -> bool:
        current_time = datetime.now()
        if card_uid in self.lockout_until and current_time < self.lockout_until[card_uid]: return False
        if card_uid in self.last_card_time and (current_time - self.last_card_time[card_uid]).total_seconds() < security_config.card_cooldown: return False
        self.last_card_time[card_uid] = current_time
        if card_uid in self.authorized_cards: return True
        self.failed_attempts[card_uid] = self.failed_attempts.get(card_uid, 0) + 1
        if self.failed_attempts[card_uid] >= security_config.max_attempts: self.lockout_until[card_uid] = current_time + timedelta(seconds=security_config.lockout_time)
        return False
    def add_card(self, card_uid: str, card_name: str): self.authorized_cards.add(card_uid); self.card_names[card_uid] = card_name; self.save_authorized_cards()
    def remove_card(self, card_uid: str):
        if card_uid in self.authorized_cards: self.authorized_cards.remove(card_uid); self.card_names.pop(card_uid, None); self.save_authorized_cards()

class GateControlSystem:
    def __init__(self) -> None:
        self.config_manager = type('ConfigManager', (), {'get_hardware_config': lambda: HardwareConfig(**DEFAULT_CONFIG['hardware']), 'get_security_config': lambda: SecurityConfig(**DEFAULT_CONFIG['security'])})()
        self.hardware_config = self.config_manager.get_hardware_config()
        self.security_config = self.config_manager.get_security_config()
        self.hardware_controller = RPiHardwareController(self.hardware_config)
        self.nfc_manager = NFCCardManager()
        self.running, self.auto_close_timer = True, None
        self.start_control_loop()
    def start_control_loop(self) -> None: threading.Thread(target=self._control_loop, daemon=True).start()
    def _control_loop(self) -> None:
        while self.running:
            card_data = self.nfc_manager.read_card()
            if card_data:
                if self.nfc_manager.is_card_authorized(card_data[0], self.security_config): self.grant_access()
                else: self.deny_access()
            time.sleep(0.1)
    def grant_access(self) -> None:
        logging.info("Access granted by NFC")
        self.hardware_controller.green_feedback()
        if self.hardware_controller.open_gate(): self._set_auto_close_timer()
    def deny_access(self) -> None:
        logging.warning("Access denied by NFC")
        self.hardware_controller.red_feedback()
    def _set_auto_close_timer(self) -> None:
        if self.auto_close_timer: self.auto_close_timer.cancel()
        delay = self.security_config.auto_close_delay
        self.auto_close_timer = threading.Timer(delay, self.manual_close_gate)
        self.auto_close_timer.start()
        logging.info(f"Auto-close timer set for {delay} seconds")
    def manual_open_gate(self) -> None:
        logging.info("Manual Open command received.")
        self.hardware_controller.green_feedback()
        if self.hardware_controller.open_gate(): self._set_auto_close_timer()
    def manual_close_gate(self) -> None:
        logging.info("Manual Close/Lock command received.")
        if self.auto_close_timer: self.auto_close_timer.cancel(); self.auto_close_timer = None
        self.hardware_controller.red_feedback()
        self.hardware_controller.close_gate()
    def get_system_status(self) -> Dict[str, Any]:
        return self.hardware_controller.get_status()
    def shutdown(self) -> None:
        if not self.running: return
        logging.info("Shutting down..."); self.running = False
        if self.auto_close_timer: self.auto_close_timer.cancel()
        self.hardware_controller.cleanup()

class GateControlGUI:
    def __init__(self, gate_system: GateControlSystem) -> None:
        self.gate_system, self.root = gate_system, tk.Tk()
        self.root.title("Gate Control System"); self.root.geometry("600x400"); self.create_widgets(); self.update_status(); self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    def create_widgets(self) -> None:
        main_frame = ttk.Frame(self.root, padding="10"); main_frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1); self.root.rowconfigure(0, weight=1)
        ttk.Label(main_frame, text="Gate Control System", font=("Arial", 16, "bold")).grid(row=0, column=0, columnspan=2, pady=10)
        status_frame = ttk.LabelFrame(main_frame, text="System Status", padding="10"); status_frame.grid(row=1, column=0, sticky="ew"); main_frame.columnconfigure(0, weight=1)
        self.status_text = tk.Text(status_frame, height=5, width=60, wrap=tk.WORD); self.status_text.pack(fill=tk.BOTH, expand=True)
        control_frame = ttk.LabelFrame(main_frame, text="Manual Controls", padding="10"); control_frame.grid(row=2, column=0, sticky="ew", pady=10)
        ttk.Button(control_frame, text="Open Gate", command=lambda: threading.Thread(target=self.gate_system.manual_open_gate).start()).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Close Gate / Lock", command=lambda: threading.Thread(target=self.gate_system.manual_close_gate).start()).pack(side=tk.LEFT, padx=5)
    def update_status(self) -> None:
        status = self.gate_system.get_system_status()
        self.status_text.delete(1.0, tk.END)
        status_lines = [f"Gate State: {status.get('gate_state', 'Unknown')}", f"Lock State: {status.get('lock_state', 'Unknown')}"]
        self.status_text.insert(tk.END, "\n".join(status_lines))
        self.root.after(1000, self.update_status)
    def on_closing(self):
        if messagebox.askokcancel("Quit", "Do you want to shut down the gate system?"): self.gate_system.shutdown(); self.root.destroy()
    def run(self) -> None: self.root.mainloop()

gate_system_instance = None
def signal_handler(signum, frame):
    if gate_system_instance: gate_system_instance.shutdown(); sys.exit(0)

def main():
    global gate_system_instance
    signal.signal(signal.SIGINT, signal_handler); signal.signal(signal.SIGTERM, signal_handler)
    try:
        gate_system_instance = GateControlSystem(); gui = GateControlGUI(gate_system_instance); gui.run()
    except Exception as e:
        logging.critical(f"A fatal error occurred: {e}", exc_info=True)
        if gate_system_instance: gate_system_instance.shutdown()
        sys.exit(1)

if __name__ == "__main__":
    main()
