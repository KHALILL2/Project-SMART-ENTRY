#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import random
import sqlite3
import datetime
import threading
import psutil
import logging
import RPi.GPIO as GPIO
from adafruit_pn532.i2c import PN532_I2C
import busio
import board
from adafruit_servokit import ServoKit
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QFrame, QStackedWidget, QDialog, QLineEdit,
    QMessageBox, QInputDialog, QRadioButton, QGroupBox
)
from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtGui import QPixmap
from flask import Flask, render_template, jsonify
from flask_caching import Cache
from werkzeug.middleware.proxy_fix import ProxyFix
import atexit

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    filename="smart_gate.log",
    filemode="a"
)
logger = logging.getLogger("SmartGate")
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Database configuration
DB_PATH = os.path.join(os.path.dirname(__file__), "database", "smart_gate.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Pin configuration
PIN_CONFIG = {
    "RED_LED": 24,
    "GREEN_LED": 23,
    "RELAY": 17,
    "RED_BUZZER": 25,
    "GREEN_BUZZER": 26
}
RELAY_ACTIVE_LOW = False

class PowerManagement:
    def __init__(self):
        self.power_mode = "normal"
        self.cpu_threshold = 80
        self.memory_threshold = 70
        self.battery_threshold = 20
        self.last_optimization = time.time()
        self.optimization_interval = 300
    
    def _monitor_resources(self):
        while True:
            try:
                cpu_percent = psutil.cpu_percent(interval=1)
                memory_percent = psutil.virtual_memory().percent
                current_time = time.time()
                if current_time - self.last_optimization >= self.optimization_interval:
                    self._optimize_system(cpu_percent, memory_percent)
                    self.last_optimization = current_time
                if cpu_percent > self.cpu_threshold or memory_percent > self.memory_threshold:
                    self.set_power_mode("power_save")
                else:
                    self.set_power_mode("normal")
                time.sleep(60)
            except Exception as e:
                logger.error(f"Error in power management: {e}")
                time.sleep(60)
    
    def _optimize_system(self, cpu_percent, memory_percent):
        try:
            if memory_percent > self.memory_threshold:
                self._clear_memory_cache()
            self._optimize_database()
            logger.info(f"System optimized - CPU: {cpu_percent}%, Memory: {memory_percent}%")
        except Exception as e:
            logger.error(f"Error during system optimization: {e}")
    
    def _clear_memory_cache(self):
        try:
            os.system("sync")
            os.system("echo 3 > /proc/sys/vm/drop_caches")
        except Exception as e:
            logger.warning(f"Could not clear memory cache: {e}")
    
    def _optimize_database(self):
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("VACUUM")
            cursor.execute("ANALYZE")
            conn.commit()
        except Exception as e:
            logger.error(f"Error optimizing database: {e}")
        finally:
            if conn:
                conn.close()
    
    def set_power_mode(self, mode):
        if mode != self.power_mode:
            self.power_mode = mode
            self._apply_power_mode()
    
    def _apply_power_mode(self):
        try:
            if self.power_mode == "power_save":
                os.system("echo powersave > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
            elif self.power_mode == "normal":
                os.system("echo ondemand > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        except Exception as e:
            logger.warning(f"Could not apply power mode {self.power_mode}: {e}")

power_manager = PowerManagement()

class HardwareController:
    def __init__(self):
        self.servo = None
        self.pn532 = None
        self.servo_kit = None
        self.i2c = None
        self.red_led_pwm = None
        self.green_led_pwm = None
        self.red_buzzer_pwm = None
        self.green_buzzer_pwm = None
        self.gate_closed = True
        self.relay_state_on = GPIO.LOW if RELAY_ACTIVE_LOW else GPIO.HIGH
        self.relay_state_off = GPIO.HIGH if RELAY_ACTIVE_LOW else GPIO.LOW
        self.stop_event = threading.Event()
        self.gpio_mode_set = False
        
        try:
            GPIO.setmode(GPIO.BCM)
            self.gpio_mode_set = True
            GPIO.setwarnings(False)
            
            self.RED_LED_PIN = PIN_CONFIG["RED_LED"]
            self.GREEN_LED_PIN = PIN_CONFIG["GREEN_LED"]
            self.RELAY_PIN = PIN_CONFIG["RELAY"]
            self.RED_BUZZER_PIN = PIN_CONFIG["RED_BUZZER"]
            self.GREEN_BUZZER_PIN = PIN_CONFIG["GREEN_BUZZER"]
            
            self.SERVO_CHANNEL = 0
            self.SERVO_MIN_PULSE = 500
            self.SERVO_MAX_PULSE = 2500
            self.SERVO_FREQ = 50
            self.SERVO_SPEED = 0.5  # Increased speed
            self.SERVO_STEPS = 30   # More steps for smoother movement
            self.SERVO_CLOSED_ANGLE = 0
            self.SERVO_OPEN_ANGLE = 120
            
            GPIO.setup(self.RED_LED_PIN, GPIO.OUT)
            GPIO.setup(self.GREEN_LED_PIN, GPIO.OUT)
            GPIO.setup(self.RELAY_PIN, GPIO.OUT)
            GPIO.setup(self.RED_BUZZER_PIN, GPIO.OUT)
            GPIO.setup(self.GREEN_BUZZER_PIN, GPIO.OUT)
            
            self.red_led_pwm = GPIO.PWM(self.RED_LED_PIN, 100)
            self.green_led_pwm = GPIO.PWM(self.GREEN_LED_PIN, 100)
            self.red_buzzer_pwm = GPIO.PWM(self.RED_BUZZER_PIN, 100)
            self.green_buzzer_pwm = GPIO.PWM(self.GREEN_BUZZER_PIN, 100)
            
            self.red_led_pwm.start(0)
            self.green_led_pwm.start(0)
            self.red_buzzer_pwm.start(0)
            self.green_buzzer_pwm.start(0)
            
            try:
                self.i2c = busio.I2C(board.SCL, board.SDA)
                self.pn532 = PN532_I2C(self.i2c, debug=False)
                self.pn532.SAM_configuration()
                logger.info("NFC reader initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize NFC reader: {e}")
                self.pn532 = None
            
            try:
                self.servo_kit = ServoKit(channels=16, frequency=self.SERVO_FREQ)
                self.servo = self.servo_kit.servo[self.SERVO_CHANNEL]
                self.servo.set_pulse_width_range(self.SERVO_MIN_PULSE, self.SERVO_MAX_PULSE)
                self.servo.angle = self.SERVO_CLOSED_ANGLE
                time.sleep(0.5)
                self.servo.fraction = None
                logger.info("Servo controller initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to initialize servo controller: {e}. Gate operations disabled.")
                self.servo = None
            
            self.led_off()
            self.buzzer_off()
            self.relay_off()
            self.gate_closed = True
            
            self.nfc_thread = threading.Thread(target=self._scan_nfc, daemon=True)
            self.nfc_thread.start()
            logger.info("Hardware controller initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing hardware controller: {e}")
            self.cleanup()
            raise
    
    def _scan_nfc(self):
        time.sleep(5)  # Delay to prevent immediate scans on startup
        while not self.stop_event.is_set():
            try:
                if not self.pn532:
                    time.sleep(5)
                    continue
                uid = self.pn532.read_passive_target(timeout=0.5)
                if uid is not None:
                    card_id = "".join([format(i, "02X") for i in uid])
                    logger.info(f"Found card with UID: {card_id}")
                    self._process_card_scan(card_id)
                    time.sleep(2)
                else:
                    time.sleep(0.1)
            except Exception as e:
                logger.error(f"Error scanning NFC: {e}")
                time.sleep(5)
    
    def _process_card_scan(self, card_id):
        student_data = get_student_by_card(card_id)
        if student_data and student_data.get("valid", False):
            logger.info(f"Valid card scanned: {card_id}, Student: {student_data.get('name')}")
            self.red_led_on()  # Switched to red for success
            self.red_buzzer_on()  # Switched to red buzzer for success
            self.open_gate()
            log_entry(card_id, student_data.get("id", "UNKNOWN"), "success")
            def delayed_close():
                time.sleep(8)  # Increased delay
                self.red_led_off()
                self.red_buzzer_off()
                self.close_gate()
            threading.Thread(target=delayed_close, daemon=True).start()
        else:
            logger.warning(f"Invalid or inactive card scanned: {card_id}")
            self.green_led_on()  # Switched to green for failure
            self.green_buzzer_on()  # Switched to green buzzer for failure
            log_entry(card_id, "UNKNOWN", "failure")
            def delayed_alarm_off():
                self.trigger_alarm()
                time.sleep(5)  # Increased delay
                self.green_led_off()
                self.green_buzzer_off()
            threading.Thread(target=delayed_alarm_off, daemon=True).start()
    
    def _move_servo_smoothly(self, target_angle):
        if not self.servo:
            logger.error("Servo not initialized, cannot move.")
            return
        try:
            current_angle = self.servo.angle if self.servo.angle is not None else self.SERVO_CLOSED_ANGLE
            target_angle = max(0, min(180, target_angle))
            if abs(current_angle - target_angle) < 1:
                self.servo.angle = target_angle
                return
            
            steps = self.SERVO_STEPS
            angle_diff = target_angle - current_angle
            angle_step = angle_diff / steps
            total_time = abs(angle_diff / 90.0) * self.SERVO_SPEED
            delay = total_time / steps
            delay = max(0.01, delay)
            
            # First ensure relay is on
            self.relay_on()
            time.sleep(0.2)  # Give relay time to stabilize
            
            # Then move servo
            for i in range(steps):
                step_target_angle = current_angle + (angle_step * (i + 1))
                self.servo.angle = step_target_angle
                time.sleep(delay)
            
            self.servo.angle = target_angle
            time.sleep(0.2)  # Hold position briefly
            
            # Turn off relay after movement
            self.relay_off()
            
        except Exception as e:
            logger.error(f"Error moving servo: {e}")
            self.relay_off()  # Ensure relay is off on error
    
    def open_gate(self):
        if self.gate_closed:
            logger.info("Opening gate...")
            try:
                if self.servo:
                    self._move_servo_smoothly(self.SERVO_OPEN_ANGLE)
                else:
                    logger.warning("Gate cannot open: Servo not initialized.")
                self.gate_closed = False
                logger.info("Gate opened successfully")
            except Exception as e:
                logger.error(f"Error opening gate: {e}")
                self.relay_off()
        else:
            logger.warning("Gate already open, open command ignored.")
    
    def close_gate(self):
        if not self.gate_closed:
            logger.info("Closing gate...")
            try:
                if self.servo:
                    self._move_servo_smoothly(self.SERVO_CLOSED_ANGLE)
                self.relay_off()
                self.gate_closed = True
                logger.info("Gate closed successfully")
            except Exception as e:
                logger.error(f"Error closing gate: {e}")
                self.relay_off()
        else:
            logger.warning("Gate already closed, close command ignored.")
    
    def green_led_on(self):
        GPIO.output(self.GREEN_LED_PIN, GPIO.HIGH)
    
    def green_led_off(self):
        GPIO.output(self.GREEN_LED_PIN, GPIO.LOW)
    
    def red_led_on(self):
        GPIO.output(self.RED_LED_PIN, GPIO.HIGH)
    
    def red_led_off(self):
        GPIO.output(self.RED_LED_PIN, GPIO.LOW)
    
    def led_off(self):
        self.green_led_off()
        self.red_led_off()
    
    def green_buzzer_on(self):
        self.green_buzzer_pwm.ChangeDutyCycle(50)
    
    def green_buzzer_off(self):
        self.green_buzzer_pwm.ChangeDutyCycle(0)
    
    def red_buzzer_on(self):
        self.red_buzzer_pwm.ChangeDutyCycle(50)
    
    def red_buzzer_off(self):
        self.red_buzzer_pwm.ChangeDutyCycle(0)
    
    def buzzer_off(self):
        self.green_buzzer_off()
        self.red_buzzer_off()
    
    def relay_on(self):
        GPIO.output(self.RELAY_PIN, self.relay_state_on)
        logger.info(f"Turning RELAY ON (Pin {self.RELAY_PIN}, State: {self.relay_state_on})")
        time.sleep(0.2)  # Increased delay for relay stability
    
    def relay_off(self):
        GPIO.output(self.RELAY_PIN, self.relay_state_off)
        logger.info(f"Turning RELAY OFF (Pin {self.RELAY_PIN}, State: {self.relay_state_off})")
        time.sleep(0.2)  # Increased delay for relay stability
    
    def trigger_alarm(self):
        def alarm_sequence():
            for _ in range(3):
                self.red_buzzer_on()
                time.sleep(0.5)  # Increased duration
                self.red_buzzer_off()
                time.sleep(0.5)  # Increased duration
        threading.Thread(target=alarm_sequence, daemon=True).start()
    
    def cleanup(self):
        self.stop_event.set()  # Signal NFC thread to stop
        if self.nfc_thread.is_alive():
            self.nfc_thread.join(timeout=2)  # Wait for thread to finish
        
        try:
            if self.gpio_mode_set:
                self.led_off()
                self.buzzer_off()
                self.relay_off()
                if self.red_led_pwm: self.red_led_pwm.stop()
                if self.green_led_pwm: self.green_led_pwm.stop()
                if self.red_buzzer_pwm: self.red_buzzer_pwm.stop()
                if self.green_buzzer_pwm: self.green_buzzer_pwm.stop()
                if self.servo: self.servo.fraction = None
                GPIO.cleanup()
            else:
                logger.warning("GPIO mode not set, skipping GPIO cleanup")
            logger.info("Hardware cleanup completed successfully")
        except Exception as e:
            logger.error(f"Error during hardware cleanup: {e}")

hardware_controller = None
try:
    hardware_controller = HardwareController()
except Exception as e:
    logger.critical(f"Failed to initialize HardwareController: {e}. Exiting.")
    try: GPIO.cleanup()
    except: pass
    sys.exit(1)
atexit.register(hardware_controller.cleanup)

class GateController:
    def __init__(self):
        self.is_open = False
        self.is_alarm_active = False
    
    def open_gate(self):
        if not self.is_open:
            hardware_controller.open_gate()
            self.is_open = True
    
    def close_gate(self):
        if self.is_open:
            hardware_controller.close_gate()
            self.is_open = False
    
    def trigger_alarm(self):
        if not self.is_alarm_active:
            self.is_alarm_active = True
            hardware_controller.trigger_alarm()
    
    def stop_alarm(self):
        if self.is_alarm_active:
            self.is_alarm_active = False
            hardware_controller.buzzer_off()

gate_controller = GateController()

os.makedirs("assets", exist_ok=True)
os.makedirs("database", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("static/css", exist_ok=True)
os.makedirs("static/js", exist_ok=True)

class DatabasePool:
    def __init__(self, max_connections=5):
        self.max_connections = max_connections
        self.connections = []
        self.lock = threading.Lock()
        for _ in range(max_connections):
            conn = self._create_connection()
            if conn:
                self.connections.append(conn)
        logger.info(f"Database pool initialized with {len(self.connections)} connections")
    
    def _create_connection(self):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-2000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA page_size=4096")
            return conn
        except Exception as e:
            logger.error(f"Error creating database connection: {e}")
            return None
    
    def get_connection(self):
        with self.lock:
            if self.connections:
                conn = self.connections.pop()
                try:
                    conn.execute("SELECT 1")
                    return conn
                except Exception:
                    logger.warning("Invalid connection found in pool, creating new connection")
                    return self._create_connection()
            return self._create_connection()
    
    def return_connection(self, conn):
        if conn is None:
            return
        with self.lock:
            try:
                conn.execute("SELECT 1")
                if len(self.connections) < self.max_connections:
                    self.connections.append(conn)
                else:
                    conn.close()
            except Exception as e:
                logger.error(f"Error returning connection to pool: {e}")
                try:
                    conn.close()
                except:
                    pass
    
    def close_all(self):
        with self.lock:
            for conn in self.connections:
                try:
                    conn.close()
                except Exception as e:
                    logger.error(f"Error closing connection: {e}")
            self.connections.clear()
            logger.info("All database connections closed")

db_pool = DatabasePool()
atexit.register(db_pool.close_all)

def get_db_connection():
    return db_pool.get_connection()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
cache = Cache(app, config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 300})

def invalidate_cache():
    cache.delete_memoized(get_recent_entries)
    cache.delete_memoized(get_all_students)
    cache.delete_memoized(get_entry_stats)
    logger.info("Cache invalidated")

def setup_database():
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            faculty TEXT,
            program TEXT,
            level TEXT,
            image_path TEXT
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_students_name ON students(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_students_faculty ON students(faculty)")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            card_id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            card_type TEXT DEFAULT "student",
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cards_student_id ON cards(student_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cards_type ON cards(card_type)")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS entry_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT,
            student_id TEXT,
            timestamp TEXT NOT NULL,
            gate TEXT DEFAULT "Main Gate",
            status TEXT NOT NULL,
            entry_type TEXT DEFAULT "regular",
            FOREIGN KEY (card_id) REFERENCES cards(card_id),
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entry_logs_timestamp ON entry_logs(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entry_logs_status ON entry_logs(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entry_logs_type ON entry_logs(entry_type)")
        cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                      ("20210001", "John Smith", "Engineering", "Computer Engineering", "3rd Year", "assets/student1.png"))
        cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                      ("20210002", "Sarah Johnson", "Science", "Physics", "2nd Year", "assets/student2.png"))
        cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                      ("20210003", "Mohammed Ali", "Medicine", "General Medicine", "4th Year", "assets/student3.png"))
        cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                      ("SECURITY001", "Security Staff", "Security", "Gate Security", "Staff", "assets/security_staff.png"))
        cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                      ("A1B2C3D4", "20210001", 1, "student"))
        cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                      ("E5F6G7H8", "20210002", 1, "student"))
        cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                      ("I9J0K1L2", "20210003", 1, "student"))
        cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                      ("ADMIN001", "SECURITY001", 1, "admin"))
        current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT OR IGNORE INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type) VALUES (?, ?, ?, ?, ?, ?)", 
                      ("A1B2C3D4", "20210001", current_date, "Main Gate", "success", "regular"))
        cursor.execute("INSERT OR IGNORE INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type) VALUES (?, ?, ?, ?, ?, ?)", 
                      ("E5F6G7H8", "20210002", current_date, "Main Gate", "success", "regular"))
        cursor.execute("INSERT OR IGNORE INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type) VALUES (?, ?, ?, ?, ?, ?)", 
                      ("I9J0K1L2", "20210003", yesterday, "Library Gate", "success", "regular"))
        cursor.execute("INSERT OR IGNORE INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type) VALUES (?, ?, ?, ?, ?, ?)", 
                      ("UNKNOWN", "UNKNOWN", yesterday, "Main Gate", "failure", "regular"))
        cursor.execute("INSERT OR IGNORE INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type) VALUES (?, ?, ?, ?, ?, ?)", 
                      ("ADMIN001", "SECURITY001", yesterday, "Main Gate", "success", "visitor_access"))
        conn.commit()
    except Exception as e:
        logger.error(f"Error setting up database: {e}")
        if conn: conn.rollback()
    finally:
        db_pool.return_connection(conn)
    logger.info("Database setup completed.")

@cache.memoize()
def get_student_by_card(card_id):
    conn = get_db_connection()
    if not conn: return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT s.id, s.name, s.faculty, s.program, s.level, s.image_path, c.is_active, c.card_type
        FROM students s
        JOIN cards c ON s.id = c.student_id
        WHERE c.card_id = ?
        """, (card_id,))
        result = cursor.fetchone()
        if result:
            student_data = {
                "id": result[0],
                "name": result[1],
                "faculty": result[2],
                "program": result[3],
                "level": result[4],
                "image_path": result[5],
                "valid": bool(result[6]),
                "card_type": result[7],
                "card_id": card_id
            }
            return student_data
        return None
    except Exception as e:
        logger.error(f"Error getting student by card {card_id}: {e}")
        return None
    finally:
        db_pool.return_connection(conn)

def log_entry(card_id, student_id, status, gate="Main Gate", entry_type="regular"):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
        INSERT INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (card_id, student_id, timestamp, gate, status, entry_type))
        conn.commit()
        invalidate_cache()
    except Exception as e:
        logger.error(f"Error logging entry: {e}")
        if conn: conn.rollback()
    finally:
        db_pool.return_connection(conn)

def add_new_card(card_id, student_id, card_type="student"):
    conn = get_db_connection()
    if not conn: return False
    try:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO cards (card_id, student_id, is_active, card_type)
        VALUES (?, ?, 1, ?)
        """, (card_id, student_id, card_type))
        conn.commit()
        invalidate_cache()
        return True
    except sqlite3.IntegrityError:
        logger.warning(f"Card ID {card_id} or Student ID {student_id} already exists.")
        return False
    except Exception as e:
        logger.error(f"Error adding new card: {e}")
        if conn: conn.rollback()
        return False
    finally:
        db_pool.return_connection(conn)

def add_new_student(student_id, name, faculty="", program="", level="", image_path=""):
    conn = get_db_connection()
    if not conn: return False
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM students WHERE id = ?", (student_id,))
        exists = cursor.fetchone()
        if exists:
            cursor.execute("""
            UPDATE students
            SET name = ?, faculty = ?, program = ?, level = ?, image_path = ?
            WHERE id = ?
            """, (name, faculty, program, level, image_path, student_id))
            logger.info(f"Updated student: {student_id}")
        else:
            cursor.execute("""
            INSERT INTO students (id, name, faculty, program, level, image_path)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (student_id, name, faculty, program, level, image_path))
            logger.info(f"Added new student: {student_id}")
        conn.commit()
        invalidate_cache()
        return True
    except Exception as e:
        logger.error(f"Error adding/updating student {student_id}: {e}")
        if conn: conn.rollback()
        return False
    finally:
        db_pool.return_connection(conn)

@cache.memoize()
def get_all_students():
    conn = get_db_connection()
    if not conn: return []
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT s.id, s.name, s.faculty, s.program, s.level, s.image_path, c.card_id
        FROM students s
        LEFT JOIN cards c ON s.id = c.student_id
        ORDER BY s.name
        """)
        result = cursor.fetchall()
        return [dict(row) for row in result]
    except Exception as e:
        logger.error(f"Error getting all students: {e}")
        return []
    finally:
        db_pool.return_connection(conn)

@cache.memoize()
def get_recent_entries(limit=10):
    conn = get_db_connection()
    if not conn: return []
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT e.id, e.card_id, e.student_id, s.name as student_name, e.timestamp, e.gate, e.status, e.entry_type
        FROM entry_logs e
        LEFT JOIN students s ON e.student_id = s.id
        ORDER BY e.timestamp DESC
        LIMIT ?
        """, (limit,))
        result = cursor.fetchall()
        return [dict(row) for row in result]
    except Exception as e:
        logger.error(f"Error getting recent entries: {e}")
        return []
    finally:
        db_pool.return_connection(conn)

@cache.memoize()
def get_entry_stats():
    conn = get_db_connection()
    if not conn: return {"total": 0, "today": 0, "successful": 0, "failed": 0, "visitor": 0}
    cursor = conn.cursor()
    try:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        cursor.execute("SELECT COUNT(*) FROM entry_logs")
        total_entries = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM entry_logs WHERE timestamp LIKE ?", (f"{today}%",))
        today_entries = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM entry_logs WHERE status = 'success'")
        successful_entries = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM entry_logs WHERE status = 'failure'")
        failed_entries = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM entry_logs WHERE entry_type = 'visitor_access'")
        visitor_entries = cursor.fetchone()[0]
        return {
            "total": total_entries,
            "today": today_entries,
            "successful": successful_entries,
            "failed": failed_entries,
            "visitor": visitor_entries
        }
    except Exception as e:
        logger.error(f"Error getting entry stats: {e}")
        return {"total": 0, "today": 0, "successful": 0, "failed": 0, "visitor": 0}
    finally:
        db_pool.return_connection(conn)

def create_placeholder_images():
    try:
        from PIL import Image, ImageDraw
        if not os.path.exists("assets/university_logo_placeholder.png"):
            img = Image.new("RGB", (200, 200), color=(25, 25, 112))
            d = ImageDraw.Draw(img)
            d.rectangle([10, 10, 190, 190], outline=(255, 255, 255), width=2)
            d.text((40, 80), "University\nLogo", fill=(255, 255, 255))
            img.save("assets/university_logo_placeholder.png")
        for i in range(1, 4):
            if not os.path.exists(f"assets/student{i}.png"):
                img = Image.new("RGB", (200, 200), color=(200, 200, 200))
                d = ImageDraw.Draw(img)
                d.rectangle([10, 10, 190, 190], outline=(100, 100, 100), width=2)
                d.text((50, 90), f"Student {i}", fill=(50, 50, 50))
                img.save(f"assets/student{i}.png")
        if not os.path.exists("assets/security_staff.png"):
            img = Image.new("RGB", (200, 200), color=(50, 50, 50))
            d = ImageDraw.Draw(img)
            d.rectangle([10, 10, 190, 190], outline=(200, 200, 200), width=2)
            d.text((40, 90), "Security Staff", fill=(200, 200, 200))
            img.save("assets/security_staff.png")
    except ImportError:
        logger.warning("PIL/Pillow not found. Cannot create placeholder images.")

def create_flask_templates():
    if not os.path.exists("templates/index.html"):
        with open("templates/index.html", "w") as f:
            f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart Gate Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
        <div class="container-fluid">
            <a class="navbar-brand" href="#">Smart Gate Dashboard</a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav">
                    <li class="nav-item">
                        <a class="nav-link active" href="#dashboard">Dashboard</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="#students">Students</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="#entries">Entry Logs</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="#stats">Statistics</a>
                    </li>
                </ul>
            </div>
        </div>
    </nav>
    <div class="container mt-4">
        <div id="dashboard" class="section active">
            <h2>Dashboard</h2>
            <div class="row mt-4">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h5>Recent Entries</h5>
                        </div>
                        <div class="card-body">
                            <div class="table-responsive">
                                <table class="table table-striped">
                                    <thead>
                                        <tr>
                                            <th>Time</th>
                                            <th>Student</th>
                                            <th>Status</th>
                                        </tr>
                                    </thead>
                                    <tbody id="recent-entries">
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <div class="card-header">
                            <h5>Today's Statistics</h5>
                        </div>
                        <div class="card-body">
                            <div class="row">
                                <div class="col-6 mb-3">
                                    <div class="stat-card bg-primary text-white">
                                        <h3 id="today-entries">0</h3>
                                        <p>Today's Entries</p>
                                    </div>
                                </div>
                                <div class="col-6 mb-3">
                                    <div class="stat-card bg-success text-white">
                                        <h3 id="successful-entries">0</h3>
                                        <p>Successful</p>
                                    </div>
                                </div>
                                <div class="col-6 mb-3">
                                    <div class="stat-card bg-danger text-white">
                                        <h3 id="failed-entries">0</h3>
                                        <p>Failed</p>
                                    </div>
                                </div>
                                <div class="col-6 mb-3">
                                    <div class="stat-card bg-info text-white">
                                        <div class="stat-card bg-info">
                                        <h3 id="visitor-entries">0</h3>
                                        <p>Visitor Access</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <div id="students" class="section">
            <h2>Students</h2>
            <div class="table-responsive mt-4">
                <table class="table table-striped">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Name</th>
                            <th>Faculty</th>
                            <th>Program</th>
                            <th>Level</th>
                            <th>Card ID</th>
                        </tr>
                    </thead>
                    <tbody id="students-table">
                    </tbody>
                </table>
            </div>
        </div>
        <div id="entries" class="section">
            <h2>Entry Logs</h2>
            <div class="table-responsive mt-4">
                <table class="table table-striped">
                    <thead>
                        <tr>
                            <th>Timestamp</th>
                            <th>Card ID</th>
                            <th>Student Name</th>
                            <th>Gate</th>
                            <th>Status</th>
                            <th>Type</th>
                        </tr>
                    </thead>
                    <tbody id="entries-table">
                    </tbody>
                </table>
            </div>
        </div>
        <div id="stats" class="section">
            <h2>Statistics</h2>
            <div class="row mt-4">
                <div class="col-md-3">
                    <div class="stat-card bg-secondary text-white">
                        <h3 id="total-entries">0</h3>
                        <p>Total Entries</p>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="stat-card bg-primary text-white">
                        <h3 id="stats-today-entries">0</h3>
                        <p>Today's Entries</p>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="stat-card bg-success text-white">
                        <h3 id="stats-successful-entries">0</h3>
                        <p>Successful</p>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="stat-card bg-danger text-white">
                    <div class="stat-card bg-danger">
                        <h3 id="stats-failed-entries">0</h3>
                        <p>Failed</p>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="{{ url_for('static', filename='js/script.js') }}"></script>
</body>
</html>
""")
    with open("static/css/style.css", "w") as f:
        f.write("""
body {
    font-family: Arial, sans-serif;
}
.navbar {
    margin-bottom: 20px;
}
.section {
    display: none !important;
}
.section.active {
    display: block !important;
}
.stat-card {
    padding: 15px;
    border-radius: 5px;
    text-align: center;
}
.stat-card h3 {
    font-size: 24px;
    margin-bottom: 5px;
}
.stat-card p {
    margin-bottom: 0;
}
.table-responsive {
    max-height: 400px;
    overflow-y: auto;
}
""")
    with open("static/js/script.js", "w") as f:
        f.write("""
document.addEventListener("DOMContentLoaded", function() {
    const navLinks = document.querySelectorAll(".nav-link");
    const sections = document.querySelectorAll(".section");
    
    function showSection(targetId) {
        sections.forEach(section => section.classList.remove("active"));
        const targetSection = document.getElementById(targetId);
        if (targetSection) targetSection.classList.add("active");
    }
    
    navLinks.forEach(link => {
        link.addEventListener("click", function(event) {
            event.preventDefault();
            const targetId = this.getAttribute("href").substring(1);
            navLinks.forEach(nav => nav.classList.remove("active"));
            this.classList.add("active");
            showSection(targetId);
        });
    });
    
    function fetchData(url, callback) {
        fetch(url)
            .then(response => response.json())
            .then(data => {
                if (data.status === "success") callback(data.data);
                else console.error("API Error:", data.message);
            })
            .catch(error => console.error("Fetch Error:", error));
    }
    
    function updateRecentEntries(entries) {
        const tbody = document.getElementById("recent-entries");
        tbody.innerHTML = "";
        entries.forEach(entry => {
            const row = `<tr>
                <td>${new Date(entry.timestamp).toLocaleTimeString()}</td>
                <td>${entry.student_name || entry.student_id || "Unknown'}</td>
                <td><span class="badge bg-${entry.status === "success" ? "success" : "danger"}">${entry.status}</span></td>
            </tr>`;
            tbody.innerHTML += row;
        });
    }
    
    function updateStudentsTable(students) {
        const tbody = document.getElementById("students-table");
        tbody.innerHTML = "";
        students.forEach(student => {
            const row = `<tr>
                <td>${student.id}</td>
                <td>${student.name}</td>
                <td>${student.faculty || 'N/A'}</td>
                <td>${student.program || 'N/A'}</td>
                <td>${student.level || 'N/A'}</td>
                <td>${student.card_id || 'N/A'}</td>
            </tr>`;
            tbody.innerHTML += row;
        });
    }
    
    function updateEntriesTable(entries) {
        const tbody = document.getElementById("entries-table");
        tbody.innerHTML = "";
        entries.forEach(entry => {
            const row = `<tr>
                <td>${entry.timestamp}</td>
                <td>${entry.card_id}</td>
                <td>${entry.student_name || entry.student_id || 'Unknown'}</td>
                <td>${entry.gate}</td>
                <td><span class="badge bg-${entry.status === 'success' ? 'success' : 'danger'}">${entry.status}</span></td>
                <td>${entry.entry_type}</td>
            </tr>`;
            tbody.innerHTML += row;
        });
    }
    
    function updateStats(stats) {
        document.getElementById('today-entries').textContent = stats.today;
        document.getElementById('successful-entries').textContent = stats.success;
        document.getElementById('failed-entries').textContent = stats.failed;
        document.getElementById('visitor-entries').textContent = stats.visitor;
        document.getElementById('total-entries').textContent = stats.total;
        document.getElementById('stats-today-entries').textContent = stats.today;
        document.getElementById('stats-successful-entries').textContent = stats.success;
        document.getElementById('stats-failed-entries').textContent = stats.failed;
    }
    
    fetchData("/api/recent_entries", updateRecentEntries);
    fetchData("/api/students", updateStudentsTable);
    fetchData("/api/entries", updateEntriesTable);
    fetchData("/api/stats", updateStats);
    
    setInterval(() => {
        fetchData("/api/recent_entries", updateRecentEntries);
        fetchData("/api/stats", updateStats);
    }, 60000);
});
""")
    
    logger.info("Flask templates created successfully.")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/recent_entries")
def api_recent_entries():
    entries = get_recent_entries(5)
    return jsonify({"status": "success", "data": entries})

@app.route("/api/students")
def api_students():
    students = get_all_students()
    return jsonify({"status": "success", "data": students})

@app.route("/api/entries")
def api_entries():
    conn = get_db_connection()
    if not conn: return jsonify({"status": "error", "message": "Database connection failed"}), 500
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT e.id,거리 e.card_id, e.student_id, s.name AS student_name, e.timestamp, e.gate, e.status, e.entry_type
        FROM entry_logs e
        LEFT JOIN students s ON e.student_id = s.id
        ORDER BY e.timestamp DESC
        LIMIT 100
        """)
        entries = [dict(row) for row in cursor.fetchall()]
        return jsonify({"status": "success", "data": entries})
    except Exception as e:
        logger.error(f"Error fetching entries: {e}")
        return jsonify({"status": "error", "message": "Failed to fetch entries"}), 500
    finally:
        db_pool.return_connection(conn)

@app.route("/api/stats")
def api_stats():
    stats = get_entry_stats()
    return jsonify({"status": "success", "data": stats})

class MainScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.init_ui()
    
    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # Header with admin button and datetime
        header_layout = QHBoxLayout()
        admin_button = QPushButton("Admin")
        admin_button.setFixedSize(100, 40)
        admin_button.setStyleSheet("""
            QPushButton { background-color: #1A237E; color: white; border-radius: 5px; font-size: 16px; }
            QPushButton:hover { background-color: #0D47A1; }
            QPushButton:pressed { background-color: #0A2472; }
        """)
        admin_button.clicked.connect(self.show_admin_screen)
        
        self.datetime_label = QLabel()
        self.datetime_label.setAlignment(Qt.AlignRight)
        self.datetime_label.setStyleSheet("font-size: 16px; color: #1A237E;")
        self.update_datetime()
        self.datetime_timer = QTimer(self)
        self.datetime_timer.timeout.connect(self.update_datetime)
        self.datetime_timer.start(1000)
        
        header_layout.addWidget(admin_button)
        header_layout.addStretch()
        header_layout.addWidget(self.datetime_label)
        
        # Control Panel
        control_frame = QFrame()
        control_frame.setFrameShape(QFrame.Box)
        control_frame.setFrameShadow(QFrame.Raised)
        control_frame.setLineWidth(2)
        control_frame.setStyleSheet("""
            QFrame { 
                border: 2px solid #1A237E;
                background-color: #E8EAF6;
                padding: 10px;
            }
        """)
        
        control_layout = QVBoxLayout(control_frame)
        
        # Title
        title_label = QLabel("Control Panel")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #1A237E; margin-bottom: 10px;")
        control_layout.addWidget(title_label)
        
        # Gate Controls
        gate_group = QGroupBox("Gate Controls")
        gate_group.setStyleSheet("""
            QGroupBox {
                font-size: 16px;
                font-weight: bold;
                border: 1px solid #1A237E;
                margin-top: 10px;
            }
            QGroupBox::title {
                color: #1A237E;
            }
        """)
        gate_layout = QHBoxLayout()
        
        open_gate_btn = QPushButton("Open Gate")
        close_gate_btn = QPushButton("Close Gate")
        for btn in [open_gate_btn, close_gate_btn]:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #1A237E;
                    color: white;
                    border-radius: 5px;
                    font-size: 16px;
                    padding: 10px;
                    min-width: 120px;
                }
                QPushButton:hover {
                    background-color: #0D47A1;
                }
                QPushButton:pressed {
                    background-color: #0A2472;
                }
            """)
        
        open_gate_btn.clicked.connect(self.open_gate)
        close_gate_btn.clicked.connect(self.close_gate)
        
        gate_layout.addWidget(open_gate_btn)
        gate_layout.addWidget(close_gate_btn)
        gate_group.setLayout(gate_layout)
        
        # Simulation Controls
        sim_group = QGroupBox("Simulation Controls")
        sim_group.setStyleSheet("""
            QGroupBox {
                font-size: 16px;
                font-weight: bold;
                border: 1px solid #1A237E;
                margin-top: 10px;
            }
            QGroupBox::title {
                color: #1A237E;
            }
        """)
        sim_layout = QHBoxLayout()
        
        valid_login_btn = QPushButton("Valid Login")
        invalid_login_btn = QPushButton("Invalid Login")
        alarm_btn = QPushButton("Trigger Alarm")
        
        for btn in [valid_login_btn, invalid_login_btn, alarm_btn]:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #1A237E;
                    color: white;
                    border-radius: 5px;
                    font-size: 16px;
                    padding: 10px;
                    min-width: 120px;
                }
                QPushButton:hover {
                    background-color: #0D47A1;
                }
                QPushButton:pressed {
                    background-color: #0A2472;
                }
            """)
        
        valid_login_btn.clicked.connect(self.simulate_valid_login)
        invalid_login_btn.clicked.connect(self.simulate_invalid_login)
        alarm_btn.clicked.connect(self.trigger_alarm)
        
        sim_layout.addWidget(valid_login_btn)
        sim_layout.addWidget(invalid_login_btn)
        sim_layout.addWidget(alarm_btn)
        sim_group.setLayout(sim_layout)
        
        # Status Display
        self.status_label = QLabel("System Ready")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("""
            QLabel {
                font-size: 18px;
                color: #1A237E;
                padding: 10px;
                background-color: #E8EAF6;
                border-radius: 5px;
            }
        """)
        
        # Add all components to control layout
        control_layout.addWidget(gate_group)
        control_layout.addWidget(sim_group)
        control_layout.addWidget(self.status_label)
        
        # Add all components to main layout
        main_layout.addLayout(header_layout)
        main_layout.addWidget(control_frame)
        main_layout.addStretch()
        
        self.setLayout(main_layout)
    
    def update_datetime(self):
        now = datetime.datetime.now()
        date_str = now.strftime("%Y/%m/%d")
        time_str = now.strftime("%H:%M:%S")
        self.datetime_label.setText(f"{date_str} {time_str}")
    
    def show_admin_screen(self):
        if self.parent:
            self.parent.show_admin_screen()
    
    def open_gate(self):
        try:
            gate_controller.open_gate()
            self.status_label.setText("Gate Opening...")
            self.status_label.setStyleSheet("""
                QLabel {
                    font-size: 18px;
                    color: #4CAF50;
                    padding: 10px;
                    background-color: #E8F5E9;
                    border-radius: 5px;
                }
            """)
            QTimer.singleShot(5000, lambda: self.status_label.setText("System Ready"))
        except Exception as e:
            logger.error(f"Error opening gate: {e}")
            self.status_label.setText("Error Opening Gate")
            self.status_label.setStyleSheet("""
                QLabel {
                    font-size: 18px;
                    color: #F44336;
                    padding: 10px;
                    background-color: #FFEBEE;
                    border-radius: 5px;
                }
            """)
    
    def close_gate(self):
        try:
            gate_controller.close_gate()
            self.status_label.setText("Gate Closing...")
            self.status_label.setStyleSheet("""
                QLabel {
                    font-size: 18px;
                    color: #4CAF50;
                    padding: 10px;
                    background-color: #E8F5E9;
                    border-radius: 5px;
                }
            """)
            QTimer.singleShot(5000, lambda: self.status_label.setText("System Ready"))
        except Exception as e:
            logger.error(f"Error closing gate: {e}")
            self.status_label.setText("Error Closing Gate")
            self.status_label.setStyleSheet("""
                QLabel {
                    font-size: 18px;
                    color: #F44336;
                    padding: 10px;
                    background-color: #FFEBEE;
                    border-radius: 5px;
                }
            """)
    
    def simulate_valid_login(self):
        try:
            student_data = {
                "id": "20210001",
                "name": "Test Student",
                "faculty": "Engineering",
                "program": "Computer Science",
                "level": "3rd Year",
                "valid": True,
                "card_id": "TEST001"
            }
            if self.parent:
                self.parent.show_student_info(student_data)
        except Exception as e:
            logger.error(f"Error simulating valid login: {e}")
            self.status_label.setText("Error in Simulation")
    
    def simulate_invalid_login(self):
        try:
            student_data = {
                "id": "UNKNOWN",
                "name": "Invalid Card",
                "valid": False,
                "card_id": "INVALID001"
            }
            if self.parent:
                self.parent.show_student_info(student_data)
        except Exception as e:
            logger.error(f"Error simulating invalid login: {e}")
            self.status_label.setText("Error in Simulation")
    
    def trigger_alarm(self):
        try:
            gate_controller.trigger_alarm()
            self.status_label.setText("Alarm Triggered!")
            self.status_label.setStyleSheet("""
                QLabel {
                    font-size: 18px;
                    color: #F44336;
                    padding: 10px;
                    background-color: #FFEBEE;
                    border-radius: 5px;
                }
            """)
            QTimer.singleShot(5000, lambda: self.status_label.setText("System Ready"))
        except Exception as e:
            logger.error(f"Error triggering alarm: {e}")
            self.status_label.setText("Error Triggering Alarm")

class StudentInfoScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.init_ui()
        self.return_timer = QTimer(self)
        self.return_timer.timeout.connect(self.return_to_main)
        self.return_timer.setSingleShot(True)
        self.visitor_mode = False
        self.current_student_data = None
    
    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        header_layout = QHBoxLayout()
        back_button = QPushButton("Back")
        back_button.setFixedSize(100, 40)
        back_button.setStyleSheet("""
            QPushButton { background-color: #607D8B; color: white; border-radius: 5px; font-size: 16px; }
            QPushButton:hover { background-color: #455A64; }
            QPushButton:pressed { background-color: #37474F; }
        """)
        back_button.clicked.connect(self.return_to_main)
        self.datetime_label = QLabel()
        self.datetime_label.setAlignment(Qt.AlignRight)
        self.datetime_label.setStyleSheet("font-size: 16px; color: #1A237E;")
        self.update_datetime()
        self.datetime_timer = QTimer(self)
        self.datetime_timer.timeout.connect(self.update_datetime)
        self.datetime_timer.start(1000)
        header_layout.addWidget(back_button)
        header_layout.addStretch()
        header_layout.addWidget(self.datetime_label)
        content_layout = QHBoxLayout()
        self.student_image_frame = QFrame()
        self.student_image_frame.setFrameShape(QFrame.Box)
        self.student_image_frame.setFrameShadow(QFrame.Raised)
        self.student_image_frame.setLineWidth(2)
        self.student_image_frame.setStyleSheet("border: 2px solid #1A237E;")
        self.student_image_frame.setFixedSize(200, 200)
        image_layout = QVBoxLayout(self.student_image_frame)
        self.student_image_label = QLabel()
        self.student_image_label.setAlignment(Qt.AlignCenter)
        image_layout.addWidget(self.student_image_label)
        info_layout = QVBoxLayout()
        info_layout.setSpacing(10)
        self.name_label = self.create_info_field("Name:")
        self.id_label = self.create_info_field("ID:")
        self.faculty_label = self.create_info_field("Faculty:")
        self.program_label = self.create_info_field("Program:")
        self.level_label = self.create_info_field("Level:")
        info_layout.addWidget(self.name_label)
        info_layout.addWidget(self.id_label)
        info_layout.addWidget(self.faculty_label)
        info_layout.addWidget(self.program_label)
        info_layout.addWidget(self.level_label)
        info_layout.addStretch()
        content_layout.addWidget(self.student_image_frame)
        content_layout.addSpacing(20)
        content_layout.addLayout(info_layout)
        self.status_frame = QFrame()
        self.status_frame.setFrameShape(QFrame.Panel)
        self.status_frame.setFrameShadow(QFrame.Raised)
        self.status_frame.setLineWidth(2)
        self.status_frame.setFixedHeight(60)
        status_layout = QHBoxLayout(self.status_frame)
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        status_layout.addWidget(self.status_label)
        self.visitor_frame = QFrame()
        self.visitor_frame.setFrameShape(QFrame.Panel)
        self.visitor_frame.setFrameShadow(QFrame.Raised)
        self.visitor_frame.setLineWidth(2)
        self.visitor_frame.setStyleSheet("background-color: #E1F5FE; border: 2px solid #0288D1;")
        self.visitor_frame.setVisible(False)
        visitor_layout = QVBoxLayout(self.visitor_frame)
        visitor_title = QLabel("Visitor Access Mode")
        visitor_title.setAlignment(Qt.AlignCenter)
        visitor_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #01579B;")
        visitor_instruction = QLabel("Press the button below to grant access to a visitor")
        visitor_instruction.setAlignment(Qt.AlignCenter)
        visitor_instruction.setStyleSheet("font-size: 14px; color: #0277BD;")
        self.grant_access_button = QPushButton("Grant Visitor Access")
        self.grant_access_button.setStyleSheet("""
            QPushButton { background-color: #2196F3; color: white; border-radius: 5px; font-size: 16px; padding: 10px; }
            QPushButton:hover { background-color: #1976D2; }
            QPushButton:pressed { background-color: #1565C0; }
        """)
        self.grant_access_button.clicked.connect(self.grant_visitor_access)
        visitor_layout.addWidget(visitor_title)
        visitor_layout.addWidget(visitor_instruction)
        visitor_layout.addWidget(self.grant_access_button)
        main_layout.addLayout(header_layout)
        main_layout.addSpacing(10)
        main_layout.addLayout(content_layout)
        main_layout.addStretch()
        main_layout.addWidget(self.status_frame)
        main_layout.addWidget(self.visitor_frame)
        self.setLayout(main_layout)
    
    def create_info_field(self, label_text):
        frame = QFrame()
        frame.setFrameShape(QFrame.Box)
        frame.setFrameShadow(QFrame.Sunken)
        frame.setLineWidth(1)
        frame.setStyleSheet("border: 1px solid #BDBDBD; background-color: white;")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 5, 10, 5)
        label = QLabel(label_text)
        label.setStyleSheet("font-size: 16px; font-weight: bold; border: none; background-color: transparent;")
        value = QLabel()
        value.setStyleSheet("font-size: 16px; border: none; background-color: transparent;")
        layout.addWidget(label)
        layout.addWidget(value)
        layout.setStretch(0, 1)
        layout.setStretch(1, 2)
        return frame
    
    def update_datetime(self):
        now = datetime.datetime.now()
        date_str = now.strftime("%Y/%m/%d")
        time_str = now.strftime("%H:%M:%S")
        self.datetime_label.setText(f"{date_str} {time_str}")
    
    def update_student_info(self, student_data):
        self.current_student_data = student_data
        is_admin_card = student_data.get("card_type") == "admin"
        self.visitor_mode = is_admin_card
        image_path = student_data.get("image_path", "assets/university_logo_placeholder.png")
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.student_image_label.setText("No Image")
            self.student_image_label.setStyleSheet("font-size: 16px;")
        else:
            pixmap = pixmap.scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.student_image_label.setPixmap(pixmap)
        self.name_label.layout().itemAt(1).widget().setText(student_data.get("name", ""))
        self.id_label.layout().itemAt(1).widget().setText(student_data.get("id", ""))
        self.faculty_label.layout().itemAt(1).widget().setText(student_data.get("faculty", ""))
        self.program_label.layout().itemAt(1).widget().setText(student_data.get("program", ""))
        self.level_label.layout().itemAt(1).widget().setText(student_data.get("level", ""))
        is_valid = student_data.get("valid", False)
        if is_valid:
            if is_admin_card:
                self.status_label.setText("Security Staff Identified")
                self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
                self.status_frame.setStyleSheet("background-color: #2196F3; border: 2px solid #1565C0;")
                self.status_frame.setStyleSheet("background-color: #2196F3; border: 2px solid #1565C0;")
                self.visitor_frame.setVisible(True)
                log_entry(student_data.get("card_id", "UNKNOWN"), student_data.get("id", "UNKNOWN"), "success", entry_type="admin_scan")
                self.return_timer.start(30000)
            else:
                self.status_label.setText("Successful Access Granted")
                self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
                self.status_frame.setStyleSheet("background-color: #4CAF50; border: 2px solid #388E3C;")
                self.visitor_frame.setVisible(False)
                log_entry(student_data.get("card_id", "UNKNOWN"), student_data.get("id", "UNKNOWN"), "success")
                logger.info("Triggering successful gate opening sequence")
                hardware_controller.green_led_on()
                hardware_controller.green_buzzer_on()
                gate_controller.open_gate()
                def delayed_close():
                    time.sleep(5)
                    hardware_controller.green_led_off()
                    hardware_controller.green_buzzer_off()
                    gate_controller.close_gate()
                    self.return_to_main()
                threading.Thread(target=delayed_close, daemon=True).start()
        else:
            self.status_label.setText("Access Denied")
            self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
            self.status_frame.setStyleSheet("background-color: #F44336; border: 2px solid #D32F2F;")
            self.visitor_frame.setVisible(False)
            log_entry(student_data.get("card_id", "UNKNOWN"), "UNKNOWN", "failure")
            logger.info("Triggering hardware sequence for invalid card")
            hardware_controller.red_led_on()
            hardware_controller.red_buzzer_on()
            gate_controller.trigger_alarm()
            def delayed_return():
                time.sleep(3)
                hardware_controller.red_led_off()
                hardware_controller.red_buzzer_off()
                self.return_to_main()
            threading.Thread(target=delayed_return, daemon=True).start()
    
    def grant_visitor_access(self):
        try:
            if self.visitor_mode and self.current_student_data:
                logger.info("Granting visitor access...")
                log_entry(self.current_student_data.get("card_id", "UNKNOWN"), 
                          self.current_student_data.get("id", "UNKNOWN"), 
                          "success", 
                          entry_type="visitor_access")
                self.status_label.setText("Visitor Access Successful")
                self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
                self.status_frame.setStyleSheet("background-color: #4CAF50; border: 2px solid #388E3C;")
                self.visitor_frame.setVisible(False)
                logger.info("Triggering successful gate opening sequence for visitor")
                hardware_controller.green_led_on()
                hardware_controller.green_buzzer_on()
                gate_controller.open_gate()
                def delayed_close():
                    time.sleep(5)
                    hardware_controller.green_led_off()
                    hardware_controller.green_buzzer_off()
                    gate_controller.close_gate()
                    self.return_to_main()
                threading.Thread(target=delayed_close, daemon=True).start()
            else:
                logger.warning("Cannot grant visitor access: Not in visitor mode or invalid state.")
                QMessageBox.warning(self, "Error", "Visitor access cannot be granted.")
        except Exception as e:
            logger.error(f"Error granting visitor access: {e}")
            QMessageBox.critical(self, "Error", "Failed to grant visitor access.")
    
    def return_to_main(self):
        self.return_timer.stop()
        if self.parent:
            QTimer.singleShot(0, self.parent.show_main_screen)

class AdminScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.init_ui()
    
    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        header_layout = QHBoxLayout()
        back_button = QPushButton("Back")
        back_button.setFixedSize(100, 40)
        back_button.setStyleSheet("""
            QPushButton { background-color: #607D8B; color: white; border-radius: 5px; font-size: 16px; }
            QPushButton:hover { background-color: #455A64; }
            QPushButton:pressed { background-color: #37474F; }
        """)
        back_button.clicked.connect(self.return_to_main)
        header_layout.addWidget(back_button)
        header_layout.addStretch()
        title_label = QLabel("Admin Control Panel")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #1A237E;")
        main_layout.addWidget(title_label)
        button_layout = QVBoxLayout()
        button_layout.setSpacing(10)
        button = self.create_admin_button("View Entry Logs", self.view_entry_logs)
        button_layout.addWidget(button)
        button = self.create_admin_button("Add New Card", self.add_new_card)
        button_layout.addWidget(button)
        button = self.create_admin_button("Add New Student", self.add_new_student)
        button_layout.addWidget(button)
        button = self.create_admin_button("Run System Diagnostics", self.run_diagnostics)
        button_layout.addWidget(button)
        button_layout.addStretch()
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)
    
    def create_admin_button(self, text, func):
        button = QPushButton(text)
        button.setStyleSheet("""
            QPushButton { background-color: #1A237E; color: white; border-radius: 5px; font-size: 16px; padding: 10px; }
            QPushButton:hover { background-color: #0D47A1; }
            QPushButton:pressed { background-color: #0A2472; }
        """)
        button.clicked.connect(func)
        return button
    
    def return_to_main(self):
        if self.parent:
            self.parent.show_main_screen()
    
    def view_entry_logs(self):
        try:
            logger.info("\n--- Recent Entry Logs ---")
            entries = get_recent_entries(20)
            if not entries:
                logger.info("No entries found.")
                QMessageBox.information(self, "Entry Logs", "No recent entries found.")
                return
            for entry in entries:
                logger.info("{} - Card: {} - Student: {} - Status: {}".format(
                    entry["timestamp"], 
                    entry["card_id"], 
                    entry.get("student_name", entry["student_id"]), 
                    entry["status"]
                ))
            logger.info("------------------------\n")
            QMessageBox.information(self, "Entry Logs", "Recent entry logs printed to console/log file.")
        except Exception as e:
            logger.error(f"Error viewing entry logs: {e}")
            QMessageBox.critical(self, "Error", "Failed to retrieve entry logs.")
    
    def add_new_card(self):
        try:
            dialog = QDialog(self)
            dialog.setWindowTitle("Add New Card")
            layout = QFormLayout()
            
            card_id_input = QLineEdit()
            card_id_input.setPlaceholderText("Enter Card ID (e.g., A1B2C3D4)")
            student_id_input = QLineEdit()
            student_id_input.setPlaceholderText("Enter Student ID (e.g., 20210001)")
            
            card_type_group = QWidget()
            card_type_layout = QHBoxLayout(card_type_group)
            student_radio = QRadioButton("Student")
            student_radio.setChecked(True)
            admin_radio = QRadioButton("Admin")
            card_type_layout.addWidget(student_radio)
            card_type_layout.addWidget(admin_radio)
            
            submit_button = QPushButton("Add Card")
            submit_button.clicked.connect(
                lambda: self.submit_new_card(
                    dialog, 
                    card_id_input.text().strip(), 
                    student_id_input.text().strip(), 
                    "student" if student_radio.isChecked() else "admin"
                )
            )
            
            layout.addRow("Card ID:", card_id_input)
            layout.addRow("Student ID:", student_id_input)
            layout.addRow("Card Type:", card_type_group)
            layout.addWidget(submit_button)
            dialog.setLayout(layout)
            dialog.exec_()
        except Exception as e:
            logger.error(f"Error opening add new card dialog: {e}")
            QMessageBox.critical(self, "Error", "Failed to open card creation dialog.")
    
    def submit_new_card(self, dialog, card_id, student_id, card_type):
        try:
            if not card_id or not student_id:
                QMessageBox.warning(self, "Invalid Input", "Card ID and Student ID are required.")
                return
            success = add_new_card(card_id, student_id, card_type)
            if success:
                QMessageBox.information(self, "Success", "Card added successfully.")
                dialog.accept()
            else:
                QMessageBox.warning(self, "Error", "Failed to add card. Check IDs or if card exists.")
        except Exception as e:
            logger.error(f"Error adding new card: {e}")
            QMessageBox.critical(self, "Error", "An error occurred while adding the card.")
    
    def add_new_student(self):
        try:
            dialog = QDialog(self)
            dialog.setWindowTitle("Add New Student")
            layout = QFormLayout()
            
            student_id_input = QLineEdit()
            student_id_input.setPlaceholderText("Enter Student ID")
            name_input = QLineEdit()
            name_input.setPlaceholderText("Enter Student Name")
            faculty_input = QLineEdit()
            faculty_input.setPlaceholderText("Enter Faculty (optional)")
            program_input = QLineEdit()
            program_input.setPlaceholderText("Enter Program (optional)")
            level_input = QLineEdit()
            level_input.setPlaceholderText("Enter Level (optional)")
            
            submit_button = QPushButton("Add Student")
            submit_button.clicked.connect(
                lambda: self.submit_new_student(
                    dialog,
                    student_id_input.text().strip(),
                    name_input.text().strip(),
                    faculty_input.text().strip(),
                    program_input.text().strip(),
                    level_input.text().strip()
                )
            )
            
            layout.addRow("Student ID:", student_id_input)
            layout.addRow("Name:", name_input)
            layout.addRow("Faculty:", faculty_input)
            layout.addRow("Program:", program_input)
            layout.addRow("Level:", level_input)
            layout.addWidget(submit_button)
            
            dialog.setLayout(layout)
            dialog.exec_()
        except Exception as e:
            logger.error(f"Error opening add student dialog: {e}")
            QMessageBox.critical(self, "Error", "Failed to open student creation dialog.")
    
    def submit_new_student(self, dialog, student_id, name, faculty, program, level):
        try:
            if not student_id or not name:
                QMessageBox.warning(self, "Invalid Input", "Student ID and name are required.")
                return
            success = add_new_student(student_id, name, faculty, program, level)
            if success:
                QMessageBox.information(self, "Success", "Student added successfully.")
                dialog.accept()
            else:
                QMessageBox.warning(self, "Error", "Failed to add student. Check ID or database.")
        except Exception as e:
            logger.error(f"Error adding new student: {e}")
            QMessageBox.critical(self, "Error", "An error occurred while adding the student.")
    
    def run_diagnostics(self):
        try:
            diagnostics = []
            # Check hardware components
            diagnostics.append("NFC Reader: OK" if hardware_controller.pn532 else "NFC Reader: Failed")
            diagnostics.append("Servo Controller: OK" if hardware_controller.servo else "Servo Controller: Failed")
            diagnostics.append("Relay Controller: OK" if hardware_controller.RELAY_PIN else "Relay Controller: Failed")
            # Check database
            conn = get_db_connection()
            if conn:
                try:
                    conn.execute("SELECT 1")
                    diagnostics.append("Database Connection: OK")
                except Exception:
                    diagnostics.append("Database Connection: Failed")
                finally:
                    db_pool.return_connection(conn)
            else:
                diagnostics.append("Database Connection: Failed")
            # Check system resources
            cpu_usage = psutil.cpu_percent(interval=0.1)
            memory_usage = psutil.virtual_memory().percent
            diagnostics.append(f"CPU Usage: {cpu_usage:.1f}%")
            diagnostics.append(f"Memory Usage: {memory_usage:.1f}%")
            # Check network (basic)
            try:
                import socket
                socket.create_connection(("1.1.1.1", 53), timeout=2)
                diagnostics.append("Network: Connected")
            except Exception:
                diagnostics.append("Network: Disconnected")
            
            diagnostics_text = "\n".join(diagnostics)
            logger.info(f"\n--- System Diagnostics ---\n{diagnostics_text}\n--- End Diagnostics ---\n")
            QMessageBox.information(self, "System Diagnostics", diagnostics_text)
        except Exception as e:
            logger.error(f"Error running diagnostics: {e}")
            QMessageBox.critical(self, "Diagnostics Error", "Failed to complete system diagnostics.")

class SmartGateMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.card_check_thread = None
    
    def init_ui(self):
        self.setWindowTitle("Smart Gate System")
        self.setMinimumSize(800, 600)
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)
        
        self.main_screen = MainScreen(self)
        self.student_info_screen = StudentInfoScreen(self)
        self.admin_screen = AdminScreen(self)
        
        self.stacked_widget.addWidget(self.main_screen)
        self.stacked_widget.addWidget(self.student_info_screen)
        self.stacked_widget.addWidget(self.admin_screen)
        
        self.stacked_widget.setCurrentWidget(self.main_screen)
        
        self.start_card_check()
        logger.info("GUI initialized successfully")
    
    def show_main_screen(self):
        self.stacked_widget.setCurrentWidget(self.main_screen)
    
    def show_student_info(self, student_data):
        self.student_info_screen.update_student_info(student_data)
        self.stacked_widget.setCurrentWidget(self.student_info_screen)
    
    def show_admin_screen(self):
        self.stacked_widget.setCurrentWidget(self.admin_screen)
    
    def start_card_check(self):
        def check_cards():
            while True:
                try:
                    if hardware_controller.pn532:
                        uid = hardware_controller.pn532.read_passive_target(timeout=0.5)
                        if uid:
                            card_id = "".join([format(i, "02X") for i in uid])
                            student_data = get_student_by_card(card_id)
                            if student_data:
                                QTimer.singleShot(0, lambda: self.show_student_info(student_data))
                            time.sleep(1)
                    time.sleep(0.1)
                except Exception as e:
                    logger.error(f"Error in card check thread: {e}")
                    time.sleep(5)
        
        self.card_check_thread = threading.Thread(target=check_cards, daemon=True)
        self.card_check_thread.start()

def start_flask_server():
    try:
        logger.info("Starting Flask web server...")
        app.run(host="0.0.0.0", port=5000, threaded=True)
    except Exception as e:
        logger.error(f"Error starting Flask server: {e}")

if __name__ == "__main__":
    try:
        logger.info("Starting Smart Gate System...")
        
        # Setup database and assets
        setup_database()
        create_placeholder_images()
        create_flask_templates()
        
        # Start power management
        power_monitor_thread = threading.Thread(target=power_manager._monitor_resources, daemon=True)
        power_monitor_thread.start()
        
        # Start Flask server in a separate thread
        flask_thread = threading.Thread(target=start_flask_server, daemon=True)
        flask_thread.start()
        
        # Start PyQt5 GUI
        logger.info("Starting PyQt5 GUI...")
        qt_app = QApplication(sys.argv)
        window = SmartGateMainWindow()
        window.show()
        sys.exit(qt_app.exec_())
    except Exception as e:
        logger.critical(f"System failure: {e}")
        try:
            hardware_controller.cleanup()
            db_pool.close_all()
        except:
            pass
        sys.exit(1)
