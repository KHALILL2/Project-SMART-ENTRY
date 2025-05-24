#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Smart Gate Access Control System with NFC and Enhanced Servo Control using pigpio

This script integrates NFC card reading, database management, GUI, logging,
and hardware control (LEDs, Buzzer, Solenoid, Servo) for a smart gate system
on a Raspberry Pi.

**Servo Enhancement:**
This version replaces the standard RPi.GPIO PWM for servo control with the
`pigpio` library for more precise and stable hardware-timed PWM signals.
This can lead to smoother, potentially faster, and more powerful servo movement,
especially when combined with an adequate external power supply for the servo.

**Prerequisites:**
1.  **pigpio Library:** Install with `sudo apt update && sudo apt install pigpio python3-pigpio`
2.  **pigpio Daemon:** Must be running. Start with `sudo systemctl start pigpiod` and enable with `sudo systemctl enable pigpiod`.
3.  **External Servo Power:** The servo **MUST** be powered by a separate, adequate power supply (e.g., 5V/6V, 1-2A+). Connect the external supply's GND to the Raspberry Pi's GND.
4.  **Dependencies:** nfcpy, ndeflib, cryptography, keyring (install via pip if needed).
5.  **Configuration:** Adjust `config.ini` (created on first run) especially the `[servo_pigpio]` section with correct pulse widths for your servo.
"""

from typing import Optional, Dict, Any, List, Tuple, Union
from dataclasses import dataclass
from enum import Enum, auto
import sqlite3
import smtplib
import logging
import threading
import time
import sys
import os
import configparser
import keyring
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler

# --- Dependency Imports with Fallbacks ---

# Add nfcpy and ndeflib module paths and disable USB driver
os.environ['NFCPY_USB_DRIVER'] = ''  # Disable USB drivers to bypass usb1 import
sys.path.insert(0, '/home/pi/Desktop/nfcpy/src') # Adjust path if needed
sys.path.insert(0, '/home/pi/Desktop/ndeflib/src') # Adjust path if needed
try:
    import nfc
    from nfc.clf import RemoteTarget
    import ndef
    print("nfcpy and ndeflib modules loaded successfully!")
    NFC_AVAILABLE = True
except ImportError as e:
    print(f"WARNING: nfc library not found or not in path. Using mock NFC. Error: {e}")
    NFC_AVAILABLE = False

# pigpio for Servo Control (Mandatory for this version)
try:
    import pigpio
    PIGPIO_AVAILABLE = True
    print("pigpio library loaded successfully!")
except ImportError:
    print("CRITICAL ERROR: pigpio library not found. This version requires pigpio.")
    print("Install using: sudo apt update && sudo apt install pigpio python3-pigpio")
    PIGPIO_AVAILABLE = False
    # Exit if pigpio is essential and not found
    # sys.exit(1)
    # Or, implement a fallback/mock if you want the rest of the app to run without servo

# RPi.GPIO for other components (LEDs, Buzzer, Solenoid)
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
    print("RPi.GPIO library loaded successfully!")
except (ImportError, RuntimeError):
    print("WARNING: RPi.GPIO not found or failed to import. Using mock GPIO for non-servo components.")
    class MockGPIO:
        BCM = 11
        OUT = 1
        LOW = 0
        HIGH = 1
        def setmode(self, mode):
            print(f"MockGPIO: Set mode to {mode}")
        def setup(self, pin, mode):
            print(f"MockGPIO: Setup pin {pin} to mode {mode}")
        def output(self, pin, state):
            print(f"MockGPIO: Set pin {pin} to state {state}")
        def cleanup(self, pin=None):
            print(f"MockGPIO: Cleanup pin {pin if pin else 'all'}")
        # Add dummy PWM methods if needed by other parts of the original code
        def PWM(self, pin, freq):
            print(f"MockGPIO: PWM setup on pin {pin} at {freq}Hz")
            class MockPWM:
                def start(self, duty):
                    print(f"MockPWM: Start on pin {pin} with duty cycle {duty}")
                def ChangeDutyCycle(self, duty):
                    print(f"MockPWM: Change duty cycle on pin {pin} to {duty}")
                def stop(self):
                    print(f"MockPWM: Stop on pin {pin}")
            return MockPWM()

    GPIO = MockGPIO()
    GPIO_AVAILABLE = False

# Standard Library Imports
from tkinter import Tk, Label, Button, messagebox, Entry, Toplevel, Text, END, Scrollbar, Frame, Canvas
from tkinter import ttk
import tkinter as tk
from cryptography.fernet import Fernet, InvalidToken
import ssl
import hashlib
from datetime import datetime, timedelta
import traceback
import json
from pathlib import Path
import queue

# --- Enums and Dataclasses ---

class AccessStatus(Enum):
    GRANTED = auto()
    DENIED = auto()
    BLACKLISTED = auto()
    RATE_LIMITED = auto()

@dataclass
class CardInfo:
    id: str
    name: Optional[str] = None
    expiry_date: Optional[datetime] = None
    is_valid: bool = False
    last_access: Optional[datetime] = None
    # Add other fields from database if needed
    faculty: Optional[str] = None
    program: Optional[str] = None
    level: Optional[str] = None
    student_id: Optional[str] = None
    email: Optional[str] = None
    photo_path: Optional[str] = None

@dataclass
class SystemMetrics:
    total_requests: int = 0
    successful_accesses: int = 0
    failed_accesses: int = 0
    average_response_time: float = 0.0
    system_uptime: float = 0.0
    last_health_check: Optional[datetime] = None
    cpu_temp: float = 0.0

# --- Logging Setup ---

class ProfessionalLogger:
    # ... (Keep the existing ProfessionalLogger class as is) ...
    # (Make sure it logs errors from pigpio connection attempts if needed)
    def __init__(self, log_dir: str = "logs") -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.logger = logging.getLogger('nfc_system')
        self.logger.setLevel(logging.INFO)
        if self.logger.hasHandlers():
            self.logger.handlers.clear()
        file_handler = RotatingFileHandler(
            self.log_dir / 'system.log',
            maxBytes=10*1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s'
        ))
        audit_logger = logging.getLogger('nfc_audit')
        audit_logger.setLevel(logging.INFO)
        if audit_logger.hasHandlers():
            audit_logger.handlers.clear()
        audit_handler = RotatingFileHandler(
            self.log_dir / 'audit.log',
            maxBytes=5*1024*1024,
            backupCount=3,
            encoding='utf-8'
        )
        audit_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(message)s'
        ))
        audit_logger.addHandler(audit_handler)
        self.audit_logger = audit_logger
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        ))
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.metrics = SystemMetrics()
        self.start_time = datetime.now()
        self.log_queue = queue.Queue(maxsize=100)  # Limit queue size

    def log_access(self, card_info: CardInfo, status: AccessStatus, response_time: float) -> None:
        log_data = {
            'timestamp': datetime.now().isoformat(),
            'card_id': card_info.id,
            'card_name': card_info.name,
            'status': status.name,
            'response_time': response_time,
            'system_metrics': self._get_current_metrics()
        }
        msg = json.dumps(log_data)
        self.logger.info(msg)
        self._queue_log(f"INFO: Access attempt - Card: {card_info.id}, Status: {status.name}")
        self._update_metrics(status, response_time)

    def log_error(self, error: Exception, context: str = "", severity: str = "ERROR") -> None:
        tb_string = traceback.format_exc()
        error_info = {
            'timestamp': datetime.now().isoformat(),
            'error': str(error),
            'context': context,
            'severity': severity,
            'traceback': tb_string,
            'system_metrics': self._get_current_metrics()
        }
        msg = json.dumps(error_info)
        self.logger.error(msg)
        self._queue_log(f"{severity}: {context} - {error}")

    def log_audit(self, action: str, details: Dict[str, Any]) -> None:
        audit_data = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'details': details,
        }
        msg = json.dumps(audit_data)
        self.audit_logger.info(msg)
        self._queue_log(f"AUDIT: {action} - {details.get('card_id', '')}")

    def log_info(self, message: str) -> None:
        self.logger.info(message)
        self._queue_log(f"INFO: {message}")

    def _queue_log(self, message: str) -> None:
        """Add log to queue without blocking if full"""
        try:
            if not self.log_queue.full():
                self.log_queue.put_nowait(message)
        except:
            pass  # Silently ignore if queue is full

    def get_recent_logs(self, max_logs=50) -> List[str]:
        logs = []
        count = 0
        while not self.log_queue.empty() and count < max_logs:
            try:
                logs.append(self.log_queue.get_nowait())
                count += 1
            except queue.Empty:
                break
        return logs

    def _update_metrics(self, status: AccessStatus, response_time: float) -> None:
        self.metrics.total_requests += 1
        if status == AccessStatus.GRANTED:
            self.metrics.successful_accesses += 1
        else:
            self.metrics.failed_accesses += 1
        total_req = self.metrics.total_requests
        if total_req > 0:
            self.metrics.average_response_time = (
                (self.metrics.average_response_time * (total_req - 1) + response_time) / total_req
            )
        self.metrics.system_uptime = (datetime.now() - self.start_time).total_seconds()
        self.metrics.last_health_check = datetime.now()
        # Add temp reading here if desired
        # self.metrics.cpu_temp = self._read_cpu_temp()

    def _get_current_metrics(self) -> Dict[str, Any]:
        # Update temp before getting metrics
        # self.metrics.cpu_temp = self._read_cpu_temp()
        return {
            'total_requests': self.metrics.total_requests,
            'successful_accesses': self.metrics.successful_accesses,
            'failed_accesses': self.metrics.failed_accesses,
            'average_response_time': round(self.metrics.average_response_time, 4),
            'system_uptime': round(self.metrics.system_uptime, 2),
            'last_health_check': self.metrics.last_health_check.isoformat() if self.metrics.last_health_check else None,
            'cpu_temp': round(self.metrics.cpu_temp, 1)
        }

    # Optional: Add CPU temp reading method if needed
    # def _read_cpu_temp(self) -> float:
    #     try:
    #         with open(config.THERMAL_FILE, 'r') as f:
    #             temp_str = f.read()
    #         return int(temp_str) / 1000.0
    #     except Exception as e:
    #         # Log error but don't crash
    #         self.log_error(e, "Failed to read CPU temperature", severity="WARNING")
    #         return 0.0

logger = ProfessionalLogger()

# --- Configuration Management ---

class Config:
    DEFAULT_VALID_PINS = [2,3,4,17,18,22,23,24,25,26,27] # Example valid GPIO pins
    DEFAULT_THERMAL_FILE = "/sys/class/thermal/thermal_zone0/temp"
    CONFIG_FILE = 'config.ini'

    def __init__(self):
        self.config = configparser.ConfigParser()
        try:
            os.umask(0o077) # Set restrictive permissions for config file
        except Exception as e:
            logger.log_error(e, "Failed to set umask")

        if not os.path.exists(self.CONFIG_FILE):
            self._create_default_config()
            logger.log_info(f"Created default config file: {self.CONFIG_FILE}")

        self.config.read(self.CONFIG_FILE)

        # Load settings with fallbacks
        self._load_settings()

    def _load_settings(self):
        # Email
        try:
            self.EMAIL_USER = keyring.get_password("nfc_gate", "email_user")
            self.EMAIL_PASS = keyring.get_password("nfc_gate", "email_pass")
        except Exception as e:
            logger.log_error(e, "Failed to retrieve email credentials from keyring")
            self.EMAIL_USER = None
            self.EMAIL_PASS = None
        self.EMAIL_HOST = self.config.get('email', 'host', fallback='smtp.gmail.com')
        self.EMAIL_PORT = self.config.getint('email', 'port', fallback=587)
        self.EMAIL_USE_TLS = self.config.getboolean('email', 'use_tls', fallback=True)
        self.EMAIL_RECIPIENT = self.config.get('email', 'recipient', fallback=None) # Add recipient

        # GPIO Pins (Non-Servo)
        self.VALID_PINS = self._parse_list(self.config.get('gpio', 'valid_pins', fallback=str(self.DEFAULT_VALID_PINS)), int)
        self.FAN_PIN = self._validate_pin(self.config.getint('gpio', 'fan', fallback=23))
        self.BUZZER_PIN = self._validate_pin(self.config.getint('gpio', 'buzzer', fallback=24))
        self.SOLENOID_PIN = self._validate_pin(self.config.getint('gpio', 'solenoid', fallback=27))
        self.LED_GREEN_PIN = self._validate_pin(self.config.getint('gpio', 'led_green', fallback=22))
        self.LED_RED_PIN = self._validate_pin(self.config.getint('gpio', 'led_red', fallback=25)) # Example: Use 25 for red

        # Servo Pin (Used by pigpio)
        self.SERVO_PIN = self._validate_pin(self.config.getint('servo_pigpio', 'servo_pin', fallback=18))

        # Servo Settings (pigpio - Pulse Widths in microseconds)
        self.SERVO_OPEN_PULSE_WIDTH = self.config.getint('servo_pigpio', 'open_pulse_width', fallback=2000)
        self.SERVO_CLOSE_PULSE_WIDTH = self.config.getint('servo_pigpio', 'close_pulse_width', fallback=1000)
        self.SERVO_DELAY = max(0.1, self.config.getfloat('servo_pigpio', 'move_delay', fallback=1.0))
        # Validate pulse widths (adjust range 500-2500 if needed)
        self.SERVO_OPEN_PULSE_WIDTH = max(500, min(2500, self.SERVO_OPEN_PULSE_WIDTH))
        self.SERVO_CLOSE_PULSE_WIDTH = max(500, min(2500, self.SERVO_CLOSE_PULSE_WIDTH))

        # Temperature Fan Control
        self.FAN_ON_TEMP = min(max(30, self.config.getfloat('temperature', 'on_temp', fallback=60)), 90)
        self.FAN_OFF_TEMP = min(max(25, self.config.getfloat('temperature', 'off_temp', fallback=50)), 85)
        self.THERMAL_FILE = self.config.get('temperature', 'thermal_file', fallback=self.DEFAULT_THERMAL_FILE)

        # NFC Reader Settings
        self.NFC_MAX_ATTEMPTS = self.config.getint('nfc', 'max_attempts', fallback=10)
        self.NFC_TIMEOUT = self.config.getint('nfc', 'timeout', fallback=30)
        self.NFC_PROTOCOL = self.config.get('nfc', 'protocol', fallback='106A') # e.g., '106A', '212F', '424F'

        # Database Settings
        self.DB_PATH = self.config.get('database', 'path', fallback='cards.db')
        self.DB_ENCRYPTED = self.config.getboolean('database', 'encrypted', fallback=True)

        # Performance/UI Settings
        self.GUI_UPDATE_INTERVAL = self.config.getint('performance', 'gui_update_ms', fallback=100)

    def _create_default_config(self):
        default_config = configparser.ConfigParser()
        default_config['email'] = {
            'host': 'smtp.gmail.com',
            'port': '587',
            'use_tls': 'True',
            '#recipient': 'your_email@example.com # Uncomment and set recipient for alerts'
        }
        default_config['gpio'] = {
            'valid_pins': str(self.DEFAULT_VALID_PINS),
            'fan': '23',
            'buzzer': '24',
            'solenoid': '27',
            'led_green': '22',
            'led_red': '25'
        }
        # --- NEW Servo Section for pigpio --- 
        default_config['servo_pigpio'] = {
            '# IMPORTANT': 'Configure these values for YOUR servo!',
            'servo_pin': '18', 
            '# Pulse widths are in microseconds (us). Typical range: 500-2500.',
            'open_pulse_width': '2000',  # Example: Adjust for your 'gate open' position
            'close_pulse_width': '1000', # Example: Adjust for your 'gate close' position
            '# move_delay is the time (seconds) allowed for the servo to move.',
            'move_delay': '1.0'
        }
        # --- Remove or comment out old [servo] section if it exists --- 
        # default_config['servo'] = {
        #     'open': '7.5', # Old duty cycle - Not used with pigpio
        #     'close': '2.5', # Old duty cycle - Not used with pigpio
        #     'delay': '1.5' # Old delay name
        # }
        default_config['temperature'] = {
            'on_temp': '60',
            'off_temp': '50',
            'thermal_file': self.DEFAULT_THERMAL_FILE
        }
        default_config['nfc'] = {
            'max_attempts': '10',
            'timeout': '30',
            'protocol': '106A'
        }
        default_config['database'] = {
            'path': 'cards.db',
            'encrypted': 'True'
        }
        default_config['performance'] = {
            'gui_update_ms': '100'
        }
        try:
            with open(self.CONFIG_FILE, 'w') as configfile:
                default_config.write(configfile)
            os.chmod(self.CONFIG_FILE, 0o600) # Set read/write only for owner
            logger.log_info(f"Default config file '{self.CONFIG_FILE}' created with permissions 600.")
        except Exception as e:
            logger.log_error(e, f"Failed to write or set permissions for config file {self.CONFIG_FILE}")


    def _parse_list(self, list_str: str, item_type: type) -> list:
        try:
            list_str = list_str.strip('[] ') # Remove brackets and spaces
            # Filter out empty strings that might result from extra commas
            items = [item.strip() for item in list_str.split(',') if item.strip()]
            return [item_type(item) for item in items]
        except Exception as e:
            logger.log_error(e, f"Failed to parse list from config string: '{list_str}'")
            return []

    def _validate_pin(self, pin):
        # Ensure pin is integer before checking
        try:
            pin_int = int(pin)
        except ValueError:
             logger.log_error(ValueError(f"Invalid pin value '{pin}'. Must be an integer."), "Config Validation")
             # Return a default or raise an error - returning default might hide issues
             # For critical pins like servo, maybe raise error? For LEDs, fallback might be ok.
             # Let's fallback for now, but log clearly.
             return 18 # Arbitrary default, adjust as needed

        if pin_int in self.VALID_PINS:
            return pin_int
        else:
            logger.log_error(ValueError(f"Pin {pin_int} is not in the list of valid pins: {self.VALID_PINS}. Falling back to default 18."), "Config Validation")
            return 18 # Fallback pin

    # This duty cycle validation is no longer needed for the servo with pigpio
    # def _validate_duty(self, duty):
    #     return min(max(2.5, duty), 12.5)

# --- Config Validation (Optional but Recommended) ---
class ConfigValidator:
    @staticmethod
    def validate_config(config_obj: Config) -> bool:
        issues = []
        try:
            # Email (Optional user/pass check)
            if not config_obj.EMAIL_HOST or not config_obj.EMAIL_PORT:
                issues.append("Email host or port missing.")
            # if config_obj.EMAIL_USER is None or config_obj.EMAIL_PASS is None:
            #     logger.log_info("Email user/pass not found in keyring (optional).")
            if not config_obj.EMAIL_RECIPIENT:
                 logger.log_info("Email recipient not set in config (optional for alerts).")

            # GPIO Pins (Check if they are in the valid list)
            gpio_pins_to_check = {
                'FAN': config_obj.FAN_PIN,
                'BUZZER': config_obj.BUZZER_PIN,
                'SOLENOID': config_obj.SOLENOID_PIN,
                'LED_GREEN': config_obj.LED_GREEN_PIN,
                'LED_RED': config_obj.LED_RED_PIN,
                'SERVO': config_obj.SERVO_PIN
            }
            for name, pin in gpio_pins_to_check.items():
                if pin not in config_obj.VALID_PINS:
                    issues.append(f"Invalid GPIO pin configured for {name}: {pin}. Not in valid list: {config_obj.VALID_PINS}")

            # Servo Pulse Widths
            if not (500 <= config_obj.SERVO_OPEN_PULSE_WIDTH <= 2500):
                 issues.append(f"Servo open pulse width ({config_obj.SERVO_OPEN_PULSE_WIDTH}us) outside typical range (500-2500us).")
            if not (500 <= config_obj.SERVO_CLOSE_PULSE_WIDTH <= 2500):
                 issues.append(f"Servo close pulse width ({config_obj.SERVO_CLOSE_PULSE_WIDTH}us) outside typical range (500-2500us).")
            if config_obj.SERVO_DELAY <= 0:
                issues.append(f"Invalid servo move delay: {config_obj.SERVO_DELAY}s. Must be positive.")

            # Temperature
            if config_obj.FAN_ON_TEMP <= config_obj.FAN_OFF_TEMP:
                issues.append("Fan ON temperature must be greater than OFF temperature.")
            if not os.path.exists(config_obj.THERMAL_FILE):
                # Log as warning, might not be critical if fan control isn't used
                logger.log_error(FileNotFoundError(f"Thermal file not found: {config_obj.THERMAL_FILE}"), "Config Validation", severity="WARNING")

            # NFC
            if config_obj.NFC_MAX_ATTEMPTS < 1 or config_obj.NFC_TIMEOUT < 1:
                issues.append("NFC max attempts and timeout must be positive integers.")

            # Database
            if not config_obj.DB_PATH:
                issues.append("Database path is required.")
            # Check if DB directory exists?
            db_dir = os.path.dirname(config_obj.DB_PATH)
            if db_dir and not os.path.exists(db_dir):
                try:
                    os.makedirs(db_dir)
                    logger.log_info(f"Created database directory: {db_dir}")
                except Exception as e:
                    issues.append(f"Failed to create database directory {db_dir}: {e}")

            if issues:
                for issue in issues:
                    logger.log_error(ValueError(issue), "Configuration validation failed")
                return False
            else:
                logger.log_info("Configuration validation successful.")
                return True

        except Exception as e:
            logger.log_error(e, "Critical error during configuration validation")
            return False

# --- Initialize Config --- 
config = Config()
if not ConfigValidator.validate_config(config):
    # Decide whether to exit or continue with potential issues
    logger.log_error(RuntimeError("CRITICAL: Configuration validation failed. Check logs. Exiting."), "Startup")
    # Optional: Allow running with defaults? Risky.
    # print("WARNING: Configuration validation failed. Attempting to run with defaults/fallbacks.")
    sys.exit(1)

# --- pigpio Instance --- 
# Global or passed to relevant classes
pigpio_instance = None
if PIGPIO_AVAILABLE:
    try:
        pigpio_instance = pigpio.pi() # Connect to the daemon
        if not pigpio_instance.connected:
            logger.log_error(RuntimeError("Failed to connect to pigpio daemon. Is it running? Try 'sudo systemctl start pigpiod'"), "Startup")
            # Handle failure: exit, or disable servo functionality
            PIGPIO_AVAILABLE = False # Mark as unavailable if connection failed
            pigpio_instance = None
            print("WARNING: pigpio daemon connection failed. Servo functionality disabled.")
        else:
            print("Successfully connected to pigpio daemon.")
    except Exception as e:
        logger.log_error(e, "Exception while connecting to pigpio daemon")
        PIGPIO_AVAILABLE = False
        pigpio_instance = None
        print("WARNING: Exception connecting to pigpio daemon. Servo functionality disabled.")

# --- Authentication --- 
class Authenticator:
    # ... (Keep the existing Authenticator class as is) ...
    SERVICE_NAME = "nfc_gate"
    ADMIN_USER_KEY = "admin_user"
    ADMIN_PASS_KEY = "admin_pass"

    @staticmethod
    def setup_credentials_interactively():
        try:
            # Check if keyring is available and usable
            try:
                keyring.get_password(Authenticator.SERVICE_NAME, "test_key")
            except Exception as e:
                print(f"Warning: Keyring access issue ({e}). Credentials might not be stored securely.")
                logger.log_error(e, "Keyring access failed during setup check", severity="WARNING")
                # Optionally fallback to less secure method or inform user

            if not keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY):
                print("Setting up admin credentials...")
                username = input("Enter admin username: ")
                password = input("Enter admin password: ")
                # Add validation for username/password complexity if desired
                if username and password:
                    keyring.set_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY, username)
                    keyring.set_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_PASS_KEY, password)
                    print("Admin credentials stored securely in keyring.")
                    logger.log_audit("admin_setup_success", {"username": username})
                else:
                    print("Username or password cannot be empty. Setup aborted.")
                    logger.log_audit("admin_setup_failed", {"reason": "empty credentials"})

        except Exception as e:
            logger.log_error(e, "Failed to setup credentials interactively")
            print(f"Error setting up credentials: {e}")

    @staticmethod
    def authenticate(parent_window=None): # Allow passing parent for modality
        try:
            stored_user = keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY)
            stored_pass = keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_PASS_KEY)
        except Exception as e:
            logger.log_error(e, "Failed to retrieve credentials from keyring")
            messagebox.showerror("Authentication Error", f"Could not retrieve credentials: {e}", parent=parent_window)
            return False

        if not stored_user or not stored_pass:
            messagebox.showerror("Setup Required", "Admin credentials not set. Run script from terminal to set up.", parent=parent_window)
            logger.log_audit("login_failed", {"reason": "credentials_not_set"})
            return False

        # Create a new Toplevel window for authentication
        auth_window = Toplevel(parent_window) # Make it a child of parent if provided
        auth_window.title("System Login")
        auth_window.geometry("300x170") # Slightly taller for status label
        auth_window.resizable(False, False)
        auth_window.transient(parent_window) # Keep on top of parent
        auth_window.grab_set()  # Make window modal

        Label(auth_window, text="Username:").pack(pady=(10,0))
        user_entry = Entry(auth_window, width=30)
        user_entry.pack()
        Label(auth_window, text="Password:").pack(pady=(5,0))
        pass_entry = Entry(auth_window, show="*", width=30)
        pass_entry.pack()

        status_label = Label(auth_window, text="", fg="red")
        status_label.pack(pady=(5,0))

        attempts = 3
        authenticated = [False] # Use list to modify in inner function

        def check_credentials():
            nonlocal attempts
            username = user_entry.get()
            password = pass_entry.get()

            # Basic input validation
            if not username or not password:
                status_label.config(text="Username/Password required.")
                return

            if username == stored_user and password == stored_pass:
                authenticated[0] = True
                logger.log_audit("login_success", {"username": username})
                auth_window.destroy()
            else:
                attempts -= 1
                logger.log_audit("login_failed", {"username": username, "attempts_left": attempts})
                if attempts > 0:
                    status_label.config(text=f"Invalid credentials. {attempts} attempts left.")
                    # messagebox.showwarning("Login Failed", f"Invalid credentials. {attempts} attempts remaining.", parent=auth_window)
                else:
                    status_label.config(text="Too many failed attempts. Locked.")
                    logger.log_audit("login_locked", {"username": username})
                    messagebox.showerror("Login Locked", "Too many failed attempts. Access denied.", parent=auth_window)
                    authenticated[0] = False # Ensure it's false
                    auth_window.destroy()

        login_button = Button(auth_window, text="Login", command=check_credentials, width=10)
        login_button.pack(pady=10)

        # Allow pressing Enter to login
        auth_window.bind('<Return>', lambda event=None: login_button.invoke())
        user_entry.focus_set() # Set focus to username field

        # Center the window (optional)
        # auth_window.update_idletasks()
        # x = parent_window.winfo_x() + (parent_window.winfo_width() // 2) - (auth_window.winfo_width() // 2)
        # y = parent_window.winfo_y() + (parent_window.winfo_height() // 2) - (auth_window.winfo_height() // 2)
        # auth_window.geometry(f"+{x}+{y}")

        auth_window.wait_window() # Wait for the window to be closed
        return authenticated[0]

# --- Database Management ---
class CardDatabase:
    # ... (Keep the existing CardDatabase class as is) ...
    # (Ensure encryption/decryption methods handle potential errors gracefully)
    def __init__(self, db_path: str, encrypted: bool = True) -> None:
        self.db_path = db_path
        self.encrypted = encrypted
        self.key = None
        self.cipher = None
        self.card_cache = {} # Simple cache
        self.cache_size = 20

        if self.encrypted:
            if not self._setup_encryption():
                logger.log_error(RuntimeError("Encryption setup failed. Database operations might be insecure or fail."), "Database Init", severity="CRITICAL")
                # Decide: Exit or continue with encryption disabled?
                # self.encrypted = False
                # sys.exit(1)

        if not self._setup_database():
             logger.log_error(RuntimeError("Database table setup failed. Exiting."), "Database Init", severity="CRITICAL")
             sys.exit(1)

        # Consider if demo data is appropriate for a production system
        # self._add_demo_data()

    def _setup_encryption(self) -> bool:
        try:
            key_file = Path("db.key")
            if not key_file.exists():
                self.key = Fernet.generate_key()
                with open(key_file, 'wb') as f:
                    f.write(self.key)
                os.chmod(key_file, 0o600) # Read/write only for owner
                logger.log_info("Generated new database encryption key.")
            else:
                # Ensure key file has correct permissions
                if os.stat(key_file).st_mode & 0o077:
                     logger.log_error(PermissionError("Encryption key file has insecure permissions. Should be 600."), "Encryption Setup", severity="WARNING")
                     # Optionally: Attempt to fix permissions? os.chmod(key_file, 0o600)
                with open(key_file, 'rb') as f:
                    self.key = f.read()
                logger.log_info("Loaded existing database encryption key.")

            self.cipher = Fernet(self.key)
            return True
        except (FileNotFoundError, PermissionError, InvalidToken) as e:
             logger.log_error(e, "Failed to setup encryption key/cipher", severity="CRITICAL")
             return False
        except Exception as e:
            logger.log_error(e, "Unexpected error during encryption setup", severity="CRITICAL")
            return False

    def _setup_database(self) -> bool:
        try:
            conn = sqlite3.connect(self.db_path, timeout=10) # Add timeout
            cursor = conn.cursor()
            # Use TEXT for dates, INTEGER for boolean
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY,
                name TEXT,
                faculty TEXT,
                program TEXT,
                level TEXT,
                student_id TEXT,
                email TEXT,
                expiry_date TEXT, 
                is_valid INTEGER DEFAULT 1, 
                last_access TEXT, 
                photo_path TEXT,
                is_blacklisted INTEGER DEFAULT 0,
                access_count INTEGER DEFAULT 0
            )
            ''')
            # Add necessary indices
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_card_id ON cards(id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_student_id ON cards(student_id)')
            # Consider adding indices for other frequently searched fields if needed
            conn.commit()
            conn.close()
            logger.log_info("Database table 'cards' initialized/verified.")
            return True
        except sqlite3.Error as e:
            logger.log_error(e, "SQLite error during database setup", severity="CRITICAL")
            return False
        except Exception as e:
             logger.log_error(e, "Unexpected error during database setup", severity="CRITICAL")
             return False

    # Demo data might be better handled by a separate setup script
    # def _add_demo_data(self) -> None: ...

    def _encrypt(self, data: Optional[str]) -> Optional[str]:
        if not self.encrypted or not self.cipher or data is None:
            return data
        try:
            return self.cipher.encrypt(data.encode()).decode()
        except Exception as e:
            logger.log_error(e, "Encryption failed for data", severity="WARNING")
            return None # Or return original data? Returning None might be safer.

    def _decrypt(self, data: Optional[str]) -> Optional[str]:
        if not self.encrypted or not self.cipher or data is None:
            return data
        try:
            # Ensure data is bytes before decrypting if needed, but it should be stored as str
            return self.cipher.decrypt(data.encode()).decode()
        except InvalidToken:
            logger.log_error(InvalidToken("Invalid token during decryption - data may be corrupted or wrong key"), "Decryption Failed", severity="ERROR")
            return "<DECRYPTION_ERROR>" # Indicate error clearly
        except Exception as e:
            logger.log_error(e, "Decryption failed", severity="ERROR")
            return "<DECRYPTION_ERROR>"

    def add_or_update_card(self, card_data: Dict[str, Any]) -> bool:
        required_fields = ['id', 'name', 'expiry_date', 'is_valid']
        if not all(field in card_data for field in required_fields):
            logger.log_error(ValueError("Missing required fields for card operation"), "Add/Update Card")
            return False

        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()

            # Prepare data, encrypting sensitive fields
            db_data = card_data.copy()
            db_data['name'] = self._encrypt(db_data.get('name'))
            db_data['faculty'] = self._encrypt(db_data.get('faculty'))
            db_data['program'] = self._encrypt(db_data.get('program'))
            db_data['level'] = self._encrypt(db_data.get('level'))
            db_data['student_id'] = self._encrypt(db_data.get('student_id'))
            db_data['email'] = self._encrypt(db_data.get('email'))
            db_data['photo_path'] = self._encrypt(db_data.get('photo_path'))

            # Ensure boolean/integer fields are correct type
            db_data['is_valid'] = int(db_data.get('is_valid', 1))
            db_data['is_blacklisted'] = int(db_data.get('is_blacklisted', 0))
            db_data['access_count'] = int(db_data.get('access_count', 0))

            # Use INSERT OR REPLACE (or separate INSERT/UPDATE logic)
            # Define columns explicitly for clarity and safety
            columns = ['id', 'name', 'faculty', 'program', 'level', 'student_id', 
                       'email', 'expiry_date', 'is_valid', 'last_access', 
                       'photo_path', 'is_blacklisted', 'access_count']
            placeholders = ', '.join(['?'] * len(columns))
            sql = f"INSERT OR REPLACE INTO cards ({', '.join(columns)}) VALUES ({placeholders})"
            
            # Create tuple in correct order, handling missing optional fields
            values = tuple(db_data.get(col) for col in columns)

            cursor.execute(sql, values)
            conn.commit()

            logger.log_audit("card_added_or_updated", {"card_id": card_data['id']})

            # Invalidate cache for this card
            if card_data['id'] in self.card_cache:
                del self.card_cache[card_data['id']]

            return True
        except sqlite3.Error as e:
            logger.log_error(e, f"SQLite error adding/updating card {card_data.get('id', 'N/A')}")
            if conn: conn.rollback()
            return False
        except Exception as e:
            logger.log_error(e, f"Unexpected error adding/updating card {card_data.get('id', 'N/A')}")
            if conn: conn.rollback()
            return False
        finally:
            if conn: conn.close()

    def get_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        if not card_id:
             return None
        # Check cache first
        if card_id in self.card_cache:
            # Return a copy to prevent external modification of cache
            return self.card_cache[card_id].copy()

        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.row_factory = sqlite3.Row # Return results as dict-like rows
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cards WHERE id = ?", (card_id,))
            row = cursor.fetchone()

            if not row:
                return None

            card_data = dict(row)

            # Decrypt sensitive fields
            card_data['name'] = self._decrypt(card_data.get('name'))
            card_data['faculty'] = self._decrypt(card_data.get('faculty'))
            card_data['program'] = self._decrypt(card_data.get('program'))
            card_data['level'] = self._decrypt(card_data.get('level'))
            card_data['student_id'] = self._decrypt(card_data.get('student_id'))
            card_data['email'] = self._decrypt(card_data.get('email'))
            card_data['photo_path'] = self._decrypt(card_data.get('photo_path'))

            # Update cache
            if len(self.card_cache) >= self.cache_size:
                # Simple FIFO cache eviction
                if self.card_cache:
                    self.card_cache.pop(next(iter(self.card_cache)))
            self.card_cache[card_id] = card_data.copy() # Store a copy

            return card_data

        except sqlite3.Error as e:
            logger.log_error(e, f"SQLite error getting card {card_id}")
            return None
        except Exception as e:
            logger.log_error(e, f"Unexpected error getting card {card_id}")
            return None
        finally:
            if conn: conn.close()

    def update_card_status(self, card_id: str, is_valid: Optional[bool] = None, is_blacklisted: Optional[bool] = None) -> bool:
        if not card_id or (is_valid is None and is_blacklisted is None):
            return False
        
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()
            updates = []
            params = []
            if is_valid is not None:
                updates.append("is_valid = ?")
                params.append(int(is_valid))
            if is_blacklisted is not None:
                updates.append("is_blacklisted = ?")
                params.append(int(is_blacklisted))
            
            params.append(card_id)
            sql = f"UPDATE cards SET {', '.join(updates)} WHERE id = ?"
            
            cursor.execute(sql, tuple(params))
            conn.commit()
            
            if cursor.rowcount > 0:
                logger.log_audit("card_status_updated", {"card_id": card_id, "valid": is_valid, "blacklisted": is_blacklisted})
                # Invalidate cache
                if card_id in self.card_cache:
                    del self.card_cache[card_id]
                return True
            else:
                logger.log_error(ValueError(f"Card ID {card_id} not found for status update."), "Update Card Status", severity="WARNING")
                return False
                
        except sqlite3.Error as e:
            logger.log_error(e, f"SQLite error updating status for card {card_id}")
            if conn: conn.rollback()
            return False
        except Exception as e:
            logger.log_error(e, f"Unexpected error updating status for card {card_id}")
            if conn: conn.rollback()
            return False
        finally:
            if conn: conn.close()

    def record_access(self, card_id: str) -> bool:
        if not card_id:
            return False
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()
            # Update last access time and increment access count
            cursor.execute(
                "UPDATE cards SET last_access = ?, access_count = access_count + 1 WHERE id = ?",
                (datetime.now().isoformat(), card_id)
            )
            conn.commit()

            if cursor.rowcount > 0:
                 # Update cache if exists (optional, depends if last_access/count is needed often)
                if card_id in self.card_cache:
                    self.card_cache[card_id]['last_access'] = datetime.now().isoformat()
                    self.card_cache[card_id]['access_count'] = self.card_cache[card_id].get('access_count', 0) + 1
                return True
            else:
                # Card might have been deleted between check and update
                logger.log_error(ValueError(f"Card ID {card_id} not found for recording access."), "Record Access", severity="WARNING")
                return False

        except sqlite3.Error as e:
            logger.log_error(e, f"SQLite error recording access for card {card_id}")
            if conn: conn.rollback()
            return False
        except Exception as e:
            logger.log_error(e, f"Unexpected error recording access for card {card_id}")
            if conn: conn.rollback()
            return False
        finally:
            if conn: conn.close()

    def get_all_cards(self) -> List[Dict[str, Any]]:
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cards ORDER BY name") # Order results
            rows = cursor.fetchall()
            cards = []
            for row in rows:
                card_data = dict(row)
                # Decrypt sensitive fields
                card_data['name'] = self._decrypt(card_data.get('name'))
                card_data['faculty'] = self._decrypt(card_data.get('faculty'))
                card_data['program'] = self._decrypt(card_data.get('program'))
                card_data['level'] = self._decrypt(card_data.get('level'))
                card_data['student_id'] = self._decrypt(card_data.get('student_id'))
                card_data['email'] = self._decrypt(card_data.get('email'))
                card_data['photo_path'] = self._decrypt(card_data.get('photo_path'))
                cards.append(card_data)
            return cards
        except sqlite3.Error as e:
            logger.log_error(e, "SQLite error getting all cards")
            return []
        except Exception as e:
            logger.log_error(e, "Unexpected error getting all cards")
            return []
        finally:
            if conn: conn.close()

    def delete_card(self, card_id: str) -> bool:
        if not card_id:
            return False
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cards WHERE id = ?", (card_id,))
            conn.commit()
            
            if cursor.rowcount > 0:
                logger.log_audit("card_deleted", {"card_id": card_id})
                # Remove from cache
                if card_id in self.card_cache:
                    del self.card_cache[card_id]
                return True
            else:
                # Card didn't exist
                logger.log_error(ValueError(f"Card ID {card_id} not found for deletion."), "Delete Card", severity="WARNING")
                return False
                
        except sqlite3.Error as e:
            logger.log_error(e, f"SQLite error deleting card {card_id}")
            if conn: conn.rollback()
            return False
        except Exception as e:
            logger.log_error(e, f"Unexpected error deleting card {card_id}")
            if conn: conn.rollback()
            return False
        finally:
            if conn: conn.close()

# --- NFC Reader Logic ---
class NFCReader:
    # ... (Keep the existing NFCReader class, ensure it handles errors) ...
    # (Consider adding robustness like re-initialization on failure)
    def __init__(self, config_obj: Config) -> None:
        self.config = config_obj
        self.clf = None
        self.connected = False
        self.stop_event = threading.Event()
        self.card_detected_event = threading.Event()
        self.card_id = None
        self.reader_thread = None
        self.mock_mode = not NFC_AVAILABLE
        self.last_error_time = None
        self.error_retry_delay = 5 # Seconds

        if not self.mock_mode:
            self._initialize_reader()
        else:
            logger.log_info("NFC Reader initialized in MOCK mode.")

    def _initialize_reader(self) -> bool:
        if self.mock_mode:
            return False
        try:
            # Try initializing on common paths
            # Common paths: 'usb', 'i2c', 'tty:S0', 'tty:AMA0'
            # Prioritize specific path if known, e.g., 'i2c:pn532:bus=1'
            path = 'i2c' # Or get from config?
            self.clf = nfc.ContactlessFrontend(path)
            if self.clf:
                logger.log_info(f"NFC reader initialized successfully on path '{path}': {self.clf}")
                self.connected = True
                self.last_error_time = None # Reset error time on success
                return True
            else:
                logger.log_error(RuntimeError(f"Failed to initialize NFC reader on path '{path}'. clf is None."), "NFC Init")
                self.connected = False
                self.last_error_time = time.time()
                return False
        except Exception as e:
            # Log the specific exception
            logger.log_error(e, f"Failed to initialize NFC reader on path '{path}'", severity="ERROR")
            self.connected = False
            self.last_error_time = time.time()
            # Consider switching to mock mode permanently after too many failures?
            # self.mock_mode = True
            return False

    def start_reading(self) -> None:
        if self.reader_thread and self.reader_thread.is_alive():
            logger.log_info("NFC reader thread already running.")
            return

        logger.log_info(f"Starting NFC reader thread (Mock Mode: {self.mock_mode}).")
        self.stop_event.clear()
        self.card_detected_event.clear()
        self.card_id = None

        if self.mock_mode:
            self.reader_thread = threading.Thread(target=self._mock_reader_loop, daemon=True)
        else:
            self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)

        self.reader_thread.start()

    def stop_reading(self) -> None:
        logger.log_info("Stopping NFC reader thread...")
        self.stop_event.set()
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=2.0)
            if self.reader_thread.is_alive():
                 logger.log_error(RuntimeError("NFC reader thread did not stop gracefully."), "NFC Stop", severity="WARNING")

        if not self.mock_mode and self.clf and self.connected:
            try:
                self.clf.close()
                logger.log_info("NFC reader connection closed.")
            except Exception as e:
                logger.log_error(e, "Error closing NFC reader connection", severity="WARNING")
        self.connected = False
        self.clf = None
        self.reader_thread = None

    def _reader_loop(self) -> None:
        attempts = 0
        while not self.stop_event.is_set():
            if not self.connected:
                # Wait before retrying initialization after an error
                if self.last_error_time and (time.time() - self.last_error_time < self.error_retry_delay):
                    time.sleep(1)
                    continue
                logger.log_info("Attempting to re-initialize NFC reader...")
                if not self._initialize_reader():
                    # Initialization failed, wait before next attempt
                    time.sleep(self.error_retry_delay)
                    continue # Skip to next loop iteration

            try:
                # Define target types based on config or common types
                # Example: Type A (MIFARE), Type F (FeliCa), Type B
                # rdwr options: check documentation for nfcpy
                # 'on-connect': lambda tag: False # Prevents nfcpy from keeping tag connected
                # 'beep-on-connect': False
                target = RemoteTarget(f"106{self.config.NFC_PROTOCOL}") # Adjust based on config
                tag = self.clf.connect(rdwr={'on-connect': lambda tag: False},
                                      targets=[target], # Pass target object
                                      interval=0.2, # Slightly longer interval?
                                      iterations=5) # Poll for 1 second (5 * 0.2s)

                if tag:
                    card_id_bytes = tag.identifier
                    self.card_id = card_id_bytes.hex().upper()
                    logger.log_info(f"NFC Tag Detected: {self.card_id}")
                    self.card_detected_event.set() # Signal main thread
                    # Wait briefly to avoid immediate re-detection of the same card
                    time.sleep(1.5)
                    self.card_detected_event.clear()
                    self.card_id = None # Clear after signaling
                else:
                    # No tag found in this iteration, continue polling
                    pass

                attempts = 0 # Reset attempts on successful poll (even if no card found)

            except nfc.clf.TimeoutError:
                 # This is expected if no card is present, not really an error
                 attempts = 0 # Reset attempts
                 # logger.log_info("NFC poll timeout (no card detected).") # Too verbose for logs
                 pass
            except nfc.clf.UnsupportedTargetError as e:
                 logger.log_error(e, f"Unsupported NFC target type detected.", severity="WARNING")
                 attempts += 1
                 time.sleep(1)
            except Exception as e:
                # Catch broader exceptions (like communication errors)
                logger.log_error(e, f"NFC reader error (attempt {attempts + 1}/{self.config.NFC_MAX_ATTEMPTS})", severity="ERROR")
                attempts += 1
                self.connected = False # Assume connection lost on error
                self.last_error_time = time.time()
                if self.clf:
                    try: self.clf.close() # Try to close cleanly
                    except: pass
                    self.clf = None
                time.sleep(1) # Wait after error

                if attempts >= self.config.NFC_MAX_ATTEMPTS:
                    logger.log_error(RuntimeError("Maximum NFC reader error attempts reached. Switching to MOCK mode."), "NFC Reader Loop")
                    self.mock_mode = True
                    self.stop_event.set() # Stop this thread
                    # Optionally: Signal main application about the failure
                    break # Exit loop

        logger.log_info("NFC reader loop finished.")
        self.connected = False
        if self.clf: try: self.clf.close(); except: pass


    def _mock_reader_loop(self) -> None:
        logger.log_info("Running MOCK NFC reader loop.")
        mock_ids = ["04010203040506", "04060708090A0B", "040C0D0E0F1011", "DEADBEEFCAFE"] # Add more test IDs
        idx = 0
        while not self.stop_event.is_set():
            try:
                # Simulate waiting for a card
                time.sleep(5) # Wait 5 seconds
                if self.stop_event.is_set(): break

                # Simulate detecting a card
                self.card_id = mock_ids[idx % len(mock_ids)]
                logger.log_info(f"[MOCK] NFC Tag Detected: {self.card_id}")
                self.card_detected_event.set()
                idx += 1

                # Simulate card removal
                time.sleep(1.5)
                if self.stop_event.is_set(): break
                self.card_detected_event.clear()
                self.card_id = None

            except Exception as e:
                 logger.log_error(e, "Error in mock NFC loop")
                 time.sleep(2)
        logger.log_info("Mock NFC reader loop finished.")

    def get_detected_card(self) -> Optional[str]:
        if self.card_detected_event.is_set():
            return self.card_id
        return None

# --- Hardware Control (LEDs, Buzzer, Solenoid, Servo) ---
class HardwareController:
    def __init__(self, config_obj: Config, pi_instance: Optional[pigpio.pi]):
        self.config = config_obj
        self.pi = pi_instance # pigpio instance for servo
        self.gpio_available = GPIO_AVAILABLE
        self.pigpio_available = PIGPIO_AVAILABLE and self.pi is not None and self.pi.connected

        if self.gpio_available:
            try:
                GPIO.setmode(GPIO.BCM) # Use Broadcom pin numbering
                GPIO.setwarnings(False)
                # Setup non-servo pins
                GPIO.setup(self.config.LED_GREEN_PIN, GPIO.OUT, initial=GPIO.LOW)
                GPIO.setup(self.config.LED_RED_PIN, GPIO.OUT, initial=GPIO.LOW)
                GPIO.setup(self.config.BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
                GPIO.setup(self.config.SOLENOID_PIN, GPIO.OUT, initial=GPIO.LOW)
                # Setup Fan pin if needed (or handle in temp monitor)
                GPIO.setup(self.config.FAN_PIN, GPIO.OUT, initial=GPIO.LOW)
                logger.log_info("RPi.GPIO pins initialized (BCM mode).")
            except Exception as e:
                 logger.log_error(e, "Failed to initialize RPi.GPIO pins", severity="ERROR")
                 self.gpio_available = False # Disable if setup fails
        else:
             logger.log_info("RPi.GPIO not available or setup failed. Using mock GPIO for non-servo components.")

        if not self.pigpio_available:
            logger.log_error(RuntimeError("pigpio not available or not connected. Servo control will be disabled."), "Hardware Init", severity="CRITICAL")

    def set_led(self, color: str, state: bool) -> None:
        if not self.gpio_available:
            # print(f"MockLED: {color} -> {'ON' if state else 'OFF'}")
            return
        pin = None
        if color == 'green':
            pin = self.config.LED_GREEN_PIN
        elif color == 'red':
            pin = self.config.LED_RED_PIN

        if pin:
            try:
                GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)
            except Exception as e:
                 logger.log_error(e, f"Failed to set {color} LED on pin {pin}", severity="WARNING")

    def buzz(self, duration: float = 0.1, times: int = 1) -> None:
        if not self.gpio_available:
            # print(f"MockBuzzer: Buzz {times} times for {duration}s")
            return
        try:
            for _ in range(times):
                GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
                time.sleep(duration)
                GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
                if times > 1:
                    time.sleep(duration * 0.5) # Short pause between beeps
        except Exception as e:
             logger.log_error(e, f"Failed to activate buzzer on pin {self.config.BUZZER_PIN}", severity="WARNING")

    def activate_solenoid(self, duration: float = 0.5) -> None:
        if not self.gpio_available:
            # print(f"MockSolenoid: Activate for {duration}s")
            return
        try:
            GPIO.output(self.config.SOLENOID_PIN, GPIO.HIGH)
            logger.log_info(f"Solenoid activated on pin {self.config.SOLENOID_PIN}")
            time.sleep(duration)
            GPIO.output(self.config.SOLENOID_PIN, GPIO.LOW)
            logger.log_info(f"Solenoid deactivated on pin {self.config.SOLENOID_PIN}")
        except Exception as e:
             logger.log_error(e, f"Failed to activate solenoid on pin {self.config.SOLENOID_PIN}", severity="WARNING")

    # --- Servo Control using pigpio --- 
    def _move_servo(self, pulse_width: int) -> None:
        """Internal function to send pulse width command via pigpio."""
        if not self.pigpio_available or self.pi is None:
            logger.log_error(RuntimeError("Attempted servo move, but pigpio is not available/connected."), "Servo Control", severity="ERROR")
            print("MockServo: Move to pulse width", pulse_width) # Mock action
            return
        
        # Ensure pulse width is within safe range (e.g., 500-2500 us)
        # Use a slightly wider internal range for safety, config validation handles user input
        safe_pulse_width = max(400, min(2600, pulse_width))
        
        try:
            self.pi.set_servo_pulsewidth(self.config.SERVO_PIN, safe_pulse_width)
            # logger.log_info(f"Servo command sent: Pin {self.config.SERVO_PIN}, Pulse Width {safe_pulse_width} us")
        except Exception as e:
            logger.log_error(e, f"Failed to set servo pulse width on pin {self.config.SERVO_PIN}", severity="ERROR")

    def open_gate(self) -> None:
        """Moves the servo to the 'open' position."""
        logger.log_info("Opening gate...")
        self._move_servo(self.config.SERVO_OPEN_PULSE_WIDTH)
        time.sleep(self.config.SERVO_DELAY) # Wait for movement
        self._stop_servo() # Stop sending pulses
        logger.log_info("Gate opened (servo pulse stopped).")

    def close_gate(self) -> None:
        """Moves the servo to the 'close' position."""
        logger.log_info("Closing gate...")
        self._move_servo(self.config.SERVO_CLOSE_PULSE_WIDTH)
        time.sleep(self.config.SERVO_DELAY) # Wait for movement
        self._stop_servo() # Stop sending pulses
        logger.log_info("Gate closed (servo pulse stopped).")

    def _stop_servo(self) -> None:
        """Stops sending PWM pulses to the servo (sets pulse width to 0)."""
        # Setting pulse width to 0 tells pigpio to stop PWM on that pin
        self._move_servo(0)
        # logger.log_info(f"Stopped servo pulses on pin {self.config.SERVO_PIN}.")

    # --- Fan Control --- (Example)
    def set_fan(self, state: bool) -> None:
        if not self.gpio_available:
            # print(f"MockFan: {'ON' if state else 'OFF'}")
            return
        try:
            GPIO.output(self.config.FAN_PIN, GPIO.HIGH if state else GPIO.LOW)
            # logger.log_info(f"Fan on pin {self.config.FAN_PIN} set to {'ON' if state else 'OFF'}")
        except Exception as e:
             logger.log_error(e, f"Failed to set fan state on pin {self.config.FAN_PIN}", severity="WARNING")

    def cleanup(self) -> None:
        logger.log_info("Cleaning up hardware resources...")
        # Stop servo pulses first
        if self.pigpio_available:
            self._stop_servo()
            # Disconnect from pigpio daemon (handled globally or in main app exit)
            # if self.pi: self.pi.stop()

        # Cleanup RPi.GPIO pins
        if self.gpio_available:
            try:
                GPIO.cleanup()
                logger.log_info("RPi.GPIO cleanup successful.")
            except Exception as e:
                 logger.log_error(e, "Error during RPi.GPIO cleanup", severity="WARNING")

# --- Temperature Monitoring (Example) ---
class TemperatureMonitor(threading.Thread):
    def __init__(self, config_obj: Config, hardware_ctrl: HardwareController, interval: int = 30):
        super().__init__(daemon=True)
        self.config = config_obj
        self.hardware = hardware_ctrl
        self.interval = interval
        self.stop_event = threading.Event()
        self.current_temp = 0.0
        self.fan_state = False

    def run(self) -> None:
        logger.log_info("Starting Temperature Monitor thread.")
        while not self.stop_event.wait(self.interval):
            try:
                self.current_temp = self._read_cpu_temp()
                logger.metrics.cpu_temp = self.current_temp # Update shared metrics
                # logger.log_info(f"CPU Temperature: {self.current_temp:.1f}°C") # Verbose

                # Fan control logic
                if self.current_temp >= self.config.FAN_ON_TEMP and not self.fan_state:
                    logger.log_info(f"Temperature high ({self.current_temp:.1f}°C), turning fan ON.")
                    self.hardware.set_fan(True)
                    self.fan_state = True
                elif self.current_temp <= self.config.FAN_OFF_TEMP and self.fan_state:
                    logger.log_info(f"Temperature low ({self.current_temp:.1f}°C), turning fan OFF.")
                    self.hardware.set_fan(False)
                    self.fan_state = False

            except Exception as e:
                logger.log_error(e, "Error in temperature monitoring loop", severity="WARNING")
                # Avoid continuous errors, maybe sleep longer?
                time.sleep(self.interval)
        logger.log_info("Temperature Monitor thread stopped.")
        # Ensure fan is turned off on exit
        if self.fan_state:
             self.hardware.set_fan(False)

    def stop(self) -> None:
        self.stop_event.set()

    def _read_cpu_temp(self) -> float:
        try:
            with open(self.config.THERMAL_FILE, 'r') as f:
                temp_str = f.read().strip()
            # Handle potential non-integer values if file format changes
            return int(temp_str) / 1000.0
        except FileNotFoundError:
             logger.log_error(FileNotFoundError(f"Thermal file not found: {self.config.THERMAL_FILE}"), "CPU Temp Read", severity="WARNING")
             return -1.0 # Indicate error
        except (ValueError, IndexError) as e:
             logger.log_error(e, f"Error parsing temperature from file: {self.config.THERMAL_FILE}", severity="WARNING")
             return -1.0
        except Exception as e:
            logger.log_error(e, "Failed to read CPU temperature", severity="WARNING")
            return -1.0

# --- Email Notification (Example) ---
class EmailNotifier:
    def __init__(self, config_obj: Config):
        self.config = config_obj
        self.enabled = bool(self.config.EMAIL_USER and self.config.EMAIL_PASS and self.config.EMAIL_RECIPIENT)
        if not self.enabled:
            logger.log_info("Email notifications disabled (missing user, pass, or recipient).")

    def send_alert(self, subject: str, body: str) -> bool:
        if not self.enabled:
            return False

        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = self.config.EMAIL_USER
        msg['To'] = self.config.EMAIL_RECIPIENT

        try:
            logger.log_info(f"Attempting to send email alert to {self.config.EMAIL_RECIPIENT}...")
            # Use ThreadPoolExecutor for non-blocking send
            with ThreadPoolExecutor(max_workers=1) as executor:
                 future = executor.submit(self._send_email_sync, msg)
                 # Optionally wait for result or handle exceptions
                 # result = future.result(timeout=30) # Wait max 30s
                 # return result
            return True # Assume success if submitted (fire and forget)

        except Exception as e:
            logger.log_error(e, "Failed to send email alert", severity="ERROR")
            return False

    def _send_email_sync(self, msg: MIMEText) -> bool:
        """Synchronous email sending logic for the thread pool."""
        context = ssl.create_default_context()
        server = None
        try:
            if self.config.EMAIL_USE_TLS:
                server = smtplib.SMTP(self.config.EMAIL_HOST, self.config.EMAIL_PORT, timeout=20)
                server.starttls(context=context)
            else: # Assuming SSL if not TLS
                server = smtplib.SMTP_SSL(self.config.EMAIL_HOST, self.config.EMAIL_PORT, timeout=20, context=context)
            
            server.login(self.config.EMAIL_USER, self.config.EMAIL_PASS)
            server.send_message(msg)
            logger.log_info(f"Email alert '{msg['Subject']}' sent successfully to {msg['To']}.")
            return True
        except smtplib.SMTPAuthenticationError as e:
             logger.log_error(e, "SMTP Authentication failed. Check email user/pass.", severity="CRITICAL")
             # Consider disabling email alerts after repeated auth failures
             # self.enabled = False
             return False
        except Exception as e:
            logger.log_error(e, "Failed to send email alert (sync part)", severity="ERROR")
            return False
        finally:
            if server: server.quit()


# --- Main Application Logic ---
class SmartGateApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Smart Gate Control System")
        # Make window non-resizable initially, or set min/max size
        # self.root.resizable(False, False)
        self.root.geometry("800x600") # Adjust size as needed

        # Initialize components
        self.config = config # Use global config
        self.logger = logger # Use global logger
        self.db = CardDatabase(self.config.DB_PATH, self.config.DB_ENCRYPTED)
        self.hardware = HardwareController(self.config, pigpio_instance)
        self.nfc_reader = NFCReader(self.config)
        self.temp_monitor = TemperatureMonitor(self.config, self.hardware)
        self.emailer = EmailNotifier(self.config)

        # Rate limiting for NFC scans
        self.last_scan_times = {}
        self.rate_limit_seconds = 3 # Prevent scanning same card within 3 seconds

        # GUI Setup
        self._setup_gui()

        # Start background threads
        self.nfc_reader.start_reading()
        self.temp_monitor.start()

        # Start the main application loop (checking NFC)
        self.check_nfc_loop()

        # Set initial state (e.g., close gate)
        self.hardware.set_led('red', True)
        self.hardware.set_led('green', False)
        # Close gate on startup (optional, depends on desired initial state)
        # self.hardware.close_gate()

        # Setup graceful shutdown
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _setup_gui(self):
        # Use ttk for better styling
        style = ttk.Style(self.root)
        style.theme_use('clam') # Or 'alt', 'default', 'classic'

        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(expand=True, fill=tk.BOTH)

        # --- Left Panel: Status & Info ---
        left_panel = ttk.Frame(main_frame, width=300)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_panel.pack_propagate(False) # Prevent resizing based on content

        ttk.Label(left_panel, text="System Status", font=("Helvetica", 16, "bold")).pack(pady=(0, 10))

        # Status Label (for NFC scans, etc.)
        self.status_label = ttk.Label(left_panel, text="Status: Initializing...", wraplength=280, font=("Helvetica", 12))
        self.status_label.pack(pady=5, anchor='w')

        # Card Info Display
        ttk.Label(left_panel, text="Last Card Scanned:", font=("Helvetica", 12, "bold")).pack(pady=(15, 5), anchor='w')
        self.card_info_frame = ttk.Frame(left_panel, borderwidth=1, relief="sunken")
        self.card_info_frame.pack(fill=tk.X, pady=5)
        self.card_info_label = ttk.Label(self.card_info_frame, text="ID: N/A\nName: N/A\nStatus: N/A", justify=tk.LEFT, padding=5)
        self.card_info_label.pack(anchor='nw')

        # System Metrics Display
        ttk.Label(left_panel, text="System Metrics:", font=("Helvetica", 12, "bold")).pack(pady=(15, 5), anchor='w')
        self.metrics_frame = ttk.Frame(left_panel, borderwidth=1, relief="sunken")
        self.metrics_frame.pack(fill=tk.X, pady=5)
        self.metrics_label = ttk.Label(self.metrics_frame, text="Temp: --°C | Uptime: --s", justify=tk.LEFT, padding=5)
        self.metrics_label.pack(anchor='nw')

        # Manual Control Buttons
        ttk.Label(left_panel, text="Manual Control:", font=("Helvetica", 12, "bold")).pack(pady=(15, 5), anchor='w')
        button_frame = ttk.Frame(left_panel)
        button_frame.pack(fill=tk.X, pady=5)
        self.open_button = ttk.Button(button_frame, text="Open Gate", command=self.manual_open_gate)
        self.open_button.pack(side=tk.LEFT, padx=5)
        self.close_button = ttk.Button(button_frame, text="Close Gate", command=self.manual_close_gate)
        self.close_button.pack(side=tk.LEFT, padx=5)

        # --- Right Panel: Logs & Management ---
        right_panel = ttk.Frame(main_frame)
        right_panel.pack(side=tk.RIGHT, expand=True, fill=tk.BOTH)

        notebook = ttk.Notebook(right_panel)
        notebook.pack(expand=True, fill=tk.BOTH, pady=(0, 10))

        # Log Tab
        log_frame = ttk.Frame(notebook, padding=5)
        notebook.add(log_frame, text="System Logs")
        self.log_text = Text(log_frame, height=15, width=60, state=tk.DISABLED, wrap=tk.WORD, font=("Courier New", 9))
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text['yscrollcommand'] = log_scroll.set
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(expand=True, fill=tk.BOTH)

        # Card Management Tab (Placeholder - Requires more implementation)
        mgmt_frame = ttk.Frame(notebook, padding=5)
        notebook.add(mgmt_frame, text="Card Management")
        ttk.Label(mgmt_frame, text="Card Management Interface (Requires Admin Login)").pack(pady=10)
        self.manage_button = ttk.Button(mgmt_frame, text="Manage Cards", command=self.open_management_window)
        self.manage_button.pack(pady=10)

        # Initial status update
        self.update_status("System Ready. Waiting for NFC card...")
        self.update_metrics_display()
        self.update_log_display()

    def update_status(self, message: str, duration_ms: int = 5000):
        """Updates the main status label."""
        self.status_label.config(text=f"Status: {message}")
        self.logger.log_info(f"Status Update: {message}")
        # Optionally reset after a delay (if not persistent status)
        # self.root.after(duration_ms, lambda: self.status_label.config(text="Status: Waiting for NFC card..."))

    def update_card_info_display(self, card_info: Optional[CardInfo], access_status: Optional[AccessStatus] = None):
        """Updates the card information display area."""
        if card_info:
            status_text = access_status.name if access_status else "Checked"
            expiry_text = card_info.expiry_date.strftime('%Y-%m-%d') if card_info.expiry_date else 'N/A'
            valid_text = "Valid" if card_info.is_valid else "Invalid"
            blacklist_text = "BLACKLISTED" if card_info.is_blacklisted else ""
            
            info_str = (
                f"ID: {card_info.id}\n"
                f"Name: {card_info.name or 'N/A'}\n"
                f"Expiry: {expiry_text} ({valid_text}) {blacklist_text}\n"
                f"Access: {status_text}"
            )
            self.card_info_label.config(text=info_str)
        else:
            self.card_info_label.config(text="ID: N/A\nName: N/A\nStatus: N/A")

    def update_metrics_display(self):
        """Updates the system metrics display area periodically."""
        metrics = self.logger._get_current_metrics() # Access metrics via logger
        uptime_td = timedelta(seconds=int(metrics['system_uptime']))
        temp = self.temp_monitor.current_temp # Get latest temp from monitor
        
        metrics_str = (
            f"CPU Temp: {temp:.1f}°C | Fan: {'ON' if self.temp_monitor.fan_state else 'OFF'}\n"
            f"Uptime: {str(uptime_td)}\n"
            f"Access OK/Fail: {metrics['successful_accesses']}/{metrics['failed_accesses']} (Total: {metrics['total_requests']})\n"
            f"Avg Response: {metrics['average_response_time']:.3f}s"
        )
        self.metrics_label.config(text=metrics_str)
        # Schedule next update
        self.root.after(5000, self.update_metrics_display) # Update every 5 seconds

    def update_log_display(self):
        """Updates the log display text widget."""
        logs = self.logger.get_recent_logs(max_logs=10) # Get a few recent logs
        if logs:
            self.log_text.config(state=tk.NORMAL)
            for log_entry in logs:
                self.log_text.insert(tk.END, log_entry + '\n')
            self.log_text.see(tk.END) # Scroll to the bottom
            self.log_text.config(state=tk.DISABLED)
        # Schedule next update
        self.root.after(self.config.GUI_UPDATE_INTERVAL, self.update_log_display)

    def check_nfc_loop(self):
        """Periodically checks the NFC reader for detected cards."""
        card_id = self.nfc_reader.get_detected_card()
        if card_id:
            start_time = time.time()
            # Rate Limiting Check
            now = time.time()
            last_scan = self.last_scan_times.get(card_id, 0)
            if (now - last_scan) < self.rate_limit_seconds:
                self.update_status(f"Card {card_id} scanned too recently. Ignoring.")
                self.logger.log_info(f"Rate limit hit for card {card_id}")
            else:
                self.last_scan_times[card_id] = now
                self.update_status(f"Card detected: {card_id}. Processing...")
                self.process_card_scan(card_id, start_time)

        # Schedule the next check
        self.root.after(200, self.check_nfc_loop) # Check every 200ms

    def process_card_scan(self, card_id: str, start_time: float):
        """Handles the logic after a card is detected."""
        card_data = self.db.get_card(card_id)
        access_status = AccessStatus.DENIED # Default
        card_info_obj = CardInfo(id=card_id) # Basic object for logging/display

        if card_data:
            # Populate CardInfo object fully
            card_info_obj = CardInfo(
                id=card_id,
                name=card_data.get('name'),
                expiry_date=datetime.fromisoformat(card_data['expiry_date']) if card_data.get('expiry_date') else None,
                is_valid=bool(card_data.get('is_valid', 0)),
                last_access=datetime.fromisoformat(card_data['last_access']) if card_data.get('last_access') else None,
                faculty=card_data.get('faculty'),
                program=card_data.get('program'),
                level=card_data.get('level'),
                student_id=card_data.get('student_id'),
                email=card_data.get('email'),
                photo_path=card_data.get('photo_path'),
                is_blacklisted=bool(card_data.get('is_blacklisted', 0))
            )

            # --- Access Logic --- 
            if card_info_obj.is_blacklisted:
                access_status = AccessStatus.BLACKLISTED
                self.update_status(f"Access DENIED: Card {card_id} is blacklisted.")
                self.hardware.set_led('red', True)
                self.hardware.buzz(duration=0.1, times=3)
                self.hardware.set_led('red', False)
                # Send alert?
                # self.emailer.send_alert("Blacklisted Card Attempt", f"Card ID: {card_id}\nName: {card_info_obj.name}")
            elif not card_info_obj.is_valid:
                access_status = AccessStatus.DENIED
                self.update_status(f"Access DENIED: Card {card_id} is marked invalid.")
                self.hardware.set_led('red', True)
                self.hardware.buzz(duration=0.2, times=2)
                self.hardware.set_led('red', False)
            elif card_info_obj.expiry_date and card_info_obj.expiry_date < datetime.now():
                access_status = AccessStatus.DENIED
                self.update_status(f"Access DENIED: Card {card_id} has expired ({card_info_obj.expiry_date.strftime('%Y-%m-%d')}).")
                self.hardware.set_led('red', True)
                self.hardware.buzz(duration=0.2, times=2)
                self.hardware.set_led('red', False)
                # Optionally mark card as invalid in DB
                # self.db.update_card_status(card_id, is_valid=False)
            else:
                # Access Granted!
                access_status = AccessStatus.GRANTED
                self.update_status(f"Access GRANTED for {card_info_obj.name or card_id}.")
                self.hardware.set_led('green', True)
                self.hardware.buzz(duration=0.3, times=1)
                # --- Gate Operation --- 
                self.hardware.open_gate() # Uses pigpio
                # Optional: Activate solenoid briefly after opening?
                # self.hardware.activate_solenoid(duration=0.2)
                # Wait for a period, then close automatically
                # Consider making auto-close time configurable
                self.root.after(5000, self.auto_close_gate) # Auto-close after 5 seconds
                # Record successful access in DB
                self.db.record_access(card_id)
        else:
            # Card not found in database
            access_status = AccessStatus.DENIED
            self.update_status(f"Access DENIED: Card {card_id} not found in database.")
            self.hardware.set_led('red', True)
            self.hardware.buzz(duration=0.1, times=2)
            self.hardware.set_led('red', False)
            # Send alert for unknown card?
            # self.emailer.send_alert("Unknown Card Scanned", f"Card ID: {card_id}")

        # Log the access attempt
        response_time = time.time() - start_time
        self.logger.log_access(card_info_obj, access_status, response_time)
        # Update GUI card info panel
        self.update_card_info_display(card_info_obj, access_status)

    def auto_close_gate(self):
        """Closes the gate automatically after a delay."""
        self.update_status("Auto-closing gate...")
        self.hardware.close_gate() # Uses pigpio
        self.hardware.set_led('green', False)
        self.hardware.set_led('red', True) # Indicate closed/ready state
        self.update_status("System Ready. Waiting for NFC card...")

    def manual_open_gate(self):
        """Manually opens the gate (requires auth?)."""
        # Add authentication check if needed for manual control
        # if not Authenticator.authenticate(self.root):
        #     self.update_status("Manual open requires admin login.")
        #     return
        self.logger.log_audit("manual_open", {"user": "GUI"})
        self.update_status("Manual Open command received.")
        self.hardware.set_led('green', True)
        self.hardware.set_led('red', False)
        self.hardware.open_gate()
        # Don't auto-close on manual open? Or set a longer timer?
        # self.root.after(15000, self.auto_close_gate) # Close after 15s?

    def manual_close_gate(self):
        """Manually closes the gate."""
        # Add authentication check if needed
        self.logger.log_audit("manual_close", {"user": "GUI"})
        self.update_status("Manual Close command received.")
        self.hardware.close_gate()
        self.hardware.set_led('green', False)
        self.hardware.set_led('red', True)
        self.update_status("System Ready. Waiting for NFC card...")

    def open_management_window(self):
        """Opens the card management interface (requires auth)."""
        if Authenticator.authenticate(self.root):
            self.logger.log_audit("management_access_success", {"user": "Admin"})
            # Create and show the management window
            # This needs a separate Toplevel window with CRUD operations for the DB
            mgmt_window = CardManagementWindow(self.root, self.db, self.logger)
            mgmt_window.grab_set() # Make modal
        else:
            self.logger.log_audit("management_access_failed", {"user": "Attempted"})
            self.update_status("Admin authentication failed.")
            messagebox.showerror("Authentication Failed", "Admin login required for card management.", parent=self.root)

    def on_closing(self):
        """Handles graceful shutdown when the main window is closed."""
        if messagebox.askokcancel("Quit", "Do you want to quit the Smart Gate System?", parent=self.root):
            self.logger.log_info("Shutdown sequence initiated by user.")
            # Stop background threads
            self.nfc_reader.stop_reading()
            self.temp_monitor.stop()
            # Wait for threads to finish (optional, use join)
            # self.nfc_reader.reader_thread.join(timeout=1)
            # self.temp_monitor.join(timeout=1)

            # Cleanup hardware
            self.hardware.cleanup()

            # Disconnect pigpio (important!)
            if pigpio_instance and pigpio_instance.connected:
                logger.log_info("Disconnecting from pigpio daemon.")
                pigpio_instance.stop()

            self.logger.log_info("Application shutting down.")
            self.root.destroy()
        else:
             self.logger.log_info("Shutdown cancelled by user.")

# --- Card Management Window (Example Structure) ---
class CardManagementWindow(Toplevel):
    def __init__(self, parent, db: CardDatabase, logger_instance: ProfessionalLogger):
        super().__init__(parent)
        self.db = db
        self.logger = logger_instance
        self.title("Card Management")
        self.geometry("900x600") # Larger window for management
        # self.resizable(False, False)

        # Add widgets for displaying, adding, editing, deleting cards
        # Use a Treeview for displaying cards
        self._setup_widgets()
        self._load_cards()

    def _setup_widgets(self):
        # Frame for Treeview
        tree_frame = ttk.Frame(self, padding=5)
        tree_frame.pack(expand=True, fill=tk.BOTH, pady=5, padx=5)

        cols = ('id', 'name', 'student_id', 'expiry_date', 'is_valid', 'is_blacklisted', 'last_access')
        self.tree = ttk.Treeview(tree_frame, columns=cols, show='headings', selectmode='browse')

        # Define headings
        self.tree.heading('id', text='Card ID')
        self.tree.heading('name', text='Name')
        self.tree.heading('student_id', text='Student ID')
        self.tree.heading('expiry_date', text='Expiry')
        self.tree.heading('is_valid', text='Valid?')
        self.tree.heading('is_blacklisted', text='Blacklisted?')
        self.tree.heading('last_access', text='Last Access')

        # Configure column widths
        self.tree.column('id', width=120, anchor=tk.W)
        self.tree.column('name', width=150, anchor=tk.W)
        self.tree.column('student_id', width=100, anchor=tk.W)
        self.tree.column('expiry_date', width=100, anchor=tk.CENTER)
        self.tree.column('is_valid', width=60, anchor=tk.CENTER)
        self.tree.column('is_blacklisted', width=80, anchor=tk.CENTER)
        self.tree.column('last_access', width=150, anchor=tk.W)

        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side='right', fill='y')
        hsb.pack(side='bottom', fill='x')
        self.tree.pack(expand=True, fill=tk.BOTH)

        # Frame for entry fields and buttons
        entry_frame = ttk.LabelFrame(self, text="Card Details", padding=10)
        entry_frame.pack(fill=tk.X, padx=5, pady=5)

        # Add Labels and Entry fields for card details (id, name, student_id, expiry, etc.)
        # Example for ID and Name:
        ttk.Label(entry_frame, text="Card ID:").grid(row=0, column=0, padx=5, pady=2, sticky=tk.W)
        self.id_entry = ttk.Entry(entry_frame, width=30)
        self.id_entry.grid(row=0, column=1, padx=5, pady=2, sticky=tk.W)
        # Add Scan button?

        ttk.Label(entry_frame, text="Name:").grid(row=1, column=0, padx=5, pady=2, sticky=tk.W)
        self.name_entry = ttk.Entry(entry_frame, width=40)
        self.name_entry.grid(row=1, column=1, columnspan=2, padx=5, pady=2, sticky=tk.W)
        
        # ... Add other fields: student_id, expiry_date (use DateEntry?), is_valid (Checkbox?), is_blacklisted (Checkbox?) ...

        # Frame for buttons
        button_frame = ttk.Frame(self, padding=5)
        button_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        ttk.Button(button_frame, text="Add Card", command=self._add_card).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Update Card", command=self._update_card).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Delete Card", command=self._delete_card).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Clear Fields", command=self._clear_fields).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Refresh List", command=self._load_cards).pack(side=tk.LEFT, padx=5)

        # Bind tree selection to populate fields
        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select)

    def _load_cards(self):
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)
        # Fetch cards from DB
        cards = self.db.get_all_cards()
        for card in cards:
            # Format data for display
            valid_str = 'Yes' if card.get('is_valid') else 'No'
            blacklisted_str = 'Yes' if card.get('is_blacklisted') else 'No'
            last_access_str = card.get('last_access', 'N/A')
            if last_access_str and last_access_str != 'N/A':
                try: last_access_str = datetime.fromisoformat(last_access_str).strftime('%Y-%m-%d %H:%M')
                except: pass # Keep original string if format error
                
            values = (
                card.get('id', ''),
                card.get('name', ''),
                card.get('student_id', ''),
                card.get('expiry_date', ''),
                valid_str,
                blacklisted_str,
                last_access_str
            )
            self.tree.insert('', tk.END, values=values)
        self.logger.log_info(f"Loaded {len(cards)} cards into management view.")

    def _on_tree_select(self, event):
        selected_item = self.tree.focus() # Get selected item ID
        if not selected_item:
            return
        item_values = self.tree.item(selected_item, 'values')
        # Populate entry fields based on item_values
        self._clear_fields()
        self.id_entry.insert(0, item_values[0])
        self.name_entry.insert(0, item_values[1])
        # ... populate other fields ...
        # Handle boolean/checkbox fields appropriately

    def _collect_card_data_from_fields(self) -> Dict[str, Any]:
        # Collect data from all entry fields, checkboxes, etc.
        # Perform basic validation (e.g., ID not empty)
        card_data = {
            'id': self.id_entry.get().strip().upper(),
            'name': self.name_entry.get().strip(),
            # ... get other fields ...
            'expiry_date': '2099-12-31', # Placeholder - use DateEntry or validation
            'is_valid': 1, # Placeholder - use Checkbox value
            'is_blacklisted': 0 # Placeholder - use Checkbox value
        }
        if not card_data['id']:
             messagebox.showerror("Input Error", "Card ID cannot be empty.", parent=self)
             return None
        return card_data

    def _add_card(self):
        card_data = self._collect_card_data_from_fields()
        if card_data:
            # Check if card already exists?
            existing = self.db.get_card(card_data['id'])
            if existing:
                 if not messagebox.askyesno("Confirm Overwrite", f"Card ID {card_data['id']} already exists. Overwrite?", parent=self):
                     return
            
            if self.db.add_or_update_card(card_data):
                messagebox.showinfo("Success", "Card added/updated successfully.", parent=self)
                self._load_cards() # Refresh list
                self._clear_fields()
            else:
                messagebox.showerror("Database Error", "Failed to add/update card. Check logs.", parent=self)

    def _update_card(self):
        selected_item = self.tree.focus()
        if not selected_item:
            messagebox.showwarning("Selection Error", "Please select a card from the list to update.", parent=self)
            return
            
        card_data = self._collect_card_data_from_fields()
        if card_data:
             # Ensure the ID hasn't been changed to a different existing card?
             original_id = self.tree.item(selected_item, 'values')[0]
             if card_data['id'] != original_id:
                  # If ID changed, check if the new ID already exists (and isn't the original selected row)
                  existing_new_id = self.db.get_card(card_data['id'])
                  if existing_new_id:
                       messagebox.showerror("Update Error", f"Cannot change Card ID to {card_data['id']} as it already exists.", parent=self)
                       return
                  # If ID changed, we might need to delete old and insert new, or handle ID change in DB logic
                  # For simplicity, let's assume add_or_update handles replacing based on the new ID
                  # We might want to delete the old record first if the PK changes
                  # if self.db.delete_card(original_id): ... proceed to add ...
                  # Let's just use add_or_update which does INSERT OR REPLACE
                  pass 

             if self.db.add_or_update_card(card_data):
                messagebox.showinfo("Success", "Card updated successfully.", parent=self)
                self._load_cards()
                self._clear_fields()
             else:
                messagebox.showerror("Database Error", "Failed to update card. Check logs.", parent=self)

    def _delete_card(self):
        selected_item = self.tree.focus()
        if not selected_item:
            messagebox.showwarning("Selection Error", "Please select a card from the list to delete.", parent=self)
            return
        
        card_id = self.tree.item(selected_item, 'values')[0]
        card_name = self.tree.item(selected_item, 'values')[1]
        
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete card:\nID: {card_id}\nName: {card_name}", parent=self):
            if self.db.delete_card(card_id):
                messagebox.showinfo("Success", f"Card {card_id} deleted.", parent=self)
                self._load_cards()
                self._clear_fields()
            else:
                 messagebox.showerror("Database Error", f"Failed to delete card {card_id}. Check logs.", parent=self)

    def _clear_fields(self):
        # Clear all entry fields
        self.id_entry.delete(0, tk.END)
        self.name_entry.delete(0, tk.END)
        # ... clear other fields ...
        # Reset checkboxes
        # Set focus back to ID field?
        self.id_entry.focus_set()

# --- Main Execution ---
if __name__ == "__main__":
    # Setup admin credentials if running interactively and they don't exist
    if sys.stdin.isatty(): # Check if running in an interactive terminal
        try:
            Authenticator.setup_credentials_interactively()
        except Exception as e:
             print(f"Could not run interactive credential setup: {e}")
             logger.log_error(e, "Failed during interactive credential setup check")

    # Check pigpio availability before starting GUI
    if not PIGPIO_AVAILABLE or not pigpio_instance:
         print("CRITICAL: pigpio is required for servo control but is not available or failed to connect.")
         print("Please ensure pigpio is installed and the daemon is running ('sudo systemctl start pigpiod').")
         # Optionally show a GUI error message before exiting
         root = Tk()
         root.withdraw() # Hide main window
         messagebox.showerror("Startup Error", "pigpio library/daemon failed. Servo control disabled. Application cannot start.")
         root.destroy()
         sys.exit(1)

    # Start the Tkinter GUI
    root = Tk()
    app = SmartGateApp(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received. Shutting down...")
        app.on_closing() # Trigger graceful shutdown
    except Exception as e:
        logger.log_error(e, "Unhandled exception in main loop", severity="CRITICAL")
        # Attempt graceful shutdown even on error
        try:
             app.on_closing()
        except Exception as shutdown_e:
             logger.log_error(shutdown_e, "Exception during error shutdown sequence", severity="CRITICAL")
        # Optionally re-raise the exception
        # raise e
    finally:
        print("Application finished.")

