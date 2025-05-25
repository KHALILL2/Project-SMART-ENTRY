#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Smart Gate System for University Entry
This application provides a GUI for a smart entry gate using NFC cards,
and a web interface for monitoring entries.
"""

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
    QMessageBox, QInputDialog, QRadioButton, QTimer
)
from PyQt5.QtGui import QPixmap, QFont, QIcon
from PyQt5.QtCore import Qt, QSize, QTimer
from flask import Flask, render_template, jsonify, request

# Ensure directories exist
os.makedirs('assets', exist_ok=True)
os.makedirs('database', exist_ok=True)
os.makedirs('templates', exist_ok=True)
os.makedirs('static/css', exist_ok=True)
os.makedirs('static/js', exist_ok=True)

# Database setup
DB_PATH = 'database/smart_gate.db'

def setup_database():
    """Initialize the SQLite database with required tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create students table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS students (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        faculty TEXT,
        program TEXT,
        level TEXT,
        image_path TEXT
    )
    ''')
    
    # Create cards table with card_type field
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS cards (
        card_id TEXT PRIMARY KEY,
        student_id TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        card_type TEXT DEFAULT 'student',
        FOREIGN KEY (student_id) REFERENCES students(id)
    )
    ''')
    
    # Create entry_logs table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS entry_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id TEXT,
        student_id TEXT,
        timestamp TEXT NOT NULL,
        gate TEXT DEFAULT 'Main Gate',
        status TEXT NOT NULL,
        entry_type TEXT DEFAULT 'regular',
        FOREIGN KEY (card_id) REFERENCES cards(card_id),
        FOREIGN KEY (student_id) REFERENCES students(id)
    )
    ''')
    
    # Insert sample data for testing
    try:
        # Sample students
        cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                      ('20210001', 'John Smith', 'Engineering', 'Computer Engineering', '3rd Year', 'assets/student1.png'))
        cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                      ('20210002', 'Sarah Johnson', 'Science', 'Physics', '2nd Year', 'assets/student2.png'))
        cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                      ('20210003', 'Mohammed Ali', 'Medicine', 'General Medicine', '4th Year', 'assets/student3.png'))
        cursor.execute("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?, ?, ?)", 
                      ('SECURITY001', 'Security Staff', 'Security', 'Gate Security', 'Staff', 'assets/security_staff.png'))
        
        # Sample cards
        cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                      ('A1B2C3D4', '20210001', 1, 'student'))
        cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                      ('E5F6G7H8', '20210002', 1, 'student'))
        cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                      ('I9J0K1L2', '20210003', 1, 'student'))
        cursor.execute("INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)", 
                      ('ADMIN001', 'SECURITY001', 1, 'admin'))
        
        # Sample entry logs
        current_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute("INSERT OR IGNORE INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type) VALUES (?, ?, ?, ?, ?, ?)", 
                      ('A1B2C3D4', '20210001', current_date, 'Main Gate', 'success', 'regular'))
        cursor.execute("INSERT OR IGNORE INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type) VALUES (?, ?, ?, ?, ?, ?)", 
                      ('E5F6G7H8', '20210002', current_date, 'Main Gate', 'success', 'regular'))
        cursor.execute("INSERT OR IGNORE INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type) VALUES (?, ?, ?, ?, ?, ?)", 
                      ('I9J0K1L2', '20210003', yesterday, 'Library Gate', 'success', 'regular'))
        cursor.execute("INSERT OR IGNORE INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type) VALUES (?, ?, ?, ?, ?, ?)", 
                      ('UNKNOWN', 'UNKNOWN', yesterday, 'Main Gate', 'failure', 'regular'))
        cursor.execute("INSERT OR IGNORE INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type) VALUES (?, ?, ?, ?, ?, ?)", 
                      ('ADMIN001', 'SECURITY001', yesterday, 'Main Gate', 'success', 'visitor_access'))
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
    
    cursor.execute('''
    SELECT s.id, s.name, s.faculty, s.program, s.level, s.image_path, c.is_active, c.card_type
    FROM students s
    JOIN cards c ON s.id = c.student_id
    WHERE c.card_id = ?
    ''', (card_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        student_data = {
            'id': result[0],
            'name': result[1],
            'faculty': result[2],
            'program': result[3],
            'level': result[4],
            'image_path': result[5],
            'valid': bool(result[6]),
            'card_type': result[7],
            'card_id': card_id
        }
        return student_data
    return None

def log_entry(card_id, student_id, status, gate='Main Gate', entry_type='regular'):
    """Log an entry attempt"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute('''
    INSERT INTO entry_logs (card_id, student_id, timestamp, gate, status, entry_type)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (card_id, student_id, timestamp, gate, status, entry_type))
    
    conn.commit()
    conn.close()

def add_new_card(card_id, student_id, card_type='student'):
    """Add a new card to the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
        INSERT INTO cards (card_id, student_id, is_active, card_type)
        VALUES (?, ?, 1, ?)
        ''', (card_id, student_id, card_type))
        
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def add_new_student(student_id, name, faculty='', program='', level='', image_path=''):
    """Add a new student to the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
        INSERT INTO students (id, name, faculty, program, level, image_path)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (student_id, name, faculty, program, level, image_path))
        
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        # Update existing student
        cursor.execute('''
        UPDATE students
        SET name = ?, faculty = ?, program = ?, level = ?, image_path = ?
        WHERE id = ?
        ''', (name, faculty, program, level, image_path, student_id))
        
        conn.commit()
        conn.close()
        return True

def get_all_students():
    """Get all students from the database"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT s.id, s.name, s.faculty, s.program, s.level, s.image_path, c.card_id
    FROM students s
    LEFT JOIN cards c ON s.id = c.student_id
    ORDER BY s.name
    ''')
    
    result = cursor.fetchall()
    conn.close()
    
    students = [dict(row) for row in result]
    return students

def get_recent_entries(limit=10):
    """Get recent entry logs"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT e.id, e.card_id, e.student_id, s.name as student_name, e.timestamp, e.gate, e.status, e.entry_type
    FROM entry_logs e
    LEFT JOIN students s ON e.student_id = s.id
    ORDER BY e.timestamp DESC
    LIMIT ?
    ''', (limit,))
    
    result = cursor.fetchall()
    conn.close()
    
    entries = [dict(row) for row in result]
    return entries

def get_entry_stats():
    """Get entry statistics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get today's date
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # Get total entries
    cursor.execute('SELECT COUNT(*) FROM entry_logs')
    total_entries = cursor.fetchone()[0]
    
    # Get today's entries
    cursor.execute('SELECT COUNT(*) FROM entry_logs WHERE timestamp LIKE ?', (f'{today}%',))
    today_entries = cursor.fetchone()[0]
    
    # Get successful entries
    cursor.execute('SELECT COUNT(*) FROM entry_logs WHERE status = "success"')
    successful_entries = cursor.fetchone()[0]
    
    # Get failed entries
    cursor.execute('SELECT COUNT(*) FROM entry_logs WHERE status = "failure"')
    failed_entries = cursor.fetchone()[0]
    
    # Get visitor entries
    cursor.execute('SELECT COUNT(*) FROM entry_logs WHERE entry_type = "visitor_access"')
    visitor_entries = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        'total': total_entries,
        'today': today_entries,
        'successful': successful_entries,
        'failed': failed_entries,
        'visitor': visitor_entries
    }

# Create placeholder images if they don't exist
def create_placeholder_images():
    """Create placeholder images for testing"""
    # University logo placeholder
    if not os.path.exists('assets/university_logo_placeholder.png'):
        # Create a simple colored square as placeholder
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGB', (200, 200), color=(25, 25, 112))
        d = ImageDraw.Draw(img)
        d.rectangle([10, 10, 190, 190], outline=(255, 255, 255), width=2)
        d.text((40, 80), "University\nLogo", fill=(255, 255, 255))
        img.save('assets/university_logo_placeholder.png')
    
    # Student placeholders
    for i in range(1, 4):
        if not os.path.exists(f'assets/student{i}.png'):
            from PIL import Image, ImageDraw
            img = Image.new('RGB', (200, 200), color=(200, 200, 200))
            d = ImageDraw.Draw(img)
            d.rectangle([10, 10, 190, 190], outline=(100, 100, 100), width=2)
            d.text((50, 90), f"Student {i}", fill=(50, 50, 50))
            img.save(f'assets/student{i}.png')
    
    # Security staff placeholder
    if not os.path.exists('assets/security_staff.png'):
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (200, 200), color=(50, 50, 50))
        d = ImageDraw.Draw(img)
        d.rectangle([10, 10, 190, 190], outline=(200, 200, 200), width=2)
        d.text((40, 90), "Security Staff", fill=(200, 200, 200))
        img.save('assets/security_staff.png')

# Create Flask templates
def create_flask_templates():
    """Create Flask templates for the web interface"""
    # Create index.html
    if not os.path.exists('templates/index.html'):
        with open('templates/index.html', 'w') as f:
            f.write('''<!DOCTYPE html>
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
                                        <p>Visitors</p>
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
            <div class="card mt-4">
                <div class="card-header">
                    <div class="row">
                        <div class="col-md-6">
                            <h5>Student List</h5>
                        </div>
                        <div class="col-md-6">
                            <input type="text" id="student-search" class="form-control" placeholder="Search students...">
                        </div>
                    </div>
                </div>
                <div class="card-body">
                    <div class="table-responsive">
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
                            <tbody id="student-list">
                                <!-- Students will be loaded here -->
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <div id="entries" class="section">
            <h2>Entry Logs</h2>
            <div class="card mt-4">
                <div class="card-header">
                    <div class="row">
                        <div class="col-md-6">
                            <h5>Entry Log</h5>
                        </div>
                        <div class="col-md-6">
                            <input type="text" id="entry-search" class="form-control" placeholder="Search entries...">
                        </div>
                    </div>
                </div>
                <div class="card-body">
                    <div class="table-responsive">
                        <table class="table table-striped">
                            <thead>
                                <tr>
                                    <th>Date</th>
                                    <th>Time</th>
                                    <th>Student ID</th>
                                    <th>Name</th>
                                    <th>Gate</th>
                                    <th>Status</th>
                                    <th>Type</th>
                                </tr>
                            </thead>
                            <tbody id="entry-list">
                                <!-- Entries will be loaded here -->
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <div id="stats" class="section">
            <h2>Statistics</h2>
            <div class="row mt-4">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h5>Entry Statistics</h5>
                        </div>
                        <div class="card-body">
                            <canvas id="entry-chart"></canvas>
                        </div>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h5>Entry Types</h5>
                        </div>
                        <div class="card-body">
                            <canvas id="type-chart"></canvas>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
        // Navigation
        document.querySelectorAll('.navbar-nav .nav-link').forEach(link => {
            link.addEventListener('click', function(e) {
                e.preventDefault();
                
                // Remove active class from all links and sections
                document.querySelectorAll('.navbar-nav .nav-link').forEach(l => l.classList.remove('active'));
                document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
                
                // Add active class to clicked link
                this.classList.add('active');
                
                // Show corresponding section
                const targetId = this.getAttribute('href').substring(1);
                document.getElementById(targetId).classList.add('active');
            });
        });

        // Load data
        window.addEventListener('load', function() {
            // Load recent entries
            fetch('/api/recent_entries')
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        const tableBody = document.getElementById('recent-entries');
                        tableBody.innerHTML = '';
                        
                        data.data.forEach(entry => {
                            const date = new Date(entry.timestamp);
                            const row = document.createElement('tr');
                            row.innerHTML = `
                                <td>${date.toLocaleTimeString()}</td>
                                <td>${entry.student_name || 'Unknown'}</td>
                                <td><span class="badge ${entry.status === 'success' ? 'bg-success' : 'bg-danger'}">${entry.status}</span></td>
                            `;
                            tableBody.appendChild(row);
                        });
                    }
                });
            
            // Load statistics
            fetch('/api/stats')
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        document.getElementById('today-entries').textContent = data.data.today;
                        document.getElementById('successful-entries').textContent = data.data.successful;
                        document.getElementById('failed-entries').textContent = data.data.failed;
                        document.getElementById('visitor-entries').textContent = data.data.visitor;
                        
                        // Create charts
                        const entryChart = new Chart(document.getElementById('entry-chart'), {
                            type: 'bar',
                            data: {
                                labels: ['Total', 'Today', 'Successful', 'Failed'],
                                datasets: [{
                                    label: 'Entries',
                                    data: [data.data.total, data.data.today, data.data.successful, data.data.failed],
                                    backgroundColor: ['#6c757d', '#0d6efd', '#198754', '#dc3545']
                                }]
                            },
                            options: {
                                responsive: true,
                                plugins: {
                                    legend: {
                                        display: false
                                    }
                                }
                            }
                        });
                        
                        const typeChart = new Chart(document.getElementById('type-chart'), {
                            type: 'pie',
                            data: {
                                labels: ['Regular', 'Visitor'],
                                datasets: [{
                                    data: [data.data.successful - data.data.visitor, data.data.visitor],
                                    backgroundColor: ['#0d6efd', '#0dcaf0']
                                }]
                            },
                            options: {
                                responsive: true
                            }
                        });
                    }
                });
            
            // Load students
            fetch('/api/students')
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        const tableBody = document.getElementById('student-list');
                        tableBody.innerHTML = '';
                        
                        data.data.forEach(student => {
                            const row = document.createElement('tr');
                            row.innerHTML = `
                                <td>${student.id}</td>
                                <td>${student.name}</td>
                                <td>${student.faculty || '-'}</td>
                                <td>${student.program || '-'}</td>
                                <td>${student.level || '-'}</td>
                                <td>${student.card_id || 'No Card'}</td>
                            `;
                            tableBody.appendChild(row);
                        });
                        
                        // Setup search
                        document.getElementById('student-search').addEventListener('input', function() {
                            const searchTerm = this.value.toLowerCase();
                            const rows = tableBody.querySelectorAll('tr');
                            
                            rows.forEach(row => {
                                const text = row.textContent.toLowerCase();
                                row.style.display = text.includes(searchTerm) ? '' : 'none';
                            });
                        });
                    }
                });
            
            // Load entries
            fetch('/api/entries')
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        const tableBody = document.getElementById('entry-list');
                        tableBody.innerHTML = '';
                        
                        data.data.forEach(entry => {
                            const date = new Date(entry.timestamp);
                            const row = document.createElement('tr');
                            row.innerHTML = `
                                <td>${date.toLocaleDateString()}</td>
                                <td>${date.toLocaleTimeString()}</td>
                                <td>${entry.student_id || 'Unknown'}</td>
                                <td>${entry.student_name || 'Unknown'}</td>
                                <td>${entry.gate}</td>
                                <td><span class="badge ${entry.status === 'success' ? 'bg-success' : 'bg-danger'}">${entry.status}</span></td>
                                <td>${entry.entry_type === 'visitor_access' ? '<span class="badge bg-info">Visitor</span>' : 
                                     entry.entry_type === 'admin_scan' ? '<span class="badge bg-primary">Admin</span>' : 
                                     '<span class="badge bg-secondary">Regular</span>'}</td>
                            `;
                            tableBody.appendChild(row);
                        });
                        
                        // Setup search
                        document.getElementById('entry-search').addEventListener('input', function() {
                            const searchTerm = this.value.toLowerCase();
                            const rows = tableBody.querySelectorAll('tr');
                            
                            rows.forEach(row => {
                                const text = row.textContent.toLowerCase();
                                row.style.display = text.includes(searchTerm) ? '' : 'none';
                            });
                        });
                    }
                });
        });
    </script>
</body>
</html>''')
    
    # Create CSS file
    if not os.path.exists('static/css/style.css'):
        with open('static/css/style.css', 'w') as f:
            f.write('''/* Main styles */
body {
    background-color: #f8f9fa;
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
}''')
    
    print("Flask templates created successfully.")

# Flask app
app = Flask(__name__)

@app.route('/')
def index():
    """Main page"""
    return render_template('index.html')

@app.route('/api/recent_entries')
def api_recent_entries():
    """API endpoint for recent entries data"""
    entries = get_recent_entries(5)
    return jsonify({"status": "success", "data": entries})

@app.route('/api/students')
def api_students():
    """API endpoint for students data"""
    students = get_all_students()
    return jsonify({'status': 'success', 'data': students})

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

@app.route('/api/stats')
def api_stats():
    """API endpoint for statistics data"""
    stats = get_entry_stats()
    return jsonify({'status': 'success', 'data': stats})

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
                self.status_frame.setStyleSheet("background-color: #4CAF50; border: 2px solid #2E7D32;")
                self.visitor_frame.setVisible(False)
                self.activate_valid_entry()
                
                # Log successful entry
                log_entry(student_data.get("card_id", "UNKNOWN"), student_data.get("id", "UNKNOWN"), "success")
                
                # Start automatic return timer
                self.return_timer.start(10000)  # 10 seconds
        else:
            # Invalid card
            self.status_label.setText("Access Denied: Invalid Card")
            self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
            self.status_frame.setStyleSheet("background-color: #F44336; border: 2px solid #C62828;")
            self.visitor_frame.setVisible(False)
            self.activate_invalid_entry()
            
            # Log failed entry
            log_entry(student_data.get("card_id", "UNKNOWN"), student_data.get("id", "UNKNOWN"), "failure")
            
            # Start automatic return timer
            self.return_timer.start(10000)  # 10 seconds
    
    def grant_visitor_access(self):
        """Grant access to a visitor"""
        if self.visitor_mode and hasattr(self, 'current_student_data'):
            # Update status to show visitor access granted
            self.status_label.setText("Visitor Access Granted")
            self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: white;")
            self.status_frame.setStyleSheet("background-color: #4CAF50; border: 2px solid #2E7D32;")
            
            # Hide visitor frame
            self.visitor_frame.setVisible(False)
            
            # Activate entry
            self.activate_valid_entry()
            
            # Log visitor access
            log_entry(
                self.current_student_data.get("card_id", "UNKNOWN"), 
                self.current_student_data.get("id", "UNKNOWN"), 
                "success", 
                entry_type="visitor_access"
            )
            
            # Reset timer
            self.return_timer.start(10000)  # 10 seconds
    
    def activate_valid_entry(self):
        """Activate valid entry indicators"""
        # This function would interact with actual hardware components (green LED, buzzer, servo)
        # Currently using a simple simulation
        print("Activating valid entry indicators (green LED, buzzer, servo)")
    
    def activate_invalid_entry(self):
        """Activate invalid entry indicators"""
        # This function would interact with actual hardware components (red LED, buzzer)
        # Currently using a simple simulation
        print("Activating invalid entry indicators (red LED, buzzer)")
    
    def return_to_main(self):
        """Return to main screen"""
        if self.parent:
            self.parent.show_main_screen()

class AdminScreen(QWidget):
    """Admin screen for system management"""
    
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
        
        # Admin title
        title_label = QLabel("Admin Panel")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #1A237E; margin: 10px 0;")
        
        # Admin options
        options_layout = QHBoxLayout()
        
        # Test components section
        test_frame = QFrame()
        test_frame.setFrameShape(QFrame.Box)
        test_frame.setFrameShadow(QFrame.Raised)
        test_frame.setLineWidth(2)
        test_frame.setStyleSheet("border: 2px solid #1A237E; background-color: #E8EAF6;")
        
        test_layout = QVBoxLayout(test_frame)
        
        test_title = QLabel("Test Components")
        test_title.setAlignment(Qt.AlignCenter)
        test_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1A237E; margin-bottom: 10px;")
        
        # Test buttons
        test_green_led_button = self.create_test_button("Test Green LED")
        test_green_led_button.clicked.connect(self.test_green_led)
        
        test_red_led_button = self.create_test_button("Test Red LED")
        test_red_led_button.clicked.connect(self.test_red_led)
        
        test_buzzer_button = self.create_test_button("Test Buzzer")
        test_buzzer_button.clicked.connect(self.test_buzzer)
        
        test_servo_button = self.create_test_button("Test Servo")
        test_servo_button.clicked.connect(self.test_servo)
        
        test_nfc_button = self.create_test_button("Test NFC Reader")
        test_nfc_button.clicked.connect(self.test_nfc_reader)
        
        # Status label
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 16px; color: #1A237E; margin-top: 10px;")
        
        # Add test buttons to layout
        test_layout.addWidget(test_title)
        test_layout.addWidget(test_green_led_button)
        test_layout.addWidget(test_red_led_button)
        test_layout.addWidget(test_buzzer_button)
        test_layout.addWidget(test_servo_button)
        test_layout.addWidget(test_nfc_button)
        test_layout.addWidget(self.status_label)
        test_layout.addStretch()
        
        # Card management section
        card_frame = QFrame()
        card_frame.setFrameShape(QFrame.Box)
        card_frame.setFrameShadow(QFrame.Raised)
        card_frame.setLineWidth(2)
        card_frame.setStyleSheet("border: 2px solid #1A237E; background-color: #E8EAF6;")
        
        card_layout = QVBoxLayout(card_frame)
        
        card_title = QLabel("Card Management")
        card_title.setAlignment(Qt.AlignCenter)
        card_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1A237E; margin-bottom: 10px;")
        
        # Card management buttons
        add_card_button = self.create_test_button("Add New Card")
        add_card_button.clicked.connect(self.add_new_card)
        
        test_valid_button = self.create_test_button("Test Valid Entry")
        test_valid_button.clicked.connect(self.test_valid_entry)
        
        test_invalid_button = self.create_test_button("Test Invalid Entry")
        test_invalid_button.clicked.connect(self.test_invalid_entry)
        
        open_web_button = self.create_test_button("Open Web Dashboard")
        open_web_button.clicked.connect(self.open_web_dashboard)
        
        # Add card management buttons to layout
        card_layout.addWidget(card_title)
        card_layout.addWidget(add_card_button)
        card_layout.addWidget(test_valid_button)
        card_layout.addWidget(test_invalid_button)
        card_layout.addWidget(open_web_button)
        card_layout.addStretch()
        
        # Add frames to options layout
        options_layout.addWidget(test_frame)
        options_layout.addWidget(card_frame)
        
        # Add elements to main layout
        main_layout.addLayout(header_layout)
        main_layout.addWidget(title_label)
        main_layout.addLayout(options_layout)
        
        self.setLayout(main_layout)
    
    def create_test_button(self, text):
        """Create a styled test button"""
        button = QPushButton(text)
        button.setStyleSheet("""
            QPushButton {
                background-color: #3F51B5;
                color: white;
                border-radius: 5px;
                font-size: 16px;
                padding: 10px;
                margin: 5px;
            }
            QPushButton:hover {
                background-color: #303F9F;
            }
            QPushButton:pressed {
                background-color: #1A237E;
            }
        """)
        return button
    
    def update_datetime(self):
        """Update date and time display"""
        now = datetime.datetime.now()
        date_str = now.strftime("%Y/%m/%d")
        time_str = now.strftime("%H:%M:%S")
        self.datetime_label.setText(f"{date_str} {time_str}")
    
    def return_to_main(self):
        """Return to main screen"""
        if self.parent:
            self.parent.show_main_screen()
    
    def test_green_led(self):
        """Test green LED"""
        self.status_label.setText("Testing Green LED...")
        # This function would interact with actual hardware components
        # Currently using a simple simulation
        QTimer.singleShot(2000, lambda: self.status_label.setText("Green LED test completed successfully"))
        print("Testing Green LED")
    
    def test_red_led(self):
        """Test red LED"""
        self.status_label.setText("Testing Red LED...")
        # This function would interact with actual hardware components
        # Currently using a simple simulation
        QTimer.singleShot(2000, lambda: self.status_label.setText("Red LED test completed successfully"))
        print("Testing Red LED")
    
    def test_buzzer(self):
        """Test buzzer"""
        self.status_label.setText("Testing Buzzer...")
        # This function would interact with actual hardware components
        # Currently using a simple simulation
        QTimer.singleShot(2000, lambda: self.status_label.setText("Buzzer test completed successfully"))
        print("Testing Buzzer")
    
    def test_servo(self):
        """Test servo"""
        self.status_label.setText("Testing Servo...")
        # This function would interact with actual hardware components
        # Currently using a simple simulation
        QTimer.singleShot(5000, lambda: self.status_label.setText("Servo test completed successfully"))
        print("Testing Servo")
    
    def test_nfc_reader(self):
        """Test NFC reader"""
        self.status_label.setText("Testing NFC Reader...")
        # This function would interact with actual hardware components
        # Currently using a simple simulation
        QTimer.singleShot(5000, lambda: self.status_label.setText("NFC Reader test completed successfully"))
        print("Testing NFC Reader")
    
    def add_new_card(self):
        """Open dialog to add a new card"""
        dialog = AddCardDialog(self)
        dialog.exec_()
    
    def test_valid_entry(self):
        """Test valid entry"""
        # Simulate a valid card scan
        student_data = {
            'id': '20210001',
            'name': 'John Smith',
            'faculty': 'Engineering',
            'program': 'Computer Engineering',
            'level': '3rd Year',
            'image_path': 'assets/student1.png',
            'valid': True,
            'card_type': 'student',
            'card_id': 'A1B2C3D4'
        }
        
        if self.parent:
            self.parent.show_student_info_screen(student_data)
    
    def test_invalid_entry(self):
        """Test invalid entry"""
        # Simulate an invalid card scan
        student_data = {
            'id': 'INVALID',
            'name': 'Unknown',
            'faculty': '',
            'program': '',
            'level': '',
            'image_path': '',
            'valid': False,
            'card_type': 'student',
            'card_id': 'INVALID'
        }
        
        if self.parent:
            self.parent.show_student_info_screen(student_data)
    
    def open_web_dashboard(self):
        """Open web dashboard in browser"""
        self.status_label.setText("Opening web dashboard...")
        # This function would open the web browser
        # Currently using a simple simulation
        import webbrowser
        webbrowser.open('http://localhost:5000')
        QTimer.singleShot(2000, lambda: self.status_label.setText("Web dashboard opened"))
        print("Opening web dashboard")


class AddCardDialog(QDialog):
    """Dialog for adding a new card"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.init_ui()
    
    def init_ui(self):
        """Setup the user interface"""
        self.setWindowTitle("Add New Card")
        self.setFixedSize(500, 450)
        self.setStyleSheet("background-color: #F5F5F5;")
        
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)
        
        # Close button
        close_layout = QHBoxLayout()
        close_button = QPushButton("Close")
        close_button.setFixedSize(80, 30)
        close_button.setStyleSheet("""
            QPushButton {
                background-color: #607D8B;
                color: white;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #455A64;
            }
            QPushButton:pressed {
                background-color: #37474F;
            }
        """)
        close_button.clicked.connect(self.close)
        
        close_layout.addWidget(close_button)
        close_layout.addStretch()
        
        # Dialog title
        title_label = QLabel("Add New Card")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #1A237E; margin: 10px;")
        
        # Input form
        form_frame = QFrame()
        form_frame.setFrameShape(QFrame.Box)
        form_frame.setFrameShadow(QFrame.Raised)
        form_frame.setLineWidth(1)
        form_frame.setStyleSheet("border: 1px solid #BDBDBD; background-color: #EEEEEE;")
        
        form_layout = QVBoxLayout(form_frame)
        form_layout.setContentsMargins(20, 20, 20, 20)
        form_layout.setSpacing(15)
        
        # Student ID field
        student_id_layout = QHBoxLayout()
        student_id_label = QLabel("Student ID:")
        student_id_label.setStyleSheet("font-size: 16px;")
        self.student_id_input = QLineEdit()
        self.student_id_input.setStyleSheet("""
            QLineEdit {
                font-size: 16px;
                padding: 5px;
                border: 1px solid #BDBDBD;
                border-radius: 3px;
                background-color: white;
            }
        """)
        student_id_layout.addWidget(student_id_label)
        student_id_layout.addWidget(self.student_id_input)
        
        # Full name field
        name_layout = QHBoxLayout()
        name_label = QLabel("Full Name:")
        name_label.setStyleSheet("font-size: 16px;")
        self.name_input = QLineEdit()
        self.name_input.setStyleSheet("""
            QLineEdit {
                font-size: 16px;
                padding: 5px;
                border: 1px solid #BDBDBD;
                border-radius: 3px;
                background-color: white;
            }
        """)
        name_layout.addWidget(name_label)
        name_layout.addWidget(self.name_input)
        
        # Card type selection
        card_type_layout = QHBoxLayout()
        card_type_label = QLabel("Card Type:")
        card_type_label.setStyleSheet("font-size: 16px;")
        
        self.card_type_student = QRadioButton("Student")
        self.card_type_student.setChecked(True)
        self.card_type_student.setStyleSheet("font-size: 16px;")
        
        self.card_type_admin = QRadioButton("Admin (Security Staff)")
        self.card_type_admin.setStyleSheet("font-size: 16px;")
        
        card_type_group = QHBoxLayout()
        card_type_group.addWidget(self.card_type_student)
        card_type_group.addWidget(self.card_type_admin)
        card_type_group.addStretch()
        
        card_type_layout.addWidget(card_type_label)
        card_type_layout.addLayout(card_type_group)
        
        # Read card button
        read_card_button = QPushButton("Scan New Card")
        read_card_button.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border-radius: 5px;
                font-size: 16px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #1565C0;
            }
        """)
        read_card_button.clicked.connect(self.read_new_card)
        
        # Read status
        self.status_label = QLabel("Ready to scan card")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 16px; color: #1A237E;")
        
        # Add fields to form
        form_layout.addLayout(student_id_layout)
        form_layout.addLayout(name_layout)
        form_layout.addLayout(card_type_layout)
        form_layout.addWidget(read_card_button, 0, Qt.AlignCenter)
        form_layout.addWidget(self.status_label)
        
        # Save button
        save_button = QPushButton("Save Card")
        save_button.setFixedSize(200, 50)
        save_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 5px;
                font-size: 18px;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
            QPushButton:pressed {
                background-color: #2E7D32;
            }
        """)
        save_button.clicked.connect(self.save_card)
        
        # Add elements to main layout
        main_layout.addLayout(close_layout)
        main_layout.addWidget(title_label)
        main_layout.addWidget(form_frame)
        main_layout.addStretch()
        main_layout.addWidget(save_button, 0, Qt.AlignCenter)
        
        self.setLayout(main_layout)
        
        # Tracking variables
        self.card_id = None
    
    def read_new_card(self):
        """Read a new card"""
        # This function would interact with actual NFC reader
        # Currently using a simple simulation
        self.status_label.setText("Reading card... Scan card on reader")
        self.status_label.setStyleSheet("font-size: 16px; color: #FF9800;")
        
        # Simulate card reading after 3 seconds
        QTimer.singleShot(3000, self.simulate_card_read)
    
    def simulate_card_read(self):
        """Simulate card reading"""
        import random
        # Generate a random card ID for simulation
        self.card_id = ''.join([random.choice('0123456789ABCDEF') for _ in range(8)])
        
        self.status_label.setText(f"Card read successfully. Card ID: {self.card_id}")
        self.status_label.setStyleSheet("font-size: 16px; color: #4CAF50;")
    
    def save_card(self):
        """Save card data"""
        student_id = self.student_id_input.text().strip()
        name = self.name_input.text().strip()
        
        if not student_id:
            QMessageBox.warning(self, "Error", "Student ID is required")
            return
        
        if not name:
            QMessageBox.warning(self, "Error", "Student name is required")
            return
        
        if not self.card_id:
            QMessageBox.warning(self, "Error", "You must scan a card first")
            return
        
        # Determine card type
        card_type = "admin" if self.card_type_admin.isChecked() else "student"
        
        # Add student if not exists
        if card_type == "admin":
            # For admin cards, set faculty and level appropriately
            student_added = add_new_student(student_id, name, "Security", "Gate Security", "Staff")
        else:
            # For student cards, just add the basic info
            student_added = add_new_student(student_id, name)
        
        # Add card with appropriate type
        card_added = add_new_card(self.card_id, student_id, card_type)
        
        if card_added:
            card_type_text = "Admin" if card_type == "admin" else "Student"
            QMessageBox.information(self, "Success", f"{card_type_text} card saved successfully")
            
            # Reset form
            self.student_id_input.clear()
            self.name_input.clear()
            self.card_id = None
            self.card_type_student.setChecked(True)
            self.status_label.setText("Ready to scan card")
            self.status_label.setStyleSheet("font-size: 16px; color: #1A237E;")
        else:
            QMessageBox.warning(self, "Error", "Card already exists or could not be saved")


class SmartGateApp(QMainWindow):
    """Main application for the smart gate"""
    
    def __init__(self):
        super().__init__()
        
        # Setup main window
        self.setWindowTitle("Smart Entry Gate")
        self.setGeometry(0, 0, 800, 480)  # Suitable for Raspberry Pi 7" display
        self.setStyleSheet("background-color: #F5F5F5;")
        
        # Create screen stack
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)
        
        # Create screens
        self.main_screen = MainScreen(self)
        self.student_info_screen = StudentInfoScreen(self)
        self.admin_screen = AdminScreen(self)
        
        # Add screens to stack
        self.stacked_widget.addWidget(self.main_screen)
        self.stacked_widget.addWidget(self.student_info_screen)
        self.stacked_widget.addWidget(self.admin_screen)
        
        # Setup inactivity timer for automatic return to main screen
        self.inactivity_timer = QTimer(self)
        self.inactivity_timer.timeout.connect(self.show_main_screen)
        
        # Show main screen
        self.show_main_screen()
    
    def show_main_screen(self):
        """Show main screen"""
        self.stacked_widget.setCurrentWidget(self.main_screen)
        self.reset_inactivity_timer()
    
    def show_student_info_screen(self, student_data=None):
        """Show student info screen"""
        if student_data:
            self.student_info_screen.update_student_info(student_data)
        self.stacked_widget.setCurrentWidget(self.student_info_screen)
        self.reset_inactivity_timer()
    
    def show_admin_screen(self):
        """Show the admin screen without password"""
        # Direct access without password
        self.stacked_widget.setCurrentWidget(self.admin_screen)
        self.reset_inactivity_timer()
    
    def reset_inactivity_timer(self):
        """Reset inactivity timer"""
        self.inactivity_timer.stop()
        self.inactivity_timer.start(300000)  # 5 minutes
    
    def read_nfc_card(self):
        """Read NFC card (would be implemented with actual hardware)"""
        # This function would interact with actual NFC reader
        # Currently using a simple simulation for testing
        pass

# Flask thread
def run_flask():
    """Run Flask app in a separate thread"""
    app.run(host='0.0.0.0', port=5000)

def main():
    """Main function"""
    # Setup database
    setup_database()
    
    # Create placeholder images
    try:
        create_placeholder_images()
    except ImportError:
        print("PIL not installed, skipping placeholder image creation")
    
    # Create Flask templates
    create_flask_templates()
    
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Start PyQt5 application
    app_qt = QApplication(sys.argv)
    window = SmartGateApp()
    window.show()
    sys.exit(app_qt.exec_())

if __name__ == "__main__":
    main()
