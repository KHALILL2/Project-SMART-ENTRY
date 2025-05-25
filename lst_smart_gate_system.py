#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import random
import sqlite3
import datetime
import threading
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QFrame, QStackedWidget, QDialog, QLineEdit,
    QMessageBox, QInputDialog, QRadioButton
)
from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtGui import QPixmap
from PyQt5.QtMultimedia import QSound

from flask import Flask, render_template, jsonify

# Gate Control and Alarm System
class GateController:
    """Controls the physical gate and alarm system"""
    
    def __init__(self):
        self.is_open = False
        self.is_alarm_active = False
        self.alarm_thread = None
        self.alarm_stop_event = threading.Event()
        
        # Load alarm sound
        self.alarm_sound = QSound("assets/alarm.wav")
        
        # Create alarm sound if it doesn't exist
        if not os.path.exists("assets/alarm.wav"):
            self.create_alarm_sound()
    
    def create_alarm_sound(self):
        """Create a simple alarm sound file"""
        try:
            from scipy.io import wavfile
            import numpy as np
            
            # Create a simple alarm sound (1 second of alternating high/low tones)
            sample_rate = 44100
            duration = 1.0
            t = np.linspace(0, duration, int(sample_rate * duration), False)
            
            # Generate alternating tones
            tone1 = np.sin(2 * np.pi * 880 * t)  # A5 note
            tone2 = np.sin(2 * np.pi * 440 * t)  # A4 note
            
            # Combine tones
            alarm_sound = np.zeros_like(t)
            for i in range(len(t)):
                if i % 2 == 0:
                    alarm_sound[i] = tone1[i]
                else:
                    alarm_sound[i] = tone2[i]
            
            # Normalize and convert to 16-bit PCM
            alarm_sound = np.int16(alarm_sound * 32767)
            
            # Save the sound file
            os.makedirs("assets", exist_ok=True)
            wavfile.write("assets/alarm.wav", sample_rate, alarm_sound)
            
        except ImportError:
            print("Warning: Could not create alarm sound file. Install scipy for sound generation.")
    
    def open_gate(self):
        """Open the gate"""
        if not self.is_open:
            print("Opening gate...")
            # In a real implementation, this would control a servo or motor
            self.is_open = True
            # Simulate gate opening delay
            time.sleep(1)
            print("Gate opened")
    
    def close_gate(self):
        """Close the gate"""
        if self.is_open:
            print("Closing gate...")
            # In a real implementation, this would control a servo or motor
            self.is_open = False
            # Simulate gate closing delay
            time.sleep(1)
            print("Gate closed")
    
    def trigger_alarm(self):
        """Trigger the alarm system"""
        if not self.is_alarm_active:
            self.is_alarm_active = True
            self.alarm_stop_event.clear()
            self.alarm_thread = threading.Thread(target=self._alarm_loop, daemon=True)
            self.alarm_thread.start()
            print("Alarm triggered!")
    
    def stop_alarm(self):
        """Stop the alarm system"""
        if self.is_alarm_active:
            self.is_alarm_active = False
            self.alarm_stop_event.set()
            if self.alarm_thread:
                self.alarm_thread.join(timeout=1)
            print("Alarm stopped")
    
    def _alarm_loop(self):
        """Alarm sound loop"""
        while not self.alarm_stop_event.is_set():
            self.alarm_sound.play()
            time.sleep(1)  # Wait for sound to finish
            if self.alarm_stop_event.is_set():
                break
            time.sleep(0.1)  # Small pause between sounds

# Ensure directories exist
os.makedirs("assets", exist_ok=True)
os.makedirs("database", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("static/css", exist_ok=True)
os.makedirs("static/js", exist_ok=True)

# Create global gate controller instance
gate_controller = GateController()

# Database setup
DB_PATH = "database/smart_gate.db"

def setup_database():
    """Initialize the SQLite database with required tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create students table
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
    
    # Create cards table with card_type field
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cards (
        card_id TEXT PRIMARY KEY,
        student_id TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        card_type TEXT DEFAULT "student",
        FOREIGN KEY (student_id) REFERENCES students(id)
    )
    """)
    
    # Create entry_logs table
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
    conn.close()
    
    print("Database setup completed.")

# Database helper functions
def get_student_by_card(card_id):
    """Get student information by card ID"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
    SELECT s.id, s.name, s.faculty, s.program, s.level, s.image_path, c.is_active, c.card_type
    FROM students s
    JOIN cards c ON s.id = c.student_id
    WHERE c.card_id = ?
    """, (card_id,))
    
    result = cursor.fetchone()
    conn.close()
    
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

def log_entry(card_id, student_id, status, gate="Main Gate", entry_type="regular"):
    """Log an entry attempt"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("""
    INSERT INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (card_id, student_id, timestamp, gate, status, entry_type))
    
    conn.commit()
    conn.close()

def add_new_card(card_id, student_id, card_type="student"):
    """Add a new card to the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
        INSERT INTO cards (card_id, student_id, is_active, card_type)
        VALUES (?, ?, 1, ?)
        """, (card_id, student_id, card_type))
        
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def add_new_student(student_id, name, faculty="", program="", level="", image_path=""):
    """Add a new student to the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
        INSERT INTO students (id, name, faculty, program, level, image_path)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (student_id, name, faculty, program, level, image_path))
        
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        # Update existing student
        cursor.execute("""
        UPDATE students
        SET name = ?, faculty = ?, program = ?, level = ?, image_path = ?
        WHERE id = ?
        """, (name, faculty, program, level, image_path, student_id))
        
        conn.commit()
        conn.close()
        return True

def get_all_students():
    """Get all students from the database"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
    SELECT s.id, s.name, s.faculty, s.program, s.level, s.image_path, c.card_id
    FROM students s
    LEFT JOIN cards c ON s.id = c.student_id
    ORDER BY s.name
    """)
    
    result = cursor.fetchall()
    conn.close()
    
    students = [dict(row) for row in result]
    return students

def get_recent_entries(limit=10):
    """Get recent entry logs"""
    conn = sqlite3.connect(DB_PATH)
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
    conn.close()
    
    entries = [dict(row) for row in result]
    return entries

def get_entry_stats():
    """Get entry statistics"""
    conn = sqlite3.connect(DB_PATH)
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
    
    conn.close()
    
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

# Flask app
app = Flask(__name__)

@app.route("/")
def index():
    """Main page"""
    return render_template("index.html")

@app.route("/api/recent_entries")
def api_recent_entries():
    """API endpoint for recent entries data"""
    entries = get_recent_entries(5)
    return jsonify({"status": "success", "data": entries})

@app.route("/api/students")
def api_students():
    """API endpoint for students data"""
    students = get_all_students()
    return jsonify({"status": "success", "data": students})

@app.route("/api/entries")
def api_entries():
    """API endpoint for entry logs data"""
    conn = sqlite3.connect(DB_PATH)
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
    conn.close()
    
    return jsonify({"status": "success", "data": entries})

@app.route("/api/stats")
def api_stats():
    """API endpoint for statistics data"""
    stats = get_entry_stats()
    return jsonify({"status": "success", "data": stats})

# PyQt5 GUI Classes
class MainScreen(QWidget):
    """Main screen for the smart gate"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.init_ui()
    
    def init_ui(self):
        """Setup the user interface"""
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)
        
        # Header with date and time
        header_layout = QHBoxLayout()
        
        # Admin button
        admin_button = QPushButton("Admin")
        admin_button.setFixedSize(100, 40)
        admin_button.setStyleSheet("""
            QPushButton {
                background-color: #1A237E;
                color: white;
                border-radius: 5px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #0D47A1;
            }
            QPushButton:pressed {
                background-color: #0A2472;
            }
        """)
        admin_button.clicked.connect(self.show_admin_screen)
        
        # Date and time
        self.datetime_label = QLabel()
        self.datetime_label.setAlignment(Qt.AlignRight)
        self.datetime_label.setStyleSheet("font-size: 16px; color: #1A237E;")
        self.update_datetime()
        
        # Timer to update date and time
        self.datetime_timer = QTimer(self)
        self.datetime_timer.timeout.connect(self.update_datetime)
        self.datetime_timer.start(1000)  # Update every second
        
        header_layout.addWidget(admin_button)
        header_layout.addStretch()
        header_layout.addWidget(self.datetime_label)
        
        # University logo
        logo_layout = QVBoxLayout()
        logo_layout.setAlignment(Qt.AlignCenter)
        
        logo_frame = QFrame()
        logo_frame.setFrameShape(QFrame.Box)
        logo_frame.setFrameShadow(QFrame.Raised)
        logo_frame.setLineWidth(2)
        logo_frame.setStyleSheet("border: 2px solid #1A237E;")
        logo_frame.setFixedSize(200, 200)
        
        logo_inner_layout = QVBoxLayout(logo_frame)
        logo_label = QLabel()
        logo_label.setAlignment(Qt.AlignCenter)
        
        # Load logo image
        logo_path = "assets/university_logo_placeholder.png"
        pixmap = QPixmap(logo_path)
        if pixmap.isNull():
            # If image not found, use text instead
            logo_label.setText("University Logo")
            logo_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #1A237E;")
        else:
            pixmap = pixmap.scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(pixmap)
        
        logo_inner_layout.addWidget(logo_label)
        logo_layout.addWidget(logo_frame)
        
        # Welcome message
        welcome_label = QLabel("Welcome to Smart Entry Gate")
        welcome_label.setAlignment(Qt.AlignCenter)
        welcome_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #1A237E; margin: 20px 0;")
        
        # Instructions
        instructions_frame = QFrame()
        instructions_frame.setFrameShape(QFrame.Box)
        instructions_frame.setFrameShadow(QFrame.Sunken)
        instructions_frame.setLineWidth(1)
        instructions_frame.setStyleSheet("border: 1px solid #BDBDBD; background-color: #E8EAF6; padding: 10px;")
        
        instructions_layout = QVBoxLayout(instructions_frame)
        
        instruction_title = QLabel("Instructions:")
        instruction_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1A237E;")
        
        instruction_text = QLabel("Please scan your NFC card to enter")
        instruction_text.setAlignment(Qt.AlignCenter)
        instruction_text.setStyleSheet("font-size: 16px; color: #1A237E; margin: 10px 0;")
        
        instructions_layout.addWidget(instruction_title)
        instructions_layout.addWidget(instruction_text)
        
        # Add elements to main layout
        main_layout.addLayout(header_layout)
        main_layout.addLayout(logo_layout)
        main_layout.addWidget(welcome_label)
        main_layout.addStretch()
        main_layout.addWidget(instructions_frame)
        
        self.setLayout(main_layout)
    
    def update_datetime(self):
        """Update date and time display"""
        now = datetime.datetime.now()
        date_str = now.strftime("%Y/%m/%d")
        time_str = now.strftime("%H:%M:%S")
        self.datetime_label.setText(f"{date_str} {time_str}")
    
    def show_admin_screen(self):
        """Show the admin screen"""
        if self.parent:
            self.parent.show_admin_screen()

class StudentInfoScreen(QWidget):
    """Student information display screen"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.init_ui()
        
        # Timer for automatic return to main screen
        self.return_timer = QTimer(self)
        self.return_timer.timeout.connect(self.return_to_main)
        self.return_timer.setSingleShot(True)
        
        # For visitor access mode
        self.visitor_mode = False
    
    def init_ui(self):
        """Setup the user interface"""
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        
        # Header bar
        header_layout = QHBoxLayout()
        
        # Back button
        back_button = QPushButton("Back")
        back_button.setFixedSize(100, 40)
        back_button.setStyleSheet("""
            QPushButton {
                background-color: #607D8B;
                color: white;
                border-radius: 5px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #455A64;
            }
            QPushButton:pressed {
                background-color: #37474F;
            }
        """)
        back_button.clicked.connect(self.return_to_main)
        
        # Date and time
        self.datetime_label = QLabel()
        self.datetime_label.setAlignment(Qt.AlignRight)
        self.datetime_label.setStyleSheet("font-size: 16px; color: #1A237E;")
        self.update_datetime()
        
        # Timer to update date and time
        self.datetime_timer = QTimer(self)
        self.datetime_timer.timeout.connect(self.update_datetime)
        self.datetime_timer.start(1000)  # Update every second
        
        header_layout.addWidget(back_button)
        header_layout.addStretch()
        header_layout.addWidget(self.datetime_label)
        
        # Student information content
        content_layout = QHBoxLayout()
        
        # Student image
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
        
        # Student information
        info_layout = QVBoxLayout()
        info_layout.setSpacing(10)
        
        # Create information fields
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
        
        # Entry status
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
        
        # Visitor access section (initially hidden)
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
            QPushButton {
                background-color: #2196F3;
                color: white;
                border-radius: 5px;
                font-size: 16px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #1565C0;
            }
        """)
        self.grant_access_button.clicked.connect(self.grant_visitor_access)
        
        visitor_layout.addWidget(visitor_title)
        visitor_layout.addWidget(visitor_instruction)
        visitor_layout.addWidget(self.grant_access_button)
        
        # Add elements to main layout
        main_layout.addLayout(header_layout)
        main_layout.addSpacing(10)
        main_layout.addLayout(content_layout)
        main_layout.addStretch()
        main_layout.addWidget(self.status_frame)
        main_layout.addWidget(self.visitor_frame)
        
        self.setLayout(main_layout)
    
    def create_info_field(self, label_text):
        """Create an information field"""
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
        """Update date and time display"""
        now = datetime.datetime.now()
        date_str = now.strftime("%Y/%m/%d")
        time_str = now.strftime("%H:%M:%S")
        self.datetime_label.setText(f"{date_str} {time_str}")
    
    def update_student_info(self, student_data):
        """Update student information display"""
        # Store current student data
        self.current_student_data = student_data
        
        # Check if this is an admin card
        is_admin_card = student_data.get("card_type") == "admin"
        self.visitor_mode = is_admin_card
        
        # Update image
        image_path = student_data.get("image_path", "assets/student_placeholder.png")
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            # If image not found, use text instead
            self.student_image_label.setText("No Image")
            self.student_image_label.setStyleSheet("font-size: 16px;")
        else:
            pixmap = pixmap.scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.student_image_label.setPixmap(pixmap)
        
        # Update information
        self.name_label.layout().itemAt(1).widget().setText(student_data.get("name", ""))
        self.id_label.layout().itemAt(1).widget().setText(student_data.get("id", ""))
        self.faculty_label.layout().itemAt(1).widget().setText(student_data.get("faculty", ""))
        self.program_label.layout().itemAt(1).widget().setText(student_data.get("program", ""))
        self.level_label.layout().itemAt(1).widget().setText(student_data.get("level", ""))
        
        # Update entry status
        is_valid = student_data.get("valid", False)
        
        if is_valid:
            if is_admin_card:
                # Admin card - show visitor access mode
                self.status_label.setText("Security Staff Identified")
                self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
                self.status_frame.setStyleSheet("background-color: #2196F3; border: 2px solid #1565C0;")
                self.visitor_frame.setVisible(True)
                
                # Log admin card scan
                log_entry(student_data.get("card_id", "UNKNOWN"), student_data.get("id", "UNKNOWN"), "success", entry_type="admin_scan")
                
                # Don't activate entry yet - wait for visitor access button
                self.return_timer.start(30000)  # 30 seconds timeout for admin mode
            else:
                # Regular student card - grant access
                self.status_label.setText("Access Granted")
                self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
                self.status_frame.setStyleSheet("background-color: #4CAF50; border: 2px solid #388E3C;")
                self.visitor_frame.setVisible(False)
                
                # Log successful entry
                log_entry(student_data.get("card_id", "UNKNOWN"), student_data.get("id", "UNKNOWN"), "success")
                
                # Open gate
                gate_controller.open_gate()
                
                # Return to main screen after delay
                self.return_timer.start(5000)  # 5 seconds
                
                # Close gate after entry
                QTimer.singleShot(6000, gate_controller.close_gate)
        else:
            # Invalid card
            self.status_label.setText("Access Denied")
            self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
            self.status_frame.setStyleSheet("background-color: #F44336; border: 2px solid #D32F2F;")
            self.visitor_frame.setVisible(False)
            
            # Log failed entry
            log_entry(student_data.get("card_id", "UNKNOWN"), student_data.get("id", "UNKNOWN"), "failure")
            
            # Trigger alarm for invalid access attempt
            gate_controller.trigger_alarm()
            
            # Return to main screen after delay
            self.return_timer.start(5000)  # 5 seconds
            
            # Stop alarm when returning to main screen
            QTimer.singleShot(5000, gate_controller.stop_alarm)
    
    def grant_visitor_access(self):
        """Grant access for a visitor"""
        if self.visitor_mode and self.current_student_data:
            print("Granting visitor access...")
            
            # Log visitor access
            log_entry(
                self.current_student_data.get("card_id", "ADMIN"), 
                self.current_student_data.get("id", "ADMIN"), 
                "success", 
                entry_type="visitor_access"
            )
            
            # Update status display
            self.status_label.setText("Visitor Access Granted")
            self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
            self.status_frame.setStyleSheet("background-color: #4CAF50; border: 2px solid #388E3C;")
            self.visitor_frame.setVisible(False)
            
            # Open gate
            gate_controller.open_gate()
            
            # Return to main screen after delay
            self.return_timer.start(5000)  # 5 seconds
            
            # Close gate after entry
            QTimer.singleShot(6000, gate_controller.close_gate)
        else:
            print("Error: Cannot grant visitor access in this mode.")
    
    def return_to_main(self):
        """Return to the main screen"""
        self.return_timer.stop()
        if self.parent:
            self.parent.show_main_screen()

class AdminScreen(QWidget):
    """Admin screen for managing the system"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.init_ui()
    
    def init_ui(self):
        """Setup the user interface"""
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        
        # Header bar
        header_layout = QHBoxLayout()
        
        # Back button
        back_button = QPushButton("Back to Main")
        back_button.setFixedSize(150, 40)
        back_button.setStyleSheet("""
            QPushButton {
                background-color: #607D8B;
                color: white;
                border-radius: 5px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #455A64;
            }
            QPushButton:pressed {
                background-color: #37474F;
            }
        """)
        back_button.clicked.connect(self.return_to_main)
        
        # Title
        title_label = QLabel("Admin Panel")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #1A237E;")
        
        header_layout.addWidget(back_button)
        header_layout.addStretch()
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addSpacing(150) # Balance the back button
        
        # Admin functions
        functions_layout = QVBoxLayout()
        functions_layout.setSpacing(10)
        
        # Add new student/card button
        add_button = QPushButton("Add New Student/Card")
        add_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 5px;
                font-size: 16px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
            QPushButton:pressed {
                background-color: #2E7D32;
            }
        """)
        add_button.clicked.connect(self.add_new_entry)
        
        # View entry logs button
        view_logs_button = QPushButton("View Entry Logs")
        view_logs_button.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border-radius: 5px;
                font-size: 16px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #1565C0;
            }
        """)
        view_logs_button.clicked.connect(self.view_entry_logs)
        
        # View statistics button
        view_stats_button = QPushButton("View Statistics")
        view_stats_button.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                border-radius: 5px;
                font-size: 16px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
            QPushButton:pressed {
                background-color: #E65100;
            }
        """)
        view_stats_button.clicked.connect(self.view_statistics)

        # Testing Interface
        test_group = QFrame()
        test_group.setFrameShape(QFrame.StyledPanel)
        test_group.setStyleSheet("""
            QFrame {
                background-color: #F5F5F5;
                border: 1px solid #BDBDBD;
                border-radius: 5px;
                padding: 10px;
            }
        """)
        test_layout = QVBoxLayout(test_group)
        
        test_title = QLabel("Testing Interface")
        test_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1A237E;")
        test_layout.addWidget(test_title)
        
        # Test card scanning buttons
        scan_buttons_layout = QHBoxLayout()
        
        # Valid student card
        valid_student_btn = QPushButton("Test Valid Student")
        valid_student_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 5px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
        """)
        valid_student_btn.clicked.connect(lambda: self.test_card_scan("A1B2C3D4"))
        
        # Admin card
        admin_card_btn = QPushButton("Test Admin Card")
        admin_card_btn.setStyleSheet("""
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
        admin_card_btn.clicked.connect(lambda: self.test_card_scan("ADMIN001"))
        
        # Invalid card
        invalid_card_btn = QPushButton("Test Invalid Card")
        invalid_card_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border-radius: 5px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
        """)
        invalid_card_btn.clicked.connect(lambda: self.test_card_scan("INVALID_CARD"))
        
        scan_buttons_layout.addWidget(valid_student_btn)
        scan_buttons_layout.addWidget(admin_card_btn)
        scan_buttons_layout.addWidget(invalid_card_btn)
        
        test_layout.addLayout(scan_buttons_layout)
        
        # Test visitor access
        visitor_btn = QPushButton("Test Visitor Access")
        visitor_btn.setStyleSheet("""
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
        visitor_btn.clicked.connect(self.test_visitor_access)
        test_layout.addWidget(visitor_btn)

        # Gate Control Testing
        gate_group = QFrame()
        gate_group.setFrameShape(QFrame.StyledPanel)
        gate_group.setStyleSheet("""
            QFrame {
                background-color: #E3F2FD;
                border: 1px solid #90CAF9;
                border-radius: 5px;
                padding: 10px;
                margin-top: 10px;
            }
        """)
        gate_layout = QVBoxLayout(gate_group)
        
        gate_title = QLabel("Gate Control Testing")
        gate_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #1565C0;")
        gate_layout.addWidget(gate_title)
        
        gate_buttons_layout = QHBoxLayout()
        
        # Open gate button
        open_gate_btn = QPushButton("Open Gate")
        open_gate_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 5px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
        """)
        open_gate_btn.clicked.connect(self.test_open_gate)
        
        # Close gate button
        close_gate_btn = QPushButton("Close Gate")
        close_gate_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border-radius: 5px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
        """)
        close_gate_btn.clicked.connect(self.test_close_gate)
        
        gate_buttons_layout.addWidget(open_gate_btn)
        gate_buttons_layout.addWidget(close_gate_btn)
        
        gate_layout.addLayout(gate_buttons_layout)
        
        # Alarm testing
        alarm_btn = QPushButton("Test Alarm System")
        alarm_btn.setStyleSheet("""
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
        alarm_btn.clicked.connect(self.test_alarm)
        
        # Stop alarm button
        stop_alarm_btn = QPushButton("Stop Alarm")
        stop_alarm_btn.setStyleSheet("""
            QPushButton {
                background-color: #9E9E9E;
                color: white;
                border-radius: 5px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #757575;
            }
        """)
        stop_alarm_btn.clicked.connect(self.stop_alarm)
        
        alarm_buttons_layout = QHBoxLayout()
        alarm_buttons_layout.addWidget(alarm_btn)
        alarm_buttons_layout.addWidget(stop_alarm_btn)
        
        gate_layout.addLayout(alarm_buttons_layout)
        
        test_layout.addWidget(gate_group)
        
        functions_layout.addWidget(add_button)
        functions_layout.addWidget(view_logs_button)
        functions_layout.addWidget(view_stats_button)
        functions_layout.addWidget(test_group)
        
        # Add elements to main layout
        main_layout.addLayout(header_layout)
        main_layout.addSpacing(20)
        main_layout.addLayout(functions_layout)
        main_layout.addStretch()
        
        self.setLayout(main_layout)
    
    def test_card_scan(self, card_id):
        """Test card scanning with a specific card ID"""
        if self.parent:
            student_data = get_student_by_card(card_id)
            if student_data:
                self.parent.show_student_info_screen(student_data)
            else:
                # Handle invalid card
                print(f"Invalid card: {card_id}")
                log_entry(card_id, "UNKNOWN", "failure")
                # Show denied status
                self.parent.show_student_info_screen({"valid": False, "card_id": card_id})
    
    def test_visitor_access(self):
        """Test visitor access functionality"""
        if self.parent:
            # Use admin card for visitor access
            student_data = get_student_by_card("ADMIN001")
            if student_data:
                self.parent.show_student_info_screen(student_data)
                # Wait a bit and then grant visitor access
                QTimer.singleShot(1000, lambda: self.parent.student_info_screen.grant_visitor_access())
    
    def test_open_gate(self):
        """Test opening the gate"""
        gate_controller.open_gate()
        QMessageBox.information(self, "Gate Control", "Gate opened successfully")
    
    def test_close_gate(self):
        """Test closing the gate"""
        gate_controller.close_gate()
        QMessageBox.information(self, "Gate Control", "Gate closed successfully")
    
    def test_alarm(self):
        """Test the alarm system"""
        gate_controller.trigger_alarm()
        QMessageBox.information(self, "Alarm System", "Alarm triggered! Click 'Stop Alarm' to deactivate.")
    
    def stop_alarm(self):
        """Stop the alarm system"""
        gate_controller.stop_alarm()
        QMessageBox.information(self, "Alarm System", "Alarm stopped")
    
    def add_new_entry(self):
        """Show dialog to add new student and card"""
        dialog = AddEntryDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            data = dialog.get_data()
            
            # Add student
            student_added = add_new_student(
                data["student_id"],
                data["name"],
                data["faculty"],
                data["program"],
                data["level"],
                data["image_path"]
            )
            
            # Add card
            card_added = add_new_card(
                data["card_id"],
                data["student_id"],
                data["card_type"]
            )
            
            if student_added and card_added:
                QMessageBox.information(self, "Success", "New student and card added successfully.")
            else:
                QMessageBox.warning(self, "Warning", "Failed to add new entry. Check if ID or Card ID already exists.")
    
    def view_entry_logs(self):
        """Show entry logs (could be a new screen or dialog)"""
        # For simplicity, just print to console
        print("\n--- Recent Entry Logs ---")
        entries = get_recent_entries(20)
        for entry in entries:
            print("{} - Card: {} - Student: {} - Status: {}".format(
                entry["timestamp"], entry["card_id"], entry["student_name"], entry["status"]
            ))
        print("------------------------\n")
        QMessageBox.information(self, "Entry Logs", "Recent entry logs printed to console.")
    
    def view_statistics(self):
        """Show entry statistics"""
        stats = get_entry_stats()
        message = f"""
Entry Statistics:

Total Entries: {stats["total"]}
Today's Entries: {stats["today"]}
Successful Entries: {stats["successful"]}
Failed Entries: {stats["failed"]}
Visitor Access: {stats["visitor"]}
        """
        QMessageBox.information(self, "Statistics", message)
    
    def return_to_main(self):
        """Return to the main screen"""
        if self.parent:
            self.parent.show_main_screen()

class AddEntryDialog(QDialog):
    """Dialog for adding a new student and card"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Student/Card")
        self.setMinimumWidth(400)
        
        self.init_ui()
    
    def init_ui(self):
        """Setup the dialog UI"""
        layout = QVBoxLayout()
        
        # Student fields
        student_group = QFrame()
        student_group.setFrameShape(QFrame.StyledPanel)
        student_layout = QVBoxLayout(student_group)
        student_layout.addWidget(QLabel("Student Information"))
        
        self.student_id_input = QLineEdit()
        self.student_id_input.setPlaceholderText("Student ID")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Name")
        self.faculty_input = QLineEdit()
        self.faculty_input.setPlaceholderText("Faculty")
        self.program_input = QLineEdit()
        self.program_input.setPlaceholderText("Program")
        self.level_input = QLineEdit()
        self.level_input.setPlaceholderText("Level")
        self.image_path_input = QLineEdit()
        self.image_path_input.setPlaceholderText("Image Path (optional)")
        
        student_layout.addWidget(self.student_id_input)
        student_layout.addWidget(self.name_input)
        student_layout.addWidget(self.faculty_input)
        student_layout.addWidget(self.program_input)
        student_layout.addWidget(self.level_input)
        student_layout.addWidget(self.image_path_input)
        
        # Card fields
        card_group = QFrame()
        card_group.setFrameShape(QFrame.StyledPanel)
        card_layout = QVBoxLayout(card_group)
        card_layout.addWidget(QLabel("Card Information"))
        
        self.card_id_input = QLineEdit()
        self.card_id_input.setPlaceholderText("Card ID")
        
        # Card type selection
        self.card_type_layout = QHBoxLayout()
        self.student_radio = QRadioButton("Student")
        self.admin_radio = QRadioButton("Admin")
        self.student_radio.setChecked(True)
        self.card_type_layout.addWidget(QLabel("Card Type:"))
        self.card_type_layout.addWidget(self.student_radio)
        self.card_type_layout.addWidget(self.admin_radio)
        self.card_type_layout.addStretch()
        
        card_layout.addWidget(self.card_id_input)
        card_layout.addLayout(self.card_type_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        self.ok_button = QPushButton("OK")
        self.cancel_button = QPushButton("Cancel")
        
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        
        layout.addWidget(student_group)
        layout.addWidget(card_group)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def get_data(self):
        """Get data from the dialog fields"""
        card_type = "admin" if self.admin_radio.isChecked() else "student"
        
        return {
            "student_id": self.student_id_input.text().strip(),
            "name": self.name_input.text().strip(),
            "faculty": self.faculty_input.text().strip(),
            "program": self.program_input.text().strip(),
            "level": self.level_input.text().strip(),
            "image_path": self.image_path_input.text().strip(),
            "card_id": self.card_id_input.text().strip(),
            "card_type": card_type
        }

class MainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Entry Gate System")
        self.setGeometry(100, 100, 800, 600)
        
        # Central widget and stacked layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        
        self.stacked_widget = QStackedWidget()
        
        # Create screens
        self.main_screen = MainScreen(self)
        self.student_info_screen = StudentInfoScreen(self)
        self.admin_screen = AdminScreen(self)
        
        # Add screens to stacked widget
        self.stacked_widget.addWidget(self.main_screen)
        self.stacked_widget.addWidget(self.student_info_screen)
        self.stacked_widget.addWidget(self.admin_screen)
        
        # Set main layout
        main_layout = QVBoxLayout(self.central_widget)
        main_layout.addWidget(self.stacked_widget)
        
        # Show main screen initially
        self.show_main_screen()
    
    def show_main_screen(self):
        """Switch to the main screen"""
        self.stacked_widget.setCurrentWidget(self.main_screen)
    
    def show_student_info_screen(self, student_data):
        """Switch to the student info screen and update data"""
        self.student_info_screen.update_student_info(student_data)
        self.stacked_widget.setCurrentWidget(self.student_info_screen)
    
    def show_admin_screen(self):
        """Switch to the admin screen"""
        self.stacked_widget.setCurrentWidget(self.admin_screen)

# Main execution
if __name__ == "__main__":
    # Setup database and create placeholders
    setup_database()
    create_placeholder_images()
    create_flask_templates()
    
    # Start Flask app in a separate thread
    def run_flask():
        try:
            # Note: Use host="0.0.0.0" to make it accessible on the network
            app.run(host="0.0.0.0", port=5000, debug=False)
        except Exception as e:
            print(f"Error running Flask app: {e}")
            
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Start PyQt5 GUI
    app_gui = QApplication(sys.argv)
    main_window = MainWindow()
    main_window.show()
    sys.exit(app_gui.exec_())
