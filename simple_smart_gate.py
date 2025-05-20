import RPi.GPIO as GPIO
import time
import threading
import os
from tkinter import Tk, Label, Button, Frame, BOTH, X, LEFT, RIGHT, TOP, BOTTOM, END, Text, Scrollbar, Y
from tkinter import ttk
import tkinter as tk

# Pin definitions
RELAY_MOTOR = 17
RELAY_LOCK = 27
SERVO_PIN = 18
LED_GREEN_PIN = 22  # Green LED with buzzer connected
LED_RED_PIN = 23    # Red LED with buzzer connected

# Initialize GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Setup pins
GPIO.setup(RELAY_MOTOR, GPIO.OUT)
GPIO.setup(RELAY_LOCK, GPIO.OUT)
GPIO.setup(SERVO_PIN, GPIO.OUT)
GPIO.setup(LED_GREEN_PIN, GPIO.OUT)
GPIO.setup(LED_RED_PIN, GPIO.OUT)

# Initialize servo
servo = GPIO.PWM(SERVO_PIN, 50)  # 50Hz frequency
servo.start(0)  # Start with 0 duty cycle (no movement)

# Initialize hardware state
GPIO.output(RELAY_MOTOR, GPIO.HIGH)  # Motor off
GPIO.output(LED_GREEN_PIN, GPIO.LOW)  # Green LED off
GPIO.output(LED_RED_PIN, GPIO.LOW)    # Red LED off

# Try multiple approaches for the lock
try:
    # Method 1: Direct GPIO
    GPIO.output(RELAY_LOCK, GPIO.HIGH)   # Lock closed (HIGH)
    
    # Method 2: System command
    os.system('gpio -g mode 27 out')
    os.system('gpio -g write 27 1')  # HIGH = locked
    
    # Method 3: Alternative GPIO library
    os.system('echo 27 > /sys/class/gpio/export 2>/dev/null || true')
    os.system('echo out > /sys/class/gpio/gpio27/direction')
    os.system('echo 1 > /sys/class/gpio/gpio27/value')
    
    print("Lock initialized with multiple methods")
except Exception as e:
    print(f"Error initializing lock: {e}")

print("Hardware initialized successfully")

# PN532 NFC Reader setup
try:
    # Try to import the PN532 library
    import Adafruit_PN532 as PN532
    
    # Configure the PN532 for I2C mode
    pn532 = PN532.PN532(i2c=True, reset=20, req=16)
    pn532.begin()
    
    # Get firmware version to verify communication
    ic, ver, rev, support = pn532.get_firmware_version()
    print(f"Found PN532 with firmware version: {ver}.{rev}")
    
    # Configure PN532 to read RFID tags
    pn532.SAM_configuration()
    
    PN532_AVAILABLE = True
    print("PN532 initialized successfully")
except ImportError:
    print("Adafruit_PN532 library not found. Using mock NFC reader.")
    PN532_AVAILABLE = False
except Exception as e:
    print(f"Error initializing PN532: {e}")
    PN532_AVAILABLE = False

# Mock NFC reader (since we don't have the actual hardware in this environment)
class MockNFCReader:
    def __init__(self):
        self.valid_cards = ["04010203040506", "1234567890"]
        
    def read_card(self):
        # This would normally wait for a card, but we'll just return None
        # The actual reading will be triggered by the GUI
        return None
        
    def simulate_read(self, card_id):
        # Simulate a card read
        return card_id

# NFC reader instance
if PN532_AVAILABLE:
    def read_pn532():
        try:
            # Check if a card is available
            uid = pn532.read_passive_target(timeout=100)
            if uid is not None:
                # Convert UID to string
                card_id = ''.join([format(i, '02X') for i in uid])
                print(f"Card detected: {card_id}")
                return card_id
            return None
        except Exception as e:
            print(f"Error reading card: {e}")
            return None
    
    card_reader = {"read_card": read_pn532}
else:
    mock_reader = MockNFCReader()
    card_reader = {"read_card": mock_reader.read_card, "simulate_read": mock_reader.simulate_read}

# Hardware control functions
def open_gate():
    """Open the gate by activating the servo motor"""
    try:
        print("Opening gate...")
        # Set servo to open position
        servo.ChangeDutyCycle(7.5)  # Adjust as needed for your servo
        time.sleep(1)
        # Stop PWM to prevent jitter
        servo.ChangeDutyCycle(0)
        print("Gate opened")
    except Exception as e:
        print(f"Error opening gate: {e}")

def close_gate():
    """Close the gate by activating the servo motor"""
    try:
        print("Closing gate...")
        # Set servo to closed position
        servo.ChangeDutyCycle(2.5)  # Adjust as needed for your servo
        time.sleep(1)
        # Stop PWM to prevent jitter
        servo.ChangeDutyCycle(0)
        print("Gate closed")
    except Exception as e:
        print(f"Error closing gate: {e}")

def unlock_door():
    """Unlock the door by activating the solenoid lock relay"""
    try:
        print("Unlocking door...")
        # Try multiple methods to ensure the lock works
        
        # Method 1: Direct GPIO
        GPIO.output(RELAY_LOCK, GPIO.LOW)
        
        # Method 2: System command
        os.system('gpio -g write 27 0')
        
        # Method 3: Alternative GPIO library
        os.system('echo 0 > /sys/class/gpio/gpio27/value')
        
        print("Door unlocked")
    except Exception as e:
        print(f"Error unlocking door: {e}")

def lock_door():
    """Lock the door by deactivating the solenoid lock relay"""
    try:
        print("Locking door...")
        # Try multiple methods to ensure the lock works
        
        # Method 1: Direct GPIO
        GPIO.output(RELAY_LOCK, GPIO.HIGH)
        
        # Method 2: System command
        os.system('gpio -g write 27 1')
        
        # Method 3: Alternative GPIO library
        os.system('echo 1 > /sys/class/gpio/gpio27/value')
        
        print("Door locked")
    except Exception as e:
        print(f"Error locking door: {e}")

def start_motor():
    """Start the motor by activating the motor relay"""
    try:
        print("Starting motor...")
        # Set relay to LOW to start motor (as per your example)
        GPIO.output(RELAY_MOTOR, GPIO.LOW)
        print("Motor started")
    except Exception as e:
        print(f"Error starting motor: {e}")

def stop_motor():
    """Stop the motor by deactivating the motor relay"""
    try:
        print("Stopping motor...")
        # Set relay to HIGH to stop motor (as per your example)
        GPIO.output(RELAY_MOTOR, GPIO.HIGH)
        print("Motor stopped")
    except Exception as e:
        print(f"Error stopping motor: {e}")

def green_led_on():
    """Turn on the green LED (and connected buzzer)"""
    try:
        # Make sure red LED is off
        GPIO.output(LED_RED_PIN, GPIO.LOW)
        # Turn on green LED
        GPIO.output(LED_GREEN_PIN, GPIO.HIGH)
        print("Green LED on (with buzzer)")
    except Exception as e:
        print(f"Error turning on green LED: {e}")

def green_led_off():
    """Turn off the green LED (and connected buzzer)"""
    try:
        GPIO.output(LED_GREEN_PIN, GPIO.LOW)
        print("Green LED off (with buzzer)")
    except Exception as e:
        print(f"Error turning off green LED: {e}")

def red_led_on():
    """Turn on the red LED (and connected buzzer)"""
    try:
        # Make sure green LED is off
        GPIO.output(LED_GREEN_PIN, GPIO.LOW)
        # Turn on red LED
        GPIO.output(LED_RED_PIN, GPIO.HIGH)
        print("Red LED on (with buzzer)")
    except Exception as e:
        print(f"Error turning on red LED: {e}")

def red_led_off():
    """Turn off the red LED (and connected buzzer)"""
    try:
        GPIO.output(LED_RED_PIN, GPIO.LOW)
        print("Red LED off (with buzzer)")
    except Exception as e:
        print(f"Error turning off red LED: {e}")

def reset_hardware():
    """Reset all hardware to default state"""
    try:
        print("Resetting hardware...")
        # Turn off LEDs
        GPIO.output(LED_GREEN_PIN, GPIO.LOW)
        GPIO.output(LED_RED_PIN, GPIO.LOW)
        # Stop motor
        GPIO.output(RELAY_MOTOR, GPIO.HIGH)
        # Lock door
        lock_door()
        # Stop servo
        servo.ChangeDutyCycle(0)
        print("Hardware reset complete")
    except Exception as e:
        print(f"Error resetting hardware: {e}")

# Workflow functions
def valid_access_workflow():
    """Complete workflow for valid access"""
    try:
        print("Starting valid access workflow...")
        # Green LED and success sound (buzzer connected to LED)
        green_led_on()
        
        # Unlock door
        unlock_door()
        
        # Open gate
        open_gate()
        
        # Wait for person to pass through
        time.sleep(5)
        
        # Close gate
        close_gate()
        
        # Lock door
        lock_door()
        
        # Turn off green LED
        time.sleep(1)
        green_led_off()
        
        print("Valid access workflow complete")
    except Exception as e:
        print(f"Error in valid access workflow: {e}")
        # Make sure to reset hardware
        reset_hardware()

def invalid_access_workflow():
    """Complete workflow for invalid access"""
    try:
        print("Starting invalid access workflow...")
        # Red LED and error sound (buzzer connected to LED)
        red_led_on()
        
        # Wait a moment
        time.sleep(3)
        
        # Turn off red LED
        red_led_off()
        
        print("Invalid access workflow complete")
    except Exception as e:
        print(f"Error in invalid access workflow: {e}")
        # Make sure to reset hardware
        reset_hardware()

# Card reading thread
def card_reading_thread(gui_instance):
    """Thread to continuously read cards"""
    while True:
        try:
            # Try to read a card
            card_id = card_reader["read_card"]()
            if card_id:
                print(f"Card detected: {card_id}")
                gui_instance.log(f"Card detected: {card_id}")
                
                # Check if it's a valid card (in a real system, check against database)
                if card_id in ["04010203040506", "1234567890"]:
                    gui_instance.log(f"Valid card: {card_id}")
                    gui_instance.status_var.set("Valid card detected")
                    # Run valid access workflow
                    valid_access_workflow()
                else:
                    gui_instance.log(f"Invalid card: {card_id}")
                    gui_instance.status_var.set("Invalid card detected")
                    # Run invalid access workflow
                    invalid_access_workflow()
            
            # Sleep to prevent CPU overuse
            time.sleep(0.1)
        except Exception as e:
            print(f"Error in card reading thread: {e}")
            time.sleep(1)  # Sleep longer on error

# GUI for controlling the system
class AdminGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SMART ENTRY Control Panel")
        self.root.geometry("800x600")
        
        # Create main frame
        main_frame = ttk.Frame(root, padding=10)
        main_frame.pack(fill=BOTH, expand=True)
        
        # Title
        title_label = ttk.Label(main_frame, text="SMART ENTRY Control Panel", font=("Helvetica", 16, "bold"))
        title_label.pack(pady=(0, 20))
        
        # Create notebook for tabs
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=BOTH, expand=True)
        
        # Hardware control tab
        self.hw_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.hw_frame, text="Hardware Control")
        self._setup_hardware_tab()
        
        # Test scenarios tab
        self.test_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.test_frame, text="Test Scenarios")
        self._setup_test_tab()
        
        # Lock troubleshooting tab
        self.lock_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.lock_frame, text="Lock Troubleshooting")
        self._setup_lock_tab()
        
        # Logs tab
        self.logs_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.logs_frame, text="Logs")
        self._setup_logs_tab()
        
        # Status bar
        self.status_var = tk.StringVar()
        self.status_var.set("System Ready")
        status_bar = ttk.Label(root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=BOTTOM, fill=X)
        
        # Reset hardware on startup
        reset_hardware()
        
        # Start card reading thread if PN532 is available
        if PN532_AVAILABLE:
            self.log("Starting card reading thread")
            threading.Thread(target=card_reading_thread, args=(self,), daemon=True).start()
        
    def _setup_hardware_tab(self):
        # Gate control frame
        gate_frame = ttk.LabelFrame(self.hw_frame, text="Gate Control")
        gate_frame.pack(fill=X, pady=10)
        
        ttk.Button(gate_frame, text="Open Gate", command=self._open_gate).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(gate_frame, text="Close Gate", command=self._close_gate).pack(side=LEFT, padx=10, pady=10)
        
        # Lock control frame
        lock_frame = ttk.LabelFrame(self.hw_frame, text="Lock Control")
        lock_frame.pack(fill=X, pady=10)
        
        ttk.Button(lock_frame, text="Lock Door", command=self._lock_door).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(lock_frame, text="Unlock Door", command=self._unlock_door).pack(side=LEFT, padx=10, pady=10)
        
        # Motor control frame
        motor_frame = ttk.LabelFrame(self.hw_frame, text="Motor Control")
        motor_frame.pack(fill=X, pady=10)
        
        ttk.Button(motor_frame, text="Start Motor", command=self._start_motor).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(motor_frame, text="Stop Motor", command=self._stop_motor).pack(side=LEFT, padx=10, pady=10)
        
        # LED control frame
        led_frame = ttk.LabelFrame(self.hw_frame, text="LED Control (with Buzzers)")
        led_frame.pack(fill=X, pady=10)
        
        ttk.Button(led_frame, text="Green LED On", command=self._green_led_on).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(led_frame, text="Green LED Off", command=self._green_led_off).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(led_frame, text="Red LED On", command=self._red_led_on).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(led_frame, text="Red LED Off", command=self._red_led_off).pack(side=LEFT, padx=10, pady=10)
        
        # Reset hardware button
        reset_frame = ttk.Frame(self.hw_frame)
        reset_frame.pack(fill=X, pady=20)
        
        ttk.Button(reset_frame, text="Reset All Hardware", command=self._reset_hardware).pack(pady=10)
        
    def _setup_test_tab(self):
        # Test scenarios frame
        test_frame = ttk.LabelFrame(self.test_frame, text="Test Complete Workflows")
        test_frame.pack(fill=X, pady=10)
        
        ttk.Button(test_frame, text="Test Valid Access", command=self._test_valid_access).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(test_frame, text="Test Invalid Access", command=self._test_invalid_access).pack(side=LEFT, padx=10, pady=10)
        
        # Card simulation frame
        card_frame = ttk.LabelFrame(self.test_frame, text="Simulate Card Scan")
        card_frame.pack(fill=X, pady=10)
        
        ttk.Label(card_frame, text="Card ID:").pack(side=LEFT, padx=10, pady=10)
        self.card_id_var = tk.StringVar()
        self.card_id_var.set("04010203040506")  # Default valid card
        ttk.Entry(card_frame, textvariable=self.card_id_var, width=20).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(card_frame, text="Scan Card", command=self._simulate_card_scan).pack(side=LEFT, padx=10, pady=10)
        
        # PN532 status frame
        pn532_frame = ttk.LabelFrame(self.test_frame, text="PN532 Status")
        pn532_frame.pack(fill=X, pady=10)
        
        status_text = "PN532 Available" if PN532_AVAILABLE else "PN532 Not Available (Using Mock)"
        ttk.Label(pn532_frame, text=status_text).pack(side=LEFT, padx=10, pady=10)
        
        if not PN532_AVAILABLE:
            ttk.Button(pn532_frame, text="Retry PN532 Initialization", command=self._retry_pn532).pack(side=LEFT, padx=10, pady=10)
    
    def _setup_lock_tab(self):
        # Lock troubleshooting instructions
        ttk.Label(
            self.lock_frame, 
            text="Lock Troubleshooting", 
            font=("Helvetica", 14, "bold")
        ).pack(pady=(0, 10))
        
        ttk.Label(
            self.lock_frame,
            text="If the lock is not working, try these direct commands:",
            wraplength=700
        ).pack(pady=5)
        
        # Method 1: GPIO commands
        method1_frame = ttk.LabelFrame(self.lock_frame, text="Method 1: Direct GPIO Commands")
        method1_frame.pack(fill=X, pady=10)
        
        ttk.Button(method1_frame, text="Lock (GPIO HIGH)", command=lambda: self._direct_lock(1)).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(method1_frame, text="Unlock (GPIO LOW)", command=lambda: self._direct_lock(0)).pack(side=LEFT, padx=10, pady=10)
        
        # Method 2: System commands
        method2_frame = ttk.LabelFrame(self.lock_frame, text="Method 2: System Commands")
        method2_frame.pack(fill=X, pady=10)
        
        ttk.Button(method2_frame, text="Lock (gpio write 1)", command=lambda: self._gpio_command(1)).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(method2_frame, text="Unlock (gpio write 0)", command=lambda: self._gpio_command(0)).pack(side=LEFT, padx=10, pady=10)
        
        # Method 3: Sysfs interface
        method3_frame = ttk.LabelFrame(self.lock_frame, text="Method 3: Sysfs Interface")
        method3_frame.pack(fill=X, pady=10)
        
        ttk.Button(method3_frame, text="Lock (echo 1)", command=lambda: self._sysfs_command(1)).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(method3_frame, text="Unlock (echo 0)", command=lambda: self._sysfs_command(0)).pack(side=LEFT, padx=10, pady=10)
        
        # Pulse test
        pulse_frame = ttk.LabelFrame(self.lock_frame, text="Pulse Test (Rapidly Toggle Lock)")
        pulse_frame.pack(fill=X, pady=10)
        
        ttk.Button(pulse_frame, text="Run Pulse Test", command=self._pulse_test).pack(side=LEFT, padx=10, pady=10)
        
    def _setup_logs_tab(self):
        # Logs text area
        self.logs_text = Text(self.logs_frame, height=20, width=80)
        self.logs_text.pack(side=LEFT, fill=BOTH, expand=True)
        
        # Add scrollbar
        scrollbar = Scrollbar(self.logs_frame, command=self.logs_text.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.logs_text.config(yscrollcommand=scrollbar.set)
        
        # Add initial log
        self.log("System started")
        
    def log(self, message):
        """Add a message to the logs"""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.logs_text.insert(END, f"[{timestamp}] {message}\n")
        self.logs_text.see(END)  # Scroll to bottom
        
    def _open_gate(self):
        open_gate()
        self.status_var.set("Gate opened")
        self.log("Gate opened")
        
    def _close_gate(self):
        close_gate()
        self.status_var.set("Gate closed")
        self.log("Gate closed")
        
    def _lock_door(self):
        lock_door()
        self.status_var.set("Door locked")
        self.log("Door locked")
        
    def _unlock_door(self):
        unlock_door()
        self.status_var.set("Door unlocked")
        self.log("Door unlocked")
        
    def _start_motor(self):
        start_motor()
        self.status_var.set("Motor started")
        self.log("Motor started")
        
    def _stop_motor(self):
        stop_motor()
        self.status_var.set("Motor stopped")
        self.log("Motor stopped")
        
    def _green_led_on(self):
        green_led_on()
        self.status_var.set("Green LED on")
        self.log("Green LED turned on (with buzzer)")
        
    def _green_led_off(self):
        green_led_off()
        self.status_var.set("Green LED off")
        self.log("Green LED turned off (with buzzer)")
        
    def _red_led_on(self):
        red_led_on()
        self.status_var.set("Red LED on")
        self.log("Red LED turned on (with buzzer)")
        
    def _red_led_off(self):
        red_led_off()
        self.status_var.set("Red LED off")
        self.log("Red LED turned off (with buzzer)")
        
    def _reset_hardware(self):
        reset_hardware()
        self.status_var.set("Hardware reset")
        self.log("All hardware reset to default state")
        
    def _test_valid_access(self):
        self.log("Testing valid access workflow")
        self.status_var.set("Running valid access test")
        # Run in a separate thread to not block the GUI
        threading.Thread(target=valid_access_workflow).start()
        
    def _test_invalid_access(self):
        self.log("Testing invalid access workflow")
        self.status_var.set("Running invalid access test")
        # Run in a separate thread to not block the GUI
        threading.Thread(target=invalid_access_workflow).start()
        
    def _simulate_card_scan(self):
        card_id = self.card_id_var.get()
        self.log(f"Simulating card scan: {card_id}")
        
        # Check if it's a valid card
        if card_id in ["04010203040506", "1234567890"]:
            self.log(f"Valid card detected: {card_id}")
            self.status_var.set("Valid card scanned")
            # Run valid access workflow
            threading.Thread(target=valid_access_workflow).start()
        else:
            self.log(f"Invalid card detected: {card_id}")
            self.status_var.set("Invalid card scanned")
            # Run invalid access workflow
            threading.Thread(target=invalid_access_workflow).start()
            
    def _retry_pn532(self):
        self.log("Attempting to reinitialize PN532...")
        try:
            # Try to import the PN532 library
            import Adafruit_PN532 as PN532
            
            # Configure the PN532 for I2C mode
            pn532 = PN532.PN532(i2c=True, reset=20, req=16)
            pn532.begin()
            
            # Get firmware version to verify communication
            ic, ver, rev, support = pn532.get_firmware_version()
            self.log(f"Found PN532 with firmware version: {ver}.{rev}")
            
            # Configure PN532 to read RFID tags
            pn532.SAM_configuration()
            
            global PN532_AVAILABLE
            PN532_AVAILABLE = True
            self.log("PN532 initialized successfully")
            
            # Restart the card reading thread
            threading.Thread(target=card_reading_thread, args=(self,), daemon=True).start()
            
            # Update the status
            self.status_var.set("PN532 initialized")
            
            # Refresh the test tab
            self.notebook.forget(1)  # Remove the old tab
            self.test_frame = ttk.Frame(self.notebook, padding=10)
            self.notebook.insert(1, self.test_frame, text="Test Scenarios")
            self._setup_test_tab()
            
        except ImportError:
            self.log("Adafruit_PN532 library not found. Using mock NFC reader.")
            self.status_var.set("PN532 library not found")
        except Exception as e:
            self.log(f"Error initializing PN532: {e}")
            self.status_var.set("PN532 initialization failed")
            
    def _direct_lock(self, state):
        """Direct GPIO control of lock"""
        try:
            GPIO.output(RELAY_LOCK, state)
            self.log(f"Direct GPIO: Set lock to {'HIGH (locked)' if state else 'LOW (unlocked)'}")
            self.status_var.set(f"Lock set to {'HIGH' if state else 'LOW'} via GPIO")
        except Exception as e:
            self.log(f"Error in direct GPIO control: {e}")
            
    def _gpio_command(self, state):
        """Control lock via gpio command"""
        try:
            os.system(f'gpio -g write 27 {state}')
            self.log(f"System command: Set lock to {'HIGH (locked)' if state else 'LOW (unlocked)'}")
            self.status_var.set(f"Lock set to {'HIGH' if state else 'LOW'} via gpio command")
        except Exception as e:
            self.log(f"Error in gpio command: {e}")
            
    def _sysfs_command(self, state):
        """Control lock via sysfs interface"""
        try:
            os.system('echo 27 > /sys/class/gpio/export 2>/dev/null || true')
            os.system('echo out > /sys/class/gpio/gpio27/direction')
            os.system(f'echo {state} > /sys/class/gpio/gpio27/value')
            self.log(f"Sysfs: Set lock to {'HIGH (locked)' if state else 'LOW (unlocked)'}")
            self.status_var.set(f"Lock set to {'HIGH' if state else 'LOW'} via sysfs")
        except Exception as e:
            self.log(f"Error in sysfs command: {e}")
            
    def _pulse_test(self):
        """Rapidly toggle the lock to test it"""
        def pulse_thread():
            try:
                self.log("Starting lock pulse test...")
                self.status_var.set("Running lock pulse test")
                
                # Pulse the lock 5 times
                for i in range(5):
                    # Unlock
                    GPIO.output(RELAY_LOCK, GPIO.LOW)
                    os.system('gpio -g write 27 0')
                    self.log(f"Pulse {i+1}/5: Unlocked")
                    time.sleep(0.5)
                    
                    # Lock
                    GPIO.output(RELAY_LOCK, GPIO.HIGH)
                    os.system('gpio -g write 27 1')
                    self.log(f"Pulse {i+1}/5: Locked")
                    time.sleep(0.5)
                    
                self.log("Lock pulse test complete")
                self.status_var.set("Lock pulse test complete")
            except Exception as e:
                self.log(f"Error in pulse test: {e}")
                
        threading.Thread(target=pulse_thread).start()

# Main function
def main():
    try:
        # Initialize Tkinter
        root = Tk()
        app = AdminGUI(root)
        
        # Run the GUI
        root.mainloop()
    except Exception as e:
        print(f"Error in main function: {e}")
    finally:
        # Clean up GPIO
        try:
            servo.stop()
            GPIO.cleanup()
            print("GPIO cleanup complete")
        except:
            pass

if __name__ == "__main__":
    main()
