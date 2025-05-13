import nfc
from nfc.clf import RemoteTarget
import threading
import queue
import time
import sqlite3
import RPi.GPIO as GPIO
import tkinter as tk
from tkinter import ttk
from datetime import datetime

# Logger setup (simplified for brevity)
class ProfessionalLogger:
    def log_info(self, message):
        print(f"INFO: {message}")
    def log_error(self, error, context):
        print(f"ERROR: {error} | Context: {context}")

logger = ProfessionalLogger()

# Configuration
class Config:
    DB_PATH = "smart_gate.db"
    SERVO_PIN = 18
    FAN_PIN = 23
    BUZZER_PIN = 24
    GREEN_LED_PIN = 25
    RED_LED_PIN = 26
    VALID_PINS = [2, 3, 4, 17, 18, 22, 23, 24, 25, 26, 27]
    NFC_PROTOCOL = "106A"  # Adjust based on NFC reader

# Database Manager
class DatabaseManager:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute('PRAGMA journal_mode=WAL;')
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS cards
                     (id TEXT PRIMARY KEY, data TEXT, last_access DATETIME)''')
        c.execute('''CREATE TABLE IF NOT EXISTS valid_cards
                     (id TEXT PRIMARY KEY, name TEXT, expiry_date DATETIME)''')
        self.conn.commit()

    def log_card(self, card_id: str, data: str):
        try:
            c = self.conn.cursor()
            c.execute("INSERT OR REPLACE INTO cards (id, data, last_access) VALUES (?, ?, ?)",
                      (card_id, data, datetime.now()))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.log_error(e, "Error logging card")

    def is_valid_card(self, card_id: str) -> bool:
        try:
            c = self.conn.cursor()
            c.execute("SELECT expiry_date FROM valid_cards WHERE id = ?", (card_id,))
            result = c.fetchone()
            if result:
                expiry_date = result[0]
                if expiry_date and datetime.fromisoformat(expiry_date) < datetime.now():
                    return False
                return True
            return False
        except sqlite3.Error as e:
            logger.log_error(e, "Error verifying card")
            return False

    def close(self):
        self.conn.close()

# Hardware Controller
class HardwareController:
    def __init__(self, config):
        self.config = config
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.config.SERVO_PIN, GPIO.OUT)
        GPIO.setup(self.config.FAN_PIN, GPIO.OUT)
        GPIO.setup(self.config.BUZZER_PIN, GPIO.OUT)
        GPIO.setup(self.config.GREEN_LED_PIN, GPIO.OUT)
        GPIO.setup(self.config.RED_LED_PIN, GPIO.OUT)
        self.servo_pwm = GPIO.PWM(self.config.SERVO_PIN, 50)
        self.servo_pwm.start(0)

    def open_gate(self):
        self.servo_pwm.ChangeDutyCycle(7.5)  # Open position
        time.sleep(1.5)
        self.servo_pwm.ChangeDutyCycle(0)

    def close_gate(self):
        self.servo_pwm.ChangeDutyCycle(2.5)  # Close position
        time.sleep(1.5)
        self.servo_pwm.ChangeDutyCycle(0)

    def turn_on_green_led(self):
        GPIO.output(self.config.GREEN_LED_PIN, GPIO.HIGH)

    def turn_off_green_led(self):
        GPIO.output(self.config.GREEN_LED_PIN, GPIO.LOW)

    def turn_on_red_led(self):
        GPIO.output(self.config.RED_LED_PIN, GPIO.HIGH)

    def turn_off_red_led(self):
        GPIO.output(self.config.RED_LED_PIN, GPIO.LOW)

    def beep_good(self):
        """Single beep for valid login"""
        GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
        time.sleep(0.1)
        GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)

    def beep_warning(self):
        """Double beep for invalid login"""
        for _ in range(2):
            GPIO.output(self.config.BUZZER_PIN, GPIO.HIGH)
            time.sleep(0.1)
            GPIO.output(self.config.BUZZER_PIN, GPIO.LOW)
            time.sleep(0.1)

    def cleanup(self):
        self.servo_pwm.stop()
        GPIO.cleanup()

# GUI
class NFCGui:
    def __init__(self, card_queue):
        self.root = tk.Tk()
        self.root.title("Smart Gate System")
        self.card_queue = card_queue
        self.card_label = ttk.Label(self.root, text="Card ID: -")
        self.card_label.pack(pady=10)
        self.data_label = ttk.Label(self.root, text="Status: -")
        self.data_label.pack(pady=10)
        ttk.Button(self.root, text="Exit", command=self.root.quit).pack(pady=10)
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

# Main System
class NFCSystem:
    def __init__(self):
        self.config = Config()
        self.db = DatabaseManager(self.config.DB_PATH)
        self.hardware = HardwareController(self.config)
        self.card_queue = queue.Queue(maxsize=50)
        self.running = False
        self.nfc_thread = None
        self.gui = NFCGui(self.card_queue)

    def start(self):
        self.running = True
        self.nfc_thread = threading.Thread(target=self.scan_nfc, daemon=True)
        self.nfc_thread.start()
        self.gui.root.mainloop()

    def stop(self):
        self.running = False
        if self.nfc_thread:
            self.nfc_thread.join(timeout=5)
        self.db.close()
        self.hardware.cleanup()

    def scan_nfc(self):
        clf = nfc.ContactlessFrontend('usb')
        try:
            while self.running:
                tag = clf.connect(rdwr={'on-connect': lambda tag: False})
                if tag:
                    card_id = tag.identifier.hex()
                    if self.db.is_valid_card(card_id):
                        self.hardware.turn_on_green_led()
                        self.hardware.beep_good()
                        self.hardware.open_gate()
                        time.sleep(3)
                        self.hardware.close_gate()
                        self.hardware.turn_off_green_led()
                        status = "Access Granted"
                    else:
                        self.hardware.turn_on_red_led()
                        self.hardware.beep_warning()
                        time.sleep(1)
                        self.hardware.turn_off_red_led()
                        status = "Access Denied"
                    self.db.log_card(card_id, status)
                    self.card_queue.put((card_id, status))
        except Exception as e:
            logger.log_error(e, "NFC scanning error")
        finally:
            clf.close()

if __name__ == "__main__":
    system = NFCSystem()
    try:
        system.start()
    except KeyboardInterrupt:
        system.stop()