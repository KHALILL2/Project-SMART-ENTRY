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
            print(f"MockGPIO: Cleanup pin {'all' if pin is None else pin}")
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
        self.SOLENOID_PIN = self._validate_pin(self.config.getint('gpio', 'solenoid', fallback=27))  # GPIO 27 as per original code
        self.LED_GREEN_PIN = self._validate_pin(self.config.getint('gpio', 'led_green', fallback=22))
        self.LED_RED_PIN = self._validate_pin(self.config.getint('gpio', 'led_red', fallback=23))
        self.SERVO_OPEN_DUTY = 12.5  # Maximize to 180 degrees
        self.SERVO_CLOSE_DUTY = 2.5  # Minimize to 0 degrees
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
            'solenoid': '27',  # GPIO 27 as per original code
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
        """Start the admin interface without authentication"""
        self.admin_gui = AdminGUI(self.db, self.hardware, self.access_controller, self.nfc_reader)
        self.admin_gui.run()
        return True

def main():
    system = SmartEntrySystem()
    print("Starting application...")
    
    system_thread = threading.Thread(target=system.start)
    system_thread.daemon = True
    system_thread.start()
    
    system.start_admin_interface()
    
    try:
        while system_thread.is_alive():
            time.sleep(0.1)
    except KeyboardInterrupt:
        system.stop()

if __name__ == "__main__":
    main()
