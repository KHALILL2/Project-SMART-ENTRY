#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Smart Gate Access Control System with NFC and Enhanced Servo Control using pigpio

Version: 2.0 (Corrected)

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
4.  **Dependencies:** nfcpy, ndeflib, cryptography, keyring (install via pip if needed: `pip3 install nfcpy ndeflib cryptography keyring`). Ensure nfcpy/ndeflib are correctly placed if not installed via pip.
5.  **Configuration:** Adjust `config.ini` (created on first run) especially the `[servo_pigpio]` section with correct pulse widths for your servo.
"""

from typing import Optional, Dict, Any, List, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum, auto
import sqlite3
import smtplib
import logging
import threading
import time
import sys
import os
import configparser
import keyring # type: ignore # Ignore type checking for keyring if stubs are missing
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler

# --- Dependency Imports with Fallbacks ---

# Add nfcpy and ndeflib module paths and disable USB driver
# Adjust these paths if nfcpy/ndeflib are not installed system-wide via pip
NFCPY_PATH = 
NDEF_PATH = 
if NFCPY_PATH and os.path.exists(NFCPY_PATH):
    sys.path.insert(0, NFCPY_PATH)
if NDEF_PATH and os.path.exists(NDEF_PATH):
    sys.path.insert(0, NDEF_PATH)

os.environ[
# Try importing nfcpy
try:
    import nfc # type: ignore
    from nfc.clf import RemoteTarget # type: ignore
    # import ndef # ndef might not be directly needed if only reading UID
    print(
    NFC_AVAILABLE = True
except ImportError as e:
    print(f
    NFC_AVAILABLE = False

# pigpio for Servo Control (Mandatory for this version)
try:
    import pigpio # type: ignore
    PIGPIO_AVAILABLE = True
    print(
except ImportError:
    print(
    print(
    PIGPIO_AVAILABLE = False
    # Exit if pigpio is essential and not found
    # sys.exit(1) # Uncomment to make pigpio strictly required

# RPi.GPIO for other components (LEDs, Buzzer, Solenoid)
try:
    import RPi.GPIO as GPIO # type: ignore
    GPIO_AVAILABLE = True
    print(
except (ImportError, RuntimeError):
    print(
    class MockGPIO:
        BCM = 11
        OUT = 1
        IN = 0 # Added IN for completeness
        LOW = 0
        HIGH = 1
        PUD_UP = 22 # Added pull-up/down if needed
        PUD_DOWN = 21
        FALLING = 32 # Added edge detection if needed
        RISING = 31
        BOTH = 33

        def __init__(self):
            self._pin_setup = {}

        def setmode(self, mode):
            print(f
        def setup(self, pin, mode, initial=LOW, pull_up_down=None):
            self._pin_setup[pin] = {
            print(f
        def output(self, pin, state):
            if pin not in self._pin_setup or self._pin_setup[pin][
                print(f
                return
            print(f
        def input(self, pin):
             if pin not in self._pin_setup or self._pin_setup[pin][
                print(f
                return 0 # Return default low
             print(f
             return 0 # Simulate low input
        def cleanup(self, pin=None):
            if pin:
                if pin in self._pin_setup:
                    del self._pin_setup[pin]
                print(f
            else:
                self._pin_setup = {}
                print(f
        def setwarnings(self, state):
             print(f
        # Add dummy PWM methods if needed by other parts of the original code
        def PWM(self, pin, freq):
            print(f
            class MockPWM:
                def start(self, duty):
                    print(f
                def ChangeDutyCycle(self, duty):
                    print(f
                def stop(self):
                    print(f
            return MockPWM()

    GPIO = MockGPIO()
    GPIO_AVAILABLE = False

# Standard Library Imports
from tkinter import Tk, Label, Button, messagebox, Entry, Toplevel, Text, END, Scrollbar, Frame, Canvas, Checkbutton, BooleanVar, StringVar
from tkinter import ttk
import tkinter as tk
# Consider adding tkinter.font for specific font control
# from tkinter import font as tkFont
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
    ERROR = auto() # Added error status

@dataclass
class CardInfo:
    id: str
    name: Optional[str] = None
    expiry_date: Optional[datetime] = None
    is_valid: bool = True
    last_access: Optional[datetime] = None
    # Add other fields from database
    faculty: Optional[str] = None
    program: Optional[str] = None
    level: Optional[str] = None
    student_id: Optional[str] = None
    email: Optional[str] = None
    photo_path: Optional[str] = None
    is_blacklisted: bool = False # Added missing field
    access_count: int = 0

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
    # Keep the existing ProfessionalLogger class as is
    # Ensure it handles different severity levels properly
    def __init__(self, log_dir: str = "logs") -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.logger = logging.getLogger(
        self.logger.setLevel(logging.INFO)
        # Prevent duplicate handlers if logger is re-initialized
        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        # System Log File Handler
        file_handler = RotatingFileHandler(
            self.log_dir / 
            maxBytes=10*1024*1024, # 10 MB
            backupCount=5,
            encoding=
        )
        file_handler.setFormatter(logging.Formatter(
            
        ))
        self.logger.addHandler(file_handler)

        # Audit Log File Handler
        audit_logger = logging.getLogger(
        audit_logger.setLevel(logging.INFO)
        if audit_logger.hasHandlers():
            audit_logger.handlers.clear()
        audit_handler = RotatingFileHandler(
            self.log_dir / 
            maxBytes=5*1024*1024, # 5 MB
            backupCount=3,
            encoding=
        )
        audit_handler.setFormatter(logging.Formatter(
            
        ))
        audit_logger.addHandler(audit_handler)
        self.audit_logger = audit_logger

        # Console Handler (for immediate feedback)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO) # Or DEBUG for more verbosity
        console_handler.setFormatter(logging.Formatter(
            
        ))
        self.logger.addHandler(console_handler)

        # Initialize metrics and log queue
        self.metrics = SystemMetrics()
        self.start_time = datetime.now()
        self.log_queue = queue.Queue(maxsize=200) # Increased queue size

    def log_access(self, card_info: CardInfo, status: AccessStatus, response_time: float) -> None:
        log_data = {
            
            
            
            
            
            
        }
        # Use structured logging if possible (e.g., JSON)
        # msg = json.dumps(log_data)
        # self.logger.info(msg)
        # For queue, keep it simple text
        self._queue_log(f
        self._update_metrics(status, response_time)

    def log_error(self, error: BaseException, context: str = "", severity: str = "ERROR") -> None:
        tb_string = traceback.format_exc()
        error_info = {
            
            
            
            
            
            
        }
        # msg = json.dumps(error_info)
        level = getattr(logging, severity.upper(), logging.ERROR)
        self.logger.log(level, f
        self._queue_log(f

    def log_audit(self, action: str, details: Optional[Dict[str, Any]] = None) -> None:
        if details is None:
            details = {}
        audit_data = {
            
            
            
        }
        # msg = json.dumps(audit_data)
        # self.audit_logger.info(msg)
        self.audit_logger.info(f
        self._queue_log(f

    def log_info(self, message: str) -> None:
        self.logger.info(message)
        self._queue_log(f

    def log_warning(self, message: str, context: str = "") -> None:
        self.logger.warning(f
        self._queue_log(f

    def _queue_log(self, message: str) -> None:
        
        try:
            # Non-blocking put
            self.log_queue.put_nowait(message)
        except queue.Full:
            # Handle full queue - maybe log a warning to main log?
            # self.logger.warning(
            pass # Silently ignore for now
        except Exception as e:
            # Log unexpected queue error
            self.logger.error(f

    def get_recent_logs(self, max_logs=50) -> List[str]:
        
        logs = []
        count = 0
        # Get logs without blocking indefinitely
        while not self.log_queue.empty() and count < max_logs:
            try:
                logs.append(self.log_queue.get_nowait())
                self.log_queue.task_done() # Mark task as done
                count += 1
            except queue.Empty:
                break
            except Exception as e:
                 self.logger.error(f
                 break
        return logs

    def _update_metrics(self, status: AccessStatus, response_time: float) -> None:
        self.metrics.total_requests += 1
        if status == AccessStatus.GRANTED:
            self.metrics.successful_accesses += 1
        else:
            # Count all non-granted as failed for simplicity here
            self.metrics.failed_accesses += 1

        total_req = self.metrics.total_requests
        if total_req > 0:
            # Avoid potential division by zero if total_req becomes 0 somehow
            # Use stable running average calculation
            self.metrics.average_response_time = (
                self.metrics.average_response_time * (total_req - 1) + response_time
            ) / total_req

        self.metrics.system_uptime = (datetime.now() - self.start_time).total_seconds()
        self.metrics.last_health_check = datetime.now()
        # CPU temp is updated by the monitor thread

    def _get_current_metrics(self) -> Dict[str, Any]:
        # Ensure metrics are accessed atomically if needed, though less critical here
        # CPU temp is read by monitor thread and updated in self.metrics
        return {
            
            
            
            
            
            
            
        }

# --- Initialize Logger --- 
logger = ProfessionalLogger()

# --- Configuration Management ---

class Config:
    DEFAULT_VALID_PINS = [2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27] # Most usable GPIO pins
    DEFAULT_THERMAL_FILE = 
    CONFIG_FILE = 

    def __init__(self):
        self.config = configparser.ConfigParser()
        try:
            # Set restrictive permissions for config file creation
            os.umask(0o077)
        except Exception as e:
            logger.log_error(e, 

        if not os.path.exists(self.CONFIG_FILE):
            self._create_default_config()
            logger.log_info(f
        else:
            # Ensure existing config file has secure permissions
            try:
                if os.stat(self.CONFIG_FILE).st_mode & 0o077:
                     logger.log_warning(f
                     # Optionally attempt to fix: os.chmod(self.CONFIG_FILE, 0o600)
            except Exception as e:
                 logger.log_error(e, f

        try:
            self.config.read(self.CONFIG_FILE)
        except configparser.Error as e:
             logger.log_error(e, f
             # Handle error: exit, use defaults, etc.
             sys.exit(1)

        # Load settings with fallbacks
        self._load_settings()

    def _load_settings(self):
        # Email
        try:
            self.EMAIL_USER = keyring.get_password(
            self.EMAIL_PASS = keyring.get_password(
        except Exception as e:
            # Log as warning, not critical if email isn't used
            logger.log_warning(f
            self.EMAIL_USER = None
            self.EMAIL_PASS = None
        self.EMAIL_HOST = self.config.get(
        self.EMAIL_PORT = self.config.getint(
        self.EMAIL_USE_TLS = self.config.getboolean(
        self.EMAIL_RECIPIENT = self.config.get(

        # GPIO Pins (Non-Servo)
        self.VALID_PINS = self._parse_list(self.config.get(
        self.FAN_PIN = self._validate_pin(self.config.getint(
        self.BUZZER_PIN = self._validate_pin(self.config.getint(
        self.SOLENOID_PIN = self._validate_pin(self.config.getint(
        self.LED_GREEN_PIN = self._validate_pin(self.config.getint(
        self.LED_RED_PIN = self._validate_pin(self.config.getint(

        # Servo Pin (Used by pigpio)
        self.SERVO_PIN = self._validate_pin(self.config.getint(

        # Servo Settings (pigpio - Pulse Widths in microseconds)
        # Define safe min/max pulse widths
        SERVO_MIN_PULSE = 500
        SERVO_MAX_PULSE = 2500
        self.SERVO_OPEN_PULSE_WIDTH = self.config.getint(
        self.SERVO_CLOSE_PULSE_WIDTH = self.config.getint(
        self.SERVO_DELAY = max(0.1, self.config.getfloat(
        # Validate pulse widths against safe range
        self.SERVO_OPEN_PULSE_WIDTH = max(SERVO_MIN_PULSE, min(SERVO_MAX_PULSE, self.SERVO_OPEN_PULSE_WIDTH))
        self.SERVO_CLOSE_PULSE_WIDTH = max(SERVO_MIN_PULSE, min(SERVO_MAX_PULSE, self.SERVO_CLOSE_PULSE_WIDTH))

        # Temperature Fan Control
        self.FAN_ON_TEMP = min(max(30.0, self.config.getfloat(
        self.FAN_OFF_TEMP = min(max(25.0, self.config.getfloat(
        self.THERMAL_FILE = self.config.get(

        # NFC Reader Settings
        self.NFC_MAX_ATTEMPTS = self.config.getint(
        self.NFC_TIMEOUT = self.config.getint(
        self.NFC_PROTOCOL = self.config.get(

        # Database Settings
        self.DB_PATH = self.config.get(
        self.DB_ENCRYPTED = self.config.getboolean(

        # Performance/UI Settings
        self.GUI_UPDATE_INTERVAL = max(50, self.config.getint(

    def _create_default_config(self):
        default_config = configparser.ConfigParser()
        default_config[
            
            
            
            
        }
        default_config[
            
            
            
            
            
            
        }
        # --- NEW Servo Section for pigpio --- 
        default_config[
            
            
            
            
            
            
            
        }
        # --- Old [servo] section REMOVED --- 
        default_config[
            
            
            
        }
        default_config[
            
            
            
        }
        default_config[
            
            
        }
        default_config[
            
        }
        try:
            with open(self.CONFIG_FILE, 
                default_config.write(configfile)
            # Set permissions explicitly after creation
            os.chmod(self.CONFIG_FILE, 0o600) # Read/write only for owner
            logger.log_info(f
        except Exception as e:
            logger.log_error(e, f

    def _parse_list(self, list_str: str, item_type: type) -> list:
        try:
            list_str = list_str.strip(
            # Filter out empty strings that might result from extra commas
            items = [item.strip() for item in list_str.split(
            return [item_type(item) for item in items]
        except Exception as e:
            logger.log_error(e, f
            return [] # Return empty list on error

    def _validate_pin(self, pin: int) -> int:
        # Pin should already be int due to getint
        if pin in self.VALID_PINS:
            return pin
        else:
            # Log clearly which pin is invalid and the fallback
            fallback_pin = 18 # Define a consistent fallback (or choose based on context)
            logger.log_error(ValueError(f
            return fallback_pin

# --- Config Validation (Optional but Recommended) ---
class ConfigValidator:
    @staticmethod
    def validate_config(config_obj: Config) -> bool:
        issues = []
        try:
            # Email (Optional user/pass check)
            if not config_obj.EMAIL_HOST or not config_obj.EMAIL_PORT:
                issues.append(
            # if config_obj.EMAIL_USER is None or config_obj.EMAIL_PASS is None:
            #     logger.log_info(
            if not config_obj.EMAIL_RECIPIENT:
                 logger.log_warning(

            # GPIO Pins (Check if they are in the valid list)
            # Use sets for efficient checking
            valid_pins_set = set(config_obj.VALID_PINS)
            gpio_pins_to_check = {
                
                
                
                
                
                
            }
            used_pins = set()
            for name, pin in gpio_pins_to_check.items():
                if pin not in valid_pins_set:
                    issues.append(f
                if pin in used_pins:
                    issues.append(f
                used_pins.add(pin)

            # Servo Pulse Widths
            if not (500 <= config_obj.SERVO_OPEN_PULSE_WIDTH <= 2500):
                 issues.append(f
            if not (500 <= config_obj.SERVO_CLOSE_PULSE_WIDTH <= 2500):
                 issues.append(f
            if config_obj.SERVO_OPEN_PULSE_WIDTH == config_obj.SERVO_CLOSE_PULSE_WIDTH:
                 issues.append(
            if config_obj.SERVO_DELAY <= 0:
                issues.append(f

            # Temperature
            if config_obj.FAN_ON_TEMP <= config_obj.FAN_OFF_TEMP:
                issues.append(
            if not os.path.exists(config_obj.THERMAL_FILE):
                # Log as warning, might not be critical if fan control isn't used
                logger.log_warning(f

            # NFC
            if config_obj.NFC_MAX_ATTEMPTS < 1 or config_obj.NFC_TIMEOUT < 1:
                issues.append(

            # Database
            if not config_obj.DB_PATH:
                issues.append(
            else:
                # Check if DB directory exists and is writable
                db_dir = os.path.dirname(os.path.abspath(config_obj.DB_PATH))
                if db_dir:
                    if not os.path.exists(db_dir):
                        try:
                            os.makedirs(db_dir)
                            logger.log_info(f
                        except Exception as e:
                            issues.append(f
                    elif not os.access(db_dir, os.W_OK):
                         issues.append(f

            if issues:
                logger.log_error(
                for issue in issues:
                    logger.log_error(f
                return False
            else:
                logger.log_info(
                return True

        except Exception as e:
            logger.log_error(e, 
            return False

# --- Initialize Config --- 
config = Config()
if not ConfigValidator.validate_config(config):
    # Decide whether to exit or continue with potential issues
    logger.log_error(RuntimeError(
    # Optional: Allow running with defaults? Risky.
    # print(
    sys.exit(1)

# --- pigpio Instance --- 
# Global or passed to relevant classes
pigpio_instance: Optional[pigpio.pi] = None
if PIGPIO_AVAILABLE:
    try:
        pigpio_instance = pigpio.pi() # Connect to the daemon
        if not pigpio_instance.connected:
            # Raise specific error if connection fails
            raise RuntimeError(
        else:
            print(
            logger.log_info(
    except Exception as e:
        logger.log_error(e, 
        PIGPIO_AVAILABLE = False
        pigpio_instance = None
        print(
        # If pigpio is strictly required, exit here
        # messagebox.showerror(
        # sys.exit(1)

# --- Authentication --- 
class Authenticator:
    # Keep the existing Authenticator class as is
    # Ensure error handling is robust
    SERVICE_NAME = 
    ADMIN_USER_KEY = 
    ADMIN_PASS_KEY = 

    @staticmethod
    def setup_credentials_interactively():
        try:
            # Check if keyring is available and usable
            try:
                # Test keyring access
                keyring.get_password(Authenticator.SERVICE_NAME, 
            except Exception as e:
                print(f
                logger.log_error(e, 
                # Optionally fallback to less secure method or inform user
                return # Abort setup if keyring fails

            # Check if admin user already exists
            if keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY):
                print(
                return

            print(
            username = input(
            password = input(
            # Add validation for username/password complexity if desired
            if username and password:
                keyring.set_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY, username)
                keyring.set_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_PASS_KEY, password)
                print(
                logger.log_audit(
            else:
                print(
                logger.log_audit(

        except Exception as e:
            logger.log_error(e, 
            print(f

    @staticmethod
    def authenticate(parent_window=None): # Allow passing parent for modality
        try:
            stored_user = keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY)
            stored_pass = keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_PASS_KEY)
        except Exception as e:
            logger.log_error(e, 
            messagebox.showerror(
            return False

        if not stored_user or not stored_pass:
            messagebox.showerror(
            logger.log_audit(
            return False

        # Create a new Toplevel window for authentication
        auth_window = Toplevel(parent_window) # Make it a child of parent if provided
        auth_window.title(
        auth_window.geometry(
        auth_window.resizable(False, False)
        # Ensure it appears on top
        auth_window.transient(parent_window)
        auth_window.grab_set()  # Make window modal

        Label(auth_window, text=
        user_entry = Entry(auth_window, width=30)
        user_entry.pack()
        Label(auth_window, text=
        pass_entry = Entry(auth_window, show=
        pass_entry.pack()

        status_label = Label(auth_window, text=
        status_label.pack(pady=(5,0))

        attempts = 3
        # Use a mutable type like list or dict for the flag
        # Or use a dedicated class variable if preferred
        auth_result = {

        def check_credentials():
            nonlocal attempts
            username = user_entry.get()
            password = pass_entry.get()

            # Basic input validation
            if not username or not password:
                status_label.config(text=
                return

            if username == stored_user and password == stored_pass:
                auth_result[
                logger.log_audit(
                auth_window.destroy()
            else:
                attempts -= 1
                logger.log_audit(
                if attempts > 0:
                    status_label.config(text=f
                    # Optionally clear password field
                    pass_entry.delete(0, END)
                else:
                    status_label.config(text=
                    logger.log_audit(
                    messagebox.showerror(
                    auth_result[
                    auth_window.destroy()

        login_button = Button(auth_window, text=
        login_button.pack(pady=10)

        # Allow pressing Enter to login
        auth_window.bind(
        user_entry.focus_set() # Set focus to username field

        # Center the window relative to the parent (optional)
        if parent_window:
            auth_window.update_idletasks()
            parent_x = parent_window.winfo_x()
            parent_y = parent_window.winfo_y()
            parent_width = parent_window.winfo_width()
            parent_height = parent_window.winfo_height()
            win_width = auth_window.winfo_width()
            win_height = auth_window.winfo_height()
            x = parent_x + (parent_width // 2) - (win_width // 2)
            y = parent_y + (parent_height // 2) - (win_height // 2)
            auth_window.geometry(f

        # Wait for the window to be closed (either by success, failure, or user closing it)
        auth_window.wait_window()
        return auth_result[

# --- Database Management ---
class CardDatabase:
    # Keep the existing CardDatabase class
    # Ensure all methods handle potential None values and decryption errors
    def __init__(self, db_path: str, encrypted: bool = True) -> None:
        self.db_path = db_path
        self.encrypted = encrypted
        self.key: Optional[bytes] = None
        self.cipher: Optional[Fernet] = None
        self.card_cache: Dict[str, Dict[str, Any]] = {} # Simple cache
        self.cache_size = 20 # Max items in cache
        self._db_lock = threading.Lock() # Lock for thread safety

        if self.encrypted:
            if not self._setup_encryption():
                logger.log_error(RuntimeError(
                # Decide: Exit or continue with encryption disabled?
                # self.encrypted = False
                # sys.exit(1)

        if not self._setup_database():
             logger.log_error(RuntimeError(
             sys.exit(1)

        # Consider if demo data is appropriate for a production system
        # self._add_demo_data()

    def _get_connection(self) -> Optional[sqlite3.Connection]:
        """Gets a thread-safe connection to the database."""
        try:
            # isolation_level=None for autocommit, or handle transactions explicitly
            conn = sqlite3.connect(self.db_path, timeout=10)
            return conn
        except sqlite3.Error as e:
            logger.log_error(e, 
            return None

    def _setup_encryption(self) -> bool:
        try:
            key_file = Path(
            if not key_file.exists():
                self.key = Fernet.generate_key()
                with open(key_file, 
                    f.write(self.key)
                os.chmod(key_file, 0o600) # Read/write only for owner
                logger.log_info(
            else:
                # Ensure key file has correct permissions
                if os.stat(key_file).st_mode & 0o077:
                     logger.log_warning(
                     # Optionally: Attempt to fix permissions? os.chmod(key_file, 0o600)
                with open(key_file, 
                    self.key = f.read()
                logger.log_info(

            if self.key:
                self.cipher = Fernet(self.key)
                return True
            else:
                 logger.log_error(RuntimeError(
                 return False
        except (FileNotFoundError, PermissionError, InvalidToken) as e:
             logger.log_error(e, 
             return False
        except Exception as e:
            logger.log_error(e, 
            return False

    def _setup_database(self) -> bool:
        with self._db_lock:
            conn = self._get_connection()
            if not conn:
                return False
            try:
                cursor = conn.cursor()
                # Use TEXT for dates, INTEGER for boolean
                cursor.execute(
                # Add necessary indices
                cursor.execute(
                cursor.execute(
                # Consider adding indices for other frequently searched fields if needed
                conn.commit()
                logger.log_info(
                return True
            except sqlite3.Error as e:
                logger.log_error(e, 
                conn.rollback()
                return False
            except Exception as e:
                 logger.log_error(e, 
                 conn.rollback()
                 return False
            finally:
                conn.close()

    # Demo data might be better handled by a separate setup script
    # def _add_demo_data(self) -> None: ...

    def _encrypt(self, data: Optional[str]) -> Optional[str]:
        if not self.encrypted or not self.cipher or data is None:
            return data
        try:
            # Ensure data is string before encoding
            return self.cipher.encrypt(str(data).encode()).decode()
        except Exception as e:
            logger.log_warning(f
            return None # Or return original data? Returning None might be safer.

    def _decrypt(self, data: Optional[str]) -> Optional[str]:
        if not self.encrypted or not self.cipher or data is None:
            return data
        try:
            # Data from DB should be string, encode to bytes for decryption
            return self.cipher.decrypt(data.encode()).decode()
        except InvalidToken:
            logger.log_error(InvalidToken(
            return 
        except Exception as e:
            logger.log_error(e, 
            return 

    def add_or_update_card(self, card_data: Dict[str, Any]) -> bool:
        required_fields = [
        if not all(field in card_data and card_data[field] is not None for field in required_fields):
            logger.log_error(ValueError(f
            return False

        with self._db_lock:
            conn = self._get_connection()
            if not conn:
                return False
            try:
                cursor = conn.cursor()

                # Prepare data, encrypting sensitive fields
                db_data = card_data.copy()
                # Encrypt only if encryption is enabled and successful
                if self.encrypted and self.cipher:
                    db_data[
                    db_data[
                    db_data[
                    db_data[
                    db_data[
                    db_data[
                    db_data[

                # Ensure boolean/integer fields are correct type (0 or 1)
                db_data[
                db_data[
                db_data[

                # Define columns explicitly for clarity and safety
                columns = [
                           
                           
                           
                           
                           
                placeholders = 
                sql = f
                
                # Create tuple in correct order, handling missing optional fields with None
                values = tuple(db_data.get(col) for col in columns)

                cursor.execute(sql, values)
                conn.commit()

                logger.log_audit(

                # Invalidate cache for this card
                card_id = card_data[
                if card_id in self.card_cache:
                    del self.card_cache[card_id]

                return True
            except sqlite3.Error as e:
                logger.log_error(e, f
                conn.rollback()
                return False
            except Exception as e:
                logger.log_error(e, f
                conn.rollback()
                return False
            finally:
                conn.close()

    def get_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        if not card_id:
             return None

        # Check cache first (thread-safe access not strictly needed for read if updates invalidate)
        if card_id in self.card_cache:
            # Return a copy to prevent external modification of cache
            return self.card_cache[card_id].copy()

        # No lock needed for read operation if acceptable to get slightly stale data
        # For guaranteed consistency, add lock: with self._db_lock:
        conn = self._get_connection()
        if not conn:
            return None
        try:
            conn.row_factory = sqlite3.Row # Return results as dict-like rows
            cursor = conn.cursor()
            cursor.execute(
            row = cursor.fetchone()

            if not row:
                return None

            card_data = dict(row)

            # Decrypt sensitive fields
            if self.encrypted and self.cipher:
                card_data[
                card_data[
                card_data[
                card_data[
                card_data[
                card_data[
                card_data[
            else:
                 # If not encrypted, ensure fields still exist
                 card_data.setdefault(
                 card_data.setdefault(
                 # ... set defaults for other encrypted fields ...

            # Convert integer fields back to boolean if needed by application logic
            card_data[
            card_data[

            # Update cache (outside lock if read lock not used)
            # Basic cache size management
            if len(self.card_cache) >= self.cache_size:
                # Simple FIFO cache eviction: remove the first added item
                try:
                    oldest_key = next(iter(self.card_cache))
                    del self.card_cache[oldest_key]
                except StopIteration:
                    pass # Cache was empty
            self.card_cache[card_id] = card_data.copy() # Store a copy

            return card_data

        except sqlite3.Error as e:
            logger.log_error(e, f
            return None
        except Exception as e:
            logger.log_error(e, f
            return None
        finally:
            conn.close()

    def update_card_status(self, card_id: str, is_valid: Optional[bool] = None, is_blacklisted: Optional[bool] = None) -> bool:
        if not card_id or (is_valid is None and is_blacklisted is None):
            logger.log_warning(
            return False
        
        with self._db_lock:
            conn = self._get_connection()
            if not conn:
                return False
            try:
                cursor = conn.cursor()
                updates = []
                params = []
                log_details: Dict[str, Any] = {
                if is_valid is not None:
                    updates.append(
                    params.append(int(is_valid))
                    log_details[
                if is_blacklisted is not None:
                    updates.append(
                    params.append(int(is_blacklisted))
                    log_details[
                
                if not updates:
                     return False # Should not happen based on initial check

                params.append(card_id)
                sql = f
                
                cursor.execute(sql, tuple(params))
                conn.commit()
                
                if cursor.rowcount > 0:
                    logger.log_audit(
                    # Invalidate cache
                    if card_id in self.card_cache:
                        del self.card_cache[card_id]
                    return True
                else:
                    # Card ID might not exist
                    logger.log_warning(f
                    return False
                    
            except sqlite3.Error as e:
                logger.log_error(e, f
                conn.rollback()
                return False
            except Exception as e:
                logger.log_error(e, f
                conn.rollback()
                return False
            finally:
                conn.close()

    def record_access(self, card_id: str) -> bool:
        if not card_id:
            return False

        with self._db_lock:
            conn = self._get_connection()
            if not conn:
                return False
            try:
                cursor = conn.cursor()
                current_time_iso = datetime.now().isoformat()
                # Update last access time and increment access count
                cursor.execute(
                    
                    (current_time_iso, card_id)
                )
                conn.commit()

                if cursor.rowcount > 0:
                    # Update cache if exists (optional, depends if last_access/count is needed often)
                    if card_id in self.card_cache:
                        self.card_cache[card_id][
                        self.card_cache[card_id][
                    return True
                else:
                    # Card might have been deleted between check and update
                    logger.log_warning(f
                    return False

            except sqlite3.Error as e:
                logger.log_error(e, f
                conn.rollback()
                return False
            except Exception as e:
                logger.log_error(e, f
                conn.rollback()
                return False
            finally:
                conn.close()

    def get_all_cards(self) -> List[Dict[str, Any]]:
        # No lock needed for read if slightly stale data is acceptable
        conn = self._get_connection()
        if not conn:
            return []
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Order results for consistent display
            cursor.execute(
            rows = cursor.fetchall()
            cards = []
            for row in rows:
                card_data = dict(row)
                # Decrypt sensitive fields
                if self.encrypted and self.cipher:
                    card_data[
                    card_data[
                    card_data[
                    card_data[
                    card_data[
                    card_data[
                    card_data[
                
                # Convert integer fields back to boolean
                card_data[
                card_data[
                cards.append(card_data)
            return cards
        except sqlite3.Error as e:
            logger.log_error(e, 
            return []
        except Exception as e:
            logger.log_error(e, 
            return []
        finally:
            conn.close()

    def delete_card(self, card_id: str) -> bool:
        if not card_id:
            return False
        
        with self._db_lock:
            conn = self._get_connection()
            if not conn:
                return False
            try:
                cursor = conn.cursor()
                cursor.execute(
                conn.commit()
                
                if cursor.rowcount > 0:
                    logger.log_audit(
                    # Remove from cache
                    if card_id in self.card_cache:
                        del self.card_cache[card_id]
                    return True
                else:
                    # Card didn't exist
                    logger.log_warning(f
                    return False
                    
            except sqlite3.Error as e:
                logger.log_error(e, f
                conn.rollback()
                return False
            except Exception as e:
                logger.log_error(e, f
                conn.rollback()
                return False
            finally:
                conn.close()

# --- NFC Reader Logic ---
class NFCReader:
    # Keep the existing NFCReader class
    # Ensure robust error handling and re-initialization
    def __init__(self, config_obj: Config) -> None:
        self.config = config_obj
        self.clf: Optional[nfc.clf.RemoteTarget] = None # Correct type hint
        self.connected = False
        self.stop_event = threading.Event()
        self.card_detected_event = threading.Event()
        self.card_id: Optional[str] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.mock_mode = not NFC_AVAILABLE
        self.last_error_time: Optional[float] = None
        self.error_retry_delay = 5 # Seconds
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5 # Switch to mock after this many errors

        if not self.mock_mode:
            # Initial attempt to connect
            self._initialize_reader()
        else:
            logger.log_info(

    def _initialize_reader(self) -> bool:
        if self.mock_mode:
            return False
        try:
            # Try initializing on common paths or specific path from config if added
            # Example: path = self.config.NFC_PATH or 'usb'
            path = 
            logger.log_info(f
            # Ensure previous clf is closed if attempting re-initialization
            if self.clf:
                try: self.clf.close();
                except: pass
                self.clf = None
                self.connected = False
                
            self.clf = nfc.ContactlessFrontend(path)
            if self.clf:
                # Test connection?
                # print(self.clf) # May block or raise error
                logger.log_info(f
                self.connected = True
                self.last_error_time = None # Reset error time on success
                self.consecutive_errors = 0
                return True
            else:
                # This case might not happen if ContactlessFrontend raises error on failure
                logger.log_error(RuntimeError(f
                self.connected = False
                self.last_error_time = time.time()
                self.consecutive_errors += 1
                return False
        except Exception as e:
            # Log the specific exception
            logger.log_error(e, f
            self.connected = False
            self.last_error_time = time.time()
            self.consecutive_errors += 1
            # Check if we should switch to mock mode
            if self.consecutive_errors >= self.max_consecutive_errors:
                 logger.log_error(RuntimeError(f
                 self.mock_mode = True
            return False

    def start_reading(self) -> None:
        if self.reader_thread and self.reader_thread.is_alive():
            logger.log_info(
            return

        logger.log_info(f
        self.stop_event.clear()
        self.card_detected_event.clear()
        self.card_id = None

        if self.mock_mode:
            self.reader_thread = threading.Thread(target=self._mock_reader_loop, daemon=True)
        else:
            self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)

        self.reader_thread.start()

    def stop_reading(self) -> None:
        logger.log_info(
        self.stop_event.set()
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=2.0)
            if self.reader_thread.is_alive():
                 logger.log_warning(

        # Close the NFC connection if open
        if self.clf and self.connected:
            try:
                self.clf.close()
                logger.log_info(
            except Exception as e:
                logger.log_error(e, 
        self.connected = False
        self.clf = None
        self.reader_thread = None

    def _reader_loop(self) -> None:
        poll_interval = 0.2 # Seconds between polls
        poll_iterations = 3 # Number of iterations per connect attempt
        
        while not self.stop_event.is_set():
            if not self.connected:
                # Wait before retrying initialization after an error
                if self.last_error_time and (time.time() - self.last_error_time < self.error_retry_delay):
                    time.sleep(1) # Short sleep while waiting
                    continue
                logger.log_info(
                if not self._initialize_reader():
                    # Initialization failed, wait longer before next attempt
                    time.sleep(self.error_retry_delay)
                    # If switched to mock mode during init, break loop
                    if self.mock_mode:
                        logger.log_info(
                        break
                    continue # Skip to next loop iteration

            # If connected, start polling
            try:
                # Define target types based on config or common types
                # Example: Type A (MIFARE), Type F (FeliCa), Type B
                # Adjust protocol based on config
                target = RemoteTarget(f
                
                # Poll for cards using connect with timeout
                # The rdwr={'on-connect': lambda tag: False} makes connect return the tag immediately
                # interval * iterations gives the effective timeout for this connect call
                tag = self.clf.connect(rdwr={
                                      targets=[target], # Pass target object
                                      interval=poll_interval,
                                      iterations=poll_iterations)

                if tag and tag.identifier:
                    card_id_bytes = tag.identifier
                    self.card_id = card_id_bytes.hex().upper()
                    logger.log_info(f
                    self.card_detected_event.set() # Signal main thread
                    
                    # Wait briefly AFTER signaling to avoid immediate re-detection by next loop
                    time.sleep(1.5)
                    
                    # Clear event and ID AFTER the sleep
                    self.card_detected_event.clear()
                    self.card_id = None
                    
                    self.consecutive_errors = 0 # Reset errors on successful read
                else:
                    # No tag found in this polling cycle, sleep briefly before next poll
                    # time.sleep(0.1) # Short sleep between polling attempts
                    pass

            except nfc.clf.TimeoutError:
                 # This is expected if no card is present, not really an error
                 # logger.log_info(
                 pass
            except nfc.clf.UnsupportedTargetError as e:
                 logger.log_warning(f
                 # Treat as a minor error, maybe don't increment consecutive_errors?
                 time.sleep(1)
            except Exception as e:
                # Catch broader exceptions (like communication errors, OS errors)
                logger.log_error(e, f
                self.consecutive_errors += 1
                self.connected = False # Assume connection lost on error
                self.last_error_time = time.time()
                if self.clf:
                    try: self.clf.close() # Try to close cleanly
                    except: pass
                    self.clf = None
                time.sleep(1) # Wait after error

                # Check if we should switch to mock mode
                if self.consecutive_errors >= self.max_consecutive_errors:
                    logger.log_error(RuntimeError(f
                    self.mock_mode = True
                    self.stop_event.set() # Stop this thread
                    break # Exit loop

            # Optional small delay to prevent tight loop if connect returns immediately
            # time.sleep(0.05)

        # End of loop
        logger.log_info(f
        self.connected = False
        if self.clf: try: self.clf.close(); except: pass
        # If loop exited due to switching to mock mode, start mock loop
        if self.mock_mode and not self.stop_event.is_set():
             self.start_reading() # Will now start the mock loop

    def _mock_reader_loop(self) -> None:
        logger.log_info(
        mock_ids = [
        idx = 0
        while not self.stop_event.is_set():
            try:
                # Simulate waiting for a card
                wait_time = 5 # Wait 5 seconds
                if self.stop_event.wait(wait_time): # Use wait for faster exit
                    break

                # Simulate detecting a card
                self.card_id = mock_ids[idx % len(mock_ids)]
                logger.log_info(f
                self.card_detected_event.set()
                idx += 1

                # Simulate card removal
                time.sleep(1.5)
                if self.stop_event.is_set(): break
                self.card_detected_event.clear()
                self.card_id = None

            except Exception as e:
                 logger.log_error(e, 
                 time.sleep(2)
        logger.log_info(

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
        self._hardware_lock = threading.Lock() # Lock for operations accessing same hardware

        if self.gpio_available:
            try:
                GPIO.setmode(GPIO.BCM) # Use Broadcom pin numbering
                GPIO.setwarnings(False) # Disable warnings
                # Setup non-servo pins with initial state LOW
                GPIO.setup(self.config.LED_GREEN_PIN, GPIO.OUT, initial=GPIO.LOW)
                GPIO.setup(self.config.LED_RED_PIN, GPIO.OUT, initial=GPIO.LOW)
                GPIO.setup(self.config.BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
                GPIO.setup(self.config.SOLENOID_PIN, GPIO.OUT, initial=GPIO.LOW)
                # Setup Fan pin
                GPIO.setup(self.config.FAN_PIN, GPIO.OUT, initial=GPIO.LOW)
                logger.log_info(
            except Exception as e:
                 logger.log_error(e, 
                 self.gpio_available = False # Disable if setup fails
        else:
             logger.log_info(

        if not self.pigpio_available:
            # Log as critical if servo control is essential
            logger.log_error(RuntimeError(

    def set_led(self, color: str, state: bool) -> None:
        if not self.gpio_available:
            # print(f
            return
        pin = None
        if color == 
            pin = self.config.LED_GREEN_PIN
        elif color == 
            pin = self.config.LED_RED_PIN

        if pin:
            with self._hardware_lock:
                try:
                    GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)
                except Exception as e:
                     logger.log_error(e, f

    def buzz(self, duration: float = 0.1, times: int = 1) -> None:
        if not self.gpio_available:
            # print(f
            return
        with self._hardware_lock:
            try:
                for _ in range(times):
                    GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
                    time.sleep(duration)
                    GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
                    if times > 1:
                        # Short pause between beeps
                        time.sleep(max(0.05, duration * 0.5))
            except Exception as e:
                 logger.log_error(e, f

    def activate_solenoid(self, duration: float = 0.5) -> None:
        if not self.gpio_available:
            # print(f
            return
        with self._hardware_lock:
            try:
                GPIO.output(self.config.SOLENOID_PIN, GPIO.HIGH)
                logger.log_info(f
                # Use a timer event instead of sleep if possible in main app
                time.sleep(duration)
                GPIO.output(self.config.SOLENOID_PIN, GPIO.LOW)
                logger.log_info(f
            except Exception as e:
                 logger.log_error(e, f

    # --- Servo Control using pigpio --- 
    def _move_servo(self, pulse_width: int) -> bool:
        
        if not self.pigpio_available or self.pi is None:
            logger.log_error(RuntimeError(
            print(f
            return False
        
        # Ensure pulse width is within safe range (e.g., 500-2500 us)
        # Use a slightly wider internal range for safety, config validation handles user input
        # 0 is a special value to turn off pulses
        if pulse_width == 0:
             safe_pulse_width = 0
        else:
             safe_pulse_width = max(500, min(2500, pulse_width))
        
        # No lock needed here as pigpio daemon handles concurrent requests
        try:
            self.pi.set_servo_pulsewidth(self.config.SERVO_PIN, safe_pulse_width)
            # logger.log_info(f
            return True
        except Exception as e:
            # Catch specific pigpio exceptions if known
            logger.log_error(e, f
            return False

    def open_gate(self) -> None:
        
        logger.log_info(
        if self._move_servo(self.config.SERVO_OPEN_PULSE_WIDTH):
            time.sleep(self.config.SERVO_DELAY) # Wait for movement
            self._stop_servo() # Stop sending pulses
            logger.log_info(
        else:
             logger.log_error(RuntimeError(

    def close_gate(self) -> None:
        
        logger.log_info(
        if self._move_servo(self.config.SERVO_CLOSE_PULSE_WIDTH):
            time.sleep(self.config.SERVO_DELAY) # Wait for movement
            self._stop_servo() # Stop sending pulses
            logger.log_info(
        else:
             logger.log_error(RuntimeError(

    def _stop_servo(self) -> None:
        
        # Setting pulse width to 0 tells pigpio to stop PWM on that pin
        self._move_servo(0)
        # logger.log_info(f

    # --- Fan Control --- 
    def set_fan(self, state: bool) -> None:
        if not self.gpio_available:
            # print(f
            return
        with self._hardware_lock:
            try:
                GPIO.output(self.config.FAN_PIN, GPIO.HIGH if state else GPIO.LOW)
                # logger.log_info(f
            except Exception as e:
                 logger.log_error(e, f

    def cleanup(self) -> None:
        logger.log_info(
        # Stop servo pulses first
        if self.pigpio_available:
            self._stop_servo()
            # Disconnect from pigpio daemon (handled globally in main app exit)
            # if self.pi: self.pi.stop()

        # Cleanup RPi.GPIO pins
        if self.gpio_available:
            # Turn off devices before cleanup
            try:
                 self.set_led(
                 self.set_led(
                 self.set_fan(False)
                 GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
                 GPIO.output(self.config.SOLENOID_PIN, GPIO.LOW)
            except Exception as e:
                 logger.log_error(e, 
            # Perform GPIO cleanup
            try:
                GPIO.cleanup()
                logger.log_info(
            except Exception as e:
                 logger.log_error(e, 

# --- Temperature Monitoring ---
class TemperatureMonitor(threading.Thread):
    def __init__(self, config_obj: Config, hardware_ctrl: HardwareController, interval: int = 30):
        super().__init__(daemon=True)
        self.config = config_obj
        self.hardware = hardware_ctrl
        self.interval = max(5, interval) # Minimum interval 5 seconds
        self.stop_event = threading.Event()
        self.current_temp = 0.0
        self.fan_state = False
        self._lock = threading.Lock()

    def get_temperature(self) -> float:
        with self._lock:
            return self.current_temp

    def run(self) -> None:
        logger.log_info(
        # Initial check
        self._check_temperature()
        
        while not self.stop_event.wait(self.interval):
            self._check_temperature()
            
        logger.log_info(
        # Ensure fan is turned off on exit
        if self.fan_state:
             logger.log_info(
             self.hardware.set_fan(False)

    def _check_temperature(self):
        try:
            temp = self._read_cpu_temp()
            if temp == -1.0: # Error reading temp
                return
                
            with self._lock:
                self.current_temp = temp
            logger.metrics.cpu_temp = self.current_temp # Update shared metrics
            # logger.log_info(f

            # Fan control logic (check state inside lock? Less critical here)
            current_fan_state = self.fan_state
            new_fan_state = current_fan_state
            
            if temp >= self.config.FAN_ON_TEMP and not current_fan_state:
                logger.log_info(f
                new_fan_state = True
            elif temp <= self.config.FAN_OFF_TEMP and current_fan_state:
                logger.log_info(f
                new_fan_state = False
            
            # Update hardware only if state changes
            if new_fan_state != current_fan_state:
                self.hardware.set_fan(new_fan_state)
                self.fan_state = new_fan_state

        except Exception as e:
            logger.log_error(e, 
            # Avoid continuous errors, maybe sleep longer?
            time.sleep(self.interval)
            
    def stop(self) -> None:
        self.stop_event.set()

    def _read_cpu_temp(self) -> float:
        try:
            # Ensure file exists before opening
            if not os.path.exists(self.config.THERMAL_FILE):
                 raise FileNotFoundError(f
                 
            with open(self.config.THERMAL_FILE, 
                temp_str = f.read().strip()
            # Handle potential non-integer values if file format changes
            return int(temp_str) / 1000.0
        except FileNotFoundError as e:
             # Log only once? Or use warning level
             logger.log_error(e, 
             return -1.0 # Indicate error
        except (ValueError, IndexError, TypeError) as e:
             logger.log_error(e, f
             return -1.0
        except Exception as e:
            logger.log_error(e, 
            return -1.0

# --- Email Notification ---
class EmailNotifier:
    def __init__(self, config_obj: Config):
        self.config = config_obj
        self.enabled = bool(self.config.EMAIL_USER and self.config.EMAIL_PASS and self.config.EMAIL_RECIPIENT)
        if not self.enabled:
            logger.log_info(
        # Use a single thread pool executor for all email sends
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=

    def send_alert(self, subject: str, body: str) -> None:
        if not self.enabled:
            return

        msg = MIMEText(body)
        msg[
        msg[
        msg[

        try:
            logger.log_info(f
            # Submit to executor for non-blocking send
            future = self._executor.submit(self._send_email_sync, msg)
            # Optional: Add callback for logging success/failure
            future.add_done_callback(self._email_sent_callback)

        except Exception as e:
            # Error submitting the task
            logger.log_error(e, 

    def _send_email_sync(self, msg: MIMEText) -> bool:
        
        context = ssl.create_default_context()
        server: Optional[Union[smtplib.SMTP, smtplib.SMTP_SSL]] = None
        try:
            host = self.config.EMAIL_HOST
            port = self.config.EMAIL_PORT
            user = self.config.EMAIL_USER
            password = self.config.EMAIL_PASS
            
            if not all([host, port, user, password]):
                 raise ValueError(
                 
            if self.config.EMAIL_USE_TLS:
                server = smtplib.SMTP(host, port, timeout=20)
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
            else: # Assuming SSL if not TLS
                server = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
            
            server.login(user, password)
            server.send_message(msg)
            # Success logged in callback
            return True
        except smtplib.SMTPAuthenticationError as e:
             logger.log_error(e, 
             # Consider disabling email alerts after repeated auth failures
             # self.enabled = False
             return False
        except Exception as e:
            # Log specific error encountered during sending
            logger.log_error(e, 
            return False
        finally:
            if server: 
                try: server.quit()
                except: pass # Ignore errors during quit

    def _email_sent_callback(self, future: ThreadPoolExecutor):
        """Callback function to log result of email sending."""
        try:
            success = future.result() # Get result (True/False)
            if success:
                 # Need msg details here - maybe pass them along?
                 # For now, generic success message
                 logger.log_info(
            # Failure is logged within _send_email_sync
        except Exception as e:
            # Log exception that occurred during the email sending task
            logger.log_error(e, 

    def shutdown(self):
        """Shutdown the email executor."""
        logger.log_info(
        self._executor.shutdown(wait=True)


# --- Main Application Logic ---
class SmartGateApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title(
        # Make window non-resizable initially, or set min/max size
        # self.root.resizable(False, False)
        self.root.minsize(800, 600) # Set minimum size
        # self.root.geometry(

        # Initialize components
        self.config = config # Use global config
        self.logger = logger # Use global logger
        self.db = CardDatabase(self.config.DB_PATH, self.config.DB_ENCRYPTED)
        self.hardware = HardwareController(self.config, pigpio_instance)
        self.nfc_reader = NFCReader(self.config)
        self.temp_monitor = TemperatureMonitor(self.config, self.hardware)
        self.emailer = EmailNotifier(self.config)

        # Rate limiting for NFC scans
        self.last_scan_times: Dict[str, float] = {}
        self.rate_limit_seconds = 3 # Prevent scanning same card within 3 seconds
        
        # Flag to prevent multiple auto-close calls
        self._auto_close_scheduled = False
        self._auto_close_timer_id: Optional[str] = None

        # GUI Setup
        self._setup_gui()

        # Start background threads
        self.nfc_reader.start_reading()
        self.temp_monitor.start()

        # Start the main application loop (checking NFC)
        # Use root.after for GUI thread safety
        self.root.after(100, self.check_nfc_loop) # Start loop after 100ms

        # Set initial hardware state (e.g., close gate, red LED)
        self.set_initial_state()

        # Setup graceful shutdown
        self.root.protocol(

    def set_initial_state(self):
        """Sets the initial state of LEDs and gate."""
        self.hardware.set_led(
        self.hardware.set_led(
        # Close gate on startup (optional, depends on desired initial state)
        # Consider safety: only close if sure it's safe
        # self.hardware.close_gate()
        # logger.log_info(
        self.update_status(

    def _setup_gui(self):
        # Use ttk for better styling
        style = ttk.Style(self.root)
        # Available themes: clam, alt, default, classic
        # On some systems: aqua, vista, xpnative
        try:
            # Try a theme known to exist on Linux/Windows
            style.theme_use(
        except tk.TclError:
            logger.log_warning(
            style.theme_use(

        # Main frame
        main_frame = ttk.Frame(self.root, padding=
        main_frame.pack(expand=True, fill=tk.BOTH)
        main_frame.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1) # Make right panel expand

        # --- Left Panel: Status & Info ---
        left_panel = ttk.Frame(main_frame, width=300)
        # Use grid layout for better control
        left_panel.grid(row=0, column=0, sticky=
        # left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        # left_panel.pack_propagate(False) # Prevent resizing based on content

        ttk.Label(left_panel, text=

        # Status Label (for NFC scans, etc.)
        self.status_label = ttk.Label(left_panel, text=
        self.status_label.pack(pady=5, anchor=

        # Card Info Display
        ttk.Label(left_panel, text=
        self.card_info_frame = ttk.Frame(left_panel, borderwidth=1, relief=
        self.card_info_frame.pack(fill=tk.X, pady=5)
        self.card_info_label = ttk.Label(self.card_info_frame, text=
        self.card_info_label.pack(anchor=

        # System Metrics Display
        ttk.Label(left_panel, text=
        self.metrics_frame = ttk.Frame(left_panel, borderwidth=1, relief=
        self.metrics_frame.pack(fill=tk.X, pady=5)
        self.metrics_label = ttk.Label(self.metrics_frame, text=
        self.metrics_label.pack(anchor=

        # Manual Control Buttons
        ttk.Label(left_panel, text=
        button_frame = ttk.Frame(left_panel)
        button_frame.pack(fill=tk.X, pady=5)
        self.open_button = ttk.Button(button_frame, text=
        self.open_button.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        self.close_button = ttk.Button(button_frame, text=
        self.close_button.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)

        # --- Right Panel: Logs & Management ---
        right_panel = ttk.Frame(main_frame)
        right_panel.grid(row=0, column=1, sticky=
        # right_panel.pack(side=tk.RIGHT, expand=True, fill=tk.BOTH)
        right_panel.rowconfigure(0, weight=1)
        right_panel.columnconfigure(0, weight=1)

        notebook = ttk.Notebook(right_panel)
        # notebook.pack(expand=True, fill=tk.BOTH, pady=(0, 10))
        notebook.grid(row=0, column=0, sticky=

        # Log Tab
        log_frame = ttk.Frame(notebook, padding=5)
        notebook.add(log_frame, text=
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        # Use tk.Text for better control over content
        self.log_text = Text(log_frame, height=15, width=60, state=tk.DISABLED, wrap=tk.WORD, font=(
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text[
        # Use grid for text and scrollbar
        self.log_text.grid(row=0, column=0, sticky=
        log_scroll.grid(row=0, column=1, sticky=
        # self.log_text.pack(expand=True, fill=tk.BOTH)
        # log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Card Management Tab
        mgmt_frame = ttk.Frame(notebook, padding=5)
        notebook.add(mgmt_frame, text=
        ttk.Label(mgmt_frame, text=
        self.manage_button = ttk.Button(mgmt_frame, text=
        self.manage_button.pack(pady=10)

        # Initial status update
        self.update_status(
        self.update_metrics_display() # Start metrics update loop
        self.update_log_display() # Start log update loop

    def update_status(self, message: str, duration_ms: Optional[int] = None):
        
        self.status_label.config(text=f
        self.logger.log_info(f
        # Optionally reset after a delay
        if duration_ms:
            self.root.after(duration_ms, lambda: self.update_status(

    def update_card_info_display(self, card_info: Optional[CardInfo], access_status: Optional[AccessStatus] = None):
        
        if card_info:
            status_text = access_status.name if access_status else 
            expiry_text = card_info.expiry_date.strftime(
            valid_text = 
            blacklist_text = 
            
            info_str = (
                f
                f
                f
                f
            )
            self.card_info_label.config(text=info_str)
        else:
            # Clear display
            self.card_info_label.config(text=

    def update_metrics_display(self):
        
        # Access metrics via logger instance
        metrics = self.logger._get_current_metrics()
        uptime_seconds = metrics.get(
        uptime_td = timedelta(seconds=int(uptime_seconds))
        # Get latest temp from monitor thread safely
        temp = self.temp_monitor.get_temperature()
        
        metrics_str = (
            f
            f
            f
            f
        )
        self.metrics_label.config(text=metrics_str)
        # Schedule next update using root.after for thread safety
        self.root.after(5000, self.update_metrics_display) # Update every 5 seconds

    def update_log_display(self):
        
        # Get logs from the queue
        logs = self.logger.get_recent_logs(max_logs=10)
        if logs:
            self.log_text.config(state=tk.NORMAL)
            for log_entry in logs:
                # Prepend timestamp?
                # timestamp = datetime.now().strftime(
                # self.log_text.insert(tk.END, f
                self.log_text.insert(tk.END, log_entry + 
            # Limit number of lines? (Optional)
            # num_lines = int(self.log_text.index(
            # if num_lines > 500: 
            #     self.log_text.delete(
            self.log_text.see(tk.END) # Scroll to the bottom
            self.log_text.config(state=tk.DISABLED)
            
        # Schedule next update
        self.root.after(self.config.GUI_UPDATE_INTERVAL, self.update_log_display)

    def check_nfc_loop(self):
        
        # Check if NFC reader is still operational
        if self.nfc_reader.mock_mode and not NFC_AVAILABLE:
             # Optionally update status if NFC failed permanently
             # self.update_status(
             pass # Continue in mock mode
        elif not self.nfc_reader.connected and not self.nfc_reader.mock_mode:
             # Optionally update status if NFC failed temporarily
             # self.update_status(
             pass # Reader thread handles reconnection

        card_id = self.nfc_reader.get_detected_card()
        if card_id:
            start_time = time.time()
            # Rate Limiting Check
            now = time.time()
            last_scan = self.last_scan_times.get(card_id, 0)
            
            if (now - last_scan) < self.rate_limit_seconds:
                # Log but maybe don't update GUI status to avoid flickering
                self.logger.log_info(f
            else:
                self.last_scan_times[card_id] = now
                self.update_status(f
                # Process in a separate thread to avoid blocking GUI?
                # For now, keep it simple, but consider for long DB lookups
                self.process_card_scan(card_id, start_time)

        # Schedule the next check using root.after
        self.root.after(200, self.check_nfc_loop) # Check every 200ms

    def process_card_scan(self, card_id: str, start_time: float):
        
        # Cancel any pending auto-close timer if a new card is scanned
        self.cancel_auto_close()
        
        card_data = self.db.get_card(card_id)
        access_status = AccessStatus.DENIED # Default
        # Create a default CardInfo object for logging/display even if card not found
        card_info_obj = CardInfo(id=card_id)

        if card_data:
            # Populate CardInfo object fully from DB data
            try:
                expiry_dt = datetime.fromisoformat(card_data[
                last_access_dt = datetime.fromisoformat(card_data[
            except (TypeError, ValueError) as e:
                 logger.log_warning(f
                 expiry_dt = None
                 last_access_dt = None
                 
            card_info_obj = CardInfo(
                id=card_id,
                name=card_data.get(
                expiry_date=expiry_dt,
                is_valid=bool(card_data.get(
                last_access=last_access_dt,
                faculty=card_data.get(
                program=card_data.get(
                level=card_data.get(
                student_id=card_data.get(
                email=card_data.get(
                photo_path=card_data.get(
                is_blacklisted=bool(card_data.get(
                access_count=int(card_data.get(
            )

            # --- Access Logic --- 
            if card_info_obj.is_blacklisted:
                access_status = AccessStatus.BLACKLISTED
                self.update_status(f
                self.hardware.set_led(
                self.hardware.buzz(duration=0.1, times=3)
                # Turn red LED off after a short period
                self.root.after(1000, lambda: self.hardware.set_led(
                # Send alert?
                # self.emailer.send_alert(
            elif not card_info_obj.is_valid:
                access_status = AccessStatus.DENIED
                self.update_status(f
                self.hardware.set_led(
                self.hardware.buzz(duration=0.2, times=2)
                self.root.after(1000, lambda: self.hardware.set_led(
            elif card_info_obj.expiry_date and card_info_obj.expiry_date < datetime.now():
                access_status = AccessStatus.DENIED
                self.update_status(f
                self.hardware.set_led(
                self.hardware.buzz(duration=0.2, times=2)
                self.root.after(1000, lambda: self.hardware.set_led(
                # Optionally mark card as invalid in DB
                # if card_info_obj.is_valid: # Only update if it was previously valid
                #    self.db.update_card_status(card_id, is_valid=False)
            else:
                # Access Granted!
                access_status = AccessStatus.GRANTED
                self.update_status(f
                self.hardware.set_led(
                self.hardware.buzz(duration=0.3, times=1)
                # --- Gate Operation --- 
                # Run hardware actions in a separate thread to avoid blocking GUI?
                # For now, keep sequential, but be mindful of delays.
                self.hardware.open_gate() # Uses pigpio
                # Optional: Activate solenoid briefly after opening?
                # self.hardware.activate_solenoid(duration=0.2)
                
                # Schedule auto-close
                self.schedule_auto_close(delay_ms=5000) # Auto-close after 5 seconds
                
                # Record successful access in DB
                self.db.record_access(card_id)
        else:
            # Card not found in database
            access_status = AccessStatus.DENIED
            self.update_status(f
            self.hardware.set_led(
            self.hardware.buzz(duration=0.1, times=2)
            self.root.after(1000, lambda: self.hardware.set_led(
            # Send alert for unknown card?
            # self.emailer.send_alert(

        # Log the access attempt
        response_time = time.time() - start_time
        self.logger.log_access(card_info_obj, access_status, response_time)
        # Update GUI card info panel
        self.update_card_info_display(card_info_obj, access_status)

    def schedule_auto_close(self, delay_ms: int):
        """Schedules the gate to close automatically after a delay."""
        # Cancel any existing timer first
        self.cancel_auto_close()
        # Schedule the new one
        self._auto_close_timer_id = self.root.after(delay_ms, self.auto_close_gate)
        self._auto_close_scheduled = True
        logger.log_info(f

    def cancel_auto_close(self):
        """Cancels any pending auto-close timer."""
        if self._auto_close_timer_id:
            self.root.after_cancel(self._auto_close_timer_id)
            logger.log_info(
            self._auto_close_timer_id = None
        self._auto_close_scheduled = False

    def auto_close_gate(self):
        """Closes the gate automatically if an auto-close was scheduled."""
        # Check flag to prevent accidental calls
        if not self._auto_close_scheduled:
             return
             
        self._auto_close_scheduled = False # Reset flag
        self._auto_close_timer_id = None
        
        self.update_status(
        self.hardware.close_gate() # Uses pigpio
        self.hardware.set_led(
        self.hardware.set_led(
        self.update_status(

    def manual_open_gate(self):
        
        # Add authentication check if needed for manual control
        if not Authenticator.authenticate(self.root):
            self.update_status(
            return
            
        # Cancel auto-close if manually opened
        self.cancel_auto_close()
        
        self.logger.log_audit(
        self.update_status(
        self.hardware.set_led(
        self.hardware.set_led(
        self.hardware.open_gate()
        # Don't auto-close on manual open? Or set a longer timer?
        # self.schedule_auto_close(delay_ms=15000) # Example: Close after 15s
        self.update_status(

    def manual_close_gate(self):
        
        # Add authentication check if needed
        # if not Authenticator.authenticate(self.root):
        #     self.update_status(
        #     return
            
        # Cancel auto-close if manually closed
        self.cancel_auto_close()
        
        self.logger.log_audit(
        self.update_status(
        self.hardware.close_gate()
        self.hardware.set_led(
        self.hardware.set_led(
        self.update_status(

    def open_management_window(self):
        
        if Authenticator.authenticate(self.root):
            self.logger.log_audit(
            # Create and show the management window
            # Pass necessary components (db, logger)
            mgmt_window = CardManagementWindow(self.root, self.db, self.logger)
            # mgmt_window.grab_set() # Make modal - prevents interaction with main window
        else:
            self.logger.log_audit(
            self.update_status(
            messagebox.showerror(

    def on_closing(self):
        
        if messagebox.askokcancel(
            self.logger.log_info(
            # Cancel pending timers
            self.cancel_auto_close()
            
            # Stop background threads first
            self.nfc_reader.stop_reading()
            self.temp_monitor.stop()
            # Wait for threads to finish (optional, use join with timeout)
            if self.nfc_reader.reader_thread:
                 self.nfc_reader.reader_thread.join(timeout=1)
            self.temp_monitor.join(timeout=1)

            # Shutdown email executor
            self.emailer.shutdown()
            
            # Cleanup hardware (turns off LEDs, stops servo, etc.)
            self.hardware.cleanup()

            # Disconnect pigpio (important!)
            # Check pigpio_instance directly as self.hardware.pi might be None
            global pigpio_instance
            if pigpio_instance and pigpio_instance.connected:
                logger.log_info(
                pigpio_instance.stop()
                pigpio_instance = None # Clear global instance

            self.logger.log_info(
            self.root.destroy()
        else:
             self.logger.log_info(

# --- Card Management Window (Improved Structure) ---
class CardManagementWindow(Toplevel):
    def __init__(self, parent, db: CardDatabase, logger_instance: ProfessionalLogger):
        super().__init__(parent)
        self.db = db
        self.logger = logger_instance
        self.title(
        self.geometry(
        # self.resizable(False, False)
        self.minsize(800, 500)

        # Make window modal
        self.transient(parent)
        self.grab_set()

        # Data variables for entry fields
        self.card_id_var = StringVar()
        self.name_var = StringVar()
        self.student_id_var = StringVar()
        self.faculty_var = StringVar()
        self.program_var = StringVar()
        self.level_var = StringVar()
        self.email_var = StringVar()
        self.expiry_date_var = StringVar() # Use string for simplicity, validate on save
        self.photo_path_var = StringVar()
        self.is_valid_var = BooleanVar(value=True)
        self.is_blacklisted_var = BooleanVar(value=False)

        # Add widgets for displaying, adding, editing, deleting cards
        self._setup_widgets()
        self._load_cards()
        
        # Focus on ID entry initially
        self.id_entry.focus_set()

    def _setup_widgets(self):
        # Frame for Treeview
        tree_frame = ttk.Frame(self, padding=5)
        tree_frame.pack(expand=True, fill=tk.BOTH, pady=5, padx=5)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        cols = (
        self.tree = ttk.Treeview(tree_frame, columns=cols, show=

        # Define headings
        self.tree.heading(
        self.tree.heading(
        self.tree.heading(
        self.tree.heading(
        self.tree.heading(
        self.tree.heading(
        self.tree.heading(

        # Configure column widths
        self.tree.column(
        self.tree.column(
        self.tree.column(
        self.tree.column(
        self.tree.column(
        self.tree.column(
        self.tree.column(

        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient=
        hsb = ttk.Scrollbar(tree_frame, orient=
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # Use grid for layout
        self.tree.grid(row=0, column=0, sticky=
        vsb.grid(row=0, column=1, sticky=
        hsb.grid(row=0, column=2, sticky=

        # Frame for entry fields
        entry_frame = ttk.LabelFrame(self, text=
        entry_frame.pack(fill=tk.X, padx=5, pady=5)
        # Grid layout for entries
        entry_col_width = 15 # Width for labels
        entry_width = 35 # Width for entry fields

        ttk.Label(entry_frame, text=
        self.id_entry = ttk.Entry(entry_frame, textvariable=self.card_id_var, width=entry_width)
        self.id_entry.grid(row=0, column=1, padx=5, pady=2, sticky=tk.W)
        # Add Scan button?
        # ttk.Button(entry_frame, text="Scan", command=self._scan_card_id).grid(row=0, column=2, padx=5, pady=2)

        ttk.Label(entry_frame, text=
        self.name_entry = ttk.Entry(entry_frame, textvariable=self.name_var, width=entry_width)
        self.name_entry.grid(row=1, column=1, padx=5, pady=2, sticky=tk.W)

        ttk.Label(entry_frame, text=
        self.student_id_entry = ttk.Entry(entry_frame, textvariable=self.student_id_var, width=entry_width)
        self.student_id_entry.grid(row=2, column=1, padx=5, pady=2, sticky=tk.W)

        ttk.Label(entry_frame, text=
        self.expiry_entry = ttk.Entry(entry_frame, textvariable=self.expiry_date_var, width=entry_width)
        self.expiry_entry.grid(row=3, column=1, padx=5, pady=2, sticky=tk.W)
        # Add calendar picker? (Requires tkcalendar: pip install tkcalendar)

        # Add other fields (Faculty, Program, Level, Email, Photo Path)
        ttk.Label(entry_frame, text=
        self.faculty_entry = ttk.Entry(entry_frame, textvariable=self.faculty_var, width=entry_width)
        self.faculty_entry.grid(row=0, column=4, padx=5, pady=2, sticky=tk.W)
        
        ttk.Label(entry_frame, text=
        self.program_entry = ttk.Entry(entry_frame, textvariable=self.program_var, width=entry_width)
        self.program_entry.grid(row=1, column=4, padx=5, pady=2, sticky=tk.W)

        ttk.Label(entry_frame, text=
        self.level_entry = ttk.Entry(entry_frame, textvariable=self.level_var, width=entry_width)
        self.level_entry.grid(row=2, column=4, padx=5, pady=2, sticky=tk.W)
        
        ttk.Label(entry_frame, text=
        self.email_entry = ttk.Entry(entry_frame, textvariable=self.email_var, width=entry_width)
        self.email_entry.grid(row=3, column=4, padx=5, pady=2, sticky=tk.W)
        
        ttk.Label(entry_frame, text=
        self.photo_entry = ttk.Entry(entry_frame, textvariable=self.photo_path_var, width=entry_width)
        self.photo_entry.grid(row=4, column=1, padx=5, pady=2, sticky=tk.W)
        # Add browse button?

        # Checkboxes for status
        self.valid_check = ttk.Checkbutton(entry_frame, text=
        self.valid_check.grid(row=4, column=4, padx=5, pady=5, sticky=tk.W)
        self.blacklist_check = ttk.Checkbutton(entry_frame, text=
        self.blacklist_check.grid(row=5, column=4, padx=5, pady=5, sticky=tk.W)

        # Frame for buttons
        button_frame = ttk.Frame(self, padding=5)
        button_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        ttk.Button(button_frame, text=
        ttk.Button(button_frame, text=
        ttk.Button(button_frame, text=
        ttk.Button(button_frame, text=
        ttk.Button(button_frame, text=

        # Bind tree selection to populate fields
        self.tree.bind(

    def _load_cards(self):
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)
        # Fetch cards from DB
        cards = self.db.get_all_cards()
        for card in cards:
            # Format data for display
            valid_str = 
            blacklisted_str = 
            last_access_str = card.get(
            if last_access_str and last_access_str != 
                try: 
                    # Attempt to parse and format
                    dt_obj = datetime.fromisoformat(last_access_str)
                    last_access_str = dt_obj.strftime(
                except (ValueError, TypeError):
                    last_access_str = 
            
            expiry_str = card.get(
            # Add other fields if needed in Treeview
            values = (
                card.get(
                card.get(
                card.get(
                expiry_str,
                valid_str,
                blacklisted_str,
                last_access_str
            )
            # Use card ID as the item ID in the tree for easier reference
            self.tree.insert(
        self.logger.log_info(f

    def _on_tree_select(self, event=None): # Add event=None for direct calls
        selected_items = self.tree.selection() # Get selected item ID(s)
        if not selected_items:
            return
        selected_item_id = selected_items[0] # Get the first selected item
        
        # Fetch full card data from DB using the ID (which is the tree item ID)
        card_data = self.db.get_card(selected_item_id)
        
        if card_data:
            # Populate entry fields based on DB data
            self._clear_fields() # Clear first
            self.card_id_var.set(card_data.get(
            self.name_var.set(card_data.get(
            self.student_id_var.set(card_data.get(
            self.faculty_var.set(card_data.get(
            self.program_var.set(card_data.get(
            self.level_var.set(card_data.get(
            self.email_var.set(card_data.get(
            self.expiry_date_var.set(card_data.get(
            self.photo_path_var.set(card_data.get(
            self.is_valid_var.set(bool(card_data.get(
            self.is_blacklisted_var.set(bool(card_data.get(
        else:
             messagebox.showwarning(
             self._clear_fields()

    def _validate_expiry_date(self, date_str: str) -> Optional[str]:
        """Validate date string and return in ISO format or None."""
        if not date_str:
            return None # Allow empty expiry date
        try:
            # Try parsing common formats
            dt = datetime.strptime(date_str, 
            return dt.date().isoformat() # Return only date part
        except ValueError:
            try:
                dt = datetime.strptime(date_str, 
                return dt.date().isoformat()
            except ValueError:
                 try:
                     # Check if already ISO format
                     dt = datetime.fromisoformat(date_str)
                     return dt.date().isoformat()
                 except ValueError:
                      messagebox.showerror(
                      return "INVALID_DATE"

    def _collect_card_data_from_fields(self) -> Optional[Dict[str, Any]]:
        
        card_id = self.card_id_var.get().strip().upper()
        if not card_id:
             messagebox.showerror(
             return None
             
        expiry_iso = self._validate_expiry_date(self.expiry_date_var.get().strip())
        if expiry_iso == "INVALID_DATE":
             return None # Validation failed
             
        card_data = {
            
            
            
            
            
            
            
            
            
            
            
            # Add other fields from DB if needed (e.g., access_count - usually not edited manually)
            # 'last_access': None, # Usually not set manually
            # 'access_count': 0, # Usually not set manually
        }
        return card_data

    def _add_card(self):
        card_data = self._collect_card_data_from_fields()
        if card_data:
            # Check if card already exists?
            existing = self.db.get_card(card_data[
            if existing:
                 if not messagebox.askyesno(
                     return
            
            if self.db.add_or_update_card(card_data):
                messagebox.showinfo(
                self._load_cards() # Refresh list
                self._clear_fields()
            else:
                messagebox.showerror(

    def _update_card(self):
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning(
            return
        original_id = selected_items[0]
            
        card_data = self._collect_card_data_from_fields()
        if card_data:
             new_id = card_data[
             # Check if ID was changed
             if new_id != original_id:
                  # If ID changed, check if the new ID already exists
                  existing_new_id = self.db.get_card(new_id)
                  if existing_new_id:
                       messagebox.showerror(
                       return
                  # If ID changed, we need to delete the old record first, then add new
                  # This is safer than relying on INSERT OR REPLACE if PK changes
                  if not messagebox.askyesno(
                       return
                  delete_success = self.db.delete_card(original_id)
                  if not delete_success:
                       messagebox.showerror(
                       return
                  # Now proceed to add the record with the new ID
                  if self.db.add_or_update_card(card_data):
                      messagebox.showinfo(
                      self._load_cards()
                      self._clear_fields()
                  else:
                      messagebox.showerror(
                      # Try to restore the old record? Complex.
             else:
                 # ID did not change, just update the existing record
                 if self.db.add_or_update_card(card_data):
                    messagebox.showinfo(
                    self._load_cards()
                    # Re-select the updated item?
                    # self.tree.selection_set(new_id)
                    # self._on_tree_select() # Update fields again
                 else:
                    messagebox.showerror(

    def _delete_card(self):
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning(
            return
        card_id = selected_items[0]
        
        # Get card name for confirmation message
        item_values = self.tree.item(card_id, 
        card_name = item_values[1] if len(item_values) > 1 else "(No Name)"
        
        if messagebox.askyesno(
            if self.db.delete_card(card_id):
                messagebox.showinfo(
                self._load_cards()
                self._clear_fields()
            else:
                 messagebox.showerror(

    def _clear_fields(self):
        # Clear all entry fields and checkboxes
        self.card_id_var.set(
        self.name_var.set(
        self.student_id_var.set(
        self.faculty_var.set(
        self.program_var.set(
        self.level_var.set(
        self.email_var.set(
        self.expiry_date_var.set(
        self.photo_path_var.set(
        self.is_valid_var.set(True) # Default to valid
        self.is_blacklisted_var.set(False)
        # Set focus back to ID field?
        self.id_entry.focus_set()
        # Clear tree selection
        self.tree.selection_remove(self.tree.selection())

# --- Main Execution ---
if __name__ == 
    # Setup admin credentials if running interactively and they don't exist
    # Check if running in an interactive terminal
    if sys.stdout.isatty() and sys.stdin.isatty():
        try:
            Authenticator.setup_credentials_interactively()
        except Exception as e:
             print(f
             logger.log_error(e, 
    else:
         logger.log_info(

    # Check pigpio availability before starting GUI
    if not PIGPIO_AVAILABLE or not pigpio_instance:
         critical_error_msg = 
         print(critical_error_msg)
         logger.log_error(RuntimeError(critical_error_msg), 
         # Show a GUI error message before exiting
         try:
             root_err = Tk()
             root_err.withdraw() # Hide main window
             messagebox.showerror(
             root_err.destroy()
         except Exception as gui_err:
              print(f
         sys.exit(1)

    # Start the Tkinter GUI
    root = Tk()
    app = SmartGateApp(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        print(
        # Ensure shutdown is called even on Ctrl+C
        app.on_closing()
    except Exception as e:
        logger.log_error(e, 
        # Attempt graceful shutdown even on error
        try:
             # Check if app exists and has on_closing method
             if 
                 app.on_closing()
        except Exception as shutdown_e:
             logger.log_error(shutdown_e, 
        # Optionally re-raise the exception
        # raise e
    finally:
        logger.log_info(
        print(

