import threading
import time
from flask import Flask, render_template_string, request, jsonify
import cv2
import numpy as np
from pymongo import MongoClient
from datetime import datetime

app = Flask(__name__)


MONGO_URI = "mongodb://localhost:27017/"  
db = client['vehicle_crash_db']
drivers_collection = db['drivers']
alerts_collection = db['alerts']


monitoring = False
sos_sent = False
cancel_window_active = False

driver_info = {}

# HTML 
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Vehicle Crash Detection</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #1e3c72, #2a5298);
            color: white;
            text-align: center;
            padding: 20px;
        }
        .form-box {
            background: rgba(255, 255, 255, 0.1);
            padding: 30px;
            margin: 0 auto 30px;
            border-radius: 10px;
            max-width: 400px;
        }
        input, button {
            padding: 10px;
            margin: 10px 0;
            width: 90%;
            border-radius: 5px;
            border: none;
            font-size: 16px;
        }
        button {
            background: #00b894;
            color: white;
            cursor: pointer;
        }
        button:hover {
            background: #019875;
        }
        #monitoring-status {
            margin-top: 20px;
            font-size: 20px;
            color: #ffd54f;
        }
        #caution-alert {
            margin-top: 10px;
            font-size: 18px;
            color: #ffab00;
            font-weight: bold;
        }
        #sos-alert {
            display: none;
            background: #d32f2f;
            color: white;
            padding: 20px;
            margin: 20px auto;
            border-radius: 10px;
            max-width: 500px;
            font-weight: bold;
        }
        #cancel-btn {
            margin-top: 15px;
            background: #555;
            cursor: pointer;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            font-size: 16px;
        }
        #cancel-btn:disabled {
            background: #999;
            cursor: not-allowed;
        }
    </style>
</head>
<body>
    {% if not monitoring %}
    <div class="form-box">
        <h2>Enter Driver & Vehicle Details</h2>
        <form method="POST" action="/start-monitoring">
            <input type="text" name="name" placeholder="Driver Name" required>
            <input type="text" name="contact" placeholder="Contact Number" required>
            <input type="text" name="emergency_1" placeholder="Emergency Contact 1" required>
            <input type="text" name="emergency_2" placeholder="Emergency Contact 2" required>
            <input type="text" name="address" placeholder="Address" required>
            <input type="text" name="vin" placeholder="Vehicle ID Number" required>
            <input type="text" name="vehicle" placeholder="Vehicle Information" required>
            <button type="submit">Start Monitoring</button>
        </form>
    </div>
    {% else %}
    <div id="monitoring-status">
        🚗 Monitoring Active - <span id="caution-alert">⚠️ Only camera damage or physical impact triggers SOS alert</span>
    </div>
    <div id="sos-alert">
        🚨 SOS ALERT: Camera Damage or Impact Detected!<br>
        <button id="cancel-btn">Cancel SOS (10s)</button>
    </div>

    <script>
        let sosAlert = document.getElementById('sos-alert');
        let cancelBtn = document.getElementById('cancel-btn');
        let cancelTime = 10; // seconds
        let countdownInterval = null;
        let sosActive = false;

        async function checkSosStatus() {
            let res = await fetch('/check-sos');
            let data = await res.json();

            if (data.sos) {
                if (!sosActive) {
                    sosActive = true;
                    sosAlert.style.display = 'block';
                    document.getElementById('monitoring-status').style.display = 'none';
                    startCountdown();
                }
            } else {
                sosActive = false;
                sosAlert.style.display = 'none';
                document.getElementById('monitoring-status').style.display = 'block';
                resetCountdown();
            }
        }

        function startCountdown() {
            cancelBtn.disabled = false;
            cancelBtn.textContent = `Cancel SOS (${cancelTime}s)`;

            countdownInterval = setInterval(() => {
                cancelTime--;
                cancelBtn.textContent = `Cancel SOS (${cancelTime}s)`;
                if (cancelTime <= 0) {
                    cancelBtn.disabled = true;
                    cancelBtn.textContent = "Cancel SOS (Expired)";
                    clearInterval(countdownInterval);
                }
            }, 1000);
        }

        function resetCountdown() {
            cancelTime = 10;
            clearInterval(countdownInterval);
            cancelBtn.disabled = true;
            cancelBtn.textContent = "Cancel SOS";
        }

        setInterval(checkSosStatus, 1000);

        cancelBtn.onclick = async () => {
            let res = await fetch('/cancel-sos', {method: 'POST'});
            let data = await res.json();
            if (data.cancelled) {
                alert('SOS Alert Cancelled. Monitoring resumed.');
                sosAlert.style.display = 'none';
                document.getElementById('monitoring-status').style.display = 'block';
                resetCountdown();
            }
        };
    </script>
    {% endif %}
</body>
</html>
"""

def is_frame_black(frame, threshold=30, percent=0.95):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    black_pixels = np.sum(gray < threshold)
    total_pixels = gray.size
    black_ratio = black_pixels / total_pixels
    return black_ratio > percent

def is_frame_frozen(prev_frame, curr_frame, threshold=30):
    if prev_frame is None or curr_frame is None:
        return False
    diff = cv2.absdiff(prev_frame, curr_frame)
    non_zero_count = np.count_nonzero(diff)
    return non_zero_count < threshold

def simulate_physical_impact():
    return False

def save_alert(alert_type):
    alert_data = {
        "driver_name": driver_info.get("name", ""),
        "contact": driver_info.get("contact", ""),
        "emergency_1": driver_info.get("emergency_1", ""),
        "emergency_2": driver_info.get("emergency_2", ""),
        "address": driver_info.get("address", ""),
        "vin": driver_info.get("vin", ""),
        "vehicle": driver_info.get("vehicle", ""),
        "alert_type": alert_type,
        "timestamp": datetime.utcnow()
    }
    alerts_collection.insert_one(alert_data)

def camera_monitor_thread():
    global sos_sent, monitoring, cancel_window_active

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Camera not accessible.")
        return

    prev_frame = None
    print("Monitoring started...")

    while monitoring and not sos_sent:
        ret, frame = cap.read()
        if not ret or frame is None:
            print("[SOS] Camera feed lost or damaged detected!")
            sos_sent = True
            cancel_window_active = True
            save_alert("Camera feed lost or damaged")
            break

        if is_frame_black(frame):
            print("[SOS] Black frame detected - possible camera damage!")
            sos_sent = True
            cancel_window_active = True
            save_alert("Black frame detected")
            break

        if is_frame_frozen(prev_frame, frame):
            print("[SOS] Frozen frame detected - possible camera damage!")
            sos_sent = True
            cancel_window_active = True
            save_alert("Frozen frame detected")
            break

        if simulate_physical_impact():
            print("[SOS] Physical impact detected!")
            sos_sent = True
            cancel_window_active = True
            save_alert("Physical impact detected")
            break

        prev_frame = frame.copy()

        time.sleep(0.5)

    cap.release()
    monitoring = False
    print("Monitoring stopped.")

@app.route('/', methods=['GET'])
def index():
    return render_template_string(HTML_TEMPLATE, monitoring=monitoring)

@app.route('/start-monitoring', methods=['POST'])
def start_monitoring():
    global driver_info, monitoring, sos_sent, cancel_window_active
    if monitoring:
        return "Already monitoring", 400

    driver_info = request.form.to_dict()
    print(f"Driver info saved: {driver_info}")

    drivers_collection.update_one(
        {"contact": driver_info.get("contact")},
        {"$set": driver_info, "$currentDate": {"last_updated": True}},
        upsert=True
    )

    monitoring = True
    sos_sent = False
    cancel_window_active = False

    threading.Thread(target=camera_monitor_thread, daemon=True).start()
    return render_template_string(HTML_TEMPLATE, monitoring=monitoring)

@app.route('/check-sos', methods=['GET'])
def check_sos():
    global sos_sent, cancel_window_active
    return jsonify({"sos": sos_sent and cancel_window_active})

@app.route('/cancel-sos', methods=['POST'])
def cancel_sos():
    global monitoring, sos_sent, cancel_window_active
    if sos_sent and cancel_window_active:
        sos_sent = False
        cancel_window_active = False
        monitoring = True
        threading.Thread(target=camera_monitor_thread, daemon=True).start()
        return jsonify({"cancelled": True})
    return jsonify({"cancelled": False})

if __name__ == '__main__':
    app.run(debug=True)
