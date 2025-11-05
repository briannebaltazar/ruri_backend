from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_mysqldb import MySQL
from dotenv import load_dotenv
import os
import bcrypt

# Load env variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# ======================
# HOME ROUTE
# ======================
@app.route('/')
def home():
    return jsonify({"message": "Ruri backend is running and connected to MySQL!"})

# Database config
app.config['MYSQL_HOST'] = os.getenv("DB_HOST")
app.config['MYSQL_USER'] = os.getenv("DB_USER")
app.config['MYSQL_PASSWORD'] = os.getenv("DB_PASSWORD")
app.config['MYSQL_DB'] = os.getenv("DB_NAME")

mysql = MySQL(app)

# ======================
# USER SIGNUP
# ======================
@app.route('/signup', methods=['POST'])
def signup():
    data = request.json
    full_name = data.get('full_name')
    email = data.get('email')
    password = data.get('password')
    phone = data.get('phone')
    address = data.get('address')
    role = data.get('role')  # client, employee, admin

    # Hash password
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

    cur = mysql.connection.cursor()
    cur.execute(
        "INSERT INTO users (full_name, email, password, phone, address, role) VALUES (%s, %s, %s, %s, %s, %s)",
        (full_name, email, hashed_password, phone, address, role)
    )
    mysql.connection.commit()
    cur.close()
    return jsonify({"message": "User created successfully!"})


# ======================
# USER LOGIN
# ======================
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')

    cur = mysql.connection.cursor()
    cur.execute("SELECT user_id, password, role FROM users WHERE email=%s", (email,))
    user = cur.fetchone()
    cur.close()

    if user and bcrypt.checkpw(password.encode('utf-8'), user[1].encode('utf-8')):
        return jsonify({"user_id": user[0], "role": user[2], "message": "Login successful"})
    return jsonify({"message": "Invalid credentials"}), 401


# ======================
# GET PRODUCTS
# ======================
@app.route('/products', methods=['GET'])
def get_products():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM products")
    products = cur.fetchall()
    cur.close()

    product_list = []
    for p in products:
        product_list.append({
            "product_id": p[0],
            "name": p[1],
            "description": p[2],
            "price": float(p[3]),
            "stock": p[4],
            "image_url": p[5],
            "created_at": str(p[6])
        })

    return jsonify(product_list)


# ======================
# CREATE ORDER
# ======================
@app.route('/orders', methods=['POST'])
def create_order():
    data = request.json
    user_id = data.get('user_id')
    items = data.get('items')  # [{product_id: 1, quantity: 2}, ...]
    total_amount = 0

    cur = mysql.connection.cursor()

    # calculate total
    for item in items:
        cur.execute("SELECT price FROM products WHERE product_id=%s", (item['product_id'],))
        price = cur.fetchone()[0]
        total_amount += price * item['quantity']

    # create order
    cur.execute("INSERT INTO orders (user_id, total_amount) VALUES (%s, %s)", (user_id, total_amount))
    order_id = cur.lastrowid

    # create order_items
    for item in items:
        cur.execute("SELECT price FROM products WHERE product_id=%s", (item['product_id'],))
        price_each = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO order_items (order_id, product_id, quantity, price_each) VALUES (%s, %s, %s, %s)",
            (order_id, item['product_id'], item['quantity'], price_each)
        )

    mysql.connection.commit()
    cur.close()
    return jsonify({"message": "Order created successfully!", "order_id": order_id})


# ======================
# GET ORDERS FOR USER
# ======================
@app.route('/orders/<int:user_id>', methods=['GET'])
def get_orders(user_id):
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM orders WHERE user_id=%s", (user_id,))
    orders = cur.fetchall()
    cur.close()
    return jsonify(orders)


# ======================
# CHAT MESSAGES (with AI)
# ======================
from nlp_model import nlp_model_respond  # add this at the top near your other imports

@app.route('/chat', methods=['POST'])
def send_message():
    data = request.json
    sender_id = data.get('sender_id')
    message = data.get('message')

    # === 1. Generate AI response ===
    response_text = nlp_model_respond(message)

    # Optional fallback if AI doesn't understand
    fallback_responses = ["I'm not sure", "I don't understand", "Sorry, I can't help with that"]
    if any(resp in response_text for resp in fallback_responses) or len(response_text.strip()) < 5:
        response_text = "I'm forwarding your question to a human representative."
        human_needed = True
    else:
        human_needed = False

# === 2. Save messages in chat_logs ===
    cur = mysql.connection.cursor()

    # user message (receiver_id = 0 for AI)
    cur.execute(
        "INSERT INTO chat_logs (sender_id, receiver_id, message, chat_type) VALUES (%s, %s, %s, %s)",
        (sender_id, 10, message, 'client_ai')
    )

    # AI response (sender_id = 0 for AI)
    cur.execute(
        "INSERT INTO chat_logs (sender_id, receiver_id, message, chat_type) VALUES (%s, %s, %s, %s)",
        (10, sender_id, response_text, 'client_ai')
    )

    mysql.connection.commit()
    cur.close()


    # === 3. Return AI reply ===
    return jsonify({
        "response": response_text,
        "human_needed": human_needed
    })

# ======================
# ATTENDANCE
# ======================
@app.route('/attendance', methods=['POST'])
def attendance():
    data = request.json
    employee_id = data.get('employee_id')
    date = data.get('date')
    time_in = data.get('time_in')
    time_out = data.get('time_out')
    status = data.get('status')

    cur = mysql.connection.cursor()
    cur.execute(
        "INSERT INTO attendance (employee_id, date, time_in, time_out, status) VALUES (%s, %s, %s, %s, %s)",
        (employee_id, date, time_in, time_out, status)
    )
    mysql.connection.commit()
    cur.close()
    return jsonify({"message": "Attendance recorded"})



# ======================
# RATINGS
# ======================
@app.route('/ratings', methods=['POST'])
def ratings():
    data = request.json
    client_id = data.get('client_id')
    employee_id = data.get('employee_id')
    order_id = data.get('order_id')
    rating = data.get('rating')
    feedback = data.get('feedback')

    cur = mysql.connection.cursor()
    cur.execute(
        "INSERT INTO ratings (client_id, employee_id, order_id, rating, feedback) VALUES (%s, %s, %s, %s, %s)",
        (client_id, employee_id, order_id, rating, feedback)
    )
    mysql.connection.commit()
    cur.close()
    return jsonify({"message": "Rating submitted successfully"})


if __name__ == '__main__':
    app.run(debug=True)
