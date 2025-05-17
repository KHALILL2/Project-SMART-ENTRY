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
        def activate(self, clf, target):
            print("MockNFC: Activating target")
            if self._target_present:
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

from tkinter import Tk, Label, Button, messagebox, Entry, Toplevel, Text, END
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

@dataclass
class SystemMetrics:
    total_requests: int = 0
    successful_accesses: int = 0
    failed_accesses: int = 0
    average_response_time: float = 0.0
    system_uptime: float = 0.0
    last_health_check: Optional[datetime] = None

class ProfessionalLogger:
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
        self.log_queue = queue.Queue()

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
        self.log_queue.put(f"INFO: Access attempt - Card: {card_info.id}, Status: {status.name}")
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
        self.log_queue.put(f"{severity}: {context} - {error}")

    def log_audit(self, action: str, details: Dict[str, Any]) -> None:
        audit_data = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'details': details,
        }
        msg = json.dumps(audit_data)
        self.audit_logger.info(msg)
        self.log_queue.put(f"AUDIT: {action} - {details.get('card_id', '')}")

    def log_info(self, message: str) -> None:
        self.logger.info(message)
        self.log_queue.put(f"INFO: {message}")

    def get_recent_logs(self, max_logs=100) -> List[str]:
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

    def _get_current_metrics(self) -> Dict[str, Any]:
        return {
            'total_requests': self.metrics.total_requests,
            'successful_accesses': self.metrics.successful_accesses,
            'failed_accesses': self.metrics.failed_accesses,
            'average_response_time': round(self.metrics.average_response_time, 4),
            'system_uptime': round(self.metrics.system_uptime, 2),
            'last_health_check': self.metrics.last_health_check.isoformat() if self.metrics.last_health_check else None
        }

logger = ProfessionalLogger()

class Config:
    DEFAULT_VALID_PINS = [2,3,4,17,18,22,23,24,25,26,27]
    DEFAULT_THERMAL_FILE = "/sys/class/thermal/thermal_zone0/temp"
    CONFIG_FILE = 'config.ini'

    def __init__(self):
        self.config = configparser.ConfigParser()
        try:
            os.umask(0o077)
        except Exception as e:
            logger.log_error(e, "Failed to set umask")
        if not os.path.exists(self.CONFIG_FILE):
            self._create_default_config()
            logger.log_info(f"Created default config file: {self.CONFIG_FILE}")
        self.config.read(self.CONFIG_FILE)
        try:
            self.EMAIL_USER = keyring.get_password("nfc_gate", "email_user")
            self.EMAIL_PASS = keyring.get_password("nfc_gate", "email_pass")
        except Exception as e:
            logger.log_error(e, "Failed to retrieve credentials from keyring")
            self.EMAIL_USER = None
            self.EMAIL_PASS = None
        self.EMAIL_HOST = self.config.get('email', 'host', fallback='smtp.gmail.com')
        self.EMAIL_PORT = self.config.getint('email', 'port', fallback=587)
        self.EMAIL_USE_TLS = self.config.getboolean('email', 'use_tls', fallback=True)
        self.VALID_PINS = self._parse_list(self.config.get('gpio', 'valid_pins', fallback=str(self.DEFAULT_VALID_PINS)), int)
        self.SERVO_PIN = self._validate_pin(self.config.getint('gpio', 'servo', fallback=18))
        self.FAN_PIN = self._validate_pin(self.config.getint('gpio', 'fan', fallback=23))
        self.BUZZER_PIN = self._validate_pin(self.config.getint('gpio', 'buzzer', fallback=24))
        self.SOLENOID_PIN = self._validate_pin(self.config.getint('gpio', 'solenoid', fallback=25))
        self.SERVO_OPEN_DUTY = self._validate_duty(self.config.getfloat('servo', 'open', fallback=7.5))
        self.SERVO_CLOSE_DUTY = self._validate_duty(self.config.getfloat('servo', 'close', fallback=2.5))
        self.SERVO_DELAY = max(0.1, self.config.getfloat('servo', 'delay', fallback=1.5))
        self.FAN_ON_TEMP = min(max(30, self.config.getfloat('temperature', 'on', fallback=60)), 90)
        self.FAN_OFF_TEMP = min(max(25, self.config.getfloat('temperature', 'off', fallback=50)), 85)
        self.THERMAL_FILE = self.config.get('temperature', 'thermal_file', fallback=self.DEFAULT_THERMAL_FILE)
        self.NFC_MAX_ATTEMPTS = self.config.getint('nfc', 'max_attempts', fallback=10)
        self.NFC_TIMEOUT = self.config.getint('nfc', 'timeout', fallback=30)
        self.NFC_PROTOCOL = self.config.get('nfc', 'protocol', fallback='106A')
        self.DB_PATH = self.config.get('database', 'path', fallback='cards.db')
        self.DB_ENCRYPTED = self.config.getboolean('database', 'encrypted', fallback=True)

    def _create_default_config(self):
        default_config = configparser.ConfigParser()
        default_config['email'] = {
            'host': 'smtp.gmail.com',
            'port': '587',
            'use_tls': 'True'
        }
        default_config['gpio'] = {
            'valid_pins': str(self.DEFAULT_VALID_PINS),
            'servo': '18',
            'fan': '23',
            'buzzer': '24',
            'solenoid': '25'
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
        try:
            list_str = list_str.strip('[] ')
            return [item_type(item.strip()) for item in list_str.split(',')]
        except Exception as e:
            logger.log_error(e, f"Failed to parse list from config: {list_str}")
            return []

    def _validate_pin(self, pin):
        if pin in self.VALID_PINS:
            return pin
        else:
            logger.log_error(ValueError(f"Invalid pin {pin}. Falling back to default 18."), "Config")
            return 18

    def _validate_duty(self, duty):
        return min(max(2.5, duty), 12.5)

class ConfigValidator:
    @staticmethod
    def validate_config(config_obj: Config) -> bool:
        try:
            if not config_obj.EMAIL_HOST or not config_obj.EMAIL_PORT:
                raise ValueError("Email configuration incomplete")
            if config_obj.EMAIL_USER is None or config_obj.EMAIL_PASS is None:
                logger.log_info("Email user/pass not found in keyring")
            for pin in [config_obj.SERVO_PIN, config_obj.FAN_PIN, config_obj.BUZZER_PIN, config_obj.SOLENOID_PIN]:
                if pin not in config_obj.VALID_PINS:
                    raise ValueError(f"Invalid pin: {pin}")
            if not (2.5 <= config_obj.SERVO_OPEN_DUTY <= 12.5) or not (2.5 <= config_obj.SERVO_CLOSE_DUTY <= 12.5):
                raise ValueError("Invalid servo duty cycle")
            if config_obj.SERVO_DELAY <= 0:
                raise ValueError(f"Invalid servo delay: {config_obj.SERVO_DELAY}")
            if config_obj.FAN_ON_TEMP <= config_obj.FAN_OFF_TEMP:
                raise ValueError("Fan ON temp must be > OFF temp")
            if not os.path.exists(config_obj.THERMAL_FILE):
                logger.log_error(FileNotFoundError(f"Thermal file not found: {config_obj.THERMAL_FILE}"))
            if config_obj.NFC_MAX_ATTEMPTS < 1 or config_obj.NFC_TIMEOUT < 1:
                raise ValueError("NFC settings must be positive")
            if not config_obj.DB_PATH:
                raise ValueError("Database path required")
            logger.log_info("Configuration validation successful")
            return True
        except Exception as e:
            logger.log_error(e, "Configuration validation failed")
            return False

config = Config()
if not ConfigValidator.validate_config(config):
    logger.log_error(RuntimeError("CRITICAL: Configuration validation failed"), "Startup")
    sys.exit(1)

class Authenticator:
    SERVICE_NAME = "nfc_gate"
    ADMIN_USER_KEY = "admin_user"
    ADMIN_PASS_KEY = "admin_pass"

    @staticmethod
    def setup_credentials_interactively():
        try:
            if not keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY):
                print("Setting up admin credentials...")
                username = input("Enter admin username: ")
                password = input("Enter admin password: ")
                keyring.set_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY, username)
                keyring.set_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_PASS_KEY, password)
                print("Admin credentials stored securely in keyring.")
        except Exception as e:
            logger.log_error(e, "Failed to setup credentials interactively")

    @staticmethod
    def authenticate():
        try:
            stored_user = keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_USER_KEY)
            stored_pass = keyring.get_password(Authenticator.SERVICE_NAME, Authenticator.ADMIN_PASS_KEY)
        except Exception as e:
            logger.log_error(e, "Failed to retrieve credentials")
            messagebox.showerror("Authentication Error", "Could not retrieve credentials")
            return False
        if not stored_user or not stored_pass:
            messagebox.showerror("Setup Required", "Admin credentials not set")
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
                    messagebox.showerror("Login Failed", "Maximum attempts reached")
                    root.destroy()
        login_button = Button(root, text="Login", command=check_credentials)
        login_button.pack(pady=10)
        root.update_idletasks()
        x = (root.winfo_screenwidth() // 2) - (root.winfo_width() // 2)
        y = (root.winfo_screenheight() // 2) - (root.winfo_height() // 2)
        root.geometry(f"+{x}+{y}")
        root.mainloop()
        return authenticated

class HardwareController:
    def __init__(self, config_obj: Config, logger_obj: ProfessionalLogger) -> None:
        self.config = config_obj
        self.logger = logger_obj
        self._lock = threading.Lock()
        self._nfc_reader = None
        self._servo_pwm = None
        self._is_initialized = False
        self._last_health_check = None
        self._error_count = 0
        self._max_retries = 3
        self._lock_state = True  # True = locked, False = unlocked
        try:
            self._initialize_hardware()
        except Exception as e:
            self.logger.log_error(e, "Hardware initialization failed")
            raise

    def _initialize_hardware(self) -> None:
        with self._lock:
            if self._is_initialized:
                return
            try:
                self.logger.log_info("Initializing hardware...")
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(self.config.SERVO_PIN, GPIO.OUT)
                GPIO.setup(self.config.FAN_PIN, GPIO.OUT)
                GPIO.setup(self.config.BUZZER_PIN, GPIO.OUT)
                GPIO.setup(self.config.SOLENOID_PIN, GPIO.OUT)
                GPIO.output(self.config.FAN_PIN, GPIO.LOW)
                GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
                GPIO.output(self.config.SOLENOID_PIN, GPIO.HIGH)  # Start locked
                self._servo_pwm = GPIO.PWM(self.config.SERVO_PIN, 50)
                self._servo_pwm.start(self.config.SERVO_CLOSE_DUTY)
                time.sleep(0.5)
                self._servo_pwm.ChangeDutyCycle(0)
                self._nfc_reader = self._get_nfc_reader_with_retry()
                if not self._nfc_reader:
                    raise RuntimeError("Failed to initialize NFC reader")
                self._is_initialized = True
                self.logger.log_audit("hardware_initialized", {
                    "servo_pin": self.config.SERVO_PIN,
                    "fan_pin": self.config.FAN_PIN,
                    "buzzer_pin": self.config.BUZZER_PIN,
                    "solenoid_pin": self.config.SOLENOID_PIN,
                    "nfc_status": "OK",
                    "status": "success"
                })
            except Exception as e:
                self.logger.log_error(e, "Hardware initialization failed")
                self._cleanup()
                raise

    def _get_nfc_reader_with_retry(self):
        base_delay = 2
        for attempt in range(1, self.config.NFC_MAX_ATTEMPTS + 1):
            try:
                clf = nfc.ContactlessFrontend('usb')
                if clf:
                    self.logger.log_info(f"NFC reader initialized on attempt {attempt}")
                    return clf
            except Exception as e:
                delay = min(base_delay * attempt, 30)
                self.logger.log_error(e, f"NFC init failed (attempt {attempt}). Retrying in {delay}s...")
                if attempt == self.config.NFC_MAX_ATTEMPTS:
                    return None
                time.sleep(delay)
        return None

    def _cleanup(self) -> None:
        self.logger.log_info("Cleaning up hardware resources...")
        with self._lock:
            try:
                if self._servo_pwm:
                    self._servo_pwm.stop()
                if self._nfc_reader:
                    self._nfc_reader.close()
                    self._nfc_reader = None
                GPIO.cleanup()
                self._is_initialized = False
                self._lock_state = True  # Reset to locked on cleanup
                self.logger.log_info("Hardware cleanup successful")
            except Exception as e:
                self.logger.log_error(e, "Hardware cleanup failed")

    def read_card(self) -> Optional[CardInfo]:
        if not self._is_initialized or not self._nfc_reader:
            self.logger.log_error(RuntimeError("Hardware not initialized"))
            return None
        start_time = time.time()
        retry_count = 0
        while retry_count < self._max_retries:
            if time.time() - start_time > self.config.NFC_TIMEOUT:
                self.logger.log_info(f"NFC read timed out after {self.config.NFC_TIMEOUT}s")
                break
            try:
                with self._lock:
                    target = self._nfc_reader.sense(RemoteTarget(self.config.NFC_PROTOCOL), iterations=1, interval=0.2)
                    if target:
                        tag = nfc.tag.activate(self._nfc_reader, target)
                        if tag and hasattr(tag, 'identifier'):
                            card_id = tag.identifier.hex()
                            self.logger.log_audit("card_read_success", {"card_id": card_id, "attempt": retry_count + 1})
                            return CardInfo(id=card_id)
            except Exception as e:
                retry_count += 1
                self._error_count += 1
                self.logger.log_error(e, f"Card read attempt {retry_count} failed")
                if retry_count >= self._max_retries:
                    return None
                time.sleep(0.2)
            time.sleep(0.1)
        return None

    def control_servo(self, open_gate: bool) -> None:
        if not self._is_initialized or not self._servo_pwm:
            self.logger.log_error(RuntimeError("Hardware not initialized"))
            return
        target_duty = self.config.SERVO_OPEN_DUTY if open_gate else self.config.SERVO_CLOSE_DUTY
        action = "open" if open_gate else "close"
        try:
            with self._lock:
                self.logger.log_info(f"Setting servo to {action} position (Duty: {target_duty})")
                self._servo_pwm.ChangeDutyCycle(target_duty)
                time.sleep(self.config.SERVO_DELAY)
                self._servo_pwm.ChangeDutyCycle(0)
                self.logger.log_audit("servo_control", {"action": action, "duty_cycle": target_duty})
        except Exception as e:
            self._error_count += 1
            self.logger.log_error(e, f"Servo control failed during {action}")

    def control_lock(self, lock: bool) -> None:
        if not self._is_initialized:
            self.logger.log_error(RuntimeError("Hardware not initialized"))
            return
        state = GPIO.HIGH if lock else GPIO.LOW
        action = "LOCK" if lock else "UNLOCK"
        try:
            with self._lock:
                GPIO.output(self.config.SOLENOID_PIN, state)
                self._lock_state = lock
                self.logger.log_info(f"Gate {action}ED")
                self.logger.log_audit("lock_control", {"action": action})
        except Exception as e:
            self._error_count += 1
            self.logger.log_error(e, f"Lock control failed during {action}")

    def get_lock_state(self) -> bool:
        return self._lock_state

    def control_fan(self, state: bool) -> None:
        if not self._is_initialized:
            self.logger.log_error(RuntimeError("Hardware not initialized"))
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
        if not self._is_initialized:
            self.logger.log_error(RuntimeError("Hardware not initialized"))
            return
        try:
            with self._lock:
                GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
            time.sleep(duration)
            with self._lock:
                GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
        except Exception as e:
            self._error_count += 1
            self.logger.log_error(e, "Buzzer control failed")

    def check_health(self) -> Dict[str, Any]:
        current_time = datetime.now()
        self._last_health_check = current_time
        nfc_ok = False
        if self._is_initialized and self._nfc_reader:
            nfc_ok = True
        health_status = {
            "timestamp": current_time.isoformat(),
            "initialized": self._is_initialized,
            "error_count": self._error_count,
            "last_health_check": self._last_health_check.isoformat() if self._last_health_check else None,
            "gpio_status": "OK" if self._is_initialized else "ERROR",
            "nfc_status": "OK" if nfc_ok else "ERROR",
            "lock_status": "Locked" if self._lock_state else "Unlocked"
        }
        self.logger.log_info(f"Health Check: Initialized={self._is_initialized}, NFC OK={nfc_ok}, Errors={self._error_count}")
        return health_status

    def __enter__(self) -> 'HardwareController':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._cleanup()

class SecureDatabaseManager:
    def __init__(self, config_obj: Config, logger_obj: ProfessionalLogger):
        self.config = config_obj
        self.logger = logger_obj
        self._db_lock = threading.Lock()
        self.cipher = None
        try:
            os.umask(0o077)
            db_dir = os.path.dirname(self.config.DB_PATH)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir)
                self.logger.log_info(f"Created database directory: {db_dir}")
            self.conn = sqlite3.connect(self.config.DB_PATH, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            if self.config.DB_ENCRYPTED:
                self._setup_encryption()
            self._init_db()
            self.logger.log_info("Database manager initialized")
        except Exception as e:
            self.logger.log_error(e, "CRITICAL: Database initialization failed")
            raise

    def _setup_encryption(self):
        try:
            db_key = keyring.get_password("nfc_gate", "db_key")
            if not db_key:
                db_key = Fernet.generate_key().decode()
                keyring.set_password("nfc_gate", "db_key", db_key)
                self.logger.log_info("New database key generated")
            self.cipher = Fernet(db_key.encode())
            self.logger.log_info("Database encryption enabled")
        except Exception as e:
            self.logger.log_error(e, "Failed to setup database encryption")
            raise RuntimeError("Failed to setup encryption key")

    def _init_db(self):
        with self._db_lock:
            with self.conn:
                self.conn.execute('''
                    CREATE TABLE IF NOT EXISTS card_scans (
                        scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        card_id TEXT NOT NULL,
                        scan_data TEXT,
                        scan_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                self.conn.execute('''
                    CREATE TABLE IF NOT EXISTS authorized_cards (
                        card_id TEXT PRIMARY KEY,
                        holder_name TEXT,
                        expiry_date DATE,
                        is_active BOOLEAN DEFAULT 1,
                        added_by TEXT,
                        added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        last_modified DATETIME DEFAULT CURRENT_TIMESTAMP
                    ) WITHOUT ROWID
                ''')
                self.conn.execute('''
                    CREATE TABLE IF NOT EXISTS access_log (
                        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        card_id TEXT,
                        access_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                        status TEXT NOT NULL,
                        details TEXT
                    )
                ''')
                self.conn.execute('''
                    CREATE TABLE IF NOT EXISTS audit_log (
                        audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT,
                        action TEXT NOT NULL,
                        target TEXT,
                        details TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_scan_timestamp ON card_scans(scan_timestamp)')
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_auth_expiry ON authorized_cards(expiry_date)')
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_auth_active ON authorized_cards(is_active)')
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_access_time ON access_log(access_time)')
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp)')
                self.conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)')
        self.logger.log_info("Database schema initialized")

    def _encrypt(self, data: Optional[str]) -> Optional[str]:
        if self.cipher and data is not None:
            try:
                return self.cipher.encrypt(data.encode()).decode()
            except Exception as e:
                self.logger.log_error(e, "Encryption failed")
                return None
        return data

    def _decrypt(self, data: Optional[str]) -> Optional[str]:
        if self.cipher and data is not None:
            try:
                return self.cipher.decrypt(data.encode()).decode()
            except InvalidToken as e:
                self.logger.log_error(e, "Decryption failed: Invalid token")
                return None
            except Exception as e:
                self.logger.log_error(e, "Decryption failed")
                return None
        return data

    def log_scan(self, card_id: str, scan_data: Optional[str] = None):
        encrypted_data = self._encrypt(scan_data)
        if scan_data is not None and encrypted_data is None and self.config.DB_ENCRYPTED:
            self.logger.log_error(RuntimeError("Failed to encrypt scan data"), "DB log_scan")
            return
        try:
            with self._db_lock:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO card_scans (card_id, scan_data) VALUES (?, ?)",
                        (card_id, encrypted_data)
                    )
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error logging scan for card {card_id}")

    def log_access_attempt(self, card_id: Optional[str], status: AccessStatus, details: str = ""):
        try:
            with self._db_lock:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO access_log (card_id, status, details) VALUES (?, ?, ?)",
                        (card_id, status.name, details)
                    )
            self.logger.log_info(f"Access attempt logged: Card={card_id}, Status={status.name}")
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error logging access attempt for card {card_id}")

    def log_audit_action(self, action: str, user_id: Optional[str] = None, target: Optional[str] = None, details: Optional[str] = None):
        try:
            with self._db_lock:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO audit_log (user_id, action, target, details) VALUES (?, ?, ?, ?)",
                        (user_id, action, target, details)
                    )
            self.logger.log_info(f"Audit action logged: Action={action}, User={user_id}")
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error logging audit action '{action}'")

    def get_card_info(self, card_id: str) -> Optional[CardInfo]:
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT card_id, holder_name, expiry_date, is_active FROM authorized_cards WHERE card_id = ?",
                (card_id,)
            )
            row = cursor.fetchone()
            if row:
                decrypted_name = self._decrypt(row['holder_name'])
                expiry_dt = None
                if row['expiry_date']:
                    try:
                        expiry_dt = datetime.strptime(row['expiry_date'], '%Y-%m-%d')
                    except ValueError:
                        self.logger.log_error(ValueError(f"Invalid expiry date: {row['expiry_date']}"))
                is_currently_valid = row['is_active'] and (expiry_dt is None or expiry_dt.date() >= datetime.now().date())
                return CardInfo(
                    id=row['card_id'],
                    name=decrypted_name,
                    expiry_date=expiry_dt,
                    is_valid=is_currently_valid
                )
            return None
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error retrieving info for card {card_id}")
            return None

    def add_or_update_card(self, card_id: str, holder_name: Optional[str], expiry_date: Optional[datetime], is_active: bool, added_by: str) -> bool:
        encrypted_name = self._encrypt(holder_name)
        if holder_name is not None and encrypted_name is None and self.config.DB_ENCRYPTED:
            self.logger.log_error(RuntimeError("Failed to encrypt holder name"), "DB add_or_update_card")
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
                        self.conn.execute('''
                            UPDATE authorized_cards 
                            SET holder_name = ?, expiry_date = ?, is_active = ?, last_modified = ?, added_by = ?
                            WHERE card_id = ?
                        ''', (encrypted_name, expiry_str, is_active, now_ts, added_by, card_id))
                        action = "CARD_UPDATED"
                    else:
                        self.conn.execute('''
                            INSERT INTO authorized_cards 
                            (card_id, holder_name, expiry_date, is_active, added_by, added_at, last_modified)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (card_id, encrypted_name, expiry_str, is_active, added_by, now_ts, now_ts))
                        action = "CARD_ADDED"
            self.log_audit_action(action=action, user_id=added_by, target=card_id,
                                  details=f"Name: {holder_name}, Expires: {expiry_str}, Active: {is_active}")
            return True
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error adding/updating card {card_id}")
            return False

    def remove_card(self, card_id: str, removed_by: str) -> bool:
        try:
            with self._db_lock:
                with self.conn:
                    cursor = self.conn.execute("DELETE FROM authorized_cards WHERE card_id = ?", (card_id,))
                    if cursor.rowcount > 0:
                        self.log_audit_action(action="CARD_REMOVED", user_id=removed_by, target=card_id)
                        return True
                    return False
        except sqlite3.Error as e:
            self.logger.log_error(e, f"DB error removing card {card_id}")
            return False

    def get_authorized_cards(self, include_inactive=False) -> List[CardInfo]:
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
                expiry_dt = None
                if row['expiry_date']:
                    try:
                        expiry_dt = datetime.strptime(row['expiry_date'], '%Y-%m-%d')
                    except ValueError:
                        expiry_dt = None
                cards.append(CardInfo(
                    id=row['card_id'],
                    name=decrypted_name,
                    expiry_date=expiry_dt,
                    is_valid=bool(row['is_active'])
                ))
            return cards
        except sqlite3.Error as e:
            self.logger.log_error(e, "DB error retrieving authorized cards")
            return []

    def close(self):
        if self.conn:
            try:
                self.conn.close()
                self.logger.log_info("Database connection closed")
            except sqlite3.Error as e:
                self.logger.log_error(e, "Error closing database connection")

class TemperatureMonitor(threading.Thread):
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
        self.logger.log_info("Temperature monitor thread started")
        while not self._stop_event.is_set():
            try:
                if not os.path.exists(self.thermal_file):
                    self.logger.log_error(FileNotFoundError(f"Thermal file not found: {self.thermal_file}"))
                    self._stop_event.wait(60)
                    continue
                with open(self.thermal_file) as f:
                    temp = float(f.read().strip()) / 1000
                    self.last_temp = temp
                if not self.fan_on and temp > self.config.FAN_ON_TEMP:
                    self.hardware.control_fan(True)
                    self.fan_on = True
                    self.logger.log_info(f"Fan turned ON (Temp: {temp:.1f}°C)")
                elif self.fan_on and temp < self.config.FAN_OFF_TEMP:
                    self.hardware.control_fan(False)
                    self.fan_on = False
                    self.logger.log_info(f"Fan turned OFF (Temp: {temp:.1f}°C)")
            except Exception as e:
                self.logger.log_error(e, "Temp monitor error")
                self._stop_event.wait(10)
            else:
                self._stop_event.wait(5)
        self.logger.log_info("Temperature monitor thread stopped")
        if self.fan_on:
            self.hardware.control_fan(False)

    def stop(self):
        self._stop_event.set()

class Notifier:
    def __init__(self, config_obj: Config, logger_obj: ProfessionalLogger):
        self.config = config_obj
        self.logger = logger_obj
        self.executor = ThreadPoolExecutor(max_workers=2)

    def send_email(self, subject: str, body: str, recipient: Optional[str] = None):
        if not self.config.EMAIL_USER or not self.config.EMAIL_PASS:
            self.logger.log_info("Email credentials not configured")
            return
        to_email = recipient if recipient else self.config.EMAIL_USER
        if not to_email:
            self.logger.log_error(ValueError("No recipient specified"))
            return
        self.executor.submit(self._send_email_task, subject, body, to_email)
        self.logger.log_info(f"Email task submitted for subject: {subject}")

    def _send_email_task(self, subject: str, body: str, to_email: str):
        try:
            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = self.config.EMAIL_USER
            msg['To'] = to_email
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            if self.config.EMAIL_USE_TLS:
                with smtplib.SMTP(self.config.EMAIL_HOST, self.config.EMAIL_PORT, timeout=20) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(self.config.EMAIL_USER, self.config.EMAIL_PASS)
                    server.send_message(msg)
            else:
                with smtplib.SMTP_SSL(self.config.EMAIL_HOST, self.config.EMAIL_PORT, context=context, timeout=20) as server:
                    server.login(self.config.EMAIL_USER, self.config.EMAIL_PASS)
                    server.send_message(msg)
            self.logger.log_info(f"Email sent successfully to {to_email}")
        except Exception as e:
            self.logger.log_error(e, f"Email failed to {to_email}")

    def shutdown(self):
        self.logger.log_info("Shutting down notifier thread pool")
        self.executor.shutdown(wait=True)
        self.logger.log_info("Notifier thread pool shut down")

class AccessControlGUI:
    def __init__(self, root: Tk, hardware: HardwareController, db_manager: SecureDatabaseManager, logger_obj: ProfessionalLogger, notifier: Notifier):
        self.root = root
        self.hardware = hardware
        self.db = db_manager
        self.logger = logger_obj
        self.notifier = notifier
        self.root.title("NFC Access Control System")
        self.root.geometry("850x650")
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.gui_queue = queue.Queue()
        self.lock_status_var = tk.StringVar(value="Lock: Unknown")
        self._setup_styles()
        self._setup_ui()
        self._start_periodic_updates()
        self.logger.log_info("GUI Initialized")

    def _setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.style.configure("Emergency.TButton", foreground="#dc3545", font=('Segoe UI', 10, 'bold'))
        self.style.configure("Action.TButton", foreground="#007bff", font=('Segoe UI', 10))
        self.style.configure("TLabelframe.Label", font=('Segoe UI', 11, 'bold'), foreground="#0056b3")
        self.style.configure("TLabel", font=('Segoe UI', 10))
        self.style.configure("Status.TLabel", font=('Segoe UI', 10, 'bold'))
        self.style.map("Emergency.TButton", background=[('active', '#f8d7da')])

    def _setup_ui(self) -> None:
        main_frame = ttk.Frame(self.root, padding="10 10 10 10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        left_panel = ttk.Frame(main_frame)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        status_frame = ttk.LabelFrame(left_panel, text="System Status", padding=10)
        status_frame.pack(fill=tk.X, pady=5)
        self.status_var = tk.StringVar(value="Initializing...")
        self.health_var = tk.StringVar(value="Health: Unknown")
        self.temp_var = tk.StringVar(value="Temp: --.-")
        ttk.Label(status_frame, text="Overall:").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(status_frame, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=1, sticky=tk.W, padx=5)
        ttk.Label(status_frame, textvariable=self.health_var).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(5,0))
        ttk.Label(status_frame, textvariable=self.temp_var).grid(row=2, column=0, columnspan=2, sticky=tk.W)
        ttk.Label(status_frame, textvariable=self.lock_status_var).grid(row=3, column=0, columnspan=2, sticky=tk.W)
        access_frame = ttk.LabelFrame(left_panel, text="Last Access Attempt", padding=10)
        access_frame.pack(fill=tk.X, pady=5)
        self.card_var = tk.StringVar(value="Card ID: None")
        self.access_status_var = tk.StringVar(value="Status: Waiting")
        self.access_time_var = tk.StringVar(value="Time: --:--:--")
        ttk.Label(access_frame, textvariable=self.card_var).pack(anchor=tk.W)
        ttk.Label(access_frame, textvariable=self.access_status_var, style="Status.TLabel").pack(anchor=tk.W)
        ttk.Label(access_frame, textvariable=self.access_time_var).pack(anchor=tk.W)
        control_frame = ttk.LabelFrame(left_panel, text="Manual Controls", padding=10)
        control_frame.pack(fill=tk.X, pady=5)
        open_button = ttk.Button(control_frame, text="Open Gate", command=self._manual_open, style="Action.TButton")
        open_button.pack(fill=tk.X, pady=2)
        close_button = ttk.Button(control_frame, text="Close Gate", command=self._manual_close, style="Action.TButton")
        close_button.pack(fill=tk.X, pady=2)
        lock_button = ttk.Button(control_frame, text="Lock Gate", command=self._manual_lock, style="Action.TButton")
        lock_button.pack(fill=tk.X, pady=2)
        unlock_button = ttk.Button(control_frame, text="Unlock Gate", command=self._manual_unlock, style="Action.TButton")
        unlock_button.pack(fill=tk.X, pady=2)
        buzz_button = ttk.Button(control_frame, text="Test Buzzer", command=self._test_buzzer, style="Action.TButton")
        buzz_button.pack(fill=tk.X, pady=2)
        emergency_button = ttk.Button(
            left_panel,
            text="Emergency Stop Hardware",
            command=self._emergency_stop,
            style="Emergency.TButton"
        )
        emergency_button.pack(fill=tk.X, pady=10)
        right_panel = ttk.Frame(main_frame)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_frame = ttk.LabelFrame(right_panel, text="System Logs", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=15, width=60, font=('Consolas', 9), relief=tk.SUNKEN, borderwidth=1)
        log_scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=log_scrollbar.set)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)
        mgmt_frame = ttk.LabelFrame(right_panel, text="Management", padding=10)
        mgmt_frame.pack(fill=tk.X, pady=5)
        config_button = ttk.Button(mgmt_frame, text="Manage Cards...", command=self._show_card_manager, style="Action.TButton")
        config_button.pack(side=tk.LEFT, padx=5)
        view_audit_button = ttk.Button(mgmt_frame, text="View Audit Log...", command=self._show_audit_log, style="Action.TButton")
        view_audit_button.pack(side=tk.LEFT, padx=5)

    def _start_periodic_updates(self) -> None:
        self._update_health_display()
        self._process_gui_queue()
        self.root.after(1000, self._start_periodic_updates)

    def _update_health_display(self) -> None:
        try:
            health = self.hardware.check_health()
            status_text = "Ready" if health["initialized"] else "Hardware Error"
            health_text = f"Health: {health['nfc_status']} NFC, {health['gpio_status']} GPIO (Errors: {health['error_count']})"
            temp_text = f"Temp: {self.get_last_temp_reading():.1f}°C" if self.get_last_temp_reading() is not None else "Temp: --.-"
            lock_text = f"Lock: {health['lock_status']}"
            self.status_var.set(status_text)
            self.health_var.set(health_text)
            self.temp_var.set(temp_text)
            self.lock_status_var.set(lock_text)
        except Exception as e:
            self.logger.log_error(e, "GUI failed to update health display")
            self.status_var.set("Error Updating")
            self.health_var.set("Health: Error")
            self.temp_var.set("Temp: Error")
            self.lock_status_var.set("Lock: Error")

    def get_last_temp_reading(self) -> Optional[float]:
        global temp_monitor
        if temp_monitor and hasattr(temp_monitor, 'last_temp'):
            return temp_monitor.last_temp
        return None

    def _process_gui_queue(self) -> None:
        try:
            while True:
                message = self.gui_queue.get_nowait()
                if isinstance(message, str):
                    self._append_log(message)
                elif isinstance(message, dict):
                    msg_type = message.get("type")
                    if msg_type == "access_update":
                        self._update_access_display(message.get("card_id"), message.get("status"), message.get("timestamp"))
        except queue.Empty:
            pass
        except Exception as e:
            self._append_log(f"ERROR processing GUI queue: {e}")

    def _append_log(self, log_message: str):
        try:
            self.log_text.config(state=tk.NORMAL)
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_text.insert(tk.END, f"[{timestamp}] {log_message}\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        except Exception as e:
            print(f"Error appending log to GUI: {e}")

    def _update_access_display(self, card_id: Optional[str], status: Optional[AccessStatus], timestamp: Optional[datetime]):
        self.card_var.set(f"Card ID: {card_id if card_id else 'N/A'}")
        status_name = status.name if status else "Unknown"
        self.access_status_var.set(f"Status: {status_name}")
        time_str = timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else "--:--:--"
        self.access_time_var.set(f"Time: {time_str}")
        color = "green" if status == AccessStatus.GRANTED else "red" if status else "black"

    def _manual_open(self):
        self.logger.log_audit("manual_gate_open", {"user": "GUI"})
        self.hardware.control_lock(lock=False)
        time.sleep(0.1)
        self.hardware.control_servo(open_gate=True)
        self.gui_queue.put("Manual gate open triggered")
        self.hardware.buzz(0.1)

    def _manual_close(self):
        self.logger.log_audit("manual_gate_close", {"user": "GUI"})
        self.hardware.control_servo(open_gate=False)
        time.sleep(0.5)
        self.hardware.control_lock(lock=True)
        self.gui_queue.put("Manual gate close triggered")

    def _manual_lock(self):
        self.logger.log_audit("manual_gate_lock", {"user": "GUI"})
        self.hardware.control_lock(lock=True)
        self.gui_queue.put("Manual gate lock triggered")

    def _manual_unlock(self):
        self.logger.log_audit("manual_gate_unlock", {"user": "GUI"})
        self.hardware.control_lock(lock=False)
        self.gui_queue.put("Manual gate unlock triggered")

    def _test_buzzer(self):
        self.logger.log_audit("manual_buzzer_test", {"user": "GUI"})
        self.hardware.buzz(0.5)
        self.gui_queue.put("Manual buzzer test triggered")

    def _emergency_stop(self):
        if messagebox.askyesno("Confirm Emergency Stop", "This will stop all hardware operations. Proceed?"):
            self.logger.log_audit("emergency_stop_triggered", {"user": "GUI"})
            self.gui_queue.put("EMERGENCY STOP ACTIVATED")
            try:
                self.hardware._cleanup()
                self.status_var.set("EMERGENCY STOPPED")
                self.health_var.set("Health: STOPPED")
            except Exception as e:
                self.logger.log_error(e, "Error during emergency stop")
                messagebox.showerror("Stop Error", f"Error during emergency stop: {e}")

    def _show_card_manager(self):
        messagebox.showinfo("Card Manager", "Card management not implemented yet")

    def _show_audit_log(self):
        messagebox.showinfo("Audit Log", "Audit log viewer not implemented yet")

    def _on_closing(self):
        if messagebox.askokcancel("Quit", "Do you want to quit?"):
            self.logger.log_info("GUI closing signal received")
            self.root.destroy()

class NFCAccessControlApp:
    def __init__(self):
        self.logger = logger
        self.config = config
        self.hardware = HardwareController(self.config, self.logger)
        self.db = SecureDatabaseManager(self.config, self.logger)
        self.notifier = Notifier(self.config, self.logger)
        self.temp_monitor = None
        self.nfc_poll_thread = None
        self.stop_event = threading.Event()
        self.gate_close_timer = None
        self.gui = None

    def start_background_tasks(self):
        if self.hardware._is_initialized:
            global temp_monitor
            temp_monitor = TemperatureMonitor(self.hardware, self.config, self.logger)
            temp_monitor.start()
            self.temp_monitor = temp_monitor
            self.nfc_poll_thread = threading.Thread(target=self._nfc_polling_loop, daemon=True)
            self.nfc_poll_thread.start()
            self.logger.log_info("Background tasks started")
        else:
            self.logger.log_error(RuntimeError("Hardware not initialized"), "Startup")

    def _nfc_polling_loop(self):
        self.logger.log_info("NFC polling thread started")
        while not self.stop_event.is_set():
            card_info = self.hardware.read_card()
            if card_info:
                self.logger.log_info(f"Card detected: {card_info.id}")
                self.hardware.buzz(0.05)
                self.process_card_access(card_info.id)
                time.sleep(2)
            else:
                self.stop_event.wait(0.5)
        self.logger.log_info("NFC polling thread stopped")

    def process_card_access(self, card_id: str):
        start_time = time.time()
        access_status = AccessStatus.DENIED
        details = ""
        card_details = None
        try:
            card_details = self.db.get_card_info(card_id)
            if card_details and card_details.is_valid:
                access_status = AccessStatus.GRANTED
                details = f"Access granted to {card_details.name or 'authorized user'}"
                self.logger.log_info(details)
                self.hardware.control_lock(lock=False)
                time.sleep(0.1)
                self.hardware.control_servo(open_gate=True)
                if self.gate_close_timer:
                    self.gate_close_timer.cancel()
                self.gate_close_timer = threading.Timer(5.0, self.close_and_lock_gate)
                self.gate_close_timer.start()
            else:
                access_status = AccessStatus.DENIED
                details = f"Access denied. Card {card_id} not found or invalid"
                self.logger.log_warning(details)
                self.hardware.buzz(0.3)
        except Exception as e:
            access_status = AccessStatus.DENIED
            details = f"Error processing card {card_id}: {e}"
            self.logger.log_error(e, f"Error processing card {card_id}")
            self.hardware.buzz(0.5)
        finally:
            response_time = time.time() - start_time
            self.db.log_access_attempt(card_id, access_status, details)
            log_card_info = card_details if card_details else CardInfo(id=card_id)
            self.logger.log_access(log_card_info, access_status, response_time)
            if self.gui:
                update_msg = {
                    "type": "access_update",
                    "card_id": card_id,
                    "status": access_status,
                    "timestamp": datetime.now()
                }
                self.gui.gui_queue.put(update_msg)

    def close_and_lock_gate(self):
        self.hardware.control_servo(open_gate=False)
        time.sleep(0.5)
        self.hardware.control_lock(lock=True)
        self.logger.log_info("Gate closed and locked after timeout")

    def run_gui(self):
        self.logger.log_info("Starting GUI")
        root = Tk()
        self.gui = AccessControlGUI(root, self.hardware, self.db, self.logger, self.notifier)
        self.start_background_tasks()
        root.mainloop()
        self.shutdown()

    def shutdown(self):
        self.logger.log_info("Initiating application shutdown")
        self.stop_event.set()
        if self.gate_close_timer:
            self.gate_close_timer.cancel()
        if self.temp_monitor and self.temp_monitor.is_alive():
            self.temp_monitor.stop()
            self.temp_monitor.join(timeout=5)
        if self.nfc_poll_thread and self.nfc_poll_thread.is_alive():
            self.nfc_poll_thread.join(timeout=5)
        if self.notifier:
            self.notifier.shutdown()
        if self.hardware:
            self.hardware._cleanup()
        if self.db:
            self.db.close()
        self.logger.log_info("Application shutdown complete")

if __name__ == "__main__":
    if "--setup" in sys.argv:
        print("Running interactive credential setup...")
        Authenticator.setup_credentials_interactively()
        sys.exit(0)
    print("Bypassing authentication for GUI demonstration...")
    authenticated_user = "demo_user"
    print("Starting application...")
    logger.log_audit("application_start", {"user": authenticated_user})
    app = NFCAccessControlApp()
    try:
        app.run_gui()
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received. Shutting down...")
        logger.log_audit("application_shutdown", {"reason": "KeyboardInterrupt"})
        app.shutdown()
    except Exception as e:
        logger.log_error(e, "CRITICAL: Unhandled exception")
        logger.log_audit("application_shutdown", {"reason": f"Unhandled Exception: {e}"})
        app.shutdown()
        sys.exit(1)
    else:
        logger.log_audit("application_shutdown", {"reason": "Normal GUI close"})
        sys.exit(0)
