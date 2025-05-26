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
    QMessageBox, QInputDialog, QRadioButton
)
from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtGui import QPixmap
from PyQt5.QtMultimedia import QSound

from flask import Flask, render_template, jsonify
from flask_caching import Cache
from werkzeug.middleware.proxy_fix import ProxyFix
import atexit

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='smart_gate.log'
)
logger = logging.getLogger('SmartGate')

# Database configuration
DB_PATH = os.path.join(os.path.dirname(__file__), "database", "smart_gate.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Pin configuration
PIN_CONFIG = {
    'RED_LED': 24,      # GPIO24 PIN18
    'GREEN_LED': 23,    # GPIO23 PIN16
    'RELAY': 17,        # GPIO17 PIN11
    'RED_BUZZER': 25,   # GPIO25 PIN22
    'GREEN_BUZZER': 26  # GPIO26 PIN37
}

class PowerManagement:
    """Manages system power consumption and performance optimization"""
    
    def __init__(self):
        self.power_mode = "normal"  # normal, power_save, performance
        self.cpu_threshold = 80  # CPU usage threshold for power saving
        self.memory_threshold = 70  # Memory usage threshold for power saving
        self.battery_threshold = 20  # Battery level threshold for power saving
        self.last_optimization = time.time()
        self.optimization_interval = 300  # 5 minutes
        
        # Start monitoring thread
        self.monitor_thread = threading.Thread(target=self._monitor_resources, daemon=True)
        self.monitor_thread.start()
    
    def _monitor_resources(self):
        """Monitor system resources and adjust power mode accordingly"""
        while True:
            try:
                # Get system metrics
                cpu_percent = psutil.cpu_percent(interval=1)
                memory_percent = psutil.virtual_memory().percent
                
                # Check if optimization is needed
                current_time = time.time()
                if current_time - self.last_optimization >= self.optimization_interval:
                    self._optimize_system(cpu_percent, memory_percent)
                    self.last_optimization = current_time
                
                # Adjust power mode based on conditions
                if (cpu_percent > self.cpu_threshold or 
                    memory_percent > self.memory_threshold):
                    self.set_power_mode("power_save")
                else:
                    self.set_power_mode("normal")
                
                time.sleep(60)  # Check every minute
                
            except Exception as e:
                print(f"Error in power management: {e}")
                time.sleep(60)
    
    def _optimize_system(self, cpu_percent, memory_percent):
        """Optimize system resources"""
        try:
            # Clear memory cache if memory usage is high
            if memory_percent > self.memory_threshold:
                self._clear_memory_cache()
            
            # Optimize database if needed
            self._optimize_database()
            
            # Log optimization
            print(f"System optimized - CPU: {cpu_percent}%, Memory: {memory_percent}%")
            
        except Exception as e:
            print(f"Error during system optimization: {e}")
    
    def _clear_memory_cache(self):
        """Clear system memory cache"""
        try:
            os.system('sync')  # Flush filesystem buffers
            os.system('echo 3 > /proc/sys/vm/drop_caches')  # Clear page cache, dentries and inodes
        except:
            pass
    
    def _optimize_database(self):
        """Optimize SQLite database"""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Run VACUUM to optimize database
            cursor.execute("VACUUM")
            
            # Analyze tables for better query planning
            cursor.execute("ANALYZE")
            
            conn.commit()
            conn.close()
        except:
            pass
    
    def set_power_mode(self, mode):
        """Set system power mode"""
        if mode != self.power_mode:
            self.power_mode = mode
            self._apply_power_mode()
    
    def _apply_power_mode(self):
        """Apply power mode settings"""
        if self.power_mode == "power_save":
            # Reduce CPU frequency
            os.system('echo powersave > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
            
            # Disable unnecessary services
            self._disable_non_essential_services()
            
        elif self.power_mode == "normal":
            # Restore normal settings
            os.system('echo ondemand > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
            
            # Enable essential services
            self._enable_essential_services()
    
    def _disable_non_essential_services(self):
        """Disable non-essential services in power save mode"""
        # Add service management logic here
        pass
    
    def _enable_essential_services(self):
        """Enable essential services in normal mode"""
        # Add service management logic here
        pass

# Create global power management instance
power_manager = PowerManagement()

class HardwareController:
    """Controls all hardware components connected to Raspberry Pi"""
    
    def __init__(self):
        try:
            # GPIO Setup
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            
            # Pin Definitions from config
            self.RED_LED_PIN = PIN_CONFIG['RED_LED']
            self.GREEN_LED_PIN = PIN_CONFIG['GREEN_LED']
            self.RELAY_PIN = PIN_CONFIG['RELAY']
            self.RED_BUZZER_PIN = PIN_CONFIG['RED_BUZZER']
            self.GREEN_BUZZER_PIN = PIN_CONFIG['GREEN_BUZZER']
            
            # Servo Parameters for Three-Arm Gate
            self.SERVO_CHANNEL = 0     # Channel 0 for the servo
            self.SERVO_MIN_PULSE = 500  # Minimum pulse length (µs)
            self.SERVO_MAX_PULSE = 2500 # Maximum pulse length (µs)
            self.SERVO_FREQ = 50       # 50Hz frequency
            self.SERVO_SPEED = 0.3     # Speed in seconds per 90 degrees
            self.SERVO_STEPS = 20      # Steps for smooth movement
            
            # Three-arm gate angles (120 degrees between each arm)
            self.SERVO_CLOSED_ANGLE = 0    # First arm position
            self.SERVO_OPEN_ANGLE = 120    # Rotate 120 degrees to next arm
            self.SERVO_FULL_ROTATION = 360 # Full rotation for reference
            
            # Setup GPIO pins
            GPIO.setup(self.RED_LED_PIN, GPIO.OUT)
            GPIO.setup(self.GREEN_LED_PIN, GPIO.OUT)
            GPIO.setup(self.RELAY_PIN, GPIO.OUT)
            GPIO.setup(self.RED_BUZZER_PIN, GPIO.OUT)
            GPIO.setup(self.GREEN_BUZZER_PIN, GPIO.OUT)
            
            # Initialize PWM for LEDs and Buzzers
            self.red_led_pwm = GPIO.PWM(self.RED_LED_PIN, 100)  # 100 Hz
            self.green_led_pwm = GPIO.PWM(self.GREEN_LED_PIN, 100)
            self.red_buzzer_pwm = GPIO.PWM(self.RED_BUZZER_PIN, 100)
            self.green_buzzer_pwm = GPIO.PWM(self.GREEN_BUZZER_PIN, 100)
            
            self.red_led_pwm.start(0)
            self.green_led_pwm.start(0)
            self.red_buzzer_pwm.start(0)
            self.green_buzzer_pwm.start(0)
            
            # Initialize I2C for PN532 NFC reader
            try:
                self.i2c = busio.I2C(board.SCL, board.SDA)
                self.pn532 = PN532_I2C(self.i2c, debug=False)
                logger.info("NFC reader initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize NFC reader: {e}")
                self.pn532 = None
            
            # Initialize servo controller
            try:
                self.servo_kit = ServoKit(channels=16, frequency=self.SERVO_FREQ)
                self.servo = self.servo_kit.servo[self.SERVO_CHANNEL]
                self.servo.set_pulse_width_range(self.SERVO_MIN_PULSE, self.SERVO_MAX_PULSE)
                logger.info("Servo controller initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize servo controller: {e}")
                self.servo = None
            
            # Set initial states
            self.gate_closed = True
            self.led_off()
            self.buzzer_off()
            self.relay_off()
            
            # Start NFC scanning thread
            self.nfc_thread = threading.Thread(target=self._scan_nfc, daemon=True)
            self.nfc_thread.start()
            logger.info("Hardware controller initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing hardware controller: {e}")
            raise
    
    def _scan_nfc(self):
        """Continuously scan for NFC cards"""
        while True:
            try:
                if not self.pn532:
                    logger.warning("NFC reader not initialized")
                    time.sleep(5)
                    continue
                
                # Check if a card is available to read
                uid = self.pn532.read_passive_target(timeout=0.5)
                
                if uid is not None:
                    # Convert UID to string
                    card_id = ''.join([hex(i)[2:].upper() for i in uid])
                    logger.info(f"Found card with UID: {card_id}")
                    
                    # Process card scan
                    self._process_card_scan(card_id)
                    
                    # Wait a bit before next scan
                    time.sleep(1)
            except Exception as e:
                logger.error(f"Error scanning NFC: {e}")
                time.sleep(5)  # Longer delay on error
    
    def _process_card_scan(self, card_id):
        """Process NFC card scan"""
        # Get student data
        student_data = get_student_by_card(card_id)
        
        if student_data and student_data.get("valid", False):
            # Valid card
            self.green_led_on()
            self.green_buzzer_on()
            self.open_gate()
            
            # Log successful entry
            log_entry(card_id, student_data.get("id", "UNKNOWN"), "success")
            
            # Close gate after delay
            threading.Timer(5, self.close_gate).start()
        else:
            # Invalid card
            self.red_led_on()
            self.red_buzzer_on()
            
            # Log failed entry
            log_entry(card_id, "UNKNOWN", "failure")
            
            # Trigger alarm
            self.trigger_alarm()
    
    def open_gate(self):
        """Open the gate using servo with controlled speed and increased torque"""
        if self.gate_closed:
            try:
                # Activate relay with delay to ensure stable power
                time.sleep(0.2)
                self.relay_on()
                time.sleep(0.2)  # Wait for relay to stabilize
                
                # Calculate steps for smooth movement with heavy load
                current_angle = self.servo.angle if self.servo.angle is not None else self.SERVO_CLOSED_ANGLE
                angle_step = (self.SERVO_OPEN_ANGLE - current_angle) / self.SERVO_STEPS
                delay = self.SERVO_SPEED / self.SERVO_STEPS
                
                # Move servo smoothly with increased torque
                for i in range(self.SERVO_STEPS):
                    target_angle = current_angle + (angle_step * (i + 1))
                    self.servo.angle = target_angle
                    time.sleep(delay)
                
                # Hold position briefly to ensure stability
                time.sleep(0.5)
                
                self.gate_closed = False
                print("Gate opened")
            except Exception as e:
                print(f"Error opening gate: {e}")
                # Ensure relay is off in case of error
                self.relay_off()
    
    def close_gate(self):
        """Close the gate using servo with controlled speed and increased torque"""
        if not self.gate_closed:
            try:
                # Calculate steps for smooth movement with heavy load
                current_angle = self.servo.angle if self.servo.angle is not None else self.SERVO_OPEN_ANGLE
                angle_step = (self.SERVO_CLOSED_ANGLE - current_angle) / self.SERVO_STEPS
                delay = self.SERVO_SPEED / self.SERVO_STEPS
                
                # Move servo smoothly with increased torque
                for i in range(self.SERVO_STEPS):
                    target_angle = current_angle + (angle_step * (i + 1))
                    self.servo.angle = target_angle
                    time.sleep(delay)
                
                # Hold position briefly to ensure stability
                time.sleep(0.5)
                
                # Deactivate relay after servo movement
                self.relay_off()
                
                self.gate_closed = True
                print("Gate closed")
            except Exception as e:
                print(f"Error closing gate: {e}")
                # Ensure relay is off in case of error
                self.relay_off()
    
    def green_led_on(self):
        """Turn on green LED"""
        GPIO.output(self.GREEN_LED_PIN, GPIO.HIGH)
    
    def green_led_off(self):
        """Turn off green LED"""
        GPIO.output(self.GREEN_LED_PIN, GPIO.LOW)
    
    def red_led_on(self):
        """Turn on red LED"""
        GPIO.output(self.RED_LED_PIN, GPIO.HIGH)
    
    def red_led_off(self):
        """Turn off red LED"""
        GPIO.output(self.RED_LED_PIN, GPIO.LOW)
    
    def led_off(self):
        """Turn off all LEDs"""
        self.green_led_off()
        self.red_led_off()
    
    def green_buzzer_on(self):
        """Turn on green buzzer"""
        self.green_buzzer_pwm.ChangeDutyCycle(50)  # 50% duty cycle
    
    def green_buzzer_off(self):
        """Turn off green buzzer"""
        self.green_buzzer_pwm.ChangeDutyCycle(0)
    
    def red_buzzer_on(self):
        """Turn on red buzzer"""
        self.red_buzzer_pwm.ChangeDutyCycle(50)  # 50% duty cycle
    
    def red_buzzer_off(self):
        """Turn off red buzzer"""
        self.red_buzzer_pwm.ChangeDutyCycle(0)
    
    def buzzer_off(self):
        """Turn off all buzzers"""
        self.green_buzzer_off()
        self.red_buzzer_off()
    
    def relay_on(self):
        """Turn on relay"""
        GPIO.output(self.RELAY_PIN, GPIO.HIGH)
    
    def relay_off(self):
        """Turn off relay"""
        GPIO.output(self.RELAY_PIN, GPIO.LOW)
    
    def trigger_alarm(self):
        """Trigger alarm sequence"""
        def alarm_sequence():
            for _ in range(3):  # 3 beeps
                self.red_buzzer_on()
                time.sleep(0.5)
                self.red_buzzer_off()
                time.sleep(0.5)
        
        threading.Thread(target=alarm_sequence, daemon=True).start()
    
    def cleanup(self):
        """Cleanup hardware resources"""
        try:
            logger.info("Starting hardware cleanup")
            
            # Stop NFC scanning thread
            if hasattr(self, 'nfc_thread'):
                self.nfc_thread.join(timeout=1)
            
            # Turn off all outputs
            self.led_off()
            self.buzzer_off()
            self.relay_off()
            
            # Stop PWM
            if hasattr(self, 'red_led_pwm'):
                self.red_led_pwm.stop()
            if hasattr(self, 'green_led_pwm'):
                self.green_led_pwm.stop()
            if hasattr(self, 'red_buzzer_pwm'):
                self.red_buzzer_pwm.stop()
            if hasattr(self, 'green_buzzer_pwm'):
                self.green_buzzer_pwm.stop()
            
            # Cleanup GPIO
            GPIO.cleanup()
            
            logger.info("Hardware cleanup completed successfully")
        except Exception as e:
            logger.error(f"Error during hardware cleanup: {e}")

# Create global hardware controller instance
hardware_controller = HardwareController()

# Gate Control and Alarm System
class GateController:
    """Controls the physical gate and alarm system"""
    
    def __init__(self):
        self.is_open = False
        self.is_alarm_active = False
        self.alarm_thread = None
        self.alarm_stop_event = threading.Event()
    
    def open_gate(self):
        """Open the gate"""
        if not self.is_open:
            hardware_controller.open_gate()
            self.is_open = True
    
    def close_gate(self):
        """Close the gate"""
        if self.is_open:
            hardware_controller.close_gate()
            self.is_open = False
    
    def trigger_alarm(self):
        """Trigger the alarm system"""
        if not self.is_alarm_active:
            self.is_alarm_active = True
            hardware_controller.trigger_alarm()
    
    def stop_alarm(self):
        """Stop the alarm system"""
        if self.is_alarm_active:
            self.is_alarm_active = False
            hardware_controller.buzzer_off()

# Create global gate controller instance
gate_controller = GateController()

# Ensure directories exist
os.makedirs("assets", exist_ok=True)
os.makedirs("database", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("static/css", exist_ok=True)
os.makedirs("static/js", exist_ok=True)

class DatabasePool:
    """Database connection pool for optimized database access"""
    
    def __init__(self, max_connections=5):
        self.max_connections = max_connections
        self.connections = []
        self.lock = threading.Lock()
        
        # Create initial connections
        for _ in range(max_connections):
            conn = self._create_connection()
            if conn:
                self.connections.append(conn)
        logger.info(f"Database pool initialized with {len(self.connections)} connections")
    
    def _create_connection(self):
        """Create a new database connection with optimized settings"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging for better concurrency
            conn.execute("PRAGMA synchronous=NORMAL")  # Faster writes with reasonable safety
            conn.execute("PRAGMA cache_size=-2000")  # Use 2MB of cache
            conn.execute("PRAGMA temp_store=MEMORY")  # Store temp tables and indices in memory
            conn.execute("PRAGMA mmap_size=30000000000")  # Use memory-mapped I/O
            conn.execute("PRAGMA page_size=4096")  # Optimal page size
            return conn
        except Exception as e:
            logger.error(f"Error creating database connection: {e}")
            return None
    
    def get_connection(self):
        """Get a connection from the pool"""
        with self.lock:
            if self.connections:
                conn = self.connections.pop()
                try:
                    # Test if connection is still valid
                    conn.execute("SELECT 1")
                    return conn
                except Exception:
                    # If connection is invalid, create a new one
                    logger.warning("Invalid connection found in pool, creating new connection")
                    return self._create_connection()
            return self._create_connection()
    
    def return_connection(self, conn):
        """Return a connection to the pool"""
        if conn is None:
            return
            
        with self.lock:
            try:
                # Test if connection is still valid
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
        """Close all connections in the pool"""
        with self.lock:
            for conn in self.connections:
                try:
                    conn.close()
                except Exception as e:
                    logger.error(f"Error closing connection: {e}")
            self.connections.clear()
            logger.info("All database connections closed")

# Create global database pool
db_pool = DatabasePool()

def get_db_connection():
    """Get a database connection from the pool"""
    return db_pool.get_connection()

def setup_database():
    """Initialize the SQLite database with required tables"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Create students table with optimized indexes
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
        
        # Create cards table with optimized indexes
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
        
        # Create entry_logs table with optimized indexes
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
        
        # Insert sample data for testing
        try:
            # Sample students
            cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                          ("20210001", "John Smith", "Engineering", "Computer Engineering", "3rd Year", "assets/student1.png"))
            cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                          ("20210002", "Sarah Johnson", "Science", "Physics", "2nd Year", "assets/student2.png"))
            cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                          ("20210003", "Mohammed Ali", "Medicine", "General Medicine", "4th Year", "assets/student3.png"))
            cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                          ("SECURITY001", "Security Staff", "Security", "Gate Security", "Staff", "assets/security_staff.png"))
            
            # Sample cards
            cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                          ("A1B2C3D4", "20210001", 1, "student"))
            cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                          ("E5F6G7H8", "20210002", 1, "student"))
            cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                          ("I9J0K1L2", "20210003", 1, "student"))
            cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                          ("ADMIN001", "SECURITY001", 1, "admin"))
            
            # Sample entry logs
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
        except sqlite3.IntegrityError:
            # Skip if data already exists
            pass
        
        conn.commit()
        
    except Exception as e:
        print(f"Error setting up database: {e}")
        conn.rollback()
    finally:
        db_pool.return_connection(conn)
    
    print("Database setup completed.")

# Database helper functions with connection pooling
def get_student_by_card(card_id):
    """Get student information by card ID"""
    conn = get_db_connection()
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
    finally:
        db_pool.return_connection(conn)

def log_entry(card_id, student_id, status, gate="Main Gate", entry_type="regular"):
    """Log an entry attempt"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute("""
        INSERT INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (card_id, student_id, timestamp, gate, status, entry_type))
        
        conn.commit()
        
        # Invalidate relevant caches
        invalidate_cache()
    finally:
        db_pool.return_connection(conn)

def add_new_card(card_id, student_id, card_type="student"):
    """Add a new card to the database"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
        INSERT INTO cards (card_id, student_id, is_active, card_type)
        VALUES (?, ?, 1, ?)
        """, (card_id, student_id, card_type))
        
        conn.commit()
        
        # Invalidate relevant caches
        invalidate_cache()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        db_pool.return_connection(conn)

def add_new_student(student_id, name, faculty="", program="", level="", image_path=""):
    """Add a new student to the database"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
        INSERT INTO students (id, name, faculty, program, level, image_path)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (student_id, name, faculty, program, level, image_path))
        
        conn.commit()
        
        # Invalidate relevant caches
        invalidate_cache()
        return True
    except sqlite3.IntegrityError:
        # Update existing student
        cursor.execute("""
        UPDATE students
        SET name = ?, faculty = ?, program = ?, level = ?, image_path = ?
        WHERE id = ?
        """, (name, faculty, program, level, image_path, student_id))
        
        conn.commit()
        
        # Invalidate relevant caches
        invalidate_cache()
        return True
    finally:
        db_pool.return_connection(conn)

def get_all_students():
    """Get all students from the database"""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
    SELECT s.id, s.name, s.faculty, s.program, s.level, s.image_path, c.card_id
    FROM students s
    LEFT JOIN cards c ON s.id = c.student_id
    ORDER BY s.name
    """)
    
    result = cursor.fetchall()
    return [dict(row) for row in result]

def get_recent_entries(limit=10):
    """Get recent entry logs"""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
    SELECT e.id, e.card_id, e.student_id, s.name as student_name, e.timestamp, e.gate, e.status, e.entry_type
    FROM entry_logs e
    LEFT JOIN students s ON e.student_id = s.id
    ORDER BY e.timestamp DESC
    LIMIT ?
    """, (limit,))
    
    result = cursor.fetchall()
    return [dict(row) for row in result]

def get_entry_stats():
    """Get entry statistics"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get today's date
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    
    # Get total entries
    cursor.execute("SELECT COUNT(*) FROM entry_logs")
    total_entries = cursor.fetchone()[0]
    
    # Get today's entries
    cursor.execute("SELECT COUNT(*) FROM entry_logs WHERE timestamp LIKE ?", (f"{today}%",))
    today_entries = cursor.fetchone()[0]
    
    # Get successful entries
    cursor.execute("SELECT COUNT(*) FROM entry_logs WHERE status = \"success\"")
    successful_entries = cursor.fetchone()[0]
    
    # Get failed entries
    cursor.execute("SELECT COUNT(*) FROM entry_logs WHERE status = \"failure\"")
    failed_entries = cursor.fetchone()[0]
    
    # Get visitor entries
    cursor.execute("SELECT COUNT(*) FROM entry_logs WHERE entry_type = \"visitor_access\"")
    visitor_entries = cursor.fetchone()[0]
    
    return {
        "total": total_entries,
        "today": today_entries,
        "successful": successful_entries,
        "failed": failed_entries,
        "visitor": visitor_entries
    }

# Create placeholder images if they don't exist
def create_placeholder_images():
    """Create placeholder images for testing"""
    # University logo placeholder
    if not os.path.exists("assets/university_logo_placeholder.png"):
        # Create a simple colored square as placeholder
        try:
            from PIL import Image, ImageDraw, ImageFont
            img = Image.new("RGB", (200, 200), color=(25, 25, 112))
            d = ImageDraw.Draw(img)
            d.rectangle([10, 10, 190, 190], outline=(255, 255, 255), width=2)
            d.text((40, 80), "University\nLogo", fill=(255, 255, 255))
            img.save("assets/university_logo_placeholder.png")
        except ImportError:
            print("PIL/Pillow not found. Cannot create placeholder images.")
    
    # Student placeholders
    for i in range(1, 4):
        if not os.path.exists(f"assets/student{i}.png"):
            try:
                from PIL import Image, ImageDraw
                img = Image.new("RGB", (200, 200), color=(200, 200, 200))
                d = ImageDraw.Draw(img)
                d.rectangle([10, 10, 190, 190], outline=(100, 100, 100), width=2)
                d.text((50, 90), f"Student {i}", fill=(50, 50, 50))
                img.save(f"assets/student{i}.png")
            except ImportError:
                pass # Ignore if PIL not found
    
    # Security staff placeholder
    if not os.path.exists("assets/security_staff.png"):
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (200, 200), color=(50, 50, 50))
            d = ImageDraw.Draw(img)
            d.rectangle([10, 10, 190, 190], outline=(200, 200, 200), width=2)
            d.text((40, 90), "Security Staff", fill=(200, 200, 200))
            img.save("assets/security_staff.png")
        except ImportError:
            pass # Ignore if PIL not found

# Create Flask templates
def create_flask_templates():
    """Create Flask templates for the web interface"""
    # Create index.html
    if not os.path.exists("templates/index.html"):
        with open("templates/index.html", "w") as f:
            f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart Gate Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="{{ url_for("static", filename="css/style.css") }}">
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
                                        <!-- Entries will be loaded here -->
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="card">
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
                        <!-- Students will be loaded here -->
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
                        <!-- Entries will be loaded here -->
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
                        <h3 id="stats-failed-entries">0</h3>
                        <p>Failed</p>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="{{ url_for("static", filename="js/script.js") }}"></script>
</body>
</html>
""")

    # Create static/css/style.css
    if not os.path.exists("static/css/style.css"):
        with open("static/css/style.css", "w") as f:
            f.write("""body {
    font-family: sans-serif;
}

.navbar {
    margin-bottom: 20px;
}

.section {
    display: none;
}

.section.active {
    display: block;
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

/* Table styles */
.table-responsive {
    max-height: 400px;
    overflow-y: auto;
}""")

    # Create static/js/script.js
    if not os.path.exists("static/js/script.js"):
        with open("static/js/script.js", "w") as f:
            f.write("""document.addEventListener("DOMContentLoaded", function() {
    const navLinks = document.querySelectorAll(".navbar-nav .nav-link");
    const sections = document.querySelectorAll(".section");

    // Function to show a section
    function showSection(targetId) {
        sections.forEach(section => {
            section.classList.remove("active");
        });
        const targetSection = document.getElementById(targetId);
        if (targetSection) {
            targetSection.classList.add("active");
        }
    }

    // Handle navigation clicks
    navLinks.forEach(link => {
        link.addEventListener("click", function(event) {
            event.preventDefault();
            const targetId = this.getAttribute("href").substring(1);
            
            // Update active link
            navLinks.forEach(nav => nav.classList.remove("active"));
            this.classList.add("active");
            
            // Show target section
            showSection(targetId);
        });
    });

    // Function to fetch and update data
    function fetchData(url, callback) {
        fetch(url)
            .then(response => response.json())
            .then(data => {
                if (data.status === "success") {
                    callback(data.data);
                } else {
                    console.error("API Error:", data.message);
                }
            })
            .catch(error => console.error("Fetch Error:", error));
    }

    // Update recent entries
    function updateRecentEntries(entries) {
        const tbody = document.getElementById("recent-entries");
        tbody.innerHTML = ""; // Clear existing
        entries.forEach(entry => {
            const row = `<tr>
                <td>${new Date(entry.timestamp).toLocaleTimeString()}</td>
                <td>${entry.student_name || entry.student_id || "Unknown"}</td>
                <td><span class="badge bg-${entry.status === "success" ? "success" : "danger"}">${entry.status}</span></td>
            </tr>`;
            tbody.innerHTML += row;
        });
    }

    // Update students table
    function updateStudentsTable(students) {
        const tbody = document.getElementById("students-table");
        tbody.innerHTML = ""; // Clear existing
        students.forEach(student => {
            const row = `<tr>
                <td>${student.id}</td>
                <td>${student.name}</td>
                <td>${student.faculty || "N/A"}</td>
                <td>${student.program || "N/A"}</td>
                <td>${student.level || "N/A"}</td>
                <td>${student.card_id || "N/A"}</td>
            </tr>`;
            tbody.innerHTML += row;
        });
    }

    // Update entries table
    function updateEntriesTable(entries) {
        const tbody = document.getElementById("entries-table");
        tbody.innerHTML = ""; // Clear existing
        entries.forEach(entry => {
            const row = `<tr>
                <td>${entry.timestamp}</td>
                <td>${entry.card_id}</td>
                <td>${entry.student_name || entry.student_id || "Unknown"}</td>
                <td>${entry.gate}</td>
                <td><span class="badge bg-${entry.status === "success" ? "success" : "danger"}">${entry.status}</span></td>
                <td>${entry.entry_type}</td>
            </tr>`;
            tbody.innerHTML += row;
        });
    }

    // Update statistics
    function updateStats(stats) {
        document.getElementById("today-entries").textContent = stats.today;
        document.getElementById("successful-entries").textContent = stats.successful;
        document.getElementById("failed-entries").textContent = stats.failed;
        document.getElementById("visitor-entries").textContent = stats.visitor;
        
        document.getElementById("total-entries").textContent = stats.total;
        document.getElementById("stats-today-entries").textContent = stats.today;
        document.getElementById("stats-successful-entries").textContent = stats.successful;
        document.getElementById("stats-failed-entries").textContent = stats.failed;
    }

    // Initial data load
    fetchData("/api/recent_entries", updateRecentEntries);
    fetchData("/api/students", updateStudentsTable);
    fetchData("/api/entries", updateEntriesTable);
    fetchData("/api/stats", updateStats);

    // Set interval for updates (e.g., every 10 seconds)
    setInterval(() => {
        fetchData("/api/recent_entries", updateRecentEntries);
        fetchData("/api/stats", updateStats);
    }, 10000);
});
""")
    
    print("Flask templates created successfully.")

# Flask app with optimizations
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configure Flask-Caching
cache = Cache(app, config={
    'CACHE_TYPE': 'simple',
    'CACHE_DEFAULT_TIMEOUT': 300
})

@app.route("/")
@cache.cached(timeout=300)  # Cache for 5 minutes
def index():
    """Main page"""
    return render_template("index.html")

@app.route("/api/recent_entries")
@cache.cached(timeout=30)  # Cache for 30 seconds
def api_recent_entries():
    """API endpoint for recent entries data"""
    entries = get_recent_entries(5)
    return jsonify({"status": "success", "data": entries})

@app.route("/api/students")
@cache.cached(timeout=300)  # Cache for 5 minutes
def api_students():
    """API endpoint for students data"""
    students = get_all_students()
    return jsonify({"status": "success", "data": students})

@app.route("/api/entries")
@cache.cached(timeout=30)  # Cache for 30 seconds
def api_entries():
    """API endpoint for entry logs data"""
    conn = get_db_connection()
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
        SELECT e.id, e.card_id, e.student_id, s.name as student_name, e.timestamp, e.gate, e.status, e.entry_type
        FROM entry_logs e
        LEFT JOIN students s ON e.student_id = s.id
        ORDER BY e.timestamp DESC
        LIMIT 100
        """)
        
        entries = [dict(row) for row in cursor.fetchall()]
        return jsonify({"status": "success", "data": entries})
    finally:
        db_pool.return_connection(conn)

@app.route("/api/stats")
@cache.cached(timeout=30)  # Cache for 30 seconds
def api_stats():
    """API endpoint for statistics data"""
    stats = get_entry_stats()
    return jsonify({"status": "success", "data": stats})

# Add cache invalidation for data changes
def invalidate_cache():
    """Invalidate all cached data"""
    cache.clear()

# Modify database functions to invalidate cache
def add_new_card(card_id, student_id, card_type="student"):
    """Add a new card to the database"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
        INSERT INTO cards (card_id, student_id, is_active, card_type)
        VALUES (?, ?, 1, ?)
        """, (card_id, student_id, card_type))
        
        conn.commit()
        
        # Invalidate relevant caches
        invalidate_cache()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        db_pool.return_connection(conn)

def add_new_student(student_id, name, faculty="", program="", level="", image_path=""):
    """Add a new student to the database"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
        INSERT INTO students (id, name, faculty, program, level, image_path)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (student_id, name, faculty, program, level, image_path))
        
        conn.commit()
        
        # Invalidate relevant caches
        invalidate_cache()
        return True
    except sqlite3.IntegrityError:
        # Update existing student
        cursor.execute("""
        UPDATE students
        SET name = ?, faculty = ?, program = ?, level = ?, image_path = ?
        WHERE id = ?
        """, (name, faculty, program, level, image_path, student_id))
        
        conn.commit()
        
        # Invalidate relevant caches
        invalidate_cache()
        return True
    finally:
        db_pool.return_connection(conn)

class MainWindow(QMainWindow):
    """Main application window with simplified single-screen interface"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Gate Control System")
        self.setGeometry(100, 100, 800, 600)
        
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        
        # Status Section
        status_group = QFrame()
        status_group.setFrameStyle(QFrame.StyledPanel)
        status_layout = QVBoxLayout(status_group)
        
        # Status header
        status_header = QLabel("System Status")
        status_header.setStyleSheet("font-size: 18px; font-weight: bold; color: #1A237E;")
        status_layout.addWidget(status_header)
        
        # Gate status
        self.gate_status = QLabel("Gate: Closed")
        self.gate_status.setStyleSheet("font-size: 16px; color: #D32F2F;")
        status_layout.addWidget(self.gate_status)
        
        # Last card scan
        self.last_scan = QLabel("Last Scan: None")
        self.last_scan.setStyleSheet("font-size: 16px;")
        status_layout.addWidget(self.last_scan)
        
        # Current time
        self.time_label = QLabel()
        self.time_label.setStyleSheet("font-size: 16px;")
        self.update_time()
        status_layout.addWidget(self.time_label)
        
        # Timer for updating time
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_time)
        self.timer.start(1000)
        
        main_layout.addWidget(status_group)
        
        # Control Section
        control_group = QFrame()
        control_group.setFrameStyle(QFrame.StyledPanel)
        control_layout = QVBoxLayout(control_group)
        
        # Control header
        control_header = QLabel("Gate Controls")
        control_header.setStyleSheet("font-size: 18px; font-weight: bold; color: #1A237E;")
        control_layout.addWidget(control_header)
        
        # Gate control buttons
        gate_buttons = QHBoxLayout()
        
        self.open_button = QPushButton("Open Gate")
        self.open_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 5px;
                padding: 10px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
        """)
        self.open_button.clicked.connect(self.open_gate)
        
        self.close_button = QPushButton("Close Gate")
        self.close_button.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border-radius: 5px;
                padding: 10px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
        """)
        self.close_button.clicked.connect(self.close_gate)
        
        gate_buttons.addWidget(self.open_button)
        gate_buttons.addWidget(self.close_button)
        control_layout.addLayout(gate_buttons)
        
        # Test buttons
        test_buttons = QHBoxLayout()
        
        self.test_valid_btn = QPushButton("Test Valid Card")
        self.test_valid_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border-radius: 5px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """)
        self.test_valid_btn.clicked.connect(lambda: self.test_card("A1B2C3D4"))
        
        self.test_admin_btn = QPushButton("Test Admin Card")
        self.test_admin_btn.setStyleSheet("""
            QPushButton {
                background-color: #9C27B0;
                color: white;
                border-radius: 5px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
        """)
        self.test_admin_btn.clicked.connect(lambda: self.test_card("ADMIN001"))
        
        self.test_invalid_btn = QPushButton("Test Invalid Card")
        self.test_invalid_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                border-radius: 5px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
        """)
        self.test_invalid_btn.clicked.connect(lambda: self.test_card("INVALID"))
        
        test_buttons.addWidget(self.test_valid_btn)
        test_buttons.addWidget(self.test_admin_btn)
        test_buttons.addWidget(self.test_invalid_btn)
        control_layout.addLayout(test_buttons)
        
        # Admin functions
        admin_buttons = QHBoxLayout()
        
        self.add_student_btn = QPushButton("Add Student")
        self.add_student_btn.setStyleSheet("""
            QPushButton {
                background-color: #009688;
                color: white;
                border-radius: 5px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #00796B;
            }
        """)
        self.add_student_btn.clicked.connect(self.add_student)
        
        self.view_logs_btn = QPushButton("View Logs")
        self.view_logs_btn.setStyleSheet("""
            QPushButton {
                background-color: #607D8B;
                color: white;
                border-radius: 5px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #455A64;
            }
        """)
        self.view_logs_btn.clicked.connect(self.view_logs)
        
        admin_buttons.addWidget(self.add_student_btn)
        admin_buttons.addWidget(self.view_logs_btn)
        control_layout.addLayout(admin_buttons)
        
        main_layout.addWidget(control_group)
        
        # Log Section
        log_group = QFrame()
        log_group.setFrameStyle(QFrame.StyledPanel)
        log_layout = QVBoxLayout(log_group)
        
        # Log header
        log_header = QLabel("Recent Activity")
        log_header.setStyleSheet("font-size: 18px; font-weight: bold; color: #1A237E;")
        log_layout.addWidget(log_header)
        
        # Log display
        self.log_display = QLabel()
        self.log_display.setStyleSheet("""
            QLabel {
                background-color: #F5F5F5;
                border: 1px solid #BDBDBD;
                border-radius: 5px;
                padding: 10px;
                font-family: monospace;
            }
        """)
        self.log_display.setWordWrap(True)
        log_layout.addWidget(self.log_display)
        
        main_layout.addWidget(log_group)
        
        # Initialize hardware controller
        self.hardware_controller = hardware_controller
        self.update_log("System initialized")
    
    def update_time(self):
        """Update the time display"""
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.time_label.setText(f"Current Time: {current_time}")
    
    def update_log(self, message):
        """Update the log display"""
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_display.setText(f"[{current_time}] {message}\n" + self.log_display.text())
    
    def open_gate(self):
        """Open the gate"""
        try:
            self.hardware_controller.open_gate()
            self.gate_status.setText("Gate: Open")
            self.gate_status.setStyleSheet("font-size: 16px; color: #4CAF50;")
            self.update_log("Gate opened")
        except Exception as e:
            self.update_log(f"Error opening gate: {e}")
    
    def close_gate(self):
        """Close the gate"""
        try:
            self.hardware_controller.close_gate()
            self.gate_status.setText("Gate: Closed")
            self.gate_status.setStyleSheet("font-size: 16px; color: #D32F2F;")
            self.update_log("Gate closed")
        except Exception as e:
            self.update_log(f"Error closing gate: {e}")
    
    def test_card(self, card_id):
        """Test card scanning"""
        try:
            student_data = get_student_by_card(card_id)
            if student_data and student_data.get("valid", False):
                self.last_scan.setText(f"Last Scan: {student_data.get('name', 'Unknown')}")
                self.update_log(f"Valid card scanned: {student_data.get('name', 'Unknown')}")
                self.open_gate()
            else:
                self.last_scan.setText("Last Scan: Invalid Card")
                self.update_log("Invalid card scanned")
                self.hardware_controller.trigger_alarm()
        except Exception as e:
            self.update_log(f"Error testing card: {e}")
    
    def add_student(self):
        """Show dialog to add new student"""
        dialog = AddEntryDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            data = dialog.get_data()
            try:
                if add_new_student(
                    data["student_id"],
                    data["name"],
                    data["faculty"],
                    data["program"],
                    data["level"],
                    data["image_path"]
                ) and add_new_card(
                    data["card_id"],
                    data["student_id"],
                    data["card_type"]
                ):
                    self.update_log(f"Added new student: {data['name']}")
                else:
                    self.update_log("Failed to add student")
            except Exception as e:
                self.update_log(f"Error adding student: {e}")
    
    def view_logs(self):
        """Show recent entry logs"""
        try:
            entries = get_recent_entries(10)
            log_text = "Recent Entry Logs:\n"
            for entry in entries:
                log_text += f"{entry['timestamp']} - {entry['student_name']} - {entry['status']}\n"
            self.update_log(log_text)
        except Exception as e:
            self.update_log(f"Error viewing logs: {e}")

# Main execution
if __name__ == "__main__":
    try:
        logger.info("Starting Smart Gate System")
        
        # Setup database and create placeholders
        setup_database()
        create_placeholder_images()
        create_flask_templates()
        
        # Start Flask app in a separate thread
        def run_flask():
            try:
                app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
            except Exception as e:
                logger.error(f"Error running Flask app: {e}")
                
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        # Start PyQt5 GUI
        app_gui = QApplication(sys.argv)
        main_window = MainWindow()
        main_window.show()
        
        # Set up cleanup on exit
        def cleanup():
            logger.info("Starting system cleanup")
            try:
                # Cleanup hardware
                if 'hardware_controller' in globals():
                    hardware_controller.cleanup()
                
                # Cleanup database
                if 'db_pool' in globals():
                    db_pool.close_all()
                
                # Cleanup power management
                if 'power_manager' in globals():
                    power_manager.cleanup()
                
                logger.info("System cleanup completed")
            except Exception as e:
                logger.error(f"Error during system cleanup: {e}")
        
        # Register cleanup function
        atexit.register(cleanup)
        
        # Run the application
        sys.exit(app_gui.exec_())
        
    except Exception as e:
        logger.error(f"Fatal error in main execution: {e}")
        sys.exit(1)
    finally:
        # Ensure cleanup is called
        cleanup()
