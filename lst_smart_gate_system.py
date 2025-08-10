"""
Gate Control System for Raspberry Pi (Direct GPIO Control)
=========================================================

System Architecture:
------------------
Raspberry Pi:
- Handles GUI interface
- Manages security and access control
- Reads NFC cards via PN532
- Controls all hardware directly via GPIO

Hardware Connections:
-------------------
Raspberry Pi GPIO Connections:
NFC Reader (PN532):
* SDA -> GPIO2 (Pin 3)    # Data connection
* SCL -> GPIO3 (Pin 5)    # Clock signal
* VCC -> 3.3V (Pin 1)     # Power supply
* GND -> GND (Pin 6)      # Ground connection

Hardware Components:
* Servo Motor -> GPIO18 (Pin 12)      # Controls gate movement (PWM)
* Gate Sensor -> GPIO24 (Pin 18)      # Detects gate position
* LED Green -> GPIO23 (Pin 16)        # Access granted indicator
* LED Red -> GPIO25 (Pin 22)          # Access denied indicator
* Buzzer -> GPIO22 (Pin 15)           # Audio feedback
* IR Sensor -> GPIO27 (Pin 13)        # Detects unauthorized access
* Solenoid Lock -> GPIO17 (Pin 11)    # Locks gate mechanism

Security Features:
----------------
1. NFC Card Authentication
2. IR Sensor Detection
   - Detects unauthorized access attempts
   - Triggers alarm if someone passes without card
3. Solenoid Lock
   - Automatically locks gate mechanism
   - Only unlocks with valid card access
4. Access Logging
   - Records all access attempts
   - Tracks unauthorized access events

Quick Start Guide:
----------------
1. Connect all hardware components as shown above
2. Run the program: python3 gate_control_modified.py
3. Use the control panel to manage access
4. Present NFC cards to grant access

Need Help?
---------
- Green light + short beep = Access granted
- Red light + long beep = Access denied
- Continuous alarm = Unauthorized access detected
- Emergency stop button is always available
- Check the status panel for current system state

Commit By: [Modified for RPi Direct Control]
Version: 7.0
"""

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

# Hardware pin definitions for Raspberry Pi
HARDWARE_PINS = {
    'SERVO_PIN': 18,      # GPIO18 (Pin 12) for servo motor control (PWM)
    'SOLENOID_PIN': 17,   # GPIO17 (Pin 11) for solenoid lock
    'GATE_SENSOR_PIN': 24, # GPIO24 (Pin 18) for gate sensor
    'GREEN_LED_PIN': 23,  # GPIO23 (Pin 16) for green LED
    'RED_LED_PIN': 25,    # GPIO25 (Pin 22) for red LED
    'IR_SENSOR_PIN': 27,  # GPIO27 (Pin 13) for IR sensor
    'BUZZER_PIN': 22,     # GPIO22 (Pin 15) for buzzer
}

class RPiHardwareController:
    """
    Manages direct hardware control on Raspberry Pi.
    Handles GPIO control for all hardware components.
    """
    
    def __init__(self, config: HardwareConfig) -> None:
        self.config = config
        self.running = True
        
        # State variables
        self.gate_state = GateState.CLOSED
        self.lock_state = LockState.LOCKED
        
        # Hardware objects
        self.servo_pwm = None
        
        # Event queues for communication with main application
        self.event_queue: queue.Queue[str] = queue.Queue()
        
        # Initialize GPIO
        self.initialize_gpio()
        
        # Start monitoring threads
        self.start_monitoring_threads()
        
        logging.info("RPi Hardware Controller initialized successfully")

    def initialize_gpio(self) -> None:
        """
        Initialize GPIO pins and hardware components.
        """
        try:
            # Set GPIO mode
            GPIO.setmode(GPIO.BCM)
            
            # Setup output pins
            GPIO.setup(HARDWARE_PINS['SERVO_PIN'], GPIO.OUT)
            GPIO.setup(HARDWARE_PINS['SOLENOID_PIN'], GPIO.OUT)
            GPIO.setup(HARDWARE_PINS['GREEN_LED_PIN'], GPIO.OUT)
            GPIO.setup(HARDWARE_PINS['RED_LED_PIN'], GPIO.OUT)
            GPIO.setup(HARDWARE_PINS['BUZZER_PIN'], GPIO.OUT)
            
            # Setup input pins with pull-up resistors
            GPIO.setup(HARDWARE_PINS['GATE_SENSOR_PIN'], GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.setup(HARDWARE_PINS['IR_SENSOR_PIN'], GPIO.IN, pull_up_down=GPIO.PUD_UP)
            
            # Initialize servo PWM
            self.servo_pwm = GPIO.PWM(HARDWARE_PINS['SERVO_PIN'], self.config.servo_frequency)
            self.servo_pwm.start(self.config.servo_close_duty)  # Start in closed position
            
            # Initialize outputs to safe states
            GPIO.output(HARDWARE_PINS['SOLENOID_PIN'], GPIO.LOW)  # Lock engaged
            GPIO.output(HARDWARE_PINS['GREEN_LED_PIN'], GPIO.LOW)
            GPIO.output(HARDWARE_PINS['RED_LED_PIN'], GPIO.LOW)
            GPIO.output(HARDWARE_PINS['BUZZER_PIN'], GPIO.LOW)
            
            # Setup event detection for sensors
            GPIO.add_event_detect(
                HARDWARE_PINS['GATE_SENSOR_PIN'], 
                GPIO.BOTH, 
                callback=self._gate_sensor_callback,
                bouncetime=self.config.sensor_debounce_time
            )
            
            GPIO.add_event_detect(
                HARDWARE_PINS['IR_SENSOR_PIN'], 
                GPIO.FALLING, 
                callback=self._ir_sensor_callback,
                bouncetime=self.config.sensor_debounce_time
            )
            
            logging.info("GPIO initialization completed successfully")
            
        except Exception as e:
            logging.error(f"Error initializing GPIO: {e}")
            raise

    def start_monitoring_threads(self):
        """
        Start hardware monitoring threads.
        """
        # Start hardware status monitoring thread
        monitor_thread = threading.Thread(target=self._monitor_hardware_status, name="Hardware Monitor")
        monitor_thread.daemon = True
        monitor_thread.start()
        logging.info("Started hardware monitoring thread")

    def _monitor_hardware_status(self):
        """
        Monitor hardware status and update state variables.
        """
        while self.running:
            try:
                # Read gate sensor
                gate_sensor_state = GPIO.input(HARDWARE_PINS['GATE_SENSOR_PIN'])
                
                # Update gate state based on sensor
                if gate_sensor_state == GPIO.LOW:  # Sensor triggered (gate closed)
                    if self.gate_state != GateState.CLOSED:
                        self.gate_state = GateState.CLOSED
                        self.event_queue.put("GATE_CLOSED")
                        logging.info("Gate closed detected")
                else:  # Sensor not triggered (gate open)
                    if self.gate_state == GateState.CLOSED:
                        self.gate_state = GateState.OPEN
                        self.event_queue.put("GATE_OPENED")
                        logging.info("Gate opened detected")
                
                time.sleep(0.1)  # Check every 100ms
                
            except Exception as e:
                logging.error(f"Error in hardware monitoring: {e}")
                time.sleep(1)

    def _gate_sensor_callback(self, channel):
        """
        Callback for gate sensor state changes.
        """
        try:
            state = GPIO.input(channel)
            if state == GPIO.LOW:
                self.event_queue.put("GATE_SENSOR_TRIGGERED")
            else:
                self.event_queue.put("GATE_SENSOR_RELEASED")
        except Exception as e:
            logging.error(f"Error in gate sensor callback: {e}")

    def _ir_sensor_callback(self, channel):
        """
        Callback for IR sensor detection.
        """
        try:
            self.event_queue.put("UNAUTHORIZED_ACCESS_DETECTED")
            logging.warning("Unauthorized access detected by IR sensor")
        except Exception as e:
            logging.error(f"Error in IR sensor callback: {e}")

    def open_gate(self) -> bool:
        """
        Open the gate by controlling the servo motor.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logging.info("Opening gate...")
            self.gate_state = GateState.OPENING
            
            # Move servo to open position
            self.servo_pwm.ChangeDutyCycle(self.config.servo_open_duty)
            time.sleep(1)  # Allow time for servo to move
            
            # Stop PWM signal to prevent servo jitter
            self.servo_pwm.ChangeDutyCycle(0)
            
            self.gate_state = GateState.OPEN
            self.event_queue.put("GATE_OPENED")
            logging.info("Gate opened successfully")
            return True
            
        except Exception as e:
            logging.error(f"Error opening gate: {e}")
            self.gate_state = GateState.ERROR
            return False

    def close_gate(self) -> bool:
        """
        Close the gate by controlling the servo motor.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logging.info("Closing gate...")
            self.gate_state = GateState.CLOSING
            
            # Move servo to closed position
            self.servo_pwm.ChangeDutyCycle(self.config.servo_close_duty)
            time.sleep(1)  # Allow time for servo to move
            
            # Stop PWM signal to prevent servo jitter
            self.servo_pwm.ChangeDutyCycle(0)
            
            self.gate_state = GateState.CLOSED
            self.event_queue.put("GATE_CLOSED")
            logging.info("Gate closed successfully")
            return True
            
        except Exception as e:
            logging.error(f"Error closing gate: {e}")
            self.gate_state = GateState.ERROR
            return False

    def lock_gate(self) -> bool:
        """
        Engage the solenoid lock.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            GPIO.output(HARDWARE_PINS['SOLENOID_PIN'], GPIO.HIGH)
            self.lock_state = LockState.LOCKED
            logging.info("Gate locked")
            return True
        except Exception as e:
            logging.error(f"Error locking gate: {e}")
            return False

    def unlock_gate(self) -> bool:
        """
        Disengage the solenoid lock.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            GPIO.output(HARDWARE_PINS['SOLENOID_PIN'], GPIO.LOW)
            self.lock_state = LockState.UNLOCKED
            logging.info("Gate unlocked")
            return True
        except Exception as e:
            logging.error(f"Error unlocking gate: {e}")
            return False

    def set_green_led(self, state: bool) -> None:
        """
        Control the green LED.
        
        Args:
            state: True to turn on, False to turn off
        """
        try:
            GPIO.output(HARDWARE_PINS['GREEN_LED_PIN'], GPIO.HIGH if state else GPIO.LOW)
        except Exception as e:
            logging.error(f"Error controlling green LED: {e}")

    def set_red_led(self, state: bool) -> None:
        """
        Control the red LED.
        
        Args:
            state: True to turn on, False to turn off
        """
        try:
            GPIO.output(HARDWARE_PINS['RED_LED_PIN'], GPIO.HIGH if state else GPIO.LOW)
        except Exception as e:
            logging.error(f"Error controlling red LED: {e}")

    def beep(self, duration: float = 0.5, frequency: Optional[int] = None) -> None:
        """
        Generate a beep sound using the buzzer.
        
        Args:
            duration: Duration of the beep in seconds
            frequency: Frequency of the beep (not used with simple buzzer)
        """
        try:
            GPIO.output(HARDWARE_PINS['BUZZER_PIN'], GPIO.HIGH)
            time.sleep(duration)
            GPIO.output(HARDWARE_PINS['BUZZER_PIN'], GPIO.LOW)
        except Exception as e:
            logging.error(f"Error controlling buzzer: {e}")

    def access_granted_feedback(self) -> None:
        """
        Provide visual and audio feedback for access granted.
        """
        try:
            # Turn on green LED
            self.set_green_led(True)
            
            # Short beep
            self.beep(0.2)
            
            # Keep LED on for a moment
            time.sleep(1)
            
            # Turn off LED
            self.set_green_led(False)
            
        except Exception as e:
            logging.error(f"Error in access granted feedback: {e}")

    def access_denied_feedback(self) -> None:
        """
        Provide visual and audio feedback for access denied.
        """
        try:
            # Turn on red LED
            self.set_red_led(True)
            
            # Long beep
            self.beep(1.0)
            
            # Keep LED on for a moment
            time.sleep(1)
            
            # Turn off LED
            self.set_red_led(False)
            
        except Exception as e:
            logging.error(f"Error in access denied feedback: {e}")

    def alarm_feedback(self, duration: float = 5.0) -> None:
        """
        Provide alarm feedback for unauthorized access.
        
        Args:
            duration: Duration of the alarm in seconds
        """
        try:
            end_time = time.time() + duration
            
            while time.time() < end_time and self.running:
                # Flash red LED and beep
                self.set_red_led(True)
                self.beep(0.1)
                time.sleep(0.1)
                
                self.set_red_led(False)
                time.sleep(0.1)
            
            # Ensure LED is off
            self.set_red_led(False)
            
        except Exception as e:
            logging.error(f"Error in alarm feedback: {e}")

    def get_status(self) -> Dict[str, Any]:
        """
        Get current hardware status.
        
        Returns:
            Dict containing current hardware status
        """
        try:
            return {
                'gate_state': self.gate_state.value,
                'lock_state': self.lock_state.value,
                'gate_sensor': GPIO.input(HARDWARE_PINS['GATE_SENSOR_PIN']),
                'ir_sensor': GPIO.input(HARDWARE_PINS['IR_SENSOR_PIN']),
                'connected': True,
                'last_update': datetime.now().isoformat()
            }
        except Exception as e:
            logging.error(f"Error getting hardware status: {e}")
            return {
                'gate_state': 'ERROR',
                'lock_state': 'UNKNOWN',
                'connected': False,
                'error': str(e)
            }

    def cleanup(self) -> None:
        """
        Clean up GPIO resources.
        """
        try:
            self.running = False
            
            if self.servo_pwm:
                self.servo_pwm.stop()
            
            # Turn off all outputs
            GPIO.output(HARDWARE_PINS['SOLENOID_PIN'], GPIO.LOW)
            GPIO.output(HARDWARE_PINS['GREEN_LED_PIN'], GPIO.LOW)
            GPIO.output(HARDWARE_PINS['RED_LED_PIN'], GPIO.LOW)
            GPIO.output(HARDWARE_PINS['BUZZER_PIN'], GPIO.LOW)
            
            # Remove event detection
            GPIO.remove_event_detect(HARDWARE_PINS['GATE_SENSOR_PIN'])
            GPIO.remove_event_detect(HARDWARE_PINS['IR_SENSOR_PIN'])
            
            # Cleanup GPIO
            GPIO.cleanup()
            
            logging.info("GPIO cleanup completed")
            
        except Exception as e:
            logging.error(f"Error during GPIO cleanup: {e}")

# NFC Card Manager (unchanged from original)
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

# Access Log Manager (unchanged from original)
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
                
                # Handle auto-close timer
                self._handle_auto_close()
                
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
            feedback_thread = threading.Thread(
                target=self.hardware_controller.access_granted_feedback,
                name="Access Granted Feedback"
            )
            feedback_thread.daemon = True
            feedback_thread.start()
            
            # Unlock and open gate
            self.hardware_controller.unlock_gate()
            time.sleep(0.5)  # Brief delay
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
            feedback_thread = threading.Thread(
                target=self.hardware_controller.access_denied_feedback,
                name="Access Denied Feedback"
            )
            feedback_thread.daemon = True
            feedback_thread.start()
            
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
            alarm_thread = threading.Thread(
                target=self.hardware_controller.alarm_feedback,
                args=(5.0,),  # 5 second alarm
                name="Unauthorized Access Alarm"
            )
            alarm_thread.daemon = True
            alarm_thread.start()
            
            # Lock the gate if it's not already locked
            if self.hardware_controller.lock_state != LockState.LOCKED:
                self.hardware_controller.lock_gate()
            
        except Exception as e:
            logging.error(f"Error handling unauthorized access: {e}")

    def _handle_gate_closed(self) -> None:
        """
        Handle gate closed event.
        """
        try:
            logging.info("Gate closed")
            
            # Lock the gate
            time.sleep(0.5)  # Brief delay
            self.hardware_controller.lock_gate()
            
            # Cancel auto-close timer
            if self.auto_close_timer:
                self.auto_close_timer.cancel()
                self.auto_close_timer = None
                
        except Exception as e:
            logging.error(f"Error handling gate closed: {e}")

    def _handle_gate_opened(self) -> None:
        """
        Handle gate opened event.
        """
        try:
            logging.info("Gate opened")
            
            # Set auto-close timer if not already set
            if not self.auto_close_timer:
                self._set_auto_close_timer()
                
        except Exception as e:
            logging.error(f"Error handling gate opened: {e}")

    def _set_auto_close_timer(self) -> None:
        """
        Set the auto-close timer.
        """
        try:
            if self.auto_close_timer:
                self.auto_close_timer.cancel()
            
            self.auto_close_timer = threading.Timer(
                self.security_config.auto_close_delay,
                self._auto_close_gate
            )
            self.auto_close_timer.start()
            
            logging.info(f"Auto-close timer set for {self.security_config.auto_close_delay} seconds")
            
        except Exception as e:
            logging.error(f"Error setting auto-close timer: {e}")

    def _auto_close_gate(self) -> None:
        """
        Automatically close the gate.
        """
        try:
            logging.info("Auto-closing gate")
            
            # Close the gate
            self.hardware_controller.close_gate()
            
            # Clear the timer
            self.auto_close_timer = None
            
        except Exception as e:
            logging.error(f"Error auto-closing gate: {e}")

    def _handle_auto_close(self) -> None:
        """
        Handle auto-close logic.
        """
        # This is now handled by the timer, but we keep this method for future enhancements
        pass

    def manual_open_gate(self) -> bool:
        """
        Manually open the gate (for emergency or testing).
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logging.info("Manual gate open requested")
            
            # Log the event
            self.access_log.log_event("MANUAL_OPEN", "Gate opened manually")
            
            # Unlock and open gate
            self.hardware_controller.unlock_gate()
            time.sleep(0.5)
            result = self.hardware_controller.open_gate()
            
            if result:
                self._set_auto_close_timer()
            
            return result
            
        except Exception as e:
            logging.error(f"Error in manual gate open: {e}")
            return False

    def manual_close_gate(self) -> bool:
        """
        Manually close the gate.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logging.info("Manual gate close requested")
            
            # Log the event
            self.access_log.log_event("MANUAL_CLOSE", "Gate closed manually")
            
            # Cancel auto-close timer
            if self.auto_close_timer:
                self.auto_close_timer.cancel()
                self.auto_close_timer = None
            
            # Close gate
            return self.hardware_controller.close_gate()
            
        except Exception as e:
            logging.error(f"Error in manual gate close: {e}")
            return False

    def emergency_stop(self) -> None:
        """
        Emergency stop - immediately stop all operations.
        """
        try:
            logging.warning("Emergency stop activated")
            
            # Log the event
            self.access_log.log_event("EMERGENCY_STOP", "Emergency stop activated")
            
            # Cancel auto-close timer
            if self.auto_close_timer:
                self.auto_close_timer.cancel()
                self.auto_close_timer = None
            
            # Lock the gate
            self.hardware_controller.lock_gate()
            
            # Turn off all LEDs
            self.hardware_controller.set_green_led(False)
            self.hardware_controller.set_red_led(False)
            
        except Exception as e:
            logging.error(f"Error in emergency stop: {e}")

    def get_system_status(self) -> Dict[str, Any]:
        """
        Get comprehensive system status.
        
        Returns:
            Dict containing system status information
        """
        try:
            hardware_status = self.hardware_controller.get_status()
            
            return {
                'system_running': self.running,
                'security_level': self.security_level.value,
                'hardware': hardware_status,
                'nfc_reader_available': self.nfc_manager.pn532 is not None,
                'authorized_cards_count': len(self.nfc_manager.authorized_cards),
                'last_access_time': self.last_access_time.isoformat() if self.last_access_time else None,
                'auto_close_timer_active': self.auto_close_timer is not None,
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
        try:
            logging.info("Shutting down gate control system")
            
            self.running = False
            
            # Cancel auto-close timer
            if self.auto_close_timer:
                self.auto_close_timer.cancel()
            
            # Emergency stop
            self.emergency_stop()
            
            # Cleanup hardware
            self.hardware_controller.cleanup()
            
            logging.info("Gate control system shutdown complete")
            
        except Exception as e:
            logging.error(f"Error during shutdown: {e}")

# GUI Application (simplified version)
class GateControlGUI:
    """
    Simple GUI for the gate control system.
    """
    
    def __init__(self, gate_system: GateControlSystem) -> None:
        self.gate_system = gate_system
        
        # Create main window
        self.root = tk.Tk()
        self.root.title("Gate Control System")
        self.root.geometry("800x600")
        
        # Create GUI elements
        self.create_widgets()
        
        # Start status update timer
        self.update_status()

    def create_widgets(self) -> None:
        """
        Create GUI widgets.
        """
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Title
        title_label = ttk.Label(main_frame, text="Gate Control System", font=("Arial", 16, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))
        
        # Status frame
        status_frame = ttk.LabelFrame(main_frame, text="System Status", padding="10")
        status_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.status_text = tk.Text(status_frame, height=10, width=70)
        self.status_text.grid(row=0, column=0, sticky=(tk.W, tk.E))
        
        status_scrollbar = ttk.Scrollbar(status_frame, orient="vertical", command=self.status_text.yview)
        status_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.status_text.configure(yscrollcommand=status_scrollbar.set)
        
        # Control frame
        control_frame = ttk.LabelFrame(main_frame, text="Manual Controls", padding="10")
        control_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # Control buttons
        ttk.Button(control_frame, text="Open Gate", command=self.open_gate).grid(row=0, column=0, padx=(0, 10))
        ttk.Button(control_frame, text="Close Gate", command=self.close_gate).grid(row=0, column=1, padx=(0, 10))
        ttk.Button(control_frame, text="Emergency Stop", command=self.emergency_stop).grid(row=0, column=2, padx=(0, 10))
        
        # Access log frame
        log_frame = ttk.LabelFrame(main_frame, text="Recent Access Log", padding="10")
        log_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.log_text = tk.Text(log_frame, height=8, width=70)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E))
        
        log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.log_text.configure(yscrollcommand=log_scrollbar.set)

    def update_status(self) -> None:
        """
        Update the status display.
        """
        try:
            # Get system status
            status = self.gate_system.get_system_status()
            
            # Update status text
            self.status_text.delete(1.0, tk.END)
            
            status_lines = [
                f"System Running: {status.get('system_running', 'Unknown')}",
                f"Security Level: {status.get('security_level', 'Unknown')}",
                f"Gate State: {status.get('hardware', {}).get('gate_state', 'Unknown')}",
                f"Lock State: {status.get('hardware', {}).get('lock_state', 'Unknown')}",
                f"NFC Reader: {'Available' if status.get('nfc_reader_available') else 'Not Available'}",
                f"Authorized Cards: {status.get('authorized_cards_count', 0)}",
                f"Auto-Close Timer: {'Active' if status.get('auto_close_timer_active') else 'Inactive'}",
                f"Last Update: {status.get('timestamp', 'Unknown')}"
            ]
            
            self.status_text.insert(tk.END, "\n".join(status_lines))
            
            # Update access log
            self.update_access_log()
            
        except Exception as e:
            logging.error(f"Error updating GUI status: {e}")
        
        # Schedule next update
        self.root.after(1000, self.update_status)

    def update_access_log(self) -> None:
        """
        Update the access log display.
        """
        try:
            # Get recent log entries
            recent_entries = self.gate_system.access_log.get_recent_entries(10)
            
            # Update log text
            self.log_text.delete(1.0, tk.END)
            
            for entry in reversed(recent_entries):  # Show most recent first
                timestamp = entry.get('timestamp', 'Unknown')
                if 'card_uid' in entry:
                    # Access log entry
                    card_name = entry.get('card_name', 'Unknown')
                    granted = entry.get('access_granted', False)
                    status = "GRANTED" if granted else "DENIED"
                    reason = entry.get('reason', '')
                    line = f"{timestamp}: {status} - {card_name} ({reason})\n"
                else:
                    # Event log entry
                    event_type = entry.get('event_type', 'Unknown')
                    description = entry.get('description', '')
                    line = f"{timestamp}: {event_type} - {description}\n"
                
                self.log_text.insert(tk.END, line)
                
        except Exception as e:
            logging.error(f"Error updating access log: {e}")

    def open_gate(self) -> None:
        """
        Handle open gate button click.
        """
        try:
            result = self.gate_system.manual_open_gate()
            if result:
                messagebox.showinfo("Success", "Gate opened successfully")
            else:
                messagebox.showerror("Error", "Failed to open gate")
        except Exception as e:
            messagebox.showerror("Error", f"Error opening gate: {e}")

    def close_gate(self) -> None:
        """
        Handle close gate button click.
        """
        try:
            result = self.gate_system.manual_close_gate()
            if result:
                messagebox.showinfo("Success", "Gate closed successfully")
            else:
                messagebox.showerror("Error", "Failed to close gate")
        except Exception as e:
            messagebox.showerror("Error", f"Error closing gate: {e}")

    def emergency_stop(self) -> None:
        """
        Handle emergency stop button click.
        """
        try:
            result = messagebox.askyesno("Emergency Stop", "Are you sure you want to activate emergency stop?")
            if result:
                self.gate_system.emergency_stop()
                messagebox.showinfo("Emergency Stop", "Emergency stop activated")
        except Exception as e:
            messagebox.showerror("Error", f"Error in emergency stop: {e}")

    def run(self) -> None:
        """
        Run the GUI application.
        """
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            logging.info("GUI interrupted by user")
        finally:
            self.gate_system.shutdown()

def signal_handler(signum, frame):
    """
    Handle system signals for graceful shutdown.
    """
    logging.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)

def main():
    """
    Main function to start the gate control system.
    """
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        logging.info("Starting Gate Control System")
        
        # Create and start the gate control system
        gate_system = GateControlSystem()
        
        # Create and run the GUI
        gui = GateControlGUI(gate_system)
        gui.run()
        
    except Exception as e:
        logging.error(f"Fatal error in main: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

