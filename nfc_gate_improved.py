import nfc
from nfc.clf import RemoteTarget
import threading
import queue
import time
import sqlite3
import RPi.GPIO as GPIO
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta
import schedule
from cryptography.fernet import Fernet
import keyring
from flask import Flask, jsonify
import os
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional
import logging
from logging.handlers import RotatingFileHandler

# Logger setup
class ProfessionalLogger:
    def __init__(self, log_file="nfc_gate.log", max_bytes=1048576, backup_count=5):
        self.logger = logging.getLogger("NFCGate")
        self.logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.queue = queue.Queue()
        self._start_flusher()

    def _start_flusher(self):
        def flush():
            while True:
                try:
                    level, msg, context = self.queue.get()
                    if level == "INFO":
                        self.logger.info(f"{msg} | Context: {context}")
                    elif level == "ERROR":
                        self.logger.error(f"{msg} | Context: {context}")
                except Exception as e:
                    self.logger.error(f"Flusher error: {e}")
                self.queue.task_done()
        threading.Thread(target=flush, daemon=True).start()

    def log_info(self, message: str, context: str = ""):
        self.queue.put(("INFO", message, context))

    def log_error(self, error, context: str = ""):
        self.queue.put(("ERROR", str(error), context))

logger = ProfessionalLogger()

# Enums and dataclasses
class AccessStatus(Enum):
    GRANTED = auto()
    DENIED = auto()

@dataclass
class CardInfo:
    id: str
    is_valid: bool = False

@dataclass
class SystemMetrics:
    total_requests: int = 0
    successful_accesses: int = 0
    failed_accesses: int = 0
    average_response_time: float = 0.0
    system_uptime: float = time.time()
    last_health_check: Optional[datetime] = None

# Configuration
class Config:
    def __init__(self):
        self.DB_PATH = "smart_gate.db"
        self.SERVO_PIN = 18
        self.BUZZER_PIN = 25
        self.FAN_PIN = 23
        self.FAN_ON_TEMP = 60  # Celsius
        self.FAN_OFF_TEMP = 50  # Celsius
        self.THERMAL_FILE = "/sys/class/thermal/thermal_zone0/temp"
        self.EMAIL_USER = keyring.get_password("nfc_gate", "email_user")
        self.EMAIL_PASS = keyring.get_password("nfc_gate", "email_pass")
        self.EMAIL_HOST = "smtp.gmail.com"
        self.EMAIL_PORT = 587
        self.NFC_PROTOCOL = "106A"  # Adjust based on NFC reader

# Database Manager with Encryption and Pruning
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute('PRAGMA journal_mode=WAL;')
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS cards
                     (id TEXT, data TEXT, timestamp DATETIME)''')
        c.execute('''CREATE TABLE IF NOT EXISTS valid_cards
                     (id TEXT PRIMARY KEY)''')
        self.conn.commit()
        # Encryption setup
        key = keyring.get_password("nfc_gate", "db_key")
        if not key:
            key = Fernet.generate_key().decode()
            keyring.set_password("nfc_gate", "db_key", key)
        self.fernet = Fernet(key.encode())

    def log_card(self, card_id: str, data: str):
        encrypted_id = self.fernet.encrypt(card_id.encode()).decode()
        try:
            c = self.conn.cursor()
            c.execute("INSERT INTO cards (id, data, timestamp) VALUES (?, ?, ?)",
                      (encrypted_id, data, datetime.now()))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.log_error(e, "Error logging card")

    def is_valid_card(self, card_id: str) -> bool:
        encrypted_id = self.fernet.encrypt(card_id.encode()).decode()
        try:
            c = self.conn.cursor()
            c.execute("SELECT * FROM valid_cards WHERE id = ?", (encrypted_id,))
            return c.fetchone() is not None
        except sqlite3.Error as e:
            logger.log_error(e, "Error verifying card")
            return False

    def prune_old_logs(self, days=30):
        try:
            cutoff = datetime.now() - timedelta(days=days)
            c = self.conn.cursor()
            c.execute("DELETE FROM cards WHERE timestamp < ?", (cutoff,))
            self.conn.commit()
            logger.log_info(f"Pruned logs older than {days} days")
        except sqlite3.Error as e:
            logger.log_error(e, "Error pruning logs")

    def close(self):
        self.conn.close()

# Hardware Controller for GPIO Management
class HardwareController:
    def __init__(self, config: Config):
        self.config = config
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.config.SERVO_PIN, GPIO.OUT)
        GPIO.setup(self.config.BUZZER_PIN, GPIO.OUT)
        GPIO.setup(self.config.FAN_PIN, GPIO.OUT)
        self.servo_pwm = GPIO.PWM(self.config.SERVO_PIN, 50)
        self.servo_pwm.start(0)
        self.fan_state = False
        self._start_temp_monitor()

    def _start_temp_monitor(self):
        def monitor_temp():
            while True:
                temp = self._read_temperature()
                if temp > self.config.FAN_ON_TEMP and not self.fan_state:
                    GPIO.output(self.config.FAN_PIN, GPIO.HIGH)
                    self.fan_state = True
                    logger.log_info("Fan turned ON")
                elif temp < self.config.FAN_OFF_TEMP and self.fan_state:
                    GPIO.output(self.config.FAN_PIN, GPIO.LOW)
                    self.fan_state = False
                    logger.log_info("Fan turned OFF")
                time.sleep(30)
        threading.Thread(target=monitor_temp, daemon=True).start()

    def _read_temperature(self) -> float:
        try:
            with open(self.config.THERMAL_FILE, 'r') as f:
                temp = int(f.read()) / 1000.0
            return temp
        except Exception as e:
            logger.log_error(e, "Error reading temperature")
            return 0.0

    def open_gate(self):
        self.servo_pwm.ChangeDutyCycle(7.5)  # Adjust for your servo
        time.sleep(1.5)
        self.servo_pwm.ChangeDutyCycle(0)

    def close_gate(self):
        self.servo_pwm.ChangeDutyCycle(2.5)  # Adjust for your servo
        time.sleep(1.5)
        self.servo_pwm.ChangeDutyCycle(0)

    def beep_granted(self):
        GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
        time.sleep(0.1)
        GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)

    def beep_denied(self):
        for _ in range(2):
            GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
            time.sleep(0.1)
            GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
            time.sleep(0.1)

    def cleanup(self):
        self.servo_pwm.stop()
        if self.fan_state:
            GPIO.output(self.config.FAN_PIN, GPIO.LOW)
        GPIO.cleanup()

# GUI with Tkinter
class NFCGui:
    def __init__(self, card_queue: queue.Queue, stop_callback):
        self.root = tk.Tk()
        self.root.title("University Smart Gate")
        self.card_queue = card_queue
        self.stop_callback = stop_callback
        self.card_label = ttk.Label(self.root, text="Card ID: -")
        self.card_label.pack(pady=10)
        self.data_label = ttk.Label(self.root, text="Status: -")
        self.data_label.pack(pady=10)
        ttk.Button(self.root, text="Exit", command=self.shutdown).pack(pady=10)
        self._schedule_updates()

    def _schedule_updates(self):
        try:
            while not self.card_queue.empty():
                card_id, status = self.card_queue.get_nowait()
                self.card_label.config(text=f"Card ID: {card_id}")
                self.data_label.config(text=f"Status: {status}")
        except queue.Empty:
            pass
        self.root.after(100, self._schedule_updates)

    def shutdown(self):
        self.stop_callback()
        self.root.quit()

# Main NFC System
class NFCSystem:
    def __init__(self, config: Config):
        self.config = config
        self.db = DatabaseManager(config.DB_PATH)
        self.hardware = HardwareController(config)
        self.card_queue = queue.Queue(maxsize=50)
        self.running = False
        self.nfc_thread = None
        self.metrics = SystemMetrics()
        self.gui = NFCGui(self.card_queue, self.stop)
        # Start pruning scheduler
        threading.Thread(target=self._start_pruning, daemon=True).start()
        # Start Flask server
        threading.Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': 5000}, daemon=True).start()

    def start(self):
        self.running = True
        self.nfc_thread = threading.Thread(target=self.scan_nfc)
        self.nfc_thread.start()
        self.gui.root.mainloop()

    def stop(self):
        self.running = False
        if self.nfc_thread:
            self.nfc_thread.join(timeout=5)
        self.db.close()
        self.hardware.cleanup()

    def scan_nfc(self):
        clf = nfc.ContactlessFrontend('usb')  # Adjust for your NFC reader
        detection_times = []
        polling_interval = 1.0  # Default 1 second
        try:
            while self.running:
                start_time = time.time()
                target = clf.sense(RemoteTarget(self.config.NFC_PROTOCOL), iterations=1, interval=0.1)
                if target:
                    tag = nfc.tag.activate(clf, target)
                    if tag:
                        card_id = tag.identifier.hex()
                        card_info = CardInfo(id=card_id)
                        card_info.is_valid = self.db.is_valid_card(card_id)
                        status = AccessStatus.GRANTED if card_info.is_valid else AccessStatus.DENIED
                        data = "Access Granted" if card_info.is_valid else "Access Denied"
                        if card_info.is_valid:
                            self.hardware.open_gate()
                            self.hardware.beep_granted()
                            time.sleep(3)
                            self.hardware.close_gate()
                        else:
                            self.hardware.beep_denied()
                        self.db.log_card(card_id, data)
                        self._send_email_async("Card Access", f"Card: {card_id}\nStatus: {data}")
                        try:
                            self.card_queue.put_nowait((card_id, data))
                        except queue.Full:
                            logger.log_error(queue.Full(), "Card queue full")
                        # Update metrics
                        self.metrics.total_requests += 1
                        if status == AccessStatus.GRANTED:
                            self.metrics.successful_accesses += 1
                        else:
                            self.metrics.failed_accesses += 1
                        # Dynamic polling adjustment
                        current_time = time.time()
                        detection_times.append(current_time)
                        detection_times = [t for t in detection_times if t > current_time - 600]  # Last 10 minutes
                        detection_count = len(detection_times)
                        if detection_count > 10:
                            polling_interval = 0.5  # High activity
                        elif detection_count > 5:
                            polling_interval = 1.0  # Medium activity
                        else:
                            polling_interval = 2.0  # Low activity
                elapsed = time.time() - start_time
                if elapsed < polling_interval:
                    time.sleep(polling_interval - elapsed)
        except Exception as e:
            logger.log_error(e, "NFC scanning error")
        finally:
            if clf:
                clf.close()

    def _start_pruning(self):
        schedule.every().day.at("00:00").do(self.db.prune_old_logs)
        while True:
            schedule.run_pending()
            time.sleep(1)

    def _send_email_async(self, subject: str, body: str):
        threading.Thread(target=send_email, args=(subject, body), daemon=True).start()

# Flask App for Monitoring
app = Flask(__name__)

@app.route('/metrics')
def get_metrics():
    metrics.last_health_check = datetime.now()
    return jsonify({
        'total_requests': metrics.total_requests,
        'successful_accesses': metrics.successful_accesses,
        'failed_accesses': metrics.failed_accesses,
        'average_response_time': metrics.average_response_time,
        'system_uptime': time.time() - metrics.system_uptime,
        'last_health_check': metrics.last_health_check.isoformat()
    })

@app.route('/health')
def health():
    return "OK", 200

# Placeholder Email Function
def send_email(subject: str, body: str):
    # Replace with actual email sending logic (e.g., using smtplib)
    print(f"Email - Subject: {subject}, Body: {body}")

# Utility to Add Valid Cards
def add_valid_card(db: DatabaseManager, card_id: str):
    encrypted_id = db.fernet.encrypt(card_id.encode()).decode()
    try:
        c = db.conn.cursor()
        c.execute("INSERT INTO valid_cards (id) VALUES (?)", (encrypted_id,))
        db.conn.commit()
        logger.log_info(f"Added valid card: {card_id}")
    except sqlite3.Error as e:
        logger.log_error(e, "Error adding valid card")

# Main Entry Point
if __name__ == "__main__":
    config = Config()
    system = NFCSystem(config)
    # Example: Add a valid card (replace with actual card ID)
    # add_valid_card(system.db, "replace_with_actual_card_id_hex")
    try:
        system.start()
    except KeyboardInterrupt:
        system.stop()