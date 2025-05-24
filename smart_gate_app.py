# -*- coding: utf-8 -*-
"""smart_gate_app.py: All-in-one script for Raspberry Pi Smart Gate Control.

Controls a PN532 NFC reader (I2C), a Servo motor (MG996R), LEDs, and Buzzers
via a Tkinter GUI.

Hardware Requirements:
- Raspberry Pi 4B (or similar model with GPIO)
- PN532 NFC Reader (configured for I2C)
- Tower Pro MG996R Servo Motor (or similar)
- Green LED + Buzzer (connected together)
- Red LED + Buzzer (connected together)
- 7-inch Raspberry Pi Display (or any display compatible with RPi)
- External 5V Power Supply (>= 2A recommended) for the Servo Motor
- Jumper Wires & Breadboard (optional)

Connections:
- PN532 (I2C):
    - VCC -> RPi 3.3V or 5V (check PN532 board specs)
    - GND -> RPi GND
    - SDA -> RPi GPIO 2 (SDA)
    - SCL -> RPi GPIO 3 (SCL)
- Servo (MG996R):
    - VCC -> External 5V (+) --- IMPORTANT! DO NOT POWER FROM RPI 5V PIN.
    - GND -> External 5V (-) AND RPi GND (Common Ground is essential)
    - Signal -> RPi GPIO 18 (or other PWM-capable pin)
- Green LED/Buzzer Pair:
    - Anode (+) -> RPi GPIO 17 (via current-limiting resistor for LED, e.g., 220-330 Ohm)
    - Cathode (-) -> RPi GND
- Red LED/Buzzer Pair:
    - Anode (+) -> RPi GPIO 27 (via current-limiting resistor for LED, e.g., 220-330 Ohm)
    - Cathode (-) -> RPi GND
- Display: Connect according to its specific instructions (DSI/HDMI/USB).

Setup Steps:
1. Install required libraries:
   sudo pip3 install gpiozero adafruit-circuitpython-pn532 adafruit-blinka
2. Enable I2C interface:
   sudo raspi-config -> Interface Options -> I2C -> Enable
3. Install and start pigpiod daemon (for better servo control):
   sudo apt-get update
   sudo apt-get install pigpio python3-pigpio
   sudo systemctl start pigpiod
   sudo systemctl enable pigpiod # Optional: start on boot
4. Configure Allowed UIDs: Edit the `ALLOWED_UIDS` list below with the hex strings of your valid NFC cards.
5. Run the script:
   python3 smart_gate_app.py
"""

import time
import tkinter as tk
from tkinter import font as tkFont
import threading
import board
import busio
from digitalio import DigitalInOut
from gpiozero import Servo, LED, Buzzer
from gpiozero.pins.pigpio import PiGPIOFactory
from adafruit_pn532.i2c import PN532_I2C

# ==============================================================================
# Configuration Settings
# ==============================================================================

# --- Allowed Card UIDs ---
# Add the UID strings (hexadecimal format, uppercase) of the cards
# that should be granted access.
# Example: ALLOWED_UIDS = ["0123ABCD", "FEDCBA98"]
# You can get the UID by running this script and scanning your cards
# (it will print detected UIDs to the console).
ALLOWED_UIDS = [
    "PLACE_YOUR_ALLOWED_CARD_UID_HERE_1", # Replace with actual UID
    "PLACE_YOUR_ALLOWED_CARD_UID_HERE_2", # Add more as needed
    # Example: "E5A4B3C2"
]

# --- Gate Timing ---
# How long the gate should stay open after a valid card is presented (in seconds)
GATE_OPEN_DURATION = 5

# --- Signal Durations ---
SUCCESS_SIGNAL_DURATION = 1 # Duration for green LED/buzzer
FAILURE_SIGNAL_DURATION = 1 # Duration for red LED/buzzer

# --- GPIO Pins (BCM numbering) ---
SERVO_PIN = 18
GREEN_LED_BUZZER_PIN = 17
RED_LED_BUZZER_PIN = 27

# --- Servo Configuration (Adjust these values for your MG996R and desired range) ---
# MG996R typically has a range of 0-180 degrees.
# Pulse widths often range from ~500us (0.0005s) to ~2500us (0.0025s).
# gpiozero uses values between -1 (min) and 1 (max).
# You might need to experiment to find the exact values for 0 and 90/180 degrees.
SERVO_MIN_PULSE_WIDTH = 0.0006 # Corresponds roughly to 0 degrees (adjust as needed)
SERVO_MAX_PULSE_WIDTH = 0.0024 # Corresponds roughly to 180 degrees (adjust as needed)
GATE_CLOSED_POS = -1 # Corresponds to min_pulse_width (e.g., 0 degrees)
GATE_OPEN_POS = 0    # Corresponds to mid position (e.g., 90 degrees - adjust if needed)

# --- NFC Reader Configuration ---
# Optional Reset pin (not typically needed for I2C, but some boards might have it)
# RESET_PIN_BOARD = board.D6 # Example if using reset on GPIO 6
IRQ_PIN = None # No IRQ pin used in this example
NFC_READ_DELAY = 0.5 # Seconds between card read attempts
NFC_DEBOUNCE_TIME = 2 # Seconds to ignore same card after read

print("Configuration loaded.")
print(f"Allowed UIDs: {ALLOWED_UIDS}")

# ==============================================================================
# Hardware Control Functions
# ==============================================================================

# Use pigpio factory for potentially smoother servo control
# Ensure pigpiod daemon is running: sudo systemctl start pigpiod
hw_factory = None
servo = None
green_led_buzzer = None
red_led_buzzer = None
hw_initialized = False

def initialize_hardware():
    """Initializes Servo, LEDs, and Buzzers."""
    global hw_factory, servo, green_led_buzzer, red_led_buzzer, hw_initialized
    try:
        print("Initializing hardware components...")
        hw_factory = PiGPIOFactory()

        # Initialize Servo
        servo = Servo(
            SERVO_PIN,
            pin_factory=hw_factory,
            min_pulse_width=SERVO_MIN_PULSE_WIDTH,
            max_pulse_width=SERVO_MAX_PULSE_WIDTH,
            initial_value=None # Don't move servo on script start
        )

        # Initialize LEDs (represent LED+Buzzer pairs)
        green_led_buzzer = LED(GREEN_LED_BUZZER_PIN, pin_factory=hw_factory)
        red_led_buzzer = LED(RED_LED_BUZZER_PIN, pin_factory=hw_factory)

        # Ensure components are off initially
        green_led_buzzer.off()
        red_led_buzzer.off()
        # Set servo to closed position initially (optional, can be done later)
        # servo.value = GATE_CLOSED_POS
        # time.sleep(0.5)
        # servo.detach() # Detach to prevent jitter if needed

        print("Hardware components initialized successfully.")
        hw_initialized = True
        return True

    except Exception as e:
        print(f"Error initializing hardware components: {e}")
        print("Please ensure the pigpiod service is running ("sudo systemctl start pigpiod")")
        print("and check GPIO connections.")
        servo = None
        green_led_buzzer = None
        red_led_buzzer = None
        hw_initialized = False
        return False

def open_gate():
    """Moves the servo to the 'open' position."""
    if servo:
        print("Opening gate...")
        servo.value = GATE_OPEN_POS
        # Optional: Detach servo after movement to save power and reduce jitter
        # time.sleep(0.5) # Allow time for movement
        # servo.detach()
    else:
        print("Servo not initialized.")

def close_gate():
    """Moves the servo to the 'closed' position."""
    if servo:
        print("Closing gate...")
        servo.value = GATE_CLOSED_POS
        # Optional: Detach servo after movement
        # time.sleep(0.5) # Allow time for movement
        # servo.detach()
    else:
        print("Servo not initialized.")

def activate_success_signal(duration=SUCCESS_SIGNAL_DURATION):
    """Activates the green LED and its corresponding buzzer for a duration."""
    if green_led_buzzer:
        print("Activating success signal...")
        green_led_buzzer.on()
        time.sleep(duration)
        green_led_buzzer.off()
    else:
        print("Green LED/Buzzer not initialized.")

def activate_failure_signal(duration=FAILURE_SIGNAL_DURATION):
    """Activates the red LED and its corresponding buzzer for a duration."""
    if red_led_buzzer:
        print("Activating failure signal...")
        red_led_buzzer.on()
        time.sleep(duration)
        red_led_buzzer.off()
    else:
        print("Red LED/Buzzer not initialized.")

def test_servo():
    """Performs a simple test sequence for the servo."""
    if servo:
        print("Testing Servo...")
        print("Moving to closed position.")
        servo.value = GATE_CLOSED_POS
        time.sleep(1)
        print("Moving to open position.")
        servo.value = GATE_OPEN_POS
        time.sleep(1)
        print("Moving back to closed position.")
        servo.value = GATE_CLOSED_POS
        time.sleep(0.5)
        # servo.detach() # Optional detach
        print("Servo test complete.")
    else:
        print("Servo not initialized.")

def test_green_led_buzzer():
    """Tests the green LED and buzzer."""
    activate_success_signal(duration=1)

def test_red_led_buzzer():
    """Tests the red LED and buzzer."""
    activate_failure_signal(duration=1)

def cleanup_hardware():
    """Cleans up GPIO resources."""
    global hw_initialized
    print("Cleaning up hardware resources...")
    if servo:
        # Move to closed position before closing? Optional.
        # servo.value = GATE_CLOSED_POS
        # time.sleep(0.5)
        servo.close()
    if green_led_buzzer:
        green_led_buzzer.close()
    if red_led_buzzer:
        red_led_buzzer.close()
    if hw_factory:
        hw_factory.close()
    hw_initialized = False
    print("Hardware cleanup complete.")

# ==============================================================================
# PN532 NFC Reader Functions
# ==============================================================================

pn532 = None
nfc_initialized = False

def initialize_pn532():
    """Initializes the PN532 reader over I2C."""
    global pn532, nfc_initialized
    try:
        print("Initializing PN532 over I2C...")
        i2c = busio.I2C(board.SCL, board.SDA)

        # With I2C, connecting RSTPD_N (reset) is optional but can improve reliability.
        reset_pin_obj = None
        # if 'RESET_PIN_BOARD' in globals():
        #     reset_pin_obj = DigitalInOut(RESET_PIN_BOARD)

        pn532 = PN532_I2C(i2c, debug=False, reset=reset_pin_obj, irq=IRQ_PIN)

        ic, ver, rev, support = pn532.firmware_version
        print(f"Found PN532 with firmware version: {ver}.{rev}")

        # Configure PN532 to communicate with MiFare cards
        pn532.SAM_configuration()
        print("PN532 Initialized and configured for MiFare cards.")
        nfc_initialized = True
        return True

    except RuntimeError as e:
        print(f"Error initializing PN532 (RuntimeError): {e}")
        print("Ensure the PN532 is connected correctly to I2C pins (SDA, SCL, GND, VCC)")
        print("and that I2C is enabled on the Raspberry Pi ("sudo raspi-config").")
        pn532 = None
        nfc_initialized = False
        return False
    except Exception as e:
        print(f"An unexpected error occurred during PN532 initialization: {e}")
        pn532 = None
        nfc_initialized = False
        return False

def read_card_uid():
    """Attempts to read the UID of a MiFare card.

    Returns:
        bytes: The UID of the card if found, otherwise None.
    """
    if not pn532:
        # print("PN532 not initialized.") # Avoid spamming console
        return None

    try:
        # Check if a card is available to read
        # listen_for_passive_target can block slightly, timeout is in seconds
        uid = pn532.read_passive_target(timeout=0.1) # Short timeout for responsiveness

        if uid is None:
            return None

        # print(f"Found card with UID: {[hex(i) for i in uid]}") # Debug print
        return uid

    except RuntimeError as e:
        # Errors can happen if the card is moved during read
        # print(f"Runtime error while reading card: {e}") # Debug print
        # Attempt to re-initialize or simply continue might be needed in a robust app
        # For simplicity here, we just return None
        return None
    except Exception as e:
        print(f"An unexpected error occurred during card read: {e}")
        return None

# ==============================================================================
# Tkinter GUI Application
# ==============================================================================

class SmartGateGUI:
    def __init__(self, master):
        self.master = master
        master.title("Smart Gate Control Panel")
        master.geometry("800x480") # Adjust for 7" display
        # Uncomment the next line to attempt full screen on the RPi display
        # master.attributes("-fullscreen", True)

        self.is_running = True # Flag to control background thread
        self.gate_is_open = False
        self.last_uid_scanned = None
        self.last_scan_time = 0

        # --- Styling ---
        default_font = tkFont.nametofont("TkDefaultFont")
        default_font.configure(size=14)
        button_font = tkFont.Font(family="Helvetica", size=16, weight="bold")
        status_font = tkFont.Font(family="Helvetica", size=18, weight="bold")
        label_font = tkFont.Font(family="Helvetica", size=12)

        # --- Status Label ---
        self.status_var = tk.StringVar()
        self.status_label = tk.Label(master, textvariable=self.status_var, font=status_font, fg="#2196F3", pady=20)
        self.status_label.pack(fill=tk.X)

        # --- Frames for Organization ---
        test_frame = tk.LabelFrame(master, text="Component Tests", padx=15, pady=15, font=label_font)
        test_frame.pack(pady=20, padx=20, fill=tk.X)

        control_frame = tk.LabelFrame(master, text="Manual Gate Control", padx=15, pady=15, font=label_font)
        control_frame.pack(pady=10, padx=20, fill=tk.X)

        # --- Test Buttons ---
        self.test_servo_button = tk.Button(test_frame, text="Test Servo", command=self.run_test_servo, font=button_font, width=15, height=2)
        self.test_servo_button.grid(row=0, column=0, padx=10, pady=10)

        self.test_green_button = tk.Button(test_frame, text="Test Green LED/Buzzer", command=self.run_test_green, font=button_font, width=20, height=2)
        self.test_green_button.grid(row=0, column=1, padx=10, pady=10)

        self.test_red_button = tk.Button(test_frame, text="Test Red LED/Buzzer", command=self.run_test_red, font=button_font, width=20, height=2)
        self.test_red_button.grid(row=0, column=2, padx=10, pady=10)

        # --- Control Buttons ---
        self.open_button = tk.Button(control_frame, text="Manual Open", command=self.run_open_gate, font=button_font, bg="#4CAF50", fg="white", width=15, height=2) # Greenish
        self.open_button.grid(row=0, column=0, padx=10, pady=10)

        self.close_button = tk.Button(control_frame, text="Manual Close", command=self.run_close_gate, font=button_font, bg="#f44336", fg="white", width=15, height=2) # Reddish
        self.close_button.grid(row=0, column=1, padx=10, pady=10)

        # --- Quit Button ---
        self.quit_button = tk.Button(master, text="Quit Application", command=self.quit_app, font=button_font, width=15)
        self.quit_button.pack(pady=20)

        # Center frames content
        master.grid_rowconfigure(0, weight=1)
        master.grid_columnconfigure(0, weight=1)
        test_frame.grid_columnconfigure(0, weight=1)
        test_frame.grid_columnconfigure(1, weight=1)
        test_frame.grid_columnconfigure(2, weight=1)
        control_frame.grid_columnconfigure(0, weight=1)
        control_frame.grid_columnconfigure(1, weight=1)

        # --- Initialize Hardware and NFC ---
        # Note: Initialization functions are called directly now
        if not hw_initialized:
            self.update_status("Error: Hardware init failed! Check logs.", error=True)
            self.disable_buttons()
        elif not nfc_initialized:
            self.update_status("Warning: NFC Reader init failed! Check logs.", error=True)
            # Allow hardware tests even if NFC fails
            self.update_status("System Ready (NFC Failed). Manual/Test Only.", error=True)
        else:
            self.update_status("System Ready. Scan Card.")
            # Start the background NFC reading thread
            self.nfc_thread = threading.Thread(target=self.nfc_read_loop, daemon=True)
            self.nfc_thread.start()

        # Ensure cleanup on window close
        master.protocol("WM_DELETE_WINDOW", self.quit_app)

    def update_status(self, message, error=False):
        print(f"GUI Status: {message}") # Also print to console
        # Use schedule to update Tkinter components from other threads
        # Check if master window still exists before scheduling
        if self.master.winfo_exists():
            self.master.after(0, lambda: self._update_status_label(message, error))

    def _update_status_label(self, message, error):
        """Internal method to update label, called via master.after"""
        if self.master.winfo_exists(): # Check again before updating
            self.status_var.set(message)
            self.status_label.config(fg="#f44336" if error else "#2196F3") # Red or Blue

    def disable_buttons(self):
        self.test_servo_button.config(state=tk.DISABLED)
        self.test_green_button.config(state=tk.DISABLED)
        self.test_red_button.config(state=tk.DISABLED)
        self.open_button.config(state=tk.DISABLED)
        self.close_button.config(state=tk.DISABLED)

    def nfc_read_loop(self):
        """Background loop to continuously check for NFC cards."""
        print("NFC reading loop started.")
        while self.is_running:
            if not nfc_initialized:
                time.sleep(5) # Wait longer if NFC init failed
                continue

            uid = read_card_uid() # Call the function directly
            current_time = time.time()

            if uid:
                # Debounce: Ignore the same card if read again within debounce_time
                if uid == self.last_uid_scanned and (current_time - self.last_scan_time) < NFC_DEBOUNCE_TIME:
                    # print("Debouncing same card...") # Debug print
                    time.sleep(NFC_READ_DELAY)
                    continue

                self.last_uid_scanned = uid
                self.last_scan_time = current_time
                uid_hex = uid.hex().upper()
                print(f"Card detected: {uid_hex}") # Log detected card UID

                if uid_hex in ALLOWED_UIDS:
                    self.handle_valid_card(uid_hex)
                else:
                    self.handle_invalid_card(uid_hex)
            else:
                # If gate is open due to card scan, ensure it closes after timeout
                # (This check is handled within handle_valid_card using master.after)
                pass

            time.sleep(NFC_READ_DELAY) # Wait before next read attempt
        print("NFC reading loop stopped.")

    def handle_valid_card(self, uid_hex):
        """Actions to perform when a valid card is detected."""
        self.update_status(f"Access Granted: {uid_hex}")
        activate_success_signal() # Call directly
        if not self.gate_is_open:
            open_gate() # Call directly
            self.gate_is_open = True
            # Schedule gate closure only if the master window exists
            if self.master.winfo_exists():
                self.master.after(GATE_OPEN_DURATION * 1000, self.auto_close_gate)
        else:
            # Optional: Reset timer if already open?
            print("Gate already open. Access granted again.")
            # If you want to reset the timer:
            # if self.master.winfo_exists():
            #     # Cancel previous timer if exists (requires storing timer ID)
            #     self.master.after(GATE_OPEN_DURATION * 1000, self.auto_close_gate)

    def handle_invalid_card(self, uid_hex):
        """Actions to perform when an invalid card is detected."""
        self.update_status(f"Access Denied: {uid_hex}", error=True)
        activate_failure_signal() # Call directly
        # Ensure gate is closed or remains closed
        if self.gate_is_open:
             print("Invalid card scanned while gate open. Keeping gate open until timer expires or manual close.")
             # Decide policy: Close immediately? Or let timer run out?
             # Current policy: Let timer run out or wait for manual close.
        else:
            close_gate() # Make sure it's closed
        # Reset status after showing denial message
        if self.master.winfo_exists():
            self.master.after(2000, lambda: self.update_status("System Ready. Scan Card." if nfc_initialized else "System Ready (NFC Failed). Manual/Test Only."))

    def auto_close_gate(self):
        """Closes the gate automatically after the open duration."""
        if self.gate_is_open:
            print("Auto-closing gate...")
            self.run_close_gate() # Use the GUI method to update status correctly

    # --- Button Callback Methods ---
    def run_test_servo(self):
        self.update_status("Testing Servo...")
        try:
            test_servo() # Call directly
            self.gate_is_open = False # Test sequence ends closed
            self.update_status("Servo Test Complete")
        except Exception as e:
            self.update_status(f"Servo Test Error: {e}", error=True)
        if self.master.winfo_exists():
            self.master.after(1500, lambda: self.update_status("System Ready. Scan Card." if nfc_initialized else "System Ready (NFC Failed). Manual/Test Only."))

    def run_test_green(self):
        self.update_status("Testing Green LED/Buzzer...")
        try:
            test_green_led_buzzer() # Call directly
            self.update_status("Green Test Complete")
        except Exception as e:
            self.update_status(f"Green Test Error: {e}", error=True)
        if self.master.winfo_exists():
            self.master.after(1500, lambda: self.update_status("System Ready. Scan Card." if nfc_initialized else "System Ready (NFC Failed). Manual/Test Only."))

    def run_test_red(self):
        self.update_status("Testing Red LED/Buzzer...")
        try:
            test_red_led_buzzer() # Call directly
            self.update_status("Red Test Complete")
        except Exception as e:
            self.update_status(f"Red Test Error: {e}", error=True)
        if self.master.winfo_exists():
            self.master.after(1500, lambda: self.update_status("System Ready. Scan Card." if nfc_initialized else "System Ready (NFC Failed). Manual/Test Only."))

    def run_open_gate(self):
        self.update_status("Manual Opening Gate...")
        try:
            open_gate() # Call directly
            self.gate_is_open = True
            self.update_status("Gate Opened Manually")
        except Exception as e:
            self.update_status(f"Manual Open Error: {e}", error=True)
            if self.master.winfo_exists():
                self.master.after(1500, lambda: self.update_status("System Ready. Scan Card." if nfc_initialized else "System Ready (NFC Failed). Manual/Test Only."))

    def run_close_gate(self):
        self.update_status("Manual Closing Gate...")
        try:
            close_gate() # Call directly
            self.gate_is_open = False
            self.update_status("Gate Closed Manually")
        except Exception as e:
            self.update_status(f"Manual Close Error: {e}", error=True)
        if self.master.winfo_exists():
            self.master.after(1500, lambda: self.update_status("System Ready. Scan Card." if nfc_initialized else "System Ready (NFC Failed). Manual/Test Only."))

    def quit_app(self):
        """Handles application shutdown cleanly."""
        if not self.is_running: # Prevent double execution
            return
        self.update_status("Exiting Application...")
        self.is_running = False # Signal the NFC thread to stop

        # Wait for NFC thread to finish
        if hasattr(self, 'nfc_thread') and self.nfc_thread.is_alive():
            print("Waiting for NFC thread to join...")
            self.nfc_thread.join(timeout=1.0) # Wait briefly for thread

        # Cleanup hardware resources
        cleanup_hardware() # Call directly

        # Destroy the Tkinter window
        if self.master.winfo_exists():
            self.master.destroy()
        print("Application exited cleanly.")

# ==============================================================================
# Main Execution Block
# ==============================================================================
if __name__ == "__main__":
    # Initialize hardware and NFC reader first
    initialize_hardware()
    initialize_pn532()

    # Create and run the Tkinter GUI
    root = tk.Tk()
    gui = SmartGateGUI(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt detected. Cleaning up...")
        # Ensure quit_app is called even on KeyboardInterrupt
        if gui:
            gui.quit_app()
    finally:
        # Final check for cleanup if quit_app wasn't called or failed
        if hw_initialized:
             cleanup_hardware()

