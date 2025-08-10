# ===================================================================================
# Gate Control System for Raspberry Pi - MODIFIED FOR NEW WIRING
# Version: 7.1
#
# --- CRITICAL SAFETY & USAGE NOTES ---
# 1. LOGIC LEVEL SHIFTERS REQUIRED: Your wiring powers the PN532 and IR Sensor
#    with 5V. The Raspberry Pi's GPIOs are 3.3V ONLY. You MUST use a
#    bidirectional logic level shifter between the 5V sensor/RFID data pins
#    and the Pi's GPIO pins to prevent permanent damage to your Pi.
#
# 2. NO GATE SENSOR: This code has been modified to work WITHOUT a physical
#    gate position sensor. The system now assumes the gate's state (Open/Closed)
#    based on timers. This is less reliable than using a real sensor.
#
# 3. ACTIVE-LOW RELAY: This code assumes your relay module is "active-low,"
#    meaning a LOW signal on the IN pin turns the relay ON (unlocking the gate).
#    If your lock works in reverse, swap GPIO.HIGH and GPIO.LOW in the
#    lock_gate() and unlock_gate() methods.
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
from typing import Dict, List, Optional, Tuple, Set, Any, Union
import subprocess
import signal
import sys
from dataclasses import dataclass
from enum import Enum, auto

# Try to import RPi.GPIO, fallback to mock for testing
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    print("Warning: RPi.GPIO not available. Using mock GPIO for testing.")
    GPIO_AVAILABLE = False
    
    # Mock GPIO class for testing on non-RPi systems
    class MockGPIO:
        BCM = "BCM"
        OUT = "OUT"
        IN = "IN"
        HIGH = 1
        LOW = 0
        PUD_UP = "PUD_UP"
        PUD_DOWN = "PUD_DOWN"
        RISING = "RISING"
        FALLING = "FALLING"
        BOTH = "BOTH"
        
        @staticmethod
        def setmode(mode): pass
        @staticmethod
        def setup(pin, mode, pull_up_down=None): pass
        @staticmethod
        def output(pin, state): pass
        @staticmethod
        def input(pin): return 0
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
    'security': {
        'max_attempts': 3,
        'lockout_time': 300,
        'unauthorized_cooldown': 60,
        'card_cooldown': 5,
        'auto_close_delay': 10
    },
    'hardware': {
        'servo_frequency': 50,
        'servo_open_duty': 7.5,
        'servo_close_duty': 2.5,
        'buzzer_frequency': 2000,
        'sensor_debounce_time': 200
    },
    'logging': {
        'max_log_size': 1024 * 1024,  # 1MB
        'backup_count': 3,
        'log_level': 'INFO'
    }
}

# Define enums for better type safety
class GateState(Enum):
    CLOSED = "CLOSED"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"

class LockState(Enum):
    LOCKED = "LOCKED"
    UNLOCKED = "UNLOCKED"
    UNKNOWN = "UNKNOWN"

class SecurityLevel(Enum):
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EMERGENCY = "EMERGENCY"

@dataclass
class SecurityConfig:
    max_attempts: int
    lockout_time: int
    unauthorized_cooldown: int
    card_cooldown: int
    auto_close_delay: int

@dataclass
class HardwareConfig:
    servo_frequency: int
    servo_open_duty: float
    servo_close_duty: float
    buzzer_frequency: int
    sensor_debounce_time: int

@dataclass
class LoggingConfig:
    max_log_size: int
    backup_count: int
    log_level: str

class ConfigurationManager:
    """
    Manages system configuration and settings.
    Handles loading, saving, and updating configuration.
    """
    
    def __init__(self) -> None:
        """
        Initialize configuration manager.
        Loads or creates default configuration.
        """
        self.config = self.load_config()
        self.validate_config()
        
    def load_config(self) -> Dict[str, Any]:
        """
        Load configuration from file or create default.
        
        Returns:
            Dict[str, Any]: The loaded configuration
        """
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                logging.info("Configuration loaded successfully")
                return config
            else:
                logging.info("No configuration file found, using defaults")
                return DEFAULT_CONFIG.copy()
        except Exception as e:
            logging.error(f"Error loading configuration: {e}")
            return DEFAULT_CONFIG.copy()
    
    def save_config(self) -> None:
        """
        Save current configuration to file.
        """
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=4)
            logging.info("Configuration saved successfully")
        except Exception as e:
            logging.error(f"Error saving configuration: {e}")
    
    def validate_config(self) -> None:
        """
        Validate configuration values and fix if necessary.
        """
        # Ensure all required sections exist
        for section in DEFAULT_CONFIG:
            if section not in self.config:
                self.config[section] = DEFAULT_CONFIG[section]
                logging.warning(f"Missing config section: {section}, using defaults")
        
        # Validate and fix values
        for section, values in DEFAULT_CONFIG.items():
            for key, default_value in values.items():
                if key not in self.config[section]:
                    self.config[section][key] = default_value
                    logging.warning(f"Missing config value: {section}.{key}, using default")
        
        # Validate data types
        try:
            # Security section
            self.config['security']['max_attempts'] = int(self.config['security']['max_attempts'])
            self.config['security']['lockout_time'] = int(self.config['security']['lockout_time'])
            self.config['security']['unauthorized_cooldown'] = int(self.config['security']['unauthorized_cooldown'])
            self.config['security']['card_cooldown'] = int(self.config['security']['card_cooldown'])
            self.config['security']['auto_close_delay'] = int(self.config['security']['auto_close_delay'])
            
            # Hardware section
            self.config['hardware']['servo_frequency'] = int(self.config['hardware']['servo_frequency'])
            self.config['hardware']['servo_open_duty'] = float(self.config['hardware']['servo_open_duty'])
            self.config['hardware']['servo_close_duty'] = float(self.config['hardware']['servo_close_duty'])
            self.config['hardware']['buzzer_frequency'] = int(self.config['hardware']['buzzer_frequency'])
            self.config['hardware']['sensor_debounce_time'] = int(self.config['hardware']['sensor_debounce_time'])
            
            # Logging section
            self.config['logging']['max_log_size'] = int(self.config['logging']['max_log_size'])
            self.config['logging']['backup_count'] = int(self.config['logging']['backup_count'])
            self.config['logging']['log_level'] = str(self.config['logging']['log_level'])
            
        except (ValueError, TypeError) as e:
            logging.error(f"Invalid configuration value type: {e}")
            # Reset to defaults if validation fails
            self.config = DEFAULT_CONFIG.copy()
        
        # Save validated configuration
        self.save_config()
    
    def get_security_config(self) -> SecurityConfig:
        """
        Get security configuration.
        
        Returns:
            SecurityConfig: Security configuration object
        """
        sec_config = self.config['security']
        return SecurityConfig(
            max_attempts=sec_config['max_attempts'],
            lockout_time=sec_config['lockout_time'],
            unauthorized_cooldown=sec_config['unauthorized_cooldown'],
            card_cooldown=sec_config['card_cooldown'],
            auto_close_delay=sec_config['auto_close_delay']
        )
    
    def get_hardware_config(self) -> HardwareConfig:
        """
        Get hardware configuration.
        
        Returns:
            HardwareConfig: Hardware configuration object
        """
        hw_config = self.config['hardware']
        return HardwareConfig(
            servo_frequency=hw_config['servo_frequency'],
            servo_open_duty=hw_config['servo_open_duty'],
            servo_close_duty=hw_config['servo_close_duty'],
            buzzer_frequency=hw_config['buzzer_frequency'],
            sensor_debounce_time=hw_config['sensor_debounce_time']
        )
    
    def get_logging_config(self) -> LoggingConfig:
        """
        Get logging configuration.
        
        Returns:
            LoggingConfig: Logging configuration object
        """
        log_config = self.config['logging']
        return LoggingConfig(
            max_log_size=log_config['max_log_size'],
            backup_count=log_config['backup_count'],
            log_level=log_config['log_level']
        )

# ==============================================================================
# === HARDWARE DEFINITIONS UPDATED TO MATCH YOUR NEW WIRING ====================
# ==============================================================================
HARDWARE_PINS = {
    'SERVO_PIN': 18,        # GPIO18 for servo motor control (Signal)
    'RELAY_PIN': 17,        # GPIO17 for relay control (IN) -> Controls the 12V lock
    'IR_SENSOR_PIN': 4,     # GPIO4 for IR sensor (OUT)
    'GREEN_LED_BUZZER_PIN': 22, # GPIO22 for Green LED and Buzzer 1
    'RED_LED_BUZZER_PIN': 27,   # GPIO27 for Red LED and Buzzer 2
}

# ==============================================================================
# === HARDWARE CONTROLLER REWRITTEN FOR NEW WIRING AND NO GATE SENSOR ==========
# ==============================================================================
class RPiHardwareController:
    """
    Manages direct hardware control on Raspberry Pi.
    Handles GPIO control for all hardware components. (REVISED FOR NEW WIRING)
    """
    
    def __init__(self, config: HardwareConfig) -> None:
        self.config = config
        self.running = True
        
        # State variables (now software-tracked due to no gate sensor)
        self.gate_state = GateState.CLOSED
        self.lock_state = LockState.LOCKED
        
        self.servo_pwm = None
        self.event_queue: queue.Queue[str] = queue.Queue()
        
        self.initialize_gpio()
        logging.info("RPi Hardware Controller initialized successfully")

    def initialize_gpio(self) -> None:
        """
        Initialize GPIO pins and hardware components. (REVISED)
        """
        try:
            GPIO.setmode(GPIO.BCM)
            
            # Setup output pins
            GPIO.setup(HARDWARE_PINS['SERVO_PIN'], GPIO.OUT)
            GPIO.setup(HARDWARE_PINS['RELAY_PIN'], GPIO.OUT)
            GPIO.setup(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], GPIO.OUT)
            GPIO.setup(HARDWARE_PINS['RED_LED_BUZZER_PIN'], GPIO.OUT)
            
            # Setup input pin for IR Sensor
            GPIO.setup(HARDWARE_PINS['IR_SENSOR_PIN'], GPIO.IN, pull_up_down=GPIO.PUD_UP)
            
            # Initialize servo PWM
            self.servo_pwm = GPIO.PWM(HARDWARE_PINS['SERVO_PIN'], self.config.servo_frequency)
            self.servo_pwm.start(self.config.servo_close_duty)
            
            # Initialize outputs to safe states
            # IMPORTANT: Relay modules are often "active-low", meaning LOW turns them ON.
            # We set the pin HIGH to ensure the relay is OFF (and the gate is LOCKED).
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.HIGH) # Relay OFF = Locked
            GPIO.output(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], GPIO.LOW)
            GPIO.output(HARDWARE_PINS['RED_LED_BUZZER_PIN'], GPIO.LOW)
            
            # Setup event detection for the IR sensor ONLY.
            # This fixes the "Failed to add edge detection" error as we no longer
            # try to use a non-existent gate sensor pin.
            ir_pin = HARDWARE_PINS['IR_SENSOR_PIN']
            logging.info(f"Adding event detection for IR_SENSOR on GPIO {ir_pin}")
            GPIO.add_event_detect(
                ir_pin, 
                GPIO.FALLING, 
                callback=self._ir_sensor_callback,
                bouncetime=self.config.sensor_debounce_time
            )
            
            logging.info("GPIO initialization completed successfully")
            
        except Exception as e:
            logging.error(f"Error initializing GPIO: {e}")
            raise

    # The _monitor_hardware_status and _gate_sensor_callback methods are removed
    # because the gate sensor hardware does not exist in the new wiring plan.

    def _ir_sensor_callback(self, channel):
        """Callback for IR sensor detection."""
        try:
            self.event_queue.put("UNAUTHORIZED_ACCESS_DETECTED")
            logging.warning("Unauthorized access detected by IR sensor")
        except Exception as e:
            logging.error(f"Error in IR sensor callback: {e}")

    def open_gate(self) -> bool:
        """Opens the gate. Assumes success after a delay."""
        try:
            if self.gate_state == GateState.OPEN:
                return True # Already open
            
            logging.info("Opening gate...")
            self.gate_state = GateState.OPENING
            
            # Unlock first
            self.unlock_gate()
            time.sleep(0.5) # Wait for relay to switch
            
            # Move servo to open position
            self.servo_pwm.ChangeDutyCycle(self.config.servo_open_duty)
            time.sleep(1.5) # Allow time for servo to move fully
            self.servo_pwm.ChangeDutyCycle(0) # Stop PWM signal to prevent jitter
            
            self.gate_state = GateState.OPEN
            self.event_queue.put("GATE_OPENED") # Notify system state changed
            logging.info("Gate is now assumed to be OPEN")
            return True
            
        except Exception as e:
            logging.error(f"Error opening gate: {e}")
            self.gate_state = GateState.ERROR
            return False

    def close_gate(self) -> bool:
        """Closes the gate. Assumes success and locks it."""
        try:
            if self.gate_state == GateState.CLOSED:
                return True # Already closed

            logging.info("Closing gate...")
            self.gate_state = GateState.CLOSING
            
            # Move servo to closed position
            self.servo_pwm.ChangeDutyCycle(self.config.servo_close_duty)
            time.sleep(1.5) # Allow time for servo to move fully
            self.servo_pwm.ChangeDutyCycle(0) # Stop PWM signal

            self.gate_state = GateState.CLOSED
            logging.info("Gate is now assumed to be CLOSED")
            
            # Lock the gate after closing
            time.sleep(0.5)
            self.lock_gate()
            
            self.event_queue.put("GATE_CLOSED") # Notify system state changed
            return True
            
        except Exception as e:
            logging.error(f"Error closing gate: {e}")
            self.gate_state = GateState.ERROR
            return False

    def lock_gate(self) -> bool:
        """Engage the lock by turning the relay OFF (assumes active-low relay)."""
        try:
            # For most relay modules, HIGH on the IN pin means OFF.
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.HIGH)
            self.lock_state = LockState.LOCKED
            logging.info("Gate locked (Relay OFF)")
            return True
        except Exception as e:
            logging.error(f"Error locking gate: {e}")
            return False

    def unlock_gate(self) -> bool:
        """Disengage the lock by turning the relay ON (assumes active-low relay)."""
        try:
            # For most relay modules, LOW on the IN pin means ON.
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.LOW)
            self.lock_state = LockState.UNLOCKED
            logging.info("Gate unlocked (Relay ON)")
            return True
        except Exception as e:
            logging.error(f"Error unlocking gate: {e}")
            return False
            
    def _flash_pin(self, pin_num: int, duration: float):
        """Helper function to flash a pin for a given duration."""
        try:
            GPIO.output(pin_num, GPIO.HIGH)
            time.sleep(duration)
            GPIO.output(pin_num, GPIO.LOW)
        except Exception as e:
            logging.error(f"Error flashing pin {pin_num}: {e}")

    def access_granted_feedback(self) -> None:
        """Provide visual and audio feedback for access granted."""
        try:
            # Short flash/beep for success
            self._flash_pin(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], 0.2)
        except Exception as e:
            logging.error(f"Error in access granted feedback: {e}")

    def access_denied_feedback(self) -> None:
        """Provide visual and audio feedback for access denied."""
        try:
            # Long flash/beep for denial
            self._flash_pin(HARDWARE_PINS['RED_LED_BUZZER_PIN'], 1.0)
        except Exception as e:
            logging.error(f"Error in access denied feedback: {e}")

    def alarm_feedback(self, duration: float = 5.0) -> None:
        """Provide alarm feedback for unauthorized access."""
        try:
            end_time = time.time() + duration
            pin = HARDWARE_PINS['RED_LED_BUZZER_PIN']
            while time.time() < end_time and self.running:
                GPIO.output(pin, GPIO.HIGH)
                time.sleep(0.1)
                GPIO.output(pin, GPIO.LOW)
                time.sleep(0.1)
            GPIO.output(pin, GPIO.LOW) # Ensure it's off
        except Exception as e:
            logging.error(f"Error in alarm feedback: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Get current hardware status."""
        try:
            return {
                'gate_state': self.gate_state.value, # Software-tracked state
                'lock_state': self.lock_state.value,
                'ir_sensor': GPIO.input(HARDWARE_PINS['IR_SENSOR_PIN']),
                'connected': True,
                'last_update': datetime.now().isoformat()
            }
        except Exception as e:
            logging.error(f"Error getting hardware status: {e}")
            return {'gate_state': 'ERROR', 'connected': False, 'error': str(e)}

    def cleanup(self) -> None:
        """Clean up GPIO resources."""
        try:
            self.running = False
            if self.servo_pwm:
                self.servo_pwm.stop()
            
            GPIO.output(HARDWARE_PINS['GREEN_LED_BUZZER_PIN'], GPIO.LOW)
            GPIO.output(HARDWARE_PINS['RED_LED_BUZZER_PIN'], GPIO.LOW)
            GPIO.output(HARDWARE_PINS['RELAY_PIN'], GPIO.HIGH) # Turn relay off

            if GPIO_AVAILABLE:
                GPIO.remove_event_detect(HARDWARE_PINS['IR_SENSOR_PIN'])
            
            GPIO.cleanup()
            logging.info("GPIO cleanup completed")
        except Exception as e:
            logging.error(f"Error during GPIO cleanup: {e}")


# ==============================================================================
# === THE FOLLOWING CLASSES REMAIN LARGELY UNCHANGED ===========================
# ==============================================================================

# NFC Card Manager
class NFCCardManager:
    """
    Manages NFC card reading and authentication.
    """
    
    def __init__(self) -> None:
        self.pn532 = None
        self.authorized_cards: Set[str] = set()
        self.card_names: Dict[str, str] = {}
        self.last_card_time: Dict[str, datetime] = {}
        self.failed_attempts: Dict[str, int] = {}
        self.lockout_until: Dict[str, datetime] = {}
        
        # Load authorized cards
        self.load_authorized_cards()
        
        # Initialize NFC reader
        self.initialize_nfc()

    def initialize_nfc(self) -> None:
        """
        Initialize the PN532 NFC reader.
        """
        try:
            # Create I2C bus
            i2c = busio.I2C(board.SCL, board.SDA)
            
            # Create PN532 instance
            self.pn532 = PN532.PN532_I2C(i2c, debug=False)
            
            # Configure PN532
            ic, ver, rev, support = self.pn532.firmware_version
            logging.info(f"Found PN532 with firmware version: {ver}.{rev}")
            
            # Configure PN532 to communicate with MiFare cards
            self.pn532.SAM_configuration()
            
            logging.info("NFC reader initialized successfully")
            
        except Exception as e:
            logging.error(f"Error initializing NFC reader: {e}")
            self.pn532 = None

    def load_authorized_cards(self) -> None:
        """
        Load authorized cards from file.
        """
        try:
            if os.path.exists('authorized_cards.json'):
                with open('authorized_cards.json', 'r') as f:
                    data = json.load(f)
                    self.authorized_cards = set(data.get('cards', []))
                    self.card_names = data.get('names', {})
                logging.info(f"Loaded {len(self.authorized_cards)} authorized cards")
            else:
                # Create default file with sample cards
                self.save_authorized_cards()
                logging.info("Created default authorized cards file")
        except Exception as e:
            logging.error(f"Error loading authorized cards: {e}")

    def save_authorized_cards(self) -> None:
        """
        Save authorized cards to file.
        """
        try:
            data = {
                'cards': list(self.authorized_cards),
                'names': self.card_names
            }
            with open('authorized_cards.json', 'w') as f:
                json.dump(data, f, indent=4)
            logging.info("Authorized cards saved successfully")
        except Exception as e:
            logging.error(f"Error saving authorized cards: {e}")

    def read_card(self) -> Optional[Tuple[str, str]]:
        """
        Read an NFC card and return its UID and name.
        
        Returns:
            Tuple of (card_uid, card_name) if successful, None otherwise
        """
        if not self.pn532:
            return None
            
        try:
            # Check for a card
            uid = self.pn532.read_passive_target(timeout=0.5)
            
            if uid is not None:
                # Convert UID to hex string
                card_uid = ''.join([hex(i)[2:].upper().zfill(2) for i in uid])
                card_name = self.card_names.get(card_uid, "Unknown Card")
                
                logging.info(f"Card detected: {card_uid} ({card_name})")
                return card_uid, card_name
                
        except Exception as e:
            logging.error(f"Error reading NFC card: {e}")
            
        return None

    def is_card_authorized(self, card_uid: str, security_config: SecurityConfig) -> bool:
        """
        Check if a card is authorized and handle security policies.
        
        Args:
            card_uid: The card UID to check
            security_config: Security configuration
            
        Returns:
            bool: True if authorized and not locked out, False otherwise
        """
        current_time = datetime.now()
        
        # Check if card is in lockout
        if card_uid in self.lockout_until:
            if current_time < self.lockout_until[card_uid]:
                logging.warning(f"Card {card_uid} is locked out until {self.lockout_until[card_uid]}")
                return False
            else:
                # Lockout expired, remove it
                del self.lockout_until[card_uid]
                if card_uid in self.failed_attempts:
                    del self.failed_attempts[card_uid]

        # Check cooldown period
        if card_uid in self.last_card_time:
            time_since_last = (current_time - self.last_card_time[card_uid]).total_seconds()
            if time_since_last < security_config.card_cooldown:
                logging.info(f"Card {card_uid} in cooldown period")
                return False

        # Update last card time
        self.last_card_time[card_uid] = current_time

        # Check if card is authorized
        if card_uid in self.authorized_cards:
            # Reset failed attempts on successful authorization
            if card_uid in self.failed_attempts:
                del self.failed_attempts[card_uid]
            return True
        else:
            # Handle failed attempt
            self.failed_attempts[card_uid] = self.failed_attempts.get(card_uid, 0) + 1
            
            if self.failed_attempts[card_uid] >= security_config.max_attempts:
                # Lock out the card
                lockout_until = current_time + timedelta(seconds=security_config.lockout_time)
                self.lockout_until[card_uid] = lockout_until
                logging.warning(f"Card {card_uid} locked out until {lockout_until}")
            
            return False

    def add_card(self, card_uid: str, card_name: str) -> None:
        """
        Add a card to the authorized list.
        
        Args:
            card_uid: The card UID
            card_name: Human-readable name for the card
        """
        self.authorized_cards.add(card_uid)
        self.card_names[card_uid] = card_name
        self.save_authorized_cards()
        logging.info(f"Added authorized card: {card_uid} ({card_name})")

    def remove_card(self, card_uid: str) -> None:
        """
        Remove a card from the authorized list.
        
        Args:
            card_uid: The card UID to remove
        """
        if card_uid in self.authorized_cards:
            self.authorized_cards.remove(card_uid)
            if card_uid in self.card_names:
                del self.card_names[card_uid]
            self.save_authorized_cards()
            logging.info(f"Removed authorized card: {card_uid}")

# Access Log Manager
class AccessLogManager:
    """
    Manages access logging and history.
    """
    
    def __init__(self, max_entries: int = 1000) -> None:
        self.max_entries = max_entries
        self.access_log: deque = deque(maxlen=max_entries)
        self.log_file = 'access_log.json'
        
        # Load existing log
        self.load_log()

    def load_log(self) -> None:
        """
        Load access log from file.
        """
        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r') as f:
                    log_data = json.load(f)
                    self.access_log = deque(log_data, maxlen=self.max_entries)
                logging.info(f"Loaded {len(self.access_log)} access log entries")
            else:
                logging.info("No existing access log found")
        except Exception as e:
            logging.error(f"Error loading access log: {e}")

    def save_log(self) -> None:
        """
        Save access log to file.
        """
        try:
            with open(self.log_file, 'w') as f:
                json.dump(list(self.access_log), f, indent=2)
        except Exception as e:
            logging.error(f"Error saving access log: {e}")

    def log_access(self, card_uid: str, card_name: str, granted: bool, reason: str = "") -> None:
        """
        Log an access attempt.
        
        Args:
            card_uid: The card UID
            card_name: Human-readable card name
            granted: Whether access was granted
            reason: Additional reason information
        """
        entry = {
            'timestamp': datetime.now().isoformat(),
            'card_uid': card_uid,
            'card_name': card_name,
            'access_granted': granted,
            'reason': reason
        }
        
        self.access_log.append(entry)
        self.save_log()
        
        status = "GRANTED" if granted else "DENIED"
        logging.info(f"Access {status}: {card_name} ({card_uid}) - {reason}")

    def log_event(self, event_type: str, description: str) -> None:
        """
        Log a system event.
        
        Args:
            event_type: Type of event
            description: Event description
        """
        entry = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'description': description
        }
        
        self.access_log.append(entry)
        self.save_log()
        
        logging.info(f"Event logged: {event_type} - {description}")

    def get_recent_entries(self, count: int = 50) -> List[Dict[str, Any]]:
        """
        Get recent access log entries.
        
        Args:
            count: Number of entries to return
            
        Returns:
            List of recent log entries
        """
        return list(self.access_log)[-count:]

# Main Gate Control System
class GateControlSystem:
    """
    Main gate control system that coordinates all components.
    """
    
    def __init__(self) -> None:
        # Initialize configuration
        self.config_manager = ConfigurationManager()
        self.security_config = self.config_manager.get_security_config()
        self.hardware_config = self.config_manager.get_hardware_config()
        
        # Initialize components
        self.hardware_controller = RPiHardwareController(self.hardware_config)
        self.nfc_manager = NFCCardManager()
        self.access_log = AccessLogManager()
        
        # System state
        self.running = True
        self.security_level = SecurityLevel.NORMAL
        self.last_access_time = None
        self.auto_close_timer = None
        
        # Start main control loop
        self.start_control_loop()

    def start_control_loop(self) -> None:
        """
        Start the main control loop.
        """
        control_thread = threading.Thread(target=self._control_loop, name="Main Control")
        control_thread.daemon = True
        control_thread.start()
        logging.info("Started main control loop")

    def _control_loop(self) -> None:
        """
        Main control loop that handles NFC card reading and access control.
        """
        while self.running:
            try:
                # Check for NFC card
                card_data = self.nfc_manager.read_card()
                
                if card_data:
                    card_uid, card_name = card_data
                    self._handle_card_access(card_uid, card_name)
                
                # Check for hardware events
                self._process_hardware_events()
                
                # Auto-close logic is now handled by timers set on gate actions
                
                time.sleep(0.1)  # Small delay to prevent excessive CPU usage
                
            except Exception as e:
                logging.error(f"Error in main control loop: {e}")
                time.sleep(1)

    def _handle_card_access(self, card_uid: str, card_name: str) -> None:
        """
        Handle NFC card access attempt.
        
        Args:
            card_uid: The card UID
            card_name: Human-readable card name
        """
        try:
            # Check if card is authorized
            if self.nfc_manager.is_card_authorized(card_uid, self.security_config):
                # Grant access
                self._grant_access(card_uid, card_name)
            else:
                # Deny access
                self._deny_access(card_uid, card_name, "Unauthorized card")
                
        except Exception as e:
            logging.error(f"Error handling card access: {e}")
            self._deny_access(card_uid, card_name, f"System error: {e}")

    def _grant_access(self, card_uid: str, card_name: str) -> None:
        """
        Grant access and open the gate.
        
        Args:
            card_uid: The card UID
            card_name: Human-readable card name
        """
        try:
            logging.info(f"Access granted to {card_name} ({card_uid})")
            
            # Log the access
            self.access_log.log_access(card_uid, card_name, True, "Valid card")
            
            # Provide feedback
            threading.Thread(
                target=self.hardware_controller.access_granted_feedback
            ).start()
            
            # Open gate
            self.hardware_controller.open_gate()
            
            # Set auto-close timer
            self.last_access_time = datetime.now()
            self._set_auto_close_timer()
            
        except Exception as e:
            logging.error(f"Error granting access: {e}")

    def _deny_access(self, card_uid: str, card_name: str, reason: str) -> None:
        """
        Deny access and provide feedback.
        
        Args:
            card_uid: The card UID
            card_name: Human-readable card name
            reason: Reason for denial
        """
        try:
            logging.warning(f"Access denied to {card_name} ({card_uid}): {reason}")
            
            # Log the access attempt
            self.access_log.log_access(card_uid, card_name, False, reason)
            
            # Provide feedback
            threading.Thread(
                target=self.hardware_controller.access_denied_feedback
            ).start()
            
        except Exception as e:
            logging.error(f"Error denying access: {e}")

    def _process_hardware_events(self) -> None:
        """
        Process events from the hardware controller.
        """
        try:
            while not self.hardware_controller.event_queue.empty():
                event = self.hardware_controller.event_queue.get_nowait()
                
                if event == "UNAUTHORIZED_ACCESS_DETECTED":
                    self._handle_unauthorized_access()
                elif event == "GATE_CLOSED":
                    self._handle_gate_closed()
                elif event == "GATE_OPENED":
                    self._handle_gate_opened()
                    
        except queue.Empty:
            pass
        except Exception as e:
            logging.error(f"Error processing hardware events: {e}")

    def _handle_unauthorized_access(self) -> None:
        """
        Handle unauthorized access detection.
        """
        try:
            logging.warning("Unauthorized access detected!")
            
            # Log the event
            self.access_log.log_event("UNAUTHORIZED_ACCESS", "IR sensor triggered")
            
            # Trigger alarm
            threading.Thread(
                target=self.hardware_controller.alarm_feedback,
                args=(5.0,),  # 5 second alarm
            ).start()
            
            # Ensure the gate is locked
            if self.hardware_controller.lock_state != LockState.LOCKED:
                self.hardware_controller.lock_gate()
            
        except Exception as e:
            logging.error(f"Error handling unauthorized access: {e}")

    def _handle_gate_closed(self) -> None:
        """Handle gate closed event."""
        try:
            logging.info("Gate is now closed.")
            # Lock is handled within the close_gate function in the controller
            # Cancel any pending auto-close timer
            if self.auto_close_timer:
                self.auto_close_timer.cancel()
                self.auto_close_timer = None
                
        except Exception as e:
            logging.error(f"Error handling gate closed: {e}")

    def _handle_gate_opened(self) -> None:
        """Handle gate opened event."""
        try:
            logging.info("Gate is now open.")
            # Set the auto-close timer if not already running
            if not (self.auto_close_timer and self.auto_close_timer.is_alive()):
                self._set_auto_close_timer()
                
        except Exception as e:
            logging.error(f"Error handling gate opened: {e}")

    def _set_auto_close_timer(self) -> None:
        """
        Set or reset the auto-close timer.
        """
        try:
            if self.auto_close_timer:
                self.auto_close_timer.cancel()
            
            delay = self.security_config.auto_close_delay
            self.auto_close_timer = threading.Timer(
                delay,
                self._auto_close_gate
            )
            self.auto_close_timer.start()
            
            logging.info(f"Auto-close timer set for {delay} seconds")
            
        except Exception as e:
            logging.error(f"Error setting auto-close timer: {e}")

    def _auto_close_gate(self) -> None:
        """
        Automatically close the gate if it's currently open.
        """
        try:
            if self.hardware_controller.gate_state == GateState.OPEN:
                logging.info("Auto-closing gate due to timer expiry.")
                self.hardware_controller.close_gate()
            
            self.auto_close_timer = None
            
        except Exception as e:
            logging.error(f"Error auto-closing gate: {e}")

    def manual_open_gate(self) -> bool:
        """
        Manually open the gate.
        """
        logging.info("Manual gate open requested")
        self.access_log.log_event("MANUAL_OPEN", "Gate opened manually via GUI")
        result = self.hardware_controller.open_gate()
        if result:
            self._set_auto_close_timer()
        return result

    def manual_close_gate(self) -> bool:
        """
        Manually close the gate.
        """
        logging.info("Manual gate close requested")
        self.access_log.log_event("MANUAL_CLOSE", "Gate closed manually via GUI")
        if self.auto_close_timer:
            self.auto_close_timer.cancel()
            self.auto_close_timer = None
        return self.hardware_controller.close_gate()

    def emergency_stop(self) -> None:
        """
        Emergency stop - immediately stop all operations and lock the gate.
        """
        logging.warning("Emergency stop activated")
        self.access_log.log_event("EMERGENCY_STOP", "Emergency stop activated")
        
        if self.auto_close_timer:
            self.auto_close_timer.cancel()
            self.auto_close_timer = None

        # Stop servo movement immediately
        if self.hardware_controller.servo_pwm:
            self.hardware_controller.servo_pwm.ChangeDutyCycle(0)
            
        self.hardware_controller.gate_state = GateState.STOPPED
        
        # Ensure gate is locked
        self.hardware_controller.lock_gate()

    def get_system_status(self) -> Dict[str, Any]:
        """
        Get comprehensive system status.
        """
        try:
            hardware_status = self.hardware_controller.get_status()
            
            timer_active = self.auto_close_timer is not None and self.auto_close_timer.is_alive()

            return {
                'system_running': self.running,
                'security_level': self.security_level.value,
                'hardware': hardware_status,
                'nfc_reader_available': self.nfc_manager.pn532 is not None,
                'authorized_cards_count': len(self.nfc_manager.authorized_cards),
                'last_access_time': self.last_access_time.isoformat() if self.last_access_time else "N/A",
                'auto_close_timer_active': timer_active,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logging.error(f"Error getting system status: {e}")
            return {
                'system_running': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }

    def shutdown(self) -> None:
        """
        Shutdown the gate control system.
        """
        if not self.running:
            return
        logging.info("Shutting down gate control system")
        self.running = False
        
        if self.auto_close_timer:
            self.auto_close_timer.cancel()
        
        self.hardware_controller.cleanup()
        logging.info("Gate control system shutdown complete")

# GUI Application
class GateControlGUI:
    """
    Simple GUI for the gate control system.
    """
    
    def __init__(self, gate_system: GateControlSystem) -> None:
        self.gate_system = gate_system
        self.root = tk.Tk()
        self.root.title("Gate Control System")
        self.root.geometry("800x600")
        self.create_widgets()
        self.update_status()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_widgets(self) -> None:
        """Create GUI widgets."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        title_label = ttk.Label(main_frame, text="Gate Control System", font=("Arial", 16, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))

        status_frame = ttk.LabelFrame(main_frame, text="System Status", padding="10")
        status_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        main_frame.columnconfigure(0, weight=1)

        self.status_text = tk.Text(status_frame, height=10, width=80, wrap=tk.WORD)
        self.status_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        control_frame = ttk.LabelFrame(main_frame, text="Manual Controls", padding="10")
        control_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Button(control_frame, text="Open Gate", command=self.open_gate).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Close Gate", command=self.close_gate).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Emergency Stop", command=self.emergency_stop).pack(side=tk.LEFT, padx=5)
        
        log_frame = ttk.LabelFrame(main_frame, text="Recent Access Log", padding="10")
        log_frame.grid(row=3, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        main_frame.rowconfigure(3, weight=1)

        self.log_text = tk.Text(log_frame, height=10, width=80, wrap=tk.WORD)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def update_status(self) -> None:
        """Update the status display."""
        try:
            status = self.gate_system.get_system_status()
            self.status_text.delete(1.0, tk.END)
            
            status_lines = [
                f"System Running: {status.get('system_running', 'Unknown')}",
                f"Gate State: {status.get('hardware', {}).get('gate_state', 'Unknown')}",
                f"Lock State: {status.get('hardware', {}).get('lock_state', 'Unknown')}",
                f"NFC Reader: {'Available' if status.get('nfc_reader_available') else 'Not Available'}",
                f"Authorized Cards: {status.get('authorized_cards_count', 0)}",
                f"Auto-Close Timer: {'Active' if status.get('auto_close_timer_active') else 'Inactive'}",
                f"Last Update: {status.get('timestamp', 'Unknown')}"
            ]
            self.status_text.insert(tk.END, "\n".join(status_lines))
            self.update_access_log()
        except Exception as e:
            logging.error(f"Error updating GUI status: {e}")
        
        self.root.after(1000, self.update_status)

    def update_access_log(self) -> None:
        """Update the access log display."""
        try:
            recent_entries = self.gate_system.access_log.get_recent_entries(15)
            self.log_text.delete(1.0, tk.END)
            
            for entry in reversed(recent_entries):
                ts_str = entry.get('timestamp', 'Unknown').split('.')[0].replace('T', ' ')
                if 'card_uid' in entry:
                    status = "GRANTED" if entry.get('access_granted') else "DENIED"
                    line = f"[{ts_str}] {status}: {entry.get('card_name')} ({entry.get('reason')})\n"
                else:
                    line = f"[{ts_str}] EVENT: {entry.get('event_type')} - {entry.get('description')}\n"
                self.log_text.insert(tk.END, line)
        except Exception as e:
            logging.error(f"Error updating access log: {e}")

    def open_gate(self) -> None:
        threading.Thread(target=self._open_gate_thread, daemon=True).start()

    def _open_gate_thread(self):
        if self.gate_system.manual_open_gate():
            messagebox.showinfo("Success", "Gate opened successfully.")
        else:
            messagebox.showerror("Error", "Failed to open gate.")

    def close_gate(self) -> None:
        threading.Thread(target=self._close_gate_thread, daemon=True).start()
        
    def _close_gate_thread(self):
        if self.gate_system.manual_close_gate():
            messagebox.showinfo("Success", "Gate closed successfully.")
        else:
            messagebox.showerror("Error", "Failed to close gate.")

    def emergency_stop(self) -> None:
        if messagebox.askyesno("Emergency Stop", "Are you sure? This will stop all operations and lock the gate."):
            self.gate_system.emergency_stop()
            messagebox.showinfo("Emergency Stop", "Emergency Stop Activated.")

    def on_closing(self):
        """Handle window close event."""
        if messagebox.askokcancel("Quit", "Do you want to shut down the gate system?"):
            self.gate_system.shutdown()
            self.root.destroy()

    def run(self) -> None:
        """Run the GUI application."""
        self.root.mainloop()

# Global instance of the system to be accessed by signal handler
gate_system_instance = None

def signal_handler(signum, frame):
    """Handle system signals for graceful shutdown."""
    logging.info(f"Received signal {signum}, initiating shutdown...")
    if gate_system_instance:
        gate_system_instance.shutdown()
    sys.exit(0)

def main():
    """Main function to start the gate control system."""
    global gate_system_instance
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        logging.info("Starting Gate Control System")
        gate_system_instance = GateControlSystem()
        
        gui = GateControlGUI(gate_system_instance)
        gui.run()
        
    except Exception as e:
        logging.critical(f"A fatal error occurred in main: {e}", exc_info=True)
        if gate_system_instance:
            gate_system_instance.shutdown()
        sys.exit(1)
    finally:
        logging.info("Application has been shut down.")

if __name__ == "__main__":
    main()
