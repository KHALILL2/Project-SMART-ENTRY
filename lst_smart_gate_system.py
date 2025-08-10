# ===================================================================================
# Gate Control System for Raspberry Pi - USER LOGIC REVERSED
# Version: 7.7 (Final Logic Reversal)
#
# --- CRITICAL SYSTEM NOTES ---
# 1. PULL-UP RESISTORS REQUIRED: To prevent LEDs and the Servo from activating
#    at boot, you MUST add a 10kΩ pull-up resistor from a 3.3V pin to EACH
#    of the following GPIO pins: 18 (Servo), 22 (Green LED), and 27 (Red LED).
#
# 2. LOGIC FULLY REVERSED AS REQUESTED:
#    - "Open Gate" command now LOCKS the relay and moves the servo.
#    - "Close Gate" command now UNLOCKS the relay and stops the servo.
#    - Servo direction for "Open" is now reversed.
#
# 3. ACTIVE-LOW CIRCUITS: This code assumes all LEDs, Buzzers, and the Relay
#    are active-low (they turn ON when the GPIO pin is LOW).
# ===================================================================================

import time
import threading
import logging
from logging.handlers import RotatingFileHandler
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
        BCM, OUT, IN, HIGH, LOW = "BCM", "OUT", "IN", 1, 0
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
        def cleanup(): pass
    class MockPWM:
        def start(self, duty_cycle): pass
        def ChangeDutyCycle(self, duty_cycle): pass
        def stop(self): pass
    GPIO = MockGPIO()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[RotatingFileHandler('gate_system.log', maxBytes=1024*1024, backupCount=3), logging.StreamHandler()])

# ==============================================================================
# === CONFIGURATION - TUNE SERVO MOVEMENT HERE =================================
# ==============================================================================
@dataclass
class HardwareConfig:
    """ Fine-tune servo travel distance here. Smaller difference = faster movement. """
    servo_frequency: int = 50
    servo_open_duty: float = 7.5  # Position for one direction
    servo_close_duty: float = 2.5 # Position for the other direction

@dataclass
class SecurityConfig:
    auto_close_delay: int = 15 # Seconds before gate auto-closes

# --- Hardware Definitions ---
HARDWARE_PINS = {'SERVO_PIN': 18, 'RELAY_PIN': 17, 'GREEN_LED_BUZZER_PIN': 22, 'RED_LED_BUZZER_PIN': 27}

# --- Enums for State Management ---
class GateState(Enum): OPEN, CLOSED, MOVING, STOPPED, ERROR = "Open/Unlocked", "Closed/Locked", "Moving", "Stopped", "Error"
class LockState(Enum): LOCKED, UNLOCKED = "Locked", "Unlocked"

class RPiHardwareController:
    """ Manages all direct hardware control with the corrected logic. """
    def __init__(self, config: HardwareConfig):
        self.config = config; self.running = True
        self.gate_state = GateState.CLOSED; self.lock_state = LockState.LOCKED
        self.servo_pwm = None
        self.initialize_gpio()

    def initialize_gpio(self) -> None:
        try:
            GPIO.setmode(GPIO.BCM)
            for pin in HARDWARE_PINS.values(): GPIO.setup(pin, GPIO.OUT)
            self.servo_pwm = GPIO.PWM(HARDWARE_PINS['SERVO_PIN'], self.config.servo_frequency)
            self.servo_pwm.start(0) # Start with servo stopped
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.HIGH) # HIGH = Locked
            GPIO.output(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], GPIO.HIGH) # HIGH = Off
            GPIO.output(HARDWARE_PINS['RED_LED_BUZZER_PIN'], GPIO.HIGH) # HIGH = Off
            logging.info("GPIO Initialized. Servo stopped, all outputs are OFF.")
        except Exception as e: logging.error(f"Error initializing GPIO: {e}"); raise

    def open_gate(self) -> bool:
        """ USER ACTION: "Open Gate". Locks the relay, then moves the servo. """
        logging.info("COMMAND: OPEN GATE")
        self.gate_state = GateState.MOVING
        
        # 1. Lock First (as requested)
        if not self.lock_gate():
            self.gate_state = GateState.ERROR; return False
        
        # 2. Move Servo
        try:
            # FIX: Using 'servo_close_duty' to reverse the direction of movement
            self.servo_pwm.ChangeDutyCycle(self.config.servo_close_duty)
            time.sleep(1.5)
            self.servo_pwm.ChangeDutyCycle(0) # Stop servo jitter
        except Exception as e:
            logging.error(f"Error moving servo: {e}"); self.gate_state = GateState.ERROR; return False

        self.gate_state = GateState.CLOSED # Reflects the final state
        logging.info("Gate is now CLOSED and LOCKED."); return True

    def close_gate(self) -> bool:
        """ USER ACTION: "Close Gate". Stops servo movement and unlocks the gate. """
        logging.info("COMMAND: CLOSE GATE / UNLOCK")
        self.gate_state = GateState.MOVING

        # 1. Stop the Servo
        try: self.servo_pwm.ChangeDutyCycle(0)
        except Exception as e:
            logging.error(f"Error stopping servo: {e}"); self.gate_state = GateState.ERROR; return False
        
        # 2. Unlock the Gate (as requested)
        if not self.unlock_gate():
            self.gate_state = GateState.ERROR; return False

        self.gate_state = GateState.OPEN # Reflects the final state
        logging.info("Gate is now STOPPED and UNLOCKED."); return True

    def lock_gate(self) -> bool:
        try: GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.HIGH); self.lock_state = LockState.LOCKED; return True
        except Exception: return False

    def unlock_gate(self) -> bool:
        try: GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.LOW); self.lock_state = LockState.UNLOCKED; return True
        except Exception: return False
            
    def _flash_pin(self, pin_num: int, duration: float):
        try: GPIO.output(pin_num, GPIO.LOW); time.sleep(duration); GPIO.output(pin_num, GPIO.HIGH)
        except Exception as e: logging.error(f"Error flashing pin {pin_num}: {e}")

    def green_feedback(self): threading.Thread(target=self._flash_pin, args=(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], 0.3), daemon=True).start()
    def red_feedback(self): threading.Thread(target=self._flash_pin, args=(HARDWARE_PINS['RED_LED_BUZZER_PIN'], 0.8), daemon=True).start()

    def cleanup(self) -> None:
        try:
            self.running = False
            if self.servo_pwm: self.servo_pwm.stop()
            for pin in HARDWARE_PINS.values(): GPIO.output(pin, GPIO.HIGH)
            GPIO.cleanup(); logging.info("GPIO cleanup completed")
        except Exception as e: logging.error(f"Error during GPIO cleanup: {e}")

class GateControlSystem:
    """ Main class that orchestrates the system components and logic. """
    def __init__(self):
        self.hardware_config = HardwareConfig()
        self.security_config = SecurityConfig()
        self.hardware = RPiHardwareController(self.hardware_config)
        self.running = True
        self.auto_close_timer = None
        logging.info("Gate Control System Initialized.")
    
    def manual_open_gate(self):
        """ Handles the manual 'Open Gate' button press. """
        logging.info("Manual Open command received.")
        self.hardware.green_feedback()
        if self.hardware.open_gate():
            self._set_auto_close_timer()

    def manual_close_gate(self):
        """ Handles the manual 'Close Gate / Lock' button press. """
        logging.info("Manual Close/Unlock command received.")
        if self.auto_close_timer: self.auto_close_timer.cancel(); self.auto_close_timer = None
        self.hardware.red_feedback()
        self.hardware.close_gate()
        
    def _set_auto_close_timer(self):
        """ Starts a timer that will automatically call the open function (since logic is reversed). """
        if self.auto_close_timer: self.auto_close_timer.cancel()
        delay = self.security_config.auto_close_delay
        # The auto function should now call the "open" command to lock the gate
        self.auto_close_timer = threading.Timer(delay, self.manual_open_gate)
        self.auto_close_timer.daemon = True
        self.auto_close_timer.start()
        logging.info(f"Gate will auto-open/lock in {delay} seconds.")

    def get_system_status(self) -> Dict[str, Any]:
        """ Gets the current state from the hardware controller. """
        return {'gate_state': self.hardware.gate_state.value, 'lock_state': self.hardware.lock_state.value}
        
    def shutdown(self):
        if not self.running: return
        logging.info("Shutting down..."); self.running = False
        if self.auto_close_timer: self.auto_close_timer.cancel()
        self.hardware.cleanup()

class GateControlGUI:
    """ Simple Tkinter GUI for system control and status monitoring. """
    def __init__(self, gate_system: GateControlSystem):
        self.gate_system = gate_system; self.root = tk.Tk()
        self.root.title("Gate Control System"); self.root.geometry("500x300")
        self.create_widgets(); self.update_status()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="10"); main_frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1); self.root.rowconfigure(0, weight=1)
        ttk.Label(main_frame, text="Gate Control System", font=("Arial", 16, "bold")).grid(row=0, column=0, columnspan=2, pady=10)
        status_frame = ttk.LabelFrame(main_frame, text="System Status", padding="10"); status_frame.grid(row=1, column=0, sticky="ew"); main_frame.columnconfigure(0, weight=1)
        self.status_text = tk.Text(status_frame, height=4, width=50, wrap=tk.WORD, font=("Courier", 10)); self.status_text.pack(fill=tk.BOTH, expand=True)
        control_frame = ttk.LabelFrame(main_frame, text="Manual Controls", padding="10"); control_frame.grid(row=2, column=0, sticky="ew", pady=10)
        ttk.Button(control_frame, text="Open & Lock Gate", command=lambda: threading.Thread(target=self.gate_system.manual_open_gate, daemon=True).start()).pack(side=tk.LEFT, padx=5, pady=5)
        ttk.Button(control_frame, text="Stop & Unlock Gate", command=lambda: threading.Thread(target=self.gate_system.manual_close_gate, daemon=True).start()).pack(side=tk.LEFT, padx=5, pady=5)

    def update_status(self):
        try:
            status = self.gate_system.get_system_status()
            self.status_text.delete(1.0, tk.END)
            status_lines = [f"Gate State: {status.get('gate_state', 'Unknown')}", f"Lock State: {status.get('lock_state', 'Unknown')}"]
            self.status_text.insert(tk.END, "\n".join(status_lines))
        except Exception as e: self.status_text.insert(tk.END, f"Error updating status: {e}")
        self.root.after(1000, self.update_status)

    def on_closing(self):
        if messagebox.askokcancel("Quit", "Do you want to shut down the gate system?"): self.gate_system.shutdown(); self.root.destroy()

    def run(self): self.root.mainloop()

# --- Main Application Execution ---
gate_system_instance = None
def signal_handler(signum, frame):
    logging.warning(f"Signal {signum} received. Shutting down.");
    if gate_system_instance: gate_system_instance.shutdown();
    sys.exit(0)

def main():
    global gate_system_instance
    signal.signal(signal.SIGINT, signal_handler); signal.signal(signal.SIGTERM, signal_handler)
    try:
        gate_system_instance = GateControlSystem(); gui = GateControlGUI(gate_system_instance); gui.run()
    except Exception as e:
        logging.critical(f"A fatal error occurred in main: {e}", exc_info=True)
        if gate_system_instance: gate_system_instance.shutdown()
        sys.exit(1)

if __name__ == "__main__":
    main()
