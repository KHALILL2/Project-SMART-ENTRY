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

# Add nfcpy and ndeflib module paths and disable USB driver
os.environ["NFCPY_USB_DRIVER"] = ""  # Disable USB drivers to bypass usb1 import
sys.path.insert(0, "/home/pi/Desktop/nfcpy/src")
sys.path.insert(0, "/home/pi/Desktop/ndeflib/src")
try:
    import nfc
    from nfc.clf import RemoteTarget
    import ndef
    print("nfcpy and ndeflib modules loaded successfully!")
    NFC_AVAILABLE = True
except ImportError as e:
    print(f"WARNING: nfc library not found. Using mock NFC.")
    NFC_AVAILABLE = False

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    print("WARNING: RPi.GPIO not found or failed to import. Using mock GPIO.")
    class MockGPIO:
        BCM = 11
        OUT = 1
        LOW = 0
        HIGH = 1
        PUD_UP = 22
        PUD_DOWN = 21
        FALLING = 32
        RISING = 31
        BOTH = 33
        def setmode(self, mode):
            print(f"MockGPIO: Set mode to {mode}")
        def setup(self, pin, mode, pull_up_down=None):
            print(f"MockGPIO: Setup pin {pin} to mode {mode} with pull_up_down={pull_up_down}")
        def output(self, pin, state):
            print(f"MockGPIO: Set pin {pin} to state {state}")
        def input(self, pin):
            print(f"MockGPIO: Reading pin {pin}")
            return 0
        def cleanup(self, pin=None):
            print(f"MockGPIO: Cleanup pin {pin if pin else 'all'}")        def add_event_detect(self, pin, edge, callback=None, bouncetime=None):
            print(f"MockGPIO: Add event detect on pin {pin} for edge {edge}")
        def remove_event_detect(self, pin):
            print(f"MockGPIO: Remove event detect on pin {pin}")
        class PWM:
            def __init__(self, pin, freq):
                print(f"MockGPIO: PWM created on pin {pin} with freq {freq}")
            def start(self, duty):
                print(f"MockGPIO: PWM start with duty {duty}")
            def ChangeDutyCycle(self, duty):
                print(f"MockGPIO: PWM change duty cycle to {duty}")
            def stop(self):
                print(f"MockGPIO: PWM stop")
    GPIO = MockGPIO()
    GPIO_AVAILABLE = False

from tkinter import Tk, Label, Button, messagebox, Entry, Toplevel, Text, END, Frame, BOTH, X, LEFT, RIGHT, TOP, BOTTOM, Y, Scrollbar
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
        self.logger = logging.getLogger("nfc_system")
        self.logger.setLevel(logging.INFO)
        if self.logger.hasHandlers():
            self.logger.handlers.clear()
        file_handler = RotatingFileHandler(
            self.log_dir / "system.log",
            maxBytes=10*1024*1024,
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s"
        ))
        audit_logger = logging.getLogger("nfc_audit")
        audit_logger.setLevel(logging.INFO)
        if audit_logger.hasHandlers():
            audit_logger.handlers.clear()
        audit_handler = RotatingFileHandler(
            self.log_dir / "audit.log",
            maxBytes=5*1024*1024,
            backupCount=3,
            encoding="utf-8"
        )
        audit_handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(message)s"
        ))
        audit_logger.addHandler(audit_handler)
        self.audit_logger = audit_logger
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s"
        ))
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.metrics = SystemMetrics()
        self.start_time = datetime.now()
        self.log_queue = queue.Queue(maxsize=100)  # Limit queue size

    def log_access(self, card_info: CardInfo, status: AccessStatus, response_time: float) -> None:
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "card_id": card_info.id,
            "card_name": card_info.name,
            "status": status.name,
            "response_time": response_time,
            "system_metrics": self._get_current_metrics()
        }
        msg = json.dumps(log_data)
        self.logger.info(msg)
        self._queue_log(f"INFO: Access attempt - Card: {card_info.id}, Status: {status.name}")
        self._update_metrics(status, response_time)

    def log_error(self, error: Exception, context: str = "", severity: str = "ERROR") -> None:
        tb_string = traceback.format_exc()
        error_info = {
            "timestamp": datetime.now().isoformat(),
            "error": str(error),
            "context": context,
            "severity": severity,
            "traceback": tb_string,
            "system_metrics": self._get_current_metrics()
        }
        msg = json.dumps(error_info)
        self.logger.error(msg)
        self._queue_log(f"{severity}: {context} - {error}")

    def log_audit(self, action: str, details: Dict[str, Any]) -> None:
        audit_data = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details,
        }
        msg = json.dumps(audit_data)
        self.audit_logger.info(msg)
        self._queue_log(f"AUDIT: {action} - {details.get("card_id", "")}")

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

    def _get_current_metrics(self) -> Dict[str, Any]:
        return {
            "total_requests": self.metrics.total_requests,
            "successful_accesses": self.metrics.successful_accesses,
            "failed_accesses": self.metrics.failed_accesses,
            "average_response_time": round(self.metrics.average_response_time, 4),
            "system_uptime": round(self.metrics.system_uptime, 2),
            "last_health_check": self.metrics.last_health_check.isoformat() if self.metrics.last_health_check else None
        }

logger = ProfessionalLogger()

class Config:
    DEFAULT_VALID_PINS = [2,3,4,17,18,22,23,24,25,26,27]
    DEFAULT_THERMAL_FILE = "/sys/class/thermal/thermal_zone0/temp"
    CONFIG_FILE = "config.ini"

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
        self.EMAIL_HOST = self.config.get("email", "host", fallback="smtp.gmail.com")
        self.EMAIL_PORT = self.config.getint("email", "port", fallback=587)
        self.EMAIL_USE_TLS = self.config.getboolean("email", "use_tls", fallback=True)
        self.VALID_PINS = self._parse_list(self.config.get("gpio", "valid_pins", fallback=str(self.DEFAULT_VALID_PINS)), int)
        self.SERVO_PIN = self._validate_pin(self.config.getint("gpio", "servo", fallback=18))
        self.FAN_PIN = self._validate_pin(self.config.getint("gpio", "fan", fallback=23))
        self.BUZZER_PIN = self._validate_pin(self.config.getint("gpio", "buzzer", fallback=24))
        self.SOLENOID_PIN = self._validate_pin(self.config.getint("gpio", "solenoid", fallback=27))  # Changed to GPIO 27 as per user"s hardware setup
        self.LED_GREEN_PIN = self._validate_pin(self.config.getint("gpio", "led_green", fallback=22))
        self.LED_RED_PIN = self._validate_pin(self.config.getint("gpio", "led_red", fallback=23))
        # MODIFIED: Use max/min duty cycles for servo
        self.SERVO_OPEN_DUTY = 12.5 # Max duty cycle for open
        self.SERVO_CLOSE_DUTY = 2.5 # Min duty cycle for close
        self.SERVO_DELAY = max(0.1, self.config.getfloat("servo", "delay", fallback=1.5))
        self.FAN_ON_TEMP = min(max(30, self.config.getfloat("temperature", "on", fallback=60)), 90)
        self.FAN_OFF_TEMP = min(max(25, self.config.getfloat("temperature", "off", fallback=50)), 85)
        self.THERMAL_FILE = self.config.get("temperature", "thermal_file", fallback=self.DEFAULT_THERMAL_FILE)
        self.NFC_MAX_ATTEMPTS = self.config.getint("nfc", "max_attempts", fallback=10)
        self.NFC_TIMEOUT = self.config.getint("nfc", "timeout", fallback=30)
        self.NFC_PROTOCOL = self.config.get("nfc", "protocol", fallback="106A")
        self.DB_PATH = self.config.get("database", "path", fallback="cards.db")
        self.DB_ENCRYPTED = self.config.getboolean("database", "encrypted", fallback=True)
        self.GUI_UPDATE_INTERVAL = self.config.getint("performance", "gui_update_ms", fallback=100)

    def _create_default_config(self):
        default_config = configparser.ConfigParser()
        default_config["email"] = {
            "host": "smtp.gmail.com",
            "port": "587",
            "use_tls": "True"
        }
        default_config["gpio"] = {
            "valid_pins": str(self.DEFAULT_VALID_PINS),
            "servo": "18",
            "fan": "23",
            "buzzer": "24",
            "solenoid": "27",  # Changed to GPIO 27 as per user"s hardware setup
            "led_green": "22",
            "led_red": "23"
        }
        default_config["servo"] = {
            "open": "12.5", # MODIFIED: Max duty cycle
            "close": "2.5", # MODIFIED: Min duty cycle
            "delay": "1.5"
        }
        default_config["temperature"] = {
            "on": "60",
            "off": "50",
            "thermal_file": self.DEFAULT_THERMAL_FILE
        }
        default_config["nfc"] = {
            "max_attempts": "10",
            "timeout": "30",
            "protocol": "106A"
        }
        default_config["database"] = {
            "path": "cards.db",
            "encrypted": "True"
        }
        default_config["performance"] = {
            "gui_update_ms": "100"
        }
        with open(self.CONFIG_FILE, "w") as configfile:
            default_config.write(configfile)

    def _parse_list(self, list_str: str, item_type: type) -> list:
        try:
            list_str = list_str.strip("[] ")
            return [item_type(item.strip()) for item in list_str.split(",")]
        except Exception as e:
            logger.log_error(e, f"Failed to parse list from config: {list_str}")
            return []

    def _validate_pin(self, pin):
        # Assume valid pins are defined before this method is called
        if hasattr(self, "VALID_PINS") and pin in self.VALID_PINS:
            return pin
        elif pin in self.DEFAULT_VALID_PINS: # Fallback check
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
            for pin in [config_obj.SERVO_PIN, config_obj.FAN_PIN, config_obj.BUZZER_PIN, config_obj.SOLENOID_PIN,
                       config_obj.LED_GREEN_PIN, config_obj.LED_RED_PIN]:
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
    # sys.exit(1) # Don"t exit in this environment

# --- REMOVED Authenticator Class --- 
# No longer needed as per user request

class DatabaseManager:
    def __init__(self, db_path: str, encrypted: bool = True):
        self.db_path = db_path
        self.encrypted = encrypted
        self.key = self._load_or_generate_key()
        self.fernet = Fernet(self.key) if self.encrypted else None
        self._create_tables()

    def _load_or_generate_key(self) -> bytes:
        key_path = Path(self.db_path + ".key")
        if key_path.exists():
            return key_path.read_bytes()
        else:
            key = Fernet.generate_key()
            key_path.write_bytes(key)
            return key

    def _encrypt(self, data: str) -> str:
        if self.fernet:
            return self.fernet.encrypt(data.encode()).decode()
        return data

    def _decrypt(self, data: str) -> str:
        if self.fernet:
            try:
                return self.fernet.decrypt(data.encode()).decode()
            except InvalidToken:
                logger.log_error(ValueError(f"Failed to decrypt data: {data[:20]}..."), "Database")
                return "DECRYPTION_ERROR"
        return data

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_tables(self) -> None:
        with self._connect() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY,
                name TEXT,
                expiry_date TEXT,
                is_valid INTEGER DEFAULT 1,
                last_access TEXT,
                student_id TEXT,
                faculty TEXT,
                program TEXT,
                level TEXT,
                photo_path TEXT
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                timestamp TEXT,
                action TEXT,
                details TEXT
            )
            """)
            conn.commit()

    def add_card(self, card_data: Dict[str, Any]) -> None:
        with self._connect() as conn:
            encrypted_data = {k: self._encrypt(str(v)) if v is not None else None for k, v in card_data.items()}
            conn.execute("""
            INSERT OR REPLACE INTO cards (id, name, expiry_date, is_valid, student_id, faculty, program, level, photo_path)
            VALUES (:id, :name, :expiry_date, :is_valid, :student_id, :faculty, :program, :level, :photo_path)
            """, encrypted_data)
            conn.commit()
        logger.log_audit("card_add", {"card_id": card_data["id"]})

    def get_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            encrypted_id = self._encrypt(card_id)
            cursor = conn.execute("SELECT * FROM cards WHERE id = ?", (encrypted_id,))
            row = cursor.fetchone()
            if row:
                decrypted_data = {k: self._decrypt(str(v)) if v is not None else None for k, v in dict(row).items()}
                # Ensure boolean is correct
                decrypted_data["is_valid"] = bool(int(decrypted_data.get("is_valid", 1)))
                return decrypted_data
            return None

    def get_all_cards(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute("SELECT * FROM cards")
            rows = cursor.fetchall()
            cards = []
            for row in rows:
                decrypted_data = {k: self._decrypt(str(v)) if v is not None else None for k, v in dict(row).items()}
                decrypted_data["is_valid"] = bool(int(decrypted_data.get("is_valid", 1)))
                cards.append(decrypted_data)
            return cards

    def update_card_status(self, card_id: str, is_valid: bool) -> None:
        with self._connect() as conn:
            encrypted_id = self._encrypt(card_id)
            conn.execute("UPDATE cards SET is_valid = ? WHERE id = ?", (int(is_valid), encrypted_id))
            conn.commit()
        logger.log_audit("card_status_update", {"card_id": card_id, "is_valid": is_valid})

    def update_last_access(self, card_id: str) -> None:
        with self._connect() as conn:
            encrypted_id = self._encrypt(card_id)
            last_access_time = self._encrypt(datetime.now().isoformat())
            conn.execute("UPDATE cards SET last_access = ? WHERE id = ?", (last_access_time, encrypted_id))
            conn.commit()

    def delete_card(self, card_id: str) -> None:
        with self._connect() as conn:
            encrypted_id = self._encrypt(card_id)
            conn.execute("DELETE FROM cards WHERE id = ?", (encrypted_id,))
            conn.commit()
        logger.log_audit("card_delete", {"card_id": card_id})

class HardwareController:
    def __init__(self, config: Config):
        self.config = config
        self.servo = None
        self.fan_on = False
        self._setup_gpio()

    def _setup_gpio(self):
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.config.SERVO_PIN, GPIO.OUT)
            GPIO.setup(self.config.FAN_PIN, GPIO.OUT)
            GPIO.setup(self.config.BUZZER_PIN, GPIO.OUT)
            GPIO.setup(self.config.SOLENOID_PIN, GPIO.OUT)
            GPIO.setup(self.config.LED_GREEN_PIN, GPIO.OUT)
            GPIO.setup(self.config.LED_RED_PIN, GPIO.OUT)
            
            # Initialize servo
            self.servo = GPIO.PWM(self.config.SERVO_PIN, 50)  # 50Hz
            self.servo.start(0) # Start with 0 duty cycle
            
            # Initialize outputs to default states
            GPIO.output(self.config.FAN_PIN, GPIO.LOW) # Fan off
            GPIO.output(self.config.BUZZER_PIN, GPIO.LOW) # Buzzer off
            GPIO.output(self.config.LED_GREEN_PIN, GPIO.LOW) # Green LED off
            GPIO.output(self.config.LED_RED_PIN, GPIO.LOW) # Red LED off
            
            # Initialize lock to locked state (HIGH for normal logic)
            GPIO.output(self.config.SOLENOID_PIN, GPIO.HIGH)
            # Also try system command for redundancy
            os.system(f"gpio -g mode {self.config.SOLENOID_PIN} out")
            os.system(f"gpio -g write {self.config.SOLENOID_PIN} 1")
            
            logger.log_info("GPIO setup complete")
        except Exception as e:
            logger.log_error(e, "Failed to setup GPIO")

    def open_gate(self):
        if self.servo:
            try:
                logger.log_info("Opening gate")
                # MODIFIED: Use max duty cycle directly
                self.servo.ChangeDutyCycle(12.5) 
                time.sleep(self.config.SERVO_DELAY)
                self.servo.ChangeDutyCycle(0) # Stop PWM to prevent jitter
            except Exception as e:
                logger.log_error(e, "Failed to open gate")

    def close_gate(self):
        if self.servo:
            try:
                logger.log_info("Closing gate")
                # MODIFIED: Use min duty cycle directly
                self.servo.ChangeDutyCycle(2.5) 
                time.sleep(self.config.SERVO_DELAY)
                self.servo.ChangeDutyCycle(0) # Stop PWM to prevent jitter
            except Exception as e:
                logger.log_error(e, "Failed to close gate")

    def engage_lock(self):
        try:
            logger.log_info("Engaging lock")
            # Assuming normal logic (HIGH = locked)
            GPIO.output(self.config.SOLENOID_PIN, GPIO.HIGH)
            os.system(f"gpio -g write {self.config.SOLENOID_PIN} 1")
        except Exception as e:
            logger.log_error(e, "Failed to engage lock")

    def disengage_lock(self):
        try:
            logger.log_info("Disengaging lock")
            # Assuming normal logic (LOW = unlocked)
            GPIO.output(self.config.SOLENOID_PIN, GPIO.LOW)
            os.system(f"gpio -g write {self.config.SOLENOID_PIN} 0")
        except Exception as e:
            logger.log_error(e, "Failed to disengage lock")

    def set_fan(self, state: bool):
        try:
            GPIO.output(self.config.FAN_PIN, GPIO.HIGH if state else GPIO.LOW)
            self.fan_on = state
            logger.log_info(f"Fan turned {"on" if state else "off"}")
        except Exception as e:
            logger.log_error(e, f"Failed to set fan state to {state}")

    def sound_buzzer(self, duration: float = 0.1, success: bool = True):
        def buzz():
            try:
                GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
                time.sleep(duration)
                GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
            except Exception as e:
                logger.log_error(e, "Failed to sound buzzer")
        threading.Thread(target=buzz, daemon=True).start()

    def set_green_led(self, state: bool):
        try:
            GPIO.output(self.config.LED_GREEN_PIN, GPIO.HIGH if state else GPIO.LOW)
        except Exception as e:
            logger.log_error(e, f"Failed to set green LED state to {state}")

    def set_red_led(self, state: bool):
        try:
            GPIO.output(self.config.LED_RED_PIN, GPIO.HIGH if state else GPIO.LOW)
        except Exception as e:
            logger.log_error(e, f"Failed to set red LED state to {state}")

    def get_temperature(self) -> Optional[float]:
        try:
            with open(self.config.THERMAL_FILE, "r") as f:
                temp_str = f.read().strip()
                return float(temp_str) / 1000.0
        except FileNotFoundError:
            logger.log_error(FileNotFoundError(f"Thermal file not found: {self.config.THERMAL_FILE}"), "Temperature")
            return None
        except Exception as e:
            logger.log_error(e, "Failed to read temperature")
            return None

    def manage_fan(self):
        temp = self.get_temperature()
        if temp is not None:
            if temp >= self.config.FAN_ON_TEMP and not self.fan_on:
                self.set_fan(True)
            elif temp <= self.config.FAN_OFF_TEMP and self.fan_on:
                self.set_fan(False)

    def cleanup(self):
        try:
            if self.servo:
                self.servo.stop()
            GPIO.cleanup()
            logger.log_info("GPIO cleanup complete")
        except Exception as e:
            logger.log_error(e, "Failed during GPIO cleanup")

class NFCReader:
    def __init__(self, config: Config):
        self.config = config
        self.clf = None
        self.target = None
        if NFC_AVAILABLE:
            try:
                self.clf = nfc.ContactlessFrontend("i2c")
                self.target = RemoteTarget(self.config.NFC_PROTOCOL)
                logger.log_info(f"NFC reader initialized: {self.clf}")
            except Exception as e:
                logger.log_error(e, "Failed to initialize NFC reader")
                self.clf = None

    def read_card(self) -> Optional[str]:
        if not self.clf:
            return None
        try:
            tag = self.clf.connect(rdwr={"on-connect": lambda tag: False}, 
                                targets=[self.target],
                                interval=0.1,
                                iterations=1) # Short interval for responsiveness
            if tag:
                card_id = tag.identifier.hex().upper()
                logger.log_info(f"Card detected: {card_id}")
                return card_id
            return None
        except Exception as e:
            # Log error but don"t crash the loop
            logger.log_error(e, "Error reading NFC card")
            # Attempt to re-initialize reader on error
            self._reinitialize_reader()
            return None

    def _reinitialize_reader(self):
        logger.log_info("Attempting to reinitialize NFC reader...")
        try:
            if self.clf:
                self.clf.close()
            self.clf = nfc.ContactlessFrontend("i2c")
            logger.log_info(f"NFC reader reinitialized: {self.clf}")
        except Exception as e:
            logger.log_error(e, "Failed to reinitialize NFC reader")
            self.clf = None

class AccessController:
    def __init__(self, db: DatabaseManager, hardware: HardwareController):
        self.db = db
        self.hardware = hardware
        self.blacklist = set()
        self.rate_limit = {}
        self.rate_limit_duration = timedelta(seconds=5)

    def process_card(self, card_id: str) -> Tuple[AccessStatus, Optional[Dict[str, Any]]]:
        start_time = time.time()
        
        # Check rate limit
        last_scan_time = self.rate_limit.get(card_id)
        if last_scan_time and (time.time() - last_scan_time) < self.rate_limit_duration.total_seconds():
            return AccessStatus.RATE_LIMITED, None
            
        # Check blacklist
        if card_id in self.blacklist:
            return AccessStatus.BLACKLISTED, None
            
        # Get card info from database
        card_data = self.db.get_card(card_id)
        if not card_data:
            # Unknown card
            return AccessStatus.DENIED, None
            
        # Update rate limit
        self.rate_limit[card_id] = time.time()
        
        # Check if card is valid
        is_valid = bool(card_data["is_valid"])
        
        # Check expiry date if present
        if card_data["expiry_date"]:
            try:
                expiry = datetime.fromisoformat(card_data["expiry_date"])
                if expiry < datetime.now():
                    is_valid = False
            except (ValueError, TypeError) as e:
                logger.log_error(e, f"Invalid expiry date format for card {card_id}")
                
        # Update last access time in database
        self.db.update_last_access(card_id)
        
        # Create card info object for logging
        card_info = CardInfo(
            id=card_id,
            name=card_data["name"],
            expiry_date=datetime.fromisoformat(card_data["expiry_date"]) if card_data["expiry_date"] else None,
            is_valid=is_valid,
            last_access=datetime.now()
        )
        
        # Determine access status
        status = AccessStatus.GRANTED if is_valid else AccessStatus.DENIED
        
        # Log access attempt
        response_time = time.time() - start_time
        logger.log_access(card_info, status, response_time)
        
        return status, card_data

    def handle_access(self, card_id: str) -> Tuple[AccessStatus, Optional[Dict[str, Any]]]:
        status, card_data = self.process_card(card_id)
        
        if status == AccessStatus.GRANTED:
            # Successful access - turn on green LED, sound success buzzer
            # Make sure red LED is off first
            self.hardware.set_red_led(False)
            self.hardware.set_green_led(True)
            self.hardware.sound_buzzer(duration=0.5, success=True)
            
            # Open gate and disengage lock
            self.hardware.disengage_lock()
            self.hardware.open_gate()
            
            # Re-engage lock and turn off LED after a delay
            def relock_after_delay():
                time.sleep(5)  # Wait for person to pass through
                self.hardware.close_gate()
                time.sleep(1)  # Wait for gate to close
                self.hardware.engage_lock()
                time.sleep(1)  # Wait a moment before turning off LED
                self.hardware.set_green_led(False)
                
            threading.Thread(target=relock_after_delay, daemon=True).start()
            
        elif status in (AccessStatus.DENIED, AccessStatus.BLACKLISTED):
            # Failed access - turn on red LED, sound error buzzer
            # Make sure green LED is off first
            self.hardware.set_green_led(False)
            self.hardware.set_red_led(True)
            self.hardware.sound_buzzer(duration=0.5, success=False)
            
            # Turn off red LED after a delay
            def reset_led_after_delay():
                time.sleep(3)
                self.hardware.set_red_led(False)
                
            threading.Thread(target=reset_led_after_delay, daemon=True).start()
            
        elif status == AccessStatus.RATE_LIMITED:
            # Rate limited - short error buzzer
            self.hardware.sound_buzzer(duration=0.2, success=False)
            
        return status, card_data

    def add_to_blacklist(self, card_id: str) -> None:
        self.blacklist.add(card_id)
        logger.log_audit("blacklist_add", {"card_id": card_id})

    def remove_from_blacklist(self, card_id: str) -> None:
        if card_id in self.blacklist:
            self.blacklist.remove(card_id)
            logger.log_audit("blacklist_remove", {"card_id": card_id})

class SmallScreenGUI:
    def __init__(self, master=None):
        # Create a new Tk window if master is None
        self.is_toplevel = master is not None
        self.root = master if master else Tk()
        
        if not self.is_toplevel:
            self.root.title("Gate Access Display")
            self.root.geometry("800x480")  # 7-inch Raspberry Pi display resolution
            # self.root.attributes("-fullscreen", True)  # Fullscreen for Raspberry Pi - Commented out for easier testing
        
        # Main frame
        self.frame = ttk.Frame(self.root, padding="10")
        self.frame.pack(fill=tk.BOTH, expand=True)
        
        # Status display
        self.status_frame = ttk.Frame(self.frame, padding="5")
        self.status_frame.pack(fill=tk.X, pady=10)
        
        self.status_label = ttk.Label(
            self.status_frame, 
            text="Ready to Scan", 
            font=("Helvetica", 24, "bold"),
            foreground="blue"
        )
        self.status_label.pack(anchor=tk.CENTER)
        
        # Student info frame
        self.info_frame = ttk.Frame(self.frame, padding="5")
        self.info_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        # Photo placeholder
        self.photo_frame = ttk.Frame(self.info_frame, borderwidth=2, relief="solid")
        self.photo_frame.pack(side=tk.LEFT, padx=10, fill=tk.Y)
        
        self.photo_label = ttk.Label(
            self.photo_frame,
            text="Photo",
            font=("Helvetica", 14),
            width=15,
            anchor=tk.CENTER
        )
        self.photo_label.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        # Student details
        self.details_frame = ttk.Frame(self.info_frame)
        self.details_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        
        # Create labels for student info
        self.name_label = self._create_info_label("Name: ")
        self.id_label = self._create_info_label("ID: ")
        self.faculty_label = self._create_info_label("Faculty: ")
        self.program_label = self._create_info_label("Program: ")
        self.level_label = self._create_info_label("Level: ")
        
        # Instructions
        self.instructions_label = ttk.Label(
            self.frame,
            text="Please scan your card to enter",
            font=("Helvetica", 16),
            foreground="gray"
        )
        self.instructions_label.pack(pady=10)
        
        # For thread-safe GUI updates
        self.update_queue = queue.Queue()
        self.root.after(100, self._process_queue)
        
        print("Small screen GUI initialized successfully")

    def _create_info_label(self, prefix):
        frame = ttk.Frame(self.details_frame)
        frame.pack(fill=tk.X, pady=5)
        
        prefix_label = ttk.Label(
            frame,
            text=prefix,
            font=("Helvetica", 14, "bold"),
            width=10,
            anchor=tk.W
        )
        prefix_label.pack(side=tk.LEFT)
        
        value_label = ttk.Label(
            frame,
            text="",
            font=("Helvetica", 14),
            anchor=tk.W
        )
        value_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        return value_label

    def clear_info(self):
        """Clear all student information"""
        self.update_queue.put(("clear", None))

    def display_card_info(self, card_data, status):
        """Thread-safe method to display card information"""
        self.update_queue.put(("display_info", (card_data, status)))

    def _process_queue(self):
        """Process GUI update queue"""
        try:
            while True:
                command, args = self.update_queue.get_nowait()
                if command == "display_info":
                    self._update_display(*args)
                elif command == "clear":
                    self._clear_display()
        except queue.Empty:
            pass
        finally:
            # Schedule next queue check
            self.root.after(100, self._process_queue)

    def _clear_display(self):
        """Clear the display (called from main thread)"""
        try:
            self.name_label.config(text="")
            self.id_label.config(text="")
            self.faculty_label.config(text="")
            self.program_label.config(text="")
            self.level_label.config(text="")
            self.photo_label.config(text="Photo")
            self.status_label.config(text="Ready to Scan", foreground="blue")
            self.instructions_label.config(text="Please scan your card to enter")
        except Exception as e:
            logger.log_error(e, "Failed to clear display")

    def _update_display(self, card_data, status):
        """Update the display with card information (called from main thread)"""
        try:
            if status == AccessStatus.GRANTED:
                self.status_label.config(text="Access Granted", foreground="green")
                self.instructions_label.config(text="Welcome! Gate is opening...")
                
                # Update student info
                self.name_label.config(text=card_data.get("name", "Unknown"))
                self.id_label.config(text=card_data.get("student_id", "Unknown"))
                self.faculty_label.config(text=card_data.get("faculty", "Unknown"))
                self.program_label.config(text=card_data.get("program", "Unknown"))
                self.level_label.config(text=card_data.get("level", "Unknown"))
                
                # TODO: Load photo if available
                photo_path = card_data.get("photo_path")
                if photo_path and os.path.exists(photo_path):
                    # In a real implementation, load the photo using PIL/Pillow
                    self.photo_label.config(text=f"Photo\n({photo_path})")
                else:
                    self.photo_label.config(text="No Photo")
                    
                # Auto-clear after delay
                self.root.after(10000, lambda: self.update_queue.put(("clear", None)))
                
            elif status == AccessStatus.DENIED:
                self.status_label.config(text="Access Denied", foreground="red")
                self.instructions_label.config(text="Card not valid. Please contact admin.")
                self.root.after(5000, lambda: self.update_queue.put(("clear", None)))
                
            elif status == AccessStatus.BLACKLISTED:
                self.status_label.config(text="Card Blacklisted", foreground="red")
                self.instructions_label.config(text="This card has been blacklisted.")
                self.root.after(5000, lambda: self.update_queue.put(("clear", None)))
                
            elif status == AccessStatus.RATE_LIMITED:
                self.status_label.config(text="Please Wait", foreground="orange")
                self.instructions_label.config(text="Card scanned too frequently. Please wait.")
                self.root.after(3000, lambda: self.update_queue.put(("clear", None)))
                
        except Exception as e:
            logger.log_error(e, f"Failed to display card info for {card_data.get("id", "unknown")}")

    def update(self):
        """Update the GUI - only needed if not using mainloop()"""
        try:
            self.root.update()
        except Exception as e:
            logger.log_error(e, "Failed to update GUI")

    def run(self):
        """Start the GUI main loop"""
        if not self.is_toplevel:
            self.root.mainloop()

class AdminGUI:
    def __init__(self, db, hardware, access_controller, nfc_reader):
        self.db = db
        self.hardware = hardware
        self.access_controller = access_controller
        self.nfc_reader = nfc_reader
        
        self.root = Tk()
        self.root.title("SMART ENTRY Admin Interface")
        self.root.geometry("1024x768")
        
        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Dashboard tab
        self.dashboard_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.dashboard_frame, text="Dashboard")
        self._setup_dashboard()
        
        # Cards management tab
        self.cards_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.cards_frame, text="Cards")
        self._setup_cards_tab()
        
        # Hardware control tab
        self.hardware_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.hardware_frame, text="Hardware Control")
        self._setup_hardware_tab()
        
        # Logs tab
        self.logs_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.logs_frame, text="Logs")
        self._setup_logs_tab()
        
        # Status bar
        self.status_bar = ttk.Label(
            self.root, 
            text="System Ready", 
            relief=tk.SUNKEN, 
            anchor=tk.W
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Setup periodic updates
        self._update_logs()
        self._update_status()

    def _setup_dashboard(self):
        # Title
        ttk.Label(
            self.dashboard_frame, 
            text="System Dashboard", 
            font=("Helvetica", 16, "bold")
        ).pack(pady=(0, 10))
        
        # Stats frame
        stats_frame = ttk.LabelFrame(self.dashboard_frame, text="System Statistics")
        stats_frame.pack(fill=tk.X, pady=10)
        
        # Create statistics labels
        self.total_requests_label = self._create_stat_label(stats_frame, "Total Requests:")
        self.successful_label = self._create_stat_label(stats_frame, "Successful Access:")
        self.failed_label = self._create_stat_label(stats_frame, "Failed Access:")
        self.avg_response_label = self._create_stat_label(stats_frame, "Avg Response Time:")
        self.uptime_label = self._create_stat_label(stats_frame, "System Uptime:")
        
        # Recent activity frame
        activity_frame = ttk.LabelFrame(self.dashboard_frame, text="Recent Activity")
        activity_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        self.activity_text = Text(activity_frame, height=10, wrap=tk.WORD)
        self.activity_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Quick actions frame
        actions_frame = ttk.LabelFrame(self.dashboard_frame, text="Quick Actions")
        actions_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(
            actions_frame, 
            text="Open Gate", 
            command=self._open_gate
        ).pack(side=tk.LEFT, padx=5, pady=5)
        
        ttk.Button(
            actions_frame, 
            text="Close Gate", 
            command=self._close_gate
        ).pack(side=tk.LEFT, padx=5, pady=5)
        
        ttk.Button(
            actions_frame, 
            text="Lock Gate", 
            command=self._engage_lock
        ).pack(side=tk.LEFT, padx=5, pady=5)
        
        ttk.Button(
            actions_frame, 
            text="Unlock Gate", 
            command=self._disengage_lock
        ).pack(side=tk.LEFT, padx=5, pady=5)
        
        ttk.Button(
            actions_frame, 
            text="Test Valid Access", 
            command=self._test_valid_access
        ).pack(side=tk.LEFT, padx=5, pady=5)
        
        ttk.Button(
            actions_frame, 
            text="Test Invalid Access", 
            command=self._test_invalid_access
        ).pack(side=tk.LEFT, padx=5, pady=5)

    def _create_stat_label(self, parent, text):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=5, pady=2)
        
        ttk.Label(
            frame, 
            text=text, 
            width=20, 
            anchor=tk.W
        ).pack(side=tk.LEFT)
        
        value_label = ttk.Label(frame, text="--")
        value_label.pack(side=tk.LEFT, fill=tk.X)
        return value_label

    def _setup_cards_tab(self):
        # Title
        ttk.Label(
            self.cards_frame, 
            text="Card Management", 
            font=("Helvetica", 16, "bold")
        ).pack(pady=(0, 10))
        
        # Card list frame
        list_frame = ttk.LabelFrame(self.cards_frame, text="Registered Cards")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        # Treeview for card list
        columns = ("id", "name", "expiry", "valid", "last_access")
        self.card_tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        
        # Define headings
        self.card_tree.heading("id", text="Card ID")
        self.card_tree.heading("name", text="Name")
        self.card_tree.heading("expiry", text="Expiry Date")
        self.card_tree.heading("valid", text="Is Valid")
        self.card_tree.heading("last_access", text="Last Access")
        
        # Configure column widths
        self.card_tree.column("id", width=150)
        self.card_tree.column("name", width=150)
        self.card_tree.column("expiry", width=100)
        self.card_tree.column("valid", width=80, anchor=tk.CENTER)
        self.card_tree.column("last_access", width=150)
        
        # Add scrollbar
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.card_tree.yview)
        self.card_tree.configure(yscrollcommand=scrollbar.set)
        
        self.card_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Populate card list
        self._refresh_card_list()
        
        # Card actions frame
        actions_frame = ttk.Frame(self.cards_frame)
        actions_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(
            actions_frame, 
            text="Add Card", 
            command=self._add_card_dialog
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(
            actions_frame, 
            text="Edit Card", 
            command=self._edit_card_dialog
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(
            actions_frame, 
            text="Delete Card", 
            command=self._delete_card
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(
            actions_frame, 
            text="Toggle Validity", 
            command=self._toggle_card_validity
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(
            actions_frame, 
            text="Refresh List", 
            command=self._refresh_card_list
        ).pack(side=tk.LEFT, padx=5)

    def _refresh_card_list(self):
        # Clear existing items
        for item in self.card_tree.get_children():
            self.card_tree.delete(item)
            
        # Get cards from database
        cards = self.db.get_all_cards()
        
        # Populate treeview
        for card in cards:
            self.card_tree.insert(
                "", 
                tk.END, 
                values=(
                    card.get("id", ""),
                    card.get("name", ""),
                    card.get("expiry_date", ""),
                    "Yes" if card.get("is_valid", False) else "No",
                    card.get("last_access", "")
                )
            )

    def _add_card_dialog(self):
        # Simple dialog for adding a card
        dialog = Toplevel(self.root)
        dialog.title("Add New Card")
        dialog.geometry("400x300")
        
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Card ID:").grid(row=0, column=0, sticky=tk.W, pady=5)
        id_entry = ttk.Entry(frame, width=30)
        id_entry.grid(row=0, column=1, pady=5)
        
        ttk.Label(frame, text="Name:").grid(row=1, column=0, sticky=tk.W, pady=5)
        name_entry = ttk.Entry(frame, width=30)
        name_entry.grid(row=1, column=1, pady=5)
        
        ttk.Label(frame, text="Expiry (YYYY-MM-DD):").grid(row=2, column=0, sticky=tk.W, pady=5)
        expiry_entry = ttk.Entry(frame, width=30)
        expiry_entry.grid(row=2, column=1, pady=5)
        
        valid_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Is Valid", variable=valid_var).grid(row=3, column=1, sticky=tk.W, pady=5)
        
        def save_card():
            card_id = id_entry.get().strip()
            name = name_entry.get().strip()
            expiry = expiry_entry.get().strip() or None
            is_valid = valid_var.get()
            
            if not card_id:
                messagebox.showerror("Error", "Card ID is required", parent=dialog)
                return
                
            card_data = {
                "id": card_id,
                "name": name,
                "expiry_date": expiry,
                "is_valid": is_valid,
                "student_id": None, # Add fields for other data if needed
                "faculty": None,
                "program": None,
                "level": None,
                "photo_path": None
            }
            
            try:
                self.db.add_card(card_data)
                self._refresh_card_list()
                dialog.destroy()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to add card: {e}", parent=dialog)
                
        ttk.Button(frame, text="Save", command=save_card).grid(row=4, column=0, columnspan=2, pady=10)

    def _edit_card_dialog(self):
        selected_item = self.card_tree.focus()
        if not selected_item:
            messagebox.showwarning("Warning", "Please select a card to edit")
            return
            
        card_values = self.card_tree.item(selected_item, "values")
        card_id = card_values[0]
        
        # Get full card data
        card_data = self.db.get_card(card_id)
        if not card_data:
            messagebox.showerror("Error", "Could not retrieve card data")
            return
            
        # Simple dialog for editing a card
        dialog = Toplevel(self.root)
        dialog.title(f"Edit Card: {card_id}")
        dialog.geometry("400x300")
        
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Card ID:").grid(row=0, column=0, sticky=tk.W, pady=5)
        id_label = ttk.Label(frame, text=card_id)
        id_label.grid(row=0, column=1, pady=5, sticky=tk.W)
        
        ttk.Label(frame, text="Name:").grid(row=1, column=0, sticky=tk.W, pady=5)
        name_entry = ttk.Entry(frame, width=30)
        name_entry.insert(0, card_data.get("name", ""))
        name_entry.grid(row=1, column=1, pady=5)
        
        ttk.Label(frame, text="Expiry (YYYY-MM-DD):").grid(row=2, column=0, sticky=tk.W, pady=5)
        expiry_entry = ttk.Entry(frame, width=30)
        expiry_entry.insert(0, card_data.get("expiry_date", "") or "")
        expiry_entry.grid(row=2, column=1, pady=5)
        
        valid_var = tk.BooleanVar(value=card_data.get("is_valid", False))
        ttk.Checkbutton(frame, text="Is Valid", variable=valid_var).grid(row=3, column=1, sticky=tk.W, pady=5)
        
        def save_card():
            name = name_entry.get().strip()
            expiry = expiry_entry.get().strip() or None
            is_valid = valid_var.get()
            
            updated_data = {
                "id": card_id,
                "name": name,
                "expiry_date": expiry,
                "is_valid": is_valid,
                "student_id": card_data.get("student_id"), # Preserve other fields
                "faculty": card_data.get("faculty"),
                "program": card_data.get("program"),
                "level": card_data.get("level"),
                "photo_path": card_data.get("photo_path")
            }
            
            try:
                self.db.add_card(updated_data) # Use add_card with REPLACE
                self._refresh_card_list()
                dialog.destroy()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to update card: {e}", parent=dialog)
                
        ttk.Button(frame, text="Save", command=save_card).grid(row=4, column=0, columnspan=2, pady=10)

    def _delete_card(self):
        selected_item = self.card_tree.focus()
        if not selected_item:
            messagebox.showwarning("Warning", "Please select a card to delete")
            return
            
        card_id = self.card_tree.item(selected_item, "values")[0]
        
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete card {card_id}?"):
            try:
                self.db.delete_card(card_id)
                self._refresh_card_list()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete card: {e}")

    def _toggle_card_validity(self):
        selected_item = self.card_tree.focus()
        if not selected_item:
            messagebox.showwarning("Warning", "Please select a card to toggle validity")
            return
            
        card_id = self.card_tree.item(selected_item, "values")[0]
        current_status_str = self.card_tree.item(selected_item, "values")[3]
        current_status = True if current_status_str == "Yes" else False
        new_status = not current_status
        
        try:
            self.db.update_card_status(card_id, new_status)
            self._refresh_card_list()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to update card status: {e}")

    def _setup_hardware_tab(self):
        # Title
        ttk.Label(
            self.hardware_frame, 
            text="Hardware Control", 
            font=("Helvetica", 16, "bold")
        ).pack(pady=(0, 10))
        
        # Gate control frame
        gate_frame = ttk.LabelFrame(self.hardware_frame, text="Gate Control")
        gate_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(gate_frame, text="Open Gate", command=self._open_gate).pack(side=tk.LEFT, padx=10, pady=10)
        ttk.Button(gate_frame, text="Close Gate", command=self._close_gate).pack(side=tk.LEFT, padx=10, pady=10)
        
        # Lock control frame
        lock_frame = ttk.LabelFrame(self.hardware_frame, text="Lock Control")
        lock_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(lock_frame, text="Engage Lock (Lock)", command=self._engage_lock).pack(side=tk.LEFT, padx=10, pady=10)
        ttk.Button(lock_frame, text="Disengage Lock (Unlock)", command=self._disengage_lock).pack(side=tk.LEFT, padx=10, pady=10)
        
        # LED control frame
        led_frame = ttk.LabelFrame(self.hardware_frame, text="LED Control")
        led_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(led_frame, text="Green LED On", command=lambda: self.hardware.set_green_led(True)).pack(side=tk.LEFT, padx=10, pady=10)
        ttk.Button(led_frame, text="Green LED Off", command=lambda: self.hardware.set_green_led(False)).pack(side=tk.LEFT, padx=10, pady=10)
        ttk.Button(led_frame, text="Red LED On", command=lambda: self.hardware.set_red_led(True)).pack(side=tk.LEFT, padx=10, pady=10)
        ttk.Button(led_frame, text="Red LED Off", command=lambda: self.hardware.set_red_led(False)).pack(side=tk.LEFT, padx=10, pady=10)
        
        # Buzzer control frame
        buzzer_frame = ttk.LabelFrame(self.hardware_frame, text="Buzzer Control")
        buzzer_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(buzzer_frame, text="Sound Success Beep", command=lambda: self.hardware.sound_buzzer(success=True)).pack(side=tk.LEFT, padx=10, pady=10)
        ttk.Button(buzzer_frame, text="Sound Error Beep", command=lambda: self.hardware.sound_buzzer(success=False)).pack(side=tk.LEFT, padx=10, pady=10)
        
        # Fan control frame
        fan_frame = ttk.LabelFrame(self.hardware_frame, text="Fan Control")
        fan_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(fan_frame, text="Fan On", command=lambda: self.hardware.set_fan(True)).pack(side=tk.LEFT, padx=10, pady=10)
        ttk.Button(fan_frame, text="Fan Off", command=lambda: self.hardware.set_fan(False)).pack(side=tk.LEFT, padx=10, pady=10)
        
        # Test scenarios frame
        test_frame = ttk.LabelFrame(self.hardware_frame, text="Test Scenarios")
        test_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(test_frame, text="Test Valid Access", command=self._test_valid_access).pack(side=tk.LEFT, padx=10, pady=10)
        ttk.Button(test_frame, text="Test Invalid Access", command=self._test_invalid_access).pack(side=tk.LEFT, padx=10, pady=10)

    def _setup_logs_tab(self):
        # Title
        ttk.Label(
            self.logs_frame, 
            text="System Logs", 
            font=("Helvetica", 16, "bold")
        ).pack(pady=(0, 10))
        
        # Log display area
        log_display_frame = ttk.Frame(self.logs_frame)
        log_display_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = Text(log_display_frame, height=20, wrap=tk.WORD)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        scrollbar = ttk.Scrollbar(log_display_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _update_logs(self):
        """Periodically update the logs display"""
        try:
            recent_logs = logger.get_recent_logs()
            for log_entry in recent_logs:
                self.log_text.insert(tk.END, log_entry + "\n")
            self.log_text.see(tk.END) # Scroll to bottom
        except Exception as e:
            print(f"Error updating logs: {e}")
        finally:
            # Schedule next update
            self.root.after(1000, self._update_logs)

    def _update_status(self):
        """Periodically update the dashboard status"""
        try:
            metrics = logger._get_current_metrics()
            self.total_requests_label.config(text=str(metrics["total_requests"]))
            self.successful_label.config(text=str(metrics["successful_accesses"]))
            self.failed_label.config(text=str(metrics["failed_accesses"]))
            self.avg_response_label.config(text=f"{metrics["average_response_time"]:.4f} s")
            uptime_seconds = metrics["system_uptime"]
            uptime_str = str(timedelta(seconds=int(uptime_seconds)))
            self.uptime_label.config(text=uptime_str)
            
            # Update status bar
            temp = self.hardware.get_temperature()
            temp_str = f"{temp:.1f}°C" if temp is not None else "N/A"
            self.status_bar.config(text=f"System Ready | Temp: {temp_str} | Fan: {"ON" if self.hardware.fan_on else "OFF"}")
            
        except Exception as e:
            print(f"Error updating status: {e}")
        finally:
            # Schedule next update
            self.root.after(2000, self._update_status)

    # Hardware control methods (delegated)
    def _open_gate(self):
        self.hardware.open_gate()

    def _close_gate(self):
        self.hardware.close_gate()

    def _engage_lock(self):
        self.hardware.engage_lock()

    def _disengage_lock(self):
        self.hardware.disengage_lock()

    def _test_valid_access(self):
        # Simulate a valid card scan
        # In a real scenario, you might use a known valid card ID
        logger.log_info("Simulating valid access test")
        self.access_controller.handle_access("VALID_TEST_CARD")

    def _test_invalid_access(self):
        # Simulate an invalid card scan
        logger.log_info("Simulating invalid access test")
        self.access_controller.handle_access("INVALID_TEST_CARD")

    def run(self):
        self.root.mainloop()

# --- REMOVED LoginWindow Class --- 
# No longer needed as per user request

class NFCSystem:
    def __init__(self):
        self.config = config
        self.db = DatabaseManager(self.config.DB_PATH, self.config.DB_ENCRYPTED)
        self.hardware = HardwareController(self.config)
        self.nfc_reader = NFCReader(self.config)
        self.access_controller = AccessController(self.db, self.hardware)
        self.small_screen_gui = None
        self.admin_gui = None
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=5)

    def start_small_screen(self):
        """Start the small screen GUI in a separate thread"""
        def run_gui():
            try:
                root = Tk()
                self.small_screen_gui = SmallScreenGUI(root)
                root.mainloop()
            except Exception as e:
                logger.log_error(e, "Error in small screen GUI thread")
                
        threading.Thread(target=run_gui, daemon=True).start()
        logger.log_info("Small screen GUI thread started")

    def start_admin_interface(self):
        """Start the admin GUI"""
        try:
            self.admin_gui = AdminGUI(
                self.db,
                self.hardware,
                self.access_controller,
                self.nfc_reader
            )
            self.admin_gui.run() # This will block until the GUI is closed
        except Exception as e:
            logger.log_error(e, "Failed to start admin interface")

    def run_background_tasks(self):
        """Run background tasks like temperature monitoring and NFC reading"""
        while not self.stop_event.is_set():
            try:
                # Manage fan based on temperature
                self.hardware.manage_fan()
                
                # Read NFC card
                card_id = self.nfc_reader.read_card()
                if card_id:
                    # Handle access attempt
                    status, card_data = self.access_controller.handle_access(card_id)
                    
                    # Update small screen GUI if available
                    if self.small_screen_gui:
                        self.small_screen_gui.display_card_info(card_data, status)
                        
                # Sleep to prevent high CPU usage
                time.sleep(0.1)
                
            except Exception as e:
                logger.log_error(e, "Error in background task loop")
                time.sleep(1) # Sleep longer on error

    def start(self):
        logger.log_info("Starting NFC Access Control System")
        
        # Start small screen GUI (optional)
        # self.start_small_screen()
        
        # Start background tasks in a separate thread
        self.background_thread = threading.Thread(target=self.run_background_tasks, daemon=True)
        self.background_thread.start()
        logger.log_info("Background tasks started")
        
        # MODIFIED: Start Admin GUI directly without authentication
        self.start_admin_interface()

    def stop(self):
        logger.log_info("Stopping NFC Access Control System")
        self.stop_event.set()
        self.executor.shutdown(wait=True)
        self.hardware.cleanup()
        logger.log_info("System stopped")

def main():
    # --- REMOVED Authentication Setup --- 
    # Authenticator.setup_credentials_interactively()
    
    system = NFCSystem()
    try:
        system.start()
    except KeyboardInterrupt:
        print("\nCtrl+C detected. Shutting down...")
    except Exception as e:
        logger.log_error(e, "Unhandled exception in main loop")
    finally:
        system.stop()

if __name__ == "__main__":
    main()

