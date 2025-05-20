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
os.environ['NFCPY_USB_DRIVER'] = ''  # Disable USB drivers to bypass usb1 import
sys.path.insert(0, '/home/pi/Desktop/nfcpy/src')
sys.path.insert(0, '/home/pi/Desktop/ndeflib/src')
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
        def setmode(self, mode):
            print(f"MockGPIO: Set mode to {mode}")
        def setup(self, pin, mode):
            print(f"MockGPIO: Setup pin {pin} to mode {mode}")
        def output(self, pin, state):
            print(f"MockGPIO: Set pin {pin} to state {state}")
        def cleanup(self, pin=None):
            print(f"MockGPIO: Cleanup pin {pin if pin else 'all'}")
    GPIO = MockGPIO()
    GPIO_AVAILABLE = False

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
        self.SOLENOID_PIN = self._validate_pin(self.config.getint('gpio', 'solenoid', fallback=27))  # Changed to GPIO 27 as per user's hardware setup
        self.LED_GREEN_PIN = self._validate_pin(self.config.getint('gpio', 'led_green', fallback=22))
        self.LED_RED_PIN = self._validate_pin(self.config.getint('gpio', 'led_red', fallback=23))
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
        self.GUI_UPDATE_INTERVAL = self.config.getint('performance', 'gui_update_ms', fallback=100)

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
            'solenoid': '27',  # Changed to GPIO 27 as per user's hardware setup
            'led_green': '22',
            'led_red': '23'
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
        default_config['performance'] = {
            'gui_update_ms': '100'
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
        
        # Create a new Toplevel window for authentication
        auth_window = Toplevel()
        auth_window.title("System Login")
        auth_window.geometry("300x150")
        auth_window.resizable(False, False)
        auth_window.grab_set()  # Make window modal
        
        Label(auth_window, text="Username:").pack(pady=(10,0))
        user_entry = Entry(auth_window)
        user_entry.pack()
        Label(auth_window, text="Password:").pack(pady=(5,0))
        pass_entry = Entry(auth_window, show="*")
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
                auth_window.destroy()
            else:
                attempts -= 1
                logger.log_audit("login_failed", {"username": username, "attempts_left": attempts})
                if attempts > 0:
                    messagebox.showwarning("Login Failed", f"Invalid credentials. {attempts} attempts remaining.")
                else:
                    logger.log_audit("login_locked", {"username": username})
                    messagebox.showerror("Login Locked", "Too many failed attempts. System locked.")
                    auth_window.destroy()
        
        Button(auth_window, text="Login", command=check_credentials).pack(pady=10)
        
        # Wait for the window to be closed
        auth_window.wait_window()
        return authenticated

class CardDatabase:
    def __init__(self, db_path: str, encrypted: bool = True) -> None:
        self.db_path = db_path
        self.encrypted = encrypted
        self.key = None
        
        # Simple cache for frequently accessed cards
        self.card_cache = {}
        self.cache_size = 20
        
        if self.encrypted:
            self._setup_encryption()
        self._setup_database()
        self._add_demo_data()

    def _setup_encryption(self) -> None:
        try:
            key_file = Path("db.key")
            if not key_file.exists():
                self.key = Fernet.generate_key()
                with open(key_file, 'wb') as f:
                    f.write(self.key)
                os.chmod(key_file, 0o600)
            else:
                with open(key_file, 'rb') as f:
                    self.key = f.read()
            self.cipher = Fernet(self.key)
        except Exception as e:
            logger.log_error(e, "Failed to setup encryption")
            self.encrypted = False

    def _setup_database(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
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
                is_valid INTEGER,
                last_access TEXT,
                photo_path TEXT
            )
            ''')
            # Add index for faster lookups
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_card_id ON cards(id)')
            conn.commit()
            conn.close()
        except Exception as e:
            logger.log_error(e, "Failed to setup database")

    def _add_demo_data(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM cards")
            count = cursor.fetchone()[0]
            if count == 0:
                demo_cards = [
                    ("04010203040506", "John Smith", "Engineering", "Computer Science", "Year 3", "ENG123456", 
                     "john.smith@university.edu", (datetime.now() + timedelta(days=365)).isoformat(), 1, 
                     None, "photos/john_smith.jpg"),
                    ("04060708090A0B", "Jane Doe", "Science", "Physics", "Year 2", "SCI789012", 
                     "jane.doe@university.edu", (datetime.now() + timedelta(days=180)).isoformat(), 1, 
                     None, "photos/jane_doe.jpg"),
                    ("040C0D0E0F1011", "Invalid User", "Business", "Management", "Year 1", "BUS345678", 
                     "invalid.user@university.edu", (datetime.now() - timedelta(days=10)).isoformat(), 0, 
                     None, "photos/invalid_user.jpg")
                ]
                for card in demo_cards:
                    self.add_card(*card)
                print("Added demo data to database")
            conn.close()
        except Exception as e:
            logger.log_error(e, "Failed to add demo data")

    def _encrypt(self, data: str) -> str:
        if not self.encrypted or not data:
            return data
        try:
            return self.cipher.encrypt(data.encode()).decode()
        except Exception as e:
            logger.log_error(e, "Encryption failed")
            return data

    def _decrypt(self, data: str) -> str:
        if not self.encrypted or not data:
            return data
        try:
            return self.cipher.decrypt(data.encode()).decode()
        except InvalidToken:
            logger.log_error(InvalidToken("Invalid token"), "Decryption failed")
            return ""
        except Exception as e:
            logger.log_error(e, "Decryption failed")
            return ""

    def add_card(self, card_id: str, name: str, faculty: str, program: str, level: str, 
                student_id: str, email: str, expiry_date: str, is_valid: int, 
                last_access: Optional[str] = None, photo_path: Optional[str] = None) -> bool:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            if self.encrypted:
                name = self._encrypt(name)
                faculty = self._encrypt(faculty)
                program = self._encrypt(program)
                level = self._encrypt(level)
                student_id = self._encrypt(student_id)
                email = self._encrypt(email)
                photo_path = self._encrypt(photo_path) if photo_path else None
            cursor.execute('''
            INSERT OR REPLACE INTO cards 
            (id, name, faculty, program, level, student_id, email, expiry_date, is_valid, last_access, photo_path) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (card_id, name, faculty, program, level, student_id, email, expiry_date, is_valid, last_access, photo_path))
            conn.commit()
            conn.close()
            
            # Update cache if card exists
            if card_id in self.card_cache:
                del self.card_cache[card_id]
                
            return True
        except Exception as e:
            logger.log_error(e, f"Failed to add card {card_id}")
            return False

    def get_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        # Check cache first
        if card_id in self.card_cache:
            return self.card_cache[card_id]
            
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cards WHERE id = ?", (card_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return None
                
            columns = [col[0] for col in cursor.description]
            card_data = dict(zip(columns, row))
            conn.close()
            
            if self.encrypted:
                card_data['name'] = self._decrypt(card_data['name'])
                card_data['faculty'] = self._decrypt(card_data['faculty'])
                card_data['program'] = self._decrypt(card_data['program'])
                card_data['level'] = self._decrypt(card_data['level'])
                card_data['student_id'] = self._decrypt(card_data['student_id'])
                card_data['email'] = self._decrypt(card_data['email'])
                if card_data['photo_path']:
                    card_data['photo_path'] = self._decrypt(card_data['photo_path'])
            
            # Add to cache
            if len(self.card_cache) >= self.cache_size:
                # Remove oldest item (first key)
                if self.card_cache:
                    self.card_cache.pop(next(iter(self.card_cache)))
            self.card_cache[card_id] = card_data
            
            return card_data
        except Exception as e:
            logger.log_error(e, f"Failed to get card {card_id}")
            return None

    def update_last_access(self, card_id: str) -> bool:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE cards SET last_access = ? WHERE id = ?", 
                (datetime.now().isoformat(), card_id)
            )
            conn.commit()
            conn.close()
            
            # Update cache if card exists
            if card_id in self.card_cache:
                self.card_cache[card_id]['last_access'] = datetime.now().isoformat()
                
            return True
        except Exception as e:
            logger.log_error(e, f"Failed to update last access for card {card_id}")
            return False

    def get_all_cards(self) -> List[Dict[str, Any]]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cards")
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
            cards = []
            for row in rows:
                card_data = dict(zip(columns, row))
                if self.encrypted:
                    card_data['name'] = self._decrypt(card_data['name'])
                    card_data['faculty'] = self._decrypt(card_data['faculty'])
                    card_data['program'] = self._decrypt(card_data['program'])
                    card_data['level'] = self._decrypt(card_data['level'])
                    card_data['student_id'] = self._decrypt(card_data['student_id'])
                    card_data['email'] = self._decrypt(card_data['email'])
                    if card_data['photo_path']:
                        card_data['photo_path'] = self._decrypt(card_data['photo_path'])
                cards.append(card_data)
            conn.close()
            return cards
        except Exception as e:
            logger.log_error(e, "Failed to get all cards")
            return []

class NFCReader:
    def __init__(self, config_obj: Config) -> None:
        self.config = config_obj
        self.clf = None
        self.connected = False
        self.stop_event = threading.Event()
        self.card_detected_event = threading.Event()
        self.card_id = None
        self.reader_thread = None
        self.mock_mode = not NFC_AVAILABLE
        
        if not self.mock_mode:
            self._initialize_reader()
        else:
            print("Using mock NFC reader (NFC libraries not available)")

    def _initialize_reader(self) -> None:
        try:
            # Initialize NFC reader with I2C
            self.clf = nfc.ContactlessFrontend('i2c')
            print(f"NFC reader initialized: {self.clf}")
            self.connected = True
        except Exception as e:
            logger.log_error(e, "Failed to initialize NFC reader")
            self.mock_mode = True
            print("Falling back to mock NFC mode due to initialization error")

    def start_reading(self) -> None:
        if self.reader_thread and self.reader_thread.is_alive():
            return
        
        self.stop_event.clear()
        self.card_detected_event.clear()
        
        if self.mock_mode:
            self.reader_thread = threading.Thread(target=self._mock_reader_loop)
        else:
            self.reader_thread = threading.Thread(target=self._reader_loop)
            
        self.reader_thread.daemon = True
        self.reader_thread.start()

    def stop_reading(self) -> None:
        self.stop_event.set()
        if self.reader_thread:
            self.reader_thread.join(timeout=2.0)
        if not self.mock_mode and self.clf:
            try:
                self.clf.close()
                self.connected = False
            except Exception as e:
                logger.log_error(e, "Error closing NFC reader")

    def _reader_loop(self) -> None:
        attempts = 0
        
        while not self.stop_event.is_set() and attempts < self.config.NFC_MAX_ATTEMPTS:
            try:
                if not self.connected:
                    self._initialize_reader()
                
                # Configure target for ISO14443-A (MIFARE) cards
                target = RemoteTarget(f"106{self.config.NFC_PROTOCOL}")
                
                # Poll for cards
                tag = self.clf.connect(rdwr={'on-connect': lambda tag: False}, 
                                      targets=[target],
                                      interval=0.1,
                                      iterations=int(self.config.NFC_TIMEOUT / 0.1))
                
                if tag:
                    # Extract card ID
                    self.card_id = str(tag.identifier.hex()).upper()
                    print(f"Card detected: {self.card_id}")
                    self.card_detected_event.set()
                    time.sleep(1)  # Prevent multiple reads of the same card
                    self.card_detected_event.clear()
                
                attempts = 0  # Reset attempts on successful operation
                
            except Exception as e:
                attempts += 1
                logger.log_error(e, f"NFC reader error (attempt {attempts}/{self.config.NFC_MAX_ATTEMPTS})")
                time.sleep(1)
                
                # Try to reinitialize the reader
                if self.clf:
                    try:
                        self.clf.close()
                    except:
                        pass
                self.connected = False
                
        if attempts >= self.config.NFC_MAX_ATTEMPTS:
            logger.log_error(RuntimeError("Maximum NFC reader attempts reached"), "NFC Reader")
            self.mock_mode = True
            print("Switching to mock mode after maximum attempts")
            # Start mock reader after real reader fails
            self._mock_reader_loop()

    def _mock_reader_loop(self) -> None:
        print("Mock mode: Starting simulated card reader")
        while not self.stop_event.is_set():
            # In mock mode, we don't automatically simulate card reads
            # We only wait for manual triggering through the wait_for_card method
            time.sleep(0.5)

    def wait_for_card(self, timeout: float = None) -> Optional[str]:
        if self.card_detected_event.wait(timeout):
            return self.card_id
        return None

    def read_card_data(self) -> Optional[Dict[str, Any]]:
        """Simulate reading additional data from card (NDEF records, etc.)"""
        if self.mock_mode:
            return {"type": "NDEF", "records": ["Mock NDEF Record"]}
        
        # In a real implementation, this would read NDEF records or other card data
        # For now, we'll just return the card ID
        if self.card_id:
            return {"id": self.card_id}
        return None
        
    def simulate_card_read(self, card_id: str) -> None:
        """Manually simulate a card read (for testing only)"""
        self.card_id = card_id
        self.card_detected_event.set()
        time.sleep(0.5)  # Brief delay
        self.card_detected_event.clear()

class HardwareController:
    def __init__(self, config_obj: Config) -> None:
        self.config = config_obj
        self.servo = None
        self.fan_running = False
        self.buzzer_running = False
        self.lock_engaged = True  # Default state is locked
        self.green_led_on = False
        self.red_led_on = False
        
        # Initialize GPIO
        if GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)  # Disable warnings to prevent runtime errors
            
            # Setup pins
            GPIO.setup(self.config.SERVO_PIN, GPIO.OUT)
            GPIO.setup(self.config.FAN_PIN, GPIO.OUT)
            GPIO.setup(self.config.BUZZER_PIN, GPIO.OUT)
            GPIO.setup(self.config.SOLENOID_PIN, GPIO.OUT)
            GPIO.setup(self.config.LED_GREEN_PIN, GPIO.OUT)
            GPIO.setup(self.config.LED_RED_PIN, GPIO.OUT)
            
            # Initialize servo
            self.servo = GPIO.PWM(self.config.SERVO_PIN, 50)  # 50Hz frequency
            self.servo.start(0)  # Start with 0 duty cycle (no movement)
            
            # Initialize LEDs (off by default)
            GPIO.output(self.config.LED_GREEN_PIN, GPIO.LOW)
            GPIO.output(self.config.LED_RED_PIN, GPIO.LOW)
            
            # Initialize solenoid lock (HIGH = locked, LOW = unlocked)
            # Use the specific initialization command for GPIO 27 as specified by user
            os.system('gpio -g mode 27 out')
            os.system('gpio -g write 27 1')  # Set to HIGH (locked) by default
            GPIO.output(self.config.SOLENOID_PIN, GPIO.HIGH)  # Start with lock engaged
            
        self.temp_monitor_thread = threading.Thread(target=self._monitor_temperature)
        self.temp_monitor_thread.daemon = True
        self.temp_monitor_thread.start()

    def open_gate(self) -> None:
        if not GPIO_AVAILABLE:
            print("MockGPIO: Opening gate")
            return
        
        try:
            # Only change duty cycle when needed
            self.servo.ChangeDutyCycle(self.config.SERVO_OPEN_DUTY)
            time.sleep(self.config.SERVO_DELAY)
            # Stop PWM to prevent jitter and continuous rotation
            self.servo.ChangeDutyCycle(0)
        except Exception as e:
            logger.log_error(e, "Failed to open gate")

    def close_gate(self) -> None:
        if not GPIO_AVAILABLE:
            print("MockGPIO: Closing gate")
            return
        
        try:
            # Only change duty cycle when needed
            self.servo.ChangeDutyCycle(self.config.SERVO_CLOSE_DUTY)
            time.sleep(self.config.SERVO_DELAY)
            # Stop PWM to prevent jitter and continuous rotation
            self.servo.ChangeDutyCycle(0)
        except Exception as e:
            logger.log_error(e, "Failed to close gate")

    def engage_lock(self) -> None:
        """Engage the solenoid lock (locked state)"""
        if not GPIO_AVAILABLE:
            print("MockGPIO: Engaging lock")
            self.lock_engaged = True
            return
        
        try:
            # Use both GPIO and direct system command for reliability
            GPIO.output(self.config.SOLENOID_PIN, GPIO.HIGH)  # HIGH = locked
            os.system('gpio -g write 27 1')  # Set GPIO 27 HIGH using gpio command
            self.lock_engaged = True
        except Exception as e:
            logger.log_error(e, "Failed to engage lock")

    def disengage_lock(self) -> None:
        """Disengage the solenoid lock (unlocked state)"""
        if not GPIO_AVAILABLE:
            print("MockGPIO: Disengaging lock")
            self.lock_engaged = False
            return
        
        try:
            # Use both GPIO and direct system command for reliability
            GPIO.output(self.config.SOLENOID_PIN, GPIO.LOW)  # LOW = unlocked
            os.system('gpio -g write 27 0')  # Set GPIO 27 LOW using gpio command
            self.lock_engaged = False
        except Exception as e:
            logger.log_error(e, "Failed to disengage lock")

    def set_green_led(self, state: bool) -> None:
        """Control the green LED"""
        if not GPIO_AVAILABLE:
            print(f"MockGPIO: Green LED {'on' if state else 'off'}")
            self.green_led_on = state
            return
            
        try:
            GPIO.output(self.config.LED_GREEN_PIN, GPIO.HIGH if state else GPIO.LOW)
            self.green_led_on = state
        except Exception as e:
            logger.log_error(e, "Failed to control green LED")
            
    def set_red_led(self, state: bool) -> None:
        """Control the red LED"""
        if not GPIO_AVAILABLE:
            print(f"MockGPIO: Red LED {'on' if state else 'off'}")
            self.red_led_on = state
            return
            
        try:
            GPIO.output(self.config.LED_RED_PIN, GPIO.HIGH if state else GPIO.LOW)
            self.red_led_on = state
        except Exception as e:
            logger.log_error(e, "Failed to control red LED")

    def sound_buzzer(self, duration: float = 0.5, success: bool = True) -> None:
        if self.buzzer_running:
            return
            
        def _buzzer_thread(duration, pattern):
            self.buzzer_running = True
            if not GPIO_AVAILABLE:
                print(f"MockGPIO: Buzzer {'success' if success else 'error'} sound for {duration}s")
                time.sleep(duration)
                self.buzzer_running = False
                return
                
            try:
                for p in pattern:
                    GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
                    time.sleep(p[0])
                    GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
                    time.sleep(p[1])
            except Exception as e:
                logger.log_error(e, "Buzzer error")
            finally:
                if GPIO_AVAILABLE:
                    GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
                self.buzzer_running = False
        
        # Different patterns for success/error
        if success:
            pattern = [(0.1, 0.05), (0.3, 0.05)]  # Short beep, long beep
        else:
            pattern = [(0.1, 0.05), (0.1, 0.05), (0.1, 0.05)]  # Three short beeps
            
        threading.Thread(target=_buzzer_thread, args=(duration, pattern), daemon=True).start()

    def control_fan(self, on: bool) -> None:
        if not GPIO_AVAILABLE:
            print(f"MockGPIO: Fan {'on' if on else 'off'}")
            self.fan_running = on
            return
            
        try:
            GPIO.output(self.config.FAN_PIN, GPIO.HIGH if on else GPIO.LOW)
            self.fan_running = on
        except Exception as e:
            logger.log_error(e, "Failed to control fan")

    def _monitor_temperature(self) -> None:
        while True:
            try:
                if os.path.exists(self.config.THERMAL_FILE):
                    with open(self.config.THERMAL_FILE, 'r') as f:
                        temp = float(f.read().strip()) / 1000.0  # Convert to Celsius
                        
                    if temp >= self.config.FAN_ON_TEMP and not self.fan_running:
                        self.control_fan(True)
                        logger.log_info(f"Fan turned ON (Temperature: {temp}°C)")
                    elif temp <= self.config.FAN_OFF_TEMP and self.fan_running:
                        self.control_fan(False)
                        logger.log_info(f"Fan turned OFF (Temperature: {temp}°C)")
            except Exception as e:
                logger.log_error(e, "Temperature monitoring error")
                
            time.sleep(30)  # Check every 30 seconds

    def cleanup(self) -> None:
        if not GPIO_AVAILABLE:
            print("MockGPIO: Cleanup")
            return
            
        try:
            # Turn off all LEDs
            GPIO.output(self.config.LED_GREEN_PIN, GPIO.LOW)
            GPIO.output(self.config.LED_RED_PIN, GPIO.LOW)
            
            # Stop servo
            if self.servo:
                self.servo.stop()
                
            GPIO.cleanup()
        except Exception as e:
            logger.log_error(e, "GPIO cleanup error")

class AccessController:
    def __init__(self, db: CardDatabase, hardware: HardwareController) -> None:
        self.db = db
        self.hardware = hardware
        self.rate_limit = {}  # Store card_id -> last_access_time
        self.rate_limit_window = 5  # seconds
        self.blacklist = set()  # Store blacklisted card IDs

    def process_card(self, card_id: str) -> Tuple[AccessStatus, Optional[Dict[str, Any]]]:
        start_time = time.time()
        
        # Check rate limiting
        if card_id in self.rate_limit:
            last_time = self.rate_limit[card_id]
            if time.time() - last_time < self.rate_limit_window:
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
        is_valid = bool(card_data['is_valid'])
        
        # Check expiry date if present
        if card_data['expiry_date']:
            try:
                expiry = datetime.fromisoformat(card_data['expiry_date'])
                if expiry < datetime.now():
                    is_valid = False
            except (ValueError, TypeError) as e:
                logger.log_error(e, f"Invalid expiry date format for card {card_id}")
                
        # Update last access time in database
        self.db.update_last_access(card_id)
        
        # Create card info object for logging
        card_info = CardInfo(
            id=card_id,
            name=card_data['name'],
            expiry_date=datetime.fromisoformat(card_data['expiry_date']) if card_data['expiry_date'] else None,
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
            self.root.attributes('-fullscreen', True)  # Fullscreen for Raspberry Pi
        
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
                self.name_label.config(text=card_data.get('name', 'Unknown'))
                self.id_label.config(text=card_data.get('student_id', 'Unknown'))
                self.faculty_label.config(text=card_data.get('faculty', 'Unknown'))
                self.program_label.config(text=card_data.get('program', 'Unknown'))
                self.level_label.config(text=card_data.get('level', 'Unknown'))
                
                # TODO: Load photo if available
                photo_path = card_data.get('photo_path')
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
            logger.log_error(e, f"Failed to display card info for {card_data.get('id', 'unknown')}")

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
        
        # Cards list frame
        list_frame = ttk.Frame(self.cards_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        # Create treeview for cards
        columns = ("ID", "Name", "Faculty", "Program", "Level", "Student ID", "Valid")
        self.cards_tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        
        # Set column headings
        for col in columns:
            self.cards_tree.heading(col, text=col)
            self.cards_tree.column(col, width=100)
        
        # Add scrollbar
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.cards_tree.yview)
        self.cards_tree.configure(yscrollcommand=scrollbar.set)
        
        self.cards_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Actions frame
        actions_frame = ttk.Frame(self.cards_frame)
        actions_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(
            actions_frame,
            text="Refresh",
            command=self._refresh_cards
        ).pack(side=tk.LEFT, padx=5)
        
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
            text="Toggle Validity",
            command=self._toggle_card_validity
        ).pack(side=tk.LEFT, padx=5)
        
        # Initial load of cards
        self._refresh_cards()

    def _setup_hardware_tab(self):
        # Title
        ttk.Label(
            self.hardware_frame, 
            text="Hardware Control", 
            font=("Helvetica", 16, "bold")
        ).pack(pady=(0, 10))
        
        # Gate control frame
        gate_frame = ttk.LabelFrame(self.hardware_frame, text="Gate Control")
        gate_frame.pack(fill=tk.X, pady=10, padx=5)
        
        ttk.Button(
            gate_frame,
            text="Open Gate",
            command=self._open_gate
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        ttk.Button(
            gate_frame,
            text="Close Gate",
            command=self._close_gate
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        # Lock control frame
        lock_frame = ttk.LabelFrame(self.hardware_frame, text="Lock Control")
        lock_frame.pack(fill=tk.X, pady=10, padx=5)
        
        ttk.Button(
            lock_frame,
            text="Engage Lock",
            command=self._engage_lock
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        ttk.Button(
            lock_frame,
            text="Disengage Lock",
            command=self._disengage_lock
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        # LED control frame
        led_frame = ttk.LabelFrame(self.hardware_frame, text="LED Control")
        led_frame.pack(fill=tk.X, pady=10, padx=5)
        
        ttk.Button(
            led_frame,
            text="Green LED On",
            command=lambda: self.hardware.set_green_led(True)
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        ttk.Button(
            led_frame,
            text="Green LED Off",
            command=lambda: self.hardware.set_green_led(False)
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        ttk.Button(
            led_frame,
            text="Red LED On",
            command=lambda: self.hardware.set_red_led(True)
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        ttk.Button(
            led_frame,
            text="Red LED Off",
            command=lambda: self.hardware.set_red_led(False)
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        # Buzzer control frame
        buzzer_frame = ttk.LabelFrame(self.hardware_frame, text="Buzzer Control")
        buzzer_frame.pack(fill=tk.X, pady=10, padx=5)
        
        ttk.Button(
            buzzer_frame,
            text="Success Sound",
            command=lambda: self.hardware.sound_buzzer(success=True)
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        ttk.Button(
            buzzer_frame,
            text="Error Sound",
            command=lambda: self.hardware.sound_buzzer(success=False)
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        # Test scenarios frame
        test_frame = ttk.LabelFrame(self.hardware_frame, text="Test Scenarios")
        test_frame.pack(fill=tk.X, pady=10, padx=5)
        
        ttk.Button(
            test_frame,
            text="Test Valid Access",
            command=self._test_valid_access
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        ttk.Button(
            test_frame,
            text="Test Invalid Access",
            command=self._test_invalid_access
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        # System control frame
        system_frame = ttk.LabelFrame(self.hardware_frame, text="System Control")
        system_frame.pack(fill=tk.X, pady=10, padx=5)
        
        ttk.Button(
            system_frame,
            text="Restart NFC Reader",
            command=self._restart_nfc
        ).pack(side=tk.LEFT, padx=10, pady=10)
        
        ttk.Button(
            system_frame,
            text="Reset Hardware",
            command=self._reset_hardware
        ).pack(side=tk.LEFT, padx=10, pady=10)

    def _setup_logs_tab(self):
        # Title
        ttk.Label(
            self.logs_frame, 
            text="System Logs", 
            font=("Helvetica", 16, "bold")
        ).pack(pady=(0, 10))
        
        # Logs text area
        self.logs_text = Text(self.logs_frame, height=20, wrap=tk.WORD)
        self.logs_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Controls frame
        controls_frame = ttk.Frame(self.logs_frame)
        controls_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(
            controls_frame,
            text="Refresh Logs",
            command=self._update_logs
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(
            controls_frame,
            text="Clear Display",
            command=lambda: self.logs_text.delete(1.0, tk.END)
        ).pack(side=tk.LEFT, padx=5)

    def _open_gate(self):
        self.hardware.open_gate()
        self.status_bar.config(text="Gate opened")
        logger.log_audit("gate_open", {"method": "manual", "user": "admin"})

    def _close_gate(self):
        self.hardware.close_gate()
        self.status_bar.config(text="Gate closed")
        logger.log_audit("gate_close", {"method": "manual", "user": "admin"})

    def _engage_lock(self):
        self.hardware.engage_lock()
        self.status_bar.config(text="Lock engaged")
        logger.log_audit("lock_engaged", {"method": "manual", "user": "admin"})

    def _disengage_lock(self):
        self.hardware.disengage_lock()
        self.status_bar.config(text="Lock disengaged")
        logger.log_audit("lock_disengaged", {"method": "manual", "user": "admin"})

    def _test_valid_access(self):
        # Simulate valid card scan
        print("Admin: Simulating valid card scan for ID 04010203040506")
        self.nfc_reader.simulate_card_read("04010203040506")
        self.status_bar.config(text="Test valid access initiated")
        logger.log_audit("test_access", {"status": "GRANTED", "card_id": "04010203040506"})

    def _test_invalid_access(self):
        # Simulate invalid card scan
        print("Admin: Simulating invalid card scan for ID 040C0D0E0F1011")
        self.nfc_reader.simulate_card_read("040C0D0E0F1011")
        self.status_bar.config(text="Test invalid access initiated")
        logger.log_audit("test_access", {"status": "DENIED", "card_id": "040C0D0E0F1011"})

    def _restart_nfc(self):
        # Restart the NFC reader
        self.nfc_reader.stop_reading()
        time.sleep(1)
        self.nfc_reader.start_reading()
        self.status_bar.config(text="NFC reader restarted")
        logger.log_audit("nfc_restart", {"user": "admin"})

    def _reset_hardware(self):
        # Reset all hardware components
        self.hardware.close_gate()
        self.hardware.engage_lock()
        self.hardware.set_green_led(False)
        self.hardware.set_red_led(False)
        self.status_bar.config(text="Hardware reset")
        logger.log_audit("hardware_reset", {"user": "admin"})

    def _refresh_cards(self):
        # Clear existing items
        for item in self.cards_tree.get_children():
            self.cards_tree.delete(item)
            
        # Get all cards from database
        cards = self.db.get_all_cards()
        
        # Add to treeview
        for card in cards:
            values = (
                card['id'],
                card['name'],
                card['faculty'],
                card['program'],
                card['level'],
                card['student_id'],
                "Yes" if card['is_valid'] else "No"
            )
            self.cards_tree.insert("", tk.END, values=values)
            
        self.status_bar.config(text=f"Loaded {len(cards)} cards")

    def _add_card_dialog(self):
        # This would open a dialog to add a new card
        messagebox.showinfo("Add Card", "This feature would allow adding a new card")

    def _edit_card_dialog(self):
        # This would open a dialog to edit the selected card
        selected = self.cards_tree.selection()
        if not selected:
            messagebox.showinfo("Edit Card", "Please select a card to edit")
            return
            
        card_id = self.cards_tree.item(selected[0], "values")[0]
        messagebox.showinfo("Edit Card", f"This feature would allow editing card {card_id}")

    def _toggle_card_validity(self):
        # This would toggle the validity of the selected card
        selected = self.cards_tree.selection()
        if not selected:
            messagebox.showinfo("Toggle Validity", "Please select a card to toggle")
            return
            
        card_id = self.cards_tree.item(selected[0], "values")[0]
        messagebox.showinfo("Toggle Validity", f"This feature would toggle validity for card {card_id}")

    def _update_logs(self):
        # Get recent logs and update the logs text area
        logs = logger.get_recent_logs()
        if logs:
            for log in logs:
                self.logs_text.insert(tk.END, log + "\n")
            self.logs_text.see(tk.END)  # Scroll to bottom
            
        # Also update the activity text in dashboard
        if logs:
            for log in logs[:5]:  # Show only the 5 most recent logs
                self.activity_text.insert(tk.END, log + "\n")
            self.activity_text.see(tk.END)
            
        # Schedule next update
        self.root.after(1000, self._update_logs)

    def _update_status(self):
        # Update statistics in dashboard
        metrics = logger._get_current_metrics()
        
        self.total_requests_label.config(text=str(metrics['total_requests']))
        self.successful_label.config(text=str(metrics['successful_accesses']))
        self.failed_label.config(text=str(metrics['failed_accesses']))
        self.avg_response_label.config(text=f"{metrics['average_response_time']:.4f} sec")
        self.uptime_label.config(text=f"{metrics['system_uptime']:.1f} sec")
        
        # Schedule next update
        self.root.after(5000, self._update_status)

    def run(self):
        self.root.mainloop()

class SmartEntrySystem:
    def __init__(self):
        self.config = config
        self.db = CardDatabase(self.config.DB_PATH, self.config.DB_ENCRYPTED)
        self.hardware = HardwareController(self.config)
        self.nfc_reader = NFCReader(self.config)
        self.access_controller = AccessController(self.db, self.hardware)
        
        # Initialize small screen GUI in a separate thread
        self.small_screen = None
        self.small_screen_thread = threading.Thread(target=self._run_small_screen)
        self.small_screen_thread.daemon = True
        
        # Main application GUI
        self.admin_gui = None
        
        # Flag to control system running
        self.running = False
        
    def _run_small_screen(self):
        """Run the small screen GUI in a separate thread"""
        root = Tk()
        self.small_screen = SmallScreenGUI(root)
        print("Initializing small screen GUI...")
        root.mainloop()
        
    def start(self):
        """Start the system"""
        self.running = True
        
        # Start the small screen GUI thread
        self.small_screen_thread.start()
        
        # Wait for small screen GUI to initialize
        time.sleep(1)
        print("Small screen GUI initialized successfully")
        
        # Start NFC reader
        self.nfc_reader.start_reading()
        
        # Start main processing loop
        self.process_loop()
        
    def process_loop(self):
        """Main processing loop"""
        try:
            while self.running:
                # Wait for card detection
                card_id = self.nfc_reader.wait_for_card(timeout=0.5)
                
                if card_id:
                    # Process card access
                    status, card_data = self.access_controller.handle_access(card_id)
                    
                    # Update small screen GUI if available
                    if self.small_screen:
                        self.small_screen.display_card_info(card_data, status)
                
                # Update small screen GUI
                if self.small_screen:
                    self.small_screen.update()
                    
        except KeyboardInterrupt:
            print("System shutdown requested")
            self.stop()
        except Exception as e:
            logger.log_error(e, "Error in main processing loop")
            self.stop()
            
    def stop(self):
        """Stop the system"""
        self.running = False
        self.nfc_reader.stop_reading()
        self.hardware.cleanup()
        print("System stopped")
        
    def start_admin_interface(self):
        """Start the admin interface"""
        # Check authentication
        if not Authenticator.authenticate():
            print("Authentication failed")
            return False
            
        # Create and run admin GUI
        self.admin_gui = AdminGUI(self.db, self.hardware, self.access_controller, self.nfc_reader)
        self.admin_gui.run()
        return True

def main():
    # Setup credentials if needed
    Authenticator.setup_credentials_interactively()
    
    # For demonstration, bypass authentication for GUI demonstration
    print("Bypassing authentication for GUI demonstration...")
    
    # Create and start the system
    system = SmartEntrySystem()
    print("Starting application...")
    
    # Start in a separate thread to allow GUI to run
    system_thread = threading.Thread(target=system.start)
    system_thread.daemon = True
    system_thread.start()
    
    # Run the admin interface (this will block until closed)
    system.start_admin_interface()
    
    # Keep the main thread running
    try:
        while system_thread.is_alive():
            time.sleep(0.1)
    except KeyboardInterrupt:
        system.stop()

if __name__ == "__main__":
    main()
