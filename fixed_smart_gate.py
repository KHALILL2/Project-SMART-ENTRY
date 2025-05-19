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

# Mock Hardware for Testing
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

# Check for NFC libraries with improved error handling
try:
    import nfc
    from nfc.clf import RemoteTarget
    print("nfcpy and ndeflib modules loaded successfully!")
    NFC_AVAILABLE = True
except ImportError:
    print("WARNING: nfc library not found. Using mock NFC.")
    NFC_AVAILABLE = False
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
        else:
            self.metrics.average_response_time = response_time
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
            logger.log_error(e, "Failed to retrieve credentials from keyring. Ensure keyring is installed and configured.")
            self.EMAIL_USER = None
            self.EMAIL_PASS = None

        self.EMAIL_HOST = self.config.get('email', 'host', fallback='smtp.gmail.com')
        self.EMAIL_PORT = self.config.getint('email', 'port', fallback=587)
        self.EMAIL_USE_TLS = self.config.getboolean('email', 'use_tls', fallback=True)

        self.VALID_PINS = self._parse_list(self.config.get('gpio', 'valid_pins', fallback=str(self.DEFAULT_VALID_PINS)), int)
        self.SERVO_PIN = self._validate_pin(self.config.getint('gpio', 'servo', fallback=18))
        self.FAN_PIN = self._validate_pin(self.config.getint('gpio', 'fan', fallback=17))
        self.BUZZER_PIN = self._validate_pin(self.config.getint('gpio', 'buzzer', fallback=25))
        self.GREEN_LED_PIN = self._validate_pin(self.config.getint('gpio', 'green_led', fallback=24))
        self.RED_LED_PIN = self._validate_pin(self.config.getint('gpio', 'red_led', fallback=23))

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
            'fan': '17',
            'buzzer': '25',
            'green_led': '24',
            'red_led': '23'
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
            logger.log_error(ValueError(f"Invalid pin {pin} configured. Not in VALID_PINS: {self.VALID_PINS}. Falling back to default 18."))
            return 18

    def _validate_duty(self, duty):
        return min(max(2.5, duty), 12.5)

config = Config()

class ConfigValidator:
    @staticmethod
    def validate_config(config_obj: Config) -> bool:
        try:
            if not config_obj.EMAIL_HOST or not config_obj.EMAIL_PORT:
                raise ValueError("Email configuration incomplete (host/port)")
            if config_obj.EMAIL_USER is None or config_obj.EMAIL_PASS is None:
                logger.log_info("Email user/pass not found in keyring")

            if config_obj.SERVO_PIN not in config_obj.VALID_PINS:
                raise ValueError(f"Invalid servo pin: {config_obj.SERVO_PIN}")
            if config_obj.FAN_PIN not in config_obj.VALID_PINS:
                raise ValueError(f"Invalid fan pin: {config_obj.FAN_PIN}")
            if config_obj.BUZZER_PIN not in config_obj.VALID_PINS:
                raise ValueError(f"Invalid buzzer pin: {config_obj.BUZZER_PIN}")
            if config_obj.GREEN_LED_PIN not in config_obj.VALID_PINS:
                raise ValueError(f"Invalid green LED pin: {config_obj.GREEN_LED_PIN}")
            if config_obj.RED_LED_PIN not in config_obj.VALID_PINS:
                raise ValueError(f"Invalid red LED pin: {config_obj.RED_LED_PIN}")

            if not (2.5 <= config_obj.SERVO_OPEN_DUTY <= 12.5):
                raise ValueError(f"Invalid servo open duty: {config_obj.SERVO_OPEN_DUTY}")
            if not (2.5 <= config_obj.SERVO_CLOSE_DUTY <= 12.5):
                raise ValueError(f"Invalid servo close duty: {config_obj.SERVO_CLOSE_DUTY}")
            if config_obj.SERVO_DELAY <= 0:
                raise ValueError(f"Invalid servo delay: {config_obj.SERVO_DELAY}")

            if config_obj.FAN_ON_TEMP <= config_obj.FAN_OFF_TEMP:
                raise ValueError("Fan ON temperature must be greater than OFF temperature")
            if not os.path.exists(config_obj.THERMAL_FILE):
                logger.log_error(FileNotFoundError(f"Thermal file not found: {config_obj.THERMAL_FILE}. Temperature monitoring may fail."))

            if config_obj.NFC_MAX_ATTEMPTS < 1:
                raise ValueError("NFC max attempts must be positive")
            if config_obj.NFC_TIMEOUT < 1:
                raise ValueError("NFC timeout must be positive")

            if not config_obj.DB_PATH:
                raise ValueError("Database path is required")
            db_dir = os.path.dirname(config_obj.DB_PATH)
            if db_dir and not os.path.exists(db_dir):
                try:
                    os.makedirs(db_dir)
                    logger.log_info(f"Created database directory: {db_dir}")
                except Exception as e:
                    raise OSError(f"Failed to create database directory {db_dir}: {e}")

            logger.log_info("Configuration validation successful")
            return True
        except Exception as e:
            logger.log_error(e, "Configuration validation failed")
            return False

class NFCReader:
    def __init__(self, config_obj: Config):
        self.config = config_obj
        self.clf = None
        self.connected = False
        self.mock_mode = not NFC_AVAILABLE

    def _get_nfc_reader_with_retry(self, max_attempts=None, retry_delay=2):
        """
        Attempt to connect to the NFC reader with multiple retries and different connection methods.
        """
        if self.mock_mode:
            print("Using mock NFC reader (NFC libraries not available)")
            return MockNFCReader("mock")
            
        if max_attempts is None:
            max_attempts = self.config.NFC_MAX_ATTEMPTS
            
        # Try different connection methods
        connection_methods = [
            'i2c:1',           # Auto-detect on I2C bus 1
            'i2c:1:0x24',      # Specific I2C address
            'i2c:0',           # Auto-detect on I2C bus 0
            'i2c:0:0x24',      # Specific I2C address on bus 0
            'usb',             # USB connection
            'tty:AMA0:pn532',  # UART connection
            'tty:S0:pn532'     # Another UART option
        ]
        
        for attempt in range(1, max_attempts + 1):
            # Try each connection method
            for method in connection_methods:
                try:
                    print(f"Attempting to connect to NFC reader using: {method}")
                    clf = nfc.ContactlessFrontend(method)
                    if clf:
                        print(f"Successfully connected to NFC reader using: {method}")
                        return clf
                except Exception as e:
                    logger.log_error(e, f"NFC init failed (attempt {attempt}). Retrying in {retry_delay}s...")
                    
            # Increase retry delay for subsequent attempts (exponential backoff)
            retry_delay = min(retry_delay * 2, 30)
            time.sleep(retry_delay)
            
        print("Failed to connect to NFC reader after multiple attempts. Using mock mode.")
        self.mock_mode = True
        return MockNFCReader("mock")

    def connect(self):
        """Connect to the NFC reader"""
        try:
            self.clf = self._get_nfc_reader_with_retry()
            self.connected = True
            return True
        except Exception as e:
            logger.log_error(e, "Failed to connect to NFC reader")
            self.mock_mode = True
            self.clf = MockNFCReader("mock")
            return False

    def read_card(self):
        """Read a card with the NFC reader"""
        if self.mock_mode:
            # In mock mode, simulate a card read
            print("Mock mode: Simulating card read")
            time.sleep(0.5)
            if time.time() % 10 < 5:  # Simulate card detection 50% of the time
                return {
                    'id': '04010203040506',
                    'type': 'MIFARE Classic 1K',
                    'data': 'Mock card data'
                }
            return None
            
        if not self.connected or not self.clf:
            print("NFC reader not connected. Attempting to reconnect...")
            if not self.connect():
                return None

        try:
            # Configure target with proper parameters
            target = RemoteTarget(self.config.NFC_PROTOCOL)
            
            # Sense for target with multiple iterations for reliability
            detected = self.clf.sense(
                target,
                iterations=3,
                interval=0.2
            )
            
            if not detected:
                return None
            
            # Activate and get tag
            tag = nfc.tag.activate(self.clf, detected)
            if tag:
                # Return tag info
                return {
                    'id': tag.identifier.hex(),
                    'type': str(tag),
                    'data': 'Card data would be read here'
                }
            
            return None
        except Exception as e:
            logger.log_error(e, "Error reading NFC card")
            # Check if we need to reconnect
            if "Error communicating with reader" in str(e):
                self.connected = False
                print("NFC reader communication error, will attempt reconnection")
            return None

    def close(self):
        """Close the NFC reader connection"""
        if self.clf and not self.mock_mode:
            try:
                self.clf.close()
                print("NFC reader connection closed")
            except Exception as e:
                logger.log_error(e, "Error closing NFC reader")
            finally:
                self.clf = None
                self.connected = False

class DatabaseManager:
    def __init__(self, config_obj: Config):
        self.config = config_obj
        self.conn = None
        self.connect()
        self._init_db()
        
        # Add some demo data for testing
        self._add_demo_data()

    def connect(self):
        """Connect to the database"""
        try:
            self.conn = sqlite3.connect(self.config.DB_PATH, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            return True
        except Exception as e:
            logger.log_error(e, "Failed to connect to database")
            return False

    def _init_db(self):
        """Initialize the database schema"""
        try:
            cursor = self.conn.cursor()
            
            # Create cards table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS cards (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    faculty TEXT,
                    program TEXT,
                    level TEXT,
                    student_id TEXT,
                    expiry_date TEXT,
                    photo_path TEXT,
                    is_admin INTEGER DEFAULT 0,
                    is_blacklisted INTEGER DEFAULT 0,
                    created_at TEXT,
                    last_access TEXT
                )
            ''')
            
            # Create access logs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS access_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_id TEXT,
                    timestamp TEXT,
                    status TEXT,
                    details TEXT,
                    FOREIGN KEY (card_id) REFERENCES cards(id)
                )
            ''')
            
            self.conn.commit()
            return True
        except Exception as e:
            logger.log_error(e, "Failed to initialize database")
            return False

    def _add_demo_data(self):
        """Add demo data for testing"""
        try:
            cursor = self.conn.cursor()
            
            # Check if we already have data
            cursor.execute("SELECT COUNT(*) FROM cards")
            count = cursor.fetchone()[0]
            
            if count == 0:
                # Add some demo cards
                demo_cards = [
                    ('04010203040506', 'John Smith', 'Engineering', 'Computer Science', '3rd Year', 'ENG123456', '2026-05-01', 'photos/john.jpg', 0, 0),
                    ('0708090a0b0c0d', 'Jane Doe', 'Medicine', 'Nursing', '2nd Year', 'MED789012', '2025-12-31', 'photos/jane.jpg', 0, 0),
                    ('0e0f101112131415', 'Admin User', 'Staff', 'IT Department', 'Staff', 'ADMIN001', '2030-01-01', 'photos/admin.jpg', 1, 0),
                    ('16171819202122', 'Blocked User', 'Business', 'Finance', '4th Year', 'BUS654321', '2025-06-30', 'photos/blocked.jpg', 0, 1)
                ]
                
                for card in demo_cards:
                    cursor.execute('''
                        INSERT OR REPLACE INTO cards 
                        (id, name, faculty, program, level, student_id, expiry_date, photo_path, is_admin, is_blacklisted, created_at, last_access)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), NULL)
                    ''', card)
                
                self.conn.commit()
                print("Added demo data to database")
            
            return True
        except Exception as e:
            logger.log_error(e, "Failed to add demo data")
            return False

    def get_card_info(self, card_id: str) -> Optional[CardInfo]:
        """Get information about a card"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, name, expiry_date, is_blacklisted, last_access
                FROM cards
                WHERE id = ?
            ''', (card_id,))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            # Convert to CardInfo object
            expiry_date = None
            if row['expiry_date']:
                try:
                    expiry_date = datetime.strptime(row['expiry_date'], '%Y-%m-%d')
                except ValueError:
                    pass
            
            last_access = None
            if row['last_access']:
                try:
                    last_access = datetime.strptime(row['last_access'], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    pass
            
            is_valid = not row['is_blacklisted']
            if expiry_date:
                is_valid = is_valid and expiry_date > datetime.now()
            
            return CardInfo(
                id=row['id'],
                name=row['name'],
                expiry_date=expiry_date,
                is_valid=is_valid,
                last_access=last_access
            )
        except Exception as e:
            logger.log_error(e, f"Failed to get card info for {card_id}")
            return None

    def get_full_card_details(self, card_id: str) -> Optional[Dict[str, Any]]:
        """Get full details about a card for display"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT *
                FROM cards
                WHERE id = ?
            ''', (card_id,))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            # Convert to dictionary
            return dict(row)
        except Exception as e:
            logger.log_error(e, f"Failed to get full card details for {card_id}")
            return None

    def update_last_access(self, card_id: str) -> bool:
        """Update the last access time for a card"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                UPDATE cards
                SET last_access = datetime('now')
                WHERE id = ?
            ''', (card_id,))
            
            self.conn.commit()
            return True
        except Exception as e:
            logger.log_error(e, f"Failed to update last access for {card_id}")
            return False

    def log_access(self, card_id: str, status: AccessStatus, details: str = "") -> bool:
        """Log an access attempt"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO access_logs (card_id, timestamp, status, details)
                VALUES (?, datetime('now'), ?, ?)
            ''', (card_id, status.name, details))
            
            self.conn.commit()
            return True
        except Exception as e:
            logger.log_error(e, f"Failed to log access for {card_id}")
            return False

    def close(self):
        """Close the database connection"""
        if self.conn:
            try:
                self.conn.close()
                self.conn = None
            except Exception as e:
                logger.log_error(e, "Error closing database connection")

class HardwareController:
    def __init__(self, config_obj: Config):
        self.config = config_obj
        self.setup_gpio()
        self.servo = None
        self.setup_servo()

    def setup_gpio(self):
        """Set up GPIO pins"""
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.config.SERVO_PIN, GPIO.OUT)
            GPIO.setup(self.config.FAN_PIN, GPIO.OUT)
            GPIO.setup(self.config.BUZZER_PIN, GPIO.OUT)
            GPIO.setup(self.config.GREEN_LED_PIN, GPIO.OUT)
            GPIO.setup(self.config.RED_LED_PIN, GPIO.OUT)
            
            # Initialize all outputs to LOW
            GPIO.output(self.config.FAN_PIN, GPIO.LOW)
            GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
            GPIO.output(self.config.GREEN_LED_PIN, GPIO.LOW)
            GPIO.output(self.config.RED_LED_PIN, GPIO.LOW)
            
            return True
        except Exception as e:
            logger.log_error(e, "Failed to set up GPIO")
            return False

    def setup_servo(self):
        """Set up servo motor"""
        try:
            self.servo = GPIO.PWM(self.config.SERVO_PIN, 50)  # 50Hz PWM
            self.servo.start(0)
            return True
        except Exception as e:
            logger.log_error(e, "Failed to set up servo")
            self.servo = None
            return False

    def open_gate(self):
        """Open the gate"""
        if not self.servo:
            print("Servo not initialized")
            return False
            
        try:
            self.servo.ChangeDutyCycle(self.config.SERVO_OPEN_DUTY)
            time.sleep(self.config.SERVO_DELAY)
            self.servo.ChangeDutyCycle(0)  # Stop PWM to prevent jitter
            return True
        except Exception as e:
            logger.log_error(e, "Failed to open gate")
            return False

    def close_gate(self):
        """Close the gate"""
        if not self.servo:
            print("Servo not initialized")
            return False
            
        try:
            self.servo.ChangeDutyCycle(self.config.SERVO_CLOSE_DUTY)
            time.sleep(self.config.SERVO_DELAY)
            self.servo.ChangeDutyCycle(0)  # Stop PWM to prevent jitter
            return True
        except Exception as e:
            logger.log_error(e, "Failed to close gate")
            return False

    def set_led(self, led: str, state: bool):
        """Set LED state"""
        try:
            pin = None
            if led.lower() == 'green':
                pin = self.config.GREEN_LED_PIN
            elif led.lower() == 'red':
                pin = self.config.RED_LED_PIN
            else:
                raise ValueError(f"Unknown LED: {led}")
                
            GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)
            return True
        except Exception as e:
            logger.log_error(e, f"Failed to set {led} LED to {state}")
            return False

    def beep(self, pattern: str = 'single'):
        """Beep the buzzer with a pattern"""
        try:
            if pattern == 'single':
                GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
                time.sleep(0.2)
                GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
            elif pattern == 'double':
                for _ in range(2):
                    GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
                    time.sleep(0.1)
                    GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
                    time.sleep(0.1)
            elif pattern == 'error':
                for _ in range(3):
                    GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
                    time.sleep(0.1)
                    GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
                    time.sleep(0.1)
            return True
        except Exception as e:
            logger.log_error(e, f"Failed to beep with pattern {pattern}")
            return False

    def set_fan(self, state: bool):
        """Set fan state"""
        try:
            GPIO.output(self.config.FAN_PIN, GPIO.HIGH if state else GPIO.LOW)
            return True
        except Exception as e:
            logger.log_error(e, f"Failed to set fan to {state}")
            return False

    def cleanup(self):
        """Clean up GPIO resources"""
        try:
            if self.servo:
                self.servo.stop()
            GPIO.cleanup()
            return True
        except Exception as e:
            logger.log_error(e, "Failed to clean up GPIO")
            return False

class AccessController:
    def __init__(self, config_obj: Config, db_manager: DatabaseManager, hardware: HardwareController, nfc_reader: NFCReader):
        self.config = config_obj
        self.db = db_manager
        self.hardware = hardware
        self.nfc = nfc_reader
        self.running = False
        self.stop_event = threading.Event()

    def process_card(self, card_data: Dict[str, Any]) -> Tuple[CardInfo, AccessStatus]:
        """Process a card read and determine access status"""
        card_id = card_data['id']
        start_time = time.time()
        
        # Get card info from database
        card_info = self.db.get_card_info(card_id)
        if not card_info:
            # Card not found in database
            card_info = CardInfo(id=card_id)
            status = AccessStatus.DENIED
        elif not card_info.is_valid:
            # Card is invalid (blacklisted or expired)
            status = AccessStatus.BLACKLISTED
        else:
            # Card is valid
            status = AccessStatus.GRANTED
            self.db.update_last_access(card_id)
        
        # Log the access attempt
        self.db.log_access(card_id, status)
        
        # Calculate response time
        response_time = time.time() - start_time
        
        # Log to system logger
        logger.log_access(card_info, status, response_time)
        
        return card_info, status

    def handle_access_result(self, card_info: CardInfo, status: AccessStatus) -> None:
        """Handle the result of an access attempt"""
        if status == AccessStatus.GRANTED:
            # Access granted
            self.hardware.set_led('green', True)
            self.hardware.beep('single')
            self.hardware.open_gate()
            
            # Turn off green LED and close gate after delay
            threading.Timer(3.0, lambda: self.hardware.set_led('green', False)).start()
            threading.Timer(5.0, lambda: self.hardware.close_gate()).start()
        elif status == AccessStatus.BLACKLISTED:
            # Access denied (blacklisted)
            self.hardware.set_led('red', True)
            self.hardware.beep('error')
            
            # Turn off red LED after delay
            threading.Timer(3.0, lambda: self.hardware.set_led('red', False)).start()
        else:
            # Access denied (not found)
            self.hardware.set_led('red', True)
            self.hardware.beep('double')
            
            # Turn off red LED after delay
            threading.Timer(3.0, lambda: self.hardware.set_led('red', False)).start()

    def start(self):
        """Start the access controller"""
        if self.running:
            return
            
        self.running = True
        self.stop_event.clear()
        
        # Connect to NFC reader
        if not self.nfc.connected:
            self.nfc.connect()
        
        # Start the main loop in a separate thread
        threading.Thread(target=self._main_loop, daemon=True).start()

    def stop(self):
        """Stop the access controller"""
        if not self.running:
            return
            
        self.running = False
        self.stop_event.set()

    def _main_loop(self):
        """Main loop for the access controller"""
        while self.running and not self.stop_event.is_set():
            try:
                # Read card
                card_data = self.nfc.read_card()
                
                if card_data:
                    # Process card
                    card_info, status = self.process_card(card_data)
                    
                    # Handle result
                    self.handle_access_result(card_info, status)
                
                # Small delay to prevent CPU hogging
                time.sleep(0.1)
            except Exception as e:
                logger.log_error(e, "Error in access controller main loop")
                time.sleep(1)  # Longer delay after error

class SmallScreenGUI:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.root = None
        self.current_card_id = None
        self.current_display_timer = None

    def initialize(self):
        """Initialize the GUI"""
        try:
            print("Initializing small screen GUI...")
            self.root = Tk()
            self.root.title("Smart Gate Access Control")
            
            # Set window size and position
            self.root.geometry("800x480")  # Common 7" touchscreen resolution
            
            # Make it fullscreen if needed
            # self.root.attributes('-fullscreen', True)
            
            # Create main frame
            self.main_frame = ttk.Frame(self.root, padding=20)
            self.main_frame.pack(fill=tk.BOTH, expand=True)
            
            # Create header
            self.header_label = ttk.Label(
                self.main_frame, 
                text="University Smart Gate System", 
                font=("Arial", 24, "bold")
            )
            self.header_label.pack(pady=(0, 20))
            
            # Create status frame
            self.status_frame = ttk.Frame(self.main_frame)
            self.status_frame.pack(fill=tk.X, pady=10)
            
            self.status_label = ttk.Label(
                self.status_frame,
                text="Please scan your card",
                font=("Arial", 18)
            )
            self.status_label.pack()
            
            # Create card info frame
            self.card_frame = ttk.Frame(self.main_frame, padding=10)
            self.card_frame.pack(fill=tk.BOTH, expand=True, pady=10)
            
            # Photo placeholder
            self.photo_frame = ttk.Frame(self.card_frame, width=150, height=200, relief=tk.RAISED, borderwidth=2)
            self.photo_frame.pack(side=tk.LEFT, padx=20, pady=10)
            self.photo_frame.pack_propagate(False)
            
            self.photo_label = ttk.Label(self.photo_frame, text="No Photo")
            self.photo_label.pack(expand=True)
            
            # Student info
            self.info_frame = ttk.Frame(self.card_frame)
            self.info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=20)
            
            # Create labels for student info
            self.name_label = self._create_info_label("Name:", "")
            self.faculty_label = self._create_info_label("Faculty:", "")
            self.program_label = self._create_info_label("Program:", "")
            self.level_label = self._create_info_label("Level:", "")
            self.id_label = self._create_info_label("Student ID:", "")
            
            # Create footer with time
            self.footer_frame = ttk.Frame(self.main_frame)
            self.footer_frame.pack(fill=tk.X, pady=10)
            
            self.time_label = ttk.Label(
                self.footer_frame,
                text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                font=("Arial", 12)
            )
            self.time_label.pack(side=tk.RIGHT)
            
            # Update time every second
            self._update_time()
            
            # Reset display to show welcome screen
            self._reset_display()
            
            print("Small screen GUI initialized successfully")
            return True
        except Exception as e:
            logger.log_error(e, "Failed to initialize small screen GUI")
            return False

    def _create_info_label(self, label_text, value_text):
        """Create a label pair for student info"""
        frame = ttk.Frame(self.info_frame)
        frame.pack(fill=tk.X, pady=5)
        
        label = ttk.Label(frame, text=label_text, width=15, font=("Arial", 14, "bold"))
        label.pack(side=tk.LEFT)
        
        value = ttk.Label(frame, text=value_text, font=("Arial", 14))
        value.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        return value

    def _update_time(self):
        """Update the time display"""
        if self.root:
            self.time_label.config(text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self.root.after(1000, self._update_time)

    def display_card_info(self, card_id):
        """Display card information"""
        try:
            # Cancel any existing timer
            if self.current_display_timer:
                self.root.after_cancel(self.current_display_timer)
                self.current_display_timer = None
            
            # Get card details
            card_details = self.db.get_full_card_details(card_id)
            if not card_details:
                self._show_access_denied("Card not recognized")
                return
            
            # Check if card is valid
            is_blacklisted = card_details['is_blacklisted'] == 1
            is_expired = False
            if card_details['expiry_date']:
                try:
                    expiry_date = datetime.strptime(card_details['expiry_date'], '%Y-%m-%d')
                    is_expired = expiry_date < datetime.now()
                except ValueError:
                    pass
            
            if is_blacklisted:
                self._show_access_denied("Card is blacklisted")
                return
                
            if is_expired:
                self._show_access_denied("Card is expired")
                return
            
            # Show access granted
            self.status_label.config(text="Access Granted", foreground="green")
            
            # Update student info
            self.name_label.config(text=card_details['name'] or "")
            self.faculty_label.config(text=card_details['faculty'] or "")
            self.program_label.config(text=card_details['program'] or "")
            self.level_label.config(text=card_details['level'] or "")
            self.id_label.config(text=card_details['student_id'] or "")
            
            # TODO: Load photo if available
            self.photo_label.config(text="Photo would be shown here")
            
            # Set timer to reset display after 10 seconds
            self.current_display_timer = self.root.after(10000, self._reset_display)
            
            # Store current card ID
            self.current_card_id = card_id
        except Exception as e:
            logger.log_error(e, f"Failed to display card info for {card_id}")
            self._show_access_denied("System error")

    def _show_access_denied(self, reason):
        """Show access denied message"""
        self.status_label.config(text=f"Access Denied: {reason}", foreground="red")
        
        # Clear student info
        self.name_label.config(text="")
        self.faculty_label.config(text="")
        self.program_label.config(text="")
        self.level_label.config(text="")
        self.id_label.config(text="")
        
        # Clear photo
        self.photo_label.config(text="No Photo")
        
        # Set timer to reset display after 5 seconds
        self.current_display_timer = self.root.after(5000, self._reset_display)

    def _reset_display(self):
        """Reset display to welcome screen"""
        self.status_label.config(text="Please scan your card", foreground="black")
        
        # Clear student info
        self.name_label.config(text="")
        self.faculty_label.config(text="")
        self.program_label.config(text="")
        self.level_label.config(text="")
        self.id_label.config(text="")
        
        # Clear photo
        self.photo_label.config(text="No Photo")
        
        # Clear current card ID
        self.current_card_id = None
        
        # Clear timer
        self.current_display_timer = None

    def update(self):
        """Update the GUI"""
        if self.root:
            try:
                self.root.update()
                return True
            except Exception as e:
                logger.log_error(e, "Failed to update GUI")
                return False
        return False

    def close(self):
        """Close the GUI"""
        if self.root:
            try:
                self.root.destroy()
                self.root = None
                return True
            except Exception as e:
                logger.log_error(e, "Failed to close GUI")
                return False
        return True

class MainApplication:
    def __init__(self):
        self.config = config
        ConfigValidator.validate_config(self.config)
        
        self.db = DatabaseManager(self.config)
        self.nfc = NFCReader(self.config)
        self.hardware = HardwareController(self.config)
        self.access_controller = AccessController(self.config, self.db, self.hardware, self.nfc)
        self.small_screen = SmallScreenGUI(self.db)
        
        self.running = False
        self.stop_event = threading.Event()

    def start(self):
        """Start the application"""
        print("Starting application...")
        self.running = True
        self.stop_event.clear()
        
        # Initialize components
        self.nfc.connect()
        self.small_screen.initialize()
        self.access_controller.start()
        
        # Start the main loop in a separate thread
        threading.Thread(target=self._main_loop, daemon=True).start()

    def stop(self):
        """Stop the application"""
        print("Stopping application...")
        self.running = False
        self.stop_event.set()
        
        # Stop components
        self.access_controller.stop()
        self.small_screen.close()
        self.nfc.close()
        self.hardware.cleanup()
        self.db.close()

    def _main_loop(self):
        """Main application loop"""
        while self.running and not self.stop_event.is_set():
            try:
                # Update GUI
                self.small_screen.update()
                
                # Read card directly for GUI display
                card_data = self.nfc.read_card()
                if card_data and self.small_screen.current_card_id != card_data['id']:
                    # Display card info in GUI
                    self.small_screen.display_card_info(card_data['id'])
                
                # Small delay to prevent CPU hogging
                time.sleep(0.1)
            except Exception as e:
                logger.log_error(e, "Error in main application loop")
                time.sleep(1)  # Longer delay after error

    def run_demo(self):
        """Run a demo with simulated card reads"""
        print("Bypassing authentication for GUI demonstration...")
        
        # Start the application
        self.start()
        
        # Simulate card reads every 15 seconds
        demo_cards = ['04010203040506', '0708090a0b0c0d', '0e0f101112131415', '16171819202122']
        card_index = 0
        
        try:
            while self.running and not self.stop_event.is_set():
                # Wait for 15 seconds
                for _ in range(150):  # 15 seconds with 0.1s checks
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.1)
                
                if self.stop_event.is_set():
                    break
                
                # Simulate card read
                card_id = demo_cards[card_index]
                print(f"Demo: Simulating card read for ID {card_id}")
                self.small_screen.display_card_info(card_id)
                
                # Move to next card
                card_index = (card_index + 1) % len(demo_cards)
        except KeyboardInterrupt:
            print("Demo interrupted by user")
        finally:
            self.stop()

def main():
    try:
        # Create and start the application
        app = MainApplication()
        
        # Run in demo mode
        app.run_demo()
    except KeyboardInterrupt:
        print("Application interrupted by user")
    except Exception as e:
        logger.log_error(e, "Fatal error in main function")
        print(f"Fatal error: {e}")
    finally:
        print("Application shutdown complete")

if __name__ == "__main__":
    main()
