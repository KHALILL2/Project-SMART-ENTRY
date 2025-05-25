#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Smart Gate System - Main Application
Integrates both the gate GUI and web application with SQLite database
"""

import sys
import os
import sqlite3
import datetime
import threading
import webbrowser
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QPushButton, QStackedWidget, 
                            QFrame, QDialog, QLineEdit, QMessageBox, QGridLayout)
from PyQt5.QtGui import QPixmap, QFont, QIcon, QColor
from PyQt5.QtCore import Qt, QTimer, QSize

# Flask imports for web application
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.serving import make_server

# Create necessary directories
os.makedirs('assets', exist_ok=True)
os.makedirs('database', exist_ok=True)

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
        conn.close()
        return False

# GUI Classes for the Gate Application
class MainScreen(QWidget):
    """Main waiting screen for the gate"""
    
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
        
        # University logo
        logo_layout = QHBoxLayout()
        logo_label = QLabel()
        # Use a placeholder for the logo (should be replaced with actual logo)
        logo_pixmap = QPixmap("assets/university_logo_placeholder.png")
        if logo_pixmap.isNull():
            # If image not found, use text instead
            logo_label.setText("UNIVERSITY LOGO")
            logo_label.setAlignment(Qt.AlignCenter)
            logo_label.setStyleSheet("font-size: 24px; color: #1A237E;")
        else:
            logo_pixmap = logo_pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(logo_pixmap)
            logo_label.setAlignment(Qt.AlignCenter)
        
        logo_layout.addStretch()
        logo_layout.addWidget(logo_label)
        logo_layout.addStretch()
        
        # Gate title
        title_label = QLabel("Smart Entry Gate")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 32px; font-weight: bold; color: #1A237E; margin: 20px;")
        
        # Instructions
        instruction_label = QLabel("Scan your card to enter")
        instruction_label.setAlignment(Qt.AlignCenter)
        instruction_label.setStyleSheet("font-size: 24px; color: #2196F3; margin: 10px;")
        
        # Admin button
        admin_layout = QHBoxLayout()
        admin_layout.addStretch()
        
        admin_button = QPushButton("Admin")
        admin_button.setFixedSize(120, 50)
        admin_button.setStyleSheet("""
            QPushButton {
                background-color: #607D8B;
                color: white;
                border-radius: 5px;
                font-size: 18px;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #455A64;
            }
            QPushButton:pressed {
                background-color: #37474F;
            }
        """)
        admin_button.clicked.connect(self.show_admin_screen)
        
        admin_layout.addWidget(admin_button)
        
        # Add elements to main layout
        main_layout.addLayout(logo_layout)
        main_layout.addWidget(title_label)
        main_layout.addStretch()
        main_layout.addWidget(instruction_label)
        main_layout.addStretch()
        main_layout.addLayout(admin_layout)
        
        self.setLayout(main_layout)
    
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
        
        # Screen title
        title_label = QLabel("System Administration")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 28px; font-weight: bold; color: #1A237E; margin: 10px;")
        
        # Function buttons
        functions_layout = QGridLayout()
        functions_layout.setSpacing(20)
        
        # Frame for first set of function buttons
        functions_frame1 = QFrame()
        functions_frame1.setFrameShape(QFrame.Box)
        functions_frame1.setFrameShadow(QFrame.Raised)
        functions_frame1.setLineWidth(1)
        functions_frame1.setStyleSheet("border: 1px solid #BDBDBD; background-color: #EEEEEE;")
        
        functions_frame1_layout = QHBoxLayout(functions_frame1)
        functions_frame1_layout.setContentsMargins(20, 20, 20, 20)
        functions_frame1_layout.setSpacing(20)
        
        # Test components button
        test_components_button = QPushButton("Test Components")
        test_components_button.setFixedSize(200, 80)
        test_components_button.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                border-radius: 5px;
                font-size: 18px;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
            QPushButton:pressed {
                background-color: #EF6C00;
            }
        """)
        test_components_button.clicked.connect(self.show_test_components_dialog)
        
        # Add new card button
        add_card_button = QPushButton("Add New Card")
        add_card_button.setFixedSize(200, 80)
        add_card_button.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border-radius: 5px;
                font-size: 18px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #1565C0;
            }
        """)
        add_card_button.clicked.connect(self.show_add_card_dialog)
        
        functions_frame1_layout.addWidget(test_components_button)
        functions_frame1_layout.addWidget(add_card_button)
        
        # Frame for second set of function buttons
        functions_frame2 = QFrame()
        functions_frame2.setFrameShape(QFrame.Box)
        functions_frame2.setFrameShadow(QFrame.Raised)
        functions_frame2.setLineWidth(1)
        functions_frame2.setStyleSheet("border: 1px solid #BDBDBD; background-color: #EEEEEE;")
        
        functions_frame2_layout = QHBoxLayout(functions_frame2)
        functions_frame2_layout.setContentsMargins(20, 20, 20, 20)
        functions_frame2_layout.setSpacing(20)
        
        # Test valid entry button
        test_valid_button = QPushButton("Test Valid Entry")
        test_valid_button.setFixedSize(200, 80)
        test_valid_button.setStyleSheet("""
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
        test_valid_button.clicked.connect(self.test_valid_entry)
        
        # Test invalid entry button
        test_invalid_button = QPushButton("Test Invalid Entry")
        test_invalid_button.setFixedSize(200, 80)
        test_invalid_button.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border-radius: 5px;
                font-size: 18px;
            }
            QPushButton:hover {
                background-color: #E53935;
            }
            QPushButton:pressed {
                background-color: #D32F2F;
            }
        """)
        test_invalid_button.clicked.connect(self.test_invalid_entry)
        
        functions_frame2_layout.addWidget(test_valid_button)
        functions_frame2_layout.addWidget(test_invalid_button)
        
        # Web interface button
        web_interface_button = QPushButton("Open Web Interface")
        web_interface_button.setFixedSize(200, 60)
        web_interface_button.setStyleSheet("""
            QPushButton {
                background-color: #9C27B0;
                color: white;
                border-radius: 5px;
                font-size: 18px;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
            QPushButton:pressed {
                background-color: #6A1B9A;
            }
        """)
        web_interface_button.clicked.connect(self.open_web_interface)
        
        # Add elements to main layout
        main_layout.addLayout(header_layout)
        main_layout.addWidget(title_label)
        main_layout.addWidget(functions_frame1)
        main_layout.addWidget(functions_frame2)
        main_layout.addStretch()
        main_layout.addWidget(web_interface_button, 0, Qt.AlignCenter)
        
        self.setLayout(main_layout)
    
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
    
    def show_test_components_dialog(self):
        """Show test components dialog"""
        dialog = TestComponentsDialog(self)
        dialog.exec_()
    
    def show_add_card_dialog(self):
        """Show add new card dialog"""
        dialog = AddCardDialog(self)
        dialog.exec_()
    
    def test_valid_entry(self):
        """Test valid entry"""
        # Sample student data for testing
        student_data = {
            "id": "20210001",
            "name": "John Smith",
            "faculty": "Engineering",
            "program": "Computer Engineering",
            "level": "3rd Year",
            "image_path": "assets/student1.png",
            "card_id": "A1B2C3D4",
            "valid": True
        }
        
        if self.parent:
            self.parent.show_student_info_screen(student_data)
    
    def test_invalid_entry(self):
        """Test invalid entry"""
        # Sample student data for testing
        student_data = {
            "id": "00000000",
            "name": "Unknown",
            "faculty": "N/A",
            "program": "N/A",
            "level": "N/A",
            "image_path": "assets/unknown_user.png",
            "card_id": "INVALID",
            "valid": False
        }
        
        if self.parent:
            self.parent.show_student_info_screen(student_data)
    
    def open_web_interface(self):
        """Open web interface in browser"""
        webbrowser.open('http://localhost:5000')


class TestComponentsDialog(QDialog):
    """Dialog for testing hardware components"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.init_ui()
    
    def init_ui(self):
        """Setup the user interface"""
        self.setWindowTitle("Test Components")
        self.setFixedSize(500, 400)
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
        title_label = QLabel("Test Components")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #1A237E; margin: 10px;")
        
        # Test buttons
        test_layout = QGridLayout()
        test_layout.setSpacing(15)
        
        # Green LED test button
        green_led_button = QPushButton("Test Green LED")
        green_led_button.setFixedSize(180, 60)
        green_led_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 5px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
            QPushButton:pressed {
                background-color: #2E7D32;
            }
        """)
        green_led_button.clicked.connect(self.test_green_led)
        
        # Red LED test button
        red_led_button = QPushButton("Test Red LED")
        red_led_button.setFixedSize(180, 60)
        red_led_button.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border-radius: 5px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #E53935;
            }
            QPushButton:pressed {
                background-color: #D32F2F;
            }
        """)
        red_led_button.clicked.connect(self.test_red_led)
        
        # Buzzer test button
        buzzer_button = QPushButton("Test Buzzer")
        buzzer_button.setFixedSize(180, 60)
        buzzer_button.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                border-radius: 5px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
            QPushButton:pressed {
                background-color: #EF6C00;
            }
        """)
        buzzer_button.clicked.connect(self.test_buzzer)
        
        # Servo test button
        servo_button = QPushButton("Test Servo")
        servo_button.setFixedSize(180, 60)
        servo_button.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border-radius: 5px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #1565C0;
            }
        """)
        servo_button.clicked.connect(self.test_servo)
        
        # NFC reader test button
        nfc_button = QPushButton("Test NFC Reader")
        nfc_button.setFixedSize(180, 60)
        nfc_button.setStyleSheet("""
            QPushButton {
                background-color: #9C27B0;
                color: white;
                border-radius: 5px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
            QPushButton:pressed {
                background-color: #6A1B9A;
            }
        """)
        nfc_button.clicked.connect(self.test_nfc_reader)
        
        test_layout.addWidget(green_led_button, 0, 0)
        test_layout.addWidget(red_led_button, 0, 1)
        test_layout.addWidget(buzzer_button, 1, 0)
        test_layout.addWidget(servo_button, 1, 1)
        test_layout.addWidget(nfc_button, 2, 0, 1, 2, Qt.AlignCenter)
        
        # Test status
        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.Panel)
        status_frame.setFrameShadow(QFrame.Sunken)
        status_frame.setLineWidth(1)
        status_frame.setStyleSheet("border: 1px solid #BDBDBD; background-color: white;")
        
        status_layout = QVBoxLayout(status_frame)
        self.status_label = QLabel("Ready for testing")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 16px; color: #1A237E;")
        status_layout.addWidget(self.status_label)
        
        # Add elements to main layout
        main_layout.addLayout(close_layout)
        main_layout.addWidget(title_label)
        main_layout.addLayout(test_layout)
        main_layout.addWidget(status_frame)
        
        self.setLayout(main_layout)
    
    def test_green_led(self):
        """Test green LED"""
        # This function would interact with actual hardware components
        # Currently using a simple simulation
        self.status_label.setText("Testing Green LED...")
        QTimer.singleShot(2000, lambda: self.status_label.setText("Green LED test completed successfully"))
        print("Testing Green LED")
    
    def test_red_led(self):
        """Test red LED"""
        # This function would interact with actual hardware components
        # Currently using a simple simulation
        self.status_label.setText("Testing Red LED...")
        QTimer.singleShot(2000, lambda: self.status_label.setText("Red LED test completed successfully"))
        print("Testing Red LED")
    
    def test_buzzer(self):
        """Test buzzer"""
        # This function would interact with actual hardware components
        # Currently using a simple simulation
        self.status_label.setText("Testing Buzzer...")
        QTimer.singleShot(2000, lambda: self.status_label.setText("Buzzer test completed successfully"))
        print("Testing Buzzer")
    
    def test_servo(self):
        """Test servo"""
        # This function would interact with actual hardware components
        # Currently using a simple simulation
        self.status_label.setText("Testing Servo...")
        QTimer.singleShot(2000, lambda: self.status_label.setText("Servo test completed successfully"))
        print("Testing Servo")
    
    def test_nfc_reader(self):
        """Test NFC reader"""
        # This function would interact with actual hardware components
        # Currently using a simple simulation
        self.status_label.setText("Testing NFC Reader... Scan a card to test")
        QTimer.singleShot(5000, lambda: self.status_label.setText("NFC Reader test completed successfully"))
        print("Testing NFC Reader")


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
        self.reset_inactivity_timer()

        # Show main screen
        self.show_main_screen()
        
        # Simulate NFC reader (for testing only)
        self.nfc_timer = QTimer(self)
        self.nfc_timer.timeout.connect(self.simulate_nfc_read)
        self.nfc_timer.start(30000)  # Every 30 seconds (for testing only)
    
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
        """Show admin screen"""
        # Check password
        password, ok = self.get_admin_password()
        if ok and password == "admin":  # Simple password for testing
            self.stacked_widget.setCurrentWidget(self.admin_screen)
            self.reset_inactivity_timer()
        else:
            QMessageBox.warning(self, "Error", "Incorrect password")
    
    def get_admin_password(self):
        """Get admin password"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Login")
        dialog.setFixedSize(300, 150)
        
        layout = QVBoxLayout()
        
        password_label = QLabel("Password:")
        password_input = QLineEdit()
        password_input.setEchoMode(QLineEdit.Password)
        
        button_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        cancel_button = QPushButton("Cancel")
        
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        
        layout.addWidget(password_label)
        layout.addWidget(password_input)
        layout.addLayout(button_layout)
        
        dialog.setLayout(layout)
        
        ok_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        
        result = dialog.exec_()
        
        return password_input.text(), result == QDialog.Accepted
    
    def reset_inactivity_timer(self):
        """Reset inactivity timer"""
        self.inactivity_timer.stop()
        self.inactivity_timer.start(30000)  # 30 seconds
    
    def simulate_nfc_read(self):
        """Simulate NFC card reading (for testing only)"""
        # This function is for testing only, will be replaced with actual NFC reading
        if self.stacked_widget.currentWidget() == self.main_screen:
            # Randomly select a card ID from the database
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT card_id FROM cards ORDER BY RANDOM() LIMIT 1")
            result = cursor.fetchone()
            conn.close()
            
            if result:
                card_id = result[0]
                student_data = get_student_by_card(card_id)
                if student_data:
                    student_data['card_id'] = card_id
                    self.show_student_info_screen(student_data)


# Flask Web Application
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'smart_gate_secret_key'
db = SQLAlchemy(app)

# Define Flask routes
@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('index.html')

@app.route('/students')
def students():
    """Students page"""
    return render_template('students.html')

@app.route('/entries')
def entries():
    """Entry logs page"""
    return render_template('entries.html')

@app.route('/stats')
def stats():
    """Statistics page"""
    return render_template('stats.html')

# API routes
@app.route('/api/students')
def api_students():
    """API endpoint for students data"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT s.id, s.name, s.faculty, s.program, s.level, s.image_path
    FROM students s
    ORDER BY s.name
    ''')
    
    students = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get total entries
    cursor.execute("SELECT COUNT(*) FROM entry_logs")
    total_entries = cursor.fetchone()[0]
    
    # Get successful entries
    cursor.execute("SELECT COUNT(*) FROM entry_logs WHERE status = 'success'")
    success_entries = cursor.fetchone()[0]
    
    # Get failed entries
    cursor.execute("SELECT COUNT(*) FROM entry_logs WHERE status = 'failure'")
    failed_entries = cursor.fetchone()[0]
    
    # Get today's entries
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    cursor.execute("SELECT COUNT(*) FROM entry_logs WHERE timestamp LIKE ?", (f'{today}%',))
    today_entries = cursor.fetchone()[0]
    
    conn.close()
    
    stats = {
        'total_entries': total_entries,
        'success_entries': success_entries,
        'failed_entries': failed_entries,
        'today_entries': today_entries,
        'success_rate': (success_entries / total_entries * 100) if total_entries > 0 else 0
    }
    
    return jsonify({'status': 'success', 'data': stats})

# Create Flask templates directory and basic templates
def create_flask_templates():
    """Create basic Flask templates for the web application"""
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static/css', exist_ok=True)
    os.makedirs('static/js', exist_ok=True)
    
    # Base template
    with open('templates/base.html', 'w') as f:
        f.write('''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Smart Gate System{% endblock %}</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    {% block extra_css %}{% endblock %}
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
        <div class="container">
            <a class="navbar-brand" href="/">Smart Gate System</a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav">
                    <li class="nav-item">
                        <a class="nav-link {% if request.path == '/' %}active{% endif %}" href="/">Dashboard</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link {% if request.path == '/students' %}active{% endif %}" href="/students">Students</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link {% if request.path == '/entries' %}active{% endif %}" href="/entries">Entry Logs</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link {% if request.path == '/stats' %}active{% endif %}" href="/stats">Statistics</a>
                    </li>
                </ul>
            </div>
        </div>
    </nav>

    <div class="container mt-4">
        {% block content %}{% endblock %}
    </div>

    <footer class="mt-5 py-3 bg-light text-center">
        <div class="container">
            <p class="mb-0">Smart Gate System &copy; 2025</p>
        </div>
    </footer>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="{{ url_for('static', filename='js/main.js') }}"></script>
    {% block extra_js %}{% endblock %}
</body>
</html>''')
    
    # Index template
    with open('templates/index.html', 'w') as f:
        f.write('''{% extends "base.html" %}

{% block title %}Dashboard - Smart Gate System{% endblock %}

{% block content %}
<h1 class="mb-4">Dashboard</h1>

<div class="row">
    <div class="col-md-6">
        <div class="card mb-4">
            <div class="card-header">
                <h5 class="card-title mb-0">Recent Entries</h5>
            </div>
            <div class="card-body">
                <div class="table-responsive">
                    <table class="table table-striped" id="recent-entries-table">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Time</th>
                                <th>Name</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td colspan="4" class="text-center">Loading...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
    
    <div class="col-md-6">
        <div class="card mb-4">
            <div class="card-header">
                <h5 class="card-title mb-0">Today's Statistics</h5>
            </div>
            <div class="card-body">
                <div class="row">
                    <div class="col-6">
                        <div class="card bg-success text-white mb-3">
                            <div class="card-body text-center">
                                <h5 class="card-title">Successful Entries</h5>
                                <h2 id="success-count">0</h2>
                            </div>
                        </div>
                    </div>
                    <div class="col-6">
                        <div class="card bg-danger text-white mb-3">
                            <div class="card-body text-center">
                                <h5 class="card-title">Failed Entries</h5>
                                <h2 id="failed-count">0</h2>
                            </div>
                        </div>
                    </div>
                </div>
                <canvas id="entry-chart" height="200"></canvas>
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    // Load recent entries
    fetch('/api/entries')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const tableBody = document.querySelector('#recent-entries-table tbody');
                tableBody.innerHTML = '';
                
                data.data.slice(0, 5).forEach(entry => {
                    const date = new Date(entry.timestamp);
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td>${date.toLocaleDateString()}</td>
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
                document.getElementById('success-count').textContent = data.data.success_entries;
                document.getElementById('failed-count').textContent = data.data.failed_entries;
                
                // Create chart
                const ctx = document.getElementById('entry-chart').getContext('2d');
                new Chart(ctx, {
                    type: 'pie',
                    data: {
                        labels: ['Successful', 'Failed'],
                        datasets: [{
                            data: [data.data.success_entries, data.data.failed_entries],
                            backgroundColor: ['#28a745', '#dc3545']
                        }]
                    },
                    options: {
                        responsive: true,
                        plugins: {
                            legend: {
                                position: 'bottom'
                            }
                        }
                    }
                });
            }
        });
});
</script>
{% endblock %}''')
    
    # Students template
    with open('templates/students.html', 'w') as f:
        f.write('''{% extends "base.html" %}

{% block title %}Students - Smart Gate System{% endblock %}

{% block content %}
<h1 class="mb-4">Students</h1>

<div class="card">
    <div class="card-header d-flex justify-content-between align-items-center">
        <h5 class="card-title mb-0">Student List</h5>
        <div class="input-group" style="max-width: 300px;">
            <input type="text" id="search-input" class="form-control" placeholder="Search...">
            <button class="btn btn-outline-secondary" type="button" id="search-button">
                <i class="bi bi-search"></i> Search
            </button>
        </div>
    </div>
    <div class="card-body">
        <div class="table-responsive">
            <table class="table table-striped" id="students-table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Name</th>
                        <th>Faculty</th>
                        <th>Program</th>
                        <th>Level</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td colspan="6" class="text-center">Loading...</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    // Load students
    fetch('/api/students')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const tableBody = document.querySelector('#students-table tbody');
                tableBody.innerHTML = '';
                
                data.data.forEach(student => {
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td>${student.id}</td>
                        <td>${student.name}</td>
                        <td>${student.faculty || '-'}</td>
                        <td>${student.program || '-'}</td>
                        <td>${student.level || '-'}</td>
                        <td>
                            <button class="btn btn-sm btn-primary view-btn" data-id="${student.id}">View</button>
                        </td>
                    `;
                    tableBody.appendChild(row);
                });
                
                // Add event listeners to view buttons
                document.querySelectorAll('.view-btn').forEach(button => {
                    button.addEventListener('click', function() {
                        const studentId = this.getAttribute('data-id');
                        alert(`View student details for ID: ${studentId}`);
                        // In a real application, this would open a modal or navigate to a student details page
                    });
                });
            }
        });
    
    // Search functionality
    document.getElementById('search-button').addEventListener('click', function() {
        const searchTerm = document.getElementById('search-input').value.toLowerCase();
        const rows = document.querySelectorAll('#students-table tbody tr');
        
        rows.forEach(row => {
            const text = row.textContent.toLowerCase();
            row.style.display = text.includes(searchTerm) ? '' : 'none';
        });
    });
    
    // Search on Enter key
    document.getElementById('search-input').addEventListener('keyup', function(event) {
        if (event.key === 'Enter') {
            document.getElementById('search-button').click();
        }
    });
});
</script>
{% endblock %}''')
    
    # Entries template
    with open('templates/entries.html', 'w') as f:
        f.write('''{% extends "base.html" %}

{% block title %}Entry Logs - Smart Gate System{% endblock %}

{% block content %}
<h1 class="mb-4">Entry Logs</h1>

<div class="card">
    <div class="card-header">
        <h5 class="card-title mb-3">Filter Options</h5>
        <div class="row g-3">
            <div class="col-md-3">
                <label class="form-label">Date Range</label>
                <div class="input-group">
                    <input type="date" id="date-from" class="form-control">
                    <span class="input-group-text">to</span>
                    <input type="date" id="date-to" class="form-control">
                </div>
            </div>
            <div class="col-md-3">
                <label class="form-label">Gate</label>
                <select id="gate-filter" class="form-select">
                    <option value="">All Gates</option>
                    <option value="Main Gate">Main Gate</option>
                    <option value="Library Gate">Library Gate</option>
                </select>
            </div>
            <div class="col-md-3">
                <label class="form-label">Status</label>
                <select id="status-filter" class="form-select">
                    <option value="">All</option>
                    <option value="success">Success</option>
                    <option value="failure">Failure</option>
                </select>
            </div>
            <div class="col-md-3 d-flex align-items-end">
                <button id="apply-filter" class="btn btn-primary w-100">Apply Filter</button>
            </div>
        </div>
    </div>
    <div class="card-body">
        <div class="table-responsive">
            <table class="table table-striped" id="entries-table">
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Time</th>
                        <th>Student ID</th>
                        <th>Name</th>
                        <th>Gate</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td colspan="6" class="text-center">Loading...</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>
    <div class="card-footer d-flex justify-content-between align-items-center">
        <button id="export-excel" class="btn btn-success">Export to Excel</button>
        <nav aria-label="Page navigation">
            <ul class="pagination mb-0">
                <li class="page-item disabled">
                    <a class="page-link" href="#" tabindex="-1">Previous</a>
                </li>
                <li class="page-item active"><a class="page-link" href="#">1</a></li>
                <li class="page-item"><a class="page-link" href="#">2</a></li>
                <li class="page-item"><a class="page-link" href="#">3</a></li>
                <li class="page-item">
                    <a class="page-link" href="#">Next</a>
                </li>
            </ul>
        </nav>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    // Set default date range (last 7 days)
    const today = new Date();
    const lastWeek = new Date();
    lastWeek.setDate(today.getDate() - 7);
    
    document.getElementById('date-to').valueAsDate = today;
    document.getElementById('date-from').valueAsDate = lastWeek;
    
    // Load entries
    loadEntries();
    
    // Apply filter button
    document.getElementById('apply-filter').addEventListener('click', loadEntries);
    
    // Export to Excel button (simulation)
    document.getElementById('export-excel').addEventListener('click', function() {
        alert('Export to Excel functionality would be implemented here');
    });
    
    function loadEntries() {
        fetch('/api/entries')
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    const tableBody = document.querySelector('#entries-table tbody');
                    tableBody.innerHTML = '';
                    
                    // Apply filters
                    const dateFrom = document.getElementById('date-from').value;
                    const dateTo = document.getElementById('date-to').value;
                    const gateFilter = document.getElementById('gate-filter').value;
                    const statusFilter = document.getElementById('status-filter').value;
                    
                    let filteredData = data.data;
                    
                    if (dateFrom && dateTo) {
                        filteredData = filteredData.filter(entry => {
                            const entryDate = new Date(entry.timestamp).toISOString().split('T')[0];
                            return entryDate >= dateFrom && entryDate <= dateTo;
                        });
                    }
                    
                    if (gateFilter) {
                        filteredData = filteredData.filter(entry => entry.gate === gateFilter);
                    }
                    
                    if (statusFilter) {
                        filteredData = filteredData.filter(entry => entry.status === statusFilter);
                    }
                    
                    if (filteredData.length === 0) {
                        tableBody.innerHTML = '<tr><td colspan="6" class="text-center">No entries found</td></tr>';
                        return;
                    }
                    
                    filteredData.forEach(entry => {
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
                }
            });
    }
});
</script>
{% endblock %}''')
    
    # Stats template
    with open('templates/stats.html', 'w') as f:
        f.write('''{% extends "base.html" %}

{% block title %}Statistics - Smart Gate System{% endblock %}

{% block content %}
<h1 class="mb-4">Statistics</h1>

<div class="row">
    <div class="col-md-3">
        <div class="card mb-4 text-center">
            <div class="card-body">
                <h5 class="card-title">Total Entries</h5>
                <h2 id="total-entries">0</h2>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card mb-4 text-center bg-success text-white">
            <div class="card-body">
                <h5 class="card-title">Success Rate</h5>
                <h2 id="success-rate">0%</h2>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card mb-4 text-center">
            <div class="card-body">
                <h5 class="card-title">Today's Entries</h5>
                <h2 id="today-entries">0</h2>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card mb-4 text-center">
            <div class="card-body">
                <h5 class="card-title">Period</h5>
                <select id="period-select" class="form-select mt-2">
                    <option value="day">Today</option>
                    <option value="week" selected>This Week</option>
                    <option value="month">This Month</option>
                </select>
            </div>
        </div>
    </div>
</div>

<div class="row">
    <div class="col-md-6">
        <div class="card mb-4">
            <div class="card-header">
                <h5 class="card-title mb-0">Entry Status Distribution</h5>
            </div>
            <div class="card-body">
                <canvas id="status-chart" height="300"></canvas>
            </div>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card mb-4">
            <div class="card-header">
                <h5 class="card-title mb-0">Entries by Time of Day</h5>
            </div>
            <div class="card-body">
                <canvas id="time-chart" height="300"></canvas>
            </div>
        </div>
    </div>
</div>

<div class="card mb-4">
    <div class="card-header">
        <h5 class="card-title mb-0">Entries Over Time</h5>
    </div>
    <div class="card-body">
        <canvas id="trend-chart" height="200"></canvas>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    // Load statistics
    loadStats();
    
    // Period select change event
    document.getElementById('period-select').addEventListener('change', loadStats);
    
    function loadStats() {
        fetch('/api/stats')
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    // Update summary numbers
                    document.getElementById('total-entries').textContent = data.data.total_entries;
                    document.getElementById('success-rate').textContent = data.data.success_rate.toFixed(1) + '%';
                    document.getElementById('today-entries').textContent = data.data.today_entries;
                    
                    // Create status distribution chart
                    const statusCtx = document.getElementById('status-chart').getContext('2d');
                    new Chart(statusCtx, {
                        type: 'pie',
                        data: {
                            labels: ['Successful', 'Failed'],
                            datasets: [{
                                data: [data.data.success_entries, data.data.failed_entries],
                                backgroundColor: ['#4CAF50', '#F44336']
                            }]
                        },
                        options: {
                            responsive: true,
                            plugins: {
                                legend: {
                                    position: 'bottom'
                                }
                            }
                        }
                    });
                    
                    // Create time of day chart (simulated data)
                    const timeCtx = document.getElementById('time-chart').getContext('2d');
                    new Chart(timeCtx, {
                        type: 'bar',
                        data: {
                            labels: ['8-9', '9-10', '10-11', '11-12', '12-13', '13-14', '14-15', '15-16', '16-17'],
                            datasets: [{
                                label: 'Number of Entries',
                                data: [15, 25, 18, 30, 40, 35, 28, 20, 10],
                                backgroundColor: '#2196F3'
                            }]
                        },
                        options: {
                            responsive: true,
                            scales: {
                                y: {
                                    beginAtZero: true
                                }
                            }
                        }
                    });
                    
                    // Create trend chart (simulated data)
                    const trendCtx = document.getElementById('trend-chart').getContext('2d');
                    const period = document.getElementById('period-select').value;
                    
                    let labels, successData, failureData;
                    
                    if (period === 'day') {
                        labels = ['8:00', '10:00', '12:00', '14:00', '16:00', '18:00'];
                        successData = [5, 12, 18, 15, 10, 5];
                        failureData = [1, 2, 3, 2, 1, 0];
                    } else if (period === 'week') {
                        labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
                        successData = [45, 50, 60, 55, 70, 30, 20];
                        failureData = [5, 7, 8, 6, 10, 3, 2];
                    } else {
                        labels = ['Week 1', 'Week 2', 'Week 3', 'Week 4'];
                        successData = [180, 200, 220, 190];
                        failureData = [20, 25, 30, 22];
                    }
                    
                    new Chart(trendCtx, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: [
                                {
                                    label: 'Successful',
                                    data: successData,
                                    borderColor: '#4CAF50',
                                    backgroundColor: 'rgba(76, 175, 80, 0.1)',
                                    fill: true
                                },
                                {
                                    label: 'Failed',
                                    data: failureData,
                                    borderColor: '#F44336',
                                    backgroundColor: 'rgba(244, 67, 54, 0.1)',
                                    fill: true
                                }
                            ]
                        },
                        options: {
                            responsive: true,
                            scales: {
                                y: {
                                    beginAtZero: true
                                }
                            }
                        }
                    });
                }
            });
    }
});
</script>
{% endblock %}''')
    
    # CSS file
    with open('static/css/style.css', 'w') as f:
        f.write('''/* Custom styles for Smart Gate System */

body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background-color: #f8f9fa;
}

.navbar {
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.card {
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
    border-radius: 8px;
    border: 1px solid rgba(0, 0, 0, 0.125);
    margin-bottom: 20px;
}

.card-header {
    background-color: #f8f9fa;
    border-bottom: 1px solid rgba(0, 0, 0, 0.125);
    padding: 15px 20px;
}

.card-body {
    padding: 20px;
}

.table th {
    font-weight: 600;
    color: #495057;
}

.badge {
    padding: 6px 10px;
    font-weight: 500;
}

.btn {
    border-radius: 5px;
    padding: 8px 16px;
}

.btn-sm {
    padding: 4px 8px;
}

footer {
    border-top: 1px solid #e9ecef;
}''')
    
    # JavaScript file
    with open('static/js/main.js', 'w') as f:
        f.write('''// Main JavaScript file for Smart Gate System

// Auto-refresh data every 30 seconds
setInterval(function() {
    if (window.location.pathname === '/') {
        // Refresh dashboard data
        console.log('Refreshing dashboard data...');
        // This would be implemented with actual refresh logic
    }
}, 30000);

// Format dates for display
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString();
}

// Format times for display
function formatTime(dateString) {
    const date = new Date(dateString);
    return date.toLocaleTimeString();
}''')
    
    print("Flask templates created successfully.")

# Main function to run the application
def main():
    """Main function to run the application"""
    # Setup database
    setup_database()
    
    # Create Flask templates
    create_flask_templates()
    
    # Start Flask server in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Start PyQt application
    app = QApplication(sys.argv)
    window = SmartGateApp()
    window.show()
    sys.exit(app.exec_())

def run_flask():
    """Run Flask server"""
    server = make_server('0.0.0.0', 5000, app)
    server.serve_forever()

if __name__ == "__main__":
    main()
