from flask import Flask, render_template, request, redirect, url_for, session, flash
import psycopg2

from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, date
import heapq
import os

app = Flask(__name__)
app.secret_key = "replace_with_a_random_secret_key"

# =====================================================
# DATABASE CONFIG
# =====================================================

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "dbname": os.getenv("DB_NAME", "laund_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "root"),
    "port": int(os.getenv("DB_PORT", 5432))

}

# def get_db():
#     return psycopg2.connect(
#         host=DB_CONFIG["host"],
#         database=DB_CONFIG["dbname"],
#         user=DB_CONFIG["user"],
#         password=DB_CONFIG["password"],
#         port=DB_CONFIG["port"],
#         cursor_factory=psycopg2.extras.RealDictCursor
#     )

# DATABASE_URL=postgresql://laundry_db_pjb0_user:an7KnbVgrIQ94qkyGGw8kjUui1DF9cBM@dpg-d5btkd75r7bs73al9sjg-a/laundry_db_pjb0

def get_db():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise Exception("DATABASE_URL environment variable is not set")

    return psycopg2.connect(
        database_url,
        cursor_factory=RealDictCursor,
        sslmode="require"
    )

# =====================================================
# HELPER FUNCTIONS
# =====================================================
def get_user_by_email(email):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cur.fetchone()
    cur.close()
    db.close()
    return user


def get_user_by_id(uid):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s", (uid,))
    user = cur.fetchone()
    cur.close()
    db.close()
    return user


def get_settings():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM system_settings WHERE id = 1")
    data = cur.fetchone()
    cur.close()
    db.close()
    return data


# =====================================================
# SLOT GENERATION
# =====================================================
def generate_daily_slots():
    today = date.today()
    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT * FROM system_settings WHERE id = 1")
    s = cur.fetchone()

    start_dt = datetime.combine(today, s["start_time"])
    end_dt = datetime.combine(today, s["end_time"])

    wash_duration = timedelta(minutes=s["wash_duration"])
    break_after = s["break_after"]
    break_duration = timedelta(minutes=s["break_duration"])
    slots_per_day = s["slots_per_day"]

    cur.execute("SELECT id FROM machines")
    machines = cur.fetchall()

    for m in machines:
        cur.execute("""
            SELECT COUNT(*) FROM slots
            WHERE machine_id=%s AND slot_date=%s
        """, (m["id"], today))

        if cur.fetchone()["count"] > 0:
            continue

        current = start_dt
        count = 0

        while current + wash_duration <= end_dt and count < slots_per_day:
            cur.execute("""
                INSERT INTO slots (machine_id, slot_date, slot_start, slot_end)
                VALUES (%s, %s, %s, %s)
            """, (
                m["id"],
                today,
                current.time(),
                (current + wash_duration).time()
            ))

            count += 1
            current += wash_duration

            if break_after > 0 and count % break_after == 0:
                current += break_duration

    db.commit()
    cur.close()
    db.close()


# =====================================================
# ROUTES
# =====================================================

@app.route("/")
def index():
    return render_template("index.html")


# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"].lower()
        rollno = request.form["rollno"]
        phone = request.form["phone"]
        password = generate_password_hash(request.form["password"])

        if get_user_by_email(email):
            flash("Email already exists", "danger")
            return redirect(url_for("register"))

        db = get_db()
        cur = db.cursor()
        cur.execute("""
            INSERT INTO users (name, email, rollno, password_hash, phone, role)
            VALUES (%s, %s, %s, %s, %s, 'user')
        """, (name, email, rollno, password, phone))
        db.commit()
        cur.close()
        db.close()

        flash("Registered successfully", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].lower()
        password = request.form["password"]

        user = get_user_by_email(email)
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["user_name"] = user["name"]
            return redirect(url_for("dashboard"))

        flash("Invalid credentials", "danger")

    return render_template("login.html")


# ---------- Logout ----------
@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('index'))


# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    role = session.get("role")

    if role == "admin":
        return redirect(url_for("admin_dashboard"))

    if role == "operator":
        return redirect(url_for("Machine_operator"))

    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT 
            b.id AS booking_id,
            b.status,
            s.slot_date,
            s.slot_start,
            s.slot_end,
            m.name AS machine_name
        FROM bookings b
        JOIN slots s ON b.slot_id = s.id
        JOIN machines m ON s.machine_id = m.id
        WHERE b.user_id = %s
        ORDER BY s.slot_date DESC
    """, (session['user_id'],))

    bookings = cur.fetchall()
    cur.close()
    db.close()

    return render_template("dashboard.html", bookings=bookings)


#------------view slots---------------
@app.route('/view_slots')
def view_slots():

    if 'user_id' not in session:
        flash("Please login to view slots.", "warning")
        return redirect(url_for('login'))

    try:
        settings = get_settings()

        if settings["auto_generate"]:
            generate_daily_slots()

        now = datetime.now()
        today = date.today()

        db = get_db()
        cur = db.cursor()

        cur.execute("""
            SELECT 
                s.id AS slot_id,
                s.slot_date,
                s.slot_start,
                s.slot_end,
                m.name AS machine_name,
                (
                    SELECT COUNT(*)
                    FROM bookings b
                    WHERE b.slot_id = s.id
                    AND b.status IN ('booked', 'validated')
                ) AS booked_count
            FROM slots s
            JOIN machines m ON s.machine_id = m.id
            WHERE 
                (s.slot_date > %s)
                OR (s.slot_date = %s AND s.slot_end > %s)
            ORDER BY s.slot_date, s.slot_start
        """, (today, today, now.time()))

        slots = cur.fetchall()

        cur.execute("SELECT id, name FROM machines")
        machines = cur.fetchall()

    except Exception as e:
        flash("Unable to load slots. Please try again later.", "danger")
        print("View slots error:", e)
        slots = []
        machines = []

    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

    return render_template(
        "view_slots.html",
        slots=slots,
        machines=machines
    )



#-----------------CREATE SLOTS--------------------
@app.route('/create_slot', methods=['GET', 'POST'])
def create_slot():

    db = get_db()
    cur = db.cursor()

    if request.method == 'POST':
        try:
            machine_id = request.form['machine_id']
            slot_date = request.form['slot_date']      # YYYY-MM-DD
            slot_start = request.form['slot_start']    # HH:MM
            slot_end = request.form['slot_end']        # HH:MM

            # Convert to proper Python date/time objects
            slot_date = datetime.strptime(slot_date, "%Y-%m-%d").date()
            slot_start = datetime.strptime(slot_start, "%H:%M").time()
            slot_end = datetime.strptime(slot_end, "%H:%M").time()

            cur.execute("""
                INSERT INTO slots (machine_id, slot_date, slot_start, slot_end)
                VALUES (%s, %s, %s, %s)
            """, (machine_id, slot_date, slot_start, slot_end))

            db.commit()

            flash("New slot created successfully!", "success")
            return redirect(url_for('view_slots'))

        except Exception as e:
            db.rollback()
            flash(f"Error creating slot: {str(e)}", "danger")

        finally:
            cur.close()
            db.close()

    # GET request
    cur.execute("SELECT id, name FROM machines")
    machines = cur.fetchall()

    cur.close()
    db.close()

    return render_template(
        'create_slot.html',
        machines=machines
    )


#------------------BOOK SLOT-------------
from datetime import datetime

@app.route('/book/<int:slot_id>', methods=['GET', 'POST'])
def book_slot(slot_id):

    if 'user_id' not in session:
        flash("Login required.", "warning")
        return redirect(url_for('login'))

    user_id = session['user_id']


    try:
        db = get_db()
        cur = db.cursor()

        # ---------------- FETCH SLOT ----------------
        cur.execute("""
            SELECT s.*, m.name AS machine_name
            FROM slots s
            JOIN machines m ON s.machine_id = m.id
            WHERE s.id = %s
        """, (slot_id,))
        slot = cur.fetchone()

        if not slot:
            flash("Slot not found.", "danger")
            return redirect(url_for('view_slots'))

        # if slot["slot_date"] == date.today() and slot["slot_end"] < datetime.now().time():
        #     flash("This slot has already expired.", "danger")
        #     return redirect(url_for("view_slots"))

        # ---------------- WEEKLY LIMIT ----------------
        cur.execute("""
            SELECT COUNT(*) AS count
            FROM bookings
            WHERE user_id = %s
            AND DATE_TRUNC('week', created_at) = DATE_TRUNC('week', CURRENT_DATE)
            AND status = 'booked'
        """, (user_id,))
        weekly_count = cur.fetchone()["count"]

        if weekly_count >= 2:
            flash("❗ Weekly limit reached. You can book only 2 slots per week.", "danger")
            return redirect(url_for('dashboard'))

        # ---------------- MONTHLY LIMIT ----------------
        cur.execute("""
            SELECT COUNT(*) AS count
            FROM bookings
            WHERE user_id = %s
            AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', CURRENT_DATE)
            AND status = 'booked'
        """, (user_id,))
        monthly_count = cur.fetchone()["count"]

        if monthly_count >= 8:
            flash("❗ Monthly limit reached. You can book only 8 slots per month.", "danger")
            return redirect(url_for('dashboard'))

        # ---------------- SLOT AVAILABILITY ----------------
        cur.execute("""
            SELECT 1 FROM bookings
            WHERE slot_id = %s AND status = 'booked'
        """, (slot_id,))
        if cur.fetchone():
            flash("Slot already booked.", "danger")
            return redirect(url_for('view_slots'))

        # ---------------- BOOK SLOT ----------------
        if request.method == 'POST':
            cur.execute("""
                INSERT INTO bookings (user_id, slot_id, status, created_at)
                VALUES (%s, %s, 'booked', CURRENT_TIMESTAMP)
            """, (user_id, slot_id))

            db.commit()
            flash("Slot booked successfully!", "success")
            return redirect(url_for('dashboard'))

    except Exception as e:
        db.rollback()
        flash("Something went wrong while booking. Please try again.", "danger")
        print("Booking error:", e)

    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

    return render_template('book_slot.html', slot=slot)

#-------------CANCEL BOOKING-----------
@app.route('/cancel/<int:booking_id>')
def cancel_booking(booking_id):

    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        db = get_db()
        cur = db.cursor()

        # Check if booking exists and belongs to user
        cur.execute("""
            SELECT id, status 
            FROM bookings 
            WHERE id = %s AND user_id = %s
        """, (booking_id, session['user_id']))

        booking = cur.fetchone()

        if not booking:
            flash("Booking not found or access denied.", "danger")
            return redirect(url_for('dashboard'))

        # Prevent cancelling already cancelled booking
        if booking['status'] == 'cancelled':
            flash("This booking is already cancelled.", "warning")
            return redirect(url_for('dashboard'))

        # Cancel booking
        cur.execute("""
            UPDATE bookings 
            SET status = 'cancelled' 
            WHERE id = %s
        """, (booking_id,))

        db.commit()

        flash("Booking cancelled successfully.", "success")

    except Exception as e:
        db.rollback()
        flash("Something went wrong. Please try again later.", "danger")
        print("Cancel booking error:", e)

    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

    return redirect(url_for('dashboard'))


#--------ADMIN DASHBOARD------------
@app.route("/admin")
def admin_dashboard():
    if session.get("role") != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    users_count = cur.fetchone()["count"]

    cur.execute("SELECT COUNT(*) FROM bookings WHERE status='booked'")
    bookings_count = cur.fetchone()["count"]

    cur.execute("SELECT * FROM machines")
    machines = cur.fetchall()

    cur.close()
    db.close()

    return render_template(
        "admin_dashboard.html",
        users_count=users_count,
        bookings_count=bookings_count,
        machines=machines
    )

# ---------- ADD Machines ----------
@app.route("/machines", methods=["GET", "POST"])
def manage_machines():
    if session.get("role") not in ["admin", "operator"]:
        flash("Access denied", "danger")
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        name = request.form["name"]
        location = request.form["location"]

        cur.execute(
            "INSERT INTO machines (name, location) VALUES (%s, %s)",
            (name, location)
        )
        db.commit()
        flash("Machine added successfully", "success")

    cur.execute("SELECT * FROM machines")
    machines = cur.fetchall()

    cur.close()
    db.close()

    return render_template("manage_machines.html", machines=machines)

#------VIEW USERS----------------
@app.route('/admin/users')
def view_users():
    if session.get('role') != 'admin':
        flash("Admin access required.", "danger")
        return redirect(url_for('dashboard'))

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        SELECT id, name, email, phone, role
        FROM users
        ORDER BY id ASC
    """)

    users = cur.fetchall()

    cur.close()
    db.close()

    return render_template('view_users.html', users=users)

#--------------DELETE USERS--------------
@app.route('/admin/delete_user/<int:user_id>')
def delete_user(user_id):

    if session.get('role') != 'admin':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('dashboard'))

    try:
        db = get_db()
        cur = db.cursor()

        # Prevent admin from deleting themselves
        if session.get("user_id") == user_id:
            flash("You cannot delete your own account.", "warning")
            return redirect(url_for('view_users'))

        # Check if user exists
        cur.execute(
            "SELECT 1 FROM bookings WHERE user_id = %s AND status = 'booked'",
            (user_id,)
        )
        if cur.fetchone():
            flash("User has active bookings.", "warning")
            return redirect(url_for('view_users'))


        if not user:
            flash("User not found.", "danger")
            return redirect(url_for('view_users'))

        # Delete user
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        db.commit()

        flash("User deleted successfully.", "success")

    except Exception as e:
        db.rollback()
        flash("Something went wrong while deleting the user.", "danger")
        print("Delete user error:", e)

    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

    return redirect(url_for('view_users'))



#----------E - RECEIPT---------------
@app.route('/receipt/<int:booking_id>')
def receipt(booking_id):

    if 'user_id' not in session:
        return redirect(url_for('login'))

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        SELECT 
            b.id,
            u.name AS user_name,
            m.name AS machine_name,
            s.slot_date,
            s.slot_start,
            s.slot_end
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN slots s ON b.slot_id = s.id
        JOIN machines m ON s.machine_id = m.id
        WHERE b.id = %s
    """, (booking_id,))

    booking = cur.fetchone()

    cur.close()
    db.close()

    if not booking:
        flash("Receipt not found.", "danger")
        return redirect(url_for('dashboard'))

    return render_template("receipt.html", booking=booking)

#-------------MACHINE OPERATOR----------------------
@app.route("/Machine_operator")
def Machine_operator():
    if session.get("role") not in ["operator", "admin"]:
        flash("Unauthorized", "danger")
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        SELECT b.id, u.name AS user_name, m.name AS machine_name,
               s.slot_date, s.slot_start, s.slot_end, b.status
        FROM bookings b
        JOIN users u ON b.user_id = u.id
        JOIN slots s ON b.slot_id = s.id
        JOIN machines m ON s.machine_id = m.id
        ORDER BY s.slot_date, s.slot_start
    """)

    rows = cur.fetchall()
    cur.close()
    db.close()

    pq = []
    cancelled = []

    for r in rows:
        if r["status"] == "cancelled":
            cancelled.append(r)
        else:
            dt = datetime.combine(r["slot_date"], r["slot_start"])
            heapq.heappush(pq, (dt, r["id"], r))

    sorted_bookings = [heapq.heappop(pq)[2] for _ in range(len(pq))]

    return render_template(
        "Machine_operator.html",
        bookings=sorted_bookings + cancelled
    )


#-----------DELETE MACHINES------------------
@app.route('/delete_machine/<int:machine_id>')
def delete_machine(machine_id):

    if session.get('role') != 'admin':
        flash("Admin access required.", "danger")
        return redirect(url_for('admin_dashboard'))

    try:
        db = get_db()
        cur = db.cursor()

        # Check machine exists
        cur.execute("SELECT id FROM machines WHERE id = %s", (machine_id,))
        machine = cur.fetchone()

        if not machine:
            flash("Machine not found.", "danger")
            return redirect(url_for('admin_dashboard'))

        # 1️⃣ Cancel FUTURE bookings only
        cur.execute("""
            UPDATE bookings
            SET status = 'cancelled'
            WHERE slot_id IN (
                SELECT s.id FROM slots s
                WHERE s.machine_id = %s
                AND (
                    s.slot_date > CURRENT_DATE
                    OR (s.slot_date = CURRENT_DATE AND s.slot_end > CURRENT_TIME)
                )
            )
        """, (machine_id,))

        # 2️⃣ Delete FUTURE slots
        cur.execute("""
            DELETE FROM slots
            WHERE machine_id = %s
            AND (
                slot_date > CURRENT_DATE
                OR (slot_date = CURRENT_DATE AND slot_end > CURRENT_TIME)
            )
        """, (machine_id,))

        # 3️⃣ Delete machine
        cur.execute("DELETE FROM machines WHERE id = %s", (machine_id,))

        db.commit()

        flash(
            "Machine deleted successfully. Future bookings were cancelled.",
            "success"
        )

    except Exception as e:
        db.rollback()
        flash("Failed to delete machine. Please try again.", "danger")
        print("Delete machine error:", e)

    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

    return redirect(url_for('admin_dashboard'))


#------------------OPERATOR VALIDATION------------------
@app.route('/operator_validate/<int:booking_id>')
def operator_validate(booking_id):

    if session.get('role') not in ['operator', 'admin']:
        flash("Operator/Admin access required.", "danger")
        return redirect(url_for('dashboard'))

    db = get_db()
    cur = db.cursor()

    # Validate booking exists
    cur.execute(
        "SELECT id FROM bookings WHERE id = %s",
        (booking_id,)
    )

    booking = cur.fetchone()
    if not booking:
        flash("Booking not found.", "danger")
        cur.close()
        db.close()
        return redirect(url_for('Machine_operator'))

    # Update booking status
    cur.execute("""
        UPDATE bookings
        SET status = 'validated'
        WHERE id = %s
    """, (booking_id,))

    db.commit()
    cur.close()
    db.close()

    flash("Receipt validated successfully! User can now use the machine.", "success")
    return redirect(url_for('Machine_operator'))

#-----------------OPERATOR BOOKING CANCELLATION--------------------------------
@app.route("/operator_cancel/<int:booking_id>")
def operator_cancel(booking_id):

    if session.get("role") not in ["admin", "operator"]:
        flash("Unauthorized access.", "danger")
        return redirect(url_for("dashboard"))

    try:
        db = get_db()
        cur = db.cursor()

        # Check if booking exists
        cur.execute("""
            SELECT id, status 
            FROM bookings 
            WHERE id = %s
        """, (booking_id,))
        booking = cur.fetchone()

        if not booking:
            flash("Booking not found.", "danger")
            return redirect(url_for("Machine_operator"))

        # Prevent cancelling twice
        if booking["status"] == "cancelled":
            flash("This booking is already cancelled.", "warning")
            return redirect(url_for("Machine_operator"))

        # Cancel booking
        cur.execute("""
            UPDATE bookings 
            SET status = 'cancelled'
            WHERE id = %s
        """, (booking_id,))

        db.commit()

        flash("Booking cancelled successfully.", "success")

    except Exception as e:
        db.rollback()
        flash("Something went wrong while cancelling the booking.", "danger")
        print("Operator cancel error:", e)

    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

    return redirect(url_for("Machine_operator"))

#------------------SYSTEM SETTINGS---------------------
@app.route('/system_settings', methods=['GET', 'POST'])
def system_settings():

    if session.get('role') != 'admin':
        flash("Admin access required.", "danger")
        return redirect(url_for('dashboard'))

    settings = get_settings()

    if request.method == 'POST':
        try:
            # Extract values
            start_time = request.form.get('start_time')  # HH:MM
            end_time = request.form.get('end_time')

            wash_duration = int(request.form.get('wash_duration') or 30)
            break_after = int(request.form.get('break_after') or 4)
            break_duration = int(request.form.get('break_duration') or 60)

            daily_limit = int(request.form.get('daily_limit') or 1)
            weekly_limit = int(request.form.get('weekly_limit') or 2)
            monthly_limit = int(request.form.get('monthly_limit') or 8)

            auto_generate = request.form.get('auto_generate') == 'on'
            slots_per_day = int(request.form.get('slots_per_day') or 20)

            # Convert time safely
            start_time = datetime.strptime(start_time, "%H:%M").time()
            end_time = datetime.strptime(end_time, "%H:%M").time()

            db = get_db()
            cur = db.cursor()

            cur.execute("""
                UPDATE system_settings
                SET 
                    start_time = %s,
                    end_time = %s,
                    wash_duration = %s,
                    break_after = %s,
                    break_duration = %s,
                    daily_limit = %s,
                    weekly_limit = %s,
                    monthly_limit = %s,
                    auto_generate = %s,
                    slots_per_day = %s
                WHERE id = 1
            """, (
                start_time,
                end_time,
                wash_duration,
                break_after,
                break_duration,
                daily_limit,
                weekly_limit,
                monthly_limit,
                auto_generate,
                slots_per_day
            ))

            db.commit()
            cur.close()
            db.close()

            flash("System settings updated successfully!", "success")
            return redirect(url_for('system_settings'))

        except Exception as e:
            flash(f"Error updating settings: {str(e)}", "danger")

    return render_template('system_settings.html', settings=settings)

#-----------FEEDBACK----------------------
@app.route("/feedback", methods=["GET", "POST"])
def feedback():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        message = request.form["message"]

        db = get_db()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO feedback (user_id, message) VALUES (%s, %s)",
            (session["user_id"], message)
        )
        db.commit()
        cur.close()
        db.close()

        flash("Feedback sent!", "success")
        return redirect(url_for("dashboard"))

    return render_template("feedback.html")

#--------------VIEW FEEDBACK -----------------
@app.route("/view_feedback")
def view_feedback():
    if session.get("role") != "admin":
        flash("Admin access required", "danger")
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        SELECT f.id, f.message, f.created_at, u.name
        FROM feedback f
        JOIN users u ON f.user_id = u.id
        ORDER BY f.created_at DESC
    """)

    feedbacks = cur.fetchall()
    cur.close()
    db.close()

    return render_template("view_feedback.html", feedbacks=feedbacks)


# ---------- Run App ----------
if __name__ == '__main__':
    app.run()


