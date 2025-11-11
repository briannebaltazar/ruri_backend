# backend/app.py
# Full working backend with admin, employee, client, attendance, products, chat (AI + human) +
# emoji-safe MySQL utf8mb4 setup. (Verbose comments + emoji markers as requested)
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_mysqldb import MySQL
from dotenv import load_dotenv
import os
import random
import bcrypt
from nlp_model import nlp_model_respond

# load environment vars from .env
load_dotenv()

app = Flask(__name__)

# ------------------------
# CORS - allow frontend only
# ------------------------
CORS(app, resources={r"/*": {"origins": ["http://localhost:3000", "http://127.0.0.1:3000"]}},
     supports_credentials=True)

# ------------------------
# MySQL config
# ------------------------
app.config['MYSQL_HOST'] = os.getenv("DB_HOST", "localhost")
app.config['MYSQL_USER'] = os.getenv("DB_USER", "root")
app.config['MYSQL_PASSWORD'] = os.getenv("DB_PASSWORD", "")
app.config['MYSQL_DB'] = os.getenv("DB_NAME", "ruri_club")
# flask_mysqldb will return tuples by default
mysql = MySQL(app)

# ------------------------
# ensure DB connection uses utf8mb4 so emojis won't break
# (run once inside app context)
# ------------------------
try:
    with app.app_context():
        cur = mysql.connection.cursor()
        cur.execute("SET NAMES utf8mb4;")
        cur.execute("SET CHARACTER SET utf8mb4;")
        cur.execute("SET character_set_connection=utf8mb4;")
        mysql.connection.commit()
        cur.close()
        print("✅ MySQL connection set to utf8mb4 (emoji-safe)")
except Exception as e:
    print("💥 MySQL encoding setup failed:", e)


# ------------------------
# helper: convert SQL row to dict (not used everywhere but handy)
# ------------------------
def row_to_dict(desc, row):
    return {desc[i][0]: row[i] for i in range(len(row))}


# ------------------------
# Root
# ------------------------
@app.route('/')
def home():
    return jsonify({"message": "✅ Ruri backend running"})


# ------------------------
# SIGNUP
# ------------------------
@app.route('/signup', methods=['OPTIONS', 'POST'])
def signup():
    if request.method == 'OPTIONS':
        return jsonify({"message": "ok"}), 200
    try:
        data = request.json or {}
        full_name = data.get('full_name')
        email = data.get('email')
        password = data.get('password')
        phone = data.get('phone', '')
        address = data.get('address', '')
        role = data.get('role', 'client')

        if not all([full_name, email, password]):
            return jsonify({"error": "Missing required fields"}), 400

        cur = mysql.connection.cursor()
        cur.execute("SELECT user_id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            cur.close()
            return jsonify({"error": "Email already exists"}), 409

        hashed_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cur.execute(
            "INSERT INTO users (full_name, email, password, phone, address, role) VALUES (%s,%s,%s,%s,%s,%s)",
            (full_name, email, hashed_pw, phone, address, role)
        )
        mysql.connection.commit()
        cur.close()

        return jsonify({"message": "Signup successful!"}), 201

    except Exception as e:
        print("💥 Signup error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


# ------------------------
# LOGIN
# ------------------------
@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.json or {}
        email = data.get('email')
        password = data.get('password', '')

        if not email or not password:
            return jsonify({"error": "Email & password required"}), 400

        cur = mysql.connection.cursor()
        cur.execute("SELECT user_id, full_name, email, password, role, phone, address FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        cur.close()

        if not row:
            return jsonify({"error": "Invalid credentials"}), 401

        user_id, full_name, email, stored_pw, role, phone, address = row

        # decode bytes if necessary
        if isinstance(stored_pw, (bytes, bytearray)):
            stored_pw = stored_pw.decode('utf-8')

        # bcrypt hashed?
        if isinstance(stored_pw, str) and stored_pw.startswith("$2"):
            if not bcrypt.checkpw(password.encode('utf-8'), stored_pw.encode('utf-8')):
                return jsonify({"error": "Invalid credentials"}), 401
        else:
            if password != stored_pw:
                return jsonify({"error": "Invalid credentials"}), 401

        return jsonify({
            "user_id": user_id,
            "full_name": full_name,
            "email": email,
            "role": role,
            "phone": phone or "",
            "address": address or "",
            "message": "Login successful"
        })

    except Exception as e:
        print("💥 Login error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


# ------------------------
# CLIENT → AI chat (primary chat endpoint)
# ------------------------
@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json or {}
        sender_id = data.get('sender_id')
        message = (data.get('message') or "").strip()

        if not sender_id or message == "":
            return jsonify({"error": "sender_id and message required"}), 400

        # Decide AI response (from nlp_model)
        response_text = nlp_model_respond(message)
        human_needed = False

        lower = message.lower()
        client_help_triggers = ["help", "support", "i need help", "human", "agent", "representative"]
        if any(t in lower for t in client_help_triggers):
            human_needed = True
            response_text = "Don't worry — I will connect you to a live agent now."

        # If AI's response suggests human, mark human_needed
        if isinstance(response_text, str) and ("speak with a live support" in response_text.lower() or "connect you to a live" in response_text.lower()):
            human_needed = True

        # Save client -> AI message and AI -> client response in chat_logs
        cur = mysql.connection.cursor()
        cur.execute(
            "INSERT INTO chat_logs (sender_id, receiver_id, message, chat_type) VALUES (%s,%s,%s,%s)",
            (sender_id, 10, message, 'client_ai')
        )
        cur.execute(
            "INSERT INTO chat_logs (sender_id, receiver_id, message, chat_type) VALUES (%s,%s,%s,%s)",
            (10, sender_id, response_text, 'client_ai')
        )
        mysql.connection.commit()
        cur.close()

        return jsonify({"response": response_text, "human_needed": human_needed})

    except Exception as e:
        print("💥 Chat error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


# ------------------------
# Client asks to connect to a human (assign employee)
# - picks from allowed employee user_ids (so you control which employees are used)
# - stores/updates client_assignments table
# - returns assigned employee id and name
# ------------------------
@app.route('/chat/request-human', methods=['POST'])
def request_human_support():
    try:
        data = request.json or {}
        client_id = data.get('user_id')
        if not client_id:
            return jsonify({"error": "user_id required"}), 400

        cur = mysql.connection.cursor()

        # Only choose from a fixed allowed set (adjust as needed)
        allowed_ids = (1, 2, 13, 14, 15)
        # fetch existing employees matching role & allowed ids
        cur.execute("SELECT user_id, full_name FROM users WHERE role='employee' AND user_id IN %s", (allowed_ids,))
        rows = cur.fetchall()
        employees = [r for r in rows]  # list of tuples (user_id, full_name)

        if not employees:
            cur.close()
            return jsonify({"error": "No employees available"}), 500

        assigned = random.choice(employees)
        assigned_employee_id = assigned[0]
        assigned_employee_name = assigned[1]

        # Insert or update client_assignments (client_id unique)
        # Ensure client_assignments has UNIQUE(client_id) in schema.
        cur.execute("""
            INSERT INTO client_assignments (client_id, employee_id)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE employee_id = VALUES(employee_id), assigned_at = CURRENT_TIMESTAMP
        """, (client_id, assigned_employee_id))

        # Add a short system trace log for transparency
        sys_msg = f"System: client {client_id} assigned to employee {assigned_employee_name} (id {assigned_employee_id})"
        cur.execute(
            "INSERT INTO chat_logs (sender_id, receiver_id, message, chat_type) VALUES (%s,%s,%s,%s)",
            (3, client_id, sys_msg, 'system')
        )

        mysql.connection.commit()
        cur.close()

        print(f"✅ Assigned employee {assigned_employee_id} ({assigned_employee_name}) to client {client_id}")
        return jsonify({"assigned_employee": assigned_employee_id, "assigned_name": assigned_employee_name})

    except Exception as e:
        print("💥 Human support error:", e)
        return jsonify({"error": str(e)}), 500


# ------------------------
# Fetch assignment for client
# ------------------------
@app.route('/assignment/<int:client_id>', methods=['GET'])
def get_assignment(client_id):
    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT u.full_name, u.user_id
            FROM client_assignments ca
            JOIN users u ON ca.employee_id = u.user_id
            WHERE ca.client_id = %s
            ORDER BY ca.assigned_at DESC
            LIMIT 1
        """, (client_id,))
        result = cur.fetchone()
        cur.close()

        if result:
            return jsonify({"employee_name": result[0], "employee_id": result[1]}), 200
        else:
            return jsonify({"error": "No assignment found"}), 404
    except Exception as e:
        print("💥 Error fetching assignment:", e)
        return jsonify({"error": "Internal Server Error"}), 500


# ------------------------
# Client -> Employee message endpoint (client sends message to assigned employee)
# - frontend ChatBox calls this when assignedEmployee exists
# ------------------------
@app.route('/chat/client/send', methods=['POST'])
def client_send_message():
    try:
        data = request.json or {}
        employee_id = data.get('employee_id')
        client_id = data.get('client_id')
        message = data.get('message')

        if not all([employee_id, client_id, message]):
            return jsonify({"error": "employee_id, client_id and message required"}), 400

        cur = mysql.connection.cursor()
        # log client -> employee
        cur.execute(
            "INSERT INTO chat_logs (sender_id, receiver_id, message, chat_type) VALUES (%s,%s,%s,%s)",
            (client_id, employee_id, message, 'client_employee')
        )

        # optionally add system note for employee inbox visibility (not required)
        sys_msg = f"System: Client {client_id} sent a message to you."
        cur.execute(
            "INSERT INTO chat_logs (sender_id, receiver_id, message, chat_type) VALUES (%s,%s,%s,%s)",
            (client_id, employee_id, sys_msg, 'client_employee')
        )

        mysql.connection.commit()
        cur.close()

        return jsonify({"status": "Message delivered to employee inbox"}), 201
    except Exception as e:
        print("💥 client_send_message error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


# ------------------------
# Employee endpoints
# ------------------------
@app.route('/employee/<int:employee_id>/assignments', methods=['GET'])
def get_employee_assignments(employee_id):
    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT a.client_id, u.full_name, a.assigned_at
            FROM client_assignments a
            JOIN users u ON a.client_id = u.user_id
            WHERE a.employee_id = %s
            ORDER BY a.assigned_at DESC
        """, (employee_id,))
        rows = cur.fetchall()
        clients = [{"user_id": r[0], "full_name": r[1], "assigned_at": str(r[2])} for r in rows]
        cur.close()
        return jsonify(clients)
    except Exception as e:
        print("💥 get_employee_assignments error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


@app.route('/chat/employee/<int:employee_id>/client/<int:client_id>', methods=['GET'])
def employee_get_chat_history(employee_id, client_id):
    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT sender_id, receiver_id, message, created_at, chat_type
            FROM chat_logs
            WHERE (sender_id=%s AND receiver_id=%s)
               OR (sender_id=%s AND receiver_id=%s)
            ORDER BY created_at ASC
        """, (client_id, employee_id, employee_id, client_id))
        rows = cur.fetchall()
        cur.close()
        chats = [{"sender_id": r[0], "receiver_id": r[1], "message": r[2], "created_at": str(r[3]), "chat_type": r[4]} for r in rows]
        return jsonify(chats)
    except Exception as e:
        print("💥 employee_get_chat_history error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


@app.route('/chat/employee/reply', methods=['POST'])
def employee_reply():
    try:
        data = request.json or {}
        employee_id = data.get('employee_id')
        client_id = data.get('client_id')
        message = data.get('message')

        if not all([employee_id, client_id, message]):
            return jsonify({"error": "employee_id, client_id and message required"}), 400

        cur = mysql.connection.cursor()
        cur.execute(
            "INSERT INTO chat_logs (sender_id, receiver_id, message, chat_type) VALUES (%s,%s,%s,%s)",
            (employee_id, client_id, message, 'employee_client')
        )
        mysql.connection.commit()
        cur.close()
        return jsonify({"status": "Message sent to client"}), 201

    except Exception as e:
        print("💥 employee_reply error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


# ------------------------
# ATTENDANCE
# ------------------------
@app.route('/attendance/mark', methods=['POST'])
def mark_attendance():
    try:
        data = request.json or {}
        employee_id = data.get('employee_id')
        status = data.get('status', 'Present')
        if not employee_id:
            return jsonify({"error": "Employee ID required"}), 400

        cur = mysql.connection.cursor()
        cur.execute("INSERT INTO attendance (employee_id, status, date) VALUES (%s,%s,CURDATE())", (employee_id, status))
        mysql.connection.commit()
        cur.close()
        return jsonify({"message": f"Attendance marked as {status} for today."}), 201
    except Exception as e:
        print("💥 mark_attendance error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


@app.route('/attendance/<int:employee_id>', methods=['GET'])
def get_attendance_records(employee_id):
    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT attendance_id, employee_id, date, time_in, time_out, status
            FROM attendance
            WHERE employee_id=%s
            ORDER BY date DESC
        """, (employee_id,))
        rows = cur.fetchall()
        cur.close()
        result = [{
            "attendance_id": r[0],
            "employee_id": r[1],
            "date": str(r[2]),
            "time_in": str(r[3]) if r[3] else None,
            "time_out": str(r[4]) if r[4] else None,
            "status": r[5]
        } for r in rows]
        return jsonify(result)
    except Exception as e:
        print("💥 get_attendance_records error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


# ------------------------
# ADMIN helper routes (summary, employees, chat)
# ------------------------
@app.route('/admin/summary', methods=['GET'])
def admin_summary():
    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE role='employee'")
        employees = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users WHERE role='client'")
        clients = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM orders")
        orders = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM attendance WHERE date = CURDATE() AND status = 'Present'")
        present_today = cur.fetchone()[0]
        cur.close()
        return jsonify({"employees": employees, "clients": clients, "orders": orders, "present_today": present_today})
    except Exception as e:
        print("💥 admin_summary error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


@app.route('/admin/employees', methods=['GET'])
def admin_get_employees():
    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT user_id, full_name, email FROM users WHERE role='employee'")
        rows = cur.fetchall()
        cur.close()
        return jsonify([{"user_id": r[0], "full_name": r[1], "email": r[2]} for r in rows])
    except Exception as e:
        print("💥 admin_get_employees error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


@app.route('/admin/chat/<int:employee_id>', methods=['GET'])
def admin_get_chat_history(employee_id):
    try:
        admin_id = 3
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT sender_id, receiver_id, message, created_at, chat_type
            FROM chat_logs
            WHERE (sender_id = %s AND receiver_id = %s)
               OR (sender_id = %s AND receiver_id = %s)
            ORDER BY created_at ASC
        """, (employee_id, admin_id, admin_id, employee_id))
        rows = cur.fetchall()
        cur.close()
        return jsonify([{"sender_id": r[0], "receiver_id": r[1], "message": r[2], "created_at": str(r[3]), "chat_type": r[4]} for r in rows])
    except Exception as e:
        print("💥 admin_get_chat_history error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


@app.route('/admin/chat/send', methods=['POST'])
def admin_send_message():
    try:
        data = request.json or {}
        admin_id = 3
        employee_id = data.get('employee_id')
        message = data.get('message')
        if not employee_id or not message:
            return jsonify({"error": "Employee ID and message required"}), 400
        cur = mysql.connection.cursor()
        cur.execute("INSERT INTO chat_logs (sender_id, receiver_id, message, chat_type) VALUES (%s,%s,%s,%s)",
                    (admin_id, employee_id, message, 'admin_employee'))
        mysql.connection.commit()
        cur.close()
        return jsonify({"message": "Message sent successfully"}), 201
    except Exception as e:
        print("💥 admin_send_message error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


@app.route('/admin/employee_ratings', methods=['GET'])
def admin_employee_ratings():
    ratings = [
        {"employee": "Alice Reyes", "rating": 4.8, "reviews": 35},
        {"employee": "Ben Cruz", "rating": 4.6, "reviews": 29},
        {"employee": "Ruri AI", "rating": 4.9, "reviews": 50}
    ]
    return jsonify(ratings)


# ------------------------
# images & products
# ------------------------
@app.route('/images/<path:filename>')
def serve_images(filename):
    return send_from_directory(os.path.join(app.root_path, 'static', 'images'), filename)


@app.route('/products', methods=['GET'])
def get_products():
    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM products ORDER BY created_at DESC")
        products = [{
            "product_id": r[0],
            "name": r[1],
            "description": r[2],
            "price": str(r[3]),
            "stock": r[4],
            "image_url": r[5],
            "created_at": str(r[6])
        } for r in cur.fetchall()]
        cur.close()
        return jsonify(products)
    except Exception as e:
        print("💥 get_products error:", e)
        return jsonify({"error": "Internal Server Error"}), 500


# ------------------------
if __name__ == "__main__":
    # debug mode for local dev
    app.run(debug=True)
