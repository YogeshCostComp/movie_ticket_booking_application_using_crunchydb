from flask import Flask, render_template, request, jsonify
import json
import psycopg2
import psycopg2.errorcodes
import time
import logging
import random
import os
import sys


# Configure logging for IBM Cloud Code Engine
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["files"] = "."

# Database configuration from environment variables
DB_HOST = os.environ.get('DB_HOST', '127.0.0.1')
DB_PORT = os.environ.get('DB_PORT', '5432')
DB_NAME = os.environ.get('DB_NAME', 'hippo')
DB_USER = os.environ.get('DB_USER', 'hippo')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'datalake')
DB_SSLMODE = os.environ.get('DB_SSLMODE', 'prefer')

def get_db_connection():
    """Create a database connection using environment variables."""
    logger.info(f"Connecting to database at {DB_HOST}:{DB_PORT}/{DB_NAME}")
    try:
        conn = psycopg2.connect(
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            sslmode=DB_SSLMODE
        )
        logger.info("Database connection successful")
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

# Health check endpoint for Code Engine
@app.route("/health")
def health():
    logger.info("Health check requested")
    return jsonify({"status": "healthy"}), 200

global data_seats

data_seats ={
"1A":"available","1B":"available","1C":"available","1D":"available","1E":"available","1F":"available",
"2A":"available","2B":"available","2C":"available","2D":"available","2E":"available","2F":"available",
"3A":"available","3B":"available","3C":"available","3D":"available","3E":"available","3F":"available",
"4A":"available","4B":"available","4C":"available","4D":"available","4E":"available","4F":"available",
"5A":"available","5B":"available","5C":"available","5D":"available","5E":"available","5F":"available",
"6A":"available","6B":"available","6C":"available","6D":"available","6E":"available","6F":"available",
"7A":"available","7B":"available","7C":"available","7D":"available","7E":"available","7F":"available",
"8A":"available","8B":"available","8C":"available","8D":"available","8E":"available","8F":"available",
"9A":"available","9B":"available","9C":"available","9D":"available","9E":"available","9F":"available",
"10A":"available","10B":"available","10C":"available","10D":"available","10E":"available","10F":"available"
}


@app.route("/")
def home():
	return render_template("UI.html")

@app.route("/create")
def create_table():
	conn = get_db_connection()
	for k,v in data_seats.items():
		insert_seats(k, v, conn)
	conn.close()
	return "<h1>Table Created, click <a href='/'>here</a> to open the app</h1>"

def insert_seats(seat_no, status, conn):
	with conn.cursor() as cur:
		cur.execute('CREATE TABLE IF NOT EXISTS userdetails (phone_no VARCHAR PRIMARY KEY, name VARCHAR, seats VARCHAR)')
		cur.execute('CREATE TABLE IF NOT EXISTS screen (seat_no VARCHAR PRIMARY KEY, status VARCHAR)')
		cur.execute("INSERT INTO screen (seat_no, status) VALUES (%s,%s)", (seat_no, status))
		logging.debug("insert_seats(): status message: {}".format(cur.statusmessage))
	conn.commit()

@app.route("/update", methods=['GET', 'POST'])
def update_seats():
	name = ''
	number = ''
	seats = []
	if request.method == 'POST':
		try:
			x = json.loads(request.form['data_seats'])
			user_data = json.loads(request.form['userdetails'])
			name = user_data.get('name', '').strip()
			number = user_data.get('number', '').strip()

			# --- Input validation ---
			if not name:
				logger.warning("Reservation failed: name is empty")
				return jsonify({"flag": 1, "error": "Name is required. Please enter your name."}), 400
			if not number:
				logger.warning("Reservation failed: phone number is empty")
				return jsonify({"flag": 1, "error": "Phone number is required. Please enter your phone number."}), 400
			if not number.isdigit() or len(number) < 7:
				logger.warning(f"Reservation failed: invalid phone number '{number}'")
				return jsonify({"flag": 1, "error": "Please enter a valid phone number (at least 7 digits)."}), 400

			for k,v in x.items():
				if v == "reserved":
					seats.append(k)
					x[k] = "blocked"

			if not seats:
				logger.warning("Reservation failed: no seats selected")
				return jsonify({"flag": 1, "error": "Please select at least one seat before reserving."}), 400

			logger.info(f"Reservation attempt: name={name}, phone={number}, seats={seats}")
			seats_string = ','.join(seats)

			# --- Check if any selected seats are already booked ---
			conn = get_db_connection()
			try:
				with conn.cursor() as cur:
					placeholders = ','.join(['%s'] * len(seats))
					cur.execute(f"SELECT seat_no FROM screen WHERE seat_no IN ({placeholders}) AND status = 'blocked'", seats)
					already_booked = [row[0] for row in cur.fetchall()]
				if already_booked:
					conn.close()
					logger.warning(f"Seats already booked: {already_booked}")
					return jsonify({"flag": 1, "error": f"Seats {', '.join(already_booked)} are already booked. Please select different seats."}), 409

				update(seats, conn)
				with conn.cursor() as cur:
					cur.execute("INSERT INTO userdetails (phone_no, name, seats) VALUES (%s,%s,%s)", (number, name, seats_string))
					logger.info(f"Booking saved: {name} - {number} - {seats_string}")
				conn.commit()
				conn.close()
				return jsonify({"flag": 0, "message": f"Successfully reserved seats: {seats_string}"})

			except psycopg2.errors.UniqueViolation:
				conn.rollback()
				conn.close()
				logger.error(f"Duplicate phone number: {number}")
				return jsonify({"flag": 1, "error": f"Phone number {number} has already been used for a booking. Please use a different phone number."}), 409

			except psycopg2.Error as db_err:
				if conn:
					conn.rollback()
					conn.close()
				logger.error(f"Database error during reservation: {db_err}")
				return jsonify({"flag": 1, "error": "A database error occurred. Please try again later."}), 500

		except json.JSONDecodeError as e:
			logger.error(f"Invalid request data: {e}")
			return jsonify({"flag": 1, "error": "Invalid request data. Please refresh the page and try again."}), 400

		except Exception as e:
			logger.error(f"Unexpected error during reservation: {e}")
			return jsonify({"flag": 1, "error": "An unexpected error occurred. Please try again."}), 500

	return jsonify({"flag": 1, "error": "Invalid request method."}), 405

def update(seats,conn):
	for i in seats:
		print(type(i))
		with conn.cursor() as cur:
			cur.execute("UPDATE screen SET status = %s WHERE seat_no = %s",("blocked",i,))
			print("update_book_details(): status message: {}".format(cur.statusmessage))
		conn.commit()

@app.route("/getUsersDetails")
def usersDetails():
    temp = {}
    arr = []
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM userdetails")
        rows = cur.fetchall()
    conn.commit()
    conn.close()
    for row in rows:
        temp = {
            "phone_no": row[0],
            "name": row[1],
            "seats": row[2]
        }
        arr.append(temp)
    return json.dumps(arr)

@app.route("/details")
def details():
	return render_template("Seats.html")

@app.route("/get")
def staus():
	data_new={}
	conn = get_db_connection()
	with conn.cursor() as cur:
		cur.execute("SELECT * FROM screen")
		logging.debug("print_balances(): status message: {}".format(cur.statusmessage))
		rows = cur.fetchall()
	conn.commit()
	for row in rows:
		data_new[row[0]]=row[1]
	conn.close()
	x = json.dumps(data_new)
	return x


# ===== Error Simulation Endpoints (for SRE testing) =====

@app.route("/simulate/error", methods=['POST'])
def simulate_error():
    """Generate simulated errors for SRE agent testing.
    Accepts JSON body: {"error_type": "500|404|db_error|timeout|exception"}
    """
    try:
        data = request.get_json(force=True)
        error_type = data.get('error_type', '500')
    except:
        error_type = '500'

    if error_type == '404':
        logger.error(f"SIMULATED ERROR: 404 Not Found - Resource '/fake/page' does not exist")
        return jsonify({
            "error": "Not Found",
            "message": "The requested resource '/fake/page' was not found on this server.",
            "simulated": True
        }), 404

    elif error_type == '500':
        logger.error(f"SIMULATED ERROR: 500 Internal Server Error - Application crashed during request processing")
        return jsonify({
            "error": "Internal Server Error",
            "message": "The server encountered an unexpected condition that prevented it from fulfilling the request.",
            "simulated": True
        }), 500

    elif error_type == '503':
        logger.error(f"SIMULATED ERROR: 503 Service Unavailable - Server is overloaded or under maintenance")
        return jsonify({
            "error": "Service Unavailable",
            "message": "The server is temporarily unable to handle the request due to maintenance or overload.",
            "simulated": True
        }), 503

    elif error_type == 'db_error':
        logger.error(f"SIMULATED ERROR: psycopg2.OperationalError - connection to database 'hippo' failed: Connection refused")
        logger.error(f"SIMULATED ERROR: DatabaseError - could not translate host name to address: Name or service not known")
        return jsonify({
            "error": "Database Connection Error",
            "message": "psycopg2.OperationalError: connection refused - database server is not accepting connections.",
            "simulated": True
        }), 500

    elif error_type == 'timeout':
        logger.error(f"SIMULATED ERROR: Request timeout exceeded - operation took longer than 30 seconds")
        logger.error(f"SIMULATED ERROR: TimeoutError - The read operation timed out")
        return jsonify({
            "error": "Request Timeout",
            "message": "The server timed out waiting for the request to complete.",
            "simulated": True
        }), 504

    elif error_type == 'exception':
        logger.error(f"SIMULATED ERROR: Unhandled Exception - Traceback (most recent call last):")
        logger.error(f"SIMULATED ERROR:   File 'app.py', line 42, in process_booking")
        logger.error(f"SIMULATED ERROR:   ZeroDivisionError: division by zero")
        logger.error(f"SIMULATED ERROR: Exception: Critical application failure in booking module")
        return jsonify({
            "error": "Unhandled Exception",
            "message": "Traceback: ZeroDivisionError - division by zero in process_booking",
            "simulated": True
        }), 500

    elif error_type == 'all':
        # Fire all error types at once
        logger.error("SIMULATED ERROR: 404 Not Found - Resource does not exist")
        logger.error("SIMULATED ERROR: 500 Internal Server Error - Application crash")
        logger.error("SIMULATED ERROR: 503 Service Unavailable - Overloaded")
        logger.error("SIMULATED ERROR: psycopg2.OperationalError - connection to database failed")
        logger.error("SIMULATED ERROR: TimeoutError - operation timed out after 30s")
        logger.error("SIMULATED ERROR: Traceback (most recent call last): ZeroDivisionError")
        logger.error("SIMULATED ERROR: Exception: Critical failure in booking module")
        return jsonify({
            "message": "All error types simulated (404, 500, 503, db_error, timeout, exception). Check Cloud Logs.",
            "errors_generated": 7,
            "simulated": True
        }), 500

    else:
        return jsonify({"error": f"Unknown error type: {error_type}", "valid_types": ["404", "500", "503", "db_error", "timeout", "exception", "all"]}), 400


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))  # Code Engine uses PORT env var
    logger.info(f"Starting Movie Ticket Booking App on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
