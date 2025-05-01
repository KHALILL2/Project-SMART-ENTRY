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
# import RPi.GPIO as GPIO # Commented out as it's hardware specific
# import nfc # Commented out as it's hardware specific
# from nfc.clf import RemoteTarget # Commented out
from tkinter import Tk, Label, Button, messagebox, Entry, Toplevel, Text, END
from tkinter import ttk # Import ttk for themed widgets
import tkinter as tk # Use tk alias for consistency
from cryptography.fernet import Fernet, InvalidToken
import ssl
import hashlib
from datetime import datetime, timedelta
import traceback
import json
from pathlib import Path
import queue # For thread-safe GUI updates

#   Mock Hardware for Testing 
# Mock RPi.GPIO if not available (for testing on non-Pi systems)
try:
    import RPi.GPIO as GPIO
except (ImportError, RuntimeError):
    print("WARNING: RPi.GPIO not found or failed to import. Using mock GPIO.")
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
    GPIO = MockGPIO()

# Mock nfc library if not available
try:
    import nfc
    from nfc.clf import RemoteTarget
except ImportError:
    print("WARNING: nfc library not found. Using mock NFC.")
    class MockRemoteTarget:
        @property
        def sdd_res(self):
            return b'01234567'

    class MockNFCReader:
        def __init__(self, path):
            print(f"MockNFC: Initialized with path {path}")
            self._target_present = False
            self._activate_target = None

        def sense(self, target_type, iterations=1, interval=0.1):
            print("MockNFC: Sensing for target...")
            # Simulate finding a card sometimes
            if time.time() % 10 < 5: 
                print("MockNFC: Target found")
                self._target_present = True
                return MockRemoteTarget()
            else:
                print("MockNFC: No target found")
                self._target_present = False
                return None

        def close(self):
            print("MockNFC: Closed")
            
        # Mock nfc.tag.activate behavior
        def activate(self, clf, target):
             print("MockNFC: Activating target")
             if self._target_present:
                 # Simulate a tag object
                 class MockTag:
                     identifier = b'\x04\x01\x02\x03\x04\x05\x06'
                     def __str__(self):
                         return f"MockTag ID: {self.identifier.hex()}"
                 self._activate_target = MockTag()
                 return self._activate_target
             return None

    class MockNFC:
        ContactlessFrontend = MockNFCReader
        tag = type('MockTagModule', (), {'activate': MockNFCReader.activate})()
        clf = type('MockClfModule', (), {'RemoteTarget': MockRemoteTarget})()

    nfc = MockNFC()
    RemoteTarget = MockNFC.clf.RemoteTarget
#   End Mock Hardware

class AccessStatus(Enum):
    """Enumeration of possible access statuses"""
    GRANTED = auto()
    DENIED = auto()
    BLACKLISTED = auto()
    RATE_LIMITED = auto()

@dataclass
class CardInfo:
    """Data class for card information"""
    id: str
    name: Optional[str] = None
    expiry_date: Optional[datetime] = None
    is_valid: bool = False
    last_access: Optional[datetime] = None

@dataclass
class SystemMetrics:
    """Data class for system performance metrics"""
    total_requests: int = 0
    successful_accesses: int = 0
    failed_accesses: int = 0
    average_response_time: float = 0.0
    system_uptime: float = 0.0
    last_health_check: Optional[datetime] = None

class ProfessionalLogger:
    
    def __init__(self, log_dir: str = "logs") -> None:
        """Initialize the logger
        
        Args:
            log_dir (str): Directory to store log files
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        
        # Set up main logger
        self.logger = logging.getLogger('nfc_system')
        self.logger.setLevel(logging.INFO)
        
        # Prevent duplicate handlers if logger already exists
        if self.logger.hasHandlers():
            self.logger.handlers.clear()
            
        # File handler with rotation and compression
        file_handler = RotatingFileHandler(
            self.log_dir / 'system.log',
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s'
        ))
        
        # Audit log handler (using a separate logger might be cleaner)
        audit_logger = logging.getLogger('nfc_audit')
        audit_logger.setLevel(logging.INFO)
        if audit_logger.hasHandlers():
            audit_logger.handlers.clear()
            
        audit_handler = RotatingFileHandler(
            self.log_dir / 'audit.log',
            maxBytes=5*1024*1024,  # 5MB
            backupCount=3,
            encoding='utf-8'
        )
        audit_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(message)s'
        ))
        audit_logger.addHandler(audit_handler)
        self.audit_logger = audit_logger
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        ))
        
        self.logger.addHandler(file_handler)
        # self.logger.addHandler(audit_handler) # Removed: Use separate audit logger
        self.logger.addHandler(console_handler)
        
        # Initialize metrics
        self.metrics = SystemMetrics()
        self.start_time = datetime.now()
        
        # For GUI log display
        self.log_queue = queue.Queue()
    
    def log_access(self, card_info: CardInfo, status: AccessStatus, response_time: float) -> None:
        """Log an access attempt with detailed information"""
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
        self.log_queue.put(f"INFO: Access attempt - Card: {card_info.id}, Status: {status.name}")
        self._update_metrics(status, response_time)
    
    def log_error(self, error: Exception, context: str = "", severity: str = "ERROR") -> None:
        """Log an error with context and stack trace.
        Recommendation: Ensure tracebacks do not leak sensitive info in production logs.
        Consider filtering or summarizing tracebacks for certain log levels/outputs.
        """
        # Basic sanitization example (could be more sophisticated)
        tb_string = traceback.format_exc()
        # if is_production_environment(): # Hypothetical check
        #    tb_string = "Traceback hidden in production log for security."
        
        error_info = {
            'timestamp': datetime.now().isoformat(),
            'error': str(error),
            'context': context,
            'severity': severity,
            'traceback': tb_string, # Use potentially sanitized traceback
            'system_metrics': self._get_current_metrics()
        }
        msg = json.dumps(error_info)
        self.logger.error(msg)
        self.log_queue.put(f"{severity}: {context} - {error}")
    
    def log_audit(self, action: str, details: Dict[str, Any]) -> None:
        """Log an audit event using the dedicated audit logger"""
        audit_data = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'details': details,
            # 'system_metrics': self._get_current_metrics() # Avoid logging metrics in audit log for clarity
        }
        msg = json.dumps(audit_data)
        self.audit_logger.info(msg)
        self.log_queue.put(f"AUDIT: {action} - {details.get('card_id', '')}")
        
    def log_info(self, message: str) -> None:
        """Log general information messages."""
        self.logger.info(message)
        self.log_queue.put(f"INFO: {message}")
        
    def get_recent_logs(self, max_logs=100) -> List[str]:
        """Retrieve recent logs from the queue for GUI display."""
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
        """Update system performance metrics"""
        self.metrics.total_requests += 1
        if status == AccessStatus.GRANTED:
            self.metrics.successful_accesses += 1
        else:
            self.metrics.failed_accesses += 1
        
        # Update average response time (using more robust calculation)
        total_req = self.metrics.total_requests
        if total_req > 0:
             self.metrics.average_response_time = (
                 (self.metrics.average_response_time * (total_req - 1) + response_time) / total_req
             )
        else:
             self.metrics.average_response_time = response_time # First request

        # Update system uptime
        self.metrics.system_uptime = (datetime.now() - self.start_time).total_seconds()
        self.metrics.last_health_check = datetime.now()
    
    def _get_current_metrics(self) -> Dict[str, Any]:
        """Get current system metrics"""
        return {
            'total_requests': self.metrics.total_requests,
            'successful_accesses': self.metrics.successful_accesses,
            'failed_accesses': self.metrics.failed_accesses,
            'average_response_time': round(self.metrics.average_response_time, 4),
            'system_uptime': round(self.metrics.system_uptime, 2),
            'last_health_check': self.metrics.last_health_check.isoformat() if self.metrics.last_health_check else None
        }

# Initialize logger (Consider Dependency Injection instead of global)
logger = ProfessionalLogger()

# ====================
# CONFIGURATION
# ====================
class Config:
    # Recommendation: Move hardcoded lists like valid_pins to config file
    DEFAULT_VALID_PINS = [2,3,4,17,18,22,23,24,25,26,27]
    DEFAULT_THERMAL_FILE = "/sys/class/thermal/thermal_zone0/temp"
    CONFIG_FILE = 'config.ini'

    def __init__(self):
        self.config = configparser.ConfigParser()
        # Secure config file permissions
        try:
            os.umask(0o077)
        except Exception as e:
            logger.log_error(e, "Failed to set umask")
            
        if not os.path.exists(self.CONFIG_FILE):
            self._create_default_config()
            logger.log_info(f"Created default config file: {self.CONFIG_FILE}")
            
        self.config.read(self.CONFIG_FILE)
        
        # Recommendation: Ensure keyring backend is secure (e.g., OS keychain)
        # Add documentation about setting up a secure keyring backend.
        try:
            self.EMAIL_USER = keyring.get_password("nfc_gate", "email_user")
            self.EMAIL_PASS = keyring.get_password("nfc_gate", "email_pass")
        except Exception as e:
            logger.log_error(e, "Failed to retrieve credentials from keyring. Ensure keyring is installed and configured.")
            self.EMAIL_USER = None
            self.EMAIL_PASS = None
            
        # Email with TLS enforcement
        self.EMAIL_HOST = self.config.get('email', 'host', fallback='smtp.gmail.com')
        self.EMAIL_PORT = self.config.getint('email', 'port', fallback=587)
        self.EMAIL_USE_TLS = self.config.getboolean('email', 'use_tls', fallback=True)
        
        # GPIO with validation from config or default
        self.VALID_PINS = self._parse_list(self.config.get('gpio', 'valid_pins', fallback=str(self.DEFAULT_VALID_PINS)), int)
        self.SERVO_PIN = self._validate_pin(self.config.getint('gpio', 'servo', fallback=18))
        self.FAN_PIN = self._validate_pin(self.config.getint('gpio', 'fan', fallback=23))
        self.BUZZER_PIN = self._validate_pin(self.config.getint('gpio', 'buzzer', fallback=24))
        
        # Servo with range checking
        self.SERVO_OPEN_DUTY = self._validate_duty(self.config.getfloat('servo', 'open', fallback=7.5))
        self.SERVO_CLOSE_DUTY = self._validate_duty(self.config.getfloat('servo', 'close', fallback=2.5))
        self.SERVO_DELAY = max(0.1, self.config.getfloat('servo', 'delay', fallback=1.5))
        
        # Temperature with validation
        self.FAN_ON_TEMP = min(max(30, self.config.getfloat('temperature', 'on', fallback=60)), 90)
        self.FAN_OFF_TEMP = min(max(25, self.config.getfloat('temperature', 'off', fallback=50)), 85)
        # Recommendation: Make thermal file path configurable
        self.THERMAL_FILE = self.config.get('temperature', 'thermal_file', fallback=self.DEFAULT_THERMAL_FILE)
        
        # NFC with attempts limit
        self.NFC_MAX_ATTEMPTS = self.config.getint('nfc', 'max_attempts', fallback=10)
        self.NFC_TIMEOUT = self.config.getint('nfc', 'timeout', fallback=30)
        self.NFC_PROTOCOL = self.config.get('nfc', 'protocol', fallback='106A')
        
        # Database encryption
        self.DB_PATH = self.config.get('database', 'path', fallback='cards.db')
        self.DB_ENCRYPTED = self.config.getboolean('database', 'encrypted', fallback=True)

    def _create_default_config(self):
        """Creates a default config.ini if it doesn't exist."""
        default_config = configparser.ConfigParser()
        default_config['email'] = {
            'host': 'smtp.gmail.com',
            'port': '587',
            'use_tls': 'True'
            # Note: User/Pass stored in keyring
        }
        default_config['gpio'] = {
            'valid_pins': str(self.DEFAULT_VALID_PINS),
            'servo': '18',
            'fan': '23',
            'buzzer': '24'
        }
        default_config['servo'] = {
            'open': '7.5',
            'close': '2.5',
            'delay': '1.5'
        }
        default_config['temperature'] = {
            'on': '60',
            'off': '50',
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
        with open(self.CONFIG_FILE, 'w') as configfile:
            default_config.write(configfile)
            
    def _parse_list(self, list_str: str, item_type: type) -> list:
        """Parses a string representation of a list from config."""
        try:
            # Handle potential formats like '[1, 2, 3]' or '1,2,3'
            list_str = list_str.strip('[] ')
            return [item_type(item.strip()) for item in list_str.split(',')]
        except Exception as e:
            logger.log_error(e, f"Failed to parse list from config: {list_str}")
            return [] # Return empty list on error

    def _validate_pin(self, pin):
        """Ensure GPIO pin is valid based on config or default."""
        if pin in self.VALID_PINS:
            return pin
        else:
            logger.log_error(ValueError(f"Invalid pin {pin} configured. Not in VALID_PINS: {self.VALID_PINS}. Falling back to default 18."))
            return 18 # Fallback to a default known pin
    
    def _validate_duty(self, duty):
        """Ensure duty cycle is valid (2.5-12.5)"""
        return min(max(2.5, duty), 12.5)

# Initialize config (Consider Dependency Injection)
config = Config()

# ====================
# CONFIGURATION VALIDATION
# ====================
class ConfigValidator:
    @staticmethod
    def validate_config(config_obj: Config) -> bool:
        """Validate all configuration parameters"""
        try:
            # Validate email settings
            if not config_obj.EMAIL_HOST or not config_obj.EMAIL_PORT:
                raise ValueError("Email configuration incomplete (host/port)")
            # Note: User/Pass validation depends on keyring setup
            if config_obj.EMAIL_USER is None or config_obj.EMAIL_PASS is None:
                 logger.log_info("Email user/pass not found in keyring. Email notifications disabled.")
            
            # Validate GPIO pins (already validated in Config class, but double check)
            if config_obj.SERVO_PIN not in config_obj.VALID_PINS:
                raise ValueError(f"Invalid servo pin: {config_obj.SERVO_PIN}")
            if config_obj.FAN_PIN not in config_obj.VALID_PINS:
                raise ValueError(f"Invalid fan pin: {config_obj.FAN_PIN}")
            if config_obj.BUZZER_PIN not in config_obj.VALID_PINS:
                raise ValueError(f"Invalid buzzer pin: {config_obj.BUZZER_PIN}")
            
            # Validate servo settings (already validated in Config class)
            if not (2.5 <= config_obj.SERVO_OPEN_DUTY <= 12.5):
                raise ValueError(f"Invalid servo open duty: {config_obj.SERVO_OPEN_DUTY}")
            if not (2.5 <= config_obj.SERVO_CLOSE_DUTY <= 12.5):
                raise ValueError(f"Invalid servo close duty: {config_obj.SERVO_CLOSE_DUTY}")
            if config_obj.SERVO_DELAY <= 0:
                raise ValueError(f"Invalid servo delay: {config_obj.SERVO_DELAY}")
            
            # Validate temperature settings
            if config_obj.FAN_ON_TEMP <= config_obj.FAN_OFF_TEMP:
                raise ValueError("Fan ON temperature must be greater than OFF temperature")
            if not os.path.exists(config_obj.THERMAL_FILE):
                 logger.log_error(FileNotFoundError(f"Thermal file not found: {config_obj.THERMAL_FILE}. Temperature monitoring may fail."))
            
            # Validate NFC settings
            if config_obj.NFC_MAX_ATTEMPTS < 1:
                raise ValueError("NFC max attempts must be positive")
            if config_obj.NFC_TIMEOUT < 1:
                raise ValueError("NFC timeout must be positive")
            
            # Validate database settings
            if not config_obj.DB_PATH:
                raise ValueError("Database path is required")
            db_dir = os.path.dirname(config_obj.DB_PATH)
            if db_dir and not os.path.exists(db_dir):
                try:
                    os.makedirs(db_dir)
                    logger.log_info(f"Created database directory: {db_dir}")
                except Exception as e:
                    raise OSError(f"Failed to create database directory {db_dir}: {e}")
            
            logger.log_info("Configuration validation successful.")
            return True
        except Exception as e:
            logger.log_error(e, "Configuration validation failed")
            return False

# Validate the loaded config
if not ConfigValidator.validate_config(config):
    logger.log_error(RuntimeError("CRITICAL: Configuration validation failed. Exiting."), "Startup")
    # In a real app, might exit or enter a safe mode
    # sys.exit(1) 

# ====================
# AUTHENTICATION (Improved Placeholder)
# ====================
class Authenticator:
    # Recommendation: Implement stronger auth (MFA, RBAC) if needed.
    # This basic implementation uses keyring for storage.
    # Hashing should be handled by a secure keyring backend.
    SERVICE_NAME = "nfc_gate"
    ADMIN_USER_KEY = "admin_user"
    ADMIN_PASS_KEY = "admin_pass"

    @staticmethod
    def setup_credentials_interactively():
        """Helper to set credentials in keyring if they don't exist."""
        try:
            if not keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY):
                print("Setting up admin credentials...")
                username = input("Enter admin username: ")
                password = input("Enter admin password: ")
                keyring.set_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY, username)
                keyring.set_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_PASS_KEY, password)
                print("Admin credentials stored securely in keyring.")
        except Exception as e:
            logger.log_error(e, "Failed to setup credentials interactively. Ensure keyring is installed and configured.")

    @staticmethod
    def authenticate():
        """Simple login window using keyring credentials."""
        try:
            stored_user = keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY)
            stored_pass = keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_PASS_KEY)
        except Exception as e:
            logger.log_error(e, "Failed to retrieve credentials from keyring. Authentication unavailable.")
            messagebox.showerror("Authentication Error", "Could not retrieve credentials. Check keyring setup.")
            return False
            
        if not stored_user or not stored_pass:
            logger.log_error(ValueError("Admin credentials not found in keyring."), "Authentication")
            messagebox.showerror("Setup Required", "Admin credentials not set. Run setup interactively or configure keyring.")
            # Optionally call setup_credentials_interactively() here if appropriate
            return False

        root = Tk()
        root.title("System Login")
        root.geometry("300x150")
        root.resizable(False, False)
        
        Label(root, text="Username:").pack(pady=(10,0))
        user_entry = Entry(root)
        user_entry.pack()
        
        Label(root, text="Password:").pack(pady=(5,0))
        pass_entry = Entry(root, show="*")
        pass_entry.pack()
        
        attempts = 3
        authenticated = False
        
        def check_credentials():
            nonlocal attempts, authenticated
            username = user_entry.get()
            password = pass_entry.get()
            
            # Compare entered credentials with those from keyring
            # Note: Keyring handles secure storage; direct comparison is okay here.
            if username == stored_user and password == stored_pass:
                authenticated = True
                logger.log_audit("login_success", {"username": username})
                root.destroy()
            else:
                attempts -= 1
                logger.log_audit("login_failed", {"username": username, "attempts_left": attempts})
                if attempts > 0:
                    messagebox.showerror("Login Failed", f"Invalid credentials. {attempts} attempts remaining.")
                else:
                    messagebox.showerror("Login Failed", "Maximum login attempts reached. Exiting.")
                    root.destroy() # Close window, but don't exit the whole app here
                    # sys.exit(1) # Avoid exiting the entire application on failed login
        
        login_button = Button(root, text="Login", command=check_credentials)
        login_button.pack(pady=10)
        
        # Center the window
        root.update_idletasks()
        x = (root.winfo_screenwidth() // 2) - (root.winfo_width() // 2)
        y = (root.winfo_screenheight() // 2) - (root.winfo_height() // 2)
        root.geometry(f"+ {x}+{y}")

        root.mainloop()
        return authenticated

# ====================
# HARDWARE CONTROL
# ====================
class HardwareController:
    """
    Hardware controller for NFC and GPIO operations.
    
    Features:
    - Thread-safe hardware operations using locks.
    - Automatic resource cleanup via context manager or explicit call.
    - Basic error recovery/retry for NFC reading.
    - Mockable for testing.
    """
    
    def __init__(self, config_obj: Config, logger_obj: ProfessionalLogger) -> None:
        """Initialize the hardware controller with dependencies."""
        self.config = config_obj
        self.logger = logger_obj
        self._lock = threading.Lock() # Lock for thread safety on hardware access
        self._nfc_reader = None
        self._servo_pwm = None # Store PWM object
        self._is_initialized = False
        self._last_health_check = None
        self._error_count = 0
        self._max_retries = 3
        
        try:
            self._initialize_hardware()
        except Exception as e:
            self.logger.log_error(e, "Hardware initialization failed during __init__")
            # Decide if this is critical. Maybe allow running without hardware?
            # raise # Re-raise if hardware is essential
    
    def _initialize_hardware(self) -> None:
        """Initialize GPIO and NFC hardware with error handling."""
        with self._lock:
            if self._is_initialized:
                return
                
            try:
                self.logger.log_info("Initializing hardware...")
                # Initialize GPIO
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(self.config.SERVO_PIN, GPIO.OUT)
                GPIO.setup(self.config.FAN_PIN, GPIO.OUT)
                GPIO.setup(self.config.BUZZER_PIN, GPIO.OUT)
                
                # Set initial states
                GPIO.output(self.config.FAN_PIN, GPIO.LOW)
                GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
                
                # Setup PWM for Servo
                self._servo_pwm = GPIO.PWM(self.config.SERVO_PIN, 50) # 50Hz frequency
                self._servo_pwm.start(self.config.SERVO_CLOSE_DUTY) # Start in closed position
                time.sleep(0.5) # Allow servo to settle
                self._servo_pwm.ChangeDutyCycle(0) # Stop sending signal until needed

                # Initialize NFC reader (with retry logic)
                self._nfc_reader = self._get_nfc_reader_with_retry()
                if not self._nfc_reader:
                    raise RuntimeError("Failed to initialize NFC reader after multiple attempts")
                
                self._is_initialized = True
                self.logger.log_audit("hardware_initialized", {
                    "servo_pin": self.config.SERVO_PIN,
                    "fan_pin": self.config.FAN_PIN,
                    "buzzer_pin": self.config.BUZZER_PIN,
                    "nfc_status": "OK",
                    "status": "success"
                })
                
            except Exception as e:
                self.logger.log_error(e, "Hardware initialization failed")
                self._cleanup() # Attempt cleanup on failure
                raise # Re-raise the exception

    def _get_nfc_reader_with_retry(self):
        """Attempt to initialize NFC reader with retries."""
        base_delay = 2
        for attempt in range(1, self.config.NFC_MAX_ATTEMPTS + 1):
            try:
                clf = nfc.ContactlessFrontend('usb') # Assuming USB interface
                if clf:
                    self.logger.log_info(f"NFC reader initialized successfully on attempt {attempt}")
                    return clf
            except Exception as e:
                delay = min(base_delay * attempt, 30) # Exponential backoff up to 30s
                self.logger.log_error(e, f"NFC init failed (attempt {attempt}/{self.config.NFC_MAX_ATTEMPTS}). Retrying in {delay}s...")
                if attempt == self.config.NFC_MAX_ATTEMPTS:
                    self.logger.log_error(RuntimeError("Max NFC init attempts reached."), "NFC Init")
                    return None
                time.sleep(delay)
        return None

    def _cleanup(self) -> None:
        """Safely cleanup hardware resources."""
        self.logger.log_info("Cleaning up hardware resources...")
        with self._lock:
            try:
                if self._servo_pwm:
                    self._servo_pwm.stop()
                if self._nfc_reader:
                    self._nfc_reader.close()
                    self._nfc_reader = None
                GPIO.cleanup() # Cleanup all GPIO channels used by this script
                self._is_initialized = False
                self.logger.log_info("Hardware cleanup successful.")
            except Exception as e:
                self.logger.log_error(e, "Hardware cleanup failed")
    
    def read_card(self) -> Optional[CardInfo]:
        """Read NFC card with retry mechanism and timeout.
        Returns CardInfo with ID only, or None if no card read.
        """
        if not self._is_initialized or not self._nfc_reader:
            self.logger.log_error(RuntimeError("Hardware not initialized, cannot read card."))
            return None
            
        start_time = time.time()
        retry_count = 0
        
        while retry_count < self._max_retries:
            if time.time() - start_time > self.config.NFC_TIMEOUT:
                self.logger.log_info(f"NFC read timed out after {self.config.NFC_TIMEOUT}s")
                break
                
            try:
                # Use lock for thread safety when accessing shared NFC reader
                with self._lock:
                    # Sense for target
                    target = self._nfc_reader.sense(RemoteTarget(self.config.NFC_PROTOCOL), iterations=1, interval=0.2)
                    
                    if target:
                        # Activate the tag
                        tag = nfc.tag.activate(self._nfc_reader, target)
                        if tag and hasattr(tag, 'identifier'):
                            card_id = tag.identifier.hex()
                            self.logger.log_audit("card_read_success", {
                                "card_id": card_id,
                                "attempt": retry_count + 1
                            })
                            return CardInfo(id=card_id)
                        else:
                             self.logger.log_info("Target sensed but failed to activate or get identifier.")
                             # Optional: Add a small delay before next sense
                             time.sleep(0.1)
                    # else: No target found in this iteration
            
            except Exception as e:
                # Check for specific NFC errors if possible, e.g., nfc.clf.TimeoutError
                retry_count += 1
                self._error_count += 1
                self.logger.log_error(e, f"Card read attempt {retry_count} failed")
                
                if retry_count >= self._max_retries:
                    self.logger.log_audit("card_read_failed_max_retries", {
                        "error_count": self._error_count,
                        "max_retries": self._max_retries
                    })
                    return None
                
                time.sleep(0.2) # Delay before next retry
            
            # Small delay even if no target found (prevent busy-waiting)
            time.sleep(0.1)
        
        # If loop finishes without returning, no card was read successfully
        self.logger.log_info("No card read within timeout/retries.")
        return None

    def control_servo(self, open_gate: bool) -> None:
        """Control the servo motor to open or close the gate."""
        if not self._is_initialized or not self._servo_pwm:
            self.logger.log_error(RuntimeError("Hardware not initialized, cannot control servo."))
            return
            
        target_duty = self.config.SERVO_OPEN_DUTY if open_gate else self.config.SERVO_CLOSE_DUTY
        action = "open" if open_gate else "close"
        
        try:
            with self._lock:
                self.logger.log_info(f"Setting servo to {action} position (Duty: {target_duty})...")
                self._servo_pwm.ChangeDutyCycle(target_duty)
                time.sleep(self.config.SERVO_DELAY) # Wait for servo to reach position
                self._servo_pwm.ChangeDutyCycle(0) # Stop signal to prevent jitter/heating
                self.logger.log_audit("servo_control", {
                    "action": action,
                    "duty_cycle": target_duty
                })
        except Exception as e:
            self._error_count += 1
            self.logger.log_error(e, f"Servo control failed during {action}")
            # Attempt to stop PWM on error
            try:
                 self._servo_pwm.ChangeDutyCycle(0)
            except Exception as pwm_e:
                 self.logger.log_error(pwm_e, "Failed to stop PWM after servo error")

    def control_fan(self, state: bool) -> None:
        """Control the cooling fan."""
        if not self._is_initialized:
            self.logger.log_error(RuntimeError("Hardware not initialized, cannot control fan."))
            return
        action = "ON" if state else "OFF"
        try:
            with self._lock:
                GPIO.output(self.config.FAN_PIN, GPIO.HIGH if state else GPIO.LOW)
                self.logger.log_info(f"Fan turned {action}")
        except Exception as e:
             self._error_count += 1
             self.logger.log_error(e, f"Fan control failed turning {action}")
             
    def buzz(self, duration: float = 0.1) -> None:
        """Activate the buzzer briefly."""
        if not self._is_initialized:
            self.logger.log_error(RuntimeError("Hardware not initialized, cannot control buzzer."))
            return
        try:
            with self._lock:
                GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
            time.sleep(duration)
            with self._lock:
                GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
        except Exception as e:
             self._error_count += 1
             self.logger.log_error(e, f"Buzzer control failed")

    def check_health(self) -> Dict[str, Any]:
        """Check hardware health status."""
        current_time = datetime.now()
        self._last_health_check = current_time
        
        # Basic check: Can we access the NFC reader object?
        nfc_ok = False
        if self._is_initialized and self._nfc_reader:
            try:
                # A simple check, like trying to get reader info (if available)
                # or just assume it's okay if the object exists.
                # In a real scenario, might involve a more specific health check command.
                nfc_ok = True 
            except Exception as e:
                self.logger.log_error(e, "NFC reader health check failed")
                nfc_ok = False
                self._error_count += 1
        
        health_status = {
            "timestamp": current_time.isoformat(),
            "initialized": self._is_initialized,
            "error_count": self._error_count,
            "last_health_check": self._last_health_check.isoformat() if self._last_health_check else None,
            "gpio_status": "OK" if self._is_initialized else "ERROR", # Basic check
            "nfc_status": "OK" if nfc_ok else "ERROR"
        }
        
        # self.logger.log_audit("health_check", health_status) # Maybe too noisy for audit log
        self.logger.log_info(f"Health Check: Initialized={self._is_initialized}, NFC OK={nfc_ok}, Errors={self._error_count}")
        return health_status
    
    def __enter__(self) -> 'HardwareController':
        """Context manager entry."""
        # Initialization is now done in __init__
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit with cleanup."""
        self._cleanup()

# ====================
# DATABASE (Encrypted)
# ====================
class SecureDatabaseManager:
    # Recommendation: Add locking for write operations if concurrent access is possible.
    # Using check_same_thread=False requires careful external locking or design.
    # Adding a simple lock here for demonstration.
    
    def __init__(self, config_obj: Config, logger_obj: ProfessionalLogger):
        self.config = config_obj
        self.logger = logger_obj
        self._db_lock = threading.Lock() # Lock for database write operations
        self.cipher = None
        
        # Set secure file permissions (moved from Config to here, closer to file creation)
        try:
            os.umask(0o077)
        except Exception as e:
            self.logger.log_error(e, "Failed to set umask for database file")

        try:
            # Ensure directory exists before connecting
            db_dir = os.path.dirname(self.config.DB_PATH)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir)
                self.logger.log_info(f"Created database directory: {db_dir}")
                
            self.conn = sqlite3.connect(self.config.DB_PATH, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row # Access columns by name
            
            if self.config.DB_ENCRYPTED:
                self._setup_encryption()
            
            self._init_db()
            self.logger.log_info("Database manager initialized successfully.")
            
        except Exception as e:
            self.logger.log_error(e, "CRITICAL: Database initialization failed")
            raise # Database is likely essential

    def _setup_encryption(self):
        """Sets up the Fernet cipher using a key from keyring."""
        try:
            db_key = keyring.get_password("nfc_gate", "db_key")
            if not db_key:
                self.logger.log_info("Database key not found in keyring. Generating a new one.")
                db_key = Fernet.generate_key().decode()
                keyring.set_password("nfc_gate", "db_key", db_key)
                self.logger.log_info("New database key generated and stored in keyring.")
            self.cipher = Fernet(db_key.encode())
            self.logger.log_info("Database encryption enabled.")
        except Exception as e:
            self.logger.log_error(e, "Failed to setup database encryption. Keyring accessible?")
            # Decide: Disable encryption or fail? Forcing encryption seems safer.
            raise RuntimeError("Failed to setup database encryption key.")
    
    def _init_db(self):
        """Initialize database schema with improved structure and audit logging."""
        with self._db_lock: # Lock during schema modification
            with self.conn:
                # Cards table (Raw scans, potentially ephemeral)
                self.conn.execute('''
                    CREATE TABLE IF NOT EXISTS card_scans (
                        scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        card_id TEXT NOT NULL,
                        scan_data TEXT, -- Encrypted if enabled
                        scan_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Authorized Cards table (Managed list)
                self.conn.execute('''
                    CREATE TABLE IF NOT EXISTS authorized_cards (
                        card_id TEXT PRIMARY KEY,
                        holder_name TEXT, -- Encrypted if enabled
                        expiry_date DATE,
                        is_active BOOLEAN DEFAULT 1, -- Added active flag
                        added_by TEXT, -- User who added/modified
                        added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        last_modified DATETIME DEFAULT CURRENT_TIMESTAMP
                    ) WITHOUT ROWID
                ''')
                
                # Access Log table
                self.conn.execute('''
                    CREATE TABLE IF NOT EXISTS access_log (
                        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        card_id TEXT,
                        access_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                        status TEXT NOT NULL, -- GRANTED, DENIED, etc.
                        details TEXT -- e.g., reason for denial
                    )
                ''')
                
                # Audit Log table (System/Admin actions)
                self.conn.execute('''
                    CREATE TABLE IF NOT EXISTS audit_log (
                        audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT, -- User performing action (if applicable)
                        action TEXT NOT NULL,
                        target TEXT, -- e.g., card_id affected
                        details TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Indexes for performance
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_scan_timestamp ON card_scans(scan_timestamp)')
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_auth_expiry ON authorized_cards(expiry_date)')
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_auth_active ON authorized_cards(is_active)')
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_access_time ON access_log(access_time)')
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp)')
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)')
                
        self.logger.log_info("Database schema initialized/verified.")

    def _encrypt(self, data: Optional[str]) -> Optional[str]:
        """Helper method for encryption."""
        if self.cipher and data is not None:
            try:
                return self.cipher.encrypt(data.encode()).decode()
            except Exception as e:
                self.logger.log_error(e, "Encryption failed")
                return None # Indicate failure
        return data # Return as is if no cipher or data is None
    
    def _decrypt(self, data: Optional[str]) -> Optional[str]:
        """Helper method for decryption with improved error handling."""
        if self.cipher and data is not None:
            try:
                return self.cipher.decrypt(data.encode()).decode()
            except InvalidToken as e: # Catch specific crypto error
                self.logger.log_error(e, f"Decryption failed: Invalid token. Data might be corrupted or wrong key used.")
                return None # Indicate failure clearly
            except Exception as e:
                self.logger.log_error(e, f"Decryption failed unexpectedly.")
                return None # Indicate failure
        return data # Return as is if no cipher or data is None

    def log_scan(self, card_id: str, scan_data: Optional[str] = None):
        """Log a raw card scan."""
        encrypted_data = self._encrypt(scan_data)
        if scan_data is not None and encrypted_data is None and self.config.DB_ENCRYPTED:
            self.logger.log_error(RuntimeError("Failed to encrypt scan data, scan not logged."), "DB log_scan")
            return
            
        try:
            with self._db_lock:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO card_scans (card_id, scan_data) VALUES (?, ?)",
                        (card_id, encrypted_data)
                    )
            # self.logger.log_info(f"Card scan logged for {card_id}") # Might be too verbose
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error logging scan for card {card_id}")

    def log_access_attempt(self, card_id: Optional[str], status: AccessStatus, details: str = ""):
        """Log an access attempt (granted or denied)."""
        try:
            with self._db_lock:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO access_log (card_id, status, details) VALUES (?, ?, ?)",
                        (card_id, status.name, details)
                    )
            self.logger.log_info(f"Access attempt logged: Card={card_id}, Status={status.name}, Details={details}")
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error logging access attempt for card {card_id}")
            
    def log_audit_action(self, action: str, user_id: Optional[str] = None, target: Optional[str] = None, details: Optional[str] = None):
        """Log an administrative or system action."""
        try:
            with self._db_lock:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO audit_log (user_id, action, target, details) VALUES (?, ?, ?, ?)",
                        (user_id, action, target, details)
                    )
            self.logger.log_info(f"Audit action logged: Action={action}, User={user_id}, Target={target}")
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error logging audit action '{action}'")

    def get_card_info(self, card_id: str) -> Optional[CardInfo]:
        """Retrieve authorization details for a specific card."""
        try:
            cursor = self.conn.cursor() # No lock needed for read
            cursor.execute(
                "SELECT card_id, holder_name, expiry_date, is_active FROM authorized_cards WHERE card_id = ?", 
                (card_id,)
            )
            row = cursor.fetchone()
            
            if row:
                decrypted_name = self._decrypt(row['holder_name'])
                if row['holder_name'] is not None and decrypted_name is None and self.config.DB_ENCRYPTED:
                     self.logger.log_error(RuntimeError(f"Failed to decrypt name for card {card_id}"), "DB get_card_info")
                     # Decide how to handle - return partial info or None?
                     # Returning partial info might be okay here.
                     decrypted_name = "[DECRYPTION FAILED]"
                     
                expiry_dt = None
                if row['expiry_date']:
                    try:
                        expiry_dt = datetime.strptime(row['expiry_date'], '%Y-%m-%d')
                    except ValueError:
                        self.logger.log_error(ValueError(f"Invalid expiry date format for card {card_id}: {row['expiry_date']}"))
                
                # Check validity based on active status and expiry date
                is_currently_valid = False
                if row['is_active']:
                    if expiry_dt is None or expiry_dt.date() >= datetime.now().date():
                        is_currently_valid = True
                        
                return CardInfo(
                    id=row['card_id'],
                    name=decrypted_name,
                    expiry_date=expiry_dt,
                    is_valid=is_currently_valid # Reflects current validity based on DB data
                    # last_access needs to be tracked separately if required
                )
            else:
                return None # Card not found in authorized list
                
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error retrieving info for card {card_id}")
            return None

    def add_or_update_card(self, card_id: str, holder_name: Optional[str], expiry_date: Optional[datetime], is_active: bool, added_by: str) -> bool:
        """Add a new card or update an existing one."""
        encrypted_name = self._encrypt(holder_name)
        if holder_name is not None and encrypted_name is None and self.config.DB_ENCRYPTED:
            self.logger.log_error(RuntimeError("Failed to encrypt holder name, card not added/updated."), "DB add_or_update_card")
            return False
            
        expiry_str = expiry_date.strftime('%Y-%m-%d') if expiry_date else None
        now_ts = datetime.now()
        
        try:
            with self._db_lock:
                with self.conn:
                    cursor = self.conn.cursor()
                    cursor.execute("SELECT 1 FROM authorized_cards WHERE card_id = ?", (card_id,))
                    exists = cursor.fetchone() is not None
                    
                    if exists:
                        # Update existing card
                        self.conn.execute('''
                            UPDATE authorized_cards 
                            SET holder_name = ?, expiry_date = ?, is_active = ?, last_modified = ?, added_by = ?
                            WHERE card_id = ?
                        ''', (encrypted_name, expiry_str, is_active, now_ts, added_by, card_id))
                        action = "CARD_UPDATED"
                    else:
                        # Insert new card
                        self.conn.execute('''
                            INSERT INTO authorized_cards 
                            (card_id, holder_name, expiry_date, is_active, added_by, added_at, last_modified)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (card_id, encrypted_name, expiry_str, is_active, added_by, now_ts, now_ts))
                        action = "CARD_ADDED"
                        
            # Log the audit action outside the transaction
            self.log_audit_action(action=action, user_id=added_by, target=card_id, 
                                  details=f"Name: {holder_name}, Expires: {expiry_str}, Active: {is_active}")
            return True
            
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error adding/updating card {card_id}")
            return False
            
    def remove_card(self, card_id: str, removed_by: str) -> bool:
        """Remove a card from the authorized list."""
        try:
            with self._db_lock:
                with self.conn:
                    cursor = self.conn.execute("DELETE FROM authorized_cards WHERE card_id = ?", (card_id,))
                    if cursor.rowcount > 0:
                         self.log_audit_action(action="CARD_REMOVED", user_id=removed_by, target=card_id)
                         return True
                    else:
                         self.logger.log_info(f"Attempted to remove non-existent card: {card_id}")
                         return False # Card wasn't there to remove
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error removing card {card_id}")
            return False
            
    def get_authorized_cards(self, include_inactive=False) -> List[CardInfo]:
        """Retrieve a list of all authorized cards."""
        cards = []
        try:
            cursor = self.conn.cursor()
            query = "SELECT card_id, holder_name, expiry_date, is_active FROM authorized_cards"
            if not include_inactive:
                query += " WHERE is_active = 1"
            query += " ORDER BY holder_name COLLATE NOCASE"
                
            cursor.execute(query)
            for row in cursor.fetchall():
                decrypted_name = self._decrypt(row['holder_name'])
                if row['holder_name'] is not None and decrypted_name is None and self.config.DB_ENCRYPTED:
                    decrypted_name = "[DECRYPTION FAILED]"
                    
                expiry_dt = None
                if row['expiry_date']:
                    try:
                        expiry_dt = datetime.strptime(row['expiry_date'], '%Y-%m-%d')
                    except ValueError:
                        expiry_dt = None # Treat invalid date as None
                        
                cards.append(CardInfo(
                    id=row['card_id'],
                    name=decrypted_name,
                    expiry_date=expiry_dt,
                    is_valid=bool(row['is_active']) # Reflects active status from DB
                ))
            return cards
        except sqlite3.Error as e:
            self.logger.log_error(e, "DB error retrieving authorized cards list")
            return [] # Return empty list on error
            
    def close(self):
        """Close the database connection."""
        if self.conn:
            try:
                self.conn.close()
                self.logger.log_info("Database connection closed.")
            except sqlite3.Error as e:
                self.logger.log_error(e, "Error closing database connection")

# ====================
# NFC READER (Refactored - Now part of HardwareController)
# ====================
# Class NFCReader removed, functionality integrated into HardwareController._get_nfc_reader_with_retry and HardwareController.read_card

# ====================
# TEMPERATURE MONITOR
# ====================
class TemperatureMonitor(threading.Thread):
    """Monitors temperature in a separate thread with hysteresis."""
    def __init__(self, hardware_controller: HardwareController, config_obj: Config, logger_obj: ProfessionalLogger):
        super().__init__(daemon=True)
        self.hardware = hardware_controller
        self.config = config_obj
        self.logger = logger_obj
        self.thermal_file = self.config.THERMAL_FILE
        self.fan_on = False
        self._stop_event = threading.Event()
        self.last_temp = None

    def run(self):
        self.logger.log_info("Temperature monitor thread started.")
        while not self._stop_event.is_set():
            try:
                if not os.path.exists(self.thermal_file):
                    self.logger.log_error(FileNotFoundError(f"Thermal file not found: {self.thermal_file}"), "Temp Monitor")
                    self._stop_event.wait(60) # Wait longer if file missing
                    continue
                    
                with open(self.thermal_file) as f:
                    temp_str = f.read().strip()
                    temp = float(temp_str) / 1000
                    self.last_temp = temp
                    
                # Hysteresis control
                if not self.fan_on and temp > self.config.FAN_ON_TEMP:
                    self.hardware.control_fan(True)
                    self.fan_on = True
                    self.logger.log_info(f"Fan turned ON (Temp: {temp:.1f}°C)")
                elif self.fan_on and temp < self.config.FAN_OFF_TEMP:
                    self.hardware.control_fan(False)
                    self.fan_on = False
                    self.logger.log_info(f"Fan turned OFF (Temp: {temp:.1f}°C)")
                
            except FileNotFoundError:
                 self.logger.log_error(FileNotFoundError(f"Thermal file disappeared: {self.thermal_file}"), "Temp Monitor")
                 # Keep trying, but maybe log less frequently
                 self._stop_event.wait(30)
            except ValueError:
                 self.logger.log_error(ValueError(f"Invalid temperature value read: '{temp_str}'"), "Temp Monitor")
                 self._stop_event.wait(10)
            except Exception as e:
                self.logger.log_error(e, "Temp monitor error")
                self._stop_event.wait(10) # Wait after generic error
            else:
                # Wait 5 seconds if everything was okay
                self._stop_event.wait(5)
                
        self.logger.log_info("Temperature monitor thread stopped.")
        # Ensure fan is turned off on exit
        try:
            if self.fan_on:
                self.hardware.control_fan(False)
                self.logger.log_info("Turned fan OFF on monitor exit.")
        except Exception as e:
            self.logger.log_error(e, "Error turning fan off during temp monitor cleanup")

    def stop(self):
        """Signal the thread to stop."""
        self._stop_event.set()

# ====================
# NOTIFICATION SYSTEM
# ====================
class Notifier:
    """Handles sending notifications, currently via email."""
    def __init__(self, config_obj: Config, logger_obj: ProfessionalLogger):
        self.config = config_obj
        self.logger = logger_obj
        self.executor = ThreadPoolExecutor(max_workers=2) # Pool for sending emails

    def send_email(self, subject: str, body: str, recipient: Optional[str] = None):
        """Submit email sending task to the thread pool."""
        if not self.config.EMAIL_USER or not self.config.EMAIL_PASS:
            self.logger.log_info("Email credentials not configured. Skipping email notification.")
            return
            
        to_email = recipient if recipient else self.config.EMAIL_USER # Default to self
        if not to_email:
             self.logger.log_error(ValueError("No recipient specified for email notification."))
             return
             
        # Submit the actual sending logic to the thread pool
        self.executor.submit(self._send_email_task, subject, body, to_email)
        self.logger.log_info(f"Email task submitted for subject: {subject}")

    def _send_email_task(self, subject: str, body: str, to_email: str):
        """The actual email sending logic, run in a worker thread."""
        try:
            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = self.config.EMAIL_USER
            msg['To'] = to_email
            
            # Enforce TLSv1.2 or higher
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            
            self.logger.log_info(f"Connecting to email server {self.config.EMAIL_HOST}:{self.config.EMAIL_PORT}...")
            if self.config.EMAIL_USE_TLS:
                # Use STARTTLS
                with smtplib.SMTP(self.config.EMAIL_HOST, self.config.EMAIL_PORT, timeout=20) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(self.config.EMAIL_USER, self.config.EMAIL_PASS)
                    server.send_message(msg)
            else:
                # Use implicit TLS/SSL
                with smtplib.SMTP_SSL(self.config.EMAIL_HOST, self.config.EMAIL_PORT, context=context, timeout=20) as server:
                    server.login(self.config.EMAIL_USER, self.config.EMAIL_PASS)
                    server.send_message(msg)
            
            self.logger.log_info(f"Email sent successfully to {to_email}")
        except smtplib.SMTPAuthenticationError as e:
             self.logger.log_error(e, "Email failed: Authentication error. Check username/password and app-specific passwords if using Gmail.")
        except smtplib.SMTPConnectError as e:
             self.logger.log_error(e, f"Email failed: Connection error connecting to {self.config.EMAIL_HOST}:{self.config.EMAIL_PORT}.")
        except ssl.SSLError as e:
             self.logger.log_error(e, "Email failed: SSL error. Check TLS settings and certificates.")
        except Exception as e:
            self.logger.log_error(e, f"Email failed unexpectedly when sending to {to_email}")
            
    def shutdown(self):
        """Shutdown the thread pool executor."""
        self.logger.log_info("Shutting down notifier thread pool...")
        self.executor.shutdown(wait=True)
        self.logger.log_info("Notifier thread pool shut down.")

# ====================
#       GUI
# ====================
class AccessControlGUI:
    """
    GUI for the NFC Access Control System using Tkinter and ttk.
    Handles updates from background threads safely using a queue.
    """
    
    def __init__(self, root: Tk, hardware: HardwareController, db_manager: SecureDatabaseManager, logger_obj: ProfessionalLogger, notifier: Notifier):
        """Initialize the GUI. Takes dependencies as arguments."""
        self.root = root
        self.hardware = hardware
        self.db = db_manager
        self.logger = logger_obj
        self.notifier = notifier
        
        self.root.title("NFC Access Control System")
        self.root.geometry("850x650")
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing) # Handle window close
        
        # Queue for cross-thread communication
        self.gui_queue = queue.Queue()
        
        self._setup_styles()
        self._setup_ui()
        self._start_periodic_updates()
        self.logger.log_info("GUI Initialized.")

    def _setup_styles(self):
        """Configure ttk styles."""
        self.style = ttk.Style()
        self.style.theme_use('clam') # Use a modern theme
        self.style.configure("Emergency.TButton", foreground="#dc3545", font=('Segoe UI', 10, 'bold'))
        self.style.configure("Action.TButton", foreground="#007bff", font=('Segoe UI', 10))
        self.style.configure("TLabelframe.Label", font=('Segoe UI', 11, 'bold'), foreground="#0056b3")
        self.style.configure("TLabel", font=('Segoe UI', 10))
        self.style.configure("Status.TLabel", font=('Segoe UI', 10, 'bold'))
        self.style.map("Emergency.TButton", background=[('active', '#f8d7da')])

    def _setup_ui(self) -> None:
        """Setup the main UI components using ttk widgets."""
        main_frame = ttk.Frame(self.root, padding="10 10 10 10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        #   Left Panel (Status & Controls)
        left_panel = ttk.Frame(main_frame)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        # Status frame
        status_frame = ttk.LabelFrame(left_panel, text="System Status", padding=10)
        status_frame.pack(fill=tk.X, pady=5)
        
        self.status_var = tk.StringVar(value="Initializing...")
        self.health_var = tk.StringVar(value="Health: Unknown")
        self.temp_var = tk.StringVar(value="Temp: --.-")
        
        ttk.Label(status_frame, text="Overall:" ).grid(row=0, column=0, sticky=tk.W)
        ttk.Label(status_frame, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=1, sticky=tk.W, padx=5)
        ttk.Label(status_frame, textvariable=self.health_var).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(5,0))
        ttk.Label(status_frame, textvariable=self.temp_var).grid(row=2, column=0, columnspan=2, sticky=tk.W)

        # Access Control frame
        access_frame = ttk.LabelFrame(left_panel, text="Last Access Attempt", padding=10)
        access_frame.pack(fill=tk.X, pady=5)
        
        self.card_var = tk.StringVar(value="Card ID: None")
        self.access_status_var = tk.StringVar(value="Status: Waiting")
        self.access_time_var = tk.StringVar(value="Time: --:--:--")
        
        ttk.Label(access_frame, textvariable=self.card_var).pack(anchor=tk.W)
        ttk.Label(access_frame, textvariable=self.access_status_var, style="Status.TLabel").pack(anchor=tk.W)
        ttk.Label(access_frame, textvariable=self.access_time_var).pack(anchor=tk.W)

        # Control Buttons frame
        control_frame = ttk.LabelFrame(left_panel, text="Manual Controls", padding=10)
        control_frame.pack(fill=tk.X, pady=5)
        
        open_button = ttk.Button(control_frame, text="Open Gate", command=self._manual_open, style="Action.TButton")
        open_button.pack(fill=tk.X, pady=2)
        
        close_button = ttk.Button(control_frame, text="Close Gate", command=self._manual_close, style="Action.TButton")
        close_button.pack(fill=tk.X, pady=2)
        
        buzz_button = ttk.Button(control_frame, text="Test Buzzer", command=self._test_buzzer, style="Action.TButton")
        buzz_button.pack(fill=tk.X, pady=2)
        
        # Emergency Button
        emergency_button = ttk.Button(
            left_panel, 
            text="Emergency Stop Hardware", 
            command=self._emergency_stop, 
            style="Emergency.TButton"
        )
        emergency_button.pack(fill=tk.X, pady=10)

        #   Right Panel (Logs & Management)
        right_panel = ttk.Frame(main_frame)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Log frame
        log_frame = ttk.LabelFrame(right_panel, text="System Logs", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=15, width=60, font=('Consolas', 9), relief=tk.SUNKEN, borderwidth=1)
        log_scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=log_scrollbar.set)
        
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED) # Read-only

        # Management Buttons (Placeholder - could open new windows)
        mgmt_frame = ttk.LabelFrame(right_panel, text="Management", padding=10)
        mgmt_frame.pack(fill=tk.X, pady=5)
        
        config_button = ttk.Button(mgmt_frame, text="Manage Cards...", command=self._show_card_manager, style="Action.TButton")
        config_button.pack(side=tk.LEFT, padx=5)
        
        view_audit_button = ttk.Button(mgmt_frame, text="View Audit Log...", command=self._show_audit_log, style="Action.TButton")
        view_audit_button.pack(side=tk.LEFT, padx=5)

    def _start_periodic_updates(self) -> None:
        """Start timers for updating health, logs, and processing queue."""
        self._update_health_display()
        self._process_gui_queue()
        self.root.after(1000, self._start_periodic_updates) # Repeat every second

    def _update_health_display(self) -> None:
        """Update system health display elements."""
        try:
            health = self.hardware.check_health()
            status_text = "Ready" if health["initialized"] else "Hardware Error"
            health_text = f"Health: {health['nfc_status']} NFC, {health['gpio_status']} GPIO (Errors: {health['error_count']})"
            temp_text = f"Temp: {self.get_last_temp_reading():.1f}°C" if self.get_last_temp_reading() is not None else "Temp: --.-"
            
            self.status_var.set(status_text)
            self.health_var.set(health_text)
            self.temp_var.set(temp_text)
            
            # Update status label color
            status_label = self.status_var.trace_info()[0][1] # Better to store ref.
            # Find the actual label widget associated with status_var if possible
            # self.status_label_widget.config(foreground="green" if health["initialized"] else "red")
            
        except Exception as e:
            self.logger.log_error(e, "GUI failed to update health display")
            self.status_var.set("Error Updating")
            self.health_var.set("Health: Error")
            self.temp_var.set("Temp: Error")
            
    def get_last_temp_reading(self) -> Optional[float]:
         """Safely get the last temperature reading from the monitor thread."""
         # Assumes temp_monitor instance is accessible, needs proper passing
         global temp_monitor # Using global for now, fix with DI
         if temp_monitor and hasattr(temp_monitor, 'last_temp'):
             return temp_monitor.last_temp
         return None

    def _process_gui_queue(self) -> None:
        """Process messages from the background threads via the queue."""
        try:
            while True: # Process all messages currently in queue
                message = self.gui_queue.get_nowait()
                
                if isinstance(message, str): # Simple log message
                    self._append_log(message)
                elif isinstance(message, dict):
                    msg_type = message.get("type")
                    if msg_type == "access_update":
                        self._update_access_display(message.get("card_id"), message.get("status"), message.get("timestamp"))
                    # Add other message types as needed
                    
        except queue.Empty:
            pass # No more messages
        except Exception as e:
            self._append_log(f"ERROR processing GUI queue: {e}")

    def _append_log(self, log_message: str):
        """Append a message to the log text widget."""
        try:
            self.log_text.config(state=tk.NORMAL)
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_text.insert(tk.END, f"[{timestamp}] {log_message}\n")
            self.log_text.see(tk.END) # Scroll to the end
            self.log_text.config(state=tk.DISABLED)
        except Exception as e:
            print(f"Error appending log to GUI: {e}") # Print directly if GUI log fails
            
    def _update_access_display(self, card_id: Optional[str], status: Optional[AccessStatus], timestamp: Optional[datetime]):
        """Update the 'Last Access Attempt' display."""
        self.card_var.set(f"Card ID: {card_id if card_id else 'N/A'}")
        status_name = status.name if status else "Unknown"
        self.access_status_var.set(f"Status: {status_name}")
        time_str = timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else "--:--:--"
        self.access_time_var.set(f"Time: {time_str}")
        
        # Update status label color based on status
        color = "green" if status == AccessStatus.GRANTED else "red" if status else "black"
        # Find the actual label widget associated with access_status_var if possible
        # self.access_status_label_widget.config(foreground=color)

    def _manual_open(self):
        """Handle manual gate open button press."""
        self.logger.log_audit("manual_gate_open", {"user": "GUI"})
        self.hardware.control_servo(open_gate=True)
        self.gui_queue.put("Manual gate open triggered.")
        # Optionally buzz or notify
        self.hardware.buzz(0.1)

    def _manual_close(self):
        """Handle manual gate close button press."""
        self.logger.log_audit("manual_gate_close", {"user": "GUI"})
        self.hardware.control_servo(open_gate=False)
        self.gui_queue.put("Manual gate close triggered.")
        
    def _test_buzzer(self):
        """Handle test buzzer button press."""
        self.logger.log_audit("manual_buzzer_test", {"user": "GUI"})
        self.hardware.buzz(0.5) # Longer buzz for test
        self.gui_queue.put("Manual buzzer test triggered.")

    def _emergency_stop(self):
        """Handle emergency stop button press."""
        if messagebox.askyesno("Confirm Emergency Stop", "This will attempt to stop all hardware operations (servo, fan, buzzer) and cleanup GPIO. Proceed?"):
            self.logger.log_audit("emergency_stop_triggered", {"user": "GUI"})
            self.gui_queue.put("EMERGENCY STOP ACTIVATED")
            try:
                # Attempt immediate hardware cleanup
                self.hardware._cleanup() 
                self.status_var.set("EMERGENCY STOPPED")
                self.health_var.set("Health: STOPPED")
                # Disable control buttons after stop? Maybe.
            except Exception as e:
                self.logger.log_error(e, "Error during emergency stop cleanup")
                messagebox.showerror("Stop Error", f"Error during emergency stop: {e}")

    def _show_card_manager(self):
        """Placeholder for showing a card management window."""
        # This would typically open a Toplevel window
        messagebox.showinfo("Card Manager", "Card management interface not fully implemented yet.")
        # Example: CardManagerWindow(self.root, self.db, self.logger)

    def _show_audit_log(self):
        """Placeholder for showing the audit log."""
        # This could open a Toplevel window displaying audit_log table contents
        messagebox.showinfo("Audit Log", "Audit log viewer not fully implemented yet.")
        # Example: AuditLogViewer(self.root, self.db, self.logger)
        
    def _on_closing(self):
        """Handle the window close event."""
        if messagebox.askokcancel("Quit", "Do you want to quit the NFC Access Control System?"):
            self.logger.log_info("GUI closing signal received.")
            self.root.destroy() # Close the Tkinter window
            # Signal background threads to stop (implement stop methods)
            # Perform cleanup (handled by main application loop)

# ====================
# Main Application Logic
# ====================
class NFCAccessControlApp:
    def __init__(self):
        # Dependency Injection: Create instances and pass them
        self.logger = logger # Use the global logger for now, or instantiate ProfessionalLogger here
        self.config = config # Use the global config for now, or instantiate Config here
        self.hardware = HardwareController(self.config, self.logger)
        self.db = SecureDatabaseManager(self.config, self.logger)
        self.notifier = Notifier(self.config, self.logger)
        
        self.temp_monitor = None
        self.nfc_poll_thread = None
        self.stop_event = threading.Event()
        
        self.gui = None # GUI will be created later if needed

    def start_background_tasks(self):
        """Start monitoring threads."""
        if self.hardware._is_initialized: # Only start if hardware is okay
            # Start Temperature Monitor Thread
            global temp_monitor # Access global for GUI update (fix with DI)
            temp_monitor = TemperatureMonitor(self.hardware, self.config, self.logger)
            temp_monitor.start()
            self.temp_monitor = temp_monitor # Store reference
            
            # Start NFC Polling Thread
            self.nfc_poll_thread = threading.Thread(target=self._nfc_polling_loop, daemon=True)
            self.nfc_poll_thread.start()
            self.logger.log_info("Background tasks (Temp Monitor, NFC Poll) started.")
        else:
            self.logger.log_error(RuntimeError("Hardware not initialized. Background tasks not started."), "Startup")

    def _nfc_polling_loop(self):
        """Continuously poll for NFC cards."""
        self.logger.log_info("NFC polling thread started.")
        while not self.stop_event.is_set():
            card_info = self.hardware.read_card()
            if card_info:
                self.logger.log_info(f"Card detected: {card_info.id}")
                self.hardware.buzz(0.05) # Short buzz on detection
                self.process_card_access(card_info.id)
                time.sleep(2) # Pause after processing a card to avoid immediate re-scan
            else:
                # No card detected, wait briefly before polling again
                self.stop_event.wait(0.5) # Wait for 0.5 seconds or until stop event
                
        self.logger.log_info("NFC polling thread stopped.")

    def process_card_access(self, card_id: str):
        """Check card validity and grant/deny access."""
        start_time = time.time()
        access_status = AccessStatus.DENIED # Default to denied
        details = ""
        card_details = None
        
        try:
            card_details = self.db.get_card_info(card_id)
            
            if card_details:
                if card_details.is_valid:
                    access_status = AccessStatus.GRANTED
                    details = f"Access granted to {card_details.name or 'authorized user'}."
                    self.logger.log_info(details)
                    self.hardware.control_servo(open_gate=True) # Open the gate
                    # Optionally keep gate open for a duration then close automatically
                    # time.sleep(5) 
                    # self.hardware.control_servo(open_gate=False)
                else:
                    access_status = AccessStatus.DENIED
                    details = f"Access denied. Card {card_id} is inactive or expired."
                    if card_details.expiry_date and card_details.expiry_date.date() < datetime.now().date():
                         details += f" (Expired: {card_details.expiry_date.date()})"
                    elif not card_details.is_valid: # Check if it was explicitly inactive
                         details += " (Inactive)"
                    self.logger.log_warning(details)
                    self.hardware.buzz(0.3) # Longer buzz for denial
            else:
                access_status = AccessStatus.DENIED
                details = f"Access denied. Card {card_id} not found in authorized list."
                self.logger.log_warning(details)
                self.hardware.buzz(0.3)
                # Optionally notify admin about unknown card attempt
                # self.notifier.send_email("Unknown Card Scan Attempt", f"Card ID: {card_id} attempted access.")
                
        except Exception as e:
            access_status = AccessStatus.DENIED
            details = f"Error processing card {card_id}: {e}"
            self.logger.log_error(e, f"Error during card access processing for {card_id}")
            self.hardware.buzz(0.5) # Error buzz
            
        finally:
            response_time = time.time() - start_time
            # Log access attempt to DB
            self.db.log_access_attempt(card_id, access_status, details)
            # Log access attempt for metrics/general log
            log_card_info = card_details if card_details else CardInfo(id=card_id) # Use fetched details if available
            self.logger.log_access(log_card_info, access_status, response_time)
            
            # Update GUI if running
            if self.gui:
                update_msg = {
                    "type": "access_update",
                    "card_id": card_id,
                    "status": access_status,
                    "timestamp": datetime.now()
                }
                self.gui.gui_queue.put(update_msg)

    def run_gui(self):
        """Initialize and run the Tkinter GUI."""
        self.logger.log_info("Starting GUI...")
        root = Tk()
        self.gui = AccessControlGUI(root, self.hardware, self.db, self.logger, self.notifier)
        self.start_background_tasks() # Start tasks after GUI is ready
        root.mainloop() # Blocks until GUI is closed
        
        #   Cleanup after GUI closes 
        self.shutdown()

    def shutdown(self):
        """Perform cleanup of all resources."""
        self.logger.log_info("Initiating application shutdown...")
        self.stop_event.set() # Signal threads to stop
        
        # Stop background threads
        if self.temp_monitor and self.temp_monitor.is_alive():
            self.temp_monitor.stop()
            self.temp_monitor.join(timeout=5)
            if self.temp_monitor.is_alive():
                 self.logger.log_warning("Temperature monitor thread did not stop gracefully.")
                 
        if self.nfc_poll_thread and self.nfc_poll_thread.is_alive():
            self.nfc_poll_thread.join(timeout=5)
            if self.nfc_poll_thread.is_alive():
                 self.logger.log_warning("NFC polling thread did not stop gracefully.")
        
        # Shutdown notifier thread pool
        if self.notifier:
            self.notifier.shutdown()
            
        # Cleanup hardware
        if self.hardware:
            self.hardware._cleanup()
            
        # Close database connection
        if self.db:
            self.db.close()
            
        self.logger.log_info("Application shutdown complete.")

# ====================
# Entry Point
# ====================
if __name__ == "__main__":
    # Recommendation: Add proper argument parsing (argparse)
    # e.g., to run setup, run without GUI, specify config file
    
    # Check if running setup is requested (simple check)
    if "--setup" in sys.argv:
        print("Running interactive credential setup...")
        Authenticator.setup_credentials_interactively()
        print("Setup complete. You can now run the application.")
        sys.exit(0)
        
    #   Bypassing authentication for GUI demonstration 
    print("Bypassing authentication for GUI demonstration...")
    authenticated_user = "demo_user"
    #   End Bypass

    # print("Authenticating user...")
    # if not Authenticator.authenticate():
    #     logger.log_error(RuntimeError("Authentication failed or cancelled."), "Startup")
    #     print("Authentication failed. Exiting.")
    #     sys.exit(1)

    print("Starting application...") # Removed "Authentication successful."
    logger.log_audit("application_start", {"user": authenticated_user}) # Use demo_user
    
    app = NFCAccessControlApp()
    
    try:
        # Run the application with GUI
        app.run_gui()
        
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received. Shutting down...")
        logger.log_audit("application_shutdown", {"reason": "KeyboardInterrupt"})
        app.shutdown()
    except Exception as e:
        logger.log_error(e, "CRITICAL: Unhandled exception in main application loop.")
        logger.log_audit("application_shutdown", {"reason": f"Unhandled Exception: {e}"})
        # Attempt graceful shutdown even on error
        app.shutdown()
        sys.exit(1)
    else:
        # Normal exit after GUI closes
        logger.log_audit("application_shutdown", {"reason": "Normal GUI close"})
        # Shutdown is called within run_gui after mainloop finishes
        sys.exit(0)
