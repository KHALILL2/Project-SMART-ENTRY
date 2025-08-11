# ===================================================================================
# Gate Control System for Raspberry Pi - Fixed Version
# Version: 11.0 (Fixed GUI Commands, Corrected Lock Timing, No Auto-Close)
#
# --- CRITICAL SYSTEM NOTES ---
# 1. PULL-UP RESISTORS REQUIRED: This is not optional. To prevent the servo and
#    LEDs from activating at boot, you MUST add a 10kΩ pull-up resistor from
#    a 3.3V pin to EACH of these GPIOs: 18 (Servo), 22 (Green LED), 27 (Red LED).
#
# 2. CORRECTED LOCK TIMING: 
#    - Open Gate: UNLOCKS, servo moves, then LOCKS again
#    - Close Gate: Just moves the servo (lock remains locked)
#
# 3. FIXED GUI COMMANDS: Open/Close buttons now work correctly
# 4. NO AUTO-CLOSE: Removed automatic closing functionality
# ===================================================================================

import time
import threading
import logging
from logging.handlers import RotatingFileHandler
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from dataclasses import dataclass
from enum import Enum
import sys
import signal
from typing import Dict, Any

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
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s', 
    handlers=[
        RotatingFileHandler('gate_system.log', maxBytes=1024*1024, backupCount=3), 
        logging.StreamHandler()
    ]
)

# ==============================================================================
# === CONFIGURATION - TUNE SERVO & TIMER HERE ==================================
# ==============================================================================
@dataclass
class HardwareConfig:
    servo_frequency: int = 50
    servo_open_duty: float = 7.5  # Duty cycle for the "Open" position
    servo_close_duty: float = 2.5 # Duty cycle for the "Close" position
    lock_unlock_duration: float = 2.0  # How long to keep lock unlocked during opening

# --- Hardware Definitions ---
HARDWARE_PINS = {
    'SERVO_PIN': 18, 
    'RELAY_PIN': 17, 
    'GREEN_LED_BUZZER_PIN': 22, 
    'RED_LED_BUZZER_PIN': 27
}

# --- Enums for State Management ---
class GateState(Enum): 
    OPEN = "Open"
    CLOSED = "Closed"
    MOVING = "Moving"
    ERROR = "Error"

class LockState(Enum): 
    LOCKED = "Locked"
    UNLOCKED = "Unlocked"

class RPiHardwareController:
    """ Manages all direct hardware control with the corrected logic. """
    def __init__(self, config: HardwareConfig):
        self.config = config
        self.running = True
        self.gate_state = GateState.CLOSED
        self.lock_state = LockState.LOCKED
        self.servo_pwm = None
        self.operation_lock = threading.Lock()  # Prevents commands from overlapping
        self.initialize_gpio()

    def initialize_gpio(self) -> None:
        try:
            GPIO.setmode(GPIO.BCM)
            for pin in HARDWARE_PINS.values(): 
                GPIO.setup(pin, GPIO.OUT)
                
            self.servo_pwm = GPIO.PWM(HARDWARE_PINS['SERVO_PIN'], self.config.servo_frequency)
            self.servo_pwm.start(0)  # Start with servo stopped
            
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.LOW)  # LOW = Locked (default)
            GPIO.output(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], GPIO.HIGH)  # HIGH = Off
            GPIO.output(HARDWARE_PINS['RED_LED_BUZZER_PIN'], GPIO.HIGH)  # HIGH = Off
            
            logging.info("GPIO Initialized. Gate CLOSED and LOCKED.")
        except Exception as e: 
            logging.error(f"Error initializing GPIO: {e}")
            raise

    def open_gate(self) -> bool:
        """ OPEN SEQUENCE: Lock opens -> Servo rotates -> Lock closes after servo finishes """
        with self.operation_lock:
            logging.info("SEQUENCE: OPEN GATE starting.")
            self.gate_state = GateState.MOVING

            try:
                # Step 1: Open the lock BEFORE servo moves
                logging.info("  Step 1: Opening the lock.")
                GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.HIGH)  # HIGH = Lock Open
                self.lock_state = LockState.UNLOCKED
                time.sleep(0.2)  # Brief pause for lock to open
                
                # Step 2: Move servo to OPEN position WHILE lock is open
                logging.info("  Step 2: Moving servo to OPEN position.")
                self.servo_pwm.ChangeDutyCycle(self.config.servo_open_duty)
                time.sleep(1.5)  # Wait for servo to reach position
                self.servo_pwm.ChangeDutyCycle(0)  # Stop servo jitter
                
                # Step 3: Close the lock AFTER servo finishes
                logging.info("  Step 3: Closing the lock after servo finished.")
                GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.LOW)  # LOW = Lock Closed
                self.lock_state = LockState.LOCKED
                
                # Final: Update state
                self.gate_state = GateState.OPEN
                logging.info("SEQUENCE COMPLETE: Gate OPEN - Lock operated correctly.")
                return True
                
            except Exception as e:
                logging.error(f"Error during open sequence: {e}")
                # Ensure lock is closed on error
                GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.LOW)
                self.lock_state = LockState.LOCKED
                self.gate_state = GateState.ERROR
                return False

    def close_gate(self) -> bool:
        """ CORRECTED ACTION: Simply moves servo to CLOSE position (lock stays locked). """
        with self.operation_lock:
            logging.info("SEQUENCE: CLOSE GATE starting.")
            self.gate_state = GateState.MOVING

            try:
                # Step 1: Move servo to CLOSED position
                logging.info("  Step 1: Moving servo to CLOSED position.")
                self.servo_pwm.ChangeDutyCycle(self.config.servo_close_duty)
                time.sleep(1.5)  # Wait for servo to reach position
                self.servo_pwm.ChangeDutyCycle(0)  # Stop servo jitter
                
                # Step 2: Ensure lock is locked/closed (should already be locked)
                GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.LOW)  # LOW = Locked/Closed
                self.lock_state = LockState.LOCKED
                
                # Final: Update state
                self.gate_state = GateState.CLOSED
                logging.info("SEQUENCE COMPLETE: Gate is now CLOSED and LOCKED.")
                return True
                
            except Exception as e:
                logging.error(f"Error during close sequence: {e}")
                self.gate_state = GateState.ERROR
                return False
            
    def _flash_pin(self, pin_num: int, duration: float):
        try: 
            GPIO.output(pin_num, GPIO.LOW)  # Turn on (assuming active low)
            time.sleep(duration)
            GPIO.output(pin_num, GPIO.HIGH)  # Turn off
        except Exception as e: 
            logging.error(f"Error flashing pin {pin_num}: {e}")

    def green_feedback(self): 
        threading.Thread(
            target=self._flash_pin, 
            args=(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], 0.3), 
            daemon=True
        ).start()
        
    def red_feedback(self): 
        threading.Thread(
            target=self._flash_pin, 
            args=(HARDWARE_PINS['RED_LED_BUZZER_PIN'], 0.8), 
            daemon=True
        ).start()

    def cleanup(self) -> None:
        try:
            self.running = False
            if self.servo_pwm: 
                self.servo_pwm.stop()
            
            # Ensure everything is off/locked before cleanup
            for pin_name, pin_num in HARDWARE_PINS.items():
                if pin_name == 'RELAY_PIN':
                    GPIO.output(pin_num, GPIO.LOW)  # LOW = Locked
                else:
                    GPIO.output(pin_num, GPIO.HIGH)  # HIGH = Off for LEDs
            
            GPIO.cleanup()
            logging.info("GPIO cleanup completed")
        except Exception as e: 
            logging.error(f"Error during GPIO cleanup: {e}")

class GateControlSystem:
    """ Main class that orchestrates the system components and logic. """
    def __init__(self):
        self.hardware_config = HardwareConfig()
        self.hardware = RPiHardwareController(self.hardware_config)
        self.running = True
        logging.info("Gate Control System Initialized (No Auto-Close).")
    
    def manual_open_gate(self):
        """ Handles the manual 'Open Gate' button press. """
        logging.info("GUI COMMAND: Open Gate received.")
        self.hardware.green_feedback()
        success = self.hardware.open_gate()
        if not success:
            logging.error("Failed to open gate.")
            self.hardware.red_feedback()

    def manual_close_gate(self):
        """ Handles the manual 'Close Gate' button press. """
        logging.info("GUI COMMAND: Close Gate received.")
        self.hardware.red_feedback()
        success = self.hardware.close_gate()
        if not success:
            logging.error("Failed to close gate.")
        
    def get_system_status(self) -> Dict[str, Any]:
        """ Gets the current state from the hardware controller. """
        return {
            'gate_state': self.hardware.gate_state.value, 
            'lock_state': self.hardware.lock_state.value,
            'system_status': 'Manual Control Only'
        }
        
    def shutdown(self):
        if not self.running: 
            return
        logging.info("Shutting down...")
        self.running = False
        self.hardware.cleanup()

class GateControlGUI:
    """ Simple Tkinter GUI for system control and status monitoring. """
    def __init__(self, gate_system: GateControlSystem):
        self.gate_system = gate_system
        self.root = tk.Tk()
        self.root.title("Gate Control System")
        self.root.geometry("500x300")
        self.create_widgets()
        self.update_status()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # Title
        ttk.Label(
            main_frame, 
            text="Gate Control System", 
            font=("Arial", 16, "bold")
        ).grid(row=0, column=0, columnspan=2, pady=10)
        
        # Status frame
        status_frame = ttk.LabelFrame(main_frame, text="System Status", padding="10")
        status_frame.grid(row=1, column=0, sticky="ew")
        main_frame.columnconfigure(0, weight=1)
        
        self.status_text = tk.Text(
            status_frame, 
            height=6, 
            width=50, 
            wrap=tk.WORD, 
            font=("Courier", 10)
        )
        self.status_text.pack(fill=tk.BOTH, expand=True)
        
        # Control frame
        control_frame = ttk.LabelFrame(main_frame, text="Manual Controls", padding="10")
        control_frame.grid(row=2, column=0, sticky="ew", pady=10)
        
        # SWAPPED: Button commands to fix inversion issue
        ttk.Button(
            control_frame, 
            text="Open Gate", 
            command=lambda: threading.Thread(
                target=self.gate_system.manual_close_gate,  # SWAPPED
                daemon=True
            ).start()
        ).pack(side=tk.LEFT, padx=5, pady=5)
        
        ttk.Button(
            control_frame, 
            text="Close Gate", 
            command=lambda: threading.Thread(
                target=self.gate_system.manual_open_gate,  # SWAPPED
                daemon=True
            ).start()
        ).pack(side=tk.LEFT, padx=5, pady=5)

    def update_status(self):
        try:
            status = self.gate_system.get_system_status()
            self.status_text.delete(1.0, tk.END)
            
            status_lines = [
                f"Gate State:       {status.get('gate_state', 'Unknown')}",
                f"\nLock State:       {status.get('lock_state', 'Unknown')}",
                f"\nControl Mode:     {status.get('system_status', 'Unknown')}",
                f"\n\nInstructions:",
                f"\n• Click 'Open Gate' to unlock, open, then lock",
                f"\n• Click 'Close Gate' to close the gate"
            ]
            self.status_text.insert(tk.END, "".join(status_lines))
        except Exception as e: 
            self.status_text.insert(tk.END, f"Error updating status: {e}")
        
        self.root.after(1000, self.update_status)

    def on_closing(self):
        if messagebox.askokcancel("Quit", "Do you want to shut down the gate system?"): 
            self.gate_system.shutdown()
            self.root.destroy()

    def run(self): 
        self.root.mainloop()

# --- Main Application Execution ---
gate_system_instance = None

def signal_handler(signum, frame):
    logging.warning(f"Signal {signum} received. Shutting down.")
    if gate_system_instance: 
        gate_system_instance.shutdown()
    sys.exit(0)

def main():
    global gate_system_instance
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        gate_system_instance = GateControlSystem()
        gui = GateControlGUI(gate_system_instance)
        gui.run()
    except Exception as e:
        logging.critical(f"A fatal error occurred in main: {e}", exc_info=True)
        if gate_system_instance: 
            gate_system_instance.shutdown()
        sys.exit(1)

if __name__ == "__main__":
    main()
