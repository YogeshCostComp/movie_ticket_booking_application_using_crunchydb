from flask import Flask, render_template, request, jsonify, g, session, make_response
import json
import psycopg2
import psycopg2.errorcodes
import time
import logging
import random
import os
import sys
import uuid
from datetime import datetime

# JWT for watsonx Orchestrate embedded chat security
try:
    import jwt  # PyJWT
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    from cryptography.hazmat.primitives import hashes
    import base64
    _JWT_AVAILABLE = True
except ImportError:
    _JWT_AVAILABLE = False


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
app.secret_key = os.environ.get('SECRET_KEY', 'cine-book-trace-secret-key-2024')

# Database configuration from environment variables
DB_HOST = os.environ.get('DB_HOST', '127.0.0.1')
DB_PORT = os.environ.get('DB_PORT', '5432')
DB_NAME = os.environ.get('DB_NAME', 'hippo')
DB_USER = os.environ.get('DB_USER', 'hippo')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'datalake')
DB_SSLMODE = os.environ.get('DB_SSLMODE', 'prefer')

# ===== Tracing System =====
def init_tracing_table():
    """Create the tracing table if it doesn't exist."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS app_traces (
                    id SERIAL PRIMARY KEY,
                    trace_id VARCHAR(36) NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    action VARCHAR(100) NOT NULL,
                    endpoint VARCHAR(200),
                    method VARCHAR(10),
                    details TEXT,
                    status VARCHAR(20) DEFAULT 'success',
                    duration_ms NUMERIC(10,2),
                    user_ip VARCHAR(50)
                )
            ''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_trace_id ON app_traces(trace_id)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_trace_timestamp ON app_traces(timestamp DESC)')
        conn.commit()
        conn.close()
        logger.info("Tracing table initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize tracing table: {e}")

def log_trace(trace_id, action, endpoint=None, method=None, details=None, status='success', duration_ms=None, user_ip=None):
    """Log a trace entry to the database."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                '''INSERT INTO app_traces (trace_id, action, endpoint, method, details, status, duration_ms, user_ip)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                (trace_id, action, endpoint, method, details, status, duration_ms, user_ip)
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log trace: {e}")

@app.before_request
def before_request_trace():
    """Generate or reuse trace_id for every request and start timer."""
    if request.path == '/':
        # Fresh session — new trace_id when user opens the app
        g.trace_id = str(uuid.uuid4())
    else:
        # Read trace_id from: query param > form data > session > generate new
        g.trace_id = (request.args.get('trace_id') or
                      request.form.get('trace_id') or
                      session.get('trace_id') or
                      str(uuid.uuid4()))
    # Always update session as backup
    session['trace_id'] = g.trace_id
    g.trace_start = time.time()
    g.user_ip = request.remote_addr or 'unknown'

@app.after_request
def after_request_trace(response):
    """Log the completed request as a trace entry."""
    trace_id = getattr(g, 'trace_id', 'unknown')

    # Add trace_id to response header for debugging/correlation
    response.headers['X-Trace-Id'] = trace_id

    # Skip health checks, static files, and trace endpoints from logging
    skip_endpoints = ['/health', '/favicon.ico', '/getRecentTraces']
    if request.path in skip_endpoints or request.path.startswith('/getTraceDetails'):
        return response

    try:
        duration_ms = round((time.time() - g.trace_start) * 1000, 2)
        status = 'success' if response.status_code < 400 else 'error'

        action = f"{request.method} {request.path}"
        details = None
        if request.path == '/update' and request.method == 'POST':
            action = 'BOOK_SEATS'
            try:
                user_data = json.loads(request.form.get('userdetails', '{}'))
                seat_data = json.loads(request.form.get('data_seats', '{}'))
                selected = [k for k, v in seat_data.items() if v == 'reserved']
                details = f"User: {user_data.get('name', 'N/A')}, Phone: {user_data.get('number', 'N/A')}, Seats: {','.join(sorted(selected))}"
                if status == 'success':
                    action = 'BOOKING_CONFIRMED'
                    details += ' → Stored in DB'
                else:
                    action = 'BOOKING_FAILED'
            except:
                details = 'Booking attempt'
        elif request.path == '/':
            action = 'USER_OPENED_APP'
            details = 'User loaded the booking page'
        elif request.path == '/details':
            action = 'VIEW_BOOKING_CONFIRMATION'
            details = 'User viewing booking confirmation page'
        elif request.path == '/get':
            action = 'LOAD_SEAT_MAP'
            details = 'Fetched current seat availability from DB'
        elif request.path == '/getUsersDetails':
            action = 'VIEW_ALL_BOOKINGS'
            details = 'User viewed all booking records'
        elif request.path == '/resetBookings':
            action = 'RESET_ALL_BOOKINGS'
            details = 'All bookings cleared, all seats reset to available'
        elif request.path == '/create':
            action = 'INIT_SEATS_TABLE'
            details = 'Database tables initialized'
        elif request.path.startswith('/simulate'):
            action = 'SRE_ERROR_SIMULATION'
            details = request.path

        log_trace(
            trace_id=trace_id,
            action=action,
            endpoint=request.path,
            method=request.method,
            details=details,
            status=status,
            duration_ms=duration_ms,
            user_ip=getattr(g, 'user_ip', 'unknown')
        )
    except Exception as e:
        logger.error(f"Tracing after_request error: {e}")

    return response

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
	return render_template("UI.html", trace_id=g.trace_id)

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
	# trace_id comes from query param (set by booking page redirect)
	return render_template("Seats.html", trace_id=g.trace_id)

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


# ===== Reset Bookings Endpoint =====

@app.route("/resetBookings", methods=['POST', 'GET'])
def reset_bookings():
    """Reset all bookings - clear userdetails and reset all seats to available."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM userdetails")
            cur.execute("UPDATE screen SET status = 'available'")
            logger.info("All bookings have been reset")
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "All bookings have been reset. All seats are now available."})
    except Exception as e:
        logger.error(f"Failed to reset bookings: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===== Tracing Query Endpoints =====

@app.route("/getRecentTraces")
def get_recent_traces():
    """Get recent unique trace IDs with summary info."""
    try:
        limit = request.args.get('limit', 20, type=int)
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute('''
                SELECT trace_id,
                       MIN(timestamp) as started_at,
                       MAX(timestamp) as ended_at,
                       COUNT(*) as event_count,
                       ARRAY_AGG(DISTINCT action) as actions,
                       MAX(user_ip) as user_ip,
                       CASE WHEN BOOL_OR(status = 'error') THEN 'error' ELSE 'success' END as overall_status
                FROM app_traces
                GROUP BY trace_id
                ORDER BY MIN(timestamp) DESC
                LIMIT %s
            ''', (limit,))
            rows = cur.fetchall()
        conn.close()

        traces = []
        for row in rows:
            traces.append({
                "trace_id": row[0],
                "started_at": row[1].isoformat() if row[1] else None,
                "ended_at": row[2].isoformat() if row[2] else None,
                "event_count": row[3],
                "actions": row[4],
                "user_ip": row[5],
                "overall_status": row[6]
            })
        return jsonify({"status": "success", "total": len(traces), "traces": traces})
    except Exception as e:
        logger.error(f"Failed to get traces: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/getTraceDetails/<trace_id>")
def get_trace_details(trace_id):
    """Get the full end-to-end transaction flow for a specific trace ID."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute('''
                SELECT id, trace_id, timestamp, action, endpoint, method,
                       details, status, duration_ms, user_ip
                FROM app_traces
                WHERE trace_id = %s
                ORDER BY timestamp ASC
            ''', (trace_id,))
            rows = cur.fetchall()
        conn.close()

        if not rows:
            return jsonify({"status": "error", "message": f"No trace found with ID: {trace_id}"}), 404

        events = []
        for row in rows:
            events.append({
                "id": row[0],
                "trace_id": row[1],
                "timestamp": row[2].isoformat() if row[2] else None,
                "action": row[3],
                "endpoint": row[4],
                "method": row[5],
                "details": row[6],
                "status": row[7],
                "duration_ms": float(row[8]) if row[8] else None,
                "user_ip": row[9]
            })

        # Calculate total duration
        first_ts = rows[0][2]
        last_ts = rows[-1][2]
        total_duration_ms = (last_ts - first_ts).total_seconds() * 1000 if first_ts and last_ts else 0

        return jsonify({
            "status": "success",
            "trace_id": trace_id,
            "total_events": len(events),
            "total_duration_ms": round(total_duration_ms, 2),
            "started_at": events[0]["timestamp"],
            "ended_at": events[-1]["timestamp"],
            "user_ip": events[0]["user_ip"],
            "overall_status": "error" if any(e["status"] == "error" for e in events) else "success",
            "events": events
        })
    except Exception as e:
        logger.error(f"Failed to get trace details: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


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


# ===== watsonx Orchestrate Embedded Chat — JWT Token Endpoint =====
_KEYS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'keys')

def _load_private_key():
    """Load client private key for signing JWTs.
    Production: reads from WXO_CLIENT_PRIVATE_KEY env var.
    Local dev:  falls back to keys/client_private_key.pem file.
    """
    env_key = os.environ.get('WXO_CLIENT_PRIVATE_KEY')
    if env_key:
        # Env vars collapse newlines — restore them
        return env_key.replace('\\n', '\n').encode('utf-8')
    key_path = os.path.join(_KEYS_DIR, 'client_private_key.pem')
    with open(key_path, 'rb') as f:
        return f.read()

def _load_ibm_public_key():
    """Load IBM public key for encrypting user_payload.
    Production: reads from WXO_IBM_PUBLIC_KEY env var.
    Local dev:  falls back to keys/ibm_public_key.pem file.
    """
    env_key = os.environ.get('WXO_IBM_PUBLIC_KEY')
    if env_key:
        pem_bytes = env_key.replace('\\n', '\n').encode('utf-8')
        return serialization.load_pem_public_key(pem_bytes)
    key_path = os.path.join(_KEYS_DIR, 'ibm_public_key.pem')
    with open(key_path, 'rb') as f:
        return serialization.load_pem_public_key(f.read())

@app.route('/createJWT', methods=['GET'])
def create_jwt_token():
    """Generate a signed JWT for watsonx Orchestrate embedded chat.
    The JWT is signed with our client private key (RS256).
    The user_payload is encrypted with IBM's public key (RSA-OAEP + SHA256).
    """
    if not _JWT_AVAILABLE:
        return jsonify({"error": "JWT dependencies not installed (PyJWT, cryptography)"}), 500

    try:
        # Get or create an anonymous user ID from cookies
        user_id = request.cookies.get('EMBED_USER_ID')
        if not user_id:
            user_id = f"anon-{uuid.uuid4().hex[:8]}"

        # Build user_payload (will be encrypted)
        user_payload = {
            "name": "CineBook User",
            "custom_message": "SRE Agent Chat Session"
        }

        # Encrypt user_payload with IBM public key (RSA-OAEP + SHA256)
        ibm_pub_key = _load_ibm_public_key()
        payload_bytes = json.dumps(user_payload).encode('utf-8')
        encrypted = ibm_pub_key.encrypt(
            payload_bytes,
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        encrypted_payload = base64.b64encode(encrypted).decode('utf-8')

        # Build JWT claims
        jwt_claims = {
            "sub": user_id,
            "user_payload": encrypted_payload
        }

        # Sign the JWT with client private key (RS256)
        private_key = _load_private_key()
        token = jwt.encode(
            jwt_claims,
            private_key,
            algorithm='RS256',
            headers={"alg": "RS256", "typ": "JWT"}
        )

        # Return the JWT and set the user ID cookie (45 days)
        resp = make_response(token)
        resp.content_type = 'text/plain'
        resp.set_cookie('EMBED_USER_ID', user_id, max_age=45 * 24 * 3600, httponly=True)
        return resp

    except FileNotFoundError as e:
        logger.error(f"JWT key file not found: {e}")
        return jsonify({"error": "Security keys not configured. Set WXO_CLIENT_PRIVATE_KEY and WXO_IBM_PUBLIC_KEY env vars, or run configure_embed_security.py --enable"}), 500
    except Exception as e:
        logger.error(f"JWT generation failed: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))  # Code Engine uses PORT env var
    logger.info(f"Starting Movie Ticket Booking App on port {port}")
    # Initialize tracing table on startup
    try:
        init_tracing_table()
    except Exception as e:
        logger.warning(f"Could not initialize tracing table on startup: {e}")
    app.run(host="0.0.0.0", port=port, debug=False)
